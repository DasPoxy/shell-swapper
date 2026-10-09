# Changelog

Shell-Swapper follows [semantic versioning](https://semver.org): until 1.0,
a minor version (0.x.0) may change how things work; patch versions only fix.
Each release is a git tag, so you can install or go back to a known one:

```sh
git -C ~/.config/omarchy/plugins/shell-swapper checkout v0.1.0-beta.1
```

## 0.1.0-beta.1 - 2026-10-09

The first tagged release, for testing beyond its author's machine.

- **Swapping**: save the shell you're using as a loadout and swap between
  loadouts (Omarchy's bar, Slime, Ruixen, Shibumi, Omacale...). Every swap is
  backed up first and reverts by itself unless you keep it; a rescue key puts
  back the last shell you kept.
- **Keys per loadout**: a loadout can carry its own keybinds, written to
  their own file; your `bindings.lua` is only changed when you ask, and is
  always backed up first. Import a shell's recommended keys from its README.
- **New shells**: a shell's installer making itself your bar is noticed, and
  that setup saved as a loadout straight away.
- **Bar pictures** in the picker, and shell-specific widgets kept on their
  own shell's bar.
- **Settings**: one lock screen for every shell, the backups folder,
  uninstalling a shell (archived, restorable; its installer's records too).
- **Install / uninstall**: `install.sh`, `install.sh --uninstall` (keeps
  the keys of the shell you're in), and installing again puts keys back.
- **Safety for new setups**: `shell-swapper doctor` checks what it needs from
  Omarchy; swaps are refused when something is missing and an untested
  Omarchy version is flagged in the picker. Uninstalling a shell keeps
  plugins credited to another author unless asked (`--all`), and needs `u`
  to confirm.
- **Tests and CI**: the swap lifecycle (apply, keep, revert, the watchdog,
  rescue), keys, uninstall/restore; run on every push.
- **Audit fixes (2026-10-09)**: Lua quoting that could break the Hyprland
  config, symlinked config files, a shell's installer refusing to reinstall.
