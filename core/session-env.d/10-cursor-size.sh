#!/bin/sh
# New processes inherit the cursor size selected from the desktop text scale and active theme.
hooks=${SCOTTLAND_HOOKS:-/usr/lib/scottland}
# The package follows its active build; development and headless trees link tools in libexec.
if [ -d "$hooks/tools/base" ]; then
  display="$hooks/tools/current/bin/scottland-display"
else
  display="$hooks/libexec/scottland-display"
fi
size=$("$display" color-scheme cursor-size 2>/dev/null) || return
case $size in
  ''|*[!0-9]*) return ;;
esac
XCURSOR_SIZE=$size
export XCURSOR_SIZE
