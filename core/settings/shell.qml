import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import Quickshell.Wayland

// Scottland layout and goo settings. Changes apply to the running session immediately while
// an overlay with input only on border handles shows the five zones on every screen. Save writes
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
  readonly property var gooControls: [
    { name: "goo_thickness", hint: "Width of the resting goo border. Higher makes it thicker; lower makes it finer.", title: "Border thickness", initial: 13, low: 4, high: 40, step: 1 },
    { name: "goo_reach", hint: "How far goo reaches toward nearby windows. Higher joins wider gaps; lower keeps it close.", title: "Reach", initial: 24, low: 6, high: 70, step: 1 },
    { name: "goo_thinning", hint: "How much a bridge thins the borders feeding it. Higher thins more; lower keeps them fuller.", title: "Bridge draw", initial: 0.45, low: 0, high: 1, step: 0.01 },
    { name: "goo_swell", hint: "How much goo grows when a window is revealed. Higher swells more; lower stays nearer its resting width.", title: "Swell", initial: 0.7, low: 0, high: 2, step: 0.01 },
    { name: "goo_noise", hint: "Unevenness along the goo edge. Higher makes it lumpier; lower makes it smooth.", title: "Mess", initial: 0.32, low: 0, high: 0.9, step: 0.01 },
    { name: "goo_lump", hint: "Size of the uneven patches. Higher makes broad lumps; lower makes small bumps.", title: "Lump size", initial: 190, low: 40, high: 500, step: 5 },
    { name: "goo_drift", hint: "How fast lumps wander while goo is awake. Higher moves faster; zero holds them still.", title: "Drift", initial: 0.12, low: 0, high: 0.6, step: 0.01 },
    { name: "goo_wave_speed", hint: "How fast ripples travel through connected goo. Higher travels faster; lower moves slowly.", title: "Wave speed", initial: 0.28, low: 0.05, high: 0.5, step: 0.01 },
    { name: "goo_wave_damp", hint: "How long ripples linger. Higher fades slowly; lower settles sooner.", title: "Wave persistence", initial: 0.985, low: 0.9, high: 0.998, step: 0.001 },
    { name: "goo_wave_height", hint: "How much ripples move the goo edge. Higher makes bigger waves; zero hides their motion.", title: "Wave height", initial: 0.55, low: 0, high: 1.5, step: 0.01 },
    { name: "goo_spread", hint: "How quickly nearby dye colors mix. Higher blends faster; lower keeps colors more local.", title: "Dye spread", initial: 0.45, low: 0, high: 0.9, step: 0.01 },
    { name: "goo_swirl", hint: "How strongly dye flows around in goo. Higher stirs more; zero stops the swirling.", title: "Dye swirl", initial: 0.9, low: 0, high: 3, step: 0.05 },
    { name: "goo_release", hint: "How quickly a window renews its dye. Higher shows state colors sooner; lower lets old colors linger.", title: "Dye release", initial: 0.06, low: 0.005, high: 0.3, step: 0.005 },
    { name: "goo_shine", hint: "Brightness of reflected highlights. Higher looks glossier; zero removes the shine.", title: "Shine", initial: 0.75, low: 0, high: 1.5, step: 0.01 },
    { name: "goo_relief", hint: "Apparent depth and background bending. Higher looks more rounded; lower looks flatter.", title: "Relief", initial: 5, low: 0.5, high: 12, step: 0.1 }]
  function gooDefaults() {
    const values = { goo: true, goo_falloff: "" }
    for (const c of gooControls) values[c.name] = c.initial
    return values
  }
  property var gooValues: gooDefaults()
  property bool gooTab: false
  readonly property var exponentialPoints: Array.from({ length: 17 }, (_, i) => ({ x: i / 16, y: Math.exp(-i / 4) }))
  property var gooPoints: exponentialPoints
  readonly property var editorPoints: gooTab ? gooPoints : curvePoints
  function setEditorPoints(points) {
    if (gooTab) {
      gooPoints = points
      gooValues = Object.assign({}, gooValues, { goo_falloff: curveText(points) })
    } else curvePoints = points
  }
  function setGoo(name, value) { gooValues = Object.assign({}, gooValues, { [name]: value }) }
  function sendGoo(values) {
    sendBatch(values)
  }
  function sendBatch(values) {
    const supported = {}
    for (const key of Object.keys(values))
      if (unsupported.indexOf(key) < 0) supported[key] = values[key]
    live.write("batch " + JSON.stringify(supported) + "\n")
  }
  onGooValuesChanged: if (loaded && !push.running) push.start()
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

  function parseCurve(text, minimum = 0.05) {
    const points = String(text || "").trim().split(/\s+/).filter(w => w.includes(":")).map(w => {
      const [x, y] = w.split(":").map(parseFloat)
      return { x: Math.min(1, Math.max(0, x)), y: Math.min(1, Math.max(minimum, y)) }
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

  function curveAt(points, t, minimum = 0.05) {
    t = Math.min(1, Math.max(0, t))
    const m = slopes(points)
    let i = 0
    while (i + 2 < points.length && t > points[i + 1].x) i++
    const a = points[i], b = points[i + 1], h = b.x - a.x
    const u = (t - a.x) / h, u2 = u * u, u3 = u2 * u
    const y = (2 * u3 - 3 * u2 + 1) * a.y + (u3 - 2 * u2 + u) * h * m[i]
      + (-2 * u3 + 3 * u2) * b.y + (u3 - u2) * h * m[i + 1]
    return Math.min(1, Math.max(minimum, y))
  }

  // --- Live updates, load, save ---------------------------------------------------------------

  // Live updates go through one long-running scottland-ctl; a short timer coalesces drags.
  Process {
    id: live
    command: [root.ctl, "stdin"]
    stdinEnabled: true
    running: true
    stdout: SplitParser { onRead: data => { if (data === "flushed") Qt.quit() } }
  }

  Timer {
    id: push
    interval: 30
    onTriggered: { root.send(root.centerWidth, root.railWidth, root.curvePoints, root.blendWidth); root.sendGoo(root.gooValues) }
  }

  function send(center, rail, points, blend) {
    sendBatch({ center_width: Number(center.toFixed(3)), rail_width: Number(rail.toFixed(3)),
      blend_width: Number(blend.toFixed(1)), scale_curve: curveText(points),
      // Endpoints double as min/max for anything reading those.
      min_scale: Number(points[points.length - 1].y.toFixed(3)), max_scale: Number(points[0].y.toFixed(3)) })
  }

  onCenterWidthChanged: if (loaded && !push.running) push.start()
  onRailWidthChanged: if (loaded && !push.running) push.start()
  onCurvePointsChanged: if (loaded && !push.running) push.start()
  onBlendWidthChanged: if (loaded && !push.running) push.start()

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
        const goo = root.gooDefaults()
        let values = {}
        try { values = JSON.parse(text) } catch (e) {}
        for (const key of Object.keys(goo)) {
          if (values[key] !== undefined) goo[key] = values[key]
          else if (key === "goo") goo[key] = root.savedText(key) === "true"
          else if (key === "goo_falloff") goo[key] = root.savedText(key)
          else goo[key] = root.savedValue(key, goo[key])
        }
        root.original = Object.assign({}, root.original, { goo: goo })
        root.gooValues = Object.assign({}, goo)
        root.gooPoints = root.parseCurve(goo.goo_falloff, 0) || root.exponentialPoints
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
    blockWrites: true   // finish the small layout file before the IPC acknowledgement quits
    atomicWrites: true
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
    sendGoo(gooValues)
    saved.setText("# Written by Scottland settings.\n[scottland]\n"
      + "center_width = " + centerWidth.toFixed(3) + "\n"
      + "rail_width = " + railWidth.toFixed(3) + "\n"
      + "blend_width = " + blendWidth.toFixed(1) + "\n"
      + "scale_curve = " + curveText(curvePoints) + "\n"
      + "min_scale = " + minScale.toFixed(3) + "\n"
      + "max_scale = " + maxScale.toFixed(3) + "\n"
      + Object.keys(gooValues).map(k => k + " = " + gooValues[k] + "\n").join(""))
    live.write("flush\n")
  }

  function cancel() {
    push.stop()
    if (original) { send(original.center_width, original.rail_width, original.curve, original.blend_width); sendGoo(original.goo) }
    live.write("flush\n")
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
      mask: Region {
        Region { item: centerLeftHandle }
        Region { item: centerRightHandle }
        Region { item: railLeftHandle }
        Region { item: railRightHandle }
        Region { item: blendLeftHandle }
        Region { item: blendRightHandle }
      }
      WlrLayershell.keyboardFocus: WlrKeyboardFocus.OnDemand
      // Keep the controls above the zones even after a border receives pointer focus.
      WlrLayershell.layer: WlrLayer.Top
      WlrLayershell.namespace: "scottland-zones"

      Item {
        id: zones
        anchors.fill: parent
        focus: true
        Keys.onEscapePressed: root.cancel()
        Keys.onReturnPressed: root.save()
        readonly property real rail: width * root.railWidth / 100
        readonly property real centerLeft: width * (0.5 - root.centerWidth / 200)
        readonly property real centerRight: width * (0.5 + root.centerWidth / 200)

        // Match place() in scottland.cpp: softness is in logical points, capped at half
        // the available side span. Percent widths use this output, never the first screen.
        readonly property real blend: Math.min(root.blendWidth, Math.max(0, centerLeft - rail) / 2)
        readonly property real blendLeft: centerLeft - blend
        readonly property real blendRight: centerRight + blend

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
        CurveShade { x: zones.rail; width: Math.max(0, zones.blendLeft - zones.rail) }
        CurveShade { x: zones.blendRight; width: Math.max(0, zones.width - zones.rail - zones.blendRight); mirrored: true }

        // Widget rails.
        Rectangle { x: 0; width: zones.rail; height: parent.height; color: Qt.rgba(0.88, 0.69, 0.41, 0.45) }
        Rectangle { x: zones.width - zones.rail; width: zones.rail; height: parent.height; color: Qt.rgba(0.88, 0.69, 0.41, 0.45) }

        // The blend band is outside the full-scale center, distinct from the scale curve.
        Rectangle { x: zones.blendLeft; width: zones.blend; height: parent.height
          color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.28) }
        Rectangle { x: zones.centerRight; width: zones.blend; height: parent.height
          color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.28) }

        component BorderHandle: Item {
          id: handle
          required property string setting
          required property real edge
          required property bool rightSide
          required property int lane
          required property color tint
          // At zero softness (or overlapping center/rail edges), preserve every target.
          readonly property bool crowded: setting === "center_width"
            ? zones.blend < 14 || Math.abs(zones.centerLeft - zones.rail) < 14
            : Math.abs(edge - (rightSide ? zones.centerRight : zones.centerLeft)) < 14
          x: Math.round(edge) - 6
          y: crowded ? lane * zones.height / 3 : 0
          width: 12
          height: crowded ? zones.height / 3 : zones.height
          Rectangle {
            anchors.horizontalCenter: parent.horizontalCenter
            width: borderMouse.containsMouse || borderMouse.pressed ? 4 : 2
            height: parent.height
            color: borderMouse.containsMouse || borderMouse.pressed ? root.textColor : handle.tint
          }
          // A visible grip identifies the input strip; shading elsewhere is click-through.
          Rectangle {
            anchors.centerIn: parent
            width: 10; height: 36; radius: 4
            color: borderMouse.containsMouse || borderMouse.pressed ? root.textColor : handle.tint
          }
          MouseArea {
            id: borderMouse
            anchors.fill: parent
            hoverEnabled: true
            preventStealing: true
            cursorShape: Qt.SizeHorCursor
            property real startX
            property real startValue
            onPressed: mouse => {
              zones.forceActiveFocus()
              startX = mapToItem(zones, mouse.x, mouse.y).x
              startValue = handle.setting === "center_width" ? root.centerWidth
                : handle.setting === "rail_width" ? root.railWidth : zones.blend
            }
            onPositionChanged: mouse => {
              if (!pressed) return
              const dx = mapToItem(zones, mouse.x, mouse.y).x - startX
              const direction = handle.rightSide ? 1 : -1
              zoneSettings.typed = ""
              if (handle.setting === "center_width") zoneSettings.set(1, startValue + direction * dx * 200 / zones.width)
              else if (handle.setting === "rail_width") zoneSettings.set(2, startValue - direction * dx * 100 / zones.width)
              else zoneSettings.set(0, startValue + direction * dx)
            }
          }
        }
        BorderHandle { id: centerLeftHandle; setting: "center_width"; edge: zones.centerLeft; rightSide: false; lane: 0; tint: root.accent }
        BorderHandle { id: centerRightHandle; setting: "center_width"; edge: zones.centerRight; rightSide: true; lane: 0; tint: root.accent }
        BorderHandle { id: railLeftHandle; setting: "rail_width"; edge: zones.rail; rightSide: false; lane: 1; tint: root.railColor }
        BorderHandle { id: railRightHandle; setting: "rail_width"; edge: zones.width - zones.rail; rightSide: true; lane: 1; tint: root.railColor }
        BorderHandle { id: blendLeftHandle; setting: "blend_width"; edge: zones.blendLeft; rightSide: false; lane: 2; tint: root.dimText }
        BorderHandle { id: blendRightHandle; setting: "blend_width"; edge: zones.blendRight; rightSide: true; lane: 2; tint: root.dimText }

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
          text: "Scottland"
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
          text: "Restart Scottland to use: " + root.unsupported.map(n => root.settingNames[n] || root.gooControls.find(c => c.name === n)?.title || n).join(", ")
            + ". This session's plugin is older; your values are still saved."
        }

        TabBar {
          Layout.fillWidth: true
          currentIndex: root.gooTab ? 1 : 0
          onCurrentIndexChanged: {
            root.gooTab = currentIndex === 1
            Qt.callLater(() => {
              gooScroll.contentItem.contentY = 0
              if (root.gooTab) gooSettings.forceActiveFocus()
              else zoneSettings.forceActiveFocus()
            })
          }
          TabButton { text: "Layout" }
          TabButton { text: "Goo" }
        }

        ScrollView {
          id: gooScroll
          function revealRow(stack, index) {
            const y = stack.y + index * (stack.rowHeight + 1)
            const bottom = y + stack.rowHeight
            if (y < contentItem.contentY) contentItem.contentY = y
            else if (bottom > contentItem.contentY + availableHeight)
              contentItem.contentY = bottom - availableHeight
          }
          Layout.fillWidth: true
          Layout.preferredHeight: Math.min(500, (Quickshell.screens[0]?.height || 800) * 0.6)
          ScrollBar.vertical.policy: ScrollBar.AlwaysOn
          contentWidth: availableWidth
          clip: true
          ColumnLayout {
            width: parent.width
            spacing: 14

        // The zone settings: one stack of rows, each row a slider (ParameterStack.qml).
        ParameterStack {
          id: zoneSettings
          visible: !root.gooTab
          readonly property real screenWidth: Quickshell.screens.length > 0 ? Quickshell.screens[0].width : 0
          Layout.fillWidth: true
          foreground: root.textColor
          accent: root.accent
          focus: true
          rows: [
            { id: "blend_width", label: "Center edge softness", min: 0, max: 300, step: 1, largeStep: 10,
              hint: "Where shrinking eases in outside the center. Higher makes a wider, gentler band; zero makes a sharp edge.",
              display: v => Math.round(v) + " pt" },
            { id: "center_width", label: "Center zone width", min: 10, max: 90, step: 0.5, largeStep: 5,
              hint: "Space where windows stay full size. Higher widens the center; lower gives more space to the sides.",
              display: v => v.toFixed(1) + "%" },
            { id: "rail_width", label: "Widget rail width", min: 0.5, max: 10, step: 0.1, largeStep: 1,
              hint: "Edge strips where windows become widgets. Higher makes wider rails; lower leaves more room for windows.",
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

        Switch {
          visible: root.gooTab
          text: "Use goo"
          contentItem: Text { text: parent.text; color: root.textColor; leftPadding: 56; verticalAlignment: Text.AlignVCenter }
          checked: root.gooValues.goo
          onToggled: root.setGoo("goo", checked)
        }
        ParameterStack {
          id: gooSettings
          visible: root.gooTab
          Layout.fillWidth: true
          foreground: root.textColor
          accent: root.accent
          rows: root.gooControls.map(c => ({ id: c.name, label: c.title, min: c.low, max: c.high,
            hint: c.hint, step: c.step, largeStep: c.step * 10, decimals: c.step < 0.01 ? 3 : c.step < 1 ? 2 : 0 }))
          values: root.gooValues
          opening: root.original && root.original.goo ? root.original.goo : ({})
          onChanged: (id, value) => root.setGoo(id, value)
          onSelectedChanged: gooScroll.revealRow(gooSettings, selected)
        }

        // The same curve editor edits scale or goo falloff.
        ColumnLayout {
          Layout.fillWidth: true
          spacing: 4

          RowLayout {
            Layout.fillWidth: true
            Text { text: root.gooTab ? "Density falloff (distance / four reaches)" : "Scale across the side zones"; color: root.textColor; font.pixelSize: 14; Layout.fillWidth: true }
            Text {
              color: root.accent; font.pixelSize: 14
              text: editor.hovered >= 0
                ? Math.round(root.editorPoints[editor.hovered].y * 100) + "% at " + Math.round(root.editorPoints[editor.hovered].x * 100) + "% across"
                : Math.round(root.editorPoints[0].y * 100) + "% → " + Math.round(root.editorPoints[root.editorPoints.length - 1].y * 100) + "%"
            }
          }

          Item {
            id: editor
            Layout.fillWidth: true
            Layout.preferredHeight: 190
            property int dragging: -1
            property int hovered: -1
            readonly property real minimum: root.gooTab ? 0 : 0.05
            readonly property real plotLeft: 40
            readonly property real plotTop: 8
            readonly property real plotWidth: width - plotLeft - 10
            readonly property real plotHeight: height - plotTop - 24

            function toX(t) { return plotLeft + t * plotWidth }
            function toY(s) { return plotTop + (1 - (s - minimum) / (1 - minimum)) * plotHeight }
            function fromX(px) { return Math.min(1, Math.max(0, (px - plotLeft) / plotWidth)) }
            function fromY(py) { return Math.min(1, Math.max(minimum, minimum + (1 - (py - plotTop) / plotHeight) * (1 - minimum))) }

            function pointAt(px, py) {
              const points = root.editorPoints
              for (let i = 0; i < points.length; i++)
                if (Math.hypot(toX(points[i].x) - px, toY(points[i].y) - py) <= 10) return i
              return -1
            }

            function move(index, px, py) {
              const points = root.editorPoints.map(p => ({ x: p.x, y: p.y }))
              const last = points.length - 1
              points[index].y = fromY(py)
              if (root.gooTab) points[index].y = Math.min(index > 0 ? points[index - 1].y : 1,
                Math.max(index < last ? points[index + 1].y : 0, points[index].y))
              if (index > 0 && index < last)  // endpoints stay at the zone edges
                points[index].x = Math.min(points[index + 1].x - 0.02, Math.max(points[index - 1].x + 0.02, fromX(px)))
              root.setEditorPoints(points)
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
                ctx.fillText(root.gooTab ? "0%" : "5%", 4, editor.toY(editor.minimum) + 4)
                ctx.strokeRect(editor.plotLeft, editor.plotTop, editor.plotWidth, editor.plotHeight)
                ctx.fillText(root.gooTab ? "window edge" : "center edge", editor.plotLeft, editor.height - 6)
                ctx.fillText(root.gooTab ? "4× reach" : "rail", editor.plotLeft + editor.plotWidth - 20, editor.height - 6)
                // The curve.
                ctx.strokeStyle = root.accent; ctx.lineWidth = 2
                ctx.beginPath()
                for (let i = 0; i <= 120; i++) {
                  const t = i / 120, x = editor.toX(t), y = editor.toY(root.curveAt(root.editorPoints, t, editor.minimum))
                  if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
                }
                ctx.stroke()
                // Points: endpoints square, interior round.
                const points = root.editorPoints
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
                function onEditorPointsChanged() { canvas.requestPaint() }
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
                  if (index > 0 && index < root.editorPoints.length - 1)
                    root.setEditorPoints(root.editorPoints.filter((_, i) => i !== index))
                  return
                }
                if (index < 0) {
                  // Add a point where clicked, between the endpoints.
                  const t = editor.fromX(mouse.x)
                  if (t <= 0.02 || t >= 0.98) return
                  const points = root.editorPoints.map(p => ({ x: p.x, y: p.y }))
                  if (root.gooTab) {
                    const near = points.findIndex(p => Math.abs(p.x - t) < 0.02)
                    if (near >= 0) {
                      editor.dragging = near
                      editor.move(near, mouse.x, mouse.y)
                      return
                    }
                  }
                  let y = editor.fromY(mouse.y)
                  if (root.gooTab) {
                    const right = points.findIndex(p => p.x > t)
                    y = Math.min(points[right - 1].y, Math.max(points[right].y, y))
                  }
                  points.push({ x: t, y: y })
                  points.sort((a, b) => a.x - b.x)
                  root.setEditorPoints(points)
                  index = points.findIndex(p => p.x === t)
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
                if (index > 0 && index < root.editorPoints.length - 1)
                  root.setEditorPoints(root.editorPoints.filter((_, i) => i !== index))
              }
            }
          }

          Text {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            color: root.dimText
            font.pixelSize: 12
            text: root.gooTab ? "Drag points to shape how density falls away from a window. The right edge is four reaches away. Click to add a point; double-click or right-click to remove it."
              : "Drag the square ends for the largest and smallest scale. Click to add a point, drag to shape the curve, double-click or right-click a point to remove it."
          }
        }

          }
        }

        RowLayout {
          Layout.fillWidth: true
          Layout.topMargin: 4
          spacing: 10

          Button {
            text: "Defaults"
            onClicked: {
              if (root.gooTab) {
                root.gooValues = root.gooDefaults()
                root.gooPoints = root.exponentialPoints
                return
              }
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
