import QtQuick

// Shared geometry, typography and session colors for every settings control.
QtObject {
  property var palette: ({})
  readonly property color background: palette.background || "#1c1d22"
  readonly property color foreground: palette.foreground || "#e6e6e9"
  readonly property color accent: palette.accent || "#7aa2f7"
  readonly property color muted: Qt.rgba(foreground.r, foreground.g, foreground.b, 0.65)
  readonly property color separator: Qt.rgba(foreground.r, foreground.g, foreground.b, 0.14)
  readonly property string family: palette.font_family || Qt.application.font.family
  readonly property real textScale: Math.max(0.5, Math.min(3, Number(palette.text_scale) || 1))
  readonly property int radius: 12
  function tint(amount) { return Qt.rgba(accent.r, accent.g, accent.b, amount) }
}
