# Desktop model

The plugin owns Scottland's desktop state. Wayfire map, geometry, title, app-ID, focus and urgency
signals are input facts; plugin position commands, scale targets and widget transitions update
the model before rendering. The renderer applies targets to Wayfire. Animations, scene disable
leases, process handles and timers are resources, not independent logical state.

## Ownership

| Field | Owner | Input or rendering |
|---|---|---|
| Windows, identity, title, size, position, zone, focus, layer | Plugin's desktop model | Wayfire signals supply facts; plugin position/layer commands supply targets. |
| Zone memories, most recent side, stable hint slot, pending rail placement, label width | Plugin's desktop model (`window_state_t`, width on desktop) | Real placements/drop establish normalized centers. Desktop snapshots publish them; marked reload uses the same atomic handover. Declutter transforms never update them. |
| Target scale | Plugin's desktop model | Zone/drag rules set it; the scale transformer animates toward it. |
| Widget lifecycle, rail, drop point, collapsed presentation, touch traits | Plugin's desktop model | One lifecycle transition applies visibility; the renderer positions the widget and holds balanced disables. |
| Launcher PID, launch unit | Plugin's desktop model | A pidfd event records launcher exit and publishes it; snapshot serialization never probes process liveness. |
| Resolved desktop identity, name, icon, built-in card trait | Plugin's desktop model | The launcher submits these once for a specific launch unit; there is no identity side file. |
| Attention source set, per window | Plugin's desktop model | Each source adds/removes its name; `builtin:` names are reserved for plugin inputs, so a configured source cannot clear a bell/urgency. User focus answers all sources. Halos read this set. |
| Desktop collapsed mode | Plugin's desktop model | Super+M updates the mode and widget presentation together. |
| Full-screen focus per screen (FS1) | Plugin's desktop model | Wayfire's fullscreen-promotion signal sets it; that screen's docked widgets are marked away (slid off and hidden) without leaving docked, and the focus.d hooks follow whether any screen is in focus. Published as `focus` (screen names). |
| Goo availability per screen | Plugin's desktop model (`goo_outputs`) | The goo renderer reports surface attachment/removal, including GPU fallback. The desktop slice publishes `goo` (screen names); widget/attention slices exclude it and simulation samples. |
| Selected window set | Plugin's desktop model | Reserved, empty until multi-select is implemented. |
| Drag origin, re-grab chain, morph, scale/grab state and held-above window | Plugin's desktop model (`drag`) | One drag-session record; Wayfire's drag controller, timers and frame buffers are input/rendering resources. The full desktop snapshot includes origin, chain, morph target and held-above identity; external slices omit them. |
| Badge count/visibility | Widget service | Unity Launcher input is a partial-update protocol; only this input is merged. The service emits complete presentation snapshots. |
| Mailbox payload | App, held by widget service for that launch | Publish replaces the payload; compositor snapshots cannot change it. |
| Card presentation | Derived only from one state-file snapshot | The card replaces its entire object; no mutable launch-environment defaults. |
| Configured source listing metadata | That source | Used to run its answered command; authoritative marked attention comes from model snapshots. |

Goo consumes model windows, widget lifecycle/away state, attention sources, drag origin and FS1
focus. Geometry, focus/attention cross-fades and collapsed/morph sizes are samples of the existing
frame presentation driven by that model. They are derived rendering data, never a second desktop
state or a subscription consumer. GPU nodes, sources, timers and sleep remain renderer resources.

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
| DM9 | Goo reads logical visibility, attention, drag and fullscreen focus from the desktop model; attached goo screens are model state in the desktop slice. GPU fields and animation samples remain renderer resources. Live disable/fallback removes those screens without changing widget or attention slices. | implemented; plumbus headless input/snapshot/fallback checked |
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

Final validation on plumbus, 2026-10-01, after merging main's WG16 resize-gravity
change and subsequent card-animation rollback (`0ca408c`, merge `cc3db98`):

