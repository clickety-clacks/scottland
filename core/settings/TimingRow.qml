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
  property bool hintHold:false
  // A row for another range or unit (the hold hotspot): the defaults are the 1 ms to 3 s timing
  // rows. 0 as minimum lets a setting be turned off.
  property real minimum:1
  property real maximum:3000
  property real step:10
  property real bigStep:100
  property string unit:"ms"
  property string endLabel:"3 s"
  property string explanation:""
  property string footer:""
  signal edited(real value)
  HoverHandler { id:helpHover }
  ControlHint {
    control:row;design:row.design;title:row.title
    explanation:row.explanation !== "" ? row.explanation : row.hintHold ? "How long a hint must be held, without letting go (or three fingers rest still on a window on a touchpad), to place an unfocused window side by side with the focused one. A shorter press stays a tap. Higher makes accidental holds less likely; lower pairs sooner." : row.doubleTap ? "The first hint press acts immediately. After releasing it, begin typing the same hint again within this interval to send the window to its rail. Complete every letter; key hold time and typing the rest do not use up the gap. Higher gives you more time; lower makes accidental double taps less likely." : "How long Alt alone must be held before window hints appear. Higher waits longer; lower enters sooner. Quick app shortcuts stay immediate."
    showing:helpHover.hovered || row.activeFocus
    viewport:row.viewport;scrollOffset:row.scrollOffset
  }
  implicitHeight:104
  activeFocusOnTab:true
  function set(x){edited(Math.round(Math.max(row.minimum,Math.min(row.maximum,(x-24)/(width-48)*row.maximum))))}
  Keys.onPressed:event=>{
    if(event.key===Qt.Key_Left||event.key===Qt.Key_Right)edited(Math.max(row.minimum,Math.min(row.maximum,value+(event.key===Qt.Key_Right?1:-1)*(event.modifiers&Qt.ShiftModifier?row.bigStep:row.step))))
    else if(event.key===Qt.Key_Backspace)edited(opening)
    else return
    event.accepted=true
  }
  Rectangle{anchors.fill:parent;radius:design.radius;color:design.tint(hit.pressed ? .25 : hit.containsMouse ? .14 : .07);border.width:row.activeFocus?1:0;border.color:design.accent}
  Text{x:18;y:14;text:row.title;color:design.foreground;font.family:design.family;font.pixelSize:15*design.textScale}
  Text{anchors.right:parent.right;anchors.rightMargin:18;y:12;text:(row.minimum===0&&row.value===0?"off":Math.round(row.value)+" "+row.unit);color:row.value!==row.opening?design.accent:design.foreground;font.family:design.family;font.pixelSize:21*design.textScale}
  Rectangle{x:24;y:61;width:parent.width-48;height:2;color:design.separator}
  Rectangle{x:24;y:60;width:(parent.width-48)*row.value/row.maximum;height:4;radius:2;color:design.accent}
  Rectangle{x:18;y:55;width:12;height:12;radius:row.doubleTap?6:2;color:design.foreground}
  Rectangle{x:18+(parent.width-48)*row.value/row.maximum;y:55;width:12;height:12;radius:row.doubleTap?6:2;color:design.accent}
  Text{x:24;y:80;text:row.footer!==""?row.footer:row.hintHold?"hint down → pairs with the focused window":row.doubleTap?"hint release → repeat begins":"Alt down → window hints appear";color:design.muted;font.family:design.family;font.pixelSize:12*design.textScale}
  Text{anchors.right:parent.right;anchors.rightMargin:24;y:80;text:row.endLabel;color:design.muted;font.family:design.family;font.pixelSize:12*design.textScale}
  MouseArea{id:hit;anchors.fill:parent;hoverEnabled:true;preventStealing:true;cursorShape:Qt.SizeHorCursor;onPressed:mouse=>{row.forceActiveFocus();row.set(mouse.x)};onPositionChanged:mouse=>{if(pressed)row.set(mouse.x)};onDoubleClicked:row.edited(row.opening)}
}
