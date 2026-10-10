#include <chrono>
#include <wayfire/plugin.hpp>
#include <wayfire/core.hpp>
#include <wayfire/output.hpp>
#include <wayfire/seat.hpp>
#include <wayfire/input-device.hpp>
#include <wayfire/bindings-repository.hpp>
#include <wayfire/plugins/wm-actions-signals.hpp>
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
#include <wayfire/view-helpers.hpp>
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
#include <wlr/types/wlr_output_layout.h>
#include <wlr/types/wlr_compositor.h>
#include <wlr/types/wlr_buffer.h>
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
#include "drag-presentation.hpp"
#include "spread.hpp"
#include "spread-job.hpp"
#include "live-drag.hpp"
#include "rail-make-room.hpp"
#include "eased-move.hpp"
#include "placement.hpp"
#include "cycle-spring.hpp"
#include "declutter.hpp"
#include "peek.hpp"
#include "alt-mode.hpp"
#include "pairing.hpp"
#include "navigation.hpp"
#include "inertia.hpp"
#include <chrono>
#include "hint-overlay.hpp"
#include <wayfire/scene-operations.hpp>
#include "key-layers.hpp"
#include "session.hpp"
#include "shortcuts.hpp"
#include "attention-color.hpp"
#include "state-dye.hpp"
#include "input-disabled-record.hpp"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <ctime>
#include <map>
#include <set>
#include <regex>
#include <optional>
#include <tuple>
#include <signal.h>
extern "C" {
#include <sys/pidfd.h>  // glibc declares it without C linkage for C++
}
#include <unistd.h>
#include <sys/socket.h>
#include <cstring>
#include <fcntl.h>
#include <sys/inotify.h>
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
//  - IPC "scottland/present" {window}: bring a window (or a widget's window) to the middle of
//    the screen at 100%, raised and focused: "I want to see this now" (L30).
//  - IPC "scottland/key-layer" {action,window|pid+namespace,keys}: focused-surface shortcut
//    claims with fall-through (docs/key-layers.md; full interface in key-layers.cpp).
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
bool valid_input_device_name(const std::string& name)
{
    return scottland::input::valid_device_name(name);
}

std::string input_device_kind(wlr_input_device *device)
{
    if (!device) return "";
    switch (device->type)
    {
      case WLR_INPUT_DEVICE_POINTER: return "pointer";
      case WLR_INPUT_DEVICE_TOUCH: return "touch";
      case WLR_INPUT_DEVICE_TABLET: return "tablet";
      default: return "";
    }
}

bool input_device_matches_kind(const std::string& requested, wlr_input_device *device)
{
    return scottland::input::device_matches_kind(requested, input_device_kind(device));
}

bool set_input_device_enabled(wf::input_device_t& device, bool enabled)
{
    // Wayfire's bool setter can treat DISABLED_ON_EXTERNAL_MOUSE as already disabled and return
    // without changing the mode. Set and verify the exact libinput mode so OFF stays OFF when the
    // external mouse goes away.
    auto handle = device.get_wlr_handle();
    if (!handle || !wlr_input_device_is_libinput(handle)) return false;

    auto libinput_device = wlr_libinput_get_device_handle(handle);
    if (!libinput_device) return false;

    const uint32_t requested_mode = enabled ? LIBINPUT_CONFIG_SEND_EVENTS_ENABLED :
        LIBINPUT_CONFIG_SEND_EVENTS_DISABLED;
    return scottland::input::set_send_events_mode(requested_mode,
        [libinput_device] (uint32_t mode)
        {
            return libinput_device_config_send_events_set_mode(libinput_device, mode) ==
                LIBINPUT_CONFIG_STATUS_SUCCESS;
        },
        [libinput_device] ()
        {
            return libinput_device_config_send_events_get_mode(libinput_device);
        });
}

std::string input_disabled_record_path(const std::string& kind)
{
    const char *base = getenv("XDG_STATE_HOME");
    std::string directory;
    if (base && *base)
    {
        directory = base;
    } else if (const char *home = getenv("HOME"))
    {
        directory = std::string(home) + "/.local/state";
    } else
    {
        return "";
    }
    return directory + "/scottland/input-" + kind + ".disabled";
}

std::optional<std::string> input_disabled_name(const std::string& kind)
{
    const auto path = input_disabled_record_path(kind);
    if (path.empty()) return std::nullopt;
    std::ifstream file(path, std::ios::binary);
    if (!file) return std::nullopt;
    std::string name, trailing;
    if (!std::getline(file, name) || std::getline(file, trailing) || file.bad() ||
        !valid_input_device_name(name))
    {
        return std::nullopt;
    }
    return name;
}

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

/** An immutable copy of the zone scale function, for a solver snapshot (spread). */
std::function<double(double)> scale_function(double width, double center_pct, double rail_pct, double min_scale,
    double max_scale, scale_curve_t curve, double blend)
{
    return [=] (double x) { return place(x, width, center_pct, rail_pct, min_scale, max_scale, curve, blend).scale; };
}

/**
 * Before a grab owner goes away (and before this library unloads), take Wayfire's pointer focus
 * off a node that has left the scene. A touch turns pointer focus off, but a grab started by touch
 * still becomes the pointer focus, and its removal can't refocus the pointer; the next mouse event
 * then sends that grab node a pointer leave. After a reload its interaction object is freed and
 * its code unmapped (the compositor crashed there). An inert core node takes the focus instead
 * (the old grab node is freed now, while its code is still here); the next pointer event focuses
 * what's under the cursor as usual.
 */
