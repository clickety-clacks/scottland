import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import Quickshell.Wayland

// Scottland Settings: layout, goo and Window mode. Changes preview live while
// an overlay with input only on border handles shows the five zones on every screen. Save writes
// ~/.config/scottland/layout.ini; Cancel or Escape restores the values from when it opened.
//
// The scale curve sets how windows shrink across the side zones: t = 0 is the center zone's
// edge, t = 1 the widget rail. The endpoints are the largest and smallest scale; points in
// between shape it, joined by the same monotone cubic spline the plugin uses.
ShellRoot {
  id: root
  Component.onCompleted: Qt.application.name = "Scottland Settings"

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
    { name: "goo_relief", hint: "Apparent depth and background bending. Higher looks more rounded; lower looks flatter.", title: "Relief", initial: 5, low: 0.5, high: 12, step: 0.1 },
    { name: "goo_depth", hint: "Height of the rounded liquid above the screen, in logical pixels. Higher makes a deeper lens; zero flattens it.", title: "Liquid depth", initial: 6, low: 0, high: 20, step: 0.1 },
    { name: "goo_profile", hint: "How strongly the rounded bead climbs the window wall. Higher raises the inner meniscus; zero leaves a free rounded bead.", title: "Wall wetting", initial: 0.65, low: 0, high: 1, step: 0.01 },
    { name: "goo_soak", hint: "How strongly the goo picks up the colors beneath it (wallpaper, or windows under the film) and mixes them into its dye. Zero turns pickup off.", title: "Wallpaper pickup", initial: 0.12, low: 0, high: 1, step: 0.01 },
    { name: "goo_pickup_balance", hint: "Share of picked-up color in the goo at full Wallpaper pickup. Zero keeps a window's own color (focus, attention); one shows only what is picked up from beneath.", title: "Pickup balance", initial: 0.45, low: 0, high: 1, step: 0.01 },
    { name: "goo_overlap_film", hint: "Width of goo over windows behind. Higher covers a wider strip; zero hides the film.", title: "Overlap film", initial: 4, low: 0, high: 20, step: 0.5 },
    { name: "goo_hover_cloudiness", hint: "Milkiness of a nearby corner or side. Higher makes the whole control denser; zero keeps it clear.", title: "Control cloudiness", initial: 0.65, low: 0, high: 1, step: 0.01 },
    { name: "goo_hover_emissivity", hint: "Light from inside a nearby corner or side. Higher glows brighter; zero turns the glow off.", title: "Control glow", initial: 0.35, low: 0, high: 1.5, step: 0.01 },
    { name: "goo_hover_distance", hint: "How far away a control starts highlighting. Higher responds sooner; zero responds only over it.", title: "Control proximity", initial: 48, low: 0, high: 150, step: 1 },
    { name: "goo_dye_density", hint: "How much dye the goo holds: its window colors and the colors it picks up alike (focus, attention and hint colors in fallback halos). Higher is denser, capped at full opacity; zero leaves clear liquid.", title: "Dye density", initial: 1, low: 0, high: 1.5, step: 0.01 }]
  readonly property var edgeControls: [
    { name: root.lightScheme ? "unfocused_edge_tone_light" : "unfocused_edge_tone_dark",
      hint: "Gray of an unfocused edge in the active color scheme. Lower is black; higher is white.",
      title: "Unfocused edge tone", initial: root.lightScheme ? 0.08 : 0.92, low: 0, high: 1, step: 0.01 },
    { name: "unfocused_edge_strength",
      hint: "How strongly an unfocused gray tint shows on the edge. Zero leaves clear glass; one uses the full tint.",
      title: "Unfocused edge strength", initial: 1, low: 0, high: 1, step: 0.01 }]
  function gooDefaults() {
    const values = { goo: true, goo_falloff: "", attention_color_family: "theme", unfocused_edge_tone_light: 0.08,
      unfocused_edge_tone_dark: 0.92, unfocused_edge_strength: 1 }
    for (const c of gooControls) values[c.name] = c.initial
    return values
  }
  property var gooValues: gooDefaults()
  property int tab: 0
  readonly property bool gooTab: tab === 1
  readonly property var motionDefaults: ({key_impulse:335, key_friction:608,
    resize_impulse:335, resize_friction:608, key_max_velocity:6000,
    cycle_overshoot:3, alt_hold_delay:300, window_double_tap_delay:300, window_hold_delay:500,
    window_avoidance_always:false, window_mode_tint:7, hint_background_opacity:100, solo_audition_hotspot:50})
  property var motionValues: Object.assign({}, motionDefaults)
  readonly property var opacityDefaults: ({center_opacity_focused:1,center_opacity_unfocused:1,
    side_opacity_focused:1,side_opacity_unfocused:1,widget_opacity_focused:1,widget_opacity_unfocused:1,
    window_mode_opacity_focused:1,window_mode_opacity_unfocused:1})
  property var opacityValues: Object.assign({},opacityDefaults)
  readonly property var widgetDefaults: ({widget_bounce:0.04,widget_peek_enter_delay:150,
    widget_peek_leave_delay:100,widget_attention_peek_duration:5000,widget_make_room_dwell:350,minimize_hold_delay:300})
  property var widgetValues: Object.assign({},widgetDefaults)
  readonly property var solarDefaults: ({enabled:true,allow_ip:true,location_set:false,latitude:0,longitude:0})
  property var solarValues: Object.assign({},solarDefaults)
  property var originalSolar: Object.assign({},solarDefaults)
  property bool solarLatitudeEdited:false
  property bool solarLongitudeEdited:false
  readonly property string solarPath: Quickshell.env("SCOTTLAND_SOLAR_FILE") ||
    (Quickshell.env("HOME") + "/.config/scottland/solar.ini")
  function setOpacity(name,value) { opacityValues=Object.assign({},opacityValues,{[name]:value}) }
  function setWidget(name,value) { widgetValues=Object.assign({},widgetValues,{[name]:value}) }
  function setSolar(name,value) { solarValues=Object.assign({},solarValues,{[name]:value}) }
  onOpacityValuesChanged: if (loaded && !push.running) push.start()
  onWidgetValuesChanged: if (loaded && !push.running) push.start()
  function setMotion(name,value) { motionValues=Object.assign({},motionValues,{[name]:value}) }
  function setMotionPair(impulseName,frictionName,impulse,friction) {
    motionValues=Object.assign({},motionValues,{[impulseName]:impulse,[frictionName]:friction})
  }
  onMotionValuesChanged: if (loaded && !push.running) push.start()
  Design { id: theme; palette: root.palette }
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
    blend_width: "Center edge softness", key_impulse:"Push strength", key_friction:"Movement deceleration",
    resize_impulse:"Resize strength",resize_friction:"Resize deceleration",
    key_max_velocity:"Speed limit", cycle_overshoot:"Hint cycle overshoot", window_mode_tint:"Hint color overlay", hint_background_opacity:"Hint background opacity", alt_hold_delay:"Alt hold timing",
    window_double_tap_delay:"Double-tap timing", window_hold_delay:"Hint hold timing",
    solo_audition_hotspot:"Hold hotspot", unfocused_edge_tone_light:"Unfocused edge tone (light)",
    unfocused_edge_tone_dark:"Unfocused edge tone (dark)",unfocused_edge_strength:"Unfocused edge strength",
    attention_color_family:"Attention color family", goo_dye_density:"Dye density" })

  // The session palette carries theme colors and the desktop's interface font/text scale.
  property var palette: ({})
  readonly property bool lightScheme: palette.scheme === "light"
  FileView {
    path: Quickshell.env("SCOTTLAND_PALETTE") ||
      (Quickshell.env("XDG_RUNTIME_DIR") + "/scottland/" + Quickshell.env("WAYLAND_DISPLAY") + ".palette.json")
    printErrors: false
    watchChanges: true
    onFileChanged: reload()
    onLoaded: {
      try { root.palette = JSON.parse(text()) } catch (error) { root.palette = ({}) }
    }
  }
  readonly property color hintBackground: palette.background || "#1c1d22"
  readonly property color hintForeground: palette.foreground || textColor
  readonly property color hintAccent: palette.accent || accent
  readonly property string hintFontFamily: palette.font_family || Qt.application.font.family
  readonly property real textScale: Math.max(0.5, Math.min(3, Number(palette.text_scale) || 1))
  readonly property bool tabsWrapped: textScale > 1.35 || (settingsWindow.screen?.width || 1280) < 1100
  readonly property color panelColor: theme.background
  readonly property color textColor: theme.foreground
  readonly property color dimText: theme.muted
  readonly property color accent: theme.accent
  readonly property color railColor: "#e0af68"

  // --- The curve (mirrors scale_curve_t in the plugin) --------------------------------------

  function curveText(points) {
    return points.map(p => p.x.toFixed(3) + ":" + p.y.toFixed(3)).join(" ")
  }

  function parseCurve(text, minimum = 0.05, maximum = 1) {
    const points = String(text || "").trim().split(/\s+/).filter(w => w.includes(":")).map(w => {
      const [x, y] = w.split(":").map(parseFloat)
      return { x: Math.min(1, Math.max(0, x)), y: Math.min(maximum, Math.max(minimum, y)) }
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

  function curveAt(points, t, minimum = 0.05, maximum = 1) {
    t = Math.min(1, Math.max(0, t))
    const m = slopes(points)
    let i = 0
    while (i + 2 < points.length && t > points[i + 1].x) i++
    const a = points[i], b = points[i + 1], h = b.x - a.x
    const u = (t - a.x) / h, u2 = u * u, u3 = u2 * u
    const y = (2 * u3 - 3 * u2 + 1) * a.y + (u3 - 2 * u2 + u) * h * m[i]
      + (-2 * u3 + 3 * u2) * b.y + (u3 - u2) * h * m[i + 1]
    return Math.min(maximum, Math.max(minimum, y))
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
    onTriggered: { root.send(root.centerWidth, root.railWidth, root.curvePoints, root.blendWidth); root.sendGoo(root.gooValues); root.sendBatch(root.motionValues); root.sendBatch(root.opacityValues); root.sendBatch(root.widgetValues) }
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
          else if (typeof goo[key] === "string") goo[key] = root.savedText(key) || goo[key]
          else goo[key] = root.savedValue(key, goo[key])
        }
        root.original = Object.assign({}, root.original, { goo: goo })
        root.gooValues = Object.assign({}, goo)
        root.gooPoints = root.parseCurve(goo.goo_falloff, 0) || root.exponentialPoints
        root.centerWidth = root.original.center_width
        root.railWidth = root.original.rail_width
        root.blendWidth = root.original.blend_width
        root.curvePoints = root.original.curve
        const motion = Object.assign({},root.motionDefaults)
        for (const k of Object.keys(motion))
          motion[k] = values[k] !== undefined ? values[k] : typeof motion[k] === "boolean" ? root.savedText(k) === "true" : typeof motion[k] === "string" ? root.savedText(k) : root.savedValue(k,motion[k])
        root.motionValues = motion
        const opacity=Object.assign({},root.opacityDefaults),widgets=Object.assign({},root.widgetDefaults)
        for (const group of [opacity,widgets]) for (const k of Object.keys(group))
          group[k] = values[k] !== undefined ? values[k] : root.savedValue(k,group[k])
        root.opacityValues=opacity;root.widgetValues=widgets
        root.original = Object.assign({},root.original,{motion:Object.assign({},motion),
          opacity:Object.assign({},opacity),widgets:Object.assign({},widgets)})
        const solar=Object.assign({},root.solarDefaults)
        for(const k of ["enabled","allow_ip","location_set"])
          solar[k]=root.solarText(k)==="true"
        for(const k of ["latitude","longitude"]){const n=parseFloat(root.solarText(k));if(!isNaN(n))solar[k]=n}
        root.solarValues=solar;root.originalSolar=Object.assign({},solar)
        root.solarLatitudeEdited=solar.location_set;root.solarLongitudeEdited=solar.location_set
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

  FileView {
    id: solarFile
    path: root.solarPath
    printErrors: false
    blockLoading: true
    blockWrites: true
    atomicWrites: true
  }
  function solarText(name) {
    const match=solarFile.text().match(new RegExp("^"+name+"\\s*=\\s*(.*)$","m"))
    return match?match[1].trim():""
  }

  // Values last saved by this app; used for settings the running session can't report.
  function savedText(name) {
    const names = name === "goo_dye_density" ? [name, "goo_dye_strength"] : [name]
    for (const key of names) {
      const match = saved.text().match(new RegExp("^\\s*" + key + "\\s*=\\s*(.*)$", "m"))
      if (match) return match[1].trim()
    }
    return ""
  }

  function savedValue(name, fallback) {
    const value = parseFloat(savedText(name))
    return isNaN(value) ? fallback : value
  }

  function save() {
    push.stop()
    send(centerWidth, railWidth, curvePoints, blendWidth)
    sendGoo(gooValues)
    sendBatch(motionValues)
    sendBatch(opacityValues)
    sendBatch(widgetValues)
    solarFile.setText("# Written by Scottland Settings.\n[solar]\n"+Object.keys(solarValues).map(k=>k+" = "+solarValues[k]+"\n").join(""))
    saved.setText("# Written by Scottland settings.\n[scottland]\n"
      + "center_width = " + centerWidth.toFixed(3) + "\n"
      + "rail_width = " + railWidth.toFixed(3) + "\n"
      + "blend_width = " + blendWidth.toFixed(1) + "\n"
      + "scale_curve = " + curveText(curvePoints) + "\n"
      + "min_scale = " + minScale.toFixed(3) + "\n"
      + "max_scale = " + maxScale.toFixed(3) + "\n"
      + Object.keys(gooValues).map(k => k + " = " + gooValues[k] + "\n").join("")
      + Object.keys(motionValues).map(k => k + " = " + motionValues[k] + "\n").join("")
      + Object.keys(opacityValues).map(k => k + " = " + opacityValues[k] + "\n").join("")
      + Object.keys(widgetValues).map(k => k + " = " + widgetValues[k] + "\n").join(""))
    live.write("flush\n")
  }

  function cancel() {
    push.stop()
    if (original) { send(original.center_width, original.rail_width, original.curve, original.blend_width); sendGoo(original.goo); sendBatch(original.motion); sendBatch(original.opacity); sendBatch(original.widgets) }
    solarValues=Object.assign({},originalSolar)
    solarLatitudeEdited=originalSolar.location_set;solarLongitudeEdited=originalSolar.location_set
    live.write("flush\n")
  }

  function testRect(item) {
    const p=item.mapToGlobal(0,0)
    return {x:p.x,y:p.y,width:item.width,height:item.height}
  }
  function curveProbe(item) {
    const r=testRect(item)
    return Object.assign(r,{selected:item.selected,hovered:item.hovered,
      knots:item.points.map(p=>({x:r.x+item.toX(p.x),y:r.y+item.toY(p.y)})),
      plot:{x:r.x+item.plotLeft,y:r.y+item.plotTop,width:item.plotWidth,height:item.plotHeight}})
  }
  function coastProbe(item) {
    const r=testRect(item)
    return Object.assign(r,{endpoint:{x:r.x+item.endpointX,y:r.y+item.endpointY},
      duration:item.duration,distance:item.distance,
      plot:{x:r.x+item.plotLeft,y:r.y+item.plotTop,width:item.plotWidth,height:item.plotHeight}})
  }
  IpcHandler {
    target: "settings-test"
    // Observations only, and only when explicitly enabled by an isolated test.
    function snapshot(): string {
      if(Quickshell.env("SCOTTLAND_SETTINGS_TEST") !== "1" || !root.loaded)return "{}"
      return JSON.stringify({title:Qt.application.name,heading:heading.text,screen:settingsWindow.screen.name,tab:root.tab,
      panel:{x:settingsWindow.x,y:settingsWindow.y,width:settingsWindow.width,height:settingsWindow.height},
      viewport:root.testRect(gooScroll), scroll:gooScroll.contentY,wheelVelocity:gooScroll.wheelVelocity,flicking:gooScroll.flicking,touchVelocity:gooScroll.verticalVelocity, contentHeight:gooScroll.contentHeight,
      zones:Object.assign(root.testRect(zoneSettings),{hinted:zoneSettings.hinted,hint:zoneSettings.visibleHint}),
      goo:Object.assign(root.testRect(gooSettings),{hinted:gooSettings.hinted,hint:gooSettings.visibleHint,
        edgeControls:root.edgeControls,rowHeight:gooSettings.rowHeight,rows:gooSettings.rows.map(row=>row.id)}),
      attentionColor:{theme:root.testRect(attentionTheme),warm:root.testRect(attentionWarm),cool:root.testRect(attentionCool)},
      editor:root.curveProbe(editor), movement:root.coastProbe(movementEditor),resize:root.coastProbe(resizeEditor),
      playground:Object.assign(root.testRect(playground),{distance:playground.distance,velocity:playground.vx,
        widgetized:playground.widgetized,widgetSide:playground.widgetSide,edgeStops:playground.edgeStops.length}),
      motionSettings:root.testRect(motionSettings),holdTiming:root.testRect(holdTiming),doubleTiming:root.testRect(doubleTiming),hintHoldTiming:root.testRect(hintHoldTiming),soloHotspot:root.testRect(soloHotspot),
      alwaysAvoidance:root.testRect(alwaysAvoidance),
      opacitySettings:root.testRect(opacitySettings),windowOpacitySettings:root.testRect(windowOpacitySettings),
      windowTintSettings:Object.assign(root.testRect(windowTintSettings),{rowHeight:windowTintSettings.rowHeight}),
      widgetSettings:root.testRect(widgetSettings),solarSettings:root.testRect(solarSettings),
      solarEnable:root.testRect(solarEnable),solarNetwork:root.testRect(solarNetwork),
      motion:root.motionValues,opacity:root.opacityValues,widgets:root.widgetValues,solar:root.solarValues,values:root.gooValues,palette:root.palette})
    }
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
        // While a border is held the whole overlay takes input, so a pointer that outruns the
        // 12 px handle stays with the drag instead of falling through to whatever is below.
        Region { item: dragCapture }
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

        property bool borderHeld: false
        Item { id: dragCapture; width: zones.borderHeld ? zones.width : 0; height: zones.borderHeld ? zones.height : 0 }

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
            onReleased: zones.borderHeld = false
            onCanceled: zones.borderHeld = false
            onPressed: mouse => {
              zones.borderHeld = true
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
          radius: theme.radius; color: root.panelColor
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
    id: settingsWindow
    // Layer-shell surfaces have no window title; the application name and heading identify it.
    anchors.bottom: true
    margins.bottom: Math.max(24, Math.round((screen?.height || 800)*0.04))
    implicitWidth: Math.min(Math.max(320,(screen?.width || 1280)-24),1040,
      Math.max(780,Math.round((screen?.width || 1280)*0.63)))
    // About half the screen's height (Mike, 2026-10-02: the near-full-height panel was too large),
    // never so short that a tab's rows get cramped on small screens.
    implicitHeight: Math.min((screen?.height || 800)-24,
      Math.max(520, Math.round((screen?.height || 800)*0.5)))
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.OnDemand
    WlrLayershell.namespace: "scottland-settings"

    Rectangle {
      anchors.fill: parent
      radius: theme.radius
      color: root.panelColor
      border.color: theme.separator

      focus: true
      Keys.onEscapePressed: root.cancel()
      Keys.onReturnPressed: root.save()

      ColumnLayout {
        id: panel
        anchors.fill: parent
        anchors.margins: 36
        spacing: 20

        Text {
          id:heading
          text: "Scottland Settings"
          color: root.textColor
          font.family: theme.family
          font.pixelSize: 18 * theme.textScale
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

        Rectangle {
          Layout.fillWidth: true
          Layout.preferredHeight: root.tabsWrapped ? 96 : 48
          radius: theme.radius
          color: theme.tint(0.07)
          clip: true
          Grid {
            anchors.fill: parent
            columns: root.tabsWrapped ? 3 : 6
            Repeater {
              model: ["Layout", "Goo", "Window mode", "Translucency", "Widgets", "Sunlight"]
              SettingAction {
                onAcceptRequested: root.save()
                required property int index
                required property string modelData
                design: theme
                width: parent.width/(root.tabsWrapped ? 3 : 6); height:48
                text: modelData; joined: true; joinPosition:index%(root.tabsWrapped ? 3 : 6); checked: root.tab === index
                onClicked: {
                  root.tab=index
                  gooScroll.halt(); gooScroll.contentY=0
                  Qt.callLater(() => {
                    if(root.tab===0)zoneSettings.forceActiveFocus()
                    else if(root.tab===1)gooSettings.forceActiveFocus()
                    else if(root.tab===2)playground.forceActiveFocus()
                    else if(root.tab===3)opacitySettings.forceActiveFocus()
                    else if(root.tab===4)widgetSettings.forceActiveFocus()
                    else solarSettings.forceActiveFocus()
                  })
                }
                Rectangle { visible: index%(root.tabsWrapped ? 3 : 6)>0; width:1;height:parent.height-20;y:10;color:theme.separator }
                Rectangle { visible: root.tabsWrapped && index>=3; width:parent.width-20;height:1;x:10;color:theme.separator }
              }
            }
          }
        }

        CoastView {
          id: gooScroll
          design:theme
          Layout.fillWidth: true
          Layout.fillHeight: true
          Layout.minimumHeight: 200
          contentHeight: contents.implicitHeight
          ColumnLayout {
            id: contents
            width: gooScroll.availableWidth
            spacing: 22

        // The zone settings: one stack of rows, each row a slider (ParameterStack.qml).
        ParameterStack {
          id: zoneSettings
          visible: root.tab === 0
          readonly property real screenWidth: Quickshell.screens.length > 0 ? Quickshell.screens[0].width : 0
          Layout.fillWidth: true
          foreground: root.textColor
          accent: root.accent
          hintBackground: root.hintBackground
          hintForeground: root.hintForeground
          hintAccent: root.hintAccent
          hintFontFamily: root.hintFontFamily
          textScale: root.textScale
          viewport: gooScroll
          scrollOffset: gooScroll.contentY
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

        SettingAction {
            onAcceptRequested: root.save()
          visible: root.gooTab
          Layout.fillWidth: true
          design: theme
          text: root.gooValues.goo ? "Goo is on" : "Goo is off"
          checked: root.gooValues.goo
          onClicked: root.setGoo("goo", !root.gooValues.goo)
        }
        ColumnLayout {
          id: attentionColorChoice
          visible: root.gooTab
          Layout.fillWidth: true
          spacing: 8
          RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Text {
              Layout.preferredWidth: 150
              text: "Attention color"
              color: root.textColor
              font.family: theme.family
              font.pixelSize: 14 * theme.textScale
              font.weight: Font.DemiBold
            }
            SettingAction {
              id: attentionTheme
              design: theme
              Layout.fillWidth: true
              text: "Theme"
              checked: root.gooValues.attention_color_family === "theme"
              onClicked: root.setGoo("attention_color_family", "theme")
              onAcceptRequested: root.setGoo("attention_color_family", "theme")
            }
            SettingAction {
              id: attentionWarm
              design: theme
              Layout.fillWidth: true
              text: "Warm"
              checked: root.gooValues.attention_color_family === "warm"
              onClicked: root.setGoo("attention_color_family", "warm")
              onAcceptRequested: root.setGoo("attention_color_family", "warm")
            }
            SettingAction {
              id: attentionCool
              design: theme
              Layout.fillWidth: true
              text: "Cool"
              checked: root.gooValues.attention_color_family === "cool"
              onClicked: root.setGoo("attention_color_family", "cool")
              onAcceptRequested: root.setGoo("attention_color_family", "cool")
            }
          }
          Text {
            Layout.fillWidth: true
            text: "Theme follows your palette. Warm shifts from red to amber; Cool shifts from olive to yellow-green."
            color: root.dimText
            wrapMode: Text.WordWrap
            font.family: theme.family
            font.pixelSize: 12 * theme.textScale
          }
        }
        ParameterStack {
          id: gooSettings
          visible: root.gooTab
          Layout.fillWidth: true
          foreground: root.textColor
          accent: root.accent
          hintBackground: root.hintBackground
          hintForeground: root.hintForeground
          hintAccent: root.hintAccent
          hintFontFamily: root.hintFontFamily
          textScale: root.textScale
          viewport: gooScroll
          scrollOffset: gooScroll.contentY
          rows: root.edgeControls.concat(root.gooControls).map(c => ({ id: c.name, label: c.title, min: c.low, max: c.high,
            hint: c.hint, step: c.step, largeStep: c.step * 10, decimals: c.step < 0.01 ? 3 : c.step < 1 ? 2 : 0 }))
          values: root.gooValues
          opening: root.original && root.original.goo ? root.original.goo : ({})
          onChanged: (id, value) => root.setGoo(id, value)
          onSelectedChanged: gooScroll.revealRow(gooSettings, selected)
        }

        CurveEditor {
          id: editor
          viewport:gooScroll; scrollOffset:gooScroll.contentY
          explanation:root.gooTab ? "How quickly density falls away from a window. Lower points thin the distant goo; higher points extend its reach." : "Window scale between the full-size center and widget rail. Higher points keep windows larger at that position."
          visible: root.tab === 0 || root.tab === 1
          Layout.fillWidth: true
          design: theme
          title: root.gooTab ? "Density falloff" : "Scale across the side zones"
          points: root.editorPoints
          opening: root.gooTab ? root.parseCurve(root.original?.goo?.goo_falloff,0) || root.exponentialPoints : root.original?.curve || root.defaults.curve
          minimum: root.gooTab ? 0 : 0.05
          descending: root.gooTab
          leftLabel: root.gooTab ? "window edge" : "center edge"
          rightLabel: root.gooTab ? "4× reach" : "rail"
          evaluate: root.curveAt
          onEdited: points => root.setEditorPoints(points)
        }
        ColumnLayout {
          visible: root.tab === 2
          Layout.fillWidth: true
          spacing: 22
          SettingAction {
            id:alwaysAvoidance
            onAcceptRequested: root.save()
            Layout.fillWidth:true
            design:theme
            text:root.motionValues.window_avoidance_always
              ? "Window avoidance · on"
              : "Window avoidance · off"
            checked:root.motionValues.window_avoidance_always
            onClicked:root.setMotion("window_avoidance_always",!root.motionValues.window_avoidance_always)
          }
          MotionPlayground {
            id: playground
            viewport:gooScroll; scrollOffset:gooScroll.contentY
            Layout.fillWidth: true
            design: theme
            values: root.motionValues
            onChanged: (name,value)=>root.setMotion(name,value)
          }
          CoastGraph {
            id: movementEditor
            Layout.fillWidth: true
            viewport:gooScroll; scrollOffset:gooScroll.contentY
            explanation:"Drag the endpoint. Right means a longer coast; up means farther travel. The graph shows window position after one arrow push; released drags use the same deceleration."
            design: theme; title: "Movement · position over time"
            impulse:root.motionValues.key_impulse;friction:root.motionValues.key_friction
            openingImpulse:root.original?.motion?.key_impulse ?? 335
            openingFriction:root.original?.motion?.key_friction ?? 608
            onEdited:(impulse,friction)=>root.setMotionPair("key_impulse","key_friction",impulse,friction)
          }
          CoastGraph {
            id: resizeEditor
            Layout.fillWidth: true
            viewport:gooScroll; scrollOffset:gooScroll.contentY
            explanation:"Drag the endpoint. Right means resizing continues longer; up means the window grows or shrinks farther after one Ctrl + arrow push."
            design: theme; title: "Resize · position over time"
            impulse:root.motionValues.resize_impulse;friction:root.motionValues.resize_friction
            openingImpulse:root.original?.motion?.resize_impulse ?? 335
            openingFriction:root.original?.motion?.resize_friction ?? 608
            onEdited:(impulse,friction)=>root.setMotionPair("resize_impulse","resize_friction",impulse,friction)
          }
          ParameterStack {
            id:motionSettings
            Layout.fillWidth:true
            foreground:root.textColor; accent:root.accent
            hintBackground:root.hintBackground; hintForeground:root.hintForeground; hintAccent:root.hintAccent
            hintFontFamily:root.hintFontFamily; textScale:root.textScale
            viewport:gooScroll; scrollOffset:gooScroll.contentY
            rows:[
              {id:"key_max_velocity",label:"Speed limit",min:1,max:20000,step:100,suffix:" pt/s",hint:"Caps each movement and resize axis, including a drag release. Higher permits faster motion when impulses build up."},
              {id:"cycle_overshoot",label:"Hint cycle overshoot",min:0,max:10,step:0.1,largeStep:1,decimals:1,suffix:"%",hint:"Elastic settlement after a Window mode hint moves a window. Higher passes the destination farther before resting; zero removes overshoot. Pushes and coasts are unaffected."}
            ]
            values:root.motionValues; opening:root.original?.motion || ({})
            onChanged:(name,value)=>root.setMotion(name,value)
            onSelectedChanged:gooScroll.revealRow(motionSettings,selected)
          }
          ParameterStack {
            id:windowOpacitySettings
            Layout.fillWidth:true
            foreground:root.textColor;accent:root.accent
            hintBackground:root.hintBackground;hintForeground:root.hintForeground;hintAccent:root.hintAccent
            hintFontFamily:root.hintFontFamily;textScale:root.textScale
            viewport:gooScroll;scrollOffset:gooScroll.contentY
            rows:[
              {id:"window_mode_opacity_focused",label:"Focused opacity",min:0,max:1,step:0.01,largeStep:0.1,decimals:2,hint:"Opacity of the selected window while Window mode is active. Higher is more solid; lower reveals what is behind it."},
              {id:"window_mode_opacity_unfocused",label:"Other windows' opacity",min:0,max:1,step:0.01,largeStep:0.1,decimals:2,hint:"Opacity of other windows while Window mode is active. Higher is more solid; lower reveals what is behind them."}
            ]
            values:root.opacityValues;opening:root.original?.opacity || ({})
            onChanged:(name,value)=>root.setOpacity(name,value)
          }
          ParameterStack {
            id:windowTintSettings
            Layout.fillWidth:true
            foreground:root.textColor;accent:root.accent
            hintBackground:root.hintBackground;hintForeground:root.hintForeground;hintAccent:root.hintAccent
            hintFontFamily:root.hintFontFamily;textScale:root.textScale
            viewport:gooScroll;scrollOffset:gooScroll.contentY
            rows:[
              {id:"window_mode_tint",label:"Hint color overlay",min:0,max:30,step:0.5,largeStep:1,decimals:1,suffix:"%",hint:"How strongly Window mode tints each window and widget card with its hint color. Higher is a stronger wash; zero turns the overlay off. Hint circles and outlines are unaffected."},
              {id:"hint_background_opacity",label:"Hint background opacity",min:0,max:100,step:1,largeStep:10,decimals:0,suffix:"%",hint:"Opacity of the backgrounds behind hint letters. Higher makes the backing more visible; 100% keeps the usual window and widget hint backgrounds, and zero removes their fill. Letters, hint rims and window opacity are unchanged."}
            ]
            values:root.motionValues;opening:root.original?.motion || ({})
            onChanged:(name,value)=>root.setMotion(name,value)
          }
          TimingRow {
            id:holdTiming
            Layout.fillWidth:true;design:theme;viewport:gooScroll;scrollOffset:gooScroll.contentY;title:"Hold Alt for Window mode"
            value:root.motionValues.alt_hold_delay;opening:root.original?.motion?.alt_hold_delay || 300
            onEdited:value=>root.setMotion("alt_hold_delay",value)
          }
          TimingRow {
            id:doubleTiming
            Layout.fillWidth:true;design:theme;viewport:gooScroll;scrollOffset:gooScroll.contentY;title:"Double-tap a hint";doubleTap:true
            value:root.motionValues.window_double_tap_delay;opening:root.original?.motion?.window_double_tap_delay || 300
            onEdited:value=>root.setMotion("window_double_tap_delay",value)
          }
          TimingRow {
            id:hintHoldTiming
            Layout.fillWidth:true;design:theme;viewport:gooScroll;scrollOffset:gooScroll.contentY;title:"Hold a hint";hintHold:true
            value:root.motionValues.window_hold_delay;opening:root.original?.motion?.window_hold_delay || 500
            onEdited:value=>root.setMotion("window_hold_delay",value)
          }
          // How far a pointer hold's audition lets the pointer move before that counts as starting
          // to drag and cancels it (ruling 10-05; WK39). Kept by that ruling; nothing reads it
          // until pointer-hold auditions land.
          TimingRow {
            id:soloHotspot
            Layout.fillWidth:true;design:theme;viewport:gooScroll;scrollOffset:gooScroll.contentY;title:"Hold hotspot"
            minimum:8;maximum:400;step:1;bigStep:10;unit:"pt";endLabel:"400 pt"
            explanation:"Not used yet: pointer-hold auditions are not built. Once they are, moving the pointer this far from where a hold fired, while it shows its solo or pair, will count as starting to drag and cancel the audition. Smaller movements will keep it."
            footer:"no effect until pointer-hold auditions are built"
            value:root.motionValues.solo_audition_hotspot;opening:root.original?.motion?.solo_audition_hotspot ?? 50
            onEdited:value=>root.setMotion("solo_audition_hotspot",value)
          }
        }
        ParameterStack {
          id:opacitySettings
          visible:root.tab===3
          Layout.fillWidth:true
          foreground:root.textColor;accent:root.accent
          hintBackground:root.hintBackground;hintForeground:root.hintForeground;hintAccent:root.hintAccent
          hintFontFamily:root.hintFontFamily;textScale:root.textScale
          viewport:gooScroll;scrollOffset:gooScroll.contentY
          rows:[
            {id:"center_opacity_focused",label:"Center · focused",min:0,max:1,step:0.01,largeStep:0.1,decimals:2,hint:"Opacity of the focused center window. Higher is more solid; lower reveals what is behind it."},
            {id:"center_opacity_unfocused",label:"Center · unfocused",min:0,max:1,step:0.01,largeStep:0.1,decimals:2,hint:"Opacity of other center windows. Higher is more solid; lower reveals what is behind them."},
            {id:"side_opacity_focused",label:"Side zones · focused",min:0,max:1,step:0.01,largeStep:0.1,decimals:2,hint:"Opacity of the focused window in a side zone. Higher is more solid; lower reveals what is behind it."},
            {id:"side_opacity_unfocused",label:"Side zones · unfocused",min:0,max:1,step:0.01,largeStep:0.1,decimals:2,hint:"Opacity of other windows in side zones. Higher is more solid; lower reveals what is behind them."},
            {id:"widget_opacity_focused",label:"Widgets · focused",min:0,max:1,step:0.01,largeStep:0.1,decimals:2,hint:"Opacity of a focused widget. Higher is more solid; lower reveals what is behind it."},
            {id:"widget_opacity_unfocused",label:"Widgets · unfocused",min:0,max:1,step:0.01,largeStep:0.1,decimals:2,hint:"Opacity of other widgets. Higher is more solid; lower reveals what is behind them."}
          ]
          values:root.opacityValues;opening:root.original?.opacity || ({})
          onChanged:(name,value)=>root.setOpacity(name,value)
          onSelectedChanged:gooScroll.revealRow(opacitySettings,selected)
        }
        ParameterStack {
          id:widgetSettings
          visible:root.tab===4
          Layout.fillWidth:true
          foreground:root.textColor;accent:root.accent
          hintBackground:root.hintBackground;hintForeground:root.hintForeground;hintAccent:root.hintAccent
          hintFontFamily:root.hintFontFamily;textScale:root.textScale
          viewport:gooScroll;scrollOffset:gooScroll.contentY
          rows:[
            {id:"widget_bounce",label:"Expand / contract bounce",min:0,max:0.1,step:0.005,largeStep:0.02,decimals:3,hint:"Elastic size overshoot when a widget expands or contracts. Higher adds a larger pop; zero removes it."},
            {id:"widget_peek_enter_delay",label:"Hover intent",min:0,max:3000,step:10,largeStep:100,suffix:" ms",hint:"How long the pointer rests on a collapsed widget before it peeks. Higher asks for more intent; lower peeks sooner."},
            {id:"widget_peek_leave_delay",label:"Hover leave",min:0,max:3000,step:10,largeStep:100,suffix:" ms",hint:"How long an expanded hover peek waits before closing after the pointer leaves. Higher gives more time to return."},
            {id:"widget_attention_peek_duration",label:"Attention peek",min:100,max:30000,step:100,largeStep:1000,suffix:" ms",hint:"How long a collapsed widget stays expanded, or a hidden one comes in, when it asks for attention. Higher keeps it open longer."},
            {id:"widget_make_room_dwell",label:"Rail make-room pause",min:100,max:1500,step:10,largeStep:100,suffix:" ms",hint:"How long a rail drag must pause within a few pixels before neighboring widgets move. Higher waits for a clearer pause; lower rearranges sooner."},
            {id:"minimize_hold_delay",label:"Super+M hold",min:100,max:2000,step:10,largeStep:100,suffix:" ms",hint:"How long Super+M must be held to show the other mode only until it's released, instead of a tap that changes the mode. Higher leaves more time for a tap."}
          ]
          values:root.widgetValues;opening:root.original?.widgets || ({})
          onChanged:(name,value)=>root.setWidget(name,value)
          onSelectedChanged:gooScroll.revealRow(widgetSettings,selected)
        }
        ColumnLayout {
          visible:root.tab===5
          Layout.fillWidth:true
          spacing:22
          SettingAction { id:solarEnable;design:theme;text:root.solarValues.enabled?"Follow sunrise and sunset":"Sun following is off";checked:root.solarValues.enabled
            onClicked:root.setSolar("enabled",!root.solarValues.enabled);onAcceptRequested:root.save() }
          ParameterStack {
            id:solarSettings
            Layout.fillWidth:true
            foreground:root.textColor;accent:root.accent
            hintBackground:root.hintBackground;hintForeground:root.hintForeground;hintAccent:root.hintAccent
            hintFontFamily:root.hintFontFamily;textScale:root.textScale
            viewport:gooScroll;scrollOffset:gooScroll.contentY
            rows:[
              {id:"latitude",label:"Fallback latitude",min:-90,max:90,step:0.1,largeStep:1,decimals:1,hint:"Latitude used if the system location service is unavailable. Set this once for your location; north is positive."},
              {id:"longitude",label:"Fallback longitude",min:-180,max:180,step:0.1,largeStep:1,decimals:1,hint:"Longitude used if the system location service is unavailable. Set this once for your location; east is positive."}
            ]
            values:root.solarValues;opening:root.originalSolar
            onChanged:(name,value)=>{
              root.setSolar(name,value)
              if(name==="latitude")root.solarLatitudeEdited=true
              if(name==="longitude")root.solarLongitudeEdited=true
              root.setSolar("location_set",root.solarLatitudeEdited && root.solarLongitudeEdited)
            }
          }
          SettingAction { id:solarNetwork;design:theme;text:root.solarValues.allow_ip?"Network location allowed":"Network location off";checked:root.solarValues.allow_ip
            onClicked:root.setSolar("allow_ip",!root.solarValues.allow_ip);onAcceptRequested:root.save() }
        }

          }
        }

        RowLayout {
          Layout.fillWidth: true
          Layout.topMargin: 4
          spacing: 10

          SettingAction {
            onAcceptRequested: root.save()
            design: theme
            text: "Defaults"
            onClicked: {
              if (root.tab === 2) { root.motionValues=Object.assign({},root.motionDefaults);
                root.opacityValues=Object.assign({},root.opacityValues,{window_mode_opacity_focused:1,window_mode_opacity_unfocused:1});return }
              if (root.tab === 3) {root.opacityValues=Object.assign({},root.opacityDefaults);return}
              if (root.tab === 4) {root.widgetValues=Object.assign({},root.widgetDefaults);return}
              if (root.tab === 5) {root.solarValues=Object.assign({},root.solarDefaults);
                root.solarLatitudeEdited=false;root.solarLongitudeEdited=false;return}
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
          SettingAction {
            onAcceptRequested: root.save(); design: theme; text: "Cancel"; onClicked: root.cancel() }
          SettingAction {
            onAcceptRequested: root.save(); design: theme; text: "Save"; emphasized: true; onClicked: root.save() }
        }
      }
    }
  }
}
