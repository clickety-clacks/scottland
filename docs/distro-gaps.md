# What Omarchy defines that a Scottland distro has not planned

Research snapshot: 2026-10-03. Omarchy **4.0.4-1**, installed on osanwe;
Scottland **20c98671af07370043ea0d2dc37c02540132bb2f** (`origin/main` at the
start of this report).

Scottland has a substantial desktop design, but almost none of the **machine
lifecycle** of a distribution is decided: installation and disk layout, boot
and recovery, accounts and authentication, repositories and upgrades, hardware
qualification, and support. Omarchy makes concrete choices in all of these
areas. Scottland also has no recorded general application catalogue, network
and peripheral policy, or standalone shell/service selection.

The clearest existing distro plans are a semantic palette engine, meaningful
terminal titles, agent attention integration, touchpad tapping, a notification
daemon with full-screen do-not-disturb integration, and some application and
shortcut defaults still to be selected. Those do not amount to an OS delivery
plan. Running Omarchy's shell and services through the adapter is an existing
integration decision, not a decision to ship them in a Scottland distro.

Many Omarchy choices could be adopted without changing the spatial desktop:
networking, printing, audio infrastructure, fonts, package tooling, and much
hardware support. The answers change for workspaces and tiling, window
presentation, notification interruptions, terminal identity, and the placement
of persistent controls. The tables identify these boundaries without selecting
new defaults. The 59 decision areas below are assessed as **34 not planned,
21 partly covered, and 4 covered**; these are scope categories, not a measure
of implementation completeness.

## Scope, evidence, and meaning of coverage

This is a distribution-decision inventory, not a compatibility audit or a new
product specification. **Omarchy facts** below describe shipped source and
configuration, not a claim that every feature was exercised or enabled on this
machine. **Scottland coverage and the decision notes are analysis** of the
recorded plans:

- **Covered:** the relevant choice or contract is recorded; it need not be built.
- **Partly:** a related contract is recorded, but the distribution still has an
  unresolved choice. Adapter support alone cannot establish standalone coverage.
- **Not planned:** no corresponding plan was found in the reviewed Scottland
  sources. This does not mean rejected, impossible, or absent from all discussion.

In the last column, **Adoptable** means the Omarchy choice could be reused at
the policy/component level if the distro chooses a compatible base; it does
not promise its scripts run unchanged on Wayfire or on a non-Arch OS.
**Spatial** means Scottland's model changes the answer. **Mixed** separates
reusable infrastructure from desktop-specific behavior. These are inferences,
not new commitments.

Omarchy source paths beginning `install/`, `config/`, `default/`, `bin/`,
`applications/`, `migrations/`, `themes/`, `shell/`, or `etc-overrides/` are
relative to **`/usr/share/omarchy/`**. `/usr/bin/` paths are absolute. A wildcard
denotes a source family, not a single file. No installed command that changes
the system was executed.

Scottland references used throughout:

| Reference | What it actually establishes |
|---|---|
| [Distro notes](distro-notes.md), abbreviated **DN** | A distro would be a separate project, depending on Scottland; a short list of defaults, not an installer or release plan. |
| [Palette engine](palette-engine.md), **PE** | Concept, not started: separate engine repository; desktop schemas; ANSI and semantic colors; boolean light/dark; distro-owned default themes; no wallpaper extraction. |
| [Core invariants](../core/INVARIANTS.md), **Core** | Desktop behavior, graphical-session lifecycle, settings, attention, input, and palette following; individual implementation statuses remain authoritative. |
| [Adapter invariants](../omarchy/INVARIANTS.md), **Adapter** | Omarchy shell, shortcut/config import, service handover, theme bridge, and desktop switching. |
| [Agent guide](../AGENTS.md), **Guide** | Core/adapter/personal split, stock Wayfire, no workspaces, additive app tuning, no modifications to Hyprland/uwsm/stock shell. |
| [Packaging](../packaging/arch/PKGBUILD) and [install hooks](../packaging/arch/scottland-omarchy.install), **Packages** | Local Arch split packages `scottland` and `scottland-omarchy`, dependencies, a Wayland session entry and systemd units; no distro image, package repository, or release channel. |
| [Tenets](tenets.md) | Attention, recognition, spatial priority, full-size center, user-controlled focus, and uninterrupted full screen. |

For unspecified analytical boundaries, tenets 2–4 rule out treating Omarchy's
workspace/tiling design as a missing Scottland feature; tenets 1, 5, and 6 make
attention and interruption behavior a Scottland integration question. They do
not choose a filesystem, browser, security policy, or update cadence.

## 1. Installation, provisioning, and ownership of the machine

