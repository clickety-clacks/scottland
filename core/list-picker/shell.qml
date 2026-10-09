import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland

ShellRoot {
  id: root

  property var entries: []
  property string filterText: ""
  property int selectedIndex: 0
  property var palette: ({})
  property bool ready: false

  readonly property string promptText: Quickshell.env("SCOTTLAND_LIST_PICKER_PROMPT") || ""
  readonly property string focusedOutputName: Quickshell.env("SCOTTLAND_LIST_PICKER_OUTPUT") || ""
  readonly property string palettePath: Quickshell.env("SCOTTLAND_PALETTE") ||
    ((Quickshell.env("XDG_RUNTIME_DIR") || "") + "/scottland/" +
      (Quickshell.env("WAYLAND_DISPLAY") || "wayland") + ".palette.json")
  readonly property var filteredEntries: {
    const needle = filterText.toLowerCase()
    return entries.map((entry, sourceIndex) => ({ entry: entry, sourceIndex: sourceIndex }))
      .filter(row => !needle || row.entry.toLowerCase().includes(needle))
  }
  readonly property var targetScreen: {
    if (root.focusedOutputName.length > 0) {
      for (let index = 0; index < Quickshell.screens.length; ++index) {
        const screen = Quickshell.screens[index]
        if (screen.name === root.focusedOutputName) return screen
      }
      return null
    }

    const active = ToplevelManager.activeToplevel
    if (active && active.screens && active.screens.length > 0) return active.screens[0]
    return Quickshell.screens.length === 1 ? Quickshell.screens[0] : null
  }
  readonly property color background: palette.background || "#1c1d22"
  readonly property color foreground: palette.foreground || "#e6e6e9"
  readonly property color muted: palette.muted || Qt.rgba(foreground.r, foreground.g, foreground.b, 0.68)
  readonly property color accent: palette.accent || "#7aa2f7"
  readonly property string fontFamily: palette.font_family || Qt.application.font.family
  readonly property real textScale: Math.max(0.5, Math.min(3, Number(palette.text_scale) || 1))
  readonly property int rowHeight: Math.round(44 * textScale)
  readonly property int listHeight: Math.max(rowHeight,
    Math.min(rowHeight * 8, filteredEntries.length * rowHeight))

  function moveSelection(amount) {
    if (filteredEntries.length === 0) return
    selectedIndex = Math.max(0, Math.min(filteredEntries.length - 1, selectedIndex + amount))
  }

  function chooseVisible(index) {
    if (!ready || index < 0 || index >= filteredEntries.length) return
    resultFile.setText(String(filteredEntries[index].sourceIndex))
    Qt.callLater(Qt.quit)
  }

  function cancel() {
    Qt.quit()
  }

  FileView {
    id: entriesFile
    path: Quickshell.env("SCOTTLAND_LIST_PICKER_INPUT") || ""
    blockLoading: true
    printErrors: false
  }

  FileView {
    id: resultFile
    path: Quickshell.env("SCOTTLAND_LIST_PICKER_RESULT") || ""
    blockWrites: true
    printErrors: false
  }

  FileView {
    path: root.palettePath
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      try { root.palette = JSON.parse(text()) } catch (error) { root.palette = ({}) }
    }
  }

  Component.onCompleted: {
    try {
      const values = JSON.parse(entriesFile.text())
      if (!Array.isArray(values) || values.some(value => typeof value !== "string"))
        throw new Error("input must be a JSON list of strings")
      root.entries = values
      root.ready = true
    } catch (error) {
      console.error("scottland-list-picker: could not read entries: " + error)
      Qt.quit()
    }
  }

  PanelWindow {
    id: pickerWindow
    screen: root.targetScreen
    visible: root.ready && root.targetScreen !== null
    anchors { top: true; bottom: true; left: true; right: true }
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.OnDemand
    WlrLayershell.namespace: "scottland-list-picker"

    MouseArea {
      anchors.fill: parent
      onClicked: {
        if (mouse.x < panel.x || mouse.y < panel.y ||
            mouse.x >= panel.x + panel.width || mouse.y >= panel.y + panel.height)
          root.cancel()
      }
    }

    Rectangle {
      id: panel
      anchors.centerIn: parent
      width: Math.max(260, Math.min(640, parent.width - 48))
      height: content.implicitHeight + 40
      radius: 14
      color: root.background
      border.width: 1
      border.color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.16)

      Column {
        id: content
        anchors.fill: parent
        anchors.margins: 20
        spacing: 12

        Text {
          visible: root.promptText.length > 0
          width: parent.width
          height: visible ? implicitHeight : 0
          text: root.promptText
          wrapMode: Text.WordWrap
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: 16 * root.textScale
          font.weight: Font.DemiBold
        }

        Rectangle {
          id: searchBox
          width: parent.width
          height: Math.round(44 * root.textScale)
          radius: 8
          color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.07)
          border.width: 1
          border.color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.18)

          Text {
            anchors.fill: search
            anchors.leftMargin: 14
            anchors.rightMargin: 14
            verticalAlignment: Text.AlignVCenter
            text: "Type to narrow"
            visible: search.text.length === 0
            color: root.muted
            font.family: root.fontFamily
            font.pixelSize: 15 * root.textScale
          }

          TextInput {
            id: search
            anchors.fill: parent
            anchors.leftMargin: 14
            anchors.rightMargin: 14
            verticalAlignment: TextInput.AlignVCenter
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: 15 * root.textScale
            selectByMouse: true
            focus: true
            onTextChanged: {
              root.filterText = text
              root.selectedIndex = 0
            }
            Keys.priority: Keys.BeforeItem
            Keys.onPressed: event => {
              if (event.key === Qt.Key_Down) {
                root.moveSelection(1)
                event.accepted = true
              } else if (event.key === Qt.Key_Up) {
                root.moveSelection(-1)
                event.accepted = true
              } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                root.chooseVisible(root.selectedIndex)
                event.accepted = true
              } else if (event.key === Qt.Key_Escape) {
                root.cancel()
                event.accepted = true
              }
            }
          }
        }

        Item {
          width: parent.width
          height: root.listHeight

          ListView {
            id: choices
            anchors.fill: parent
            clip: true
            model: root.filteredEntries
            currentIndex: root.selectedIndex
            boundsBehavior: Flickable.StopAtBounds

            delegate: Item {
              required property var modelData
              required property int index
              width: choices.width
              height: root.rowHeight

              Rectangle {
                anchors.fill: parent
                radius: 7
                color: index === root.selectedIndex
                  ? Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.28)
                  : "transparent"
              }

              Text {
                anchors.fill: parent
                anchors.leftMargin: 12
                anchors.rightMargin: 12
                verticalAlignment: Text.AlignVCenter
                text: modelData.entry
                elide: Text.ElideRight
                color: index === root.selectedIndex ? root.foreground : root.muted
                font.family: root.fontFamily
                font.pixelSize: 14 * root.textScale
              }

              MouseArea {
                anchors.fill: parent
                hoverEnabled: true
                onEntered: root.selectedIndex = index
                onClicked: root.chooseVisible(index)
              }
            }

            Text {
              anchors.centerIn: parent
              visible: root.filteredEntries.length === 0
              text: "No matches"
              color: root.muted
              font.family: root.fontFamily
              font.pixelSize: 14 * root.textScale
            }
          }
        }
      }
    }

    Component.onCompleted: Qt.callLater(() => search.forceActiveFocus())
  }
}
