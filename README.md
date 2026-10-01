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
- **Backups.** `shell.json` is backed up before every swap (the last 20, in
  `~/.local/state/shell-swapper/backups/`), and swaps never run while the
  screen is locked.

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
`~/.config/hypr/bindings.lua`:

```lua
o.bind("SUPER + CTRL + ALT + S", "Shell-Swapper", "omarchy-shell shell toggle shell-swapper '{}'")
o.bind("SUPER + CTRL + ALT + BACKSPACE", "Shell-Swapper rescue", "shell-swapper rescue")
```

## The picker

| key | does |
|---|---|
| ↑ ↓ (j k) | pick a loadout, or a suite to start fresh from |
| Enter (or double-click) | swap to it |
| s | save the current shell as a new loadout (or update one by its name) |
| r | rename the loadout |
| d, d | delete it (to `~/.local/state/shell-swapper/trash/`) |
| Esc | close |

The right side sketches the loadout's bar: what sits left, centre and right.
Along the bottom: **apply by** *restart* (the sure way) or *reload* (Omarchy
hot-reloads `shell.json`, no restart flash), and the keep-it timer.

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
shell-swapper rescue
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
| `~/.local/state/shell-swapper/` | backups, the last kept config, a waiting swap |

MIT licensed.
