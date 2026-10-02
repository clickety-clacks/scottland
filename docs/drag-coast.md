# Drag release physics

L32 in core/INVARIANTS.md defines the behavior. Drag release seeds the same independent
velocity axes, movement friction curve and tick integration as WK17, rather than starting a
destination animation. Scottland Settings exposes the shared law in its Window mode tab (S14);
an empty curve retains constant deceleration. Samples use monotonic receipt timestamps because
the common drag-motion signal provides layout coordinates but no device timestamp; this covers
pointer, touch and three-finger input.
Scottland's own live controller supplies these signals for pointer, halo, touch and swipe moves
([L33](live-drag.md)); stock client move requests retain the same observer path.
A least-squares fit uses the last 100 ms, with at least 20 ms of samples. A stationary tail
accounts for time since the last event; 50 ms without motion or speed below 60 px/s means no
coast. At the default friction, the threshold represents less than three pixels of travel.
The configured keyboard speed cap also bounds drag velocity. Resizes retain their existing
release behavior. Docked widgets retain explicit rail placement; restored widget drags also
finish their existing form transition without adding a coast.

Decisions follow the tenets:

- Tenet 3 (position means priority): coasts continuously follow zones and scale. Shared screen
  edges permit passage in global layout coordinates. Explicit Alt pinning remains in force.
- Tenets 2 (recognition) and 3 (position means priority): WK20 keeps 100 logical pt of
  the scaled footprint visible at exposed top/bottom edges, stopping that axis without bounce.
  Outward side travel morphs into the matching rail widget as the scaled footprint touches
  the rail boundary; WG1-ineligible windows stop there without changing form. Adjoining
  outputs permit passage. Pointer/finger rail intent while held keeps WG1 precedence.
  Restitution remains only for existing widgets moving vertically along their rails (WK23).
- Tenet 4 (concede as little as possible): precise or paused drops do not drift; grabbing a
  coasting object catches it, and Esc after release stops released coasts in place.
  In entered Alt mode, Esc retains WK22's undo for arrow-touched windows. Esc during a held drag
  still returns to the origin (tenet 3 / L27). Other objects can continue their independent coasts.
- Tenet 1 (attention): pause declutter globally while any inertial axis is active, keeping the
  hints attached to their moving windows without sending neighbors chasing them. Freeze the
  current offsets, then use the existing interpolation when the solver resumes at rest.

Tests use isolated headless sessions only; no live session is reloaded. The release estimator
has standalone timing/threshold/wrap tests. `tests/drag-coast-test.sh` uses real timed stipc
pointer/touch motion and records trajectories for analytic deceleration, vertical stops, side widgetization, output passage,
catch/Esc, pinning, zone scaling, rail release and declutter suspension/resumption.

## Earlier drag-coast validation (2026-10-02, before the WK20 edge change)

Historical run setup (do not reuse: current AGENTS.md rule 7 forbids private runtimes):
only isolated headless sessions were used, each with `SCOTTLAND_HEADLESS_DIR` and a private
runtime below this checkout's `build/`. The private runtimes expose only the user manager's
scope socket so the existing widget process-lifecycle checks can create their test scopes;
they never import an environment or start live desktop services. No install or live reload
was performed. Headless directories are removed by the runners; logs, screenshots and motion
samples remain under `build/coast-*`.

| Suite | Result |
|---|---|
| `tests/drag-coast-test.sh` | 22 single-output and 3 two-output checks passed |
| `tests/inertia-test.sh` | 61 single-output and 8 two-output checks passed |
| `tests/windowing-test.sh` | 84 checks passed |
| `tests/widgets-test.sh` | 146 checks passed |
| `tests/widget-morph-test.sh` | Final pass: 185 checks passed |
| `tests/inertia-unit.sh` | 43 checks passed |
| `tests/windowing-unit.sh` | 75 checks passed |

The windowing and inertia placement fixtures now explicitly pause before releasing; they are
precise drops, not flicks. Windowing's IPC goes directly to its recorded headless socket to keep
quick chords independent of process startup, and its model subscription is consumed during
input to avoid filling the compositor's send buffer.

The final branch morph run passed all 185 checks (`build/coast-morph-checked.log`).
Earlier rendering instability is retained as a test-stability caveat: the first branch run passed 184/185 checks
with a crossfade screenshot failure; a concurrent run passed 174/185 with goo-alignment and
collapse animation failures. An untouched `git archive e63ad97` built below `build/coast-baseline`
passed 182/185 and also failed goo-alignment and blended-pixel checks. These failures vary by run;
the baseline comparison does not establish the cause of every branch failure. No morph assertion
or widget implementation was changed to suppress them. The baseline evidence is in
`build/coast-baseline-morph.log`; earlier branch evidence is in `build/coast-morph.log` and
`build/coast-morph-final.log`.


## WK20 edge replacement (2026-10-02)

The `push-edges` change uses the same integrator and release estimator; only boundary handling
changes. Artifacts live under this checkout's `build/push-edges-*`. Each session uses the normal
`XDG_RUNTIME_DIR` and its own `SCOTTLAND_HEADLESS_DIR`, shipped config, and private widget D-Bus.
No install, physical session, live widget service or main checkout is touched. Test runners stop
and remove their own headless directories.

Real stipc keyboard input passes 65 single-output and 8 two-output checks
(`build/push-edges-inertia-verified.log`). This includes 100 pt stops at full and peripheral scale,
independent diagonal axes, scaled-footprint contact at both rails, overlapping drops without an
inward snap, intermediate morph frames,
Esc during startup (including the return glide) and after docking, existing resize recovery, and passage in both directions.
Real pointer/touch input passes 26 single-output and 3 two-output coast checks
(`build/push-edges-final-drag-coast.log`), including flicks at all four exposed edges.

The windowing suite passes all 84 checks (`build/push-edges-final-windowing.log`). The widgets
suite completes with every check passing (`build/push-edges-final-widgets.log`), including its
83-check input suite, lifecycle timeouts, private-service replacement, plugin reload and
no-scope fallback. Inertia/windowing unit suites pass 43/83 checks.

The expanded WG22 morph suite passes all 210 checks (`build/push-edges-morph-final.log`). Its
four added entry paths use real left/right keyboard pushes and pointer flicks, sampling
intermediate shapes, crossfade pixels, continuous visibility and goo alignment. Screenshots
and JSON trajectories remain alongside the logs.

Earlier runs are retained: concurrent runs missed some timing/animation observations; the
first sequential morph run passed 209/210 with a goo-expansion alignment sample failure.
The final full morph rerun passes without relaxing its assertions. One initial widgets command
was interrupted before completion; its session was explicitly stopped, and the full persistent
rerun passes. The new inertia tests wait through card startup before ending morph sampling.
