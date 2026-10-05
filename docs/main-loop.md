# The main loop

Wayfire dispatches input, IPC, timers, rendering and every plugin callback on one thread. Anything
long that Scottland does there freezes the pointer (P8). This doc is the rule for that budget, how it
is measured and enforced, what still misses it (each with a number and an owner), and how a
reload hands the running plugin's state to the next copy. Design and review history:
`~/.local/state/scottland-jobs/mainloop/design.md` and `review-astra-1..6.md` (signed off for
Phases 0, 1, 2 and 4 on 2026-10-03; Mike decided the two Phase 3 questions the same day).

## Invariants

| ID | Rule | Status |
|---|---|---|
| ML1 | Target: no Scottland callback on the main loop runs longer than **2 ms** (optimized build, test host, 30-window fixture). A callback is one entry from Wayfire, including one whole event-source dispatch. What misses it is a named exception in the table below, with a number and an owner; an exception is an open delivery risk, not a met target. | measured; open exceptions below |
| ML2 | Target: Scottland's outermost callbacks occupy at most **4 ms of any 16.7 ms** of wall time, all outputs together, frames or not. | measured (`ml2_max_ms`); open where the exceptions run |
| ML3 | Work that cannot meet ML1 becomes a worker job or stays a named exception until its design is signed off. No job runs on the main loop as a fallback. | rule |
| ML4 | No callback after `init()` calls `fork`, `system`, `sleep`, `waitpid`, a GPU read that waits, or file I/O, except the named exceptions: `core_run` (D3), the alpha-shape and energy readbacks and the hit-test sample (D2, Phase 3), the wallpaper capture (GO20), one procfs gather per new process, fonts (D4). | rule; exceptions measured |
| ML5 | Every Scottland entry point is timed (`SCOTTLAND_LOOP_SCOPE`); slow callbacks and unresponsive periods are recorded in the diagnostic ring. Nothing in the plugin writes to stderr or Wayfire's log after `init()`. | implemented (Phase 1) |
| ML6 | No plugin thread, event source or callback outlives `fini()`. | implemented: the watchdog and its eventfd source stop last in `fini()`; reload tests count descriptors |
| ML7 | A late result never overrides a newer state. | rule (worker, Phase 4) |
| ML8 | Window mode entry (Alt held: `alt_hold` → `begin_window_keys`, including the first badge raster of a session) is an acceptance target of this work: it was 41-53 ms the first time (cold font) and 7-20 ms after at 10 windows, 92 ms at 30. | open: 15 ms at 10 windows and 47 ms at 30 on the aarch64 test machine after the font warm-up, the overlay batching and Phase 3; see "Window mode entry" |

## Timing every entry point

`core/plugin/src/loop.hpp`. Each entry from Wayfire opens a scope named in
`core/plugin/src/loop-table.def` (signal handlers, timers, idle calls, IPC methods, bindings,
render hooks and render instances, hit tests and input interactions, event-loop fd sources,
option callbacks). Scopes nest: per name the monitor keeps calls, total, maximum and calls over
2 ms; only the outermost scope counts toward ML2 and the history. Nested scopes inside entry points
attribute known costs (`peek_step`, `hint_raster`, `goo_energy_readback`, `goo_sample_at`,
`goo_shape_update`, `goo_wallpaper_capture`, `widget_capture`, `core_run`, `publish_model`, ...).
Pure accessors Wayfire calls per node (bounding boxes, transformer getters) are not scoped.

- `scottland/loop-stats` (IPC, `scottland-ctl loop-stats [--reset]`): per-scope counters, the ML2
  maximum, the last 256 outermost callbacks, watchdog counters, and the reload state
  (`reload.load`, `reload.nonce`, `reload.outcome`).
- ML2 is exact for every window ending at a callback exit: a queue of the outermost intervals that
  intersect the last 16.7 ms, the oldest clipped. Each run of windows over 4 ms (an episode) is
  recorded with the scopes that filled its worst window (`ml2_episodes`, the last 1,024): the latency
  gate excuses an episode only by the open exceptions inside that window.
- Cost of a scope: two monotonic clock reads and, for an outermost scope, a handful of atomic
  stores; measured in the latency test (scopes compiled in, against the same build without them).

## Diagnostic ring and watchdog

`$XDG_RUNTIME_DIR/scottland/$WAYLAND_DISPLAY.loop` (64 KB, `loop-abi.hpp`), mapped and locked
in memory at `init()`. Every word is a lock-free 64-bit atomic, stored and loaded `seq_cst` by
every party; two rings with one producer each (A: the main thread, B: the watchdog), records with a
per-slot sequence, a record of the plugin copy (`instance`) that wrote it, and a shared sample of
what the main thread is doing. The names file beside it (`.loop.names`) belongs to one instance and
build; a reader uses names only when the header agrees before and after reading it.

- A callback over 8 ms writes a `slow` record (with its slowest inner scope).
- Former log lines are `note` records with up to four numbers (`NOTE` rows in `loop-table.def`).
- The watchdog thread wakes every 100 ms while Scottland is working (a work scope running or one
  ended within 1 s) and every 2 s otherwise. It records `stuck` when one scope has run for 100 ms
  (again at 1 s, 5 s, then every 10 s) and `unresponsive` when its heartbeat (one outstanding at a
  time, through an eventfd) is unanswered for 100 ms with no Scottland scope running: the loop is
  busy elsewhere (Wayfire, another plugin, the GPU driver).
