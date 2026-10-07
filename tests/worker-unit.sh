#!/bin/bash
# The worker and the shrink job without a compositor: once under ThreadSanitizer (no
# suppressions), once optimized. Run on a test host.
#   tests/worker-unit.sh [--calibrate]
# --calibrate adds the work-unit calibration (a benchmark: the cost of a density term and of the
# slowest single operation on this machine), which the default run leaves out.
set -euo pipefail
cd "$(dirname -- "$0")/.."
calibrate=()
for arg in "$@"; do
    case "$arg" in
        --calibrate) calibrate=(--calibrate) ;;
        *) echo "usage: $0 [--calibrate]" >&2; exit 2 ;;
    esac
done
mkdir -p build
work=$(mktemp -d "$PWD/build/scottland-worker-unit.XXXXXX")
trap 'rm -rf "$work"' EXIT
src=(tests/worker-unit.cpp core/plugin/src/pure/worker.cpp core/plugin/src/pure/shrink.cpp core/plugin/src/goo-model.cpp)
c++ -std=c++17 -Wall -Wextra -Icore/plugin/src "${src[@]}" -pthread -O1 -g -fsanitize=thread -o "$work/tsan"
c++ -std=c++17 -Wall -Wextra -Icore/plugin/src "${src[@]}" -pthread -O2 -o "$work/timing"
echo "== ThreadSanitizer"
TSAN_OPTIONS=halt_on_error=1 "$work/tsan"
echo "== optimized"
"$work/timing" --timing "${calibrate[@]}"
