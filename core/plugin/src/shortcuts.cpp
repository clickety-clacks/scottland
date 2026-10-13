#include "shortcuts.hpp"
#include "session.hpp"
#include <wayfire/core.hpp>
#include <wayfire/output.hpp>
#include <wayfire/plugin.hpp>
#include <wayfire/seat.hpp>
#include <wayfire/bindings.hpp>
#include <wayfire/bindings-repository.hpp>
#include <wayfire/option-wrapper.hpp>
#include <wayfire/config/compound-option.hpp>
#include <wayfire/config/types.hpp>
#include <wayfire/util.hpp>
#include <wayfire/util/log.hpp>
#include <string>
#include <vector>

namespace scottland
{
struct shortcuts_t::impl
{
    struct shortcut_t
    {
        std::string command;
        bool repeat  = false;
        bool locked  = false;
        bool release = false;
        bool any_mods = false;
        wf::activatorbinding_t keys;  // any_mods: matched on the raw key, not registered
        wf::activator_callback callback;
        uint32_t held_key  = 0;  // the key of the press this shortcut matched, until it is let go
        uint32_t held_mods = 0;  // the modifiers held at that press
        wf::wl_timer<false> repeat_delay;
        wf::wl_timer<true> repeat_tick;
    };

    wf::option_wrapper_t<wf::config::compound_list_t<wf::activatorbinding_t, std::string, bool, bool, bool,
        bool>> list{"scottland/shortcuts"};
    wf::option_wrapper_t<int> kb_repeat_delay{"input/kb_repeat_delay"};
    wf::option_wrapper_t<int> kb_repeat_rate{"input/kb_repeat_rate"};
    std::vector<std::unique_ptr<shortcut_t>> shortcuts;  // stable: the repository keeps callback pointers
    claimed_t claimed;

    static bool eligible(const shortcut_t& shortcut)
    {
        if (shortcut.locked)
        {
            return true;
        }

        auto output = wf::get_core().seat->get_active_output();
        return !session_locked() && output && output->can_activate_plugin(wf::CAPABILITY_GRAB_INPUT);
    }

    /** The modifier a modifier key itself sets: still held in the state its own release reports. */
    static uint32_t own_modifier(uint32_t keycode)
    {
        return wf::get_core().seat->modifier_from_keycode(keycode);
    }

    static void run(const shortcut_t& shortcut)
    {
        wf::get_core().run(shortcut.command);
    }

    static void let_go(shortcut_t& shortcut)
    {
        shortcut.held_key = 0;
        shortcut.repeat_delay.disconnect();
        shortcut.repeat_tick.disconnect();
    }

    void start_repeat(shortcut_t& shortcut)
    {
        int delay = kb_repeat_delay;
        int rate  = kb_repeat_rate;
        if ((delay <= 0) || (rate <= 0) || (rate > 1000))
        {
            return;
        }

        shortcut.repeat_delay.set_timeout(delay, [&shortcut, rate] ()
        {
            if (!eligible(shortcut))
            {
                return;
            }

            run(shortcut);
            shortcut.repeat_tick.set_timeout(1000 / rate, [&shortcut] ()
            {
                if (!eligible(shortcut))
                {
                    return false;
                }

                run(shortcut);
                return true;
            });
        });
    }

    void clear()
    {
        for (auto& shortcut : shortcuts)
        {
            let_go(*shortcut);
            if (!shortcut->any_mods)
            {
                wf::get_core().bindings->rem_binding(&shortcut->callback);
            }
        }

        shortcuts.clear();
    }

    void load()
    {
        clear();
        for (const auto& [name, keys, command, repeat, locked, release, any_mods] : list.value())
        {
            if (command.empty())
            {
                continue;
            }

            auto shortcut = std::make_unique<shortcut_t>();
            shortcut->command = command;
            shortcut->repeat  = repeat;
            shortcut->locked  = locked;
            shortcut->release  = release;
            shortcut->any_mods = any_mods;
            shortcut->keys     = keys;
            auto *self = shortcut.get();
            shortcut->callback = [this, self] (const wf::activator_data_t& data)
            {
                if (!eligible(*self))
                {
                    return false;
                }

                bool key = (data.source == wf::activator_source_t::KEYBINDING) && data.activation_data;
                if (self->release && key)
                {
                    self->held_key  = data.activation_data;
                    self->held_mods = wf::get_core().seat->get_keyboard_modifiers();
                    return true;
                }

                run(*self);
                if (self->repeat && key)
                {
                    let_go(*self);
                    self->held_key = data.activation_data;
                    start_repeat(*self);
                }

                return true;
            };
            if (!any_mods)
            {
                wf::get_core().bindings->add_activator(wf::create_option(keys), &shortcut->callback);
            }

            shortcuts.push_back(std::move(shortcut));
        }
    }

    /** An any_mods shortcut's press: its key, whatever modifiers are held (the key still reaches
     *  the focused app, as nothing claimed it). */
    void any_mods_press(uint32_t keycode, uint32_t mods)
    {
        for (auto& shortcut : shortcuts)
        {
            if (!shortcut->any_mods || !shortcut->keys.has_match(wf::keybinding_t{0, keycode}) ||
                !eligible(*shortcut))
            {
                continue;
            }

            let_go(*shortcut);
            if (shortcut->release)
            {
                shortcut->held_key  = keycode;
                shortcut->held_mods = mods;
                continue;
            }

            run(*shortcut);
            if (shortcut->repeat)
            {
                shortcut->held_key = keycode;
                start_repeat(*shortcut);
            }
        }
    }

    wf::signal::connection_t<wf::input_event_signal<wlr_keyboard_key_event>> on_key =
        [=] (wf::input_event_signal<wlr_keyboard_key_event> *ev)
    {
        bool skip = (ev->mode == wf::input_event_processing_mode_t::IGNORE) || (claimed && claimed(ev));
        uint32_t mods = wf::get_core().seat->get_keyboard_modifiers();
        if (ev->event->state != WL_KEYBOARD_KEY_STATE_RELEASED)
        {
            if (!skip)
            {
                any_mods_press(ev->event->keycode, mods);
            }

            return;
        }

        for (auto& shortcut : shortcuts)
        {
            if (!shortcut->held_key || (shortcut->held_key != ev->event->keycode))
            {
                continue;
            }

            bool same_mods = shortcut->any_mods ||
                ((mods & ~own_modifier(ev->event->keycode)) == shortcut->held_mods);
            let_go(*shortcut);
            if (shortcut->release && !skip && same_mods && eligible(*shortcut))
            {
                run(*shortcut);
            }
        }
    };
};

shortcuts_t::shortcuts_t() = default;
shortcuts_t::~shortcuts_t()
{
    fini();
}

void shortcuts_t::init(claimed_t claimed)
{
    priv = std::make_unique<impl>();
    priv->claimed = std::move(claimed);
    priv->load();
    priv->list.set_callback([this] { priv->load(); });
    wf::get_core().connect(&priv->on_key);
}

void shortcuts_t::fini()
{
    if (!priv)
    {
        return;
    }

    priv->on_key.disconnect();
    priv->clear();
    priv.reset();
}
}
