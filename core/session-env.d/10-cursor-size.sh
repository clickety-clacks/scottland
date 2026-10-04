#!/bin/sh
# New processes inherit the cursor size selected from the desktop text scale and active theme.
size=$("${SCOTTLAND_HOOKS:-/usr/lib/scottland}/libexec/scottland-color-scheme" cursor-size 2>/dev/null) || return
case $size in
  ''|*[!0-9]*) return ;;
esac
XCURSOR_SIZE=$size
export XCURSOR_SIZE
