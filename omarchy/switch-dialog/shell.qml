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
  readonly property string settingsPath: Quickshell.env("SETTINGS")
  readonly property string targetName: target === "scottland" ? "Scottland" : "Hyprland"
  readonly property string currentName: current === "scottland" ? "Scottland" : "Hyprland"
  property bool closeCurrent: true
  property bool busy: false

  FileView {
    id: settingsFile
    path: root.settingsPath
    printErrors: false
    onLoaded: {
      try { root.closeCurrent = JSON.parse(text()).closeCurrent !== false } catch (e) {}
    }
  }

  Process {
    id: switchProc
    onExited: Qt.quit()
  }

  function toggle() {
    closeCurrent = !closeCurrent
  }

  function confirm() {
    if (busy) return
    busy = true
    settingsFile.setText(JSON.stringify({ closeCurrent: closeCurrent }, null, 2) + "\n")
    switchProc.command = ["scottland-switch", "--to", target, closeCurrent ? "--close" : "--keep"]
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
      Keys.onSpacePressed: root.toggle()

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

        Item {
          width: parent.width
          height: checkRow.implicitHeight

          Row {
            id: checkRow
            spacing: 10 * Style.fontScale

            Rectangle {
              width: Style.font.body * 1.3
              height: width
              anchors.verticalCenter: label.verticalCenter
              color: "transparent"
              border.color: Color.menu.text
              border.width: 1
              radius: Style.cornerRadius / 2

              Text {
                anchors.centerIn: parent
                visible: root.closeCurrent
                text: "✓"
                color: Color.menu.selectedText
                font.pixelSize: Style.font.body
              }
            }

            Text {
              id: label
              text: "Close " + root.currentName + " when switching"
              color: Color.menu.text
              font.family: Style.font.family
              font.pixelSize: Style.font.body
            }
          }

          MouseArea {
            anchors.fill: checkRow
            cursorShape: Qt.PointingHandCursor
            onClicked: root.toggle()
          }
        }

        Text {
          width: parent.width
          wrapMode: Text.WordWrap
          leftPadding: Style.font.body * 1.3 + 10 * Style.fontScale
          text: "Unchecked keeps both sessions running. Some apps may not work correctly when both run at once."
          color: Qt.rgba(Color.menu.text.r, Color.menu.text.g, Color.menu.text.b, 0.65)
          font.family: Style.font.family
          font.pixelSize: Style.font.bodySmall
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
