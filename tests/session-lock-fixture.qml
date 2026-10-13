// A minimal ext-session-lock client for tests: locks at once with a solid magenta surface on every
// output and unlocks when Return is pressed on it. Omarchy's own lock (Quickshell WlSessionLock)
// uses the same protocol path.
import QtQuick
import Quickshell
import Quickshell.Wayland

ShellRoot {
    WlSessionLock {
        id: lock
        locked: true

        WlSessionLockSurface {
            Rectangle {
                anchors.fill: parent
                color: "#ff00ff"
                focus: true
                Keys.onReturnPressed: lock.locked = false
            }
        }
    }
}
