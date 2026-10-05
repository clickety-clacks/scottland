# Omarchy adapter gaps beyond Mike's setup

Research snapshot: October 3, 2026. Scottland `20c98671af07370043ea0d2dc37c02540132bb2f`
(`origin/main`, branch `adapter-gaps`); installed Omarchy **4.0.4-1**, Hyprland **0.56.2-2**.

The highest-impact gaps are **display/input configuration that silently does nothing**, a
**missing compatible screen-sharing portal in the installed package set**, and **ignored
window rules**, including password-manager capture exclusions and game idle inhibition.
Stock users also encounter failures Mike's replacements can conceal: **F9 push-to-talk loses
its press action**, and **the stock screensaver's launch command reports success without
launching**. Both were reproduced in an isolated Scottland session on plumbus.

Changing from Ghostty to Foot, Alacritty or Kitty, or from Chromium to Firefox, is not by itself
the big incompatibility. The standard launch paths preserve those choices. What does not
carry over is much of the surrounding Omarchy experience: scrolling adjustments, monitor
scale, keyboard layouts, capture helpers, autostart, and app-specific compositor behavior.
A working shell and successful `uwsm-app` launch do not establish that these features work.

This is **adapter research**, not a proposal to reproduce Hyprland's desktop. Tenet 2 and C2
exclude workspaces and hidden scratchpads; tenets 3–4 preserve Scottland's spatial layout;
tenet 5 rules out blindly importing focus-stealing rules. C5 favors translating shared
mechanisms with small adapter changes. C8 requires generic compositor capabilities and
configuration-driven app matching, not named-app branches in core. The fix layers below
are recommendations, not accepted new product commitments.

## Reading the evidence

Each row is an audit of a behavior users could reasonably expect from Omarchy. **Breaks**
means that specific feature fails, not that the whole app is unusable. **Silent no-op** means
its action is discarded, often with a success response. **Degraded** means some behavior is
lost. **Works** is limited to the stated path and evidence; it is not an application certification.

- **S — source verified:** producer and consumer were inspected. The stated missing translation
  is established; the end-user symptom is an inference unless backed by H or L.
- **H — headless observed on plumbus:** exact observations H1–H3 are recorded below. H1 used
  real `stipc` key press/release input; H2/H3 were IPC probes. None is a physical-hardware test.
- **L — existing log:** a read-only observation from Mike's retained shim log. Its lines have
  times but no dates or build IDs; historical entries alone do not prove current behavior.
- **P — prior project status:** existing invariants report verification or implementation;
  this audit did not rerun that workflow.

Source abbreviations keep the table readable:

- `H/` = `/usr/share/omarchy/default/hypr/`; `Q/` = `/usr/share/omarchy/shell/`.
- `O/` = `/usr/share/omarchy/`; `B/` = `/usr/bin/` (the installed `O/bin` entries link here).
- `R/` = this Scottland checkout; `L:N` = line N of
  `~/.local/state/scottland/hyprshim.log`, captured with 484 lines.
- `M:N` = `O/default/omarchy/omarchy-menu.jsonc:N`.
- **Importer** = `R/omarchy/config.d/50-omarchy-shortcuts`; **shim** =
  `R/omarchy/shim/scottland-hyprshim`; **Lua host** = `R/omarchy/libexec/scottland-luahost`.

The shared negative evidence is precise: `R/omarchy/libexec/scottland-luahost:133` implements
binds, environment, command execution and limited callbacks, but its fallback at line 163
makes `hl.config`, `hl.monitor`, `hl.device`, `hl.window_rule`, `hl.layer_rule`, gestures and
other unimplemented calls inert. Only `window.active` callbacks are invoked by its runtime
loop (`:245`), on shortcut execution. `R/omarchy/shim/scottland-hyprshim:343` translates a
small dispatch subset; `:430` acknowledges several unsupported commands with `ok`.
These are separate gaps: making a command return success does not implement its configuration,
state queries, events or Wayland protocols.

## Compatibility table

