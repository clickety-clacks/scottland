import QtQuick

FocusScope {
  id: play
  property Item viewport:null
  property real scrollOffset:0
  required property var design
  required property var values
  required property var movement
  required property var resizing
  required property var evaluate
  signal changed(string name, real value)
  HoverHandler { id:helpHover }
  ControlHint {
    control:play;design:play.design;title:"Motion playground"
    explanation:"Flick the sample or use arrows to see travel and edge bounces; Ctrl + arrows resizes. Drag the velocity arrow to change each push, or the bounce handle to change retained speed. The graph laws also drive real windows."
    showing:helpHover.hovered || play.activeFocus
    viewport:play.viewport;scrollOffset:play.scrollOffset
  }
  implicitHeight: 290
  activeFocusOnTab: true
  property real px: width/2
  property real py: 155
  property real sampleWidth: 70
  property real sampleHeight: 44
  property real vx: 0
  property real vy: 0
  property real vw: 0
  property real vh: 0
  property real distance: 0
  property var trail: []
  property var bounces: []
  readonly property real stoppingDistance: (vx || vy) ? Math.hypot(stopDistance(vx),stopDistance(vy)) : stopDistance(values.key_impulse)
  function stopDistance(speed) {
    // Integrate v/a(v) in speed space: bounded work even at very low friction.
    speed=Math.abs(speed)
    let distance=0
    for(let i=0;i<128;i++) {
      const v=speed*(i+.5)/128
      distance+=v/(values.key_friction*evaluate(movement,Math.min(1,v/values.key_max_velocity),.05,4))*speed/128
    }
    return distance
  }
  readonly property real impulseLength: 28 + Math.sqrt(values.key_impulse/10000)*130
  function push(x,y,resize) {
    forceActiveFocus()
    if(!vx && !vy && !vw && !vh) { distance=0; trail=[{x:px,y:py}]; bounces=[] }
    const cap=values.key_max_velocity
    if(resize) { vw=Math.max(-cap,Math.min(cap,vw+x*values.key_impulse));vh=Math.max(-cap,Math.min(cap,vh+y*values.key_impulse)) }
    else { vx=Math.max(-cap,Math.min(cap,vx+x*values.key_impulse));vy=Math.max(-cap,Math.min(cap,vy+y*values.key_impulse)) }
  }
  function axis(v,dt,points) {
    let d=0
    while(dt>0 && v!==0) {
      const a=values.key_friction*evaluate(points,Math.min(1,Math.abs(v)/values.key_max_velocity),0.05,4)
      const t=Math.min(dt,1/240,Math.abs(v)/a)
      d+=Math.sign(v)*(Math.abs(v)*t-a*t*t/2)
      v=Math.sign(v)*Math.max(0,Math.abs(v)-a*t);dt-=t
    }
    return {v:v,d:d}
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
      c.strokeRect(18,56,width-36,153)
      c.strokeStyle=design.accent;c.lineWidth=2;c.beginPath()
      play.trail.forEach((p,i)=>{if(i)c.lineTo(p.x,p.y);else c.moveTo(p.x,p.y)});c.stroke()
      for(const b of play.bounces) {c.beginPath();c.arc(b.x,b.y,9,0,2*Math.PI);c.stroke()}
      if(play.trail.length && !play.vx && !play.vy) {
        c.setLineDash([3,3]);c.strokeRect(play.px-play.sampleWidth/2-4,play.py-play.sampleHeight/2-4,play.sampleWidth+8,play.sampleHeight+8);c.setLineDash([])
      }
      // The direct impulse handle is a velocity arrow; it controls the next push.
      if(impulseGrip.containsMouse || impulseGrip.pressed) {
        c.fillStyle=design.tint(impulseGrip.pressed ? .42 : .25);c.beginPath();c.arc(30+play.impulseLength,238,17,0,2*Math.PI);c.fill()
      }
      c.beginPath();c.moveTo(30,238);c.lineTo(30+play.impulseLength,238);c.lineTo(24+play.impulseLength,232);c.moveTo(30+play.impulseLength,238);c.lineTo(24+play.impulseLength,244);c.stroke()
      c.fillStyle=design.accent;c.beginPath();c.arc(30+play.impulseLength,238,6,0,2*Math.PI);c.fill()
      // Bounce trace and retained-velocity handle.
      const bx=width-150, by=246-36*play.values.key_restitution
      c.beginPath();c.moveTo(bx-45,224);c.lineTo(bx,248);c.lineTo(bx+45,by);c.stroke()
      if(bounceGrip.containsMouse || bounceGrip.pressed) {
        c.fillStyle=design.tint(bounceGrip.pressed ? .42 : .25);c.beginPath();c.arc(bx+45,by,17,0,2*Math.PI);c.fill()
      }
      c.fillStyle=design.accent;c.beginPath();c.arc(bx+45,by,7,0,2*Math.PI);c.fill()
    }
  }
  onValuesChanged:trace.requestPaint()
  onTrailChanged:trace.requestPaint()
  Connections { target:design;function onPaletteChanged(){trace.requestPaint()} }
  Rectangle {
    id:sample
    x:play.px-width/2;y:play.py-height/2;width:play.sampleWidth;height:play.sampleHeight
    radius:6;color:design.tint(sampleGrab.pressed ? .42 : sampleGrab.containsMouse ? .26 : .19);border.color:design.accent
    Rectangle { x:8;y:8;width:parent.width-16;height:2;color:design.foreground;opacity:0.6 }
    MouseArea {
      id:sampleGrab
      anchors.fill:parent;hoverEnabled:true;preventStealing:true;cursorShape:pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
      property point previous
      property double stamp
      property real releaseX:0
      property real releaseY:0
      onPressed: mouse=> {play.forceActiveFocus();play.vx=play.vy=play.vw=play.vh=0;previous=mapToItem(play,mouse.x,mouse.y);stamp=Date.now();releaseX=releaseY=0;play.distance=0;play.trail=[];play.bounces=[]}
      onPositionChanged: mouse=> {
        if(!pressed)return
        const p=mapToItem(play,mouse.x,mouse.y),now=Date.now(),dt=Math.max(.001,(now-stamp)/1000)
        releaseX=(p.x-previous.x)/dt;releaseY=(p.y-previous.y)/dt
        play.px=Math.max(18+width/2,Math.min(play.width-18-width/2,play.px+p.x-previous.x))
        play.py=Math.max(56+height/2,Math.min(209-height/2,play.py+p.y-previous.y))
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
    x:18;y:215;width:200;height:38;preventStealing:true;hoverEnabled:true
    function apply(x){play.changed("key_impulse",Math.round(Math.max(1,Math.min(10000,Math.pow(Math.max(0,x-40)/130,2)*10000))))}
    onPressed:mouse=>apply(mouse.x)
    onPositionChanged:mouse=>{if(pressed)apply(mouse.x)}
  }
  MouseArea {
    id:bounceGrip
    cursorShape:Qt.SizeVerCursor
    onContainsMouseChanged:trace.requestPaint()
    onPressedChanged:trace.requestPaint()
    x:parent.width-205;y:214;width:170;height:40;preventStealing:true;hoverEnabled:true
    function apply(y){play.changed("key_restitution",Math.max(0,Math.min(1,(34-y)/36)))}
    onPressed:mouse=>apply(mouse.y)
    onPositionChanged:mouse=>{if(pressed)apply(mouse.y)}
  }
  Text { x:18;y:260;text:"Push · "+Math.round(values.key_impulse)+" pt/s";color:design.muted;font.family:design.family;font.pixelSize:12*design.textScale }
  Text { anchors.right:parent.right;anchors.rightMargin:18;y:260;text:"Bounce · "+Math.round(values.key_restitution*100)+"%";color:design.muted;font.family:design.family;font.pixelSize:12*design.textScale }
  Timer {
    interval:16;repeat:true;running:play.vx!==0||play.vy!==0||play.vw!==0||play.vh!==0
    property double last:0
    onRunningChanged:last=Date.now()
    onTriggered: {
      const now=Date.now(),dt=Math.min(.05,(now-last)/1000);last=now
      const x=play.axis(play.vx,dt,play.movement),y=play.axis(play.vy,dt,play.movement)
      const w=play.axis(play.vw,dt,play.resizing),h=play.axis(play.vh,dt,play.resizing)
      play.vx=x.v;play.vy=y.v;play.vw=w.v;play.vh=h.v
      play.sampleWidth=Math.max(30,Math.min(play.width-36,play.sampleWidth+w.d))
      play.sampleHeight=Math.max(24,Math.min(153,play.sampleHeight+h.d))
      if(play.sampleWidth===30||play.sampleWidth===play.width-36)play.vw=0
      if(play.sampleHeight===24||play.sampleHeight===153)play.vh=0
      play.px+=x.d;play.py+=y.d;play.distance+=Math.hypot(x.d,y.d)
      const lx=18+play.sampleWidth/2,hx=play.width-18-play.sampleWidth/2,ly=56+play.sampleHeight/2,hy=209-play.sampleHeight/2
      if((play.px<=lx&&play.vx<0)||(play.px>=hx&&play.vx>0)) {play.vx*=-play.values.key_restitution;play.bounces=play.bounces.concat([{x:Math.max(lx,Math.min(hx,play.px)),y:play.py}])}
      if((play.py<=ly&&play.vy<0)||(play.py>=hy&&play.vy>0)) {play.vy*=-play.values.key_restitution;play.bounces=play.bounces.concat([{x:play.px,y:Math.max(ly,Math.min(hy,play.py))}])}
      play.px=Math.max(lx,Math.min(hx,play.px));play.py=Math.max(ly,Math.min(hy,play.py))
      play.trail=play.trail.concat([{x:play.px,y:play.py}]).slice(-1000)
    }
  }
}
