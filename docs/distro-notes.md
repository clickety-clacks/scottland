# Notes for a Scottland distro

Scottland is a desktop (a window manager on Wayfire) that installs on top of a distro (Omarchy, Ubuntu).
A Scottland distro, if there is one, is a separate project: installer, package choices, default
configuration, update channel. It would depend on Scottland the way Omarchy depends on Hyprland.

These are defaults that belong to such a distro rather than to Scottland's core, collected as they
come up, so the decision isn't lost while there is nowhere better to put them.

## Defaults to ship

- **Terminal window titles read "session on host"** (Mike, 2026-10-01), the same on every machine:
  tmux `set -g set-titles on` and `set -g set-titles-string '#S on #h'`, and no `[mosh] ` prefix
  (mosh honors `MOSH_TITLE_NOPREFIX=1` in the client's environment). The window title is what a
  widget's card shows first (WG10), so it should say what's in the window and where.
- **agentd ([clickety-clacks/agentd](https://github.com/clickety-clacks/agentd)) as an attention
  source**: the distro installs agentd and wires it into Scottland's attention as a configured source
  (docs/attention.md: AT3, a source fed over the network, and AT5, resolving an agent to its terminal
  window), so an agent that needs the user lights up its window or widget wherever the agent runs.
- **Coding agents ring the terminal's bell when they need the user**, so Scottland's attention
  (WG15, docs/attention.md) sees them, including over mosh where desktop notifications from the
  remote host can't arrive (Claude Code: `preferredNotifChannel: terminal_bell`).
- **Touchpad tapping and tap-and-drag on**: double-tap-and-drag with drag lock (L22) and the three-finger tap-and-drag resize (L24) ride on
  libinput's tap-and-drag.
- **A notification daemon with a do-not-disturb control, and a `focus.d` hook for it**, so full screen
  holds notifications (FS1, tenet 6). On Omarchy the adapter provides this.
- **Which terminal Super+Enter opens** (`command_terminal`, today `ghostty` in core's shipped config).
- **Linking Scottland's agent skill into the coding agents a user runs** (today core's
  autostart.d/02-link-agent-skills picks Claude Code, Codex, pi, Hermes).
- **The opinionated keybindings** in core's shipped config that follow Omarchy's choices.

- **Cross-app theming engine and day/night themes**: a Scottland distro chooses and runs a
  cross-app theme engine (Omarchy’s, an equivalent, or Aether) and its default named day/night
  themes. Scottland core follows the active light/dark preference and palette; its optional solar
  schedule requests only a mode. The Omarchy adapter applies Omarchy themes when their mode is wrong.

## Not distro material

App-compatibility knowledge that every Scottland install needs stays in Scottland's shipped config
(for example, which apps take touchscreen scrolling as wheel steps), and the Omarchy adapter stays an
integration package (it could become its own repository).
