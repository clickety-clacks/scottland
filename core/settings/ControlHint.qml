import QtQuick
import Quickshell

// Passive explanation outside its control, using the same palette and shape as the rows.
PopupWindow {
  id: bubble
  required property Item control
  required property var design
  property string title
  property string explanation
  property bool showing: false
  property bool refreshing: false
  readonly property string style: [design.background,design.foreground,design.accent,design.family,design.textScale].join("|")
  onStyleChanged: if(visible) {
    // As with the numeric row hints, remap to discard Wayfire's cached popup buffer.
    refreshing=true
    Qt.callLater(()=>refreshing=false)
  }
  property Item viewport: null
  property real scrollOffset: 0
  readonly property real controlY: { scrollOffset; control.y; control.height; return viewport ? control.mapToItem(viewport,0,0).y : 0 }
  visible: control.visible && showing && !refreshing && (!viewport || controlY >= 0 && controlY + control.height <= viewport.height+1)
  onControlYChanged: if(visible)anchor.updateAnchor()
  anchor.item:control
  anchor.rect:Qt.rect(-28,0,control.width+56,control.height)
  anchor.edges:Edges.Right
  anchor.gravity:Edges.Right
  anchor.adjustment:PopupAdjustment.FlipX|PopupAdjustment.SlideY
  implicitWidth:320
  implicitHeight:content.implicitHeight+28
  color:"transparent"
  grabFocus:false
  mask:Region {}
  Rectangle {
    anchors.fill:parent;radius:design.radius;color:design.background;border.color:design.accent
    Column {
      id:content
      anchors{left:parent.left;right:parent.right;top:parent.top;margins:14}
      spacing:6
      Text{width:parent.width;text:bubble.title;color:design.accent;font.family:design.family;font.pixelSize:15*design.textScale;font.weight:Font.DemiBold;wrapMode:Text.WordWrap}
      Text{width:parent.width;text:bubble.explanation;color:design.foreground;font.family:design.family;font.pixelSize:15*design.textScale;wrapMode:Text.WordWrap}
    }
  }
}
