#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
build_dir=$(mktemp -d "${TMPDIR:-/tmp}/scottland-inertia-unit.XXXXXX")
trap 'rm -rf "$build_dir"' EXIT
c++ -std=c++17 -Wall -Wextra -Werror -O2 -Icore/plugin/src tests/inertia-unit.cpp core/plugin/src/inertia.cpp -o "$build_dir/test"
"$build_dir/test"
