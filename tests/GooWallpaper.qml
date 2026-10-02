import QtQuick
import Quickshell
import Quickshell.Wayland

// Static, colorful background layer for GO14/GO15. No app content or animation.
ShellRoot {
  PanelWindow {
    anchors { top: true; bottom: true; left: true; right: true }
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.layer: WlrLayer.Background
    WlrLayershell.namespace: "goo-wallpaper-test"
    color: "#101827"
    Rectangle {
      anchors.fill: parent
      gradient: Gradient {
        orientation: Gradient.Horizontal
        GradientStop { position: 0; color: "#163251" }
        GradientStop { position: 0.23; color: "#358ca0" }
        GradientStop { position: 0.43; color: "#754c97" }
        GradientStop { position: 0.64; color: "#d78653" }
        GradientStop { position: 0.84; color: "#668b58" }
        GradientStop { position: 1; color: "#172941" }
      }
    }
    Repeater {
      model: 12
      Rectangle {
        required property int index
        x: -100; y: index * 85 - 300
        width: 1800; height: 28; rotation: 12
        color: index % 2 ? "#25ffffff" : "#25000000"
      }
    }
    Rectangle {
      anchors.fill: parent
      visible: Quickshell.env("GOO_WALLPAPER_COLOR") !== ""
      color: Quickshell.env("GOO_WALLPAPER_COLOR") || "transparent"
      Rectangle {
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
        height: parent.height / 2
        color: Quickshell.env("GOO_WALLPAPER_BOTTOM") || "transparent"
      }
    }
  }
}
