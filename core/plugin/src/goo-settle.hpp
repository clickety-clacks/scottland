#pragma once
// When the goo's simulation may fall asleep, and which energy readings may decide it (GO10,
// main-loop Phase 3). Pure, so the exact boundaries are unit-tested with supplied times
// (tests/goo-settle-unit.cpp).
#include <cstdint>

namespace scottland::goo
{
// Where the settle check can't be read without waiting (GLES 2, a readback failure, the test
// switch), the goo sleeps on time alone: 6 s after the last change (Mike, 2026-10-03).
constexpr double timed_sleep_after_s = 6;
// Otherwise it sleeps once the energy reads settled, at least 3 s after the last change and one
// full reading interval (30 steps) after a wake, so the reading describes the response to it.
constexpr double settle_after_s = 3;
constexpr uint64_t settle_steps = 30;

inline bool may_sleep(bool timed, double since_change, float energy, float sleep_energy, uint64_t steps_since_wake)
{
    if (timed) return since_change > timed_sleep_after_s;
    return since_change > settle_after_s && energy <= sleep_energy && steps_since_wake >= settle_steps;
}

/** What a reading was issued against, or the renderer's state now. */
struct reading_tag_t
{
    uint64_t step = 0, invalidation = 0, generation = 0;
    int w = 0, h = 0;
};
/** A collected reading applies only if nothing changed since it was issued (no impulse, source,
 *  settings change or wake; the same generation and size) and it is newer than the last applied
 *  one, so a reading completing late never overrides a newer one (ML7). */
inline bool reading_applies(const reading_tag_t& reading, const reading_tag_t& now, uint64_t last_applied_step)
{
    return reading.generation == now.generation && reading.invalidation == now.invalidation &&
           reading.w == now.w && reading.h == now.h && reading.step > last_applied_step;
}
} // namespace scottland::goo
