#include "navigation.hpp"
#include <cmath>

namespace scottland::windowing
{
std::optional<uint64_t> neighbor(nav_candidate origin, nav_direction d, const std::vector<nav_candidate>& candidates)
{
    bool horizontal = d == nav_direction::left || d == nav_direction::right;
    bool forward = d == nav_direction::right || d == nav_direction::down;
    auto center = [] (rectangle r) { return point{r.x + r.width / 2, r.y + r.height / 2}; };
    auto o = center(origin.drawn);
    std::optional<uint64_t> best;
    double best_distance = 0;
    for (const auto& c : candidates)
    {
        if (c.id == origin.id) continue;
        auto p = center(c.drawn);
        double along = (forward ? 1 : -1) * (horizontal ? p.x - o.x : p.y - o.y);
        bool coincident = p.x == o.x && p.y == o.y;
        if (coincident ? (c.id > origin.id) != forward : !(along > 0)) continue;
        double distance = std::hypot(p.x - o.x, p.y - o.y);
        // Walk coincident centers in id order, independent of their stacking order. Other
        // exact-distance ties keep the earlier (further in front) candidate.
        bool next_coincident = coincident && best && best_distance == 0 &&
            (forward ? c.id < *best : c.id > *best);
        if (!best || distance < best_distance || next_coincident)
        { best = c.id; best_distance = distance; }
    }
    return best;
}
}
