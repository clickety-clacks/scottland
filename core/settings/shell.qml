import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import Quickshell.Wayland

// Scottland layout settings. Changes apply to the running session immediately (windows rescale
// live) while a click-through overlay shows the five zones on every screen. Save writes
// ~/.config/scottland/layout.ini; Cancel or Escape restores the values from when it opened.
//
// The scale curve sets how windows shrink across the side zones: t = 0 is the center zone's
// edge, t = 1 the widget rail. The endpoints are the largest and smallest scale; points in
// between shape it, joined by the same monotone cubic spline the plugin uses.
ShellRoot {
  id: root

  readonly property string ctl: Quickshell.env("SCOTTLAND_CTL") || "scottland-ctl"
  readonly property string layoutFile: Quickshell.env("SCOTTLAND_LAYOUT_FILE")
    || (Quickshell.env("HOME") + "/.config/scottland/layout.ini")

  readonly property var defaults: ({ center_width: 33.333, rail_width: 2, blend_width: 40,
    curve: [{ x: 0, y: 1 }, { x: 1, y: 0.2 }] })
  property var original: null
  property real centerWidth: defaults.center_width
  property real railWidth: defaults.rail_width
  property real blendWidth: defaults.blend_width
  property var curvePoints: defaults.curve
  readonly property real maxScale: curvePoints[0].y
  readonly property real minScale: curvePoints[curvePoints.length - 1].y
  property bool loaded: false
  // Settings the running Scottland doesn't support yet (its plugin predates them).
  property var unsupported: []
  readonly property var settingNames: ({ center_width: "Center zone width", rail_width: "Widget rail width",
    min_scale: "Smallest scale", max_scale: "Largest scale", scale_curve: "Scale curve",
    blend_width: "Center edge softness" })

  readonly property color panelColor: "#f21c1d22"
  readonly property color textColor: "#e6e6e9"
  readonly property color dimText: "#9a9ba3"
  readonly property color accent: "#7aa2f7"
  readonly property color railColor: "#e0af68"

  // --- The curve (mirrors scale_curve_t in the plugin) --------------------------------------

  function curveText(points) {
    return points.map(p => p.x.toFixed(3) + ":" + p.y.toFixed(3)).join(" ")
  }

  function parseCurve(text) {
    const points = String(text || "").trim().split(/\s+/).filter(w => w.includes(":")).map(w => {
      const [x, y] = w.split(":").map(parseFloat)
      return { x: Math.min(1, Math.max(0, x)), y: Math.min(1, Math.max(0.05, y)) }
    }).filter(p => !isNaN(p.x) && !isNaN(p.y)).sort((a, b) => a.x - b.x)
    if (points.length < 2 || points[0].x > 0 || points[points.length - 1].x < 1) return null
    return points
  }

  function slopes(points) {
    const n = points.length, delta = [], m = new Array(n).fill(0)
    for (let i = 0; i + 1 < n; i++)
      delta.push((points[i + 1].y - points[i].y) / Math.max(1e-6, points[i + 1].x - points[i].x))
    m[0] = delta[0]; m[n - 1] = delta[n - 2]
    for (let i = 1; i + 1 < n; i++) {
      if (delta[i - 1] * delta[i] <= 0) continue
      const h0 = points[i].x - points[i - 1].x, h1 = points[i + 1].x - points[i].x
      const w1 = 2 * h1 + h0, w2 = h1 + 2 * h0
      m[i] = (w1 + w2) / (w1 / delta[i - 1] + w2 / delta[i])
    }
    return m
  }

  function curveAt(points, t) {
    t = Math.min(1, Math.max(0, t))
    const m = slopes(points)
    let i = 0
    while (i + 2 < points.length && t > points[i + 1].x) i++
    const a = points[i], b = points[i + 1], h = b.x - a.x
    const u = (t - a.x) / h, u2 = u * u, u3 = u2 * u
    const y = (2 * u3 - 3 * u2 + 1) * a.y + (u3 - 2 * u2 + u) * h * m[i]
      + (-2 * u3 + 3 * u2) * b.y + (u3 - u2) * h * m[i + 1]
    return Math.min(1, Math.max(0.05, y))
  }

  // --- Live updates, load, save ---------------------------------------------------------------

  // Live updates go through one long-running scottland-ctl; a short timer coalesces drags.
  Process {
    id: live
    command: [root.ctl, "stdin"]
    stdinEnabled: true
    running: true
  }

  Timer {
    id: push
    interval: 30
    onTriggered: root.send(root.centerWidth, root.railWidth, root.curvePoints, root.blendWidth)
  }

  function send(center, rail, points, blend) {
    live.write("center_width " + center.toFixed(3) + "\n"
      + "rail_width " + rail.toFixed(3) + "\n"
      + "blend_width " + blend.toFixed(1) + "\n"
      + "scale_curve " + curveText(points) + "\n"
      // Endpoints double as min/max for anything reading those.
      + "min_scale " + points[points.length - 1].y.toFixed(3) + "\n"
      + "max_scale " + points[0].y.toFixed(3) + "\n")
  }

  onCenterWidthChanged: if (loaded) push.restart()
  onRailWidthChanged: if (loaded) push.restart()
  onCurvePointsChanged: if (loaded) push.restart()
  onBlendWidthChanged: if (loaded) push.restart()

  Process {
    id: reader
    command: [root.ctl, "get"]
    running: true
    stdout: StdioCollector {
      onStreamFinished: {
        try {
          const values = JSON.parse(text)
          root.unsupported = values.unsupported || []
          for (const name of ["center_width", "rail_width", "min_scale", "max_scale", "blend_width"])
            if (values[name] === undefined) values[name] = root.savedValue(name, null)
          if (values.scale_curve === undefined) values.scale_curve = root.savedText("scale_curve")
          const points = root.parseCurve(values.scale_curve) || [
            { x: 0, y: values.max_scale !== null ? Math.max(values.max_scale, values.min_scale || 0.05) : 1 },
            { x: 1, y: values.min_scale !== null ? values.min_scale : 0.2 }]
          root.original = { center_width: values.center_width ?? root.defaults.center_width,
            rail_width: values.rail_width ?? root.defaults.rail_width,
            blend_width: values.blend_width ?? root.defaults.blend_width, curve: points }
        } catch (e) {
          root.original = root.defaults
        }
        root.centerWidth = root.original.center_width
        root.railWidth = root.original.rail_width
        root.blendWidth = root.original.blend_width
        root.curvePoints = root.original.curve
        root.loaded = true
      }
    }
  }

  FileView {
    id: saved
    path: root.layoutFile
    printErrors: false
    blockLoading: true  // read before the running values arrive
  }

  // Values last saved by this app; used for settings the running session can't report.
  function savedText(name) {
    const match = saved.text().match(new RegExp("^" + name + "\\s*=\\s*(.*)$", "m"))
    return match ? match[1].trim() : ""
  }

  function savedValue(name, fallback) {
    const value = parseFloat(savedText(name))
    return isNaN(value) ? fallback : value
  }

  function save() {
    push.stop()
    send(centerWidth, railWidth, curvePoints, blendWidth)
    saved.setText("# Written by Scottland settings.\n[scottland]\n"
      + "center_width = " + centerWidth.toFixed(3) + "\n"
      + "rail_width = " + railWidth.toFixed(3) + "\n"
      + "blend_width = " + blendWidth.toFixed(1) + "\n"
      + "scale_curve = " + curveText(curvePoints) + "\n"
      + "min_scale = " + minScale.toFixed(3) + "\n"
      + "max_scale = " + maxScale.toFixed(3) + "\n")
    quitSoon.start()
  }

  function cancel() {
    push.stop()
    if (original) send(original.center_width, original.rail_width, original.curve, original.blend_width)
    quitSoon.start()
  }

  Timer {
    id: quitSoon
    interval: 150  // let the last values reach the compositor
    onTriggered: Qt.quit()
  }

  // --- Zone overlay on every screen: click-through, drawn above windows -------------------------

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

        // Continuous zones: shading deepens as the curve takes windows smaller.
        component CurveShade: Rectangle {
          property bool mirrored: false
          function shade(t) { return Qt.rgba(0.48, 0.64, 0.97, 0.04 + 0.30 * (1 - root.curveAt(root.curvePoints, t))) }
          height: parent.height
          gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0; color: shade(mirrored ? 0 : 1) }
            GradientStop { position: 0.25; color: shade(mirrored ? 0.25 : 0.75) }
            GradientStop { position: 0.5; color: shade(0.5) }
            GradientStop { position: 0.75; color: shade(mirrored ? 0.75 : 0.25) }
            GradientStop { position: 1; color: shade(mirrored ? 1 : 0) }
          }
        }
        CurveShade { x: zones.rail; width: Math.max(0, zones.centerLeft - zones.rail) }
        CurveShade { x: zones.centerRight; width: Math.max(0, zones.width - zones.rail - zones.centerRight); mirrored: true }

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
          text: Math.round(root.maxScale * 100) + "% → " + Math.round(root.minScale * 100) + "%"
        }
        ZoneLabel {
          x: Math.min(zones.width - zones.rail - width - 4, (zones.centerRight + zones.width - zones.rail - width) / 2)
          y: parent.height * 0.3
          text: Math.round(root.minScale * 100) + "% ← " + Math.round(root.maxScale * 100) + "%"
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

  // --- The settings panel: an overlay surface, so the layout never scales it -------------------

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

        Text {
          Layout.fillWidth: true
          visible: root.unsupported.length > 0
          wrapMode: Text.WordWrap
          color: root.railColor
          font.pixelSize: 13
          text: "Restart Scottland to use: " + root.unsupported.map(n => root.settingNames[n] || n).join(", ")
            + ". This session's plugin is older; your values are still saved."
        }

        // The zone settings: one stack of rows, each row a slider (ParameterStack.qml).
        ParameterStack {
          id: zoneSettings
          readonly property real screenWidth: Quickshell.screens.length > 0 ? Quickshell.screens[0].width : 0
          Layout.fillWidth: true
          foreground: root.textColor
          accent: root.accent
          focus: true
          rows: [
            { id: "blend_width", label: "Center edge softness", min: 0, max: 300, step: 1, largeStep: 10,
              display: v => Math.round(v) + " pt" },
            { id: "center_width", label: "Center zone width", min: 10, max: 90, step: 0.5, largeStep: 5,
              display: v => v.toFixed(1) + "%" },
            { id: "rail_width", label: "Widget rail width", min: 0.5, max: 10, step: 0.1, largeStep: 1,
              display: v => v.toFixed(1) + "% · " + Math.round(screenWidth * v / 100) + " pt" },
          ]
          values: ({ blend_width: root.blendWidth, center_width: root.centerWidth, rail_width: root.railWidth })
          opening: root.original ? ({ blend_width: root.original.blend_width, center_width: root.original.center_width,
            rail_width: root.original.rail_width }) : ({})
          onChanged: (id, value) => {
            if (id === "blend_width") root.blendWidth = value
            else if (id === "center_width") root.centerWidth = value
            else if (id === "rail_width") root.railWidth = value
          }
        }

        // Scale curve editor.
        ColumnLayout {
          Layout.fillWidth: true
          spacing: 4

          RowLayout {
            Layout.fillWidth: true
            Text { text: "Scale across the side zones"; color: root.textColor; font.pixelSize: 14; Layout.fillWidth: true }
            Text {
              color: root.accent; font.pixelSize: 14
              text: editor.hovered >= 0
                ? Math.round(root.curvePoints[editor.hovered].y * 100) + "% at " + Math.round(root.curvePoints[editor.hovered].x * 100) + "% across"
                : Math.round(root.maxScale * 100) + "% → " + Math.round(root.minScale * 100) + "%"
            }
          }

          Item {
            id: editor
            Layout.fillWidth: true
            Layout.preferredHeight: 190
            property int dragging: -1
            property int hovered: -1
            readonly property real plotLeft: 40
            readonly property real plotTop: 8
            readonly property real plotWidth: width - plotLeft - 10
            readonly property real plotHeight: height - plotTop - 24

            function toX(t) { return plotLeft + t * plotWidth }
            function toY(s) { return plotTop + (1 - (s - 0.05) / 0.95) * plotHeight }
            function fromX(px) { return Math.min(1, Math.max(0, (px - plotLeft) / plotWidth)) }
            function fromY(py) { return Math.min(1, Math.max(0.05, 0.05 + (1 - (py - plotTop) / plotHeight) * 0.95)) }

            function pointAt(px, py) {
              const points = root.curvePoints
              for (let i = 0; i < points.length; i++)
                if (Math.hypot(toX(points[i].x) - px, toY(points[i].y) - py) <= 10) return i
              return -1
            }

            function move(index, px, py) {
              const points = root.curvePoints.map(p => ({ x: p.x, y: p.y }))
              const last = points.length - 1
              points[index].y = fromY(py)
              if (index > 0 && index < last)  // endpoints stay at the zone edges
                points[index].x = Math.min(points[index + 1].x - 0.02, Math.max(points[index - 1].x + 0.02, fromX(px)))
              root.curvePoints = points
            }

            Canvas {
              id: canvas
              anchors.fill: parent
              onPaint: {
                const ctx = getContext("2d")
                ctx.reset()
                // Grid and labels.
                ctx.strokeStyle = "#3a3b44"; ctx.lineWidth = 1
                ctx.fillStyle = root.dimText; ctx.font = "11px sans-serif"
                for (const s of [0.25, 0.5, 0.75, 1.0]) {
                  const y = editor.toY(s)
                  ctx.beginPath(); ctx.moveTo(editor.plotLeft, y); ctx.lineTo(editor.plotLeft + editor.plotWidth, y); ctx.stroke()
                  ctx.fillText(Math.round(s * 100) + "%", 4, y + 4)
                }
                ctx.fillText("5%", 4, editor.toY(0.05) + 4)
                ctx.strokeRect(editor.plotLeft, editor.plotTop, editor.plotWidth, editor.plotHeight)
                ctx.fillText("center edge", editor.plotLeft, editor.height - 6)
                ctx.fillText("rail", editor.plotLeft + editor.plotWidth - 20, editor.height - 6)
                // The curve.
                ctx.strokeStyle = root.accent; ctx.lineWidth = 2
                ctx.beginPath()
                for (let i = 0; i <= 120; i++) {
                  const t = i / 120, x = editor.toX(t), y = editor.toY(root.curveAt(root.curvePoints, t))
                  if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
                }
                ctx.stroke()
                // Points: endpoints square, interior round.
                const points = root.curvePoints
                for (let i = 0; i < points.length; i++) {
                  const x = editor.toX(points[i].x), y = editor.toY(points[i].y)
                  ctx.fillStyle = (i === editor.hovered || i === editor.dragging) ? "#ffffff" : root.accent
                  ctx.strokeStyle = "#1c1d22"; ctx.lineWidth = 2
                  ctx.beginPath()
                  if (i === 0 || i === points.length - 1) ctx.rect(x - 6, y - 6, 12, 12)
                  else ctx.arc(x, y, 6, 0, 2 * Math.PI)
                  ctx.fill(); ctx.stroke()
                }
              }
              Connections {
                target: root
                function onCurvePointsChanged() { canvas.requestPaint() }
              }
              Connections {
                target: editor
                function onHoveredChanged() { canvas.requestPaint() }
                function onDraggingChanged() { canvas.requestPaint() }
                function onWidthChanged() { canvas.requestPaint() }
              }
            }

            MouseArea {
              anchors.fill: parent
              hoverEnabled: true
              acceptedButtons: Qt.LeftButton | Qt.RightButton
              cursorShape: editor.hovered >= 0 ? Qt.PointingHandCursor : Qt.CrossCursor

              onPressed: mouse => {
                let index = editor.pointAt(mouse.x, mouse.y)
                if (mouse.button === Qt.RightButton) {
                  if (index > 0 && index < root.curvePoints.length - 1)
                    root.curvePoints = root.curvePoints.filter((_, i) => i !== index)
                  return
                }
                if (index < 0) {
                  // Add a point where clicked, between the endpoints.
                  const t = editor.fromX(mouse.x)
                  if (t <= 0.02 || t >= 0.98) return
                  const points = root.curvePoints.map(p => ({ x: p.x, y: p.y }))
                  points.push({ x: t, y: editor.fromY(mouse.y) })
                  points.sort((a, b) => a.x - b.x)
                  root.curvePoints = points
                  index = editor.pointAt(mouse.x, mouse.y)
                }
                editor.dragging = index
              }
              onPositionChanged: mouse => {
                if (editor.dragging >= 0) editor.move(editor.dragging, mouse.x, mouse.y)
                else editor.hovered = editor.pointAt(mouse.x, mouse.y)
              }
              onReleased: editor.dragging = -1
              onExited: editor.hovered = -1
              onDoubleClicked: mouse => {
                const index = editor.pointAt(mouse.x, mouse.y)
                if (index > 0 && index < root.curvePoints.length - 1)
                  root.curvePoints = root.curvePoints.filter((_, i) => i !== index)
              }
            }
          }

          Text {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            color: root.dimText
            font.pixelSize: 12
            text: "Drag the square ends for the largest and smallest scale. Click to add a point, drag to shape the curve, double-click or right-click a point to remove it."
          }
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
              root.blendWidth = root.defaults.blend_width
              root.curvePoints = root.defaults.curve
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
