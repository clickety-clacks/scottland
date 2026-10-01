#include <wayfire/plugin.hpp>
#include <wayfire/core.hpp>
#include <wayfire/output.hpp>
#include <wayfire/seat.hpp>
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
#include <wayfire/window-manager.hpp>
#include <linux/input-event-codes.h>
#include <wayfire/util/log.hpp>
#include <wayfire/util/duration.hpp>
#include <wayfire/util.hpp>

extern "C" {
#include <wlr/types/wlr_pointer.h>
#include <wlr/types/wlr_keyboard.h>
#include <wlr/types/wlr_seat.h>
#include <wlr/backend/libinput.h>
#include <wlr/backend/headless.h>
#include <wlr/backend/multi.h>
#include <wlr/interfaces/wlr_pointer.h>
}

#include <libinput.h>

#include <xkbcommon/xkbcommon.h>

#include "frame.hpp"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <ctime>
#include <map>
#include <regex>
#include <optional>
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

        double center_x = start_geometry.x + start_geometry.width / 2.0;
        double center_y = start_geometry.y + start_geometry.height / 2.0;
        wf::geometry_t desired;
        desired.x      = std::round(center_x - width / 2.0);
        desired.y      = std::round(center_y - height / 2.0);
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

    void load_color_scheme()
    {
        scottland::palette.light = std::string(color_scheme) == "light";
        wf::color_t accent = accent_color;
        scottland::palette.accent = {accent.r, accent.g, accent.b};
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

    /** Move a window toward `target`, animating jumps. */
    void set_scale(wayfire_toplevel_view view, double target)
    {
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
        [=] (wf::keyboard_focus_changed_signal*) { update_focus(); };
    wf::signal::connection_t<wf::view_unmapped_signal> on_unmapped =
        [=] (wf::view_unmapped_signal *ev)
    {
        if (auto toplevel = wf::toplevel_cast(ev->view))
        {
            if (auto frame = frame_of(toplevel, false))
            {
                forget_owner(frame);
                frame->set_neighbors({});
            }
        }

        // Neighbors are recomputed without it once it's gone from the stacking list.
        idle_neighbors.run_once([=] () { update_all_neighbors(); });
    };
    wf::wl_idle_call idle_neighbors;

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
            auto running = transitions.find(drag->view->get_id());
            drag_target = running != transitions.end() ? running->second.animation.end : displayed_scale(drag->view);
            update_neighbors(output);
        }
    };

    wf::signal::connection_t<wf::move_drag::drag_motion_signal> on_drag_motion =
        [=] (wf::move_drag::drag_motion_signal *ev)
    {
        auto view   = drag->view;
        auto output = drag->current_output;
        if (!view || !output || view->pending_fullscreen())
        {
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
        if (main && main->is_mapped() && main->get_output() && !main->pending_fullscreen())
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
                set_scale(dragged.view, placement_of(dragged.view).scale);
            }
        }

        drag_input_override.reset();
        // The dropped window rejoins its neighbors' liquid once the drag has let go of it.
        idle_neighbors.run_once([=] () { update_all_neighbors(); });
    };

    wf::signal::connection_t<wf::view_mapped_signal> on_mapped = [=] (wf::view_mapped_signal *ev)
    {
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
            entry["zone"]  = zone_name(placement.zone);
            entry["scale"] = placement.scale;
            auto transformer = view->get_transformed_node()->get_transformer<
                wf::scene::view_2d_transformer_t>(TRANSFORMER);
            entry["applied_scale"] = transformer ? transformer->scale_x : 1.0;
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
        wf::get_core().connect(&on_swipe_update);
        wf::get_core().connect(&on_swipe_end);
        wf::get_core().connect(&on_touchpad_button);
        wf::get_core().connect(&on_touch_down);
        wf::get_core().connect(&on_touch_motion);
        wf::get_core().connect(&on_touch_up);
        synthesize_pop();
        ipc_repo->register_method("scottland/test-input", test_input);
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
