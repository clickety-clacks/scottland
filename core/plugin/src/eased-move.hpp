#pragma once

#include <algorithm>
#include <cmath>
#include <utility>

namespace scottland::motion
{
// P11: automatic movement (window avoidance, rail make-room) never goes faster than this.
static constexpr double AUTOMATIC_MAX_SPEED = 1000.0;  // px/s

struct vec2_t
{
    double x = 0, y = 0;
};

// A move that eases in and out. It starts at the velocity the thing already had, so a
// retarget in mid-move doesn't kick, and ends at rest on the target without overshooting.
// It is a cubic Hermite from (from, v0) to (to, 0); from rest it is smoothstep.
struct eased_move_t
{
    vec2_t from, to, v0;     // v0 in px/ms
    double duration = 0;     // ms

    vec2_t position(double elapsed) const
    {
        if (duration <= 0 || elapsed >= duration) return to;
        const double s = std::max(elapsed, 0.0) / duration;
        const double h10 = s * (1 - s) * (1 - s), h01 = s * s * (3 - 2 * s);
        return {from.x + h01 * (to.x - from.x) + h10 * duration * v0.x,
            from.y + h01 * (to.y - from.y) + h10 * duration * v0.y};
    }

    vec2_t velocity(double elapsed) const
    {
        if (duration <= 0 || elapsed >= duration) return {};
        const double s = std::max(elapsed, 0.0) / duration;
        const double d01 = 6 * s * (1 - s) / duration, d10 = (1 - s) * (1 - 3 * s);
        return {d01 * (to.x - from.x) + d10 * v0.x, d01 * (to.y - from.y) + d10 * v0.y};
    }

    double peak_speed() const  // px/ms, sampled
    {
        double peak = 0;
        for (int k = 0; k <= 32; ++k)
        {
            auto v = velocity(duration * k / 32.0);
            peak = std::max(peak, std::hypot(v.x, v.y));
        }
        return peak;
    }
};

// Plans a move from `from` to `to` that starts at velocity `v0` (px/ms). Its duration grows
// with distance from `min_ms` to `max_ms`, and further when that would exceed `max_speed`
// (px/s). Velocity against the new direction is dropped: the move turns there rather than
// carrying on the wrong way.
inline eased_move_t plan_eased_move(vec2_t from, vec2_t to, vec2_t v0, double min_ms, double max_ms,
    double ms_per_px, double max_speed = AUTOMATIC_MAX_SPEED)
{
    eased_move_t move;
    move.from = from;
    move.to = to;
    const double dx = to.x - from.x, dy = to.y - from.y;
    const double cap = max_speed / 1000.0;
    auto keep = [] (double v, double d) { return v * d > 0 ? v : 0.0; };
    move.v0 = {keep(v0.x, dx), keep(v0.y, dy)};
    const double speed = std::hypot(move.v0.x, move.v0.y);
    if (speed > cap) { move.v0.x *= cap / speed; move.v0.y *= cap / speed; }

    const double distance = std::hypot(dx, dy);
    move.duration = std::clamp(min_ms + distance * ms_per_px, min_ms, max_ms);
    // From rest the peak speed is 1.5·distance/duration; a head start may need a little more.
    move.duration = std::max(move.duration, 1.5 * distance / cap);
    for (int k = 0; k < 64 && move.peak_speed() > cap * 1.0001; ++k) move.duration *= 1.03;

    // Arriving faster than 3·distance/duration would carry it past the target. Shorten the
    // move just enough: it then decelerates steadily from its current speed to rest there.
    for (auto [v, d] : {std::pair{move.v0.x, dx}, std::pair{move.v0.y, dy}})
        if (v != 0) move.duration = std::min(move.duration, 3 * d / v);
    move.duration = std::max(move.duration, 1.0);
    return move;
}
}
