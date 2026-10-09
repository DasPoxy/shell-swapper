#!/usr/bin/env bash
# Finish installing Shell-Swapper: links the `shell-swapper` CLI into
# ~/.local/bin, pointing back at this folder, so `omarchy plugin update
# shell-swapper` (or a `git pull`) updates it too.
#
# After `omarchy plugin add`, run it from the plugin's folder:
#   ~/.config/omarchy/plugins/shell-swapper/install.sh
# From a checkout elsewhere (for personal tweaks), it copies that checkout in
# as the plugin (a real folder, not a link: some shells' plugin catalogs won't
# follow a symlinked plugin) and enables it. Then:
#   install.sh --sync        copy your edits in again
#   install.sh --dev-hooks   do that after every commit, pull and checkout
#
# To take it out again:
#   install.sh --uninstall [--yes] [--purge]
# (installing again afterwards offers to put your keys back as they were;
# --yes does it without asking)
# keeps the keys of the shell you're in (moved into bindings.lua), takes out
# what Shell-Swapper added to your Hyprland config (backed up first), and
# removes the plugin and the CLI; your shell stays as it is. Your loadouts
# and backups are kept unless you add --purge.
set -euo pipefail

here="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
plugin_dir="$HOME/.config/omarchy/plugins/shell-swapper"

mode=install
yes=0
purge=0
for a in "$@"; do
  case $a in
    --sync) mode=sync ;;
    --dev-hooks) mode=hooks ;;
    --uninstall) mode=uninstall ;;
    --yes|-y) yes=1 ;;
    --purge) purge=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown option: $a (see --help)" >&2; exit 2 ;;
  esac
done