| Area | Omarchy fact and source | Scottland coverage | Decision a Scottland distro needs |
|---|---|---|---|
| OS base, architecture, and kernel | Arch repositories plus an Omarchy repository; package lists include `linux-omarchy` and headers, with a T2-specific kernel path. `default/pacman/pacman-stable.conf`, `install/omarchy-other.packages`, `migrations/1789325478.sh`. | **Not planned.** Packages builds an x86_64 desktop package; Guide permits other host distros. Neither selects the distro's base. | **Adoptable:** select the base OS, supported architectures, kernel source, and maintenance owner. |
| Installer, media, and offline setup | Package lists explicitly feed the ISO's pacstrap/offline mirror; system and user setup are separate entry points, with ISO-chroot handling and bundled Node installation. `install/omarchy-base.packages`, `install/omarchy-other.packages`, `bin/omarchy-apply-system`, `bin/omarchy-provision-user`, `install/user/mise.sh`. | **Not planned.** DN names an installer as distro work without designing one. | **Adoptable:** define install media, online/offline guarantees, installation flow, and supported upgrade-from-existing-OS paths. |
| Disk layout and encryption | Recovery assumes Btrfs root `@`, a factory snapshot `@factory`, and separate `@home`/`@log`; boot tooling expects an ESP at `/boot`. Provisioning handles encrypted and unencrypted installs, LUKS re-keying, and a drive-password changer. `bin/omarchy-system-factory-reset`, `bin/omarchy-system-factory-reset-finish`, `bin/omarchy-provision-owner`, `bin/omarchy-drive-password`, `default/limine/default.conf`. **The actual partitioner is outside this installed tree.** | **Not planned.** No disk, encryption, dual-boot, or storage policy in DN/Packages. | **Adoptable:** choose filesystem/subvolumes, encryption default and recovery, disk selection/destruction flow, and coexistence policy. |
| Account, identity, keyboard, timezone | Shared setup form prompts for keyboard, username/password, optional Git identity, hostname (default `omarchy`), and timezone; password prompt says user/root/encryption share the supplied password when applicable. Owner provisioning creates the account and wheel access. `install/provisioning/setup-form.sh`, `bin/omarchy-provision-owner`, `install/user/git.sh`. | **Not planned.** E1's graphical session is not account provisioning. | **Adoptable:** define account/root policy, locale and timezone setup, identity questions, and additional-user support. |
| Deferred ownership and factory reset | A deferred install can boot to tty1 owner setup before SDDM; factory reset stages a replacement root and a next-boot wipe/reprovisioning worker; the normal reset path requires the ISO-created factory snapshot. Scripts explicitly distinguish deletion and LUKS passphrase changes from secure erasure. `install/provisioning/`, `bin/omarchy-system-factory-reset*`. | **Not planned.** No OEM/handoff/reset lifecycle in DN/Packages. | **Adoptable:** decide whether machines can be shipped unowned and reset for reuse, with explicit data-erasure semantics. |
| User seeding and first login | `/etc/skel` supplies seed configs; provisioning supplies theme, Git, XCompose, Work directory, keyring and tool wrappers; first login enables user units and invites Wi-Fi/update, dictation, fingerprint, and default-agent setup. `bin/omarchy-provision-user`, `install/user/all.sh`, `install/user/first-run/`. | **Partly.** Core E6 and DN cover linking Scottland's agent skill and a few defaults, not a general first-run experience. | **Mixed:** select onboarding and seed ownership; welcome prompts must respect attention rather than crowd the center (tenets 1/5). |
| Login manager and autologin | SDDM with a branded theme. Login setup states that the ISO owns normal-install autologin; the owner-provisioning path retains autologin for encrypted installs and removes it after the first boot on unencrypted installs. `install/login/sddm.sh`, `default/sddm/`, `bin/omarchy-provision-owner` (`configure_login`, `install_autologin_once_cleanup`). | **Partly.** Adapter W1–W10 and Packages select SDDM-based switching/last-desktop behavior for Omarchy, not standalone distro login policy. | **Adoptable:** choose greeter, autologin/authentication boundary, multi-user behavior, and session fallback. |
| Graphical session and service lifecycle | Hyprland launched through uwsm, session environment import, shell/autostart, systemd user services; logout/reboot/shutdown helpers close app windows before ending the session. `bin/omarchy-system-logout`, `bin/omarchy-system-reboot`, `bin/omarchy-system-shutdown`, `default/wayland-sessions/omarchy.desktop`, `default/uwsm/`, `default/hypr/autostart.lua`, `default/systemd/user/`. | **Covered for Scottland's session contract.** Core E1/E5, Guide C3/C4 and Packages establish Wayfire and Scottland's graphical-session target; Adapter W5–W9 cover handover. | **Mixed:** retain the recorded core lifecycle and select which distro services attach to it; uwsm is not a core dependency. |

## 2. Boot, updates, recovery, and configuration maintenance

