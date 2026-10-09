# Shell-Swapper

Swap between shell loadouts on Omarchy in a keystroke: Omarchy's own bar,
Slime Shell, Ruixen, or any other plugin suite that replaces the bar. Each
keeps its own layout and widget settings, every swap is backed up, and a swap
reverts itself unless you keep it.

## What it does

- **Finds your bar suites.** Any installed plugin that can be the bar
  (`"kinds": ["bar"]` in its manifest) is listed, and Omarchy's own bar
  always is.
- **Loadouts.** A loadout is a named snapshot of what makes a shell that
  shell: the `bar` section of `~/.config/omarchy/shell.json` (which bar, its
  layout, its widgets' settings), the plugins it brings and the ones it
  switches off. Tweak your layout, swap away, swap back: the tweaks are still
  there (the shell you leave is saved as you go).
- **Global plugins stay global.** Plugins that don't belong to a suite (a
  theme manager, Taskchy...) stay exactly as they are whatever you swap to.
  A suite's own plugins (`slime.*`, `ruixen.*`) follow its loadout.
- **No clashes.** Only one notification server runs (the suite's own, else
  Omarchy's); Ruixen's notch is off unless Ruixen is the bar; Omarchy's menu
  widget comes back with Omarchy's bar.
- **Keep it, or it goes back.** After a swap the new shell asks *Keep this
  shell?* with a countdown (10, 15 or 30 s). Enter keeps it; Esc, or waiting,
  goes back. A watchdog outside the shell does the reverting, so a shell that
  never comes up to ask is put back too.
- **Rescue.** `shell-swapper rescue` (bind it to a key) puts back the last
  shell you kept and restarts, even when the bar won't start.
- **Keys per shell.** A loadout can carry its own keybinds (Slime's launcher
  on SUPER+R, Ruixen's on SUPER+X...). See [Keys](#keys).
- **Backups**, in `~/.local/state/shell-swapper/backups/`:
  - `original/`: your `bindings.lua`, `hyprland.lua` and `shell.json` as they
    were before Shell-Swapper first changed them. Kept for good, never
    touched again.
  - `swaps/<time>/`: a quick copy of `shell.json`, `bindings.lua` and the
    loadout keys file before every swap (the last 20).
  - `loadouts/<loadout>/`: each loadout as it was before every change to it
    (the last 10 of each).
  - `keys/`: every keybind file before Shell-Swapper writes it (the last 40).

  Swaps never run while the screen is locked.

**New shells are checked the first time they're seen** (a few seconds after
the shell starts, and whenever the picker opens). A shell's own installer
usually makes itself your bar before Shell-Swapper knows it exists, so:

- if the new bar is your bar now, it's saved as a loadout straight away,
  exactly as its installer set it up, before any swap could change it;
- what its installer left on it from other shells (another suite's widgets
  or plugins) is reported, with one click (plan first) to take them out;
- it says if it can't tell which plugins are the shell's own, if a bar that
  isn't set up yet needs its installer run first, or if it brings its own
  notification server.

You get a notification, and a note at the top of the picker. Starting fresh
from a suite never makes an empty bar: it takes what its installer set up,
or a layout the suite declares, or tells you to run its installer first.
(`shell-swapper check [BAR_ID]` runs the same check by hand.)

On first run it saves the shell you're using as a loadout. If Slime Shell's
`slime-shell use` has a backup of the bar it replaced, that's imported as a
loadout too.

## Install

```sh
omarchy plugin add https://github.com/DasPoxy/shell-swapper
~/.config/omarchy/plugins/shell-swapper/install.sh
```

`install.sh` links the `shell-swapper` CLI into `~/.local/bin` (pointing back
at the plugin, so updates reach it) and prints the keys to add to
`~/.config/hypr/bindings.lua`. Run from a checkout of your own elsewhere, it
copies that checkout in as the plugin (a real folder: some shells' plugin
catalogs won't follow a symlinked one); `install.sh --sync` copies your edits
in again, and `install.sh --dev-hooks` does that after every commit and pull.

```lua
o.bind("SUPER + CTRL + ALT + S", "Shell-Swapper", "omarchy-shell shell toggle shell-swapper '{}'")
o.bind("SUPER + CTRL + ALT + BACKSPACE", "Shell-Swapper rescue", "shell-swapper rescue")
```

## Uninstall

From the plugin's folder (or the checkout you installed from):

```sh
~/.config/omarchy/plugins/shell-swapper/install.sh --uninstall
```

It says what it's going to do and asks first (`--yes` skips the question),
and won't run while a swap is waiting to be kept or reverted. It:

- keeps the keys of the shell you're in: the loadout keys live in
  `~/.config/hypr/shell_swapper.lua` are moved into `bindings.lua`, under a
  heading saying where they came from;
- takes out the block that loads loadout keys from `hyprland.lua`, and
  comments out (doesn't delete) the Shell-Swapper keys in `bindings.lua`;
  all three files are backed up first, next to themselves, as
  `*.before-shell-swapper-uninstall-<time>`;
- disables and removes the plugin, the `shell-swapper` CLI link and, in a
  checkout, its `--dev-hooks` git hooks.

Your shell stays as it is. Your loadouts (`~/.config/shell-swapper`) and
backups (`~/.local/state/shell-swapper`) are kept, so installing again picks
up where you left off; add `--purge` to delete them too (that includes the
backups of your setup from before Shell-Swapper).

Installing again afterwards (`omarchy plugin add`, then `install.sh`) notices
what the uninstall left in `bindings.lua` and offers to put it back: the
loadout keys move back into the loadout, Shell-Swapper's keys are
un-commented and the keys hook returns - as before the uninstall (`--yes`
does it without asking; or later, `shell-swapper keys reinstate --yes`).

## The picker

| key | does |
|---|---|
| ↑ ↓ | pick a loadout, or a suite to start fresh from |
| Enter (or double-click) | swap to it |
| k | its keybinds (see [Keybinds](#keybinds)) |
| v | its picture, or its sketch |
| p | picture the bar of the shell you're using |
| s | save the current shell as a new loadout (or update one by its name) |
| r | rename the loadout |
| d, d | delete it (to `~/.local/state/shell-swapper/trash/`); keys you moved into it go back into `bindings.lua` |
| , (or the ⚙ settings button, top right) | settings: the lock screen for every shell, the backups folder, uninstalling and restoring a shell |
| Esc | close |

The right side shows the loadout's bar: a **picture** of it (in three
pieces, left / centre / right, so it's big enough to read; side by side for
a bar on the left or right), or a **sketch** of what sits where (**v**
switches). Hovering a loadout shows its picture larger. Only the shell on
screen can be pictured, so pictures are taken when you keep a swap, when
the shell starts with a loadout that has none, after you save one, or when
you ask (**p** on the shell you're using). Never while something covers
the bar: a fullscreen window, or a swap still asking. They live in
`~/.local/state/shell-swapper/previews/`.
Along the bottom: **apply by** *restart* (the sure way) or *reload* (Omarchy
hot-reloads `shell.json`, no restart flash), and the keep-it timer.

### Lock screen (settings)

Only one lock screen can run, and it's the same in every shell: **Omarchy's
own** by default. In the settings you can pick another lock screen plugin
instead, such as [Lock Screen Explorer](https://github.com/SirJul1337/omarchy-lock-explorer)
(offered to install if you don't have it). The pick switches over at once
and stays on whichever shell you swap to, with its own settings, over a
shell's own lock screen; picking Omarchy's goes back. Without a pick, a
shell that brings its own lock screen uses it while it's on screen.

### Backups and uninstalling a shell (settings)

**b** opens the backups folder (`~/.local/state/shell-swapper/backups`):
every swap's `shell.json` and keybind files, your setup from before
Shell-Swapper (`original/`) and shells you've uninstalled.

**u** uninstalls a shell: pick one and it shows exactly what goes first - its
bar and the plugins that come with it (the ones sharing its id's prefix:
`synoptik.bar`, `synoptik.notifications`...). Omarchy's own and the shell
you're in aren't offered, and anything the shell you're in or another
shell's loadout still uses is kept (say, a Slime widget on another shell's
bar). The removed plugins are archived first, in `backups/uninstalled/`
(**r** in the settings restores one); your loadouts for that shell are kept
until you delete them. A shell whose own installer keeps records of what it
installed (Shibumi's `~/.local/state/shibumi`) has those archived and cleared
too - left behind, they still say "installed" and its installer refuses to
install it again. From the command line: `shell-swapper uninstall-shell
SUITE [--yes]`, `shell-swapper uninstalled`, `shell-swapper restore-shell
ARCHIVE [--yes]`.

## Widgets stay on their own shell's bar

A widget belongs to a shell when that shell owns its plugin (`slime.*` is
Slime's, `ruixen.*` Ruixen's, `hancore.shibumi.*` Shibumi's). One on another
shell's bar is flagged: in the loadout's sketch (red), with a note under it
and **Take them off** (plan first; only the bar changes, the plugins stay,
since keys may still use them). A widget whose plugin isn't installed is
shown struck through. When a save (yours, or the automatic one as you swap
away) puts another shell's widget on a bar, the picker tells you, so one
added through a bar's own widget picker doesn't go unnoticed.
`shell-swapper widgets [ID]` lists them; `shell-swapper take-off ID [WIDGET...]`
takes them off.

## Keybinds

**k** (or the *Keybinds* button) on a loadout opens its keybinds:

- **1 Its actions.** Everything that shell can do from a key, found by
  scanning its own plugins for IPC calls (`omarchy-shell slime-shell
  toggleTab home`, `omarchy-shell ruixen.notch toggleDashboard`...) and its
  panels (`omarchy-shell shell toggle <plugin>`), plus the keys it already
  has. Calls that list their options in a comment get one entry per option;
  others take their arguments in boxes. Pick one, set its key (the
  SUPER / CTRL / ALT / SHIFT buttons, then press **c** and a key), and see
  straight away who has that key now: your `bindings.lua`, this shell, or
  Omarchy's defaults.
- **Same key in other shells (t).** Each action has a category (dashboard,
  launcher, settings, wallpapers, tasks, media...). Your key is offered for
  the most similar call in every other shell: Slime's command centre on
  SUPER+ALT+C becomes Ruixen's notch dashboard on SUPER+ALT+C. You review each
  one (←/→ picks another call or leaves that shell alone, space turns one
  off), then see the full plan before anything is set. It's kept as a
  template (**3 Templates**).
- **2 From bindings.lua.** Move keys you already have into the shell (below).
- **4 Import.** Paste the binds a shell's repo recommends (its README, docs,
  install notes), in Omarchy's style (`o.bind("SUPER + R", "Launcher",
  "omarchy-shell ...")`) or Hyprland's (`bind = SUPER ALT, C, exec, ...`,
  `bindd` with a description). Keybinds written as a README table or list
  count too (`| Super + Alt + C | Command centre | \`omarchy-shell ...\` |`,
  ``- `Super+R`: launcher (`omarchy-shell ...`)``): a whole key combo and a
  command beside it. **f** looks through the shell's own checkout for you
  (or, for a shell its installer copied in, the checkout in your home it
  came from): code, scripts, recipes in comments, and every `.md` / `.txt`
  that talks about binds or keybinds, keeping only binds that call that
  shell. Installed copies often leave the repo's docs behind (Ruixen's
  `docs/KEYBINDS.md`), so **f** also reads the shell's upstream repo on
  GitHub: the one its manifest names (`repository`, `homepage`, or
  `shellSwapper.repo`), else the GitHub link in its own files whose name has
  the shell's name in it. Only public GitHub, only when you press f, cached
  for a day in `~/.cache/shell-swapper/remote/`; offline, it uses what's
  local. Every bind found is checked: a combo Hyprland can bind (known
  modifiers, exactly one real key) and a whole command (no `<placeholder>`
  left in it); the rest are listed with what's wrong. On the right you see what each line became:
  new, replacing one of its keys, already there, or left out (and why: a
  non-exec Hyprland bind, the same key twice, a call it already has on
  another key, prose). **i** imports them, after showing the plan: into the
  shell's loadout and, tagged, into its group in `bindings.lua`. A key they
  replace keeps its line there, marked `--[shell-swapper:<id>:replaced]`;
  taking an imported key out again removes the line Shell-Swapper added
  (it never came from your file, so it isn't handed to every shell).

## Keys

A loadout can have keys of its own. They live in a file of their own,
`~/.config/hypr/shell_swapper.lua`, loaded by one guarded line after your
`bindings.lua` (`pcall(require, "hypr.shell_swapper")`, so nothing breaks if
the file goes away). Swapping writes that file, never your `bindings.lua`.

- **Nothing changes without asking.** Setting up that one line, moving keys
  in or out, and a swap whose keys differ from the ones you have all show you
  exactly what would change first. A swap asks *Swap keys too / Only my keys
  / Cancel*; "only my keys" means just your `bindings.lua`, so none of the
  shell you're leaving stays bound.
- **Everything is backed up first.** `bindings.lua`, `hyprland.lua` and the
  keys file are copied to `~/.local/state/shell-swapper/backups/keys/`
  before every write (the last 40). Going back from a swap, and rescue, put
  the keys back too.
- **Moving keys in** (b, then pick keys from your `bindings.lua` with space
  and press m): they're commented out in `bindings.lua`, tagged with the
  loadout, not deleted, along with any `hl.unbind` of the same key (and a
  comment sitting between two of them). Taking a key out of the loadout (d)
  un-comments it again, exactly as it was.
- **Tidy groups.** Each loadout's keys are gathered in one group in
  `bindings.lua`, under a heading, with a blank line before and after:

  ```lua
  -- SlimeS-Bar keys (Shell-Swapper loadout: slimes-bar)
  -- Tagged lines belong to that loadout: Shell-Swapper loads them from
  -- hypr/shell_swapper.lua while it's your shell. Untagged lines work in every shell.
  --[shell-swapper:slimes-bar] hl.unbind("SUPER + R")
  --[shell-swapper:slimes-bar] o.bind("SUPER + R", "Slime launcher", "omarchy-shell slime-launcher apps")
  ```

  A block of yours that's only comments and one loadout's keys becomes its
  group in place; otherwise the group is added after the others (or at the
  end). Nothing else in the file moves. `o` in the keys manager (or `keys
  organize`) tidies a file that's drifted, showing the diff first.
- **No double keys.** A loadout key that your `bindings.lua` binds too would
  fire both, so it's left out (and shown in red) until you sort it out.
- Each loadout key is written with an `hl.unbind` before it, so it replaces
  Omarchy's default for that key while the loadout is in use, and the
  default comes back when you swap to a shell without it.

## The command line

Everything the picker does, as JSON, for scripts and agents:

```sh
shell-swapper list                  # suites, loadouts, the active one, a pending swap
shell-swapper apply ruixen-bar      # swap (then keep it when it asks)
shell-swapper apply suite:omarchy.bar   # a suite's default layout, as a new loadout
shell-swapper apply --suite slime.bar   # that suite's loadout (or its default)
shell-swapper apply --previous      # back to the one before
shell-swapper confirm | revert      # answer a waiting swap
shell-swapper save "Work bar"       # the current shell, as a loadout
shell-swapper rename ID NAME | delete ID
shell-swapper config apply reload   # or restart; config confirmSeconds 30
shell-swapper locks                 # the lock screens to choose from
shell-swapper lock io.github.sirjul1337.lock-explorer   # the lock in every shell (omarchy.lock: the default)
shell-swapper preview               # picture the bar on screen, for its loadout
shell-swapper rescue

# keys: anything that would change the keys you have now prints its plan and
# exits with status 2; run it again with --yes to do it
shell-swapper apply ruixen-bar --keys        # or --keep-keys
shell-swapper keys status | hook | scan      # the hook, your bindings.lua's binds
shell-swapper keys list ID | diff ID
shell-swapper keys move ID "SUPER + R" ...   # from bindings.lua into a loadout
shell-swapper keys remove ID N               # out again (back to bindings.lua)
shell-swapper keys add ID 'o.bind("SUPER + R", "Launcher", "my-launcher")'
shell-swapper keys organize                  # gather each loadout's keys into its group
shell-swapper keys actions ID                # what that shell can do: its IPC calls, by category
shell-swapper keys check ID "SUPER + ALT + C"   # who has that key now
shell-swapper keys set ID "SUPER + ALT + C" "Dashboard" "omarchy-shell ruixen.notch toggleDashboard"
shell-swapper keys set-many '[{"loadout": ..., "keys": ..., "desc": ..., "command": ...}]'
shell-swapper keys propose ID "SUPER + ALT + C" "omarchy-shell slime-shell toggleTab home"
shell-swapper keys templates | template-save NAME JSON | template-delete NAME
shell-swapper keys find ID [--local] [--repo owner/repo]   # binds the shell's files and repo recommend
shell-swapper keys import ID --check < binds.txt   # what a paste would become
shell-swapper keys import ID --text '...'    # import (plan first; --yes to do it)
shell-swapper backups                        # everything kept, by kind
shell-swapper keys restore original          # your bindings.lua from before Shell-Swapper
shell-swapper keys restore swaps/<time>/bindings.lua
shell-swapper loadout-restore ID FILE        # a loadout as it was (see backups)
```

## For suite authors

A bar plugin can say what comes with it, in its `manifest.json`:

```json
"shellSwapper": {
  "enable": ["mysuite.notifications"],
  "disable": ["omarchy.notifications"],
  "owns": ["mysuite."],
  "layout": "layouts/default.json"
}
```

`enable` / `disable`: plugins switched on and off with it. `owns`: id prefixes
of its own plugins (they follow its loadouts; default: the bar id's prefix).
`layout`: its default `bar` config, relative to the plugin folder, for
starting fresh. Slime Shell, Ruixen and Omarchy's bar have these built in.

## Files

| | |
|---|---|
| `~/.config/shell-swapper/loadouts/*.json` | your loadouts |
| `~/.config/shell-swapper/config.json` | apply mode, keep-it timer |
| `~/.local/state/shell-swapper/` | backups (shell.json and keybind files, `uninstalled/` shells), the last kept config, a waiting swap |
| `~/.config/hypr/shell_swapper.lua` | the keys of the loadout in use (written on a swap) |

MIT licensed.
