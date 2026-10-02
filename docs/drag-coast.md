# Drag release physics

L32 in core/INVARIANTS.md defines the behavior. Drag release seeds the same independent
velocity axes, movement friction curve and tick integration as WK17, rather than starting a destination animation. Scottland Settings exposes the shared law in its Window mode tab (S14); an empty curve retains constant deceleration.
Samples use monotonic receipt timestamps because Wayfire's common move-drag signal provides
layout coordinates but no device timestamp; this covers pointer, touch and three-finger input.
A least-squares fit uses the last 100 ms, with at least 20 ms of samples. A stationary tail
accounts for time since the last event; 50 ms without motion or speed below 60 px/s means no
coast. At the default friction, the threshold represents less than three pixels of travel.
The configured keyboard speed cap also bounds drag velocity. Resizes retain their existing
release behavior. Docked widgets retain explicit rail placement; restored widget drags also
finish their existing form transition without adding a coast.

Decisions follow the tenets:

- Tenet 3 (position means priority): coasts continuously follow zones and scale. Shared screen
  edges permit passage in global layout coordinates. Explicit Alt pinning remains in force.
- Tenet 2 (recognition): exposed edges bounce so the object remains visible; passive motion
  never changes it into a widget. Pointer/finger rail intent keeps WG1 precedence.
- Tenet 4 (concede as little as possible): precise or paused drops do not drift; grabbing a
  coasting object catches it, and Esc after release stops released coasts in place.
  In entered Alt mode, Esc retains WK22's undo for arrow-touched windows. Esc during a held drag
  still returns to the origin (tenet 3 / L27). Other objects can continue their independent coasts.
- Tenet 1 (attention): pause declutter globally while any inertial axis is active, keeping the
  hints attached to their moving windows without sending neighbors chasing them. Freeze the
  current offsets, then use the existing interpolation when the solver resumes at rest.

Tests use isolated headless sessions only; no live session is reloaded. The release estimator
has standalone timing/threshold/wrap tests. `tests/drag-coast-test.sh` uses real timed stipc
pointer/touch motion and records trajectories for analytic deceleration, bounce, output passage,
catch/Esc, pinning, zone scaling, rail release and declutter suspension/resumption.

## Validation (2026-10-02)

Only isolated headless sessions were used, each with `SCOTTLAND_HEADLESS_DIR` and a private
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
