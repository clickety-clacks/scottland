// Scottland's default card renders one complete presentation snapshot from its state file.
// The launch environment carries identity and paths only (docs/widgets.md, WG8).
import QtQuick
import Quickshell
import Quickshell.Io

FloatingWindow {
    id: root
    title: "Scottland widget: " + appTitle
    // As wide as its text needs, up to a maximum; square around the icon when there's no text.
    readonly property int pad: 16
    readonly property int iconSize: 56
    readonly property int gap: 14
    readonly property int maxWidth: 320
    readonly property bool hasText: appName !== "" || appTitle !== ""
    property var state: ({})
    readonly property bool minimized: state.minimized === true
    readonly property bool showsText: hasText && !minimized
    // 0 is the square around the icon, 1 the card with its text. (Expanding and collapsing will be
    // animated by Scottland itself, from a snapshot, as the window/widget morph is; a card resizing
    // its own window every frame is choppy and can stop short.)
    readonly property real expansion: showsText ? 1 : 0
    // The icon's inset from the screen edge: centered in the square, the card's padding when open.
    readonly property real rowPad: (implicitHeight - iconSize) / 2 * (1 - expansion) + pad * expansion
    // Measured from the strings, not the Text items: a card that starts collapsed has never shown
    // its text, and a Text that has never been visible isn't laid out (its width reads 0).
    readonly property bool showsName: appName !== "" && appName !== appTitle
    readonly property int textWidth: Math.min(maxWidth - 2 * pad - iconSize - gap,
        Math.ceil(Math.max(appTitle !== "" ? titleMetrics.advanceWidth : 0, showsName ? nameMetrics.advanceWidth : 0)))
    readonly property int openWidth: hasText ? 2 * pad + iconSize + gap + textWidth : implicitHeight
    implicitWidth: Math.round(implicitHeight + (openWidth - implicitHeight) * expansion)
    implicitHeight: 96
    // An open window keeps its size when the implicit size changes: pin it, so the card follows
    // its text, the title and Super+M (and so does the compositor, through the size limits).
    minimumSize: Qt.size(implicitWidth, implicitHeight)
    maximumSize: Qt.size(implicitWidth, implicitHeight)
    color: "transparent"
    visible: typeof state.revision === "number"

    readonly property string appId: state.app_id || ""
    readonly property string appTitle: state.title || ""
    // The app's name (its .desktop entry's), else its app-id.
    readonly property string appName: state.name || appId
    // Which screen edge the widget is on ("left" or "right"), live.
    readonly property string rail: state.rail || "right"
    readonly property string iconName: state.icon || appId
    // The app's icon from the theme, else a generic app icon, else none (a monogram is drawn).
    readonly property string iconSource: Quickshell.iconPath(iconName, true)
        || Quickshell.iconPath(appId.toLowerCase(), true)
        || Quickshell.iconPath("application-x-executable", true)
    readonly property int badge: state.badge || 0

    TextMetrics { id: titleMetrics; text: root.appTitle; font: titleText.font }
    TextMetrics { id: nameMetrics; text: root.appName; font: nameText.font }

    // Live state from the widget service.
    FileView {
        path: Quickshell.env("SCOTTLAND_WIDGET_STATE") || ""
        watchChanges: true
        onFileChanged: reload()
        onLoaded: {
            try {
                root.state = JSON.parse(this.text())
                Qt.callLater(root.reportRendered)
            } catch (e) {}
        }
    }

    property bool reportPending: false
    function reportRendered() {
        if (renderReport.running) { reportPending = true; return }
        reportPending = false
        if (Quickshell.env("SCOTTLAND_WIDGET_AUDIT") !== "1") return
        renderReport.command = ["busctl", "--user", "call", "org.scottland.Widgets",
            "/org/scottland/Widgets", "org.scottland.Diagnostics", "Rendered", "s",
            JSON.stringify({id: state.id, revision: state.revision, version: state.version,
                title: titleText.text, title_shown: titleText.visible && titleText.parent.visible,
                collapsed: minimized, rail: rail, width: width})]
        renderReport.running = true
    }
    Process {
        id: renderReport
        onExited: if (root.reportPending) Qt.callLater(root.reportRendered)
    }
    onWidthChanged: Qt.callLater(root.reportRendered)

    Process {
        id: openWindow
        command: ["busctl", "--user", "call", "org.scottland.Widgets",
            "/org/scottland/widget/" + (Quickshell.env("SCOTTLAND_WIDGET_ID") || ""),
            "org.scottland.Widget", "Open"]
    }

    // Theme: the session's palette (SCOTTLAND_PALETTE, kept current by Scottland: light or dark,
    // the accent, and an integration's full palette such as Omarchy's theme). Followed live.
    property color background: "#2e3440"
    property color foreground: "#d8dee9"
    property color muted: "#97a3ab"
    property color accent: "#81a1c1"
    property color alert: "#bf616a"

    FileView {
        path: Quickshell.env("SCOTTLAND_PALETTE") || ""
        watchChanges: true
        onFileChanged: reload()
        onLoaded: {
            try {
                const p = JSON.parse(this.text())
                const valid = c => typeof c === "string" && /^#[0-9a-fA-F]{6}$/.test(c)
                if (valid(p.background)) root.background = p.background
                if (valid(p.foreground)) root.foreground = p.foreground
                if (valid(p.muted)) root.muted = p.muted
                if (valid(p.accent)) root.accent = p.accent
                if (valid(p.alert)) root.alert = p.alert
            } catch (e) {}
        }
    }

    Item {
        anchors.fill: parent

        // Keep the badge's overhang inside the client surface, including at the top of a rail.
        // Reserve it even with no count, so updates never move the card or its contents.
        Rectangle {
            anchors.fill: parent
            anchors.topMargin: 6
            anchors.leftMargin: root.rail === "right" ? 6 : 0
            anchors.rightMargin: root.rail === "left" ? 6 : 0
            radius: 16
            color: root.background
        }

        // A click opens the app's window in the middle of the screen (WG17).
        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: openWindow.running = true
        }

        // The icon sits on the screen-edge side: left of the text on the left rail, right of it on
        // the right rail (RightToLeft lays the row out mirrored), following the rail live.
        // Hugs the screen-edge side, with the card's padding, whatever the card's width.
        Row {
            anchors.verticalCenter: parent.verticalCenter
            x: root.rail === "right" ? parent.width - width - root.rowPad : root.rowPad
            spacing: root.gap
            layoutDirection: root.rail === "right" ? Qt.RightToLeft : Qt.LeftToRight

            Item {
                width: root.iconSize; height: root.iconSize
                anchors.verticalCenter: parent.verticalCenter

                Image {
                    anchors.fill: parent
                    visible: root.iconSource !== ""
                    source: root.iconSource
                    sourceSize: Qt.size(2 * root.iconSize, 2 * root.iconSize)
                    smooth: true
                    mipmap: true
                }

                // No icon anywhere: the app's initial on its accent.
                Rectangle {
                    anchors.fill: parent
                    visible: root.iconSource === ""
                    radius: 14
                    color: root.accent

                    Text {
                        anchors.centerIn: parent
                        text: (root.appName || "?").charAt(0).toUpperCase()
                        color: root.background
                        font.pixelSize: 28
                        font.weight: Font.Bold
                    }
                }
            }

            // Two lines: what's in the window (its title, bold), then the app (regular).
            Column {
                anchors.verticalCenter: parent.verticalCenter
                visible: root.hasText && root.expansion > 0.01
                width: root.textWidth
                spacing: 2

                Text {
                    id: titleText
                    width: parent.width
                    visible: root.appTitle !== ""
                    text: root.appTitle
                    color: root.foreground
                    font.pixelSize: 15
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                    horizontalAlignment: root.rail === "right" ? Text.AlignRight : Text.AlignLeft
                }

                Text {
                    id: nameText
                    width: parent.width
                    visible: root.showsName
                    text: root.appName
                    color: root.foreground
                    font.pixelSize: 14
                    font.weight: Font.Normal
                    elide: Text.ElideRight
                    horizontalAlignment: root.rail === "right" ? Text.AlignRight : Text.AlignLeft
                }
            }
        }

        // Alert badge overlaps the card's upper corner away from the screen edge.
        Rectangle {
            visible: root.badge > 0
            x: root.rail === "right" ? 0 : parent.width - width
            y: 0
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
}
