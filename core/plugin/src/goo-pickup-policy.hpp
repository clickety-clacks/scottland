#pragma once
#include <algorithm>

namespace scottland::goo
{
// Content pickup scheduling, independent of compositor timers. All times are monotonic seconds.
struct pickup_policy_t
{
    static constexpr double first_gap = 20, max_gap = 300, check_gap = .5;
    double next = 0, gap = first_gap, last_check = 0, last_activity = -1e9;
    bool pending = false;

    // A quiet interval resets the backoff. Return true when a deferred change becomes ready.
    bool activity(double t)
    {
        bool ready = false;
        if (t - last_activity > first_gap && (gap > first_gap || t < next))
        {
            gap = first_gap;
            next = std::min(next, t);
            ready = pending;
            pending = false;
        }
        last_activity = t;
        return ready;
    }
    bool change(double t)
    {
        pending = t < next;
        return !pending;
    }
    bool expire(double t)
    {
        if (!pending || t < next) return false;
        pending = false;
        gap = std::min(gap * 2, max_gap);
        return true;
    }
    void start(double t) { next = t + gap; }
    double check_wait(double t) const { return std::max(0., check_gap - (t - last_check)); }
};
} // namespace scottland::goo
