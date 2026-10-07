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

// The neighbor of `origin` in direction `d`, from candidates listed front to back: of those whose
// center lies on that side of the origin's center (right of it for Right, and so on), the nearest
// center; equal distances go to the one further in front. A center exactly on the origin's is on
// no side, so those are ordered by id instead: Right and Down reach higher ids, Left and Up lower.
// None: nothing happens (no wrap-around).
std::optional<uint64_t> neighbor(nav_candidate origin, nav_direction d, const std::vector<nav_candidate>& candidates);
}
