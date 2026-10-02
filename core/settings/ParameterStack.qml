import QtQuick

// A stack of numeric settings, one row each: the whole row is the slider (click or drag anywhere
// sets it), the label on the left, the value large on the right, rows joined in one rounded block
// with thin separators. Keyboard: Left/Right adjust (Shift for a larger step), Up/Down or Tab move
// between rows, digits type a value, Backspace erases typed input or (when empty) resets the row,
// double-click resets a row to the value it had when the panel opened. Escape and Return are left
// to the panel (cancel and save). After the shared parameter-slider design (widgets skill).
//
// rows: [{ id, label, min, max, step, largeStep, decimals, suffix, hint, display(value) }]
// values: { id: value }, kept by the owner; changes come back through changed(id, value).
// opening: { id: value } when the panel opened, for reset and the modified color.
FocusScope {
  id: stack

  property var rows: []
  property var values: ({})
  property var opening: ({})
  property color foreground: "#e6e6e9"
  property color accent: "#7aa2f7"
  property color modified: accent
  property int rowHeight: 58
  property int selected: 0
  property string typed: ""
  property int hovered: -1
  readonly property int hinted: hovered >= 0 ? hovered : activeFocus ? selected : -1
  signal changed(string id, real value)

  implicitHeight: rows.length * rowHeight + Math.max(0, rows.length - 1)
  activeFocusOnTab: true

  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)) }
  function valueOf(row) { return Number(values[row.id]) }
  function set(index, value) {
    const row = rows[index]
    if (!row) return
    const step = Number(row.step || 0)
    let v = clamp(Number(value), Number(row.min), Number(row.max))
    if (step > 0 && typed === "") v = Math.round(v / step) * step
    stack.changed(row.id, v)
  }
  function adjust(index, direction, large) {
    const row = rows[index]
    if (!row) return
    typed = ""
    const step = Number(large ? (row.largeStep || Number(row.step || 1) * 10) : (row.step || 1))
    set(index, valueOf(row) + direction * step)
  }
  function reset(index) {
    const row = rows[index]
    if (!row || opening[row.id] === undefined) return
    typed = ""
    set(index, opening[row.id])
  }
  function shown(row, index) {
    if (index === selected && typed !== "" && activeFocus) return typed + (row.suffix || "")
    return row.display ? row.display(valueOf(row)) : valueOf(row).toFixed(row.decimals || 0) + (row.suffix || "")
  }
  function isModified(row) {
    return opening[row.id] !== undefined && Math.abs(valueOf(row) - Number(opening[row.id])) > 0.0001
  }

  Keys.onPressed: event => {
    const shift = (event.modifiers & Qt.ShiftModifier) !== 0
    if (event.key === Qt.Key_Left) adjust(selected, -1, shift)
    else if (event.key === Qt.Key_Right) adjust(selected, 1, shift)
    else if (event.key === Qt.Key_Up || event.key === Qt.Key_Backtab) { typed = ""; selected = clamp(selected - 1, 0, rows.length - 1) }
    else if (event.key === Qt.Key_Down || event.key === Qt.Key_Tab) { typed = ""; selected = clamp(selected + 1, 0, rows.length - 1) }
    else if (event.key === Qt.Key_Backspace || event.key === Qt.Key_Delete) {
      if (typed === "") reset(selected)
      else {
        typed = typed.slice(0, -1)
        if (typed !== "" && isFinite(Number(typed))) set(selected, Number(typed))
      }
    } else if (/^[0-9.]$/.test(event.text) && !(event.text === "." && typed.includes("."))) {
      typed += event.text
      if (isFinite(Number(typed))) set(selected, Number(typed))
    } else return  // Escape, Return and the rest go to the panel
    // The most recent input decides which row explains itself. A stationary pointer
    // must not hide the hint for a newly keyboard-selected row.
    hovered = -1
    event.accepted = true
  }
  onActiveFocusChanged: if (!activeFocus) typed = ""

  Rectangle {
    id: block
    anchors.fill: parent
    radius: 12
    color: "transparent"
    clip: true

    Column {
      anchors.fill: parent

      Repeater {
        model: stack.rows

        Item {
          id: rowItem
          required property var modelData
          required property int index
          readonly property real fraction: stack.clamp((stack.valueOf(modelData) - modelData.min)
            / Math.max(1e-9, modelData.max - modelData.min), 0, 1)
          width: parent.width
          height: stack.rowHeight + (index < stack.rows.length - 1 ? 1 : 0)

          Rectangle {
            id: track
            width: parent.width; height: stack.rowHeight
            color: Qt.rgba(stack.accent.r, stack.accent.g, stack.accent.b, 0.16)

            Rectangle {
              width: parent.width * rowItem.fraction; height: parent.height
              color: Qt.rgba(stack.accent.r, stack.accent.g, stack.accent.b,
                rowItem.index === stack.selected && stack.activeFocus ? 0.85 : 0.6)
            }
          }

          Rectangle {
            visible: rowItem.index < stack.rows.length - 1
            anchors.bottom: parent.bottom
            width: parent.width; height: 1
            color: Qt.rgba(stack.foreground.r, stack.foreground.g, stack.foreground.b, 0.14)
          }

          Text {
            id: labelText
            anchors.left: parent.left; anchors.leftMargin: 18
            anchors.baseline: valueText.baseline
            text: rowItem.modelData.label
            color: stack.foreground
            font.pixelSize: 15
            font.weight: Font.Medium
          }

          Text {
            id: valueText
            anchors.right: parent.right; anchors.rightMargin: 18
            y: rowItem.index === stack.hinted ? 3 : (stack.rowHeight - height) / 2
            text: stack.shown(rowItem.modelData, rowItem.index)
            color: stack.isModified(rowItem.modelData) ? stack.modified : stack.foreground
            font.pixelSize: 21
            font.weight: Font.DemiBold
            font.features: { "tnum": 1 }
          }

          Text {
            anchors { left: parent.left; right: parent.right; bottom: track.bottom
              leftMargin: 18; rightMargin: 18; bottomMargin: 4 }
            visible: rowItem.index === stack.hinted
            text: rowItem.modelData.hint || ""
            color: stack.foreground
            opacity: 0.85
            font.pixelSize: 11
            wrapMode: Text.WordWrap
            maximumLineCount: 2
          }

          MouseArea {
            hoverEnabled: true
            onEntered: stack.hovered = rowItem.index
            onExited: if (stack.hovered === rowItem.index) stack.hovered = -1
            width: parent.width; height: stack.rowHeight
            preventStealing: true
            cursorShape: Qt.SizeHorCursor
            function apply(x) {
              stack.set(rowItem.index, rowItem.modelData.min
                + stack.clamp(x / width, 0, 1) * (rowItem.modelData.max - rowItem.modelData.min))
            }
            onPressed: mouse => {
              stack.forceActiveFocus()
              stack.typed = ""
              stack.selected = rowItem.index
              apply(mouse.x)
            }
            onPositionChanged: mouse => {
              if (pressed) apply(mouse.x)
              else stack.hovered = rowItem.index
            }
            onDoubleClicked: stack.reset(rowItem.index)
          }
        }
      }
    }
  }
}