| Area | Omarchy fact and source | Scottland coverage | Decision a Scottland distro needs |
|---|---|---|---|
| Bootloader, initramfs, boot UI | Limine, mkinitcpio hooks, Plymouth decryption/boot artwork, SDDM artwork; optional direct EFI boot of a UKI with firmware exclusions and a warning about reaching snapshots. `default/limine/`, `default/plymouth/`, `install/omarchy-other.packages`, `bin/omarchy-refresh-limine`, `bin/omarchy-setup-direct-boot`, `bin/omarchy-plymouth-*`. | **Not planned.** Desktop session entries do not cover boot. | **Adoptable:** choose boot chain, rescue entry, firmware constraints, and boot/decryption presentation. |
| Snapshot and rollback policy | Root Btrfs snapshots for pre-update recovery, numeric retention of five, no timeline creation; cleanup and Limine snapshot sync enabled. Update warns and continues if snapshot creation fails; restore uses `limine-snapper-restore`. `default/snapper/root`, `install/config/snapper.sh`, `bin/omarchy-snapshot`, `bin/omarchy-update`. | **Not planned.** Core E4 reload is desktop development functionality, not OS rollback. | **Adoptable:** define what is snapshotted, retention, update failure policy, boot recovery, and restore UX. |
| Repositories, trust, release channels | Stable/rc/edge configs and mirrors; dev checkout linking; Omarchy/Arch keyring handling; `SigLevel = Required DatabaseOptional`. `default/pacman/`, `bin/omarchy-channel-set`, `bin/omarchy-update-keyring`, `migrations/1787589206.sh`. | **Not planned.** DN names an update channel; Packages has no repository publication or signing plan. | **Adoptable:** choose channels, signing/trust roots, mirrors, release promotion, and support lifetime. |
| Whole-system update workflow | Lock, space check, cache pruning, snapshot, sleep inhibition, system packages, migrations, post-update hooks, AUR, mise, orphan review, log analysis, and restart/reboot handling. Direct pacman upgrades have a guard. `bin/omarchy-update`, `bin/omarchy-update-*`, `default/libalpm/hooks/`. | **Not planned.** No distro updater, update notifications, or unattended-update policy. | **Adoptable:** define the transaction boundary, prompts, failure/retry rules, and responsibility for non-system tools. |
| Migrations and multi-user changes | Timestamped scripts run in order with per-user success markers; some scripts add machine-wide markers. Login notification detects pending work; fresh installs mark shipped migrations complete. `bin/omarchy-migrate`, `bin/omarchy-migrate-notify`, `bin/omarchy-provision-user`, `migrations/`. | **Not planned.** E7 guarantees atomic session-config generation, not distribution migration. | **Adoptable:** define schema/default migrations, root versus user scope, retries, and preservation of user edits. |
| Config ownership, refresh, reinstall | Packaged defaults plus user configs; refresh commands back up/reset selected configs; reinstall can reset broad user state; post-install restores pacman configuration and updates locate/udev state. `config/`, `default/`, `bin/omarchy-refresh-config`, `bin/omarchy-reinstall-configs`, `install/post-install/`. | **Partly.** Core E3/E7 and Guide C7/D1 define personal overrides, atomic builds, additive app config, and package/dev ownership. No distro-wide reset or migration UI. | **Mixed:** extend the ownership contract across shipped applications and system files without silently overwriting personal configuration. |
| Personal backups and sync | Root snapshots, local config backups, optional Dropbox, and rsync helpers are present; no general scheduled home/off-machine backup and tested restore policy was found. `default/snapper/root`, `bin/omarchy-refresh-config`, `bin/omarchy-install-service-dropbox`, `default/bash/fns/rsyncing`. | **Not planned.** No backup policy in the reviewed plans. **Also not established as a complete Omarchy policy.** | **Adoptable components:** decide whether the distro promises personal-data backups and how users restore them; do not label root rollback a backup solution. |

## 3. Security and authentication

| Area | Omarchy fact and source | Scottland coverage | Decision a Scottland distro needs |
|---|---|---|---|
| Lock and privilege prompts | Quickshell lock and polkit plugins; separate password/fingerprint PAM flows; ten failed password attempts and 120-second lockout configured. `shell/plugins/lock/`, `shell/plugins/polkit/`, `bin/omarchy-apply-lock`, `install/config/increase-lockout-limit.sh`. | **Not planned for the distro.** Adapter O1 inherits the shell; O11's reporting of real lock state is explicitly not built. No standalone locker/auth-agent selection. | **Mixed:** choose trusted lock/auth components and secure session/suspend integration; ordinary spatial windows cannot substitute for a session lock. |
| Secret storage | GNOME keyring/libsecret installed; a passwordless default keyring is seeded; SDDM PAM keyring lines are removed to avoid a conflicting encrypted login keyring. `install/omarchy-base.packages`, `install/user/default-keyring.sh`, `install/login/sddm.sh`, `migrations/1784508556.sh`. | **Not planned.** Service handover does not choose how a distro stores/unlocks secrets. | **Adoptable:** decide keyring encryption/unlock policy and browser/application secret integration. |
| Fingerprint and FIDO2 | Optional fingerprint enrollment for sudo/polkit/lock, including lid-aware handling; FIDO2 setup for sudo/polkit uses a root-owned authfile. Removal paths exist. `bin/omarchy-setup-security-fingerprint`, `bin/omarchy-setup-security-fido2`, `bin/omarchy-remove-security-*`, `migrations/1784818437.sh`, `migrations/1787494718.sh`. | **Not planned.** No authentication-method or enrollment plan. | **Adoptable:** choose optional methods and recovery/removal UX; FIDO2 here is not evidence of disk unlock or Secure Boot support. |
| Privilege and device-access policy | Docker daemon enabled, but docker-group membership is opt-in as root-equivalent; passwordless-sudo toggle; migration removes broad input-group access. `install/config/docker.sh`, `bin/omarchy-setup-security-sudoless-docker`, `bin/omarchy-sudo-passwordless`, `migrations/1787865477.sh`. | **Not planned for a distro baseline.** Packages' narrowly scoped session-switch polkit helper is an adapter mechanism. | **Adoptable:** define privileged helpers, optional elevated access, and raw-input/device permission policy. |
| Firewall and remote access | UFW denies incoming/allows outgoing, allows LocalSend and container DNS, and adds ufw-docker rules. Optional SSH setup authorizes a key, hardens password authentication, and opens the firewall; removal closes it. `install/config/firewall.sh`, `bin/omarchy-setup-security-sshd`, `bin/omarchy-remove-security-sshd`, `migrations/1788124236.sh`. | **Not planned.** No distro network-exposure policy. | **Adoptable:** select baseline firewall, service exceptions, SSH defaults, and optional VPN/streaming exposure. |
| Extension and theme trust | Shell plugins are explicitly arbitrary unsandboxed code with a confirmation flow; theme staging excludes executable Lua and selected app configs from git-installed themes; git URLs are checked before cloning. `bin/omarchy-plugin-add`, `bin/omarchy-theme-set`, `bin/omarchy-git-url-check`. | **Partly.** PE defines schema/config expressions and separate packaging, but not third-party distribution, expression safety, trust, or update rules. | **Mixed:** define trusted themes/widgets/extensions and how executable integrations are installed and updated. |

## 4. Hardware, displays, memory, and power

