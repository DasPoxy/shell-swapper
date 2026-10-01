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
set -euo pipefail

here="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
plugin_dir="$HOME/.config/omarchy/plugins/shell-swapper"

mode=install
case ${1:-} in
  "") ;;
  --sync) mode=sync ;;
  --dev-hooks) mode=hooks ;;
  -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
  *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
esac

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

cat <<'MSG'

Open the picker:   omarchy-shell shell toggle shell-swapper '{}'
Keys for it, in ~/.config/hypr/bindings.lua:
  o.bind("SUPER + CTRL + ALT + S", "Shell-Swapper", "omarchy-shell shell toggle shell-swapper '{}'")
  o.bind("SUPER + CTRL + ALT + BACKSPACE", "Shell-Swapper rescue", "shell-swapper rescue")
The rescue key puts back the last shell you kept, even if the bar won't start.
MSG
