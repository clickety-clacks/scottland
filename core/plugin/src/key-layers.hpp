#pragma once

#include <memory>
#include <wayfire/signal-definitions.hpp>

namespace scottland
{
/** IPC scottland/key-layer {action:"list"|"set"|"clear", window:ID | pid:PID+namespace:NAME,
 *  keys:["MODMASK:keysym",...]}. set replaces a mapped surface's claims, clear removes them,
 *  list returns mapped surfaces and registered/active state. Bits: Shift=1 Ctrl=4 Alt=8 Super=64.
 *  Full contract and defaults: docs/key-layers.md.
 *
 *  Focus-scoped layers today; kept separate from desktop layout and binding storage so additional
 *  scopes and stacked fall-through can be added here without changing the desktop model. */
class key_layers_t
{
  public:
    key_layers_t();
    ~key_layers_t();
    void init();
    void fini();

    /** Raw-key consumers (remaps, release commands, hints) must leave a claimed event alone.
     *  Connect them after init(). Unclaimed input and compositor input grabs keep their usual path. */
    bool handles(const wf::input_event_signal<wlr_keyboard_key_event> *event) const;

  private:
    struct impl;
    std::unique_ptr<impl> priv;
};
}
