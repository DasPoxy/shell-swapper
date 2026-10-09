---
name: Something went wrong
about: A swap, the picker, keys, installing or uninstalling misbehaved
labels: bug
---

**What happened, and what you expected**


**Steps** (which shells, what you pressed)


**Your setup** - paste the output of:

```sh
shell-swapper doctor
shell-swapper status
omarchy-shell shell ping; hyprctl version | head -1
```

**Got stuck?** `SUPER + CTRL + ALT + BACKSPACE` (or `shell-swapper rescue`)
puts back the last shell you kept; every change is backed up in
`~/.local/state/shell-swapper/backups/`.
