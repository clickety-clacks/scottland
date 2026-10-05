#pragma once

#include <cstdint>

namespace scottland::drag_chain
{
// WG14/L27: picking up again what was just let go continues the same move, so Esc still goes
// back to where (and as what) the move began. It exists for lifting the fingers to reset them
// on a touchpad, or running out of room.
static constexpr uint32_t SAME_FORM_MS = 2500;

// A drop that changed the form (a window let go on a rail became its widget, or a widget let go
// off its rail became its window) is a placement the user saw happen (P14). Only an immediate
// re-grab, a finger reset, continues the move through it; picking the new form up later is a new
// move, and Esc returns it to where that drop put it. Measured finger resets are 0.25-0.4 s;
// deliberately grabbing the new widget took 1.7-1.9 s (Mike's report, 2026-10-05).
static constexpr uint32_t FORM_CHANGE_MS = 1000;

/** How long after a drop a re-grab still continues the move. */
inline constexpr uint32_t window_ms(bool form_changed)
{
    return form_changed ? FORM_CHANGE_MS : SAME_FORM_MS;
}

/** Does grabbing `grabbed` `since_drop_ms` after the last drop continue that move? `became` is
 *  what stands for the dropped item now; `form_changed` whether the drop changed its form. */
inline constexpr bool continues(uint64_t grabbed, uint64_t became, uint32_t since_drop_ms, bool form_changed)
{
    return became != 0 && grabbed == became && since_drop_ms < window_ms(form_changed);
}
}