| Area | Omarchy fact and source | Scottland coverage | Decision a Scottland distro needs |
|---|---|---|---|
| GPU/firmware/acceleration stack | Hardware-selected NVIDIA open or legacy DKMS drivers, early KMS, Intel/AMD/Apple Vulkan selection, Intel media acceleration, SOF firmware and IPU7 camera support; separate fwupd update command. `install/hardware/nvidia.sh`, `install/hardware/vulkan.sh`, `install/hardware/intel/`, `bin/omarchy-update-firmware`. | **Not planned.** Stock Wayfire and goo fallback are desktop choices, not a driver/kernel support matrix. | **Adoptable:** select supported hardware, firmware/licensing policy, driver delivery, and qualification criteria. |
| Model-specific quirks | ASUS ROG/Panther Lake display, touchpad and mic fixes; Framework QMK/RGB/audio; Dell haptics/text/speaker tuning; Surface keyboard/firmware; Apple SPI/T2/NVMe/Broadcom; Lenovo bass speakers; Tuxedo backlight; Motorcomm Ethernet; Synaptics/F-key fixes. `install/hardware/all.sh`, its referenced scripts, `install/user/hardware/`, `default/audio/tunings/`, `default/udev/`. | **Not planned.** Guide C8's generic compositor/app approach does not define a distro hardware-quirk programme. | **Mixed:** adopt applicable OS fixes, replace Hyprland-specific fixes, and define supported models and quirk retirement. |
| Displays, docking, scale, brightness | Preferred modes and auto placement/scale; GDK scale default 2; monitor scaling/mirroring/clamshell controls and unplug recovery; internal, DDC/CI, and Apple display brightness. `config/hypr/monitors.lua`, `bin/omarchy-hyprland-monitor-*`, `bin/omarchy-brightness-display*`, `default/systemd/user/omarchy-recover-internal-monitor.service`. | **Partly.** Core defines per-screen zones and output crossing; L15's importing display scale is not built. No standalone monitor configuration/docking policy. | **Mixed:** retain brightness infrastructure while defining scale, hotplug, mirroring, and how windows/rails survive output changes. |
| Power profiles and thermals | power-profiles-daemon enabled; remembered AC/battery preferences, default performance on AC when available and balanced otherwise; Intel thermald/lpmd and hardware fan support. `install/config/enable-services.sh`, `bin/omarchy-powerprofiles-*`, `install/hardware/intel/thermald.sh`, `install/hardware/intel/lpmd.sh`. | **Not planned.** No distro power/performance defaults. | **Adoptable:** choose power defaults, hardware thermal services, and persistent user controls. |
| Idle, lock, suspend, lid, battery | Shell config sets screensaver 150 seconds and lock 300 seconds; Stay Awake control; pre-suspend lock monitoring; undocked lid-close locking and clamshell reconciliation; low-battery threshold 10%. `config/omarchy/shell.json`, `shell/plugins/services/idle/`, `shell/plugins/services/battery/Service.qml`, `bin/omarchy-system-sleep-*`, `bin/omarchy-system-lid-close`. | **Not planned for the distro.** Fullscreen FS1 concerns attention/DND, not a complete power or lock policy. | **Mixed:** select timers and secure suspend/lid behavior, then reconcile idle inhibition with full-screen focus. |
| Hibernation and memory pressure | Optional disk-swap/resume setup with removal/check helpers; RAM-sized zstd zram at priority 100; oomd targets app.slice rather than compositor session.slice; shorter shutdown timeout. `bin/omarchy-hibernation-*`, `default/systemd/zram-generator.conf.d/90-omarchy.conf`, `default/systemd/user/app.slice.d/10-oomd.conf`, `default/systemd/faster-shutdown.conf`. | **Not planned.** No swap, hibernation, OOM, or shutdown-timeout policy. | **Mixed:** choose memory/recovery defaults and ensure Scottland's service placement preserves the intended compositor protection. |

## 5. Connectivity, peripherals, and everyday system services

