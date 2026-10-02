#pragma once
#include <algorithm>
#include <cmath>

namespace scottland
{
// An underdamped step through its first peak, then a C1 finite settling tail.
// No second lobe or endless timer: endpoints and their velocities are exact.
inline double widget_spring(double t, double amount)
{
    t = std::clamp(t, 0.0, 1.0);
    amount = std::clamp(amount, 0.0, 0.1);
    if (t == 0 || t == 1) return t;
    if (amount == 0) return t * t * (3 - 2 * t);
    constexpr double peak = 0.6, pi = 3.14159265358979323846;
    if (t <= peak)
    {
        double u = t / peak, decay = -std::log(amount);
        return 1 - std::exp(-decay * u) * (std::cos(pi * u) + decay / pi * std::sin(pi * u));
    }
    double u = (t - peak) / (1 - peak);
    return 1 + amount * (1 - u * u * (3 - 2 * u));
}

inline double widget_spring_size(double from, double to, double progress)
{
    // Very large app -> small card arrivals must retain a recognizable card.
    // Limit only the overshoot, without a flat clamp at the peak.
    double overshoot = std::max(0.0, progress - 1);
    double delta = to - from;
    double limit = delta < 0 ? std::min(1.0, to * 0.25 / std::max(1.0, -delta * 0.1)) : 1.0;
    return from + delta * (std::min(progress, 1.0) + overshoot * limit);
}
}
