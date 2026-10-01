#!/usr/bin/env python3
"""Shell-Swapper: swap between Omarchy shell loadouts (bar suites).

A *suite* is any plugin that can be the bar ("kinds": ["bar"] in its
manifest): Omarchy's own bar, Slime Shell, Ruixen... A *loadout* is a named
snapshot of the parts of ~/.config/omarchy/shell.json that make a suite what
it is: `bar` (which bar, its layout and widget settings), plus the plugins
that bar brings along and the ones it switches off.

Plugins that don't belong to any suite (your Taskchy, theme manager, ...) are
global: they stay as they are whatever loadout you swap to. A suite's own
plugins ("slime.*", "ruixen.*") come from the loadout.

Every swap backs up shell.json first and waits for you to confirm it. The
shell shows "Keep this shell?"; if nobody answers (or the new shell never
comes up to ask), a watchdog puts the old config back.

Commands (all print JSON; errors go to stderr with exit status 1):

  list                          suites, loadouts, the active one, a pending swap
  suites                        the bar suites installed
  status                        the active loadout and the current bar
  save [NAME]                   save the current shell as loadout NAME
                                (default: the active loadout, else the suite's name)
  apply ID                      swap to loadout ID (or "suite:<bar id>" for that
                                suite's default layout), then confirm or revert
  apply --suite BAR_ID          the loadout for that suite (last used), or its default
  apply --previous              back to the loadout before this one
  confirm                       keep the swap that's waiting
  revert                        undo the swap that's waiting
  seen TOKEN                    (the shell's prompt) it's on screen: start the countdown
  rescue                        put the last confirmed config back and restart
                                the shell (bind it to a key: it works even when
                                the bar doesn't come up)
  rename ID NAME
  delete ID                     (not the active one)
  config [KEY VALUE]            settings: apply = restart | reload, confirmSeconds

Keys (a loadout's own keybinds, in hypr/shell_swapper.lua; your bindings.lua
is only touched when you move keys in or out, and always backed up first):
  apply ... --keys | --keep-keys   when the loadout's keys differ from the ones
                                you have: bring them too, or keep yours
  keys status                   the hook, the keys live now, recent backups
  keys hook                     add the one line that loads loadout keys
  keys scan                     the binds in your bindings.lua (line numbers)
  keys list ID | keys diff ID   a loadout's keys | what swapping to it changes
  keys move ID LINE...          move binds from bindings.lua into a loadout
                                (commented out there, tagged to come back)
  keys remove ID N              take key N out (back to bindings.lua if it came from there)
  keys add ID 'o.bind(...)'     give a loadout a key
  keys backups | keys restore BACKUP
Anything that would change the keys you have now prints its plan and exits
with status 2 instead; run it again with --yes to do it.

Environment (tests): SHELL_SWAPPER_RESTART (default "omarchy restart shell"),
SHELL_SWAPPER_RELOAD, SHELL_SWAPPER_LOCKED (default
"omarchy-hyprland-session-locked"), OMARCHY_PATH.
"""
import copy
import datetime as dt
import fcntl
import glob
import json
import os
import re
import shlex
import subprocess
import sys
import time
import uuid

HOME = os.path.expanduser("~")
OMARCHY = os.environ.get("OMARCHY_PATH", "/usr/share/omarchy")
SHELL_JSON = os.path.join(HOME, ".config/omarchy/shell.json")
DEFAULTS_JSON = os.path.join(OMARCHY, "config/omarchy/shell.json")
PLUGIN_DIRS = [os.path.join(HOME, ".config/omarchy/plugins"), os.path.join(OMARCHY, "shell/plugins")]
CONF_DIR = os.path.join(HOME, ".config/shell-swapper")
LOADOUTS = os.path.join(CONF_DIR, "loadouts")
CONFIG = os.path.join(CONF_DIR, "config.json")
STATE = os.path.join(HOME, ".local/state/shell-swapper")
BACKUPS = os.path.join(STATE, "backups")
PENDING = os.path.join(STATE, "pending.json")
STATUS = os.path.join(STATE, "status.json")
LAST_GOOD = os.path.join(STATE, "last-good.json")
SLIME_BACKUP = os.path.join(HOME, ".config/omarchy/slime-shell/previous-bar.json")

SELF_ID = "shell-swapper"
KEEP_BACKUPS = 20
# before the shell's prompt is up (a restart takes a few seconds), and after
SETTLE_SECONDS = 45
GRACE_SECONDS = 2
DEFAULT_CONFIG = {"apply": "restart", "confirmSeconds": 15}

# Only one notification server can run: whichever the suite wants wins, else
# Omarchy's.
NOTIFIERS = ["omarchy.notifications", "slime.notifications"]

# What a suite brings and switches off, for suites that don't say so
# themselves (a bar plugin's manifest can carry a "shellSwapper" object with
# the same keys, which wins):
#   enable   plugins switched on with it      disable  plugins switched off
#   owns     id prefixes of its own plugins   layout   its default bar config
#                                                      (a JSON file, relative
#                                                      to the plugin folder)
BUILTIN_RULES = {
    "omarchy.bar": {"enable": ["omarchy.notifications", "omarchy.menu"], "disable": ["ruixen.notch"], "owns": []},
    "slime.bar": {"enable": ["slime.notifications"], "disable": ["omarchy.notifications", "ruixen.notch"],
                  "owns": ["slime."], "layout": "../../layouts/default.json"},
    "ruixen.bar": {"enable": ["omarchy.notifications"], "disable": [], "owns": ["ruixen."]},
}


class Fail(Exception):
    pass


# ---- files ----------------------------------------------------------------

def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return copy.deepcopy(default)


