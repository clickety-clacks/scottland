#include "key-layers.hpp"
#include <wayfire/core.hpp>
#include <wayfire/seat.hpp>
#include <wayfire/bindings-repository.hpp>
#include <wayfire/plugins/common/shared-core-data.hpp>
#include <wayfire/plugins/ipc/ipc-method-repository.hpp>
#include <wayfire/input-device.hpp>
extern "C" {
#include <wlr/types/wlr_keyboard.h>
#include <wlr/types/wlr_seat.h>
#include <wlr/types/wlr_compositor.h>
#define namespace namespace_t
#include <wlr/types/wlr_layer_shell_v1.h>
#undef namespace
}
#include <xkbcommon/xkbcommon.h>
#include <algorithm>
#include <map>
#include <set>
#include <string>
#include <vector>

namespace scottland
{
namespace
{
constexpr uint32_t shortcut_mods = WLR_MODIFIER_SHIFT | WLR_MODIFIER_CTRL |
    WLR_MODIFIER_ALT | WLR_MODIFIER_LOGO;

struct chord_t
{
    uint32_t mods;
    xkb_keysym_t sym;
};

bool matches(const chord_t& chord, wlr_keyboard *keyboard, uint32_t keycode)
{
    auto state = keyboard->xkb_state;
    auto map = keyboard->keymap;
    if (!state || !map)
    {
        return false;
    }

    uint32_t mods = wlr_keyboard_get_modifiers(keyboard) & shortcut_mods;
    auto code = keycode + 8;
    auto layout = xkb_state_key_get_layout(state, code);
    const xkb_keysym_t *syms;
    // Unshifted names with an explicit modifier mask: 5:j is Ctrl+Shift+J. Locks don't
    // change shortcuts. Use the current layout, not the first layout in the keymap.
    int count = xkb_keymap_key_get_syms_by_level(map, code, layout, 0, &syms);
    for (int i = 0; i < count; ++i)
    {
        if ((mods == chord.mods) && (syms[i] == chord.sym))
        {
            return true;
        }
    }

    // Also accept a produced symbol: Ctrl+Shift+= may be written 4:plus. Only remove
    // Shift if XKB consumed it to produce that symbol (never remove Ctrl/Alt/Super).
    uint32_t symbol_mods = mods;
    auto shift = xkb_keymap_mod_get_index(map, XKB_MOD_NAME_SHIFT);
    if ((shift != XKB_MOD_INVALID) && xkb_state_mod_index_is_consumed2(
            state, code, shift, XKB_CONSUMED_MODE_XKB))
    {
        symbol_mods &= ~WLR_MODIFIER_SHIFT;
    }

    count = xkb_state_key_get_syms(state, code, &syms);
    for (int i = 0; i < count; ++i)
    {
        if (((mods == chord.mods) || (symbol_mods == chord.mods)) && (syms[i] == chord.sym))
        {
            return true;
        }
    }

    return false;
}

pid_t client_pid(wayfire_view view)
{
    pid_t pid = 0;
    if (auto client = view->get_client())
    {
        wl_client_get_credentials(client, &pid, nullptr, nullptr);
    }

    return pid;
}

wlr_layer_surface_v1 *layer_surface(wayfire_view view)
{
    auto surface = view->get_wlr_surface();
    return surface ? wlr_layer_surface_v1_try_from_wlr_surface(surface) : nullptr;
}
}

struct key_layers_t::impl
{
    struct surface_layer_t
    {
        std::weak_ptr<wf::view_interface_t> view;
        std::vector<chord_t> keys;
    };
    std::map<uint32_t, surface_layer_t> layers;
    // Once a press was claimed, its release completes that event pair even if the app
    // changes its claims, unmaps, or loses focus while the key is held.
    std::set<std::pair<wlr_input_device*, uint32_t>> held;
    struct event_t
    {
        wlr_keyboard_key_event *key;
        bool claimed;
        bool suspended;
    };
    std::vector<event_t> events;
    wf::shared_data::ref_ptr_t<wf::ipc::method_repository_t> ipc;
    // Wayfire has no public enabled-state query. A private binding with an impossible
    // modifier bit AND keycode probes it without matching any configurable/hardware key.
    // In 0.11, disabling an already inhibited repository crosses zero and enables its key
    // callbacks again. Probe first, including for nested input, and balance only our own hold.
    wf::option_sptr_t<wf::keybinding_t> probe = wf::create_option(
        wf::keybinding_t{0x80000000u, 0xffffffffu});
    wf::key_callback probe_callback = [] (const wf::keybinding_t&) { return true; };

