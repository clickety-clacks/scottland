import QtQuick
import Quickshell
import Quickshell.Io
FloatingWindow {
    id: root
    property var state: ({})
    property bool tint: false
    Timer { interval: 25; running: true; repeat: true; onTriggered: root.tint = !root.tint }
    title: "Scottland widget: " + (state.title || "round")
    property bool offset: (state.title || "").startsWith("offset-")
    implicitWidth: offset ? 240 : 120
    implicitHeight: 120
    minimumSize: Qt.size(implicitWidth, 120)
    maximumSize: Qt.size(implicitWidth, 120)
    color: "transparent"
    visible: typeof state.revision === "number"
    FileView {
        path: Quickshell.env("SCOTTLAND_WIDGET_STATE")
        watchChanges: true
        onFileChanged: reload()
        onLoaded: root.state = JSON.parse(text())
    }
    Rectangle {
        x: 12
        anchors.verticalCenter: parent.verticalCenter
        width: 96; height: 96; radius: 48
        color: root.tint ? "#4cae80" : "#5692c6"
    }
}