| Area | Omarchy fact and source | Scottland coverage | Decision a Scottland distro needs |
|---|---|---|---|
| Network and DNS | NetworkManager plus systemd-resolved; retire competing networkd/iwd state and avoid waiting for network at desktop startup; Wi-Fi selection, band pinning, QR sharing, DNS provider control and speed tests; timezone-based regulatory domain. `install/hardware/network.sh`, `install/hardware/set-wireless-regdom.sh`, `install/config/enable-services.sh`, `bin/omarchy-network-*`, `bin/omarchy-dns`, `shell/plugins/panels/network/`. | **Not planned.** Adapter O1 can expose the inherited UI without selecting a standalone stack. | **Adoptable:** select networking/DNS defaults, credential UI, regulatory handling, and offline behavior. |
| Printing and discovery | CUPS, filters, administration GUI, print applet and Avahi supplied/enabled. Automatic `cups-browsed` discovery is explicitly removed by a later migration, despite retained override files. `install/omarchy-base.packages`, `install/config/enable-services.sh`, `config/autostart/print-applet.desktop`, `migrations/1788009111.sh`, `etc-overrides/cups-cups-files.conf`. | **Not planned.** No printing/scanning policy. | **Adoptable:** choose printer setup/discovery and support scope; scanning is not established by these sources. |
| Bluetooth | BlueZ service, pairing agent and shell controls; power state persisted through rfkill; WirePlumber A2DP autoconnect configuration. `install/hardware/bluetooth.sh`, `default/systemd/user/bt-agent.service`, `bin/omarchy-bluetooth-*`, `config/wireplumber/wireplumber.conf.d/bluetooth-a2dp-autoconnect.conf`. | **Not planned.** No standalone pairing/service plan. | **Adoptable:** choose pairing, reconnection, adapter-power defaults, and UI. |
| Audio and media control | PipeWire components and WirePlumber; default devices, stream movement, volume/mute, mic LED, MPRIS source switching, audio panel, recovery and speaker-filter tuning. `install/omarchy-other.packages`, `bin/omarchy-audio-*`, `bin/omarchy-restart-audio`, `default/audio/`, `shell/plugins/services/media/`. | **Partly.** Core A13 follows system volume/mute for interface sounds; it does not select or configure the audio stack. | **Adoptable:** choose backend, routing/mixer UI, media controls, and hardware tuning. |
| Files, removable media, local sharing | Nautilus, GVfs MTP/NFS/SMB, GNOME Disks, udiskie automount, locate, LocalSend, and Nautilus sharing/transcode extensions. `install/omarchy-base.packages`, `default/hypr/autostart.lua`, `default/nautilus-python/extensions/`, `bin/omarchy-menu-share`, `install/post-install/localdb.sh`. | **Not planned.** No file-manager, automount, search, or sharing defaults. | **Adoptable:** select file/device workflows, automount policy, and network-share integration. |
| Optional network/cloud services | Install/remove flows for Tailscale/Taildrop, Dropbox, 1Password, Sunshine/Moonlight, NordVPN and ONCE; service-specific launchers/panels and firewall setup. `bin/omarchy-install-service-*`, `bin/omarchy-remove-service-*`, `bin/omarchy-tailscale-*`, `shell/plugins/panels/tailscale/`, `shell/plugins/panels/dropbox/`. | **Not planned.** DN's networked agent attention source is not a VPN/cloud-service catalogue. | **Adoptable:** decide which integrations are offered, their defaults, data handling, and removal responsibilities. |
| Time, weather, and external lookups | Clock/calendar and timezone picker; weather location can be saved or IP-detected through wttr.in, and weather commands request an external service. `shell/plugins/panels/clock/`, `shell/plugins/panels/weather/`, `bin/omarchy-menu-timezone`, `bin/omarchy-weather-location`, `bin/omarchy-weather-status`, `bin/omarchy-update-time`. | **Partly.** Core A15 defines Geoclue, saved coordinates and optional IP fallback for Sunlight; no distro weather/provider, time-service, or broader location/privacy policy. | **Adoptable:** choose time/location services, network lookup defaults, and controls for external data requests. |

## 6. Applications, terminals, development, and gaming

The two package lists contain **147 base entries and 57 other entries**. The
second is an ISO-availability list including optional/hardware packages, not
a statement that all 204 entries are installed on every computer. Dependency
closure and separate settings/editor packages add more; the lists are not the
whole installed-system manifest.

| Area | Omarchy fact and source | Scottland coverage | Decision a Scottland distro needs |
|---|---|---|---|
| General application catalogue | Base list chooses Chromium, Nautilus, Neovim, LibreOffice Fresh, Evince, Obsidian, Xournal++, Pinta, imv, mpv, CLIamp, OBS, Kdenlive, Moonlight, Omacalc/Omacut/Omawrite, among system/developer tools. Preinstall removal/restoration is an explicit feature. `install/omarchy-base.packages`, `bin/omarchy-install-preinstalls`, `bin/omarchy-remove-preinstalls`. | **Not planned.** DN does not define office, productivity, communication, graphics, or media defaults. | **Adoptable:** choose base versus optional apps, proprietary/network-service policy, footprint, and uninstall expectations. |
| Default handlers and browser integration | MIME choices include Nautilus, imv, Evince, mpv, nvim, Chromium and HEY mailto. Browser chooser and managed policies; Chromium Copy URL/yt-dlp/WhatsApp extensions and native hosts; alternative-browser installation. `default/applications/mimeapps.list`, `config/chromium/`, `default/chromium/`, `default/firefox/policies.json`, `bin/omarchy-default-browser`, `bin/omarchy-install-browser`, `install/config/browser-policy.sh`. | **Not planned.** No distro MIME, browser, email, extension, or browser-policy plan. | **Adoptable:** choose handlers, browser integrations, secret backend, and what is opt-in. |
| Terminal choice and launch behavior | Foot is the shipped xdg-terminal-exec preference; config/templates also support Alacritty, Kitty and Ghostty. Launch helpers reuse cwd and offer persistent tmux/Herdr sessions. `default/xdg-terminal-exec/hyprland-xdg-terminals.list`, `config/foot/`, `config/alacritty/`, `config/kitty/`, `config/ghostty/`, `bin/omarchy-default-terminal`, `bin/omarchy-launch-terminal*`. | **Partly.** DN explicitly reserves the terminal choice to the distro; core currently ships `ghostty`. No final distro choice or cwd/session-launch contract. | **Mixed:** choose terminal and launch semantics; window identity and user-requested presentation must suit rails and center focus. |
| Terminal window identity | Shipped tmux enables titles and sets `#h:#W`. `config/tmux/tmux.conf`. | **Covered as a planned difference.** DN explicitly chooses `#S on #h` and `MOSH_TITLE_NOPREFIX=1` so widget titles identify session and host. | **Spatial:** carry that recorded default into distro provisioning; do not silently inherit Omarchy's title string. |
| Shell and TUI environment | Bash defaults, Starship, completion/aliases, fzf/zoxide, Git/lazygit, btop, dua, tmux/Herdr, archive/drive/worktree/SSH-reconnect helpers, remote PATH and keepalives; a Work/tries directory. `default/bash/`, `config/starship.toml`, `config/git/config`, `config/lazygit/config.yml`, `config/herdr/`, `install/user/mise-work.sh`, `install/config/ssh-command-path.sh`, `install/config/ssh-keepalive.sh`. | **Partly.** DN covers terminal titles; no general shell, dotfile, TUI, or work-directory plan. | **Adoptable:** choose command-line ergonomics and defaults, preserving the terminal-title decision. |
| Web apps and TUI launchers | Sixteen shipped desktop entries include Basecamp, Discord, Google services, HEY, WhatsApp, X, YouTube, Zoom, Docker and Disk Usage; generic install/remove/launch tools and HEY/Zoom protocol handlers. `applications/`, `bin/omarchy-webapp-*`, `bin/omarchy-tui-*`, `bin/omarchy-launch-webapp`, `bin/omarchy-launch-tui`. | **Not planned.** No distro launcher catalogue, web-app/browser contract, or TUI wrapper policy. | **Mixed:** reusable desktop entries need meaningful titles/app identity and Scottland-aware presentation, not workspace addressing. |
| Developer runtimes, editors, containers | mise-managed Node/tool wrappers; optional language environments, editor installers, Docker/Compose/buildx/lazydocker and database-container setup; packaged Neovim setup/reset. `install/user/mise.sh`, `bin/omarchy-install-dev-env`, `bin/omarchy-install-editor-*`, `bin/omarchy-install-docker-dbs`, `/usr/bin/omarchy-nvim-setup`, `/usr/bin/omarchy-nvim-refresh`. | **Not planned.** A coding-agent skill is not a general development-environment plan. | **Adoptable:** choose preinstalled versus lazy-installed tooling, editor defaults, runtime upgrades, and container privilege policy. |
| Coding agents and assistance | Lazy mise wrappers for multiple agent CLIs; default-agent picker (no agent chosen by default); desktop-agent installers; agent usage panel; Omarchy and crash-diagnosis skills; crash-to-agent handoff. `install/user/mise.sh`, `install/user/first-run/setup-agent.hook`, `default/agents/skills/`, `shell/plugins/agents/`, `bin/omarchy-agent*`. | **Partly.** DN chooses agentd attention, terminal bells and skill linking; Core E6 supplies the skill. No distro agent catalogue, auth/onboarding, usage UI, or assistance policy. | **Mixed:** select tooling separately from the already-planned attention contract; agents must not take center focus unasked (tenet 5). |
| Games, controllers, Windows | Optional Steam, Lutris, Heroic, Battle.net, RetroArch, cloud gaming, GPU lib32 drivers and Xbox controllers; Windows VM install/start/stop/status/remove workflow. `bin/omarchy-install-gaming-*`, `bin/omarchy-remove-gaming-*`, `bin/omarchy-games-retro-*`, `bin/omarchy-windows-vm`. | **Not planned.** No compatibility/gaming/VM catalogue. | **Mixed:** adopt optional tooling if wanted; controller/fullscreen/capture interactions still need the spatial desktop's focus contract. |

