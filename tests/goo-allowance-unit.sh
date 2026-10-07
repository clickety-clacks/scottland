#!/bin/bash
# The goo's collection allowance on a real wl_event_loop, under AddressSanitizer and
# UndefinedBehaviorSanitizer, without a compositor or GPU. Run on a test host.
set -euo pipefail
cd "$(dirname -- "$0")/.."
mkdir -p build
work=$(mktemp -d "$PWD/build/scottland-goo-allowance-unit.XXXXXX")
trap 'rm -rf "$work"' EXIT
c++ -std=c++17 -Wall -Wextra -O1 -g -fsanitize=address,undefined -fno-sanitize-recover=all -rdynamic \
    -Icore/plugin/src tests/goo-allowance-unit.cpp core/plugin/src/loop.cpp \
    $(pkg-config --cflags --libs wayland-server) -pthread -ldl -o "$work/allowance"
"$work/allowance"
