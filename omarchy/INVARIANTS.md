# Scottland Omarchy adapter invariants

Behaviors that exist because Scottland runs on Omarchy beside Hyprland and uwsm (package
`scottland-omarchy`). Core behaviors are in [../core/INVARIANTS.md](../core/INVARIANTS.md);
project-wide scope and rules in [../AGENTS.md](../AGENTS.md).

Status: **verified** = exercised with real input on a real session (plumbus); **implemented** =
built and tested headless or by IPC only; **not built** = agreed but not implemented yet.

## Menu

| ID | Invariant | Status |
|---|---|---|
| S8 | Omarchy menu > System has "Reload Scottland" (shown only inside Scottland). | implemented |
| S7 | Omarchy menu > System has "Scottland Settings" to open it (added by `scottland-omarchy-setup`). | implemented |

## Switching between Scottland and Hyprland

| ID | Invariant | Status |
|---|---|---|
| W1 | Omarchy menu > System > "Switch Desktop" opens the switch dialog; Super+Escape in Scottland opens the Omarchy System menu, which has it. | implemented |
| W2 | The dialog has a checkbox "Close <current desktop> when switching", checked by default, with a note below it that some apps may not work correctly when both run at once. The choice is remembered. | implemented |
| W3 | Switching needs no password. | verified |
| W4 | The machine boots into whichever desktop was used last. | implemented |
| W5 | Close mode, Hyprland to Scottland: Hyprland's apps get to close cleanly first; Scottland starts as the only desktop with the user's graphical-session services (Yoohoo, Voxtype, portals, 1Password, ClipMesh, …) running in it. | verified |
| W6 | Close mode, Scottland to Hyprland: Hyprland starts cleanly (no stale session state blocks uwsm) and its services come back attached to it. | verified |
| W7 | Keep mode: both desktops keep running on separate VTs; switching flips between them without closing anything. | verified |
| W8 | Keep mode: the graphical-session services follow whichever desktop is on screen, including plain Ctrl+Alt+F-key switches, restarting as needed. Switches may be slow; a fully capable desktop matters more than speed. | verified |
| W9 | Quitting Scottland in keep mode hands everything back to Hyprland. | verified |
| W10 | If Scottland dies within seconds of starting, the next boot goes to Hyprland (crash guard). | implemented |
| W11 | Session services exit cleanly on a switch (no crash reports from apps losing their display). | implemented (1Password still crashes on keep-mode handover restarts; proposed: exclude it from the handover) |

## Omarchy environment inside Scottland

