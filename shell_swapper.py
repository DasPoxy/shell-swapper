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
  delete ID                     (not the active one; keys moved into it go back
                                into bindings.lua: plan first, --yes to do it)
  config [KEY VALUE]            settings: apply = restart | reload, confirmSeconds
  preview [ID]                  picture the bar of the shell on screen, for its loadout
  check-new                     check bars installed since (a new one that's your bar
                                now is saved as a loadout, as its installer set it up)
  check [BAR_ID]                what a bar needs: its own plugins, others' widgets left on it...
  widgets [ID]                  other shells' widgets (and missing ones) on loadouts' bars
  take-off ID [WIDGET...]       take other shells' widgets (or the ones named) off its bar
                                (only the bar: plugins stay; plan first, --yes to do it)
  strip-foreign ID              take other shells' widgets and plugins out of a loadout
  notice-dismiss [BAR_ID]

Keys (a loadout's own keybinds, in hypr/shell_swapper.lua; your bindings.lua
is only touched when you move keys in or out, and always backed up first):
  apply ... --keys | --keep-keys   when the loadout's keys differ from the ones
                                you have: bring them too, or only your own
                                (bindings.lua; no shell's keys at all)
  keys status                   the hook, the keys live now, recent backups
  keys hook                     add the one line that loads loadout keys
  keys scan                     the binds in your bindings.lua (line numbers)
  keys list ID | keys diff ID   a loadout's keys | what swapping to it changes
  keys move ID KEYS...          move binds from bindings.lua into a loadout, by key
                                combo ("SUPER + R") or line number (keys scan);
                                they're commented out there, tagged, and gathered
                                under the loadout's heading
  keys organize                 gather every loadout's tagged keys into its group
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
import shutil
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
# backups/original   your files as they were before Shell-Swapper first changed them (kept for good)
#        /swaps/<t>  a quick copy of shell.json, the keys file and bindings.lua before every swap
#        /loadouts/<id>/<t>.json   a loadout before every change to it
#        /keys       every keybind file before Shell-Swapper writes it
BACKUPS = os.path.join(STATE, "backups")
ORIGINALS = os.path.join(BACKUPS, "original")
SWAP_BACKUPS = os.path.join(BACKUPS, "swaps")
LOADOUT_BACKUPS = os.path.join(BACKUPS, "loadouts")
KEEP_LOADOUT_BACKUPS = 10
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
# Omarchy's. (Slime's isn't a clone of Omarchy's, so it's named here.)
NOTIFIERS = ["omarchy.notifications", "slime.notifications"]

_MANIFESTS = None


def manifests():
    """Every installed plugin's manifest, by id: [(id, folder, manifest)], your
    plugin folder first (its copy wins over Omarchy's). Read once per run."""
    global _MANIFESTS
    if _MANIFESTS is None:
        out, seen = [], set()
        for base in PLUGIN_DIRS:
            for mf in sorted(glob.glob(os.path.join(base, "*", "manifest.json"))):
                m = read_json(mf, None)
                pid = str(m.get("id") or "") if isinstance(m, dict) else ""
                if pid and pid not in seen:
                    seen.add(pid)
                    out.append((pid, os.path.dirname(mf), m))
        _MANIFESTS = out
    return _MANIFESTS


def clone_groups(all_suites):
    """Omarchy services and the copies that replace them, of which only one may
    run: {source: [replacements]}, e.g. omarchy.lock: [a lock screen plugin].
    A copy is a manifest with "omarchy": {"clonedFrom": SOURCE}: a suite's own
    (synoptik.notifications) or one no suite owns, that you installed for every
    shell (a lock screen plugin). Bar widget copies don't count: they run fine
    beside Omarchy's."""
    groups = {"omarchy.notifications": [n for n in NOTIFIERS if n != "omarchy.notifications"]}
    for pid, _, m in manifests():
        meta = m.get("omarchy")
        src = str(meta.get("clonedFrom") or "") if isinstance(meta, dict) else ""
        if not src.startswith("omarchy.") or "bar" in (m.get("kinds") or []) or src == "omarchy.bar":
            continue
        members = groups.setdefault(src, [])
        if pid not in members:
            members.insert(0, pid)
    # only services that can't run twice: Omarchy's original is an installed
    # plugin and not a bar widget (widget copies sit happily beside it)
    kinds = {pid: m.get("kinds") or [] for pid, _, m in manifests()}
    return {src: ms for src, ms in groups.items()
            if ms and (src == "omarchy.notifications" or (src in kinds and "bar-widget" not in kinds[src]))}

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
    write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


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
    for sid, d, m in manifests():
        if "bar" in (m.get("kinds") or []):
            found[sid] = {"id": sid, "name": m.get("name") or sid, "description": m.get("description", ""),
                          "dir": d, "manifest": m}
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
        prefix = suite_id.rsplit(".", 1)[0] + "."
        # a reverse-domain author namespace (io.github.someone.bar) holds the
        # author's other plugins too: those aren't claimed unless the bar's
        # manifest says so (shellSwapper.owns)
        if not re.match(r"(io|com|org|net|dev|app|me|co|xyz)\.[^.]+\.$", prefix) and \
                not re.match(r"(io|com|org|net|dev|app|me|co|xyz)\.github\.[^.]+\.$", prefix):
            r["owns"] = [prefix]
    elif not r["owns"] and suite_id != "omarchy.bar":
        # no dot (celestune-bar): its name without the "bar" part
        base = re.sub(r"[-_]?bar$", "", suite_id)
        r["owns"] = [base] if base and base != suite_id else []
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
    # a group's Omarchy service and the suites' copies follow the loadout; a
    # copy no suite owns is yours, global like your other plugins
    for src, members in clone_groups(all_suites).items():
        out.update([src] + [m for m in members if owner(m, all_suites)])
    for sid in all_suites:
        r = rules(sid, all_suites)
        out.update(r["enable"])
        out.update(r["disable"])
    return out


def plugin_source(d):
    """Where a plugin folder came from: a copy carries .synced-from naming its
    checkout (Slime Shell, Shell-Swapper copy themselves in); else itself."""
    try:
        with open(os.path.join(d, ".synced-from"), encoding="utf-8") as f:
            src = f.readline().strip()
        if src and os.path.isdir(src):
            return os.path.realpath(src)
    except OSError:
        pass
    return d


def default_bar(suite_id, all_suites):
    """A suite's default bar config, for a fresh loadout."""
    r = rules(suite_id, all_suites)
    if r.get("layout"):
        b = read_json(os.path.normpath(os.path.join(plugin_source(all_suites[suite_id]["dir"]), r["layout"])), None)
        if isinstance(b, dict):
            b = dict(b)
            b["id"] = suite_id
            return b
    if suite_id == "omarchy.bar":
        b = (read_json(DEFAULTS_JSON, {}) or {}).get("bar")
        if isinstance(b, dict):
            return b
    return None   # no layout known (a bar with nothing on it is no default)


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


def get_loadout(lid, outs=None):
    """A loadout by id, or Fail. Pass `outs` (loadouts()) to look in that."""
    d = (loadouts() if outs is None else outs).get(check_id(lid))
    if d is None:
        raise Fail(f"no loadout '{lid}'")
    return d


def loadout_names():
    return {lid: d.get("name", lid) for lid, d in loadouts().items()}


def status():
    return read_json(STATUS, {}) or {}


def set_status(**kw):
    s = status()
    s.update(kw)
    write_json(STATUS, s)


def snapshot(cfg, name, suite_id):
    """A loadout of the shell in `cfg`: its bar, and only the plugins a swap
    takes from a loadout (a suite's own, the services suites switch on and
    off). Global ones (Taskchy, your theme manager...) and their settings
    stay in shell.json, whatever the loadout."""
    all_suites = suites()
    ruled = ruled_ids(all_suites)

    def follows(pid):
        return owner(pid, all_suites) is not None or pid in ruled
    return {"name": name, "suite": suite_id, "saved": now(),
            "bar": copy.deepcopy(cfg.get("bar") or {}),
            "plugins": [copy.deepcopy(p) for p in cfg.get("plugins") or [] if follows(entry_id(p))],
            "disabledPlugins": [d for d in cfg.get("disabledPlugins") or [] if follows(str(d))]}


def unique_id(name, taken):
    base = slugify(name)
    lid, n = base, 2
    while lid in taken:
        lid, n = f"{base}-{n}", n + 1
    return lid


def save_loadout(lid, data):
    data = {k: v for k, v in data.items() if k != "id"}
    path = loadout_path(lid)
    old = read_json(path, None)
    if isinstance(old, dict) and old != data:
        # the loadout as it was, before this change
        d = os.path.join(LOADOUT_BACKUPS, lid)
        os.makedirs(d, exist_ok=True)
        write_json(os.path.join(d, dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4] + ".json"), old)
        for f in sorted(glob.glob(os.path.join(d, "*.json")))[:-KEEP_LOADOUT_BACKUPS]:
            os.remove(f)
    write_json(path, data)


def keep_original(path):
    """The first time Shell-Swapper changes one of your files, a copy of it as
    it was goes to backups/original/, and stays there."""
    if not os.path.isfile(path):
        return
    os.makedirs(ORIGINALS, exist_ok=True)
    dest = os.path.join(ORIGINALS, os.path.basename(path))
    if not os.path.exists(dest):
        with open(path, "rb") as f:
            data = f.read()
        with open(dest, "wb") as f:
            f.write(data)
    note = os.path.join(ORIGINALS, "README.txt")
    if not os.path.exists(note):
        with open(note, "w") as f:
            f.write("Your files as they were before Shell-Swapper first changed them.\n"
                    "Shell-Swapper never changes or deletes these.\n")


def migrate_backups():
    """Older layouts: backups/*.json (shell.json before a swap) and
    keybind-backups/ move into backups/swaps and backups/keys."""
    for f in glob.glob(os.path.join(BACKUPS, "*.json")):
        d = os.path.join(SWAP_BACKUPS, os.path.basename(f)[:-5])
        os.makedirs(d, exist_ok=True)
        os.replace(f, os.path.join(d, "shell.json"))
    old = os.path.join(STATE, "keybind-backups")
    if os.path.isdir(old):
        os.makedirs(KEY_BACKUPS, exist_ok=True)
        for f in glob.glob(os.path.join(old, "*")):
            os.replace(f, os.path.join(KEY_BACKUPS, os.path.basename(f)))
        os.rmdir(old)


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
    # one of each Omarchy service and the copies that replace it (its
    # notifications, lock screen, OSD): the one the suite asks for, else one
    # you installed for every shell (no suite's: your choice wins over a
    # suite's own), else the suite's own copy, else Omarchy's own. Another
    # suite's copy never carries over.
    def on(n):
        return n not in disabled and (n.startswith("omarchy.") or any(entry_id(p) == n for p in plugins))
    for src, members in clone_groups(all_suites).items():
        group = members + [src]
        keep = ([n for n in group if n in r["enable"]]
                + [n for n in members if owner(n, all_suites) is None and on(n)]
                + [n for n in members if owner(n, all_suites) == suite and on(n)]
                + [src])[0]
        for n in group:
            if n == keep:
                enable(n)
            elif on(n):
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
KEY_BACKUPS = os.path.join(BACKUPS, "keys")
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
    if path != KEYS_FILE:
        keep_original(path)
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


# ---- keeping bindings.lua tidy: one group per loadout ----
#
# Keys moved into a loadout are gathered under that loadout's heading in
# bindings.lua, a blank line before and after:
#
#   -- SlimeS-Bar keys (Shell-Swapper loadout: slimes-bar)
#   -- Tagged lines belong to that loadout: ...
#   --[shell-swapper:slimes-bar] o.bind("SUPER + R", ...)

GROUP_RE = re.compile(r"^-- (.*) keys \(Shell-Swapper loadout: ([a-z0-9][a-z0-9-]*)\)\s*$")
TAG_RE = re.compile(r"^--\[shell-swapper:([a-z0-9][a-z0-9-]*)\] (.*)$")
OLD_HEAD_RE = re.compile(r"^-- .*(these live in the .* loadout|Shell-Swapper loads them from)")
HEAD_MORE = ("-- Tagged lines belong to that loadout", "-- hypr/shell_swapper.lua while it's your shell")


