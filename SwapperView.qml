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

  // ---- asking first: anything that would change your keys shows its plan --
  property var ask: null                // {title, lines: [{text, tone}], buttons: [{label, hint, key, primary, act}]}
  function closeAsk() { ask = null; sw.forceActiveFocus() }
  function lineList(p) {
    var out = [], home = Quickshell.env("HOME") || ""
    function short(f) { return home && String(f).indexOf(home) === 0 ? "~" + String(f).slice(home.length) : String(f) }
    function base(f) { return String(f).split("/").pop() }
    if (p.lines && p.file) {
      out.push({ text: "Adds these lines to " + short(p.file) + ", after line " + p.afterLine + ":" })
      p.lines.forEach(function(l) { out.push({ text: l, tone: "code" }) })
    }
    if (p.move) {
      out.push({ text: "Moves these out of your bindings.lua into " + (p.loadoutName || p.loadout) + ":" })
      p.move.forEach(function(m) { out.push({ text: m.keys + "   " + (m.desc || "") + "   (line " + m.line + ")", tone: "add" }) })
      ;(p.alsoCommentOut || []).forEach(function(u) { out.push({ text: "and line " + u.line + ":  " + u.lua, tone: "code" }) })
      out.push({ text: "In bindings.lua they're commented out and tagged, not deleted: taking them out of the loadout puts them back." , tone: "dim" })
      if (p.note) out.push({ text: p.note.charAt(0).toUpperCase() + p.note.slice(1) + ".", tone: "dim" })
    }
    if (p.keys && typeof p.keys === "string") {
      out.push({ text: "Takes " + p.keys + (p.desc ? " (" + p.desc + ")" : "") + " out of the loadout." })
      if (p.backToBindings && p.backToBindings.length)
        out.push({ text: "It goes back into your bindings.lua (line " + p.backToBindings.join(", ") + ").", tone: "add" })
      else if (p.activeNow) out.push({ text: "It stops working now: it's the shell you're using.", tone: "warn" })
    }
    if (p.diff && p.diff.length) {
      out.push({ text: "Only these lines change (- before, + after):" })
      p.diff.slice(2, 42).forEach(function(l) {
        if (l.indexOf("@@") === 0) return
        var c = l.charAt(0)
        out.push({ text: (c === "+" ? "+   " : c === "-" ? "−   " : "    ") + l.slice(1),
                   tone: c === "+" ? "add" : c === "-" ? "remove" : "code" })
      })
      if (p.diff.length > 42) out.push({ text: "…and more", tone: "dim" })
    }
    if (p.from && p.file && !p.lines) out.push({ text: "Puts " + short(p.file) + " back as it was in " + base(p.from) + "." })
    if (p.backupTo) out.push({ text: "Everything it changes is backed up first, to " + short(p.backupTo) + ".", tone: "dim" })
    return out
  }
  // run a command that may need your OK: its plan (exit 2) is shown first
  function planRun(args, done) {
    if (planProc.running) return
    planProc.args = args
    planProc.done = done || null
    planProc.command = ["python3", script].concat(args)
    planProc.running = true
  }
  function askPlan(args, plan, done) {
    var t = String(plan.action || "Change your keys")
    ask = {
      title: t.charAt(0).toUpperCase() + t.slice(1) + "?",
      lines: lineList(plan),
      buttons: [
        { label: "Yes, do it", hint: "Enter", primary: true, act: function() { closeAsk(); run(args.concat(["--yes"]), done) } },
        { label: "Cancel", hint: "Esc", act: function() { closeAsk() } }
      ]
    }
  }
  function askSwap(id, name, plan) {
    var lines = [{ text: name + " has its own keys. Swapping keys too would change these:" }]
    var k = plan.keys || {}
    ;(k.add || []).forEach(function(a) { lines.push({ text: "+  " + a.keys + "   " + (a.desc || ""), tone: "add" }) })
    ;(k.remove || []).forEach(function(r) { lines.push({ text: "−  " + r.keys + "   " + (r.desc || ""), tone: "remove" }) })
    ;(k.conflicts || []).forEach(function(c) { lines.push({ text: "!  " + c.keys + " is bound in your bindings.lua too (line " + c.line + "): left out", tone: "warn" }) })
    lines.push({ text: "Your bindings.lua isn't touched either way. The keys file is backed up first, and going back puts it back.", tone: "dim" })
    ask = {
      title: "Swap to " + name + "?",
      lines: lines,
      buttons: [
        { label: "Swap keys too", hint: "Enter", primary: true, act: function() { closeAsk(); doSwap(id, name, "--keys") } },
        { label: "Keep my keys", hint: "k", key: Qt.Key_K, act: function() { closeAsk(); doSwap(id, name, "--keep-keys") } },
        { label: "Cancel", hint: "Esc", act: function() { closeAsk() } }
      ]
    }
  }
  function doSwap(id, name, flag) {
    var args = ["apply", id]
    if (flag) args.push(flag)
    swapProc.name = name
    swapProc.command = ["python3", script].concat(args)
    swapProc.running = true
  }

  // ---- the keys manager (k) -----------------------------------------------------
  property string mode: "list"          // list | keys
  property string keysFor: ""           // the loadout being edited
  readonly property var keysLoadout: (info.loadouts || []).filter(function(l) { return l.id === keysFor })[0] || null
  property var scan: []                 // your bindings.lua's binds
  readonly property var scanBinds: scan.filter(function(b) { return b.kind === "bind" })
  property int kcol: 1                  // 0: the loadout's keys, 1: your bindings.lua
  property int kcur0: 0
  property int kcur1: 0
  property var marked: ({})             // bindings.lua lines picked to move
  function openKeys() {
    if (!sel || sel.kind !== "loadout") { say("Pick one of your shells to give it keys."); return }
    keysFor = sel.id; mode = "keys"; marked = ({}); kcur0 = 0; kcur1 = 0
    kcol = sel.l.keys.length ? 0 : 1
    refreshScan()
  }
  function refreshScan() { if (!scanProc.running) scanProc.running = true }
  function toggleMark() {
    var b = scanBinds[kcur1]
    if (!b) return
    var m = Object.assign({}, marked)
    if (m[b.line]) delete m[b.line]; else m[b.line] = true
    marked = m
  }
  function moveMarked() {
    if (!info.keysHook) { setupHook(); return }
    // by key combo, not line number: lines move as bindings.lua is tidied
    var picks = scanBinds.filter(function(b) { return marked[b.line] }).map(function(b) { return b.keys })
    if (!picks.length && scanBinds[kcur1]) picks = [scanBinds[kcur1].keys]
    if (!picks.length) return
    planRun(["keys", "move", keysFor].concat(picks), function(ok) { if (ok) { marked = ({}); say("Moved into " + (keysLoadout ? keysLoadout.name : keysFor)) } })
  }
  function takeOut() {
    var k = keysLoadout ? keysLoadout.keys[kcur0] : null
    if (!k) return
    planRun(["keys", "remove", keysFor, String(kcur0)], function(ok) { if (ok) say("Took " + k.keys + " out") })
  }
  function setupHook() { planRun(["keys", "hook"], function(ok) { if (ok) say("Loadout keys are set up.") }) }

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
    // it asks first if the loadout's keys differ from yours
    doSwap(sel.id, sel.name, "")
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
  // swaps: exit 2 = its keys differ, ask which way
  Process {
    id: swapProc
    property string name: ""
    stdout: StdioCollector { id: swapOut }
    stderr: StdioCollector { id: swapErr }
    onExited: code => {
      if (code === 2) {
        var r = null
        try { r = JSON.parse(swapOut.text) } catch (e) {}
        var args = swapProc.command.slice(2)
        if (r && r.plan) sw.askSwap(args[1], swapProc.name, r.plan)
        return
      }
      if (code !== 0) { sw.say(swapErr.text.trim() || "That didn't work.", true); return }
      sw.say("Swapping to " + swapProc.name + "…")
      if (sw.closeRequest) sw.closeRequest()
    }
  }
  Process {
    id: planProc
    property var args: []
    property var done: null
    stdout: StdioCollector { id: planOut }
    stderr: StdioCollector { id: planErr }
    onExited: code => {
      if (code === 2) {
        var r = null
        try { r = JSON.parse(planOut.text) } catch (e) {}
        if (r && r.plan) {
          if (sw.keysLoadout) r.plan.loadoutName = sw.keysLoadout.name
          sw.askPlan(planProc.args, r.plan, planProc.done)
        }
        return
      }
      if (code !== 0) sw.say(planErr.text.trim() || "That didn't work.", true)
      else if (planProc.done) planProc.done(true)
      sw.refresh(); sw.refreshScan()
    }
  }
  Process {
    id: scanProc
    command: ["python3", sw.script, "keys", "scan"]
    stdout: StdioCollector { onStreamFinished: { try { sw.scan = JSON.parse(text).binds || [] } catch (e) {} } }
  }
  Process {
    id: actProc
    property var done: null
    stderr: StdioCollector { id: actErr }
    onExited: code => {
      if (code !== 0) sw.say(actErr.text.trim() || "That didn't work.", true)
      if (actProc.done) actProc.done(code === 0)
      sw.refresh()
      if (sw.mode === "keys") sw.refreshScan()
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
    if (ask) {
      var bs = ask.buttons
      if (k === Qt.Key_Return || k === Qt.Key_Enter) { var p = bs.filter(function(b) { return b.primary })[0]; if (p) p.act() }
      else if (k === Qt.Key_Escape) closeAsk()
      else { var hit = bs.filter(function(b) { return b.key === k })[0]; if (hit) hit.act() }
      e.accepted = true
      return
    }
    if (mode === "keys") {
      var n0 = keysLoadout ? keysLoadout.keys.length : 0
      if (k === Qt.Key_Tab || k === Qt.Key_Left || k === Qt.Key_Right || k === Qt.Key_H || k === Qt.Key_L) kcol = kcol ? 0 : 1
      else if (k === Qt.Key_Down || k === Qt.Key_J) { if (kcol) kcur1 = Math.min(scanBinds.length - 1, kcur1 + 1); else kcur0 = Math.min(n0 - 1, kcur0 + 1) }
      else if (k === Qt.Key_Up || k === Qt.Key_K) { if (kcol) kcur1 = Math.max(0, kcur1 - 1); else kcur0 = Math.max(0, kcur0 - 1) }
      else if (k === Qt.Key_Space) { if (kcol) toggleMark() }
      else if (k === Qt.Key_M || (kcol === 1 && (k === Qt.Key_Return || k === Qt.Key_Enter))) moveMarked()
      else if (k === Qt.Key_D || (kcol === 0 && (k === Qt.Key_Return || k === Qt.Key_Enter))) { if (kcol === 0) takeOut() }
      else if (k === Qt.Key_U) { if (!info.keysHook) setupHook() }
      else if (k === Qt.Key_O) planRun(["keys", "organize"], function(ok) { if (ok) say("bindings.lua is tidy.") })
      else if (k === Qt.Key_Escape) { mode = "list"; marked = ({}) }
      else return
      e.accepted = true
      return
    }
    if (k === Qt.Key_Down || k === Qt.Key_J) step(1)
    else if (k === Qt.Key_Up || k === Qt.Key_K) step(-1)
    else if (k === Qt.Key_Home) { cursor = 0; clampCursor() }
    else if (k === Qt.Key_End) { cursor = rows.length - 1; clampCursor() }
    else if (k === Qt.Key_Return || k === Qt.Key_Enter) swap()
    else if (k === Qt.Key_S) startSave()
    else if (k === Qt.Key_R) startRename()
    else if (k === Qt.Key_D) del()
    else if (k === Qt.Key_B) openKeys()
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

  component KeyRow: Rectangle {
    id: kr
    property string keysText: ""
    property string sub: ""
    property bool cur: false
    property bool picked: false
    property bool warn: false
    signal clicked()
    width: parent ? parent.width : 0
    height: sw.px(40)
    radius: sw.rad
    color: kr.cur ? sw.cursorFill : krMouse.containsMouse ? sw.hoverFill : sw.rowFill
    border.color: kr.cur ? sw.accent : kr.picked ? sw.tint(sw.accent, 0.6) : "transparent"
    Text {
      id: tick
      x: 10; anchors.verticalCenter: parent.verticalCenter
      text: kr.picked ? "\u2713" : ""
      width: sw.px(12)
      color: sw.accent; font.family: sw.font; font.pixelSize: sw.px(12)
    }
    Column {
      x: tick.x + tick.width + 6
      width: parent.width - x - 10
      anchors.verticalCenter: parent.verticalCenter
      Text { width: parent.width; elide: Text.ElideRight; textFormat: Text.PlainText; text: kr.keysText; color: kr.warn ? sw.urgent : sw.fg; font.family: sw.font; font.pixelSize: sw.px(12) }
      Text { width: parent.width; elide: Text.ElideRight; textFormat: Text.PlainText; text: kr.sub; color: sw.faint; font.family: sw.font; font.pixelSize: sw.px(10) }
    }
    MouseArea { id: krMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: kr.clicked() }
  }

  // ---- layout -------------------------------------------------------------------------------
  Text {
    anchors.right: parent.right
    y: sw.px(5)
    text: sw.mode === "keys" ? "tab side  ↑↓ pick  space mark  m move in  d take out  o tidy  esc back"
      : "↑↓ pick  ⏎ swap  b keys  s save  r rename  d delete  esc close"
    color: sw.faint; font.family: sw.font; font.pixelSize: sw.px(10)
  }
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
    visible: sw.mode === "list"
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

        // its own keys (they come with it when you swap, if you say so)
        Heading { visible: !!detail.r && detail.r.kind === "loadout"; text: "ITS KEYS" }
        Text {
          visible: !!detail.r && detail.r.kind === "loadout" && detail.r.l.keys.length === 0
          width: parent.width
          wrapMode: Text.Wrap
          text: "None yet: your bindings.lua keys work the same in every shell. Press b to give this shell keys of its own."
          color: sw.faint; font.family: sw.font; font.pixelSize: sw.px(11)
        }
        Column {
          visible: !!detail.r && detail.r.kind === "loadout" && detail.r.l.keys.length > 0
          width: parent.width
          spacing: 3
          Repeater {
            model: detail.r && detail.r.kind === "loadout" ? detail.r.l.keys : []
            Row {
              required property var modelData
              spacing: 10
              Text { width: sw.px(170); elide: Text.ElideRight; textFormat: Text.PlainText; text: modelData.keys; color: modelData.clashLine ? sw.urgent : sw.fg; font.family: sw.font; font.pixelSize: sw.px(11) }
              Text {
                textFormat: Text.PlainText
                text: modelData.desc + (modelData.clashLine ? "   (also in bindings.lua, line " + modelData.clashLine + ": left out)" : "")
                color: modelData.clashLine ? sw.urgent : sw.dim; font.family: sw.font; font.pixelSize: sw.px(11)
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
          Btn { visible: !!detail.r && detail.r.kind === "loadout"; text: "Keys"; hint: "b"; onClicked: sw.openKeys() }
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

  // ---- the keys manager ----
  Item {
    id: keysPane
    visible: sw.mode === "keys"
    anchors { top: head.bottom; topMargin: 14; bottom: foot.top; bottomMargin: 12; left: parent.left; right: parent.right }

    Column {
      id: keysHead
      width: parent.width
      spacing: 6
      Text {
        textFormat: Text.PlainText
        text: "Keys for " + (sw.keysLoadout ? sw.keysLoadout.name : "")
        color: sw.fg; font.family: sw.font; font.pixelSize: sw.px(15); font.bold: true
      }
      Text {
        width: parent.width
        wrapMode: Text.Wrap
        text: "Keys you move in here only work while this is your shell (and only if you say so when you swap). "
          + "In bindings.lua they're gathered under this shell's heading. Nothing changes without asking you, and "
          + "everything is backed up first (your original bindings.lua is kept for good)."
        color: sw.dim; font.family: sw.font; font.pixelSize: sw.px(11)
      }
      // not set up yet: one line in hyprland.lua loads the loadout keys
      Rectangle {
        visible: !sw.info.keysHook
        width: parent.width
        height: visible ? hookRow.implicitHeight + 14 : 0
        radius: sw.rad
        color: sw.tint(sw.accent, 0.12)
        border.color: sw.accent
        Row {
          id: hookRow
          x: 10; anchors.verticalCenter: parent.verticalCenter
          spacing: 10
          Text {
            anchors.verticalCenter: parent.verticalCenter
            text: "Loadout keys need one line in your hyprland.lua to load them."
            color: sw.fg; font.family: sw.font; font.pixelSize: sw.px(12)
          }
          Btn { text: "Set it up…"; hint: "u"; on: true; onClicked: sw.setupHook() }
        }
      }
    }

    Item {
      anchors { top: keysHead.bottom; topMargin: 12; bottom: parent.bottom; left: parent.left; right: parent.right }
      // left: the loadout's keys
      Column {
        id: kLeft
        width: Math.round(parent.width * 0.45)
        height: parent.height
        spacing: 6
        Heading { text: "THIS SHELL'S KEYS"; color: sw.kcol === 0 ? sw.accent : sw.faint }
        Text {
          visible: !sw.keysLoadout || sw.keysLoadout.keys.length === 0
          width: parent.width; wrapMode: Text.Wrap
          text: "None yet. Pick keys on the right (space) and move them in (m)."
          color: sw.faint; font.family: sw.font; font.pixelSize: sw.px(11)
        }
        ListView {
          width: parent.width
          height: parent.height - y
          clip: true
          spacing: 4
          model: sw.keysLoadout ? sw.keysLoadout.keys : []
          delegate: KeyRow {
            required property var modelData
            required property int index
            keysText: modelData.keys
            sub: modelData.desc + (modelData.clashLine ? "  ·  also in bindings.lua (line " + modelData.clashLine + "): left out" : "")
            warn: !!modelData.clashLine
            cur: sw.kcol === 0 && index === sw.kcur0
            onClicked: { sw.kcol = 0; sw.kcur0 = index }
          }
        }
      }
      // right: your bindings.lua
      Column {
        anchors { left: kLeft.right; leftMargin: 18; right: parent.right }
        height: parent.height
        spacing: 6
        Heading { text: "YOUR BINDINGS.LUA"; color: sw.kcol === 1 ? sw.accent : sw.faint }
        ListView {
          id: kRight
          width: parent.width
          height: parent.height - y
          clip: true
          spacing: 4
          model: sw.scanBinds
          currentIndex: sw.kcur1
          onCurrentIndexChanged: positionViewAtIndex(currentIndex, ListView.Contain)
          delegate: KeyRow {
            required property var modelData
            required property int index
            keysText: modelData.keys
            sub: "line " + modelData.line + (modelData.desc ? "  ·  " + modelData.desc : "")
              + (modelData.inLoadouts.length ? "  ·  a loadout has this key too" : "")
            warn: modelData.inLoadouts.length > 0
            picked: !!sw.marked[modelData.line]
            cur: sw.kcol === 1 && index === sw.kcur1
            onClicked: { sw.kcol = 1; sw.kcur1 = index; sw.toggleMark() }
          }
        }
      }
    }
  }

  // ---- the question: what would change, and your OK ----
  Rectangle {
    anchors.fill: parent
    visible: !!sw.ask
    z: 50
    color: sw.tint(sw.solid, 0.75)
    radius: sw.rad
    MouseArea { anchors.fill: parent; onClicked: {} }
    Rectangle {
      width: Math.min(parent.width - 60, sw.px(620))
      height: askCol.implicitHeight + 36
      anchors.centerIn: parent
      radius: sw.rad
      color: sw.solid
      border.color: sw.accent
      border.width: 1
      Column {
        id: askCol
        x: 18; y: 18
        width: parent.width - 36
        spacing: 8
        Text {
          width: parent.width
          wrapMode: Text.Wrap
          textFormat: Text.PlainText
          text: sw.ask ? sw.ask.title : ""
          color: sw.fg; font.family: sw.font; font.pixelSize: sw.px(15); font.bold: true
        }
        Repeater {
          model: sw.ask ? sw.ask.lines : []
          Text {
            required property var modelData
            width: askCol.width
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
            text: modelData.text
            leftPadding: modelData.tone === "code" ? 12 : 0
            color: modelData.tone === "add" ? sw.accent : modelData.tone === "remove" || modelData.tone === "warn" ? sw.urgent
              : modelData.tone === "dim" || modelData.tone === "code" ? sw.dim : sw.fg
            font.family: sw.font
            font.pixelSize: sw.px(modelData.tone === "code" ? 11 : 12)
          }
        }
        Item { width: 1; height: 4 }
        Row {
          spacing: 8
          Repeater {
            model: sw.ask ? sw.ask.buttons : []
            Btn {
              required property var modelData
              text: modelData.label
              hint: modelData.hint || ""
              on: !!modelData.primary
              onClicked: modelData.act()
            }
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
