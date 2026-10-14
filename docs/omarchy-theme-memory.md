# Omarchy theme memory

The existing Sunlight consumer remembers the last observed manual theme
publication separately for the light and dark **solar periods**. A selection
made during the dark period belongs to dark memory, regardless of its palette.
At a new transition, it saves any final outgoing-period publication before
switching to the destination period's remembered selection. An unset memory
uses the existing explicit day/night mapping, or Watercolor Dream Light/Dark
when that mapping is unset. User configuration is not rewritten.

The consumer watches Omarchy's published `current/theme.name` under the existing
Omarchy state root. It never edits that file or the theme tree. Remembering a
publication does not invoke a theme command. The consumer's existing global
lock serializes observation, application and acknowledgement across sessions.
Only an unacknowledged enabled transition invokes `omarchy theme set`.

`$XDG_STATE_HOME/scottland/omarchy-theme-memory.json` (defaulting to the user's
local state directory) holds the two choices, outgoing period and last observed
publication. Atomic replacement persists these across consumer restarts. An
automatic target is recorded before the command, so a restart after a partial
application can distinguish its publication from a manual choice. A failed
command retains the existing pending-transition retry behavior. Successful
application does not update either manual-choice entry. Explicitly selecting
the same theme later is observable as a new publication and can be remembered.

On first use the already active publication establishes a baseline: the
consumer cannot retrospectively identify whether an older adapter or the user
selected it. It does not invent manual-choice provenance for that baseline.
Subsequent selections are remembered. The observation model preserves the last
publication seen before a transition; it does not promise to capture every
intermediate theme selection while the consumer is stopped. Other external
writers are not identified as human input. Overlap with a manual setter during
an automatic apply remains governed by Omarchy's existing serialized setter.

Missing/unset/false integration configuration and disabled transition records
remain inert. No scheduler, daemon, UI, configuration option or timer migration
is added. The Core transition producer, opt-in defaults and stock timer are
unchanged. The existing first-enable/re-enable/catch-up transition exceptions
remain intact.

`tests/omarchy-theme-memory-test.py` defines isolated consumer scenarios with
temporary publications and a stand-in theme setter: independent restoration,
poll/restart preservation, outgoing-period attribution, both fallback modes,
manual reselection, disabled/invalid no-ops, explicit mapping, partial-command
failure, corrupt memory and watcher observation. This fixture bypasses the real
picker, theme command, compositor and timer. Its execution result and the
existing solar policy/scheduler/opt-in regressions must be reported separately;
no installed behavior follows from source or fixture inspection.
