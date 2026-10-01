import Quickshell
import Quickshell.Io
import QtQuick
import qs.Commons
import qs.Ui

// The picker's contents: your loadouts, and every bar suite's default layout
// to start a new one from. Keyboard driven:
//   ↑/↓ (j/k) pick   Enter swap   s save the current shell   r rename
//   d (twice) delete   Esc close
FocusScope {
  id: sw

  property var closeRequest: null
  property bool active: true
  readonly property string script: Qt.resolvedUrl("shell_swapper.py").toString().replace("file://", "")

  // ---- look: follows the Omarchy theme, like Taskchy -----------------------------
  readonly property color fg: Color.menu.text
  readonly property color solid: Qt.rgba(Color.menu.background.r, Color.menu.background.g, Color.menu.background.b, 1)
  readonly property color accent: Color.accent
  readonly property color urgent: Color.urgent
  readonly property string font: Style.font.family
  readonly property real fs: Style.fontScale
  readonly property int rad: Math.max(6, Style.cornerRadius)
  function px(n) { return Math.round(n * fs) }
  function tint(c, o) { return Util.alpha(c, o) }
  readonly property color dim: tint(fg, 0.6)
  readonly property color faint: tint(fg, 0.4)
  readonly property color line: tint(fg, 0.12)
  readonly property color rowFill: tint(fg, 0.045)
  readonly property color hoverFill: tint(fg, 0.085)
  readonly property color cursorFill: tint(accent, 0.15)
  readonly property color armedFill: tint(urgent, 0.32)

  // ---- data ------------------------------------------------------------------------
  property var info: ({ loadouts: [], suites: [], config: { apply: "restart", confirmSeconds: 15 } })
  readonly property var rows: {
    var out = []
    var ls = (info.loadouts || []).slice().sort(function(a, b) {
      return (b.active - a.active) || a.name.localeCompare(b.name)
    })
    if (ls.length) out.push({ kind: "head", id: "", name: "YOUR SHELLS" })
    ls.forEach(function(l) { out.push({ kind: "loadout", id: l.id, name: l.name, l: l }) })
    out.push({ kind: "head", id: "", name: "START FRESH FROM A SUITE" })
    ;(info.suites || []).forEach(function(s) { out.push({ kind: "suite", id: "suite:" + s.id, name: s.name, s: s }) })
    return out
  }
  readonly property int loadoutCount: (info.loadouts || []).length
  property int cursor: 1
  readonly property var sel: rows.length && rows[cursor] && rows[cursor].kind !== "head" ? rows[cursor] : null
  // move the cursor, over the headings
  function step(d) {
    var i = cursor + d
    while (i >= 0 && i < rows.length && rows[i].kind === "head") i += d
    if (i >= 0 && i < rows.length) cursor = i
  }
  function clampCursor() {
    cursor = Math.max(0, Math.min(cursor, rows.length - 1))
    if (rows.length && rows[cursor].kind === "head") { step(1); if (rows[cursor].kind === "head") step(-1) }
  }
  property string armed: ""            // the loadout "d" was pressed on once
  property string status: ""
  property bool statusBad: false
  property string editing: ""           // "save" | "rename" while the name box is up

  function refresh() { if (!listProc.running) listProc.running = true }
  function say(text, bad) { status = text; statusBad = !!bad; statusTimer.restart() }
  function run(args, done) {
    if (actProc.running) return
    actProc.done = done || null
    actProc.command = ["python3", script].concat(args)
    actProc.running = true
  }
  function suiteName(id) {
    var s = (info.suites || []).filter(function(x) { return x.id === id })[0]
    return s ? s.name : id
  }
  function shortId(id) { var i = String(id).lastIndexOf("."); return i > 0 ? String(id).slice(i + 1) : String(id) }

  function swap() {
    if (!sel || info.pending) return
    if (sel.kind === "loadout" && sel.l.active) { say("That's the shell you're using now."); return }
    if (sel.kind === "loadout" && !sel.l.installed) { say(sel.l.suiteName + " isn't installed.", true); return }
    // detached: a restart ends this shell, and the new one asks to keep it
    Quickshell.execDetached(["python3", script, "apply", sel.id])
    say("Swapping to " + sel.name + "…")
    if (closeRequest) closeRequest()
  }
  function startSave() { editing = "save"; nameBox.input.text = ""; nameBox.input.forceActiveFocus() }
  function startRename() {
    if (!sel || sel.kind !== "loadout") return
    editing = "rename"; nameBox.input.text = sel.name; nameBox.input.selectAll(); nameBox.input.forceActiveFocus()
  }
  function finishName(text) {
    var mode = editing
    editing = ""
    sw.forceActiveFocus()
    if (!text) return
    if (mode === "save") run(["save", text], function(ok) { if (ok) say("Saved the current shell as " + text) })
    else if (mode === "rename" && sel) run(["rename", sel.id, text], function(ok) { if (ok) say("Renamed to " + text) })
  }
  function del() {
    if (!sel || sel.kind !== "loadout") return
    if (sel.l.active) { say("That's the shell you're using; swap to another one first.", true); return }
    if (armed !== sel.id) { armed = sel.id; say("Press d again to delete " + sel.name + " (it goes to the trash)."); return }
    armed = ""
    run(["delete", sel.id], function(ok) { if (ok) say("Deleted " + sel.name) })
  }
  onCursorChanged: armed = ""
  onActiveChanged: if (active) refresh()

  Process {
    id: listProc
    command: ["python3", sw.script, "list"]
    stdout: StdioCollector {
      onStreamFinished: {
        try { sw.info = JSON.parse(text) } catch (e) { return }
        sw.clampCursor()
      }
    }
    stderr: StdioCollector { onStreamFinished: if (text.trim() !== "") sw.say(text.trim(), true) }
  }
  Process {
    id: actProc
    property var done: null
    stderr: StdioCollector { id: actErr }
    onExited: code => {
      if (code !== 0) sw.say(actErr.text.trim() || "That didn't work.", true)
      if (actProc.done) actProc.done(code === 0)
      sw.refresh()
    }
  }
  // a swap waiting to be kept: keep the picker in step
  Timer { interval: 1000; repeat: true; running: sw.active && !!sw.info.pending; onTriggered: sw.refresh() }
  Timer { id: statusTimer; interval: 5000; onTriggered: sw.status = "" }

  // ---- keys ----------------------------------------------------------------------------
  focus: true
  Keys.onPressed: e => {
    if (editing !== "") return
    var k = e.key
    if (k === Qt.Key_Down || k === Qt.Key_J) step(1)
    else if (k === Qt.Key_Up || k === Qt.Key_K) step(-1)
    else if (k === Qt.Key_Home) { cursor = 0; clampCursor() }
    else if (k === Qt.Key_End) { cursor = rows.length - 1; clampCursor() }
    else if (k === Qt.Key_Return || k === Qt.Key_Enter) swap()
    else if (k === Qt.Key_S) startSave()
    else if (k === Qt.Key_R) startRename()
    else if (k === Qt.Key_D) del()
    else if (k === Qt.Key_Escape) { if (armed) { armed = ""; status = "" } else if (closeRequest) closeRequest() }
    else return
    e.accepted = true
  }

  // ---- parts ------------------------------------------------------------------------------
  component Btn: Rectangle {
    id: btn
    property string text: ""
    property string hint: ""
    property bool on: false
    property bool danger: false
    signal clicked()
    height: sw.px(28)
    width: btnRow.implicitWidth + 18
    radius: sw.rad
    color: btn.danger ? sw.armedFill : btn.on ? sw.tint(sw.accent, 0.16) : btnMouse.containsMouse ? sw.hoverFill : sw.rowFill
    border.color: btn.on ? sw.accent : btnMouse.containsMouse ? sw.tint(sw.fg, 0.25) : sw.line
    border.width: 1
    Row {
      id: btnRow
      anchors.centerIn: parent
      spacing: 7
      Text { text: btn.text; color: btn.on ? sw.accent : sw.fg; font.family: sw.font; font.pixelSize: sw.px(11); anchors.verticalCenter: parent.verticalCenter }
      Text { visible: btn.hint !== ""; text: btn.hint; color: sw.faint; font.family: sw.font; font.pixelSize: sw.px(10); anchors.verticalCenter: parent.verticalCenter }
    }
    MouseArea { id: btnMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: btn.clicked() }
  }

  component Heading: Text {
    color: sw.faint
    font.family: sw.font
    font.pixelSize: sw.px(10)
    font.letterSpacing: 1.2
    font.bold: true
  }

  // a widget in the bar sketch
  component Chip: Rectangle {
    property string label: ""
    height: sw.px(18)
    width: chipText.implicitWidth + 12
    radius: height / 2
    color: sw.tint(sw.fg, 0.08)
    border.color: sw.line
    Text { id: chipText; anchors.centerIn: parent; text: parent.label; textFormat: Text.PlainText; color: sw.dim; font.family: sw.font; font.pixelSize: sw.px(9) }
  }

  // ---- layout -------------------------------------------------------------------------------
  Column {
    id: head
    width: parent.width
    spacing: 4
    Row {
      spacing: 10
      Text { text: "Shell-Swapper"; color: sw.fg; font.family: sw.font; font.pixelSize: sw.px(18); font.bold: true }
      Text {
        y: Math.round(sw.px(18) * 0.42)
        text: "using " + sw.suiteName(sw.info.current || "")
        textFormat: Text.PlainText
        color: sw.faint; font.family: sw.font; font.pixelSize: sw.px(11)
      }
    }
    // a swap waiting to be kept
    Rectangle {
      visible: !!sw.info.pending
      width: parent.width
      height: visible ? pendRow.implicitHeight + 14 : 0
      radius: sw.rad
      color: sw.tint(sw.accent, 0.12)
      border.color: sw.accent
      Row {
        id: pendRow
        x: 10; anchors.verticalCenter: parent.verticalCenter
        spacing: 10
        Text {
          anchors.verticalCenter: parent.verticalCenter
          text: sw.info.pending ? "Swapped to " + sw.info.pending.toName + ": keep it?" : ""
          textFormat: Text.PlainText
          color: sw.fg; font.family: sw.font; font.pixelSize: sw.px(12)
        }
        Btn { text: "Keep"; on: true; onClicked: sw.run(["confirm"]) }
        Btn { text: "Go back"; onClicked: Quickshell.execDetached(["python3", sw.script, "revert"]) }
      }
    }
  }

  Item {
    id: body
    anchors { top: head.bottom; topMargin: 14; bottom: foot.top; bottomMargin: 12; left: parent.left; right: parent.right }

    // ---- the list ----
    ListView {
      id: list
      width: Math.round(parent.width * 0.42)
      height: parent.height
      clip: true
      model: sw.rows
      currentIndex: sw.cursor
      spacing: 4
      boundsBehavior: Flickable.StopAtBounds
      highlightFollowsCurrentItem: false
      onCurrentIndexChanged: positionViewAtIndex(currentIndex, ListView.Contain)
      delegate: Item {
        id: slot
        required property var modelData
        required property int index
        width: list.width
        height: modelData.kind === "head" ? sw.px(index === 0 ? 18 : 30) : sw.px(46)
        Heading {
          visible: slot.modelData.kind === "head"
          anchors.bottom: parent.bottom; anchors.bottomMargin: 4
          text: slot.modelData.name
        }
        Rectangle {
        id: row
        visible: slot.modelData.kind !== "head"
        readonly property var modelData: slot.modelData
        readonly property int index: slot.index
        readonly property var lo: modelData.l || ({})
        readonly property var su: modelData.s || ({})
        readonly property bool cur: index === sw.cursor
        readonly property bool isActive: modelData.kind === "loadout" && !!modelData.l && modelData.l.active === true
        width: list.width
        height: sw.px(46)
        radius: sw.rad
        color: sw.armed === modelData.id ? sw.armedFill : cur ? sw.cursorFill : rowMouse.containsMouse ? sw.hoverFill : sw.rowFill
        border.color: cur ? sw.accent : "transparent"
        border.width: 1
        // the active one: a bar of accent down its side
        Rectangle { visible: row.isActive; x: 5; y: 8; width: 3; height: parent.height - 16; radius: 1.5; color: sw.accent }
        Column {
          x: 16
          width: parent.width - 32 - badge.width
          anchors.verticalCenter: parent.verticalCenter
          spacing: 2
          Text {
            width: parent.width
            elide: Text.ElideRight
            textFormat: Text.PlainText
            text: (row.modelData.kind === "suite" ? "+  " : "") + row.modelData.name
            color: sw.fg; font.family: sw.font; font.pixelSize: sw.px(13); font.bold: row.isActive
          }
          Text {
            width: parent.width
            elide: Text.ElideRight
            textFormat: Text.PlainText
            text: row.modelData.kind === "suite" ? "its default layout · " + row.su.id
              : [row.lo.name !== row.lo.suiteName ? row.lo.suiteName : "", row.lo.saved ? "saved " + row.lo.saved : ""]
                  .filter(function(x) { return x }).join(" · ")
            color: sw.faint; font.family: sw.font; font.pixelSize: sw.px(10)
          }
        }
        Text {
          id: badge
          anchors { right: parent.right; rightMargin: 12; verticalCenter: parent.verticalCenter }
          text: row.isActive ? "in use" : row.modelData.kind === "loadout" && row.lo.installed === false ? "not installed" : ""
          color: row.isActive ? sw.accent : sw.urgent
          font.family: sw.font; font.pixelSize: sw.px(10)
        }
        MouseArea {
          id: rowMouse
          anchors.fill: parent
          hoverEnabled: true
          cursorShape: Qt.PointingHandCursor
          onClicked: { sw.cursor = row.index; sw.forceActiveFocus() }
          onDoubleClicked: { sw.cursor = row.index; sw.swap() }
        }
        }
      }
    }

    // ---- the one picked ----
    Item {
      id: detail
      anchors { left: list.right; leftMargin: 18; right: parent.right; top: parent.top; bottom: parent.bottom }
      readonly property var r: sw.sel
      readonly property var lay: !r ? null : r.kind === "loadout" ? r.l.layout : null

      Column {
        width: parent.width
        spacing: 10
        visible: !!detail.r
        Text {
          width: parent.width
          elide: Text.ElideRight
          textFormat: Text.PlainText
          text: detail.r ? detail.r.name : ""
          color: sw.fg; font.family: sw.font; font.pixelSize: sw.px(17); font.bold: true
        }
        Text {
          width: parent.width
          wrapMode: Text.Wrap
          textFormat: Text.PlainText
          text: !detail.r ? "" : detail.r.kind === "suite"
            ? (detail.r.s.description || "") + (detail.r.s.hasLoadout ? "" : "\n\nNo loadout for it yet: swapping makes one, with its default layout.")
            : sw.suiteName(detail.r.l.suite) + " · bar at the " + detail.r.l.position
              + (detail.r.l.note ? "\n" + detail.r.l.note : "")
          color: sw.dim; font.family: sw.font; font.pixelSize: sw.px(11)
        }

        // a sketch of its bar: what sits left, centre and right
        Heading { visible: !!detail.lay; text: "ON ITS BAR" }
        Rectangle {
          visible: !!detail.lay
          width: parent.width
          height: sketch.implicitHeight + 16
          radius: sw.rad
          color: sw.tint(sw.fg, 0.03)
          border.color: sw.line
          Column {
            id: sketch
            x: 10; y: 8
            width: parent.width - 20
            spacing: 8
            Repeater {
              model: ["left", "center", "right"]
              Row {
                id: side
                required property string modelData
                width: sketch.width
                spacing: 8
                Text { width: sw.px(44); text: parent.modelData; color: sw.faint; font.family: sw.font; font.pixelSize: sw.px(10) }
                Flow {
                  width: parent.width - sw.px(52)
                  spacing: 4
                  Repeater {
                    model: detail.lay ? detail.lay[side.modelData] || [] : []
                    Chip { required property string modelData; label: sw.shortId(modelData) }
                  }
                }
              }
            }
          }
        }

        Flow {
          width: parent.width
          spacing: 6
          Btn {
            text: detail.r && detail.r.kind === "loadout" && detail.r.l.active ? "In use" : "Swap to it"
            hint: "Enter"; on: true
            onClicked: sw.swap()
          }
          Btn { visible: !!detail.r && detail.r.kind === "loadout"; text: "Rename"; hint: "r"; onClicked: sw.startRename() }
          Btn {
            visible: !!detail.r && detail.r.kind === "loadout"
            text: sw.armed !== "" && detail.r && sw.armed === detail.r.id ? "Delete it?" : "Delete"
            hint: "d"; danger: sw.armed !== "" && !!detail.r && sw.armed === detail.r.id
            onClicked: sw.del()
          }
        }
      }
    }
  }

  // ---- bottom: save, settings, status ----
  Column {
    id: foot
    anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
    spacing: 8

    // the name box (save as / rename)
    Rectangle {
      id: nameBox
      property alias input: nameInput
      visible: sw.editing !== ""
      width: parent.width
      height: visible ? sw.px(32) : 0
      radius: sw.rad
      color: sw.tint(sw.fg, 0.07)
      border.color: sw.accent
      Text {
        id: nameLabel
        x: 11; anchors.verticalCenter: parent.verticalCenter
        text: sw.editing === "rename" ? "Rename to" : "Save the current shell as"
        color: sw.faint; font.family: sw.font; font.pixelSize: sw.px(11)
      }
      TextInput {
        id: nameInput
        x: nameLabel.x + nameLabel.implicitWidth + 10
        width: parent.width - x - 11
        anchors.verticalCenter: parent.verticalCenter
        color: sw.fg
        selectionColor: sw.tint(sw.accent, 0.4)
        selectedTextColor: sw.fg
        font.family: sw.font
        font.pixelSize: sw.px(12)
        clip: true
        onAccepted: sw.finishName(text.trim())
        Keys.onEscapePressed: { text = ""; sw.finishName("") }
      }
    }

    Item {
      width: parent.width
      height: sw.px(28)
      Row {
        spacing: 6
        anchors.verticalCenter: parent.verticalCenter
        Btn { text: "Save current"; hint: "s"; onClicked: sw.startSave() }
        Text { text: "  apply by"; color: sw.faint; font.family: sw.font; font.pixelSize: sw.px(10); anchors.verticalCenter: parent.verticalCenter }
        Btn { text: "restart"; on: !!sw.info.config && sw.info.config.apply === "restart"; onClicked: sw.run(["config", "apply", "restart"]) }
        Btn { text: "reload"; on: !!sw.info.config && sw.info.config.apply === "reload"; onClicked: sw.run(["config", "apply", "reload"]) }
        Text { text: "  keep-it timer"; color: sw.faint; font.family: sw.font; font.pixelSize: sw.px(10); anchors.verticalCenter: parent.verticalCenter }
        Repeater {
          model: [10, 15, 30]
          Btn {
            required property int modelData
            text: modelData + " s"
            on: !!sw.info.config && sw.info.config.confirmSeconds === modelData
            onClicked: sw.run(["config", "confirmSeconds", String(modelData)])
          }
        }
      }
      Text {
        anchors { right: parent.right; verticalCenter: parent.verticalCenter }
        text: "↑↓ pick  ⏎ swap  s save  r rename  d delete  esc close"
        color: sw.faint; font.family: sw.font; font.pixelSize: sw.px(10)
      }
    }
    Text {
      width: parent.width
      height: text === "" ? 0 : implicitHeight
      wrapMode: Text.Wrap
      textFormat: Text.PlainText
      text: sw.status
      color: sw.statusBad ? sw.urgent : sw.dim
      font.family: sw.font; font.pixelSize: sw.px(11)
    }
  }
}
