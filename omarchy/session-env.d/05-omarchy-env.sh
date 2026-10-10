# Load the same session environment Omarchy's Hyprland session gets through uwsm: OMARCHY_PATH,
# PATH additions, TERMINAL/EDITOR defaults, mise, and the user's own overrides.
for omarchy_env in /usr/share/uwsm/env.d/* "${XDG_CONFIG_HOME:-$HOME/.config}"/uwsm/env.d/*; do
  [ -r "$omarchy_env" ] && . "$omarchy_env"
done
unset omarchy_env
# Keep the existing theme timer authoritative until Sunlight is explicitly enabled.
export SCOTTLAND_SOLAR_OPT_IN=1
SCOTTLAND_IMPORT_ENV="${SCOTTLAND_IMPORT_ENV:+$SCOTTLAND_IMPORT_ENV }SCOTTLAND_SOLAR_OPT_IN"
export SCOTTLAND_IMPORT_ENV
