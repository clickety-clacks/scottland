#!/bin/bash
# Pure tests of the window-avoidance peek engine (core/plugin/src/peek.cpp): no compositor.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p build
build_dir=$(mktemp -d "$PWD/build/scottland-peek-unit.XXXXXX")
trap 'rm -rf "$build_dir"' EXIT
export TMPDIR=$build_dir
c++ -std=c++17 -Wall -Wextra -Werror -O2 -Icore/plugin/src tests/peek-unit.cpp \
  core/plugin/src/placement.cpp core/plugin/src/peek.cpp -o "$build_dir/test"
"$build_dir/test"
