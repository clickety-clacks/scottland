#!/bin/bash
# The worker and the shrink job without a compositor: once under ThreadSanitizer (no
# suppressions), once optimized with the calibration. Run on a test host.
set -euo pipefail
cd "$(dirname -- "$0")/.."
mkdir -p build
work=$(mktemp -d "$PWD/build/scottland-worker-unit.XXXXXX")
trap 'rm -rf "$work"' EXIT
src=(tests/worker-unit.cpp core/plugin/src/pure/worker.cpp core/plugin/src/pure/shrink.cpp core/plugin/src/goo-model.cpp)
c++ -std=c++17 -Wall -Wextra -Icore/plugin/src "${src[@]}" -pthread -O1 -g -fsanitize=thread -o "$work/tsan"
c++ -std=c++17 -Wall -Wextra -Icore/plugin/src "${src[@]}" -pthread -O2 -o "$work/timing"
echo "== ThreadSanitizer"
TSAN_OPTIONS=halt_on_error=1 "$work/tsan"
echo "== optimized"
"$work/timing" --timing
