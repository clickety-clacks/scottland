#pragma once
#include "placement.hpp"
#include <cstdint>
#include <optional>
#include <vector>

namespace scottland::windowing
{
// WK40: Super+arrows move focus to the neighboring window or widget, judged by drawn rectangles
// (zone scale and any avoidance offset applied), in layout coordinates with y growing downward.
enum class nav_direction { left, right, up, down };
struct nav_candidate { uint64_t id; rectangle drawn; };

// The neighbor of `origin` in direction `d`, from candidates listed front to back. Only candidates
// whose center lies within the 45° cone around that direction count (none: nothing happens, no
// wrap-around). Aligned ones win: one center within the other's drawn row (left/right) or
// column (up/down). Then the nearer center, then the one further in front.
std::optional<uint64_t> neighbor(rectangle origin, nav_direction d, const std::vector<nav_candidate>& candidates);
}
