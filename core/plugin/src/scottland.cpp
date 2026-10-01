#include <wayfire/plugin.hpp>
#include <wayfire/core.hpp>
#include <wayfire/output.hpp>
#include <wayfire/seat.hpp>
#include <wayfire/input-device.hpp>
#include <wayfire/toplevel-view.hpp>
#include <wayfire/view-transform.hpp>
#include <wayfire/signal-definitions.hpp>
#include <wayfire/option-wrapper.hpp>
#include <wayfire/config/compound-option.hpp>
#include <wayfire/plugins/common/shared-core-data.hpp>
#include <wayfire/plugins/ipc/ipc-method-repository.hpp>
#include <wayfire/plugins/common/move-drag-interface.hpp>
#include <wayfire/plugins/common/input-grab.hpp>
#include <wayfire/per-output-plugin.hpp>
#include <wayfire/txn/transaction-manager.hpp>
#include <wayfire/toplevel.hpp>
#include <wayfire/touch/touch.hpp>
#include <wayfire/workspace-set.hpp>
#include <wayfire/workarea.hpp>
#include <wayfire/window-manager.hpp>
#include <linux/input-event-codes.h>
#include <wayfire/util/log.hpp>
#include <wayfire/util/duration.hpp>
#include <wayfire/util.hpp>
#include <wayfire/config.h>

extern "C" {
#include <wlr/types/wlr_pointer.h>
#include <wlr/types/wlr_keyboard.h>
#include <wlr/types/wlr_seat.h>
#include <wlr/backend/libinput.h>
#include <wlr/backend/headless.h>
#include <wlr/backend/multi.h>
#include <wlr/interfaces/wlr_pointer.h>
#include <wlr/types/wlr_xdg_activation_v1.h>
#include <wlr/types/wlr_compositor.h>
#if WF_HAS_XWAYLAND
#include <pthread.h>  // as Wayfire does: xwayland.h uses C++ keywords as names
#define class class_t
#define static
#include <wlr/xwayland.h>
#undef static
#undef class
#endif
}

#include <libinput.h>

#include <xkbcommon/xkbcommon.h>

#include "frame.hpp"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <ctime>
#include <map>
#include <set>
#include <regex>
#include <optional>
#include <signal.h>
extern "C" {
#include <sys/pidfd.h>  // glibc declares it without C linkage for C++
}
#include <unistd.h>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

// Scottland layout plugin.
//
// Spatial layout: each screen has five vertical zones. A center zone (center_width % of the width)
// shows windows at 100%. Thin rails at the far left and right (rail_width %) are widget rails:
// windows there are asked to render as widgets and otherwise stay at min_scale. Between them, the
// scale falls linearly from max_scale next to the center zone to min_scale at the rails. A window's
// zone is set by its center; scaling is a real, interactive transform around that center. Jumps in
// scale (crossing from the center into a zone that starts below 100%) animate instead of snapping.
//
// General compositor features Scottland's integrations rely on:
//
//  - IPC "scottland/send-key": press/release a key with explicit modifiers on the focused
//    surface, independent of keys physically held (Hyprland's send_key_state).
//  - [scottland] release_key_<name> / release_command_<name>: run a command when a key is
//    released (e.g. accept a switcher when Super is let go).
// Custom IPC events go through the ipc-rules plugin, which relays this signal (emitted on the shared
// IPC method repository) to clients subscribed to the event's name. Declared as in Wayfire's
// ipc-rules-common.hpp, which can't be included outside Wayfire's tree (it needs its config.h).
namespace wf::ipc_rules::detail
{
struct custom_event_signal_t
{
    wf::json_t data;
};
}

namespace
{
uint32_t now_msec()
{
    timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

std::string upper(std::string text)
{
    for (auto& c : text)
    {
        c = std::toupper(static_cast<unsigned char>(c));
    }

    return text;
}

/** Resolve a key given as an xkb keysym name ("Insert", "C", "Super_L") or "code:N" (xkb keycode)
 *  to an evdev keycode, using the seat's current keymap. */
std::optional<uint32_t> evdev_keycode(xkb_keymap *keymap, const std::string& name)
{
    if (name.rfind("code:", 0) == 0)
    {
        return std::stoul(name.substr(5)) - 8;
    }

    xkb_keysym_t wanted = xkb_keysym_from_name(name.c_str(), XKB_KEYSYM_NO_FLAGS);
    if (wanted == XKB_KEY_NoSymbol)
    {
        wanted = xkb_keysym_from_name(name.c_str(), XKB_KEYSYM_CASE_INSENSITIVE);
    }

    if ((wanted == XKB_KEY_NoSymbol) || !keymap)
    {
        return {};
    }

    for (xkb_keycode_t code = xkb_keymap_min_keycode(keymap); code <= xkb_keymap_max_keycode(keymap); code++)
    {
        auto levels = xkb_keymap_num_levels_for_key(keymap, code, 0);
        for (xkb_level_index_t level = 0; level < levels; level++)
        {
            const xkb_keysym_t *syms;
            int count = xkb_keymap_key_get_syms_by_level(keymap, code, 0, level, &syms);
            for (int i = 0; i < count; i++)
            {
                if (syms[i] == wanted)
                {
                    return code - 8;
                }
            }
        }
    }

    return {};
}

/** Modifier mask for names like "CTRL SHIFT" or "SUPER,ALT". */
uint32_t modifier_mask(xkb_keymap *keymap, const std::string& names)
{
    std::string normalized = upper(names);
    for (auto& c : normalized)
    {
        if ((c == '+') || (c == ','))
        {
            c = ' ';
        }
    }

    uint32_t mask = 0;
    std::istringstream words(normalized);
    std::string word;
    while (words >> word)
    {
        const char *xkb_name = nullptr;
        if ((word == "CTRL") || (word == "CONTROL"))
        {
            xkb_name = XKB_MOD_NAME_CTRL;
        } else if (word == "SHIFT")
        {
            xkb_name = XKB_MOD_NAME_SHIFT;
        } else if (word == "ALT")
        {
            xkb_name = XKB_MOD_NAME_ALT;
        } else if ((word == "SUPER") || (word == "LOGO") || (word == "MOD4"))
        {
            xkb_name = XKB_MOD_NAME_LOGO;
        }

        if (xkb_name)
        {
            auto index = xkb_keymap_mod_get_index(keymap, xkb_name);
            if (index != XKB_MOD_INVALID)
            {
                mask |= 1u << index;
            }
        }
    }

    return mask;
}
}

namespace
{
/** Deliver one key state to the focused surface with exactly `mask` held, then restore the
 *  keyboard's real modifier state (so a physically held modifier doesn't leak in). */
void inject_key(wlr_seat *seat, wlr_keyboard *keyboard, uint32_t keycode, uint32_t mask, bool pressed)
{
    wlr_keyboard_modifiers saved = keyboard->modifiers;
    wlr_keyboard_modifiers synthetic = saved;
    synthetic.depressed = mask;
    synthetic.latched   = 0;
    wlr_seat_keyboard_notify_modifiers(seat, &synthetic);
    wlr_seat_keyboard_notify_key(seat, now_msec(), keycode,
        pressed ? WL_KEYBOARD_KEY_STATE_PRESSED : WL_KEYBOARD_KEY_STATE_RELEASED);
    wlr_seat_keyboard_notify_modifiers(seat, &saved);
}

/** Split "CTRL+ALT+W" into its modifier names and key. */
std::pair<std::string, std::string> split_combo(const std::string& combo)
{
    auto plus = combo.find_last_of('+');
    return plus == std::string::npos ? std::make_pair(std::string{}, combo) :
           std::make_pair(combo.substr(0, plus), combo.substr(plus + 1));
}

enum class zone_t { center, continuous, widget };

struct placement_t
{
    zone_t zone;
    double scale;
};

const char *zone_name(zone_t zone)
{
    switch (zone)
    {
      case zone_t::center:
        return "center";

      case zone_t::continuous:
        return "continuous";

      case zone_t::widget:
        return "widget";
    }

    return "";
}

/**
 * The scale curve across a continuous zone: points (t, scale), t = 0 at the center zone's edge
 * and 1 at the widget rail, joined by a monotone cubic (PCHIP) spline, which passes through the
 * points smoothly without overshooting them. Stored as "t:scale t:scale ...".
 */
class scale_curve_t
{
    std::vector<double> xs, ys, slopes;

  public:
    bool parse(const std::string& text)
    {
        std::vector<std::pair<double, double>> points;
        std::istringstream words(text);
        std::string word;
        while (words >> word)
        {
            auto colon = word.find(':');
            if (colon == std::string::npos)
            {
                return false;
            }

            try {
                points.emplace_back(std::clamp(std::stod(word.substr(0, colon)), 0.0, 1.0),
                    std::clamp(std::stod(word.substr(colon + 1)), 0.05, 1.0));
            } catch (...)
            {
                return false;
            }
        }

        std::sort(points.begin(), points.end());
        if ((points.size() < 2) || (points.front().first > 0.0) || (points.back().first < 1.0))
        {
            return false;
        }

        xs.clear();
        ys.clear();
        for (auto& [x, y] : points)
        {
            if (!xs.empty() && (x - xs.back() < 1e-6))
            {
                continue;
            }

            xs.push_back(x);
            ys.push_back(y);
        }

        size_t n = xs.size();
        std::vector<double> delta(n - 1);
        for (size_t i = 0; i + 1 < n; i++)
        {
            delta[i] = (ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i]);
        }

        slopes.assign(n, 0.0);
        slopes[0]     = delta[0];
        slopes[n - 1] = delta[n - 2];
        for (size_t i = 1; i + 1 < n; i++)
        {
            if (delta[i - 1] * delta[i] <= 0)
            {
                continue;
            }

            double h0 = xs[i] - xs[i - 1], h1 = xs[i + 1] - xs[i];
            double w1 = 2 * h1 + h0, w2 = h1 + 2 * h0;
            slopes[i] = (w1 + w2) / (w1 / delta[i - 1] + w2 / delta[i]);
        }

        return true;
    }

    bool empty() const
    {
        return xs.empty();
    }

    /** d(scale)/dt where the curve starts (t = 0). */
    double start_slope() const
    {
        return slopes.empty() ? 0.0 : slopes[0];
    }

    double operator ()(double t) const
    {
        t = std::clamp(t, 0.0, 1.0);
        size_t i = 0;
        while (i + 2 < xs.size() && t > xs[i + 1])
        {
            i++;
        }

        double h = xs[i + 1] - xs[i];
        double u = (t - xs[i]) / h;
        double u2 = u * u, u3 = u2 * u;
        double y = (2 * u3 - 3 * u2 + 1) * ys[i] + (u3 - 2 * u2 + u) * h * slopes[i] +
            (-2 * u3 + 3 * u2) * ys[i + 1] + (u3 - u2) * h * slopes[i + 1];
        return std::clamp(y, 0.05, 1.0);
    }
};

/** Zone and scale for a window centered at x on a screen `width` wide. Without a curve, the
 *  continuous zones run linearly from max_scale to min_scale. A band `blend` px wide just outside
 *  the center zone eases from 100% into the curve: flat where it meets the center, matching the
 *  curve's starting slope where it meets the curve, so the join has no jump and no corner. */
placement_t place(double x, double width, double center_pct, double rail_pct, double min_scale,
    double max_scale, const scale_curve_t& curve, double blend)
{
    max_scale = std::max(max_scale, min_scale);
    double center_half = width * std::clamp(center_pct, 0.0, 100.0) / 200.0;
    double rail = width * std::clamp(rail_pct, 0.0, 50.0) / 100.0;
    double from_middle = std::abs(x - width / 2.0);
    double to_rail     = width / 2.0 - rail;

    if (from_middle <= center_half)
    {
        return {zone_t::center, 1.0};
    }

    if (from_middle >= to_rail)
    {
        return {zone_t::widget, curve.empty() ? min_scale : curve(1.0)};
    }

    double span = std::max(1.0, to_rail - center_half);
    blend = std::clamp(blend, 0.0, span * 0.5);
    auto curve_at = [&] (double t)
    {
        return curve.empty() ? max_scale - t * (max_scale - min_scale) : curve(t);
    };

    double into = from_middle - center_half;
    if (into < blend)
    {
        // Cubic Hermite from (0, 1.0, slope 0) to (blend, curve(0), curve'(0) in px).
        double u  = into / blend;
        double p1 = curve_at(0.0);
        double m1 = (curve.empty() ? -(max_scale - min_scale) : curve.start_slope()) * blend / (span - blend);
        double u2 = u * u, u3 = u2 * u;
        double s  = (2 * u3 - 3 * u2 + 1) * 1.0 + (-2 * u3 + 3 * u2) * p1 + (u3 - u2) * m1;
        return {zone_t::continuous, std::clamp(s, 0.05, 1.0)};
    }

    double t = (into - blend) / std::max(1.0, span - blend);
    return {zone_t::continuous, curve_at(t)};
}
}

/**
 * Center-anchored resize (Super + right-drag by default), like visionOS: the window grows or
 * shrinks symmetrically around its center, which stays put, so it keeps its zone and scale. Cursor
 * motion is divided by the window's current scale so the edges track the cursor on screen.
 */