def group_heading(name, lid):
    return [f"-- {name} keys (Shell-Swapper loadout: {lid})",
            "-- Tagged lines belong to that loadout: Shell-Swapper loads them from",
            "-- hypr/shell_swapper.lua while it's your shell. Untagged lines work in every shell."]


def tagged_blocks(lines):
    """(loadout, start, end) of each tagged statement (end exclusive)."""
    out, i, n = [], 0, len(lines)
    while i < n:
        m = TAG_RE.match(lines[i])
        if not m:
            i += 1
            continue
        j = i + 1
        while j < n:
            m2 = TAG_RE.match(lines[j])
            if m2 and m2.group(1) == m.group(1) and not classify(m2.group(2)):
                j += 1
            else:
                break
        out.append((m.group(1), i, j))
        i = j
    return out


def paragraph_of(lines, i):
    a = i
    while a > 0 and lines[a - 1].strip():
        a -= 1
    b = i
    while b < len(lines) and lines[b].strip():
        b += 1
    return a, b


def organize_text(text, names):
    """bindings.lua with each loadout's tagged keys gathered into its group.
    Nothing else moves; blank lines left behind by a move are tidied."""
    lines = text.split("\n")
    blocks = tagged_blocks(lines)
    lids = []
    for lid, _, _ in blocks:
        if lid not in lids:
            lids.append(lid)
    heads = {}
    for i, l in enumerate(lines):
        m = GROUP_RE.match(l)
        if m:
            heads.setdefault(m.group(2), i)
    target, adopt = {}, {}
    for lid in lids:
        if lid in heads:
            target[lid] = paragraph_of(lines, heads[lid])
            continue
        first = next(b for b in blocks if b[0] == lid)
        a, b = paragraph_of(lines, first[1])
        others = [x for x in blocks if a <= x[1] < b and x[0] != lid]
        # only a block that's all comments and this loadout's keys becomes its group
        if not others and all(lines[k].lstrip().startswith("--") and not GROUP_RE.match(lines[k]) for k in range(a, b)):
            target[lid], adopt[lid] = (a, b), True
        else:
            target[lid] = None
    # a comment between two of a loadout's moving keys goes with them
    def outside(blk):
        t = target[blk[0]]
        return t is None or not (t[0] <= blk[1] < t[1])
    for k in range(1, len(blocks)):
        p, q = blocks[k - 1], blocks[k]
        if p[0] == q[0] and outside(p) and outside(q) and p[2] < q[1] and all(
                lines[x].lstrip().startswith("--") and not TAG_RE.match(lines[x]) and not GROUP_RE.match(lines[x])
                for x in range(p[2], q[1])):
            blocks[k] = (q[0], p[2], q[2])
    moving = {lid: [] for lid in lids}
    skip = set()
    for lid, a, b in blocks:
        t = target[lid]
        if t is None or not (t[0] <= a < t[1]):
            moving[lid].append(lines[a:b])
            skip.update(range(a, b))
    # a group left with nothing but its heading goes too
    for lid, h in heads.items():
        a, b = paragraph_of(lines, h)
        body = [k for k in range(a, b) if k not in skip and not GROUP_RE.match(lines[k])
                and not lines[k].startswith(HEAD_MORE)]
        if not body and not moving.get(lid):
            skip.update(range(a, b))
    starts = {t[0]: lid for lid, t in target.items() if t}
    ends = {}
    for lid, t in target.items():
        if t:
            ends.setdefault(t[1], []).append(lid)
    out, removed = [], False

    def emit(l):
        nonlocal removed
        if not l.strip():
            if removed and out and not out[-1].strip():
                return
        else:
            removed = False
        out.append(l)

    for i, l in enumerate(lines):
        for lid in ends.get(i, []):
            for blk in moving[lid]:
                out.extend(blk)
        if i in starts and adopt.get(starts[i]):
            lid = starts[i]
            out.extend(group_heading(names.get(lid, lid), lid))
        if i in skip:
            removed = True
            continue
        # an adopted block's old hand-written heading gives way to the standard one
        if any(t and adopt.get(lid) and t[0] <= i < t[1] for lid, t in target.items()) and OLD_HEAD_RE.match(l):
            continue
        m = GROUP_RE.match(l)
        if m and m.group(2) in names and m.group(1) != names[m.group(2)]:
            l = group_heading(names[m.group(2)], m.group(2))[0]   # renamed loadout
        emit(l)
    for lid in ends.get(len(lines), []):
        for blk in moving[lid]:
            out.extend(blk)
    # loadouts with no group yet: a new one, after the last group (or at the end)
    new = [lid for lid in lids if target[lid] is None]
    if new:
        last = max((i for i, l in enumerate(out) if GROUP_RE.match(l)), default=None)
        at = paragraph_of(out, last)[1] if last is not None else len(out)
        while at == len(out) and out and not out[-1].strip():
            out.pop()
            at = len(out)
        chunk = []
        for lid in new:
            chunk += [""] + group_heading(names.get(lid, lid), lid) + [l for blk in moving[lid] for l in blk]
        if at < len(out) and out[at].strip():
            chunk.append("")
        out[at:at] = chunk
    result = "\n".join(out)
    if text.endswith("\n") and not result.endswith("\n"):
        result += "\n"
    return result


def organize_keys(yes):
    with Lock():
        text = read_text(BINDINGS)
        if text is None:
            raise Fail(f"can't read {BINDINGS}")
        names = loadout_names()
        new = organize_text(text, names)
        if new == text:
            return {"organized": False, "already": True}
        import difflib
        diff = list(difflib.unified_diff(text.split("\n"), new.split("\n"), "bindings.lua", "bindings.lua (tidied)", lineterm="", n=1))
        plan = {"action": "gather each loadout's keys into its own group in bindings.lua",
                "diff": diff, "keysChange": False, "backupTo": KEY_BACKUPS}
        if not yes:
            raise NeedsYes(plan)
        key_backup(BINDINGS)
        write_text(BINDINGS, new)
        hypr_reload()
        return dict(plan, organized=True)


def tag_lines(stmt, lid):
    return "\n".join(MOVED_TAG.format(lid) + l for l in stmt.split("\n"))


def move_keys(lid, picks, yes):
    """Move binds from bindings.lua into loadout `lid`: they're commented out
    there (tagged, so they can come back), with any hl.unbind of the same key."""
    with Lock():
        outs = loadouts()
        get_loadout(lid, outs)
        if not hook_file():
            raise Fail("loadout keys aren't loaded yet: set that up first (shell-swapper keys hook)")
        text = read_text(BINDINGS)
        if text is None:
            raise Fail(f"can't read {BINDINGS}")
        allb = parse_binds(text)
        # picks: key combos ("SUPER + R": they can't go stale) or line numbers
        lines_ = {int(p) for p in picks if str(p).isdigit()}
        keys_ = {norm_keys(p) for p in picks if not str(p).isdigit()}
        picked = [b for b in allb if b["kind"] == "bind" and (b["line"] in lines_ or b["keys"] in keys_)]
        missing = [p for p in picks if not str(p).isdigit() and norm_keys(p) not in {b["keys"] for b in picked}]
        if missing:
            raise Fail("not bound in bindings.lua: " + ", ".join(missing))
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
        names = loadout_names()
        write_text(BINDINGS, organize_text(text, names))
        if active and hook_file():
            write_keys(keys_plan(d)["text"])
        hypr_reload()
        return dict(plan, moved=True)


def release_keys(text, lid, keys=None, imported=()):
    """bindings.lua with loadout `lid`'s tagged lines let go: back to plain
    lines, except keys Shell-Swapper imported (`imported`: they were never
    yours), which are dropped. Only the key combos in `keys`, or all of them;
    lines an import replaced come back with it. Returns (text, back, dropped):
    the 1-based first lines of what came back and of what went."""
    tags = (MOVED_TAG.format(lid), f"--[shell-swapper:{lid}:replaced] ")
    lines, drop, back, gone, i = text.split("\n"), set(), [], [], 0
    while i < len(lines):
        t = next((x for x in tags if lines[i].startswith(x)), None)
        info = classify(lines[i][len(t):]) if t else None
        if not info:
            i += 1
            continue
        # the statement's first line, then the rest of it (tagged lines that
        # don't start a statement of their own)
        span, j = [i], i + 1
        while j < len(lines) and lines[j].startswith(t) and not classify(lines[j][len(t):]):
            span.append(j)
            j += 1
        if keys is None or info["keys"] in keys:
            if t == tags[0] and info["keys"] in imported:
                drop.update(span)   # Shell-Swapper added it: it goes (backed up), not into every shell
                gone.append(i + 1)
            else:
                for x in span:
                    lines[x] = lines[x][len(t):]
                back.append(i + 1)
        i = j
    return "\n".join(l for x, l in enumerate(lines) if x not in drop), back, gone


def remove_key(lid, idx, yes):
    """Take a key out of a loadout. If it came from bindings.lua, it goes back
    there (un-commented)."""
    with Lock():
        d = get_loadout(lid)
        binds = d.get("keybinds") or []
        if not 0 <= idx < len(binds):
            raise Fail(f"no key {idx} in {lid}")
        b = binds[idx]
        text = read_text(BINDINGS) or ""
        imported = {b["keys"]} if b.get("from") == "import" else set()
        new_text, back, gone = release_keys(text, lid, {b["keys"]}, imported)
        active = status().get("active", "") == lid
        plan = {"action": "take " + b["keys"] + " out of " + d.get("name", lid), "loadout": lid,
                "keys": b["keys"], "desc": b.get("desc", ""), "backToBindings": back, "dropFromBindings": gone,
                "activeNow": active, "backupTo": KEY_BACKUPS}
        live = active or back or gone
        if live and not yes:
            raise NeedsYes(plan)
        d["keybinds"] = binds[:idx] + binds[idx + 1:]
        save_loadout(lid, d)
        if back or gone:
            key_backup(BINDINGS)
            write_text(BINDINGS, organize_text(new_text, loadout_names()))
        if active and hook_file():
            write_keys(keys_plan(d)["text"])
        if live:
            hypr_reload()
        return dict(plan, removed=True)


def delete_loadout(lid, yes):
    """Delete a loadout (into the trash). Keys moved into it from bindings.lua
    go back there as they were; ones it imported go (backed up first)."""
    with Lock():
        d = get_loadout(lid)
        if lid == status().get("active"):
            raise Fail("that's the shell you're using; swap to another one first")
        name = d.get("name", lid)
        text = read_text(BINDINGS) or ""
        imported = {b["keys"] for b in d.get("keybinds") or [] if b.get("from") == "import"}
        new_text, back, gone = release_keys(text, lid, None, imported)
        plan = {"action": "delete " + name, "loadout": lid, "backToBindings": back, "dropFromBindings": gone,
                "note": "keys moved into it go back into bindings.lua (for every shell)" if back else "",
                "backupTo": KEY_BACKUPS}
        if (back or gone) and not yes:
            raise NeedsYes(plan)
        if back or gone:
            # its group heading becomes a plain note: the keys under it are yours again
            lines = []
            for l in new_text.split("\n"):
                m = GROUP_RE.match(l)
                if m and m.group(2) == lid:
                    lines.append(f"-- Keys that were in the {name} loadout (deleted {now()})")
                elif not (l.startswith(HEAD_MORE) and lines and lines[-1].startswith(("-- Keys that were in", HEAD_MORE))):
                    lines.append(l)
            key_backup(BINDINGS)
            write_text(BINDINGS, organize_text("\n".join(lines), {k: v for k, v in loadout_names().items() if k != lid}))
            hypr_reload()
        trash = os.path.join(STATE, "trash")
        os.makedirs(trash, exist_ok=True)
        os.replace(loadout_path(lid), os.path.join(trash, dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + lid + ".json"))
        return dict(plan, deleted=lid)


def add_key(lid, lua, yes):
    with Lock():
        outs = loadouts()
        get_loadout(lid, outs)
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


