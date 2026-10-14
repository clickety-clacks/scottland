import QtQuick
import Quickshell
import Quickshell.Io

Item {
    id: root

    readonly property string socketPath: Quickshell.env("SCOTTLAND_WIDGET_OSD_SOCKET") || ""
    readonly property string widgetBinary: Quickshell.env("SCOTTLAND_WIDGET_OSD_BINARY") || ""
    readonly property string panelPath: Quickshell.env("SCOTTLAND_WIDGET_OSD_PANEL") || ""
    property var queue: []
    property var current: null
    property var focusedScreen: null
    property var panelComponent: null
    property var panelInstance: null

    function reply(client, value) {
        if (!client || !client.connected) return
        client.write(JSON.stringify(value) + "\n")
        client.flush()
        client.connected = false
    }

    function failCurrent(reason) {
        if (!current) return
        readyTimer.stop()
        reply(current.client, {error: reason})
        current = null
        focusedScreen = null
        Qt.callLater(root.processNext)
    }

    function finishShown() {
        if (!current || !panelInstance) return
        const panel = panelInstance
        const expectedProgress = current.data.progress === undefined ? -1 : current.data.progress
        if (panel.visible && panel.backingWindowVisible
                && panel.message === current.data.message
                && panel.progress === expectedProgress && panel.screen === focusedScreen) {
            readyTimer.stop()
            reply(current.client, {result: "shown"})
            current = null
            focusedScreen = null
            Qt.callLater(root.processNext)
        }
    }

    function presentLoadedPanel() {
        if (!current || !panelInstance) return
        const panel = panelInstance
        if (typeof panel.present !== "function") {
            failCurrent("the OSD panel does not implement present")
            return
        }
        readyTimer.restart()
        try {
            panel.screen = focusedScreen
            panel.present(
                current.data.message,
                current.data.icon || "",
                current.data.progress === undefined ? -1 : current.data.progress,
                current.data.duration_ms === undefined ? 0 : current.data.duration_ms)
        } catch (error) {
            failCurrent("the OSD panel could not present the request")
            return
        }
        finishShown()
    }

    function focusedOutputDone(exitCode, exitStatus, outputName) {
        if (!current) return
        if (exitCode !== 0 || exitStatus !== 0) {
            failCurrent("could not determine the focused output")
            return
        }
        if (!outputName) {
            failCurrent("the session did not report a focused output")
            return
        }
        beginPanel(current, outputName)
    }

    function beginPanel(request, outputName) {
        let screen = null
        for (let index = 0; index < Quickshell.screens.length; index++) {
            const candidate = Quickshell.screens[index]
            if (candidate.name === outputName) {
                screen = candidate
                break
            }
        }
        if (!screen) {
            reply(request.client, {error: "the focused output is unavailable"})
            current = null
            focusedScreen = null
            Qt.callLater(root.processNext)
            return
        }
        focusedScreen = screen
        current = request
        if (panelInstance) {
            presentLoadedPanel()
            return
        }
        if (!panelPath) {
            failCurrent("the OSD panel is not installed")
            return
        }
        readyTimer.restart()
        if (!panelComponent)
            panelComponent = Qt.createComponent(panelPath, Component.Asynchronous)
        panelComponentStatusChanged()
    }

    function panelComponentStatusChanged() {
        if (!current || !panelComponent) return
        if (panelComponent.status === Component.Error) {
            failCurrent("the OSD panel could not be loaded")
            return
        }
        if (panelComponent.status !== Component.Ready || panelInstance) return
        panelInstance = panelComponent.createObject(null)
        if (!panelInstance) {
            failCurrent("the OSD panel could not be created")
            return
        }
        presentLoadedPanel()
    }

    function startFocusedOutputQuery(request) {
        current = request
        const process = focusedOutputProcess.createObject(root, {
            command: [widgetBinary, "__focused-output"]
        })
        process.running = true
    }

    function processNext() {
        if (current || queue.length === 0) return
        const request = queue.shift()
        if (!widgetBinary) {
            reply(request.client, {error: "the widget binary is unavailable"})
            Qt.callLater(root.processNext)
            return
        }
        startFocusedOutputQuery(request)
    }

    function acceptLine(client, line) {
        let request
        try {
            request = JSON.parse(line)
        } catch (error) {
            reply(client, {error: "the OSD request is not valid JSON"})
            return
        }
        if (!request || request.method !== "osd.present" || !request.data
                || typeof request.data.message !== "string" || request.data.message.length === 0
                || (request.data.icon !== undefined && typeof request.data.icon !== "string")
                || (request.data.progress !== undefined
                    && (!Number.isInteger(request.data.progress)
                        || request.data.progress < 0 || request.data.progress > 100))
                || (request.data.duration_ms !== undefined
                    && (!Number.isInteger(request.data.duration_ms) || request.data.duration_ms <= 0))) {
            reply(client, {error: "the OSD request has invalid fields"})
            return
        }
        queue.push({client: client, data: request.data})
        root.processNext()
    }

    SocketServer {
        active: true
        path: root.socketPath
        handler: Socket {
            parser: SplitParser {
                splitMarker: "\n"
                onRead: data => root.acceptLine(parent, data)
            }
        }
    }

    Component {
        id: focusedOutputProcess
        Process {
            stdout: StdioCollector { id: outputResult }
            onExited: (exitCode, exitStatus) => {
                root.focusedOutputDone(exitCode, exitStatus, outputResult.text.trim())
                destroy()
            }
        }
    }

    Connections {
        target: root.panelComponent
        function onStatusChanged() { root.panelComponentStatusChanged() }
    }

    Connections {
        target: root.panelInstance
        function onVisibleChanged() { root.finishShown() }
        function onBackingWindowVisibleChanged() { root.finishShown() }
    }

    Timer {
        id: readyTimer
        interval: 8000
        repeat: false
        onTriggered: root.failCurrent("the OSD panel did not become visible")
    }
}