static void release_stale_pointer_focus()
{
    auto focus = wf::get_core().get_cursor_focus();
    if (!focus || (typeid(*focus) == typeid(wf::scene::node_t))) return;  // none, or already inert
    auto top = focus.get();
    while (top->parent()) top = top->parent();
    if (top == wf::get_core().scene().get()) return;
    LOGI("scottland: released stale pointer focus ", focus->stringify());
    focus.reset();
    // A core node_t has core code only, but its shared_ptr control block is instantiated here: it
    // is never released, so Wayfire dropping it later can't call into an unloaded library.
    auto inert = new wf::scene::node_ptr(std::make_shared<wf::scene::node_t>(false));
    wf::get_core().transfer_grab(*inert);
    // The transfer also took keyboard focus and made the inert node an explicit pointer grab: an
    // input-state update ends that grab (the node isn't in the scene) and refocus restores keys.
    wf::scene::update(wf::get_core().scene(), wf::scene::update_flag::INPUT_STATE);
    wf::get_core().seat->refocus();
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
    std::function<void()> on_start;
    /** Resize `target` around its center until `with_button` is released. Moving the cursor
     *  by (dx, dy) grows the window by (sign_x * dx, sign_y * dy) on each side. */
    bool start(wayfire_toplevel_view target, uint32_t with_button, int grow_x_sign, int grow_y_sign,
        int finger = -1)
    {
        if (!target || !target->is_mapped() || target->pending_fullscreen() ||
            (target->get_output() != output) || !scottland::can_resize(target))
        {
            return false;
        }

        if (!output->activate_plugin(&grab_interface))
        {
            return false;
        }

        if (on_start) on_start();
        input_grab->set_wants_raw_input(true);
        input_grab->grab_input(wf::scene::layer::OVERLAY);
        if (target->pending_tiled_edges())
        {
            target->toplevel()->pending().tiled_edges = 0;
        }

        settle.disconnect();  // the previous resize's: it would stop this one's centering
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

        if (auto frame = target->get_transformed_node()->get_transformer<scottland::frame_t>("scottland-scale"))
            scottland::goo_impulse(*frame, 0.8f);
        wf::get_core().set_cursor("all-scroll");
        return true;
    }

    /** A window move takes over: stop keeping the last resized window centered. */
    void stop_settling()
    {
        settle.disconnect();
        on_geometry.disconnect();
        recenter_view.reset();
    }

  private:
    void end()
    {
        if (input_grab->is_grabbed())
        {
            input_grab->ungrab_input();
        }

        output->deactivate_plugin(&grab_interface);
        if (auto target = wf::toplevel_cast(view.lock()))
            if (auto frame = target->get_transformed_node()->get_transformer<scottland::frame_t>("scottland-scale"))
                scottland::goo_impulse(*frame, 1.2f);
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
        release_stale_pointer_focus();  // it may be this grab, whose interaction is this object
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

    explicit virtual_pointer_t(const char *name = "scottland-touch-pointer")
    {
        auto& core = wf::get_core();
        backend = wlr_headless_backend_create(core.ev_loop);
        wlr_multi_backend_add(core.backend, backend);
        wlr_pointer_init(&pointer, &impl, name);
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

    /** Scroll by (dx, dy) pixels: smooth, as a finger on a touchpad (the content follows it one
     *  to one), or with `wheel` as a high-resolution wheel (fine value120 steps), for apps that
     *  ignore smooth scrolling from a device like this (Ghostty). (0, 0) ends a smooth scroll. */
    void scroll(double dx, double dy, bool wheel = false)
    {
        if (wheel && (dx == 0) && (dy == 0))
        {
            return;  // a wheel has no "stop"
        }

        for (auto [orientation, delta] : {std::pair{WL_POINTER_AXIS_VERTICAL_SCROLL, dy},
            std::pair{WL_POINTER_AXIS_HORIZONTAL_SCROLL, dx}})
        {
            // A zero from a finger means "stop" (axis_stop): only the axes that moved, unless
            // both are zero (the scroll ends).
            if ((delta == 0) && (wheel || (dx != 0) || (dy != 0)))
            {
                continue;
            }

            wlr_pointer_axis_event ev;
            ev.pointer     = &pointer;
            ev.time_msec   = now_msec();
            ev.source      = wheel ? WL_POINTER_AXIS_SOURCE_WHEEL : WL_POINTER_AXIS_SOURCE_FINGER;
            ev.orientation = orientation;
            ev.relative_direction = WL_POINTER_AXIS_RELATIVE_DIRECTION_IDENTICAL;
            ev.delta = delta;
            ev.delta_discrete = wheel ? (int32_t)std::lround(delta * 8) : 0;  // value120: 15 px a notch
            wl_signal_emit(&pointer.events.axis, &ev);
        }

        wl_signal_emit(&pointer.events.frame, NULL);
    }

    void click(uint32_t button)
    {
        for (auto state : {WL_POINTER_BUTTON_STATE_PRESSED, WL_POINTER_BUTTON_STATE_RELEASED})
            press(button, state == WL_POINTER_BUTTON_STATE_PRESSED);
    }

    void press(uint32_t button, bool down)
    {
        wlr_pointer_button_event ev;
        ev.pointer   = &pointer;
        ev.time_msec = now_msec();
        ev.button    = button;
        ev.state     = down ? WL_POINTER_BUTTON_STATE_PRESSED : WL_POINTER_BUTTON_STATE_RELEASED;
        wl_signal_emit(&pointer.events.button, &ev);
        wl_signal_emit(&pointer.events.frame, NULL);
    }

    // Touchpad gestures as libinput's backend reports them, through the same device signals, so
    // Wayfire's input path (and every plugin on it) sees them as it would a real touchpad's.
    void hold_begin(uint32_t fingers)
    {
        wlr_pointer_hold_begin_event ev{&pointer, now_msec(), fingers};
        wl_signal_emit(&pointer.events.hold_begin, &ev);
    }

    void hold_end(bool cancelled)
    {
        wlr_pointer_hold_end_event ev{&pointer, now_msec(), cancelled};
        wl_signal_emit(&pointer.events.hold_end, &ev);
    }

    void swipe_begin(uint32_t fingers)
    {
        wlr_pointer_swipe_begin_event ev{&pointer, now_msec(), fingers};
        wl_signal_emit(&pointer.events.swipe_begin, &ev);
    }

    void swipe_update(uint32_t fingers, double dx, double dy)
    {
        wlr_pointer_swipe_update_event ev{&pointer, now_msec(), fingers, dx, dy};
        wl_signal_emit(&pointer.events.swipe_update, &ev);
    }

    void swipe_end(bool cancelled)
    {
        wlr_pointer_swipe_end_event ev{&pointer, now_msec(), cancelled};
        wl_signal_emit(&pointer.events.swipe_end, &ev);
    }
};

class scottland_plugin_t : public wf::plugin_interface_t,
    public wf::per_output_tracker_mixin_t<center_resize_t>
{
    scottland::key_layers_t key_layers;
    scottland::session_t session_state;
    scottland::shortcuts_t shortcuts;

    static constexpr const char *TRANSFORMER = "scottland-scale";
    scottland::goo_t goo;

    wf::option_wrapper_t<double> center_width{"scottland/center_width"};
    wf::option_wrapper_t<double> rail_width{"scottland/rail_width"};
    wf::option_wrapper_t<double> min_scale{"scottland/min_scale"};
    wf::option_wrapper_t<double> max_scale{"scottland/max_scale"};
    wf::option_wrapper_t<std::string> scale_curve_text{"scottland/scale_curve"};
    wf::option_wrapper_t<double> blend_width{"scottland/blend_width"};
    wf::option_wrapper_t<std::string> screen_zones_text{"scottland/screen_zones"};
    wf::option_wrapper_t<double> center_opacity_focused{"scottland/center_opacity_focused"};
    wf::option_wrapper_t<double> center_opacity_unfocused{"scottland/center_opacity_unfocused"};
    wf::option_wrapper_t<double> side_opacity_focused{"scottland/side_opacity_focused"};
    wf::option_wrapper_t<double> side_opacity_unfocused{"scottland/side_opacity_unfocused"};
    wf::option_wrapper_t<double> widget_opacity_focused{"scottland/widget_opacity_focused"};
    wf::option_wrapper_t<double> widget_opacity_unfocused{"scottland/widget_opacity_unfocused"};
    wf::option_wrapper_t<double> window_mode_opacity_focused{"scottland/window_mode_opacity_focused"};
    wf::option_wrapper_t<double> window_mode_opacity_unfocused{"scottland/window_mode_opacity_unfocused"};
    wf::option_wrapper_t<double> unfocused_edge_tone_light{"scottland/unfocused_edge_tone_light"};
    wf::option_wrapper_t<double> unfocused_edge_tone_dark{"scottland/unfocused_edge_tone_dark"};
    wf::option_wrapper_t<double> unfocused_edge_strength{"scottland/unfocused_edge_strength"};
    wf::option_wrapper_t<int> widget_make_room_dwell{"scottland/widget_make_room_dwell"};
    wf::option_wrapper_t<double> window_mode_tint{"scottland/window_mode_tint"};
    wf::option_wrapper_t<double> hint_background_opacity{"scottland/hint_background_opacity"};
    wf::option_wrapper_t<bool> window_avoidance_always{"scottland/window_avoidance_always"};
    wf::option_wrapper_t<double> goo_dye_density{"scottland/goo_dye_density"};
    // Keep parsing the historical key so existing user config still opts in.
    wf::option_wrapper_t<bool> hint_avoidance_always{"scottland/hint_avoidance_always"};
    wf::option_wrapper_t<std::string> color_scheme{"scottland/color_scheme"};
    wf::option_wrapper_t<wf::color_t> accent_color{"scottland/accent_color"};
    wf::option_wrapper_t<wf::color_t> attention_color{"scottland/attention_color"};
    wf::option_wrapper_t<std::string> attention_color_family{"scottland/attention_color_family"};

    #include "windowing-bridge.hpp"

    using screen_identity_t = std::tuple<std::string, std::string, std::string>;
    struct screen_zone_t
    {
        double center = 33.333, rail = 2, blend = 40, minimum = 0.2, maximum = 1.0;
        std::string curve_text;
        scale_curve_t curve;
    };
    struct resolved_zone_t
    {
        double center, rail, blend, minimum, maximum;
        const scale_curve_t *curve;
        const std::string *curve_text;
    };
    std::map<screen_identity_t, screen_zone_t> screen_zones;
    std::string global_scale_curve_text;
    // Headless acceptance can name otherwise unidentified outputs. This seam is registered only
    // in SCOTTLAND_TEST_MODEL=1 sessions and never wins over identity reported by the compositor.
    std::map<std::string, screen_identity_t> test_identity_by_output;

    void load_color_scheme()
    {
        scottland::palette.light = std::string(color_scheme) == "light";
        scottland::palette.unfocused_edge_tone_light = unfocused_edge_tone_light;
        scottland::palette.unfocused_edge_tone_dark = unfocused_edge_tone_dark;
        scottland::palette.unfocused_edge_strength = unfocused_edge_strength;
        scottland::palette.hint_tint = window_mode_tint_strength();
        scottland::palette.dye_strength = std::clamp(float(goo_dye_density), 0.f, 1.5f);
        wf::color_t accent = accent_color;
        scottland::palette.accent = {accent.r, accent.g, accent.b};
        wf::color_t attention = attention_color;
        const auto selected_attention = scottland::attention_color::select(
            std::string(attention_color_family), scottland::palette.light,
            {attention.r, attention.g, attention.b});
        scottland::palette.attention = {
            selected_attention.r, selected_attention.g, selected_attention.b};
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
        global_scale_curve_text = text;
        if (!scale_curve.parse(text))
        {
            if (!text.empty())
            {
                LOGE("scottland: ignoring invalid scale_curve \"", text, "\"");
            }

            scale_curve = {};
        }
    }

    static std::string output_identity_part(const char *part)
    {
        return part ? std::string(part) : std::string();
    }

    static std::string output_name(wf::output_t *output)
    {
        if (!output) return {};
        return output->handle && output->handle->name ? std::string(output->handle->name) : output->to_string();
    }

    screen_identity_t reported_screen_identity(wf::output_t *output) const
    {
        if (!output || !output->handle)
            return {};
        return {output_identity_part(output->handle->make),
            output_identity_part(output->handle->model), output_identity_part(output->handle->serial)};
    }

    screen_identity_t screen_identity(wf::output_t *output) const
    {
        auto identity = reported_screen_identity(output);
        if (std::get<0>(identity).empty() && std::get<1>(identity).empty() && std::get<2>(identity).empty())
        {
            auto synthetic = test_identity_by_output.find(output_name(output));
            if (synthetic != test_identity_by_output.end())
                identity = synthetic->second;
        }
        return identity;
    }

    static std::string display_identity(const screen_identity_t& identity)
    {
        std::string result;
        for (const auto& part : {std::get<0>(identity), std::get<1>(identity), std::get<2>(identity)})
        {
            if (part.empty()) continue;
            if (!result.empty()) result += ' ';
            result += part;
        }
        return result;
    }

    static bool json_number(const wf::json_t& value)
    {
        return value.is_double() || value.is_int() || value.is_int64();
    }

    static double json_number_value(const wf::json_t& value)
    {
        if (value.is_double()) return value.as_double();
        if (value.is_int64()) return double(value.as_int64());
        return double(value.as_int());
    }

    void load_screen_zones()
    {
        screen_zones.clear();
        std::string text = screen_zones_text;
        if (text.empty()) return;
        wf::json_t entries;
        if (wf::json_t::parse_string(text, entries) || !entries.is_array())
        {
            LOGE("scottland: invalid screen_zones JSON; using global zone sizes on every output");
            return;
        }
        for (size_t i = 0; i < entries.size(); ++i)
        {
            auto entry = entries[i];
            if (!entry.is_object() || !entry.has_member("make") || !entry["make"].is_string() ||
                !entry.has_member("model") || !entry["model"].is_string() ||
                !entry.has_member("serial") || !entry["serial"].is_string() ||
                !entry.has_member("scale_curve") || !entry["scale_curve"].is_string())
            {
                LOGE("scottland: ignoring screen_zones entry ", i, " with an invalid identity or curve");
                continue;
            }
            bool valid = true;
            for (const auto *key : {"center_width", "rail_width", "blend_width", "min_scale", "max_scale"})
                valid &= entry.has_member(key) && json_number(entry[key]);
            if (!valid)
            {
                LOGE("scottland: ignoring screen_zones entry ", i, " with a missing or non-numeric zone size");
                continue;
            }

            screen_identity_t identity{entry["make"].as_string(), entry["model"].as_string(), entry["serial"].as_string()};
            if (std::get<0>(identity).empty() && std::get<1>(identity).empty() && std::get<2>(identity).empty())
            {
                LOGE("scottland: ignoring screen_zones entry ", i, " without a screen identity");
                continue;
            }
            screen_zone_t zone;
            zone.center = std::clamp(json_number_value(entry["center_width"]), 0.0, 100.0);
            zone.rail = std::clamp(json_number_value(entry["rail_width"]), 0.0, 25.0);
            zone.blend = std::clamp(json_number_value(entry["blend_width"]), 0.0, 400.0);
            zone.minimum = std::clamp(json_number_value(entry["min_scale"]), 0.05, 1.0);
            zone.maximum = std::clamp(json_number_value(entry["max_scale"]), 0.05, 1.0);
            zone.curve_text = entry["scale_curve"].as_string();
            if (!zone.curve.parse(zone.curve_text))
            {
                LOGE("scottland: ignoring invalid scale_curve for screen ", display_identity(identity));
                zone.curve = {};
            }
            if (screen_zones.count(identity))
                LOGE("scottland: duplicate screen_zones identity ", display_identity(identity), "; keeping the last entry");
            screen_zones[std::move(identity)] = std::move(zone);
        }
    }

    resolved_zone_t zone_for(wf::output_t *output) const
    {
        if (output)
        {
            auto found = screen_zones.find(screen_identity(output));
            if (found != screen_zones.end())
            {
                const auto& zone = found->second;
                return {zone.center, zone.rail, zone.blend, zone.minimum, zone.maximum, &zone.curve, &zone.curve_text};
            }
        }
        return {double(center_width), double(rail_width), double(blend_width),
            std::clamp((double)min_scale, 0.05, 1.0), std::clamp((double)max_scale, 0.05, 1.0),
            &scale_curve, &global_scale_curve_text};
    }

    bool output_has_own_zones(wf::output_t *output) const
    {
        auto identity = screen_identity(output);
        return !(std::get<0>(identity).empty() && std::get<1>(identity).empty() && std::get<2>(identity).empty()) &&
            screen_zones.count(identity);
    }

    placement_t place_at(wf::output_t *output, double x, double width)
    {
        auto zone = zone_for(output);
        return place(x, width, zone.center, zone.rail, zone.minimum, zone.maximum,
            *zone.curve, std::max(0.0, zone.blend));
    }

    /** Is Shift held (alone or with others) on the keyboard? */
    static bool shift_held()
    {
        auto keyboard = wlr_seat_get_keyboard(wf::get_core().get_current_seat());
        return keyboard && (wlr_keyboard_get_modifiers(keyboard) & WLR_MODIFIER_SHIFT);
    }

    /** The scale a window shows: the one Shift pinned it at (L31), else its zone's. */
    double scale_for(wayfire_toplevel_view view)
    {
        auto found = model.windows.find(view->get_id());
        if ((found != model.windows.end()) && found->second.pinned_scale && !view->pending_fullscreen())
        {
            return *found->second.pinned_scale;
        }

        return placement_of(view).scale;
    }

    void pin_scale(wayfire_toplevel_view view, std::optional<double> scale)
    {
        auto& state = model.windows[view->get_id()];
        if (state.pinned_scale != scale)
        {
            state.pinned_scale = scale;
            LOGI("scottland: window ", view->get_id(), scale ? " keeps a pinned scale (Shift or pairing)" : " follows its zone again");
            publish_model();
        }
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
        return place_at(output, x, output->get_relative_geometry().width);
    }

    void apply_opacity(wayfire_toplevel_view view, std::optional<double> center_x = {})
    {
        if (!view || !view->is_mapped() || view->pending_fullscreen()) return;
        auto frame = frame_of(view, false);
        if (!frame) return;
        bool focused = wf::get_core().seat->get_active_view() == view;
        double target;
        if (untouched_since_pairing(view))
            target = 1.0;  // pairing shows both fully opaque, once (WK36)
        else if (window_keys.active)
            target = focused ? double(window_mode_opacity_focused) : double(window_mode_opacity_unfocused);
        else if (is_widget(view))
            target = focused ? double(widget_opacity_focused) : double(widget_opacity_unfocused);
        else
        {
            auto output = view->get_output();
            auto zone = center_x && output ? place_at(output, *center_x, output->get_relative_geometry().width).zone : placement_of(view).zone;
            bool center = zone == zone_t::center;
            target = center ? (focused ? double(center_opacity_focused) : double(center_opacity_unfocused)) :
                (focused ? double(side_opacity_focused) : double(side_opacity_unfocused));
        }
        frame->set_configured_opacity(target);
    }

    // Pairing makes both windows fully opaque once (Mike, 2026-10-06; WK36). Opacity is recomputed
    // on every focus change, Window mode entry and exit, and setting change, so "once" is kept by the
    // pair's existing placement record rather than a new flag: a window still exactly where pairing
    // put it, at the pair's scale, and not being dragged. Any move, resize, rescale or drag ends it.
    bool untouched_since_pairing(wayfire_toplevel_view view)
    {
        auto found = model.windows.find(view->get_id());
        if (found == model.windows.end() || !found->second.paired_placement || drag->view == view ||
            is_widget(view) || !view->get_output()) return false;
        auto& paired = *found->second.paired_placement;
        return paired.geometry == placed_geometry(view) && paired.output == view->get_output()->to_string() &&
            paired.pin == found->second.pinned_scale;
    }

    void apply_all_opacity()
    {
        for (auto& any_view : wf::get_core().get_all_views())
            if (auto view = wf::toplevel_cast(any_view)) apply_opacity(view);
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
            apply_opacity(view);
            return;
        }

        // A held drag transforms the live subtree; geometry changes only on release;
        // on_drag_motion keeps the scale live instead.
        if (drag->view == view)
        {
            return;
        }

        set_scale(view, scale_for(view));
        apply_opacity(view);
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
        observe_view(view);
        auto cycle = glides.find(view->get_id());
        if (cycle != glides.end() && cycle->second.cycle)
            target = cycle->second.scale_to; // transaction may still report the old center
        model.windows[view->get_id()].scale = target;
        publish_model();
        // The cycle's destination owns scale until its geometry transaction commits.
        if (cycle != glides.end() && cycle->second.cycle) return;
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

    /** Set a window's scale with no animation (it's already shown at that size, e.g. by the
     *  morph it was dropped from). */
    void set_scale_now(wayfire_toplevel_view view, double target)
    {
        set_scale(view, target);
        transitions.erase(view->get_id());
        apply_scale(view, target);
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
            frame->is_widget = [this] (auto v) { return is_widget(v); };
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
            apply_opacity(view);
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
        double best_distance = std::max({scottland::NEAR_RANGE, scottland::SWELL_VICINITY,
            scottland::goo_enabled() ? scottland::goo_hover_distance() : 0.0});
        if (scottland::goo_enabled())
        {
            for (auto& [view, frame] : frames_on(output))
                if (scottland::goo_handle(*frame, p) != scottland::handle_t::none) return frame;
        }
        for (auto& [view, frame] : frames_on(output))
        {
            // Measured where each frame is drawn (a peeking window's offset, a drag).
            auto local = frame->unpresented(p);
            double d = frame->band_distance(local);
            if (d < best_distance)
            {
                best = frame;
                best_distance = d;
            }

            if (frame->liquid_distance(local) <= 0)
            {
                break;  // the cursor is over this window or its halo: those behind are covered
            }
        }

        return best;
    }

    void track_pointer()
    {
        update_widget_peeks();
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
                    apply_opacity(toplevel);
                }
            }
        }

        for (auto& [id, state] : model.windows)
        {
            state.focused = active && active->get_id() == id;
        }
        record_focus_recency(active);
        publish_model();
    }

    wf::signal::connection_t<wf::keyboard_focus_changed_signal> on_focus =
        [=] (wf::keyboard_focus_changed_signal*)
    {
        update_focus();
        if (window_keys.active)
        {
            // A raise changes occlusion before the next hint tick. Conceal rear badges
            // immediately so none can spend a frame over the newly foreground window.
            auto front = wf::toplevel_cast(wf::get_core().seat->get_active_view());
            declutter_signature.clear();
            for (auto& [id, visual] : hint_visuals)
                if (visual.hint && represented_view(id) != front &&
                    !link_of_widget(represented_view(id)))
                { wf::scene::remove_child(visual.hint); visual.hint.reset(); }
        }
        declutter_signature.clear(); refresh_layout_avoidance();
        // Going to another window ends a just-dropped window's hold above the others (L29): the one
        // the user went to comes forward, now and when the hold would have ended.
        if (auto held = model.drag.held_above.lock(); held && (wf::get_core().seat->get_active_view().get() != held.get()))
        {
            release_above();
        }

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
            auto disappearing_card = link_of_widget(toplevel);
            bool lost_entry = disappearing_card && entering_widget(disappearing_card->window_id);
            if (model.drag.rail.presentation.is_active() &&
                model.drag.rail.presentation.contains(toplevel->get_id()))
                cancel_rail_drag();
            if (pending_drag_layout && pending_drag_layout->contains(toplevel->get_id()))
            {
                set_drag_layout_offset(toplevel, 0, 0);
                pending_drag_layout->forget(toplevel->get_id());
                pending_drag_layout->finalize_targets();
                if (pending_drag_layout->empty()) pending_drag_layout.reset();
            }
            stop_widget_transition(toplevel);
            model.windows.erase(toplevel->get_id());
            render_hidden(toplevel, false);  // return our lease even if Wayfire already unmapped it
            if (auto frame = frame_of(toplevel, false))
            {
                forget_owner(frame);
            }

            // Tied lifecycles (WG5): the app's window closed takes its widget along; a widget
            // closed by the user closes the app's window (unless it's going because of a restore).
            if (auto link = link_of_window(toplevel))
            {
                transition_widget(*link, widget_link_t::lifecycle_t::closing);  // if it maps again, it's an ordinary window
                auto widget = wf::toplevel_cast(link->widget.lock());
                auto launcher = link->launcher;
                model.widgets.erase(uint64_t(link->window_id));
                announce_widgets();
                close_view_or_process(widget, launcher);
            } else if (auto link = link_of_widget(toplevel))
            {
                if (lost_entry)
                {
                    auto launcher = link->launcher;
                    transition_widget(*link, widget_link_t::lifecycle_t::restoring);
                    model.windows[link->window_id].pending_rail.reset();
                    model.widgets.erase(uint64_t(link->window_id));
                    end_process(launcher, 0);
                    announce_widgets();
                } else if (!link->docked())  // a preview going is no reason to close the app
                {
                    auto launcher = link->launcher;
                    bool preview  = link->previewing();
                    model.widgets.erase(uint64_t(link->window_id));
                    if (preview)
                    {
                        end_process(launcher, 0);  // whatever else it started goes with it
                    }

                    announce_widgets();
                } else
                {
                    link->widget.reset();
                    close_linked(*link);
                }
            }
        }

        publish_model();
        refresh_layout_avoidance();
    };
    wf::wl_idle_call idle_focus;

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
        bool cancelled = false;
        std::string unit;
        scottland_plugin_t *owner = nullptr;
        wl_event_source *exit_watch = nullptr;
        ~widget_process_t()
        {
            if (exit_watch)
            {
                wl_event_source_remove(exit_watch);
            }
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
        std::string desktop, name, icon;             // launcher-resolved identity, owned here
        bool card = false;                          // the built-in renderer can report in tests
        enum class lifecycle_t { previewing, docked, restoring, closing, handed_over };
        lifecycle_t lifecycle = lifecycle_t::previewing;
        bool collapsed = false;                      // intent, independent of lifecycle and peeks
        bool peek = false;                           // temporary presentation; never changes mode
        bool peek_pointer = false, peek_hover = false;
        std::optional<uint32_t> peek_hover_due, peek_attention_due, peek_hint_due;
        bool minimized() const { return collapsed && !peek; }
        bool away = false;                           // slid off its screen edge and hidden (FS1, hidden mode)
        bool make_room_pending = false;              // non-drag arrivals wait for their mapped rail spot
        // A drag's drop already laid the rail out for this landing interval (shown during the
        // drag); if it lands there, nothing is solved again.
        bool drop_solved = false;
        double drop_lo = 0, drop_hi = 0;

        bool previewing() const { return lifecycle == lifecycle_t::previewing; }
        bool docked() const { return lifecycle == lifecycle_t::docked; }
        bool touch_drag = false;                     // a finger drag anywhere moves it (its manifest, WG18)
        uint32_t launched_at = 0;
    };

    // A fitted pair is not a peripheral visit (WK36). Keep its exact placement provenance
    // until this window moves, changes size/scale, or goes to another output.
    struct paired_placement_t
    {
        wf::geometry_t geometry;
        std::string output;
        std::optional<double> pin;
    };

    struct window_state_t
    {
        std::weak_ptr<wf::view_interface_t> view;
        std::string app_id, title;
        pid_t pid = 0;
        wf::geometryf_t geometry = {0, 0, 0, 0};
        zone_t zone = zone_t::center;
        double scale = 1.0;                           // target, never an animation sample
        bool focused = false;
        bool above = false;
        std::optional<double> pinned_scale;          // kept by Shift during drag or arrow motion (L31)
        std::optional<scottland::windowing::window_memory> placement;
        std::optional<paired_placement_t> paired_placement;
        std::optional<scottland::windowing::point> pending_rail; // refine on widget adoption
        std::set<std::string> attention;
    };

    static constexpr int MORPH_MS = 240;
    static constexpr double PROVISIONAL_WIDGET_W = 300, PROVISIONAL_WIDGET_H = 96;
    struct drag_origin_t
    {
        uint64_t view = 0;                // the window this origin is for (the one being dragged)
        wf::output_t *output = nullptr;
        wf::point_t position{0, 0};
        std::string rail;                 // widget origin, independent of its changing width
        // The move began with this window, in this form (a re-grab can continue a move whose drop
        // changed the form: a window that became a widget, or the reverse).
        uint64_t first_view = 0;
        bool first_widget = false;
        wf::dimensions_t first_size{0, 0};
        std::optional<double> pin;        // the first window's Shift scale pin then (L31): Esc restores it
        uint64_t became = 0;              // after a drop: the window that now stands for it
    };
    struct drag_morph_t
    {
        std::weak_ptr<wf::view_interface_t> dragged;
        bool from_widget = false;  // dragging a widget: its other form is its app's window
        bool toward = false;       // heading for the other form
        double center_x = 0;       // where the dragged frame is centered (output coords)
        wf::animation::simple_animation_t shape{wf::create_option<int>(MORPH_MS)};
        wf::animation::simple_animation_t fade{wf::create_option<int>(MORPH_MS * 3 / 4)};
        scottland::widget_image_t snapshot;
        wf::geometry_t snapshot_box{}, other_geometry{};
        bool snapshot_ready = false;
        int ticks = 0;
        double shown_w = 0, shown_h = 0;
    };
    struct rail_drag_t
    {
        scottland::rail::solver_t solver;
        scottland::drag_presentation_t presentation;
        wf::output_t *output = nullptr;
        uint64_t dragged = 0;
        bool left = false;
        double top = 0, bottom = 0;
        scottland::rectf_t last_item, solved_item;  // latest landing; the one the last solve cleared
        wf::pointf_t pause_anchor{0, 0};
        uint32_t pause_deadline = 0;
        bool has_item = false, has_pause_anchor = false, pause_pending = false, solved_once = false;
        bool active = false;
    };
    // One drag session owns the Esc origin, re-grab chain, morph and temporarily raised view.
    // The live drag controller and animation timers remain renderer/input resources.
    struct drag_session_t
    {
        double relative_x = 0.5;
        double margin = 0.0;
        double target = 1.0;
        double last_center = 0;
        bool started = false;
        wf::pointf_t start_cursor{0, 0};
        std::weak_ptr<wf::view_interface_t> held_above;
        drag_origin_t origin;
        bool cancelled = false;
        uint64_t widget = 0;
        drag_origin_t last_drop;
        uint32_t last_drop_at = 0;
        std::optional<wf::pointf_t> input_override;
        std::optional<drag_morph_t> morph;
        rail_drag_t rail;
    };

    // Super+M's widget mode (WG16). Hidden widgets slide off their screen edges and come back expanded.
    enum class widget_mode_t { expanded, collapsed, hidden };
    static const char *widget_mode_name(widget_mode_t mode)
    {
        return mode == widget_mode_t::collapsed ? "collapsed" : mode == widget_mode_t::hidden ? "hidden" : "expanded";
    }

    static std::optional<widget_mode_t> parse_widget_mode(const std::string& name)
    {
        if (name == "expanded") return widget_mode_t::expanded;
        if (name == "collapsed") return widget_mode_t::collapsed;
        if (name == "hidden") return widget_mode_t::hidden;
        return std::nullopt;
    }

    struct desktop_model_t
    {
        uint64_t version = 0;
        std::string session;
        std::map<uint64_t, window_state_t> windows;
        std::map<uint64_t, widget_link_t> widgets;    // keyed by the app window
        widget_mode_t widget_mode = widget_mode_t::expanded;  // the mode taps choose; survives a reload
        std::optional<widget_mode_t> widget_mode_held;        // Super+M held: momentary until released
        unsigned hint_width = 1; // stable prefix-free labels until all represented windows close
        drag_session_t drag;
        std::set<wf::output_t*> goo_outputs;          // screens with an available goo surface
        std::set<uint64_t> selected;                 // reserved for future multi-select
        std::set<wf::output_t*> focused_outputs;     // a fullscreen window in front: focus (FS1)
    } model;
    struct rail_layout_motion_t
    {
        std::weak_ptr<wf::view_interface_t> view;
        scottland::motion::eased_move_t move;
        uint32_t started = 0;
    };
    std::map<uint64_t, rail_layout_motion_t> rail_layout_motions;
    double rail_easing_speed_max = 0;  // px/s, planned; reported for the P11 speed-limit regression
    // Measured from what is drawn, independent of the planned curve: each tick's change in
    // a widget's drawn position along the rail (geometry plus rail offset) over the time
    // since the previous tick, the largest such step, and the longest gap between ticks.
    // Only y: a card's x changes with its width (rail gravity), which is not rail motion.
    struct rail_drawn_sample_t { double y = 0; uint32_t at = 0; double geometry_y = 0, offset = 0; };
    std::map<uint64_t, rail_drawn_sample_t> rail_drawn_samples;
    double rail_drawn_speed_max = 0, rail_drawn_step_max = 0;
    uint64_t rail_drop_layouts_kept = 0;  // landings that kept the drop's layout (no second solve)
    uint32_t rail_tick_gap_max = 0;
    wf::wl_timer<true> rail_layout_tick;
    wf::wl_timer<false> rail_dwell_tick;
    static constexpr double RAIL_POINTER_WOBBLE = 4.0;
    // A committed rail audition keeps its visual offsets until each Wayfire geometry transaction
    // applies. Only one small, capped commit can be outstanding; a later drag can still proceed.
    std::optional<scottland::drag_presentation_t> pending_drag_layout;

    // Visible rectangle including input/hint presentation transforms, in output coordinates.
    scottland::rectf_t scene_rectangle(wayfire_toplevel_view view, wf::output_t *output)
    {
        auto frame = frame_of(view, false);
        if (!frame) return {};
        auto r = frame->screen_rect();
        std::shared_ptr<wf::scene::transformer_base_node_t> move;
        for (auto n = frame->parent(); n && n != view->get_transformed_node().get(); n = n->parent())
            if (n->stringify() == "scottland-live-drag" || n->stringify() == "move-drag")
            {
                move = std::dynamic_pointer_cast<wf::scene::transformer_base_node_t>(n->shared_from_this());
                break;
            }
        for (auto n = frame->parent(); n && n != view->get_transformed_node().get() && n != move.get(); n = n->parent())
        {
            auto a = n->to_global({r.x1, r.y1}), b = n->to_global({r.x2, r.y2});
            r = {a.x, a.y, b.x, b.y};
        }
        if (move && output)
        {
            auto shown = move->get_bounding_box(), inner = move->get_children_bounding_box();
            if (inner.width > 0 && inner.height > 0)
            {
                auto origin = wf::origin(output->get_layout_geometry());
                float sx = float(shown.width) / inner.width, sy = float(shown.height) / inner.height;
                r = {shown.x + (r.x1 - inner.x) * sx - origin.x, shown.y + (r.y1 - inner.y) * sy - origin.y,
                    shown.x + (r.x2 - inner.x) * sx - origin.x, shown.y + (r.y2 - inner.y) * sy - origin.y};
            }
        }
        return r;
    }

    std::vector<scottland::goo::source_t> goo_sources(wf::output_t *output)
    {
        std::vector<scottland::goo::source_t> result;
        // FS1 is the authoritative fullscreen focus, including transparent fullscreen clients.
        if (in_focus_mode(output))
        {
            auto g = output->get_relative_geometry();
            scottland::goo::source_t island;
            island.emitter = false;
            island.rect = {g.width / 2.f, g.height / 2.f, g.width / 2.f, g.height / 2.f};
            island.liquid = {0, 0, 0, 0};
            result.push_back(island);
            append_hint_goo(output, result);
            return result;
        }
        for (auto& [id, state] : model.windows)
        {
            auto v = wf::toplevel_cast(state.view.lock());
            if (!v || !v->is_mapped()) continue;
            auto app_link = model.widgets.find(id);
            auto widget_link = link_of_widget(v);
            // Lifecycle owns the visible form. The scene check below also handles transient
            // renderer leases (preview/morph) without creating another lifecycle owner.
            if ((app_link != model.widgets.end() && app_link->second.docked() && !widget_transitions.count(id)) ||
                (widget_link && (widget_link->away || widget_link->lifecycle == widget_link_t::lifecycle_t::closing)))
                continue;
            auto move = v->get_transformed_node()->get_transformer<wf::scene::transformer_base_node_t>(
                "scottland-live-drag");
            auto frame = v->get_transformed_node()->get_transformer<scottland::frame_t>("scottland-scale");
            // Client move requests still use stock Wayfire, whose transformer is registered
            // by type. Find that public node in the frame's chain when no live drag exists.
            if (!move && frame)
                for (auto n = frame->parent(); n && n != v->get_transformed_node().get(); n = n->parent())
                    if (n->stringify() == "scottland-live-drag" || n->stringify() == "move-drag")
                    {
                        move = std::dynamic_pointer_cast<wf::scene::transformer_base_node_t>(n->shared_from_this());
                        break;
                    }
            if (!move && v->get_output() != output)
                continue;
            auto root = v->get_root_node();
            bool visible = true;
            for (wf::scene::node_t *n = root.get(); n; n = n->parent())
                if (!n->is_enabled())
                {
                    visible = false;
                    break;
                }
            if (!visible)
                continue;
            if (!frame || frame->get_alpha() < .01)
                continue;
            auto r = frame->screen_rect();
            float radius = frame->screen_radius();
            // Parent presentation transforms (e.g. Alt declutter) move the rendered island
            // without changing model geometry. Compose them before the cross-output drag.
            for (auto n = frame->parent(); n && n != v->get_transformed_node().get() && n != move.get(); n = n->parent())
            {
                auto a = n->to_global({r.x1, r.y1}), b = n->to_global({r.x2, r.y2});
                if (r.width() > 0) radius *= std::abs((b.x - a.x) / r.width());
                r = {a.x, a.y, b.x, b.y};
            }
            if (move)
            {
                auto shown = move->get_bounding_box(), inner = move->get_children_bounding_box();
                if (inner.width > 0 && inner.height > 0)
                {
                    auto origin = wf::origin(output->get_layout_geometry());
                    float sx = float(shown.width) / inner.width, sy = float(shown.height) / inner.height;
                    r = {shown.x + (r.x1 - inner.x) * sx - origin.x, shown.y + (r.y1 - inner.y) * sy - origin.y,
                         shown.x + (r.x2 - inner.x) * sx - origin.x, shown.y + (r.y2 - inner.y) * sy - origin.y};
                    radius *= sx;
                }
            }
            auto extent = output->get_relative_geometry();
            if (move && (r.x2 < 0 || r.y2 < 0 || r.x1 > extent.width || r.y1 > extent.height))
                continue;
            scottland::goo::source_t s;
            s.id = v->get_id();
            s.shape = frame->body_shape();
            if (s.shape)
            {
                auto own = frame->screen_rect();
                auto body = s.shape->presented_bounds({(own.x1 + own.x2)/2, (own.y1 + own.y2)/2,
                    own.width()/2, own.height()/2});
                s.shape_body = {r.x1 + (body.x - own.x1) * r.width()/own.width(),
                    r.y1 + (body.y - own.y1) * r.height()/own.height(),
                    body.z * r.width()/own.width(), body.w * r.height()/own.height()};
            }

            s.rect = {(r.x1 + r.x2) / 2, (r.y1 + r.y2) / 2, r.width() / 2, r.height() / 2};
            bool attention = needs_attention(widget_link ? widget_link->window_id : id);
            s.liquid = {1, radius, float(s.id) * 1.618f, attention ? 3.f : 1.f};
            auto neutral = scottland::palette.unfocused_edge_tone();
            s.dye = glm::mix(glm::mix(neutral, scottland::palette.accent, float(frame->focus_mix)), scottland::palette.attention,
                             float(frame->attention_mix));
            // Window mode tints the goo with the hint's color (WK14); it has no separate rim.
            if (frame->hint_dye)
            {
                s.dye = *frame->hint_dye;
                s.hinted = true;
            }
            s.dye_strength = scottland::edge_style::tint_strength(
                scottland::palette.unfocused_edge_strength, float(frame->focus_mix),
                float(frame->attention_mix), s.hinted);
            s.state_mix = scottland::state_dye::mix(float(frame->focus_mix),
                float(frame->attention_mix), s.hinted);
            s.neutral_strength = scottland::palette.unfocused_edge_strength;
            if (frame->can_resize())
                s.corners = {frame->cloud[0], frame->cloud[1], frame->cloud[2], frame->cloud[3]};
            s.sides = {frame->side_cloud[0], frame->side_cloud[1], frame->side_cloud[2], frame->side_cloud[3]};
            s.control_extent = radius + scottland::CORNER_EXTRA;
            s.light = scottland::palette.light;
            s.scale = frame->halo_scale();
            s.swell = frame->swell;
            s.attention = attention;
            s.grabbed = (model.drag.started && model.drag.origin.view == id) || frame->is_pressed() || frame->is_lifted();
            auto own = frame->screen_rect();
            auto dot = frame->dot_center();
            float dot_x = r.x1 + (dot.x - own.x1) * r.width() / std::max(own.width(), .001);
            float dot_y = r.y1 + (dot.y - own.y1) * r.height() / std::max(own.height(), .001);
            s.dot = {dot_x, dot_y, float(frame->dot_glow), scottland::DOT_RADIUS};
            result.push_back(s);
        }
        std::map<wf::scene::node_t*, uint64_t> roots;
        for (auto &s : result)
            if (auto v = model.windows.at(s.id).view.lock())
                roots[(drag->is_live() && drag->view.get() == v.get()) ? v->get_transformed_node().get() :
                    v->get_root_node().get()] = s.id;
        std::map<uint64_t, size_t> order;
        std::function<void(wf::scene::node_t*)> walk = [&](auto n)
        {
            if (!n->is_enabled()) return;
            if (auto i = roots.find(n); i != roots.end()) order[i->second] = order.size();
            for (auto &child : n->get_children()) walk(child.get());
        };
        walk(wf::get_core().scene().get());
        std::stable_sort(result.begin(), result.end(), [&](auto &a, auto &b) { return order[a.id] < order[b.id]; });
        append_hint_goo(output, result);
        return result;
    }
    #include "widget-spawn.hpp"
    #include "widget-presentation.hpp"
    #include "widget-peek.hpp"

    bool needs_attention(uint64_t window) const
    {
        auto found = model.windows.find(window);
        return found != model.windows.end() && !found->second.attention.empty();
    }

    /** Wayfire facts enter the model here, on map/geometry/title/focus signals. Rendering does
     *  not become a second owner: transformations and disable leases follow model targets. */
    void observe_view(wayfire_toplevel_view view)
    {
        if (!view || !view->is_mapped())
        {
            return;
        }

        auto& state = model.windows[view->get_id()];
        state.view = view->weak_from_this();
        state.app_id = view->get_app_id();
        state.title = view->get_title();
        state.pid = view_pid(view);
        auto g = view->get_geometry();
        state.geometry = g;
        state.above = view->has_data("wm-actions-above");
        state.zone = placement_of(view).zone;
        state.focused = wf::get_core().seat->get_active_view() == view;
    }

    void move_window(wayfire_toplevel_view view, double x, double y)
    {
        observe_view(view);
        auto& state = model.windows[view->get_id()];
        state.geometry.x = x;
        state.geometry.y = y;
        view->move(state.geometry.x, state.geometry.y);
        publish_model();
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
    void show_attention(uint64_t window, bool raised = false)
    {
        bool on   = needs_attention(window);
        auto link = model.widgets.find(window);
        auto view = view_by_id(window);
        bool widgetized = (link != model.widgets.end()) && !link->second.previewing() &&
            !widget_transitions.count(window);
        if (view && view->is_mapped())
        {
            if (auto frame = frame_of(view, false))
            {
                frame->set_attention(on && !widgetized);
            }
        }

        if (link != model.widgets.end())
        {
            // A collapsed widget shows expanded for a while; a hidden one comes in from its edge.
            if (raised && link->second.docked() &&
                (link->second.collapsed || (shown_widget_mode() == widget_mode_t::hidden)))
            {
                link->second.peek_attention_due = now_msec() + uint32_t(widget_attention_peek_duration);
                update_widget_peeks();
            }
            if (auto widget = wf::toplevel_cast(link->second.widget.lock()))
            {
                if (auto frame = frame_of(widget))
                {
                    frame->set_attention(on && widgetized);
                }
            }

            announce_widgets();
        }

        reconcile_rail_slides();  // a hidden widget peeks in, or goes, with its attention
        publish_model();
    }

    /** The user went to it (focused its widget, or its window): attention is answered. */
    void clear_attention(uint64_t window)
    {
        bool had = needs_attention(window);
        if (auto state = model.windows.find(window); state != model.windows.end())
        {
            state->second.attention.clear();
        }
        if (had)
        {
            show_attention(window);
        }
    }

    // Super+M (WG16): a tap cycles the widgets expanded -> collapsed (icons) -> hidden (slid off
    // their screen edges) -> expanded. Holding it is momentary until it's released: from
    // expanded it hides them, from collapsed or hidden it expands them; the release returns to
    // the mode they were in. Widgets learn their presentation from the widget service
    // (Minimized, and the state file). It's a mode: a widget made in it starts in it.
    wf::option_wrapper_t<wf::keybinding_t> minimize_key{"scottland/minimize_widget"};
    wf::option_wrapper_t<int> minimize_hold_delay{"scottland/minimize_hold_delay"};
    // One activation per held key, across devices. A duplicate down (including one from
    // another device) is not a new press. Only the last device's release rearms it; no timer
    // filters intentional press-release-press sequences. Keep old keys until release if the
    // binding changes while held.
    struct minimize_press_t
    {
        std::set<wlr_input_device*> devices;
        bool activated = false;
        bool consumed = false;
    };
    std::map<uint32_t, minimize_press_t> minimize_presses;
    uint64_t minimize_edge = 0;
    uint32_t minimize_event_time = 0;
    std::string minimize_device;
    // The press being judged: released before the hold delay it's a tap, else a hold.
    uint32_t minimize_gesture = 0;
    uint32_t minimize_gesture_pressed = 0;  // the press's own input time
    wf::wl_timer<false> minimize_hold;

    wf::signal::connection_t<wf::input_event_signal<wlr_keyboard_key_event>> on_minimize_edge =
        [=] (wf::input_event_signal<wlr_keyboard_key_event> *ev)
    {
        if (key_layers.handles(ev) || (ev->mode == wf::input_event_processing_mode_t::IGNORE &&
            !minimize_presses.count(ev->event->keycode))) return;
        auto code = ev->event->keycode;
        auto binding = minimize_key.value();
        bool modifiers_held = (wf::get_core().seat->get_keyboard_modifiers() & binding.get_modifiers()) ==
            binding.get_modifiers();
        // Track/log only binding-modified edges or an already tracked press (including its
        // release after Super is up). Plain typing must not become a keystroke trail; duplicate
        // activation diagnostics still retain the entire relevant press across devices.
        if (!minimize_presses.count(code) && ((code != binding.get_key()) || !modifiers_held))
        {
            return;
        }

        auto& press = minimize_presses[code];
        bool down = ev->event->state == WL_KEYBOARD_KEY_STATE_PRESSED;
        bool duplicate = down && press.devices.count(ev->device);
        if (down)
        {
            press.devices.insert(ev->device);
        } else
        {
            press.devices.erase(ev->device);
        }

        std::ostringstream device;
        device << (ev->device && ev->device->name ? ev->device->name : "unknown")
               << "@" << static_cast<void*>(ev->device);
        minimize_device = device.str();
        minimize_event_time = ev->event->time_msec;
        LOGI("scottland: minimize-key edge=", ++minimize_edge, " device=", minimize_device,
            " time_msec=", minimize_event_time, " received_msec=", now_msec(), " key=", code,
            " state=", down ? "press" : "release", " duplicate_down=", duplicate,
            " held_devices=", press.devices.size(), " activated=", press.activated,
            " mode=", widget_mode_name(model.widget_mode), " processing=", (int)ev->mode);
        if (press.devices.empty())
        {
            minimize_presses.erase(code);
            if (code == minimize_gesture)
            {
                end_minimize_gesture();
            }
        }
    };

    wf::signal::connection_t<wf::input_device_removed_signal> on_minimize_device_removed =
        [=] (wf::input_device_removed_signal *ev)
    {
        auto device = ev->device->get_wlr_handle();
        for (auto it = widget_return_keys.begin(); it != widget_return_keys.end();)
        {
            it = it->first == device ? widget_return_keys.erase(it) : std::next(it);
        }

        for (auto it = minimize_presses.begin(); it != minimize_presses.end();)
        {
            if (it->second.devices.erase(device))
            {
                LOGI("scottland: minimize-key device-removed key=", it->first,
                    " device=", static_cast<void*>(device),
                    " received_msec=", now_msec(), " held_devices=", it->second.devices.size());
            }

            if (it->second.devices.empty())
            {
                bool gesture = it->first == minimize_gesture;
                it = minimize_presses.erase(it);
                if (gesture) end_minimize_gesture(false);
            } else
            {
                ++it;
            }
        }
    };

    wf::signal::connection_t<wf::input_device_added_signal> on_input_device_added =
        [=] (wf::input_device_added_signal *ev)
    {
        if (!ev || !ev->device) return;
        auto handle = ev->device->get_wlr_handle();
        if (!handle || !handle->name) return;
        const auto class_name = input_device_kind(handle);
        const std::string kind = class_name == "pointer" ? "touchpad" :
            ((class_name == "touch" || class_name == "tablet") ? "touchscreen" : "");
        if (kind.empty()) return;

        const std::string name = handle->name;
        if (!valid_input_device_name(name)) return;
        auto disabled = input_disabled_name(kind);
        if (!scottland::input::disabled_record_matches(kind, disabled, class_name, name)) return;

        if (!set_input_device_enabled(*ev->device, false))
        {
            LOGE("scottland: could not keep newly added ", kind, " device disabled: ", name);
        }
    };

    wf::signal::connection_t<wf::reload_config_signal> on_input_config_reloaded =
        [=] (wf::reload_config_signal *)
    {
        // Wayfire updates input options in its earlier reload listener. Reapply synchronously here,
        // so the stored off state wins before the event loop handles the next input event.
        const std::string kind = "touchpad";
        const auto disabled = input_disabled_name(kind);
        auto devices = wf::get_core().get_input_devices();
        scottland::input::apply_disabled_record(kind, disabled, devices,
            [] (const auto& device) -> std::optional<std::string>
            {
                if (!device) return std::nullopt;
                auto handle = device->get_wlr_handle();
                if (!handle || !handle->name) return std::nullopt;
                return std::string(handle->name);
            },
            [] (const auto& device)
            {
                return device ? input_device_kind(device->get_wlr_handle()) : std::string{};
            },
            [=] (auto& device)
            {
                if (!device || !set_input_device_enabled(*device, false))
                {
                    auto handle = device ? device->get_wlr_handle() : nullptr;
                    LOGE("scottland: could not restore touchpad disabled after config reload: ",
                        handle && handle->name ? handle->name : "(unnamed)");
                }
            });
    };

    wf::key_callback on_minimize_key = [=] (const wf::keybinding_t& key)
    {
        auto& press = minimize_presses[key.get_key()];
        if (key.get_key() && press.activated)
        {
            LOGI("scottland: minimize-key ignored-duplicate edge=", minimize_edge,
                " device=", minimize_device, " time_msec=", minimize_event_time,
                " key=", key.get_key(), " modifiers=", key.get_modifiers(),
                " mode=", widget_mode_name(model.widget_mode));
            return press.consumed;
        }

        // Modifier-only bindings are invoked on release by Wayfire, with keycode zero.
        press.activated = key.get_key() != 0;
        bool any = false;
        for (auto& [id, link] : model.widgets)
        {
            any |= link.docked();
        }

        if (!any)
        {
            LOGI("scottland: minimize-key no-widgets edge=", minimize_edge,
                " device=", minimize_device, " time_msec=", minimize_event_time);
            return false;  // no widgets: the key goes on to the app
        }

        press.consumed = true;
        LOGI("scottland: minimize-key activation edge=", minimize_edge, " device=", minimize_device,
            " time_msec=", minimize_event_time, " key=", key.get_key(),
            " modifiers=", key.get_modifiers(), " mode=", widget_mode_name(model.widget_mode));
        if (!key.get_key())
        {
            cycle_widget_mode();  // already released: a tap
            return true;
        }

        // A press of another binding key while one is judged ends that one first.
        if (minimize_gesture) end_minimize_gesture(false);
        minimize_gesture = key.get_key();
        minimize_gesture_pressed = minimize_event_time;
        minimize_hold.set_timeout(std::max(1, int(minimize_hold_delay)), [=] ()
        {
            model.widget_mode_held = model.widget_mode == widget_mode_t::expanded ?
                widget_mode_t::hidden : widget_mode_t::expanded;
            LOGI("scottland: minimize-key hold mode=", widget_mode_name(model.widget_mode),
                " shown=", widget_mode_name(*model.widget_mode_held));
            apply_widget_mode();
        });
        return true;
    };

    /** The judged Super+M press was released: before the hold delay it was a tap; after it, the
     *  momentary mode ends and the mode it was in comes back. The key events' own times decide,
     *  so a busy main loop can't turn a quick tap into a hold (or a long press into a tap). */
    void end_minimize_gesture(bool released = true)
    {
        minimize_gesture = 0;
        bool tap = released && int32_t(minimize_event_time - minimize_gesture_pressed) <
            std::max(1, int(minimize_hold_delay));
        bool pending = minimize_hold.is_connected();
        minimize_hold.disconnect();
        if (model.widget_mode_held)
        {
            model.widget_mode_held.reset();
            LOGI("scottland: minimize-key hold-release mode=", widget_mode_name(model.widget_mode));
            if (!tap) apply_widget_mode();
        }

        if (tap)
        {
            cycle_widget_mode();
        } else if (pending)
        {
            LOGI("scottland: minimize-key late-hold mode=", widget_mode_name(model.widget_mode));
        }
    }

    void cycle_widget_mode()
    {
        auto next = model.widget_mode == widget_mode_t::expanded ? widget_mode_t::collapsed :
            model.widget_mode == widget_mode_t::collapsed ? widget_mode_t::hidden : widget_mode_t::expanded;
        LOGI("scottland: minimize-key tap mode=", widget_mode_name(next));
        set_widget_mode(next);
    }

    void set_widget_mode(widget_mode_t mode)
    {
        model.widget_mode = mode;
        model.widget_mode_held.reset();
        apply_widget_mode(true);
    }

    /** The mode shown now: a held Super+M's, else the chosen one (Window mode expands on top). */
    widget_mode_t shown_widget_mode() const
    {
        return model.widget_mode_held.value_or(model.widget_mode);
    }

    /** Window mode (Alt) and holding Super+M from collapsed or hidden show every widget
     *  expanded, until released. Temporary presentation: the mode itself never changes. */
    bool widgets_revealed() const
    {
        return window_keys.active || (model.widget_mode_held == widget_mode_t::expanded);
    }

    bool widget_revealed(const widget_link_t& link) const
    {
        return (link.docked() || link.previewing()) && widgets_revealed() &&
            (link.collapsed || (shown_widget_mode() == widget_mode_t::hidden));
    }

    /** Bring every widget's presentation and place in line with the mode. A mode change
     *  (`ending_peeks`) also ends hover and attention peeks, as Super+M always has. */
    void apply_widget_mode(bool ending_peeks = false)
    {
        for (auto& [id, link] : model.widgets)
        {
            if (ending_peeks) reset_widget_peek(link);
            // Hidden widgets keep their look while they slide off, then become expanded there
            // (finish_rail_slide); previews show what a drop will show.
            bool collapsed = model.widget_mode == widget_mode_t::collapsed ||
                ((model.widget_mode == widget_mode_t::hidden) && link.docked() && link.collapsed);
            bool peek = widget_revealed(link) || (!ending_peeks && link.peek);
            if (wanted_place(link, peek) != rail_place_t::away) return_from_away(link);
            set_widget_presentation(link, collapsed, peek);  // snapshot before publication
        }

        update_widget_peeks();
        reconcile_rail_slides();
        announce_widgets();
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
        if ((!link || link->previewing()) && from_active_app)
        {
            wf::get_core().default_wm->focus_raise_view(view);
            return;
        }

        model.windows[view->get_id()].attention.insert("builtin:activation");
        show_attention(view->get_id(), true);
    }

    // Requests from the desktop (a switcher) for a widgetized window go to its widget: its
    // window is hidden.
    wf::signal::connection_t<wf::view_focus_request_signal> on_focus_request =
        [=] (wf::view_focus_request_signal *ev)
    {
        auto view = wf::toplevel_cast(ev->view);
        if (auto widget_link = view ? link_of_widget(view) : nullptr;
            widget_link && (widget_link->away || in_focus_mode(widget_link->output)) && !window_keys.active)
        {
            ev->carried_out = true;
            return;
        }
        auto link = view ? link_of_window(view) : nullptr;
        if (!link || link->previewing() || ev->carried_out)
        {
            return;
        }

        ev->carried_out = true;
        if (auto widget = link->widget.lock())
        {
            wf::get_core().default_wm->focus_raise_view(widget);
        }
    };

    /** IPC scottland/attention {window, attention, source}: another process says a window needs
     *  the user (the widget service for desktop notifications; configured attention sources,
     *  docs/attention.md). `source` names who says so (default "ipc"); turning attention off
     *  takes back only that source's. Replies in_front when the user is already on it. */
    wf::ipc::method_callback attention_method = [=] (wf::json_t data) -> wf::json_t
    {
        if (!data.has_member("window") || !data["window"].is_int() || !data.has_member("attention") ||
            !data["attention"].is_bool())
        {
            return wf::ipc::json_error("attention needs integer \"window\" and boolean \"attention\"");
        }

        uint64_t window = (uint64_t)data["window"].as_int64();
        std::string source = (data.has_member("source") && data["source"].is_string()) ?
            data["source"].as_string() : "ipc";
        if (source.rfind("builtin:", 0) == 0)
        {
            return wf::ipc::json_error("builtin attention sources are owned by the plugin");
        }
        if (!model.windows.count(window))
        {
            return wf::ipc::json_error("no such mapped window");
        }
        if (data["attention"].as_bool())
        {
            auto active = wf::get_core().seat->get_active_view();
            auto link   = model.widgets.find(window);
            if (active && ((active->get_id() == window) ||
                ((link != model.widgets.end()) && (link->second.widget.lock().get() == active.get()))))
            {
                auto reply = model_snapshot("attention");
                reply["version"] = (int64_t)model.version;
                reply["in_front"] = true;  // already in front of the user: nothing to show, answered
                return reply;
            }

            model.windows[window].attention.insert(source);
            show_attention(window, true);
        } else if (model.windows[window].attention.erase(source))
        {
            show_attention(window);  // only that source's is taken back
        }

        auto reply = model_snapshot("attention");
        reply["version"] = (int64_t)model.version;
        return reply;
    };

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
        publish_model();
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
            model.windows[ev->view->get_id()].attention.insert("builtin:urgency");
        } else
        {
            model.windows[ev->view->get_id()].attention.erase("builtin:urgency");
        }

        show_attention(ev->view->get_id(), ev->demands_attention);
    };
    wf::wl_timer<true> widget_watchdog;
    static constexpr uint32_t WIDGET_ADOPT_MS = 8000;  // no widget window by then: give up, restore

    /** The app's process. X11 windows all belong to the XWayland server's client, so theirs is the
     *  _NET_WM_PID they declare (X11 apps can already see each other; no stronger check exists). */
    static pid_t surface_pid(wlr_surface *surface)
    {
        pid_t pid = 0;
        if (!surface)
        {
            return pid;
        }

#if WF_HAS_XWAYLAND
        if (auto xsurface = wlr_xwayland_surface_try_from_wlr_surface(surface))
        {
            return xsurface->pid;
        }
#endif
        wl_client_get_credentials(wl_resource_get_client(surface->resource), &pid, nullptr, nullptr);

        return pid;
    }

    static pid_t view_pid(wayfire_view view)
    {
        return surface_pid(view ? view->get_wlr_surface() : nullptr);
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
        auto found = view ? model.widgets.find(view->get_id()) : model.widgets.end();
        return found == model.widgets.end() ? nullptr : &found->second;
    }

    widget_link_t *link_of_widget(wayfire_view view)
    {
        for (auto& [id, link] : model.widgets)
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

    // Rendering resources, not desktop state: each entry holds exactly one Wayfire disable.
    std::set<uint64_t> disabled_nodes;

    void render_hidden(wayfire_view view, bool hidden)
    {
        if (!view || (bool(disabled_nodes.count(view->get_id())) == hidden))
        {
            return;
        }

        wf::scene::set_node_enabled(view->get_root_node(), !hidden);
        if (hidden)
        {
            disabled_nodes.insert(view->get_id());
        } else
        {
            disabled_nodes.erase(view->get_id());
        }
    }

    /** Apply a lifecycle to the scene. All logical lifecycle changes go through here; the
     *  disable leases above only keep Wayfire's reference counts balanced. */
    void transition_widget(widget_link_t& link, widget_link_t::lifecycle_t next)
    {
        if (next != link.lifecycle) reconcile_rail_slides();  // docked widgets have a place
        if (next != widget_link_t::lifecycle_t::docked && next != widget_link_t::lifecycle_t::previewing)
            stop_widget_transition(wf::toplevel_cast(link.window.lock()));
        link.lifecycle = next;
        // A widget never scales (WG4), so its window keeps no pin: whichever way it leaves the rail
        // it follows its new zone or that zone's remembered pin. The zone it left already has the
        // pin in its memory (WP1): drag starts and cycles record it before the window docks.
        if (auto window = wf::toplevel_cast(link.window.lock());
            window && next == widget_link_t::lifecycle_t::docked && model.windows.count(window->get_id()))
            pin_scale(window, std::nullopt);
        bool waiting_form = widget_transitions.count(link.window_id) && entering_widget(link.window_id);
        render_hidden(link.window.lock(), (link.docked() && !waiting_form) || next == widget_link_t::lifecycle_t::handed_over);
        auto widget = wf::toplevel_cast(link.widget.lock());
        bool hidden = (!link.docked() && next != widget_link_t::lifecycle_t::handed_over) || link.away || waiting_form;
        if (!link.docked() && !link.previewing()) stop_widget_transition(widget);
        render_hidden(widget, hidden);
        if (widget)
        {
            if (auto frame = frame_of(widget))
            {
                frame->alpha = hidden ? 0.0 : 1.0;
                apply_opacity(widget);
                frame->damage();
            }
        }
    }

    /** Process exit is a model input, not a fact discovered while serializing a snapshot. */
    void watch_process(const widget_process& process)
    {
        if (process->pidfd < 0)
        {
            return;
        }
        process->owner = this;
        process->exit_watch = wl_event_loop_add_fd(wf::get_core().ev_loop, process->pidfd,
            WL_EVENT_READABLE, [] (int, uint32_t, void *data)
        {
            auto process = static_cast<widget_process_t*>(data);
            wl_event_source_remove(process->exit_watch);
            process->exit_watch = nullptr;
            process->pid = 0;
            process->owner->publish_model();
            return 0;
        }, process.get());
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

        process->cancelled = true; // a pending launch is ended when its pidfd arrives
        if (!process->unit.empty())
        {
            // Stopping a scope that already ended (the widget exited) does nothing.
            wf::json_t request;
            request["stop"] = process->unit;
            if (!send_widget_spawn(request))
            {
                // A failed broker must not leave a scope's descendants alive.
                // The synchronous path is only recovery, never a healthy conversion.
                wf::get_core().run("systemctl --user stop --no-block " + shell_quote(process->unit) +
                    " >/dev/null 2>&1");
            }
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
    void widgetize(wayfire_toplevel_view view, bool preview = false, std::optional<std::string> rail = {},
        const char *reason = "rail-drop")
    {
        if (auto existing = link_of_window(view))
        {
            if (existing->previewing() && !preview)
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
        link.make_room_pending = !preview;
        link.drop   = {geometry.x + geometry.width / 2.0, geometry.y + geometry.height / 2.0};
        link.rail   = rail ? *rail : (link.drop.x < width / 2 ? "left" : "right");
        link.launched_at = now_msec();
        LOGI("scottland: widgetize window=", link.window_id, " reason=", reason,
            " preview=", preview, " rail=", link.rail,
            " geometry=", geometry.x, ",", geometry.y, ",", geometry.width, ",", geometry.height);

        wf::json_t context;
        context["id"]     = std::to_string(link.window_id);
        context["window"] = (int64_t)link.window_id;
        context["app_id"] = view->get_app_id();
        context["title"]  = view->get_title();
        context["pid"]    = (int64_t)view_pid(view);
        context["rail"]   = link.rail;
        // So it starts in the mode, even as a preview (expanded while Window mode shows them so).
        link.collapsed = model.widget_mode == widget_mode_t::collapsed;
        link.peek = widget_revealed(link);
        context["minimized"] = link.minimized();
        auto process = std::make_shared<widget_process_t>();
        const char *display = getenv("WAYLAND_DISPLAY");
        process->unit = "scottland-widget-" + std::string(display ? display : "wayland") + "-" +
            std::to_string(link.window_id) + "-" + std::to_string(link.launched_at) + ".scope";
        for (auto& c : process->unit)
        {
            c = (std::isalnum((unsigned char)c) || (c == '-') || (c == '.')) ? c : '_';
        }

        context["unit"] = process->unit;  // the launcher runs the widget in this scope
        if (!send_widget_spawn(context))
        {
            LOGE("scottland: couldn't queue a widget for ", view->get_app_id());
            return;
        }
        widget_spawn_pending[process->unit] = process;
        link.launcher  = process;
        if (!preview) begin_window_widget_transition(view);
        transition_widget(link, preview ? widget_link_t::lifecycle_t::previewing : widget_link_t::lifecycle_t::docked);

        if (!preview)
        {
            if (wf::get_core().seat->get_active_view() == view)
            {
                wf::get_core().seat->refocus();
            }
        }

        model.widgets[link.window_id] = std::move(link);
        announce_widgets();

        if (!widget_watchdog.is_connected())
        {
            widget_watchdog.set_timeout(500, [=] () { return check_widget_launches(); });
        }
    }

    /** Widgets whose window never appeared: restore their app window. */
    bool check_widget_launches()
    {
        bool waiting = false;
        for (auto it = model.widgets.begin(); it != model.widgets.end();)
        {
            auto& link = it->second;
            if (!link.widget.lock() && (now_msec() - link.launched_at > WIDGET_ADOPT_MS))
            {
                LOGE("scottland: no widget window appeared for window ", link.window_id, "; restoring it");
                transition_widget(link, widget_link_t::lifecycle_t::restoring);
                model.windows[link.window_id].pending_rail.reset();
                end_process(link.launcher, 0);  // a late widget would show up unlinked
                it = model.widgets.erase(it);
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
        for (auto& [id, link] : model.widgets)
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
            if (link.previewing())
            {
                // Unseen until the drop: the dragged window's frame shows it as it morphs.
                transition_widget(link, link.lifecycle);
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

            if ((model.drag.last_drop.became == 0) && (model.drag.last_drop.first_view == link.window_id))
            {
                model.drag.last_drop.became = view->get_id();  // dropped before it appeared: the move goes on through it
            }

            // A late widget arrives directly hidden during fullscreen focus, never flashing
            // or taking the fullscreen window's attention before its first slide.
            link.away = in_focus_mode(output) && !window_keys.active;
            transition_widget(link, link.lifecycle);
            reconcile_rail_slides();  // e.g. one docked while widgets are hidden slides off
            keep_above(view);
            place_cycled_widget(view, link.window_id, link.rail);
            place_widget(view, output, link);
            auto pending_geometry = view->toplevel()->pending().geometry;
            if (view->get_geometry() == pending_geometry && link.make_room_pending)
                make_room_for_arrival(view, link);
            set_scale(view, 1.0);
            show_attention(link.window_id);  // asked before its widget appeared
            return true;
        }

        return false;
    }

    static constexpr int WIDGET_INSET = (int)scottland::SWOLLEN + 3;  // room for the halo at its widest

    /** Per-session files; headless tests keep them in their owned session directory. */
    static std::string runtime_file(const std::string& suffix)
    {
        const char *session = getenv("SCOTTLAND_SESSION_DIR");
        const char *runtime = getenv("XDG_RUNTIME_DIR");
        const char *display = getenv("WAYLAND_DISPLAY");
        if (session && *session)
        {
            return std::string(session) + "/" + (display ? display : "wayland") + suffix;
        }
        return std::string(runtime ? runtime : "/tmp") + "/scottland/" + (display ? display : "wayland") + suffix;
    }

    /** Is the process part of a widget (in a widget scope: one still closing after an unload)? */
    static bool runs_as_widget(pid_t pid)
    {
        std::ifstream cgroup("/proc/" + std::to_string(pid) + "/cgroup");
        std::string line;
        while (std::getline(cgroup, line))
        {
            if (line.find("/scottland-widget-") != std::string::npos)
            {
                return true;
            }
        }

        return false;
    }

    /** On load: a window whose center is on a rail is a widget (WG1), however it got there (a
     *  reload that couldn't hand widgets over left their windows where they were). */
    void widgetize_windows_on_rails()
    {
        for (auto& any : wf::get_core().get_all_views())
        {
            auto view = wf::toplevel_cast(any);
            if (!view || !view->is_mapped() || !can_widgetize(view) || link_of_window(view) || is_widget(view) ||
                (view->role != wf::VIEW_ROLE_TOPLEVEL) || runs_as_widget(view_pid(view)))
            {
                continue;
            }

            auto g = view->get_geometry();
            double width = view->get_output()->get_relative_geometry().width;
            if (place_at(view->get_output(), std::clamp(g.x + g.width / 2.0, 0.0, width - 1), width).zone == zone_t::widget)
            {
                LOGI("scottland: window ", view->get_id(), " (", view->get_title(), ") is on a rail: a widget again");
                widgetize(view, false, {}, "load-rail-recovery");
            }
        }
    }

    /** One-time upgrade input: the previous launcher left identity in its process environment
     *  and a launch-unit-qualified file. Never used by ordinary snapshots or new launches. */
    void migrate_widget_identity(widget_link_t& link, wayfire_toplevel_view widget,
        const std::string& unit)
    {
        std::map<std::string, std::string> env;
        std::ifstream environment("/proc/" + std::to_string(view_pid(widget)) + "/environ");
        std::string field;
        while (std::getline(environment, field, '\0'))
        {
            auto equal = field.find('=');
            if (equal != std::string::npos) env[field.substr(0, equal)] = field.substr(equal + 1);
        }
        auto session_path = runtime_file("");
        auto slash = session_path.find_last_of('/');
        auto state = session_path.substr(0, slash) + "/widgets/" + session_path.substr(slash + 1) + "/" + unit;
        if ((env["SCOTTLAND_WIDGET_WINDOW"] == std::to_string(link.window_id)) &&
            (env["SCOTTLAND_WIDGET_STATE"] == state + ".json"))
        {
            link.desktop = env["SCOTTLAND_WIDGET_DESKTOP"];
            link.name = env["SCOTTLAND_WIDGET_NAME"];
            link.icon = env["SCOTTLAND_WIDGET_ICON"];
            std::ifstream arguments("/proc/" + std::to_string(view_pid(widget)) + "/cmdline");
            while (std::getline(arguments, field, '\0'))
            {
                link.card |= field == env["SCOTTLAND_HOOKS"] + "/widgets/card/shell.qml" ||
                    field == "/usr/lib/scottland/widgets/card/shell.qml";
            }
        }
        std::ifstream launch(state + ".launch.json");
        std::string text((std::istreambuf_iterator<char>(launch)), std::istreambuf_iterator<char>());
        wf::json_t identity;
        if (!wf::json_t::parse_string(text, identity) && identity.has_member("unit") &&
            (identity["unit"].as_string() == unit) && identity.has_member("desktop"))
        {
            link.desktop = identity["desktop"].as_string();
        }
        auto window = link.window.lock();
        if (link.name.empty() && window) link.name = window->get_app_id();
        if (link.icon.empty() && window) link.icon = window->get_app_id();
    }

    /** After a reload: take over the widgets the previous plugin handed over. */
    void take_handover()
    {
        auto collapsed = runtime_file(".widgets-collapsed");
        model.widget_mode = access(collapsed.c_str(), F_OK) == 0 ? widget_mode_t::collapsed : widget_mode_t::expanded;
        std::remove(collapsed.c_str());
        auto path = runtime_file(".widget-handover.json");
        std::ifstream in(path);
        if (!in)
        {
            return;
        }

        std::string text((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
        in.close();
        std::remove(path.c_str());
        wf::json_t entries;
        if (wf::json_t::parse_string(text, entries))
        {
            LOGE("scottland: unreadable widget handover");
            return;
        }

        if (!entries.is_array())
        {
            model.version = std::max(model.version, (uint64_t)entries["version"].as_int64());
            auto mode = entries.has_member("widget_mode") ?
                parse_widget_mode(entries["widget_mode"].as_string()) : std::nullopt;
            model.widget_mode = mode ? *mode :
                entries["collapsed"].as_bool() ? widget_mode_t::collapsed : widget_mode_t::expanded;
            if (entries.has_member("hint_width")) model.hint_width = std::clamp(entries["hint_width"].as_int(), 1, 7);
            auto windows = entries["windows"];
            for (size_t i = 0; i < windows.size(); i++)
            {
                auto entry = windows[i];
                auto found = model.windows.find((uint64_t)entry["id"].as_int64());
                if (found == model.windows.end())
                {
                    continue;
                }
                found->second.scale = entry["scale"].as_double();
                if (entry.has_member("placement")) found->second.placement = read_memory(entry["placement"]);
                if (entry.has_member("pending_rail")) found->second.pending_rail = scottland::windowing::point{
                    entry["pending_rail"]["x"].as_double(), entry["pending_rail"]["y"].as_double()};
                if (entry.has_member("pinned_scale") && entry["pinned_scale"].is_double())
                    found->second.pinned_scale = scottland::windowing::valid_pin(entry["pinned_scale"].as_double());
                if (entry.has_member("paired_placement"))
                {
                    auto p = entry["paired_placement"];
                    // Geometry coordinates serialize as doubles in the desktop snapshot.
                    found->second.paired_placement = paired_placement_t{
                        {p["x"].as_double(), p["y"].as_double(), p["width"].as_double(), p["height"].as_double()},
                        p["output"].as_string(), p.has_member("pin") ?
                            scottland::windowing::valid_pin(p["pin"].as_double()) : std::nullopt};
                }
                auto sources = entry["attention"];
                for (size_t j = 0; j < sources.size(); j++)
                {
                    found->second.attention.insert(sources[j].as_string());
                }
            }
            entries = entries["links"];
        }

        for (size_t i = 0; i < entries.size(); i++)
        {
            wf::json_t entry = entries[i];
            auto window = view_by_id((uint64_t)entry["window"].as_int64());
            auto widget = view_by_id((uint64_t)entry["widget"].as_int64());
            auto process = std::make_shared<widget_process_t>();
            // New handovers transfer an already-open handle inside this compositor process.
            process->pidfd = entry.has_member("pidfd") ? (int)entry["pidfd"].as_int64() : -1;
            if (!window || !window->is_mapped())
            {
                continue;
            }

            if (!widget || !widget->is_mapped())
            {
                wf::scene::set_node_enabled(window->get_root_node(), true);  // the handed-over disable
                continue;
            }

            widget_link_t link;
            link.window_id = window->get_id();
            link.window = window->weak_from_this();
            link.widget = widget->weak_from_this();
            disabled_nodes.insert(window->get_id());  // adopt the previous renderer's lease
            link.output = widget->get_output();
            link.away = !link.output->node_for_layer(wf::scene::layer::TOP)->is_enabled();
            transition_widget(link, widget_link_t::lifecycle_t::docked);
            rail_slides[link.window_id].snap = true;  // in its place for the mode, without sliding
            link.rail   = entry["rail"].as_string();
            link.drop   = {entry["x"].as_double(), entry["y"].as_double()};
            link.collapsed   = entry["minimized"].as_bool();
            link.touch_drag  = entry.has_member("touch_drag") && entry["touch_drag"].as_bool();
            link.desktop = entry.has_member("desktop") ? entry["desktop"].as_string() : "";
            link.name = entry.has_member("name") ? entry["name"].as_string() : "";
            link.icon = entry.has_member("icon") ? entry["icon"].as_string() : "";
            link.card = entry.has_member("card") && entry["card"].as_bool();
            if (!entry.has_member("desktop"))
            {
                migrate_widget_identity(link, widget, entry["unit"].as_string());
            }
            link.launched_at = now_msec();
            process->pid  = (pid_t)entry["pid"].as_int64();
            process->unit = entry["unit"].as_string();
            if ((process->pidfd < 0) && (process->pid > 1) &&
                (in_scope(process->pid, process->unit) || descends_from(view_pid(widget), process->pid)))
            {
                process->pidfd = pidfd_open(process->pid, 0);
                // Legacy upgrade: validate the live relationship around opening the handle.
                if (!in_scope(process->pid, process->unit) && !descends_from(view_pid(widget), process->pid))
                {
                    if (process->pidfd >= 0) ::close(process->pidfd);
                    process->pidfd = -1;
                }
            }
            if (process->pidfd < 0)
            {
                process->pid = 0;  // unobserved numeric identities must never survive a reload
            }

            watch_process(process);
            link.launcher = process;
            model.widgets[link.window_id] = std::move(link);
            keep_above(widget);
            set_scale(widget, 1.0);
            place_widget(widget, widget->get_output(), model.widgets[window->get_id()]);
        }

        announce_widgets();
    }

    /** Widgets float above all ordinary windows (WG4), in the always-above layer Wayfire's
     *  wm-actions keeps. */
    void keep_above(wayfire_toplevel_view widget)
    {
        if (widget && widget->get_output())
        {
            wf::wm_actions_set_above_state_signal above;
            above.view  = widget;
            model.windows[widget->get_id()].above = true;
            above.above = model.windows[widget->get_id()].above;
            widget->get_output()->emit(&above);
        }
    }

    /** Before mapping, the view's surface accessor is still null. The wl_surface resource
     *  already belongs to its toplevel, though: use Wayfire's public resource-to-view lookup
     *  and read credentials from that surface's wl_client (X11 keeps its declared PID).
     *  This lookup runs only for initial mapping transactions while widget launches exist;
     *  it needs neither unstable Wayfire pre-map signals nor cached client identities. */
    static pid_t mapping_pid(wayfire_toplevel_view view)
    {
        if (!view || view->get_wlr_surface())
        {
            return view_pid(view);
        }

        struct lookup_t
        {
            wayfire_toplevel_view view;
            pid_t pid = 0;
        } lookup{view};
        wl_client *client;
        wl_client_for_each(client, wl_display_get_client_list(wf::get_core().display))
        {
            wl_client_for_each_resource(client, [] (wl_resource *resource, void *data)
            {
                auto& lookup = *static_cast<lookup_t*>(data);
                if ((std::string(wl_resource_get_class(resource)) == "wl_surface") &&
                    (wf::wl_surface_to_wayfire_view(resource) == lookup.view))
                {
                    lookup.pid = surface_pid(wlr_surface_from_resource(resource));
                    return WL_ITERATOR_STOP;
                }

                return WL_ITERATOR_CONTINUE;
            }, &lookup);
            if (lookup.pid)
            {
                break;
            }
        }

        return lookup.pid;
    }

    // Wayfire's place plugin positions every window as it maps (centered, cascaded...), in the
    // transaction that maps it. A widget's window has its place already: recognized as it maps,
    // it gets that place in the same transaction and is marked as positioned (startup-x/y, which
    // place leaves alone), so no other placement ever applies to it.
    wf::signal::connection_t<wf::txn::new_transaction_signal> on_new_transaction =
        [=] (wf::txn::new_transaction_signal *ev)
    {
        if (model.widgets.empty())
        {
            return;
        }

        for (const auto& object : ev->tx->get_objects())
        {
            auto toplevel = std::dynamic_pointer_cast<wf::toplevel_t>(object);
            if (!toplevel || !toplevel->pending().mapped)
            {
                continue;
            }

            auto view = wf::toplevel_cast(wf::find_view_for_toplevel(toplevel));
            if (toplevel->current().mapped)
            {
                // Reconcile client size changes before this transaction commits, using its
                // pending size. Never schedule a second move from a geometry notification.
                auto link = link_of_widget(view);
                auto& pending = toplevel->pending();
                auto& current = toplevel->current().geometry;
                if (link && (drag->view != view) && (view->get_id() != model.drag.widget) &&
                    ((pending.geometry.width != current.width) || (pending.geometry.height != current.height)))
                {
                    auto output = output_alive(link->output) ? link->output : view->get_output();
                    position_widget(view, pending, output, *link);
                }

                continue;
            }

            pid_t pid = mapping_pid(view);
            for (auto& [id, link] : model.widgets)
            {
                if (link.widget.lock() || !link.launcher || !(in_scope(pid, link.launcher->unit) ||
                    (alive(link.launcher) && descends_from(pid, link.launcher->pid))))
                {
                    continue;
                }

                auto output = output_alive(link.output) ? link.output : view->get_output();
                if (!output)
                {
                    break;
                }

                auto& pending = toplevel->pending();
                position_widget(view, pending, output, link);
                // Scottland owns this form's appearance. Wayfire's ordinary map zoom/fade
                // would shrink/fade the app snapshot a second time during the handoff.
                view->set_property("open-animation-type", std::string("none"));
                view->set_property("startup-x", pending.geometry.x);
                view->set_property("startup-y", pending.geometry.y);
                break;
            }
        }
    };

    /** Where a widget `width` x `height` goes: its drop point, its screen-edge side against the
     *  edge, wholly on screen with room for its halo. */
    wf::point_t widget_spot(wf::output_t *output, const widget_link_t& link, int width, int height)
    {
        double screen = output->get_relative_geometry().width;
        wf::pointf_t at = link.drop;
        at.x = link.rail == "right" ? std::max(at.x, screen - width / 2.0) : std::min(at.x, width / 2.0);
        auto area = output->workarea->get_workarea();
        area.x += WIDGET_INSET;
        area.y += WIDGET_INSET;
        area.width  -= 2 * WIDGET_INSET;
        area.height -= 2 * WIDGET_INSET;
        double x = std::clamp(at.x - width / 2.0, (double)area.x, std::max((double)area.x, (double)(area.x + area.width - width)));
        double y = std::clamp(at.y - height / 2.0, (double)area.y, std::max((double)area.y, (double)(area.y + area.height - height)));
        return {(int)std::round(x), (int)std::round(y)};
    }

    /** Rail gravity and placement always travel in the same pending state, starting with
     *  the mapping transaction. A resize keeps its edge without a post-apply correction. */
    void position_widget(wayfire_toplevel_view view, wf::toplevel_state_t& pending,
        wf::output_t *output, const widget_link_t& link)
    {
        pending.gravity = ((link.rail == "left") ? WLR_EDGE_LEFT : WLR_EDGE_RIGHT) | WLR_EDGE_TOP;
        if (output)
        {
            auto spot = widget_spot(output, link, pending.geometry.width, pending.geometry.height);
            pending.geometry.x = spot.x;
            pending.geometry.y = spot.y;
        }

        if (auto state = model.windows.find(view->get_id()); state != model.windows.end())
        {
            state->second.geometry = pending.geometry;
        }
    }

    /** Place a newly adopted, committed or dropped widget, using the size already pending. */
    void place_widget(wayfire_toplevel_view view, wf::output_t *output, const widget_link_t& link)
    {
        auto& pending = view->toplevel()->pending();
        auto before = pending;
        position_widget(view, pending, output, link);
        if ((pending.geometry != before.geometry) || (pending.gravity != before.gravity))
        {
            wf::get_core().tx_manager->schedule_object(view->toplevel());
            publish_model();
        }
    }

    void set_drag_layout_offset(wayfire_toplevel_view view, double dx, double dy)
    {
        auto frame = view ? frame_of(view, false) : nullptr;
        if (!frame || (std::abs(frame->drag_layout_x - dx) < 0.0001 &&
            std::abs(frame->drag_layout_y - dy) < 0.0001)) return;
        frame->damage();
        frame->drag_layout_x = dx;
        frame->drag_layout_y = dy;
        frame->damage();
        view->damage();
    }

    void animate_drag_layout_offset(wayfire_toplevel_view view, double dx, double dy)
    {
        auto frame = view ? frame_of(view, false) : nullptr;
        if (!frame) return;
        refresh_hint_palette();
        auto id = view->get_id();
        if (hints_reduced_motion)
        {
            rail_layout_motions.erase(id);
            set_drag_layout_offset(view, dx, dy);
            return;
        }

        // A retarget in mid-move carries on from the speed it already has (no kick).
        uint32_t now = now_msec();
        scottland::motion::vec2_t velocity;
        if (auto running = rail_layout_motions.find(id); running != rail_layout_motions.end())
        {
            velocity = running->second.move.velocity(uint32_t(now - running->second.started));
            rail_layout_motions.erase(running);
        }

        scottland::motion::vec2_t from{frame->drag_layout_x, frame->drag_layout_y};
        if (std::hypot(dx - from.x, dy - from.y) < 0.05)
        {
            set_drag_layout_offset(view, dx, dy);
            return;
        }

        // WG26: ease in and out over 190-360 ms, longer only to stay under the automatic
        // speed limit shared with window avoidance (P11).
        rail_layout_motion_t motion;
        motion.view = view->weak_from_this();
        motion.move = scottland::motion::plan_eased_move(from, {dx, dy}, velocity, 190.0, 360.0, 0.32);
        motion.started = now;
        rail_layout_motions[id] = motion;
        if (!rail_drawn_samples.count(id)) note_rail_drawn(view, id, now);  // measure from the first frame
        if (!rail_layout_tick.is_connected())
            rail_layout_tick.set_timeout(8, [=] () { return step_rail_layout_motions(); });
    }

    bool step_rail_layout_motions()
    {
        refresh_hint_palette();
        uint32_t now = now_msec();
        for (auto it = rail_layout_motions.begin(); it != rail_layout_motions.end();)
        {
            auto view = wf::toplevel_cast(it->second.view.lock());
            if (!view || !view->is_mapped())
            {
                rail_drawn_samples.erase(it->first);
                it = rail_layout_motions.erase(it);
                continue;
            }

            auto& motion = it->second;
            if (hints_reduced_motion)
            {
                set_drag_layout_offset(view, motion.move.to.x, motion.move.to.y);
                rail_drawn_samples.erase(it->first);
                it = rail_layout_motions.erase(it);
                continue;
            }

            double elapsed = uint32_t(now - motion.started);
            auto at = motion.move.position(elapsed);
            auto velocity = motion.move.velocity(elapsed);
            rail_easing_speed_max = std::max(rail_easing_speed_max, 1000 * std::hypot(velocity.x, velocity.y));
            set_drag_layout_offset(view, at.x, at.y);
            note_rail_drawn(view, it->first, now);
            if (elapsed >= motion.move.duration)
            {
                rail_drawn_samples.erase(it->first);
                it = rail_layout_motions.erase(it);
            } else ++it;
        }

        return !rail_layout_motions.empty();
    }

    void note_rail_drawn(wayfire_toplevel_view view, uint64_t id, uint32_t now)
    {
        auto frame = frame_of(view, false);
        if (!frame) return;
        auto g = view->get_geometry();
        rail_drawn_sample_t sample{g.y + frame->drag_layout_y, now, g.y, frame->drag_layout_y};
        auto previous = rail_drawn_samples.find(id);
        if (previous != rail_drawn_samples.end())
        {
            uint32_t gap = now - previous->second.at;
            // A sample from an earlier, finished motion is not a frame of this one.
            if (gap > 0 && gap < 250)
            {
                double step = std::abs(sample.y - previous->second.y);
                if (step > 30)  // a visible jump: geometry and rail offset out of step
                    LOGI("scottland: rail drawn step ", step, " px in ", gap, " ms for view ", id, ": geometry y ",
                        previous->second.geometry_y, " -> ", sample.geometry_y, ", offset ",
                        previous->second.offset, " -> ", sample.offset);
                rail_drawn_step_max = std::max(rail_drawn_step_max, step);
                rail_drawn_speed_max = std::max(rail_drawn_speed_max, 1000 * step / gap);
                rail_tick_gap_max = std::max(rail_tick_gap_max, gap);
            }
        }
        rail_drawn_samples[id] = sample;
    }

    /** How far a widget's rail make-room ease still has to go (0 when settled). */
    wf::pointf_t rail_layout_remaining(wayfire_toplevel_view view)
    {
        auto found = view ? rail_layout_motions.find(view->get_id()) : rail_layout_motions.end();
        auto frame = view ? frame_of(view, false) : nullptr;
        if (found == rail_layout_motions.end() || !frame) return {0, 0};
        return {found->second.move.to.x - frame->drag_layout_x, found->second.move.to.y - frame->drag_layout_y};
    }

    wayfire_toplevel_view model_view(uint64_t id)
    {
        auto found = model.windows.find(id);
        return found == model.windows.end() ? nullptr : wf::toplevel_cast(found->second.view.lock());
    }

    void clear_rail_drag()
    {
        auto& rail = model.drag.rail;
        rail_dwell_tick.disconnect();
        for (size_t i = 0; i < rail.presentation.size(); ++i)
        {
            animate_drag_layout_offset(model_view(rail.presentation.actor(i).id), 0, 0);
        }
        rail.presentation.clear();
        rail.solver = {};
        rail.output = nullptr;
        rail.dragged = 0;
        rail.left = false;
        rail.top = rail.bottom = 0;
        rail.last_item = rail.solved_item = {};
        rail.pause_anchor = {};
        rail.pause_deadline = 0;
        rail.has_item = rail.has_pause_anchor = rail.pause_pending = rail.solved_once = false;
        rail.active = false;
    }

    bool begin_rail_drag(wf::output_t *output, bool left, uint64_t dragged)
    {
        clear_rail_drag();
        auto& rail = model.drag.rail;
        // Use the session count as a constant-time upper bound before collecting/sorting any
        // actors. The solver and presentation layer each enforce the same cap defensively.
        if (!output || model.widgets.size() > scottland::rail::MAX_ACTORS ||
            (pending_drag_layout && pending_drag_layout->is_committing())) return false;

        std::vector<scottland::rail::interval_t> intervals;
        std::vector<scottland::drag_actor_position_t> origins;
        intervals.reserve(model.widgets.size());
        origins.reserve(model.widgets.size());
        for (auto& [window_id, link] : model.widgets)
        {
            // Widgets hidden by Super+M keep their places (they come back); full screen's don't count.
            if (window_id == dragged || !link.docked() || link.output != output ||
                link.rail != (left ? "left" : "right") || (link.away && in_focus_mode(output))) continue;
            auto widget = wf::toplevel_cast(link.widget.lock());
            if (!widget || !widget->is_mapped() || widget->get_output() != output) continue;
            auto rect = scene_rectangle(widget, output);
            if (rect.height() <= 0) continue;
            // A previous rail audition may still be easing its temporary presentation
            // offset away. It is not part of the widget's true interval or a new geometry.
            if (auto frame = frame_of(widget, false))
            {
                rect.y1 -= frame->drag_layout_y;
                rect.y2 -= frame->drag_layout_y;
            }
            auto geometry = widget->get_geometry();
            intervals.push_back({widget->get_id(), rect.y1, rect.y2});
            origins.push_back({widget->get_id(), (double)geometry.x, (double)geometry.y});
        }

        // WG4's widget placement inset defines the usable rail span for both the solver and drop.
        auto area = output->workarea->get_workarea();
        double top = area.y + WIDGET_INSET;
        double bottom = area.y + area.height - WIDGET_INSET;
        if (bottom < top || !rail.solver.begin(intervals) ||
            !rail.presentation.begin(origins, scottland::rail::MAX_ACTORS)) return false;

        rail.output = output;
        rail.dragged = dragged;
        rail.left = left;
        rail.top = top;
        rail.bottom = bottom;
        rail.active = true;
        return true;
    }

    void solve_rail_layout(const scottland::rectf_t& item)
    {
        auto& rail = model.drag.rail;
        if (!rail.active || item.height() <= 0) return;
        rail.last_item = item;
        rail.has_item = true;
        rail.solved_item = item;
        rail.solver.solve(item.y1, item.y2, rail.top, rail.bottom);
        auto& shifts = rail.solver.shifts();
        for (size_t i = 0; i < rail.presentation.size(); ++i)
        {
            double dy = i < shifts.size() ? shifts[i] : 0;
            rail.presentation.set_offset(i, 0, dy);
            animate_drag_layout_offset(model_view(rail.presentation.actor(i).id), 0, dy);
        }

        rail.solved_once = true;
        rail.pause_pending = false;
    }

    void on_rail_dwell_tick()
    {
        auto& rail = model.drag.rail;
        if (!rail.active || !rail.pause_pending || !rail.has_item) return;

        // Wayfire timers can fire on a millisecond boundary just before their requested
        // deadline. Re-arm the remaining fraction instead of losing this pause entirely.
        uint32_t remaining = rail.pause_deadline - now_msec();
        if (remaining && remaining < 0x80000000u)
        {
            rail_dwell_tick.set_timeout(remaining, [=] () { on_rail_dwell_tick(); });
            return;
        }

        solve_rail_layout(rail.last_item);
    }

    void schedule_rail_dwell(const wf::pointf_t& pointer)
    {
        auto& rail = model.drag.rail;
        uint32_t now = now_msec();
        bool new_pause = false;
        if (!rail.has_pause_anchor)
        {
            rail.pause_anchor = pointer;
            rail.has_pause_anchor = true;
            rail.pause_pending = true;
            new_pause = true;
        } else if (std::hypot(pointer.x - rail.pause_anchor.x, pointer.y - rail.pause_anchor.y) >
            RAIL_POINTER_WOBBLE)
        {
            rail.pause_anchor = pointer;
            rail.pause_pending = true;
            new_pause = true;
        }

        // Input noise within the wobble radius belongs to the current pause and must not
        // keep extending its deadline. A new timer starts only at the initial landing or
        // after the pointer travels outside that radius from the pause anchor.
        if (!rail.pause_pending || !new_pause) return;
        uint32_t dwell = (uint32_t)std::clamp(int(widget_make_room_dwell), 100, 1500);
        rail.pause_deadline = now + dwell;
        rail_dwell_tick.disconnect();
        rail_dwell_tick.set_timeout(dwell, [=] () { on_rail_dwell_tick(); });
    }

    void update_rail_drag(wayfire_toplevel_view dragged, wf::output_t *output,
        std::optional<wf::pointf_t> pointer = {})
    {
        if (!dragged || !output || !model.drag.morph || !morph_widget_shaped() ||
            !dragged->is_mapped() || (pending_drag_layout && pending_drag_layout->is_committing()))
        {
            clear_rail_drag();
            return;
        }

        double screen_width = output->get_relative_geometry().width;
        bool left = model.drag.morph->center_x < screen_width / 2.0;
        auto link = is_widget(dragged) ? link_of_widget(dragged) : link_of_window(dragged);
        uint64_t dragged_window = link ? link->window_id : dragged->get_id();
        auto& rail = model.drag.rail;
        if (!rail.active || rail.output != output || rail.left != left || rail.dragged != dragged_window)
        {
            if (!begin_rail_drag(output, left, dragged_window)) return;
        }

        auto item = scene_rectangle(dragged, output);
        if (item.height() <= 0) return;
        rail.last_item = item;
        rail.has_item = true;
        if (pointer) schedule_rail_dwell(*pointer);
    }

    bool make_room_for_arrival(wayfire_toplevel_view widget, widget_link_t& link)
    {
        auto output = output_alive(link.output) ? link.output : (widget ? widget->get_output() : nullptr);
        if (!widget || !widget->is_mapped() || !output || !link.docked() ||
            model.drag.rail.active || (pending_drag_layout && pending_drag_layout->is_committing()))
            return false;

        // Where it has landed: without a landing glide still under way or an earlier rail
        // ease, both of which are presentation, not its place on the rail.
        auto item = scene_rectangle(widget, output);
        if (auto frame = frame_of(widget, false))
        {
            item.y1 -= frame->translation_y + frame->drag_layout_y;
            item.y2 -= frame->translation_y + frame->drag_layout_y;
        }
        // P14: it stays exactly there. If the drop already laid the rail out for this
        // interval (what was shown during the drag), that layout stands as committed;
        // solving again from it could only disagree with what was shown.
        const bool kept = link.drop_solved && std::abs(item.y1 - link.drop_lo) <= RAIL_POINTER_WOBBLE &&
            std::abs(item.y2 - link.drop_hi) <= RAIL_POINTER_WOBBLE;
        link.drop_solved = false;
        if (kept)
        {
            link.make_room_pending = false;
            ++rail_drop_layouts_kept;
            return true;
        }
        // A different landing (the real card is another size, or placement moved it).
        if (item.height() <= 0 || !begin_rail_drag(output, link.rail == "left", link.window_id))
            return false;
        auto& rail = model.drag.rail;
        rail.last_item = item;
        rail.has_item = true;
        solve_rail_layout(item);
        // Clear this before committing: view->move() may synchronously acknowledge geometry
        // and retry pending arrivals. Leaving the flag set would re-enter this solve against
        // the just-committed layout and restart the same presentation animation at zero.
        link.make_room_pending = false;
        commit_rail_drag();
        link.drop_solved = false;  // an arrival, not a drag's drop
        return true;
    }

    void retry_widget_arrivals()
    {
        if (model.drag.rail.active || (pending_drag_layout && pending_drag_layout->is_committing())) return;
        for (auto& [id, link] : model.widgets)
        {
            if (!link.make_room_pending || !link.docked()) continue;
            if (auto widget = wf::toplevel_cast(link.widget.lock()); widget && widget->is_mapped())
                make_room_for_arrival(widget, link);
            if (model.drag.rail.active || (pending_drag_layout && pending_drag_layout->is_committing())) return;
        }
    }

    void cancel_rail_drag()
    {
        clear_rail_drag();
    }

    void commit_rail_drag()
    {
        auto& rail = model.drag.rail;
        if (!rail.active || !rail.presentation.is_active())
        {
            clear_rail_drag();
            return;
        }

        rail_dwell_tick.disconnect();
        // Drop commits the layout the user saw, if it was for this landing spot. Dropped
        // before any pause, or after moving on from the last one (beyond the pointer
        // wobble), that layout was for somewhere else: solve once more for where it actually
        // lands, from the original positions, so neighbors it no longer blocks go home (P2).
        auto& solved = rail.solved_item;
        if (rail.has_item && (!rail.solved_once ||
            std::max(std::abs(rail.last_item.y1 - solved.y1), std::abs(rail.last_item.y2 - solved.y2)) >
                RAIL_POINTER_WOBBLE))
            solve_rail_layout(rail.last_item);
        // Remember the interval this layout was solved for, so the landing doesn't solve again.
        if (auto dropped = model.widgets.find(rail.dragged); dropped != model.widgets.end())
        {
            dropped->second.drop_solved = rail.has_item;
            dropped->second.drop_lo = rail.solved_item.y1;
            dropped->second.drop_hi = rail.solved_item.y2;
        }

        rail.presentation.commit();
        std::vector<scottland::drag_actor_position_t> moves;
        moves.reserve(rail.presentation.size());
        for (size_t i = 0; i < rail.presentation.size(); ++i)
        {
            auto actor = rail.presentation.actor(i);
            int x = std::lround(actor.target_x);
            int y = std::lround(actor.target_y);
            rail.presentation.set_target(i, x, y);
            actor = rail.presentation.actor(i);
            auto view = model_view(actor.id);
            if (!view || !view->is_mapped())
            {
                rail.presentation.forget(actor.id);
                set_drag_layout_offset(view, 0, 0);
                continue;
            }

            if (actor.applied) continue;
            if (auto link = link_of_widget(view)) link->drop.y += actor.dy;
            moves.push_back({actor.id, (double)x, (double)y});
        }
        rail.presentation.finalize_targets();

        if (!moves.empty() && rail.presentation.is_committing())
        {
            pending_drag_layout.emplace(std::move(rail.presentation));
        }
        bool any_target = !moves.empty();
        rail.presentation.clear();
        rail.solver = {};
        rail.output = nullptr;
        rail.dragged = 0;
        rail.active = false;
        rail.has_item = rail.has_pause_anchor = rail.pause_pending = rail.solved_once = false;
        for (const auto& move : moves)
        {
            auto view = model_view(move.id);
            if (!view || !view->is_mapped())
            {
                if (pending_drag_layout) pending_drag_layout->forget(move.id);
                continue;
            }
            auto state = model.windows.find(move.id);
            if (state != model.windows.end())
            {
                state->second.geometry.x = move.x;
                state->second.geometry.y = move.y;
            }
            if (auto link = link_of_widget(view))
            {
                if (auto window = wf::toplevel_cast(link->window.lock()); window && window->is_mapped())
                {
                    auto hidden = window->get_geometry();
                    int hidden_y = std::lround(link->drop.y - hidden.height / 2.0);
                    if (auto window_state = model.windows.find(window->get_id()); window_state != model.windows.end())
                        window_state->second.geometry.y = hidden_y;
                    window->move(hidden.x, hidden_y);
                }
            }
            view->move(move.x, move.y);
        }
        if (pending_drag_layout && pending_drag_layout->is_committing())
            pending_drag_layout->finalize_targets();
        if (pending_drag_layout && pending_drag_layout->is_committing() && any_target)
            publish_model();
        else if (pending_drag_layout && !pending_drag_layout->is_committing()) pending_drag_layout.reset();
    }

    void finish_drag_layout(wayfire_toplevel_view view)
    {
        if (!pending_drag_layout || !view) return;
        auto geometry = view->get_geometry();
        double moved_x = 0, moved_y = 0;
        bool found = false;
        for (size_t i = 0; i < pending_drag_layout->size(); ++i)
        {
            const auto& actor = pending_drag_layout->actor(i);
            if (actor.id == view->get_id())
            {
                moved_x = actor.target_x - actor.origin_x;
                moved_y = actor.target_y - actor.origin_y;
                found = true;
                break;
            }
        }

        if (found && pending_drag_layout->acknowledge(view->get_id(), geometry.x, geometry.y))
        {
            if (auto frame = frame_of(view, false))
            {
                set_drag_layout_offset(view, frame->drag_layout_x - moved_x, frame->drag_layout_y - moved_y);
                animate_drag_layout_offset(view, 0, 0);
            }

            if (pending_drag_layout->empty()) pending_drag_layout.reset();
            retry_widget_arrivals();
        }
    }

    /** A drag over a rail ended there: the preview becomes the widget, at `at`. */
    void commit_preview(widget_link_t& link, wf::pointf_t at)
    {
        auto window = wf::toplevel_cast(link.window.lock());
        begin_window_widget_transition(window);
        set_widget_presentation(link, model.widget_mode == widget_mode_t::collapsed, widget_revealed(link));
        transition_widget(link, widget_link_t::lifecycle_t::docked);
        // The live drag solves against its morphing footprint. Once the real widget is
        // docked, solve once more against its actual card geometry so a small preview
        // cannot leave the larger landing card overlapping its neighbors.
        link.make_room_pending = true;
        link.drop    = at;
        if (window && window->get_output())
        {
            link.output = window->get_output();
            link.rail   = at.x < window->get_output()->get_relative_geometry().width / 2 ? "left" : "right";
        }

        show_attention(link.window_id);  // now shown on the widget, not the window
        if (auto widget = wf::toplevel_cast(link.widget.lock()))
        {
            auto output = output_alive(link.output) ? link.output : widget->get_output();
            if (output && (widget->get_output() != output))
            {
                wf::move_view_to_output(widget, output, false);
            }

            keep_above(widget);
            place_widget(widget, output, link);
            auto pending = widget->toplevel()->pending().geometry;
            if (widget->get_geometry() == pending && link.make_room_pending)
                make_room_for_arrival(widget, link);
            set_scale(widget, 1.0);
            // It was let go at `at` (the dragged window was drawn there as the widget): glide to
            // its place against the screen edge rather than jump.
            auto placed = widget->toplevel()->pending().geometry;
            start_glide(widget, at.x - (placed.x + placed.width / 2.0), at.y - (placed.y + placed.height / 2.0));
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
        transition_widget(link, widget_link_t::lifecycle_t::restoring);
        model.widgets.erase(uint64_t(link.window_id));  // `link` is gone from here on
        close_view_or_process(widget, launcher);
        announce_widgets();
    }

    /** Back to the window, at `at` (output coords); the widget goes (it isn't closed: WG5). */
    void restore_window(widget_link_t& link, std::optional<wf::pointf_t> at, bool grow = false)
    {
        if (auto state = model.windows.find(link.window_id); state != model.windows.end())
            state->second.pending_rail.reset();
        auto window = wf::toplevel_cast(link.window.lock());
        auto widget = wf::toplevel_cast(link.widget.lock());
        transition_widget(link, widget_link_t::lifecycle_t::restoring);
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
                move_window(window, std::round(at->x - g.width / 2.0), std::round(at->y - g.height / 2.0));
                // At once at the size it has here: the drag already showed it at this size (the
                // morph), so the drop is just a drop, no growing from its size on the rail.
                // Opened from the widget (a click), it grows out of it instead.
                if (!grow)
                {
                    set_scale_now(window, scale_for(window));
                }
            }

            transition_widget(link, widget_link_t::lifecycle_t::restoring);
            wf::get_core().default_wm->focus_raise_view(window);  // (going to it answers attention)
        }

        uint64_t id = link.window_id;
        auto launcher = link.launcher;
        close_view_or_process(widget, launcher);
        model.widgets.erase(id);
        announce_widgets();
    }

    /** Close the app's window and its widget together (WG5). */
    void close_linked(widget_link_t& link)
    {
        auto window = wf::toplevel_cast(link.window.lock());
        auto widget = wf::toplevel_cast(link.widget.lock());
        auto launcher = link.launcher;
        // Shown again first: if the app asks before closing (unsaved work), the question is visible.
        transition_widget(link, widget_link_t::lifecycle_t::closing);
        model.widgets.erase(uint64_t(link.window_id));  // `link` is gone from here on
        announce_widgets();
        close_view_or_process(widget, launcher);
        if (window)
        {
            window->close();
        }
    }

    /** A drop: windows dropped on a rail become widgets; widgets dropped off the rail restore.
     *  After a drag that morphed (WG13), the shape shown at the drop decides (`widget_shaped`). */
    /**
     * Is the pointer, at `at` (output coords), on a rail? Entering one means reaching the rail zone
     * at the screen's edge. A widget (or a drag already showing one) is on it while the pointer is
     * anywhere between the edge and the widget's inner side, which is wider than the rail zone:
     * it leaves when the pointer goes past it. So a widget grabbed anywhere stays one until moved
     * off the rail, and the form changes as the pointer crosses, not when the window's center does.
     */
    bool on_rail(wf::output_t *output, double at, double width, wayfire_toplevel_view widget)
    {
        if (place_at(output, std::clamp(at, 0.0, width - 1), width).zone == zone_t::widget)
        {
            return true;
        }

        if (!widget || !widget->is_mapped())
        {
            return false;
        }

        double from_edge = (at < width / 2) ? at : width - at;
        return from_edge <= WIDGET_INSET + widget->get_geometry().width;
    }

    /** A drop, released at `pointer` (layout coords): the pointer's place says widget or window. */
    void handle_widget_drop(wayfire_toplevel_view view, std::optional<bool> widget_shaped, wf::pointf_t pointer)
    {
        if (!view || !view->is_mapped() || !view->get_output())
        {
            return;
        }

        auto geometry = view->get_geometry();
        auto output   = view->get_output();
        double width  = output->get_relative_geometry().width;
        wf::pointf_t center{geometry.x + geometry.width / 2.0, geometry.y + geometry.height / 2.0};
        auto in_rail = [&] (double x) { return place_at(output, std::clamp(x, 0.0, width - 1), width).zone == zone_t::widget; };
        double at = pointer.x - output->get_layout_geometry().x;
        bool on_rail = widget_shaped.value_or(this->on_rail(output, at, width, is_widget(view) ? view : nullptr));
        if (auto link = link_of_widget(view))
        {
            if (!on_rail)
            {
                restore_window(*link, center);
                return;
            }

            bool left = in_rail(at) ? (at < width / 2) : (center.x < width / 2);  // else its nearer rail

            auto rail = left ? "left" : "right";
            bool changed = (link->rail != rail) || (link->output != output);
            link->drop   = center;
            link->rail   = rail;
            link->output = output;
            place_widget(view, output, *link);  // position and new rail gravity in one transaction
            auto placed = view->toplevel()->pending().geometry;
            start_glide(view, center.x - (placed.x + placed.width / 2.0),
                center.y - (placed.y + placed.height / 2.0));
            if (changed)
            {
                announce_widgets();
            }
        } else if (on_rail)
        {
            widgetize(view, false, (in_rail(at) ? at : center.x) < width / 2 ? "left" : "right");
        } else if (auto link = link_of_window(view); link && link->previewing())
        {
            cancel_preview(*link);
        }
    }

    static const char *lifecycle_name(widget_link_t::lifecycle_t state)
    {
        switch (state)
        {
          case widget_link_t::lifecycle_t::previewing: return "previewing";
          case widget_link_t::lifecycle_t::docked: return "docked";
          case widget_link_t::lifecycle_t::restoring: return "restoring";
          case widget_link_t::lifecycle_t::closing: return "closing";
          case widget_link_t::lifecycle_t::handed_over: return "handed-over";
        }

        return "window";
    }

    wf::json_t widget_snapshot(bool previews = false, bool external = false)
    {
        auto list = wf::json_t::array();
        for (auto& [id, link] : model.widgets)
        {
            if (!previews && !link.docked())
            {
                continue;
            }

            auto state = model.windows.find(id);
            if (state == model.windows.end())
            {
                continue;
            }

            auto widget = link.widget.lock();
            wf::json_t entry;
            entry["id"] = std::to_string(id);
            entry["window"] = (int64_t)id;
            entry["widget_view"] = widget ? (int64_t)widget->get_id() : (int64_t)-1;
            entry["app_id"] = state->second.app_id;
            entry["title"] = state->second.title;
            entry["pid"] = (int64_t)state->second.pid;
            auto shown = widget ? model.windows.find(widget->get_id()) : model.windows.end();
            entry["widget_pid"] = (int64_t)(shown != model.windows.end() ? shown->second.pid : 0);
            entry["widget_unit"] = link.launcher ? link.launcher->unit : "";
            entry["launcher_pid"] = (int64_t)(link.launcher ? link.launcher->pid : 0);
            entry["rail"] = link.rail;
            entry["focused"] = shown != model.windows.end() && shown->second.focused;
            entry["minimized"] = link.minimized();
            entry["collapsed"] = link.collapsed;
            entry["peek"] = link.peek;
            entry["place"] = rail_place_name(rail_place(link));
            entry["away"] = link.away;
            entry["urgent"] = needs_attention(id);
            entry["lifecycle"] = lifecycle_name(link.lifecycle);
            entry["touch_drag"] = link.touch_drag;
            entry["desktop"] = link.desktop;
            entry["name"] = link.name;
            entry["icon"] = link.icon;
            entry["card"] = link.card;
            if (!external)
            {
                entry["drop_x"] = link.drop.x;
                entry["drop_y"] = link.drop.y;
            }
            list.append(entry);
        }

        return list;
    }

    wf::json_t model_snapshot(const std::string& slice)
    {
        auto reply = wf::ipc::json_ok();
        reply["collapsed"] = model.widget_mode == widget_mode_t::collapsed;
        reply["widget_mode"] = widget_mode_name(model.widget_mode);
        reply["widget_mode_shown"] = widgets_revealed() ? "expanded" : widget_mode_name(shown_widget_mode());
        reply["session"] = model.session;
        auto focus = wf::json_t::array();
        for (auto output : model.focused_outputs)
        {
            focus.append(output->to_string());  // screens in full-screen focus (FS1)
        }

        reply["focus"] = focus;
        if (slice == "desktop")
        {
            auto goo = wf::json_t::array();
            for (auto output : model.goo_outputs) goo.append(output->to_string());
            reply["goo"] = goo;
        }
        auto windows = wf::json_t::array();
        for (auto& [id, state] : model.windows)
        {
            wf::json_t entry;
            entry["id"] = (int64_t)id;
            entry["pid"] = (int64_t)state.pid;
            auto sources = wf::json_t::array();
            for (auto& source : state.attention)
            {
                sources.append(source);
            }
            entry["attention"] = sources;
            if (slice != "attention")
            {
                entry["app_id"] = state.app_id;
                entry["title"] = state.title;
                entry["scale"] = state.scale;
                entry["focused"] = state.focused;
                auto link = model.widgets.find(id);
                auto widget_link = link_of_widget(state.view.lock());
                entry["form"] = widget_link ? "widget" : (link == model.widgets.end() ? "window" : lifecycle_name(link->second.lifecycle));
                auto represented = widget_link ? widget_link : (link == model.widgets.end() ? nullptr : &link->second);
                entry["rail"] = represented ? represented->rail : "";
                entry["collapsed"] = represented && represented->collapsed;
                if (slice == "desktop")
                {
                    if (model.drag.morph && model.drag.morph->dragged.lock().get() == state.view.lock().get())
                    {
                        entry["form"] = "morphing";
                        entry["widget_shaped"] = morph_widget_shaped();
                    }
                    if (state.placement) entry["placement"] = memory_snapshot(*state.placement);
                    if (state.pending_rail)
                    {
                        entry["pending_rail"]["x"] = state.pending_rail->x;
                        entry["pending_rail"]["y"] = state.pending_rail->y;
                    }
                    if (state.pinned_scale) entry["pinned_scale"] = *state.pinned_scale;
                    if (state.paired_placement)
                    {
                        auto& p = *state.paired_placement;
                        auto& g = p.geometry;
                        entry["paired_placement"]["x"] = g.x;
                        entry["paired_placement"]["y"] = g.y;
                        entry["paired_placement"]["width"] = g.width;
                        entry["paired_placement"]["height"] = g.height;
                        entry["paired_placement"]["output"] = p.output;
                        if (p.pin) entry["paired_placement"]["pin"] = *p.pin;
                    }
                    entry["zone"] = zone_name(state.zone);
                    entry["layer"] = state.above ? "above" : "normal";
                    entry["x"] = state.geometry.x;
                    entry["y"] = state.geometry.y;
                    entry["width"] = state.geometry.width;
                    entry["height"] = state.geometry.height;
                }
            }

            windows.append(entry);
        }

        reply["windows"] = windows;
        if (slice == "desktop")
        {
            reply["hint_width"] = int(model.hint_width);
            auto selected = wf::json_t::array();
            for (auto id : model.selected)
            {
                selected.append((int64_t)id);
            }
            reply["selected"] = selected;
            wf::json_t drag;
            drag["window"] = (int64_t)model.drag.origin.view;
            drag["started"] = model.drag.started;
            drag["cancelled"] = model.drag.cancelled;
            drag["origin_window"] = (int64_t)model.drag.origin.first_view;
            drag["origin_x"] = model.drag.origin.position.x;
            drag["origin_y"] = model.drag.origin.position.y;
            drag["origin_widget"] = model.drag.origin.first_widget;
            drag["origin_rail"] = model.drag.origin.rail;
            drag["chain_window"] = (int64_t)model.drag.last_drop.became;
            drag["chain_at"] = (int64_t)model.drag.last_drop_at;
            auto held = model.drag.held_above.lock();
            drag["held_above"] = held ? (int64_t)held->get_id() : (int64_t)-1;
            drag["target_scale"] = model.drag.target;
            if (model.drag.morph)
            {
                wf::json_t morph;
                auto dragged = model.drag.morph->dragged.lock();
                morph["window"] = dragged ? (int64_t)dragged->get_id() : (int64_t)-1;
                morph["from_widget"] = model.drag.morph->from_widget;
                morph["toward"] = model.drag.morph->toward;
                morph["center_x"] = model.drag.morph->center_x;
                drag["morph"] = morph;
            }
            reply["drag"] = drag;
        }
        if (slice != "attention")
        {
            auto widgets = widget_snapshot(true, slice != "desktop");
            reply["widgets"] = widgets;
        }

        return reply;
    }

    bool installing_model = true;  // no partial init/handover snapshots escape
    std::map<std::string, std::string> published_slices;
    std::map<std::string, uint64_t> published_versions;
    // A transaction that changes many windows at once (a spread commit) publishes the model once
    // at its end instead of once per change: each publish serializes the whole desktop.
    int publish_batch = 0;
    bool publish_pending = false;
    struct publish_batch_t
    {
        scottland_plugin_t *self;
        explicit publish_batch_t(scottland_plugin_t *self) : self(self) { ++self->publish_batch; }
        ~publish_batch_t()
        {
            if (--self->publish_batch == 0 && self->publish_pending)
            {
                self->publish_pending = false;
                self->publish_model();
            }
        }
    };

    void publish_model()
    {
        if (installing_model)
        {
            return;
        }

        if (publish_batch)
        {
            publish_pending = true;
            return;
        }

        auto full = model_snapshot("desktop");
        auto text = full.serialize();
        if (published_slices["desktop"] == text)
        {
            return;
        }

        model.version++;
        published_slices["desktop"] = text;
        full["version"] = (int64_t)model.version;
        send_ipc_event(full, "scottland-model#");
        for (auto slice : {"widgets", "attention"})
        {
            auto snapshot = model_snapshot(slice);
            auto value = snapshot.serialize();
            if (published_slices[slice] != value)
            {
                published_slices[slice] = value;
                published_versions[slice] = model.version;
                snapshot["version"] = (int64_t)model.version;
                send_ipc_event(snapshot, std::string("scottland-") + slice + "#");
            }
        }
    }

    wf::ipc::method_callback desktop_state = [=] (wf::json_t data) -> wf::json_t
    {
        std::string slice = data.has_member("slice") && data["slice"].is_string() ? data["slice"].as_string() : "desktop";
        if ((slice != "desktop") && (slice != "widgets") && (slice != "attention"))
        {
            return wf::ipc::json_error("unknown desktop-model slice");
        }

        auto reply = model_snapshot(slice);
        reply["version"] = (int64_t)model.version;
        return reply;
    };
    wf::ipc::method_callback_full subscribe_model = [=] (wf::json_t data, wf::ipc::client_interface_t *client) -> wf::json_t
    {
        auto snapshot = desktop_state(data);
        if (!client || snapshot.has_member("error"))
        {
            return client ? snapshot : wf::ipc::json_error("subscribe needs an IPC connection");
        }

        std::string slice = data.has_member("slice") && data["slice"].is_string() ? data["slice"].as_string() : "desktop";
        wf::json_t watch;
        auto events = wf::json_t::array();
        events.append(slice == "desktop" ? "scottland-model#" : std::string("scottland-") + slice + "#");
        watch["events"] = events;
        auto installed = ipc_repo->call_method("window-rules/events/watch", watch, client);
        if (installed.has_member("error"))
        {
            return installed;
        }

        // Installation and the initial snapshot happen in one compositor turn. The event
        // repository owns the connection, so subscriptions also survive plugin replacement.
        return snapshot;
    };
    wf::ipc::method_callback widgets_state = [=] (wf::json_t) -> wf::json_t
    {
        auto reply = wf::ipc::json_ok();
        reply["widgets"] = widget_snapshot();
        reply["version"] = (int64_t)model.version;
        return reply;
    };

    // Test-only observations cross IPC explicitly. No live drift monitor or repair path exists.
    wf::ipc::method_callback audit_model = [=] (wf::json_t data) -> wf::json_t
    {
        auto issues = wf::json_t::array();
        auto fail = [&] (const std::string& message) { issues.append(message); };
        for (auto& any : wf::get_core().get_all_views())
        {
            auto view = wf::toplevel_cast(any);
            if (view && view->is_mapped() && !model.windows.count(view->get_id()))
            {
                fail("mapped window absent from model: " + std::to_string(view->get_id()));
            }
        }
        for (auto& [id, state] : model.windows)
        {
            auto view = wf::toplevel_cast(state.view.lock());
            if (!view || !view->is_mapped())
            {
                fail("model window not mapped: " + std::to_string(id));
                continue;
            }
            auto g = view->get_geometry();
            if ((g.x != state.geometry.x) || (g.y != state.geometry.y) ||
                (g.width != state.geometry.width) || (g.height != state.geometry.height))
            {
                fail("scene geometry differs: " + std::to_string(id));
            }
            if ((view->get_title() != state.title) || (view->get_app_id() != state.app_id) ||
                ((wf::get_core().seat->get_active_view() == view) != state.focused))
            {
                fail("scene identity/focus differs: " + std::to_string(id));
            }
            auto app_link = model.widgets.find(id);
            auto widget_link = link_of_widget(view);
            bool attention = widget_link ? (widget_link->docked() &&
                !widget_transitions.count(widget_link->window_id) && needs_attention(widget_link->window_id)) :
                (needs_attention(id) && (app_link == model.widgets.end() || !app_link->second.docked() ||
                    widget_transitions.count(id)));
            if (auto frame = frame_of(view, false); frame && frame->needs_attention() != attention)
            {
                fail("scene attention differs: " + std::to_string(id));
            }
            if (view->has_data("wm-actions-above") != state.above)
            {
                fail("scene layer differs: " + std::to_string(id));
            }
            if (!transitions.count(id) && std::abs(displayed_scale(view) - state.scale) > 0.003)
            {
                fail("scene scale differs: " + std::to_string(id));
            }
        }
        if (!data.has_member("service"))
        {
            fail("widget service observations required");
        } else
        {
            auto service = data["service"];
            auto snapshot = service["model"];
            if (!snapshot.has_member("version") || snapshot["version"].as_int64() < (int64_t)published_versions["widgets"])
            {
                fail("widget service has an older snapshot");
            }
            auto expected = model_snapshot("widgets");
            if (wf::json_t(snapshot["windows"]).serialize() != wf::json_t(expected["windows"]).serialize() ||
                wf::json_t(snapshot["widgets"]).serialize() != wf::json_t(expected["widgets"]).serialize() ||
                wf::json_t(snapshot["collapsed"]).serialize() != wf::json_t(expected["collapsed"]).serialize())
            {
                fail("widget service model copy differs");
            }
            auto widgets = service["widgets"];
            auto rendered = service["rendered"];
            for (auto& [id, link] : model.widgets)
            {
                auto window = wf::toplevel_cast(link.window.lock());
                auto widget = wf::toplevel_cast(link.widget.lock());
                auto label = std::to_string(id);
                bool waiting_form = widget_transitions.count(id) && entering_widget(id);
                if (!window || (window->get_root_node()->is_enabled() == (link.docked() && !waiting_form)))
                {
                    fail("app visibility differs: " + label);
                }
                if (!widget || !widget->is_mapped())
                {
                    fail("widget not mapped: " + label);
                    continue;
                }
                if (widget->get_root_node()->is_enabled() != (link.docked() && !link.away && !waiting_form))
                {
                    fail("widget visibility differs: " + label);
                }
                if (!link.docked())
                {
                    continue;
                }
                // Independent FS1 check: asking for hints explicitly reveals widgets (WK12).
                if (!window_keys.active && !widget->get_output()->node_for_layer(wf::scene::layer::TOP)->is_enabled() &&
                    !(rail_slides.count(id) && rail_slides[id].running) && widget->get_root_node()->is_enabled())
                {
                    fail("widget interrupts promoted fullscreen: " + label);
                }
                if (!widget->has_data("wm-actions-above"))
                {
                    fail("widget is not in the above layer: " + label);
                }
                auto g = widget->get_geometry();
                auto spot = widget_spot(widget->get_output(), link, g.width, g.height);
                if ((std::abs(g.x - spot.x) > 1) || (std::abs(g.y - spot.y) > 1))
                {
                    fail("widget rail position differs: " + label);
                }
                if (!widgets.has_member(label))
                {
                    fail("widget absent from service: " + label);
                    continue;
                }
                auto state = widgets[label];
                if (state["Title"].as_string() != model.windows[id].title ||
                    state["Rail"].as_string() != link.rail || state["Minimized"].as_bool() != link.minimized() ||
                    state["Urgent"].as_bool() != needs_attention(id))
                {
                    fail("widget service presentation differs: " + label);
                }
                if (link.card && link.launcher)
                {
                    if (!rendered.has_member(link.launcher->unit))
                    {
                        fail("card render report absent: " + label);
                        continue;
                    }
                    auto report = rendered[link.launcher->unit];
                    bool title_shown = !link.minimized() && !model.windows[id].title.empty();
                    if (report["revision"].as_int64() != state["_revision"].as_int64() ||
                        report["version"].as_int64() != state["_model_version"].as_int64() ||
                        report["title"].as_string() != model.windows[id].title ||
                        report["title_shown"].as_bool() != title_shown ||
                        report["collapsed"].as_bool() != link.minimized() ||
                        report["rail"].as_string() != link.rail || std::abs(report["width"].as_double() - g.width) > 1 ||
                        (link.minimized() && std::abs(g.width - 96) > 1) || (!link.minimized() && title_shown && g.width <= 96))
                    {
                        fail("card rendered presentation differs: " + label);
                    }
                }
            }
        }

        auto reply = wf::ipc::json_ok();
        reply["ok"] = issues.size() == 0;
        reply["issues"] = issues;
        reply["version"] = (int64_t)model.version;
        return reply;
    };

    wf::signal::connection_t<wf::wm_actions_above_changed_signal> on_above =
        [=] (wf::wm_actions_above_changed_signal *ev)
    {
        observe_view(wf::toplevel_cast(ev->view));
        publish_model();
    };

    void handle_new_output(wf::output_t *output) override
    {
        wf::per_output_tracker_mixin_t<center_resize_t>::handle_new_output(output);
        output_instance[output]->on_start = [=] () { stop_keyboard_motion(); bypass_window_keys(); };
        output->connect(&on_above);
        watch_fullscreen(output);
    }

    void handle_output_removed(wf::output_t *output) override
    {
        output->disconnect(&on_above);
        unwatch_fullscreen(output);
        wf::per_output_tracker_mixin_t<center_resize_t>::handle_output_removed(output);
    }

    wf::signal::connection_t<wf::view_title_changed_signal> on_title =
        [=] (wf::view_title_changed_signal *ev)
    {
        observe_view(wf::toplevel_cast(ev->view));
        publish_model();
    };

    wf::signal::connection_t<wf::view_app_id_changed_signal> on_app_id =
        [=] (wf::view_app_id_changed_signal *ev)
    {
        observe_view(wf::toplevel_cast(ev->view));
        publish_model();
    };

    // What the widget's manifest says that Scottland acts on, told by the launcher once it has
    // chosen the widget: {window, unit, touch_drag}. The unit names the launch, so a late call
    // from an earlier launch for the same window changes nothing.
    wf::ipc::method_callback widget_traits = [=] (wf::json_t data) -> wf::json_t
    {
        if (!data.has_member("window") || !data["window"].is_int64() || !data.has_member("unit") ||
            !data["unit"].is_string())
        {
            return wf::ipc::json_error("widget-traits needs \"window\" and \"unit\"");
        }

        auto found = model.widgets.find((uint64_t)data["window"].as_int64());
        if ((found == model.widgets.end()) || !found->second.launcher ||
            (found->second.launcher->unit != data["unit"].as_string()))
        {
            return wf::ipc::json_error("no such widget launch");
        }

        auto& link = found->second;
        link.touch_drag = data.has_member("touch_drag") && data["touch_drag"].is_bool() && data["touch_drag"].as_bool();
        for (auto field : {"desktop", "name", "icon"})
        {
            if (!data.has_member(field) || !data[field].is_string())
            {
                return wf::ipc::json_error("widget-traits needs resolved identity");
            }
        }
        link.desktop = data["desktop"].as_string();
        link.name = data["name"].as_string();
        link.icon = data["icon"].as_string();
        link.card = data.has_member("card") && data["card"].as_bool();
        publish_model();
        return wf::ipc::json_ok();
    };

    /** The widget clicked: its window comes back in the middle of the screen, flying out of the
     *  widget and growing to its size there (WG17). False when it has no screen to go to. */
    bool open_widget(widget_link_t& link, bool hint_cycle = false)
    {
        auto widget = wf::toplevel_cast(link.widget.lock());
        auto window = wf::toplevel_cast(link.window.lock());
        auto output = widget ? widget->get_output() : (window ? window->get_output() : nullptr);
        if (!output)
        {
            return false;
        }

        auto area = output->workarea->get_workarea();
        wf::pointf_t middle{area.x + area.width / 2.0, area.y + area.height / 2.0};
        wf::pointf_t from = middle;
        if (widget)
        {
            auto g = widget->get_geometry();
            from = {g.x + g.width / 2.0, g.y + g.height / 2.0};
        }

        if (widget) remember_window(widget);
        double from_scale = window ? displayed_scale(window) : 1.0;
        if (window) pin_scale(window, std::nullopt);
        if (window) middle = zone_spot(window, scottland::windowing::zone::center, {from.x, from.y}, nullptr, output);
        restore_window(link, middle, true);
        if (window) remember_window(window);
        if (window)
        {
            // A remembered center spot just past the zone's edge keeps its own (full-looking)
            // scale (WP8); anywhere in the center zone that is 100%.
            if (hint_cycle) start_cycle_glide(window, from, from_scale, middle, scale_for(window));
            else start_glide(window, from.x - middle.x, from.y - middle.y);
        }

        return true;
    }

    /** IPC scottland/present {window}: "I want to see this now" (L30). A widget's window opens
     *  as if its widget were clicked; a window in a side zone flies to the middle of its screen,
     *  growing to 100% there; a window already in the center zone stays where it is. All are
     *  raised and focused. `window` may name the app's window or its widget. */
    wf::ipc::method_callback present_method = [=] (wf::json_t data) -> wf::json_t
    {
        if (!data.has_member("window") || !data["window"].is_int())
        {
            return wf::ipc::json_error("present needs integer \"window\"");
        }

        uint64_t id = (uint64_t)data["window"].as_int64();
        auto found  = model.widgets.find(id);
        if (found == model.widgets.end())
        {
            for (auto it = model.widgets.begin(); it != model.widgets.end(); ++it)
            {
                auto widget = it->second.widget.lock();
                if (widget && (widget->get_id() == id))
                {
                    found = it;
                    break;
                }
            }
        }

        if ((found != model.widgets.end()) && !found->second.previewing())
        {
            if (!open_widget(found->second))
            {
                return wf::ipc::json_error("the widget has no screen");
            }

            auto reply = wf::ipc::json_ok();
            reply["presented"] = "widget";
            return reply;
        }

        wayfire_toplevel_view view = nullptr;
        for (auto& any_view : wf::get_core().get_all_views())
        {
            if (any_view->get_id() == id)
            {
                view = wf::toplevel_cast(any_view);
                break;
            }
        }

        if (!view || !view->is_mapped())
        {
            return wf::ipc::json_error("no such window");
        }

        auto reply = wf::ipc::json_ok();
        reply["presented"] = "window";
        auto output = view->get_output();
        if (output && !view->pending_fullscreen() && (placement_of(view).zone != zone_t::center))
        {
            auto g    = view->get_geometry();
            wf::pointf_t from{g.x + g.width / 2.0, g.y + g.height / 2.0};
            remember_window(view);
            pin_scale(view, std::nullopt);
            wf::pointf_t middle = zone_spot(view, scottland::windowing::zone::center, {from.x, from.y});
            move_window(view, std::round(middle.x - g.width / 2.0), std::round(middle.y - g.height / 2.0));
            start_glide(view, from.x - middle.x, from.y - middle.y);
            remember_window(view);
            reply["presented"] = "moved";
        }

        wf::get_core().default_wm->focus_raise_view(view);
        return reply;
    };

    // The widget mode (WG16) for menus and scripts: {"mode": "expanded" | "collapsed" | "hidden" |
    // "next"} sets it as a Super+M tap would; with no mode it only reports.
    wf::ipc::method_callback widget_mode_ipc = [=] (wf::json_t data) -> wf::json_t
    {
        if (data.has_member("mode"))
        {
            auto name = data["mode"].is_string() ? data["mode"].as_string() : "";
            auto mode = parse_widget_mode(name);
            if (name == "next")
            {
                cycle_widget_mode();
            } else if (!mode)
            {
                return wf::ipc::json_error("mode is expanded, collapsed, hidden or next");
            } else
            {
                set_widget_mode(*mode);
            }
        }

        auto reply = wf::ipc::json_ok();
        reply["mode"] = widget_mode_name(model.widget_mode);
        reply["shown"] = widgets_revealed() ? "expanded" : widget_mode_name(shown_widget_mode());
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

        auto found = model.widgets.find(id);
        if ((found == model.widgets.end()) || !found->second.docked())
        {
            return wf::ipc::json_error("no such widget");
        }

        std::string action = data["action"].as_string();
        if (action == "open")
        {
            if (!open_widget(found->second))
            {
                return wf::ipc::json_error("the widget has no screen");
            }
        } else if (action == "minimize")
        {
            reset_widget_peek(found->second);
            set_widget_presentation(found->second, !found->second.collapsed);
            announce_widgets();
        } else if (action == "test-peek" && getenv("SCOTTLAND_TEST_MODEL"))
        {
            set_widget_presentation(found->second, found->second.collapsed, data["peek"].as_bool());
            announce_widgets();
        } else if (action == "restore")
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
                // Use the finger position, even before Wayfire finishes delivering touch down.
                start_touch_drag(view, finger);
            } else
            {
                start_pointer_drag(view);
            }
        }
    }

    // Touchpad window gestures (L23, L24). A three-finger swipe moves the window under the pointer
    // through the same live drag as Super+drag; a
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
        if (test_touchpad_pointers || (test_touchpad && device == &test_touchpad->pointer.base))
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
        cancel_touchpad_hold(); // the fingers moved first: a drag (L23)
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
        drag->set_input(-1, true);
        drag->set_pending_drag(wf::get_core().get_cursor_position());
        drag->start_drag(view);
        note_drag_start();  // now, where the fingers began: the first update may be a while
        swipe_moving = true;
    }

    void swipe_update(double dx, double dy)
    {
        if (!swipe_moving || !drag->view)
        {
            swipe_moving = false;  // its drag ended some other way
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
            // The fingers lifted: the drag ends now. (A drag of the same window soon after counts
            // as the same move only for where Esc sends it back, WG14.)
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

    // Three-finger hold (WK35/WK36): fingers down and still for the hint hold delay, before any
    // click or drag. libinput reports still fingers as a hold gesture and ends it (cancelled) as
    // soon as they move or click, so a swipe (L23) or click (L24) that starts first keeps its
    // meaning. On an unfocused window it pairs with the focused one; on the focused window it
    // reaches the solo hook. The gesture still reaches the app (it stops kinetic scrolling).
    struct touchpad_hold_t { uint64_t window = 0, partner = 0; };
    std::optional<touchpad_hold_t> touchpad_hold;
    wf::wl_timer<false> touchpad_hold_timer;
    std::unique_ptr<virtual_pointer_t> test_touchpad; // scottland/test-touchpad only

    void cancel_touchpad_hold() { touchpad_hold.reset(); touchpad_hold_timer.disconnect(); }

    void touchpad_hold_begin(wlr_input_device *device, uint32_t fingers, uint32_t time_msec)
    {
        cancel_touchpad_hold();
        if (!touchpad_gestures || (fingers != 3) || !is_touchpad(device) || drag->view || swipe_moving ||
            middle_pending || middle_resizing) return;
        auto view = gesture_target();
        if (!view) return;
        auto link = link_of_widget(view);
        uint64_t window = link ? link->window_id : view->get_id();
        if (!model.windows.count(window)) return;
        touchpad_hold = touchpad_hold_t{window, window_keys.focused ? window_keys.focused() : 0};
        // Timed from the fingers' event, on the same clock as hint holds (WK39).
        uint32_t delay = std::clamp(int(window_hold_delay), 1, 3000);
        uint32_t now = now_msec(), elapsed = now - time_msec;
        uint32_t remaining = (elapsed > 1000) ? delay : (elapsed >= delay ? 1 : delay - elapsed);
        touchpad_hold_timer.set_timeout(remaining, [=] () { touchpad_hold_due(); });
    }

    void touchpad_hold_due()
    {
        if (!touchpad_hold) return;
        auto hold = *touchpad_hold; touchpad_hold.reset();
        // Still the window under the fingers, and nothing grabbed it meanwhile.
        auto view = gesture_target();
        auto link = view ? link_of_widget(view) : nullptr;
        if (!view || drag->view || swipe_moving || (link ? link->window_id : view->get_id()) != hold.window) return;
        LOGI("scottland: three-finger hold on ", hold.window, hold.window == hold.partner ? " (focused)" : "");
        if (hold.window == hold.partner) solo_window(hold.window);
        else if (hold.partner) pair_windows(hold.window, hold.partner);
    }

    wf::signal::connection_t<wf::input_event_signal<wlr_pointer_hold_begin_event>> on_hold_begin =
        [=] (wf::input_event_signal<wlr_pointer_hold_begin_event> *ev)
    { touchpad_hold_begin(ev->device, ev->event->fingers, ev->event->time_msec); };
    wf::signal::connection_t<wf::input_event_signal<wlr_pointer_hold_end_event>> on_hold_end =
        [=] (wf::input_event_signal<wlr_pointer_hold_end_event>*) { cancel_touchpad_hold(); };

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
        if (ev->event->state == WL_POINTER_BUTTON_STATE_PRESSED && is_touchpad(ev->device))
            cancel_touchpad_hold(); // a click came first (L24)
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
    // Test sessions can hold the long-press timer while still delivering real touch input.
    bool test_hold_lift_timer = false;
    std::weak_ptr<scottland::frame_t> lifted_frame;
    int lifted_finger = -1;
    std::string pop_sound;

    void cancel_hold()
    {
        hold_timer.disconnect();
        hold_finger = -1;
        hold_view.reset();
    }

    // Touch for apps that ignore touch (L26): such an app never gets the touch at all. Scottland
    // takes it before delivery (the window under the finger is found here, as the compositor
    // hasn't routed it yet) and gives the app only the pointer equivalent: scrolling, a quick tap
    // as a click; a long press still lifts the window.
    std::map<int, std::weak_ptr<wf::view_interface_t>> captured_touches;  // finger -> the window it's on
    /** Where a touch event's 0..1 position is on the screens (the touchscreen's own screen, or
     *  the whole layout when it names none). */
    static wf::pointf_t touch_to_layout(wlr_touch *touch, double x, double y)
    {
        wf::geometry_t box{0, 0, 0, 0};
        wf::output_t *mapped = nullptr;
        for (auto output : wf::get_core().output_layout->get_outputs())
        {
            if (touch && touch->output_name && (output->to_string() == touch->output_name))
            {
                mapped = output;
            }
        }

        auto outputs = wf::get_core().output_layout->get_outputs();
        if (!mapped && (outputs.size() == 1))
        {
            mapped = outputs.front();
        }

        if (mapped)
        {
            box = mapped->get_layout_geometry();
        } else
        {
            wlr_box whole;
            wlr_output_layout_get_box(wf::get_core().output_layout->get_handle(), nullptr, &whole);
            box = {whole.x, whole.y, whole.width, whole.height};
        }

        return {box.x + x * box.width, box.y + y * box.height};
    }

    wf::signal::connection_t<wf::input_event_signal<wlr_touch_down_event>> on_touch_down_capture =
        [=] (wf::input_event_signal<wlr_touch_down_event> *ev)
    {
        if (!captured_touches.empty() || !wf::get_core().get_touch_state().fingers.empty())
        {
            return;  // another finger is down: a multi-finger touch, the app's as usual
        }

        auto at  = touch_to_layout(ev->event->touch, ev->event->x, ev->event->y);
        auto hit = wf::get_core().scene()->find_node_at(at);
        if (!hit || dynamic_cast<scottland::frame_t*>(hit->node.get()))
        {
            return;  // nothing, or the halo (the frame handles it)
        }

        auto view = wf::toplevel_cast(wf::node_to_view(hit->node->shared_from_this()));
        if (view && view->is_mapped() && frame_of(view, false) && wants_touch_scroll(view))
        {
            // The compositor follows the touch as usual; the app gets nothing of it.
            ev->mode = wf::input_event_processing_mode_t::NO_CLIENT;
            captured_touches[ev->event->touch_id] = view->weak_from_this();
        }
    };

    wf::signal::connection_t<wf::input_event_signal<wlr_touch_motion_event>> on_touch_motion_capture =
        [=] (wf::input_event_signal<wlr_touch_motion_event> *ev)
    {
        if (captured_touches.count(ev->event->touch_id))
        {
            ev->mode = wf::input_event_processing_mode_t::NO_CLIENT;
        }
    };

    wf::signal::connection_t<wf::input_event_signal<wlr_touch_up_event>> on_touch_up_capture =
        [=] (wf::input_event_signal<wlr_touch_up_event> *ev)
    {
        if (captured_touches.count(ev->event->touch_id))
        {
            ev->mode = wf::input_event_processing_mode_t::NO_CLIENT;
        }
    };

    wf::signal::connection_t<wf::post_input_event_signal<wlr_touch_down_event>> on_touch_down =
        [=] (wf::post_input_event_signal<wlr_touch_down_event> *ev)
    {

        int finger = ev->event->touch_id;
        log_widget_touch(finger);
        if ((hold_finger >= 0) || (lifted_finger >= 0) ||
            (wf::get_core().get_touch_state().fingers.size() != 1))
        {
            if (wf::get_core().get_touch_state().fingers.size() == 1)
            {
                LOGI("scottland: touch ignored: a finger still counted down (hold ", hold_finger,
                    ", lifted ", lifted_finger, ")");
            }

            cancel_hold();  // a second finger: this is a multi-finger touch, never a lift
            end_touch_scroll(scroll_finger, false);
            return;
        }

        // A captured touch has no focus in the compositor (it goes to no app): its window is known.
        auto captured = captured_touches.find(finger);
        auto focus = wf::get_core().get_touch_focus(finger);
        if ((captured == captured_touches.end()) && (!focus || dynamic_cast<scottland::frame_t*>(focus.get())))
        {
            return;  // nothing, or the halo (the frame handles it)
        }

        auto view = captured != captured_touches.end() ? wf::toplevel_cast(captured->second.lock()) :
            wf::toplevel_cast(wf::node_to_view(focus));
        if (!view || !view->is_mapped() || !frame_of(view, false) ||
            !(view->get_allowed_actions() & wf::VIEW_ALLOW_MOVE))
        {
            return;
        }

        if (auto link = link_of_widget(view))
        {
            LOGI("scottland: touch on widget ", view->get_id(), link->touch_drag ? " (a drag moves it)" :
                " (its own drags)");
        }

        start_touch_scroll(finger, view);
        hold_finger = finger;
        hold_origin = wf::get_core().get_touch_position(finger);
        hold_view   = view->weak_from_this();
        hold_timer.set_timeout(std::max(50, (int)lift_delay), [=] ()
        {
            if (getenv("SCOTTLAND_TEST_MODEL") && test_hold_lift_timer) return;
            lift_held_window();
        });
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
            // A widget whose manifest says drags are never its own (WG18) moves now, no long
            // press; a tap is still the widget's. Anything else: the touch is the app's (scroll,
            // rotate, draw...).
            auto view = wf::toplevel_cast(hold_view.lock());
            auto link = view ? link_of_widget(view) : nullptr;
            if (link && link->touch_drag)
            {
                LOGI("scottland: touch drag on widget ", view->get_id(), ": moving it");
                lift_held_window(false);
            } else
            {
                cancel_hold();
            }
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
        captured_touches.erase(ev->event->touch_id);
        if (ev->event->touch_id == lifted_finger)
        {
            lifted_finger = -1;
            if (auto frame = lifted_frame.lock())
            {
                frame->drop();
            }

            lifted_frame.reset();
            // The live grab normally ends it; finish it if touch focus already changed.
            if (drag->view && wf::get_core().get_touch_state().fingers.empty())
            {
                drag->handle_input_released();
            }
        }
    };

    /** A touch landing on a widget: what decides it, in the log (diagnosing touches on real input). */
    void log_widget_touch(int finger)
    {
        auto at  = wf::get_core().get_touch_position(finger);
        auto hit = wf::get_core().scene()->find_node_at(at);
        auto hit_view = hit ? wf::toplevel_cast(wf::node_to_view(hit->node->shared_from_this())) : nullptr;
        auto focus = wf::get_core().get_touch_focus(finger);
        bool on_frame = hit && dynamic_cast<scottland::frame_t*>(hit->node.get());
        wayfire_toplevel_view widget = (hit_view && is_widget(hit_view)) ? hit_view : nullptr;
        if (!widget && on_frame)
        {
            for (auto& [id, link] : model.widgets)
            {
                auto w = wf::toplevel_cast(link.widget.lock());
                if (w && (frame_of(w, false).get() == hit->node.get()))
                {
                    widget = w;
                }
            }
        }

        if (!widget)
        {
            return;
        }

        auto link = link_of_widget(widget);
        LOGI("scottland: touch down on widget ", widget->get_id(), " at ", at.x, ",", at.y, ": hit ",
            on_frame ? "its halo" : "its surface", ", touch focus ", focus ? (dynamic_cast<scottland::frame_t*>(
            focus.get()) ? "a halo" : "a surface") : "none", ", fingers ",
            wf::get_core().get_touch_state().fingers.size(), ", hold ", hold_finger, ", lifted ", lifted_finger,
            ", movable ", (widget->get_allowed_actions() & wf::VIEW_ALLOW_MOVE) ? "yes" : "no",
            ", touch_drag ", (link && link->touch_drag) ? "yes" : "no");
    }

    void lift_held_window(bool pop = true)
    {
        int finger = hold_finger;
        auto view  = wf::toplevel_cast(hold_view.lock());
        cancel_hold();
        auto captured = captured_touches.find(finger);
        bool is_captured = captured != captured_touches.end();
        if (!view || !view->is_mapped() || drag->view || !wf::get_core().get_touch_state().fingers.count(finger))
        {
            return;
        }

        auto frame = frame_of(view, false);
        if (!frame)
        {
            return;
        }

        end_touch_scroll(finger, false);
        if (!is_captured)  // (a captured touch never reached the app)
        {
            // The app already has this touch: tell it to forget it.
            auto seat  = wf::get_core().get_current_seat();
            auto point = wlr_seat_touch_get_point(seat, finger);
            if (point && point->client)
            {
                wlr_seat_touch_notify_cancel(seat, point->client);
            }
        }

        wf::get_core().default_wm->focus_raise_view(view);
        frame->lift();
        if (pop)
        {
            play_pop();
        }

        lifted_frame = frame;
        start_touch_drag(view, finger);
    }

    /** Drag `view` with finger `finger`, grabbed exactly where the finger is. */
    void start_touch_drag(wayfire_toplevel_view view, int finger, std::optional<wf::pointf_t> where = {})
    {
        if (drag->view)
        {
            return;
        }

        lifted_finger = finger;
        auto at = where.value_or(wf::get_core().get_touch_position(finger));
        model.drag.input_override = at;
        drag->set_input(finger);
        drag->set_pending_drag(at);
        // Kept until the drag ends so the common start logic uses the finger, not the cursor.
        drag->start_drag(view);
        note_drag_start();  // now, where the finger is: the drag may end before it moves
    }

    /** The lift's sound, synthesized (not a sample): a "bloop", like a bubble. A soft sine whose
     *  pitch rises quickly, with a gentle attack, a touch of second harmonic for roundness, and
     *  no noise. Written once as a WAV in the runtime directory. */
    void synthesize_pop()
    {
        const char *session = getenv("SCOTTLAND_SESSION_DIR");
        const char *runtime = getenv("XDG_RUNTIME_DIR");
        std::string dir = (session && *session) ? std::string(session) :
            std::string(runtime ? runtime : "/tmp") + "/scottland";
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

    // Apps that take the touch-as-pointer scrolling as a wheel rather than smooth scrolling
    // ([scottland] touch_scroll_wheel = <app-id regex>; shipped: Ghostty, which ignores smooth
    // scrolling from Scottland's pointer).
    wf::option_wrapper_t<std::string> touch_scroll_wheel{"scottland/touch_scroll_wheel"};
    bool scroll_as_wheel = false;

    bool wants_wheel(wayfire_view view)
    {
        std::string pattern = touch_scroll_wheel;
        try {
            return view && !pattern.empty() && std::regex_search(view->get_app_id(), std::regex(pattern, std::regex::icase));
        } catch (const std::regex_error&)
        {
            LOGE("scottland: bad touch_scroll_wheel regex: ", pattern);
            return false;
        }
    }

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

    void start_touch_scroll(int finger, wayfire_view view, std::optional<wf::pointf_t> at = {})
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
        scroll_as_wheel = wants_wheel(view);
        scroll_finger  = finger;
        scroll_moved   = false;
        scroll_origin  = scroll_last = at.value_or(wf::get_core().get_touch_position(finger));
        scroll_down_time = scroll_last_time = now_msec();
        scroll_velocity  = {0, 0};
        touch_pointer->move_to(scroll_origin);  // the pointer goes where the finger is
    }

    void touch_scroll_motion(int finger, std::optional<wf::pointf_t> where = {})
    {
        if (finger != scroll_finger)
        {
            return;
        }

        auto at = where.value_or(wf::get_core().get_touch_position(finger));
        if (!scroll_moved && (std::hypot(at.x - scroll_origin.x, at.y - scroll_origin.y) < HOLD_SLOP))
        {
            return;  // not yet a scroll: still a tap, or a long press to lift
        }

        scroll_moved = true;
        uint32_t now = now_msec();
        double dt = std::max(1u, now - scroll_last_time);
        wf::pointf_t d = {at.x - scroll_last.x, at.y - scroll_last.y};
        // Direct manipulation: the content follows the finger.
        touch_pointer->scroll(-d.x, -d.y, scroll_as_wheel);
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
            touch_pointer->scroll(0, 0, scroll_as_wheel);  // stop
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
            touch_pointer->scroll(0, 0, scroll_as_wheel);
            return;
        }

        momentum.set_timeout(16, [=] ()
        {
            touch_pointer->scroll(scroll_velocity.x * 16, scroll_velocity.y * 16, scroll_as_wheel);
            scroll_velocity = {scroll_velocity.x * 0.94, scroll_velocity.y * 0.94};
            if (std::hypot(scroll_velocity.x, scroll_velocity.y) < 0.03)
            {
                touch_pointer->scroll(0, 0, scroll_as_wheel);
                return false;
            }

            return true;
        });
    }

    // Isolated test sessions only: a virtual touchpad that emits wlroots' touchpad events
    // (hold, swipe, button) the way libinput's backend does, so they take Wayfire's real input
    // path: core input signals, plugins, then the client's gesture protocol.
    wf::ipc::method_callback test_touchpad_input = [=] (wf::json_t data) -> wf::json_t
    {
        if (!getenv("SCOTTLAND_TEST_MODEL")) return wf::ipc::json_error("test sessions only");
        if (!test_touchpad) test_touchpad = std::make_unique<virtual_pointer_t>("scottland-test-touchpad");
        std::string event = data.has_member("event") && data["event"].is_string() ? data["event"].as_string() : "";
        uint32_t fingers = data.has_member("fingers") ? uint32_t(data["fingers"].as_int()) : 3;
        bool cancelled = data.has_member("cancelled") && data["cancelled"].as_bool();
        if (event == "hold_begin") test_touchpad->hold_begin(fingers);
        else if (event == "hold_end") test_touchpad->hold_end(cancelled);
        else if (event == "swipe_begin") test_touchpad->swipe_begin(fingers);
        else if (event == "swipe_update") test_touchpad->swipe_update(fingers,
            data.has_member("dx") ? data["dx"].as_double() : 0.0, data.has_member("dy") ? data["dy"].as_double() : 0.0);
        else if (event == "swipe_end") test_touchpad->swipe_end(cancelled);
        else if (event == "scroll") test_touchpad->scroll(  // two-finger scroll (finger source)
            data.has_member("dx") ? data["dx"].as_double() : 0.0, data.has_member("dy") ? data["dy"].as_double() : 0.0);
        else if (event == "button") test_touchpad->press(
            data.has_member("button") && data["button"].as_string() == "left" ? BTN_LEFT : BTN_MIDDLE,
            data.has_member("pressed") && data["pressed"].as_bool());
        else if (!event.empty()) return wf::ipc::json_error("unknown touchpad event");
        auto reply = wf::ipc::json_ok();
        reply["hold_pending"] = bool(touchpad_hold);
        reply["swipe_moving"] = swipe_moving;
        reply["middle_pending"] = middle_pending;
        reply["middle_resizing"] = middle_resizing;
        reply["dragging"] = bool(drag->view);
        return reply;
    };

    // Tests can't produce real touchpad gestures: this feeds the same handlers synthetic ones.
    wf::ipc::method_callback test_input = [=] (wf::json_t data) -> wf::json_t
    {
        // Real wlroots pointer-axis input for isolated settings scroll tests. This is
        // deliberately unavailable in ordinary sessions, and never sets QML state.
        // A test-only timer hold keeps the real long touch armed past its production timeout.
        if (getenv("SCOTTLAND_TEST_MODEL") && data.has_member("hold_lift_timer") &&
            data["hold_lift_timer"].is_bool())
        {
            test_hold_lift_timer = data["hold_lift_timer"].as_bool();
        }
        if (getenv("SCOTTLAND_TEST_MODEL") && data.has_member("scroll_y"))
        {
            if (!touch_pointer) touch_pointer = std::make_unique<virtual_pointer_t>();
            // Virtual touchscreen scroll bypasses L17 by design. An explicit test
            // touchpad follows the shipped compositor multiplier before Qt sees it.
            double delta = data["scroll_y"].as_double();
            if (data.has_member("touchpad") && data["touchpad"].as_bool())
                delta *= touchpad_scroll_factor().first;
            touch_pointer->scroll(0, delta,
                data.has_member("wheel") && data["wheel"].as_bool());
        }
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
        if (getenv("SCOTTLAND_TEST_MODEL"))
        {
            auto held = hold_view.lock();
            reply["hold_window"] = held ? held->get_id() : 0;
            auto anchor = drag->view ? drag->view : wf::toplevel_cast(wf::get_core().seat->get_active_view());
            reply["avoidance_anchor_window"] = anchor ? anchor->get_id() : 0;
            reply["lift_timer_held"] = test_hold_lift_timer;
        }
        reply["lifted"]     = lifted_finger >= 0;
        reply["dragging"]   = (bool)drag->view;
        reply["drag_renderer"] = drag->view ? (drag->is_live() ? "scottland-live" : "wayfire-move") : "none";
        reply["drag_center"] = model.drag.last_center;
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
    std::unique_ptr<scottland::live_drag_t> drag = std::make_unique<scottland::live_drag_t>();

    wf::option_wrapper_t<wf::buttonbinding_t> move_button{"scottland/move"};
    wf::option_wrapper_t<wf::buttonbinding_t> move_shift_button{"scottland/move_shift"};
    wf::button_callback on_move = [this] (auto)
    {
        auto view = gesture_target();
        if (!view || drag->view) return false;
        start_pointer_drag(view, wf::buttonbinding_t(move_button).get_button());
        return bool(drag->view);
    };
    wf::button_callback on_move_shift = [this] (auto)
    {
        auto view = gesture_target();
        if (!view || drag->view) return false;
        start_pointer_drag(view, wf::buttonbinding_t(move_shift_button).get_button());
        return bool(drag->view);
    };

    void start_pointer_drag(wayfire_toplevel_view view, uint32_t button = BTN_LEFT)
    {
        drag->set_input(-1, false, button);
        drag->set_pending_drag(wf::get_core().get_cursor_position());
        drag->start_drag(view);
    }

    static constexpr double CLICK_SLOP = 6.0;  // a press and release within this is a click, not a move

    wf::wl_timer<false> held_above_timer;

    void set_above(wayfire_toplevel_view view, bool above)
    {
        if (view && view->get_output())
        {
            wf::wm_actions_set_above_state_signal signal;
            signal.view  = view;
            model.windows[view->get_id()].above = above;
            signal.above = model.windows[view->get_id()].above;
            view->get_output()->emit(&signal);
        }
    }

    void hold_above(wayfire_toplevel_view view)
    {
        if (model.drag.held_above.lock().get() != view.get())
        {
            release_above();
            if (view->has_data("wm-actions-above"))
            {
                return;  // the user's own: theirs to change
            }

            set_above(view, true);
            model.drag.held_above = view->weak_from_this();
        }

        held_above_timer.set_timeout(DRAG_CHAIN_MS, [=] () { release_above(); });
    }

    void release_above()
    {
        held_above_timer.disconnect();
        auto view = wf::toplevel_cast(model.drag.held_above.lock());
        model.drag.held_above.reset();
        if (view && view->is_mapped() && !is_widget(view))
        {
            set_above(view, false);
            // Back with the ordinary windows, it must not end up in front of the one the user is on.
            auto active = wf::toplevel_cast(wf::get_core().seat->get_active_view());
            if (active && (active != view) && active->is_mapped())
            {
                wf::get_core().default_wm->focus_raise_view(active);
            }
        }
    }

    void note_drag_start()
    {
        if (model.drag.started || !drag->view)
        {
            return;
        }

        keyboard_motions.erase(drag->view->get_id()); // catch the object at its current position
        // Grabbed where it's drawn, peeking or not (peek-strip decision 9). The committed move
        // applies at the next idle, so this start reads placed_geometry().
        commit_grab_offset(drag->view);
        drag_velocity.clear();
        auto input = model.drag.input_override.value_or(wf::get_core().get_cursor_position());
        drag_velocity.add(now_msec(), input.x, input.y);
        window_entries();
        remember_window(drag->view);
        // Esc puts back the pin it had (L27, WP1); the drag may clear it just below.
        auto pin_at_start = origin_of(drag->view).pin;
        bypass_window_keys();  // L31 owns Alt for this entire drag chord
        model.drag.started = true;
        // Dragging it again without Shift: it follows the zones again (L31).
        if (auto view = wf::toplevel_cast(drag->view); view && !shift_held() && !is_widget(view))
        {
            pin_scale(view, std::nullopt);
        }

        if (model.drag.held_above.lock().get() == drag->view.get())
        {
            held_above_timer.disconnect();  // picked up again: above until this drag's drop
        } else
        {
            release_above();
        }

        model.drag.start_cursor = model.drag.input_override.value_or(wf::get_core().get_cursor_position());
        // Every resize's after-care stops: it would move the window back toward its center.
        for (auto& [output, instance] : output_instance)
        {
            instance->stop_settling();
        }

        // Remember where across the window it was grabbed. The drag keeps the grabbed point at
        // the same fraction of the view's bounding box (which includes the halo margin), so
        // measure it the same way, or the predicted center (and zone, and scale) drifts from
        // where the window really lands.
        auto output = drag->view->get_output();
        auto cursor = model.drag.input_override.value_or(wf::get_core().get_cursor_position());
        double local_x = cursor.x - (output ? output->get_layout_geometry().x : 0);
        // Use the window's resting box (its scaled width plus the halo margin), not the live
        // bounding box: a just-lifted window is mid-bulge, which inflates the box for a moment.
        auto geometry = placed_geometry(drag->view);
        double drawn  = geometry.width * displayed_scale(drag->view);
        auto frame    = frame_of(drag->view, false);
        model.drag.margin   = frame ? frame->margin() :
            std::max(0.0, (drag->view->get_bounding_box().width - drawn) / 2.0);
        double box   = drawn + 2 * model.drag.margin;
        double avoidance_x = 0;
        if (auto visual = hint_visuals.find(drag->view->get_id());
            visual != hint_visuals.end() && visual->second.offset_attached)
            avoidance_x = visual->second.offset->translation_x;
        // The pointer hit the displayed surface, which may be translated away from
        // its true frame. Include that temporary presentation in the grab fraction
        // so focusing/grabbing cannot pull the visible window out from under the cursor.
        double left  = geometry.x + avoidance_x + geometry.width / 2.0 - box / 2.0;
        model.drag.relative_x = box > 0 ? (local_x - left) / box : 0.5;
        // Where Esc sends it back (WG14). Picked up again soon after it was let go (fingers
        // reset on the touchpad, out of room), it's the same move: keep the first origin.
        bool continued = (model.drag.last_drop.became == drag->view->get_id()) &&
            ((int32_t)(now_msec() - model.drag.last_drop_at) < DRAG_CHAIN_MS);
        stop_glide(drag->view);  // picked up again mid-glide: it's where it's drawn
        model.drag.widget = is_widget(drag->view) ? drag->view->get_id() : 0;
        model.drag.origin = origin_of(drag->view);
        model.drag.origin.pin = pin_at_start;
        if (continued)
        {
            auto view = model.drag.origin.view;
            model.drag.origin = model.drag.last_drop;
            model.drag.origin.view = view;  // the window being dragged now (maybe the other form)
        }

        LOGI("scottland: drag start: window ", drag->view->get_id(), continued ? " continues the move" :
            " starts a move", " (", (int32_t)(now_msec() - model.drag.last_drop_at), " ms after the last drop, of window ",
            model.drag.last_drop.became, "); Esc goes to ", model.drag.origin.position.x, ",", model.drag.origin.position.y);
        model.drag.cancelled = false;
        auto running = transitions.find(drag->view->get_id());
        model.drag.target = running != transitions.end() ? running->second.animation.end : displayed_scale(drag->view);
        publish_model();
    }

    wf::signal::connection_t<wf::move_drag::drag_focus_output_signal> on_drag_output =
        [=] (wf::move_drag::drag_focus_output_signal *ev)
    {
        if (!ev->previous_focus_output)
        {
            note_drag_start();
        }
    };

    // Live morph (WG13): while a window is dragged over a widget rail it turns into its widget
    // (the frame reshapes to the widget's size while the contents cross-fade to the widget's), and
    // a widget dragged off its rail turns back into its window the same way; the drop keeps the
    // shape shown. The widget is launched (unseen) when the drag first reaches the rail; until
    // it shows, the frame reshapes around the window's own contents.

    wf::wl_timer<true> morph_tick;

    /** Is the dragged view showing its widget shape (or heading there)? */
    bool morph_widget_shaped() const
    {
        return model.drag.morph && (model.drag.morph->from_widget ? !model.drag.morph->toward : model.drag.morph->toward);
    }

    wayfire_toplevel_view morph_other()
    {
        auto dragged = wf::toplevel_cast(model.drag.morph ? model.drag.morph->dragged.lock() : nullptr);
        if (!dragged)
        {
            return nullptr;
        }

        if (model.drag.morph->from_widget)
        {
            auto link = link_of_widget(dragged);
            return link ? wf::toplevel_cast(link->window.lock()) : nullptr;
        }

        auto link = link_of_window(dragged);
        return link ? wf::toplevel_cast(link->widget.lock()) : nullptr;
    }

    void end_morph()
    {
        if (!model.drag.morph)
        {
            return;
        }

        if (auto dragged = wf::toplevel_cast(model.drag.morph->dragged.lock()))
        {
            if (auto frame = frame_of(dragged, false))
            {
                frame->damage();
                frame->morph = {};
                frame->damage();
            }
        }

        model.drag.morph.reset();
        morph_tick.disconnect();
        publish_model();
    }

    /** Follow the drag: decide which form it should show, and start the widget if it's needed. */
    void update_drag_morph(wayfire_toplevel_view view, wf::output_t *output, wf::pointf_t pointer)
    {
        bool dragging_widget = is_widget(view);
        if (!model.drag.morph || (model.drag.morph->dragged.lock().get() != view.get()))
        {
            end_morph();
            if (!dragging_widget && !can_widgetize(view))
            {
                return;
            }

            model.drag.morph.emplace();
            double bounce = std::clamp(double(widget_bounce), 0.0, 0.1);
            if (bounce > 0)
                model.drag.morph->shape = wf::animation::simple_animation_t{
                    wf::create_option<int>(360), [bounce] (double t) { return scottland::widget_spring(t, bounce); }};
            model.drag.morph->dragged     = view->weak_from_this();
            model.drag.morph->from_widget = dragging_widget;
            model.drag.morph->shape.set(0, 0);
            model.drag.morph->fade.set(0, 0);
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
        double x1 = pointer.x - origin - model.drag.relative_x * box + margin;
        double x2 = x1 + r.width();
        model.drag.morph->center_x = (x1 + x2) / 2.0;
        auto in_rail = [&] (double x) { return place_at(output, std::clamp(x, 0.0, width - 1), width).zone == zone_t::widget; };

        // The pointer (or finger) decides, not the window's geometry (WG1): entering the rail
        // makes it a widget, leaving the rail's widgets makes it a window. The drop follows the
        // shape shown.
        double at = pointer.x - origin;
        bool want_widget = on_rail(output, at, width, morph_widget_shaped() ?
            (model.drag.morph->from_widget ? view : morph_other()) : nullptr);
        bool toward = model.drag.morph->from_widget ? !want_widget : want_widget;
        if (toward != model.drag.morph->toward)
        {
            if (!model.drag.morph->from_widget && toward)
            {
                // Launched now, unseen, so it's ready to fade in; on the rail the drag is over.
                widgetize(view, true, at < width / 2 ? "left" : "right", "drag-preview");
            }

            model.drag.morph->toward = toward;
            model.drag.morph->shape.animate(model.drag.morph->shape, toward ? 1.0 : 0.0);
        }

        if (!morph_tick.is_connected())
        {
            morph_tick.set_timeout(8, [=] () { return step_morph(); });
        }
    }

    bool step_morph()
    {
        if (!model.drag.morph)
        {
            return false;
        }

        auto dragged = wf::toplevel_cast(model.drag.morph->dragged.lock());
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

        if (other && other->is_mapped() && other->get_output() && (!model.drag.morph->snapshot_ready || (model.drag.morph->ticks % 2 == 0)))
        {
            auto other_frame = frame_of(other, false);
            if (other_frame && other_frame->presentation)
            {
                auto image = scottland::widget_morph_renderer().freeze(*other_frame->presentation,
                    other->get_output()->handle->scale);
                model.drag.morph->snapshot = image;
                model.drag.morph->snapshot_box = image.box;
                model.drag.morph->other_geometry = {0, 0, image.width, image.height};
            } else
            {
                model.drag.morph->snapshot = scottland::widget_image_t::capture(other, 0);
                model.drag.morph->other_geometry = other->get_geometry();
                model.drag.morph->snapshot_box = model.drag.morph->snapshot.box;
                model.drag.morph->snapshot_box.x += model.drag.morph->other_geometry.x;
                model.drag.morph->snapshot_box.y += model.drag.morph->other_geometry.y;
            }
            model.drag.morph->snapshot_ready = bool(model.drag.morph->snapshot);
        }

        model.drag.morph->ticks++;
        if (!model.drag.morph->toward && (drag->view != dragged) && !model.drag.morph->shape.running() && !model.drag.morph->fade.running())
        {
            end_morph();  // a cancelled drag finished morphing back
            return false;
        }

        double fade_to = (model.drag.morph->toward && model.drag.morph->snapshot_ready && other) ? 1.0 : 0.0;
        if (std::abs(model.drag.morph->fade.end - fade_to) > 0.001)
        {
            model.drag.morph->fade.animate(model.drag.morph->fade, fade_to);
        }

        // The other form's size on screen: the widget at 100%, or the window at the scale it would
        // have where the frame is now.
        // Until the widget exists (its program takes a moment to start the first time), aim for
        // the size the default card will have for this window: its text, up to the card's maximum,
        // or the square icon when widgets are collapsed.
        double w = PROVISIONAL_WIDGET_W, h = PROVISIONAL_WIDGET_H, scale = 1.0;
        if (!model.drag.morph->from_widget)
        {
            size_t chars = std::max(dragged->get_title().size(), dragged->get_app_id().size());
            bool icon = (model.widget_mode == widget_mode_t::collapsed) && !widgets_revealed();
            w = icon ? h : std::clamp(102.0 + 7.6 * chars, h, 320.0);
        }

        if (model.drag.morph->from_widget && other)
        {
            auto output = output_alive(drag->current_output) ? drag->current_output : dragged->get_output();
            scale = output ? place_at(output, model.drag.morph->center_x, output->get_relative_geometry().width).scale : 1.0;
            auto g = other->get_geometry();
            w = g.width * scale;
            h = g.height * scale;
        } else if (other)
        {
            auto g = other->get_geometry();
            w = g.width;
            h = g.height;
        }

        // The widget's size it aims for changes when the widget appears: follow it smoothly
        // (~150 ms) instead of snapping.
        if ((model.drag.morph->shown_w <= 0) || model.drag.morph->from_widget)  // a window's size tracks the pointer exactly (L8)
        {
            model.drag.morph->shown_w = w;
            model.drag.morph->shown_h = h;
        } else
        {
            double follow = 1.0 - std::exp(-8.0 / 50.0);
            model.drag.morph->shown_w += (w - model.drag.morph->shown_w) * follow;
            model.drag.morph->shown_h += (h - model.drag.morph->shown_h) * follow;
        }

        frame->damage();
        frame->morph.shape = model.drag.morph->shape;
        frame->morph.fade  = model.drag.morph->fade;
        frame->morph.w     = model.drag.morph->shown_w;
        frame->morph.h     = model.drag.morph->shown_h;
        frame->morph.scale = scale;
        frame->morph.snapshot       = model.drag.morph->snapshot_ready ? model.drag.morph->snapshot : scottland::widget_image_t{};
        frame->morph.snapshot_box   = model.drag.morph->snapshot_box;
        frame->morph.other_geometry = model.drag.morph->other_geometry;
        frame->damage();
        if (drag->view == dragged && drag->current_output)
            update_rail_drag(dragged, drag->current_output);
        dragged->damage();  // through the drag's own transform, so it repaints with the pointer still
        return true;
    }

    // Esc cancels a drag (WG14): the window goes back where it was picked up, gliding from where
    // it was let go, and a drag that changed it into its other form (window/widget) morphs back.
    /** Where a drag picked a window up: which window, on which screen, where. */
    static constexpr int DRAG_CHAIN_MS = 2500;  // a new drag of the same window within this continues the move

    /** Its geometry, at the position a move just asked for: Wayfire applies moves at the next
     *  idle, and a grab may have just committed a peek offset (decision 9). The size stays the
     *  current one (a pending resize waits for the client). */
    static wf::geometry_t placed_geometry(wayfire_toplevel_view view)
    {
        auto g = view->get_geometry();
        auto pending = view->toplevel()->pending().geometry;
        g.x = pending.x; g.y = pending.y;
        return g;
    }

    drag_origin_t origin_of(wayfire_toplevel_view view)
    {
        auto g = placed_geometry(view);
        drag_origin_t origin;
        origin.view = origin.first_view = view->get_id();
        origin.output   = view->get_output();
        origin.position = {g.x, g.y};
        origin.first_widget = is_widget(view);
        if (auto link = link_of_widget(view))
        {
            origin.rail = link->rail;
        }
        else if (auto found = model.windows.find(view->get_id()); found != model.windows.end())
        {
            origin.pin = found->second.pinned_scale;
        }
        origin.first_size   = {g.width, g.height};
        return origin;
    }

    /** The origin recorded for this drag, if it's this window's. A drag that ended before it
     *  moved may not have recorded one (its start is noticed at the first motion): then the
     *  window is where it was picked up. Never another window's origin. */
    drag_origin_t origin_for(wayfire_toplevel_view view)
    {
        return model.drag.origin.view == view->get_id() ? model.drag.origin : origin_of(view);
    }
    static constexpr int GLIDE_MS = 260;
    // A glide: a window drawn moving from where it was to where it now is (Esc sending it home;
    // a widget settling against the screen edge after a drop). Several can run at once.
    struct glide_t
    {
        std::weak_ptr<wf::view_interface_t> view;
        double dx = 0, dy = 0;  // where it's drawn from, relative to where it is
        bool outward = false;   // drawn going away to (dx, dy) instead (then `done`)
        std::function<void()> done;
        bool cycle = false;
        double overshoot = 0, scale_from = 1, scale_to = 1;
        scottland::rectf_t bounds;
        std::chrono::steady_clock::time_point started;
        wf::animation::simple_animation_t progress{wf::create_option<int>(GLIDE_MS)};
    };
    std::map<uint64_t, glide_t> glides;
    wf::wl_timer<true> glide_tick;
    wf::option_wrapper_t<double> cycle_overshoot{"scottland/cycle_overshoot"};
    static constexpr double CYCLE_MS = 300;
    #include "spread-bridge.hpp"  // after glide_t

    void start_cycle_glide(wayfire_toplevel_view view, wf::pointf_t from, double from_scale,
        wf::pointf_t to, double to_scale)
    {
        auto g = view->get_geometry();
        double x = to.x, y = to.y;
        double amount = std::clamp(double(cycle_overshoot), 0.0, 10.0) / 100;
        if (amount == 0)
        {
            start_glide(view, from.x - x, from.y - y);
            set_scale(view, to_scale);
            return;
        }
        auto frame = frame_of(view, false);
        if (!frame || !view->get_output()) return;
        stop_glide(view);
        transitions.erase(view->get_id());
        auto& glide = glides[view->get_id()];
        glide.view = view->weak_from_this();
        glide.cycle = true;
        glide.dx = from.x - x; glide.dy = from.y - y;
        glide.scale_from = from_scale; glide.scale_to = to_scale;
        glide.overshoot = amount;
        glide.started = std::chrono::steady_clock::now();
        auto screen = view->get_output()->get_relative_geometry();
        // WP2/WP7: don't change an existing off-screen memory or an oversized
        // window's destination. Only permit overflow already in either endpoint.
        glide.bounds = {std::min({0.0, from.x - g.width * from_scale / 2, x - g.width * glide.scale_to / 2}),
            std::min({0.0, from.y - g.height * from_scale / 2, y - g.height * glide.scale_to / 2}),
            std::max({double(screen.width), from.x + g.width * from_scale / 2, x + g.width * glide.scale_to / 2}),
            std::max({double(screen.height), from.y + g.height * from_scale / 2, y + g.height * glide.scale_to / 2})};
        model.windows[view->get_id()].scale = glide.scale_to;
        publish_model();
        frame->damage();
        frame->translation_x = glide.dx; frame->translation_y = glide.dy;
        frame->scale_x = frame->scale_y = from_scale;
        frame->damage();
        if (!glide_tick.is_connected()) glide_tick.set_timeout(8, [=] () { return step_glides(); });
    }

    /** Draw `view` gliding away from where it is to (dx, dy) off it, then run `done`. */
    void start_glide_out(wayfire_toplevel_view view, double dx, double dy, std::function<void()> done)
    {
        auto frame = frame_of(view, false);
        if (!frame)
        {
            done();
            return;
        }

        stop_glide(view);
        auto& glide = glides[view->get_id()];
        glide.view = view->weak_from_this();
        glide.dx = dx;
        glide.dy = dy;
        glide.outward = true;
        glide.done = std::move(done);
        glide.progress.animate(0.0, 1.0);
        if (!glide_tick.is_connected())
        {
            glide_tick.set_timeout(8, [=] () { return step_glides(); });
        }
    }

    // Full screen is focus (FS1, tenet 6): while a fullscreen window is in front on a screen,
    // nothing interrupts it. That screen's widgets slide off its edges, and come back when it
    // isn't anymore; integrations hold notifications (focus.d hooks, run with "on" while any
    // screen is in focus, "off" after).
    std::map<wf::output_t*, std::unique_ptr<wf::signal::connection_t<wf::fullscreen_layer_focused_signal>>>
    fullscreen_watch;
    bool focus_hooks_on = false;

    void watch_fullscreen(wf::output_t *output)
    {
        auto watch = std::make_unique<wf::signal::connection_t<wf::fullscreen_layer_focused_signal>>(
            [=] (wf::fullscreen_layer_focused_signal *ev) { set_focus_mode(output, ev->has_promoted); });
        output->connect(watch.get());
        fullscreen_watch[output] = std::move(watch);
    }

    void unwatch_fullscreen(wf::output_t *output)
    {
        fullscreen_watch.erase(output);
        model.focused_outputs.erase(output);
        run_focus_hooks();
    }

    bool in_focus_mode(wf::output_t *output) const
    {
        return model.focused_outputs.count(output) > 0;
    }

    void set_focus_mode(wf::output_t *output, bool on)
    {
        if (in_focus_mode(output) == on)
        {
            return;
        }

        if (on)
        {
            model.focused_outputs.insert(output);
        } else
        {
            model.focused_outputs.erase(output);
        }

        LOGI("scottland: full screen on ", output->to_string(), on ? ": focus (widgets away)" : ": widgets back");
        update_widget_peeks();
        reconcile_rail_slides();
        run_focus_hooks();
        publish_model();
    }

    // Where a widget is drawn at its screen edge (FS1, and WG16's hidden mode): in its place,
    // away (slid off the edge, then hidden), or peeking in: while widgets are hidden and its app
    // needs the user, a strip of it shows at the edge, its halo breathing the attention color.
    // Each change slides from where it's drawn now, so reversals never jump.
    enum class rail_place_t { in, away, peeking };
    static const char *rail_place_name(rail_place_t place)
    {
        return place == rail_place_t::away ? "away" : place == rail_place_t::peeking ? "peeking" : "in";
    }

    static constexpr double PEEK_STRIP = 24;  // pt (the peek-strip depth), grows with text size
    struct rail_slide_t
    {
        rail_place_t to = rail_place_t::in;
        double from = 0;
        bool running = false;
        bool snap = false;  // take the place without sliding (a reload's takeover)
        wf::animation::simple_animation_t progress{wf::create_option<int>(GLIDE_MS)};
    };
    std::map<uint64_t, rail_slide_t> rail_slides;  // keyed by the app window, like model.widgets
    wf::wl_timer<true> rail_slide_tick;

    rail_place_t wanted_place(const widget_link_t& link)
    {
        return wanted_place(link, link.peek);
    }

    rail_place_t wanted_place(const widget_link_t& link, bool peek)
    {
        if (!link.docked() || window_keys.active)
        {
            return rail_place_t::in;  // asking for hints brings them back (WK12)
        }

        if (!link.away && widget_held(link))
        {
            return rail_place(link);  // never slides from under a grab
        }

        if (in_focus_mode(link.output))
        {
            return rail_place_t::away;  // full screen: attention waits (tenet 6)
        }

        if ((shown_widget_mode() != widget_mode_t::hidden) || peek)
        {
            return rail_place_t::in;
        }

        return needs_attention(link.window_id) ? rail_place_t::peeking : rail_place_t::away;
    }

    rail_place_t rail_place(const widget_link_t& link) const
    {
        auto found = rail_slides.find(link.window_id);
        return found == rail_slides.end() ? (link.away ? rail_place_t::away : rail_place_t::in) : found->second.to;
    }

    /** The slide offset for `place`, relative to where the widget is drawn in its place. */
    double place_offset(wayfire_toplevel_view widget, scottland::frame_t& frame, const widget_link_t& link,
        rail_place_t place)
    {
        if ((place == rail_place_t::in) || !widget->get_output())
        {
            return 0;
        }

        auto r = frame.screen_rect();
        double x1 = r.x1 - frame.rail_slide_x, x2 = r.x2 - frame.rail_slide_x;
        double width = widget->get_output()->get_relative_geometry().width;
        bool left = link.rail == "left";
        if (place == rail_place_t::away)
        {
            return left ? -(x2 + frame.margin()) : (width - x1 + frame.margin());
        }

        double strip = PEEK_STRIP * hints_palette.text_scale;
        return left ? std::min(0.0, strip - x2) : std::max(0.0, width - strip - x1);
    }

    void set_rail_slide(scottland::frame_t& frame, double x)
    {
        if (std::abs(frame.rail_slide_x - x) < 0.01)
        {
            return;
        }

        frame.damage();
        frame.rail_slide_x = x;
        frame.damage();
    }

    void focus_recent_shown_window()
    {
        for (auto id : focus_recency)
        {
            auto view = view_by_id(id);
            if (view && view->is_mapped() && view->get_root_node()->is_enabled() && !is_widget(view) &&
                !model.widgets.count(id))
            {
                wf::get_core().default_wm->focus_raise_view(view);
                return;
            }
        }

        wf::get_core().seat->refocus();
    }

    /** Show a widget that's away again, just off its edge, so it can slide in. Done before it
     *  changes presentation: its client draws (and the change animates) only while shown. */
    void return_from_away(widget_link_t& link)
    {
        if (!link.away)
        {
            return;
        }

        auto widget = wf::toplevel_cast(link.widget.lock());
        auto frame = (widget && widget->is_mapped() && widget->get_output()) ? frame_of(widget, false) : nullptr;
        if (!frame)
        {
            return;
        }

        set_rail_slide(*frame, place_offset(widget, *frame, link, rail_place_t::away));
        rail_slides[link.window_id].to = rail_place_t::away;
        link.away = false;
        transition_widget(link, link.lifecycle);
    }

    /** Start (or keep) every widget sliding toward the place it should be in. */
    void reconcile_rail_slides()
    {
        bool pending = false;
        for (auto& [id, link] : model.widgets)
        {
            auto place = wanted_place(link);
            auto found = rail_slides.find(id);
            pending |= (found == rail_slides.end()) ? (place != rail_place_t::in) || link.away :
                (found->second.to != place) || found->second.running || found->second.snap ||
                (place == rail_place_t::peeking);
        }

        if (pending && !rail_slide_tick.is_connected())
        {
            rail_slide_tick.set_timeout(8, [=] () { return step_rail_slides(); });
        }
    }

    bool step_rail_slides()
    {
        for (auto it = rail_slides.begin(); it != rail_slides.end();)
        {
            it = model.widgets.count(it->first) ? std::next(it) : rail_slides.erase(it);
        }

        bool active = false, changed = false;
        for (auto& [id, link] : model.widgets)
        {
            auto widget = wf::toplevel_cast(link.widget.lock());
            auto frame = (widget && widget->is_mapped() && widget->get_output()) ? frame_of(widget, false) : nullptr;
            if (!frame)
            {
                continue;  // logical visibility (link.away) still applies to a widget launching
            }

            auto [found, created] = rail_slides.try_emplace(id);
            auto& slide = found->second;
            if (created)
            {
                slide.to = link.away ? rail_place_t::away : rail_place_t::in;
                set_rail_slide(*frame, place_offset(widget, *frame, link, slide.to));
            }

            auto place = wanted_place(link);
            // A widget arriving (its window's image still turning into it) or settling against
            // the edge after a drop finishes that first, then slides away.
            bool busy = widget_transitions.count(id) || glides.count(widget->get_id());
            if (slide.snap)
            {
                slide.snap = false;
                slide.to = place;
                slide.running = false;
                set_rail_slide(*frame, place_offset(widget, *frame, link, place));
                if ((place == rail_place_t::away) != link.away)
                {
                    link.away = place == rail_place_t::away;
                    transition_widget(link, link.lifecycle);
                }

                changed = true;
                continue;
            }

            if ((place != slide.to) && (place != rail_place_t::in) && busy && !link.away)
            {
                active = true;
                continue;
            }

            if (place != slide.to)
            {
                if (link.away)
                {
                    // Coming back: from just off the edge, at its current size.
                    set_rail_slide(*frame, place_offset(widget, *frame, link, rail_place_t::away));
                    link.away = false;
                    transition_widget(link, link.lifecycle);
                }

                slide.from = frame->rail_slide_x;
                slide.to = place;
                slide.running = true;
                slide.progress.animate(0.0, 1.0);
                changed = true;
            }

            double target = place_offset(widget, *frame, link, slide.to);
            if (!slide.running)
            {
                set_rail_slide(*frame, target);  // a peeking widget follows its own size,
                active |= (slide.to == rail_place_t::peeking) && widget_transitions.count(widget->get_id());
                continue;                        // also while it expands or collapses
            }

            double t = slide.progress;
            set_rail_slide(*frame, slide.from + (target - slide.from) * t);
            widget->damage();
            if (slide.progress.running())
            {
                active = true;
                continue;
            }

            slide.running = false;
            set_rail_slide(*frame, target);
            active |= (slide.to == rail_place_t::peeking) && widget_transitions.count(widget->get_id());
            if (slide.to == rail_place_t::away)
            {
                link.away = true;
                transition_widget(link, link.lifecycle);
                // Keys never go to a widget that isn't there: focus passes to the window used
                // most recently that is still shown.
                if (wf::get_core().seat->get_active_view() == widget)
                {
                    focus_recent_shown_window();
                }
            }

            changed = true;
        }

        if (changed)
        {
            announce_widgets();
        }

        return active;
    }

    void run_focus_hooks()
    {
        bool any = false;
        any = !model.focused_outputs.empty();

        if (any == focus_hooks_on)
        {
            return;
        }

        focus_hooks_on = any;
        const char *hooks = getenv("SCOTTLAND_HOOKS");
        wf::get_core().run(shell_quote(std::string(hooks ? hooks : "/usr/lib/scottland") +
            "/libexec/scottland-focus-mode") + (any ? " on" : " off"));
    }

    /** Draw `view` gliding from (dx, dy) away to where it is. */
    void start_glide(wayfire_toplevel_view view, double dx, double dy)
    {
        auto frame = frame_of(view, false);
        if (!frame || ((std::abs(dx) < 0.5) && (std::abs(dy) < 0.5)))
        {
            return;
        }

        stop_glide(view);
        auto& glide = glides[view->get_id()];
        glide.view = view->weak_from_this();
        glide.dx = dx;
        glide.dy = dy;
        glide.progress.animate(0.0, 1.0);
        frame->damage();
        frame->translation_x = dx;
        frame->translation_y = dy;
        view->damage();
        if (!glide_tick.is_connected())
        {
            glide_tick.set_timeout(8, [=] () { return step_glides(); });
        }
    }

    wf::signal::connection_t<wf::input_event_signal<wlr_keyboard_key_event>> on_cancel_key =
        [=] (wf::input_event_signal<wlr_keyboard_key_event> *ev)
    {
        if (ev->mode == wf::input_event_processing_mode_t::IGNORE) return;
        if ((ev->event->keycode == KEY_ESC) && (ev->event->state == WL_KEYBOARD_KEY_STATE_PRESSED) &&
            drag->view && !model.drag.cancelled)
        {
            model.drag.cancelled = true;
            ev->mode = wf::input_event_processing_mode_t::IGNORE;  // the app under it doesn't get it
            drag->handle_input_released();
        }
    };

    /** The drag was cancelled and just let go: put the window back, drawn gliding home. */
    void cancel_drop(wayfire_toplevel_view view)
    {
        auto origin = origin_for(view);
        model.drag.origin = {};
        if (origin.first_view && (origin.first_view != view->get_id()) && cancel_form_change(view, origin))
        {
            return;
        }

        // Back to the screen it was picked up on (drawn gliding from where it was let go, in
        // layout coordinates, so across screens too).
        auto from_output = view->get_output();
        auto g = view->get_geometry();
        wf::point_t from{g.x, g.y};
        if (from_output)
        {
            auto layout = from_output->get_layout_geometry();
            from = {from.x + layout.x, from.y + layout.y};
        }

        if (output_alive(origin.output) && (origin.output != from_output))
        {
            wf::move_view_to_output(view, origin.output, false);
        }

        LOGI("scottland: Esc: window ", view->get_id(), " (", view->get_title(), ") back from ", g.x, ",", g.y,
            " to ", origin.position.x, ",", origin.position.y);
        move_window(view, origin.position.x, origin.position.y);
        if (auto link = link_of_widget(view))
        {
            // Esc restores the anchor too. A later title/collapse resize must not move the
            // widget back to its cancelled drop, or to a rail crossed during this move.
            link->rail = origin.rail;
            link->output = view->get_output();
            link->drop = {origin.position.x + origin.first_size.width / 2.0,
                origin.position.y + origin.first_size.height / 2.0};
            place_widget(view, view->get_output(), *link);
            announce_widgets();
        }
        auto placed = view->toplevel()->pending().geometry;
        wf::point_t to{(int)placed.x, (int)placed.y};
        if (view->get_output())
        {
            auto layout = view->get_output()->get_layout_geometry();
            to = {to.x + layout.x, to.y + layout.y};
        }

        double dx = from.x - to.x, dy = from.y - to.y;
        if (model.drag.morph && (model.drag.morph->dragged.lock().get() == view.get()) && model.drag.morph->toward)
        {
            model.drag.morph->toward = false;  // back to the form it had
            model.drag.morph->shape.animate(model.drag.morph->shape, 0.0);
        }

        if (auto link = link_of_window(view); link && link->previewing())
        {
            cancel_preview(*link);
        }

        if (!is_widget(view))
        {
            pin_scale(view, origin.pin);  // in its original form: its scale pin too (L27, L31)
        }

        apply(view);
        start_glide(view, dx, dy);
    }

    /** Esc on a move whose drop changed the form (WG14): put the first form back where the move
     *  began. True if it did. */
    bool cancel_form_change(wayfire_toplevel_view view, const drag_origin_t& origin)
    {
        auto layout_origin = [] (wf::output_t *output)
        {
            auto g = output ? output->get_layout_geometry() : wf::geometry_t{0, 0, 0, 0};
            return wf::pointf_t{(double)g.x, (double)g.y};
        };
        auto g = view->get_geometry();
        auto view_origin = layout_origin(view->get_output());
        wf::pointf_t from{view_origin.x + g.x + g.width / 2.0, view_origin.y + g.y + g.height / 2.0};  // layout
        wf::pointf_t home{origin.position.x + origin.first_size.width / 2.0,
            origin.position.y + origin.first_size.height / 2.0};  // on the screen it began on
        auto home_output = output_alive(origin.output) ? origin.output : view->get_output();

        if (auto link = link_of_widget(view); link && !origin.first_widget &&
            (link->window_id == origin.first_view))
        {
            // It began as a window and became this widget: the window comes back where it began.
            auto window = wf::toplevel_cast(link->window.lock());
            restore_window(*link, home);
            if (window)
            {
                if (home_output && (window->get_output() != home_output))
                {
                    wf::move_view_to_output(window, home_output, false);
                    auto wg = window->get_geometry();
                    move_window(window, std::round(home.x - wg.width / 2.0), std::round(home.y - wg.height / 2.0));
                }

                pin_scale(window, origin.pin);  // the pin it had before it became a widget
                apply(window);
                auto to = layout_origin(window->get_output());
                start_glide(window, from.x - (to.x + home.x), from.y - (to.y + home.y));
            }

            return true;
        }

        if (origin.first_widget && !is_widget(view))
        {
            // It began as a widget and became this window: a widget again, where it was (a widget
            // previewed on the way, dragging back toward a rail, goes).
            if (auto preview = link_of_window(view); preview && preview->previewing())
            {
                cancel_preview(*preview);
            }

            if (link_of_window(view))
            {
                return false;
            }

            if (home_output && (view->get_output() != home_output))
            {
                wf::move_view_to_output(view, home_output, false);
            }

            // Capture where the undocked app is shown; the entry morph returns it
            // to the original rail. Moving first would cut to that rail on Esc.
            widgetize(view, false, origin.rail, "cancel-form-return");
            if (auto link = link_of_window(view))
            {
                link->drop = home;
            }

            return true;
        }

        return false;
    }

    /** End a window's glide at once: it's drawn where it is. */
    void stop_glide(wayfire_toplevel_view view)
    {
        auto found = view ? glides.find(view->get_id()) : glides.end();
        if (found == glides.end())
        {
            return;
        }

        if (auto frame = frame_of(view, false))
        {
            frame->damage();
            frame->translation_x = frame->translation_y = 0;
            if (found->second.cycle) frame->scale_x = frame->scale_y = found->second.scale_to;
            frame->damage();
        }

        glides.erase(found);
    }

    bool step_glides()
    {
        // A glide moves only the drawn frame, so its end changes the layout window avoidance
        // sees without any geometry signal. Ask for one solve on the settled frames then
        // (WK13/WK36: a window the move left covered must still peek out in this hold).
        bool settled = false;
        for (auto it = glides.begin(); it != glides.end();)
        {
            auto view  = wf::toplevel_cast(it->second.view.lock());
            auto frame = view ? frame_of(view, false) : nullptr;
            if (!frame)
            {
                it = glides.erase(it);
                continue;
            }

            auto& glide = it->second;
            if (glide.cycle)
            {
                double elapsed = std::chrono::duration<double, std::milli>(
                    std::chrono::steady_clock::now() - glide.started).count();
                double left = scottland::windowing::cycle_spring_remaining(elapsed / CYCLE_MS, glide.overshoot);
                auto g = view->get_geometry();
                double x = g.x + g.width / 2.0, y = g.y + g.height / 2.0;
                double scale = glide.scale_to + (glide.scale_from - glide.scale_to) * left;
                // Scale has its own fixed target, never the spring position's
                // zone scale. Constrain the live footprint, even beside an output seam.
                scale = std::min({std::max(0.05, scale), glide.bounds.width() / g.width, glide.bounds.height() / g.height});
                frame->damage();
                frame->scale_x = frame->scale_y = scale;
                frame->translation_x = std::clamp(x + glide.dx * left,
                    glide.bounds.x1 + g.width * scale / 2, glide.bounds.x2 - g.width * scale / 2) - x;
                frame->translation_y = std::clamp(y + glide.dy * left,
                    glide.bounds.y1 + g.height * scale / 2, glide.bounds.y2 - g.height * scale / 2) - y;
                frame->damage(); view->damage();
                if (elapsed >= CYCLE_MS)
                {
                    frame->translation_x = frame->translation_y = 0;
                    frame->scale_x = frame->scale_y = glide.scale_to;
                    it = glides.erase(it);
                    settled = true;
                } else ++it;
                continue;
            }
            double left = glide.outward ? (double)glide.progress : 1.0 - (double)glide.progress;
            frame->damage();
            frame->translation_x = glide.dx * left;
            frame->translation_y = glide.dy * left;
            frame->damage();
            view->damage();
            if (!glide.progress.running())
            {
                auto done = std::move(glide.done);
                it = glides.erase(it);
                settled = true;
                if (done)
                {
                    done();  // (it puts the frame where it's to stay)
                } else
                {
                    frame->translation_x = frame->translation_y = 0;
                }

                continue;
            }

            ++it;
        }

        if (settled) refresh_layout_avoidance();
        return !glides.empty();
    }

    wf::signal::connection_t<wf::move_drag::drag_motion_signal> on_drag_motion =
        [=] (wf::move_drag::drag_motion_signal *ev)
    {
        auto view   = drag->view;
        auto output = drag->current_output;
        refresh_layout_avoidance();
        note_drag_start();
        drag_velocity.add(now_msec(), ev->current_position.x, ev->current_position.y);
        if (view && output && !view->pending_fullscreen())
        {
            update_drag_morph(view, output, ev->current_position);
            update_rail_drag(view, output, ev->current_position);
            publish_model();  // morph direction/center changes even when widget scale stays 1
        } else
        {
            cancel_rail_drag();
        }

        if (!view || !output || view->pending_fullscreen() || is_widget(view))
        {
            return;  // widgets stay at 100% wherever they're dragged
        }

        if (model.drag.morph && (model.drag.morph->dragged.lock().get() == view.get()) && model.drag.morph->toward)
        {
            // Shown as its widget: the window keeps the scale of where it is, for if it's dragged
            // back out.
            set_scale(view, place_at(output, model.drag.morph->center_x, output->get_relative_geometry().width).scale);
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
        // live drag draws the window in a box sized from the view's own box (this frame plus its
        // halo margin) divided by its zoom, placed so the grab sits at a fixed fraction of it.
        // Measure that box, the grab fraction and the margins now; then the center at any scale s
        // follows exactly: the box grows by unscaled * ds, around the grab.
        std::function<double(double)> center_at = [&] (double s)
        {
            return pointer_x + (0.5 - model.drag.relative_x) * (unscaled * s + 2 * model.drag.margin);
        };
        auto drag_box = view->get_transformed_node()->get_transformer<wf::scene::transformer_base_node_t>(
            "scottland-live-drag");
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

        auto zone_scale = [&] (double s) { return place_at(output, center_at(s), screen).scale; };
        model.drag.last_center = center_at(model.drag.target);
        apply_opacity(view, model.drag.last_center);

        // Shift held while dragging: keep the displayed scale through the drag and drop (L31).
        if (!is_widget(view))
        {
            if (shift_held())
            {
                auto& state = model.windows[view->get_id()];
                if (!state.pinned_scale)
                {
                    pin_scale(view, model.drag.target);
                }

                model.drag.target = *state.pinned_scale;
                set_scale(view, model.drag.target);
                return;
            } else if (model.windows[view->get_id()].pinned_scale)
            {
                pin_scale(view, std::nullopt);
            }
        }

        double chosen = zone_scale(model.drag.target);
        if (std::abs(chosen - model.drag.target) > 0.001)
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
                model.drag.target = chosen;
            }
        }

        set_scale(view, model.drag.target);
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
        swipe_moving = false;  // whatever ended it (a button release can end a swipe drag)
        reconcile_rail_slides();  // a widget held in place by the grab may go now
        if (model.drag.cancelled)
        {
            model.drag.cancelled = false;
            model.drag.input_override.reset();
            cancel_rail_drag();
            if (main && main->is_mapped())
            {
                cancel_drop(main);
            }

            release_above();  // Esc ends the move and its temporary layer ownership
            model.drag.last_drop = {};  // the move is over
            model.drag.widget = 0;
            model.drag.started = false;
            retry_widget_arrivals();
            publish_model();
            return;
        }

        if (main && model.drag.started)
        {
            update_rail_drag(main, main->get_output());  // validate the final landing footprint
            commit_rail_drag();
        }
        if (main)
        {
            model.drag.last_drop    = origin_for(main);
            model.drag.last_drop_at = now_msec();
            model.drag.last_drop.became = main->get_id();
            LOGI("scottland: drop: window ", main->get_id(), " (the move began at ", model.drag.last_drop.position.x, ",",
                model.drag.last_drop.position.y, ")");
        }

        model.drag.origin = {};

        std::optional<bool> widget_shaped;  // the shape a morphing drag showed at the drop (WG13)
        if (model.drag.morph && main && (model.drag.morph->dragged.lock().get() == main.get()))
        {
            widget_shaped = morph_widget_shaped();
        }

        // A click on a halo (pressed and let go where it was) is no move: it never changes a
        // widget into its window or the reverse.
        auto released_at = ev->grab_position; // authoritative for pointer, touch and touchpad drags
        if (main && model.drag.started && (std::hypot(released_at.x - model.drag.start_cursor.x,
            released_at.y - model.drag.start_cursor.y) < CLICK_SLOP))
        {
            widget_shaped = is_widget(main);
        }

        if (main && !is_widget(main) && widget_shaped.value_or(false))
            begin_window_widget_transition(main); // capture the shown drag before its renderer ends
        end_morph();
        if (main && main->is_mapped() && main->get_output() && !main->pending_fullscreen() && !is_widget(main) &&
            !widget_shaped.value_or(false))
        {
            auto geometry = main->get_geometry();
            double screen = main->get_output()->get_relative_geometry().width;
            double center = geometry.x + geometry.width / 2.0;
            if (shift_held())
            {
                pin_scale(main, model.drag.target);  // dropped with Shift held (L31)
            } else if (std::abs(place_at(main->get_output(), center, screen).scale - model.drag.target) > JUMP)
            {
                for (int d = 1; d <= 400; d++)
                {
                    int found = 0;
                    for (int sign : {-1, 1})
                    {
                        if (std::abs(place_at(main->get_output(), center + sign * d, screen).scale - model.drag.target) <= 0.003)
                        {
                            found = sign;
                            break;
                        }
                    }

                    if (found)
                    {
                        move_window(main, geometry.x + found * d, geometry.y);
                        break;
                    }
                }
            }
        }

        for (auto& dragged : ev->all_views)
        {
            if (dragged.view && dragged.view->is_mapped())
            {
                set_scale(dragged.view, is_widget(dragged.view) ? 1.0 : scale_for(dragged.view));
            }
        }

        model.drag.input_override.reset();
        if (main)
        {
            // What stands for it now, if the drop changed its form: a re-grab of that continues
            // the move (Esc goes back to where, and as what, it began).
            bool was_widget = is_widget(main);
            auto link = was_widget ? link_of_widget(main) : nullptr;
            uint64_t app_window = link ? link->window_id : 0;
            handle_widget_drop(main, widget_shaped, released_at);
            auto dropped = represented_view(was_widget ? app_window : main->get_id());
            if (dropped)
            {
                auto link = link_of_widget(dropped);
                auto id = link ? link->window_id : dropped->get_id();
                if (auto state = model.windows.find(id); state != model.windows.end())
                    state->second.paired_placement.reset(); // a real drop establishes a new spot, even in place
                remember_window(dropped);
            }
            if (!was_widget && dropped == main && !widget_shaped.value_or(false) &&
                std::hypot(released_at.x - model.drag.start_cursor.x,
                    released_at.y - model.drag.start_cursor.y) >= CLICK_SLOP)
                start_drag_coast(dropped);
            if (was_widget && !link_of_window(view_by_id(app_window)))
            {
                model.drag.last_drop.became = app_window;  // restored: the window stands for it
            } else if (!was_widget)
            {
                if (auto now = link_of_window(main); now && !now->previewing())
                {
                    auto widget = wf::toplevel_cast(now->widget.lock());
                    model.drag.last_drop.became = widget ? widget->get_id() : 0;  // it's a widget now
                }
            }
        }

        // Let go (fingers lifted to reset on the touchpad, say): until a re-grab could no longer
        // continue the move, the window stays above the widgets, as it was while dragged (L29).
        if (auto stands = main ? wf::toplevel_cast(view_by_id(model.drag.last_drop.became)) : nullptr;
            stands && stands->is_mapped() && !is_widget(stands))
        {
            hold_above(stands);
        }

        model.drag.widget = 0;
        model.drag.started = false;
        retry_widget_arrivals();
        publish_model();
        refresh_layout_avoidance();
    };

    // P12: what covers what can change without any of the signals above, e.g. a dropped window's
    // hold above the widgets ending (L29) or the widget layer coming back. Any change to the scene's
    // stacking or to what is enabled asks window avoidance to look again; it re-solves only if its
    // layout signature changed, and requests are coalesced onto one tick.
    wf::signal::connection_t<wf::scene::root_node_update_signal> on_scene_structure =
        [=] (wf::scene::root_node_update_signal *ev)
    {
        // Without avoidance there are no offsets to keep honest.
        if (!window_keys.active && !window_avoidance_always && !hint_avoidance_always) return;
        if (ev->flags & (wf::scene::update_flag::CHILDREN_LIST | wf::scene::update_flag::ENABLED))
            refresh_layout_avoidance();
    };

    wf::signal::connection_t<wf::view_mapped_signal> on_mapped = [=] (wf::view_mapped_signal *ev)
    {
        if (auto toplevel = wf::toplevel_cast(ev->view))
        {
            observe_view(toplevel);
            if (!model.widgets.empty())
            {
                adopt_widget(toplevel);
            }
        }

        apply(ev->view);
        hint_registration.run_once([=] () { refresh_layout_avoidance(); });
        idle_focus.run_once([=] () { update_focus(); });
    };

    wf::signal::connection_t<wf::view_geometry_changed_signal> on_geometry =
        [=] (wf::view_geometry_changed_signal *ev)
    {
        if (auto view = wf::toplevel_cast(ev->view))
        {
            recenter_keyboard_resize(view);
            observe_view(view);
            if (auto frame = frame_of(view, false))
            {
                if (frame->presentation)
                    frame->presentation->geometry_applied(ev->old_geometry, view->get_geometry());
                frame->damage_previous(ev->old_geometry);
                finish_drag_layout(view);
                frame->damage();
            }

            if (auto link = link_of_widget(view); link && link->make_room_pending && link->docked())
                make_room_for_arrival(view, *link);

            // Widget placement during map is pending until its transaction commits. Save the
            // committed center, not the provisional center reported in view-mapped.
            if (auto link = link_of_widget(view); link && link->docked() &&
                drag->view != view && view->get_id() != model.drag.widget)
                remember_window(view);
            if (auto link = link_of_widget(view); link && rail_place(*link) == rail_place_t::peeking)
                reconcile_rail_slides();  // its strip follows its size
        }

        apply(ev->view);
        publish_model();
        refresh_layout_avoidance();
    };

    wf::signal::connection_t<wf::view_set_output_signal> on_output = [=] (wf::view_set_output_signal *ev)
    {
        apply(ev->view);
        refresh_layout_avoidance();
    };

    // Test sessions: write the next frame an output renders on its own to
    // $SCOTTLAND_TEST_STATE/render-frame.ppm. A screenshot request can force a repaint of
    // the whole output, which would hide a mistake in a partially damaged frame (GO21;
    // after Astra's goo-breath-damage test).
    uint64_t captured_frames = 0;
    std::string capture_output;
    bool capture_next_frame = false;
    wf::signal::connection_t<wf::render_pass_end_signal> on_test_render_end =
        [this] (wf::render_pass_end_signal *ev)
    {
        if (!capture_next_frame)
            return;
        auto target = ev->pass.get_target();
        bool ours = false;
        for (auto o : wf::get_core().output_layout->get_outputs())
            ours |= (capture_output.empty() || capture_output == o->handle->name) &&
                target.geometry == wf::geometry_t(o->get_layout_geometry());
        if (!ours)
            return;
        capture_next_frame = false;
        auto size = target.get_size();
        std::vector<unsigned char> pixels(size_t(size.width) * size.height * 4);
        wf::gles::run_in_context_if_gles([&]
        {
            GLint previous = 0;
            glGetIntegerv(GL_FRAMEBUFFER_BINDING, &previous);
            glBindFramebuffer(GL_FRAMEBUFFER, wf::gles::ensure_render_buffer_fb_id(target));
            glReadPixels(0, 0, size.width, size.height, GL_RGBA, GL_UNSIGNED_BYTE, pixels.data());
            glBindFramebuffer(GL_FRAMEBUFFER, previous);
        });
        std::ofstream image(std::string(getenv("SCOTTLAND_TEST_STATE")) + "/render-frame.ppm", std::ios::binary);
        image << "P6\n" << size.width << " " << size.height << "\n255\n";
        for (size_t i = 0; i < pixels.size(); i += 4)
            image.write((char *)&pixels[i], 3);
        ++captured_frames;
    };

    wf::ipc::method_callback layout_state = [=] (wf::json_t data) -> wf::json_t
    {
        wf::json_t reply = wf::ipc::json_ok();
        wf::json_t views = wf::json_t::array();
        wf::json_t outputs = wf::json_t::array();
        reply["widget_transition_count"] = (int64_t)widget_transitions.size();
        reply["widget_transition_steps"] = (int64_t)widget_transition_steps;
        for (auto output : wf::get_core().output_layout->get_outputs())
        {
            auto identity = screen_identity(output);
            auto zone = zone_for(output);
            wf::json_t entry;
            entry["output"] = output_name(output);
            entry["make"] = std::get<0>(identity);
            entry["model"] = std::get<1>(identity);
            entry["serial"] = std::get<2>(identity);
            entry["identity"] = display_identity(identity);
            entry["own"] = output_has_own_zones(output);
            entry["center_width"] = zone.center;
            entry["rail_width"] = zone.rail;
            entry["blend_width"] = zone.blend;
            entry["min_scale"] = zone.minimum;
            entry["max_scale"] = zone.maximum;
            entry["scale_curve"] = *zone.curve_text;
            outputs.append(entry);
        }
        reply["outputs"] = outputs;
        if (getenv("SCOTTLAND_TEST_MODEL"))
        {
            reply["captured_frames"] = (int64_t)captured_frames;
            if (data.has_member("capture_next_frame") && getenv("SCOTTLAND_TEST_STATE"))
            {
                capture_output = data["capture_next_frame"].is_string() ? data["capture_next_frame"].as_string() : "";
                capture_next_frame = true;
            }
            auto cursor_view = wf::get_core().get_cursor_focus_view();
            reply["cursor_view"] = cursor_view ? (int64_t)cursor_view->get_id() : (int64_t)-1;
        }
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
            entry["widgetized"] = link && !link->previewing();
            entry["preview"] = (link && link->previewing()) || (link_of_widget(view) && link_of_widget(view)->previewing());
            entry["hidden"] = !view->get_root_node()->is_enabled();
            entry["zone"]  = zone_name(placement.zone);
            entry["scale"] = placement.scale;
            auto transformer = view->get_transformed_node()->get_transformer<
                wf::scene::view_2d_transformer_t>(TRANSFORMER);
            entry["applied_scale"] = transformer ? transformer->scale_x : 1.0;
            entry["opacity"] = transformer ? transformer->get_alpha() : 1.0;
            // Where it's going, as the desktop model publishes it, not a step of an animation.
            auto state = model.windows.find(view->get_id());
            entry["target_scale"] = state != model.windows.end() ? state->second.scale : 1.0;
            if (auto frame = frame_of(view, false))
            {
                auto r = frame->screen_rect();
                entry["frame"] = wf::json_t{};
                if (getenv("SCOTTLAND_TEST_MODEL"))
                {
                    auto shown = scene_rectangle(view, view->get_output());
                    entry["scene_frame"]["x"] = shown.x1;
                    entry["scene_frame"]["y"] = shown.y1;
                    entry["scene_frame"]["width"] = shown.width();
                    entry["scene_frame"]["height"] = shown.height();
                }
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
                if (frame->alpha_shape)
                {
                    auto &mask = *frame->alpha_shape;
                    wf::json_t diagnostic;
                    diagnostic["checks"] = (int64_t)mask.checks;
                    diagnostic["builds"] = (int64_t)mask.builds;
                    diagnostic["check_ms"] = mask.check_ms;
                    diagnostic["rebuild_ms"] = mask.rebuild_ms;
                    if (mask.shape)
                    {
                        auto r = frame->screen_rect();
                        auto body = mask.shape->presented_bounds({(r.x1+r.x2)/2,(r.y1+r.y2)/2,r.width()/2,r.height()/2});
                        diagnostic["body_left"] = body.x - body.z;
                    }
                    entry["frame"]["alpha_shape"] = diagnostic;
                }

                if (frame->presentation)
                {
                    auto& p = *frame->presentation;
                    entry["frame"]["presentation"] = wf::json_t{};
                    entry["frame"]["presentation"]["waiting"] = p.waiting;
                    entry["frame"]["presentation"]["fade"] = p.fade;
                    entry["frame"]["presentation"]["steps"] = (int64_t)p.steps;
                    entry["frame"]["presentation"]["fallback"] = p.fallback;
                    entry["frame"]["presentation"]["response_ms"] = (int64_t)p.response_ms;
                }
                entry["attention"] = needs_attention(view->get_id());
                if (frame->morphing())
                {
                    entry["frame"]["morph"] = wf::json_t{};
                    entry["frame"]["morph"]["shape"] = frame->morph.shape;
                    entry["frame"]["morph"]["fade"]  = frame->morph.fade;
                    entry["frame"]["morph"]["snapshot"] = (bool)frame->morph.snapshot;
                }
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
        reply["rail_max_easing_speed_px_s"] = rail_easing_speed_max;
        reply["rail_max_drawn_speed_px_s"] = rail_drawn_speed_max;
        reply["rail_max_drawn_step_px"] = rail_drawn_step_max;
        reply["rail_max_tick_gap_ms"] = int64_t(rail_tick_gap_max);
        reply["rail_drop_layouts_kept"] = int64_t(rail_drop_layouts_kept);
        return reply;
    };

    wf::ipc::method_callback test_screen_identities = [=] (wf::json_t data) -> wf::json_t
    {
        if (!getenv("SCOTTLAND_TEST_MODEL") || std::string(getenv("SCOTTLAND_TEST_MODEL")) != "1")
            return wf::ipc::json_error("test sessions only");
        if (!data.has_member("outputs") || !data["outputs"].is_array())
            return wf::ipc::json_error("outputs must be an array");
        std::map<std::string, screen_identity_t> identities;
        for (size_t i = 0; i < data["outputs"].size(); ++i)
        {
            auto entry = data["outputs"][i];
            if (!entry.is_object() || !entry.has_member("output") || !entry["output"].is_string() ||
                !entry.has_member("make") || !entry["make"].is_string() ||
                !entry.has_member("model") || !entry["model"].is_string() ||
                !entry.has_member("serial") || !entry["serial"].is_string())
                return wf::ipc::json_error("each output needs string output, make, model and serial fields");
            screen_identity_t identity{entry["make"].as_string(), entry["model"].as_string(), entry["serial"].as_string()};
            if (std::get<0>(identity).empty() && std::get<1>(identity).empty() && std::get<2>(identity).empty())
                return wf::ipc::json_error("test identity must contain at least one non-empty part");
            std::string name = entry["output"].as_string();
            auto outputs = wf::get_core().output_layout->get_outputs();
            auto found = std::find_if(outputs.begin(), outputs.end(), [=] (wf::output_t *output)
                { return output_name(output) == name; });
            if (found == outputs.end())
                return wf::ipc::json_error("test output is not connected");
            auto reported = reported_screen_identity(*found);
            if (!std::get<0>(reported).empty() || !std::get<1>(reported).empty() || !std::get<2>(reported).empty())
                return wf::ipc::json_error("test identities may only be set on outputs without a reported identity");
            identities[name] = std::move(identity);
        }
        test_identity_by_output = std::move(identities);
        apply_all();
        publish_model();
        return wf::ipc::json_ok();
    };

    wf::shared_data::ref_ptr_t<wf::ipc::method_repository_t> ipc_repo;

    wf::ipc::method_callback input_devices = [] (wf::json_t) -> wf::json_t
    {
        wf::json_t devices = wf::json_t::array();
        for (auto& device : wf::get_core().get_input_devices())
        {
            if (!device) continue;
            auto handle = device->get_wlr_handle();
            const auto kind = input_device_kind(handle);
            if (kind.empty()) continue;
            wf::json_t entry;
            entry["kind"] = kind;
            entry["name"] = (handle && handle->name) ? handle->name : "";
            devices.append(std::move(entry));
        }
        wf::json_t reply;
        reply["devices"] = std::move(devices);
        return reply;
    };

    wf::ipc::method_callback input_device = [] (wf::json_t data) -> wf::json_t
    {
        if (!data.is_object() || !data.has_member("kind") || !data["kind"].is_string() ||
            !data.has_member("name") || !data["name"].is_string() ||
            !data.has_member("enabled") || !data["enabled"].is_bool() ||
            (data.has_member("missing_ok") && !data["missing_ok"].is_bool()))
        {
            return wf::ipc::json_error("input-device needs string kind/name and boolean enabled/missing_ok fields");
        }

        const std::string kind = data["kind"].as_string();
        const std::string name = data["name"].as_string();
        const bool enabled = data["enabled"].as_bool();
        const bool missing_ok = data.has_member("missing_ok") && data["missing_ok"].as_bool();
        if ((kind != "touchpad" && kind != "touchscreen") || !valid_input_device_name(name))
        {
            return wf::ipc::json_error("invalid input device kind or name");
        }

        for (auto& device : wf::get_core().get_input_devices())
        {
            if (!device) continue;
            auto handle = device->get_wlr_handle();
            if (!handle || !handle->name || std::string(handle->name) != name ||
                !input_device_matches_kind(kind, handle))
            {
                continue;
            }

            if (!set_input_device_enabled(*device, enabled))
            {
                return wf::ipc::json_error(std::string("compositor refused to ") +
                    (enabled ? "enable " : "disable ") + kind + " device " + name);
            }
            wf::json_t reply;
            reply["present"] = true;
            return reply;
        }

        if (missing_ok)
        {
            wf::json_t reply;
            reply["present"] = false;
            return reply;
        }
        return wf::ipc::json_error("input device is no longer present");
    };

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
    // Per-app speeds: [scottland] touchpad_scroll_apps_<name> = <regex matching the whole app-id>,
    // touchpad_scroll_initial_apps_<name> = <regex matching the whole app-id the window had when it
    // mapped> (default .*: any), touchpad_scroll_factor_<name> = <speed>. The window under the
    // pointer takes the first entry (by name) whose regexes both match; that speed replaces
    // touchpad_scroll_speed for it. Anything else (no match, a panel or other layer surface)
    // scrolls at touchpad_scroll_speed.
    wf::option_wrapper_t<wf::config::compound_list_t<std::string, std::string, double>> touchpad_scroll_apps{
        "scottland/touchpad_scroll_speeds"};
    std::map<std::string, std::optional<std::regex>> touchpad_scroll_regexes;  // pattern -> compiled
    // The app-id each window had when it mapped; apps may change theirs later. A window already
    // mapped when the plugin loaded counts the app-id it had then.
    std::map<uint32_t, std::string> initial_app_ids;

    wf::signal::connection_t<wf::view_mapped_signal> on_initial_app_id = [=] (wf::view_mapped_signal *ev)
    {
        initial_app_ids[ev->view->get_id()] = ev->view->get_app_id();
    };
    wf::signal::connection_t<wf::view_unmapped_signal> on_forget_app_id = [=] (wf::view_unmapped_signal *ev)
    {
        initial_app_ids.erase(ev->view->get_id());
    };

    bool touchpad_scroll_matches(const std::string& name, const std::string& pattern, const std::string& app_id)
    {
        auto cached = touchpad_scroll_regexes.find(pattern);
        if (cached == touchpad_scroll_regexes.end())
        {
            std::optional<std::regex> compiled;  // stays empty for a bad pattern, logged once
            try
            {
                compiled = std::regex(pattern);
            } catch (const std::regex_error&)
            {
                LOGE("scottland: bad touchpad_scroll regex for ", name, ": ", pattern);
            }

            cached = touchpad_scroll_regexes.emplace(pattern, std::move(compiled)).first;
        }

        return cached->second && std::regex_match(app_id, *cached->second);
    }

    /** The touchpad scroll speed for the window under the pointer, and whether an app entry set it. */
    std::pair<double, bool> touchpad_scroll_factor()
    {
        double global = std::max(0.0, (double)touchpad_scroll_speed);
        auto view = wf::toplevel_cast(wf::get_core().get_cursor_focus_view());
        if (!view)
        {
            return {global, false};
        }

        std::string app_id = view->get_app_id();
        auto initial = initial_app_ids.find(view->get_id());
        const std::string& initial_app_id = (initial != initial_app_ids.end()) ? initial->second : app_id;
        for (const auto& [name, pattern, initial_pattern, factor] : touchpad_scroll_apps.value())
        {
            if (touchpad_scroll_matches(name, pattern, app_id) &&
                touchpad_scroll_matches(name, initial_pattern, initial_app_id))
            {
                return {std::max(0.0, factor), true};
            }
        }

        return {global, false};
    }

    wf::signal::connection_t<wf::input_event_signal<wlr_pointer_axis_event>> on_axis =
        [=] (wf::input_event_signal<wlr_pointer_axis_event> *ev)
    {
        if ((ev->event->source != WL_POINTER_AXIS_SOURCE_FINGER) || !ev->device ||
            (ev->device->type != WLR_INPUT_DEVICE_POINTER) ||
            (touch_pointer && (ev->device == &touch_pointer->pointer.base)))
        {
            return;
        }

        auto [speed, per_app] = touchpad_scroll_factor();
#if WAYFIRE_API_ABI_VERSION_MACRO > 2026'07'26
        // Newer Wayfire applies touchpad_scroll_speed itself: only an app's own speed is ours.
        double global = std::max(0.0, (double)touchpad_scroll_speed);
        if (!per_app || (global <= 0))
        {
            return;
        }

        speed /= global;
#endif
        ev->event->delta *= speed;
        ev->event->delta_discrete = std::lround(ev->event->delta_discrete * speed);
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
        if (ev->mode == wf::input_event_processing_mode_t::IGNORE) return;
        if (key_layers.handles(ev))
        {
            return;
        }

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

  public:
    void init() override
    {
        // Wayfire's PRINT_TRACE SIGABRT handler prints then _Exit(-1), losing the
        // core (and can itself block in a hung process). Use the kernel's core
        // disposition for operator diagnostics, independent of the event loop.
        // SIGQUIT is the preferred capture signal, including before plugin load.
        signal(SIGABRT, SIG_DFL);
        signal(SIGQUIT, SIG_DFL);
        // Compile the shared blend shader during plugin startup, before any input-driven morph.
        wf::gles::run_in_context_if_gles([] { scottland::widget_morph_renderer().prepare(); });
        key_layers.init();  // before raw-key consumers: claims override release bindings/remaps
        session_state.init();
        if (getenv("SCOTTLAND_TEST_MODEL"))
            wf::get_core().connect(&on_test_render_end);
        init_output_tracking();
        init_hint_palette_watch();
        init_widget_spawn();
        if (!getenv("SCOTTLAND_INTERNAL_MODEL_SESSION"))
        {
            auto session = std::to_string(getpid()) + "-" + std::to_string(now_msec());
            setenv("SCOTTLAND_INTERNAL_MODEL_SESSION", session.c_str(), 1);
        }
        model.session = getenv("SCOTTLAND_INTERNAL_MODEL_SESSION");
        if (auto version = getenv("SCOTTLAND_INTERNAL_MODEL_VERSION"))
        {
            model.version = std::strtoull(version, nullptr, 10);
        }
        ipc_repo->register_method("scottland/send-key", send_key);
        ipc_repo->register_method("scottland/layout-state", layout_state);
        ipc_repo->register_method("scottland/input-devices", input_devices);
        ipc_repo->register_method("scottland/input-device", input_device);
        wf::get_core().connect(&on_axis);
        wf::get_core().connect(&on_initial_app_id);
        wf::get_core().connect(&on_forget_app_id);
        for (auto& view : wf::get_core().get_all_views())
        {
            if (view->is_mapped())
            {
                initial_app_ids[view->get_id()] = view->get_app_id();
            }
        }

        wf::get_core().connect(&on_mapped);
        wf::get_core().scene()->connect(&on_scene_structure);
        wf::get_core().connect(&on_geometry);
        wf::get_core().connect(&on_output);
        wf::get_core().connect(&on_focus);
        wf::get_core().connect(&on_unmapped);
        wf::get_core().connect(&on_motion);
        wf::get_core().connect(&on_swipe_begin);
        wf::get_core().connect(&on_swipe_update);
        wf::get_core().connect(&on_swipe_end);
        wf::get_core().connect(&on_hold_begin);
        wf::get_core().connect(&on_hold_end);
        wf::get_core().connect(&on_touchpad_button);
        wf::get_core().connect(&on_touch_down_capture);
        wf::get_core().connect(&on_touch_motion_capture);
        wf::get_core().connect(&on_touch_up_capture);
        wf::get_core().connect(&on_touch_down);
        wf::get_core().connect(&on_touch_motion);
        wf::get_core().connect(&on_touch_up);
        synthesize_pop();
        ipc_repo->register_method("scottland/test-input", test_input);
        ipc_repo->register_method("scottland/test-touchpad", test_touchpad_input);
        ipc_repo->register_method("scottland/widgets", widgets_state);
        ipc_repo->register_method("scottland/desktop-model", desktop_state);
        ipc_repo->register_method("scottland/subscribe", subscribe_model);
        if (getenv("SCOTTLAND_TEST_MODEL") && std::string(getenv("SCOTTLAND_TEST_MODEL")) == "1")
        {
            ipc_repo->register_method("scottland/audit-model", audit_model);
            ipc_repo->register_method("scottland/test-screen-identities", test_screen_identities);
        }
        wf::get_core().connect(&on_title);
        wf::get_core().connect(&on_app_id);
        wf::get_core().connect(&on_hints);
        ipc_repo->register_method("scottland/widget-action", widget_action);
        ipc_repo->register_method("scottland/widget-mode", widget_mode_ipc);
        ipc_repo->register_method("scottland/present", present_method);
        ipc_repo->register_method("scottland/widget-traits", widget_traits);
        ipc_repo->register_method("scottland/attention", attention_method);
        wf::get_core().tx_manager->connect(&on_new_transaction);
        for (auto& view : wf::get_core().get_all_views())
        {
            observe_view(wf::toplevel_cast(view));
        }
        take_handover();
        init_window_keys();
        init_spread();
        wf::get_core().connect(&on_minimize_edge);
        wf::get_core().connect(&on_minimize_device_removed);
        wf::get_core().connect(&on_cancel_key);
        shortcuts.init([this] (auto *ev) { return key_layers.handles(ev); });
        wf::get_core().connect(&on_remap_key);
        // Wayfire's promotion manager disables each output's TOP node while fullscreen is
        // promoted. Read that compositor fact for every screen, regardless of keyboard focus.
        for (auto output : wf::get_core().output_layout->get_outputs())
        {
            set_focus_mode(output, !output->node_for_layer(wf::scene::layer::TOP)->is_enabled());
        }
        load_curve();
        load_screen_zones();
        widgetize_windows_on_rails();
        wf::get_core().bindings->add_key(minimize_key, &on_minimize_key);
        wf::get_core().bindings->add_key(navigate_left, &on_navigate_left);
        wf::get_core().bindings->add_key(navigate_right, &on_navigate_right);
        wf::get_core().bindings->add_key(navigate_up, &on_navigate_up);
        wf::get_core().bindings->add_key(navigate_down, &on_navigate_down);
        wf::get_core().connect(&on_focus_request);
        start_activation();
        installing_model = false;
        announce_widgets();  // a widget service that outlived a reload catches up
        wf::get_core().connect(&on_motion_abs);
        wf::get_core().connect(&on_button);
        wf::get_core().bindings->add_button(move_button, &on_move);
        wf::get_core().bindings->add_button(move_shift_button, &on_move_shift);
        drag->connect(&on_drag_output);
        drag->connect(&on_drag_motion);
        drag->connect(&on_drag_done);
        center_width.set_callback([=] { apply_all(); });
        rail_width.set_callback([=] { apply_all(); });
        min_scale.set_callback([=] { apply_all(); });
        max_scale.set_callback([=] { apply_all(); });
        scale_curve_text.set_callback([=] { load_curve(); apply_all(); });
        blend_width.set_callback([=] { apply_all(); });
        screen_zones_text.set_callback([=] { load_screen_zones(); apply_all(); });
        center_opacity_focused.set_callback([=] { apply_all_opacity(); });
        center_opacity_unfocused.set_callback([=] { apply_all_opacity(); });
        side_opacity_focused.set_callback([=] { apply_all_opacity(); });
        side_opacity_unfocused.set_callback([=] { apply_all_opacity(); });
        widget_opacity_focused.set_callback([=] { apply_all_opacity(); });
        widget_opacity_unfocused.set_callback([=] { apply_all_opacity(); });
        window_mode_opacity_focused.set_callback([=] { apply_all_opacity(); });
        window_mode_opacity_unfocused.set_callback([=] { apply_all_opacity(); });
        unfocused_edge_tone_light.set_callback([=] { load_color_scheme(); });
        unfocused_edge_tone_dark.set_callback([=] { load_color_scheme(); });
        unfocused_edge_strength.set_callback([=] { load_color_scheme(); });
        goo_dye_density.set_callback([=] { load_color_scheme(); });
        window_mode_tint.set_callback([=] { load_color_scheme(); refresh_layout_avoidance(); });
        hint_background_opacity.set_callback([=] { refresh_layout_avoidance(); });
        auto avoidance_setting_changed = [=] {
            declutter_signature.clear();
            refresh_layout_avoidance();
        };
        window_avoidance_always.set_callback(avoidance_setting_changed);
        hint_avoidance_always.set_callback(avoidance_setting_changed);
        color_scheme.set_callback([=] { load_color_scheme(); });
        accent_color.set_callback([=] { load_color_scheme(); });
        attention_color.set_callback([=] { load_color_scheme(); });
        attention_color_family.set_callback([=] { load_color_scheme(); });
        load_color_scheme();
        apply_all();
        update_focus();
        for (auto& [id, state] : model.windows)
        {
            if (!link_of_widget(state.view.lock()))
            {
                show_attention(id);
            }
        }
        goo.start([this](wf::output_t *output) { return goo_sources(output); },
            [this](wf::output_t *output, bool on)
            {
                if (on) model.goo_outputs.insert(output);
                else model.goo_outputs.erase(output);
                publish_model();
            },
            [this](wf::output_t *) { apply_all(); });
        refresh_layout_avoidance();
        wf::get_core().connect(&on_input_device_added);
        wf::get_core().connect(&on_input_config_reloaded);
        LOGI("scottland: plugin loaded");
    }

    void fini() override
    {
        fini_spread();  // first: the drop that releasing the drag causes must not accept an offer
        drag->handle_input_released();
        wf::get_core().bindings->rem_binding(&on_move);
        wf::get_core().bindings->rem_binding(&on_move_shift);
        bool reloading = access(runtime_file(".reloading").c_str(), F_OK) == 0;
        installing_model = reloading;  // teardown is also part of the atomic handover
        widget_peek_tick.disconnect();
        goo.stop();
        fini_window_keys();
        key_layers.fini();
        session_state.fini();

        fini_output_tracking();
        ipc_repo->unregister_method("scottland/send-key");
        ipc_repo->unregister_method("scottland/layout-state");
        ipc_repo->unregister_method("scottland/input-devices");
        ipc_repo->unregister_method("scottland/input-device");
        on_minimize_edge.disconnect();
        on_minimize_device_removed.disconnect();
        on_input_device_added.disconnect();
        on_input_config_reloaded.disconnect();
        shortcuts.fini();
        on_axis.disconnect();
        on_initial_app_id.disconnect();
        on_forget_app_id.disconnect();
        on_remap_key.disconnect();
        on_mapped.disconnect();
        on_geometry.disconnect();
        on_output.disconnect();
        on_focus.disconnect();
        on_unmapped.disconnect();
        idle_focus.disconnect();
        on_motion.disconnect();
        swipe_end();
        on_swipe_begin.disconnect();
        on_cancel_key.disconnect();
        glide_tick.disconnect();
        rail_dwell_tick.disconnect();
        rail_layout_tick.disconnect();
        for (auto& [id, motion] : rail_layout_motions)
            set_drag_layout_offset(model_view(id), 0, 0);
        rail_layout_motions.clear();
        rail_drawn_samples.clear();
        glides.clear();
        minimize_hold.disconnect();
        rail_slide_tick.disconnect();
        rail_slides.clear();
        widget_transition_tick.disconnect();
        for (auto& [id, transition] : widget_transitions)
            if (auto view = wf::toplevel_cast(transition->view.lock()))
                if (auto frame = frame_of(view, false)) frame->presentation.reset();
        widget_transitions.clear();
        on_swipe_update.disconnect();
        on_swipe_end.disconnect();
        on_hold_begin.disconnect();
        on_hold_end.disconnect();
        cancel_touchpad_hold();
        test_touchpad.reset();
        ipc_repo->unregister_method("scottland/test-touchpad");
        on_touchpad_button.disconnect();
        on_touch_down.disconnect();
        on_touch_down_capture.disconnect();
        on_touch_motion_capture.disconnect();
        on_touch_up_capture.disconnect();
        on_touch_motion.disconnect();
        on_touch_up.disconnect();
        cancel_hold();
        momentum.disconnect();
        touch_pointer.reset();
        ipc_repo->unregister_method("scottland/test-input");
        ipc_repo->unregister_method("scottland/widgets");
        ipc_repo->unregister_method("scottland/desktop-model");
        ipc_repo->unregister_method("scottland/subscribe");
        ipc_repo->unregister_method("scottland/audit-model");
        on_title.disconnect();
        on_app_id.disconnect();
        on_above.disconnect();
        on_hints.disconnect();
        ipc_repo->unregister_method("scottland/widget-action");
        ipc_repo->unregister_method("scottland/widget-mode");
        ipc_repo->unregister_method("scottland/present");
        ipc_repo->unregister_method("scottland/widget-traits");
        ipc_repo->unregister_method("scottland/attention");
        wf::get_core().bindings->rem_binding(&on_minimize_key);
        wf::get_core().bindings->rem_binding(&on_navigate_left);
        wf::get_core().bindings->rem_binding(&on_navigate_right);
        wf::get_core().bindings->rem_binding(&on_navigate_up);
        wf::get_core().bindings->rem_binding(&on_navigate_down);
        on_focus_request.disconnect();
        on_new_transaction.disconnect();
        on_activate.disconnect();
        // Unloading gives every app its window back. A reload (scottland-reload marks it) hands the
        // widgets over instead: they stay, their windows stay hidden, and the new plugin takes
        // the links from a file (WG5).
        end_morph();
        widget_watchdog.disconnect();
        release_above();  // a just-dropped window doesn't stay above for good
        wf::json_t handover = wf::json_t::array();
        for (auto& [id, link] : model.widgets)
        {
            auto widget = wf::toplevel_cast(link.widget.lock());
            if (reloading && link.docked() && widget && widget->is_mapped())
            {
                wf::json_t entry;
                entry["window"] = (int64_t)id;
                entry["widget"] = (int64_t)widget->get_id();
                entry["unit"]   = link.launcher ? link.launcher->unit : "";
                entry["pid"]    = (int64_t)(link.launcher ? link.launcher->pid : 0);
                entry["pidfd"]  = (int64_t)(link.launcher && link.launcher->pidfd >= 0 ? fcntl(link.launcher->pidfd, F_DUPFD_CLOEXEC, 0) : -1);
                entry["rail"]   = link.rail;
                entry["x"] = link.drop.x;
                entry["y"] = link.drop.y;
                entry["minimized"] = link.collapsed;
                entry["touch_drag"] = link.touch_drag;
                entry["desktop"] = link.desktop;
                entry["name"] = link.name;
                entry["icon"] = link.icon;
                entry["card"] = link.card;
                handover.append(entry);
                transition_widget(link, widget_link_t::lifecycle_t::handed_over);
                continue;  // the window keeps its disable: the next plugin holds it
            }

            transition_widget(link, widget_link_t::lifecycle_t::restoring);
            close_view_or_process(widget, link.launcher);
        }

        if (reloading)
        {
            auto snapshot = model_snapshot("desktop");
            snapshot["version"] = (int64_t)model.version;
            snapshot["links"] = handover;
            auto path = runtime_file(".widget-handover.json");
            std::ofstream out(path + ".tmp");
            out << snapshot.serialize();
            out.close();
            std::rename((path + ".tmp").c_str(), path.c_str());
        }

        // Returning a disable is part of unloading the renderer. Only handed-over apps keep
        // their lease for the incoming plugin; a disabled widget must never outlive this one.
        auto leases = disabled_nodes;
        for (auto id : leases)
        {
            auto link = model.widgets.find(id);
            if (link == model.widgets.end() || link->second.lifecycle != widget_link_t::lifecycle_t::handed_over)
            {
                render_hidden(view_by_id(id), false);
            }
        }
        model.widgets.clear();
        if (handover.size() == 0)
        {
            announce_widgets();  // the widget service drops its objects
        }
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
        scottland::windowing::release_hint_gl();
        fini_widget_spawn();
        fini_hint_palette_watch();
        release_stale_pointer_focus();  // e.g. the live drag's grab: its owner and code go next
        // The next plugin copy continues the version. Set once here, not per publish: glibc keeps
        // every value ever given to setenv, so per-publish calls grew the heap without bound.
        setenv("SCOTTLAND_INTERNAL_MODEL_VERSION", std::to_string(model.version).c_str(), 1);
        scottland::release_aux_color_transform();  // last: nothing renders through it after this
        LOGI("scottland: plugin unloaded");
    }
};

DECLARE_WAYFIRE_PLUGIN(scottland_plugin_t);