## 7. Shell, interaction, and system controls

| Area | Omarchy fact and source | Scottland coverage | Decision a Scottland distro needs |
|---|---|---|---|
| Shell, bar, tray, menus, settings entry points | Quickshell shell with configurable bar/plugins, tray, clock/calendar, weather, agents, updates, network/audio/power/display panels and hierarchical menu. `config/omarchy/shell.json`, `default/omarchy/omarchy-menu.jsonc`, `shell/plugins/`, `bin/omarchy-bar`, `bin/omarchy-menu`. | **Partly.** Adapter O1 selects the stock shell on Omarchy; Core has Scottland Settings and rail widgets. No standalone shell, launcher, tray, or system-control selection. | **Spatial:** decide where persistent controls live alongside rails and how much screen space they occupy; a workspace indicator has no Scottland role (tenet 2). |
| Window model and window shortcuts | Hyprland tiling/workspace layouts, window rules, app exceptions, opacity, pop-out and layout toggles. `default/hypr/windows.lua`, `default/hypr/apps/`, `default/hypr/workspace-layouts.lua`, `default/hypr/bindings/tiling.lua`, `bin/omarchy-hyprland-window-*`. | **Covered by a different design.** Guide C2/C3 and Core layout/window invariants choose Wayfire, zones, rails, scaling, and no workspaces; Adapter O4 excludes layout/workspace shortcuts. | **Spatial:** ship Scottland's recorded model; Omarchy tiling/workspace features are not gaps to fill. |
| Application/media shortcuts and discoverability | Default application, clipboard, utility, media and dictation bindings; live keybinding menus; Lua helpers and user overrides. `default/hypr/bindings/`, `config/hypr/bindings.lua`, `bin/omarchy-menu-keybindings`, `bin/omarchy-menu-tmux-keybindings`. | **Partly.** DN names opinionated bindings; Adapter O3–O9/O13 import and arbitrate host bindings; Core K1 supports focused-surface key layers. No complete distro binding/help catalogue. | **Mixed:** select non-window shortcuts and a discoverable cheatsheet around Scottland's reserved feature keys. |
| Launch versus focus/present | Launch-or-focus helpers identify windows and dispatch Hyprland focus; application launches use Omarchy/uwsm helpers. `bin/omarchy-launch-or-focus*`, `bin/omarchy-hyprland-focus-app`, `default/hypr/helpers.lua`. | **Partly.** Core L28 distinguishes input-backed activation from attention, and L30 defines explicit presentation into the center; Adapter O10 covers launching. Distro launcher selection/integration remains open. | **Spatial:** choose which user actions call present, plain focus, or launch; an unsolicited request cannot move focus (tenets 3/5). |
| Notifications, reminders, attention | Shell notification daemon with history/DND, notification commands, lightweight reminders and battery warnings. `shell/plugins/notifications/`, `shell/plugins/reminders/`, `bin/omarchy-notification-*`, `bin/omarchy-reminder`. | **Partly.** DN requires a daemon with DND and focus hook; Core FS1 and Adapter O17 define full-screen suppression; agentd/bell attention is planned. No standalone daemon choice or full notification policy. | **Spatial:** choose delivery/history and attention routing while preserving explicit DND and deferring interruptions in full screen (tenets 1/5/6). |
| Clipboard, emoji, capture, portals | Clipboard history/paste and emoji UI; screenshot/recording with webcam, region freeze/selection, QR/OCR, annotation tools and transcode helpers; GTK/Hyprland portals and share-picker configuration. `shell/plugins/clipboard/`, `shell/plugins/emojis/`, `bin/omarchy-capture-*`, `bin/omarchy-transcode*`, `config/hypr/xdph.conf`, `install/omarchy-base.packages`. | **Not planned as a standalone suite.** Adapter environment/service handover does not select a Wayfire portal/capture stack or clipboard retention policy. | **Mixed:** choose utilities and privacy defaults; define whether capture/picking targets an app surface, its scaled appearance, or its widget. |
| User extensibility and automation | Configurable menu extensions; plugin add/clone/enable/update/remove; hooks for theme, font, boot, update, battery and refresh; persistent state/toggles and a discoverable CLI dispatcher. `config/omarchy/extensions/`, `config/omarchy/hooks/`, `bin/omarchy-plugin-*`, `bin/omarchy-hook*`, `bin/omarchy-state`, `bin/omarchy`. | **Partly.** Core supplies overrides, hooks, widget/attention interfaces and key layers; PE is a separate extension point. No unified distro CLI/plugin ecosystem or lifecycle. | **Mixed:** decide which interfaces belong to the distro versus core and how their discovery, compatibility and trust are maintained. |