| Suite | Result |
|---|---|
| `tests/widgets-test.sh` | 103 checks passed, including main's L29 overlap-pixel assertions |
| `tests/state-model-test.sh 271828 50` | 95 checks passed |
| Seed 104729, 50 operations, `SCOTTLAND_DBUS_LEGACY=1` | 100 checks passed on the final build |
| `tests/state-regressions-test.sh` | 6 checks passed: unscoped exit after reload, late fullscreen adoption, reload on another output, return from fullscreen, app-ID-only changes, running attention helper survival |
| `tests/upgrade-test.sh ~/Projects/scottland-state-fixes-main` | 2 checks passed, using a separate build of main (`ea1d0f4`) with its legacy launcher, service and card; identity/traits survive and subsequent badges route correctly |
| `tests/attention-sources-test.py` | 5 checks passed |
| `tests/widget-launch-test.py` | 17 checks passed |
| `tests/widget-bus-test.py` | 13 checks passed |
| `tests/build-config-test.sh` | 5 rounds passed, each with 20 concurrent builds |
| `tests/omarchy-focus-test.sh` | 3 checks passed |

An earlier legacy-seed run, before the final main merge, failed at operation 22 with a
card-render presentation mismatch. It did not recur in two complete reruns of that build
or in the final build's run; its cause remains unconfirmed. The audit now retains
complete service, card, desktop and scene observations on failure in
`scottland-model-artifacts/seed-SEED-failure.json`. This is a remaining test-stability risk,
separate from the eight fixed review findings; the audit was not weakened or disabled.

All sessions used the checkout's own helpers, private D-Bus and
`SCOTTLAND_HEADLESS_DIR=$XDG_RUNTIME_DIR/scottland-headless-state-fixes`, with build/test scratch
files under `~/.cache/scottland-test-tmp`. The final session matrix also used a private
`XDG_RUNTIME_DIR` (`/run/user/1000/scottland-state-fixes-runtime`): a prior widget run was
interrupted when shared-host activity removed its recorded session environment while its
compositor remained alive. That infrastructure failure's log was retained, and the complete
session matrix was rerun successfully with the private runtime. Unit/config/focus tests create
their own temporary runtimes and also passed after the final main merge.
Model runs use real stipc pointer/touch/key input,
real widget programs and audits after each operation. Expanded-card, fullscreen and
other-output reload screenshots were retained and inspected. No live session on osanwe or
physical screen on plumbus was touched. The isolated runtime was stopped after testing.

## Window-key integration

Alt navigation reads `model.windows` and `model.widgets`; widget cycles use the same lifecycle
as real drops and card clicks. The desktop slice publishes `placement` (slot, side and normalized
positions), optional `pending_rail`, `pinned_scale` and desktop `hint_width`. External presentation
and attention slices omit placement. Closing removes all placement state with the window. The
atomic model handover retains memories and slots on marked reload; an older Alt-branch position
file is imported once, never written by the model build. Explicit zone navigation clears a drag
scale pin; hints never engage during a drag (L31). Card restore applies normalized memory to its
destination output, including a different logical size/scale. After merging main `e76bc56`, rail
placement uses its pending-size/gravity transaction; geometry callbacks record committed memories
without a corrective move. The collapse raw-key tracker connects after layers and hints and
respects claimed/consumed input.

Integrated validation of code `756b8d7` is recorded in [windowing-keys.md](windowing-keys.md).
The later merge `56b0cc1` includes main's documentation-only tip `16286df` without changing code.
All 146 widget checks passed, including current main's 43 collapse-input/preview checks.
The 73 windowing checks independently assert drop subscription delivery, rendering-only declutter,
close removal, external-slice filtering and reload preservation. All seven focused model
regressions pass, including a real card drag/click across differently scaled outputs. The final
native seed 271828 and legacy-D-Bus seed 104729 each ran 50 operations and passed 95 and 100 checks
respectively. The historical legacy-card audit stability risk above did not recur in these runs;
this integration does not claim its cause was resolved. All sessions were stopped afterward.