| ID | Invariant | Status |
|---|---|---|
| O1 | The stock Omarchy shell (bar, menus, theme, wallpaper) and installed plugins run unmodified in Scottland. User-installed plugins such as Ask are not stock Omarchy features. | verified |
| O2 | The Hyprland shim answers Hyprland IPC generically (monitors, clients, workspaces, devices, options, events, dispatch exec in any Lua string form, send-key); requests it cannot carry out fail visibly: they answer `error: unsupported in Scottland: …`, so `hyprctl` exits non-zero (7), and are logged to the session state directory. The Lua host does the same for shortcuts: an unsupported `hl.*` call or a dispatch the shim refuses is an error the shortcut's code sees (and is logged); while the config loads, such calls are ignored and logged once so the rest of the config still loads (AG19; visible-failure rule, 2026-10-05). On Wayfire IPC closure (including reload), the shim reconnects while retaining its Hyprland sockets; reload starts a missing shim. Headless `--omarchy` logs stay in its isolated state directory. | verified |
| O3 | Scottland's shortcuts are generated from the user's live Hyprland config at each session start and regenerated when that config changes; no copy is made at install. | implemented |
| O23 | `hyprctl reload` re-reads the Hyprland config now, as far as Scottland translates it: the session's config is rebuilt (imported shortcuts, Lua host), the same rebuild the config watcher runs on file changes, and the reply is `ok` only when that rebuild succeeded. Omarchy runs it after changes the watcher cannot see (installing Voxtype makes its F9 shortcuts appear). Monitor, input and window-rule settings are still not applied (AG03, AG07, AG28). | implemented (nacelle headless: F9 starts working after `hyprctl reload`, `tests/omarchy-shim-test.py`, 2026-10-05) |
| O4 | Window navigation, layout, workspace and group shortcuts are not imported; each appears in O20 under its reason group, as does every other shortcut the adapter cannot translate. | implemented |
| O5 | Scottland keeps the user's Omarchy shortcuts as far as it can and overrides them only where its own features need the keys: an imported shortcut on the same keys as a Scottland feature binding (the `[scottland]` section, including both center-window Alt+Tab directions, or a `scottland_*` command such as Super+, for Scottland Settings) or any `key_remaps` `from` combo steps aside, listed in the generated config and O20 report. Remap combos are reserved globally because imported bindings apply to every app, while remaps apply only to matching apps. On keys held by any other default (Wayfire's, or Scottland's generic ones such as the terminal) the user's shortcut wins. Collision detection includes Scottland metadata defaults after explicit base INI overrides, normalizes modifier order/case and xkb key names, and removes every conflicting owner while preserving unclaimed alternatives. | implemented (headless) |
| O20 | Whenever the adapter (or gooarchy-flavorings) changes or omits an Omarchy shortcut or mapping, it writes a stable, reason-grouped plain-text report: each group has one explanation, and each key says what it did in the live Omarchy configuration, what happens now, and whether it matches an Omarchy default or is user-added/changed. The report begins with **Your own shortcuts that don't work in Scottland**, containing every affected entry labeled `[Your custom/changed shortcut]` (including user-installed Omarchy plugins), its new behavior, and its reason; if there are none, it says so. The same computed custom-only list is included in the agent prompt. The agent starts with a short plain-language explanation, leads with those own shortcuts, explains each reason simply, and offers to move each to a free Scottland key; it briefly summarizes defaults afterward, or keeps the response brief when there are no own shortcuts. Bindings loaded from `~/.config/omarchy/plugins/` are always labeled user-added/changed, even if their signature matches a shipped default; plugin actions keep their own plain descriptions and are not presented as Omarchy features. A recoverable warning from a live config module does not clear labels for bindings the scan captured: a valid default scan classifies those rows, while identities absent from a partial live scan stay `[Source not verified]`. This includes O4 omissions, unsupported shortcuts and O9 Super+W. At setup and whenever unseen report content is generated, the adapter asynchronously opens Omarchy's selected coding agent; if no default agent is selected or its tools are missing, it uses Omarchy's editor launcher. The prompt points to the installed Scottland agent skill. The report is stored at `${XDG_STATE_HOME:-~/.local/state}/scottland/omarchy-overrides.txt`, remains available to reread, and is opened only when its digest differs from the last content shown. Startup display is deferred until Wayfire and the Hyprland shim are running. Sorted text drop-ins let flavorings add grouped entries. Scottland-side personal key overrides live in `~/.config/scottland/overrides.ini`. Details and format: [Omarchy override report](../docs/omarchy-overrides.md). (Mike, 2026-10-03; custom callout and scan recovery updates, 2026-10-04) | implemented (Plumbus source-label, copied Omarchy 4 defaults, partial live-scan, custom-callout, rendered-prompt, no-custom, and digest/fallback tests; no real windows opened per request) |
| O21 | gooarchy-flavorings is installed by the adapter: Omarchy has no widget concept, so its widgets and defaults are additions. It may also override an Omarchy choice, but only where that gives a better core Scottland/Gooarchy experience than Omarchy's binding, and every such override falls under O20. The flavorings report drop-in format is ready; the repository is empty today. (Mike, 2026-10-03) | not built |
| O6 | Lua-function shortcuts (Yoohoo Super+Tab, universal copy/cut/paste) run through the Lua host with their state intact. | implemented |
| O7 | Key-release shortcuts work (Yoohoo accepts on Super release; Voxtype push-to-talk stops on release). A press and a release binding on the same keys are separate shortcuts, each keeping its action (stock F9: record start on press, record stop on release), in the generated config and in the Lua host. Release chords with modifiers (Hyprland bindr on SUPER + key) run when the key of the pressed chord is let go. ([adapter gaps](../docs/adapter-gaps.md) AG09, AG10) | implemented (nacelle headless real-key input, `tests/omarchy-bindings-test.py`, 2026-10-05) |
| O22 | Imported shortcuts keep Hyprland's binding flags: `repeating` repeats while the key is held; `locked` also runs on the lock screen (once there, without repeat); shortcuts without `locked`, including release shortcuts, do not run while the session is locked. Where Scottland ships its own binding on the same keys, it steps aside so the Omarchy shortcut keeps its flags. A release shortcut marked `locked` runs only while unlocked and is listed in the O20 report as a limitation. (AG10) | implemented (nacelle headless real-key input with a real session-lock client, `tests/omarchy-bindings-test.py`, 2026-10-05) |
| O8 | Injected shortcuts reach the focused window with only their own modifiers; a physically held Super does not leak in (universal copy sends plain Ctrl+C / Ctrl+Insert). | implemented (headless) |
| O13 | Shortcuts a Hyprland config switches on and off (`:set_enabled()`, e.g. Ctrl+W remapped only while Chromium is focused) follow the focused window; while off, their keys pass through to the app unchanged (terminals keep Ctrl+W = delete word). | implemented |
| O14 | Apps in Scottland get the environment Omarchy's Hyprland config sets with `hl.env` (e.g. `QT_QPA_PLATFORMTHEME=gtk3` for native file dialogs in Qt apps, Electron/Chromium Wayland hints, cursor size, compose file, theme colors, the user's own variables), read from the same config by the Lua host, and handed to user services as uwsm does; the variables naming the desktop stay Scottland's. | implemented |
| O15 | Scottland's palette (widgets such as the default card) follows the Omarchy theme: background, foreground, muted, accent, alert and attention colors from the current theme, switching live with `omarchy theme set` (the `accent.d` provider's `--palette`). Attention reads the optional `attention = "#rrggbb"` key from `colors.toml` and falls back to `yellow`; the shipped Watercolor themes omit this key. | implemented (Plumbus isolated palette checks, 2026-10-03) |
| O18 | When core Sunlight (S21) requests a day/night mode, the adapter checks the current Omarchy theme with `omarchy-theme-color --file <current>/theme/colors.toml mode` and runs `omarchy theme set` only if that mode is wrong. Adapter defaults are Watercolor Dream Light by day and Watercolor Dream Dark by night; `~/.config/scottland/omarchy-solar.ini` can override either choice, and an omitted value uses that shipped default. A matching user-picked theme stays selected. | implemented (Plumbus isolated test, 2026-10-03) |
| O19 | `scottland-omarchy-setup` installs Watercolor Dream Light and Dark into the user's Omarchy theme directory only when each destination name is absent; an existing same-named theme is kept intact. The adapter package and dev-install snapshot ship the themes, including their `.aether-managed` markers; dev-install links the matching setup command from its snapshot. | implemented (Plumbus isolated config check, 2026-10-03) |
| O17 | Full screen holds Omarchy's notifications (FS1): the `focus.d` hook turns the shell's do-not-disturb on while a fullscreen window is in front, and back off after; a do-not-disturb the user already had on stays on. | implemented (tests/omarchy-focus-test.sh, with a stand-in shell) |
| O9 | A close action on Super+W is left unbound so a press cannot become an immediate single-press close; the omitted shortcut appears in the O20 report. | implemented (Plumbus headless real-input test) |
| O10 | Apps launched through Omarchy's launcher (uwsm-app) open in Scottland. | verified |
| O11 | The shim reports the real session-lock state. | not built |
| O12 | Keyboard layout switching and night light work under the shim. Runtime surface shortcuts use core key layers (K1; [../docs/key-layers.md](../docs/key-layers.md)), rather than adapter submaps. | layout/night light: not built; key layers: core K1 |