| ID / app or feature | Who hits it | What happens under Scottland | Evidence and verification boundary | Likely fix layer |
|---|---|---|---|---|
| AG01 — 1Password / Bitwarden capture exclusion | Optional password managers; 1Password is also in Mike's set | **Degraded; privacy-relevant:** Omarchy's `no_screen_share` rules are discarded. Users must not assume Scottland preserves those exclusions. Actual captured pixels were not tested. | **S:** `H/apps/1password.lua:1`, `H/apps/bitwarden.lua:1`; Lua fallback above. This is independent of whether a particular recorder honors Hyprland's rule. | **Adapter + core/upstream:** generic capture-exclusion capability, then rule translation; document capture paths that cannot honor it. |
| AG02 — browser meetings, OBS, portal-based capture and global shortcuts | Default Chromium/OBS; optional Firefox, Zen, Chrome, Brave, Edge, conferencing apps | **Breaks or degraded (inferred):** no compatible ScreenCast backend is supplied by Scottland's dependencies. Mike has GTK and Hyprland portals, no `xdg-desktop-portal-wlr`. Restarting portals during handover cannot replace the missing implementation. GTK file pickers are a separate, supported interface. | **S:** `O/install/omarchy-base.packages:142–143`, `R/packaging/arch/PKGBUILD:21,72`; installed `/usr/share/xdg-desktop-portal/wayfire-portals.conf:2` prefers `wlr;*`; `portals/gtk.portal:3` lacks ScreenCast; `portals/hyprland.portal:3` advertises ScreenCast/GlobalShortcuts/InputCapture. No meeting/OBS transaction tested. | **Core packaging + adapter:** select/install a compatible portal and preserve service environment. **Upstream/core** for interfaces beyond its capabilities. |
| AG03 — fractional scaling, rotation, refresh rate, monitor placement, display-panel toggles | Stock auto-scale, HiDPI users, optional multi-monitor/custom monitor config | **Silent no-op:** `hl.monitor` config and live scale/enable changes are ignored. The shim advertises scale 1, transform 0, 60 Hz and no available modes rather than true output metadata. `GDK_SCALE` can be imported while the corresponding monitor scale is not, producing inconsistent sizing. | **S + H2 + L:** `O/config/hypr/monitors.lua:4`, `B/omarchy-hyprland-monitor-scaling:97`, `Q/plugins/panels/monitor/Panel.qml:299`; shim `:210–237`; `L:35`; core L15 says not built. H2 observed unchanged shim monitor data, not physical modesetting. | **Adapter + shim:** translate monitor config to Wayfire and report actual metadata; **core/upstream** only where output abilities are absent. |
| AG04 — docks, lid close/open, laptop-display disable and mirroring | Laptop and multi-monitor users | **Breaks/degraded (inferred):** lid switch bindings cannot map to evdev key names; clamshell scripts' monitor/DPMS/reload actions are inert. The stock monitor watcher is registered on an uncalled startup event; the shim emits no output-added/removed events. Default Wayfire hotplug may still work, but Omarchy's reconciliation does not. | **S:** `H/bindings/utilities.lua:32`, `H/autostart.lua:9`, `B/omarchy-hyprland-monitor-clamshell:173,206,237`, `B/omarchy-hyprland-monitor-internal-mirror:39`; importer `:171–187,309`; shim `:446–488`. Physical dock/lid/suspend not tested. | **Adapter + shim**, generic switch input/output lifecycle support from **core/upstream** if needed. |
| AG05 — Apple Studio Display / Pro Display XDR brightness | Optional external Apple displays | **Breaks/degraded (inferred):** Apple detection requires monitor make/model, which the shim blanks. The brightness helper therefore misses the Apple-specific `asdcontrol` route and chooses another route. Ordinary laptop backlight is not implicated. | **S:** `B/omarchy-hyprland-monitor-focused-apple:8–11`, `B/omarchy-brightness-display:38,79`; shim `:220–223`. No Apple display tested. | **Shim:** truthful EDID/make/model/output mapping; preserve existing Omarchy hardware helper. |
| AG06 — non-US layouts, variants, keyboard options and layout bar | Default non-US installations; optional multiple layouts | **Breaks/degraded:** Scottland ships `us` and `compose:caps`; Omarchy's vconsole-derived layouts and user `hl.config` overrides do not transfer. Layout-bar clicks and lock-time reset return `ok` without switching. Queries mix hardcoded configured layout with actual active keymap. | **S + H2 + L:** `H/input.lua:29–55`, `O/config/hypr/input.lua:8–20`, `Q/plugins/bar/widgets/KeyboardLayout.qml:76`, `B/omarchy-system-lock:11`; `R/core/config/scottland.ini:16`; shim `:32,308–324,430`; `L:7,12,23,36,69,76,81`; O12. H2 checked acknowledgement, not a multilingual typing session. | **Adapter + shim:** import XKB state, implement switching and layout events; test physical/keysym binding semantics across layouts. |
| AG07 — touchpad/touchscreen disable, per-device tuning, natural scroll, acceleration | Default hardware menu; users with customized input, tablets or accessibility devices | **Silent no-op:** device enable/disable and Hyprland input settings are ignored. A saved toggle can claim a device was disabled while it still accepts input. Scottland's own touch gestures are available independently. | **S:** `B/omarchy-toggle-input-device:48`, `H/disabled-input-device.lua`, `O/config/hypr/input.lua:22–49`; Lua fallback. `L:24` records an unsupported clickfinger query. No hardware disable attempted. | **Adapter**, **shim** for runtime operations; generic **core/upstream** device controls where needed. |
| AG08 — Fcitx5 / compose / CJK input | Default Fcitx packages; optional language engines | **Works (service path), language entry unverified:** Fcitx is a supervised user service, not inherently dependent on Hyprland IPC. The adapter imports `XCOMPOSEFILE` and hands over services. Lost XKB settings in AG06 can still break expected compose/layout behavior. Do not infer all IMEs work from a running process. | **S + local process observation:** `O/install/omarchy-base.packages:35`, `H/envs.lua:24`, `/usr/lib/systemd/user/omarchy-fcitx5.service:24`, `O/migrations/1785167800.sh:14`; adapter O14/W5. Candidate popups and committing CJK text in GTK, Qt and browsers not tested. | **Adapter** for config/service continuity; protocol failures, if reproduced, belong to **upstream/core**. |
| AG09 — Voxtype F9 push-to-talk | Optional Dictation installer, stock F9 binding | **Breaks (H1):** press runs nothing; release runs `voxtype record stop`. Importer identifies a binding by chord alone, so the later release overwrites the press. The separate toggle shortcut follows a normal exec path. | **S + H1:** `H/bindings/voxtype.lua:2–4`; importer `:81–88,296–308`; Lua host `:136–139` has the same identity issue. O7's release support does not establish this paired-binding workflow. Microphone/transcription not tested. | **Adapter:** preserve press and release as distinct bindings in both scan and runtime host. |
| AG10 — held media keys, shortcuts while locked, modified release chords | Default media keys; custom push-to-talk/keyboard workflows | **Degraded (inferred):** scanner reads `opts["repeat"]`, while Omarchy supplies `repeating`; `locked` and other binding flags are not retained. Modified release bindings are explicitly skipped. Some default command replacements may retain an existing repeatable binding, so not every media key necessarily fails. | **S:** `H/bindings/media.lua:2–15`; importer `:87,301,357–375`. Locked operation/repeat timing not exercised. | **Adapter**; expose missing generic binding semantics in **core/upstream** only as needed. |
| AG11 — screenshot picker keyboard navigation | Default Print workflow; users who navigate capture without a mouse | **Breaks (inferred):** Return/Tab/arrow bindings are installed from `layer.opened` and removed from `layer.closed`; neither callback runs. `cursorpos` is unknown and cursor-warp `eval` is inert. | **S + H2 + L:** `H/bindings/utilities.lua:52–82`, `B/omarchy-capture-region:123,157,269`; Lua host `:245`; shim `:413`; `L:20,37,62`. H2 checked cursor query, not picker input. | **Adapter + shim** for layer lifecycle and cursor queries; use generic **core key layers** (K1) for scoped shortcuts. |
| AG12 — screenshots / OCR / QR region and window geometry | Default capture tools, especially scaled windows and multiple monitors | **Degraded (inferred):** shim window rectangles come from Wayfire view geometry, not Scottland's drawn scaled footprint; monitor scale/transform are synthetic. Window snapping/highlights/crops may disagree with what is visible. Screenshot cursor workaround queries an unsupported option and its updates do nothing. Freehand `slurp` + `grim` is a separate path and is not proved broken. | **S + H2:** `B/omarchy-capture-region:48–62`, `B/omarchy-capture-screenshot:42–56`, `B/omarchy-capture-text:14`, `B/omarchy-capture-qr:14`; shim `:227,247–248,423`. H3 proves only bare `grim` capture. No OCR/QR result tested. | **Shim + adapter**, generic **core** visible-geometry API if needed. |
| AG13 — screen recording, including portal/HDR/eGPU options | Default recorder; optional recording options and OBS users | **Degraded / portal path at risk:** default script uses KMS capture, not the portal, so AG02 does not prove all recording broken. Region selection inherits AG11/12. Opt-in portal capture inherits AG02; native-size and mixed-output behavior need testing. | **S:** `B/omarchy-capture-screenrecording:9–15,115–159`. No recording, encoding or HDR experiment run. | **Adapter + core packaging** for portal routing; **upstream** for recorder/GPU backend limitations. |
| AG14 — recording webcam overlay | Optional `--with-webcam`, all supported cameras | **Degraded:** app launch remains, but corner placement, size presets, no-initial-focus rules and live resize/move commands are not translated. Recording can contain a misplaced or wrongly sized camera window. | **S:** `H/apps/webcam-overlay.lua:1–29`, `B/omarchy-capture-screenrecording:86–105`, `B/omarchy-capture-webcam-resize:145–148`; shim `:343–371`. Camera/pixels not tested. | **Adapter rule translation + shim**; generic **core** geometry/focus policy respecting tenet 5. |
| AG15 — color picker | Default Super+Print and Capture > Color | **Degraded:** importer drops the shortcut solely because its command contains `hyprpicker`; the menu still runs it. This is not evidence that the picker binary itself cannot work on Wayfire. | **S:** `H/bindings/utilities.lua:41`, `M:59`; importer `:145,315–318`. Picker protocol/pixel selection not tested. | **Adapter:** capability-based import, then test picker compatibility before replacing it. |
| AG16 — night light | Default menu/bar toggle and Hyprsunset schedule | **Breaks (inferred):** shell and CLI control Hyprsunset, whose compositor protocol is not supplied by an IPC shim. No Scottland night-light bridge exists (O12). Core Sunlight's theme mode schedule is not color-temperature control. | **S:** `Q/plugins/services/nightlight/Service.qml:50–57`, `B/omarchy-toggle-nightlight:14,38–55`; O12/O18 and [Hyprsunset protocol requirement](https://github.com/hyprwm/hyprsunset#readme). No color-temperature measurement. | **Adapter + core/upstream:** compatible color-temperature implementation and truthful shell state; not just a fake temperature reply. |
| AG17 — lock, idle lock and orphaned-lock recovery | Default lock/idle; suspend/resume and lock-client restart | **Degraded:** modern Omarchy uses Quickshell `WlSessionLock` and `IdleMonitor`, not stock Hyprlock/Hypridle. Wayfire's session-lock plugin is enabled. However, shim monitor state always omits `LOCK`, so the helper reports unlocked even for a stranded lock; layout reset also fails. This audit does not claim locking itself is broken or verified secure. | **S:** `Q/plugins/lock/Service.qml:230,391–399`, `Q/plugins/services/idle/Service.qml:251`; `B/omarchy-hyprland-session-locked:14–24`; shim `:230–232`; `R/core/config/scottland.ini:85`; O11. No lock/unlock/crash/suspend test. | **Shim + adapter** with a real **core/upstream** session-lock state source. |
| AG18 — display off/on after lock or idle | Default laptop/desktop power behavior | **Silent no-op:** Omarchy dispatches DPMS; shim accepts but does not apply it, and always reports DPMS on. Wake helper can consequently skip its enable request. Backlight percentage changes are a different path. | **S + H2:** `B/omarchy-brightness-display:62–71`, `Q/plugins/lock/Service.qml:411`; shim `:229,370`. H2 has no physical panel. | **Shim + adapter**, generic **core/upstream** DPMS. |
| AG19 — stock animated screensaver | Default idle experience; all four approved terminal choices | **Breaks (H2 dispatch mechanism):** launcher uses `hl.dsp.exec_cmd([[…]])`; shim accepts only double-quoted Lua strings or classic `exec`, so it returns `ok` without spawning. Monitor-focus dispatch and fullscreen/cursor rules are separate missing pieces even after launch is fixed. | **S + H2:** `B/omarchy-launch-screensaver:28–36,56–70`, `H/apps/system.lua:35`, `B/omarchy-screensaver:21`; shim `:341–351`; `L:6,11`. H2 used a harmless marker command, not the full idle cycle. | **Shim** for supported Lua syntax/focus; **adapter** for generic rules/startup behavior. |
| AG20 — extra autostart, Sunshine and post-boot hooks | Optional apps installed with `o.launch_on_start`; stock startup helpers | **Silent no-op:** `hyprland.start` handlers are registered but never fired. Sunshine's installer writes exactly that startup helper. Stock power-profile initialization, udiskie startup, monitor watcher and post-boot hook are also missed through this route. Already-running or independently supervised services can conceal it. | **S:** `H/helpers.lua:112–119`, `H/autostart.lua:1–14`, `B/omarchy-install-service-sunshine:16–17,63`; Lua host `:235–260`. Shell/environment startup is separately implemented by adapter hooks. No fresh full login tested. | **Adapter:** define a once-per-session startup lifecycle without rerunning side effects during config scans/reloads. |
| AG21 — Foot, Alacritty, Kitty, Ghostty and terminal TUIs | Foot is in base; other terminals offered by installer; tmux/Herdr and CLI editors | **Works (launch path; inferred):** Omarchy chooses `xdg-terminal-exec`'s preference, not a hardcoded Ghostty. Imported Super+Return can override core's generic terminal default. Terminal clipboard tagging covers all four. Terminal-specific cwd paths still need execution coverage. | **S + P:** `B/omarchy-install-terminal:15–19,44`, `B/omarchy-launch-terminal:6`, `B/omarchy-launch-tui:13`, `B/omarchy-cmd-terminal-cwd:6–11`; importer `:336–368`; shim `:45,259`; O5/O8/O10. Not four app smoke tests. | **Adapter** regression coverage; no new terminal default or app-specific core branch. |
| AG22 — terminal touchpad scroll feel | Especially Foot/Alacritty/Kitty users coming from stock tuning | **Degraded:** Omarchy applies different per-app `scroll_touchpad` multipliers; those rules are lost. Scottland's global 0.2 default is not an import of the user's values. Its touchscreen terminal adaptation (L26) is distinct from touchpad scrolling. | **S:** `H/input.lua:64–79`, `O/config/hypr/input.lua:47`; `R/core/config/scottland.ini:6–17`; core L17/L26. No cross-terminal scroll measurement. | **Adapter** to import generic per-app input rules; generic **core** control if missing. |
| AG23 — alternative browsers and web apps | Default Chromium/web apps; optional Chrome, Brave/Origin, Edge, Firefox, Zen | **Works (launch path; inferred), with shared gaps:** default/private browser launch reads user choice. Web apps intentionally fall back to Chromium for Firefox/Zen even on Omarchy; that is not an adapter regression. PiP, screen sharing and focus/presentation need their own coverage. | **S + P:** `B/omarchy-install-browser:30–81`, `B/omarchy-launch-browser:6–33`, `B/omarchy-launch-webapp:6–13`; O10. No browser matrix run. | **Adapter** for shared mechanisms; browser choice/fallback remains **distro** policy. |
| AG24 — browser shortcuts and user-defined window tags | Alternate browsers, custom terminal app-ids, custom copy/paste rules | **Degraded (inferred):** shim synthesizes only its hardcoded terminal tag, not the user's tag rules. Clipboard logic can classify a custom terminal incorrectly. Core's shipped Ctrl+W remap covers Chromium/Firefox/Brave patterns but does not name Zen or Edge, so the same personal workflow is not uniform across offered browsers. | **S:** `H/apps/terminals.lua:4`, `H/bindings/clipboard.lua:23–43`; shim `:45,259`; `R/core/config/scottland.ini:332–337`. Dynamic tag failures at `L:33,54,483`. | **Adapter/shim:** generic tag lifecycle and config-derived matching. Any deliberate shipped remap coverage change needs a **core product decision**, not a browser branch in C++. |
| AG25 — editor alternatives | Default Neovim; menu offers VS Code, Cursor, Zed, Sublime, Helix, Vim, Emacs | **Works (launch path; inferred):** selected CLI editor runs in chosen terminal, graphical editor via `uwsm-app`. Lost file-dialog/focus rules can degrade particular apps without breaking installation or editing generally. JetBrains' optional focus workaround is not translated. | **S:** `M:226–232`, `B/omarchy-launch-editor:15–32`, `H/apps/system.lua:17–20`, `H/apps/jetbrains.lua:2`. No editor/app matrix run. | **Adapter** for applicable rules; **core/upstream** only for generic focus issues that reproduce. |
| AG26 — browser Picture-in-Picture / Meet PiP | Default and optional browsers, video calls | **Degraded:** Omarchy's PiP matching, geometry, aspect constraint and decoration rules are ignored. Existing native app behavior may compensate partly. Hyprland's pin-across-workspaces semantics cannot be copied literally into a desktop without workspaces. | **S:** `H/apps/pip.lua:2–23`; Lua fallback. No PiP video tested. | **Adapter + generic core** capabilities; resolve layout/priority behavior against tenets 3–5. |
| AG27 — gaming / streaming idle inhibition and automatic fullscreen | Base Moonlight; optional Steam, RetroArch, GeForce NOW; custom `noidle` rules | **Degraded (inferred):** fullscreen and `idle_inhibit` rules are ignored. Native app fullscreen/inhibitor requests may still work, so not every game will idle-lock. Shim falsely reports `inhibitingIdle=false` for all windows, weakening diagnostics. | **S:** `H/apps/steam.lua:1`, `H/apps/retroarch.lua:1`, `H/apps/moonlight.lua:1`, `H/apps/geforce.lua:1`, `H/apps/system.lua:57`; shim `:262`; `B/omarchy-debug-idle:42–46`. No controller/fullscreen idle-cycle test. | **Adapter + core/upstream:** generic inhibition/fullscreen policy and reporting. |
| AG28 — other app rules, floating dialogs and install windows | Base LocalSend, Evince, mpv, Pinta, Kdenlive, calculator; optional Battle.net, Resolve, Hermes, QEMU/Windows VM | **Degraded (inferred):** sizing, decoration, focus and opacity exceptions are not imported. All Scottland windows already avoid tiling, so loss of `float=true` alone is not a bug. Do not claim media becomes translucent: Omarchy's global opacity rule is ignored too. Windows VM setup also reads the shim's synthetic scale. | **S:** `H/apps/system.lua:1–57`, `H/apps/localsend.lua:2`, `H/apps/battlenet.lua:4–15`, `H/apps/davinci-resolve.lua:3–11`, `H/apps/hermes.lua:2`, `H/apps/qemu.lua:1`, `B/omarchy-windows-vm:1449`; `B/omarchy-launch-floating-terminal-with-presentation:13`. Applications not exercised. | **Adapter** policy translation; generic **core** abilities. Focus-stealing and forced-placement rules require tenet review, not blanket equivalence. |
| AG29 — stock launcher/menu and launch-or-focus helpers | Default launcher users; users without Mike's Ask/Yoohoo replacements | **Works (P) for shell/launch; degraded navigation possible:** address-based focus is translated, but merely focuses a peripheral window instead of presenting it at the center. Unsupported selectors/monitor focus silently fail. Choosing an app should use Scottland's explicit presentation path where appropriate. | **S + P:** O1/O10; `B/omarchy-launch-or-focus:13–16`, `B/omarchy-hyprland-focus-app:30`; shim `:363–370`; core L30 distinguishes focus from present. `L:19` shows unsupported title-based focus. No stock launcher selection test here. | **Adapter + shim** using generic **core** `scottland/present`, with user-initiated selection distinct from unsolicited activation (tenet 5). |
| AG30 — keybinding help / command discovery | Default Super+K users | **Degraded:** `hyprctl binds` returns `unknown request` with exit status 0. Help can supplement from Lua source but lacks a trustworthy runtime inventory, including which shortcuts Scottland displaced/skipped. It may advertise unavailable operations. | **S + H2 + L:** `B/omarchy-menu-keybindings:269,446–456,459–496`; shim fallback; `L:25,47`. Help UI contents not captured. | **Adapter + shim:** expose effective bindings, retain explicit displaced/unsupported status. |
| AG31 — workspace/scratchpad/group/tiling controls | Stock shortcuts/bar; customized layouts and scratchpads | **Silent no-op or deliberately omitted:** navigation shortcuts are excluded; bar workspace buttons and menu toggles can still advertise actions that cannot work. No-workspace behavior is intentional, not a request to restore hidden workspaces. | **S:** `H/bindings/tiling.lua:20–37`, `Q/plugins/bar/widgets/Workspaces.qml:35`, `M:92–94`; importer `:145,320–327`; `L:21`. O4/C2. | **Adapter presentation/configuration:** hide or explain incompatible actions while preserving the unmodified stock shell (C6); upstream capability hooks if needed. |
| AG32 — themes, Aether-generated styling and config tools | Default themes; optional theme tools and customized appearance | **Works (P) for palette; degraded for compositor settings:** shell/app theme generation remains; Scottland follows Omarchy palette via O15/O18. Hyprland rounding/gaps/opacity/cursor settings are ignored and shim options are fixed. Setup > Monitors/Input opens editable files that do not configure Scottland. | **S + P:** `B/omarchy-theme-set:298–329`, `Q/Commons/Style.qml:444,453`, `H/omarchy.lua:21–22`, `M:106,121–123,182`; shim `:27–35`; adapter O15/O18. Theme-app retint matrix not tested. | **Adapter** for semantic configuration and honest menu routes; theme/package selection remains **distro**, per `docs/distro-notes.md`. |
| AG33 — volume, brightness, media and OSD | Default shell/hardware keys | **Works (source path/P), except noted gaps:** OSD calls the shell; audio and normal backlight/DDC percentage changes do not inherently require Hyprland dispatch. Separate DPMS, Apple routing, repeat and locked-binding gaps are AG05/10/18. | **S + P:** `B/omarchy-osd:40`, `B/omarchy-brightness-display:79–99`, `H/bindings/media.lua:2–15`; O1. No audio/hardware setting changed for research. | **Adapter/shim** for the identified exceptions; preserve existing hardware helpers. |
| AG34 — NVIDIA / nouveau / hybrid GPU | Optional hardware support chosen by installer | **Degraded risk; hardware unverified:** NVIDIA `hl.env` variables have an import path, so they are not all lost. Nouveau's `cursor.no_hardware_cursors` workaround is `hl.config` and is lost. Hyprland-specific GPU assumptions do not certify Wayfire/wlroots, KMS recording, multi-GPU outputs or explicit sync. | **S:** `H/nvidia.lua:10–18`, `O/install/hardware/nvidia.sh:16`, `O/install/user/hardware/fix-nouveau-cursor.sh:21–25`; adapter O14. No NVIDIA machine tested; no claim of universal NVIDIA failure. | **Adapter** for applicable environment/workaround mapping; **upstream** for Wayfire/wlroots driver compatibility; **distro** for driver packages. |
| AG35 — Apple laptops, Framework and other hardware installation | Optional T2 Mac, Framework, vendor-specific hardware | **Works (source-level separation), compositor details unverified:** kernel/modules, firmware, udev, audio profiles and QMK installation are not undone by changing compositor. Display/lid/input problems remain AG03–07; those are not evidence the hardware packages are broken. | **S:** `O/install/hardware/apple/fix-t2.sh:6–26`, `O/install/hardware/framework16.sh:1`, `O/install/hardware/framework/qmk-hid.sh:4`, `O/install/user/hardware/framework/fix-f13-amd-audio-input.sh:1–4`. No physical vendor tests. | **Distro/upstream** for kernel/driver support; **adapter** for compositor configuration. |
| AG36 — user services / keep-mode handover | Optional tray apps, password managers, dictation, portals; users keeping both desktops | **Works (P) with a known degraded case:** generic graphical services follow the desktop, but W11 already records 1Password crashes during keep-mode restarts. Hypr-named units are explicitly skipped; a retained custom Hypridle/Hyprlock/Hyprsunset unit is not automatically a Scottland service. | **S + P:** W5–W11; `R/omarchy/libexec/scottland-handover:26–38`. No handover performed. Modern stock Omarchy removal list includes Hypridle/Hyprlock (`B/omarchy-upgrade-to-quattro:822–823`). | **Adapter** service lifecycle; **upstream** app crash investigation as warranted. |
| AG37 — custom launchers/plugins, dynamic tags, layers and IPC consumers | Optional/user extensions; also Mike's Ask/attention tools | **Degraded/silent no-op:** only a subset of Hyprland events/actions exists. `layers` is always empty, tag mutations unsupported, and unknown queries can still have exit status 0. External `eval` is not passed to the Lua shortcut host. Core key layers are available but do not implement arbitrary Hyprland plugin APIs. | **S + H2 + L:** shim `:259,413–435,446–488`; `L:10,15,17,32` Ask shortcuts, `L:33,483` tags, `L:61` dynamic Omasnap rule. Some historical requests may have newer native replacements; do not treat all as current regressions. | **Shim + adapter**, generic **core** state/event APIs; plugin authors/**upstream** for native capabilities. |
| AG38 — zoom accessibility and custom gestures | Stock zoom shortcuts; optional touchpad gestures | **Silent no-op:** zoom function runs against `get_config=nil` and inert `hl.config`; custom Hyprland gestures are also inert. Scottland's moving/resizing gestures do not replace accessibility magnification. Workspace gestures are intentionally out of scope. | **S:** `H/bindings/utilities.lua:117–124`, `O/config/hypr/input.lua:51–57`; Lua host `:162–163`. No visual magnification test. | **Adapter + generic core/upstream** magnification; preserve intentional gesture ownership. |

