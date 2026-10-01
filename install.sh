#!/usr/bin/env bash
# Finish installing Shell-Swapper: links the `shell-swapper` CLI into
# ~/.local/bin, pointing back at this folder, so `omarchy plugin update
# shell-swapper` (or a `git pull`) updates it too.
#
# After `omarchy plugin add`, run it from the plugin's folder:
#   ~/.config/omarchy/plugins/shell-swapper/install.sh
# From a copy elsewhere (for personal tweaks), it also links that checkout in
# as the plugin and enables it.
set -euo pipefail

here="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
plugin_dir="$HOME/.config/omarchy/plugins/shell-swapper"

case ${1:-} in
  "") ;;
  -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
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

# a checkout outside the plugins folder: link it in as the plugin
if [[ $(readlink -f "$plugin_dir" 2>/dev/null) != "$here" ]]; then
  link "$here" "$plugin_dir"
fi
if [[ $(readlink -f "$plugin_dir" 2>/dev/null) == "$here" ]] \
   && command -v omarchy-shell >/dev/null && omarchy-shell shell ping >/dev/null 2>&1; then
  omarchy-shell shell rescanPlugins >/dev/null || true
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
