#!/usr/bin/env bash
# Test-machine only. Five fractional transport probes on each supported shader dialect.
set -euo pipefail
repo=$(cd -- "$(dirname -- "$0")/.." && pwd)
work=$(mktemp -d "$repo/build/dye-filter.XXXXXX")
trap 'rm -rf "$work"' EXIT
c++ -std=c++17 -Wall -Wextra -Werror -I"$repo/core/plugin/src" \
  "$repo/tests/goo-dye-filter-test.cpp" $(pkg-config --cflags --libs egl glesv2) -o "$work/test"
EGL_PLATFORM=surfaceless "$work/test" 2
EGL_PLATFORM=surfaceless "$work/test" 3
