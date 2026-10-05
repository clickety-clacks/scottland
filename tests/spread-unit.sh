#!/bin/bash
# Spread solver unit suite: fixtures, fuzzed invariants, determinism, slices, timing (no compositor).
#   tests/spread-unit.sh [--bench]
set -euo pipefail
cd "$(dirname -- "$0")/.."
mkdir -p build
build_dir=$(mktemp -d "$PWD/build/scottland-spread-unit.XXXXXX")
trap 'rm -rf "$build_dir"' EXIT
c++ -std=c++17 -Wall -Wextra -Werror -O2 -g -fno-omit-frame-pointer -Icore/plugin/src \
  tests/spread-unit.cpp core/plugin/src/spread.cpp -o "$build_dir/test"
"$build_dir/test" "$@"
