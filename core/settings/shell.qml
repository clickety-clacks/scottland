import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import Quickshell.Wayland

// Scottland layout settings. Sliders apply to the running session immediately (windows rescale
// live) while a click-through overlay shows the five zones on every screen. Save writes
// ~/.config/scottland/layout.ini; Cancel or Escape restores the values from when it opened.
ShellRoot {
  id: root

  readonly property string ctl: Quickshell.env("SCOTTLAND_CTL") || "scottland-ctl"
  readonly property string layoutFile: Quickshell.env("SCOTTLAND_LAYOUT_FILE")
    || (Quickshell.env("HOME") + "/.config/scottland/layout.ini")

  readonly property var defaults: ({ center_width: 33.333, rail_width: 2, min_scale: 0.2, max_scale: 1 })
  property var original: null
  property real centerWidth: defaults.center_width
  property real railWidth: defaults.rail_width
  property real minScale: defaults.min_scale
  property real maxScale: defaults.max_scale
  property bool loaded: false

  readonly property color panelColor: "#f21c1d22"
  readonly property color textColor: "#e6e6e9"
  readonly property color dimText: "#9a9ba3"
  readonly property color accent: "#7aa2f7"
  readonly property color railColor: "#e0af68"

  function scaleAt(fraction) {
    // fraction: position across the screen, 0..1 (mirrors the plugin's place())
    const fromMiddle = Math.abs(fraction - 0.5)
    const centerHalf = centerWidth / 200
    const toRail = 0.5 - railWidth / 100
    if (fromMiddle <= centerHalf) return 1
    if (fromMiddle >= toRail) return minScale
    const t = (fromMiddle - centerHalf) / Math.max(0.0001, toRail - centerHalf)
    const top = Math.max(maxScale, minScale)
    return top - t * (top - minScale)
  }

  // Live updates go through one long-running scottland-ctl; a short timer coalesces slider drags.
  Process {
    id: live
    command: [root.ctl, "stdin"]
    stdinEnabled: true
    running: true
  }

  Timer {
    id: push
    interval: 30
    onTriggered: root.send(root.centerWidth, root.railWidth, root.minScale, root.maxScale)
  }

  function send(center, rail, scale, top) {
    live.write("center_width " + center.toFixed(3) + "\n"
      + "rail_width " + rail.toFixed(3) + "\n"
      + "min_scale " + scale.toFixed(3) + "\n"
      + "max_scale " + top.toFixed(3) + "\n")
  }

  onCenterWidthChanged: if (loaded) push.restart()
  onRailWidthChanged: if (loaded) push.restart()
  onMinScaleChanged: if (loaded) push.restart()
  onMaxScaleChanged: if (loaded) push.restart()

  Process {
    id: reader
    command: [root.ctl, "get"]
    running: true
    stdout: StdioCollector {
      onStreamFinished: {
        try {
          const values = JSON.parse(text)
          root.original = values
          root.centerWidth = values.center_width
          root.railWidth = values.rail_width
          root.minScale = values.min_scale
          root.maxScale = values.max_scale !== undefined ? values.max_scale : root.defaults.max_scale
        } catch (e) {
          root.original = root.defaults
        }
        root.loaded = true
      }
    }
  }

  FileView {
    id: saved
    path: root.layoutFile
    printErrors: false
  }

  function save() {
    push.stop()
    send(centerWidth, railWidth, minScale, maxScale)
    saved.setText("# Written by Scottland settings.\n[scottland]\n"
      + "center_width = " + centerWidth.toFixed(3) + "\n"
      + "rail_width = " + railWidth.toFixed(3) + "\n"
      + "min_scale = " + minScale.toFixed(3) + "\n"
      + "max_scale = " + maxScale.toFixed(3) + "\n")
    quitSoon.start()
  }

  function cancel() {
    push.stop()
    if (original) send(original.center_width, original.rail_width, original.min_scale,
      original.max_scale !== undefined ? original.max_scale : defaults.max_scale)
    quitSoon.start()
  }

  Timer {
    id: quitSoon
    interval: 150  // let the last values reach the compositor
    onTriggered: Qt.quit()
  }

  // Zone overlay on every screen: click-through, drawn above windows.
  Variants {
    model: Quickshell.screens

    PanelWindow {
      required property var modelData
      screen: modelData
      anchors { top: true; bottom: true; left: true; right: true }
      color: "transparent"
      exclusionMode: ExclusionMode.Ignore
      mask: Region {}
      WlrLayershell.layer: WlrLayer.Overlay
      WlrLayershell.namespace: "scottland-zones"

      Item {
        id: zones
        anchors.fill: parent
        readonly property real rail: width * root.railWidth / 100
        readonly property real centerLeft: width * (0.5 - root.centerWidth / 200)
        readonly property real centerRight: width * (0.5 + root.centerWidth / 200)

        // Continuous zones: shading deepens as windows get smaller.
        Rectangle {
          x: zones.rail; width: Math.max(0, zones.centerLeft - zones.rail); height: parent.height
          gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0; color: Qt.rgba(0.48, 0.64, 0.97, 0.30) }
            GradientStop { position: 1; color: Qt.rgba(0.48, 0.64, 0.97, 0.04) }
          }
        }
        Rectangle {
          x: zones.centerRight; width: Math.max(0, zones.width - zones.rail - zones.centerRight); height: parent.height
          gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0; color: Qt.rgba(0.48, 0.64, 0.97, 0.04) }
            GradientStop { position: 1; color: Qt.rgba(0.48, 0.64, 0.97, 0.30) }
          }
        }

        // Widget rails.
        Rectangle { x: 0; width: zones.rail; height: parent.height; color: Qt.rgba(0.88, 0.69, 0.41, 0.45) }
        Rectangle { x: zones.width - zones.rail; width: zones.rail; height: parent.height; color: Qt.rgba(0.88, 0.69, 0.41, 0.45) }

        // Center zone edges.
        Rectangle { x: zones.centerLeft - 1; width: 2; height: parent.height; color: root.accent }
        Rectangle { x: zones.centerRight - 1; width: 2; height: parent.height; color: root.accent }

        component ZoneLabel: Rectangle {
          property alias text: label.text
          width: label.implicitWidth + 16; height: label.implicitHeight + 8
          radius: 4; color: "#cc1c1d22"
          Text { id: label; anchors.centerIn: parent; color: root.textColor; font.pixelSize: 14 }
        }

        ZoneLabel {
          x: (zones.centerLeft + zones.centerRight - width) / 2; y: parent.height * 0.3
          text: "Center · 100%"
        }
        ZoneLabel {
          x: Math.max(zones.rail + 4, (zones.rail + zones.centerLeft - width) / 2); y: parent.height * 0.3
          text: Math.round(Math.max(root.maxScale, root.minScale) * 100) + "% → " + Math.round(root.minScale * 100) + "%"
        }
        ZoneLabel {
          x: Math.min(zones.width - zones.rail - width - 4, (zones.centerRight + zones.width - zones.rail - width) / 2)
          y: parent.height * 0.3
          text: Math.round(root.minScale * 100) + "% ← " + Math.round(Math.max(root.maxScale, root.minScale) * 100) + "%"
        }
        ZoneLabel {
          x: zones.rail + 6; y: parent.height * 0.3 + 44
          text: "◀ widget rail"
        }
        ZoneLabel {
          x: zones.width - zones.rail - width - 6; y: parent.height * 0.3 + 44
          text: "widget rail ▶"
        }
      }
    }
  }

  // The settings panel itself: an overlay surface, so the layout never scales it.
  PanelWindow {
    anchors.bottom: true
    margins.bottom: 48
    implicitWidth: 560
    implicitHeight: panel.implicitHeight + 40
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.OnDemand
    WlrLayershell.namespace: "scottland-settings"

    Rectangle {
      anchors.fill: parent
      radius: 10
      color: root.panelColor
      border.color: "#3a3b44"

      focus: true
      Keys.onEscapePressed: root.cancel()
      Keys.onReturnPressed: root.save()

      ColumnLayout {
        id: panel
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 20 }
        spacing: 14

        Text {
          text: "Scottland layout"
          color: root.textColor
          font.pixelSize: 18
          font.bold: true
        }

        component SettingRow: ColumnLayout {
          id: row
          property string title
          property string valueText
          property alias from: slider.from
          property alias to: slider.to
          property alias stepSize: slider.stepSize
          property real value
          signal moved(real value)
          Layout.fillWidth: true
          spacing: 2

          RowLayout {
            Layout.fillWidth: true
            Text { text: row.title; color: root.textColor; font.pixelSize: 14; Layout.fillWidth: true }
            Text { text: row.valueText; color: root.accent; font.pixelSize: 14 }
          }
          Slider {
            id: slider
            Layout.fillWidth: true
            value: row.value
            onMoved: row.moved(value)
          }
        }

        SettingRow {
          title: "Center zone width"
          valueText: root.centerWidth.toFixed(1) + "% of the screen"
          from: 10; to: 90; stepSize: 0.5
          value: root.centerWidth
          onMoved: v => root.centerWidth = v
        }

        SettingRow {
          readonly property real screenWidth: Quickshell.screens.length > 0 ? Quickshell.screens[0].width : 0
          title: "Widget rail width"
          valueText: root.railWidth.toFixed(1) + "% · " + Math.round(screenWidth * root.railWidth / 100) + " pt"
          from: 0.5; to: 10; stepSize: 0.1
          value: root.railWidth
          onMoved: v => root.railWidth = v
        }

        SettingRow {
          title: "Largest scale (next to the center)"
          valueText: Math.round(Math.max(root.maxScale, root.minScale) * 100) + "%"
          from: 0.05; to: 1; stepSize: 0.01
          value: root.maxScale
          onMoved: v => root.maxScale = v
        }

        SettingRow {
          title: "Smallest scale (next to the rails)"
          valueText: Math.round(root.minScale * 100) + "%"
          from: 0.05; to: 1; stepSize: 0.01
          value: root.minScale
          onMoved: v => root.minScale = v
        }

        RowLayout {
          Layout.fillWidth: true
          Layout.topMargin: 4
          spacing: 10

          Button {
            text: "Defaults"
            onClicked: {
              root.centerWidth = root.defaults.center_width
              root.railWidth = root.defaults.rail_width
              root.minScale = root.defaults.min_scale
              root.maxScale = root.defaults.max_scale
            }
          }
          Item { Layout.fillWidth: true }
          Button { text: "Cancel"; onClicked: root.cancel() }
          Button { text: "Save"; highlighted: true; onClicked: root.save() }
        }
      }
    }
  }
}