## What the tests actually established

**H1 — F9 regression, real input on plumbus.** Built this checkout with
`SCOTTLAND_DEPLOY_DIR=Projects/scottland-adapter-gaps tests/deploy.sh plumbus --tests-only`.
A fresh session used `build/headless-adapter-gaps` as its unique `SCOTTLAND_HEADLESS_DIR`,
a private D-Bus, a fixture HOME under this checkout's `build/research/home`, and the shipped
Scottland base config. No personal layout or overrides were loaded. The tiny Hyprland fixture
loaded the installed `default/hypr/bindings/voxtype.lua` unchanged through an `o.bind` wrapper
that forwards string commands to `hl.dsp.exec_cmd`, like Omarchy's helper. Its `cmd_present`
returned true. A fixture `voxtype` executable recorded arguments rather than using a microphone
or the real daemon. The source file SHA-256 matched on osanwe and plumbus:
`ff39dcab7a834082abc12a5563c3b37ce434a39a1969a1ecb2f025553b69c446`.

`stipc/feed_key {"key":"KEY_F9","state":true}` produced **zero command calls**.
Releasing F9 produced **one call: `record stop`**. The generated INI contained the release
command and lacked `record start`. This verifies the paired-binding import failure; it does
not test Voxtype speech recognition, output injection or its OSD.

