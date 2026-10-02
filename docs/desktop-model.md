# Desktop model

The plugin owns Scottland's desktop state. Wayfire map, geometry, title, app-ID, focus and urgency
signals are input facts; plugin position commands, scale targets and widget transitions update
the model before rendering. The renderer applies targets to Wayfire. Animations, scene disable
leases, process handles and timers are resources, not independent logical state.

## Ownership

| Field | Owner | Input or rendering |
|---|---|---|
| Windows, identity, title, size, position, zone, focus, layer | Plugin's desktop model | Wayfire signals supply facts; plugin position/layer commands supply targets. |
| Target scale | Plugin's desktop model | Zone/drag rules set it; the scale transformer animates toward it. |
| Widget lifecycle, rail, drop point, collapsed presentation, touch traits | Plugin's desktop model | One lifecycle transition applies visibility; the renderer positions the widget and holds balanced disables. |
| Launcher PID, launch unit | Plugin's desktop model | A pidfd event records launcher exit and publishes it; snapshot serialization never probes process liveness. |
| Resolved desktop identity, name, icon, built-in card trait | Plugin's desktop model | The launcher submits these once for a specific launch unit; there is no identity side file. |
| Attention source set, per window | Plugin's desktop model | Each source adds/removes its name; `builtin:` names are reserved for plugin inputs, so a configured source cannot clear a bell/urgency. User focus answers all sources. Halos read this set. |
| Desktop collapsed mode | Plugin's desktop model | Super+M updates the mode and widget presentation together. |
| Full-screen focus per screen (FS1) | Plugin's desktop model | Wayfire's fullscreen-promotion signal sets it; that screen's docked widgets are marked away (slid off and hidden) without leaving docked, and the focus.d hooks follow whether any screen is in focus. Published as `focus` (screen names). |
| Selected window set | Plugin's desktop model | Reserved, empty until multi-select is implemented. |
| Drag origin, re-grab chain, morph, scale/grab state and held-above window | Plugin's desktop model (`drag`) | One drag-session record; Wayfire's drag controller, timers and frame buffers are input/rendering resources. The full desktop snapshot includes origin, chain, morph target and held-above identity; external slices omit them. |
| Badge count/visibility | Widget service | Unity Launcher input is a partial-update protocol; only this input is merged. The service emits complete presentation snapshots. |
| Mailbox payload | App, held by widget service for that launch | Publish replaces the payload; compositor snapshots cannot change it. |
| Card presentation | Derived only from one state-file snapshot | The card replaces its entire object; no mutable launch-environment defaults. |
| Configured source listing metadata | That source | Used to run its answered command; authoritative marked attention comes from model snapshots. |

## Subscription interface

`scottland/subscribe {"slice":"desktop"|"widgets"|"attention"}` installs the watch and returns
the complete current slice in one compositor turn. Later events are `scottland-model#`,
`scottland-widgets#` or `scottland-attention#` respectively. The existing Wayfire event repository
owns subscription connections, so a plugin reload preserves them. Disconnect/reconnect means
subscribe again: the reply is current immediately. `scottland/desktop-model` accepts the same
slice parameter for a read without a watch.

Every reply/event has a compositor `session` identity and a monotonically increasing `version`. A new session replaces all prior state, regardless of its version. Consumers replace the entire slice,
ignore older/equal versions, and never fill omitted fields from a previous snapshot. Versions
are session-local, persist across plugin replacement (including an ordinary unload/load), and
may have gaps when another slice changes. Events contain complete current state, never deltas.
The old `scottland/widgets` read remains a compatibility snapshot of committed widgets only.

The desktop slice includes positions, sizes, zones, layers, live morphing form and selection.
External slices omit geometry and animation samples: drag motion that changes only position
sends them nothing. The widget service receives widget presentation/identity and window target
scales for `org.scottland.Windows`; a target-scale change is useful app state, never an animation
sample. Unchanged widget presentation does not rewrite the card file. Attention sources receive
window IDs, PIDs and source sets, without title, layout or scale traffic.

