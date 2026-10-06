#!/usr/bin/env bash
set -euo pipefail
repo=$(cd -- "$(dirname -- "$0")/.." && pwd)
work=$(mktemp -d "$repo/build/pickup-policy.XXXXXX")
trap 'rm -rf "$work"' EXIT
c++ -std=c++17 -Wall -Wextra -Werror -I"$repo/core/plugin/src" "$repo/tests/goo-pickup-policy-test.cpp" -o "$work/test"
"$work/test"