**H2 — shim contract probes in that same session.** Ten calls returned the following:

| Request | Result | Interpretation |
|---|---|---|
| `eval hl.monitor({ output="HEADLESS-1", scale=1.5 })` | exit 0, `ok` | Unsupported acknowledgement; no applied scale translation. |
| `keyword monitor HEADLESS-1,disable` | exit 0, `ok` | Unsupported acknowledgement. |
| `dispatch hl.dsp.dpms({ action="disable" })` | exit 0, `ok` | Unsupported acknowledgement, not a physical DPMS measurement. |
| `switchxkblayout all next` | exit 0, `ok` | Unsupported acknowledgement. |
| `reload` | exit 0, `ok` | Unsupported acknowledgement. |
| `-j layers` | exit 0, `{}` | Stub result; this run had no layer surfaces to compare. |
| `binds` | exit 0, `unknown request` | Missing inventory with misleading success exit. |
| `cursorpos` | exit 0, `unknown request` | Missing query with misleading success exit. |
| `dispatch hl.dsp.exec_cmd([[touch <fixture>/long-string]])` | exit 0, `ok`; file absent | Omarchy screensaver's Lua long-string syntax is not executed. |
| Same marker command with a double-quoted Lua string | exit 0, `ok`; file present | Positive control: session exec worked with supported syntax. |