The launcher's `widget-traits` call submits resolved identity and traits with the window ID and
unique unit. `org.scottland.WidgetLaunch.Prepare(id, unit)` synchronously writes the current
presentation before exec. The service is the only file writer. Each file includes complete
identity, presentation, mailbox/badge, model version and local presentation revision, and is
replaced atomically. D-Bus `PropertiesChanged` carries the complete public property set with
`Version` and `Revision`, emitted after the file is written; property reads return the same counters. The service persists only its own badge/mailbox snapshot, keyed by compositor
session identity, so a helper restart retains these values while a new session cannot inherit them. The environment carries identity and paths; title, rail, collapsed mode
and badge are read from the file. Manifest placeholders are still expanded for a launch command.
A marked plugin reload writes one atomic model handover containing mode, source sets, version
and widget launch records. It reads the old handover format solely for upgrades from an older
build; new reloads don't create a separate collapsed-mode file. Legacy upgrades migrate resolved
identity from the surviving widget's launch-qualified environment and the old unit-qualified
identity file, once, before publication. Card traits come from its actual launch command.
`tests/upgrade-test.sh MAIN_CHECKOUT` exercises a real main build and future badge matching.

## Invariants

| ID | Invariant | Status |
|---|---|---|
| DM1 | Every mapped toplevel is represented in one plugin-owned desktop model. Wayfire facts enter through signals; scale, position, layer and lifecycle targets drive the renderer. Each field has one documented owner. | implemented (headless) |
| DM2 | A subscription returns a full current versioned slice immediately, then full replacements on logical change (including held widget morph creation, direction and center). Consumers replace compositor-owned fields and reject older/equal versions; subscribe, reconnect, helper restart and marked reload reconstruct the current slice. | implemented (headless) |
| DM3 | External slices exclude geometry and animation samples; an unchanged presentation never rewrites its card file. There is no model polling or drift-repair service. | implemented (headless) |
| DM4 | The widget service atomically writes a complete presentation file before the widget process starts. The card renders exclusively from that snapshot. Resolved launch identity enters the plugin, without a launch side file. Full D-Bus property signals expose the same model version and already-written file revision. | implemented (headless) |
| DM5 | A marked reload between model builds preserves collapsed mode, attention sources and mapped committed widget identities (previews are ended). A legacy main upgrade migrates surviving launch identity in one atomic model handover; publication waits for every surviving link to be installed, preserving service-owned mailboxes, and its version increases afterward. | implemented (headless) |
| DM6 | Test-only `scottland/audit-model` compares model facts/targets with the Wayfire scene, widget service replica and card-reported text visibility, collapse and actual width. Seeded random real-input sequences audit after every operation and print their seed/trace on failure. Independent regressions additionally assert mailbox payload, input-intended morph direction, Esc layer release, fullscreen visibility against promotion, process exit, migrated identity/badge routing and app-ID-only changes. They run only in an isolated headless session. | implemented (headless) |
| DM7 | The drag session is a field of the desktop model: current origin, original form, re-grab chain, morph target, drag scale/grab state and temporarily held-above window have one owner. Existing Esc and re-grab behavior stays the same. | implemented (headless) |
| DM8 | A marked reload transfers the live pidfd, including launches without systemd scopes. Legacy upgrades open a handle only for a verified live launcher; an unverifiable launcher is reported as PID zero. Launcher exit enters through a pidfd event, changes the tracked PID and publishes a newer full snapshot while a forked widget stays mapped. Serializing a snapshot never probes process liveness. | implemented (plumbus headless) |

App-ID-only updates are exercised by a real GTK Wayland client in
`tests/state-regressions-test.sh`; it changes no title or geometry.

Tests: `tests/widgets-test.sh` retains the behavior regression checks;
`tests/state-model-test.sh SEED STEPS` adds model/scene/replica/actual-render assertions after
every operation (dock, undock, collapse, title, reload, Esc, close, finger drag and attention).
It also checks immediate subscription/reconnect, geometry filtering, helper restart with a badge,
attention/mode reload preservation and rejection of deliberately incorrect observations.
The test endpoint and D-Bus render diagnostics exist only with `SCOTTLAND_TEST_MODEL=1`, set by
the isolated headless harness. Live cards launch no reporting process.

Validation on plumbus, 2026-10-01: `tests/widgets-test.sh` passed 97 checks (the existing 96
plus launcher-exit subscription coverage); `tests/widget-launch-test.py` passed 17;
`tests/widget-bus-test.py` passed 13; `tests/build-config-test.sh` passed all five rounds of
20 concurrent builds. `tests/state-model-test.sh 271828 50` passed 84 checks; seed 104729
with 50 operations passed 89 using `SCOTTLAND_DBUS_LEGACY=1`. The model runs use real stipc
pointer/touch/key input, real widget programs and private D-Bus, audit after each operation,
and retain expanded/final screenshots in `$XDG_RUNTIME_DIR/scottland-model-artifacts/`.
Expanded-card screenshots were inspected. These are headless checks; physical screen,
webcam and hardware input validation were not performed for this change.
