# Tests to-do

What the test cleanup of 2026-10-04 removed or left weak, and what each area still needs. The
findings come from the test audit of 2026-10-04 (reviewed main `43be4bd`, plus the branches that
have since landed); "Top N" is the audit's ranked problem list, "suite row" its per-suite table.
Line numbers in the audit refer to that snapshot. The standard these fixes should meet is
"Testing standard" in [AGENTS.md](../AGENTS.md).

Nothing here was rewritten in the cleanup: ineffective checks were deleted, and the work below is
what remains to give those behaviors a real test.

Priority: **Fix first** (can silently test the wrong session or hang every suite), **High** (a
behavior has no real test, or a check hides failures), **Medium** (weak oracle or timing-sensitive
verdict), **Low** (cost, duplication, diagnostics).

## Fix first: shared helpers

Every suite runs through these; an excellent assertion is worthless against the wrong session.

| Problem | What to do | Finding |
|---|---|---|
| `tests/wfipc.py` picks the newest `wayfire-*.socket` when `WAYFIRE_SOCKET` is unset, so a test can drive someone else's session | Require the session's recorded environment (or an explicit socket); fail without it | Top 1; harness table |
| `tests/wfipc.py` has no timeout, and its `recv` loops never see EOF: a compositor crash mid-reply hangs the caller forever | Bounded reads with a timeout; treat an empty `recv` as an error | Top 1 |
| `tests/headless.sh start` trusts any live PID in `$dir/pid` as "already running", then the suite attaches to it; otherwise it `rm -rf`s the directory it was given | Ownership record (run token, PID start time, compositor command, build ID) checked before attach or stop; refuse directories outside the checkout's `build/` | Top 1 |
| `tests/headless.sh` silently falls back from the checkout's hooks to the dev install, then the package | Verification runs use checkout-built helpers only, or fail | Top 1 |
| `tests/state-regressions-test.sh`, `tests/upgrade-test.sh` compute a default directory that differs from `headless.sh`'s and don't export it | Pass one owned directory through | Top 1; suite rows |
| `hint-outline-test`, `translucency-test` guard against the live session by guessing display names (`wayland-1`) | Replace with the ownership check above | Top 1; suite rows |
| Cleanup is installed after startup in several wrappers, and `drag-test.py`/`resize-test.py` can leave a button or key held after an exception | Register cleanup before starting anything; release held input in `finally` | Top 15; harness table |
| `tests/deploy.sh` dev-installs into a shared checkout by default and hardcodes its install directory; `tests/nested.sh` shares one runtime directory and hand-names sockets | Make test-only deployment the default path for tests; keep `nested.sh` manual only | harness table |

## Deleted in the cleanup: behavior that still needs a real test

| Removed | Behavior still needing a test | Finding | Priority |
|---|---|---|---|
| `tests/present-test.sh` (whole suite, 6 checks). It required presented windows to land in the screen middle, which L30 no longer promises | L30 through `scottland/present`: a center-zone window stays put, raised and focused; a side window goes to its remembered center spot (WP2) or else the nearest least-overlapping full-size spot at 100% (WP5), separate fixtures for each; a widget opens as a WG17 card click; existing windows don't move; an unknown id is an error. Clean up its own clients. L30 currently has no automated test | Top 12; suite row | High |
| `tests/go23-default-path-test.py` (whole file, 10 source-string asserts) | GO23: at strength 1 the goo renders exactly as the original path. Numeric default-compatibility cases plus rendered equality against a reference capture at strength 1 | Top 10; goo-release-review2 row | High |
| `tests/settings-coverage-test.py`: 24 source-text asserts removed, 2 trimmed (QML snippets, help prose, element ids, function names, the `CoastGraph` count, row literals matching metadata). Option membership and metadata checks stay | Open Settings and check at runtime that each Goo row shows its metadata title, hint, default and range verbatim (GO9, GO14, GO15, GO23); the attention-color choices (GO22) and the unfocused-edge tone row per scheme; that `window_mode_tint`, `widget_bounce` and both coast graphs are reachable and change their option | Top 10; suite row | Medium |
| `tests/goo-shader-variants-test.sh`: 37 checks that grepped preprocessed shader text (the 39 compile checks stay) | Each variant (fast, cached, refraction, intrinsic, cache_both) renders what the general path renders: specialization equivalence by pixels, as `goo-exact-test` does for caches | Top 10; goo-release-review2 row | Low |
| `tests/state-model-test.py`: "headless scene renders" was `check()` with no condition (the screenshot is still saved) | After the randomized inputs, the scene shows the windows the model says are mapped: pixel check against known window colors | Top 3; suite row | Medium |
| `tests/solar-test.py`: "Geoclue has priority" was `check(..., True)` (the mock that raises on an IP lookup still guards it) | Assert the Geoclue location is what reached `apply` | suite row | Low |
| Wall-clock gates: `windowing-unit` (50-window occlusion pass < 2 ms, whole block), `hint-outline-test` (occlusion pass < 2 ms), `hint-avoidance-hang-test` (solve < 4 ms; now printed), `peek-strip-test` G (every refresh ≤ 2 ms; now printed) | P8 as a performance lane: occlusion pass, avoidance refresh and slice cost under a real drag, with machine, renderer, build type, load, warm-up and sample count recorded. The functional checks that settled drags stop solving and IPC stays responsive remain | Top 5; suite rows | Medium |
| `tests/peek-unit.cpp` slice calibration block (units per ms) | A benchmark that measures units/ms and reports what `peek_slice_units` costs on the machine, outside the unit run | peek-unit row | Low |
| `tests/rail-make-room-unit.cpp`: the fuzz check required exactly 600,000 solves (its own loop count) | Nothing: the properties stay checked and the count is still printed as solves | rail-pause row; Top 14 | — |

