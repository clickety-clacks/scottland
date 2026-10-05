#!/bin/bash
# The diagnostic ring and watchdog without a compositor: once under ThreadSanitizer (producer,
# watchdog and an in-process reader, no suppressions), once optimized with the timing checks.
# Run on a test host.
set -euo pipefail
cd "$(dirname -- "$0")/.."
mkdir -p build
work=$(mktemp -d "$PWD/build/scottland-loop-unit.XXXXXX")
trap 'rm -rf "$work"' EXIT
flags=(-std=c++17 -Wall -Wextra -Icore/plugin/src tests/loop-unit.cpp core/plugin/src/loop.cpp
       $(pkg-config --cflags --libs wayland-server) -pthread)
c++ "${flags[@]}" -O1 -g -fsanitize=thread -o "$work/tsan"
c++ "${flags[@]}" -O2 -o "$work/timing"
c++ -std=c++17 -Wall -Wextra -Icore/plugin/src core/plugin/src/loop-read.cpp -O2 -o "$work/loop-read"
mkdir -p "$work/a" "$work/b"
echo "== ThreadSanitizer"
TSAN_OPTIONS=halt_on_error=1 "$work/tsan" --race "$work/a" "$work/loop-read"
echo "== timing"
"$work/timing" --timing "$work/b" "$work/loop-read"
