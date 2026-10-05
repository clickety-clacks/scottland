#pragma once

#include <memory>

namespace scottland
{
/** True while a session lock holds the screen: some output is inhibited by Wayfire's session-lock
 *  plugin and shows a lock surface (or, after the lock client died, the lock's stand-in). Locking
 *  is in progress, not held, until the lock client has confirmed its surfaces. */
bool session_locked();

/** Session state for integrations, and hardware switch bindings.
 *
 *  IPC scottland/session-state -> {locked: bool}
 *  [scottland] switch_device_<n> / switch_state_<n> / switch_command_<n>: run a command when a
 *    switch (the libinput device name, e.g. "Lid Switch") turns on, off, or either ("toggle").
 *    Switches are hardware state, not keystrokes, so they run whether or not the session is locked.
 *  Test sessions only (SCOTTLAND_TEST_MODEL=1): IPC scottland/test-switch {device, state:bool}
 *    adds a virtual switch device to the compositor's input (as stipc adds its keyboard) and
 *    toggles it, so the event takes Wayfire's own switch path; only libinput is bypassed. */
class session_t
{
  public:
    session_t();
    ~session_t();
    void init();
    void fini();

  private:
    struct impl;
    std::unique_ptr<impl> priv;
};
}