## Review regression coverage

| Finding | Fix | Independent regression |
|---|---|---|
| 1: mailbox loss during handover | Suppress publication during marked teardown and initialization until all links are installed. | A second widget's app publishes a literal payload; the payload must survive a two-widget marked reload (`state-model-test.py`). |
| 2: stale attention listing crashes helper | Filter missing IDs and validate action replies before replacing snapshots; preserve acknowledgement of unchanged valid replies. | Unit checks cover marking/removal races; a running helper must survive a source repeatedly listing a closed window, then mark another window (`attention-sources-test.py`, `state-regressions-test.py`). |
| 3: late/fullscreen widget visibility | Remember away state while launching; adopt hidden and deny focus during fullscreen; reconstruct promotion from every output's compositor state before first handover rendering. | Delayed widget must stay hidden and leave the fullscreen client focused; reload while another output has keyboard focus must keep the first output's widget hidden (`state-regressions-test.py`). The audit also checks against compositor promotion, independently of `link.away`. |
| 4: Esc strands a re-grab above widgets | Cancellation releases temporary layer ownership and publishes the completed drag. | A fixture first proves the ordinary drop is above, re-grabs and presses Esc, then requires the real scene's ordinary layer and no held owner (`state-model-test.py`). |
| 5: missing morph subscription updates | Publish logical drag changes independently of scale. Main's estimated first-card target and smoothed actual size are in the model-owned morph. | One held widget drag goes off and back onto its rail; delivered direction and center must follow that input, advance versions and match direct reads (`state-model-test.py`). |
| 6: lost launcher exit observation | Transfer the existing close-on-exec pidfd; legacy upgrades verify the live relationship before opening a new handle and use PID zero when unverifiable. | An unscoped launcher forks a widget, survives marked reload, then exits; the subscription must publish PID zero with the widget still docked (`model-process-test.py --reload`, `state-regressions-test.sh`). |
| 7: lost legacy resolved identity | Read surviving widget launch-qualified environment and legacy unit-qualified identity once during migration; derive card traits from the actual command. | Real main-to-branch reload compares original desktop/name/icon and surviving launch, then sends a new badge for the original desktop ID (`upgrade-test.sh`). |
| 8: stale app-ID | Observe Wayfire's app-ID change signal and publish it. | A real GTK Wayland client changes only its app-ID; subscription and late read must expose it while title and geometry stay unchanged (`app-id-app.py`, `state-regressions-test.py`). |


## Goo merge validation (2026-10-01)

Goo now reads model windows, widget lifecycle/away state, attention, drag and FS1 focus,
with rendered geometry/cross-fades/morph size sampled from the existing frames. Available
screens are model state in the desktop `goo` array. Real-input goo checks additionally assert
newer versions on availability changes, exclusion from widget/attention slices, removal on
live disable and unsupported-GPU fallback, FS1 suspension and resumption. Simulation steps,
GPU resources and animation samples never enter snapshots.

The isolated `Projects/scottland-goo-merge` checkout on plumbus first passed the full matrix
on main `a198ede`. Main then advanced to `10774a2` with Alt hints, key layers and placement.
The goo provider now composes parent presentation transforms before a cross-output drag;
input uses the same derived source island rectangle, radius and close point. Declutter changes
no model geometry or zone memory. Two additional real-Alt goo checks cover displaced clipping
and clearing that displaced area after release.

Current-main validation: goo 40 normal / 40 packed, 12 flow/FS1/two-output, 3 fallback;
windowing 73, key layers 59, state regressions 7, pure windowing units 41. The repeated widget
matrix passed 146 checks each with goo off/on; the seeded model audit `271828 50` with goo on
passed 95 checks, including audits after all 50 real-input operations. Unit/config/focus checks also passed; the complete
record and retained failed infrastructure attempts are in
[goo.md](goo.md#main-merge-validation-2026-10-01). No live session or physical screen was used.
