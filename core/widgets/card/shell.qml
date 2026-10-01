// Scottland's default widget ("card"): the app's icon, the window title, and an alert badge.
// Shown for any app with no widget configured. Inputs come from the widget launch contract:
//   SCOTTLAND_WIDGET_APP_ID, SCOTTLAND_WIDGET_TITLE, SCOTTLAND_WIDGET_ICON (resolved by Scottland
//   from the app's .desktop entry), SCOTTLAND_WIDGET_BADGE (alert count; empty or 0 = none).
// Colors follow the Omarchy theme when there is one, else a neutral dark palette.
import QtQuick
import Quickshell
import Quickshell.Io

FloatingWindow {
    id: root
    title: "Scottland widget: " + appTitle
    implicitWidth: 300
    implicitHeight: 96
    color: "transparent"

    readonly property string appId: Quickshell.env("SCOTTLAND_WIDGET_APP_ID") || ""
    readonly property string appTitle: Quickshell.env("SCOTTLAND_WIDGET_TITLE") || appId
    readonly property string iconName: Quickshell.env("SCOTTLAND_WIDGET_ICON") || appId
    readonly property int badge: parseInt(Quickshell.env("SCOTTLAND_WIDGET_BADGE") || "0") || 0

    // Theme: Omarchy's colors.toml if present.
    property color background: "#2e3440"
    property color foreground: "#d8dee9"
    property color muted: "#97a3ab"
    property color accent: "#81a1c1"
    property color alert: "#bf616a"

    FileView {
        path: Quickshell.env("HOME") + "/.local/state/omarchy/current/theme/colors.toml"
        onLoaded: {
            const text = this.text()
            const pick = name => {
                const m = text.match(new RegExp("^" + name + "\\s*=\\s*\"(#[0-9a-fA-F]{6})\"", "m"))
                return m ? m[1] : null
            }
            root.background = pick("lighter_background") || pick("background") || root.background
            root.foreground = pick("foreground") || root.foreground
            root.muted = pick("light_foreground") || root.muted
            root.accent = pick("accent") || root.accent
            root.alert = pick("red") || root.alert
        }
    }

    Rectangle {
        anchors.fill: parent
        radius: 16
        color: root.background

        Row {
            anchors.fill: parent
            anchors.margins: 16
            spacing: 14

            Item {
                width: 56; height: 56
                anchors.verticalCenter: parent.verticalCenter

                Image {
                    anchors.fill: parent
                    source: Quickshell.iconPath(root.iconName, "application-x-executable")
                    sourceSize: Qt.size(112, 112)
                    smooth: true
                    mipmap: true
                }

                // Alert badge, on the icon's top-right corner.
                Rectangle {
                    visible: root.badge > 0
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.rightMargin: -6
                    anchors.topMargin: -6
                    height: 22
                    width: Math.max(22, badgeText.implicitWidth + 12)
                    radius: 11
                    color: root.alert
                    border.width: 2
                    border.color: root.background

                    Text {
                        id: badgeText
                        anchors.centerIn: parent
                        text: root.badge > 99 ? "99+" : String(root.badge)
                        color: "white"
                        font.pixelSize: 12
                        font.bold: true
                    }
                }
            }

            Column {
                anchors.verticalCenter: parent.verticalCenter
                width: parent.width - 56 - parent.spacing
                spacing: 3

                Text {
                    width: parent.width
                    text: root.appTitle
                    color: root.foreground
                    font.pixelSize: 15
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                    maximumLineCount: 2
                    wrapMode: Text.Wrap
                }
            }
        }
    }
}
