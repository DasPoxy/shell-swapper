"""Tests for shell_swapper.py, in a throwaway home: fake plugins, no shell
restarts, no Hyprland. Run: python3 -m unittest discover -s tests"""
import json
import os
import sys
import tempfile
import unittest

HOME = tempfile.mkdtemp(prefix="shell-swapper-test-")
OMARCHY = os.path.join(HOME, "omarchy")
os.environ.update({
    "HOME": HOME, "OMARCHY_PATH": OMARCHY,
    # nothing real gets restarted, reloaded or asked
    "SHELL_SWAPPER_RESTART": "", "SHELL_SWAPPER_RELOAD": "", "SHELL_SWAPPER_CHECK": "",
    "SHELL_SWAPPER_HYPR_RELOAD": "", "SHELL_SWAPPER_LOCKED": "false",
    "SHELL_SWAPPER_PLUGIN_LIST": os.path.join(HOME, "no-plugin-list.json"),
})
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import shell_swapper as ss  # noqa: E402


def plugin(pid, kinds, base="user", **extra):
    d = os.path.join(HOME, ".config/omarchy/plugins" if base == "user" else "omarchy/shell/plugins", pid)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "manifest.json"), "w") as f:
        json.dump(dict({"id": pid, "name": pid, "kinds": kinds}, **extra), f)


# Omarchy's own services, and a suite ("acme") that replaces three of them
plugin("omarchy.bar", ["bar"], base="omarchy")
for p in ("notifications", "lock"):
    plugin("omarchy." + p, ["service"], base="omarchy")
plugin("omarchy.osd", ["panel"], base="omarchy")
plugin("omarchy.audio", ["bar-widget"], base="omarchy")
plugin("acme.bar", ["bar"])
plugin("acme.notifications", ["service"], omarchy={"clonedFrom": "omarchy.notifications"})
plugin("acme.lock", ["service"], omarchy={"clonedFrom": "omarchy.lock"})
plugin("acme.osd", ["panel"], omarchy={"clonedFrom": "omarchy.osd"})
plugin("acme.audio", ["bar-widget"], omarchy={"clonedFrom": "omarchy.audio"})
plugin("other.bar", ["bar"])
plugin("solo.bar", ["bar"])          # draws its own bar: no widget plugins
plugin("solo.lock", ["service"])
plugin("taskchy", ["overlay"])


def on(cfg, pid):
    dis = cfg.get("disabledPlugins") or []
    listed = any(ss.entry_id(p) == pid for p in cfg.get("plugins") or [])
    return pid not in dis and (pid.startswith("omarchy.") or listed)


ACME = {"name": "Acme", "suite": "acme.bar", "bar": {"id": "acme.bar"},
        "plugins": [{"id": "acme.notifications"}, {"id": "acme.lock"}, {"id": "acme.osd"}],
        "disabledPlugins": ["omarchy.notifications", "omarchy.lock", "omarchy.osd"]}
# an older loadout saved while acme's lock was on for everything
OTHER = {"name": "Other", "suite": "other.bar", "bar": {"id": "other.bar"},
         "plugins": [{"id": "acme.lock"}], "disabledPlugins": ["omarchy.lock"]}


