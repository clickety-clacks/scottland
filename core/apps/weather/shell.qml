//@ pragma AppId scottland-weather

import QtQuick
import QtQuick.Layouts
import QtQml
import Quickshell
import Quickshell.Io

ShellRoot {
  id: root

  Component.onCompleted: {
    Qt.application.name = "Weather"
    stateMonitor.running = true
    refresh()
  }

  readonly property string runtimeScript: Quickshell.shellDir + "/runtime.py"
  readonly property string palettePath: Quickshell.env("SCOTTLAND_PALETTE") ||
    (Quickshell.env("XDG_RUNTIME_DIR") + "/scottland/" + Quickshell.env("WAYLAND_DISPLAY") + ".palette.json")
  property var palette: ({})
  property var reading: null
  property var lastSuccess: null
  property string lastPublished: ""
  property bool fetching: false
  property bool widgetized: false
  property bool closing: false

  readonly property color background: palette.background || "#1c1d22"
  readonly property color foreground: palette.foreground || "#e6e6e9"
  readonly property color accent: palette.accent || "#7aa2f7"
  readonly property color muted: Qt.rgba(foreground.r, foreground.g, foreground.b, 0.68)
  readonly property string fontFamily: palette.font_family || Qt.application.font.family
  readonly property real textScale: Math.max(0.5, Math.min(3, Number(palette.text_scale) || 1))
  readonly property bool imperial: {
    const system = Qt.locale().measurementSystem
    return system === Locale.ImperialUSSystem || system === Locale.ImperialUKSystem
  }

  FileView {
    path: root.palettePath
    printErrors: false
    watchChanges: true
    onFileChanged: reload()
    onLoaded: {
      try { root.palette = JSON.parse(text()) } catch (error) { root.palette = ({}) }
    }
  }

  function refresh() {
    if (fetching || closing) return
    fetching = true
    locationProcess.exec(["python3", runtimeScript, "location"])
  }

  function onLocationResult(text) {
    let data
    try {
      data = JSON.parse(text.trim())
    } catch (error) {
      failFetch()
      return
    }

    if (data.status === "no-location") {
      fetching = false
      setReading({ status: "no-location" })
      return
    }

    const latitude = data.latitude
    const longitude = data.longitude
    if (data.status !== "located" || typeof latitude !== "number" || !Number.isFinite(latitude)
        || typeof longitude !== "number" || !Number.isFinite(longitude)
        || latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180) {
      failFetch()
      return
    }

    fetchWeather(latitude, longitude)
  }

  function fetchWeather(latitude, longitude) {
    const temperatureUnit = imperial ? "fahrenheit" : "celsius"
    const windUnit = imperial ? "mph" : "kmh"
    const query = [
      "latitude=" + encodeURIComponent(String(latitude)),
      "longitude=" + encodeURIComponent(String(longitude)),
      "current=" + encodeURIComponent("temperature_2m,weather_code,wind_speed_10m,is_day"),
      "temperature_unit=" + temperatureUnit,
      "wind_speed_unit=" + windUnit,
      "timezone=auto"
    ].join("&")
    const request = new XMLHttpRequest()
    let finished = false

    function failed() {
      if (finished) return
      finished = true
      root.failFetch()
    }

    request.open("GET", "https://api.open-meteo.com/v1/forecast?" + query)
    request.timeout = 12000
    request.onerror = failed
    request.ontimeout = failed
    request.onreadystatechange = function() {
      if (request.readyState !== 4 || finished) return
      finished = true
      if (request.status < 200 || request.status >= 300) {
        root.failFetch()
        return
      }
      try {
        const response = JSON.parse(request.responseText)
        const current = response.current
        const temperature = current.temperature_2m
        const wind = current.wind_speed_10m
        const weatherCode = current.weather_code
        const offset = response.utc_offset_seconds
        const isDay = current.is_day
        if (typeof temperature !== "number" || !Number.isFinite(temperature)
            || typeof wind !== "number" || !Number.isFinite(wind)
            || typeof weatherCode !== "number" || !Number.isInteger(weatherCode)
            || typeof offset !== "number" || !Number.isFinite(offset)
            || (isDay !== 0 && isDay !== 1))
          throw new Error("incomplete current conditions")

        const condition = root.conditionFor(weatherCode, isDay === 1)
        const successful = {
          condition: condition.word,
          icon: condition.icon,
          temperature: Math.round(temperature) + "°" + (root.imperial ? "F" : "C"),
          wind: "Wind " + Math.round(wind) + " " + (root.imperial ? "mph" : "km/h"),
          updated: root.fetchTime(offset)
        }
        root.fetching = false
        root.lastSuccess = successful
        root.setReading(Object.assign({ status: "current" }, successful))
      } catch (error) {
        root.failFetch()
      }
    }
    request.send()
  }

  function fetchTime(offsetSeconds) {
    if (!Number.isInteger(offsetSeconds) || offsetSeconds % 60 !== 0)
      throw new Error("invalid local time offset")
    const offset = offsetSeconds
    const local = new Date(Date.now() + offset * 1000).toISOString().slice(0, 19)
    const absolute = Math.abs(offset)
    const hours = Math.floor(absolute / 3600)
    const minutes = Math.floor((absolute % 3600) / 60)
    const sign = offset >= 0 ? "+" : "-"
    return local + sign + String(hours).padStart(2, "0") + ":" + String(minutes).padStart(2, "0")
  }

  function conditionFor(code, isDay) {
    if (code === 0) return { word: "Clear", icon: isDay ? "weather-clear" : "weather-clear-night" }
    if (code === 1 || code === 2)
      return { word: code === 1 ? "Mostly clear" : "Partly cloudy",
        icon: isDay ? "weather-few-clouds" : "weather-few-clouds-night" }
    if (code === 3) return { word: "Overcast", icon: "weather-overcast" }
    if (code === 45 || code === 48) return { word: "Fog", icon: "weather-fog" }
    if (code >= 51 && code <= 55) return { word: "Drizzle", icon: "weather-showers-scattered" }
    if (code === 56 || code === 57) return { word: "Freezing drizzle", icon: "weather-freezing-rain" }
    if (code >= 61 && code <= 65) return { word: "Rain", icon: "weather-showers" }
    if (code === 66 || code === 67) return { word: "Freezing rain", icon: "weather-freezing-rain" }
    if (code >= 71 && code <= 77) return { word: "Snow", icon: "weather-snow" }
    if (code >= 80 && code <= 82) return { word: "Showers", icon: "weather-showers" }
    if (code === 85 || code === 86) return { word: "Snow showers", icon: "weather-snow-scattered" }
    if (code >= 95 && code <= 99) return { word: "Thunderstorm", icon: "weather-storm" }
    return { word: "Conditions", icon: "weather-severe-alert" }
  }

  function failFetch() {
    fetching = false
    if (lastSuccess)
      setReading(Object.assign({ status: "stale" }, lastSuccess))
    else
      setReading({ status: "unavailable" })
  }

  function publishedReading(value) {
    const result = { status: value.status }
    if (value.status === "current" || value.status === "stale") {
      result.condition = value.condition
      result.icon = value.icon
      result.temperature = value.temperature
      result.updated = value.updated
    }
    return result
  }

  function setReading(value) {
    const before = reading ? JSON.stringify(publishedReading(reading)) : ""
    const after = JSON.stringify(publishedReading(value))
    reading = value
    if (after !== before) publishReading(false)
  }

  function publishReading(force) {
    if (!reading || closing) return
    const payload = JSON.stringify(publishedReading(reading))
    if (!force && payload === lastPublished) return
    lastPublished = payload
    publisher.exec(["python3", runtimeScript, "publish", payload])
  }

  function onWidgetState(line) {
    const match = line.match(/StateChanged \(uint32 (\d+),\s*(?:boolean\s+)?(true|false),/)
    if (!match || Number(match[1]) !== Number(Quickshell.processId)) return
    widgetized = match[2] === "true"
    if (widgetized && reading) widgetPublication.start()
  }

  readonly property string stateText: {
    if (!reading) return "Checking the Scottland location…"
    switch (reading.status) {
    case "current": return "Current conditions"
    case "stale": return "Connection failed · showing the last successful reading"
    case "unavailable": return "Weather unavailable"
    case "no-location": return "No location · set it in Settings, Sunlight tab"
    default: return "Weather unavailable"
    }
  }

  Process {
    id: locationProcess
    stdout: StdioCollector {
      onStreamFinished: root.onLocationResult(text)
    }
  }

  Process {
    id: publisher
    onExited: (exitCode, exitStatus) => {
      if (exitCode !== 0 && root.widgetized && !root.closing) publishRetry.start()
    }
  }

  Process {
    id: stateMonitor
    command: ["gdbus", "monitor", "--session", "--dest", "org.scottland.Widgets",
      "--object-path", "/org/scottland/Widgets"]
    stdout: SplitParser {
      splitMarker: "\n"
      onRead: line => root.onWidgetState(line)
    }
    onExited: {
      if (!root.closing) monitorRetry.start()
    }
  }

  Timer {
    interval: 15 * 60 * 1000
    repeat: true
    running: !root.closing
    onTriggered: root.refresh()
  }

  Timer {
    id: widgetPublication
    interval: 100
    repeat: false
    onTriggered: root.publishReading(true)
  }

  Timer {
    id: publishRetry
    interval: 1000
    repeat: false
    onTriggered: root.publishReading(true)
  }

  Timer {
    id: monitorRetry
    interval: 3000
    repeat: false
    onTriggered: if (!root.closing) stateMonitor.running = true
  }

  Connections {
    target: Quickshell
    function onLastWindowClosed() {
      root.closing = true
      locationProcess.running = false
      publisher.running = false
      stateMonitor.running = false
      Qt.quit()
    }
  }

  FloatingWindow {
    id: weatherWindow
    title: "Weather"
    width: 520 * root.textScale
    height: 360 * root.textScale
    color: root.background
    visible: true

    Rectangle {
      anchors.fill: parent
      color: root.background

      ColumnLayout {
        anchors.fill: parent
        anchors.margins: 28 * root.textScale
        spacing: 12 * root.textScale

        Text {
          Layout.fillWidth: true
          text: "Weather"
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: 20 * root.textScale
          font.bold: true
          horizontalAlignment: Text.AlignHCenter
        }

        Item { Layout.fillHeight: true }

        Image {
          Layout.alignment: Qt.AlignHCenter
          Layout.preferredWidth: 72 * root.textScale
          Layout.preferredHeight: 72 * root.textScale
          visible: root.reading && (root.reading.status === "current" || root.reading.status === "stale")
          source: visible ? Quickshell.iconPath(root.reading.icon, true) : ""
          sourceSize.width: width
          sourceSize.height: height
          fillMode: Image.PreserveAspectFit
        }

        Text {
          Layout.fillWidth: true
          visible: root.reading && (root.reading.status === "current" || root.reading.status === "stale")
          text: visible ? root.reading.condition : ""
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: 26 * root.textScale
          horizontalAlignment: Text.AlignHCenter
        }

        Text {
          Layout.fillWidth: true
          visible: root.reading && (root.reading.status === "current" || root.reading.status === "stale")
          text: visible ? root.reading.temperature : ""
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: 22 * root.textScale
          horizontalAlignment: Text.AlignHCenter
        }

        Text {
          Layout.fillWidth: true
          visible: root.reading && (root.reading.status === "current" || root.reading.status === "stale")
          text: visible ? root.reading.wind : ""
          color: root.muted
          font.family: root.fontFamily
          font.pixelSize: 16 * root.textScale
          horizontalAlignment: Text.AlignHCenter
        }

        Item { Layout.fillHeight: true }

        Text {
          Layout.fillWidth: true
          text: root.stateText
          color: root.reading && root.reading.status === "stale" ? root.accent : root.muted
          font.family: root.fontFamily
          font.pixelSize: 13 * root.textScale
          wrapMode: Text.WordWrap
          horizontalAlignment: Text.AlignHCenter
        }

        Text {
          Layout.fillWidth: true
          visible: root.reading && (root.reading.status === "current" || root.reading.status === "stale")
          text: visible ? "Updated " + root.reading.updated.slice(11, 16) : ""
          color: root.muted
          font.family: root.fontFamily
          font.pixelSize: 12 * root.textScale
          horizontalAlignment: Text.AlignHCenter
        }
      }
    }
  }
}
