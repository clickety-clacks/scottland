// A minimal rail widget: the window's title on the desktop's colors. Click opens the window.
import QtQuick
import Quickshell
import Quickshell.Io

FloatingWindow {
    id: root
    implicitWidth: 240
    implicitHeight: 64
    color: "transparent"

    // Live presentation (title, rail, collapsed, badge...) and the desktop's colors.
    property var state: ({})
    property var colors: ({})
    FileView {
        path: Quickshell.env("SCOTTLAND_WIDGET_STATE")
        watchChanges: true
        onFileChanged: reload()
        onLoaded: { try { root.state = JSON.parse(text()) } catch (e) {} }
    }
    FileView {
        path: Quickshell.env("SCOTTLAND_PALETTE")
        watchChanges: true
        onFileChanged: reload()
        onLoaded: { try { root.colors = JSON.parse(text()) } catch (e) {} }
    }

    // Open the window in the center, as a click on the default card does.
    Process {
        id: open
        command: ["busctl", "--user", "call", "org.scottland.Widgets",
            "/org/scottland/widget/" + Quickshell.env("SCOTTLAND_WIDGET_ID"),
            "org.scottland.Widget", "Open"]
    }

    Rectangle {
        anchors.fill: parent
        radius: 20
        color: root.colors.background || "#2e3440"
        Text {
            anchors.fill: parent
            anchors.margins: 16
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
            text: root.state.title || root.state.app_id || ""
            color: root.colors.foreground || "#d8dee9"
            font.bold: true
        }
        MouseArea {
            anchors.fill: parent
            onClicked: open.running = true
        }
    }
}