## Weak checks still in place

### High

- **`widget-race-test` retries the hint press up to three times** until the widget is gone, so a
  missed first press passes. One action, one acknowledged outcome; fixture barriers for pre-map,
  first commit, mapped, cancel and destroy; a separate bounded soak that reports first-attempt
  success. (Top 8)
- **`settings-help-test` can continue on a dead panel or the wrong tab:** `snapshot()` returns
  cached state when the panel has died, `tab()` prints `TAB MISS` and carries on, "maps" checks
  process liveness, negative hint checks take the first capture. Fail on panel death and tab
  misses; wait for a fresh snapshot and a frame with/without the hint; split tabs into
  independently initialized cases. (Top 7)
- **`window-double-tap-test` doesn't know which interval it delivered:** sender-side sleeps can
  turn a 250 ms double tap into 319 ms (seen under load) and the case still expects widgetization;
  the focused-widget timeout exits before `results.json` is written. Exact boundaries stay in
  `windowing-unit`; GUI cases sit well inside/outside the window and record received event times;
  write results in `finally`. (Top 6)
- **`widget-morph-test` measures its poller as if it were the animation:** sample counts, last
  sampled width vs settled width (a skipped frame reads as a snap), polling timestamps as duration;
  "entry reload: no snapshots retained" after a fixed 1 s sleep without logging the count. Easing
  math with supplied timestamps; frame-tagged pixels for integration; bounded lifecycle wait with a
  state dump. (Top 4)
- **`translucency-test` checks only the published opacity**; a shader that ignores it passes.
  Check the foreground/background pixel mix. (Top 3)

### Medium