## 8. Input, typography, themes, and identity

| Area | Omarchy fact and source | Scottland coverage | Decision a Scottland distro needs |
|---|---|---|---|
| Touchpad, mouse, touch, device controls | Clickfinger, non-natural scrolling, repeat rate 40/delay 250, numlock, terminal-specific scroll factors, input-device toggles and recovery commands. `default/hypr/input.lua`, `bin/omarchy-toggle-input-device`, `bin/omarchy-toggle-touchpad`, `bin/omarchy-toggle-touchscreen`, `bin/omarchy-restart-trackpad`. | **Partly.** Core L17/L21–L26 define gestures/scrolling/touch compatibility; DN chooses tapping/tap-and-drag. No complete distro device-control UI or input-default set. | **Mixed:** keep Scottland gestures and tapping; decide remaining device defaults and controls without copying conflicting Hyprland behavior. |
| Layouts, compose, input methods, dictation | Provisioned console/XKB layout; Latin-first fallback for non-Latin bindings; CapsLock compose and both-Shifts Caps Lock; XCompose; supervised fcitx5 with GTK/Qt support; optional Voxtype/model setup. `install/provisioning/setup-form.sh`, `default/hypr/input.lua`, `default/xcompose`, `install/user/xcompose.sh`, `default/environment.d/10-omarchy-fcitx.conf`, `default/systemd/user/omarchy-fcitx5.service`, `bin/omarchy-voxtype-*`. | **Partly.** Adapter O12 records keyboard layout switching as not built; no standalone layout/IME/compose/dictation selection. | **Mixed:** choose international input and accessible text-entry workflows while making shortcuts work across layouts. |
| Fonts, fallback, text size | JetBrainsMono Nerd Font default monospace; Liberation aliases, Noto CJK/emoji and Arabic/Urdu fallback, iA Writer and icon fonts; font install/select and cross-shell/GTK/terminal text sizing. `default/fontconfig/conf.avail/50-omarchy.conf`, `default/fonts/`, `install/omarchy-base.packages`, `bin/omarchy-font-*`, `bin/omarchy-display-text-size`. | **Partly.** Core's referenced window-hint design follows desktop typography; no distro font family/coverage or global scaling selection. | **Mixed:** adopt font infrastructure while deciding legibility and accessibility across full-size windows, scaled periphery, and widgets. |
| Palette and light/dark integration | Themes have `colors.toml`, wallpapers and app assets; template generation, live application refresh, GTK mode/icons, terminal OSC and hardware keyboard colors. Twenty-two stock theme directories; initial theme Tokyo Night. `themes/`, `default/themed/`, `bin/omarchy-theme-set*`, `install/user/theme.sh`. | **Covered at architectural-plan level.** DN/PE choose a separate semantic palette engine, desktop schema, distro-owned themes and Scottland reader/adapter refactor; concept not started. Core A8/A15 and Adapter O15/O18 cover current following/scheduling. | **Spatial:** retain the planned semantic attention colors; exact schema, algorithms, app integrations and named distro themes still need completion. |
| Theme catalogue, wallpaper, night light | Theme/wallpaper picker, git installation/update/removal, background cycling/cache, user overrides, themed Plymouth; hyprsunset night-light control is separate from light/dark themes and is neutral by default. `bin/omarchy-theme-*`, `bin/omarchy-plymouth-*`, `shell/plugins/background/`, `shell/plugins/services/nightlight/`, `config/hypr/hyprsunset.conf`. | **Partly.** DN covers cross-app/day-night themes; PE explicitly excludes wallpaper extraction. Adapter O12 night light is not built. No complete wallpaper catalogue/distribution or standalone color-temperature plan. | **Mixed:** choose wallpaper/theme delivery and night-light implementation; do not treat solar theme mode as screen-temperature control. |
| Branding and visual assets | OS identity and support URLs, logos/icon font, fastfetch/About/screensaver branding, boot/login art and theme previews. `etc-overrides/os-release`, `logo.svg`, `icon.png`, `default/fonts/omarchy/`, `default/plymouth/`, `default/sddm/`, `bin/omarchy-branding-*`. | **Partly.** Guide C1 fixes credit to Scott Jenson and independence; no distro branding/art/identity package. | **Adoptable mechanisms:** choose distro identity, credits, artwork and boot-to-desktop presentation; Omarchy's brand itself is not Scottland's. |

