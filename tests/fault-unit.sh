#!/bin/bash
# Allocation-failure injection for the handover owners and the worker, without a compositor.
# Not under ThreadSanitizer (the test replaces operator new). Run on a test host.
set -euo pipefail
cd "$(dirname -- "$0")/.."
mkdir -p build
work=$(mktemp -d "$PWD/build/scottland-fault-unit.XXXXXX")
trap 'rm -rf "$work"' EXIT
src=(tests/fault-unit.cpp core/plugin/src/pure/worker.cpp core/plugin/src/pure/shrink.cpp core/plugin/src/goo-model.cpp)
c++ -std=c++17 -Wall -Wextra -Icore/plugin/src "${src[@]}" -pthread -O1 -g -o "$work/fault"
"$work/fault"
