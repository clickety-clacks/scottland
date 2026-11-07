# On-screen display

The on-screen display is a brief response to an explicit
`scottland-widget osd --message TEXT [--icon NAME] [--progress N] [--duration MS]`
call. The existing session shell selects the focused output and hosts one panel. A later
call replaces the content and restarts its timer instead of creating another panel. The
caller returns after the panel is visible.

The panel uses the current session palette, font and text scale. It sits near the bottom
of the focused output and overlays fullscreen content. It takes no keyboard focus and
has an empty input region so pointer events pass through. An unavailable icon theme
entry leaves the message and progress display intact. The default lifetime is 1500 ms.
The panel runs inside the existing session shell.

| ID | Behavior | Source status |
|---|---|---|
| OSD1–OSD5 | An explicit call shows the message with optional icon and progress, acknowledges when shown, replaces existing content, and hides after the requested duration or 1500 ms by default. | Command and panel source present; behavior verification pending |
| OSD6 | Invalid arguments or an unreachable session produce a nonzero result and one-line error without showing a panel. | Command validation and session transport source present; behavior verification pending |
| OSD7–OSD9 | The panel does not take focus or pointer input, follows the current palette and text scale, and adds no separate long-running process. | Panel source present; behavior verification pending |
| OSD10 | Scottland audio and display tools use the OSD call for their own changes. | Caller behavior is separate from this panel change |
| OSD11 | The panel overlays fullscreen content on the focused output only. | Panel source present; behavior verification pending |