class Compose(unittest.TestCase):
    def setUp(self):
        ss._MANIFESTS = None
        self.suites = ss.suites()

    def test_groups(self):
        g = ss.clone_groups(self.suites)
        self.assertEqual(g["omarchy.lock"], ["acme.lock"])
        self.assertEqual(g["omarchy.osd"], ["acme.osd"])
        self.assertIn("acme.notifications", g["omarchy.notifications"])
        self.assertNotIn("omarchy.audio", g, "bar widget copies run beside Omarchy's")

    def test_into_the_suite(self):
        cur = {"bar": {"id": "omarchy.bar"}, "plugins": [{"id": "taskchy"}], "disabledPlugins": []}
        new = ss.compose(cur, ACME, self.suites)
        for svc in ("notifications", "lock", "osd"):
            self.assertTrue(on(new, "acme." + svc), svc)
            self.assertFalse(on(new, "omarchy." + svc), svc)
        self.assertTrue(on(new, "taskchy"), "global plugins stay")

    def test_out_of_the_suite_always_one_lock(self):
        cur = ss.compose({"bar": {"id": "omarchy.bar"}, "plugins": [], "disabledPlugins": []}, ACME, self.suites)
        for target in (OTHER, {"name": "Omarchy", "suite": "omarchy.bar", "bar": {"id": "omarchy.bar"},
                               "plugins": [], "disabledPlugins": []}):
            new = ss.compose(cur, target, self.suites)
            for svc in ("notifications", "lock", "osd"):
                ons = [p for p in ("omarchy." + svc, "acme." + svc) if on(new, p)]
                self.assertEqual(ons, ["omarchy." + svc], f"{target['name']}: {svc}")

    def test_snapshot_keeps_only_what_swaps_use(self):
        cfg = {"bar": {"id": "acme.bar"}, "plugins": [{"id": "taskchy", "setting": 1}, {"id": "acme.lock"}],
               "disabledPlugins": ["omarchy.lock", "something.else"]}
        snap = ss.snapshot(cfg, "Acme", "acme.bar")
        self.assertEqual(snap["plugins"], [{"id": "acme.lock"}])
        self.assertEqual(snap["disabledPlugins"], ["omarchy.lock"])

    def test_shell_swapper_stays_on(self):
        new = ss.compose({"bar": {}, "plugins": [], "disabledPlugins": ["shell-swapper"]}, ACME, self.suites)
        self.assertTrue(on(new, "shell-swapper"))


class Ownership(unittest.TestCase):
    def test_prefixes(self):
        owns = lambda sid: ss.rules(sid, {})["owns"]
        self.assertEqual(owns("acme.bar"), ["acme."])
        self.assertEqual(owns("hancore.shibumi.bar"), ["hancore.shibumi."])
        self.assertEqual(owns("io.github.someone.bar"), [], "not every plugin that author made")
        self.assertEqual(owns("com.example.bar"), [])
        self.assertEqual(owns("io.github.someone.cool-shell.bar"), ["io.github.someone.cool-shell."])


class Listing(unittest.TestCase):
    def test_position_from_the_picture(self):
        self.assertEqual(ss.bar_position({"bar": {"position": "top"}, "preview": {"position": "left"}}), "left")
        self.assertEqual(ss.bar_position({"bar": {"position": "bottom"}}), "bottom")

    def test_empty_layout_advice(self):
        ss._MANIFESTS = None
        for sid, draws_own in (("solo.bar", True), ("acme.bar", False)):
            cur = {"bar": {"id": sid, "layout": {}}, "plugins": [], "disabledPlugins": []}
            f = ss.check_suite(sid, ss.suites(), cur)
            self.assertEqual(f["drawsOwn"], draws_own, sid)
            self.assertEqual(any("no widgets" in a for a in f["advice"]), not draws_own, sid)


class Imports(unittest.TestCase):
    def test_plain_binds_rebuilt(self):
        found, _ = ss.parse_import('o.bind("SUPER + R", "Launcher", [[omarchy-shell acme toggle]])\n'
                                   'hl.bind("SUPER + B", hl.dsp.exec_cmd("omarchy-shell acme bar"), { description = "Bar" })',
                                   prose=False)
        self.assertEqual([f["lua"] for f in found],
                         ['o.bind("SUPER + R", "Launcher", "omarchy-shell acme toggle")',
                          'o.bind("SUPER + B", "Bar", "omarchy-shell acme bar")'])

    def test_code_in_arguments_refused(self):
        for bad in ('o.bind("SUPER + Q", "x", (function() os.execute("touch /tmp/p") return "a" end)())',
                    'o.bind("SUPER + Q", "x", "a" .. os.getenv("HOME"))',
                    'hl.bind("SUPER + Q", hl.dsp.exec_cmd(os.getenv("X")))'):
            found, skipped = ss.parse_import(bad, prose=False)
            self.assertEqual(found, [], bad)
            self.assertTrue(any("plain" in s["why"] for s in skipped), bad)

    def test_bind_command(self):
        self.assertEqual(ss.bind_command('hl.bind("A", hl.dsp.exec_cmd("cmd x"), { description = "d" })'), "cmd x")
        self.assertEqual(ss.bind_command('o.bind("A", "d", "omarchy-shell shell toggle t \'{}\'")'),
                         "omarchy-shell shell toggle t '{}'")


