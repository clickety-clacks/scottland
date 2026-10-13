// Logs every Hyprland IPC event a Quickshell client receives, as the Omarchy shell's Hyprland
// module would see them (tests: does the event stream survive a shim restart?).
import QtQuick
import Quickshell
import Quickshell.Hyprland

ShellRoot {
    Component.onCompleted: console.log("probe ready")

    Connections {
        target: Hyprland
        function onRawEvent(event) { console.log("event " + event.name) }
    }
}
