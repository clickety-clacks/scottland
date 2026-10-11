# Only Scottland-session descendants receive the text-size command integration.
# Do not import PATH into the user manager or change the vendor's bin directory.
if [ "${XDG_SESSION_DESKTOP:-}" = scottland ] && [ -x "$SCOTTLAND_HOOKS/omarchy-bin/omarchy" ]; then
  PATH="$SCOTTLAND_HOOKS/omarchy-bin:$PATH"
  export PATH
fi