# ---- a shell's actions: its IPC calls, for keys of its own ------------------
#
# Every IpcHandler in a suite's own plugins (and each of its panels, opened
# with `omarchy-shell shell toggle <plugin>`) is something a key could do in
# that shell. Each gets a category (dashboard, launcher, settings...) from its
# name, arguments and comment, so a key set for one shell can be offered for
# the similar call in the others: a template, reviewed before it's applied.

TEMPLATES = os.path.join(CONF_DIR, "templates.json")
QUERY_RE = re.compile(r"^(status|ping|version|is[A-Z].*|get[A-Z].*|list[A-Z].*|.*State|.*Status|.*List[A-Z]?.*)$")

CATEGORIES = [
    ("dashboard", "Dashboard / command centre", ["dashboard", "command centre", "command center", "control center",
                                                 "controlcenter", "home"]),
    ("launcher", "App launcher", ["launcher", "applauncher", "apps", "drawer", "app launcher"]),
    ("settings", "Settings", ["settings"]),
    ("wallpapers", "Wallpapers", ["wallpaper", "wallpapers", "background"]),
    ("system", "System monitor", ["system", "monitor", "metrics", "stats"]),
    ("tasks", "Tasks", ["tasks", "kanban", "todo", "todos"]),
    ("media-play", "Play / pause", ["playpause", "play/pause", "play / pause", "play pause", "toggle play"]),
    ("media-next", "Next track", ["next track", "media next", "nexttrack"]),
    ("media-prev", "Previous track", ["previous track", "media previous", "prevtrack"]),
    ("lyrics", "Lyrics / karaoke", ["lyrics", "karaoke"]),
    ("notifications", "Notifications", ["notification", "notifications", "history"]),
    ("dnd", "Do not disturb", ["dnd", "do not disturb"]),
    ("power", "Power menu", ["power"]),
    ("layer", "Above / behind windows", ["layer", "above", "behind"]),
    ("plugins", "Plugins", ["plugins"]),
]
CATEGORY_NAMES = {c[0]: c[1] for c in CATEGORIES}


def split_words(s):
    """camelCase and kebab-case into words: toggleDashboard -> toggle dashboard."""
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(s))
    return re.sub(r"[^a-zA-Z0-9/]+", " ", s).lower().strip()


def categorize(text, media=False):
    words = " " + split_words(text) + " "
    best, score = "", 0
    for cid, _, keys in CATEGORIES:
        sc = sum(3 if " " + k + " " in words else 0 for k in keys)
        if cid == "media-next" and media and " next " in words:
            sc += 3
        if cid == "media-prev" and media and " previous " in words:
            sc += 3
        if cid == "media-play" and media and (" toggle " in words or " play pause " in words):
            sc += 2
        if sc > score:
            best, score = cid, sc
    return best, score


def arg_values(comment):
    """Values a comment lists for an argument: 'on a tab: home, system, wallpapers'."""
    tail = comment.rsplit(":", 1)[-1] if ":" in comment else comment
    vals = [v.strip(" .()'\"") for v in re.split(r"[|,]", tail)]
    vals = [v for v in vals if re.fullmatch(r"[a-z][a-z0-9-]{0,20}", v)]
    return vals if len(vals) >= 2 else []


def scan_ipc(path):
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return []
    out = []
    for m in re.finditer(r"\bIpcHandler\s*\{", text):
        i, depth = m.end(), 1
        while i < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        block = text[m.end():i - 1]
        tm = re.search(r"^\s*target:\s*(.+?)\s*$", block, re.M)
        if not tm:
            continue
        t = tm.group(1).strip()
        if t[:1] in "\"'":
            target = t.strip("\"'")
        else:
            pm = re.search(r"property\s+string\s+" + re.escape(t.split(".")[-1]) + r"\s*:\s*[\"']([^\"']+)[\"']", text)
            if not pm:
                continue
            target = pm.group(1)
        blines = block.split("\n")
        seen_fns = []
        for k, l in enumerate(blines):
            fm = re.match(r"\s*function\s+(\w+)\s*\(([^)]*)\)\s*(?::\s*(\w+))?", l)
            if not fm:
                continue
            params = [p.strip() for p in fm.group(2).split(",") if p.strip()]
            params = [p.split(":")[0].strip() for p in params]
            com, j = [], k - 1
            while j >= 0 and blines[j].strip().startswith("//"):
                com.insert(0, blines[j].strip()[2:].strip())
                j -= 1
            comment = " ".join(com)
            vals = arg_values(comment)
            # a function with no comment of its own shares the values of one
            # above it taking the same arguments (tab(name) / toggleTab(name))
            if not vals and params:
                vals = next((f["values"] for f in reversed(seen_fns) if f["params"] == params and f["values"]), [])
            a = {"target": target, "function": fm.group(1), "params": params, "returns": fm.group(3) or "",
                 "comment": comment, "values": vals}
            seen_fns.append(a)
            out.append(a)
    return out


def suite_plugins(sid, all_suites):
    """The suite's own plugin folders: those its id prefixes own (the bar's
    for Omarchy: its first-party plugins)."""
    out = {}
    r = rules(sid, all_suites)
    for pid, d, m in manifests():
        mine = any(pid.startswith(p) for p in r["owns"]) if r["owns"] else \
            (sid == "omarchy.bar" and os.path.dirname(d) == PLUGIN_DIRS[1])
        if mine:
            out[pid] = {"dir": os.path.realpath(d), "manifest": m}
    return out


def ipc_command(target, function, args):
    return " ".join(["omarchy-shell", target, function] + [shlex.quote(a) for a in args])


def actions_for(lid_or_suite, show_all=False):
    """What keys could do in a loadout's shell: one choice per call (and per
    listed argument value), with its category."""
    all_suites, outs = suites(), loadouts()
    if lid_or_suite.startswith("suite:"):
        sid, lout = lid_or_suite[6:], None
    else:
        lout = get_loadout(lid_or_suite, outs)
        sid = lout.get("suite", "")
    if sid not in all_suites:
        raise Fail(f"{sid} isn't installed")
    bound = {}
    for b in (lout or {}).get("keybinds") or []:
        bound.setdefault(bind_command(b["lua"]), b["keys"])
    choices = []
    for pid, info in sorted(suite_plugins(sid, all_suites).items()):
        name = info["manifest"].get("name") or pid
        kinds = info["manifest"].get("kinds") or []
        if any(k in kinds for k in ("panel", "overlay", "menu")):
            cmd = f"omarchy-shell shell toggle {pid} '{{}}'"
            cat, sc = categorize(pid + " " + name)
            choices.append({"plugin": pid, "pluginName": name, "label": "Open " + name, "command": cmd,
                            "call": f"shell toggle {pid}", "comment": info["manifest"].get("description", ""),
                            "category": cat, "score": sc, "args": [], "shared": False})
        for q in glob.glob(os.path.join(info["dir"], "**", "*.qml"), recursive=True):
            for a in scan_ipc(q):
                if QUERY_RE.match(a["function"]):
                    continue   # a question (status, ping...), not something to do
                shared = a["target"].startswith("omarchy.") or a["target"] == "notifications"
                if shared and not show_all and sid != "omarchy.bar":
                    continue
                media = "media" in a["target"]
                # (not the comment: comments mention other things too often)
                base_text = f'{a["target"]} {a["function"]}'
                if len(a["params"]) == 1 and a["values"]:
                    for v in a["values"]:
                        # the value says what it is (toggleTab system: the system monitor)
                        cat, sc = categorize(v, media)
                        if not sc:
                            cat, sc = categorize(f'{a["target"]} {a["function"]} {v}', media)
                        choices.append({"plugin": pid, "pluginName": name, "label": f'{split_words(a["function"])}: {v}',
                                        "command": ipc_command(a["target"], a["function"], [v]),
                                        "call": f'{a["target"]} {a["function"]} {v}', "comment": a["comment"],
                                        "category": cat, "score": sc, "args": [], "shared": shared})
                else:
                    cat, sc = categorize(base_text if not a["params"] else f'{a["target"]} {a["function"]}', media)
                    # a suite's own shell target, toggled or opened bare: its main panel
                    if not sc and a["function"] in ("toggle", "open") and not a["params"] \
                            and re.search(r"(^|-)shell$", a["target"]):
                        cat, sc = "dashboard", 2
                    choices.append({"plugin": pid, "pluginName": name, "label": split_words(a["function"]),
                                    "command": ipc_command(a["target"], a["function"], [f"<{p}>" for p in a["params"]]),
                                    "call": f'{a["target"]} {a["function"]}', "comment": a["comment"],
                                    "category": cat, "score": sc, "args": a["params"], "shared": shared})
    # the loadout's own keys are things it can do too (Ruixen's settings open
    # through a launcher payload no IPC scan would find)
    for b in (lout or {}).get("keybinds") or []:
        cmd = bind_command(b["lua"])
        if cmd and cmd not in {c["command"] for c in choices}:
            cat, sc = categorize(b.get("desc", ""), "media" in cmd)   # your description says what it is
            if not sc:
                cat, sc = categorize(cmd, "media" in cmd)
            choices.insert(0, {"plugin": "", "pluginName": "its keys", "label": b.get("desc", "") or cmd, "command": cmd,
                               "call": cmd, "comment": "", "category": cat, "score": sc + 1, "args": [], "shared": False})
    seen, uniq = set(), []
    for c in choices:
        if c["command"] not in seen:
            seen.add(c["command"])
            c["bound"] = bound.get(c["command"], "")
            c["categoryName"] = CATEGORY_NAMES.get(c["category"], "")
            uniq.append(c)
    return {"loadout": lid_or_suite, "suite": sid, "suiteName": all_suites[sid]["name"], "actions": uniq,
            "categories": [{"id": c[0], "name": c[1]} for c in CATEGORIES]}


def lua_text(lit):
    """The text of one Lua string literal."""
    if lit.startswith("[["):
        return lit[2:-2]
    return re.sub(r"\\(.)", r"\1", lit[1:-1])


def bind_command(lua):
    """What a bind runs: the string in hl.dsp.exec_cmd(...) if it has one,
    else its last string (o.bind's command; an options table after it, like
    { description = ... }, doesn't count)."""
    m = re.search(r"hl\.dsp\.exec_cmd\(\s*(" + LUA_STR_RE.pattern + r")\s*\)", lua, re.S)
    if m:
        return lua_text(m.group(1))
    lua = re.sub(r",\s*\{[^{}]*\}\s*\)\s*$", ")", lua)
    strs = re.findall(r"""\[\[(.*?)\]\]|"((?:\\.|[^"\\])*)"|'((?:\\.|[^'\\])*)'""", lua, re.S)
    vals = [next(g for g in t if g != "") if any(t) else "" for t in strs]
    return vals[-1].replace('\\"', '"') if vals else ""


# A Lua string literal: "...", '...' or [[...]] (a [==[ long string is left
# unmatched on purpose, so a bind using one isn't taken as plain).
LUA_STR_RE = re.compile(r"""\[\[.*?\]\]|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'""", re.S)
# A bind whose arguments are only text: o.bind("KEYS", "what", "command"),
# hl.bind("KEYS", hl.dsp.exec_cmd("command"), { description = "what" })...
PLAIN_BIND_RE = re.compile(r"""^(?:o\.bind|o\.bind_toggle|hl\.bind)\((?:S,)*(?:S|hl\.dsp\.exec_cmd\(S\))"""
                           r"""(?:,\{(?:\w+=(?:S|true|false|\d+),?)*\})?\)$""")


def plain_bind(stmt):
    """True when every argument of a bind is plain text (or exec_cmd of text,
    or a table of plain options): nothing in it can run as Lua code when
    Hyprland loads its config."""
    skeleton = re.sub(r"\s+", "", LUA_STR_RE.sub("S", stmt))
    return bool(PLAIN_BIND_RE.match(skeleton))


def lua_str(s):
    if '"' not in s and "\\" not in s and "\n" not in s:
        return '"' + s + '"'
    if "]]" not in s:
        return "[[" + s + "]]"
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def bind_lua(keys, desc, command):
    return f"o.bind({lua_str(keys)}, {lua_str(desc)}, {lua_str(command)})"