def write_json(path, data):
    """Atomic: a temp file in the same folder, then a rename."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


class Lock:
    def __enter__(self):
        os.makedirs(STATE, exist_ok=True)
        self.f = open(os.path.join(STATE, "lock"), "w")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


def now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def config():
    c = dict(DEFAULT_CONFIG)
    c.update(read_json(CONFIG, {}) or {})
    if c.get("apply") not in ("restart", "reload"):
        c["apply"] = "restart"
    try:
        c["confirmSeconds"] = max(5, min(120, int(c.get("confirmSeconds", 15))))
    except (TypeError, ValueError):
        c["confirmSeconds"] = 15
    return c


def shell_config():
    c = read_json(SHELL_JSON, None)
    if not isinstance(c, dict):
        raise Fail(f"can't read {SHELL_JSON}")
    return c


# ---- suites -----------------------------------------------------------------

def suites():
    """Installed bar plugins, by id (your plugin folder wins over Omarchy's)."""
    found = {}
    for base in PLUGIN_DIRS:
        for mf in sorted(glob.glob(os.path.join(base, "*", "manifest.json"))):
            m = read_json(mf, None)
            if not isinstance(m, dict) or "bar" not in (m.get("kinds") or []):
                continue
            sid = str(m.get("id") or "")
            if not sid or sid in found:
                continue
            found[sid] = {"id": sid, "name": m.get("name") or sid, "description": m.get("description", ""),
                          "dir": os.path.dirname(mf), "manifest": m}
    if "omarchy.bar" not in found:   # always there, even if the folder moved
        found["omarchy.bar"] = {"id": "omarchy.bar", "name": "Omarchy", "description": "Omarchy's own bar",
                                "dir": os.path.join(OMARCHY, "shell/plugins/bar"), "manifest": {}}
    if found["omarchy.bar"]["name"] in ("Bar", "omarchy.bar"):
        found["omarchy.bar"]["name"] = "Omarchy"
    return found


def rules(suite_id, all_suites=None):
    s = (all_suites or suites()).get(suite_id)
    r = {"enable": [], "disable": [], "owns": []}
    r.update(BUILTIN_RULES.get(suite_id, {}))
    own = s and isinstance(s["manifest"].get("shellSwapper"), dict) and s["manifest"]["shellSwapper"]
    if own:
        r.update({k: v for k, v in own.items() if k in ("enable", "disable", "owns", "layout")})
    if not r["owns"] and "." in suite_id and suite_id != "omarchy.bar":
        r["owns"] = [suite_id.rsplit(".", 1)[0] + "."]
    return r


def bar_id(cfg):
    b = cfg.get("bar") if isinstance(cfg, dict) else None
    return str((b or {}).get("id") or "omarchy.bar")


def owner(pid, all_suites):
    """The suite a plugin belongs to ("slime.notifications" -> slime.bar), or None."""
    for sid in all_suites:
        for prefix in rules(sid, all_suites)["owns"]:
            if pid.startswith(prefix):
                return sid
    return None


def ruled_ids(all_suites):
    """Plugins some suite switches on or off: they follow the loadout too."""
    out = set(NOTIFIERS)
    for sid in all_suites:
        r = rules(sid, all_suites)
        out.update(r["enable"])
        out.update(r["disable"])
    return out


def default_bar(suite_id, all_suites):
    """A suite's default bar config, for a fresh loadout."""
    r = rules(suite_id, all_suites)
    if r.get("layout"):
        b = read_json(os.path.normpath(os.path.join(all_suites[suite_id]["dir"], r["layout"])), None)
        if isinstance(b, dict):
            b = dict(b)
            b["id"] = suite_id
            return b
    if suite_id == "omarchy.bar":
        b = (read_json(DEFAULTS_JSON, {}) or {}).get("bar")
        if isinstance(b, dict):
            return b
    return {"id": suite_id}


# ---- loadouts ---------------------------------------------------------------

def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")[:40] or "loadout"


def check_id(lid):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", lid or ""):
        raise Fail(f"'{lid}' isn't a loadout id")
    return lid


def loadout_path(lid):
    return os.path.join(LOADOUTS, check_id(lid) + ".json")


def loadouts():
    out = {}
    for p in sorted(glob.glob(os.path.join(LOADOUTS, "*.json"))):
        d = read_json(p, None)
        lid = os.path.basename(p)[:-5]
        if isinstance(d, dict) and isinstance(d.get("bar"), dict) and re.fullmatch(r"[a-z0-9][a-z0-9-]*", lid):
            d["id"] = lid
            out[lid] = d
    return out


def status():
    return read_json(STATUS, {}) or {}


def set_status(**kw):
    s = status()
    s.update(kw)
    write_json(STATUS, s)


def snapshot(cfg, name, suite_id):
    return {"name": name, "suite": suite_id, "saved": now(),
            "bar": copy.deepcopy(cfg.get("bar") or {}),
            "plugins": copy.deepcopy(cfg.get("plugins") or []),
            "disabledPlugins": list(cfg.get("disabledPlugins") or [])}


def unique_id(name, taken):
    base = slugify(name)
    lid, n = base, 2
    while lid in taken:
        lid, n = f"{base}-{n}", n + 1
    return lid


def save_loadout(lid, data):
    data = {k: v for k, v in data.items() if k != "id"}
    write_json(loadout_path(lid), data)


def seed():
    """First run: the current shell becomes a loadout (and Slime Shell's
    backup of the bar it replaced, if there is one)."""
    if loadouts():
        return
    all_suites = suites()
    cfg = shell_config()
    cur = bar_id(cfg)
    name = all_suites.get(cur, {}).get("name", cur)
    lid = unique_id(name, set())
    save_loadout(lid, snapshot(cfg, name, cur))
    set_status(active=lid, previous=status().get("previous", ""))
    prev = read_json(SLIME_BACKUP, None)
    if isinstance(prev, dict) and isinstance(prev.get("bar"), dict) and bar_id(prev) != cur:
        pid = bar_id(prev)
        pname = all_suites.get(pid, {}).get("name", pid)
        # its own plugins only: the global ones are taken from the shell as it is
        plugins = [p for p in (prev.get("plugins") or []) if owner(str(p.get("id", "")), all_suites) == pid]
        if not plugins:
            plugins = [p for p in (cfg.get("plugins") or []) if owner(str(p.get("id", "")), all_suites) == pid]
        snap = snapshot({"bar": prev["bar"], "plugins": plugins, "disabledPlugins": prev.get("disabledPlugins") or []},
                        pname, pid)
        snap["note"] = "imported from Slime Shell's backup of the bar it replaced"
        save_loadout(unique_id(pname, {lid}), snap)
    if not os.path.exists(LAST_GOOD):
        write_json(LAST_GOOD, cfg)


# ---- building the new shell.json --------------------------------------------

def entry_id(e):
    return str(e.get("id", "")) if isinstance(e, dict) else str(e)


def compose(cur, target, all_suites):
    """The shell config for `target` (a loadout), keeping what's global from
    the current config `cur`."""
    suite = target.get("suite") or bar_id(target)
    r = rules(suite, all_suites)
    ruled = ruled_ids(all_suites)

    def follows_loadout(pid):
        return owner(pid, all_suites) is not None or pid in ruled

    new = copy.deepcopy(cur)
    new["bar"] = copy.deepcopy(target["bar"])
    # plugins: suite-owned ones from the loadout, everything else as it is now
    plugins = [p for p in (cur.get("plugins") or []) if not follows_loadout(entry_id(p))]
    plugins += [p for p in (target.get("plugins") or []) if follows_loadout(entry_id(p))]
    disabled = [d for d in (cur.get("disabledPlugins") or []) if not follows_loadout(str(d))]
    disabled += [d for d in (target.get("disabledPlugins") or []) if follows_loadout(str(d))]

    def enable(pid):
        nonlocal disabled
        disabled = [d for d in disabled if d != pid]
        first_party = os.path.isdir(os.path.join(OMARCHY, "shell/plugins")) and pid.startswith("omarchy.")
        if not first_party and not any(entry_id(p) == pid for p in plugins):
            plugins.append({"id": pid})

    def disable(pid):
        if pid not in disabled:
            disabled.append(pid)

    for pid in r["enable"]:
        enable(pid)
    for pid in r["disable"]:
        disable(pid)
    # one notification server: the suite's, else whichever the loadout has on,
    # else Omarchy's
    wanted = [n for n in NOTIFIERS if n in r["enable"]]
    on = [n for n in NOTIFIERS if n not in disabled and (n.startswith("omarchy.") or any(entry_id(p) == n for p in plugins))]
    keep = (wanted or on or ["omarchy.notifications"])[0]
    for n in NOTIFIERS:
        if n == keep:
            enable(n)
        elif n not in disabled and (n.startswith("omarchy.") or any(entry_id(p) == n for p in plugins)):
            disable(n)
    # Shell-Swapper itself stays on, so it can ask (and so you can swap back)
    disabled = [d for d in disabled if d != SELF_ID]
    if not any(entry_id(p) == SELF_ID for p in plugins):
        self_entries = [p for p in (cur.get("plugins") or []) if entry_id(p) == SELF_ID]
        plugins += self_entries or [{"id": SELF_ID}]
    # no duplicates, first wins
    seen, uniq = set(), []
    for p in plugins:
        if entry_id(p) not in seen:
            seen.add(entry_id(p))
            uniq.append(p)
    new["plugins"] = uniq
    new["disabledPlugins"] = sorted(set(disabled), key=disabled.index)
    return new


# ---- keybinds ------------------------------------------------------------------
#
# A loadout can carry its own keys (Slime's launcher on SUPER+R, Ruixen's on
# SUPER+X...). They're written to their own file, hypr/shell_swapper.lua,
# loaded by one guarded line after your bindings.lua, so a swap never edits
# your own keys. Anything that changes the keys you have right now only says
# what it would do, unless it's given --yes (the picker asks you first), and
# every keybind file is backed up before it's written.

HYPR = os.path.join(HOME, ".config/hypr")
BINDINGS = os.path.join(HYPR, "bindings.lua")
HYPRLAND = os.path.join(HYPR, "hyprland.lua")
KEYS_FILE = os.path.join(HYPR, "shell_swapper.lua")
KEY_BACKUPS = os.path.join(STATE, "keybind-backups")
LAST_GOOD_KEYS = os.path.join(STATE, "last-good-keys.lua")
KEEP_KEY_BACKUPS = 40
HOOK_BEGIN = "-- >>> shell-swapper keys >>>"
HOOK_END = "-- <<< shell-swapper keys <<<"
HOOK = [HOOK_BEGIN,
        "-- Keys that belong to your Shell-Swapper loadouts, kept in hypr/shell_swapper.lua",
        "-- (written by Shell-Swapper on a swap). Remove this block to stop loading them.",
        'pcall(require, "hypr.shell_swapper")',
        HOOK_END]
MOVED_TAG = "--[shell-swapper:{}] "
MODS = ["SUPER", "CTRL", "ALT", "SHIFT"]


class NeedsYes(Exception):
    """A change to your keys: here's what it would do; run again with --yes."""
    def __init__(self, plan):
        super().__init__("needs --yes")
        self.plan = plan


def norm_keys(k):
    parts = [p.strip().upper() for p in str(k).split("+") if p.strip()]
    parts = ["CTRL" if p in ("CONTROL", "CTL") else p for p in parts]
    mods = [m for m in MODS if m in parts]
    rest = [p for p in parts if p not in MODS]
    return " + ".join(mods + rest)


def lua_statements(text):
    """Top-level statements of a Lua file, as (start, end) offsets: comments
    skipped, strings and brackets respected, a statement ending at a newline
    once its brackets are closed."""
    out, i, n, depth, start = [], 0, len(text), 0, None

    def long_close(j):
        m = re.match(r"\[(=*)\[", text[j:])
        return ("]" + m.group(1) + "]", j + len(m.group(0))) if m else (None, j)

    while i < n:
        c = text[i]
        if text.startswith("--", i):
            close, j = long_close(i + 2)
            if close:
                k = text.find(close, j)
                i = n if k < 0 else k + len(close)
            else:
                k = text.find("\n", i)
                i = n if k < 0 else k
            continue
        if c in "\"'":
            if start is None:
                start = i
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            i = j + 1
            continue
        if c == "[":
            close, j = long_close(i)
            if close:
                if start is None:
                    start = i
                k = text.find(close, j)
                i = n if k < 0 else k + len(close)
                continue
        if c == "\n":
            if start is not None and depth <= 0:
                out.append((start, i))
                start, depth = None, 0
            i += 1
            continue
        if c in "({[":
            depth += 1
        elif c in ")}]":
            depth -= 1
        if start is None and not c.isspace():
            start = i
        i += 1
    if start is not None:
        out.append((start, n))
    return out


BIND_RE = re.compile(r"""(o\.bind_toggle|o\.bind|hl\.bind|hl\.unbind)\s*\(\s*(["'])((?:\\.|(?!\2).)*)\2""", re.S)
STR_ARG_RE = re.compile(r"""\s*,\s*(["'])((?:\\.|(?!\1).)*)\1""", re.S)


def classify(stmt):
    m = BIND_RE.match(stmt.strip())
    if not m:
        return None
    fn, keys = m.group(1), m.group(3)
    desc = ""
    if fn.startswith("o.bind"):
        d = STR_ARG_RE.match(stmt.strip()[m.end():])
        desc = d.group(2) if d else ""
    return {"kind": "unbind" if fn == "hl.unbind" else "bind", "keys": norm_keys(keys), "rawKeys": keys, "desc": desc}


def parse_binds(text):
    """Binds and unbinds in a Lua file: line numbers, keys, description, code."""
    out = []
    for s, e in lua_statements(text):
        stmt = text[s:e].rstrip()
        info = classify(stmt)
        if info:
            info.update({"line": text.count("\n", 0, s) + 1, "endLine": text.count("\n", 0, e) + 1,
                         "start": s, "end": e, "lua": stmt})
            out.append(info)
    return out


def read_text(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def key_backup(path):
    """Copy a keybind file into the backups before it's written. Returns the
    copy ("" if there was no file yet)."""
    text = read_text(path)
    if text is None:
        return ""
    os.makedirs(KEY_BACKUPS, exist_ok=True)
    dest = os.path.join(KEY_BACKUPS, dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
                        + "-" + os.path.basename(path))
    write_text(dest, text)
    for old in sorted(glob.glob(os.path.join(KEY_BACKUPS, "*")))[:-KEEP_KEY_BACKUPS]:
        os.remove(old)
    return dest


def hypr_reload():
    run_cmd("SHELL_SWAPPER_HYPR_RELOAD", "hyprctl reload")


def hook_file():
    for f in (HYPRLAND, BINDINGS):
        if HOOK_BEGIN in (read_text(f) or ""):
            return f
    return ""


def user_binds():
    """Your own keys, in bindings.lua (Shell-Swapper's hook block aside)."""
    return [b for b in parse_binds(read_text(BINDINGS) or "") if b["kind"] == "bind"]


def render_keys(name, binds):
    lines = ["-- Shell-Swapper: keys for the \"" + name.replace("\n", " ") + "\" loadout.",
             "-- Written on every swap: change them in the picker (k), not here. Your own",
             "-- keys stay in bindings.lua.", ""]
    for b in binds:
        lines.append(f'hl.unbind("{b["keys"]}")')
        lines.append(b["lua"])
    return "\n".join(lines).rstrip() + "\n"


def keys_plan(loadout):
    """What writing `loadout`'s keys would change, against the keys file as it
    is: keys added and removed, and keys skipped because bindings.lua binds
    them too (both would fire)."""
    mine = {b["keys"]: b for b in user_binds()}
    binds = (loadout or {}).get("keybinds") or []
    conflicts = [dict(keys=b["keys"], desc=b.get("desc", ""), line=mine[b["keys"]]["line"])
                 for b in binds if b["keys"] in mine]
    use = [b for b in binds if b["keys"] not in mine]
    text = render_keys((loadout or {}).get("name", "no loadout"), use) if loadout else ""
    cur_text = read_text(KEYS_FILE) or ""
    cur = {b["keys"]: b for b in parse_binds(cur_text) if b["kind"] == "bind"}
    new = {b["keys"]: b for b in use}
    return {"text": text, "changes": text.strip() != cur_text.strip() and bool(cur or new),
            "add": [{"keys": k, "desc": new[k].get("desc", "")} for k in new if k not in cur or cur[k]["lua"] != new[k]["lua"]],
            "remove": [{"keys": k, "desc": cur[k].get("desc", "")} for k in cur if k not in new],
            "conflicts": conflicts}


def write_keys(text):
    """Write the keys file (backed up first). The caller has asked."""
    b = key_backup(KEYS_FILE)
    existed = os.path.exists(KEYS_FILE)
    write_text(KEYS_FILE, text)
    return {"backup": b, "existed": existed}


def restore_keys(info):
    """Undo write_keys (a revert): the old file back, or none if there wasn't one."""
    key_backup(KEYS_FILE)
    old = read_text(info.get("backup", "")) if info.get("backup") else None
    if old is not None:
        write_text(KEYS_FILE, old)
    elif not info.get("existed") and os.path.exists(KEYS_FILE):
        os.remove(KEYS_FILE)


def keys_status():
    active = status().get("active", "")
    outs = loadouts()
    cur = read_text(KEYS_FILE)
    return {"hook": hook_file(), "file": KEYS_FILE, "fileExists": cur is not None,
            "live": [{"keys": b["keys"], "desc": b["desc"]} for b in parse_binds(cur or "") if b["kind"] == "bind"],
            "active": active,
            "activeKeys": len((outs.get(active) or {}).get("keybinds") or []),
            "backups": sorted(os.path.basename(p) for p in glob.glob(os.path.join(KEY_BACKUPS, "*")))[-10:]}


def install_hook(yes):
    if hook_file():
        return {"hook": hook_file(), "installed": False, "already": True}
    text = read_text(HYPRLAND)
    target, at = HYPRLAND, None
    if text is not None:
        lines = text.split("\n")
        for i, l in enumerate(lines):
            if re.match(r"""\s*require\s*\(?\s*["']hypr\.bindings["']\s*\)?\s*$""", l):
                at = i + 1
                break
    if at is None:   # no require of bindings.lua to follow: the end of bindings.lua
        target, text = BINDINGS, read_text(BINDINGS) or ""
        at = len(text.rstrip("\n").split("\n"))
    plan = {"action": "add the keys hook", "file": target, "afterLine": at, "lines": HOOK,
            "backupTo": KEY_BACKUPS}
    if not yes:
        raise NeedsYes(plan)
    lines = text.split("\n")
    key_backup(target)
    new = lines[:at] + HOOK + lines[at:]
    write_text(target, "\n".join(new))
    if not os.path.exists(KEYS_FILE):
        write_text(KEYS_FILE, "-- Shell-Swapper: no loadout keys yet.\n")
    hypr_reload()
    return dict(plan, installed=True)


def tag_lines(stmt, lid):
    return "\n".join(MOVED_TAG.format(lid) + l for l in stmt.split("\n"))


def move_keys(lid, line_nos, yes):
    """Move binds from bindings.lua into loadout `lid`: they're commented out
    there (tagged, so they can come back), with any hl.unbind of the same key."""
    with Lock():
        outs = loadouts()
        if check_id(lid) not in outs:
            raise Fail(f"no loadout '{lid}'")
        if not hook_file():
            raise Fail("loadout keys aren't loaded yet: set that up first (shell-swapper keys hook)")
        text = read_text(BINDINGS)
        if text is None:
            raise Fail(f"can't read {BINDINGS}")
        allb = parse_binds(text)
        picked = [b for b in allb if b["kind"] == "bind" and b["line"] in line_nos]
        if not picked:
            raise Fail("no binds on those lines of bindings.lua")
        have = {b["keys"] for b in outs[lid].get("keybinds") or []}
        dup = [b for b in picked if b["keys"] in have]
        picked = [b for b in picked if b["keys"] not in have]
        keys = {b["keys"] for b in picked}
        unbinds = [b for b in allb if b["kind"] == "unbind" and b["keys"] in keys]
        active = status().get("active", "") == lid
        plan = {"action": "move keys into " + outs[lid].get("name", lid), "loadout": lid,
                "move": [{"line": b["line"], "keys": b["keys"], "desc": b["desc"]} for b in picked],
                "alsoCommentOut": [{"line": b["line"], "lua": b["lua"]} for b in unbinds],
                "alreadyThere": [b["keys"] for b in dup],
                "activeNow": active,
                "note": ("they keep working now (it's the shell you're using)" if active else
                         "they'll only work while " + outs[lid].get("name", lid) + " is your shell"),
                "backupTo": KEY_BACKUPS}
        if not picked:
            raise Fail("those keys are already in that loadout")
        if not yes:
            raise NeedsYes(plan)
        key_backup(BINDINGS)
        # comment out from the bottom up, so the offsets stay right
        for b in sorted(picked + unbinds, key=lambda b: b["start"], reverse=True):
            text = text[:b["start"]] + tag_lines(text[b["start"]:b["end"]].rstrip(), lid) + text[b["end"]:]
        d = outs[lid]
        d["keybinds"] = (d.get("keybinds") or []) + [
            {"keys": b["keys"], "desc": b["desc"], "lua": b["lua"], "from": "bindings.lua"} for b in picked]
        save_loadout(lid, d)
        write_text(BINDINGS, text)
        if active and hook_file():
            write_keys(keys_plan(d)["text"])
        hypr_reload()
        return dict(plan, moved=True)


def remove_key(lid, idx, yes):
    """Take a key out of a loadout. If it came from bindings.lua, it goes back
    there (un-commented)."""
    with Lock():
        outs = loadouts()
        if check_id(lid) not in outs:
            raise Fail(f"no loadout '{lid}'")
        d = outs[lid]
        binds = d.get("keybinds") or []
        if not 0 <= idx < len(binds):
            raise Fail(f"no key {idx} in {lid}")
        b = binds[idx]
        text = read_text(BINDINGS) or ""
        tag = MOVED_TAG.format(lid)
        back = [i for i, l in enumerate(text.split("\n"))
                if l.startswith(tag) and (classify(l[len(tag):]) or {}).get("keys") == b["keys"]]
        active = status().get("active", "") == lid
        plan = {"action": "take " + b["keys"] + " out of " + d.get("name", lid), "loadout": lid,
                "keys": b["keys"], "desc": b.get("desc", ""),
                "backToBindings": [i + 1 for i in back], "activeNow": active, "backupTo": KEY_BACKUPS}
        live = active or back
        if live and not yes:
            raise NeedsYes(plan)
        d["keybinds"] = binds[:idx] + binds[idx + 1:]
        save_loadout(lid, d)
        if back:
            key_backup(BINDINGS)
            lines = text.split("\n")
            for i in back:
                # the statement's first line, then the rest of it (tagged
                # lines that don't start a statement of their own)
                lines[i] = lines[i][len(tag):]
                j = i + 1
                while j < len(lines) and lines[j].startswith(tag) and not classify(lines[j][len(tag):]):
                    lines[j] = lines[j][len(tag):]
                    j += 1
            write_text(BINDINGS, "\n".join(lines))
        if active and hook_file():
            write_keys(keys_plan(d)["text"])
        if live:
            hypr_reload()
        return dict(plan, removed=True)


def add_key(lid, lua, yes):
    with Lock():
        outs = loadouts()
        if check_id(lid) not in outs:
            raise Fail(f"no loadout '{lid}'")
        info = classify(lua)
        if not info or info["kind"] != "bind":
            raise Fail("that isn't a bind: o.bind(\"KEYS\", \"what it does\", \"command\") or hl.bind(...)")
        d = outs[lid]
        binds = [b for b in d.get("keybinds") or [] if b["keys"] != info["keys"]]
        binds.append({"keys": info["keys"], "desc": info["desc"], "lua": lua.strip()})
        active = status().get("active", "") == lid
        plan = {"action": "add " + info["keys"] + " to " + d.get("name", lid), "loadout": lid,
                "keys": info["keys"], "desc": info["desc"], "activeNow": active, "backupTo": KEY_BACKUPS}
        if active and hook_file() and not yes:
            raise NeedsYes(plan)
        d["keybinds"] = binds
        save_loadout(lid, d)
        if active and hook_file():
            write_keys(keys_plan(d)["text"])
            hypr_reload()
        return dict(plan, added=True)


def restore_key_backup(name, yes):
    check = os.path.basename(name)
    src = os.path.join(KEY_BACKUPS, check)
    if check != name or not os.path.isfile(src):
        raise Fail(f"no keybind backup '{name}'")
    target = next((f for f in (BINDINGS, HYPRLAND, KEYS_FILE) if check.endswith("-" + os.path.basename(f))), None)
    if not target:
        raise Fail("can't tell which file that backup is of")
    plan = {"action": "restore " + os.path.basename(target) + " from a backup", "file": target, "from": src,
            "backupTo": KEY_BACKUPS}
    if not yes:
        raise NeedsYes(plan)
    key_backup(target)
    write_text(target, read_text(src))
    hypr_reload()
    return dict(plan, restored=True)


# ---- applying ----------------------------------------------------------------

def run_cmd(env_key, default, detach=False):
    cmd = os.environ.get(env_key, default)
    if not cmd:
        return 0
    if detach:
        subprocess.Popen(shlex.split(cmd), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return 0
    return subprocess.call(shlex.split(cmd), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def locked():
    cmd = os.environ.get("SHELL_SWAPPER_LOCKED", "omarchy-hyprland-session-locked")
    try:
        return subprocess.call(shlex.split(cmd), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0
    except OSError:
        return False


def bring_up(mode):
    """Make the shell pick up shell.json: it hot-reloads on save; a restart
    is the sure way."""
    if mode == "reload":
        run_cmd("SHELL_SWAPPER_RELOAD", "omarchy-shell -q shell reloadConfig")
        # the running shell asks to keep it (no restart to start it asking)
        run_cmd("SHELL_SWAPPER_CHECK", "omarchy-shell -q shell-swapper check", detach=True)
    else:
        run_cmd("SHELL_SWAPPER_RESTART", "omarchy restart shell", detach=True)


def backup(cfg):
    os.makedirs(BACKUPS, exist_ok=True)
    path = os.path.join(BACKUPS, dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6] + ".json")
    write_json(path, cfg)
    for old in sorted(glob.glob(os.path.join(BACKUPS, "*.json")))[:-KEEP_BACKUPS]:
        os.remove(old)
    return path


def pending():
    p = read_json(PENDING, None)
    return p if isinstance(p, dict) and p.get("token") else None


def target_for(arg, all_suites, outs):
    """A loadout (by id), or a suite's default: "suite:<bar id>"."""
    if arg.startswith("suite:"):
        sid = arg[6:]
        if sid not in all_suites:
            raise Fail(f"no bar suite '{sid}' installed")
        return None, {"name": all_suites[sid]["name"], "suite": sid, "bar": default_bar(sid, all_suites),
                      "plugins": [], "disabledPlugins": []}
    check_id(arg)
    if arg not in outs:
        raise Fail(f"no loadout '{arg}'")
    t = outs[arg]
    if t.get("suite") and t["suite"] not in all_suites:
        raise Fail(f"loadout '{arg}' needs the {t['suite']} plugin, which isn't installed")
    return arg, t


def apply(arg, confirm=True, keys=None):
    """keys: "swap" (the loadout's keys come too), "keep" (yours stay as they
    are) or None (ask: NeedsYes with what would change)."""
    if locked():
        raise Fail("the screen is locked; swap after unlocking")
    with Lock():
        seed()
        if pending():
            raise Fail("a swap is still waiting to be kept or reverted")
        all_suites, outs, st = suites(), loadouts(), status()
        lid, target = target_for(arg, all_suites, outs)
        cur = shell_config()
        active = st.get("active", "")
        # keep tweaks made since: the shell you're leaving is saved first
        if active in outs and outs[active].get("suite") == bar_id(cur):
            snap = snapshot(cur, outs[active].get("name", active), bar_id(cur))
            for k in ("note", "preview", "keybinds"):
                if k in outs[active]:
                    snap[k] = outs[active][k]
            save_loadout(active, snap)
        created = lid is None
        if created:   # a suite's default: it becomes a loadout of its own
            lid = unique_id(target["name"], set(outs))
            save_loadout(lid, dict(target, saved=now()))
        new = compose(cur, target, all_suites)
        kplan = keys_plan(target) if hook_file() else None
        keys_change = bool(kplan and kplan["changes"])
        if keys_change and keys is None:
            if created:
                os.remove(loadout_path(lid))
            raise NeedsYes({"action": "swap to " + target.get("name", lid), "loadout": arg,
                            "keys": {k: kplan[k] for k in ("add", "remove", "conflicts")},
                            "choose": "--keys (its keys come too) or --keep-keys (yours stay as they are)"})
        if new == cur and not (keys_change and keys == "swap"):
            set_status(active=lid)
            return {"applied": lid, "changed": False}
        kinfo = write_keys(kplan["text"]) if keys_change and keys == "swap" else None
        bpath = backup(cur)
        if not os.path.exists(LAST_GOOD):
            write_json(LAST_GOOD, cur)
        write_json(SHELL_JSON, new)
        c = config()
        token = uuid.uuid4().hex[:12]
        if confirm:
            write_json(PENDING, {"token": token, "from": active, "to": lid,
                                 "toName": outs.get(lid, target).get("name", lid),
                                 "backup": bpath, "created": created, "keys": kinfo, "started": time.time(),
                                 "deadline": time.time() + SETTLE_SECONDS, "seen": False,
                                 "confirmSeconds": c["confirmSeconds"], "mode": c["apply"]})
            subprocess.Popen([sys.executable, os.path.abspath(__file__), "_watchdog", token],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        else:
            write_json(LAST_GOOD, new)
            keep_good_keys()
        set_status(active=lid, previous=active if active != lid else st.get("previous", ""))
    if kinfo is not None:
        hypr_reload()
    bring_up(c["apply"])
    return {"applied": lid, "changed": True, "pending": confirm, "token": token if confirm else "",
            "backup": bpath, "mode": c["apply"], "keysSwapped": kinfo is not None}


def keep_good_keys():
    """The keys that go with the last kept config (rescue puts them back)."""
    text = read_text(KEYS_FILE)
    if text is None:
        if os.path.exists(LAST_GOOD_KEYS):
            os.remove(LAST_GOOD_KEYS)
    else:
        write_text(LAST_GOOD_KEYS, text)


def revert(token=None, why="reverted"):
    with Lock():
        p = pending()
        if not p or (token and p["token"] != token):
            return {"reverted": False}
        b = read_json(p.get("backup", ""), None)
        if not isinstance(b, dict):
            b = read_json(LAST_GOOD, None)
        if not isinstance(b, dict):
            os.remove(PENDING)
            raise Fail("no backup to go back to")
        write_json(SHELL_JSON, b)
        if p.get("keys"):
            restore_keys(p["keys"])
        os.remove(PENDING)
        # a suite's default layout that was never kept isn't a loadout yet
        if p.get("created") and os.path.exists(loadout_path(p["to"])):
            os.remove(loadout_path(p["to"]))
        set_status(active=p.get("from", ""), previous=status().get("previous", ""), lastRevert=why)
        mode = p.get("mode", "restart")
    if p.get("keys"):
        hypr_reload()
    bring_up(mode)
    return {"reverted": True, "active": p.get("from", ""), "why": why}


def confirm(token=None):
    with Lock():
        p = pending()
        if not p or (token and p["token"] != token):
            return {"confirmed": False}
        write_json(LAST_GOOD, shell_config())
        keep_good_keys()
        os.remove(PENDING)
        set_status(active=p["to"], lastRevert="")
        return {"confirmed": True, "active": p["to"]}


def seen(token):
    with Lock():
        p = pending()
        if not p or p["token"] != token:
            return {"seen": False}
        if not p.get("seen"):
            p["seen"] = True
            p["deadline"] = time.time() + p.get("confirmSeconds", 15) + GRACE_SECONDS
            write_json(PENDING, p)
        return {"seen": True, "deadline": p["deadline"]}


def watchdog(token):
    """Detached: if the swap is still waiting at its deadline, undo it."""
    while True:
        p = pending()
        if not p or p["token"] != token:
            return
        if time.time() >= float(p.get("deadline", 0)):
            revert(token, why="no answer" if p.get("seen") else "the new shell never asked")
            return
        time.sleep(0.5)


def rescue():
    with Lock():
        good = read_json(LAST_GOOD, None)
        if not isinstance(good, dict):
            newest = sorted(glob.glob(os.path.join(BACKUPS, "*.json")))
            good = read_json(newest[-1], None) if newest else None
        if not isinstance(good, dict):
            raise Fail("nothing to rescue with: no confirmed config or backup yet")
        cur = read_json(SHELL_JSON, None)
        if isinstance(cur, dict):
            backup(cur)
        write_json(SHELL_JSON, good)
        good_keys = read_text(LAST_GOOD_KEYS)
        if good_keys is not None and hook_file() and good_keys != read_text(KEYS_FILE):
            key_backup(KEYS_FILE)
            write_text(KEYS_FILE, good_keys)
            hypr_reload()
        if os.path.exists(PENDING):
            os.remove(PENDING)
        # which loadout is that? the one with the same bar, if any
        active = next((lid for lid, d in loadouts().items() if d.get("bar") == good.get("bar")), "")
        set_status(active=active, lastRevert="rescue")
    run_cmd("SHELL_SWAPPER_RESTART", "omarchy restart shell", detach=True)
    return {"rescued": True, "active": active}


# ---- listing -------------------------------------------------------------------

def summary_bar(bar):
    lay = (bar or {}).get("layout") or {}
    return {k: [entry_id(e) for e in (lay.get(k) or [])] for k in ("left", "center", "right")}


def listing():
    with Lock():
        seed()
    all_suites, outs, st = suites(), loadouts(), status()
    cur = shell_config()
    used = {d.get("suite") for d in outs.values()}
    mine = {b["keys"]: b["line"] for b in user_binds()}
    return {
        "keysHook": hook_file(),
        "active": st.get("active", ""),
        "previous": st.get("previous", ""),
        "lastRevert": st.get("lastRevert", ""),
        "current": bar_id(cur),
        "pending": pending(),
        "config": config(),
        "loadouts": [{"id": lid, "name": d.get("name", lid), "suite": d.get("suite", ""),
                      "suiteName": all_suites.get(d.get("suite", ""), {}).get("name", d.get("suite", "")),
                      "installed": d.get("suite", "") in all_suites, "saved": d.get("saved", ""),
                      "note": d.get("note", ""), "preview": d.get("preview", ""),
                      "position": (d.get("bar") or {}).get("position", "top"),
                      "layout": summary_bar(d.get("bar")), "active": lid == st.get("active"),
                      "keys": [{"keys": b["keys"], "desc": b.get("desc", ""), "clashLine": mine.get(b["keys"], 0)}
                               for b in d.get("keybinds") or []]}
                     for lid, d in outs.items()],
        "suites": [{"id": s["id"], "name": s["name"], "description": s["description"],
                    "hasLoadout": s["id"] in used} for s in all_suites.values()],
    }


# ---- the command line ------------------------------------------------------------

def run(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return None
    cmd, rest = argv[0], argv[1:]
    if cmd == "list":
        return listing()
    if cmd == "suites":
        return {"suites": [{k: v for k, v in s.items() if k != "manifest"} for s in suites().values()]}
    if cmd == "status":
        st, cur = status(), shell_config()
        return {"active": st.get("active", ""), "current": bar_id(cur), "pending": pending()}
    if cmd == "save":
        with Lock():
            seed()
            cur, outs, st = shell_config(), loadouts(), status()
            sid = bar_id(cur)
            name = " ".join(rest).strip()
            if not name:
                lid = st.get("active", "")
                if lid not in outs or outs[lid].get("suite") != sid:
                    raise Fail("the active loadout is for another bar; give the new one a name")
                name = outs[lid].get("name", lid)
            else:
                same = [l for l, d in outs.items() if d.get("name", "").lower() == name.lower()]
                lid = same[0] if same else unique_id(name, set(outs))
            snap = snapshot(cur, name, sid)
            for k in ("note", "preview", "keybinds"):
                if lid in outs and k in outs[lid]:
                    snap[k] = outs[lid][k]
            save_loadout(lid, snap)
            set_status(active=lid)
            write_json(LAST_GOOD, cur)
            return {"saved": lid, "name": name}
    if cmd == "apply":
        if not rest:
            raise Fail("apply what? a loadout id, --suite BAR_ID or --previous")
        if rest[0] == "--previous":
            prev = status().get("previous", "")
            if not prev:
                raise Fail("no previous loadout")
            return apply(prev, keys=keys_flag(rest))
        if rest[0] == "--suite":
            if len(rest) < 2:
                raise Fail("--suite needs a bar id")
            with Lock():
                seed()
            mine = sorted(((d.get("saved", ""), lid) for lid, d in loadouts().items() if d.get("suite") == rest[1]),
                          reverse=True)
            act = status().get("active", "")
            pick = act if any(lid == act for _, lid in mine) else (mine[0][1] if mine else "suite:" + rest[1])
            return apply(pick, confirm="--no-confirm" not in rest, keys=keys_flag(rest))
        return apply(rest[0], confirm="--no-confirm" not in rest, keys=keys_flag(rest))
    if cmd == "keys":
        return keys_cmd(rest)
    if cmd == "confirm":
        return confirm(rest[0] if rest else None)
    if cmd == "revert":
        return revert(rest[0] if rest else None)
    if cmd == "seen":
        return seen(rest[0] if rest else "")
    if cmd == "_watchdog":
        watchdog(rest[0])
        return None
    if cmd == "rescue":
        return rescue()
    if cmd == "rename":
        if len(rest) < 2:
            raise Fail("rename ID NAME")
        with Lock():
            outs = loadouts()
            if check_id(rest[0]) not in outs:
                raise Fail(f"no loadout '{rest[0]}'")
            d = outs[rest[0]]
            d["name"] = " ".join(rest[1:]).strip() or d.get("name", rest[0])
            save_loadout(rest[0], d)
            return {"renamed": rest[0], "name": d["name"]}
    if cmd == "delete":
        if not rest:
            raise Fail("delete ID")
        with Lock():
            if check_id(rest[0]) == status().get("active"):
                raise Fail("that's the shell you're using; swap to another one first")
            if not os.path.exists(loadout_path(rest[0])):
                raise Fail(f"no loadout '{rest[0]}'")
            trash = os.path.join(STATE, "trash")
            os.makedirs(trash, exist_ok=True)
            os.replace(loadout_path(rest[0]), os.path.join(trash, dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + rest[0] + ".json"))
            return {"deleted": rest[0]}
    if cmd == "config":
        c = config()
        if len(rest) >= 2:
            key, val = rest[0], rest[1]
            if key == "apply" and val in ("restart", "reload"):
                c["apply"] = val
            elif key == "confirmSeconds" and val.isdigit():
                c["confirmSeconds"] = int(val)
            else:
                raise Fail("config apply restart|reload, or config confirmSeconds N")
            write_json(CONFIG, c)
            c = config()
        return c
    raise Fail(f"unknown command '{cmd}' (see --help)")


def keys_flag(rest):
    return "swap" if "--keys" in rest else "keep" if "--keep-keys" in rest else None


def keys_cmd(rest):
    yes = "--yes" in rest
    args = [a for a in rest if a != "--yes"]
    with Lock():
        seed()
    sub = args[0] if args else "status"
    if sub == "status":
        return keys_status()
    if sub == "hook":
        return install_hook(yes)
    if sub == "scan":
        outs = loadouts()
        owners = {}
        for lid, d in outs.items():
            for b in d.get("keybinds") or []:
                owners.setdefault(b["keys"], []).append(lid)
        return {"file": BINDINGS, "binds": [{"line": b["line"], "endLine": b["endLine"], "kind": b["kind"],
                                             "keys": b["keys"], "desc": b["desc"], "lua": b["lua"],
                                             "inLoadouts": owners.get(b["keys"], [])}
                                            for b in parse_binds(read_text(BINDINGS) or "")]}
    if sub == "list":
        if len(args) < 2:
            raise Fail("keys list ID")
        d = loadouts().get(check_id(args[1]))
        if d is None:
            raise Fail(f"no loadout '{args[1]}'")
        plan = keys_plan(d)
        clash = {c["keys"] for c in plan["conflicts"]}
        return {"loadout": args[1], "keys": [dict(i=i, keys=b["keys"], desc=b.get("desc", ""), lua=b["lua"],
                                                  clash=b["keys"] in clash)
                                             for i, b in enumerate(d.get("keybinds") or [])]}
    if sub == "diff":
        if len(args) < 2:
            raise Fail("keys diff ID")
        d = loadouts().get(check_id(args[1]))
        if d is None:
            raise Fail(f"no loadout '{args[1]}'")
        plan = keys_plan(d)
        return {k: plan[k] for k in ("changes", "add", "remove", "conflicts")}
    if sub == "move":
        if len(args) < 3:
            raise Fail("keys move ID LINE [LINE...]")
        try:
            nums = [int(a) for a in args[2:]]
        except ValueError:
            raise Fail("lines are numbers (see keys scan)")
        return move_keys(args[1], nums, yes)
    if sub == "remove":
        if len(args) < 3 or not args[2].isdigit():
            raise Fail("keys remove ID N")
        return remove_key(args[1], int(args[2]), yes)
    if sub == "add":
        if len(args) < 3:
            raise Fail("keys add ID 'o.bind(\"KEYS\", \"what\", \"command\")'")
        return add_key(args[1], " ".join(args[2:]), yes)
    if sub == "backups":
        return {"dir": KEY_BACKUPS, "backups": sorted(os.path.basename(p) for p in glob.glob(os.path.join(KEY_BACKUPS, "*")))}
    if sub == "restore":
        if len(args) < 2:
            raise Fail("keys restore BACKUP")
        return restore_key_backup(args[1], yes)
    raise Fail(f"unknown keys command '{sub}' (see --help)")


def main():
    try:
        out = run(sys.argv[1:])
    except NeedsYes as e:
        # nothing changed: here's what would, for you (or the picker) to ask
        print(json.dumps({"needsYes": True, "plan": e.plan}, ensure_ascii=False))
        sys.exit(2)
    except Fail as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    if out is not None:
        print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