- **Sampled-motion verdicts** that depend on IPC polling catching the right frame: `cycle-overshoot`
  (4 ms sampling for a spring peak), `drag-coast` (12% speed, 4–5 px trajectory, < 17 px steps),
  `hint-avoidance-animation` (6 ms polling thread; its exceptions don't reach the result),
  `hint-pop` (growth/overshoot windows), `live-drag` (`unique >= 8` of 12 captures),
  `zone-pin` (sampled landings), `widget-conversion` (capture 0.24 s after observing),
  `widget-hints` and `widget-peek-options` (peek expiry `< 6.2 s`), `inertia` (travel assumes
  scheduled key intervals), `rail-make-room-pause` (first movement at 250–470 ms, 1150 px/s sampled
  speed). Deterministic math in units; presented-frame timestamps in integration. (suite rows; Top 4)
- **`peek-strip-test`:** the "matches a fresh solve" check compares offset lengths, not vectors
  (opposite equal offsets pass); the no-room hint is verified by flags. (Top 13; window-peek-strip row)
- **`pairing-test`:** `expected()` mirrors `fit_pair`, so shared mistakes pass; `settle()` returns
  silently on timeout. Independent order/size/gap/visibility properties and hand-derived fixtures.
  (Top 13; pairing-wk36 row)
- **`build-config-test`:** bare `wait` loses writer failures, and only the final file is read, so
  the transient truncation it advertises can be missed. Check every exit status; a concurrent
  reader parses the file throughout. (Top 9)
- **`reload-touch-focus-test`:** a fixed 0.8 s reload wait and a 0.05 s "mid-animation" guess.
  Assert the active grab/morph and both build IDs around the swap. Same-build reload is not an
  old-to-new rehearsal. (Top 11)
- **`widget-mode-reload-rehearsal`** copies XML into the supplied old checkout's metadata; use an
  owned, expendable old snapshot. (superm-modes row)
- **`goo-fallback-test`** infers the fallback halo from "goo off and dragging works"; add pixels
  showing the halo. **`goo-flow-test`** checks fullscreen clipping with one distance sample; add a
  pixel control. (suite rows)
- **`widget-input-test`** pins internal diagnostic log counts and exactly one geometry
  notification, and opens live IPC at import time; test visible edge stability and delivery.
  (Top 15; suite row)
- **`omarchy-override-report-test`** needs an installed Omarchy 4 with at least 200 captured
  rows; use versioned fixtures plus a separate installed-adapter smoke. (suite row)
- **`state-model-test`** writes artifacts and logs to shared runtime paths. (suite row)
- **`widget-batch-race-test`** sets the double-tap delay to 3000 ms, so it doesn't cover default
  timing under load; label it lifecycle stress and keep a default-timing case. (suite row)
- **A performance lane** for the benches (`goo-bench`, `goo-breath-bench`, `goo-draw-bench`,
  `goo-idle-bench`, `pairing-latency`) and the P8 budgets removed above, reporting CPU work,
  queue delay and presented latency separately; `pairing-latency` reports a sampled distribution,
  not a proven bound. (Top 5; suite rows)

### Low

- `peek-unit` "every no_room window has no strip anywhere" samples the first three windows on an
  8 px grid; say so or use an exact reference. (window-peek-strip row)
- `solar-test` accepts light or dark after requesting light; assert light. (suite row)
- `goo-strip-test`'s dry-content precondition defaults to passing when the field is missing.
  (suite row)
- `goo-watercolor-test` compares the same PNG bytes twice (`read_bytes` or `cmp`); decode pixels.
  Its one-minute idle check belongs in a soak lane. (goo-release-review2 row)
- `goo-breath-keys-test` pins exact log prose and line count; check the reason fields and visible
  output during forced fallback. (goo-release-review2 row)
- `goo_breath_checks.py`, `goo_idle_checks.py` gate on 22–27 Hz cadence and wall-time phase;
  consolidate and split visual correctness from cadence. (suite rows)
- Saved-but-uninspected screenshots: `avoidance-widget-test`, `hint-front-center-test`,
  `spread`-style "drawn" checks. (suite rows)
- `cursor-size-live-test` restores only the watcher, not settings it changed. (suite row)
- `model-process-test` races startup with a 3 s fixture lifetime and a 0.8 s reload sleep.
  (suite row)
- Fixed settle sleeps throughout the GUI suites (badges, dnd, key-layers, attention, goo-*):
  replace with waits on state as each suite is touched.
- Duplication and size: `widgets-test.sh` (955 lines) reruns all of widget-input; repeat, race,
  batch and morph overlap; `goo-test` duplicates the film, hints and settings suites;
  `widget-elastic-frames` belongs in morph as an optional capture mode; `windowing-test` and
  `hint-visible-test` are long scenario chains. One primary suite per behavior. (Top 15)
- Diagnostics that must not be reported as passing gates: `chromium-gap-test`,
  `session-core-test` (opt-in; leaves system core records), `drag-test.py`, `resize-test.py`,
  `shell-probe.sh` (reads current user config), `instrument-conversion.py` (edits product source;
  disposable copy only). (harness table)

## Missing proof (no suite covers it yet)

- The virtual touchpad (`scottland/test-touchpad`) tests compositor dispatch, not libinput's
  gesture recognition from physical motion; keep a small device-replay or physical acceptance
  check.
- Reload acceptance against the actually deployed old build, with confirmed in-flight state,
  visible surviving clients and working input afterwards.
- An integrated smoke on merged builds where widget modes, pairing/spread, peeking and reload
  share state: branches passing separately don't establish the combination.
- Omarchy adapter fixes (adapter-fixes branch, 2026-10-05): the lid uses core's virtual switch
  (`scottland/test-switch`), not libinput or a real lid; DPMS is judged by screencopy on a headless
  output, not a physical panel; dictation, keyboard media hardware and real password managers are
  stand-ins. Each needs a physical acceptance check on the test machine.
- `tests/portal-test.py` fails in most runs with Arch's xdg-desktop-portal-wlr 0.8.4 (a frozen
  stream or a duplicate-frame disconnect; docs/adapter-gaps.md, AG02): a real defect in that build,
  not a flaky test. It passes with Gooarchy's patched build. It needs xdg-desktop-portal-wlr installed or
  `XDPW_ROOT` (and `GST_PLUGIN_PATH` for GStreamer's pipewiresrc) pointing at extracted packages.

## On unlanded branches (fix there before landing; not edited by the cleanup)

`spread-impl` (audited at `17d8dbe`; the findings still hold at `bb821e3`):

- **`spread-unit`:** the default run does 600 fuzz scenes, 4,000 crowding scenes and an
  uncapped `timing()` block, and reports per-window/per-pair assertion evaluations as "checks"
  (78,025 in 15 s). Move timing and extended fuzz behind explicit flags, report properties,
  scenes and evaluations separately, keep the historical counterexamples in the default run.
  `--bench` actually reduces the fuzz; `--svg` returns 0 after counted failures. (Top 14) High.
- **`spread-load-test`:** the delivery allowance is `wall_limit + longest observed gap + 2.5 ms`,
  so a stall widens its own limit; it imports helpers by splitting `spread-test.py` at `try:` and
  executing the prefix. Independent latency limit; ordinary helper module. (Top 5, Top 15) High.
- **`spread-test`:** "drawn" checks compare `layout-state` with planned targets; require visible
  window bounds at the offer and after refusal. Medium.
- **`spread-reload-test`:** add presented pixels and exact build identities; share helpers by
  import. Medium.
- It carries copies of the pairing suites: run the merged result once, not once per branch.
