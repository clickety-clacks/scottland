import QtQuick
import Quickshell
import Quickshell.Io
FloatingWindow {
    id: root
    property var state: ({})
    property bool tint: false
    property bool inset: (state.title || "").startsWith("inset-")
    Timer { interval: 25; running: !root.inset; repeat: true; onTriggered: root.tint = !root.tint }
    title: "Scottland widget: " + (state.title || "round")
    property bool offset: (state.title || "").startsWith("offset-")
    implicitWidth: inset ? 400 : offset ? 240 : 120
    implicitHeight: inset ? 400 : 120
    minimumSize: Qt.size(implicitWidth, implicitHeight)
    maximumSize: Qt.size(implicitWidth, implicitHeight)
    color: "transparent"
    visible: typeof state.revision === "number"
    FileView {
        path: Quickshell.env("SCOTTLAND_WIDGET_STATE")
        watchChanges: true
        onFileChanged: reload()
        onLoaded: root.state = JSON.parse(text())
    }
    Rectangle {
        x: root.inset ? (root.width - width) / 2 : 12
        anchors.verticalCenter: parent.verticalCenter
        width: 96; height: 96; radius: 48
        color: root.tint ? "#4cae80" : "#5692c6"
    }
}