- `scottland-loop-read` (built with the plugin; `scottland-ctl loop [--follow] [--json]`) is the
  only external reader. It never writes, so a slow or stopped reader cannot affect the compositor,
  and it works while the compositor is stuck: that is when it is needed. Its header reads the
  running scope as one coherent sample (the watchdog's sequence protocol) and says "unavailable"
  when it can't.
- The file continues in place only for the next plugin copy in the same compositor process and
  ABI. Another process on the display name (or another ABI) gets a new file renamed over the old
  one, so a reader still mapping the old inode keeps an intact old stream.
- If the file can't be created or mapped the ring and watchdog are off and only the in-memory
  counters remain; a failed `mlock` is recorded in the header. Remaining risk: without the lock, a
  record write can page-fault under memory pressure.
- One compositor wakeup every 2 s on a still desktop (the heartbeat).

## Reload

`scottland-reload` swaps the plugin in place; widgets stay (WG5). Two kinds of resource cross a
reload: duplicated launcher pidfds, and leases (Scottland's one disable on a widgetized app's root
node; the node's enabled state is a reference count shared with Wayfire's minimize and other
plugins). Each is adopted or returned exactly once, by number or window id, from a list whose
ownership is established outside the handover file.

- **The script** (Python) takes a lock and keeps one attempt record per compositor process. A fresh
  attempt writes the receipt (nonce, compositor pid and start time, model session) and the
  `.reloading` mark before anything that can make Wayfire swap the plugin, then sets the plugin
  list and waits up to 30 s for the new copy's acknowledgment or `fini()`'s failure record. Once a
  swap may have started, an error or a timeout is an unresolved outcome: receipt, handover and mark
  stay, and the next invocation decides by evidence: the attempt's own outcome, a later load, or
  the compositor's exit. Elapsed time is not evidence (Astra, implementation review 2): Wayfire's
  config backend debounces file changes and each further write restarts the delay, so a swap can be
  queued while the old copy keeps answering. While a Scottland copy answers: it is resolved if that
  copy consumed this receipt, or is a later load than the one that answered when the attempt began
  (it loaded without the receipt and imported nothing). If it is still that same load, within the
  attempt's timeout the request is refused (nothing changed); after it, the same attempt is driven
  again: its own plugin list is set again over IPC. A queued swap of that copy and this request
  load the same copy, and whichever comes first consumes the one receipt; nothing is abandoned and
  no new attempt starts. With no answer it changes nothing. With no Scottland copy loaded it loads
  the attempt's copy again with `wayfire/reload-plugins`. Either retry first checks that the
  attempt's receipt is still unconsumed and names it, its mark is in place and its copy exists;
  otherwise it reports the attempt unresolved and changes nothing.
- **The outgoing copy** hands over only if its `init()` completed and the mark exists. It publishes
  completely or not at all: duplicated handles, the file (envelope: format 2, an id, compositor pid
  and start time, session) and the environment list
  `SCOTTLAND_INTERNAL_HANDOVER=<id>;version=<model version>;fds=...;leases=...`, written after the
  model version it is bound to. Each duplicated handle has an owner from the moment it exists; on
  any failure, an allocation included, the widgets are torn down as in an ordinary unload and the
  duplicates closed. At most 256 widgets are handed over; the rest unload.
- **The incoming copy**, first thing in `init()`, owns what the environment list names, with
  fixed-capacity storage: parsing, validation and ownership allocate nothing, so an allocation
  failure can't drop a handle or a lease between the list and its owner. The list is
  bound to the model version the outgoing copy also leaves in the environment: an older build loaded
  in between (a rollback) changes it, and a stale list owns nothing. It then consumes the receipt
  (renamed to `.reload-importing`; only one naming this compositor process), and imports a file
  whose id matches the list, or a legacy file (the installed writer's) of the receipt's session.
  A handle needs an open pidfd that no other entry named and, if its process is alive, the recorded
  pid; a reaped launcher is owned-dead. Adopting a link takes each resource into its destination
  before the pending owner lets go of it; a failure while adopting one link returns its lease
  once, closes its handle and its card (as an ordinary unload would) and goes on with the rest.
  Whatever is not adopted is closed or returned once.
- **Failed `init()`**: `fini()` never hands over, returns what was owned and writes the failure
  record.
- Stated exceptions (not recoveries): a corrupted legacy file leaves its handles open and its apps
  hidden until the session restarts; a reload started by an old `scottland-reload` (no receipt)
  imports nothing, with the same effect. Neither occurs once both sides are this build.
- Pending launches at reload: a launch whose card hasn't mapped is not handed over (the app is
  restored, the launch stopped through the broker, which drains queued stops even after a reset);
  a card mapped before the broker's reply is carried with its unit and no process handle.
- Tests: `tests/reload-handover-test.py [--from INSTALLED_CHECKOUT]` (headless; the upgrade
  rehearsal starts from the installed build): lease balance by the plugin's `hidden` field and by
  captured pixels (each app paints its own color), descriptor counts and sentinels, the fault
  matrix (including allocation failures while exporting and adopting) and a queued config reload
  behind the debounce. `tests/fault-unit.sh`: allocation failures injected into acquisition,
  validation, export and adoption ordering, and into the worker's thread. Test sessions reload with
  `SCOTTLAND_TEST_RELOAD_DIR` so plugin copies and config stay out of the machine's real runtime
  directory.

## Phase 2: repeated work

| Item | What changed | Verified (the aarch64 test machine, headless, real input) |
|---|---|---|
| 2.1 Model publication | A change marks the model dirty; a timer publishes at most once per 8 ms, always with a trailing publication; replies that carry the model publish first, after their last change (DM2). | `tests/model-publish-test.py`: attention reply has the new state and the next version; the subscription's first slice is current; widget-traits' event carries the next version; an unchanged flush keeps the version; a 2 s drag published 216 times for 23,850 motion events |
| 2.2 `/proc` | Adoption gathers a process's facts once per dispatch (cgroup + up to 8 `stat` reads), shared by every link; each window stores whether its process runs as a widget, set when it maps. Hints, the switcher and drags do no `/proc` I/O. Stated limitation: a process moved into or out of a widget scope by something else is noticed when its window maps again. | `tests/mainloop-phase2-test.py`: no `/proc` read during an Alt hold, Window mode keys and a drag start (`loop-stats` `proc_reads`) |
| 2.3 Palette | The palette file is read at `init()` and once when it changes (its inotify watch, which landed on main after the design and replaces the helper's IPC push); hints rebuild their colors from the copy in memory. | no palette read during interaction; a file change is read once |
| 2.4 Pointer path | Visual proximity runs once per frame (the output's pre-render hook) and before a button is routed; hit testing stays per event and unchanged (A5). The hit test's filtered sources are rebuilt once per source or settings change. | 9-22 proximity runs for 600 pointer events; A5: a quarter-scale halo with thickness 4 is grabbed 10 px outside its edge and not 14 px (before and after) |
| 2.5 Caches | App-id patterns compiled once and their results memoized (bounding nothing for a pathological pattern: D5). Not done: the per-output goo source cache (measured 0.04-0.1 ms per frame; its invalidators are many), left for review. | key-layer and touch suites |
| 2.6 | One arrow repeat per due tick (WK17); the layout once per option batch; the settings app sends one batch per tick with only changed values (S3); the Hyprland shim watches only the seven event kinds it translates. | one layout for a five-option batch |
| 2.7 | The switcher's title: at most 256 codepoints and a binary search for the fitting prefix (at most 9 measurements); badge rasters capped at 512x512; `mkdir(2)` instead of `system("mkdir -p")`. | a 4,096-character title: switcher update 0.37 ms |
| ML8 | The hint font is loaded at `init()`. | first Window mode entry: badge raster 0.87 ms (13.9 ms on Phase 1); entry 5.5 ms first and 2.5 ms after at 6 windows (41-53 ms first on Phase 1) |

## Phase 4: the shrink worker

`core/plugin/src/pure/`: a static library with no Wayfire, wlroots or GL in it. `worker_t` runs
jobs in steps of at most one allowance of work units, charged before each unit operation, with
cancellation checked at every charge; each consumer owns a lane (one pending, one running, one
finished slot) with a ticket (every submit and cancel), an epoch (the consumer's invalidating
events) and a policy (`exact`: a newer ticket or epoch cancels; `latest_completed`: only a newer
epoch). Results come back through an eventfd on the main loop, at most 1 ms of deliveries per
dispatch, each checked against the consumer's acceptance rule. A failed eventfd write marks the
worker broken; the heartbeat handler and every submit observe it. The worker's own bookkeeping
allocates nothing on its thread (fixed-capacity lane copies; finished results are held in place),
so only a job's step or result can fail, and that fails only the job; anything else that escapes
its loop marks the worker broken, and its thread still ends and is joined. The thread blocks every
signal and runs only the library's code, its mutex, condition variable and eventfd. `fini()` stops it
first (the watchdog last): stop, notify, join, then destroy every pending, running and finished
job and result on the main thread.

The goo's breathing shrink (GO19/GO20) is its first job: when the goo falls asleep, the goo node
snapshots its sources (shapes are shared and immutable), settings, time, band rectangles and output
incarnation and submits, if the snapshot is within its limits (checked before copying and again at
submit, without allocating): at most 8 MB of allocated storage with every distinct shape's pixels
counted once, at most 256 sources, and at most 4,096 rectangles, which bounds the job's output and
result (1 MB). Past any limit the goo keeps its loose strips; any change of sources, shapes, settings, strips, a wake or a new output
mode bumps the lane's epoch. A result is installed only if its ticket and epoch are current, the
goo still sleeps and the output is the same incarnation. A job capped at the whole-job limit
tightens only the rectangles it finished; none finished means no change. Work units (one per
density term) are calibrated by `tests/worker-unit.sh`: 29-36 ns each on the aarch64 test machine, so a step of
60,000 units is about 2 ms and the cap of 9,000,000 about 300 ms; the slowest single operation (a
256-source density call) is 9-10 µs at p99.

Verified (the aarch64 test machine): `tests/worker-unit.sh` (ThreadSanitizer, no suppressions: forced interleavings
of submit, finish, deliver, cancel, epoch bump, close and stop for both policies; `broken` without
the eventfd; 16-lane limit; stop with running, pending and undelivered jobs; identical results at
a 1-unit and an unlimited allowance; caps at the first unit, mid-row and the last unit; equality
with GO19's main-thread algorithm; the snapshot limits at and past their boundaries with distinct
shapes; stop with maximum-size jobs pending and running and maximum-rectangle results undelivered
on all 16 lanes, every shape released) and `tests/shrink-worker-test.py` (settling through the
worker with no shrink step on the main loop; the tight region renders the same pixels as the loose
bands; a job held running by a test switch with a newer one observed pending, cancelled on
release and never installed; an output removed under a running job and a recreated one; reload
with a job running: one worker thread, descriptors back to baseline; worker thread and eventfd
failures leave the loose strips).

## Phase 3: no GPU wait

- **Hit test.** `goo_handle` uses the resting outline (no wave term) and never calls
  `goo_sample_at`, which remains only as the test probe in `goo-state` (GO5, Mike 2026-10-03).
  A5's minimum target and the rest of the predicate are unchanged.
- **Energy.** Every 30th simulation step issues the reduction into one of four pixel buffers per
  renderer with a fence and a tag (step, invalidation counter, generation, renderer size), then
  `glFlush()`; with every slot busy it skips. The read sets the pixel-pack state it depends on
  (alignment, row length, skipped pixels and rows) and restores Wayfire's. Readings are collected
  without waiting (`glClientWaitSync` with a zero timeout) inside the render pass and otherwise by
  one 16 ms timer, armed only while a reading is in flight, starting at a rotating output. All of
  them share one allowance of two slots per main-loop dispatch, refilled by an idle callback once
  the dispatch's events are handled. A reading applies only if no impulse, source or settings
  change or wake happened since it was issued (the invalidation counter), it is of the current
  generation (a resize or a new output mode, scale or transform starts one and retires the slots
  in flight), the size is the same and it is newer than the last applied one. A GL error on the
  buffer allocation or the read, a failed wait or map, an unmap reporting lost contents, a missing
  fence, or a reading unsignalled after 1 s retires the slot and puts the renderer on the timed
  fallback; nothing is applied, so a stale buffer is never read as zero energy.
- **Fallback.** GLES 2, any readback failure, or the `goo-state` test switch: the simulation
  sleeps 6 s after the last change (GO10, Mike 2026-10-03), until the renderer is re-created.

Measured (the aarch64 test machine, Asahi): collection at most 0.03-0.05 ms; issue 0.7 ms at 12 windows and
2.1 ms at 30, of which the fence and flush cost 0.001 ms and the reduction passes' submission
0.6-1.6 ms. The design's issue target (0.5 ms) is **not met on the aarch64 test machine**, so Phase 3 stays open
with that number in the exception table; the x86 test machine (busy with a VM build) and an Intel test host
are not measured yet. `tests/goo-readback-test.py`: no GPU read on pointer motion; the goo
sleeps on asynchronous readings; nothing stays in flight once asleep; readings held in flight
(test switch) across a change, a resize A→B→A or a reload don't apply; a full ring is collected
oldest first at most two slots per dispatch, also with two outputs; an output removed with
readings in flight; a failed read over a known prior buffer value (Astra's pack-state probe), a
failed wait, map or unmap each apply nothing and sleep at about 6 s, observed in the compositor's
CPU time; legal incoming pack state is normalized and readings still apply.

## Window mode entry (ML8)

Attribution on the aarch64 test machine (aarch64, Asahi GPU, 10 windows, Phase 1 scopes): the first Alt hold of a
session spends 40-53 ms in `begin_window_keys`, of which the first badge raster (`hint_raster`,
cold font) is 25-38 ms and the bounded solve (`hint_solve`) 1.9 ms; a later entry spends 7-20 ms,
about 1 ms per window creating badges, outlines and dyes (`hint_visual`). The acceptance target is
that entry, cold or warm, stays inside ML1 or is listed with its number. Done: the font is loaded at
`init()` (Phase 2: the first badge raster 13.9 -> 0.9 ms); hit tests no longer read back from the
GPU when badges change the scene (Phase 3); new badges and outlines enter the overlay in one scene
update per tick instead of one per node plus two per circle per outline (each update rebuilt every
window's render instances: 42,720 rebuilds in one entry at 30 windows). Entry is now about 15 ms
at 10 windows and 47 ms at 30 on the aarch64 test machine (open): what remains is per-window badge, outline and
offset work (about 1.5 ms per window) and the goo's own render on this GPU. Next: create badges
over several ticks within a budget, or the raster helper (D4) and the solve worker (D1).

## Exceptions

Measured numbers, with the build and host, are in "Baselines". An entry leaves only when its phase
closes or its owner's design ships. Ceilings for the latency test are in
`tests/mainloop-exceptions.json`.

Measured on the aarch64 test machine (aarch64, Asahi GPU; the x86 test machine was busy), optimized build with the Phase 1
scopes, headless 2560x1600, real stipc input, 2026-10-04. Maxima over every scenario of
`tests/mainloop-latency-test.sh`. Nested scopes are listed with the entry points that contain them,
so one cost can appear on several rows. Also listed without a scope of its own: `core_run` at the
sites the broker does not cover (focus hook, pop sound, key-release commands, broker start, stop
fallback; 6-13 ms each on the x86 test machine) and the `fini()` wait loop (up to 0.5 s) (D3); a font miss on a
font family other than the one warmed at `init()` (D4); pathological user regexes (D5);
`place_rectangle` at 60+ obstacles (D6); `init()`/`fini()` file I/O on reload.

| Scope | 10 windows | 30 windows (Mike's goo) | What | Leaves in |
|---|---|---|---|---|
| `goo_sample_at` | 25.3 ms | 65.3 ms | pointer hit test reads one pixel of wave height back from the GPU; runs inside frame_find_node_at, and inside every scene change that refocuses the pointer | Phase 3 |
| `frame_find_node_at` | 25.3 ms | 65.3 ms | the halo hit test (contains goo_sample_at) | Phase 3 |
| `goo_energy_readback` | 23.6 ms | 44 ms | every 30th simulation step waits for a 1-pixel energy read | Phase 3 |
| `goo_render` | 28.8 ms | 58.3 ms | simulation step (contains goo_energy_readback); the first frame of a newly loaded copy also builds its renderer: 200-400 ms on the aarch64 test machine after a reload | Phase 3 |
| `goo_shape_update` | 14.3 ms | 31.1 ms | widget alpha-shape readbacks | D2 |
| `frame_render` | 16.8 ms | 31.1 ms | window render instance (contains goo_shape_update) | D2 |
| `hints_tick` | 18.3 ms | 287.2 ms | Window mode tick at 30 windows: each badge, outline or offset changes the scene and Wayfire's pointer refocus runs the hit test readback; the solve itself stays at 2.2 ms | Phase 3, ML8, D1 |
| `step_hints` | 40.9 ms | 287.2 ms | same work as hints_tick, also run from Window mode entry | Phase 3, ML8, D1 |
| `alt_hold` | 41.2 ms | 91.7 ms | Window mode entry: first badge raster (cold font) and badge creation | ML8 (Phase 2 warm-up), Phase 3 |
| `begin_window_keys` | 41.2 ms | 91.7 ms | Window mode entry (inside alt_hold) | ML8 (Phase 2 warm-up), Phase 3 |
| `hint_visual` | 28.4 ms | 80 ms | one window's badge, outline and offset step (scene updates trigger hit-test readbacks) | Phase 3, ML8 |
| `hint_raster` | 27.9 ms | 19.5 ms | badge text raster: first use of a font | ML8 (Phase 2 warm-up), D4 |
| `on_window_key` | 0.8 ms | 85.4 ms | a Window mode key that changes hints | Phase 3, ML8 |
| `keyboard_tick` | 11.2 ms | 67.3 ms | keyboard motion; each move refocuses the pointer (hit-test readback) | Phase 3 |
| `on_motion` | 7.1 ms | 22.9 ms | pointer motion bookkeeping (contains hit tests) | Phase 3, Phase 2.4 |
| `track_pointer` | 7.1 ms | 22.9 ms | visual proximity per pointer event | Phase 3, Phase 2.4 |
| `widget_transition_tick` | 21.5 ms | 51.5 ms | window/widget transition animation tick | open: attribution (Phase 1 item) |
| `live_drag_render` | 8 ms | 30.9 ms | the dragged subtree's texture render | open: attribution (Phase 1 item) |
| `option_layout` | 19.2 ms | 46.9 ms | a layout option callback (apply_all per option) | Phase 2.6 |
| `publish_model` | 12.3 ms | 2.2 ms | model publication (2,806 per settings-slider run before coalescing) | Phase 2.1 |
| `goo_prepare` | 9.8 ms | 15.4 ms | per-frame goo sources and bands | Phase 2.5 (sources cache); bands stay exact |
| `goo_settle_tick` | 6.1 ms | 12.4 ms | GO19 breathing shrink slice (2.5 ms on the x86 test machine, slower on the aarch64 test machine) | Phase 4: closed (the scope no longer exists) |
| `tighten_breathing` | 6.1 ms | 12.4 ms | inside goo_settle_tick | Phase 4: closed |
| `on_mapped` | 4.4 ms | 12.3 ms | widget adoption procfs gather and placement | Phase 2.2, D6 |
| `on_move` | 4.6 ms | 10.4 ms | drag start: /proc reads and publishes | Phase 2.1, 2.2 |
| `live_drag_pointer_button` | 8.1 ms | 8.8 ms | drop handling | Phase 2.1, 2.2 |
| `on_drag_done` | 5.8 ms | 6.2 ms | drop handling | Phase 2.1 |
| `on_drag_output` | 2.3 ms | 5.3 ms | drag output change | Phase 2.1 |
| `window_entries` | 18.1 ms | 2.5 ms | window list for hints: /proc reads per window | Phase 2.2 |
| `goo_tick` | 9.1 ms | 2.8 ms | goo tick (damage; contains readbacks when it renders) | Phase 3 |
| `transition_tick` | 7.2 ms | 0.2 ms | zone transition tick (publishes) | Phase 2.1 |
| `on_focus` | 0.6 ms | 4.3 ms | focus change (publishes) | Phase 2.1 |
| `held_above_timer` | 0.2 ms | 3.5 ms | drop chain timer | Phase 2.1 |
| `attention_method` | 1.8 ms | 3.4 ms | attention IPC | Phase 2.1 |
| `hint_render` | 3.3 ms | 3.3 ms | badge texture upload on first draw | ML8 |
| `hints_state` | 1 ms | 3.1 ms | hints IPC (re-reads /proc) | Phase 2.2 |
| `frame_gen_render_instances` | 0.7 ms | 3 ms | render instance creation on scene changes | open |
| `goo_option` | 3.5 ms | 0.8 ms | goo option callback | Phase 2.6 |
| `live_drag_pointer_motion` | 3.4 ms | 2.8 ms | drag motion (publishes) | Phase 2.1 |
| `on_drag_motion` | 3.4 ms | 2.7 ms | drag motion (publishes) | Phase 2.1 |
| `goo_schedule` | 2.6 ms | 1 ms | goo damage scheduling | open |
| `on_geometry` | 1.3 ms | 2.3 ms | geometry change (publishes) | Phase 2.1 |
| `hint_solve` | 3.6 ms | 2.2 ms | Luna's bounded avoidance solve | D1 (Phase 0: bounded at 2 ms + overshoot) |

**Status after Phases 2-4** (the aarch64 test machine, `39e9580` and the ML8 batching):

| Scope | Now | |
|---|---|---|
| `goo_sample_at` in input | gone from every input path | closed (Phase 3) |
| `goo_energy_readback` (waiting read) | gone; replaced by `goo_energy_issue` 0.7-2.3 ms (reduction submission) and `goo_energy_collect` <= 0.06 ms | issue open (target 0.5 ms) |
| `goo_settle_tick` / `tighten_breathing` | gone: on the worker; install 0.06 ms | closed (Phase 4) |
| `hint_raster` (cold) | 0.6-0.9 ms | closed for the shipped font (ML8); other families D4 |
| `hints_tick` at 30 windows | 28-32 ms (from 287) | open (ML8) |
| `alt_hold` / `begin_window_keys` | 15 ms at 10 windows, 47 at 30 | open (ML8) |
| `option_layout`, `publish_model` | out of the worst scopes; publish <= 0.7 ms | closed (Phase 2) |
| `goo_render` | 6-50 ms: the simulation's GPU submission on Asahi | open: not in this design's scope; needs a GPU profile |
| `goo_shape_update`, `frame_render` | 9-30 ms | D2 |
| `widget_transition_tick` | 13-43 ms at 30 windows | open: attribution |
| `goo_prepare` | 4-35 ms at 30 windows (settings slider) | open (Phase 2.5 source cache not done) |

Not over 2 ms in any scenario: widget and morph captures (`widget_capture`, the retained-pixel
path), the wallpaper capture, the broker reply. That is not closure: the uncached capture fallback
and `core_run` were not exercised, so both stay open exceptions (Astra, implementation review 7).

Phase 0 (Luna's bounded avoidance, on main from `b6955db` until the peek-strip engine replaced it;
history: since the join with main its per-output slice is `peek_step`): the solve itself was at
most 2.2 ms at 30 windows and 3.6 ms at 10 (first entry). Window mode as a whole is not: its ticks
reach 287 ms at 30 windows on the aarch64 test machine, and the cost is not the solve. Each badge, outline or offset
it creates changes the scene, Wayfire refocuses the pointer, and the halo hit test waits for a GPU
readback (`goo_sample_at`, up to 65 ms each). Phase 3 removes that readback from the hit test; the
first-entry font miss leaves with the warm-up at `init()` (Phase 2).

## Baselines

Ping lateness p99/max in ms per scenario (the probe pings Wayfire IPC at 1 kHz; a late ping means
the main loop was busy). "main" is `5a2fb5d` without scopes, "Phase 1" this build; same host, one
run each, host load 3-6 from other agents. The differences between the columns are within the
run-to-run spread of this shared host; the scopes' direct cost is 0.1 µs per scope pair
(`tests/loop-unit.sh`, measured), against callbacks of 1-300 ms. The spread of the host itself: a
second run of main at 10 windows (load 1.6) measured pointer-halo 58.6/71.3 ms against 12.9/20.6 in
the first, window-mode-entry 64.6/85.4 against 30.7/51.6, settings-slider 33.3/64.9 against
36.5/57.2. (The other repeat runs lost their ping channel to a harness bug, since fixed.)

| Scenario | main 10 | Phase 1 10 | main 30 | Phase 1 30 |
|---|---|---|---|---|
| idle | 2.07/4.00 | 2.61/5.57 | 2.11/3.20 | 2.37/3.56 |
| window-mode-entry | 30.73/51.55 | 46.57/66.62 | 257.75/278.60 | 284.05/301.67 |
| ipc-queries | 8.97/8.97 | 7.05/7.05 | 12.39/12.83 | 23.95/27.28 |
| pointer-sweep | 10.22/21.97 | 14.27/24.19 | 158.31/299.35 | 170.29/251.16 |
| pointer-halo | 12.91/20.61 | 42.15/73.29 | 71.83/118.60 | 123.88/248.61 |
| drag | 6.22/10.78 | 10.89/22.94 | 108.70/122.37 | 92.49/101.81 |
| after-drag-settle | 6.40/11.89 | 7.66/17.53 | 20.85/30.78 | 23.46/44.11 |
| window-mode | 22.42/33.48 | 12.92/22.74 | 186.43/221.48 | 202.04/234.43 |
| window-mode-arrows | 22.22/38.47 | 14.32/24.57 | 234.84/293.27 | 136.08/189.95 |
| always-avoid-drag | 15.81/33.69 | 29.55/56.77 | 137.62/220.70 | 74.26/108.64 |
| map-unmap | 8.44/22.79 | 12.10/23.13 | 28.30/49.42 | 30.83/57.49 |
| long-title | 8.30/16.21 | 13.92/24.40 | 507.77/535.38 | 194.42/225.77 |
| attention-breath-sleep | 4.83/14.60 | 8.31/28.57 | 23.55/46.16 | 17.80/31.33 |
| slow-subscriber | 9.55/15.61 | 9.49/16.68 | 31.46/42.37 | 28.12/36.45 |
| widgetize | 14.69/18.66 | 16.88/32.71 | 48.61/58.98 | 73.24/93.19 |
| widgets-8 | 20.43/36.33 | 27.81/45.03 | 92.19/135.38 | 81.18/128.42 |
| widget-attention-sleep | 10.87/26.54 | 14.41/31.17 | 84.09/185.76 | 44.40/76.36 |
| settings-slider | 36.53/57.17 | 61.58/93.39 | 90.11/115.98 | 65.58/113.31 |
| scale-change | 64.04/80.36 | 91.61/121.48 | 87.41/157.42 | 126.10/144.67 |


## Results after Phases 1-4 (the aarch64 test machine)

Ping lateness p99/max in ms (pings to Wayfire IPC at 1 kHz with real input; a late ping means
the main loop was busy). Same host, one run per column, host load 1-6 from other agents' tests;
the run-to-run spread of one build is shown under "Baselines". "Phases 2+3+4" is `39e9580`.

10 windows
| Scenario | main | Phase 2 | Phases 2+3+4 |
|---|---|---|---|
| idle | 2.07/4.00 | 2.70/8.17 | 0.71/2.89 |
| window-mode-entry | 30.73/51.55 | 41.16/60.99 | 52.36/71.94 |
| ipc-queries | 8.97/8.97 | 5.24/5.24 | 10.18/11.08 |
| pointer-sweep | 10.22/21.97 | 14.63/25.85 | 9.63/13.39 |
| pointer-halo | 12.91/20.61 | 25.32/51.13 | 9.38/14.35 |
| drag | 6.22/10.78 | 12.38/19.72 | 11.50/15.46 |
| after-drag-settle | 6.40/11.89 | 10.95/20.22 | 9.06/16.67 |
| window-mode | 22.42/33.48 | 14.76/40.73 | 14.04/39.72 |
| window-mode-arrows | 22.22/38.47 | 17.29/30.35 | 13.84/31.54 |
| always-avoid-drag | 15.81/33.69 | 47.87/85.81 | 31.39/67.57 |
| map-unmap | 8.44/22.79 | 13.95/21.64 | 11.75/16.20 |
| long-title | 8.30/16.21 | 18.38/33.29 | 16.19/25.39 |
| attention-breath-sleep | 4.83/14.60 | 7.54/29.60 | 7.91/17.53 |
| slow-subscriber | 9.55/15.61 | 14.61/18.73 | 12.27/17.11 |
| widgetize | 14.69/18.66 | 14.76/21.28 | 8.75/12.74 |
| widgets-8 | 20.43/36.33 | 19.77/34.89 | 10.69/16.41 |
| widget-attention-sleep | 10.87/26.54 | 10.33/26.69 | 9.85/26.67 |
| settings-slider | 36.53/57.17 | 42.15/70.95 | 42.89/77.35 |
| scale-change | 64.04/80.36 | 71.80/95.60 | 84.87/101.26 |

30 windows (Mike goo values)
| Scenario | main | Phase 2 | Phases 2+3+4 |
|---|---|---|---|
| idle | 2.11/3.20 | 2.45/3.27 | 1.97/3.53 |
| window-mode-entry | 257.75/278.60 | 265.97/283.61 | 200.10/220.88 |
| ipc-queries | 12.39/12.83 | 23.73/26.44 | 14.71/16.76 |
| pointer-sweep | 158.31/299.35 | 151.50/209.55 | 14.03/24.30 |
| pointer-halo | 71.83/118.60 | 101.20/179.96 | 18.09/25.47 |
| drag | 108.70/122.37 | 73.88/88.68 | 13.98/25.12 |
| after-drag-settle | 20.85/30.78 | 21.39/33.53 | 18.24/22.70 |
| window-mode | 186.43/221.48 | 220.94/253.30 | 228.61/259.98 |
| window-mode-arrows | 234.84/293.27 | 222.19/275.04 | 169.46/222.34 |
| always-avoid-drag | 137.62/220.70 | 67.03/87.97 | 63.22/89.25 |
| map-unmap | 28.30/49.42 | 28.22/57.47 | 22.19/28.70 |
| long-title | 507.77/535.38 | 204.83/237.18 | 211.95/245.31 |
| attention-breath-sleep | 23.55/46.16 | 18.23/32.11 | 16.90/23.32 |
| slow-subscriber | 31.46/42.37 | 21.48/28.32 | 15.72/25.80 |
| widgetize | 48.61/58.98 | 46.68/56.99 | 20.62/26.74 |
| widgets-8 | 92.19/135.38 | 69.43/107.13 | 31.65/49.48 |
| widget-attention-sleep | 84.09/185.76 | 40.63/64.33 | 34.65/45.77 |
| settings-slider | 90.11/115.98 | 65.39/105.13 | 60.53/73.73 |
| scale-change | 87.41/157.42 | 115.44/163.29 | 73.42/98.95 |

Window mode with the ML8 overlay batching (`331a54b`), same host:

| Scenario | 10 windows | 30 windows |
|---|---|---|
| window-mode-entry | 29.62/51.17 (entry callback 14.8 ms) | 67.56/88.55 (entry 46.6 ms) |
| window-mode | 10.85/34.37 | 58.08/70.70 (ticks 28-32 ms, from 207) |
| window-mode-arrows | 15.54/31.54 | 60.34/66.65 |
| long-title | - | 57.58/69.89 |

What changed for the user (measured): at 30 windows pointer motion over windows and halos no
longer stalls on the GPU (pointer-sweep p99 158 -> 14 ms, pointer reply p99 218 -> 13 ms), drags
and widget conversions stay under about 25 ms, and Window mode's ticks drop from about 200 ms
to about 30 ms. What remains over the targets is in the exception table: the goo's simulation
step on this GPU (goo_render 10-50 ms on the aarch64 test machine's Asahi driver), widget shape readbacks (D2),
Window mode entry (ML8: per-window badge work), the settings slider (Wayfire's config reload).

## Joined with main (2026-10-05)

The branch was joined onto main at `2fcd22a` (the peek-strip engine, spread and solo, pairing, the
Super+M widget modes, GO24 watercolor, GO26 breathing keys). What carried over:

- Scopes on main's new entry points (spread and audition timers, the touchpad hold, rail slides and
  dwell, hint hold, deferred pairing, the water tick, widget-mode and spread-state IPC) and on the
  peek-strip engine: Window mode entry (`alt_hold`, `begin_window_keys`), each tick (`hints_tick`)
  and each output's slice (`peek_step`) are timed separately.
- Coalesced publication keeps main's spread-commit batch: nothing, not even a barrier, publishes a
  batch's partial state; the widget-mode reply follows a barrier.
- Main's waiting energy read is replaced by Phase 3's asynchronous one; GO24 watercolor still
  decides sleep on wave energy, also on the timed fallback. Main's main-thread shrink slices are
  replaced by the worker (GO26's quarter-second cap is the job's whole-job cap).
- Main's new diagnostics (Super+M taps, holds and edges, the breathing path, rail steps, the
  three-finger hold) are ring notes; the tests that read them use `scottland-loop-read`.
- The handover carries main's widget mode; adopted widgets snap into their rail place.
- Spread solves stay in main's measured event-loop slices (`spread_tick`); moving that job to the
  worker is not part of this branch.

Measured after the join (the aarch64 test machine, Asahi GPU, headless with real stipc input, host
load 0.7-1.1; `tests/mainloop-latency-test.sh`, rebuilt into `tests/mainloop-exceptions.json` by
`tests/mainloop-exceptions-update.py`: 32 open exceptions, each a measured residual, 16 closed as
history, `core_run` and the uncached capture open and unmeasured):

| Scenario | ping p99 / max, 10 windows | ping p99 / max, 30 windows |
|---|---|---|
| idle | 0.12 / 2.8 ms | 0.15 / 2.8 ms |
| pointer sweep | 9.4 / 13 ms | 16 / 33 ms |
| pointer over halos | 8.8 / 13 ms | 19 / 50 ms |
| drag | 11 / 17 ms | 14 / 24 ms |
| Window mode entry | 41 / 62 ms | 118 / 138 ms |
| settings slider | 54 / 89 ms | 57 / 84 ms |

P8 is not met. With every open exception inside its ceiling, 11 of 20 scenarios at 10 windows and
18 of 20 at 30 still have pings over 10 ms at p99 that no single scope explains: attribution
unknown. The largest residuals are Window mode entry (`alt_hold` 73 ms at 30 windows, mostly
`step_hints` on the peek-strip engine's first pass and badge creation), the goo's render on this
GPU (`goo_render` up to 38 ms), widget shape readbacks (D2, 27 ms) and render instance rebuilds
(`frame_gen_render_instances` 25 ms at 30 windows). The energy issue (`goo_energy_issue` 2.9-6.3
ms) stays over its 0.5 ms target. Not measured: the x86 test machine and an Intel GPU.

## Tests

- `tests/mainloop-latency-test.sh LABEL [--windows N] [--mike] [--widgets] [--outputs 2] [--gate]`:
  the scenarios of the design at 10 and 30 windows, with paced, pipelined input (pings at 1 kHz,
  pointer at 500 Hz) and per-scenario scope maxima, ML2 and host load. Every number is labelled
  measured, ceiling or open exception.
- `tests/loop-watchdog-test.py`: stuck and unresponsive separately, idle rounds 2 s apart, the
  external reader during a stuck scope and across a reload, failure injection.
- `tests/reload-handover-test.py`: the reload matrix above.
- `tests/widget-spawn-test.py`: the broker's disconnect cases.
