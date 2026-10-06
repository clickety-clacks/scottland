#pragma once

#include <functional>
#include <memory>
#include <wayfire/signal-definitions.hpp>

namespace scottland
{
/** Shortcuts that keep Hyprland-style flags together, for integrations whose bindings Wayfire's
 *  command plugin cannot express (its config has no repeating or release binding that also runs
 *  on the lock screen).
 *
 *  [scottland] shortcut_keys_<n>    = activator, e.g. KEY_F9 or <super> KEY_P
 *              shortcut_command_<n> = command to run
 *              shortcut_repeat_<n>  = true: runs again at the keyboard's repeat rate while held
 *              shortcut_locked_<n>  = true: also runs while the session is locked
 *              shortcut_release_<n> = true: runs when the pressed key is let go, not on press
 *
 *  A shortcut without `locked` runs only while the session is unlocked and no other plugin holds
 *  the input, like Wayfire's ordinary bindings; with `locked` it always runs, like Wayfire's
 *  always bindings. Eligibility is checked on press, on every repeat and again on release. A
 *  release shortcut runs only for a press it matched itself (same keys, same modifiers), and only
 *  when the modifiers held at release are still the ones held at that press. Repeat and release
 *  apply to keys; a modifier-only, button or gesture activation runs once when Wayfire fires it. */
class shortcuts_t
{
  public:
    /** Whether a key event belongs to a key layer (docs/key-layers.md): those are skipped. */
    using claimed_t = std::function<bool(const wf::input_event_signal<wlr_keyboard_key_event>*)>;

    shortcuts_t();
    ~shortcuts_t();
    void init(claimed_t claimed);
    void fini();

  private:
    struct impl;
    std::unique_ptr<impl> priv;
};
}
