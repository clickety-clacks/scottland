#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p build
build_dir=$(mktemp -d "$PWD/build/scottland-pairing-unit.XXXXXX")
trap 'rm -rf "$build_dir"' EXIT
export TMPDIR=$build_dir
c++ -std=c++17 -Wall -Wextra -Werror -O2 -Icore/plugin/src tests/pairing-unit.cpp \
  core/plugin/src/pairing.cpp core/plugin/src/alt-mode.cpp core/plugin/src/placement.cpp -o "$build_dir/test"
"$build_dir/test"
