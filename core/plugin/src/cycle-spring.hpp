#pragma once
#include <algorithm>
#include <cmath>

namespace scottland::windowing
{
// Remaining distance of a unit step. The underdamped spring reaches its first
// peak at 60% of the duration; a C1 cubic tail brings it exactly to rest without
// a second oscillation. `amount` is the peak as a fraction of the original move.
inline double cycle_spring_remaining(double progress, double amount)
{
    if (progress <= 0) return 1;
    if (progress >= 1) return 0;
    amount = std::clamp(amount, 0.00001, 0.1);
    if (progress >= 0.6)
    {
        double t = (progress - 0.6) / 0.4;
        return -amount * (1 - t * t * (3 - 2 * t));
    }
    constexpr double pi = 3.14159265358979323846;
    double phase = pi * progress / 0.6;
    double damping = -std::log(amount) / pi;
    return std::exp(-damping * phase) * (std::cos(phase) + damping * std::sin(phase));
}
}