    bool claim(wf::input_event_signal<wlr_keyboard_key_event> *ev)
    {
        auto token = std::make_pair(ev->device, ev->event->keycode);
        if (held.count(token))
        {
            if (ev->event->state == WL_KEYBOARD_KEY_STATE_RELEASED)
            {
                held.erase(token);
            }

            return true;
        }

        // A layer never takes input away from a compositor grab (drag, lock screen,
        // hints, etc.). wlroots' focused_surface, unlike active_view, includes layer-shell.
        if ((ev->mode != wf::input_event_processing_mode_t::FULL) ||
            (ev->event->state != WL_KEYBOARD_KEY_STATE_PRESSED))
        {
            return false;
        }

        auto seat = wf::get_core().get_current_seat();
        auto focus = seat->keyboard_state.focused_surface;
        auto keyboard = ev->device ? wlr_keyboard_from_input_device(ev->device) :
            wlr_seat_get_keyboard(seat);
        if (!focus || !keyboard || wlr_seat_keyboard_has_grab(seat))
        {
            return false;
        }

        for (const auto& [id, layer] : layers)
        {
            auto view = layer.view.lock();
            if (!view || !view->is_mapped() || (view->get_keyboard_focus_surface() != focus))
            {
                continue;
            }

            for (const auto& chord : layer.keys)
            {
                if (matches(chord, keyboard, ev->event->keycode))
                {
                    held.insert(token);
                    return true;
                }
            }
        }

        return false;
    }

    wf::signal::connection_t<wf::input_event_signal<wlr_keyboard_key_event>> before =
        [this] (auto *ev)
    {
        bool claimed = claim(ev);
        bool suspended = claimed && wf::get_core().bindings->handle_key(probe->get_value(), 0);
        events.push_back({ev->event, claimed, suspended});
        if (suspended)
        {
            // Disable only until this event's post signal. Core still updates its modifier-
            // binding state and pressed keys, and delivers ordinary press/release events.
            wf::get_core().bindings->set_enabled(false);
        }
    };
    wf::signal::connection_t<wf::post_input_event_signal<wlr_keyboard_key_event>> after =
        [this] (auto *ev)
    {
        auto it = std::find_if(events.rbegin(), events.rend(), [ev] (auto& pending)
        {
            return pending.key == ev->event;
        });
        if (it != events.rend())
        {
            if (it->suspended)
            {
                wf::get_core().bindings->set_enabled(true);
            }

            events.erase(std::next(it).base());
        }
    };
    wf::signal::connection_t<wf::view_unmapped_signal> unmapped = [this] (auto *ev)
    {
        layers.erase(ev->view->get_id());
    };
    wf::signal::connection_t<wf::input_device_removed_signal> removed = [this] (auto *ev)
    {
        auto device = ev->device->get_wlr_handle();
        for (auto it = held.begin(); it != held.end();)
        {
            if (it->first == device)
            {
                it = held.erase(it);
            } else
            {
                ++it;
            }
        }
    };

