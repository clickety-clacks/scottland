#!/bin/bash
# WK40 neighbor choice (Super+arrows), pure unit checks.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p build
build_dir=$(mktemp -d "$PWD/build/scottland-navigation-unit.XXXXXX")
trap 'rm -rf "$build_dir"' EXIT
export TMPDIR=$build_dir
c++ -std=c++17 -Wall -Wextra -Werror -O2 -Icore/plugin/src tests/navigation-unit.cpp \
  core/plugin/src/navigation.cpp -o "$build_dir/test"
"$build_dir/test"
