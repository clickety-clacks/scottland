#!/bin/bash
set -euo pipefail
cd "$(dirname -- "$0")/.."
mkdir -p build
build_dir=$(mktemp -d "$PWD/build/scottland-rail-make-room-unit.XXXXXX")
trap 'rm -rf "$build_dir"' EXIT
c++ -std=c++17 -Wall -Wextra -Werror -O2 -Icore/plugin/src \
  tests/rail-make-room-unit.cpp -o "$build_dir/test"
"$build_dir/test"