    /** IPC scottland/key-layer:
     *  {action:"list"} -> {surfaces:[{window,pid,title,app_id,namespace?,active,keys}]};
     *  {action:"set",window:ID,keys:["MODMASK:keysym",...]} replaces a mapped surface's layer;
     *  {action:"clear",window:ID} removes it. Instead of window, {pid,namespace} resolves one
     *  mapped layer-shell surface; ambiguous matches are rejected. MODMASK is 1 Shift, 4 Ctrl,
     *  8 Alt, 64 Super, combined by addition. See docs/key-layers.md for symbol matching/lifetime.
     *  Lifetime belongs to the Wayland surface, not to the short-lived IPC connection. */
    wf::ipc::method_callback method = [this] (wf::json_t data) -> wf::json_t
    {
        if (!data.has_member("action") || !data["action"].is_string())
        {
            return wf::ipc::json_error("key-layer needs action: set, clear or list");
        }

        auto action = data["action"].as_string();
        if (action == "list")
        {
            auto reply = wf::ipc::json_ok();
            reply["surfaces"] = wf::json_t::array();
            for (auto view : wf::get_core().get_all_views())
            {
                if (!view->is_mapped() || !view->get_wlr_surface())
                {
                    continue;
                }

                wf::json_t row;
                row["window"] = view->get_id();
                row["pid"] = client_pid(view);
                row["title"] = view->get_title();
                row["app_id"] = view->get_app_id();
                if (auto layer = layer_surface(view))
                {
                    row["namespace"] = layer->namespace_t ? layer->namespace_t : "";
                }

                row["keys"] = wf::json_t::array();
                auto it = layers.find(view->get_id());
                row["registered"] = it != layers.end();
                row["active"] = (it != layers.end()) && view->get_keyboard_focus_surface() &&
                    (view->get_keyboard_focus_surface() ==
                        wf::get_core().get_current_seat()->keyboard_state.focused_surface);
                if (it != layers.end())
                {
                    for (const auto& chord : it->second.keys)
                    {
                        char name[128];
                        xkb_keysym_get_name(chord.sym, name, sizeof(name));
                        row["keys"].append(std::to_string(chord.mods) + ":" + name);
                    }
                }

                reply["surfaces"].append(row);
            }

            return reply;
        }

        if ((action != "set") && (action != "clear"))
        {
            return wf::ipc::json_error("unknown key-layer action");
        }

        bool by_window = data.has_member("window");
        bool by_layer = data.has_member("pid") || data.has_member("namespace");
        if ((by_window == by_layer) || (by_window && (!data["window"].is_int() ||
                (data["window"].as_int() <= 0))) || (by_layer &&
                (!data.has_member("pid") || !data["pid"].is_int() || (data["pid"].as_int() <= 0) ||
                    !data.has_member("namespace") || !data["namespace"].is_string())))
        {
            return wf::ipc::json_error("choose integer window, or positive pid and string namespace");
        }

        wayfire_view target;
        for (auto view : wf::get_core().get_all_views())
        {
            if (!view->is_mapped() || !view->get_wlr_surface())
            {
                continue;
            }

            bool match = by_window && (view->get_id() == (uint32_t)data["window"].as_int());
            if (by_layer && (client_pid(view) == data["pid"].as_int()))
            {
                auto layer = layer_surface(view);
                match = layer && layer->namespace_t && (data["namespace"].as_string() == layer->namespace_t);
            }

            if (match)
            {
                if (target)
                {
                    return wf::ipc::json_error("ambiguous layer surface; select a window id from list");
                }

                target = view;
            }
        }

        if (!target)
        {
            return wf::ipc::json_error("no such mapped surface");
        }

        std::vector<chord_t> keys;
        if (action == "set")
        {
            if (!data.has_member("keys") || !data["keys"].is_array())
            {
                return wf::ipc::json_error("set needs keys: an array of MODMASK:keysym strings");
            }

            for (size_t i = 0; i < data["keys"].size(); ++i)
            {
                if (!data["keys"][i].is_string())
                {
                    return wf::ipc::json_error("each key must be a MODMASK:keysym string");
                }

                auto text = data["keys"][i].as_string();
                auto colon = text.find(':');
                if ((colon == std::string::npos) || (colon == 0) || (colon > 2) ||
                    !std::all_of(text.begin(), text.begin() + colon, [] (char c) { return c >= '0' && c <= '9'; }))
                {
                    return wf::ipc::json_error("bad chord: " + text);
                }

                auto mods = (uint32_t)std::stoul(text.substr(0, colon));
                auto sym = xkb_keysym_from_name(text.substr(colon + 1).c_str(), XKB_KEYSYM_NO_FLAGS);
                if ((mods & ~shortcut_mods) || (sym == XKB_KEY_NoSymbol))
                {
                    return wf::ipc::json_error("bad modifier mask or keysym: " + text);
                }

                keys.push_back({mods, sym});
            }
        }

        if (keys.empty())
        {
            layers.erase(target->get_id());
        } else
        {
            layers[target->get_id()] = {target->weak_from_this(), std::move(keys)};
        }

        auto reply = wf::ipc::json_ok();
        reply["window"] = target->get_id();
        return reply;
    };
};

key_layers_t::key_layers_t() = default;
key_layers_t::~key_layers_t()
{
    fini();
}

void key_layers_t::init()
{
    priv = std::make_unique<impl>();
    wf::get_core().bindings->add_key(priv->probe, &priv->probe_callback);
    wf::get_core().connect(&priv->before);
    wf::get_core().connect(&priv->after);
    wf::get_core().connect(&priv->unmapped);
    wf::get_core().connect(&priv->removed);
    priv->ipc->register_method("scottland/key-layer", priv->method);
}

void key_layers_t::fini()
{
    if (priv)
    {
        priv->ipc->unregister_method("scottland/key-layer");
        wf::get_core().bindings->rem_binding(&priv->probe_callback);
        for (auto& event : priv->events)
        {
            if (event.suspended)
            {
                wf::get_core().bindings->set_enabled(true);
            }
        }

        priv.reset();
    }
}

bool key_layers_t::handles(const wf::input_event_signal<wlr_keyboard_key_event> *ev) const
{
    return priv && !priv->events.empty() && (priv->events.back().key == ev->event) &&
        priv->events.back().claimed;
}
}
