#!/bin/sh
# Test-only replacement for 01-record-environment. The session driver owns its runtime paths.
exec python3 "${SCOTTLAND_HEADLESS_SESSION_HELPER:?}" record-env