Monitor JSON before/after was identical (1280×720, scale 1, DPMS true). These stub replies
cannot independently prove physical output state; the source's absence of a Wayfire operation
establishes the no-op. We did not send any of these mutations to Mike's session or plumbus's
shared session.

**H3 — capture smoke check and cleanup.** `grim` inside the isolated session produced a
1280×720 PNG. Visual inspection found only the expected empty dark desktop: no stock shell,
apps, widgets or capture picker were launched. This is not screenshot-workflow or visual UI
verification. Session started after its own build; no pre-existing session/build mismatch or
reload was involved. The harness stopped its compositor/helpers and removed its exact headless
directory. A process check found no remaining task process other than the inspection command.

Artifacts copied back under this checkout's `build/research/` (not committed):
`probe.sh`, `probe.py`, `probe-output.txt`, `results/generated.ini`, `results/wayfire.log`,
`results/hyprshim.log`, `results/headless.png`, `inventory.txt`, `hypr-dependent-files.txt`.
No GPU performance measurements were made. The host had other apps running; no conclusion
about timing or performance is drawn from this shared-host run.

## Why Mike's usage is an incomplete acceptance test

Read-only inspection found **Ghostty selected** in `~/.config/xdg-terminals.list`,
**Chromium selected** in `~/.config/mimeapps.list:3,6`, and packages including Foot, Neovim,
Fcitx5, Voxtype, 1Password, OBS, Moonlight, Sunshine and Remmina. The queried package set did
not include Alacritty, Kitty, Firefox, Zen, Chrome, Brave, Edge, Zed, VS Code, Helix, Emacs,
Steam, RetroArch, Lutris or Heroic. Installation is not proof of use: the process snapshot
showed Ghostty, Fcitx5, Voxtype, 1Password, Remmina and Sunshine, among others; it did not
establish which apps he uses regularly. The editor preference file was absent, so the stock
launcher would fall back to Neovim.

