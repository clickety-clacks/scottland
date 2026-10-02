import QtQuick
import QtQuick.Layouts

FocusScope {
  id: editor
  property Item viewport:null
  property real scrollOffset:0
  property string explanation
  required property var design
  property string title
  property string hint: "Click to add or select · drag to shape · Delete / Backspace removes a selected point"
  property string leftLabel: "center edge"
  property string rightLabel: "rail"
  property real minimum: 0.05
  property real maximum: 1
  property bool descending: false
  property var points: [{x:0,y:1},{x:1,y:0.2}]
  property var opening: []
  property var evaluate
  property var formatValue: v => Math.round(v*100) + "%"
  property int selected: -1
  property int hovered: -1
  property int dragging: -1
  readonly property real plotLeft: 58
  readonly property real plotTop: 56
  readonly property real plotWidth: width-plotLeft-24
  readonly property real plotHeight: height-plotTop-68
  readonly property bool modified: JSON.stringify(points) !== JSON.stringify(opening)
  signal edited(var points)
  HoverHandler { id:helpHover }
  ControlHint {
    control:editor;design:editor.design;title:editor.title
    explanation:editor.explanation
    showing:helpHover.hovered || editor.activeFocus
    viewport:editor.viewport;scrollOffset:editor.scrollOffset
  }
  implicitHeight: 340
  activeFocusOnTab: true
  function toX(v) { return plotLeft+v*plotWidth }
  function toY(v) { return plotTop+(maximum-v)/(maximum-minimum)*plotHeight }
  function fromX(v) { return Math.max(0, Math.min(1,(v-plotLeft)/plotWidth)) }
  function fromY(v) { return Math.max(minimum,Math.min(maximum,maximum-(v-plotTop)/plotHeight*(maximum-minimum))) }
  function pointAt(x,y) {
    let best = -1, distance = 18
    for (let i=0;i<points.length;i++) {
      const d = Math.hypot(toX(points[i].x)-x,toY(points[i].y)-y)
      if (d < distance) { best=i; distance=d }
    }
    return best
  }
  function remove(index) {
    if (index>0 && index<points.length-1) { edited(points.filter((_,i)=>i!==index)); selected=-1; hovered=-1; dragging=-1 }
  }
  function move(index,x,y) {
    const p=points.map(v=>({x:v.x,y:v.y})), last=p.length-1
    p[index].y=fromY(y)
    if (descending) p[index].y=Math.min(index>0?p[index-1].y:maximum,Math.max(index<last?p[index+1].y:minimum,p[index].y))
    if (index>0 && index<last) p[index].x=Math.max(p[index-1].x+0.02,Math.min(p[index+1].x-0.02,fromX(x)))
    edited(p)
  }
  Keys.onPressed: event => {
    if (event.key===Qt.Key_Delete || event.key===Qt.Key_Backspace) remove(selected)
    else if (event.key===Qt.Key_Left || event.key===Qt.Key_Right) selected=Math.max(0,Math.min(points.length-1,selected+(event.key===Qt.Key_Right?1:-1)))
    else if ((event.key===Qt.Key_Up || event.key===Qt.Key_Down) && selected>=0)
      move(selected,toX(points[selected].x),toY(points[selected].y)+(event.key===Qt.Key_Up?-3:3))
    else return
    event.accepted=true
  }
  Rectangle {
    anchors.fill: parent; radius: design.radius; color: design.tint(0.07)
    border.width: editor.activeFocus ? 1 : 0; border.color: design.accent
  }
  Text {
    x:18; y:15; text:editor.title; color:design.foreground
    font.family:design.family; font.pixelSize:15*design.textScale; font.weight:Font.Medium
  }
  Rectangle { x:18; y:42; width:parent.width-36; height:1; color:design.separator }
  Canvas {
    id: plot; anchors.fill: parent
    onPaint: {
      const c=getContext("2d"); c.reset()
      c.font=(11*design.textScale)+"px "+design.family; c.fillStyle=design.muted
      const ticks=[editor.minimum]
      for(let i=1;i<=4;i++)if(editor.maximum*i/4>editor.minimum)ticks.push(editor.maximum*i/4)
      for (const value of ticks) {
        const y=editor.toY(value)
        c.strokeStyle=design.separator; c.lineWidth=1; c.beginPath(); c.moveTo(editor.plotLeft,y); c.lineTo(editor.toX(1),y); c.stroke()
        c.fillText(editor.formatValue(value),8,y+4)
      }
      c.fillText(editor.leftLabel,editor.plotLeft,editor.height-46)
      c.textAlign="right"; c.fillText(editor.rightLabel,editor.toX(1),editor.height-46); c.textAlign="left"
      c.strokeStyle=design.accent; c.lineWidth=2; c.beginPath()
      for(let i=0;i<=120;i++) {
        const x=editor.toX(i/120), y=editor.toY(editor.evaluate(editor.points,i/120,editor.minimum,editor.maximum))
        if(i===0)c.moveTo(x,y); else c.lineTo(x,y)
      }
      c.stroke()
      for(let i=0;i<editor.points.length;i++) {
        const x=editor.toX(editor.points[i].x),y=editor.toY(editor.points[i].y)
        const active=i===editor.selected || i===editor.hovered
        if(active) { c.fillStyle=design.tint(i===editor.dragging ? 0.42 : 0.25); c.beginPath(); c.arc(x,y,17,0,2*Math.PI); c.fill() }
        c.fillStyle=editor.modified?design.accent:design.foreground
        c.strokeStyle=design.background; c.lineWidth=2; c.beginPath()
        if(i===0 || i===editor.points.length-1)c.rect(x-6,y-6,12,12)
        else c.arc(x,y,7,0,2*Math.PI)
        c.fill();c.stroke()
      }
    }
  }
  onPointsChanged: {
    if(selected>=points.length)selected=-1
    if(hovered>=points.length)hovered=-1
    plot.requestPaint()
  }
  onSelectedChanged: plot.requestPaint()
  onHoveredChanged: plot.requestPaint()
  onDraggingChanged: plot.requestPaint()
  onWidthChanged: plot.requestPaint()
  onHeightChanged: plot.requestPaint()
  onDesignChanged: plot.requestPaint()
  Connections { target: design; function onPaletteChanged() { plot.requestPaint() } }
  Text {
    x:18; y:parent.height-32; width:parent.width-36
    text:editor.hint; color:design.muted; font.family:design.family; font.pixelSize:11*design.textScale
    wrapMode:Text.WordWrap
  }
  MouseArea {
    anchors.fill:parent; hoverEnabled:true; preventStealing:true
    acceptedButtons:Qt.LeftButton|Qt.RightButton
    cursorShape:editor.hovered>=0?Qt.PointingHandCursor:Qt.CrossCursor
    onPressed: mouse => {
      editor.forceActiveFocus()
      let index=editor.pointAt(mouse.x,mouse.y)
      if(mouse.button===Qt.RightButton) { editor.remove(index); return }
      if(index<0) {
        if(mouse.y<editor.plotTop-18 || mouse.y>editor.toY(editor.minimum)+18)return
        const x=editor.fromX(mouse.x)
        if(x<=0.02 || x>=0.98)return
        const near=editor.points.findIndex(p=>Math.abs(p.x-x)<0.02)
        if(near>=0) index=near
        else {
          const p=editor.points.map(v=>({x:v.x,y:v.y}))
          let y=editor.fromY(mouse.y)
          if(editor.descending) { const right=p.findIndex(v=>v.x>x); y=Math.min(p[right-1].y,Math.max(p[right].y,y)) }
          p.push({x:x,y:y});p.sort((a,b)=>a.x-b.x);editor.edited(p);index=p.findIndex(v=>v.x===x)
        }
      }
      editor.selected=index;editor.dragging=index
    }
    onPositionChanged: mouse => {
      if(pressed && editor.dragging>=0)editor.move(editor.dragging,mouse.x,mouse.y)
      else editor.hovered=editor.pointAt(mouse.x,mouse.y)
    }
    onReleased:editor.dragging=-1
    onCanceled:editor.dragging=-1
    onExited:editor.hovered=-1
    onDoubleClicked: mouse => editor.remove(editor.pointAt(mouse.x,mouse.y))
  }
}