def live_binds():
    """Hyprland's binds now, by key combo (for "that's Omarchy's default for...")."""
    path = os.environ.get("SHELL_SWAPPER_HYPRCTL_BINDS")
    try:
        if path:
            data = read_json(path, [])
        else:
            data = json.loads(subprocess.run(["hyprctl", "binds", "-j"], capture_output=True, text=True, timeout=3).stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}
    out = {}
    for b in data or []:
        m = int(b.get("modmask", 0))
        mods = [n for n, bit in (("SUPER", 64), ("CTRL", 4), ("ALT", 8), ("SHIFT", 1)) if m & bit]
        out.setdefault(norm_keys(" + ".join(mods + [str(b.get("key", ""))])), b.get("description") or b.get("arg") or "")
    return out


def check_keys(lid, keys):
    """Who uses a key combo now: your bindings.lua, this loadout, Omarchy."""
    k = norm_keys(keys)
    mine = [b for b in user_binds() if b["keys"] == k]
    lout = loadouts().get(check_id(lid)) or {}
    here = [b for b in lout.get("keybinds") or [] if b["keys"] == k]
    cur_file = {b["keys"] for b in parse_binds(read_text(KEYS_FILE) or "") if b["kind"] == "bind"}
    live = live_binds()
    default = live.get(k, "") if not mine and k not in cur_file else ""
    return {"keys": k, "bindings": [{"line": b["line"], "desc": b["desc"]} for b in mine],
            "loadout": [{"desc": b.get("desc", "")} for b in here], "omarchy": default}


def set_many(items, yes):
    """Give loadouts keys: [{loadout, keys, command, desc}]. A loadout's key
    for the same combo, or for the same command, is replaced."""
    with Lock():
        outs = loadouts()
        active = status().get("active", "")
        mine = {b["keys"]: b["line"] for b in user_binds()}
        changes, touched, unchanged = [], {}, []
        for it in items:
            lid = str(it.get("loadout", ""))
            get_loadout(lid, outs)
            k = norm_keys(it.get("keys", ""))
            cmd = str(it.get("command", "")).strip()
            if not k or not cmd or "<" in cmd:
                raise Fail("each key needs a key combo and a complete command (fill in its arguments)")
            desc = str(it.get("desc", "")).strip() or cmd
            d = touched.setdefault(lid, outs[lid])
            old = d.get("keybinds") or []
            # already that key for that call: leave it (and its description) be
            if any(b["keys"] == k and bind_command(b["lua"]) == cmd for b in old):
                unchanged.append({"loadout": lid, "loadoutName": d.get("name", lid), "keys": k, "command": cmd})
                continue
            new_lua = bind_lua(k, desc, cmd)
            replaced = [b for b in old if (b["keys"] == k or cmd == bind_command(b["lua"])) and b["lua"] != new_lua]
            old = [b for b in old if b["lua"] != new_lua]
            d["keybinds"] = [b for b in old if b not in replaced] + [{"keys": k, "desc": desc, "lua": new_lua}]
            changes.append({"loadout": lid, "loadoutName": d.get("name", lid), "keys": k, "desc": desc,
                            "command": cmd, "replaces": [{"keys": b["keys"], "desc": b.get("desc", "")} for b in replaced],
                            "clashLine": mine.get(k, 0), "activeNow": lid == active})
        plan = {"action": "set " + ("these keys" if len(changes) > 1 else "this key"), "set": changes,
                "unchanged": unchanged, "backupTo": KEY_BACKUPS}
        if not changes:
            return dict(plan, done=True, nothing=True)
        if not yes:
            raise NeedsYes(plan)
        live = False
        for lid, d in touched.items():
            save_loadout(lid, d)
            if lid == active and hook_file():
                write_keys(keys_plan(d)["text"])
                live = True
        if live:
            hypr_reload()
        return dict(plan, done=True)


def templates():
    t = read_json(TEMPLATES, [])
    return t if isinstance(t, list) else []


def propose(keys, command, from_lid):
    """A key set for one shell, offered for the similar call in every other
    loadout: the best matches by category, for you to review."""
    outs = loadouts()
    src = actions_for(from_lid, show_all=True)
    pick = next((a for a in src["actions"] if a["command"] == command), None)
    cat = pick["category"] if pick else categorize(command)[0]
    out = []
    for lid, d in outs.items():
        if lid == from_lid or not d.get("suite"):
            continue
        try:
            acts = actions_for(lid)["actions"]
        except Fail:
            continue
        def rank(a):
            f = a["call"].split(" ")[1] if " " in a["call"] else ""
            return -(a["score"] + (1 if f.startswith("toggle") else 0.5 if f.startswith(("open", "show")) else
                                   -2 if f.startswith(("close", "hide")) else 0))
        cands = sorted([a for a in acts if cat and a["category"] == cat and not a["args"]], key=rank)
        out.append({"loadout": lid, "loadoutName": d.get("name", lid), "suite": d.get("suite"),
                    "candidates": [{"label": a["label"], "command": a["command"], "pluginName": a["pluginName"],
                                    "comment": a["comment"][:120], "bound": a.get("bound", "")} for a in cands[:6]],
                    "clash": check_keys(lid, keys)})
    return {"keys": norm_keys(keys), "command": command, "from": from_lid, "category": cat,
            "categoryName": CATEGORY_NAMES.get(cat, ""), "label": pick["label"] if pick else command,
            "proposals": out}


def save_template(name, keys, category, binds):
    with Lock():
        ts = [t for t in templates() if t.get("name") != name and t.get("keys") != norm_keys(keys)]
        ts.append({"name": name, "keys": norm_keys(keys), "category": category, "binds": binds, "saved": now()})
        write_json(TEMPLATES, ts)
        return {"saved": name}


# ---- importing a shell's recommended keys -----------------------------------
#
# Paste binds from a shell's repo (its README, docs, an install script) and
# they become that loadout's keys, in its group in bindings.lua too. Both
# styles: Omarchy's Lua (o.bind / hl.bind) and Hyprland's (bind = MODS, KEY,
# exec, command; bindd with a description). `keys find` looks through the
# shell's own checkout for them, keeping only binds that call that shell.

HYPR_BIND_RE = re.compile(r"^\s*bind([a-z]*)\s*=\s*(.+)$")


def hypr_conf_bind(line):
    """'bind = SUPER SHIFT, R, exec, cmd' -> (keys, desc, command) or a reason."""
    m = HYPR_BIND_RE.match(line)
    if not m:
        return None
    flags, rest = m.group(1), m.group(2)
    parts = [p.strip() for p in rest.split(",")]
    has_desc = "d" in flags
    need = 5 if has_desc else 4
    if len(parts) < need - 1:
        return "not a whole bind line"
    mods, key = parts[0], parts[1]
    desc = parts[2] if has_desc else ""
    disp = parts[3 if has_desc else 2].lower()
    arg = ",".join(parts[4 if has_desc else 3:]).strip()
    if disp != "exec":
        return f"only exec binds can come in (this one is '{disp}')"
    mm = [x for x in re.split(r"[\s_+]+", mods) if x]
    mm = ["SUPER" if x.lower() in ("$mainmod", "$mod", "super", "mod4", "win") else x for x in mm]
    keys = norm_keys(" + ".join(mm + [key]))
    return (keys, desc or arg, arg)


# ---- keybinds written in prose (READMEs, notes) --------------------------------
#
# READMEs often list keys as a table or a list rather than as code:
#   | Super + Alt + C | Command centre | `omarchy-shell slime-shell toggleTab home` |
#   - `Super+R`: the launcher (`omarchy-shell slime-launcher apps`)
# A line counts only with a whole key combo (modifiers + one key) AND a
# command beside it; every bind found (prose or code) has its combo checked.

MOD_ALIASES = {"super": "SUPER", "mod4": "SUPER", "mod": "SUPER", "meta": "SUPER", "win": "SUPER",
               "windows": "SUPER", "cmd": "SUPER", "$mainmod": "SUPER", "$mod": "SUPER", "logo": "SUPER",
               "ctrl": "CTRL", "control": "CTRL", "ctl": "CTRL", "alt": "ALT", "shift": "SHIFT"}
KEY_ALIASES = {"enter": "RETURN", "esc": "ESCAPE", "del": "DELETE", "pageup": "PRIOR", "pagedown": "NEXT",
               "pgup": "PRIOR", "pgdn": "NEXT", "bksp": "BACKSPACE", "/": "SLASH", ".": "PERIOD", ",": "COMMA"}
KEY_NAMES = {"RETURN", "SPACE", "TAB", "ESCAPE", "BACKSPACE", "DELETE", "SLASH", "PERIOD", "COMMA", "MINUS",
             "EQUAL", "GRAVE", "SEMICOLON", "APOSTROPHE", "BACKSLASH", "BRACKETLEFT", "BRACKETRIGHT", "LEFT",
             "RIGHT", "UP", "DOWN", "HOME", "END", "PRINT", "INSERT", "PRIOR", "NEXT"}
_MOD = r"(?:super|mod4|mod|meta|win|windows|cmd|logo|ctrl|control|ctl|alt|shift|\$mainmod|\$mod)"
_KEY = (r"(?:f(?:2[0-4]|1[0-9]|[1-9])|return|enter|space|tab|escape|esc|backspace|bksp|delete|del|slash|period|"
        r"comma|minus|equal|grave|semicolon|apostrophe|backslash|bracketleft|bracketright|left|right|up|down|home|"
        r"end|print|insert|pageup|pagedown|pgup|pgdn|[a-z0-9]|[/.,])")
COMBO_RE = re.compile(rf"(?<![\w$])({_MOD}(?:\s*[+\-]\s*{_MOD})*\s*[+\-]\s*{_KEY})(?![\w])", re.I)
CMD_START = re.compile(r"^(omarchy-|uwsm|hyprctl|notify-send|xdg-open|qs |quickshell |[a-z][\w.-]*-[\w.-]+ )")


def combo_problem(keys):
    """'' if a combo is a well-formed bind, else what's wrong with it."""
    parts = [p.strip().upper() for p in str(keys).split("+")]
    if any(not p for p in parts):
        return "a '+' with nothing on one side"
    mods = [p for p in parts if p in MODS]
    rest = [p for p in parts if p not in MODS]
    if len(rest) != 1:
        return "no key after the modifiers" if not rest else "more than one key (" + " + ".join(rest) + ")"
    k = rest[0]
    if not (re.fullmatch(r"[A-Z0-9]", k) or re.fullmatch(r"F(?:2[0-4]|1[0-9]|[1-9])", k) or k in KEY_NAMES
            or re.fullmatch(r"MOUSE:\d+|XF86\w+|CODE:\d+", k)):
        return f"'{k}' isn't a key name Hyprland knows"
    if len(set(mods)) != len(mods):
        return "a modifier twice"
    return ""


def norm_combo(text):
    parts = [p for p in re.split(r"\s*[+\-]\s*", text.strip()) if p]
    out = []
    for p in parts:
        low = p.lower()
        out.append(MOD_ALIASES.get(low) or KEY_ALIASES.get(low) or p.upper())
    return norm_keys(" + ".join(out))