His bindings choose Browser on Super+Return and Terminal on Super+Ctrl+Return
(`~/.config/hypr/bindings.lua:24–25`, repeated at `:405–406`), Ask on menu keys (`:181–183`),
Yoohoo on Super+Tab and release (`:212–216`), custom dictation controls (`:371–377`), and
Omasnap on Print (`:398`). That leaves stock F9, stock screenshot selection, stock launcher
help and alternative-terminal behavior underexercised. His window rules include a Chromium
activation exception (`~/.config/hypr/hyprland.lua:48`), Quickshell slider placement
(`looknfeel.lua:79`), and attention tags/borders (`attention.lua:36–45`). These are examples
of personal integrations, not a default Omarchy contract to bake into core.

The retained shim log contains **45 unsupported records across 12 command categories**
in **484 lines**: 12 dispatch, 7 layout switch, 6 reload, 4 Ask shortcuts, 4 eval, 3 cursorpos,
3 submap, 2 binds, and one each plugin/getoption/getcursorpos/repl. This is **not an occurrence
rate or coverage measure**. `Log.unsupported` deduplicates by first command word per process
and truncates requests to 200 characters (`R/omarchy/shim/scottland-hyprshim:72–76`), so an
unsupported focus request can hide later unsupported DPMS or tag requests. Some entries
already have current support (`plugin list`), and config calls swallowed in the Lua host need
never appear here. Absence from this log is particularly weak evidence for unused optional apps.

