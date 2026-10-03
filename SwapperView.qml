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
  function closeAsk() { ask = null; sw.takeKeys() }
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
    if (p["import"]) {
      var take = p["import"].filter(function(r) { return r.status === "new" || r.status === "replace" })
      out.push({ text: "Adds " + take.length + " key" + (take.length === 1 ? "" : "s") + " to " + (p.loadoutName || p.loadout) + ":" })
      take.forEach(function(r) {
        out.push({ text: (r.status === "replace" ? "↻  " : "+  ") + r.keys + "   " + r.desc, tone: "add" })
        if (r.why) out.push({ text: r.why, tone: r.clashLine ? "warn" : "dim" })
      })
      var left = p["import"].length - take.length
      if (left) out.push({ text: left + " left out (already there, or the same key twice).", tone: "dim" })
      out.push({ text: "They go into this shell's loadout and, tagged, into its group in bindings.lua. "
        + "A key they replace keeps its line there, marked as replaced.", tone: "dim" })
      out.push({ text: p.activeNow ? "It's the shell you're using: they work now." : "They work when you swap to it (and bring its keys).", tone: "dim" })
    }
    if (p.widgets || (p.plugins && p.action && p.action.indexOf("other shells") >= 0)) {
      out.push({ text: "Takes these out of " + p.loadout + " (they belong to other shells):" })
      ;(p.widgets || []).forEach(function(w) { out.push({ text: "widget  " + w, tone: "remove" }) })
      ;(p.plugins || []).forEach(function(x) { out.push({ text: "plugin  " + x, tone: "remove" }) })
      out.push({ text: p.activeNow ? "It's your shell now: swap to it again (or re-apply) to see it without them."
                                   : "You'll see it the next time you swap to it.", tone: "dim" })
    }
    if (p.takeOff) {
      out.push({ text: "Takes these off " + (p.loadoutName || p.loadout) + "'s bar (only the bar: their plugins stay):" })
      p.takeOff.forEach(function(w) { out.push({ text: w.id + (w.ownerName ? "   (" + w.ownerName + "'s)" : ""), tone: "remove" }) })
      out.push({ text: p.activeNow ? "It's the shell you're using: swap to it again (or re-apply) to see it without them."
                                   : "You'll see it the next time you swap to it.", tone: "dim" })
    }
    if (p.set) {
      ;(p.unchanged || []).forEach(function(x) { out.push({ text: x.loadoutName + " already has " + x.keys + " for that: left as it is.", tone: "dim" }) })
      out.push({ text: p.set.length > 1 ? "Sets these keys:" : "Sets this key:" })
      p.set.forEach(function(x) {
        out.push({ text: x.loadoutName + ":  " + x.keys + "  →  " + x.desc, tone: "add" })
        out.push({ text: x.command, tone: "code" })
        ;(x.replaces || []).forEach(function(r) { out.push({ text: "replaces its " + r.keys + " (" + r.desc + ")", tone: "warn" }) })
        if (x.clashLine) out.push({ text: "your bindings.lua binds " + x.keys + " too (line " + x.clashLine + "): left out until that's sorted", tone: "warn" })
      })
      var live = p.set.filter(function(x) { return x.activeNow }).length
      out.push({ text: live ? "The shell you're using gets its key now; the others when you swap to them (if you bring their keys)."
                            : "They work when you swap to those shells (if you bring their keys).", tone: "dim" })
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
    lines.push({ text: "Only my keys: no shell's keys at all, just your bindings.lua (nothing from the shell you're leaving stays on). Your bindings.lua isn't touched either way; the keys file is backed up first, and going back puts it back.", tone: "dim" })
    ask = {
      title: "Swap to " + name + "?",
      lines: lines,
      buttons: [
        { label: "Swap keys too", hint: "Enter", primary: true, act: function() { closeAsk(); doSwap(id, name, "--keys") } },
        { label: "Only my keys", hint: "k", key: Qt.Key_K, act: function() { closeAsk(); doSwap(id, name, "--keep-keys") } },
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
  function openKeys() { openBinds("file") }
  // the Keybinds panel (k): its tabs are actions | file | templates
  property string btab: "actions"
  function openBinds(tab) {
    if (!sel || sel.kind !== "loadout") { say("Pick one of your shells to give it keys."); return }
    keysFor = sel.id; mode = "keys"; marked = ({}); kcur0 = 0; kcur1 = 0
    kcol = sel.l.keys.length ? 0 : 1
    btab = tab || "actions"
    acur = 0; filter = ""; capturing = false; review = null
    if (keysFor !== importFor) { importText = ""; importFound = ""; importCheck = null; importFor = keysFor }
    refreshScan(); refreshActions(); refreshTemplates()
  }

  // ---- actions: what keys could do in this shell (its IPC calls) ----
  property var acts: []                 // from shell-swapper keys actions
  property string filter: ""
  readonly property var shownActs: {
    var f = filter.toLowerCase()
    var list = acts.filter(function(a) {
      return !f || (a.label + " " + a.call + " " + a.categoryName + " " + a.pluginName).toLowerCase().indexOf(f) >= 0
    })
    // its own keys first, then by plugin; headings between
    var out = [], last = null
    list.forEach(function(a) {
      if (a.pluginName !== last) { out.push({ head: true, label: a.pluginName }); last = a.pluginName }
      out.push(a)
    })
    return out
  }
  property int acur: 1
  readonly property var act: shownActs[acur] && !shownActs[acur].head ? shownActs[acur] : null
  function astep(d) {
    var i = acur + d
    while (i >= 0 && i < shownActs.length && shownActs[i].head) i += d
    if (i >= 0 && i < shownActs.length) acur = i
  }
  onShownActsChanged: { if (acur >= shownActs.length || (shownActs[acur] && shownActs[acur].head)) { acur = 0; astep(1) } }
  // the key being set: modifiers + one key
  property var mods: ({ SUPER: true, CTRL: false, ALT: false, SHIFT: false })
  property string bkey: ""
  property bool capturing: false
  property var argVals: ({})
  property var checkInfo: null
  readonly property string combo: {
    if (!bkey) return ""
    var m = ["SUPER", "CTRL", "ALT", "SHIFT"].filter(function(x) { return mods[x] })
    return m.concat([bkey]).join(" + ")
  }
  readonly property string fullCommand: {
    if (!act) return ""
    var c = act.command
    ;(act.args || []).forEach(function(p) {
      var v = argVals[p] || ""
      c = c.replace("'<" + p + ">'", v ? "'" + v.replace(/'/g, "'\\''") + "'" : "'<" + p + ">'")
    })
    return c
  }
  onActChanged: loadAct()
  function loadAct() {
    argVals = ({}); capturing = false
    if (act && act.bound) {
      var parts = act.bound.split(" + ")
      var m = { SUPER: false, CTRL: false, ALT: false, SHIFT: false }
      parts.slice(0, -1).forEach(function(x) { m[x] = true })
      mods = m; bkey = parts[parts.length - 1]
    } else { mods = { SUPER: true, CTRL: false, ALT: false, SHIFT: false }; bkey = "" }
    recheck()
  }
  onComboChanged: recheck()
  function recheck() {
    checkInfo = null
    if (!combo || !keysFor) return
    checkProc.command = ["python3", script, "keys", "check", keysFor, combo]
    checkProc.running = true
  }
  function toggleMod(m) { var x = Object.assign({}, mods); x[m] = !x[m]; mods = x }
  // a key press while capturing: its name as Hyprland writes it
  function keyName(e) {
    var k = e.key
    if (k >= Qt.Key_A && k <= Qt.Key_Z) return String.fromCharCode(k)
    if (k >= Qt.Key_0 && k <= Qt.Key_9) return String.fromCharCode(k)
    if (k >= Qt.Key_F1 && k <= Qt.Key_F24) return "F" + (k - Qt.Key_F1 + 1)
    var names = {}
    names[Qt.Key_Return] = "RETURN"; names[Qt.Key_Enter] = "RETURN"; names[Qt.Key_Space] = "SPACE"
    names[Qt.Key_Backspace] = "BACKSPACE"; names[Qt.Key_Tab] = "TAB"; names[Qt.Key_Delete] = "DELETE"
    names[Qt.Key_Slash] = "SLASH"; names[Qt.Key_Period] = "PERIOD"; names[Qt.Key_Comma] = "COMMA"
    names[Qt.Key_Minus] = "MINUS"; names[Qt.Key_Equal] = "EQUAL"; names[Qt.Key_Semicolon] = "SEMICOLON"
    names[Qt.Key_Apostrophe] = "APOSTROPHE"; names[Qt.Key_QuoteLeft] = "GRAVE"; names[Qt.Key_Backslash] = "BACKSLASH"
    names[Qt.Key_BracketLeft] = "BRACKETLEFT"; names[Qt.Key_BracketRight] = "BRACKETRIGHT"
    names[Qt.Key_Left] = "LEFT"; names[Qt.Key_Right] = "RIGHT"; names[Qt.Key_Up] = "UP"; names[Qt.Key_Down] = "DOWN"
    names[Qt.Key_Home] = "HOME"; names[Qt.Key_End] = "END"; names[Qt.Key_PageUp] = "PRIOR"; names[Qt.Key_PageDown] = "NEXT"
    names[Qt.Key_Print] = "PRINT"; names[Qt.Key_Insert] = "INSERT"
    return names[k] || ""
  }
  function capture(e) {
    var mk = { }
    mk[Qt.Key_Meta] = "SUPER"; mk[Qt.Key_Super_L] = "SUPER"; mk[Qt.Key_Super_R] = "SUPER"
    mk[Qt.Key_Control] = "CTRL"; mk[Qt.Key_Alt] = "ALT"; mk[Qt.Key_Shift] = "SHIFT"
    if (e.key === Qt.Key_Escape) { capturing = false; return }
    if (mk[e.key]) { var x = Object.assign({}, mods); x[mk[e.key]] = true; mods = x; return }
    var n = keyName(e)
    if (!n) { say("That key can't be used here.", true); return }
    // modifiers held with the press win; otherwise the ones picked above stay
    if (e.modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.ShiftModifier | Qt.MetaModifier))
      mods = { SUPER: !!(e.modifiers & Qt.MetaModifier), CTRL: !!(e.modifiers & Qt.ControlModifier),
               ALT: !!(e.modifiers & Qt.AltModifier), SHIFT: !!(e.modifiers & Qt.ShiftModifier) }
    bkey = n; capturing = false
  }
  function setKey() {
    if (!act) return
    if (!combo) { capturing = true; say("Press the key (pick SUPER / CTRL / ALT / SHIFT above)."); return }
    if (fullCommand.indexOf("'<") >= 0) { say("Fill in its arguments first.", true); return }
    var item = [{ loadout: keysFor, keys: combo, desc: act.label.charAt(0).toUpperCase() + act.label.slice(1), command: fullCommand }]
    planRun(["keys", "set-many", JSON.stringify(item)], function(ok) { if (ok) { say(combo + " is set."); refreshActions() } })
  }
  function clearKey() {
    if (!act || !act.bound) return
    var ks = keysLoadout ? keysLoadout.keys : []
    for (var i = 0; i < ks.length; i++) if (ks[i].keys === act.bound) {
      planRun(["keys", "remove", keysFor, String(i)], function(ok) { if (ok) { say("Cleared."); refreshActions() } })
      return
    }
  }
  function refreshActions() {
    if (!keysFor) return
    actsProc.command = ["python3", script, "keys", "actions", keysFor]
    actsProc.running = true
  }

  // ---- templates: the same key for the similar call in your other shells ----
  property var review: null             // {keys, label, categoryName, source, items: [{..., pick, on}], cur}
  property var tmpls: []
  property int tcur: 0
  function startTemplate() {
    if (!act) return
    var k = combo || act.bound
    if (!k) { say("Give it a key first, then make it a template.", true); return }
    if (fullCommand.indexOf("'<") >= 0) { say("Fill in its arguments first.", true); return }
    proposeProc.label = act.label
    proposeProc.command = ["python3", script, "keys", "propose", keysFor, k, fullCommand]
    proposeProc.running = true
  }
  function reviewMove(d) { if (review) { var r = Object.assign({}, review); r.cur = Math.max(0, Math.min(r.items.length - 1, r.cur + d)); review = r } }
  function reviewCycle(d) {
    if (!review || !review.items.length) return
    var r = Object.assign({}, review), it = Object.assign({}, r.items[r.cur])
    var n = it.candidates.length
    // -1 = leave this shell alone
    it.pick = it.pick + d
    if (it.pick >= n) it.pick = -1
    if (it.pick < -1) it.pick = n - 1
    it.on = it.pick >= 0
    var items = r.items.slice(); items[r.cur] = it; r.items = items; review = r
  }
  function reviewToggle() {
    if (!review || !review.items.length) return
    var r = Object.assign({}, review), it = Object.assign({}, r.items[r.cur])
    if (it.pick < 0 && it.candidates.length) it.pick = 0
    it.on = !it.on && it.pick >= 0
    var items = r.items.slice(); items[r.cur] = it; r.items = items; review = r
  }
  function reviewApply() {
    if (!review) return
    var rv = review
    // this shell's own key, unless it's already exactly that
    var items = act && act.bound === rv.keys && act.command === rv.source ? []
      : [{ loadout: keysFor, keys: rv.keys, desc: rv.desc, command: rv.source }]
    rv.items.forEach(function(it) {
      if (it.on && it.pick >= 0) {
        var c = it.candidates[it.pick]
        items.push({ loadout: it.loadout, keys: rv.keys, desc: rv.desc, command: c.command })
      }
    })
    review = null
    if (!items.length) { say("Nothing to set: no other shell was picked."); return }
    planRun(["keys", "set-many", JSON.stringify(items)], function(ok) {
      if (!ok) return
      var binds = items.map(function(x) { return { loadout: x.loadout, command: x.command } })
      run(["keys", "template-save", rv.desc, JSON.stringify({ keys: rv.keys, category: rv.category, binds: binds })])
      say(rv.keys + " now does " + rv.desc.toLowerCase() + " in " + items.length + " shell" + (items.length === 1 ? "" : "s") + ".")
      refreshActions(); refreshTemplates()
    })
  }
  function refreshTemplates() { if (!tmplProc.running) tmplProc.running = true }

  // ---- import: binds pasted from the shell's repo ----
  property string importText: ""
  property string importFor: ""
  property var importCheck: null        // the plan --check returns: rows + skipped lines
  property string importFound: ""       // where "find in its repo" looked
  onImportTextChanged: importTimer.restart()
  Timer { id: importTimer; interval: 350; onTriggered: sw.checkImport() }
  function checkImport() {
    if (!keysFor || !importText.trim()) { importCheck = null; return }
    importCheckProc.command = ["python3", script, "keys", "import", keysFor, "--check", "--text", importText]
    importCheckProc.running = true
  }
  readonly property int importCount: importCheck ? importCheck["import"].filter(function(r) { return r.status === "new" || r.status === "replace" }).length : 0
  function findInRepo() { if (keysFor && !findProc.running) { findProc.command = ["python3", script, "keys", "find", keysFor]; findProc.running = true } }
  function doImport() {
    if (!importCount) { say(importText.trim() ? "Nothing new to import." : "Paste some binds first (or f: find them in its repo).", true); return }
    planRun(["keys", "import", keysFor, "--text", importText], function(ok) {
      if (ok) { say("Imported into " + (keysLoadout ? keysLoadout.name : keysFor) + "."); importText = ""; importCheck = null; refreshActions() }
    })
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

  // ---- pictures of the bars ----
  property bool showPicture: true        // the picture, or the sketch (v)
  property var hoverShot: null           // {shot, name, y}: a loadout under the mouse
  property bool pictureOnClose: false    // after a save: picture it once the picker's gone
  function isForeign(r, id) { return !!r && r.kind === "loadout" && !!r.l.widgets && r.l.widgets.foreign.some(function(w) { return w.id === id }) }
  function isMissing(r, id) { return !!r && r.kind === "loadout" && !!r.l.widgets && r.l.widgets.missing.some(function(w) { return w.id === id }) }
  function takeOffForeign(lid, ids) {
    planRun(["take-off", lid].concat(ids || []), function(ok) { if (ok) say("Taken off. It's in effect the next time you swap to it.") })
  }
  function hasShot(l) { return !!l && !!l.preview && typeof l.preview === "object" && !!l.preview.path }
  function retakePicture() {
    if (!sel || sel.kind !== "loadout" || !sel.l.active) { say("Only the shell on screen can be pictured: swap to it first."); return }
    // the picker has to be out of the way first
    Quickshell.execDetached(["sh", "-c", "sleep 1.2; exec python3 \"$0\" preview --auto", script])
    say("Picturing it…")
    if (closeRequest) closeRequest()
  }
  function pictureIfAsked() {
    if (!pictureOnClose) return
    pictureOnClose = false
    Quickshell.execDetached(["sh", "-c", "sleep 1.2; exec python3 \"$0\" preview --auto", script])
  }
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
    sw.takeKeys()
    if (!text) return
    if (mode === "save") run(["save", text], function(ok) { if (ok) { say("Saved the current shell as " + text); pictureOnClose = true } })
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
  onActiveChanged: { if (active) refresh(); else pictureIfAsked() }

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
    id: actsProc
    stdout: StdioCollector { onStreamFinished: { try { sw.acts = JSON.parse(text).actions || [] } catch (e) {} } }
    stderr: StdioCollector { onStreamFinished: if (text.trim() !== "") sw.say(text.trim(), true) }
  }
  Process {
    id: checkProc
    stdout: StdioCollector { onStreamFinished: { try { sw.checkInfo = JSON.parse(text) } catch (e) { sw.checkInfo = null } } }
  }
  Process {
    id: proposeProc
    property string label: ""
    stdout: StdioCollector {
      onStreamFinished: {
        var r = null
        try { r = JSON.parse(text) } catch (e) { return }
        var lbl = proposeProc.label
        sw.review = {
          keys: r.keys, source: r.command, category: r.category,
          categoryName: r.categoryName || "",
          desc: (r.categoryName || lbl).replace(/^./, function(c) { return c.toUpperCase() }),
          label: lbl, cur: 0,
          items: r.proposals.map(function(p) {
            return { loadout: p.loadout, loadoutName: p.loadoutName, candidates: p.candidates, clash: p.clash,
                     pick: p.candidates.length ? 0 : -1, on: p.candidates.length > 0 }
          })
        }
      }
    }
    stderr: StdioCollector { onStreamFinished: if (text.trim() !== "") sw.say(text.trim(), true) }
  }
  Process {
    id: importCheckProc
    stdout: StdioCollector { onStreamFinished: { try { sw.importCheck = JSON.parse(text) } catch (e) { sw.importCheck = null } } }
    stderr: StdioCollector { onStreamFinished: if (text.trim() !== "") sw.say(text.trim(), true) }
  }
  Process {
    id: findProc
    stdout: StdioCollector {
      onStreamFinished: {
        var r = null
        try { r = JSON.parse(text) } catch (e) { return }
        sw.importFound = (r.files || []).length ? (r.binds.length + " found in " + r.files.map(function(f) { return f.split("/").pop() }).join(", "))
                                                 : "none found in " + (r.roots || []).map(function(x) { return x.replace(Quickshell.env("HOME") || "", "~") }).slice(0, 2).join(", ")
                                                   + ((r.roots || []).length > 2 ? " and " + ((r.roots || []).length - 2) + " more" : "")
        if (r.text) sw.importText = (sw.importText.trim() ? sw.importText.trim() + "\n" : "") + r.text
      }
    }
    stderr: StdioCollector { onStreamFinished: if (text.trim() !== "") sw.say(text.trim(), true) }
  }
  Process {
    id: tmplProc
    command: ["python3", sw.script, "keys", "templates"]
    stdout: StdioCollector { onStreamFinished: { try { sw.tmpls = JSON.parse(text).templates || [] } catch (e) {} } }
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
  // Keys go to this item, not the FocusScope itself: forceActiveFocus() on a
  // scope hands focus back to whichever child last had it (the search box,
  // say), so typing would land there instead.
  focus: true
  Item {
    id: keyCatcher
    focus: true
    Keys.onPressed: e => sw.onKey(e)
  }
  function takeKeys() { keyCatcher.forceActiveFocus() }
  function onKey(e) {
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
    if (mode === "keys" && capturing) { capture(e); e.accepted = true; return }
    if (mode === "keys" && review) {
      if (k === Qt.Key_Up) reviewMove(-1)
      else if (k === Qt.Key_Down) reviewMove(1)
      else if (k === Qt.Key_Left) reviewCycle(-1)
      else if (k === Qt.Key_Right) reviewCycle(1)
      else if (k === Qt.Key_Space) reviewToggle()
      else if (k === Qt.Key_Return || k === Qt.Key_Enter) reviewApply()
      else if (k === Qt.Key_Escape) review = null
      e.accepted = true
      return
    }
    if (mode === "keys" && (k === Qt.Key_1 || k === Qt.Key_2 || k === Qt.Key_3 || k === Qt.Key_4)) {
      btab = k === Qt.Key_1 ? "actions" : k === Qt.Key_2 ? "file" : k === Qt.Key_3 ? "templates" : "import"
      e.accepted = true
      return
    }
    if (mode === "keys" && btab === "actions") {
      if (k === Qt.Key_Down) astep(1)
      else if (k === Qt.Key_Up) astep(-1)
      else if (k === Qt.Key_Return || k === Qt.Key_Enter) setKey()
      else if (k === Qt.Key_C) { if (act) capturing = true }
      else if (k === Qt.Key_X) clearKey()
      else if (k === Qt.Key_T) startTemplate()
      else if (k === Qt.Key_Slash) { filterInput.forceActiveFocus() }
      else if (k === Qt.Key_Escape) { if (filter) filter = ""; else mode = "list" }
      else return
      e.accepted = true
      return
    }
    if (mode === "keys" && btab === "import") {
      if (k === Qt.Key_F) findInRepo()
      else if (k === Qt.Key_I) doImport()
      else if (k === Qt.Key_E || k === Qt.Key_Return || k === Qt.Key_Enter) importEdit.forceActiveFocus()
      else if (k === Qt.Key_Escape) mode = "list"
      else return
      e.accepted = true
      return
    }
    if (mode === "keys" && btab === "templates") {
      if (k === Qt.Key_Down) tcur = Math.min(tmpls.length - 1, tcur + 1)
      else if (k === Qt.Key_Up) tcur = Math.max(0, tcur - 1)
      else if (k === Qt.Key_D) { if (tmpls[tcur]) run(["keys", "template-delete", tmpls[tcur].name], function() { refreshTemplates() }) }
      else if (k === Qt.Key_Escape) mode = "list"
      else return
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
    else if (k === Qt.Key_Up) step(-1)
    else if (k === Qt.Key_K) openBinds("actions")
    else if (k === Qt.Key_V) showPicture = !showPicture
    else if (k === Qt.Key_P) retakePicture()
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

  // A bar's picture: scaled down whole it'd be a sliver, so it's shown in
  // pieces. A top / bottom bar: three strips (left, centre, right) stacked,
  // each as wide as there's room. A bar on the left / right: cut into pieces
  // about 240 px tall (2 to 8), side by side, near full size.
  component BarShot: Item {
    id: bs
    property var shot: null
    property real maxPiece: 240           // how tall a piece of a side bar may be
    readonly property bool vertical: !!shot && (shot.position === "left" || shot.position === "right")
    readonly property int pieces: !shot ? 0 : vertical ? Math.max(2, Math.min(10, Math.ceil(shot.h / maxPiece))) : 3
    readonly property real pw: !shot ? 1 : vertical ? shot.w : shot.w / pieces
    readonly property real ph: !shot ? 1 : vertical ? shot.h / pieces : shot.h
    readonly property real k: !shot ? 1 : vertical ? Math.min(1.2, (width / pieces - 4) / pw, maxPiece / ph) : width / pw
    implicitHeight: !shot ? 0 : vertical ? ph * k : (ph * k + 4) * pieces - 4
    Repeater {
      model: bs.pieces
      Rectangle {
        required property int index
        x: bs.vertical ? index * (bs.pw * bs.k + 4) : 0
        y: bs.vertical ? 0 : index * (bs.ph * bs.k + 4)
        width: bs.pw * bs.k
        height: bs.ph * bs.k
        radius: 4
        clip: true
        color: "transparent"
        border.color: sw.line
        Image {
          anchors.fill: parent
          source: bs.shot ? "file://" + bs.shot.path + "?t=" + bs.shot.t : ""
          cache: false
          asynchronous: true
          smooth: true
          fillMode: Image.Stretch
          sourceClipRect: !bs.shot ? Qt.rect(0, 0, 0, 0)
            : bs.vertical ? Qt.rect(0, parent.index * bs.ph, bs.pw, bs.ph) : Qt.rect(parent.index * bs.pw, 0, bs.pw, bs.ph)
        }
      }
    }
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
    property bool foreign: false          // another shell's widget
    property bool missing: false          // its plugin isn't installed
    height: sw.px(18)
    width: chipText.implicitWidth + 12
    radius: height / 2
    color: foreign ? sw.tint(sw.urgent, 0.18) : sw.tint(sw.fg, 0.08)
    border.color: foreign ? sw.urgent : sw.line
    opacity: missing ? 0.45 : 1
    Text { id: chipText; anchors.centerIn: parent; text: parent.label; textFormat: Text.PlainText; color: parent.foreign ? sw.urgent : sw.dim; font.strikeout: parent.missing; font.family: sw.font; font.pixelSize: sw.px(9) }
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
    text: sw.mode === "keys" ? (sw.btab === "actions" ? "1-4 tabs  ↑↓ pick  / search  c key  ⏎ set  x clear  t other shells  esc back"
        : sw.btab === "templates" ? "1-4 tabs  ↑↓ pick  d forget  esc back"
        : sw.btab === "import" ? "1-4 tabs  e paste box  f find in its repo  i import  esc back"
        : "1-4 tabs  tab side  ↑↓ pick  space mark  m move in  d take out  o tidy  esc back")
          : "↑↓ pick  ⏎ swap  k keybinds  v picture/sketch  p picture it  s save  r rename  d delete  esc close"
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
    // a shell found since last time: what the check found, and what to do
    Rectangle {
      readonly property var n: (sw.info.notices || []).length ? sw.info.notices[0] : null
      visible: !!n && sw.mode === "list"
      width: parent.width
      height: visible ? noteCol.implicitHeight + 16 : 0
      radius: sw.rad
      color: sw.tint(sw.accent, 0.10)
      border.color: sw.accent
      Column {
        id: noteCol
        x: 12; y: 8; width: parent.width - 24
        spacing: 4
        readonly property var n: parent.n
        Text {
          width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText
          text: !noteCol.n ? "" : noteCol.n.kind === "foreign" ? "Another shell's widget on " + noteCol.n.name
            : "New shell: " + noteCol.n.name + (noteCol.n.captured ? " (saved as a loadout, as its installer set it up)" : "")
          color: sw.fg; font.family: sw.font; font.pixelSize: sw.px(12); font.bold: true
        }
        Repeater {
          model: noteCol.n ? noteCol.n.advice : []
          Text { required property string modelData; width: noteCol.width; wrapMode: Text.Wrap; textFormat: Text.PlainText; text: "·  " + modelData; color: sw.dim; font.family: sw.font; font.pixelSize: sw.px(11) }
        }
        Row {
          spacing: 6
          Btn {
            visible: !!noteCol.n && !!noteCol.n.loadout && (noteCol.n.foreignWidgets.length + noteCol.n.foreignPlugins.length) > 0
            text: noteCol.n && noteCol.n.kind === "foreign" ? "Take it off" : "Remove other shells' widgets"; on: true
            onClicked: {
              var n = noteCol.n
              var args = n.kind === "foreign" ? ["take-off", n.loadout].concat(n.foreignWidgets) : ["strip-foreign", n.loadout]
              sw.planRun(args, function(ok) { if (ok) { sw.say("Taken off. Swap to it (or re-apply) to see it."); sw.run(["notice-dismiss", n.suite]) } })
            }
          }
          Btn { text: "Got it"; onClicked: sw.run(["notice-dismiss", noteCol.n.suite]) }
        }
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
        Btn {
          text: "Keep"; on: true
          onClicked: sw.run(["confirm"], function(ok) { if (ok) { sw.pictureOnClose = true; if (sw.closeRequest) sw.closeRequest() } })
        }
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
            text: row.modelData.kind === "suite" ? (row.su.canStartFresh === false ? "set it up with its installer first · " : "its default layout · ") + row.su.id
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
          onClicked: { sw.cursor = row.index; sw.takeKeys() }
          onDoubleClicked: { sw.cursor = row.index; sw.swap() }
          // a loadout's picture while the mouse is on it (unless it's the one picked)
          onContainsMouseChanged: {
            var l = row.modelData.l
            if (containsMouse && row.modelData.kind === "loadout" && sw.hasShot(l) && row.index !== sw.cursor)
              sw.hoverShot = { shot: l.preview, name: l.name, y: row.mapToItem(sw, 0, 0).y }
            else if (!containsMouse && sw.hoverShot && sw.hoverShot.name === (l ? l.name : ""))
              sw.hoverShot = null
          }
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
        id: detailCol
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
            ? (detail.r.s.description || "") + (detail.r.s.canStartFresh === false
                ? "\n\nShell-Swapper doesn't know a layout for it. Set it up with its own installer (that makes it your bar), then save it here with s."
                : detail.r.s.hasLoadout ? "" : "\n\nNo loadout for it yet: swapping makes one, with its default layout.")
            : sw.suiteName(detail.r.l.suite) + " · bar at the " + detail.r.l.position
              + (detail.r.l.note ? "\n" + detail.r.l.note : "")
          color: sw.dim; font.family: sw.font; font.pixelSize: sw.px(11)
        }

        // a sketch of its bar: what sits left, centre and right
        Row {
          visible: !!detail.lay
          spacing: 10
          Heading { anchors.verticalCenter: parent.verticalCenter; text: "ON ITS BAR" }
          Btn {
            visible: !!detail.r && detail.r.kind === "loadout" && sw.hasShot(detail.r.l)
            text: sw.showPicture ? "sketch" : "picture"; hint: "v"
            onClicked: sw.showPicture = !sw.showPicture
          }
          Btn {
            visible: !!detail.r && detail.r.kind === "loadout" && detail.r.l.active
            text: sw.hasShot(detail.r ? detail.r.l : null) ? "retake picture" : "take its picture"; hint: "p"
            onClicked: sw.retakePicture()
          }
        }
        BarShot {
          visible: !!detail.r && detail.r.kind === "loadout" && sw.hasShot(detail.r.l) && sw.showPicture
          maxPiece: 150   // shorter here, so its keys below keep room
          width: parent.width
          height: visible ? implicitHeight : 0
          shot: detail.r && detail.r.kind === "loadout" && sw.hasShot(detail.r.l) ? detail.r.l.preview : null
        }
        Text {
          visible: !!detail.r && detail.r.kind === "loadout" && !sw.hasShot(detail.r.l)
          width: parent.width; wrapMode: Text.Wrap
          text: "No picture yet: it's taken when this shell is on screen (after you keep a swap to it)."
          color: sw.faint; font.family: sw.font; font.pixelSize: sw.px(10)
        }
        Rectangle {
          visible: !!detail.lay && !(detail.r && detail.r.kind === "loadout" && sw.hasShot(detail.r.l) && sw.showPicture)
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
                    Chip {
                      required property string modelData
                      label: sw.shortId(modelData)
                      foreign: sw.isForeign(detail.r, modelData)
                      missing: sw.isMissing(detail.r, modelData)
                    }
                  }
                }
              }
            }
          }
        }

        // other shells' widgets on this bar (and ones not installed)
        Row {
          readonly property var wr: detail.r && detail.r.kind === "loadout" ? detail.r.l.widgets : null
          visible: !!wr && (wr.foreign.length > 0 || wr.missing.length > 0)
          width: parent.width
          spacing: 8
          Text {
            width: parent.width - (takeOffBtn.visible ? takeOffBtn.width + 8 : 0)
            anchors.verticalCenter: parent.verticalCenter
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
            readonly property var wr: parent.wr
            text: !wr ? "" : [wr.foreign.length ? "Other shells' widgets on this bar: " + wr.foreign.map(function(w) { return sw.shortId(w.id) + " (" + w.ownerName + ")" }).join(", ") : "",
                              wr.missing.length ? "Not installed: " + wr.missing.map(function(w) { return w.id }).join(", ") : ""]
                             .filter(function(x) { return x }).join(".  ")
            color: sw.urgent; font.family: sw.font; font.pixelSize: sw.px(10)
          }
          Btn {
            id: takeOffBtn
            visible: !!parent.wr && parent.wr.foreign.length > 0
            text: "Take them off"
            onClicked: sw.takeOffForeign(detail.r.id)
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
          Btn { visible: !!detail.r && detail.r.kind === "loadout"; text: "Keybinds"; hint: "k"; onClicked: sw.openBinds("actions") }
          Btn { visible: !!detail.r && detail.r.kind === "loadout"; text: "Rename"; hint: "r"; onClicked: sw.startRename() }
          Btn {
            visible: !!detail.r && detail.r.kind === "loadout"
            text: sw.armed !== "" && detail.r && sw.armed === detail.r.id ? "Delete it?" : "Delete"
            hint: "d"; danger: sw.armed !== "" && !!detail.r && sw.armed === detail.r.id
            onClicked: sw.del()
          }
        }

        // its own keys (they come with it when you swap, if you say so);
        // the list below scrolls, so the buttons above stay in view
        Heading { visible: !!detail.r && detail.r.kind === "loadout"; text: "ITS KEYS" + (detail.r && detail.r.kind === "loadout" && detail.r.l.keys.length ? "  (" + detail.r.l.keys.length + ")" : "") }
        Text {
          visible: !!detail.r && detail.r.kind === "loadout" && detail.r.l.keys.length === 0
          width: parent.width
          wrapMode: Text.Wrap
          text: "None yet: your bindings.lua keys work the same in every shell. Press k to give this shell keys of its own."
          color: sw.faint; font.family: sw.font; font.pixelSize: sw.px(11)
        }
      }
      ListView {
        id: detailKeys
        objectName: "detailKeys"
        visible: !!detail.r && detail.r.kind === "loadout" && detail.r.l.keys.length > 0
        anchors { top: detailCol.bottom; topMargin: 6; bottom: parent.bottom; left: parent.left; right: parent.right; rightMargin: 8 }
        clip: true
        spacing: 3
        boundsBehavior: Flickable.StopAtBounds
        model: detail.r && detail.r.kind === "loadout" ? detail.r.l.keys : []
        delegate: Row {
          required property var modelData
          spacing: 10
          Text { width: sw.px(170); elide: Text.ElideRight; textFormat: Text.PlainText; text: modelData.keys; color: modelData.clashLine ? sw.urgent : sw.fg; font.family: sw.font; font.pixelSize: sw.px(11) }
          Text {
            width: detailKeys.width - sw.px(180)
            elide: Text.ElideRight
            textFormat: Text.PlainText
            text: modelData.desc + (modelData.clashLine ? "   (also in bindings.lua, line " + modelData.clashLine + ": left out)" : "")
            color: modelData.clashLine ? sw.urgent : sw.dim; font.family: sw.font; font.pixelSize: sw.px(11)
          }
        }
      }
      // a slim scrollbar when the keys don't all fit
      Rectangle {
        visible: detailKeys.visible && detailKeys.contentHeight > detailKeys.height
        x: detail.width - 3
        y: detailKeys.y + detailKeys.visibleArea.yPosition * detailKeys.height
        width: 3
        height: Math.max(sw.px(16), detailKeys.visibleArea.heightRatio * detailKeys.height)
        radius: 1.5
        color: sw.tint(sw.fg, 0.3)
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
      Row {
        spacing: 14
        Text {
          anchors.verticalCenter: parent.verticalCenter
          textFormat: Text.PlainText
          text: "Keybinds for " + (sw.keysLoadout ? sw.keysLoadout.name : "")
          color: sw.fg; font.family: sw.font; font.pixelSize: sw.px(15); font.bold: true
        }
        Repeater {
          model: [["actions", "1", "Its actions"], ["file", "2", "From bindings.lua"], ["templates", "3", "Templates"], ["import", "4", "Import"]]
          Btn {
            required property var modelData
            text: modelData[2]; hint: modelData[1]
            on: sw.btab === modelData[0]
            onClicked: { sw.btab = modelData[0]; sw.takeKeys() }
          }
        }
      }
      Text {
        width: parent.width
        wrapMode: Text.Wrap
        text: sw.btab === "actions"
          ? "What this shell can do (its own IPC calls and panels). Pick one, give it a key, and press t to offer the same key "
            + "for the similar call in your other shells: you review each one before anything is set."
          : sw.btab === "templates"
          ? "Keys you've set across shells at once. Each shell keeps its own call; d forgets a template (the keys stay)."
          : sw.btab === "import"
          ? "Paste binds from this shell's repo (its README, docs, install notes): Omarchy's o.bind(...) lines or Hyprland's "
            + "bind = MODS, KEY, exec, command. f looks through its own files for you. Check what's understood on the right, then i to import."
          : "Keys you move in here only work while this is your shell (and only if you say so when you swap). "
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

    // ---- tab 1: its actions ----
    Item {
      visible: sw.btab === "actions"
      anchors { top: keysHead.bottom; topMargin: 12; bottom: parent.bottom; left: parent.left; right: parent.right }
      Column {
        id: aLeft
        width: Math.round(parent.width * 0.48)
        height: parent.height
        spacing: 6
        Rectangle {
          width: parent.width
          height: sw.px(28)
          radius: sw.rad
          color: sw.rowFill
          border.color: filterInput.activeFocus ? sw.accent : sw.line
          Text { visible: !filterInput.text; x: 10; anchors.verticalCenter: parent.verticalCenter; text: "/ search actions"; color: sw.faint; font.family: sw.font; font.pixelSize: sw.px(11) }
          TextInput {
            id: filterInput
            x: 10; width: parent.width - 20
            anchors.verticalCenter: parent.verticalCenter
            text: sw.filter
            onTextChanged: sw.filter = text
            color: sw.fg; font.family: sw.font; font.pixelSize: sw.px(11)
            Keys.onReturnPressed: sw.takeKeys()
            Keys.onEscapePressed: { text = ""; sw.takeKeys() }
            Keys.onDownPressed: { sw.takeKeys(); sw.astep(1) }
          }
        }
        ListView {
          id: aList
          width: parent.width
          height: parent.height - y
          clip: true
          spacing: 3
          model: sw.shownActs
          currentIndex: sw.acur
          onCurrentIndexChanged: positionViewAtIndex(currentIndex, ListView.Contain)
          boundsBehavior: Flickable.StopAtBounds
          delegate: Item {
            id: aslot
            required property var modelData
            required property int index
            width: aList.width
            height: modelData.head ? sw.px(24) : sw.px(38)
            Heading { visible: !!aslot.modelData.head; anchors.bottom: parent.bottom; anchors.bottomMargin: 3; text: String(aslot.modelData.label || "").toUpperCase() }
            Rectangle {
              visible: !aslot.modelData.head
              anchors.fill: parent
              radius: sw.rad
              readonly property bool cur: aslot.index === sw.acur
              color: cur ? sw.cursorFill : aMouse.containsMouse ? sw.hoverFill : sw.rowFill
              border.color: cur ? sw.accent : "transparent"
              Column {
                x: 10; width: parent.width - 20 - aBadge.width
                anchors.verticalCenter: parent.verticalCenter
                Text { width: parent.width; elide: Text.ElideRight; textFormat: Text.PlainText; text: aslot.modelData.label || ""; color: sw.fg; font.family: sw.font; font.pixelSize: sw.px(12) }
                Text {
                  width: parent.width; elide: Text.ElideRight; textFormat: Text.PlainText
                  text: (aslot.modelData.categoryName ? aslot.modelData.categoryName + "  ·  " : "") + (aslot.modelData.call || "")
                  color: sw.faint; font.family: sw.font; font.pixelSize: sw.px(9)
                }
              }
              Text {
                id: aBadge
                anchors { right: parent.right; rightMargin: 10; verticalCenter: parent.verticalCenter }
                textFormat: Text.PlainText
                text: aslot.modelData.bound || ""
                color: sw.accent; font.family: sw.font; font.pixelSize: sw.px(10)
              }
              MouseArea { id: aMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: { sw.acur = aslot.index; sw.takeKeys() } }
            }
          }
        }
      }

      // the key editor for the one picked
      Column {
        anchors { left: aLeft.right; leftMargin: 18; right: parent.right }
        spacing: 9
        visible: !!sw.act
        Text { width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText; text: sw.act ? sw.act.label.charAt(0).toUpperCase() + sw.act.label.slice(1) : ""; color: sw.fg; font.family: sw.font; font.pixelSize: sw.px(15); font.bold: true }
        Text { width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText; text: sw.act ? sw.act.pluginName + (sw.act.categoryName ? "  ·  " + sw.act.categoryName : "") : ""; color: sw.dim; font.family: sw.font; font.pixelSize: sw.px(11) }
        Text { visible: !!sw.act && !!sw.act.comment; width: parent.width; wrapMode: Text.Wrap; maximumLineCount: 3; elide: Text.ElideRight; textFormat: Text.PlainText; text: sw.act ? sw.act.comment : ""; color: sw.faint; font.family: sw.font; font.pixelSize: sw.px(10) }
        // its arguments, if it takes any
        Repeater {
          model: sw.act ? sw.act.args : []
          Row {
            required property string modelData
            spacing: 8
            Text { width: sw.px(80); anchors.verticalCenter: parent.verticalCenter; textFormat: Text.PlainText; text: modelData; color: sw.dim; font.family: sw.font; font.pixelSize: sw.px(11) }
            Rectangle {
              width: sw.px(220); height: sw.px(26); radius: sw.rad; color: sw.rowFill
              border.color: argIn.activeFocus ? sw.accent : sw.line
              TextInput {
                id: argIn
                x: 8; width: parent.width - 16; anchors.verticalCenter: parent.verticalCenter
                color: sw.fg; font.family: sw.font; font.pixelSize: sw.px(11)
                onTextChanged: { var v = Object.assign({}, sw.argVals); v[modelData] = text; sw.argVals = v }
                Keys.onReturnPressed: sw.takeKeys()
                Keys.onEscapePressed: sw.takeKeys()
              }
            }
          }
        }
        Text { width: parent.width; wrapMode: Text.WrapAnywhere; textFormat: Text.PlainText; text: sw.fullCommand; color: sw.faint; font.family: sw.font; font.pixelSize: sw.px(10) }

        Heading { text: "ITS KEY" }
        Row {
          spacing: 6
          Repeater {
            model: ["SUPER", "CTRL", "ALT", "SHIFT"]
            Btn { required property string modelData; text: modelData; on: !!sw.mods[modelData]; onClicked: sw.toggleMod(modelData) }
          }
          Text { anchors.verticalCenter: parent.verticalCenter; text: "+"; color: sw.faint; font.family: sw.font; font.pixelSize: sw.px(12) }
          // press a key here
          Rectangle {
            width: Math.max(sw.px(90), capText.implicitWidth + 20)
            height: sw.px(28)
            radius: sw.rad
            color: sw.capturing ? sw.tint(sw.accent, 0.18) : capMouse.containsMouse ? sw.hoverFill : sw.rowFill
            border.color: sw.capturing ? sw.accent : sw.line
            Text {
              id: capText
              anchors.centerIn: parent
              text: sw.capturing ? "press a key…" : (sw.bkey || "set (c)")
              color: sw.capturing || sw.bkey ? sw.fg : sw.faint
              font.family: sw.font; font.pixelSize: sw.px(11); font.bold: !!sw.bkey && !sw.capturing
            }
            MouseArea { id: capMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: { sw.capturing = true; sw.takeKeys() } }
          }
        }
        // who has that key now
        Text {
          width: parent.width
          wrapMode: Text.Wrap
          visible: !!sw.combo
          textFormat: Text.PlainText
          readonly property var c: sw.checkInfo
          text: !c ? "" : c.bindings.length ? sw.combo + " is bound in your bindings.lua (line " + c.bindings[0].line + ", " + c.bindings[0].desc + "): it would be left out until that's moved or removed."
            : c.loadout.length && sw.act && sw.act.bound !== sw.combo ? sw.combo + " is this shell's key for " + c.loadout[0].desc + " now: it'd do this instead."
            : c.omarchy ? sw.combo + " is Omarchy's key for \"" + c.omarchy + "\": this replaces it while this shell is in use."
            : sw.act && sw.act.bound === sw.combo ? sw.combo + " already does this." : sw.combo + " is free."
          color: c && c.bindings.length ? sw.urgent : c && (c.loadout.length || c.omarchy) ? sw.accent : sw.dim
          font.family: sw.font; font.pixelSize: sw.px(11)
        }
        Flow {
          width: parent.width
          spacing: 6
          Btn { text: sw.act && sw.act.bound ? "Change key" : "Set key"; hint: "Enter"; on: true; onClicked: sw.setKey() }
          Btn { visible: !!sw.act && !!sw.act.bound; text: "Clear key"; hint: "x"; onClicked: sw.clearKey() }
          Btn { text: "Same key in other shells…"; hint: "t"; onClicked: sw.startTemplate() }
        }
      }
    }

    // ---- tab 4: import ----
    Item {
      visible: sw.btab === "import"
      anchors { top: keysHead.bottom; topMargin: 12; bottom: parent.bottom; left: parent.left; right: parent.right }
      Column {
        id: impLeft
        width: Math.round(parent.width * 0.5)
        height: parent.height
        spacing: 6
        Row {
          spacing: 6
          Btn { text: "Find in its repo"; hint: "f"; onClicked: sw.findInRepo() }
          Btn { text: "Clear"; onClicked: { sw.importText = ""; sw.importFound = "" } }
          Text { anchors.verticalCenter: parent.verticalCenter; textFormat: Text.PlainText; text: sw.importFound; color: sw.faint; font.family: sw.font; font.pixelSize: sw.px(10) }
        }
        Rectangle {
          width: parent.width
          height: parent.height - y - impBtn.height - 6
          radius: sw.rad
          color: sw.rowFill
          border.color: importEdit.activeFocus ? sw.accent : sw.line
          Flickable {
            id: impFlick
            anchors.fill: parent
            anchors.margins: 8
            clip: true
            contentWidth: width
            contentHeight: importEdit.implicitHeight
            boundsBehavior: Flickable.StopAtBounds
            TextEdit {
              id: importEdit
              width: impFlick.width
              text: sw.importText
              onTextChanged: if (text !== sw.importText) sw.importText = text
              wrapMode: TextEdit.WrapAnywhere
              selectByMouse: true
              persistentSelection: true
              color: sw.fg
              selectionColor: sw.tint(sw.accent, 0.4)
              selectedTextColor: sw.fg
              font.family: sw.font
              font.pixelSize: sw.px(11)
              Keys.onEscapePressed: sw.takeKeys()
              Text {
                visible: !importEdit.text && !importEdit.activeFocus
                text: "Paste binds here (Ctrl+V), e.g.\n\no.bind(\"SUPER + R\", \"Launcher\", \"omarchy-shell my-shell launcher\")\nbind = SUPER ALT, C, exec, omarchy-shell my-shell dashboard"
                color: sw.faint; font.family: sw.font; font.pixelSize: sw.px(11)
              }
            }
          }
          MouseArea { anchors.fill: parent; acceptedButtons: Qt.NoButton; cursorShape: Qt.IBeamCursor }
        }
        Btn {
          id: impBtn
          text: sw.importCount ? "Import " + sw.importCount + " key" + (sw.importCount === 1 ? "" : "s") : "Import"
          hint: "i"; on: sw.importCount > 0
          onClicked: sw.doImport()
        }
      }
      // what was understood
      Column {
        anchors { left: impLeft.right; leftMargin: 18; right: parent.right }
        height: parent.height
        spacing: 5
        Heading { text: "WHAT IT FOUND" }
        Text {
          visible: !sw.importCheck || sw.importCheck["import"].length === 0
          width: parent.width; wrapMode: Text.Wrap
          text: sw.importText.trim() ? "No binds in that yet." : "Nothing pasted yet."
          color: sw.faint; font.family: sw.font; font.pixelSize: sw.px(11)
        }
        ListView {
          width: parent.width
          height: Math.min(contentHeight, parent.height * 0.62)
          clip: true
          spacing: 4
          model: sw.importCheck ? sw.importCheck["import"] : []
          delegate: Rectangle {
            id: irow
            required property var modelData
            readonly property bool taken: modelData.status === "new" || modelData.status === "replace"
            width: ListView.view.width
            height: icol.implicitHeight + 10
            radius: sw.rad
            color: sw.rowFill
            Column {
              id: icol
              x: 10; y: 5; width: parent.width - 20
              Text {
                width: parent.width; elide: Text.ElideRight; textFormat: Text.PlainText
                text: (irow.modelData.status === "new" ? "+  " : irow.modelData.status === "replace" ? "↻  " : irow.modelData.status === "already" ? "=  " : "×  ")
                  + irow.modelData.keys + "   " + irow.modelData.desc
                color: irow.taken ? (irow.modelData.clashLine ? sw.urgent : sw.accent) : sw.faint
                font.family: sw.font; font.pixelSize: sw.px(11)
              }
              Text { visible: !!irow.modelData.why; width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText; text: irow.modelData.why; color: irow.modelData.clashLine ? sw.urgent : sw.faint; font.family: sw.font; font.pixelSize: sw.px(10) }
              Text { width: parent.width; elide: Text.ElideRight; textFormat: Text.PlainText; text: irow.modelData.command; color: sw.faint; font.family: sw.font; font.pixelSize: sw.px(9) }
            }
          }
        }
        Heading { visible: !!sw.importCheck && sw.importCheck.skipped.length > 0; text: "NOT BINDS (LEFT OUT)" }
        Repeater {
          model: sw.importCheck ? sw.importCheck.skipped.slice(0, 6) : []
          Text {
            required property var modelData
            width: parent.width; elide: Text.ElideRight; textFormat: Text.PlainText
            text: modelData.line + "   (" + modelData.why + ")"
            color: sw.faint; font.family: sw.font; font.pixelSize: sw.px(10)
          }
        }
      }
    }

    // ---- tab 3: templates ----
    Item {
      visible: sw.btab === "templates"
      anchors { top: keysHead.bottom; topMargin: 12; bottom: parent.bottom; left: parent.left; right: parent.right }
      Text {
        visible: sw.tmpls.length === 0
        width: parent.width; wrapMode: Text.Wrap
        text: "None yet. In its actions (1), give something a key and press t: the same key is offered for the similar call in each of your other shells."
        color: sw.faint; font.family: sw.font; font.pixelSize: sw.px(11)
      }
      ListView {
        anchors.fill: parent
        clip: true
        spacing: 6
        model: sw.tmpls
        currentIndex: sw.tcur
        onCurrentIndexChanged: positionViewAtIndex(currentIndex, ListView.Contain)
        delegate: Rectangle {
          id: trow
          required property var modelData
          required property int index
          width: ListView.view.width
          height: tcol.implicitHeight + 16
          radius: sw.rad
          color: index === sw.tcur ? sw.cursorFill : sw.rowFill
          border.color: index === sw.tcur ? sw.accent : "transparent"
          Column {
            id: tcol
            x: 12; y: 8; width: parent.width - 24
            spacing: 3
            Text { textFormat: Text.PlainText; text: trow.modelData.keys + "   " + trow.modelData.name; color: sw.fg; font.family: sw.font; font.pixelSize: sw.px(12); font.bold: true }
            Repeater {
              model: trow.modelData.binds || []
              Text {
                required property var modelData
                width: tcol.width; elide: Text.ElideRight; textFormat: Text.PlainText
                text: modelData.loadout + ":  " + modelData.command
                color: sw.dim; font.family: sw.font; font.pixelSize: sw.px(10)
              }
            }
          }
          MouseArea { anchors.fill: parent; onClicked: { sw.tcur = trow.index; sw.takeKeys() } }
        }
      }
    }

    // ---- tab 2: from your bindings.lua ----
    Item {
      visible: sw.btab === "file"
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
          objectName: "shellKeysList"
          // follow the keyboard cursor
          currentIndex: sw.kcur0
          onCurrentIndexChanged: positionViewAtIndex(currentIndex, ListView.Contain)
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

  // ---- a loadout's picture, while the mouse is on it ----
  Rectangle {
    visible: !!sw.hoverShot && sw.mode === "list"
    z: 30
    x: Math.round(parent.width * 0.42) + 18
    width: parent.width - x
    y: !sw.hoverShot ? 0 : Math.max(0, Math.min(sw.hoverShot.y - 10, parent.height - height))
    height: hsCol.implicitHeight + 20
    radius: sw.rad
    color: sw.solid
    border.color: sw.accent
    Column {
      id: hsCol
      x: 10; y: 10; width: parent.width - 20
      spacing: 6
      Text { textFormat: Text.PlainText; text: sw.hoverShot ? sw.hoverShot.name : ""; color: sw.fg; font.family: sw.font; font.pixelSize: sw.px(12); font.bold: true }
      BarShot { width: parent.width; height: implicitHeight; shot: sw.hoverShot ? sw.hoverShot.shot : null }
    }
  }

  // ---- reviewing a template: the same key in your other shells ----
  Rectangle {
    anchors.fill: parent
    visible: !!sw.review
    z: 45
    color: sw.tint(sw.solid, 0.8)
    radius: sw.rad
    MouseArea { anchors.fill: parent; onClicked: {} }
    Rectangle {
      width: Math.min(parent.width - 60, sw.px(680))
      height: rvCol.implicitHeight + 36
      anchors.centerIn: parent
      radius: sw.rad
      color: sw.solid
      border.color: sw.accent
      Column {
        id: rvCol
        x: 18; y: 18
        width: parent.width - 36
        spacing: 8
        Text {
          width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText
          text: sw.review ? sw.review.keys + " for " + (sw.review.categoryName || sw.review.label).toLowerCase() + " in your other shells?" : ""
          color: sw.fg; font.family: sw.font; font.pixelSize: sw.px(15); font.bold: true
        }
        Text {
          width: parent.width; wrapMode: Text.Wrap
          text: "Each shell gets the call most like it. Check them: ←/→ picks another call (or leaves that shell alone), space turns one off. "
            + "Nothing is set until you say so on the next screen."
          color: sw.dim; font.family: sw.font; font.pixelSize: sw.px(11)
        }
        Repeater {
          model: sw.review ? sw.review.items : []
          Rectangle {
            id: rvrow
            required property var modelData
            required property int index
            readonly property var cand: modelData.pick >= 0 ? modelData.candidates[modelData.pick] : null
            width: rvCol.width
            height: rvInner.implicitHeight + 14
            radius: sw.rad
            color: sw.review && index === sw.review.cur ? sw.cursorFill : sw.rowFill
            border.color: sw.review && index === sw.review.cur ? sw.accent : "transparent"
            Column {
              id: rvInner
              x: 10; y: 7; width: parent.width - 20
              spacing: 2
              Text {
                textFormat: Text.PlainText
                text: (rvrow.modelData.on ? "\u2713  " : "     ") + rvrow.modelData.loadoutName
                color: rvrow.modelData.on ? sw.accent : sw.faint; font.family: sw.font; font.pixelSize: sw.px(12); font.bold: true
              }
              Text {
                width: parent.width; elide: Text.ElideRight; textFormat: Text.PlainText
                text: rvrow.cand ? "‹  " + rvrow.cand.label + "  ›     " + rvrow.cand.command
                  + (sw.review && rvrow.cand.bound === sw.review.keys ? "     (already its key)" : "")
                  : rvrow.modelData.candidates.length ? "‹  leave this shell alone  ›" : "nothing similar in this shell"
                color: rvrow.modelData.on ? sw.fg : sw.faint; font.family: sw.font; font.pixelSize: sw.px(11)
              }
              Text {
                visible: !!rvrow.modelData.clash && (rvrow.modelData.clash.bindings.length > 0 || rvrow.modelData.clash.loadout.length > 0)
                  && !(rvrow.cand && sw.review && rvrow.cand.bound === sw.review.keys)
                width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText
                text: !rvrow.modelData.clash ? "" : rvrow.modelData.clash.bindings.length
                  ? "your bindings.lua binds it too (line " + rvrow.modelData.clash.bindings[0].line + "): it'd be left out there"
                  : rvrow.modelData.clash.loadout.length ? "replaces its key for " + rvrow.modelData.clash.loadout[0].desc : ""
                color: sw.urgent; font.family: sw.font; font.pixelSize: sw.px(10)
              }
            }
            MouseArea { anchors.fill: parent; onClicked: { var r = Object.assign({}, sw.review); r.cur = rvrow.index; sw.review = r; sw.reviewCycle(1) } }
          }
        }
        Row {
          spacing: 8
          Btn { text: "Review and set"; hint: "Enter"; on: true; onClicked: sw.reviewApply() }
          Btn { text: "Cancel"; hint: "Esc"; onClicked: sw.review = null }
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
      visible: text !== ""
      maximumLineCount: 2
      elide: Text.ElideRight
      wrapMode: Text.Wrap
      textFormat: Text.PlainText
      text: sw.status
      color: sw.statusBad ? sw.urgent : sw.dim
      font.family: sw.font; font.pixelSize: sw.px(11)
    }
  }
}