def parse_prose(line):
    """One line of a README / notes -> (bind or None, reason it's left out or '')."""
    raw = line.strip()
    clean = re.sub(r"</?kbd>|\*\*|__", "", raw)
    clean = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", clean)       # list markers
    spans = re.findall(r"`([^`]+)`", clean)
    flat = re.sub(r"`", "", clean)
    m = COMBO_RE.search(re.sub(r"\s*`\s*", "", clean) if re.search(r"`[A-Za-z$]+`\s*[+\-]\s*`", clean) else flat)
    listing = bool(re.match(r"^\s*(?:[-*+]|\d+[.)])\s+", raw) or "|" in raw or "`" in raw or "<kbd>" in raw.lower())
    if not m:
        # a modifier and a '+', a command, but no combo Hyprland could bind
        if re.search(r"(?<![\w$])(?:super|ctrl|control|alt|shift|\$mainmod|\$mod|mod4?)\s*\+", flat, re.I) and (
                any(" " in x.strip() for x in spans) or "omarchy-shell" in flat):
            return None, "its key combo isn't one Hyprland can bind (check the + signs and the key)"
        return None, ""
    combo = m.group(1)
    keys = norm_combo(combo)
    # the command: a backticked span that isn't the combo, else an omarchy-shell call
    cmd = ""
    for s in spans:
        s2 = s.strip()
        if COMBO_RE.fullmatch(s2) or norm_combo(s2) == keys or not s2:
            continue
        if " " in s2 or CMD_START.match(s2):
            cmd = s2
            break
    if not cmd:
        mm = re.search(r"(omarchy-shell\s+[^|`)\]\n]+)", flat)
        cmd = mm.group(1).strip().rstrip(".,;") if mm else ""
    if not cmd:
        # plain prose that mentions a key ("Alt-tab still works") isn't a bind
        return None, ("a key, but no command beside it" if listing else "")
    # what it does: a table's other cell, or the rest of the line
    if "|" in clean:
        cells = [c.replace("`", "").strip() for c in clean.strip().strip("|").split("|")]
        rest = [c for c in cells if c and c != cmd and not COMBO_RE.search(c) and c != combo]
        desc = rest[0] if rest else ""
    else:
        desc = flat.replace(combo, " ").replace(cmd, " ")
        desc = re.sub(r"[()\[\]:→=>—–|]+", " ", desc)
        desc = re.sub(r"\s+", " ", desc).strip(" -.,;")
    desc = (desc or cmd)[:80]
    return {"keys": keys, "desc": desc, "command": cmd, "lua": bind_lua(keys, desc, cmd),
            "from": raw[:120], "loose": True}, ""


def parse_import(text, prose=True):
    """Pasted text -> binds found, and the lines that weren't. With prose,
    keybinds written as README tables / lists count too."""
    found, skipped, lua_lines, prose_found = [], [], [], []
    for raw in text.replace("\r", "").split("\n"):
        line = re.sub(r"^\s*(//+|\*)\s?", "", raw)            # recipes inside code comments
        if prose and not HYPR_BIND_RE.match(line) and not re.search(r"\b(o\.bind|hl\.bind|hl\.unbind)\s*\(", line):
            b, why = parse_prose(line)
            if b:
                prose_found.append(b)
                lua_lines.append("")
                continue
            if why:
                skipped.append({"line": line.strip()[:120], "why": why})
                lua_lines.append("")
                continue
        if HYPR_BIND_RE.match(line):
            r = hypr_conf_bind(line)
            if isinstance(r, tuple):
                k, d, c = r
                found.append({"keys": k, "desc": d, "command": c, "lua": bind_lua(k, d, c), "from": line.strip()})
            else:
                skipped.append({"line": line.strip(), "why": r})
            lua_lines.append("")
            continue
        lua_lines.append(line)
    lua = "\n".join(lua_lines)
    # every o.bind( / hl.bind( call, wherever it sits (a recipe in prose too)
    spans = []
    for m in re.finditer(r"\b(o\.bind_toggle|o\.bind|hl\.bind)\s*\(", lua):
        i, depth, q = m.end(), 1, None
        while i < len(lua) and depth:
            c = lua[i]
            if q:
                if c == "\\":
                    i += 1
                elif c == q:
                    q = None
            elif c in "\"'":
                q = c
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            i += 1
        if depth:
            continue
        stmt = lua[m.start():i]
        info = classify(stmt)
        if not info or info["kind"] != "bind" or not info["keys"]:
            continue
        one = re.sub(r"\s*\n\s*", " ", stmt)
        spans.append((m.start(), i))
        if not plain_bind(one):
            # what you'd review (keys, what, command) must be all that's written:
            # a bind with code in its arguments could run more than that
            skipped.append({"line": stmt.split("\n")[0][:120],
                            "why": "only plain binds come in: its arguments must be quoted text"})
            continue
        cmd = bind_command(one)
        dm = re.search(r"description\s*=\s*(" + LUA_STR_RE.pattern + ")", one, re.S)
        desc = info["desc"] or (lua_text(dm.group(1)) if dm else "") or cmd
        # written from the parts you see, never as found
        found.append({"keys": info["keys"], "desc": desc, "command": cmd,
                      "lua": bind_lua(info["keys"], desc, cmd), "from": stmt.split("\n")[0][:120]})
    found += prose_found
    # every bind, however it was written: a combo Hyprland can bind, a command
    good = []
    for b in found:
        why = combo_problem(b["keys"]) or ("no command" if not b.get("command") else "") or (
            "its command has a placeholder to fill in (" + re.search(r"<[\w .-]+>", b["command"]).group(0) + ")"
            if re.search(r"<[\w .-]+>", b.get("command", "")) else "")
        if why:
            skipped.append({"line": b["from"], "why": why})
        else:
            good.append(b)
    found = good
    # lines with something on them that wasn't a bind (for the preview)
    for st, en in lua_statements(lua):
        if any(a <= st < b or st <= a < en for a, b in spans):
            continue
        stmt = lua[st:en].strip()
        info = classify(stmt)
        if info and info["kind"] == "unbind":
            continue   # Shell-Swapper writes its own hl.unbind before each key
        if prose:
            continue   # prose is mostly not binds: only lines with a key in them are reported
        if stmt and not stmt.startswith(("--", "#", "```")) and len(skipped) < 40:
            skipped.append({"line": stmt.split("\n")[0][:120], "why": "not a bind"})
    return found, skipped


def import_plan(lid, text):
    return _import(lid, text)[0]


def _import(lid, text):
    """The plan for importing `text` into loadout `lid`, and each key's Lua
    (the first of a key in the text, as the plan says)."""
    outs = loadouts()
    d = get_loadout(lid, outs)
    have = d.get("keybinds") or []
    mine = {b["keys"]: b["line"] for b in user_binds()}
    found, skipped = parse_import(text)
    lua = {}
    for f in found:
        lua.setdefault(f["keys"], f["lua"])
    rows, seen = [], set()
    for f in found:
        if f["keys"] in seen:
            f["status"], f["why"] = "skip", "this key is in the paste twice: the first one wins"
            rows.append(f)
            continue
        seen.add(f["keys"])
        same = [b for b in have if b["keys"] == f["keys"]]
        other = [b for b in have if b["keys"] != f["keys"] and f["command"] and bind_command(b["lua"]) == f["command"]]
        if same and bind_command(same[0]["lua"]) == f["command"]:
            f["status"], f["why"] = "already", "this shell already has it"
        elif other:
            f["status"], f["why"] = "skip", "this shell already does that on " + other[0]["keys"]
        elif same:
            f["status"], f["why"] = "replace", "replaces its " + same[0]["keys"] + " (" + same[0].get("desc", "") + ")"
        else:
            f["status"], f["why"] = "new", ""
        if f["status"] in ("new", "replace") and f["keys"] in mine:
            f["clashLine"] = mine[f["keys"]]
            f["why"] = (f["why"] + "; " if f["why"] else "") + \
                f"your bindings.lua binds it too (line {mine[f['keys']]}): left out until that's sorted"
        rows.append(f)
    return {"action": "import keys into " + d.get("name", lid), "loadout": lid, "loadoutName": d.get("name", lid),
            "import": [{k: r.get(k, "") for k in ("keys", "desc", "command", "status", "why", "from", "clashLine")}
                       for r in rows],
            "skipped": skipped, "activeNow": status().get("active", "") == lid, "backupTo": KEY_BACKUPS}, lua


def import_keys(lid, text, yes):
    with Lock():
        plan, lua = _import(lid, text)
        take = [r for r in plan["import"] if r["status"] in ("new", "replace")]
        if not take:
            return dict(plan, nothing=True)
        if not yes:
            raise NeedsYes(plan)
        d = get_loadout(lid)
        keys = {r["keys"] for r in take}
        replaced = [b for b in d.get("keybinds") or [] if b["keys"] in keys]
        d["keybinds"] = [b for b in d.get("keybinds") or [] if b["keys"] not in keys] + [
            {"keys": r["keys"], "desc": r["desc"], "lua": lua[r["keys"]], "from": "import"} for r in take]
        save_loadout(lid, d)
        # bindings.lua: the new keys, tagged, into the shell's group; a key
        # they replace keeps its line, marked as replaced (never deleted)
        btext = read_text(BINDINGS) or ""
        key_backup(BINDINGS)
        tag = MOVED_TAG.format(lid)
        lines = btext.split("\n")
        for i, l in enumerate(lines):
            if l.startswith(tag) and (classify(l[len(tag):]) or {}).get("keys") in {b["keys"] for b in replaced}:
                lines[i] = f"--[shell-swapper:{lid}:replaced] " + l[len(tag):]
        while lines and not lines[-1].strip():
            lines.pop()
        lines += [""] + [tag_lines(lua[r["keys"]], lid) for r in take] + [""]
        names = loadout_names()
        write_text(BINDINGS, organize_text("\n".join(lines), names))
        if plan["activeNow"] and hook_file():
            write_keys(keys_plan(d)["text"])
        hypr_reload()
        return dict(plan, imported=len(take))


def shell_targets(sid, all_suites):
    """What a shell's commands look like: its plugin ids and IPC targets."""
    marks = set()
    for pid, info in suite_plugins(sid, all_suites).items():
        marks.add(pid)
        for q in glob.glob(os.path.join(info["dir"], "**", "*.qml"), recursive=True):
            for a in scan_ipc(q):
                if not a["target"].startswith("omarchy."):
                    marks.add(a["target"])
    return marks


# ---- a shell's upstream repo: its docs, when they weren't installed ----------
#
# Installed copies often leave the repo's docs behind (Ruixen's
# docs/KEYBINDS.md). The repo is the one the manifest names, else the GitHub
# link in the shell's own files whose name has the shell's name in it. Only
# when you ask (f), only public GitHub, cached for a day.