## Coverage and priorities

The installed package metadata says 4.0.4-1, although `O/version` says `4.0.0.alpha`.
This report follows the installed files, not an assumption about a matching upstream tag.
A mechanical pass over `install/`, `config/`, `default/`, `bin/`, `applications/`, `migrations/`
and `shell/` read **1,034 text files** and found **97 files** containing direct Hyprland/API/tool
markers. This count excludes unreadable/binary files and is a search inventory, not 1,034
manual reviews or 97 independent bugs. App rules using `o.window`, install menus and their
helper chains were reviewed separately. The package lists distinguish shipped apps from
hardware/optional packages; the menu supplies additional editor, browser, terminal, gaming,
AI, service and development choices beyond `omarchy-other.packages`.

CLI development packages, fonts, package removal, Docker setup and ordinary `.desktop`/web-app
creation mostly share package-manager/terminal launch paths, not compositor operations.
Their installation was not run, and apps without a demonstrated dependency are not branded
broken. Likewise, Hyprlock/Hypridle references in upgrade/migration material are not evidence
that 4.0.4's stock lock still uses them. Remaining uncertainty includes CJK engines, third-party
launchers/plugins, all optional-app rendering, portal selection and end-to-end capture,
password-window masking, lock recovery, lid/suspend, mixed-DPI docks, and NVIDIA/Apple/Framework
hardware. No additional Wayfire/Quickshell process or test ran on osanwe.