class center_resize_t : public wf::per_output_plugin_instance_t, public wf::pointer_interaction_t,
    public wf::touch_interaction_t
{
    wf::option_wrapper_t<wf::buttonbinding_t> button{"scottland/resize"};
    wf::option_wrapper_t<wf::buttonbinding_t> alt_button{"scottland/resize_alt"};
    std::unique_ptr<wf::input_grab_t> input_grab;
    wf::plugin_activation_data_t grab_interface = {
        .name = "scottland-resize",
        .capabilities = wf::CAPABILITY_GRAB_INPUT | wf::CAPABILITY_MANAGE_DESKTOP,
    };

    std::weak_ptr<wf::view_interface_t> view;
    std::weak_ptr<wf::view_interface_t> recenter_view;  // keeps its center after release, until settled
    wf::pointf_t anchor_center;
    wf::pointf_t grab_start;
    wf::geometry_t start_geometry;
    double scale = 1.0;
    int sign_x = 1, sign_y = 1;

    int touch_finger = -1;  // the finger driving the resize, or -1 for the pointer

    // Touchpad drag lock (L22) keeps a tap-and-drag going for a moment after the finger lifts, so
    // putting it back down continues the drag. For moving a window that's a grace period; for a
    // resize, which magnifies motion at small scales, it's a trap: you lift thinking it's done, go
    // to move the cursor, and the window resizes. So a resize turns drag lock off while it runs
    // (libinput reads it when the finger lifts) and puts each touchpad's setting back after.
    void suspend_drag_lock()
    {
        for (auto& device : wf::get_core().get_input_devices())
        {
            auto handle = device->get_wlr_handle();
            if (!handle || !wlr_input_device_is_libinput(handle))
            {
                continue;
            }

            auto li = wlr_libinput_get_device_handle(handle);
            if (!li || (libinput_device_config_tap_get_finger_count(li) == 0))
            {
                continue;
            }

            auto state = libinput_device_config_tap_get_drag_lock_enabled(li);
            if (state != LIBINPUT_CONFIG_DRAG_LOCK_DISABLED)
            {
                libinput_device_ref(li);
                libinput_device_config_tap_set_drag_lock_enabled(li, LIBINPUT_CONFIG_DRAG_LOCK_DISABLED);
                suspended_locks.push_back({li, state});
            }
        }
    }

    void restore_drag_lock()
    {
        for (auto& [li, state] : suspended_locks)
        {
            libinput_device_config_tap_set_drag_lock_enabled(li, state);
            libinput_device_unref(li);
        }

        suspended_locks.clear();
    }

    std::vector<std::pair<libinput_device*, libinput_config_drag_lock_state>> suspended_locks;

    wf::pointf_t input_coords()
    {
        auto global = (touch_finger >= 0) ? wf::get_core().get_touch_position(touch_finger) :
            wf::get_core().get_cursor_position();
        return global - wf::origin(output->get_layout_geometry());
    }

    uint32_t grab_button = BTN_RIGHT;

    wf::button_callback on_activate = [=] (auto)
    {
        auto target = wf::toplevel_cast(wf::get_core().get_cursor_focus_view());
        // Direction is absolute, wherever the window was grabbed: right/up grows, left/down shrinks.
        return start(target, wf::buttonbinding_t(button).get_button(), 1, -1);
    };

    // The same resize on a second binding (Super+Alt+drag by default).
    wf::button_callback on_activate_alt = [=] (auto)
    {
        auto target = wf::toplevel_cast(wf::get_core().get_cursor_focus_view());
        return start(target, wf::buttonbinding_t(alt_button).get_button(), 1, -1);
    };

  public:
    /** Resize `target` around its center until `with_button` is released. Moving the cursor
     *  by (dx, dy) grows the window by (sign_x * dx, sign_y * dy) on each side. */
    bool start(wayfire_toplevel_view target, uint32_t with_button, int grow_x_sign, int grow_y_sign,
        int finger = -1)
    {
        if (!target || !target->is_mapped() || target->pending_fullscreen() ||
            (target->get_output() != output) || !(target->get_allowed_actions() & wf::VIEW_ALLOW_RESIZE))
        {
            return false;
        }

        if (!output->activate_plugin(&grab_interface))
        {
            return false;
        }

        input_grab->set_wants_raw_input(true);
        input_grab->grab_input(wf::scene::layer::OVERLAY);
        if (target->pending_tiled_edges())
        {
            target->toplevel()->pending().tiled_edges = 0;
        }

        view = target->weak_from_this();
        recenter_view = view;
        touch_finger   = finger;
        grab_start     = input_coords();
        start_geometry = target->get_geometry();
        anchor_center  = {start_geometry.x + start_geometry.width / 2.0,
            start_geometry.y + start_geometry.height / 2.0};
        target->connect(&on_geometry);
        auto transformer = target->get_transformed_node()->get_transformer<
            wf::scene::view_2d_transformer_t>("scottland-scale");
        scale = std::max(0.05, transformer ? (double)transformer->scale_x : 1.0);
        grab_button = with_button;
        sign_x = grow_x_sign;
        sign_y = grow_y_sign;
        if (finger < 0)
        {
            suspend_drag_lock();
        }

        wf::get_core().set_cursor("all-scroll");
        return true;
    }

  private:
    void end()
    {
        if (input_grab->is_grabbed())
        {
            input_grab->ungrab_input();
        }

        output->deactivate_plugin(&grab_interface);
        view.reset();
        touch_finger = -1;
        restore_drag_lock();
        // Let the app's final commit land, then stop keeping it centered.
        settle.set_timeout(300, [=] ()
        {
            on_geometry.disconnect();
            recenter_view.reset();
        });
    }

    wf::wl_timer<false> settle;

    // Apps often commit a different size than requested (terminals snap to whole cells). Keep the
    // anchor center by moving the window to match whatever size it actually took.
    wf::signal::connection_t<wf::view_geometry_changed_signal> on_geometry =
        [=] (wf::view_geometry_changed_signal *ev)
    {
        auto target = wf::toplevel_cast(ev->view);
        if (!target || (target.get() != recenter_view.lock().get()))
        {
            return;
        }

        auto g = target->get_geometry();
        double x = std::round(anchor_center.x - g.width / 2.0);
        double y = std::round(anchor_center.y - g.height / 2.0);
        if ((std::abs(g.x - x) > 0.5) || (std::abs(g.y - y) > 0.5))
        {
            target->move(x, y);
        }
    };

  public:
    void init() override
    {
        input_grab = std::make_unique<wf::input_grab_t>("scottland-resize", output, nullptr, this, this);
        grab_interface.cancel = [=] () { end(); };
        output->add_button(button, &on_activate);
        output->add_button(alt_button, &on_activate_alt);
    }

    void fini() override
    {
        end();
        settle.disconnect();
        on_geometry.disconnect();
        output->rem_binding(&on_activate);
        output->rem_binding(&on_activate_alt);
    }

    void handle_pointer_button(const wlr_pointer_button_event& event) override
    {
        if ((event.state == WL_POINTER_BUTTON_STATE_RELEASED) && (event.button == grab_button))
        {
            end();
        }
    }

    void handle_touch_motion(uint32_t, int finger_id, wf::pointf_t) override
    {
        if (finger_id == touch_finger)
        {
            update();
        }
    }

    void handle_touch_up(uint32_t, int finger_id, wf::pointf_t) override
    {
        if (finger_id == touch_finger)
        {
            end();
        }
    }

    void handle_pointer_motion(wf::pointf_t, uint32_t) override
    {
        if (touch_finger < 0)
        {
            update();
        }
    }

    void update()
    {
        auto target = wf::toplevel_cast(view.lock().get());
        if (!target || !target->is_mapped())
        {
            end();
            return;
        }

        auto input = input_coords();
        double grow_x = sign_x * (input.x - grab_start.x) / scale;
        double grow_y = sign_y * (input.y - grab_start.y) / scale;

        auto min_size = target->toplevel()->get_min_size();
        auto max_size = target->toplevel()->get_max_size();
        int width  = std::max({1, min_size.width, (int)std::lround(start_geometry.width + 2 * grow_x)});
        int height = std::max({1, min_size.height, (int)std::lround(start_geometry.height + 2 * grow_y)});
        if (max_size.width > 0)
        {
            width = std::min(width, max_size.width);
        }

        if (max_size.height > 0)
        {
            height = std::min(height, max_size.height);
        }

        // Ask for the size only, where the window is now; on_geometry centers whatever size the
        // app commits. Positioning for the size asked would flicker with apps that don't take it
        // (one capped at the screen's size commits a smaller one): each request would place it
        // for the size asked, the commit would land there at the size taken, and the recenter
        // would move it back, again and again as the pointer moves.
        auto current = target->get_geometry();
        wf::geometry_t desired;
        desired.x      = current.x;
        desired.y      = current.y;
        desired.width  = width;
        desired.height = height;
        if (target->toplevel()->pending().geometry != desired)
        {
            target->toplevel()->pending().gravity  = 0;
            target->toplevel()->pending().geometry = desired;
            wf::get_core().tx_manager->schedule_object(target->toplevel());
        }
    }
};

/**
 * A pointer device of Scottland's own. Its events go through Wayfire's normal input path, like
 * any mouse, so pointer focus and delivery stay right. Used to give apps that ignore touch the
 * pointer equivalent of a finger: scrolling and clicks.
 */
class virtual_pointer_t
{
    static inline const wlr_pointer_impl impl = {.name = "scottland-touch-pointer"};
    wlr_backend *backend = nullptr;

  public:
    wlr_pointer pointer;

    virtual_pointer_t()
    {
        auto& core = wf::get_core();
        backend = wlr_headless_backend_create(core.ev_loop);
        wlr_multi_backend_add(core.backend, backend);
        wlr_pointer_init(&pointer, &impl, "scottland-touch-pointer");
        wl_signal_emit_mutable(&backend->events.new_input, &pointer.base);
        if (core.get_current_state() >= wf::compositor_state_t::RUNNING)
        {
            wlr_backend_start(backend);
        }
    }

    ~virtual_pointer_t()
    {
        wlr_pointer_finish(&pointer);
        wlr_multi_backend_remove(wf::get_core().backend, backend);
        wlr_backend_destroy(backend);
    }

    void move_to(wf::pointf_t to)
    {
        auto cursor = wf::get_core().get_cursor_position();
        wlr_pointer_motion_event ev;
        ev.pointer   = &pointer;
        ev.time_msec = now_msec();
        ev.delta_x   = ev.unaccel_dx = to.x - cursor.x;
        ev.delta_y   = ev.unaccel_dy = to.y - cursor.y;
        wl_signal_emit(&pointer.events.motion, &ev);
        wl_signal_emit(&pointer.events.frame, NULL);
    }

    void scroll(double dx, double dy)
    {
        for (auto [orientation, delta] : {std::pair{WL_POINTER_AXIS_VERTICAL_SCROLL, dy},
            std::pair{WL_POINTER_AXIS_HORIZONTAL_SCROLL, dx}})
        {
            wlr_pointer_axis_event ev;
            ev.pointer     = &pointer;
            ev.time_msec   = now_msec();
            ev.source      = WL_POINTER_AXIS_SOURCE_FINGER;  // smooth, like a trackpad
            ev.orientation = orientation;
            ev.relative_direction = WL_POINTER_AXIS_RELATIVE_DIRECTION_IDENTICAL;
            ev.delta = delta;
            ev.delta_discrete = 0;
            wl_signal_emit(&pointer.events.axis, &ev);
        }

        wl_signal_emit(&pointer.events.frame, NULL);
    }

    void click(uint32_t button)
    {
        for (auto state : {WL_POINTER_BUTTON_STATE_PRESSED, WL_POINTER_BUTTON_STATE_RELEASED})
        {
            wlr_pointer_button_event ev;
            ev.pointer   = &pointer;
            ev.time_msec = now_msec();
            ev.button    = button;
            ev.state     = state;
            wl_signal_emit(&pointer.events.button, &ev);
            wl_signal_emit(&pointer.events.frame, NULL);
        }
    }
};

