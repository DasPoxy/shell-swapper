import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import QtQuick
import qs.Commons
import qs.Ui

// Runs from shell start-up: after a swap, the new shell asks "Keep this
// shell?" here, counting down. Keep it, or revert (Esc, or just wait). The
// CLI's watchdog does the reverting, so a shell that never gets this far is
// put back too.
Item {
  id: svc

  property var shell: null
  property var manifest: null

  readonly property string script: Qt.resolvedUrl("shell_swapper.py").toString().replace("file://", "")
  readonly property string pendingPath: Quickshell.env("HOME") + "/.local/state/shell-swapper/pending.json"

  // (for offscreen pictures of the question)
  readonly property alias askWindow: ask
  property var pending: null
  property string answered: ""          // the swap we've answered (hide at once)
  property string announced: ""         // the swap we've told the CLI is on screen
  property real nowSec: Date.now() / 1000
  readonly property bool asking: pending !== null && answered !== pending.token
  readonly property int secondsLeft: !pending ? 0
    : pending.seen ? Math.max(0, Math.ceil(pending.deadline - nowSec)) : pending.confirmSeconds
  readonly property real fraction: !pending ? 0 : Math.max(0, Math.min(1, secondsLeft / Math.max(1, pending.confirmSeconds)))

  function readPending(raw) {
    var p = null
    try { p = JSON.parse(raw) } catch (e) { p = null }
    if (!p || !p.token) { svc.pending = null; return }
    svc.pending = p
    if (svc.announced !== p.token) {
      svc.announced = p.token
      seenProc.command = ["python3", svc.script, "seen", p.token]
      seenProc.running = true
    }
  }
  function keep() {
    if (!svc.pending) return
    svc.answered = svc.pending.token
    confirmProc.command = ["python3", svc.script, "confirm", svc.pending.token]
    confirmProc.running = true
  }
  function revert() {
    if (!svc.pending) return
    svc.answered = svc.pending.token
    // detached: the revert restarts the shell this runs in
    Quickshell.execDetached(["python3", svc.script, "revert", svc.pending.token])
  }

  FileView {
    id: pendingFile
    path: svc.pendingPath
    printErrors: false
    onLoaded: svc.readPending(text())
    onLoadFailed: svc.pending = null
  }
  // look again while a swap is waiting (and when the CLI says so, below)
  Timer {
    interval: 500
    repeat: true
    running: svc.pending !== null
    onTriggered: { svc.nowSec = Date.now() / 1000; pendingFile.reload() }
  }
  Process { id: seenProc; onExited: pendingFile.reload() }
  Process { id: confirmProc; onExited: pendingFile.reload() }

  IpcHandler {
    target: "shell-swapper"
    // the CLI, after a swap that didn't restart the shell
    function check(): void { pendingFile.reload() }
    function keep(): void { svc.keep() }
    function revert(): void { svc.revert() }
  }

  // ---- the question ----
  PanelWindow {
    id: ask
    visible: svc.asking
    anchors { top: true; left: true; right: true }
    implicitHeight: card.height + Style.space(120)
    color: "transparent"
    WlrLayershell.namespace: "shell-swapper-ask"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.Exclusive
    exclusionMode: ExclusionMode.Ignore

    readonly property color fg: Color.menu.text
    readonly property color accent: Color.accent
    readonly property real fs: Style.fontScale
    function px(n) { return Math.round(n * fs) }
    function tint(c, o) { return Util.alpha(c, o) }

    BorderSurface {
      id: card
      width: Math.min(ask.px(460), ask.width - 40)
      height: col.implicitHeight + Style.spacing.panelPadding * 2 + 8
      anchors.horizontalCenter: parent.horizontalCenter
      y: Style.space(70)
      radius: Style.cornerRadius
      color: Qt.rgba(Color.menu.background.r, Color.menu.background.g, Color.menu.background.b, 1)
      borderSpec: Border.surfaceSpec("menu", "border", Color.menu.border, Math.max(1, Style.space(2)))
      padding: Style.spacing.panelPadding

      Item {
        id: keys
        anchors.fill: parent
        focus: svc.asking
        Keys.onReturnPressed: svc.keep()
        Keys.onEnterPressed: svc.keep()
        Keys.onEscapePressed: svc.revert()
        Keys.onPressed: e => {
          if (e.key === Qt.Key_Y || e.key === Qt.Key_K) { svc.keep(); e.accepted = true }
          else if (e.key === Qt.Key_N || e.key === Qt.Key_R) { svc.revert(); e.accepted = true }
        }
      }

      Column {
        id: col
        x: card.contentLeftInset
        y: card.contentTopInset + 4
        width: card.width - card.contentLeftInset - card.contentRightInset
        spacing: 10

        Text {
          width: parent.width
          wrapMode: Text.Wrap
          textFormat: Text.PlainText
          text: "Keep " + (svc.pending ? svc.pending.toName : "this shell") + "?"
          color: ask.fg
          font.family: Style.font.family
          font.pixelSize: ask.px(16)
          font.bold: true
        }
        Text {
          width: parent.width
          wrapMode: Text.Wrap
          text: "Going back to the shell you had in " + svc.secondsLeft + " s, unless you keep this one."
          color: ask.tint(ask.fg, 0.7)
          font.family: Style.font.family
          font.pixelSize: ask.px(12)
        }
        // the time left, running out
        Rectangle {
          width: parent.width
          height: 4
          radius: 2
          color: ask.tint(ask.fg, 0.12)
          Rectangle {
            width: parent.width * svc.fraction
            height: parent.height
            radius: 2
            color: ask.accent
            Behavior on width { NumberAnimation { duration: 450 } }
          }
        }
        Row {
          spacing: 8
          AskButton { text: "Keep it"; hint: "Enter"; on: true; onClicked: svc.keep() }
          AskButton { text: "Go back"; hint: "Esc"; onClicked: svc.revert() }
        }
      }
    }
  }

  component AskButton: Rectangle {
    id: b
    property string text: ""
    property string hint: ""
    property bool on: false
    signal clicked()
    height: ask.px(30)
    width: bRow.implicitWidth + 22
    radius: Math.max(6, Style.cornerRadius)
    color: b.on ? ask.tint(ask.accent, 0.16) : bMouse.containsMouse ? ask.tint(ask.fg, 0.085) : ask.tint(ask.fg, 0.045)
    border.color: b.on ? ask.accent : ask.tint(ask.fg, 0.12)
    border.width: 1
    Row {
      id: bRow
      anchors.centerIn: parent
      spacing: 8
      Text { text: b.text; color: b.on ? ask.accent : ask.fg; font.family: Style.font.family; font.pixelSize: ask.px(12) }
      Text { text: b.hint; color: ask.tint(ask.fg, 0.45); font.family: Style.font.family; font.pixelSize: ask.px(10); anchors.verticalCenter: parent.verticalCenter }
    }
    MouseArea { id: bMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: b.clicked() }
  }
}
