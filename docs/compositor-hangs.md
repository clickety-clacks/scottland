# Compositor hang diagnostics

The October 2 live freezes are unresolved until a looping stack is captured.
Two widget launches for one app shortly before IPC stops establish timing, not
the identity of the widgetizing path or the cause of the loop.

## Preserve the next incident

Session start keeps the last three logs as `wayfire.log.previous`,
`wayfire.log.previous.2`, and `wayfire.log.previous.3`, each capped at its last
1 MiB. Copy the relevant logs before a fourth new session. The launcher raises the core
soft limit to unlimited, warning if the login manager's hard limit prevents it.
The core plugin restores default dispositions for SIGABRT and SIGQUIT.

[Wayfire's main.cpp](https://github.com/WayfireWM/wayfire/blob/master/src/main.cpp)
installs a SIGABRT handler when built with PRINT_TRACE. That handler prints a
trace and calls `_Exit(-1)`, which prevents a kernel core dump. Its trace work
is also unsuitable for diagnosing an already hung process. SIGQUIT is not in
that upstream handler list and is the preferred signal, including on the old
live build or before Scottland loads. This changes no Wayfire source.

From another terminal or SSH, identify the **compositor** PID with
`ps -C wayfire -o pid,lstart,args` (there may be test sessions). Capture with
`kill -QUIT COMPOSITOR_PID`. This terminates that session and its windows.
After systemd finishes storing the dump:

```sh
coredumpctl info COMPOSITOR_PID
core=$(mktemp "$HOME/.cache/scottland-hang.XXXXXX.core")
coredumpctl dump COMPOSITOR_PID --output="$core"
gdb -q -batch /usr/bin/wayfire "$core" \
  -ex 'set pagination off' -ex 'thread apply all bt full'
rm -f "$core"
```

Use the exact executable and plugin snapshot that ran that PID for symbols.
The core contains all threads, not just the receiver of the signal. Keep the
stack output, logs and build revision; remove the extracted core after diagnosis.
On systemd hosts, verify `/proc/sys/kernel/core_pattern` routes to
systemd-coredump and `/proc/PID/limits` permits cores. Scottland does not rewrite
host coredump policy. ABRT also dumps after this plugin loads; the old build
needs QUIT.

Every new widget launch logs its app window ID, reason, preview flag, rail and
real geometry. Reasons distinguish `drag-preview`, `rail-drop`, `hint-rail`,
`inertial-contact`, `cancel-form-return`, and `load-rail-recovery`.

## Avoidance lead

In `343419c`, WK13/WK31's always-on solver writes only
`scottland-hint-offset`'s scene translation. It neither moves a view nor calls
`widgetize`. `placement_of`, `window_zone`, zone memories, placement obstacles,
and load rail recovery use real geometry. The drawn image can enter a rail
visually without changing its zone or lifecycle. Dragging explicitly takes
over the drawn image, but rail entry is then decided by pointer input. No
avoidance-to-widget map-feedback loop was found in this code trace.

Widgetizing without a `drop:` message is possible:

- A held drag enters the rail: a preview launches before release.
- A hint cycle or WK15 double-tap explicitly requests the rail.
- A keyboard push or released drag coast reaches side contact (WK20/L32).
- Esc returns a previously undocked window to its original widget form.
- Plugin load recovers an app whose real center is on a rail (once at load).

The earlier `c99f116` build has these widget/form/coast paths too, but its
avoidance runs only while Alt is active. Always-on avoidance is therefore not
necessary for both reported incidents; an indirect common cause remains open.

## Isolated reproduction

All compositor testing runs on plumbus, with this checkout's helpers, shipped
config, a private D-Bus, and a unique headless directory under its `build/`.
No test reads personal settings or touches a physical or live session.

`tests/headless.sh start --widgets --gdb` starts a debugger as the compositor's
parent (works with Yama ptrace_scope=1). Sending SIGINT to **that gdb PID**
prints all thread stacks into the headless `wayfire.log`, then resumes. Stop
the harness afterward; its private process group includes the inferior.
Save the log before stop removes the session directory. Wait for gdb's
`Continuing`/inferior resumption before another interrupt; interrupting gdb
while it is printing stacks can abort its command file.

The input suites use stipc and a five-second IPC socket timeout:
`widget-repeat-test.py` (36 dock/open rounds), `widget-race-test.py` (72
preview/Esc/re-entry rounds per terminal), `widget-batch-race-test.py`
(concurrent handoffs under large windows), and `avoidance-widget-test.py`
(six large windows with and without attention, outside/inside Alt).
Invoke through `tests/headless.sh run python3 tests/SUITE.py ARTIFACT_DIR`.
The race suite accepts `foot` or `ghostty` as its final argument.
`tests/session-log-test.sh` verifies launcher rotation; `tests/session-core-test.sh`
deliberately terminates only its own isolated compositor twice to verify real
ABRT/QUIT systemd cores and all-thread stack extraction.

## Results (October 3, plumbus headless)

Baseline plugin: `343419c`, started after its build, under gdb. No compositor
IPC timeouts or persistent 100% loop reproduced in:

| Probe | Result |
|---|---|
| Dock/open/redock: Super-drag, cycles, WK15, card click, widget drag/hint open | 36 rounds passed; delays 0, 0.1, 0.6 and 2.6 s |
| Preview/Esc/re-entry and opening during entry, expanded/collapsed, attention | 72 rounds passed with foot; 72 with Ghostty; map delays 0–0.8 s |
| Six large windows, attention off/on, outside/inside Alt | 4 checks passed; avoidance translations reached ±280 logical px, real geometry unchanged, no widgets |
| Existing drag-coast suite, same baseline | 24 passed, 3 failed; no hang. Two failures still expect avoidance to pause during coast, which `343419c` deliberately stopped doing; the overlapping-offset fixture also failed |

A debugger interrupt successfully extracted all threads; the sampled main
thread was `epoll_wait → wl_event_loop_dispatch → wl_display_run → main`, not
a looping Scottland stack. Mesa worker threads waited on condition variables.
This is a responsive sample, not evidence identifying the live hang.

Eight-large-window concurrent entry missed docking at the default 300 ms
double-tap interval. The stress fixture uses the supported
3000 ms interval to request docking, then restores 300 ms before opening cards.
Two initial fixture attempts reached no docked links; they retained responsive
IPC, not the reported freeze. A subsequent first six-card batch passed. A debugger
sample then exposed a harness bug: detaching exited the private bus wrapper. The
updated gdb command file resumes after capture, retaining D-Bus; the harness also records
the actual compositor PID via IPC socket credentials and cleans its own process
group/inferior. This affected only the isolated test. Rapid repeated debugger
interrupts were also found to abort stack printing; the final runner requests
only one capture on failure.

The diagnostic-build batch still failed its all-six-cards-mapped assertion.
The launch watchdog restored earlier apps after eight seconds; two late cards
mapped. IPC remained responsive and no looping stack was observed. Preserve
this failure separately: it is a card-startup stress failure, not a reproduced
main-thread hang. `build/freeze-final-failed/` and `build/freeze-hot/` contain
the failed observations and logs. Its latency mechanism has not been proven.

The diagnostic build's source and plugin SHA-256 matched osanwe/plumbus.
Launcher rotation/core-limit fixtures passed for three successive sessions.
Real ABRT and QUIT tests each produced a stored systemd Wayfire core and **19
thread stacks**. Those compositors had real foot/stipc input before capture;
their sampled main threads were waiting in epoll. Extracted cores were deleted.
These tests establish kernel signal capture; they do not reproduce the hang.

Final diagnostic-build probes passed **36 repeated dock/open rounds** and the
four large-window attention/Alt checks again. The corrected gdb command-file
smoke test captured all threads, resumed successful widget IPC and retained
the private widget D-Bus owner. All owned compositors were stopped. The
eight-window/six-card stress failure remains recorded separately, not erased
by these passes.

Read-only checks of osanwe's current live compositor found unlimited soft/hard
core limits, systemd-coredump routing, unblocked signals and no SIGQUIT handler
or ignore disposition. Its ABRT handler remains installed by the old build.
No signal was sent to it; use QUIT to capture a future live hang.

Evidence copied into this checkout: `build/freeze-baseline/` (round logs,
states, screenshots, debugger sample), `build/session-core-results/` (signal
metadata/stacks), and `build/widget-repeat-results/` (final checks).

The live freeze is still unresolved: no looping stack, proven root cause, or
before/after hang regression exists. No speculative lifecycle/avoidance fix is
claimed. The first incident's `c99f116` compositor was not replayed. Long-lived
hours-old breathing state, the exact live layout/timing, and osanwe's Intel
graphics path have not been reproduced on plumbus's AMD GPU. Testing did not
deploy to, reload, or change either physical desktop.
