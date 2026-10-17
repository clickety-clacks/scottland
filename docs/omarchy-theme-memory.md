# Omarchy theme and wallpaper memory

The existing Sunlight consumer remembers the last observed manual theme
publication separately for the light and dark **solar periods**. A selection
made during the dark period belongs to dark memory, regardless of its palette.
At a new transition, it saves any final outgoing-period publication before
switching to the destination period's remembered selection. An unset memory
uses the existing explicit day/night mapping, or Watercolor Dream Light/Dark
when that mapping is unset. User configuration is not rewritten.
If a remembered theme has been removed from both Omarchy's user and built-in
theme directories, that memory is discarded and the same fallback is used.
Removal racing an attempted restore also selects the fallback; unrelated setter
errors remain failures and do not silently select a different theme.

The consumer watches Omarchy's published `current/theme.name` under
`~/.local/state/omarchy`, the path its setters use even with a custom
`XDG_STATE_HOME`. Scottland's own memory still honors `XDG_STATE_HOME`.
It never edits that file or the theme tree. Remembering a
publication does not invoke a theme command. The consumer's existing global
lock serializes observation, application and acknowledgement across sessions.
Only an unacknowledged enabled transition invokes `omarchy theme set`.

The same consumer observes `current/background`, Omarchy's selected-image link,
and remembers a wallpaper for each period independently of the theme name.
Wallpaper-only changes count; a final outgoing observation happens before the
period changes. Restoring a period first sets its theme and then calls
`omarchy theme bg set` with its remembered image. This order retains that image
even when setting the theme replaces the background, including when both
periods use the same theme with different wallpapers. Observation, repeated
events and restarts after acknowledgement do not reapply either selection.

External images are stored as resolved absolute paths, preserving spaces and
distinguishing equal basenames in different directories. Omarchy copies some
images into its replaceable `current/theme` directory. Those are stored by theme
name and relative image path, and resolved only after that exact theme is
restored. The mutable `current/background` link is never the remembered image.
This remembers references, not image backups: current contents of a remembered
file are used, with no copying, search for renamed files or synchronization.

Missing, unreadable, empty or non-regular remembered images are unavailable for
that restore. The theme's normal wallpaper remains; if it supplies none, the
consumer sends no blanking command and leaves the existing display alone. A
reference that disappears during the wallpaper command recovers the valid
theme default when available. Such unavailability does not prevent transition
acknowledgement and is not retried on every poll. Other setter failures remain
errors without acknowledgement. Availability checks are not an image decoder;
the supported Omarchy setter and shell own media validation and rendering.

Wallpaper observations are kept separately from theme first-use provenance.
An already acknowledged current period may acquire its initial image without
inventing a manual theme choice or an opposite-period image. Old schema-1
theme-only records remain valid without migration. Saved wallpaper references
survive unavailable-image fallback until another observed choice replaces them.
Automatic theme defaults and wallpaper restores update observation baselines,
not saved choices. Durable command intent suppresses publications after an
interrupted apply; a reported failed command also records its partial effects
before allowing later manual wallpaper observations in the destination period.

`$XDG_STATE_HOME/scottland/omarchy-theme-memory.json` (defaulting to the user's
local state directory) holds the two choices, outgoing period and last observed
publication. Atomic replacement persists these across consumer restarts. An
automatic target is recorded before the command, so a restart after a partial
application can distinguish its publication from a manual choice. A failed
command retains the existing pending-transition retry behavior. The destination
period is persisted before applying, so later manual picks cannot overwrite the
opposite period's choice while a delivery remains pending. Successful
application does not update either manual-choice entry. Explicitly selecting
the same theme later is observable as a new publication and can be remembered.

On first use the already active publication establishes a baseline: the
consumer cannot retrospectively identify whether an older adapter or the user
selected it. It does not invent manual-choice provenance for that baseline.
Subsequent selections are remembered. The observation model preserves the last
publication seen before a transition; it does not promise to capture every
intermediate theme selection while the consumer is stopped. Other external
writers are not identified as human input. Overlap with a manual setter during
an automatic apply remains governed by Omarchy's existing serialized theme
setter. The background setter is not a transaction spanning both commands;
external writes racing that interval or a hard interruption cannot reliably be
identified as human input. The consumer does not claim reconstruction of those
intermediate selections. It observes the existing publication paths, adds no
hooks to Omarchy, and never rewrites user configuration.

Missing/unset/false integration configuration and disabled transition records
remain inert. No scheduler, daemon, UI, configuration option or timer migration
is added. The Core transition producer, opt-in defaults and stock timer are
unchanged. The existing first-enable/re-enable/catch-up transition exceptions
remain intact.

`tests/omarchy-theme-memory-test.py` defines isolated consumer scenarios with
temporary publications and a stand-in theme setter: independent restoration,
poll/restart preservation, outgoing-period attribution, both fallback modes,
manual reselection, disabled/invalid no-ops, explicit mapping, partial-command
failure, removed themes (including removal during application), destination
attribution after a failed fallback, corrupt memory and watcher observation.
Wallpaper scenarios additionally cover independent image references, copied
theme images, same-theme round trips, wallpaper-only and final outgoing
selections, theme setter side effects, old state, missing/unreadable images,
partial failures and restarts. This fixture bypasses the real
picker, theme command, compositor and timer. Its execution result and the
existing solar policy/scheduler/opt-in regressions must be reported separately;
no installed behavior follows from source or fixture inspection.