The optional `dispatch hl.dsp.window.tag(...)` translation remains logged as unsupported.
Wayfire has no equivalent dynamic tag state; implementing it needs a per-window tag lifecycle
that the shim can also report consistently in `clients` and events. A dispatch-only reply would
make tags appear to change without updating those readers.

[Omarchy compatibility audit](../docs/adapter-gaps.md) records 38 behavior rows (AG01–AG38)
for stock and optional Omarchy choices, with source evidence, observed headless failures and
unverified outcomes kept separate. O1/O2/O10 describe their exercised shell, IPC and launch
paths; they do not establish compatibility for every menu action, application rule, portal,
input layout or hardware configuration. The audit proposes fix layers, not new commitments.

## Switching from Mike's personal solar timer

Mike's `~/.local/bin/omarchy-solar-theme` and its user timer stay in place until he chooses to
switch. To switch, disable that user timer, enable Sunlight in Scottland Settings, enter fallback
coordinates if Geoclue is unavailable, and optionally create
`~/.config/scottland/omarchy-solar.ini` with `[themes]`, `day = watercolor-dream-light`,
`night = watercolor-dream-dark` (or another theme pair). The adapter reads these values on each
check. Do not run both schedulers.

## Watercolor theme assets and verification

Each original 2752×1728 sRGB PNG was identical between `preview.png` and its background file.
The adapter stores one quality-95 WebP per theme and makes `preview.webp` a relative link to that
background; Omarchy's theme picker and background selection enumerate WebP files. The light
painting is 1,712,560 bytes (42.6 dB PSNR from the 8,769,559-byte PNG); the dark painting is
1,722,764 bytes (41.2 dB from the 8,385,881-byte PNG). This avoids a second copy of each preview
and reduces the pair from 34.3 MB of duplicated PNGs to 3.4 MB of image data. Side-by-side
comparisons are under `build/part6-theme-candidates/`.

On Plumbus, `tests/solar-test.py` passed **24 checks** for solar defaults and overrides, the optional
attention-color fallback, theme setup, marker preservation and collision safety in a temporary
config. `makepkg --nodeps` built both Arch packages; the 3,484,027-byte `scottland-omarchy` package
contains both themes, `.aether-managed` markers and WebP previews. Package and test evidence is
under `build/part6-plumbus/`.
