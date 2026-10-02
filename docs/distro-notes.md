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
- **Which terminal Super+Enter opens** (`command_terminal`, today `ghostty` in core's shipped config).
- **Linking Scottland's agent skill into the coding agents a user runs** (today core's
  autostart.d/02-link-agent-skills picks Claude Code, Codex, pi, Hermes).
- **The opinionated keybindings** in core's shipped config that follow Omarchy's choices.

## Not distro material

App-compatibility knowledge that every Scottland install needs stays in Scottland's shipped config
(for example, which apps take touchscreen scrolling as wheel steps), and the Omarchy adapter stays an
integration package (it could become its own repository).
