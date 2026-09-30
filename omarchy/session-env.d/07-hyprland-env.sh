# The environment Omarchy's Hyprland config sets with hl.env (Qt/GTK/Electron integration, cursor
# size, compose file, theme colors, the user's own variables), evaluated by the Lua host from the
# same config Hyprland reads, so apps get the same environment in either desktop. The names are
# also handed to user services (autostart.d/05-import-environment), as uwsm does under Hyprland,
# so apps started through systemd or D-Bus get them too.
if command -v lua >/dev/null 2>&1; then
  hyprland_env=$("${SCOTTLAND_HOOKS:-/usr/lib/scottland}/libexec/scottland-luahost" --print-env 2>/dev/null) &&
    eval "$hyprland_env"
  hyprland_env_names=$(printf '%s\n' "$hyprland_env" | sed -n 's/^export \([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' | tr '\n' ' ')
  SCOTTLAND_IMPORT_ENV="${SCOTTLAND_IMPORT_ENV:+$SCOTTLAND_IMPORT_ENV }$hyprland_env_names"
  export SCOTTLAND_IMPORT_ENV
  unset hyprland_env hyprland_env_names
fi
