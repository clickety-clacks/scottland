import QtQuick

FocusScope {
  id: play
  property Item viewport:null
  property real scrollOffset:0
  required property var design
  required property var values
  signal changed(string name, real value)
  HoverHandler { id:helpHover }
  ControlHint {
    control:play;design:play.design;title:"Motion playground"
    explanation:"Flick the sample or use arrows to see travel, 100 pt vertical stops and a side-rail widget morph; Ctrl + arrows resizes. Drag the velocity arrow to change the push strength."
    showing:helpHover.hovered || play.activeFocus
    viewport:play.viewport;scrollOffset:play.scrollOffset
  }
  implicitHeight: 380
  activeFocusOnTab: true
  property real px: width/2
  property real py: 192
  property real sampleWidth: 70
  property real sampleHeight: 44
  property real vx: 0
  property real vy: 0
  property real vw: 0
  property real vh: 0
  property real distance: 0
  property var trail: []
  property var edgeStops: []
  property bool widgetized: false
  property int widgetSide: 0
  property real widgetMorph: 0
  Behavior on widgetMorph { NumberAnimation { duration:180;easing.type:Easing.OutCubic } }
  readonly property real stoppingDistance: (vx || vy) ? Math.hypot(stopDistance(vx),stopDistance(vy)) : stopDistance(values.key_impulse)
  function stopDistance(speed) { return speed*speed/(2*Math.max(1,values.key_friction)) }
  readonly property real impulseLength: 28 + Math.sqrt(values.key_impulse/10000)*130
  function push(x,y,resize) {
    forceActiveFocus()
    if(!vx && !vy && !vw && !vh) { distance=0; trail=[{x:px,y:py}]; edgeStops=[];widgetized=false;widgetSide=0 }
    const cap=values.key_max_velocity
    if(resize) { vw=Math.max(-cap,Math.min(cap,vw+x*values.resize_impulse));vh=Math.max(-cap,Math.min(cap,vh+y*values.resize_impulse)) }
    else { vx=Math.max(-cap,Math.min(cap,vx+x*values.key_impulse));vy=Math.max(-cap,Math.min(cap,vy+y*values.key_impulse)) }
  }
  function axis(v,dt,deceleration) {
    if(v===0)return {v:0,d:0}
    const a=Math.max(1,deceleration),t=Math.min(dt,Math.abs(v)/a)
    return {v:Math.sign(v)*Math.max(0,Math.abs(v)-a*t),
      d:Math.sign(v)*(Math.abs(v)*t-a*t*t/2)}
  }
  Keys.onPressed: event => {
    const resize=(event.modifiers & Qt.ControlModifier)!==0
    if(event.key===Qt.Key_Left)push(-1,0,resize)
    else if(event.key===Qt.Key_Right)push(1,0,resize)
    else if(event.key===Qt.Key_Up)push(0,resize?1:-1,resize)
    else if(event.key===Qt.Key_Down)push(0,resize?-1:1,resize)
    else return
    event.accepted=true
  }
  Rectangle { anchors.fill:parent; radius:design.radius; color:design.tint(0.07); border.width:play.activeFocus?1:0; border.color:design.accent }
  Text { x:18;y:14;text:"Motion playground";color:design.foreground;font.family:design.family;font.pixelSize:15*design.textScale }
  Text { anchors.right:parent.right;anchors.rightMargin:18;y:16;text:Math.round(play.distance)+" pt travelled · "+Math.round(play.stoppingDistance)+(play.vx || play.vy ? " pt to stop" : " pt next push");color:design.accent;font.family:design.family;font.pixelSize:12*design.textScale }
  Rectangle { x:18;y:42;width:parent.width-36;height:1;color:design.separator }
  Canvas {
    id:trace;anchors.fill:parent
    onPaint: {
      const c=getContext("2d");c.reset();c.strokeStyle=design.separator;c.lineWidth=1
      c.strokeRect(18,56,width-36,height-126)
      c.strokeStyle=design.accent;c.lineWidth=2;c.beginPath()
      play.trail.forEach((p,i)=>{if(i)c.lineTo(p.x,p.y);else c.moveTo(p.x,p.y)});c.stroke()
      for(const stop of play.edgeStops) {c.beginPath();c.moveTo(stop.x-8,stop.y);c.lineTo(stop.x+8,stop.y);c.stroke()}
      if(play.trail.length && !play.vx && !play.vy && !play.widgetized) {
        c.setLineDash([3,3]);c.strokeRect(play.px-play.sampleWidth/2-4,play.py-play.sampleHeight/2-4,play.sampleWidth+8,play.sampleHeight+8);c.setLineDash([])
      }
      // The direct impulse handle is a velocity arrow; it controls the next push.
      if(impulseGrip.containsMouse || impulseGrip.pressed) {
        c.fillStyle=design.tint(impulseGrip.pressed ? .42 : .25);c.beginPath();c.arc(30+play.impulseLength,height-55,17,0,2*Math.PI);c.fill()
      }
      c.beginPath();c.moveTo(30,height-55);c.lineTo(30+play.impulseLength,height-55);c.lineTo(24+play.impulseLength,height-61);c.moveTo(30+play.impulseLength,height-55);c.lineTo(24+play.impulseLength,height-49);c.stroke()
      c.fillStyle=design.accent;c.beginPath();c.arc(30+play.impulseLength,height-55,6,0,2*Math.PI);c.fill()

    }
  }
  onValuesChanged:trace.requestPaint()
  onTrailChanged:trace.requestPaint()
  Connections { target:design;function onPaletteChanged(){trace.requestPaint()} }
  Rectangle {
    id:sample
    property real targetWidth: play.widgetized ? 42 : play.sampleWidth
    property real targetHeight: play.widgetized ? 32 : play.sampleHeight
    x:play.widgetized ? (play.widgetSide<0 ? 18 : play.width-18-width) : play.px-width/2
    y:play.py-height/2
    width:play.sampleWidth+(targetWidth-play.sampleWidth)*play.widgetMorph
    height:play.sampleHeight+(targetHeight-play.sampleHeight)*play.widgetMorph
    radius:6+8*play.widgetMorph
    color:design.tint(sampleGrab.pressed ? .42 : sampleGrab.containsMouse ? .26 : .19);border.color:design.accent
    Behavior on width { NumberAnimation { duration:180;easing.type:Easing.OutCubic } }
    Behavior on height { NumberAnimation { duration:180;easing.type:Easing.OutCubic } }
    Behavior on x { NumberAnimation { duration:180;easing.type:Easing.OutCubic } }
    Behavior on radius { NumberAnimation { duration:180;easing.type:Easing.OutCubic } }
    Rectangle { x:8;y:8;width:parent.width-16;height:2;color:design.foreground;opacity:.6*(1-play.widgetMorph) }
    Text { anchors.centerIn:parent;text:"▦";opacity:play.widgetMorph;color:design.foreground;font.pixelSize:18 }
    MouseArea {
      id:sampleGrab
      anchors.fill:parent;hoverEnabled:true;preventStealing:true;cursorShape:pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
      property point previous
      property double stamp
      property real releaseX:0
      property real releaseY:0
      onPressed: mouse=> {play.forceActiveFocus();play.widgetized=false;play.widgetSide=0;play.widgetMorph=0;play.edgeStops=[];play.vx=play.vy=play.vw=play.vh=0;previous=mapToItem(play,mouse.x,mouse.y);stamp=Date.now();releaseX=releaseY=0;play.distance=0;play.trail=[]}
      onPositionChanged: mouse=> {
        if(!pressed)return
        const p=mapToItem(play,mouse.x,mouse.y),now=Date.now(),dt=Math.max(.001,(now-stamp)/1000)
        releaseX=(p.x-previous.x)/dt;releaseY=(p.y-previous.y)/dt
        play.px=Math.max(18+width/2,Math.min(play.width-18-width/2,play.px+p.x-previous.x))
        play.py=Math.max(56+height/2,Math.min(play.height-70-height/2,play.py+p.y-previous.y))
        previous=p;stamp=now
      }
      onReleased: {
        if(Date.now()-stamp<50) {const cap=play.values.key_max_velocity;play.vx=Math.max(-cap,Math.min(cap,releaseX));play.vy=Math.max(-cap,Math.min(cap,releaseY))}
      }
    }
  }
  MouseArea {
    id:impulseGrip
    cursorShape:Qt.SizeHorCursor
    onContainsMouseChanged:trace.requestPaint()
    onPressedChanged:trace.requestPaint()
    x:18;y:parent.height-78;width:200;height:38;preventStealing:true;hoverEnabled:true
    function apply(x){play.changed("key_impulse",Math.round(Math.max(1,Math.min(10000,Math.pow(Math.max(0,x-40)/130,2)*10000))))}
    onPressed:mouse=>apply(mouse.x)
    onPositionChanged:mouse=>{if(pressed)apply(mouse.x)}
  }
  Text { x:18;y:parent.height-29;text:"Push · "+Math.round(values.key_impulse)+" pt/s";color:design.muted;font.family:design.family;font.pixelSize:12*design.textScale }
  Timer {
    interval:16;repeat:true;running:play.vx!==0||play.vy!==0||play.vw!==0||play.vh!==0
    property double last:0
    onRunningChanged:last=Date.now()
    onTriggered: {
      const now=Date.now(),dt=Math.min(.05,(now-last)/1000);last=now
      const x=play.axis(play.vx,dt,play.values.key_friction),y=play.axis(play.vy,dt,play.values.key_friction)
      const w=play.axis(play.vw,dt,play.values.resize_friction),h=play.axis(play.vh,dt,play.values.resize_friction)
      play.vx=x.v;play.vy=y.v;play.vw=w.v;play.vh=h.v
      play.sampleWidth=Math.max(30,Math.min(play.width-36,play.sampleWidth+w.d))
      play.sampleHeight=Math.max(24,Math.min(play.height-126,play.sampleHeight+h.d))
      if(play.sampleWidth===30||play.sampleWidth===play.width-36)play.vw=0
      if(play.sampleHeight===24||play.sampleHeight===play.height-126)play.vh=0
      play.px+=x.d;play.py+=y.d;play.distance+=Math.hypot(x.d,y.d)
      const lx=18+play.sampleWidth/2,hx=play.width-18-play.sampleWidth/2,ly=56+play.sampleHeight/2,hy=play.height-70-play.sampleHeight/2
      const side=(play.px<=lx&&play.vx<0)?-1:(play.px>=hx&&play.vx>0)?1:0
      if(side) {
        play.px=side<0?lx:hx;play.widgetSide=side;play.widgetized=true
        play.widgetMorph=1;play.vx=play.vy=play.vw=play.vh=0
      }
      if((play.py<=ly&&play.vy<0)||(play.py>=hy&&play.vy>0)) {
        play.py=Math.max(ly,Math.min(hy,play.py));play.vy=0
        play.edgeStops=play.edgeStops.concat([{x:play.px,y:play.py}])
      }
      play.px=Math.max(lx,Math.min(hx,play.px));play.py=Math.max(ly,Math.min(hy,play.py))
      play.trail=play.trail.concat([{x:play.px,y:play.py}]).slice(-1000)
    }
  }
}
