import QtQuick
import Quickshell
import Quickshell.Wayland

// Exercise the real stack with too little space on its preferred (right) side.
ShellRoot {
  PanelWindow {
    anchors { top: true; right: true }
    margins { top: 176; right: 24 }
    implicitWidth: 560
    implicitHeight: 98
    color: "#1c1d22"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.OnDemand
    ParameterStack {
      anchors { fill: parent; margins: 20 }
      rows: [{ id: "width", label: "Center zone width", min: 10, max: 90, step: 0.5,
        hint: "Space where windows stay full size. Higher widens the center; lower gives more space to the sides." }]
      values: ({ width: 33.3 })
      opening: values
    }
  }
}
