import QtQuick

FocusScope {
  id:row
  property Item viewport:null
  property real scrollOffset:0
  required property var design
  property string title
  property real value:300
  property real opening:300
  property bool doubleTap:false
  signal edited(real value)
  HoverHandler { id:helpHover }
  ControlHint {
    control:row;design:row.design;title:row.title
    explanation:row.doubleTap ? "The first hint press acts immediately. A second within this interval sends the window to its rail. Higher gives you more time; lower makes accidental double taps less likely." : "How long Alt alone must be held before window hints appear. Higher waits longer; lower enters sooner. Quick app shortcuts stay immediate."
    showing:helpHover.hovered || row.activeFocus
    viewport:row.viewport;scrollOffset:row.scrollOffset
  }
  implicitHeight:104
  activeFocusOnTab:true
  function set(x){edited(Math.round(Math.max(1,Math.min(3000,(x-24)/(width-48)*3000))))}
  Keys.onPressed:event=>{
    if(event.key===Qt.Key_Left||event.key===Qt.Key_Right)edited(Math.max(1,Math.min(3000,value+(event.key===Qt.Key_Right?1:-1)*(event.modifiers&Qt.ShiftModifier?100:10))))
    else if(event.key===Qt.Key_Backspace)edited(opening)
    else return
    event.accepted=true
  }
  Rectangle{anchors.fill:parent;radius:design.radius;color:design.tint(hit.pressed ? .25 : hit.containsMouse ? .14 : .07);border.width:row.activeFocus?1:0;border.color:design.accent}
  Text{x:18;y:14;text:row.title;color:design.foreground;font.family:design.family;font.pixelSize:15*design.textScale}
  Text{anchors.right:parent.right;anchors.rightMargin:18;y:12;text:Math.round(row.value)+" ms";color:row.value!==row.opening?design.accent:design.foreground;font.family:design.family;font.pixelSize:21*design.textScale}
  Rectangle{x:24;y:61;width:parent.width-48;height:2;color:design.separator}
  Rectangle{x:24;y:60;width:(parent.width-48)*row.value/3000;height:4;radius:2;color:design.accent}
  Rectangle{x:18;y:55;width:12;height:12;radius:row.doubleTap?6:2;color:design.foreground}
  Rectangle{x:18+(parent.width-48)*row.value/3000;y:55;width:12;height:12;radius:row.doubleTap?6:2;color:design.accent}
  Text{x:24;y:80;text:row.doubleTap?"first tap → second tap sends to rail":"Alt down → window hints appear";color:design.muted;font.family:design.family;font.pixelSize:12*design.textScale}
  Text{anchors.right:parent.right;anchors.rightMargin:24;y:80;text:"3 s";color:design.muted;font.family:design.family;font.pixelSize:12*design.textScale}
  MouseArea{id:hit;anchors.fill:parent;hoverEnabled:true;preventStealing:true;cursorShape:Qt.SizeHorCursor;onPressed:mouse=>{row.forceActiveFocus();row.set(mouse.x)};onPositionChanged:mouse=>{if(pressed)row.set(mouse.x)};onDoubleClicked:row.edited(row.opening)}
}
