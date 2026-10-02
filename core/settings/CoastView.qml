import QtQuick
import QtQuick.Controls

Flickable {
  id: view
  clip: true
  contentWidth: width
  boundsBehavior: Flickable.StopAtBounds
  flickableDirection: Flickable.VerticalFlick
  flickDeceleration: 1600
  maximumFlickVelocity: 2400
  property var design
  property real wheelVelocity: 0
  readonly property real availableHeight: height
  readonly property real availableWidth: width - 12
  function halt() { wheelVelocity = 0; cancelFlick() }
  function revealRow(stack, index) {
    halt()
    const y = stack.y + index * (stack.rowHeight + 1)
    const bottom = y + stack.rowHeight
    if (y < contentY) contentY = y
    else if (bottom > contentY + height) contentY = bottom - height
  }
  onDraggingChanged: if (dragging) wheelVelocity = 0
  // Pixel deltas from touchpads and angle deltas from wheels feed one physical coast.
  WheelHandler {
    target: null
    acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
    onWheel: event => {
      view.cancelFlick()
      const delta = event.pixelDelta.y || event.angleDelta.y / 3
      view.wheelVelocity = Math.max(-2400, Math.min(2400, view.wheelVelocity - delta * 7))
      event.accepted = true
    }
  }
  Timer {
    interval: 16; repeat: true; running: Math.abs(view.wheelVelocity) > 0
    property double last: 0
    onRunningChanged: last = Date.now()
    onTriggered: {
      const now = Date.now(), dt = Math.min(0.05, (now-last)/1000); last = now
      const speed = Math.abs(view.wheelVelocity), t = Math.min(dt, speed/1600)
      const dy = Math.sign(view.wheelVelocity) * (speed*t - 800*t*t)
      const next = Math.max(0, Math.min(Math.max(0, view.contentHeight-view.height), view.contentY+dy))
      view.wheelVelocity = next === view.contentY ? 0 : Math.sign(view.wheelVelocity)*Math.max(0, speed-1600*t)
      view.contentY = next
    }
  }
  ScrollBar.vertical: ScrollBar {
    policy:ScrollBar.AlwaysOn
    contentItem:Rectangle {
      implicitWidth:6; implicitHeight:40; radius:3
      color:view.design ? view.design.tint(parent.pressed ? .8 : parent.hovered ? .6 : .3) : "#7aa2f7"
    }
  }
}