REMOTE_CACHE = os.path.join(HOME, ".cache/shell-swapper/remote")
GITHUB_RE = re.compile(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?=[/\s\"'#)>\]]|$)")


def shell_repo(sid, all_suites):
    """owner/repo of a suite's upstream on GitHub, or ''."""
    own = suite_plugins(sid, all_suites)
    for info in own.values():
        m = info["manifest"]
        for v in ((m.get("shellSwapper") or {}).get("repo") if isinstance(m.get("shellSwapper"), dict) else None,
                  m.get("repository"), m.get("homepage"), m.get("url"), m.get("source")):
            g = GITHUB_RE.search(str(v or ""))
            if g:
                return g.group(1) + "/" + g.group(2)
    # the shell's name: its id's words, minus the generic ones
    words = {w for w in re.split(r"[.\-_]", sid.lower()) if len(w) >= 4} - {"omarchy", "shell", "plugin", "plugins", "suite"}
    counts = {}
    for info in own.values():
        for f in glob.glob(os.path.join(info["dir"], "**", "*"), recursive=True):
            if not f.endswith((".qml", ".js", ".md", ".json", ".sh", ".py", ".txt")) or os.path.getsize(f) > 2_000_000:
                continue
            try:
                text = read_text(f) or ""
            except (UnicodeDecodeError, ValueError, OSError):
                continue
            for g in GITHUB_RE.finditer(text):
                name = g.group(2).lower()
                if g.group(1).lower() != "repos" and any(w in name for w in words):
                    k = g.group(1) + "/" + g.group(2)
                    counts[k] = counts.get(k, 0) + 1
    return max(counts, key=counts.get) if counts else ""


def http_get(url, timeout=8):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "shell-swapper (keys find)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(2_000_001)


def remote_docs(repo):
    """{path: text} of the repo's .md / .txt files that talk about binds
    (cached for a day). {} when offline or it isn't public."""
    os.makedirs(REMOTE_CACHE, exist_ok=True)
    cache = os.path.join(REMOTE_CACHE, repo.replace("/", "__") + ".json")
    c = read_json(cache, None)
    if isinstance(c, dict) and time.time() - c.get("at", 0) < 86400:
        return c.get("docs", {})
    docs = {}
    try:
        tree = json.loads(http_get(f"https://api.github.com/repos/{repo}/git/trees/HEAD?recursive=1"))
        paths = [t["path"] for t in tree.get("tree", []) if t.get("type") == "blob"
                 and t["path"].lower().endswith((".md", ".txt")) and t.get("size", 0) < 400_000
                 and "/node_modules/" not in "/" + t["path"]]
        # the likely ones first (keybind docs, READMEs), at most 30 files
        paths.sort(key=lambda p: (0 if re.search(r"key|bind", p, re.I) else 1 if "readme" in p.lower() else 2, p))
        for p in paths[:30]:
            try:
                text = http_get(f"https://raw.githubusercontent.com/{repo}/HEAD/{p}").decode("utf-8", "replace")
            except OSError:
                continue
            if re.search(r"(key)?binds?\b|keybindings?|bindings", text, re.I):
                docs[p] = text
    except (OSError, ValueError, KeyError):
        return c.get("docs", {}) if isinstance(c, dict) else {}   # offline: whatever we had
    write_json(cache, {"at": time.time(), "repo": repo, "docs": docs})
    return docs


def find_checkout(pid):
    """A copied-in plugin (no repo, no .synced-from): its checkout somewhere in
    your home, a folder named for it with the same manifest id (a shell's own
    installer often copies from one, like ~/Shibumi-Shell)."""
    skip = {os.path.join(HOME, x) for x in (".cache", ".local", ".config", ".mozilla", ".steam", ".var")}
    for top, dirs, _ in os.walk(HOME):
        depth = top[len(HOME):].count(os.sep)
        dirs[:] = [d for d in dirs if not d.startswith(".") and os.path.join(top, d) not in skip
                   and d not in ("node_modules", "venv", ".venv")] if depth < 3 else []
        if pid in dirs:
            m = read_json(os.path.join(top, pid, "manifest.json"), None)
            if isinstance(m, dict) and m.get("id") == pid:
                r = subprocess.run(["git", "-C", os.path.join(top, pid), "rev-parse", "--show-toplevel"],
                                   capture_output=True, text=True)
                if r.returncode == 0 and r.stdout.strip():
                    return r.stdout.strip()
    return ""


def find_keys(lid, remote=True, repo_override=""):
    """Binds a shell recommends, from its own checkout: READMEs, docs,
    scripts and code comments; only ones that call that shell."""
    all_suites, outs = suites(), loadouts()
    d = get_loadout(lid, outs)
    sid = d.get("suite", "")
    marks = shell_targets(sid, all_suites)
    roots = set()
    for pid, info in suite_plugins(sid, all_suites).items():
        src = plugin_source(info["dir"])
        top = subprocess.run(["git", "-C", src, "rev-parse", "--show-toplevel"], capture_output=True, text=True)
        root = top.stdout.strip() if top.returncode == 0 else ""
        # a repo holding the plugins folder itself (~/.config/omarchy under
        # git, say) isn't this shell's: just its own plugin folders then
        plugins_home = os.path.realpath(PLUGIN_DIRS[0])
        if not root or plugins_home == root or plugins_home.startswith(root + os.sep):
            root = src
        roots.add(root)
    # all copies without a repo: look for the checkout they came from
    if all(subprocess.run(["git", "-C", r, "rev-parse"], capture_output=True).returncode != 0 for r in roots):
        co = find_checkout(sid)
        if co:
            roots = {co}
    found, files = [], []
    for root in sorted(roots):
        for pat in ("**/*.md", "**/*.lua", "**/*.conf", "**/*.sh", "**/*.qml", "**/*.js", "**/*.txt"):
            for f in glob.glob(os.path.join(root, pat), recursive=True):
                if "/node_modules/" in f or "/.git/" in f:
                    continue
                try:
                    text = read_text(f) or ""
                except (UnicodeDecodeError, ValueError):
                    continue   # not text (a binary, another encoding): nothing to find in it
                doc = f.endswith((".md", ".txt"))
                if doc and not re.search(r"(key)?binds?\b|keybindings?|bindings", text, re.I):
                    continue
                if not doc and "bind" not in text:
                    continue
                for b in parse_import(text, prose=doc)[0]:
                    if any(m in b["command"] for m in marks):
                        b["file"] = os.path.relpath(f, root)
                        found.append(b)
                        if f not in files:
                            files.append(f)
    # its upstream repo's docs (installed copies often leave them behind)
    repo = repo_override or (shell_repo(sid, all_suites) if remote else "")
    remote_files = []
    if repo and remote:
        for path, text in remote_docs(repo).items():
            for b in parse_import(text, prose=True)[0]:
                if any(m in b["command"] for m in marks):
                    b["file"] = f"github.com/{repo}/{path}"
                    found.append(b)
                    if b["file"] not in remote_files:
                        remote_files.append(b["file"])
    seen, uniq = set(), []
    for b in found:
        k = (b["keys"], b["command"])
        if k not in seen:
            seen.add(k)
            uniq.append(b)
    return {"loadout": lid, "suite": sid, "roots": sorted(roots) + ([f"github.com/{repo}"] if repo else []),
            "repo": repo, "files": files + remote_files,
            "binds": uniq, "text": "\n".join(b["lua"] for b in uniq)}


def backup_file(name):
    """A backup by its path under backups/ (original/bindings.lua,
    swaps/<t>/bindings.lua, keys/<file>), or just a keys/ file's name."""
    if name == "original":
        name = "original/bindings.lua"
    src = os.path.normpath(os.path.join(BACKUPS, name if "/" in name else os.path.join("keys", name)))
    if os.path.commonpath([src, BACKUPS]) != BACKUPS or not os.path.isfile(src):
        raise Fail(f"no backup '{name}' (see shell-swapper backups)")
    return src


def restore_key_backup(name, yes):
    src = backup_file(name)
    check = os.path.basename(src)
    target = next((f for f in (BINDINGS, HYPRLAND, KEYS_FILE)
                   if check == os.path.basename(f) or check.endswith("-" + os.path.basename(f))), None)
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


# ---- a new bar: checked the first time it's seen ------------------------------
#
# A shell's own installer usually makes itself your bar, with its layout and
# plugins, before Shell-Swapper knows it exists. The first time a bar is seen
# it's checked, and if it's your bar now it's saved as a loadout straight
# away, exactly as its installer left it, so no swap can lose that setup.

KNOWN = os.path.join(STATE, "known-suites.json")
NOTICES = os.path.join(STATE, "notices.json")


def check_suite(sid, all_suites, cur):
    """What a bar needs before it's safe to swap to: is it your bar now,
    which plugins are its own, what else its installer left on it."""
    is_bar = bar_id(cur) == sid
    own = suite_plugins(sid, all_suites)
    kinds = {}
    for info in own.values():
        for k in info["manifest"].get("kinds") or []:
            kinds[k] = kinds.get(k, 0) + 1
    f = {"suite": sid, "name": all_suites[sid]["name"], "isBar": is_bar,
         "ownPlugins": len(own), "ownKinds": kinds,
         # no bar widgets of its own: it draws its bar's contents itself
         # (Synoptik), so an empty layout is how it's meant to be
         "drawsOwn": "bar-widget" not in kinds,
         "declaresRules": isinstance(all_suites[sid]["manifest"].get("shellSwapper"), dict),
         "layoutKnown": default_bar(sid, all_suites) is not None,
         "notifiers": sorted(p for p, i in own.items()
                             if "service" in (i["manifest"].get("kinds") or []) and "notif" in p),
         "foreignWidgets": [], "foreignPlugins": [], "advice": []}
    if is_bar:
        lay = (cur.get("bar") or {}).get("layout") or {}
        f["widgets"] = sum(len(v or []) for v in lay.values())
        f["foreignWidgets"] = [entry_id(e) for sec in lay.values() for e in sec or []
                               if owner(entry_id(e), all_suites) not in (None, sid)]
        f["foreignPlugins"] = [entry_id(p) for p in cur.get("plugins") or []
                               if owner(entry_id(p), all_suites) not in (None, sid)
                               and entry_id(p) not in (cur.get("disabledPlugins") or [])]
        if not f["widgets"] and not f["drawsOwn"]:
            f["advice"].append("its bar has no widgets: run its installer again, then save it (s)")
    elif not f["layoutKnown"]:
        f["advice"].append("not set up yet: run its own installer (it makes itself your bar), then it's saved for you")
    if f["foreignWidgets"] or f["foreignPlugins"]:
        f["advice"].append(f"its installer left {len(f['foreignWidgets'])} widget(s) and {len(f['foreignPlugins'])} "
                           "plugin(s) from other shells on it: remove them if they shouldn't be there")
    if not f["ownPlugins"]:
        f["advice"].append("Shell-Swapper can't tell which plugins are its own (it can say so in its manifest: shellSwapper.owns)")
    if f["notifiers"]:
        f["advice"].append("it has its own notification server (" + ", ".join(f["notifiers"]) +
                           "): only one can run, so it's switched off while other shells are in use")
    return f


def check_new(auto=True):
    """Bars not seen before: check each; save one that's your bar now as a
    loadout (if it has none). Returns what's new."""
    with Lock():
        seed()
        all_suites, cur = suites(), shell_config()
        known = read_json(KNOWN, None)
        if not isinstance(known, dict):
            # first time: everything installed now is already known
            known = {sid: {"firstSeen": now()} for sid in all_suites}
            write_json(KNOWN, known)
            return {"new": [], "initialized": True}
        found = []
        for sid in all_suites:
            if sid in known:
                continue
            f = check_suite(sid, all_suites, cur)
            outs = loadouts()
            have = [lid for lid, d in outs.items() if d.get("suite") == sid]
            f["loadout"] = have[0] if have else ""
            if auto and f["isBar"] and not have and (f.get("widgets") or f["drawsOwn"]):
                lid = unique_id(f["name"], set(outs))
                snap = snapshot(cur, f["name"], sid)
                snap["note"] = "saved when it was first found, exactly as its installer set it up"
                save_loadout(lid, snap)
                st = status()
                set_status(active=lid, previous=st.get("active", "") if st.get("active") != lid else st.get("previous", ""))
                f["loadout"], f["captured"] = lid, True
            known[sid] = {"firstSeen": now()}
            found.append(f)
        if found:
            write_json(KNOWN, known)
            notes = read_json(NOTICES, []) or []
            notes = [n for n in notes if n.get("suite") not in {f["suite"] for f in found}]
            write_json(NOTICES, notes + [dict(f, at=now()) for f in found])
        return {"new": found}


# ---- shell-specific widgets stay on their own shell's bar ----------------------
#
# A widget belongs to a shell when that shell owns its plugin (its id
# prefix: slime.*, ruixen.*, hancore.shibumi.*). On another shell's bar it's
# flagged (it may lean on its own bar to work, and it shows up where it
# doesn't belong); so is a widget whose plugin isn't installed.

def installed_ids():
    """Every plugin id the shell knows, built-in widgets included."""
    fx = os.environ.get("SHELL_SWAPPER_PLUGIN_LIST")
    data = read_json(fx, None) if fx else None
    if data is None and not fx:
        try:
            out = subprocess.run(["omarchy-plugin-list", "--json"], capture_output=True, text=True, timeout=5).stdout
            data = json.loads(out)
        except (OSError, ValueError, subprocess.SubprocessError):
            data = None
    if isinstance(data, list) and data:
        return {str(p.get("id")) for p in data if isinstance(p, dict)}
    ids = set()   # the shell isn't running: the manifests on disk
    for base in PLUGIN_DIRS:
        for mf in glob.glob(os.path.join(base, "**", "*manifest.json"), recursive=True):
            m = read_json(mf, None)
            if isinstance(m, dict) and m.get("id"):
                ids.add(str(m["id"]))
    return ids


def widget_report(d, all_suites, installed):
    """A loadout's bar: widgets of other shells, and ones not installed."""
    sid = d.get("suite", "")
    lay = (d.get("bar") or {}).get("layout") or {}
    foreign, missing = [], []
    for section, entries in lay.items():
        for e in entries or []:
            wid = entry_id(e)
            own = owner(wid, all_suites)
            if own not in (None, sid):
                foreign.append({"id": wid, "section": section, "owner": own,
                                "ownerName": all_suites.get(own, {}).get("name", own)})
            if installed and wid not in installed:
                missing.append({"id": wid, "section": section})
    return {"foreign": foreign, "missing": missing}


def take_off_widgets(lid, ids, yes):
    """Take widgets off a loadout's bar (only the bar: its plugins stay)."""
    with Lock():
        all_suites, outs = suites(), loadouts()
        d = get_loadout(lid, outs)
        rep = widget_report(d, all_suites, installed_ids())
        if not ids:
            ids = [w["id"] for w in rep["foreign"]]
        lay = (d.get("bar") or {}).get("layout") or {}
        present = {entry_id(e) for sec in lay.values() for e in sec or []}
        take = [i for i in ids if i in present]
        if not take:
            return {"nothing": True, "loadout": lid}
        names = {w["id"]: w["ownerName"] for w in rep["foreign"]}
        active = status().get("active", "") == lid
        plan = {"action": "take " + ("these widgets" if len(take) > 1 else "this widget") + " off " + d.get("name", lid) + "'s bar",
                "loadout": lid, "loadoutName": d.get("name", lid),
                "takeOff": [{"id": i, "ownerName": names.get(i, "")} for i in take],
                "activeNow": active, "backupTo": LOADOUT_BACKUPS}
        if not yes:
            raise NeedsYes(plan)
        for k in lay:
            lay[k] = [e for e in lay[k] or [] if entry_id(e) not in take]
        save_loadout(lid, d)
        return dict(plan, done=True)


def note_new_foreign(lid, before, after):
    """A save that put another shell's widget on this bar: tell the picker."""
    gained = [w for w in after["foreign"] if w["id"] not in {x["id"] for x in before["foreign"]}]
    if not gained:
        return
    outs = loadouts()
    name = outs.get(lid, {}).get("name", lid)
    notes = [n for n in (read_json(NOTICES, []) or []) if not (n.get("kind") == "foreign" and n.get("loadout") == lid)]
    notes.append({"kind": "foreign", "suite": "foreign:" + lid, "loadout": lid, "name": name, "at": now(),
                  "foreignWidgets": [w["id"] for w in gained], "foreignPlugins": [],
                  "advice": [f"{name}'s bar now has " + ", ".join(f"{w['id']} ({w['ownerName']}'s)" for w in gained)
                             + ": widgets of another shell may not work, or look right, here"]})
    write_json(NOTICES, notes)


def strip_foreign(lid, yes):
    """Take other shells' widgets and plugins out of a loadout."""
    with Lock():
        all_suites, outs = suites(), loadouts()
        d = get_loadout(lid, outs)
        sid = d.get("suite", "")
        lay = (d.get("bar") or {}).get("layout") or {}
        widgets = [entry_id(e) for sec in lay.values() for e in sec or [] if owner(entry_id(e), all_suites) not in (None, sid)]
        plugins = [entry_id(p) for p in d.get("plugins") or [] if owner(entry_id(p), all_suites) not in (None, sid)]
        plan = {"action": "take other shells' widgets and plugins out of " + d.get("name", lid), "loadout": lid,
                "widgets": widgets, "plugins": plugins, "activeNow": status().get("active", "") == lid,
                "backupTo": LOADOUT_BACKUPS}
        if not widgets and not plugins:
            return dict(plan, nothing=True)
        if not yes:
            raise NeedsYes(plan)
        for k in lay:
            lay[k] = [e for e in lay[k] or [] if owner(entry_id(e), all_suites) in (None, sid)]
        d["plugins"] = [p for p in d.get("plugins") or [] if owner(entry_id(p), all_suites) in (None, sid)]
        d["disabledPlugins"] = [x for x in d.get("disabledPlugins") or [] if owner(str(x), all_suites) in (None, sid)]
        save_loadout(lid, d)
        return dict(plan, done=True, note="apply it (swap to it) to see it")


# ---- a picture of each shell's bar --------------------------------------------
#
# Only the shell on screen can be pictured, so it's taken then: after you
# keep a swap, when the shell starts with a loadout that has none, after a
# save, or when you ask. Never while something covers the bar (a fullscreen
# window, a swap still asking).

PREVIEWS = os.path.join(STATE, "previews")


def hypr_json(*args):
    # tests: SHELL_SWAPPER_HYPR_FIXTURES=<dir> with monitors.json, layers.json, activewindow.json
    fx = os.environ.get("SHELL_SWAPPER_HYPR_FIXTURES")
    if fx:
        return read_json(os.path.join(fx, args[0] + ".json"), None)
    try:
        out = subprocess.run(["hyprctl", *args, "-j"], capture_output=True, text=True, timeout=3).stdout
        return json.loads(out)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def bar_region():
    """(x, y, w, h, position) of the bar on the focused monitor, in the
    desktop's (logical) coordinates as grim takes them, or None. Bars on any
    edge: top, bottom, left, right."""
    mons = hypr_json("monitors") or []
    mon = next((m for m in mons if m.get("focused")), mons[0] if mons else None)
    layers = hypr_json("layers") or {}
    if not mon or not isinstance(layers, dict):
        return None
    sc = mon.get("scale", 1) or 1
    mw, mh = mon["width"] / sc, mon["height"] / sc
    if int(mon.get("transform", 0)) % 2:          # turned 90 / 270 degrees
        mw, mh = mh, mw
    mx, my = mon.get("x", 0), mon.get("y", 0)
    here = []
    for lvl in ((layers.get(mon["name"]) or {}).get("levels") or {}).values():
        for l in lvl:
            # layer positions may be the desktop's or the monitor's own
            x = l["x"] if mx <= l["x"] < mx + mw else l["x"] + mx
            y = l["y"] if my <= l["y"] < my + mh else l["y"] + my
            here.append(dict(l, x=x, y=y))

    def edge(l):
        # a floating bar sits a little off its edge (Ruixen's: 13 px down):
        # within its own thickness (or 24 px) of the edge still counts
        if l["w"] >= mw * 0.9 and l["h"] <= mh * 0.2:
            gap = max(24, l["h"])
            return "top" if l["y"] - my <= gap else "bottom" if (my + mh) - (l["y"] + l["h"]) <= gap else ""
        if l["h"] >= mh * 0.9 and l["w"] <= mw * 0.2:
            gap = max(24, l["w"])
            return "left" if l["x"] - mx <= gap else "right" if (mx + mw) - (l["x"] + l["w"]) <= gap else ""
        return ""
    # The edge the compositor keeps clear for a bar (the monitor's reserved
    # space: left, top, right, bottom): where the bar is, whatever draws it.
    res = list(mon.get("reserved") or [0, 0, 0, 0])[:4] + [0, 0, 0, 0]
    names = ["left", "top", "right", "bottom"]
    i_max = max(range(4), key=lambda i: res[i])
    bar_edge = names[i_max] if res[i_max] >= 16 else ""
    # Omarchy's own bar window (every suite's bar lives in it), else a bar-ish
    # layer, else a thin full-length one at an edge; on the reserved edge if
    # there is one
    found = None
    for pick in (lambda l: l.get("namespace") == "omarchy-bar",
                 lambda l: "bar" in str(l.get("namespace", "")).lower() and "drip" not in str(l.get("namespace", "")).lower(),
                 lambda l: True):
        cands = [l for l in here if pick(l) and edge(l) and (not bar_edge or edge(l) == bar_edge)]
        if cands:
            l = max(cands, key=lambda l: l["w"] * l["h"])
            found = (l["x"], l["y"], l["w"], l["h"], edge(l))
            break
    if not found and bar_edge:
        # a shell that draws its bar in a window of its own covering the
        # screen (Omacale): the reserved strip along that edge
        r = res[i_max]
        found = {"left": (mx, my, r, mh), "top": (mx, my, mw, r),
                 "right": (mx + mw - r, my, r, mh), "bottom": (mx, my + mh - r, mw, r)}[bar_edge] + (bar_edge,)
    if found:
        x, y, w, h, e = found
        # drips hanging off the bar (Slime's), on the side away from the
        # edge: a little of them, so it looks like it does on screen
        if any("drip" in str(d.get("namespace", "")).lower() for d in here):
            if e == "top":
                h += 20
            elif e == "bottom":
                y, h = y - 20, h + 20
            elif e == "left":
                w += 20
            else:
                x, w = x - 20, w + 20
        return (int(x), int(y), int(w), int(h), e)
    return None


def png_size(path):
    try:
        with open(path, "rb") as f:
            head = f.read(24)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
    except OSError:
        pass
    return None


def take_preview(lid=None, if_missing=False):
    """Picture the bar of the shell on screen, for its loadout."""
    with Lock():
        outs, st = loadouts(), status()
        active = st.get("active", "")
        lid = lid or active
        get_loadout(lid, outs)
        p = outs[lid].get("preview")
        if if_missing and isinstance(p, dict) and os.path.exists(p.get("path", "")):
            return {"preview": p, "loadout": lid, "already": True}
        if lid != active or outs[lid].get("suite") != bar_id(shell_config()):
            raise Fail("only the shell on screen can be pictured: swap to it first")
        if pending():
            raise Fail("a swap is still asking to be kept")
        if locked():
            raise Fail("the screen is locked")
        win = hypr_json("activewindow") or {}
        if isinstance(win, dict) and win.get("fullscreen"):
            raise Fail("a fullscreen window is covering the bar")
        reg = bar_region()
        if not reg:
            raise Fail("couldn't find the bar on screen")
        x, y, w, h, pos = reg
        os.makedirs(PREVIEWS, exist_ok=True)
        path = os.path.join(PREVIEWS, lid + ".png")
        tmp = path + ".tmp.png"
        cmd = os.environ.get("SHELL_SWAPPER_GRIM", "grim")
        r = subprocess.run(shlex.split(cmd) + ["-g", f"{x},{y} {w}x{h}", tmp], capture_output=True, text=True, timeout=10)
        if r.returncode != 0 or not os.path.exists(tmp):
            raise Fail("the screenshot didn't work: " + (r.stderr.strip() or "grim failed"))
        os.replace(tmp, path)
        # the picture's own size (at a scaled display it's bigger than the bar's)
        pw, ph = png_size(path) or (w, h)
        d = outs[lid]
        d["preview"] = {"path": path, "w": pw, "h": ph, "position": pos, "at": now(), "t": int(time.time())}
        save_loadout(lid, d)
        return {"preview": d["preview"], "loadout": lid}


def backups_listing():
    def rel(p):
        return os.path.relpath(p, BACKUPS)
    return {
        "dir": BACKUPS,
        "original": sorted(rel(p) for p in glob.glob(os.path.join(ORIGINALS, "*")) if not p.endswith("README.txt")),
        "swaps": [{"at": os.path.basename(d), "files": sorted(os.listdir(d))}
                  for d in sorted(glob.glob(os.path.join(SWAP_BACKUPS, "*")))],
        "loadouts": {os.path.basename(d): sorted(rel(p) for p in glob.glob(os.path.join(d, "*.json")))
                     for d in sorted(glob.glob(os.path.join(LOADOUT_BACKUPS, "*")))},
        "keys": sorted(rel(p) for p in glob.glob(os.path.join(KEY_BACKUPS, "*"))),
    }


def restore_loadout(lid, name, yes):
    with Lock():
        outs = loadouts()
        get_loadout(lid, outs)
        src = os.path.normpath(os.path.join(LOADOUT_BACKUPS, lid, os.path.basename(name)))
        d = read_json(src, None)
        if not isinstance(d, dict):
            raise Fail(f"no backup '{name}' for {lid} (see shell-swapper backups)")
        active = status().get("active", "") == lid
        keys_live = active and hook_file() and keys_plan(d)["changes"]
        plan = {"action": "put " + outs[lid].get("name", lid) + " back as it was in " + os.path.basename(src),
                "loadout": lid, "keysChange": bool(keys_live), "backupTo": LOADOUT_BACKUPS}
        if keys_live and not yes:
            raise NeedsYes(plan)
        save_loadout(lid, d)
        if keys_live:
            write_keys(keys_plan(d)["text"])
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
    """A quick copy of everything a swap can change, before it: shell.json,
    the keys file and your bindings.lua. Returns the shell.json copy."""
    keep_original(SHELL_JSON)
    d = os.path.join(SWAP_BACKUPS, dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6])
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "shell.json")
    write_json(path, cfg)
    for f in (KEYS_FILE, BINDINGS):
        text = read_text(f)
        if text is not None:
            write_text(os.path.join(d, os.path.basename(f)), text)
    for old in sorted(glob.glob(os.path.join(SWAP_BACKUPS, "*")))[:-KEEP_BACKUPS]:
        shutil.rmtree(old, ignore_errors=True)
    return path