class scottland_plugin_t : public wf::plugin_interface_t,
    public wf::per_output_tracker_mixin_t<center_resize_t>
{
    static constexpr const char *TRANSFORMER = "scottland-scale";

    wf::option_wrapper_t<double> center_width{"scottland/center_width"};
    wf::option_wrapper_t<double> rail_width{"scottland/rail_width"};
    wf::option_wrapper_t<double> min_scale{"scottland/min_scale"};
    wf::option_wrapper_t<double> max_scale{"scottland/max_scale"};
    wf::option_wrapper_t<std::string> scale_curve_text{"scottland/scale_curve"};
    wf::option_wrapper_t<double> blend_width{"scottland/blend_width"};
    wf::option_wrapper_t<std::string> color_scheme{"scottland/color_scheme"};
    wf::option_wrapper_t<wf::color_t> accent_color{"scottland/accent_color"};
    wf::option_wrapper_t<wf::color_t> attention_color{"scottland/attention_color"};

    void load_color_scheme()
    {
        scottland::palette.light = std::string(color_scheme) == "light";
        wf::color_t accent = accent_color;
        scottland::palette.accent = {accent.r, accent.g, accent.b};
        wf::color_t attention = attention_color;
        scottland::palette.attention = {attention.r, attention.g, attention.b};
        for (auto& view : wf::get_core().get_all_views())
        {
            if (auto toplevel = wf::toplevel_cast(view))
            {
                if (auto frame = frame_of(toplevel, false))
                {
                    frame->damage();
                }
            }
        }
    }
    scale_curve_t scale_curve;

    void load_curve()
    {
        std::string text = scale_curve_text;
        if (!scale_curve.parse(text))
        {
            if (!text.empty())
            {
                LOGE("scottland: ignoring invalid scale_curve \"", text, "\"");
            }

            scale_curve = {};
        }
    }

    placement_t place_at(double x, double width)
    {
        return place(x, width, center_width, rail_width, std::clamp((double)min_scale, 0.05, 1.0),
            std::clamp((double)max_scale, 0.05, 1.0), scale_curve, std::max(0.0, (double)blend_width));
    }

    placement_t placement_of(wayfire_toplevel_view view)
    {
        auto output = view->get_output();
        if (!output || view->pending_fullscreen())
        {
            return {zone_t::center, 1.0};
        }

        auto geometry = view->get_geometry();
        double x = geometry.x + geometry.width / 2.0;
        return place_at(x, output->get_relative_geometry().width);
    }

    void apply(wayfire_view any_view)
    {
        auto view = wf::toplevel_cast(any_view);
        if (!view || !view->is_mapped())
        {
            return;
        }

        // Widgets are always at 100%: they never follow the zones (WG4).
        if (is_widget(view))
        {
            set_scale(view, 1.0);
            return;
        }

        // While Wayfire's move tool drags a window, its geometry only changes on release;
        // on_drag_motion keeps the scale live instead.
        if (drag->view == view)
        {
            return;
        }

        set_scale(view, placement_of(view).scale);
    }

    // Scale changes bigger than this animate; smaller ones (a drag moving through a continuous
    // zone) apply immediately so the window tracks the pointer.
    static constexpr double JUMP = 0.03;

    struct transition_t
    {
        std::weak_ptr<wf::view_interface_t> view;
        wf::animation::simple_animation_t animation;
    };

    std::shared_ptr<wf::config::option_t<int>> transition_ms = wf::create_option<int>(180);
    std::map<uint64_t, transition_t> transitions;
    wf::wl_timer<true> transition_tick;

    double displayed_scale(wayfire_toplevel_view view)
    {
        auto transformer = view->get_transformed_node()->get_transformer<
            wf::scene::view_2d_transformer_t>(TRANSFORMER);
        return transformer ? transformer->scale_x : 1.0;
    }

    std::map<uint64_t, double> announced_scale;

    /** Move a window toward `target`, animating jumps. */
    void set_scale(wayfire_toplevel_view view, double target)
    {
        // Apps can follow their own scale over IPC/D-Bus (WG12): announce target changes.
        auto& last = announced_scale[view->get_id()];
        if (std::abs(last - target) > 0.01)
        {
            last = target;
            wf::json_t event;
            event["window"] = (int64_t)view->get_id();
            event["scale"]  = target;
            send_ipc_event(event, "scottland-scale#");
        }

        auto found = transitions.find(view->get_id());
        if (found != transitions.end())
        {
            // Already animating: re-aim at the new target without restarting the clock.
            found->second.animation.end = target;
            return;
        }

        double current = displayed_scale(view);
        if (std::abs(target - current) <= JUMP)
        {
            apply_scale(view, target);
            return;
        }

        auto& transition = transitions[view->get_id()];
        transition.view = view->weak_from_this();
        transition.animation = wf::animation::simple_animation_t{transition_ms};
        transition.animation.animate(current, target);
        if (!transition_tick.is_connected())
        {
            transition_tick.set_timeout(8, [=] () { return step_transitions(); });
        }
    }

    bool step_transitions()
    {
        for (auto it = transitions.begin(); it != transitions.end();)
        {
            auto view = wf::toplevel_cast(it->second.view.lock().get());
            if (!view || !view->is_mapped())
            {
                it = transitions.erase(it);
                continue;
            }

            auto& animation = it->second.animation;
            if (animation.running())
            {
                apply_scale(view, animation);
                ++it;
            } else
            {
                apply_scale(view, animation.end);
                it = transitions.erase(it);
            }
        }

        return !transitions.empty();
    }

    /** The window's frame (rounding, scale and handles), created on demand. Fullscreen windows
     *  have none: they're drawn edge to edge, unscaled. */
    std::shared_ptr<scottland::frame_t> frame_of(wayfire_toplevel_view view, bool create = true)
    {
        auto node = view->get_transformed_node();
        auto frame = node->get_transformer<scottland::frame_t>(TRANSFORMER);
        if (view->pending_fullscreen())
        {
            if (frame)
            {
                forget_owner(frame);
                view->damage();
                node->rem_transformer(frame);
                view->damage();
            }

            return nullptr;
        }

        if (!frame && create)
        {
            frame = std::make_shared<scottland::frame_t>(view);
            frame->on_press = [=] (wayfire_toplevel_view v, scottland::handle_t h, int finger)
            {
                handle_pressed(v, h, finger);
            };
            // A widget's close dot closes it and its app's window together, and a widget that
            // ignores the request is ended (WG5).
            frame->on_close = [=] (wayfire_toplevel_view v)
            {
                if (auto link = link_of_widget(v))
                {
                    close_linked(*link);
                } else
                {
                    v->close();
                }
            };
            frame->set_focused(wf::get_core().seat->get_active_view() == view);
            node->add_transformer(frame, wf::TRANSFORMER_2D, TRANSFORMER);
            view->damage();
        }

        return frame;
    }

    void apply_scale(wayfire_toplevel_view view, double scale)
    {
        auto frame = frame_of(view);
        if (frame && (std::abs(frame->scale_x - scale) > 0.0005))
        {
            frame->damage();
            frame->scale_x = frame->scale_y = scale;
            frame->damage();
            update_neighbors(view->get_output());
        }
    }

    // Halos (A3-A11). The frame nearest the cursor follows it (corner clouds, close dot, hover);
    // the others are told it left.
    std::weak_ptr<scottland::frame_t> pointer_owner;

    void forget_owner(const std::shared_ptr<scottland::frame_t>& frame)
    {
        if (pointer_owner.lock() == frame)
        {
            pointer_owner.reset();
        }
    }

    /** Framed windows on an output, topmost first. */
    std::vector<std::pair<wayfire_toplevel_view, std::shared_ptr<scottland::frame_t>>> frames_on(
        wf::output_t *output)
    {
        std::vector<std::pair<wayfire_toplevel_view, std::shared_ptr<scottland::frame_t>>> list;
        if (!output || !output->wset())
        {
            return list;
        }

        for (auto& view : output->wset()->get_views(wf::WSET_MAPPED_ONLY | wf::WSET_SORT_STACKING))
        {
            if (!view->get_root_node()->is_enabled())
            {
                continue;  // hidden behind its widget
            }

            if (auto frame = frame_of(view, false))
            {
                list.emplace_back(view, frame);
            }
        }

        return list;
    }

    /** The window whose halo follows the cursor at p (output coordinates): the one whose halo band
     *  is nearest, from outside or inside its window. Windows behind the one the cursor is over
     *  don't respond (that one still does). */
    std::shared_ptr<scottland::frame_t> frame_near(wf::output_t *output, wf::pointf_t p)
    {
        std::shared_ptr<scottland::frame_t> best;
        double best_distance = std::max(scottland::NEAR_RANGE, scottland::SWELL_VICINITY);
        for (auto& [view, frame] : frames_on(output))
        {
            double d = frame->band_distance(p);
            if (d < best_distance)
            {
                best = frame;
                best_distance = d;
            }

            if (frame->liquid_distance(p) <= 0)
            {
                break;  // the cursor is over this window or its halo: those behind are covered
            }
        }

        return best;
    }

    void track_pointer()
    {
        auto owner = pointer_owner.lock();
        if (owner && owner->is_pressed())
        {
            return;
        }

        auto cursor = wf::get_core().get_cursor_position();
        auto output = wf::get_core().output_layout->find_closest_output(cursor);
        std::shared_ptr<scottland::frame_t> frame;
        wf::pointf_t local = cursor;
        if (output && !output->is_plugin_active("move") && !output->is_plugin_active("scottland-resize"))
        {
            local = cursor - wf::origin(output->get_layout_geometry());
            frame = frame_near(output, local);
        }

        if (owner && (owner != frame))
        {
            owner->leave();
        }

        if (frame)
        {
            frame->track(local);
        }

        pointer_owner = frame;
    }

    /** Tell each framed window where the liquid of the windows around it is (A10), so halos
     *  merge and reach for each other. A window being dragged is drawn away from its geometry,
     *  so it takes no part until it's dropped. */
    void update_neighbors(wf::output_t *output)
    {
        auto list = frames_on(output);
        auto dragged = drag->view;
        for (size_t i = 0; i < list.size(); i++)
        {
            auto& [view, frame] = list[i];
            std::vector<scottland::neighbor_t> neighbors;
            if (view != dragged)
            {
                auto mine = frame->screen_rect().grown(frame->thickness());
                for (size_t j = 0; j < list.size(); j++)
                {
                    auto& [other_view, other] = list[j];
                    if ((j == i) || (other_view == dragged))
                    {
                        continue;
                    }

                    auto theirs = other->screen_rect().grown(other->thickness());
                    double gx = std::max({mine.x1 - theirs.x2, theirs.x1 - mine.x2, 0.0});
                    double gy = std::max({mine.y1 - theirs.y2, theirs.y1 - mine.y2, 0.0});
                    if (std::hypot(gx, gy) < scottland::MERGE)
                    {
                        neighbors.push_back({other->screen_rect(), other->screen_radius(),
                            other->thickness(), j < i});
                    }
                }
            }

            if (neighbors.size() > (size_t)scottland::MAX_NEIGHBORS)
            {
                neighbors.resize(scottland::MAX_NEIGHBORS);
            }

            frame->set_neighbors(std::move(neighbors));
        }
    }

    void update_all_neighbors()
    {
        for (auto& output : wf::get_core().output_layout->get_outputs())
        {
            update_neighbors(output);
        }
    }

    void update_focus()
    {
        auto active = wf::get_core().seat->get_active_view();
        for (auto& view : wf::get_core().get_all_views())
        {
            if (auto toplevel = wf::toplevel_cast(view))
            {
                if (auto frame = frame_of(toplevel, false))
                {
                    frame->set_focused(view == active);
                }
            }
        }

        // Focus usually comes with raising: stacking decides whose liquid is in front.
        update_all_neighbors();
    }

    wf::signal::connection_t<wf::keyboard_focus_changed_signal> on_focus =
        [=] (wf::keyboard_focus_changed_signal*)
    {
        update_focus();
        // Going to a window, or to the widget standing in for it, answers its attention (WG15).
        if (auto active = wf::toplevel_cast(wf::get_core().seat->get_active_view()))
        {
            auto link = link_of_widget(active);
            clear_attention(link ? link->window_id : active->get_id());
        }
    };
    wf::signal::connection_t<wf::view_unmapped_signal> on_unmapped =
        [=] (wf::view_unmapped_signal *ev)
    {
        if (auto toplevel = wf::toplevel_cast(ev->view))
        {
            announced_scale.erase(toplevel->get_id());
            if (auto frame = frame_of(toplevel, false))
            {
                forget_owner(frame);
                frame->set_neighbors({});
            }

            // Tied lifecycles (WG5): the app's window closed takes its widget along; a widget
            // closed by the user closes the app's window (unless it's going because of a restore).
            if (auto link = link_of_window(toplevel))
            {
                set_hidden(*link, false);  // if it maps again, it's an ordinary window
                auto widget = wf::toplevel_cast(link->widget.lock());
                auto launcher = link->launcher;
                widget_links.erase(uint64_t(link->window_id));
                announce_widgets();
                close_view_or_process(widget, launcher);
            } else if (auto link = link_of_widget(toplevel))
            {
                if (link->dismissing || link->preview)  // a preview going is no reason to close the app
                {
                    widget_links.erase(uint64_t(link->window_id));
                    announce_widgets();
                } else
                {
                    link->widget.reset();
                    close_linked(*link);
                }
            }
        }

        // Neighbors are recomputed without it once it's gone from the stacking list.
        idle_neighbors.run_once([=] () { update_all_neighbors(); });
    };
    wf::wl_idle_call idle_neighbors;

    // Rail widgets (docs/widgets.md). A window dropped onto a widget rail is hidden (kept alive)
    // and a widget program is launched in its place; the widget's window, recognized by its
    // process, floats where the window was dropped at 100%. The two are tied: closing either
    // closes both; dragging the widget off the rail restores the window there.
    /** A widget's processes: its systemd scope (the launcher runs the widget in one, so ending
     *  it ends every process the widget started, escalating to SIGKILL), and as a fallback where
     *  there's no systemd, a pidfd on its first process, opened at launch so a recycled process
     *  id is never signalled. */
    struct widget_process_t
    {
        pid_t pid = 0;
        int pidfd = -1;
        std::string unit;
        ~widget_process_t()
        {
            if (pidfd >= 0)
            {
                ::close(pidfd);
            }
        }
    };
    using widget_process = std::shared_ptr<widget_process_t>;

    struct widget_link_t
    {
        uint64_t window_id = 0;
        std::weak_ptr<wf::view_interface_t> window;  // the app's window (hidden)
        std::weak_ptr<wf::view_interface_t> widget;  // the widget's window, once it maps
        widget_process launcher;                     // the widget's processes
        wf::output_t *output = nullptr;
        wf::pointf_t drop;                           // where the window was dropped (output coords)
        std::string rail;                            // "left" or "right"
        bool dismissing = false;                     // restoring the window: the widget just goes
        bool hidden = false;                         // holds a disable on the app's window
        bool preview = false;                        // launched during a drag over a rail (WG13): the
                                                     // window isn't hidden yet, the widget is kept unseen
        bool widget_hidden = false;                  // holds a disable on the widget's window
        uint32_t launched_at = 0;
    };

    std::map<uint64_t, widget_link_t> widget_links;  // by the app window's id
    std::set<uint64_t> wants_attention;              // windows demanding attention (urgency hint)
    std::set<uint64_t> asked_attention;              // windows that asked another way (WG15):
                                                     // activation requests, desktop notifications

    bool needs_attention(uint64_t window) const
    {
        return wants_attention.count(window) || asked_attention.count(window);
    }

    static wayfire_toplevel_view view_by_id(uint64_t id)
    {
        for (auto& view : wf::get_core().get_all_views())
        {
            if (view->get_id() == id)
            {
                return wf::toplevel_cast(view);
            }
        }

        return nullptr;
    }

    /** Show (or stop showing) that a window's app needs attention (WG15): on its halo, or on its
     *  widget's when it's a widget. Announced to IPC subscribers as scottland-attention#. */
    void show_attention(uint64_t window)
    {
        bool on   = needs_attention(window);
        auto link = widget_links.find(window);
        auto view = view_by_id(window);
        bool widgetized = (link != widget_links.end()) && !link->second.preview;
        if (view && view->is_mapped())
        {
            if (auto frame = frame_of(view, false))
            {
                frame->set_attention(on && !widgetized);
            }
        }

        if (link != widget_links.end())
        {
            if (auto widget = wf::toplevel_cast(link->second.widget.lock()))
            {
                if (auto frame = frame_of(widget))
                {
                    frame->set_attention(on && widgetized);
                }
            }

            announce_widgets();
        }

        wf::json_t event;
        event["window"]    = (int64_t)window;
        event["attention"] = on;
        send_ipc_event(event, "scottland-attention#");
    }

    /** The user went to it (focused its widget, or its window): attention is answered. */
    void clear_attention(uint64_t window)
    {
        bool had = needs_attention(window);
        wants_attention.erase(window);
        asked_attention.erase(window);
        if (had)
        {
            show_attention(window);
        }
    }

    // Apps asking to be focused (xdg-activation: a terminal's bell, a finished task). Scottland
    // implements the protocol itself (Wayfire's plugin drops requests not made from input, so a
    // bell never arrives): a request made from input in the app you're using (a click opening a
    // link in another app) takes focus; any other request doesn't take focus from you, it
    // becomes attention (WG15), shown on the app's widget. A widgetized app is never focused
    // this way: its window is hidden.
    //
    // The protocol object lives as long as the compositor; a reloaded plugin finds the one
    // already made (its address in this process's environment), as a second would be a second
    // global for apps to bind.
    wlr_xdg_activation_v1 *activation = nullptr;
    wf::wl_listener_wrapper on_activate;

    void start_activation()
    {
        static const char *KEY = "SCOTTLAND_INTERNAL_XDG_ACTIVATION";
        if (const char *found = getenv(KEY))
        {
            activation = reinterpret_cast<wlr_xdg_activation_v1*>(std::strtoull(found, nullptr, 16));
        }

        if (!activation)
        {
            activation = wlr_xdg_activation_v1_create(wf::get_core().display);
            if (!activation)
            {
                LOGE("scottland: couldn't offer xdg-activation");
                return;
            }

            char address[32];
            snprintf(address, sizeof(address), "%llx", (unsigned long long)(uintptr_t)activation);
            setenv(KEY, address, 1);
        }

        on_activate.set_callback([=] (void *data)
        {
            auto ev = static_cast<wlr_xdg_activation_v1_request_activate_event*>(data);
            auto view = wf::toplevel_cast(ev->surface ? wf::wl_surface_to_wayfire_view(ev->surface->resource) : nullptr);
            if (view && view->is_mapped())
            {
                handle_activation(view, ev->token);
            }
        });
        on_activate.connect(&activation->events.request_activate);
    }

    void handle_activation(wayfire_toplevel_view view, wlr_xdg_activation_token_v1 *token)
    {
        auto active = wf::get_core().seat->get_active_view();
        auto link = link_of_window(view);
        if ((active == view) || (link && active && (link->widget.lock().get() == active.get())))
        {
            return;  // already in front of the user (the window, or its widget)
        }

        bool from_input = token && token->seat && token->surface;
        auto source = from_input ? wf::wl_surface_to_wayfire_view(token->surface->resource) : nullptr;
        bool from_active_app = source && active && (source->get_client() == active->get_client());
        if ((!link || link->preview) && from_active_app)
        {
            wf::get_core().default_wm->focus_raise_view(view);
            return;
        }

        asked_attention.insert(view->get_id());
        show_attention(view->get_id());
    }

    // Requests from the desktop (a switcher) for a widgetized window go to its widget: its
    // window is hidden.
    wf::signal::connection_t<wf::view_focus_request_signal> on_focus_request =
        [=] (wf::view_focus_request_signal *ev)
    {
        auto view = wf::toplevel_cast(ev->view);
        auto link = view ? link_of_window(view) : nullptr;
        if (!link || link->preview || ev->carried_out)
        {
            return;
        }

        ev->carried_out = true;
        if (auto widget = link->widget.lock())
        {
            wf::get_core().default_wm->focus_raise_view(widget);
        }
    };

    /** IPC scottland/attention {window, attention}: the widget service saw the app ask for
     *  attention another way (a desktop notification, WG15). */
    wf::ipc::method_callback attention_method = [=] (wf::json_t data) -> wf::json_t
    {
        if (!data.has_member("window") || !data["window"].is_int() || !data.has_member("attention") ||
            !data["attention"].is_bool())
        {
            return wf::ipc::json_error("attention needs integer \"window\" and boolean \"attention\"");
        }

        uint64_t window = (uint64_t)data["window"].as_int64();
        if (data["attention"].as_bool())
        {
            auto active = wf::get_core().seat->get_active_view();
            auto link   = widget_links.find(window);
            if (active && ((active->get_id() == window) ||
                ((link != widget_links.end()) && (link->second.widget.lock().get() == active.get()))))
            {
                return wf::ipc::json_ok();  // already in front of the user (the window, or its widget)
            }

            asked_attention.insert(window);
            show_attention(window);
        } else
        {
            clear_attention(window);
        }

        return wf::ipc::json_ok();
    };

    /** Tell IPC subscribers (the widget service) that widgets changed. */
    /** Send a custom IPC event (names must end in '#') to subscribed clients. */
    void send_ipc_event(wf::json_t data, const std::string& name)
    {
        data["event"] = name;
        wf::ipc_rules::detail::custom_event_signal_t ev;
        ev.data = std::move(data);
        ipc_repo->emit(&ev);
    }

    void announce_widgets()
    {
        send_ipc_event(wf::json_t{}, "scottland-widgets#");
    }

    wf::signal::connection_t<wf::view_hints_changed_signal> on_hints =
        [=] (wf::view_hints_changed_signal *ev)
    {
        if (!ev->view)
        {
            return;
        }

        if (ev->demands_attention)
        {
            wants_attention.insert(ev->view->get_id());
        } else
        {
            wants_attention.erase(ev->view->get_id());
        }

        show_attention(ev->view->get_id());
    };
    wf::wl_timer<true> widget_watchdog;
    static constexpr uint32_t WIDGET_ADOPT_MS = 8000;  // no widget window by then: give up, restore

    /** The app's process. X11 windows all belong to the XWayland server's client, so theirs is the
     *  _NET_WM_PID they declare (X11 apps can already see each other; no stronger check exists). */
    static pid_t view_pid(wayfire_view view)
    {
        pid_t pid = 0;
        if (!view)
        {
            return pid;
        }

#if WF_HAS_XWAYLAND
        if (auto surface = view->get_wlr_surface())
        {
            if (auto xsurface = wlr_xwayland_surface_try_from_wlr_surface(surface))
            {
                return xsurface->pid;
            }
        }
#endif
        if (view->get_client())
        {
            wl_client_get_credentials(view->get_client(), &pid, nullptr, nullptr);
        }

        return pid;
    }

    /** Is `ancestor` the process `pid` or one of its ancestors (a few levels up)? */
    static bool descends_from(pid_t pid, pid_t ancestor)
    {
        for (int depth = 0; (pid > 1) && (depth < 8); depth++)
        {
            if (pid == ancestor)
            {
                return true;
            }

            std::ifstream stat("/proc/" + std::to_string(pid) + "/stat");
            std::string line;
            if (!std::getline(stat, line))
            {
                return false;
            }

            // pid (comm) state ppid ...: comm may contain spaces, so parse after the last ')'.
            auto close = line.rfind(')');
            if (close == std::string::npos)
            {
                return false;
            }

            std::istringstream rest(line.substr(close + 1));
            std::string state;
            rest >> state >> pid;
        }

        return false;
    }

    /** Is process `pid` in systemd scope `unit`? (A widget's processes stay in its scope even
     *  when a launcher forks and exits.) */
    static bool in_scope(pid_t pid, const std::string& unit)
    {
        if ((pid <= 1) || unit.empty())
        {
            return false;
        }

        std::ifstream cgroup("/proc/" + std::to_string(pid) + "/cgroup");
        std::string line;
        while (std::getline(cgroup, line))
        {
            if ((line.size() > unit.size()) && (line.compare(line.size() - unit.size() - 1, std::string::npos,
                "/" + unit) == 0))
            {
                return true;
            }
        }

        return false;
    }

    widget_link_t *link_of_window(wayfire_view view)
    {
        auto found = view ? widget_links.find(view->get_id()) : widget_links.end();
        return found == widget_links.end() ? nullptr : &found->second;
    }

    widget_link_t *link_of_widget(wayfire_view view)
    {
        for (auto& [id, link] : widget_links)
        {
            if (view && (link.widget.lock().get() == view.get()))
            {
                return &link;
            }
        }

        return nullptr;
    }

    bool is_widget(wayfire_view view)
    {
        return link_of_widget(view) != nullptr;
    }

    /** Hide the app's window behind its widget, or show it again. Wayfire counts disables (its
     *  own unmap is one too), so the link remembers whether it holds one and gives back exactly
     *  that: a window unmapped and remapped while widgetized isn't left hidden. */
    static void set_hidden(widget_link_t& link, bool hidden)
    {
        auto window = link.window.lock();
        if (!window || (link.hidden == hidden))
        {
            return;
        }

        wf::scene::set_node_enabled(window->get_root_node(), !hidden);
        link.hidden = hidden;
    }


    /** Widgets asked to go, ended if they're still running at their deadline. */
    struct ending_t
    {
        widget_process process;
        uint32_t deadline;
        bool terminated = false;  // SIGTERM sent: SIGKILL at the next deadline if it's still there
    };
    std::vector<ending_t> endings;
    wf::wl_timer<true> ending_timer;

    static bool alive(const widget_process& process)
    {
        return process && (process->pidfd >= 0) && (pidfd_send_signal(process->pidfd, 0, nullptr, 0) == 0);
    }

    void end_now(const widget_process& process)
    {
        if (!process)
        {
            return;
        }

        if (!process->unit.empty())
        {
            // Stopping a scope that already ended (the widget exited) does nothing.
            wf::get_core().run("systemctl --user stop --no-block " + shell_quote(process->unit) +
                " >/dev/null 2>&1");
        }

        if (process->pidfd >= 0)
        {
            pidfd_send_signal(process->pidfd, SIGTERM, nullptr, 0);  // fails harmlessly if it exited
        }
    }

    /** End a widget's processes after `delay_ms` (0: now). */
    void end_process(const widget_process& process, uint32_t delay_ms)
    {
        if (!process)
        {
            return;
        }

        if (delay_ms == 0)
        {
            end_now(process);
            delay_ms = 2000;  // then SIGKILL, if it ignored that (no scope to do it for us)
            endings.push_back({process, now_msec() + delay_ms, true});
        } else
        {
            endings.push_back({process, now_msec() + delay_ms});
        }

        if (!ending_timer.is_connected())
        {
            ending_timer.set_timeout(250, [=] () { return end_due_processes(false); });
        }
    }

    /** End the widgets past their deadline: SIGTERM (and their scope stopped), then SIGKILL two
     *  seconds later for a first process still there. With `all` (unloading), everything now,
     *  waiting at most half a second before the SIGKILLs. True while some wait. */
    bool end_due_processes(bool all)
    {
        auto now = now_msec();
        for (auto& e : endings)
        {
            if ((all || ((int32_t)(e.deadline - now) <= 0)) && !e.terminated)
            {
                end_now(e.process);
                e.terminated = true;
                e.deadline   = now + 2000;
            }
        }

        if (all)
        {
            for (int waited = 0; waited < 500; waited += 20)
            {
                if (std::none_of(endings.begin(), endings.end(), [] (auto& e) { return alive(e.process); }))
                {
                    break;
                }

                usleep(20 * 1000);
            }
        }

        endings.erase(std::remove_if(endings.begin(), endings.end(), [&] (const ending_t& e)
        {
            if (!all && (!e.terminated || ((int32_t)(e.deadline - now) > 0)))
            {
                return false;
            }

            if (alive(e.process))
            {
                pidfd_send_signal(e.process->pidfd, SIGKILL, nullptr, 0);
            }

            return true;
        }), endings.end());
        return !endings.empty();
    }

    /** Close a widget's window; one that won't go is ended. A widget that never showed a window
     *  (still launching) is ended now. */
    void close_view_or_process(wayfire_view view, const widget_process& process)
    {
        if (view)
        {
            view->close();
            end_process(process, 3000);  // a moment to close on its own first
        } else
        {
            end_process(process, 0);
        }
    }

    static bool output_alive(wf::output_t *output)
    {
        auto outputs = wf::get_core().output_layout->get_outputs();
        return output && (std::find(outputs.begin(), outputs.end(), output) != outputs.end());
    }

    std::string widget_launcher()
    {
        const char *hooks = getenv("SCOTTLAND_HOOKS");
        return std::string(hooks ? hooks : "/usr/lib/scottland") + "/libexec/scottland-widget-launch";
    }

    static std::string shell_quote(const std::string& text)
    {
        std::string out = "'";
        for (char c : text)
        {
            out += (c == '\'') ? std::string("'\\''") : std::string(1, c);
        }

        return out + "'";
    }

    /** The window was dropped on a rail: hide it and launch its widget. */
    static bool can_widgetize(wayfire_toplevel_view view)
    {
        return view && view->get_output() && !view->parent && !view->pending_fullscreen();
    }

    /** Turn the window into a widget. With `preview` (a drag is over a rail, WG13) the widget is
     *  launched but the window stays, and the widget is kept unseen until the drop commits. */
    void widgetize(wayfire_toplevel_view view, bool preview = false)
    {
        if (auto existing = link_of_window(view))
        {
            if (existing->preview && !preview)
            {
                auto g = view->get_geometry();
                commit_preview(*existing, {g.x + g.width / 2.0, g.y + g.height / 2.0});
            }

            return;
        }

        auto output = view->get_output();
        if (!can_widgetize(view) || is_widget(view))
        {
            return;
        }

        auto geometry = view->get_geometry();
        double width  = output->get_relative_geometry().width;
        widget_link_t link;
        link.window_id = view->get_id();
        link.window = view->weak_from_this();
        link.output = output;
        link.drop   = {geometry.x + geometry.width / 2.0, geometry.y + geometry.height / 2.0};
        link.rail   = link.drop.x < width / 2 ? "left" : "right";
        link.launched_at = now_msec();

        wf::json_t context;
        context["id"]     = std::to_string(link.window_id);
        context["window"] = (int64_t)link.window_id;
        context["app_id"] = view->get_app_id();
        context["title"]  = view->get_title();
        context["pid"]    = (int64_t)view_pid(view);
        context["rail"]   = link.rail;
        auto process = std::make_shared<widget_process_t>();
        const char *display = getenv("WAYLAND_DISPLAY");
        process->unit = "scottland-widget-" + std::string(display ? display : "wayland") + "-" +
            std::to_string(link.window_id) + "-" + std::to_string(link.launched_at) + ".scope";
        for (auto& c : process->unit)
        {
            c = (std::isalnum((unsigned char)c) || (c == '-') || (c == '.')) ? c : '_';
        }

        context["unit"] = process->unit;  // the launcher runs the widget in this scope
        process->pid = wf::get_core().run(shell_quote(widget_launcher()) + " " +
            shell_quote(context.serialize()));
        if (process->pid <= 0)
        {
            LOGE("scottland: couldn't launch a widget for ", view->get_app_id());
            return;
        }

        // Taken right away, while the process is certainly the one just started.
        process->pidfd = pidfd_open(process->pid, 0);
        link.launcher  = process;
        link.preview   = preview;

        if (!preview)
        {
            set_hidden(link, true);
            if (wf::get_core().seat->get_active_view() == view)
            {
                wf::get_core().seat->refocus();
            }
        }

        widget_links[link.window_id] = std::move(link);
        if (!preview)
        {
            announce_widgets();
        }

        if (!widget_watchdog.is_connected())
        {
            widget_watchdog.set_timeout(500, [=] () { return check_widget_launches(); });
        }
    }

    /** Widgets whose window never appeared: restore their app window. */
    bool check_widget_launches()
    {
        bool waiting = false;
        for (auto it = widget_links.begin(); it != widget_links.end();)
        {
            auto& link = it->second;
            if (!link.widget.lock() && (now_msec() - link.launched_at > WIDGET_ADOPT_MS))
            {
                LOGE("scottland: no widget window appeared for window ", link.window_id, "; restoring it");
                set_hidden(link, false);
                end_process(link.launcher, 0);  // a late widget would show up unlinked
                it = widget_links.erase(it);
                announce_widgets();
                continue;
            }

            waiting |= !link.widget.lock();
            ++it;
        }

        return waiting;
    }

    /** A window mapped: is it a widget we launched? Then place it where its app window was dropped. */
    bool adopt_widget(wayfire_toplevel_view view)
    {
        pid_t pid = view_pid(view);
        for (auto& [id, link] : widget_links)
        {
            if (link.widget.lock() || !link.launcher ||
                !(in_scope(pid, link.launcher->unit) ||
                  // Without a scope: descent from the launched process, while it still runs (its
                  // number could belong to someone else once it has exited).
                  (alive(link.launcher) && descends_from(pid, link.launcher->pid) && alive(link.launcher))))
            {
                continue;
            }

            link.widget = view->weak_from_this();
            set_scale(view, 1.0);
            if (link.preview)
            {
                // Unseen until the drop: the dragged window's frame shows it as it morphs.
                set_widget_hidden(link, true);
                if (auto window = wf::toplevel_cast(link.window.lock()))
                {
                    if (wf::get_core().seat->get_active_view() == view)
                    {
                        wf::get_core().default_wm->focus_raise_view(window);
                    }
                }

                return true;
            }

            auto output = output_alive(link.output) ? link.output : view->get_output();
            if (output && (view->get_output() != output))
            {
                wf::move_view_to_output(view, output, false);
            }

            place_widget(view, output, link.drop);
            set_scale(view, 1.0);
            show_attention(link.window_id);  // asked before its widget appeared
            return true;
        }

        return false;
    }

    static constexpr int WIDGET_INSET = (int)scottland::SWOLLEN + 3;  // room for the halo at its widest

    /** Center the widget on `at`, kept wholly on screen, halo included. */
    void place_widget(wayfire_toplevel_view view, wf::output_t *output, wf::pointf_t at)
    {
        auto geometry = view->get_geometry();
        auto area = output ? output->workarea->get_workarea() : geometry;
        int inset = WIDGET_INSET;
        area.x += inset;
        area.y += inset;
        area.width  -= 2 * inset;
        area.height -= 2 * inset;
        double x = std::clamp(at.x - geometry.width / 2.0, (double)area.x,
            std::max((double)area.x, (double)(area.x + area.width - geometry.width)));
        double y = std::clamp(at.y - geometry.height / 2.0, (double)area.y,
            std::max((double)area.y, (double)(area.y + area.height - geometry.height)));
        view->move(std::round(x), std::round(y));
    }

    /** Keep the widget's window unseen (a preview, or one going away), or show it. Its frame is
     *  made transparent too: a map animation can hold the window enabled for a moment. */
    void set_widget_hidden(widget_link_t& link, bool hidden)
    {
        auto widget = wf::toplevel_cast(link.widget.lock());
        if (!widget || (link.widget_hidden == hidden))
        {
            return;
        }

        if (auto frame = frame_of(widget))
        {
            frame->alpha = hidden ? 0.0 : 1.0;
            frame->damage();
        }

        wf::scene::set_node_enabled(widget->get_root_node(), !hidden);
        link.widget_hidden = hidden;
    }

    /** A drag over a rail ended there: the preview becomes the widget, at `at`. */
    void commit_preview(widget_link_t& link, wf::pointf_t at)
    {
        auto window = wf::toplevel_cast(link.window.lock());
        link.preview = false;
        link.drop    = at;
        if (window && window->get_output())
        {
            link.output = window->get_output();
            link.rail   = at.x < window->get_output()->get_relative_geometry().width / 2 ? "left" : "right";
        }

        set_hidden(link, true);
        show_attention(link.window_id);  // now shown on the widget, not the window
        if (auto widget = wf::toplevel_cast(link.widget.lock()))
        {
            auto output = output_alive(link.output) ? link.output : widget->get_output();
            if (output && (widget->get_output() != output))
            {
                wf::move_view_to_output(widget, output, false);
            }

            place_widget(widget, output, at);
            set_scale(widget, 1.0);
            set_widget_hidden(link, false);
            wf::get_core().default_wm->focus_raise_view(widget);
        } else if (window && (wf::get_core().seat->get_active_view() == window))
        {
            wf::get_core().seat->refocus();
        }

        announce_widgets();
    }

    /** A drag over a rail left it and ended elsewhere: the preview widget goes. */
    void cancel_preview(widget_link_t& link)
    {
        auto widget   = wf::toplevel_cast(link.widget.lock());
        auto launcher = link.launcher;
        link.dismissing = true;
        widget_links.erase(uint64_t(link.window_id));  // `link` is gone from here on
        close_view_or_process(widget, launcher);
    }

    /** Back to the window, at `at` (output coords); the widget goes (it isn't closed: WG5). */
    void restore_window(widget_link_t& link, std::optional<wf::pointf_t> at)
    {
        auto window = wf::toplevel_cast(link.window.lock());
        auto widget = wf::toplevel_cast(link.widget.lock());
        link.dismissing = true;
        if (window)
        {
            // Dropped on another screen: the window goes there.
            auto to = widget ? widget->get_output() : nullptr;
            if (at && to && (window->get_output() != to))
            {
                wf::move_view_to_output(window, to, false);
            }

            if (at)
            {
                auto g = window->get_geometry();
                window->move(std::round(at->x - g.width / 2.0), std::round(at->y - g.height / 2.0));
            }

            set_hidden(link, false);
            wf::get_core().default_wm->focus_raise_view(window);  // (going to it answers attention)
        }

        uint64_t id = link.window_id;
        auto launcher = link.launcher;
        set_widget_hidden(link, true);  // gone at once, not when its program gets round to closing
        close_view_or_process(widget, launcher);
        widget_links.erase(id);
        announce_widgets();
    }

    /** Close the app's window and its widget together (WG5). */
    void close_linked(widget_link_t& link)
    {
        auto window = wf::toplevel_cast(link.window.lock());
        auto widget = wf::toplevel_cast(link.widget.lock());
        auto launcher = link.launcher;
        // Shown again first: if the app asks before closing (unsaved work), the question is visible.
        set_hidden(link, false);
        widget_links.erase(uint64_t(link.window_id));  // `link` is gone from here on
        announce_widgets();
        close_view_or_process(widget, launcher);
        if (window)
        {
            window->close();
        }
    }

    /** A drop: windows dropped on a rail become widgets; widgets dropped off the rail restore.
     *  After a drag that morphed (WG13), the shape shown at the drop decides (`widget_shaped`). */
    void handle_widget_drop(wayfire_toplevel_view view, std::optional<bool> widget_shaped = {})
    {
        if (!view || !view->is_mapped() || !view->get_output())
        {
            return;
        }

        auto geometry = view->get_geometry();
        auto output   = view->get_output();
        double width  = output->get_relative_geometry().width;
        wf::pointf_t center{geometry.x + geometry.width / 2.0, geometry.y + geometry.height / 2.0};
        auto in_rail = [&] (double x) { return place_at(std::clamp(x, 0.0, width - 1), width).zone == zone_t::widget; };
        if (auto link = link_of_widget(view))
        {
            // A widget is wider than the rail and kept on screen, so its center can sit outside
            // the rail: it stays a widget while any of it (halo included) is on a rail, and
            // leaves when none is.
            double x1 = geometry.x - WIDGET_INSET, x2 = geometry.x + geometry.width + WIDGET_INSET;
            bool left  = (x1 < width / 2) && in_rail(x1);
            bool right = (x2 > width / 2) && in_rail(x2);
            if (widget_shaped ? !*widget_shaped : (!left && !right))
            {
                restore_window(*link, center);
                return;
            }

            if (!left && !right)
            {
                left = center.x < width / 2;  // kept a widget by the shape shown: its nearer rail
            }

            place_widget(view, output, center);
            auto rail = left ? "left" : "right";
            if ((link->rail != rail) || (link->output != output))
            {
                link->rail   = rail;
                link->output = output;
                announce_widgets();
            }

            auto placed = view->get_geometry();
            link->drop = {placed.x + placed.width / 2.0, placed.y + placed.height / 2.0};
        } else if (widget_shaped ? *widget_shaped : in_rail(center.x))
        {
            widgetize(view);
        } else if (auto link = link_of_window(view); link && link->preview)
        {
            cancel_preview(*link);
        }
    }

    wf::ipc::method_callback widgets_state = [=] (wf::json_t) -> wf::json_t
    {
        auto reply = wf::ipc::json_ok();
        wf::json_t list = wf::json_t::array();
        auto active = wf::get_core().seat->get_active_view();
        for (auto& [id, link] : widget_links)
        {
            if (link.preview)
            {
                continue;  // not a widget yet (WG13)
            }

            auto window = wf::toplevel_cast(link.window.lock());
            auto widget = wf::toplevel_cast(link.widget.lock());
            wf::json_t entry;
            entry["id"]     = std::to_string(id);
            entry["window"] = (int64_t)id;
            entry["widget_view"] = widget ? (int64_t)widget->get_id() : (int64_t)-1;
            entry["app_id"] = window ? window->get_app_id() : "";
            entry["title"]  = window ? window->get_title() : "";
            entry["pid"]    = (int64_t)(window ? view_pid(window) : 0);
            entry["widget_pid"] = (int64_t)(widget ? view_pid(widget) : 0);  // its window's process, once it has one
            entry["widget_unit"] = link.launcher ? link.launcher->unit : "";
            // Only while it runs (its pidfd says so): a number that may since have been reused is
            // no one's identity.
            entry["launcher_pid"] = (int64_t)(alive(link.launcher) ? link.launcher->pid : 0);
            entry["rail"]    = link.rail;
            entry["focused"] = widget && (active == widget);
            entry["urgent"]  = needs_attention(id);
            list.append(entry);
        }

        reply["widgets"] = list;
        return reply;
    };

    wf::ipc::method_callback widget_action = [=] (wf::json_t data) -> wf::json_t
    {
        if (!data.has_member("id") || !data["id"].is_string() || !data.has_member("action") ||
            !data["action"].is_string())
        {
            return wf::ipc::json_error("widget-action needs string \"id\" and \"action\"");
        }

        uint64_t id = 0;
        try {
            id = std::stoull(data["id"].as_string());
        } catch (...)
        {
            return wf::ipc::json_error("bad widget id");
        }

        auto found = widget_links.find(id);
        if (found == widget_links.end())
        {
            return wf::ipc::json_error("no such widget");
        }

        std::string action = data["action"].as_string();
        if (action == "restore")
        {
            restore_window(found->second, std::nullopt);
        } else if (action == "close")
        {
            close_linked(found->second);
        } else if (action == "focus")
        {
            if (auto widget = found->second.widget.lock())
            {
                wf::get_core().default_wm->focus_raise_view(widget);
            }
        } else
        {
            return wf::ipc::json_error("unknown action: " + action);
        }

        return wf::ipc::json_ok();
    };

    void handle_pressed(wayfire_toplevel_view view, scottland::handle_t h, int finger = -1)
    {
        using scottland::handle_t;
        if (scottland::is_corner(h))
        {
            // Corners resize around the center; dragging a corner outward grows the window.
            int sx = (h == handle_t::top_right || h == handle_t::bottom_right) ? 1 : -1;
            int sy = (h == handle_t::bottom_left || h == handle_t::bottom_right) ? 1 : -1;
            auto output = view->get_output();
            if (output && output_instance.count(output))
            {
                output_instance[output]->start(view, BTN_LEFT, sx, sy, finger);
            }
        } else if (h == handle_t::halo)
        {
            if (finger >= 0)
            {
                // Not move_request: for touch, the move plugin learns where the finger went down
                // only after this runs, and would grab the window at a stale position.
                start_touch_drag(view, finger);
            } else
            {
                wf::get_core().default_wm->move_request(view);
            }
        }
    }

    // Touchpad window gestures (L23, L24). A three-finger swipe moves the window under the pointer
    // through the same drag as Super+drag (Wayfire's move plugin finishes it, with live scaling); a
    // three-finger click-drag (clickfinger's middle button) resizes it around its center. A
    // three-finger click that doesn't move stays a middle click, replayed to the app on release.
    wf::option_wrapper_t<bool> touchpad_gestures{"scottland/touchpad_gestures"};
    bool swipe_moving = false;
    bool middle_pending  = false;
    bool middle_resizing = false;
    wf::pointf_t middle_origin;
    std::weak_ptr<wf::view_interface_t> middle_view;
    bool test_touchpad_pointers = false;  // scottland/test-input: count every pointer as a touchpad

    bool is_touchpad(wlr_input_device *device)
    {
        if (test_touchpad_pointers)
        {
            return true;
        }

        if (!device || !wlr_input_device_is_libinput(device))
        {
            return false;
        }

        auto handle = wlr_libinput_get_device_handle(device);
        return handle && (libinput_device_config_tap_get_finger_count(handle) > 0);
    }

    /** The window a touchpad gesture acts on: the framed window under the pointer. */
    wayfire_toplevel_view gesture_target()
    {
        auto view = wf::toplevel_cast(wf::get_core().get_cursor_focus_view());
        if (!view || !view->is_mapped() || view->pending_fullscreen() || !frame_of(view, false))
        {
            return nullptr;
        }

        return view;
    }

    void swipe_begin(uint32_t fingers)
    {
        if (!touchpad_gestures || (fingers != 3) || drag->view || swipe_moving)
        {
            return;
        }

        auto view = gesture_target();
        if (!view || !(view->get_allowed_actions() & wf::VIEW_ALLOW_MOVE))
        {
            return;
        }

        wf::get_core().default_wm->focus_raise_view(view);
        drag->set_pending_drag(wf::get_core().get_cursor_position());
        wf::move_drag::drag_options_t options;
        options.join_views = false;
        options.enable_snap_off = false;
        drag->start_drag(view, options);
        swipe_moving = true;
    }

    void swipe_update(double dx, double dy)
    {
        if (!swipe_moving)
        {
            return;
        }

        // Gestures don't move the pointer; the fingers carry both pointer and window.
        wf::get_core().warp_cursor(wf::get_core().get_cursor_position() + wf::pointf_t{dx, dy});
        drag->handle_motion(wf::get_core().get_cursor_position());
    }

    void swipe_end()
    {
        if (swipe_moving)
        {
            swipe_moving = false;
            drag->handle_input_released();
        }
    }

    wf::signal::connection_t<wf::input_event_signal<wlr_pointer_swipe_begin_event>> on_swipe_begin =
        [=] (wf::input_event_signal<wlr_pointer_swipe_begin_event> *ev) { swipe_begin(ev->event->fingers); };
    wf::signal::connection_t<wf::input_event_signal<wlr_pointer_swipe_update_event>> on_swipe_update =
        [=] (wf::input_event_signal<wlr_pointer_swipe_update_event> *ev)
    {
        swipe_update(ev->event->dx, ev->event->dy);
    };
    wf::signal::connection_t<wf::input_event_signal<wlr_pointer_swipe_end_event>> on_swipe_end =
        [=] (wf::input_event_signal<wlr_pointer_swipe_end_event>*) { swipe_end(); };

    void replay_middle_click()
    {
        auto seat = wf::get_core().get_current_seat();
        uint32_t time = now_msec();
        wlr_seat_pointer_notify_button(seat, time, BTN_MIDDLE, WL_POINTER_BUTTON_STATE_PRESSED);
        wlr_seat_pointer_notify_frame(seat);
        wlr_seat_pointer_notify_button(seat, time, BTN_MIDDLE, WL_POINTER_BUTTON_STATE_RELEASED);
        wlr_seat_pointer_notify_frame(seat);
    }

    wf::signal::connection_t<wf::input_event_signal<wlr_pointer_button_event>> on_touchpad_button =
        [=] (wf::input_event_signal<wlr_pointer_button_event> *ev)
    {
        if (!touchpad_gestures || (ev->event->button != BTN_MIDDLE) || !is_touchpad(ev->device))
        {
            return;
        }

        if (ev->event->state == WL_POINTER_BUTTON_STATE_PRESSED)
        {
            auto view = gesture_target();
            if (!view || !(view->get_allowed_actions() & wf::VIEW_ALLOW_RESIZE))
            {
                return;  // an ordinary middle click
            }

            // Hold the click until the fingers either move (resize) or lift (middle click).
            middle_pending = true;
            middle_origin  = wf::get_core().get_cursor_position();
            middle_view    = view->weak_from_this();
            ev->mode = wf::input_event_processing_mode_t::IGNORE;
        } else if (middle_pending)
        {
            middle_pending = false;
            ev->mode = wf::input_event_processing_mode_t::IGNORE;
            replay_middle_click();
        } else if (middle_resizing)
        {
            middle_resizing = false;  // the release reaches the resize grab, which ends it
        }
    };

    void check_middle_drag()
    {
        if (!middle_pending)
        {
            return;
        }

        auto cursor = wf::get_core().get_cursor_position();
        if (std::hypot(cursor.x - middle_origin.x, cursor.y - middle_origin.y) < 8.0)
        {
            return;
        }

        middle_pending = false;
        auto view   = wf::toplevel_cast(middle_view.lock());
        auto output = view ? view->get_output() : nullptr;
        if (view && output && output_instance.count(output) &&
            output_instance[output]->start(view, BTN_MIDDLE, 1, -1))
        {
            middle_resizing = true;
        }
    }

    // Long press to lift (L25). A finger held still on a window for lift_delay ms lifts it: the
    // app's touch is cancelled (it already had the touch: no delay for scrolling, rotating,
    // drawing), and the window follows the finger through Wayfire's drag, with live scaling, an
    // elastic bulge, a swollen halo and a synthesized pop. The move plugin ends the drag when the
    // finger lifts. Touches on the halo are the frame's (no wait); two or more fingers never lift.
    wf::option_wrapper_t<int> lift_delay{"scottland/lift_delay"};
    wf::option_wrapper_t<bool> sounds{"scottland/sounds"};
    static constexpr double HOLD_SLOP = 10.0;
    int hold_finger = -1;
    wf::pointf_t hold_origin;
    std::weak_ptr<wf::view_interface_t> hold_view;
    wf::wl_timer<false> hold_timer;
    std::weak_ptr<scottland::frame_t> lifted_frame;
    int lifted_finger = -1;
    std::optional<wf::pointf_t> drag_input_override;  // where a touch drag was grabbed (until it ends)
    std::string pop_sound;

    void cancel_hold()
    {
        hold_timer.disconnect();
        hold_finger = -1;
        hold_view.reset();
    }

    wf::signal::connection_t<wf::post_input_event_signal<wlr_touch_down_event>> on_touch_down =
        [=] (wf::post_input_event_signal<wlr_touch_down_event> *ev)
    {
        int finger = ev->event->touch_id;
        if ((hold_finger >= 0) || (lifted_finger >= 0) ||
            (wf::get_core().get_touch_state().fingers.size() != 1))
        {
            cancel_hold();  // a second finger: this is a multi-finger touch, never a lift
            end_touch_scroll(scroll_finger, false);
            return;
        }

        auto focus = wf::get_core().get_touch_focus(finger);
        if (!focus || dynamic_cast<scottland::frame_t*>(focus.get()))
        {
            return;  // nothing, or the halo (the frame handles it)
        }

        auto view = wf::toplevel_cast(wf::node_to_view(focus));
        if (!view || !view->is_mapped() || !frame_of(view, false) ||
            !(view->get_allowed_actions() & wf::VIEW_ALLOW_MOVE))
        {
            return;
        }

        start_touch_scroll(finger, view);
        hold_finger = finger;
        hold_origin = wf::get_core().get_touch_position(finger);
        hold_view   = view->weak_from_this();
        hold_timer.set_timeout(std::max(50, (int)lift_delay), [=] () { lift_held_window(); });
    };

    wf::signal::connection_t<wf::post_input_event_signal<wlr_touch_motion_event>> on_touch_motion =
        [=] (wf::post_input_event_signal<wlr_touch_motion_event> *ev)
    {
        if ((ev->event->touch_id == lifted_finger) && drag->view)
        {
            // The lifted window follows the finger.
            drag->handle_motion(wf::get_core().get_touch_position(lifted_finger));
            return;
        }

        touch_scroll_motion(ev->event->touch_id);
        if (ev->event->touch_id != hold_finger)
        {
            return;
        }

        auto at = wf::get_core().get_touch_position(hold_finger);
        if (std::hypot(at.x - hold_origin.x, at.y - hold_origin.y) > HOLD_SLOP)
        {
            cancel_hold();  // it moved: the touch is the app's (scroll, rotate, draw...)
        }
    };

    wf::signal::connection_t<wf::post_input_event_signal<wlr_touch_up_event>> on_touch_up =
        [=] (wf::post_input_event_signal<wlr_touch_up_event> *ev)
    {
        if (ev->event->touch_id == hold_finger)
        {
            cancel_hold();
        }

        end_touch_scroll(ev->event->touch_id, true);
        if (ev->event->touch_id == lifted_finger)
        {
            lifted_finger = -1;
            if (auto frame = lifted_frame.lock())
            {
                frame->drop();
            }

            lifted_frame.reset();
            // The move plugin ends the drag on the last finger up; finish it if nothing did.
            if (drag->view && wf::get_core().get_touch_state().fingers.empty())
            {
                drag->handle_input_released();
            }
        }
    };

    void lift_held_window()
    {
        int finger = hold_finger;
        auto view  = wf::toplevel_cast(hold_view.lock());
        cancel_hold();
        if (!view || !view->is_mapped() || drag->view ||
            !wf::get_core().get_touch_state().fingers.count(finger))
        {
            return;
        }

        auto frame = frame_of(view, false);
        if (!frame)
        {
            return;
        }

        end_touch_scroll(finger, false);
        // The app already has this touch: tell it to forget it.
        auto seat  = wf::get_core().get_current_seat();
        auto point = wlr_seat_touch_get_point(seat, finger);
        if (point && point->client)
        {
            wlr_seat_touch_notify_cancel(seat, point->client);
        }

        wf::get_core().default_wm->focus_raise_view(view);
        frame->lift();
        play_pop();
        lifted_frame = frame;
        start_touch_drag(view, finger);
    }

    /** Drag `view` with finger `finger`, grabbed exactly where the finger is. */
    void start_touch_drag(wayfire_toplevel_view view, int finger)
    {
        if (drag->view)
        {
            return;
        }

        lifted_finger = finger;
        auto at = wf::get_core().get_touch_position(finger);
        drag_input_override = at;
        drag->set_pending_drag(at);
        wf::move_drag::drag_options_t options;
        options.join_views = false;
        options.enable_snap_off = false;
        // Kept until the drag ends: the drag reports its start (on_drag_output) on the first motion.
        drag->start_drag(view, options);
    }

    /** The lift's sound, synthesized (not a sample): a "bloop", like a bubble. A soft sine whose
     *  pitch rises quickly, with a gentle attack, a touch of second harmonic for roundness, and
     *  no noise. Written once as a WAV in the runtime directory. */
    void synthesize_pop()
    {
        const char *runtime = getenv("XDG_RUNTIME_DIR");
        std::string dir = std::string(runtime ? runtime : "/tmp") + "/scottland";
        std::string mkdir = "mkdir -p '" + dir + "'";
        if (system(mkdir.c_str()) != 0)
        {
            return;
        }

        const int rate = 48000;
        const int count = rate * 140 / 1000;
        std::vector<int16_t> samples(count);
        double phase = 0.0;
        for (int i = 0; i < count; i++)
        {
            double t = (double)i / rate;
            double frequency = 380.0 + 620.0 * (1.0 - std::exp(-t / 0.018));  // rises like a bubble
            phase += 2.0 * M_PI * frequency / rate;
            double envelope = (1.0 - std::exp(-t / 0.004)) * std::exp(-t / 0.035);
            double tail = std::min(1.0, (count - i) / (rate * 0.01));          // click-free end
            double value = (std::sin(phase) + 0.15 * std::sin(2.0 * phase)) * envelope;
            samples[i] = (int16_t)std::clamp(value * 0.5 * tail * 32767.0, -32767.0, 32767.0);
        }

        pop_sound = dir + "/pop.wav";
        std::ofstream out(pop_sound, std::ios::binary);
        auto u32 = [&] (uint32_t v) { out.write((const char*)&v, 4); };
        auto u16 = [&] (uint16_t v) { out.write((const char*)&v, 2); };
        uint32_t data_bytes = count * 2;
        out.write("RIFF", 4); u32(36 + data_bytes); out.write("WAVE", 4);
        out.write("fmt ", 4); u32(16); u16(1); u16(1); u32(rate); u32(rate * 2); u16(2); u16(16);
        out.write("data", 4); u32(data_bytes);
        out.write((const char*)samples.data(), data_bytes);
    }

    void play_pop()
    {
        if (!sounds || pop_sound.empty())
        {
            return;
        }

        wf::get_core().run("pw-play '" + pop_sound + "' 2>/dev/null || paplay '" + pop_sound +
            "' 2>/dev/null || aplay -q '" + pop_sound + "' 2>/dev/null");
    }

    // Touch as pointer (L26). Apps that ignore touch (terminals, mostly) get a finger's pointer
    // equivalent: a one-finger drag scrolls smoothly, with momentum after a flick, and a quick tap
    // clicks. Which apps: [scottland] touch_scroll_<name> = <app-id regex>, shipped for common
    // ones and set, added to or emptied in the user's overrides. Long press still lifts the window.
    wf::option_wrapper_t<wf::config::compound_list_t<std::string>> touch_scroll_apps{"scottland/touch_scroll"};
    std::unique_ptr<virtual_pointer_t> touch_pointer;
    int scroll_finger = -1;
    bool scroll_moved = false;
    wf::pointf_t scroll_origin, scroll_last;
    uint32_t scroll_down_time = 0, scroll_last_time = 0;
    wf::pointf_t scroll_velocity{0, 0};  // px per ms, smoothed
    wf::wl_timer<true> momentum;

    bool wants_touch_scroll(wayfire_view view)
    {
        if (!view)
        {
            return false;
        }

        std::string app = view->get_app_id();
        for (const auto& [name, pattern] : touch_scroll_apps.value())
        {
            if (pattern.empty())
            {
                continue;  // an emptied entry: the user switched a default off
            }

            try {
                if (std::regex_search(app, std::regex(pattern, std::regex::icase)))
                {
                    return true;
                }
            } catch (const std::regex_error&)
            {
                LOGE("scottland: bad touch_scroll_", name, " regex: ", pattern);
            }
        }

        return false;
    }

    void start_touch_scroll(int finger, wayfire_view view)
    {
        if (!wants_touch_scroll(view))
        {
            return;
        }

        if (!touch_pointer)
        {
            touch_pointer = std::make_unique<virtual_pointer_t>();
        }

        momentum.disconnect();
        scroll_finger  = finger;
        scroll_moved   = false;
        scroll_origin  = scroll_last = wf::get_core().get_touch_position(finger);
        scroll_down_time = scroll_last_time = now_msec();
        scroll_velocity  = {0, 0};
        touch_pointer->move_to(scroll_origin);  // the pointer goes where the finger is
    }

    void touch_scroll_motion(int finger)
    {
        if (finger != scroll_finger)
        {
            return;
        }

        auto at = wf::get_core().get_touch_position(finger);
        if (!scroll_moved && (std::hypot(at.x - scroll_origin.x, at.y - scroll_origin.y) < HOLD_SLOP))
        {
            return;  // not yet a scroll: still a tap, or a long press to lift
        }

        scroll_moved = true;
        uint32_t now = now_msec();
        double dt = std::max(1u, now - scroll_last_time);
        wf::pointf_t d = {at.x - scroll_last.x, at.y - scroll_last.y};
        // Direct manipulation: the content follows the finger.
        touch_pointer->scroll(-d.x, -d.y);
        scroll_velocity = {0.6 * (-d.x / dt) + 0.4 * scroll_velocity.x, 0.6 * (-d.y / dt) + 0.4 * scroll_velocity.y};
        scroll_last = at;
        scroll_last_time = now;
    }

    void end_touch_scroll(int finger, bool lifted_off)
    {
        if ((finger < 0) || (finger != scroll_finger) || !touch_pointer)
        {
            return;
        }

        scroll_finger = -1;
        if (!lifted_off)
        {
            touch_pointer->scroll(0, 0);  // stop
            return;
        }

        if (!scroll_moved)
        {
            if (now_msec() - scroll_down_time < 300)
            {
                touch_pointer->click(BTN_LEFT);
            }

            return;
        }

        // A flick keeps going and eases out; a finger that stopped before lifting doesn't.
        if ((now_msec() - scroll_last_time > 80) || (std::hypot(scroll_velocity.x, scroll_velocity.y) < 0.2))
        {
            touch_pointer->scroll(0, 0);
            return;
        }

        momentum.set_timeout(16, [=] ()
        {
            touch_pointer->scroll(scroll_velocity.x * 16, scroll_velocity.y * 16);
            scroll_velocity = {scroll_velocity.x * 0.94, scroll_velocity.y * 0.94};
            if (std::hypot(scroll_velocity.x, scroll_velocity.y) < 0.03)
            {
                touch_pointer->scroll(0, 0);
                return false;
            }

            return true;
        });
    }

    // Tests can't produce real touchpad gestures: this feeds the same handlers synthetic ones.
    wf::ipc::method_callback test_input = [=] (wf::json_t data) -> wf::json_t
    {
        if (data.has_member("touchpad_pointers"))
        {
            test_touchpad_pointers = data["touchpad_pointers"].as_bool();
        }

        if (data.has_member("swipe") && data["swipe"].is_string())
        {
            std::string phase = data["swipe"].as_string();
            if (phase == "begin")
            {
                swipe_begin(data.has_member("fingers") ? (uint32_t)data["fingers"].as_int() : 3);
            } else if (phase == "update")
            {
                swipe_update(data.has_member("dx") ? data["dx"].as_double() : 0.0,
                    data.has_member("dy") ? data["dy"].as_double() : 0.0);
            } else if (phase == "end")
            {
                swipe_end();
            }
        }

        auto reply = wf::ipc::json_ok();
        reply["swipe_moving"]    = swipe_moving;
        reply["middle_pending"]  = middle_pending;
        reply["middle_resizing"] = middle_resizing;
        reply["hold_armed"] = hold_finger >= 0;
        reply["lifted"]     = lifted_finger >= 0;
        reply["dragging"]   = (bool)drag->view;
        reply["drag_center"] = last_drag_center;
        return reply;
    };

    wf::signal::connection_t<wf::post_input_event_signal<wlr_pointer_motion_event>> on_motion =
        [=] (auto) { check_middle_drag(); track_pointer(); };
    wf::signal::connection_t<wf::post_input_event_signal<wlr_pointer_motion_absolute_event>> on_motion_abs =
        [=] (auto) { check_middle_drag(); track_pointer(); };
    wf::signal::connection_t<wf::post_input_event_signal<wlr_pointer_button_event>> on_button =
        [=] (wf::post_input_event_signal<wlr_pointer_button_event> *ev)
    {
        if (ev->event->state == WL_POINTER_BUTTON_STATE_RELEASED)
        {
            if (auto owner = pointer_owner.lock())
            {
                owner->release();
            }

            track_pointer();
        }
    };

    void apply_all()
    {
        for (auto& view : wf::get_core().get_all_views())
        {
            apply(view);
        }
    }

    // Live scaling while a window is dragged. The drag keeps the grabbed point of the window's
    // (transformed) bounding box under the pointer, so rescaling mid-drag stays anchored there.
    wf::shared_data::ref_ptr_t<wf::move_drag::core_drag_t> drag;
    double drag_relative_x = 0.5;   // where across the dragged window's bounding box it was grabbed
    double drag_margin = 0.0;       // bounding box minus window, per side (halo margin), on screen
    double drag_target = 1.0;     // the scale the dragged window is heading for
    double last_drag_center = 0;  // where the dragged window's center is shown (output coords)

    wf::signal::connection_t<wf::move_drag::drag_focus_output_signal> on_drag_output =
        [=] (wf::move_drag::drag_focus_output_signal *ev)
    {
        if (!ev->previous_focus_output && drag->view)
        {
            // Drag just began: remember where across the window it was grabbed.
            // The drag keeps the grabbed point at the same fraction of the view's bounding box (which
            // includes the halo margin), so measure it the same way, or the predicted center (and
            // zone, and scale) drifts from where the window really lands.
            auto output = drag->view->get_output();
            auto cursor = drag_input_override.value_or(wf::get_core().get_cursor_position());
            double local_x = cursor.x - (output ? output->get_layout_geometry().x : 0);
            // Use the window's resting box (its scaled width plus the halo margin), not the live
            // bounding box: a just-lifted window is mid-bulge, which inflates the box for a moment.
            auto geometry = drag->view->get_geometry();
            double drawn  = geometry.width * displayed_scale(drag->view);
            auto frame    = frame_of(drag->view, false);
            drag_margin   = frame ? frame->margin() :
                std::max(0.0, (drag->view->get_bounding_box().width - drawn) / 2.0);
            double box   = drawn + 2 * drag_margin;
            double left  = geometry.x + geometry.width / 2.0 - box / 2.0;
            drag_relative_x = box > 0 ? (local_x - left) / box : 0.5;
            drag_origin = {geometry.x, geometry.y};  // where Esc sends it back (WG14)
            drag_cancelled = false;
            auto running = transitions.find(drag->view->get_id());
            drag_target = running != transitions.end() ? running->second.animation.end : displayed_scale(drag->view);
            update_neighbors(output);
        }
    };

    // Live morph (WG13): while a window is dragged over a widget rail it turns into its widget
    // (the frame reshapes to the widget's size while the contents cross-fade to the widget's), and
    // a widget dragged off its rail turns back into its window the same way; the drop keeps the
    // shape shown. The widget is launched (unseen) when the drag first reaches the rail; until
    // it shows, the frame reshapes around the window's own contents.
    static constexpr int MORPH_MS = 240;
    static constexpr double PROVISIONAL_WIDGET_W = 300, PROVISIONAL_WIDGET_H = 96;
    struct drag_morph_t
    {
        std::weak_ptr<wf::view_interface_t> dragged;
        bool from_widget = false;  // dragging a widget: its other form is its app's window
        bool toward = false;       // heading for the other form
        double center_x = 0;       // where the dragged frame is centered (output coords)
        wf::animation::simple_animation_t shape{wf::create_option<int>(MORPH_MS)};
        wf::animation::simple_animation_t fade{wf::create_option<int>(MORPH_MS * 3 / 4)};
        std::shared_ptr<wf::auxilliary_buffer_t> snapshot = std::make_shared<wf::auxilliary_buffer_t>();
        wf::geometry_t snapshot_box{}, other_geometry{};
        bool snapshot_ready = false;
        int ticks = 0;
    };
    std::optional<drag_morph_t> morph;
    wf::wl_timer<true> morph_tick;

    /** Is the dragged view showing its widget shape (or heading there)? */
    bool morph_widget_shaped() const
    {
        return morph && (morph->from_widget ? !morph->toward : morph->toward);
    }

    wayfire_toplevel_view morph_other()
    {
        auto dragged = wf::toplevel_cast(morph ? morph->dragged.lock() : nullptr);
        if (!dragged)
        {
            return nullptr;
        }

        if (morph->from_widget)
        {
            auto link = link_of_widget(dragged);
            return link ? wf::toplevel_cast(link->window.lock()) : nullptr;
        }

        auto link = link_of_window(dragged);
        return link ? wf::toplevel_cast(link->widget.lock()) : nullptr;
    }

    void end_morph()
    {
        if (!morph)
        {
            return;
        }

        if (auto dragged = wf::toplevel_cast(morph->dragged.lock()))
        {
            if (auto frame = frame_of(dragged, false))
            {
                frame->damage();
                frame->morph = {};
                frame->damage();
            }
        }

        morph.reset();
        morph_tick.disconnect();
    }

    /** Follow the drag: decide which form it should show, and start the widget if it's needed. */
    void update_drag_morph(wayfire_toplevel_view view, wf::output_t *output, wf::pointf_t pointer)
    {
        bool dragging_widget = is_widget(view);
        if (!morph || (morph->dragged.lock().get() != view.get()))
        {
            end_morph();
            if (!dragging_widget && !can_widgetize(view))
            {
                return;
            }

            morph.emplace();
            morph->dragged     = view->weak_from_this();
            morph->from_widget = dragging_widget;
            morph->shape.set(0, 0);
            morph->fade.set(0, 0);
        }

        auto frame = frame_of(view, false);
        if (!frame)
        {
            return;
        }

        // Where the frame is on screen: the drag keeps the grabbed point at the same fraction of the
        // view's box (the frame plus its halo margin) under the pointer.
        auto r = frame->screen_rect();
        double margin = frame->margin();
        double box    = r.width() + 2 * margin;
        double origin = output->get_layout_geometry().x;
        double width  = output->get_relative_geometry().width;
        double x1 = pointer.x - origin - drag_relative_x * box + margin;
        double x2 = x1 + r.width();
        morph->center_x = (x1 + x2) / 2.0;
        auto in_rail = [&] (double x) { return place_at(std::clamp(x, 0.0, width - 1), width).zone == zone_t::widget; };

        // The same rules as a drop: the widget shape stays while any of it (halo included) is on a
        // rail; the window shape turns into a widget when its center reaches one.
        bool want_widget;
        if (morph_widget_shaped())
        {
            double a = x1 - WIDGET_INSET, b = x2 + WIDGET_INSET;
            want_widget = ((a < width / 2) && in_rail(a)) || ((b > width / 2) && in_rail(b));
        } else
        {
            want_widget = in_rail(morph->center_x);
        }

        bool toward = morph->from_widget ? !want_widget : want_widget;
        if (toward != morph->toward)
        {
            if (!morph->from_widget && toward)
            {
                widgetize(view, true);  // launched now, unseen, so it's ready to fade in
            }

            morph->toward = toward;
            morph->shape.animate(morph->shape, toward ? 1.0 : 0.0);
        }

        if (!morph_tick.is_connected())
        {
            morph_tick.set_timeout(8, [=] () { return step_morph(); });
        }
    }

    bool step_morph()
    {
        if (!morph)
        {
            return false;
        }

        auto dragged = wf::toplevel_cast(morph->dragged.lock());
        auto frame   = dragged && dragged->is_mapped() ? frame_of(dragged, false) : nullptr;
        if (!frame)
        {
            end_morph();
            return false;
        }

        // The other form's contents, live: it isn't on screen, so it isn't asked to draw; ask it to
        // (frame callbacks), and take a fresh snapshot every other tick. Peeking at a widget's
        // window shows what the app shows now, not what it showed when it became a widget.
        auto other = morph_other();
        if (other && other->is_mapped())
        {
            if (auto surface = other->get_wlr_surface())
            {
                timespec now;
                clock_gettime(CLOCK_MONOTONIC, &now);
                wlr_surface_for_each_surface(surface, [] (wlr_surface *s, int, int, void *data)
                {
                    wlr_surface_send_frame_done(s, static_cast<timespec*>(data));
                }, &now);
            }
        }

        if (other && other->is_mapped() && other->get_output() && (!morph->snapshot_ready || (morph->ticks % 2 == 0)))
        {
            other->take_snapshot(*morph->snapshot);
            morph->snapshot_box   = other->get_surface_root_node()->get_bounding_box();
            morph->other_geometry = other->get_geometry();
            morph->snapshot_ready = morph->snapshot->get_buffer() != nullptr;
        }

        morph->ticks++;
        if (!morph->toward && (drag->view != dragged) && !morph->shape.running() && !morph->fade.running())
        {
            end_morph();  // a cancelled drag finished morphing back
            return false;
        }

        double fade_to = (morph->toward && morph->snapshot_ready && other) ? 1.0 : 0.0;
        if (std::abs(morph->fade.end - fade_to) > 0.001)
        {
            morph->fade.animate(morph->fade, fade_to);
        }

        // The other form's size on screen: the widget at 100%, or the window at the scale it would
        // have where the frame is now.
        double w = PROVISIONAL_WIDGET_W, h = PROVISIONAL_WIDGET_H, scale = 1.0;
        if (morph->from_widget && other)
        {
            auto output = dragged->get_output();
            scale = output ? place_at(morph->center_x, output->get_relative_geometry().width).scale : 1.0;
            auto g = other->get_geometry();
            w = g.width * scale;
            h = g.height * scale;
        } else if (other)
        {
            auto g = other->get_geometry();
            w = g.width;
            h = g.height;
        }

        frame->damage();
        frame->morph.shape = morph->shape;
        frame->morph.fade  = morph->fade;
        frame->morph.w     = w;
        frame->morph.h     = h;
        frame->morph.scale = scale;
        frame->morph.snapshot       = morph->snapshot_ready ? morph->snapshot : nullptr;
        frame->morph.snapshot_box   = morph->snapshot_box;
        frame->morph.other_geometry = morph->other_geometry;
        frame->damage();
        dragged->damage();  // through the drag's own transform, so it repaints with the pointer still
        return true;
    }

    // Esc cancels a drag (WG14): the window goes back where it was picked up, gliding from where
    // it was let go, and a drag that changed it into its other form (window/widget) morphs back.
    wf::point_t drag_origin{0, 0};
    bool drag_cancelled = false;
    static constexpr int GLIDE_MS = 260;
    struct glide_t
    {
        std::weak_ptr<wf::view_interface_t> view;
        double dx = 0, dy = 0;  // where it's drawn from, relative to where it is
        wf::animation::simple_animation_t progress{wf::create_option<int>(GLIDE_MS)};
    };
    std::optional<glide_t> glide;
    wf::wl_timer<true> glide_tick;

    wf::signal::connection_t<wf::input_event_signal<wlr_keyboard_key_event>> on_cancel_key =
        [=] (wf::input_event_signal<wlr_keyboard_key_event> *ev)
    {
        if ((ev->event->keycode == KEY_ESC) && (ev->event->state == WL_KEYBOARD_KEY_STATE_PRESSED) &&
            drag->view && !drag_cancelled)
        {
            drag_cancelled = true;
            ev->mode = wf::input_event_processing_mode_t::IGNORE;  // the app under it doesn't get it
            drag->handle_input_released();
        }
    };

    /** The drag was cancelled and just let go: put the window back, drawn gliding home. */
    void cancel_drop(wayfire_toplevel_view view)
    {
        auto g = view->get_geometry();
        double dx = g.x - drag_origin.x, dy = g.y - drag_origin.y;
        view->move(drag_origin.x, drag_origin.y);
        if (morph && (morph->dragged.lock().get() == view.get()) && morph->toward)
        {
            morph->toward = false;  // back to the form it had
            morph->shape.animate(morph->shape, 0.0);
        }

        if (auto link = link_of_window(view); link && link->preview)
        {
            cancel_preview(*link);
        }

        apply(view);
        if (auto frame = frame_of(view, false); frame && ((dx != 0) || (dy != 0)))
        {
            glide.emplace();
            glide->view = view->weak_from_this();
            glide->dx = dx;
            glide->dy = dy;
            glide->progress.animate(0.0, 1.0);
            frame->translation_x = dx;
            frame->translation_y = dy;
            view->damage();
            if (!glide_tick.is_connected())
            {
                glide_tick.set_timeout(8, [=] () { return step_glide(); });
            }
        }
    }

    bool step_glide()
    {
        auto view  = glide ? wf::toplevel_cast(glide->view.lock()) : nullptr;
        auto frame = view ? frame_of(view, false) : nullptr;
        if (!frame)
        {
            glide.reset();
            return false;
        }

        double left = 1.0 - (double)glide->progress;
        frame->damage();
        frame->translation_x = glide->dx * left;
        frame->translation_y = glide->dy * left;
        frame->damage();
        view->damage();
        // The halos it passes over are cut where it's in front of them: follow it home, or the
        // cut stays where it was let go.
        update_neighbors(view->get_output());
        if (!glide->progress.running())
        {
            frame->translation_x = frame->translation_y = 0;
            glide.reset();
            update_all_neighbors();
            return false;
        }

        return true;
    }

    wf::signal::connection_t<wf::move_drag::drag_motion_signal> on_drag_motion =
        [=] (wf::move_drag::drag_motion_signal *ev)
    {
        auto view   = drag->view;
        auto output = drag->current_output;
        if (view && output && !view->pending_fullscreen())
        {
            update_drag_morph(view, output, ev->current_position);
        }

        if (!view || !output || view->pending_fullscreen() || is_widget(view))
        {
            return;  // widgets stay at 100% wherever they're dragged
        }

        if (morph && (morph->dragged.lock().get() == view.get()) && morph->toward)
        {
            // Shown as its widget: the window keeps the scale of where it is, for if it's dragged
            // back out.
            set_scale(view, place_at(morph->center_x, output->get_relative_geometry().width).scale);
            return;
        }

        // The drag keeps the grabbed point under the pointer, so the window's center (which picks
        // its zone) depends on its size, and its size on the zone. Choose a scale that agrees
        // with the center it produces. Where none does (right at a jump in scale, e.g. from the
        // center zone's 100% into a curve starting lower), keep the current one until the
        // pointer has moved far enough for the other to agree: otherwise the window flips
        // between the two sizes on every motion. Sizes are the targets, not the animated ones.
        double unscaled  = view->get_geometry().width;
        double origin_x  = output->get_layout_geometry().x;
        double pointer_x = ev->current_position.x - origin_x;
        double screen    = output->get_relative_geometry().width;

        // Where the window's center is shown, read from the drag itself rather than predicted. The
        // move tool draws the window in a box sized from the view's own box (this frame plus its
        // halo margin) divided by its zoom, placed so the grab sits at a fixed fraction of it.
        // Measure that box, the grab fraction and the margins now; then the center at any scale s
        // follows exactly: the box grows by unscaled * ds, around the grab.
        std::function<double(double)> center_at = [&] (double s)
        {
            return pointer_x + (0.5 - drag_relative_x) * (unscaled * s + 2 * drag_margin);
        };
        auto drag_box = view->get_transformed_node()->get_transformer<wf::scene::transformer_base_node_t>(
            "move-drag-transformer");
        auto frame = frame_of(view, false);
        if (drag_box && frame)
        {
            auto shown = drag_box->get_bounding_box();           // where it's drawn (layout coords)
            auto inner = drag_box->get_children_bounding_box();  // the view's own box
            if ((shown.width > 0) && (inner.width > 0))
            {
                double zoom  = inner.width / shown.width;          // the drag's own scale-down
                double grab  = (ev->current_position.x - shown.x) / shown.width;
                auto rect    = frame->screen_rect();
                double now_w = rect.width();
                double left  = (rect.x1 + rect.x2) / 2.0 - now_w / 2.0 - inner.x;     // margin left
                double right = inner.x + inner.width - (rect.x1 + rect.x2) / 2.0 - now_w / 2.0;
                double bulge = displayed_scale(view) > 0 ? now_w / (unscaled * displayed_scale(view)) : 1.0;
                center_at = [=] (double s)
                {
                    double width = unscaled * s * bulge;
                    double box   = (width + left + right) / zoom;
                    double box_x = ev->current_position.x - grab * box;
                    return box_x + (left + width / 2.0) / zoom - origin_x;
                };
            }
        }

        auto zone_scale = [&] (double s) { return place_at(center_at(s), screen).scale; };
        last_drag_center = center_at(drag_target);

        double chosen = zone_scale(drag_target);
        if (std::abs(chosen - drag_target) > 0.001)
        {
            // Settle on a self-consistent scale starting from the zone's answer, if there is one.
            bool consistent = false;
            for (int i = 0; i < 16; i++)
            {
                double next = zone_scale(chosen);
                if (std::abs(next - chosen) < 0.002)
                {
                    consistent = true;
                    chosen = next;
                    break;
                }

                chosen = next;
            }

            if (consistent)
            {
                drag_target = chosen;
            }
        }

        set_scale(view, drag_target);
    };

    wf::signal::connection_t<wf::move_drag::drag_done_signal> on_drag_done =
        [=] (wf::move_drag::drag_done_signal *ev)
    {
        // The dropped geometry is final; drag->view may still point at the view here.
        // What you saw while dragging is what you get: right at a jump in scale (the drag keeps
        // its size there until the other one agrees, see on_drag_motion) the drop can land where
        // the zone says the other size. Then keep the size shown, and nudge the window sideways
        // by the least distance that puts its center where that size belongs.
        auto main = ev->main_view;
        if (drag_cancelled)
        {
            drag_cancelled = false;
            drag_input_override.reset();
            if (main && main->is_mapped())
            {
                cancel_drop(main);
            }

            idle_neighbors.run_once([=] () { update_all_neighbors(); });
            return;
        }

        std::optional<bool> widget_shaped;  // the shape a morphing drag showed at the drop (WG13)
        if (morph && main && (morph->dragged.lock().get() == main.get()))
        {
            widget_shaped = morph_widget_shaped();
        }

        end_morph();
        if (main && main->is_mapped() && main->get_output() && !main->pending_fullscreen() && !is_widget(main) &&
            !widget_shaped.value_or(false))
        {
            auto geometry = main->get_geometry();
            double screen = main->get_output()->get_relative_geometry().width;
            double center = geometry.x + geometry.width / 2.0;
            if (std::abs(place_at(center, screen).scale - drag_target) > JUMP)
            {
                for (int d = 1; d <= 400; d++)
                {
                    int found = 0;
                    for (int sign : {-1, 1})
                    {
                        if (std::abs(place_at(center + sign * d, screen).scale - drag_target) <= 0.003)
                        {
                            found = sign;
                            break;
                        }
                    }

                    if (found)
                    {
                        main->move(geometry.x + found * d, geometry.y);
                        break;
                    }
                }
            }
        }

        for (auto& dragged : ev->all_views)
        {
            if (dragged.view && dragged.view->is_mapped())
            {
                set_scale(dragged.view, is_widget(dragged.view) ? 1.0 : placement_of(dragged.view).scale);
            }
        }

        drag_input_override.reset();
        if (main)
        {
            handle_widget_drop(main, widget_shaped);
        }

        // The dropped window rejoins its neighbors' liquid once the drag has let go of it.
        idle_neighbors.run_once([=] () { update_all_neighbors(); });
    };

    wf::signal::connection_t<wf::view_mapped_signal> on_mapped = [=] (wf::view_mapped_signal *ev)
    {
        if (auto toplevel = wf::toplevel_cast(ev->view))
        {
            if (!widget_links.empty())
            {
                adopt_widget(toplevel);
            }
        }

        apply(ev->view);
        idle_neighbors.run_once([=] () { update_focus(); });
    };

    wf::signal::connection_t<wf::view_geometry_changed_signal> on_geometry =
        [=] (wf::view_geometry_changed_signal *ev)
    {
        if (auto view = wf::toplevel_cast(ev->view))
        {
            if (auto frame = frame_of(view, false))
            {
                frame->damage_previous(ev->old_geometry);
                frame->damage();
            }

            update_neighbors(view->get_output());

            // A widget that changes size (a card whose title changed) keeps its screen-edge side
            // where it was and stays wholly on screen.
            auto g = view->get_geometry();
            auto link = link_of_widget(view);
            if (link && !link->preview && (drag->view != view) && view->get_output() &&
                ((g.width != ev->old_geometry.width) || (g.height != ev->old_geometry.height)))
            {
                auto old = ev->old_geometry;
                double cx = link->rail == "right" ? old.x + old.width - g.width / 2.0 : old.x + g.width / 2.0;
                double cy = old.y + old.height / 2.0;
                place_widget(view, view->get_output(), {cx, cy});
                auto placed = view->get_geometry();
                link->drop = {placed.x + placed.width / 2.0, placed.y + placed.height / 2.0};
            }
        }

        apply(ev->view);
    };

    wf::signal::connection_t<wf::view_set_output_signal> on_output = [=] (wf::view_set_output_signal *ev)
    {
        apply(ev->view);
    };

    wf::ipc::method_callback layout_state = [=] (wf::json_t) -> wf::json_t
    {
        wf::json_t reply = wf::ipc::json_ok();
        wf::json_t views = wf::json_t::array();
        for (auto& any_view : wf::get_core().get_all_views())
        {
            auto view = wf::toplevel_cast(any_view);
            if (!view || !view->is_mapped())
            {
                continue;
            }

            auto placement = placement_of(view);
            wf::json_t entry;
            entry["id"]    = (int64_t)view->get_id();
            entry["title"] = view->get_title();
            entry["app_id"] = view->get_app_id();
            entry["widget"] = is_widget(view);
            auto link = link_of_window(view);
            entry["widgetized"] = link && !link->preview;
            entry["preview"] = (link && link->preview) || (link_of_widget(view) && link_of_widget(view)->preview);
            entry["hidden"] = !view->get_root_node()->is_enabled();
            entry["zone"]  = zone_name(placement.zone);
            entry["scale"] = placement.scale;
            auto transformer = view->get_transformed_node()->get_transformer<
                wf::scene::view_2d_transformer_t>(TRANSFORMER);
            entry["applied_scale"] = transformer ? transformer->scale_x : 1.0;
            // Where it's going, exactly as scottland-scale# announced it, not a step of an animation.
            auto announced = announced_scale.find(view->get_id());
            entry["target_scale"] = announced != announced_scale.end() ? announced->second : displayed_scale(view);
            if (auto frame = frame_of(view, false))
            {
                auto r = frame->screen_rect();
                entry["frame"] = wf::json_t{};
                entry["frame"]["x"] = r.x1;
                entry["frame"]["y"] = r.y1;
                entry["frame"]["width"]  = r.width();
                entry["frame"]["height"] = r.height();
                entry["frame"]["thickness"] = frame->thickness();
                entry["frame"]["swell"]   = frame->swell;
                entry["frame"]["focus"]   = (double)frame->focus_mix;
                entry["frame"]["hovered"] = scottland::handle_name(frame->hovered_handle());
                entry["frame"]["dot"]     = frame->dot_glow;
                entry["frame"]["attention"] = frame->needs_attention();
                entry["attention"] = needs_attention(view->get_id());
                if (frame->morphing())
                {
                    entry["frame"]["morph"] = wf::json_t{};
                    entry["frame"]["morph"]["shape"] = frame->morph.shape;
                    entry["frame"]["morph"]["fade"]  = frame->morph.fade;
                    entry["frame"]["morph"]["snapshot"] = (bool)frame->morph.snapshot;
                }
                entry["frame"]["neighbors"] = (int64_t)frame->get_neighbors().size();
                wf::json_t clouds = wf::json_t::array();
                for (double c : frame->cloud)
                {
                    clouds.append(c);
                }

                entry["frame"]["cloud"] = clouds;
            }
            views.append(entry);
        }

        reply["views"] = views;
        return reply;
    };

    wf::shared_data::ref_ptr_t<wf::ipc::method_repository_t> ipc_repo;
    wf::option_wrapper_t<wf::config::compound_list_t<std::string, std::string>> release_bindings{
        "scottland/release_bindings"};

    wf::ipc::method_callback send_key = [] (wf::json_t data) -> wf::json_t
    {
        if (!data.has_member("key") || !data["key"].is_string())
        {
            return wf::ipc::json_error("send-key needs a string \"key\"");
        }

        auto seat     = wf::get_core().get_current_seat();
        auto keyboard = wlr_seat_get_keyboard(seat);
        if (!keyboard || !keyboard->keymap)
        {
            return wf::ipc::json_error("no keyboard on the seat");
        }

        auto key = evdev_keycode(keyboard->keymap, data["key"].as_string());
        if (!key)
        {
            return wf::ipc::json_error("unknown key: " + data["key"].as_string());
        }

        std::string mods  = (data.has_member("mods") && data["mods"].is_string()) ? data["mods"].as_string() : "";
        std::string state = (data.has_member("state") && data["state"].is_string()) ?
            data["state"].as_string() : "press";

        // Present only the requested modifiers to the client for this key, then restore the real
        // state, so a physically held Super doesn't turn Ctrl+C into Super+Ctrl+C.
        uint32_t mask = modifier_mask(keyboard->keymap, mods);
        if ((state == "down") || (state == "press"))
        {
            inject_key(seat, keyboard, *key, mask, true);
        }

        if ((state == "up") || (state == "press"))
        {
            inject_key(seat, keyboard, *key, mask, false);
        }

        return wf::ipc::json_ok();
    };

    // Wayfire <= 0.11 never applies input/touchpad_scroll_speed to touchpad finger scrolling:
    // pointing_device_t::get_scroll_speed() only returns it for tablet pads, so touchpads (pointer
    // devices) always scroll at 1.0 (https://github.com/WayfireWM/wayfire/issues/3148). Apply it
    // here, before Wayfire handles the event. Remove this
    // once the upstream fix ships (the ABI check below turns it off for newer Wayfire builds).
    wf::option_wrapper_t<double> touchpad_scroll_speed{"input/touchpad_scroll_speed"};
    wf::signal::connection_t<wf::input_event_signal<wlr_pointer_axis_event>> on_axis =
        [=] (wf::input_event_signal<wlr_pointer_axis_event> *ev)
    {
#if WAYFIRE_API_ABI_VERSION_MACRO <= 2026'07'26
        if ((ev->event->source == WL_POINTER_AXIS_SOURCE_FINGER) && ev->device &&
            (ev->device->type == WLR_INPUT_DEVICE_POINTER) &&
            !(touch_pointer && (ev->device == &touch_pointer->pointer.base)))
        {
            double speed = std::max(0.0, (double)touchpad_scroll_speed);
            ev->event->delta *= speed;
            ev->event->delta_discrete = std::lround(ev->event->delta_discrete * speed);
        }
#endif
    };

    // Per-app key remaps: [scottland] remap_apps_<name> (app-id regex, case-insensitive),
    // remap_from_<name> and remap_to_<name> (e.g. "CTRL+W" -> "CTRL+BackSpace"). The original key
    // is swallowed before bindings and apps see it; the replacement goes down and up with it, so
    // holding the key repeats the replacement.
    wf::option_wrapper_t<wf::config::compound_list_t<std::string, std::string, std::string>> key_remaps{
        "scottland/key_remaps"};
    struct active_remap_t
    {
        uint32_t keycode;
        uint32_t mask;
    };
    std::map<uint32_t, active_remap_t> active_remaps;  // physical keycode -> replacement held

    std::string focused_app_id()
    {
        auto view = wf::get_core().seat->get_active_view();
        return view ? view->get_app_id() : "";
    }

    wf::signal::connection_t<wf::input_event_signal<wlr_keyboard_key_event>> on_remap_key =
        [=] (wf::input_event_signal<wlr_keyboard_key_event> *ev)
    {
        auto seat     = wf::get_core().get_current_seat();
        auto keyboard = wlr_seat_get_keyboard(seat);
        if (!keyboard || !keyboard->keymap)
        {
            return;
        }

        if (ev->event->state == WL_KEYBOARD_KEY_STATE_RELEASED)
        {
            auto held = active_remaps.find(ev->event->keycode);
            if (held != active_remaps.end())
            {
                inject_key(seat, keyboard, held->second.keycode, held->second.mask, false);
                active_remaps.erase(held);
                ev->mode = wf::input_event_processing_mode_t::IGNORE;
            }

            return;
        }

        uint32_t relevant = modifier_mask(keyboard->keymap, "CTRL SHIFT ALT SUPER");
        uint32_t held_mods = keyboard->modifiers.depressed & relevant;
        std::string app = focused_app_id();
        for (const auto& [name, apps, from, to] : key_remaps.value())
        {
            auto [from_mods, from_key] = split_combo(from);
            auto from_code = evdev_keycode(keyboard->keymap, from_key);
            if (!from_code || (*from_code != ev->event->keycode) ||
                (modifier_mask(keyboard->keymap, from_mods) != held_mods))
            {
                continue;
            }

            try {
                if (!std::regex_search(app, std::regex(apps, std::regex::icase)))
                {
                    continue;
                }
            } catch (const std::regex_error&)
            {
                LOGE("scottland: bad remap_apps_", name, " regex: ", apps);
                continue;
            }

            auto [to_mods, to_key] = split_combo(to);
            auto to_code = evdev_keycode(keyboard->keymap, to_key);
            if (!to_code)
            {
                continue;
            }

            uint32_t mask = modifier_mask(keyboard->keymap, to_mods);
            inject_key(seat, keyboard, *to_code, mask, true);
            active_remaps[ev->event->keycode] = {*to_code, mask};
            ev->mode = wf::input_event_processing_mode_t::IGNORE;
            return;
        }
    };

    wf::signal::connection_t<wf::input_event_signal<wlr_keyboard_key_event>> on_key =
        [=] (wf::input_event_signal<wlr_keyboard_key_event> *ev)
    {
        if (ev->event->state != WL_KEYBOARD_KEY_STATE_RELEASED)
        {
            return;
        }

        auto keyboard = wlr_seat_get_keyboard(wf::get_core().get_current_seat());
        xkb_keymap *keymap = keyboard ? keyboard->keymap : nullptr;
        for (const auto& [name, key, command] : release_bindings.value())
        {
            auto code = evdev_keycode(keymap, key);
            if (code && (*code == ev->event->keycode))
            {
                wf::get_core().run(command);
            }
        }
    };

  public:
    void init() override
    {
        init_output_tracking();
        ipc_repo->register_method("scottland/send-key", send_key);
        ipc_repo->register_method("scottland/layout-state", layout_state);
        wf::get_core().connect(&on_key);
        wf::get_core().connect(&on_axis);
        wf::get_core().connect(&on_remap_key);
        wf::get_core().connect(&on_mapped);
        wf::get_core().connect(&on_geometry);
        wf::get_core().connect(&on_output);
        wf::get_core().connect(&on_focus);
        wf::get_core().connect(&on_unmapped);
        wf::get_core().connect(&on_motion);
        wf::get_core().connect(&on_swipe_begin);
        wf::get_core().connect(&on_cancel_key);
        wf::get_core().connect(&on_swipe_update);
        wf::get_core().connect(&on_swipe_end);
        wf::get_core().connect(&on_touchpad_button);
        wf::get_core().connect(&on_touch_down);
        wf::get_core().connect(&on_touch_motion);
        wf::get_core().connect(&on_touch_up);
        synthesize_pop();
        ipc_repo->register_method("scottland/test-input", test_input);
        ipc_repo->register_method("scottland/widgets", widgets_state);
        wf::get_core().connect(&on_hints);
        ipc_repo->register_method("scottland/widget-action", widget_action);
        ipc_repo->register_method("scottland/attention", attention_method);
        wf::get_core().connect(&on_focus_request);
        start_activation();
        announce_widgets();  // a widget service that outlived a reload catches up
        wf::get_core().connect(&on_motion_abs);
        wf::get_core().connect(&on_button);
        drag->connect(&on_drag_output);
        drag->connect(&on_drag_motion);
        drag->connect(&on_drag_done);
        center_width.set_callback([=] { apply_all(); });
        rail_width.set_callback([=] { apply_all(); });
        min_scale.set_callback([=] { apply_all(); });
        max_scale.set_callback([=] { apply_all(); });
        load_curve();
        scale_curve_text.set_callback([=] { load_curve(); apply_all(); });
        blend_width.set_callback([=] { apply_all(); });
        color_scheme.set_callback([=] { load_color_scheme(); });
        accent_color.set_callback([=] { load_color_scheme(); });
        attention_color.set_callback([=] { load_color_scheme(); });
        load_color_scheme();
        apply_all();
        update_focus();
        LOGI("scottland: plugin loaded");
    }

    void fini() override
    {
        fini_output_tracking();
        ipc_repo->unregister_method("scottland/send-key");
        ipc_repo->unregister_method("scottland/layout-state");
        on_key.disconnect();
        on_axis.disconnect();
        on_remap_key.disconnect();
        on_mapped.disconnect();
        on_geometry.disconnect();
        on_output.disconnect();
        on_focus.disconnect();
        on_unmapped.disconnect();
        idle_neighbors.disconnect();
        on_motion.disconnect();
        swipe_end();
        on_swipe_begin.disconnect();
        on_cancel_key.disconnect();
        glide_tick.disconnect();
        glide.reset();
        on_swipe_update.disconnect();
        on_swipe_end.disconnect();
        on_touchpad_button.disconnect();
        on_touch_down.disconnect();
        on_touch_motion.disconnect();
        on_touch_up.disconnect();
        cancel_hold();
        momentum.disconnect();
        touch_pointer.reset();
        ipc_repo->unregister_method("scottland/test-input");
        ipc_repo->unregister_method("scottland/widgets");
        on_hints.disconnect();
        ipc_repo->unregister_method("scottland/widget-action");
        ipc_repo->unregister_method("scottland/attention");
        on_focus_request.disconnect();
        on_activate.disconnect();
        // Unloading (or reloading) forgets the links: give every app its window back.
        end_morph();
        widget_watchdog.disconnect();
        for (auto& [id, link] : widget_links)
        {
            set_hidden(link, false);

            close_view_or_process(wf::toplevel_cast(link.widget.lock()), link.launcher);
        }

        widget_links.clear();
        announce_widgets();  // the widget service drops its objects (a reloaded plugin re-announces)
        end_due_processes(true);
        ending_timer.disconnect();
        on_motion_abs.disconnect();
        on_button.disconnect();
        on_drag_output.disconnect();
        on_drag_motion.disconnect();
        on_drag_done.disconnect();
        transition_tick.disconnect();
        transitions.clear();
        for (auto& view : wf::get_core().get_all_views())
        {
            view->get_transformed_node()->rem_transformer(TRANSFORMER);
        }

        scottland::gl_programs().release();
        LOGI("scottland: plugin unloaded");
    }
};

DECLARE_WAYFIRE_PLUGIN(scottland_plugin_t);
