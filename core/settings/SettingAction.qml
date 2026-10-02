import QtQuick
import QtQuick.Controls

AbstractButton {
  id: control
  required property var design
  property bool emphasized: false
  property bool joined: false
  property int joinPosition: -1
  signal acceptRequested()
  Keys.onReturnPressed: acceptRequested()
  Keys.onEnterPressed: acceptRequested()
  implicitWidth: Math.max(100, label.implicitWidth + 36)
  implicitHeight: 42
  hoverEnabled: true
  activeFocusOnTab: true
  background: Rectangle {
    radius: !control.joined || control.joinPosition !== 1 ? control.design.radius : 0
    color: Qt.tint(control.design.background, control.design.tint(control.down ? 0.42 : control.checked || control.emphasized ? 0.25 : control.hovered ? 0.16 : 0.07))
    Rectangle {
      visible:control.joined && control.joinPosition!==1
      x:control.joinPosition===0 ? parent.width-width : 0
      width:control.design.radius; height:parent.height; color:parent.color
    }
    border.width: control.activeFocus ? 2 : 0
    border.color: control.design.accent
    Rectangle {
      visible: control.checked
      anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: 10 }
      height: 2; radius: 1; color: control.design.accent
    }
  }
  contentItem: Text {
    id: label
    text: control.text
    horizontalAlignment: Text.AlignHCenter
    verticalAlignment: Text.AlignVCenter
    color: control.checked || control.emphasized ? control.design.accent : control.design.foreground
    font.family: control.design.family
    font.pixelSize: 15 * control.design.textScale
    font.weight: control.checked || control.emphasized ? Font.DemiBold : Font.Medium
  }
}