Recommended order, based on user impact rather than the number of unsupported calls:

1. **Trustworthy sessions:** establish capture-exclusion guarantees, compatible portal selection,
   lock-state reporting, DPMS and lid behavior. A shell that looks correct can still conceal
   missing sharing/privacy or power behavior. These need real-session/hardware acceptance tests.
2. **Users' input and displays:** import monitor and XKB/device settings and report real state.
   Prioritize non-US keyboards and multi-monitor/HiDPI installations over appearance parity.
3. **Stock paths Mike replaces:** fix paired press/release import and Lua exec syntax, then run
   the complete stock dictation, screensaver, screenshot keyboard and launcher workflows.
4. **Generic compatibility surfaces:** implement selected rule/event/autostart semantics,
   improve effective shortcut discovery, and make unsupported operations observable to callers.
   Do not simply enable every rule: attention/placement/workspace rules need Scottland policy.
5. **Broaden the acceptance matrix:** all four approved terminals, Chromium plus Firefox/Zen and
   a second Chromium family, one GUI and one CLI editor, PiP/conferencing, Steam/RetroArch and
   one input method. Include keep-mode service handover separately from a fresh Scottland login.

For portals, a wlroots backend is a candidate for Screenshot/ScreenCast, not a promise to
implement every Hyprland portal interface or per-window sharing. Its own
[documented scope](https://github.com/emersion/xdg-desktop-portal-wlr#readme) should constrain
that choice. App choices, drivers, theme engines and defaults remain distro concerns
([distro notes](distro-notes.md)); preserving existing Omarchy choices is the adapter's job.

No product code, live settings or packages were changed. These 38 audit rows record current
limits and hypotheses; H1/H2 confirm failures, not their repair. Existing O1/O2/O7/O10 status
must be read at the granularity of their exercised paths, not as blanket Omarchy compatibility.

## Capture exclusion in Scottland (AG01, 2026-10-05)

What a Scottland session offers for capture, from Wayfire 0.11's source (`src/core/core.cpp`)
and wlroots 0.20's headers:

- **wlr-screencopy-v1** (grim, xdg-desktop-portal-wlr's ScreenCast and Screenshot, OBS's
  wlrobs): copies an output's composed frame.
- **ext-image-copy-capture-v1** with **output** sources only: the same composed frame. Wayfire
  creates no toplevel (per-window) capture source.
- **wlr-export-dmabuf-v1**: exports the output's buffer.
- **KMS capture** (Omarchy's default recorder, gpu-screen-recorder): reads the display planes
  through DRM; the compositor is not involved, under Hyprland either.

None of these lets the compositor, or a Scottland plugin, leave a window out of what is captured
while still showing it on screen: there is no capture-time render pass to change. So Hyprland's
`no_screen_share` cannot be enforced, and the adapter does not pretend to: every such rule is
listed in the O20 report as not enforced (adapter O27), and a test confirms that a window the
1Password rule matches does appear in a screen capture.

What could be built, each a product decision for Mike (none chosen):

1. **A capture path that renders without excluded windows.** Scottland would serve the capture
   protocols itself, rendering a separate frame for capture clients. Large: it replaces
   wlroots' screencopy handling and needs Wayfire support; KMS capture would still bypass it.
2. **Hide excluded windows on screen while anything captures.** Possible only for captures the
   compositor sees (not KMS), and it changes what the user sees during a call.
3. **Warn when a matching window is on screen during a capture.** Same detection limits as 2.