class KeepKeys(unittest.TestCase):
    def test_only_my_keys_clears_the_old_shells(self):
        ss._MANIFESTS = None
        hypr = os.path.join(HOME, ".config/hypr")
        os.makedirs(hypr, exist_ok=True)
        with open(os.path.join(hypr, "hyprland.lua"), "w") as f:
            f.write("\n".join(ss.HOOK) + "\n")
        with open(ss.KEYS_FILE, "w") as f:
            f.write(ss.render_keys("Acme", [{"keys": "SUPER + R", "lua": 'o.bind("SUPER + R", "L", "omarchy-shell acme go")'}]))
        with open(ss.SHELL_JSON, "w") as f:
            json.dump({"bar": {"id": "acme.bar"}, "plugins": [], "disabledPlugins": []}, f)
        ss.save_loadout("other", dict(OTHER, keybinds=[{"keys": "SUPER + X", "desc": "x",
                                                         "lua": 'o.bind("SUPER + X", "x", "omarchy-shell other x")'}]))
        ss.set_status(active="")
        ss.apply("other", confirm=False, keys="keep")
        left = [b for b in ss.parse_binds(ss.read_text(ss.KEYS_FILE)) if b["kind"] == "bind"]
        self.assertEqual(left, [], "neither the old shell's keys nor the new one's")


class ImportKeys(unittest.TestCase):
    def test_import_writes_what_was_reviewed(self):
        os.makedirs(os.path.dirname(ss.BINDINGS), exist_ok=True)
        with open(ss.BINDINGS, "w") as f:
            f.write('o.bind("SUPER + A", "mine", "a")\n')
        ss.save_loadout("imp", {"name": "Imp", "suite": "acme.bar", "bar": {"id": "acme.bar"}, "plugins": []})
        ss.set_status(active="")
        text = ('o.bind("SUPER + K", "Panel", "omarchy-shell acme panel")\n'
                'o.bind("SUPER + K", "Again", "omarchy-shell acme other")\n'
                'o.bind("SUPER + Q", "x", (function() return "y" end)())\n')
        with self.assertRaises(ss.NeedsYes):
            ss.import_keys("imp", text, yes=False)
        ss.import_keys("imp", text, yes=True)
        kb = ss.get_loadout("imp")["keybinds"]
        self.assertEqual([(b["keys"], b["lua"]) for b in kb],
                         [("SUPER + K", 'o.bind("SUPER + K", "Panel", "omarchy-shell acme panel")')])
        self.assertIn('--[shell-swapper:imp] o.bind("SUPER + K", "Panel"', ss.read_text(ss.BINDINGS))


class Delete(unittest.TestCase):
    def test_keys_go_back(self):
        os.makedirs(os.path.dirname(ss.BINDINGS), exist_ok=True)
        with open(ss.BINDINGS, "w") as f:
            f.write("\n".join(['o.bind("SUPER + A", "mine", "a")', ""]
                              + ss.group_heading("Gone", "gone")
                              + ['--[shell-swapper:gone] o.bind("SUPER + R", "moved", "r")',
                                 '--[shell-swapper:gone] o.bind("SUPER + I", "imported", "i")', ""]))
        ss.save_loadout("gone", {"name": "Gone", "suite": "acme.bar", "bar": {"id": "acme.bar"}, "plugins": [],
                                 "keybinds": [{"keys": "SUPER + R", "desc": "moved", "lua": "", "from": "bindings.lua"},
                                              {"keys": "SUPER + I", "desc": "imported", "lua": "", "from": "import"}]})
        ss.set_status(active="")
        with self.assertRaises(ss.NeedsYes) as plan:
            ss.delete_loadout("gone", yes=False)
        self.assertEqual((len(plan.exception.plan["backToBindings"]), len(plan.exception.plan["dropFromBindings"])), (1, 1))
        self.assertTrue(os.path.exists(ss.loadout_path("gone")), "nothing done without --yes")
        ss.delete_loadout("gone", yes=True)
        text = ss.read_text(ss.BINDINGS)
        keys = [b["keys"] for b in ss.parse_binds(text) if b["kind"] == "bind"]
        self.assertEqual(keys, ["SUPER + A", "SUPER + R"])
        self.assertNotIn("shell-swapper:gone", text)
        self.assertFalse(os.path.exists(ss.loadout_path("gone")))


if __name__ == "__main__":
    unittest.main()
