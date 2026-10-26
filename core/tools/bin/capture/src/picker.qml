import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland

// scottland-capture's pick overlay: the still on every output, the drag and the highlight.
// It decides nothing. It reports the pointer and keys to scottland-capture over a socket in
// global logical coordinates and draws what it is told:
//   to the binary:   hover X Y | release X1 Y1 X2 Y2 | key NAME
//   from the binary: highlight X Y W H | highlight none | notice TEXT | quit
ShellRoot {
  id: root

  readonly property var config: JSON.parse(Quickshell.env("SCOTTLAND_CAPTURE_PICK") || "{}")
  property bool hasHighlight: false
  property rect highlight: Qt.rect(0, 0, 0, 0)
  property string notice: ""
  property bool dragging: false
  property point pressed: Qt.point(0, 0)
  property point pointer: Qt.point(0, 0)

  function send(line) {
    link.write(line + "\n")
    link.flush()
  }

  function receive(line) {
    const words = line.split(" ")
    if (words[0] === "highlight") {
      root.hasHighlight = words[1] !== "none"
      if (root.hasHighlight)
        root.highlight = Qt.rect(+words[1], +words[2], +words[3], +words[4])
    } else if (words[0] === "notice") {
      root.notice = line.slice(7)
      noticeTimer.restart()
    } else if (words[0] === "quit") {
      Qt.quit()
    }
  }

  Socket {
    id: link
    path: root.config.socket || ""
    connected: true
    parser: SplitParser { onRead: line => root.receive(line) }
    onConnectedChanged: if (!connected) Qt.quit()
  }

  Timer {
    id: noticeTimer
    interval: 4000
    onTriggered: root.notice = ""
  }

  Variants {
    model: Quickshell.screens

    PanelWindow {
      id: panel
      required property var modelData
      readonly property bool focusedOutput: modelData.name === root.config.focused
      screen: modelData
      anchors { top: true; bottom: true; left: true; right: true }
      color: "black"
      exclusionMode: ExclusionMode.Ignore
      WlrLayershell.layer: WlrLayer.Overlay
      WlrLayershell.keyboardFocus: focusedOutput ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.None
      WlrLayershell.namespace: "scottland-capture"

      function globalX(x) { return Math.floor(x) + panel.modelData.x }
      function globalY(y) { return Math.floor(y) + panel.modelData.y }

      Image {
        anchors.fill: parent
        source: root.config.stills && root.config.stills[panel.modelData.name]
          ? "file://" + root.config.stills[panel.modelData.name] : ""
        smooth: false
        mipmap: false
        cache: false
        fillMode: Image.Stretch
      }

      Rectangle {
        visible: root.hasHighlight && !root.dragging
        x: root.highlight.x - panel.modelData.x
        y: root.highlight.y - panel.modelData.y
        width: root.highlight.width
        height: root.highlight.height
        color: Qt.rgba(0.48, 0.64, 0.97, 0.18)
        border.color: Qt.rgba(0.48, 0.64, 0.97, 0.9)
        border.width: 2
      }

      Rectangle {
        visible: root.dragging
        x: Math.min(root.pressed.x, root.pointer.x) - panel.modelData.x
        y: Math.min(root.pressed.y, root.pointer.y) - panel.modelData.y
        width: Math.abs(root.pointer.x - root.pressed.x)
        height: Math.abs(root.pointer.y - root.pressed.y)
        color: Qt.rgba(1, 1, 1, 0.12)
        border.color: "white"
        border.width: 1
      }

      Rectangle {
        visible: panel.focusedOutput && root.notice !== ""
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 48
        width: Math.min(parent.width - 48, noticeText.implicitWidth + 32)
        height: noticeText.implicitHeight + 20
        radius: 8
        color: Qt.rgba(0, 0, 0, 0.8)
        Text {
          id: noticeText
          anchors.centerIn: parent
          width: Math.min(implicitWidth, panel.width - 80)
          wrapMode: Text.Wrap
          color: "white"
          font.pixelSize: 15
          text: root.notice
        }
      }

      MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton
        cursorShape: Qt.CrossCursor
        onPressed: mouse => {
          root.pressed = Qt.point(panel.globalX(mouse.x), panel.globalY(mouse.y))
          root.pointer = root.pressed
          root.dragging = true
        }
        onPositionChanged: mouse => {
          root.pointer = Qt.point(panel.globalX(mouse.x), panel.globalY(mouse.y))
          root.send("hover " + root.pointer.x + " " + root.pointer.y)
        }
        onReleased: mouse => {
          root.pointer = Qt.point(panel.globalX(mouse.x), panel.globalY(mouse.y))
          root.dragging = false
          root.send("release " + root.pressed.x + " " + root.pressed.y + " "
            + root.pointer.x + " " + root.pointer.y)
        }
      }

      Item {
        anchors.fill: parent
        focus: panel.focusedOutput
        Keys.onPressed: event => {
          const ctrl = (event.modifiers & Qt.ControlModifier) !== 0
          let name = ""
          switch (event.key) {
          case Qt.Key_Tab: name = ctrl ? "ctrl-tab" : "tab"; break
          case Qt.Key_Left: name = "left"; break
          case Qt.Key_Right: name = "right"; break
          case Qt.Key_Up: name = "up"; break
          case Qt.Key_Down: name = "down"; break
          case Qt.Key_Return:
          case Qt.Key_Enter: name = ctrl ? "ctrl-return" : "return"; break
          case Qt.Key_Escape: name = "escape"; break
          }
          if (name !== "") {
            root.send("key " + name)
            event.accepted = true
          }
        }
      }
    }
  }
}