# Is this path one of ours: this checkout, or another Shell-Swapper checkout
# (a folder with Shell-Swapper's own plugin manifest)?
ours() {
  local p="$1" root
  p="$(readlink -f "$p" 2>/dev/null)" || return 1
  [[ $p == "$here" || $p == "$here"/* ]] && return 0
  root="$p"; [[ -d $root ]] || root="$(dirname "$root")"
  while [[ $root != / && $root != . ]]; do
    if [[ -f $root/manifest.json ]] && grep -q '"id": *"shell-swapper"' "$root/manifest.json" 2>/dev/null; then return 0; fi
    root="$(dirname "$root")"
  done
  return 1
}

# Link $at -> $target, but never over something that isn't ours: an existing
# file or folder is left alone, and so is a link pointing somewhere else.
link() {
  local target="$1" at="$2"
  mkdir -p "$(dirname "$at")"
  if [[ -L $at ]]; then
    if [[ $(readlink -f "$at" 2>/dev/null) == "$(readlink -f "$target")" ]]; then
      echo "already linked $at"; return
    fi
    if ! ours "$at"; then
      echo "skipping $at: it links to $(readlink "$at"), which isn't Shell-Swapper's" >&2
      return
    fi
  elif [[ -e $at ]]; then
    echo "skipping $at: something that isn't a link is already there" >&2
    return
  fi
  ln -sfn "$target" "$at"
  echo "linked $at -> $target"
}

# A checkout outside the plugins folder is copied in as the plugin, with
# .synced-from naming it. A link from an older install is swapped for the
# folder in one step (mv -T --exchange); a folder that isn't ours is left be.
sync_in() {
  [[ $(readlink -f "$plugin_dir" 2>/dev/null) == "$here" && ! -L $plugin_dir ]] && return 0   # installed in place
  mkdir -p "$(dirname "$plugin_dir")"
  if [[ -L $plugin_dir ]]; then
    if ! ours "$plugin_dir"; then
      echo "skipping $plugin_dir: it links to $(readlink "$plugin_dir"), which isn't Shell-Swapper's" >&2; return 1
    fi
    local tmp; tmp="$(dirname "$plugin_dir")/.sync-shell-swapper"
    rm -rf "$tmp"
    rsync -a --exclude __pycache__ --exclude .git --exclude demo "$here/" "$tmp/"
    printf '%s\n' "$here" > "$tmp/.synced-from"
    mv -T --exchange "$tmp" "$plugin_dir" || { rm -rf "$tmp"; echo "couldn't replace the link $plugin_dir" >&2; return 1; }
    [[ -L $tmp ]] && rm -f "$tmp"
    echo "copied $here into $plugin_dir (it was a link)"
  elif [[ -e $plugin_dir && ! -f $plugin_dir/.synced-from ]]; then
    echo "skipping $plugin_dir: something that isn't Shell-Swapper's copy is there" >&2; return 1
  else
    rsync -a --delete --delay-updates --exclude __pycache__ --exclude .git --exclude demo --exclude .synced-from \
      "$here/" "$plugin_dir/"
    [[ $(cat "$plugin_dir/.synced-from" 2>/dev/null) == "$here" ]] || printf '%s\n' "$here" > "$plugin_dir/.synced-from"
  fi
  return 0
}

# ---- uninstall -----------------------------------------------------------------
uninstall() {
  local hypr="$HOME/.config/hypr" conf="$HOME/.config/shell-swapper" state="$HOME/.local/state/shell-swapper"
  local bindings="$hypr/bindings.lua" hyprland="$hypr/hyprland.lua" keys="$hypr/shell_swapper.lua"
  local cli="$HOME/.local/bin/shell-swapper" stamp; stamp="$(date +%Y%m%d-%H%M%S)"

  # never in the middle of a swap: keep or revert it first
  if command -v shell-swapper >/dev/null && shell-swapper status 2>/dev/null | grep -q '"pending": *{'; then
    echo "a swap is waiting to be kept or reverted: do that first (or: shell-swapper rescue)" >&2; return 1
  fi
  local active=""
  command -v shell-swapper >/dev/null && active="$(shell-swapper status 2>/dev/null | sed -n 's/.*"active": *"\([^"]*\)".*/\1/p')"

  echo "Uninstalling Shell-Swapper:"
  [[ -s $keys ]] && echo "  - keep the keys of the shell you're in${active:+ ($active)}: move them from $keys into bindings.lua"
  grep -q '>>> shell-swapper keys >>>' "$hyprland" 2>/dev/null && echo "  - take the shell-swapper keys block out of hyprland.lua"
  grep -qE 'shell toggle shell-swapper|shell-swapper rescue' "$bindings" 2>/dev/null && echo "  - comment out the Shell-Swapper keys in bindings.lua"
  echo "  (bindings.lua, hyprland.lua and shell_swapper.lua are backed up first, as *.before-shell-swapper-uninstall-$stamp)"
  [[ -e $plugin_dir ]] && echo "  - disable and remove the plugin ($plugin_dir)"
  [[ -L $cli ]] && echo "  - remove the CLI link ($cli)"
  if (( purge )); then
    echo "  - PURGE: delete your loadouts ($conf) and Shell-Swapper's backups ($state)"
  else
    echo "  - keep your loadouts ($conf) and backups ($state): --purge to delete them too"
  fi
  echo "  Your shell stays as it is."
  if (( ! yes )); then
    if [[ -t 0 ]]; then
      read -r -p "Go ahead? [y/N] " ok
      [[ $ok == [yY]* ]] || { echo "nothing changed"; return 1; }
    else
      echo "run again with --yes to do it"; return 2
    fi
  fi

  # keybinds: back up, keep the live keys, take out what Shell-Swapper added
  for f in "$bindings" "$hyprland" "$keys"; do
    [[ -f $f ]] && cp -p "$f" "$f.before-shell-swapper-uninstall-$stamp"
  done
  python3 - "$bindings" "$hyprland" "$keys" "$active" "$stamp" <<'PY'
import os, re, sys
bindings, hyprland, keys, active, stamp = sys.argv[1:6]
b = open(bindings).read() if os.path.exists(bindings) else ""
# the Shell-Swapper keys themselves: commented out, never deleted
b = "\n".join(("-- (Shell-Swapper uninstalled) " + l) if re.search(r"shell toggle shell-swapper|shell-swapper rescue", l) and not l.lstrip().startswith("--") else l
              for l in b.split("\n"))
# the live keys of the loadout in use: into bindings.lua, so this shell keeps them
if os.path.exists(keys):
    body = [l for l in open(keys).read().split("\n") if l.strip() and not l.lstrip().startswith("--")]
    if body:
        b = b.rstrip("\n") + "\n\n-- Keys from your Shell-Swapper loadout" + (f" ({active})" if active else "") + \
            f", kept here when Shell-Swapper was uninstalled ({stamp})\n" + "\n".join(body) + "\n"
    os.remove(keys)
open(bindings, "w").write(b)
if os.path.exists(hyprland):
    h = open(hyprland).read()
    h = re.sub(r"-- >>> shell-swapper keys >>>.*?-- <<< shell-swapper keys <<<\n?", "", h, flags=re.S)
    open(hyprland, "w").write(h)
PY
  echo "keys: kept in bindings.lua; Shell-Swapper's block taken out"

  # the plugin
  if [[ -e $plugin_dir || -L $plugin_dir ]]; then
    command -v omarchy-shell >/dev/null && omarchy-shell shell setPluginEnabled shell-swapper false >/dev/null 2>&1 || true
    if [[ -L $plugin_dir ]] || [[ -f $plugin_dir/.synced-from ]] || grep -q '"id": *"shell-swapper"' "$plugin_dir/manifest.json" 2>/dev/null; then
      rm -rf -- "$plugin_dir"
      echo "removed $plugin_dir"
    else
      echo "left $plugin_dir alone: it isn't Shell-Swapper's" >&2
    fi
  fi
  # the CLI, if it's our link
  if [[ -L $cli ]] && ours "$cli" 2>/dev/null || [[ -L $cli && ! -e $cli ]]; then
    rm -f -- "$cli"; echo "removed $cli"
  fi
  # this checkout's dev hooks
  for h in post-commit post-merge post-checkout; do
    [[ -f $here/.git/hooks/$h ]] && grep -q 'install.sh --sync' "$here/.git/hooks/$h" && rm -f "$here/.git/hooks/$h"
  done
  if (( purge )); then
    rm -rf -- "$conf" "$state"; echo "deleted $conf and $state"
  fi
  command -v omarchy-shell >/dev/null && omarchy-shell shell rescanPlugins >/dev/null 2>&1 || true
  command -v hyprctl >/dev/null && hyprctl reload >/dev/null 2>&1 || true
  echo "Shell-Swapper is uninstalled."
}

if [[ $mode == uninstall ]]; then uninstall; exit $?; fi
if [[ $mode == sync ]]; then sync_in; exit $?; fi
if [[ $mode == hooks ]]; then
  [[ -d $here/.git/hooks ]] || { echo "not a git checkout: no hooks" >&2; exit 1; }
  for h in post-commit post-merge post-checkout; do
    if [[ -e $here/.git/hooks/$h ]] && ! grep -q 'install.sh --sync' "$here/.git/hooks/$h"; then
      echo "skipping the $h hook: there's one already" >&2; continue
    fi
    printf '#!/bin/sh\n# Shell-Swapper: copy this checkout into Omarchy after it changes\n"%s/install.sh" --sync >/dev/null 2>&1 &\n' "$here" > "$here/.git/hooks/$h"
    chmod +x "$here/.git/hooks/$h"
  done
  echo "git hooks: copied in after every commit, pull and checkout"
  exit 0
fi

sync_in || true
if [[ -f $plugin_dir/manifest.json ]] \
   && command -v omarchy-shell >/dev/null && omarchy-shell shell ping >/dev/null 2>&1; then
  omarchy-shell shell rescanPlugins >/dev/null || true
  # discovery is asynchronous: wait (up to 4 s) until the shell knows it
  for _ in $(seq 40); do
    omarchy-plugin-list --json 2>/dev/null | grep -q '"shell-swapper"' && break
    sleep 0.1
  done
  omarchy-shell shell setPluginEnabled shell-swapper true >/dev/null || true
  echo "enabled the shell-swapper plugin"
fi

link "$here/bin/shell-swapper" "$HOME/.local/bin/shell-swapper"

# installing again after an uninstall: offer to put the keys back as they were
if grep -qE '^-- Keys from your Shell-Swapper loadout|^-- \(Shell-Swapper uninstalled\) ' "$HOME/.config/hypr/bindings.lua" 2>/dev/null; then
  echo ""
  echo "An earlier uninstall left your loadout keys in bindings.lua (and commented out Shell-Swapper's)."
  echo "Putting them back moves them into the loadout again, un-comments Shell-Swapper's keys and"
  echo "restores the keys hook - bindings.lua is backed up first."
  ok=n
  if (( yes )); then ok=y
  elif [[ -t 0 ]]; then read -r -p "Put them back? [Y/n] " ok; [[ -z $ok ]] && ok=y
  else echo "(run install.sh --yes, or: shell-swapper keys reinstate --yes)"
  fi
  if [[ $ok == [yY]* ]]; then
    "$here/bin/shell-swapper" keys reinstate --yes >/dev/null && echo "keys put back" && { hyprctl reload >/dev/null 2>&1 || true; }
  fi
fi

cat <<'MSG'

Open the picker:   omarchy-shell shell toggle shell-swapper '{}'
Keys for it, in ~/.config/hypr/bindings.lua:
  o.bind("SUPER + CTRL + ALT + S", "Shell-Swapper", "omarchy-shell shell toggle shell-swapper '{}'")
  o.bind("SUPER + CTRL + ALT + BACKSPACE", "Shell-Swapper rescue", "shell-swapper rescue")
The rescue key puts back the last shell you kept, even if the bar won't start.
MSG
