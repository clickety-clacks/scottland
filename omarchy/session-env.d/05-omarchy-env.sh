# Load the same session environment Omarchy's Hyprland session gets through uwsm: OMARCHY_PATH,
# PATH additions, TERMINAL/EDITOR defaults, mise, and the user's own overrides.
for omarchy_env in /usr/share/uwsm/env.d/* "${XDG_CONFIG_HOME:-$HOME/.config}"/uwsm/env.d/*; do
  [ -r "$omarchy_env" ] && . "$omarchy_env"
done
unset omarchy_env
