#!/usr/bin/env bash
# The goo's sleep decision and reading acceptance, on supplied times (no compositor).
set -euo pipefail
repo=$(cd -- "$(dirname -- "$0")/.." && pwd)
mkdir -p "$repo/build"
work=$(mktemp -d "$repo/build/goo-settle-unit.XXXXXX")
trap 'rm -rf "$work"' EXIT
c++ -std=c++17 -Wall -Wextra -Werror -I"$repo/core/plugin/src" "$repo/tests/goo-settle-unit.cpp" -o "$work/test"
"$work/test"
