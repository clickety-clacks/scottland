# Night Light policy module

This module implements the isolated command/state/status behavior slice of the
Night Light v3 contract (`art_28ec42f4`, SHA-256
`756abe1113a776a30d5302726efe3de67d1e72823224d941bed44b42108cbf6a`). The
source is `core/tools/lib/src/night_light.rs`. It is pure policy: it parses the
operation after `night-light`, calculates the next session request, returns
intended per-output actions, and formats status/error/exit results from injected
watcher facts. It performs no filesystem, process, D-Bus, Wayland, gamma, or
capture I/O.

The accepted command words are `on`, `off`, `toggle`, and `status`, exposed by
the future command as `scottland-display night-light {on,off,toggle,status}`.
New session request state starts off. `on` and `toggle` leave the request
unchanged and fail if the existing display watcher is unavailable. `off`
clears the request even if that watcher is unavailable, then reports failure
and unknown output state; clearing the request prevents a later watcher start
from reapplying a tint the user turned off. `status` remains read-only and
reports unavailable output facts as unknown.

When the watcher is available, warming actions use the fixed 4000K target. The
module reports an output as warm only when the injected acquisition and
application facts both succeeded. `off` restores only outputs the watcher
identifies as warm and owned by Night Light; an unowned output is left alone.
Partial failures keep the request state but return a failing exit status and
per-output statuses, so a successful display is not hidden by another
display's failure. Unknown facts remain unknown.

The module does not provide the shared `scottland-display` dispatcher, the
session runtime transport, the per-session color-scheme watcher, gamma
acquisition or output lifecycle. Those remain with the serialized Display
integration owner. Until the accepted Display handoff wires this module into
the tools workspace, its focused behavior checks run directly:

```sh
rustc --edition=2024 --test core/tools/lib/src/night_light.rs -o build/night_light_logic_tests
build/night_light_logic_tests
```
