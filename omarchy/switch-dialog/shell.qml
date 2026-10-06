import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import qs.Commons

// "Switch to …" dialog for scottland-switch. Runs standalone (qs -p) so it works in
// Hyprland and in Scottland, with or without the Omarchy shell running.
ShellRoot {
  id: root

  readonly property string target: Quickshell.env("OTHER") || "scottland"
  readonly property string current: Quickshell.env("CURRENT") || "hyprland"
  readonly property string targetName: target === "scottland" ? "Scottland" : "Hyprland"
  readonly property string currentName: current === "scottland" ? "Scottland" : "Hyprland"
  property bool busy: false

  Process {
    id: switchProc
    onExited: Qt.quit()
  }

  function confirm() {
    if (busy) return
    busy = true
    switchProc.command = ["scottland-switch", "--to", target]
    switchProc.running = true
  }

  PanelWindow {
    anchors { top: true; bottom: true; left: true; right: true }
    color: Color.menu.scrim
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.Exclusive
    WlrLayershell.namespace: "scottland-switch"

    MouseArea {
      anchors.fill: parent
      onClicked: Qt.quit()
    }

    Rectangle {
      id: card
      anchors.centerIn: parent
      width: 440 * Style.fontScale
      height: body.implicitHeight + 48 * Style.fontScale
      color: Color.menu.background
      border.color: Color.menu.border
      border.width: 1
      radius: Style.cornerRadius
      focus: true

      Keys.onEscapePressed: Qt.quit()
      Keys.onReturnPressed: root.confirm()
      Keys.onEnterPressed: root.confirm()

      MouseArea { anchors.fill: parent }  // clicks inside the card don't dismiss

      Column {
        id: body
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 24 * Style.fontScale }
        spacing: 16 * Style.fontScale

        Text {
          text: "Switch to " + root.targetName
          color: Color.menu.text
          font.family: Style.font.family
          font.pixelSize: Style.font.title
          font.bold: true
        }

        Text {
          width: parent.width
          wrapMode: Text.WordWrap
          text: "Switching will close all windows in " + root.currentName + "."
          color: Color.urgent
          font.family: Style.font.family
          font.pixelSize: Style.font.body
          font.bold: true
        }

        Row {
          anchors.right: parent.right
          spacing: 10 * Style.fontScale

          DialogButton { text: "Cancel"; onActivated: Qt.quit() }
          DialogButton { text: root.busy ? "Switching…" : "Switch"; primary: true; onActivated: root.confirm() }
        }
      }
    }
  }

  component DialogButton: Rectangle {
    id: button
    property alias text: caption.text
    property bool primary: false
    signal activated()

    width: caption.implicitWidth + 28 * Style.fontScale
    height: caption.implicitHeight + 14 * Style.fontScale
    radius: Style.cornerRadius
    color: primary ? Qt.rgba(Color.accent.r, Color.accent.g, Color.accent.b, 0.18) : "transparent"
    border.color: primary ? Color.accent : Color.menu.border
    border.width: 1

    Text {
      id: caption
      anchors.centerIn: parent
      color: Color.menu.text
      font.family: Style.font.family
      font.pixelSize: Style.font.body
    }

    MouseArea {
      anchors.fill: parent
      cursorShape: Qt.PointingHandCursor
      onClicked: button.activated()
    }
  }
}
