// Equal XDG min/max hints, changed live with Space; H fixes only the height.
import QtQuick
import Quickshell

FloatingWindow {
    id: root
    title: "fixedsize-fixture"
    property bool fixed: true
    property bool heightOnly: false
    implicitWidth: 320
    implicitHeight: 200
    minimumSize: fixed ? Qt.size(320, 200) : Qt.size(100, heightOnly ? 200 : 100)
    maximumSize: fixed ? Qt.size(320, 200) : Qt.size(10000, heightOnly ? 200 : 10000)
    color: "#303030"
    visible: true
    Item {
        anchors.fill: parent
        focus: true
        Keys.onSpacePressed: { root.fixed = !root.fixed; root.heightOnly = false }
        Keys.onPressed: event => {
            if (event.key === Qt.Key_H) { root.fixed = false; root.heightOnly = true }
        }
        Text {
            anchors.centerIn: parent
            color: "white"
            text: root.fixed ? "Fixed size (320 × 200)" : (root.heightOnly ? "Fixed height" : "Resizable")
        }
    }
}
