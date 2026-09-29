# Make Hyprland-aware programs (the Omarchy shell, hyprctl, Quickshell) talk to scottland-hyprshim.
HYPRLAND_INSTANCE_SIGNATURE="scottland_$(date +%s)_$$"
export HYPRLAND_INSTANCE_SIGNATURE
SCOTTLAND_IMPORT_ENV="${SCOTTLAND_IMPORT_ENV:+$SCOTTLAND_IMPORT_ENV }HYPRLAND_INSTANCE_SIGNATURE"
export SCOTTLAND_IMPORT_ENV
