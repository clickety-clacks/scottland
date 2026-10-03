#!/usr/bin/env bash
set -euo pipefail
repo=$(cd -- "$(dirname -- "$0")/.." && pwd)
mkdir -p "$repo/build"
c++ -std=c++17 -Wall -Wextra -Werror -O2 -I"$repo/core/plugin/src" \
  "$repo/tests/edge-style-test.cpp" -o "$repo/build/edge-style-test"
"$repo/build/edge-style-test"
