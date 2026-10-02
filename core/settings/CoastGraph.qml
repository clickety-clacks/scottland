import QtQuick

// A single impulse's position over time. The endpoint is the moment it stops.
FocusScope {
  id: graph
  required property var design
  required property string title
  required property string explanation
  required property real impulse
  required property real friction
  property real openingImpulse:335
  property real openingFriction:608
  property Item viewport:null
  property real scrollOffset:0
  signal edited(real impulse, real friction)
  readonly property real duration:impulse/Math.max(1,friction)
  readonly property real distance:impulse*impulse/(2*Math.max(1,friction))
  readonly property bool modified:Math.abs(impulse-openingImpulse)>0.01 || Math.abs(friction-openingFriction)>0.01
  readonly property real maxDuration:2.5
  readonly property real maxDistance:600
  readonly property real plotLeft:56
  readonly property real plotTop:62
  readonly property real plotRight:width-30
  readonly property real plotBottom:height-58
  readonly property real plotWidth:Math.max(1,plotRight-plotLeft)
  readonly property real plotHeight:Math.max(1,plotBottom-plotTop)
  readonly property real endpointX:plotLeft+Math.min(1,duration/maxDuration)*plotWidth
  readonly property real endpointY:plotBottom-Math.min(1,distance/maxDistance)*plotHeight
  implicitHeight:340
  activeFocusOnTab:true
  HoverHandler { id:helpHover }
  ControlHint {
    control:graph;design:graph.design;title:graph.title;explanation:graph.explanation
    showing:helpHover.hovered || graph.activeFocus
    viewport:graph.viewport;scrollOffset:graph.scrollOffset
  }
  function setFromEndpoint(x,y) {
    const seconds=Math.max(0.08,Math.min(maxDuration,(x-plotLeft)/plotWidth*maxDuration))
    const points=Math.max(4,Math.min(maxDistance,(plotBottom-y)/plotHeight*maxDistance))
    // Position is v*t-a*t²/2, and velocity reaches zero at the endpoint.
    edited(Math.max(1,Math.min(10000,2*points/seconds)),
           Math.max(1,Math.min(20000,2*points/(seconds*seconds))))
  }
  Keys.onPressed:event=>{
    if(event.key===Qt.Key_Left || event.key===Qt.Key_Right)
      setFromEndpoint(endpointX+(event.key===Qt.Key_Right?8:-8),endpointY)
    else if(event.key===Qt.Key_Up || event.key===Qt.Key_Down)
      setFromEndpoint(endpointX,endpointY+(event.key===Qt.Key_Up?-8:8))
    else return
    event.accepted=true
  }
  Rectangle {
    anchors.fill:parent;radius:design.radius;color:design.tint(0.07)
    border.width:graph.activeFocus?1:0;border.color:design.accent
  }
  Text {
    x:18;y:15;text:graph.title;color:design.foreground
    font.family:design.family;font.pixelSize:15*design.textScale;font.weight:Font.Medium
  }
  Rectangle { x:18;y:42;width:parent.width-36;height:1;color:design.separator }
  Canvas {
    id:plot;anchors.fill:parent
    onPaint:{
      const c=getContext("2d");c.reset()
      c.strokeStyle=design.separator;c.lineWidth=1
      for(let i=0;i<=4;i++){
        const x=graph.plotLeft+i*graph.plotWidth/4
        const y=graph.plotBottom-i*graph.plotHeight/4
        c.beginPath();c.moveTo(x,graph.plotTop);c.lineTo(x,graph.plotBottom);c.stroke()
        c.beginPath();c.moveTo(graph.plotLeft,y);c.lineTo(graph.plotRight,y);c.stroke()
      }
      c.fillStyle=design.muted;c.font=(11*design.textScale)+"px "+design.family
      c.fillText("position (pt)",8,graph.plotTop-7)
      c.textAlign="right";c.fillText("time (s) →",graph.plotRight,graph.plotBottom+20);c.textAlign="left"
      c.strokeStyle=design.accent;c.lineWidth=2.5;c.beginPath()
      const visible=Math.min(graph.duration,graph.maxDuration)
      for(let i=0;i<=64;i++){
        const t=visible*i/64
        const p=Math.max(0,graph.impulse*t-graph.friction*t*t/2)
        const x=graph.plotLeft+t/graph.maxDuration*graph.plotWidth
        const y=graph.plotBottom-Math.min(1,p/graph.maxDistance)*graph.plotHeight
        if(i===0)c.moveTo(x,y);else c.lineTo(x,y)
      }
      c.stroke()
      if(grip.containsMouse || grip.pressed){
        c.fillStyle=design.tint(grip.pressed?0.42:0.25)
        c.beginPath();c.arc(graph.endpointX,graph.endpointY,18,0,2*Math.PI);c.fill()
      }
      c.fillStyle=graph.modified?design.accent:design.foreground
      c.strokeStyle=design.background;c.lineWidth=2;c.beginPath()
      c.arc(graph.endpointX,graph.endpointY,7,0,2*Math.PI);c.fill();c.stroke()
    }
  }
  onImpulseChanged:plot.requestPaint()
  onFrictionChanged:plot.requestPaint()
  onWidthChanged:plot.requestPaint()
  onHeightChanged:plot.requestPaint()
  Connections { target:design;function onPaletteChanged(){plot.requestPaint()} }
  MouseArea {
    id:grip;anchors.fill:parent;hoverEnabled:true;preventStealing:true
    cursorShape:nearEndpoint?Qt.OpenHandCursor:Qt.ArrowCursor
    property bool nearEndpoint:false
    onPositionChanged:mouse=>{
      nearEndpoint=Math.hypot(mouse.x-graph.endpointX,mouse.y-graph.endpointY)<=22
      if(pressed)graph.setFromEndpoint(mouse.x,mouse.y)
      plot.requestPaint()
    }
    onPressed:mouse=>{graph.forceActiveFocus();graph.setFromEndpoint(mouse.x,mouse.y)}
    onReleased:plot.requestPaint()
    onExited:{nearEndpoint=false;plot.requestPaint()}
  }
  Text {
    x:18;y:parent.height-30;width:parent.width-36
    text:Math.round(graph.distance)+" pt · "+graph.duration.toFixed(2)+" s     "+Math.round(graph.impulse)+" pt/s · "+Math.round(graph.friction)+" pt/s²"
    color:design.muted;font.family:design.family;font.pixelSize:11*design.textScale
  }
}