def pending():
    p = read_json(PENDING, None)
    return p if isinstance(p, dict) and p.get("token") else None


def fresh_loadout(sid, all_suites):
    """A new loadout for a suite. If its bar is the one in shell.json now
    (its own installer set it up), that's it: its layout, its own plugins,
    and the ones it switched off. Otherwise its known default layout, with
    its own services on; and with neither, no loadout (an empty bar isn't a
    shell): install it with its own installer, then save it."""
    if sid not in all_suites:
        raise Fail(f"no bar suite '{sid}' installed")
    name = all_suites[sid]["name"]
    cur = shell_config()
    if bar_id(cur) == sid:
        # exactly as it is, like saving it: its installer may keep other
        # suites' plugins running for widgets it put on the bar
        snap = snapshot(cur, name, sid)
        snap["note"] = "taken from your shell as its installer set it up"
        return snap
    bar = default_bar(sid, all_suites)
    if bar is None:
        raise Fail(f"Shell-Swapper doesn't know a layout for {name}. Set it up with its own installer "
                   f"(that makes it your bar), then save it here as a loadout (s in the picker, or: shell-swapper save \"{name}\").")
    services = [{"id": pid} for pid, info in suite_plugins(sid, all_suites).items()
                if "service" in (info["manifest"].get("kinds") or [])]
    return {"name": name, "suite": sid, "bar": bar, "plugins": services, "disabledPlugins": []}


