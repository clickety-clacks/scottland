//@ pragma AppId scottland-system-stats

import QtQuick
import QtQuick.Layouts
import Quickshell
import Quickshell.Io

ShellRoot {
    id: root

    Component.onCompleted: Qt.application.name = "System Stats"

    property var reading: ({})
    property var palette: ({})
    property color background: "#2e3440"
    property color foreground: "#d8dee9"
    property color muted: "#97a3ab"
    property color accent: "#81a1c1"
    readonly property real textScale: Math.max(0.5, Math.min(3, Number(palette.text_scale) || 1))
    readonly property string fontFamily: palette.font_family || Qt.application.font.family
    readonly property string palettePath: Quickshell.env("SCOTTLAND_PALETTE") ||
        ((Quickshell.env("XDG_RUNTIME_DIR") || "") + "/scottland/" +
            (Quickshell.env("WAYLAND_DISPLAY") || "") + ".palette.json")
    readonly property string backend: Quickshell.env("SCOTTLAND_SYSTEM_STATS_BIN") || "scottland-system-stats"

    FileView {
        path: root.palettePath
        printErrors: false
        watchChanges: true
        onFileChanged: reload()
        onLoaded: {
            try {
                const value = JSON.parse(text())
                root.palette = value
                if (typeof value.background === "string") root.background = value.background
                if (typeof value.foreground === "string") root.foreground = value.foreground
                if (typeof value.muted === "string") root.muted = value.muted
                if (typeof value.accent === "string") root.accent = value.accent
            } catch (error) {
                root.palette = ({})
            }
        }
    }

    Process {
        id: stateChanges
        command: ["busctl", "--user", "monitor",
            "--match=interface='org.scottland.Windows',member='StateChanged'", "org.scottland.Widgets"]
        running: true
        stdout: SplitParser {
            onRead: line => { if (readings.running) readings.write(line + "\n") }
        }
    }

    Process {
        id: readings
        command: [root.backend, "--watch"]
        stdinEnabled: true
        running: true
        stdout: SplitParser {
            onRead: line => {
                try { root.reading = JSON.parse(line) } catch (error) {}
            }
        }
    }

    FloatingWindow {
        id: window
        title: "System Stats"
        visible: true
        width: 440 * root.textScale
        height: 280 * root.textScale
        color: root.background
        onClosing: Qt.quit()

        Rectangle {
            anchors.fill: parent
            color: root.background
            radius: 18

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 24 * root.textScale
                spacing: 12 * root.textScale

                Text {
                    text: "System Stats"
                    color: root.accent
                    font.family: root.fontFamily
                    font.pixelSize: 22 * root.textScale
                    font.weight: Font.DemiBold
                }

                Text {
                    text: "CPU " + (typeof root.reading.cpu === "number" ? root.reading.cpu + "%" : "unavailable")
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: 20 * root.textScale
                    Layout.fillWidth: true
                }

                Text {
                    text: "Memory " + (typeof root.reading.memory_used_gb === "number"
                        ? root.reading.memory_used_gb.toFixed(1) + " GB" : "unavailable") + " / " +
                        (typeof root.reading.memory_total_gb === "number"
                            ? root.reading.memory_total_gb.toFixed(1) + " GB" : "unavailable") + " (" +
                        (typeof root.reading.memory === "number" ? root.reading.memory + "%" : "unavailable") + ")"
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: 20 * root.textScale
                    Layout.fillWidth: true
                }

                Text {
                    text: "Load " + (typeof root.reading.load === "number" ? root.reading.load.toFixed(2) : "unavailable")
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: 20 * root.textScale
                    Layout.fillWidth: true
                }

                Item { Layout.fillHeight: true }

                Text {
                    text: "Updated " + (typeof root.reading.updated === "string" ? root.reading.updated : "unavailable")
                    color: root.muted
                    font.family: root.fontFamily
                    font.pixelSize: 12 * root.textScale
                    Layout.fillWidth: true
                }
            }
        }
    }
}
