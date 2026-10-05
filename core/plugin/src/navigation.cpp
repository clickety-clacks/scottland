#include "navigation.hpp"
#include <cmath>

namespace scottland::windowing
{
std::optional<uint64_t> neighbor(rectangle origin, nav_direction d, const std::vector<nav_candidate>& candidates)
{
    bool horizontal = d == nav_direction::left || d == nav_direction::right;
    double sign = d == nav_direction::right || d == nav_direction::down ? 1 : -1;
    auto center = [] (rectangle r) { return point{r.x + r.width / 2, r.y + r.height / 2}; };
    // Within the perpendicular extent: a row for left/right, a column for up/down.
    auto within = [=] (double v, rectangle r) {
        return horizontal ? v >= r.y && v <= r.y + r.height : v >= r.x && v <= r.x + r.width; };
    auto o = center(origin);
    std::optional<uint64_t> best;
    bool best_aligned = false;
    double best_distance = 0;
    for (const auto& c : candidates)
    {
        auto p = center(c.drawn);
        double along = sign * (horizontal ? p.x - o.x : p.y - o.y);
        double across = horizontal ? p.y - o.y : p.x - o.x;
        if (!(along > 0) || std::abs(across) > along) continue;
        bool aligned = within(horizontal ? p.y : p.x, origin) || within(horizontal ? o.y : o.x, c.drawn);
        double distance = std::hypot(along, across);
        // Strict comparisons keep the earlier (further in front) candidate on exact ties.
        if (!best || (aligned && !best_aligned) || (aligned == best_aligned && distance < best_distance))
        { best = c.id; best_aligned = aligned; best_distance = distance; }
    }
    return best;
}
}