def target_for(arg, all_suites, outs):
    """A loadout (by id), or a suite's default: "suite:<bar id>"."""
    if arg.startswith("suite:"):
        return None, fresh_loadout(arg[6:], all_suites)
    t = get_loadout(arg, outs)
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
        # keep tweaks made since: the shell you're leaving is saved first,
        # but only if you changed it since it was last applied or kept (the
        # loadout may be newer than the live config: edited, or repaired
        # after a swap went back), and never when re-applying it itself
        good = read_json(LAST_GOOD, None)
        tweaked = not isinstance(good, dict) or any(cur.get(k) != good.get(k)
                                                    for k in ("bar", "plugins", "disabledPlugins"))
        if active in outs and active != lid and tweaked and outs[active].get("suite") == bar_id(cur):
            snap = snapshot(cur, outs[active].get("name", active), bar_id(cur))
            note_new_foreign(active, widget_report(outs[active], all_suites, set()), widget_report(snap, all_suites, set()))
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
        if keys_change and keys == "keep":
            # only your own keys (bindings.lua): no shell's keys at all, rather
            # than the old shell's left calling a shell that isn't running
            old_keys = [b for b in parse_binds(read_text(KEYS_FILE) or "") if b["kind"] == "bind"]
            kplan = dict(kplan, text=render_keys(target.get("name", lid), []))
            keys_change = bool(old_keys)
        if keys_change and keys is None:
            if created:
                os.remove(loadout_path(lid))
            raise NeedsYes({"action": "swap to " + target.get("name", lid), "loadout": arg,
                            "keys": {k: kplan[k] for k in ("add", "remove", "conflicts")},
                            "choose": "--keys (its keys come too) or --keep-keys (only your bindings.lua keys)"})
        if new == cur and not keys_change:
            set_status(active=lid)
            return {"applied": lid, "changed": False}
        bpath = backup(cur)   # before anything changes: shell.json, keys, bindings.lua
        kinfo = write_keys(kplan["text"]) if keys_change else None
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
            newest = sorted(glob.glob(os.path.join(SWAP_BACKUPS, "*", "shell.json")))
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


def bar_position(d):
    """Where a loadout's bar sits: as its picture found it (a shell that keeps
    its own position setting, like Synoptik, doesn't use shell.json's), else
    shell.json's bar.position."""
    p = d.get("preview")
    if isinstance(p, dict) and p.get("position") in ("top", "bottom", "left", "right"):
        return p["position"]
    return (d.get("bar") or {}).get("position", "top")


def listing():
    with Lock():
        seed()
    all_suites, outs, st = suites(), loadouts(), status()
    cur = shell_config()
    used = {d.get("suite") for d in outs.values()}
    inst = installed_ids()
    mine = {b["keys"]: b["line"] for b in user_binds()}
    return {
        "notices": [n for n in (read_json(NOTICES, []) or []) if not n.get("dismissed")],
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
                      "position": bar_position(d),
                      "layout": summary_bar(d.get("bar")), "active": lid == st.get("active"),
                      "widgets": widget_report(d, all_suites, inst),
                      "keys": [{"keys": b["keys"], "desc": b.get("desc", ""), "clashLine": mine.get(b["keys"], 0)}
                               for b in d.get("keybinds") or []]}
                     for lid, d in outs.items()],
        "suites": [{"id": s["id"], "name": s["name"], "description": s["description"],
                    "hasLoadout": s["id"] in used,
                    "canStartFresh": s["id"] == bar_id(cur) or default_bar(s["id"], all_suites) is not None}
                   for s in all_suites.values()],
    }


# ---- the command line ------------------------------------------------------------

def run(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return None
    cmd, rest = argv[0], argv[1:]
    if os.path.isdir(STATE):
        with Lock():
            migrate_backups()
    if cmd == "backups":
        return backups_listing()
    if cmd == "loadout-restore":
        if len(rest) < 2:
            raise Fail("loadout-restore ID BACKUP (see backups)")
        return restore_loadout(rest[0], rest[1], "--yes" in rest)
    if cmd == "list":
        check_new()   # a bar installed since: checked (and saved) before anything else
        return listing()
    if cmd == "check-new":
        return check_new()
    if cmd == "preview":
        # preview [ID] [--auto]: --auto (the shell's own triggers) says nothing
        # when it can't, rather than failing
        ids = [a for a in rest if not a.startswith("--")]
        try:
            return take_preview(ids[0] if ids else None, if_missing="--if-missing" in rest)
        except Fail as e:
            if "--auto" in rest:
                return {"preview": None, "why": str(e)}
            raise
    if cmd == "check":
        all_suites = suites()
        sid = rest[0] if rest else bar_id(shell_config())
        if sid not in all_suites:
            raise Fail(f"no bar suite '{sid}' installed")
        return check_suite(sid, all_suites, shell_config())
    if cmd == "notice-dismiss":
        with Lock():
            notes = read_json(NOTICES, []) or []
            for n in notes:
                if not rest or n.get("suite") == rest[0]:
                    n["dismissed"] = True
            write_json(NOTICES, notes)
        return {"dismissed": rest[0] if rest else "all"}
    if cmd == "widgets":
        # widgets [ID]: other shells' widgets and missing ones, on a loadout's bar (or all)
        all_suites, outs, inst = suites(), loadouts(), installed_ids()
        pick = [check_id(rest[0])] if rest else sorted(outs)
        return {lid: dict(widget_report(outs[lid], all_suites, inst), suite=outs[lid].get("suite", ""))
                for lid in pick if lid in outs}
    if cmd == "take-off":
        # take-off ID [WIDGET...]: other shells' widgets (or those named) off its bar
        if not rest:
            raise Fail("take-off ID [WIDGET...]")
        return take_off_widgets(rest[0], [a for a in rest[1:] if not a.startswith("--")], "--yes" in rest)
    if cmd == "strip-foreign":
        if not rest:
            raise Fail("strip-foreign ID")
        return strip_foreign(rest[0], "--yes" in rest)
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
            a_s = suites()
            note_new_foreign(lid, widget_report(outs.get(lid) or {"suite": sid}, a_s, set()), widget_report(snap, a_s, set()))
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
            d = get_loadout(rest[0], outs)
            d["name"] = " ".join(rest[1:]).strip() or d.get("name", rest[0])
            save_loadout(rest[0], d)
            return {"renamed": rest[0], "name": d["name"]}
    if cmd == "delete":
        if not rest:
            raise Fail("delete ID")
        return delete_loadout(rest[0], "--yes" in rest)
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
        d = get_loadout(args[1])
        plan = keys_plan(d)
        clash = {c["keys"] for c in plan["conflicts"]}
        return {"loadout": args[1], "keys": [dict(i=i, keys=b["keys"], desc=b.get("desc", ""), lua=b["lua"],
                                                  clash=b["keys"] in clash)
                                             for i, b in enumerate(d.get("keybinds") or [])]}
    if sub == "diff":
        if len(args) < 2:
            raise Fail("keys diff ID")
        d = get_loadout(args[1])
        plan = keys_plan(d)
        return {k: plan[k] for k in ("changes", "add", "remove", "conflicts")}
    if sub == "move":
        if len(args) < 3:
            raise Fail('keys move ID "SUPER + R" ... (key combos, or line numbers from keys scan)')
        return move_keys(args[1], args[2:], yes)
    if sub == "remove":
        if len(args) < 3 or not args[2].isdigit():
            raise Fail("keys remove ID N")
        return remove_key(args[1], int(args[2]), yes)
    if sub == "add":
        if len(args) < 3:
            raise Fail("keys add ID 'o.bind(\"KEYS\", \"what\", \"command\")'")
        return add_key(args[1], " ".join(args[2:]), yes)
    if sub == "actions":
        if len(args) < 2:
            raise Fail("keys actions ID [--all]")
        return actions_for(args[1], "--all" in rest)
    if sub == "check":
        if len(args) < 3:
            raise Fail('keys check ID "SUPER + R"')
        return check_keys(args[1], " ".join(args[2:]))
    if sub == "set":
        # keys set ID "SUPER + R" "Launcher" "omarchy-shell ..." [--yes]
        if len(args) < 5:
            raise Fail('keys set ID "KEYS" "what it does" "command"')
        return set_many([{"loadout": args[1], "keys": args[2], "desc": args[3], "command": " ".join(args[4:])}], yes)
    if sub == "set-many":
        if len(args) < 2:
            raise Fail("keys set-many JSON")
        try:
            items = json.loads(args[1])
        except ValueError:
            raise Fail("set-many takes a JSON list of {loadout, keys, desc, command}")
        if not isinstance(items, list) or not items:
            raise Fail("set-many takes a JSON list of {loadout, keys, desc, command}")
        return set_many(items, yes)
    if sub == "propose":
        if len(args) < 4:
            raise Fail('keys propose FROM_ID "KEYS" "command"')
        return propose(args[2], " ".join(args[3:]), args[1])
    if sub == "templates":
        return {"templates": templates()}
    if sub == "template-save":
        if len(args) < 3:
            raise Fail("keys template-save NAME JSON")
        try:
            t = json.loads(args[2])
        except ValueError:
            raise Fail("template-save takes JSON {keys, category, binds}")
        return save_template(args[1], t.get("keys", ""), t.get("category", ""), t.get("binds", []))
    if sub == "template-delete":
        with Lock():
            ts = [t for t in templates() if t.get("name") != (args[1] if len(args) > 1 else "")]
            write_json(TEMPLATES, ts)
        return {"templates": ts}
    if sub == "find":
        if len(args) < 2:
            raise Fail("keys find ID [--local] [--repo owner/repo]")
        repo = ""
        if "--repo" in rest:
            k = rest.index("--repo")
            g = GITHUB_RE.search("github.com/" + rest[k + 1].split("github.com/")[-1]) if k + 1 < len(rest) else None
            if not g:
                raise Fail("--repo takes owner/repo (or its GitHub URL)")
            repo = g.group(1) + "/" + g.group(2)
        return find_keys(args[1], remote="--local" not in rest, repo_override=repo)
    if sub == "import":
        if len(args) < 2:
            raise Fail("keys import ID --text TEXT (or the text on stdin)")
        text = None
        if "--text" in rest:
            k = rest.index("--text")
            text = rest[k + 1] if k + 1 < len(rest) else ""
        if text is None:
            text = sys.stdin.read()
        if "--check" in rest:
            with Lock():
                return import_plan(args[1], text)
        return import_keys(args[1], text, yes)
    if sub == "organize":
        return organize_keys(yes)
    if sub == "backups":
        return backups_listing()
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