## 9. Documentation, support, diagnostics, and delivery ownership

| Area | Omarchy fact and source | Scottland coverage | Decision a Scottland distro needs |
|---|---|---|---|
| Learning and support channels | Learn menu points to Omarchy manual, Hyprland/Arch documentation, Neovim and Bash references; OS metadata identifies homepage/manual, Discord support and GitHub issue tracker; update failure points to the community. `default/omarchy/omarchy-menu.jsonc`, `etc-overrides/os-release`, `bin/omarchy-launch-discord-community`, `bin/omarchy-update`. | **Not planned for a distro.** Scottland's contributor/design docs and agent skill do not define installation/recovery docs or user-support commitments. | **Adoptable structure:** define onboarding/manual, support ownership, bug/security reporting, and what upstream versus distro supports. |
| Diagnostics and crash assistance | Debug collection, idle diagnostics, log upload to Omarchy's log service with 24-hour expiry parameter; systemd crash watcher and optional default-agent diagnosis; disk/network performance utilities. `/usr/bin/omarchy-debug`, `/usr/bin/omarchy-debug-idle`, `/usr/bin/omarchy-upload-log`, `bin/omarchy-crash-watch`, `bin/omarchy-agent-crash`, `bin/omarchy-disk-speedtest`. | **Partly.** Core E5/session logs and project test tools diagnose Scottland; no distro support bundle, upload/privacy policy, or crash-assistance service. | **Mixed:** choose collection/redaction/consent and support tooling; crash notices must follow the attention contract. |
| Maintaining and publishing the distribution | Omarchy exposes package channels, migration authoring, dev-link, local package testing, shell UI/theme previews and refresh/reinstall workflows. `bin/omarchy-channel-set`, `bin/omarchy-dev-*`, `bin/omarchy-upgrade-to-quattro`. These prove maintenance interfaces, not a complete published release-engineering process. | **Not planned for the distro.** Packages and Guide define desktop development/build discipline, not image CI, repository operation, promotion or release support. | **Adoptable structure:** decide distro repositories/ownership, image/package qualification, release gates, infrastructure and support commitments. |

## Inventory coverage and limitations

The filesystem inventory enumerated every file-bearing entry in the requested
trees and the `/usr/bin/omarchy` / `omarchy-*` surface, then inspected setup
orchestration, defaults, package lists, command metadata/implementations, shell
manifests, and migrations relevant to the decisions above. Related helpers are
grouped by product decision rather than counted as separate product features.
Binary artwork was inventoried by path, not visually reviewed.

| Installed source tree | Files counted | Decisions traced here |
|---|---:|---|
| `install/` | 83 | Provisioning, config, user/first-run, hardware, login, post-install, package lists. |
| `config/` | 41 | User seeds for apps, compositor, shell, input, autostart, hooks and templates. |
| `default/` | 181 | System/app defaults, boot/login, services, command-line environment, themes, menus, skills. |
| `bin/` | 441 | CLI dispatcher and application, hardware, service, setup, update, maintenance and development commands. |
| `applications/` | 16 | Shipped desktop launchers, including web apps and TUIs. |
| `migrations/` | 106 | Existing-install transitions; latest behavior checked against retained older assets. |
| `themes/` | 249 | Twenty-two named themes with palettes, wallpaper/preview/unlock assets and app overrides. |
| `shell/` | 183 | Shell services, controls and UI; 29 plugin manifests, plus built-in bar components. |
| `etc-overrides/` | 7 | Additional packaged OS identity, printing, shell, NSS, Plymouth and authentication overrides. |

There are **446 `/usr/bin` names**, including symlinks, matching `omarchy` or
`omarchy-*`. The five not represented by a same-named file in the installed
`bin/` tree are `omarchy-debug`, `omarchy-debug-idle`, `omarchy-upload-log`,
`omarchy-nvim-setup`, and the `omarchy-nvim-refresh` alias. They are included
above. The nine source trees contain **1,307 file entries** in total.
Source-file counts include file symlinks and are an inventory measure,
not a code-size or capability measure.

Three limits matter when using this as a planning list:

1. **The installed package is not the complete ISO/build repository.** It
   establishes provisioning contracts and downstream storage assumptions, but
   does not establish partition sizes, the exact encryption default in the ISO,
   full dual-boot support, Secure Boot/TPM policy, image reproducibility, or a
   complete release pipeline. Those are unresolved evidence questions, not
   invented Omarchy facts.
2. **Omarchy itself does not establish every possible distro capability here.**
   A full personal-backup policy, scanning stack, comprehensive accessibility
   suite, or localization/support matrix was not found. Fonts, text scaling,
   IME and dictation are concrete choices, not proof of complete accessibility
   coverage. These are potential questions for both projects, not advantages
   credited to Omarchy without evidence.
3. **Source presence is not installed-state verification.** Hardware gates,
   optional installers, user settings and migration history matter. For
   example, `cups-browsed` override files remain while migration 1788009111
   removes automatic discovery; FIDO2 enrollment is optional; a package list
   entry is not proof that it is running. No personal config or credentials
   were used to infer shipped defaults.

## Validation record

This report changes no product behavior and adds no product invariants. The
coverage rows record planning status; they do not authorize implementation or
promote any existing invariant to verified. No recommendations have been
adopted by this report.

Research was read-only against osanwe's installed sources. No desktop commands,
tests, builds, services, sessions, reloads, deployment, or plumbus operations
were run. Validation consists of source/reference existence checks, inventory
counts, comparison with the named Scottland plans, and `git diff --check`.
No runtime or visual claims were tested; there are no screenshots or performance
measurements. End-to-end installation, update, rollback and hardware behavior
remain unverified by this research.
