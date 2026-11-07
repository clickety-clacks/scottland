import QtQuick
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import Quickshell.Wayland

QtObject {
    id: root

    property bool visible: false
    property var screen: null
    property string message: ""
    property string iconName: ""
    property int progress: -1
    property double remainingMs: 0
    property double timerStartedAt: 0
    property var palette: ({})

    readonly property string palettePath: Quickshell.env("SCOTTLAND_PALETTE") ||
        ((Quickshell.env("SCOTTLAND_SESSION_DIR") ||
          ((Quickshell.env("XDG_RUNTIME_DIR") || "") + "/scottland")) + "/" +
         (Quickshell.env("WAYLAND_DISPLAY") || "wayland") + ".palette.json")
    readonly property string displayMessage: message.replace(/[\r\n\t]+/g, " ")
    readonly property string iconPath: iconName ? (Quickshell.iconPath(iconName, true) || "") : ""
    readonly property color background: palette.background || "#2e3440"
    readonly property color foreground: palette.foreground || "#d8dee9"
    readonly property color muted: palette.muted || Qt.rgba(foreground.r, foreground.g, foreground.b, 0.68)
    readonly property color accent: palette.accent || "#81a1c1"
    readonly property string fontFamily: palette.font_family || Qt.application.font.family
    readonly property real textScale: Math.max(0.5, Math.min(3, Number(palette.text_scale) || 1))
    readonly property bool backingWindowVisible: surface.visible
    readonly property real cardWidth: Math.max(1, Math.min(
        620 * textScale, (screen ? screen.width : 1280) - 48 * textScale))

    function present(text, icon, percentage, durationMs) {
        hideTimer.stop()
        timerStartedAt = 0
        message = text
        iconName = icon || ""
        progress = percentage
        remainingMs = durationMs > 0 ? durationMs : 1500
        visible = true
        syncTimer()
    }

    function syncTimer() {
        if (!visible || !surface.visible) {
            if (hideTimer.running) {
                remainingMs = Math.max(0, remainingMs - (Date.now() - timerStartedAt))
                timerStartedAt = 0
                hideTimer.stop()
            }
            return
        }
        if (remainingMs <= 0) {
            visible = false
            return
        }
        timerStartedAt = Date.now()
        hideTimer.interval = Math.min(Math.ceil(remainingMs), 2147483647)
        hideTimer.restart()
    }

    function timerElapsed() {
        remainingMs = Math.max(0, remainingMs - (Date.now() - timerStartedAt))
        timerStartedAt = 0
        if (remainingMs <= 0) {
            visible = false
            return
        }
        syncTimer()
    }

    onVisibleChanged: syncTimer()

    FileView {
        path: root.palettePath
        printErrors: false
        watchChanges: true
        onFileChanged: reload()
        onLoaded: {
            try { root.palette = JSON.parse(text()) } catch (error) { root.palette = ({}) }
        }
    }

    PanelWindow {
        id: surface
        screen: root.screen
        visible: root.visible
        anchors { left: true; right: true; bottom: true }
        implicitHeight: card.implicitHeight + 56 * root.textScale
        color: "transparent"
        focusable: false
        exclusionMode: ExclusionMode.Ignore
        mask: Region {}
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
        WlrLayershell.namespace: "scottland-osd"

        onVisibleChanged: root.syncTimer()

        Rectangle {
            id: card
            width: root.cardWidth
            implicitHeight: content.implicitHeight + 28 * root.textScale
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: parent.bottom
            anchors.bottomMargin: 28 * root.textScale
            radius: 14 * root.textScale
            color: root.background
            border.width: Math.max(1, root.textScale)
            border.color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.16)

            ColumnLayout {
                id: content
                anchors.fill: parent
                anchors.margins: 14 * root.textScale
                spacing: 10 * root.textScale

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 12 * root.textScale

                    Image {
                        source: root.iconPath
                        visible: root.iconPath !== ""
                        Layout.preferredWidth: visible ? 34 * root.textScale : 0
                        Layout.preferredHeight: visible ? 34 * root.textScale : 0
                        fillMode: Image.PreserveAspectFit
                        smooth: true
                    }

                    Text {
                        text: root.displayMessage
                        Layout.fillWidth: true
                        color: root.foreground
                        font.family: root.fontFamily
                        font.pixelSize: Math.max(12, Math.round(17 * root.textScale))
                        font.weight: Font.Medium
                        elide: Text.ElideRight
                        wrapMode: Text.NoWrap
                        maximumLineCount: 1
                        textFormat: Text.PlainText
                    }
                }

                RowLayout {
                    visible: root.progress >= 0
                    Layout.fillWidth: true
                    spacing: 10 * root.textScale

                    Item {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 7 * root.textScale

                        Rectangle {
                            anchors.fill: parent
                            radius: height / 2
                            color: Qt.rgba(root.muted.r, root.muted.g, root.muted.b, 0.28)
                        }

                        Rectangle {
                            width: parent.width * Math.max(0, Math.min(100, root.progress)) / 100
                            height: parent.height
                            radius: height / 2
                            color: root.accent
                        }
                    }

                    Text {
                        text: root.progress + "%"
                        Layout.preferredWidth: 44 * root.textScale
                        horizontalAlignment: Text.AlignRight
                        color: root.foreground
                        font.family: root.fontFamily
                        font.pixelSize: Math.max(11, Math.round(13 * root.textScale))
                    }
                }
            }
        }
    }

    Timer {
        id: hideTimer
        repeat: false
        onTriggered: root.timerElapsed()
    }
}
