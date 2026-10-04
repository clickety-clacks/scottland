#pragma once
#include "placement.hpp"
#include <algorithm>
#include <array>
#include <cstdint>
namespace scottland::windowing
{
enum class zone { center, left_periphery, right_periphery, left_rail, right_rail };
// The desktop model window_state_t owns this record. Positions are output-relative
// normalized centers, never compositor pointers. last_side records the most recently visited side.
// pins holds the Shift scale pin (L31) the window had at each remembered spot, empty for zone
// scaling (WP1). It is the window's own scale factor, so it means the same logical size on any
// screen. Only the peripheries keep one: the center is always 100% (tenet 4, WP5) and widgets never
// scale (WG4).
struct window_memory
{
    std::array<std::optional<point>, 5> positions;
    std::array<std::optional<double>, 5> pins;
    int last_side = 0; // -1 left, +1 right, 0 neither
    unsigned hint_slot = 0;
};
inline bool zone_keeps_pin(zone z) { return z == zone::left_periphery || z == zone::right_periphery; }
// A pin within the scales a window can have (place()'s 0.05 to 1); none for zero, negative or NaN.
inline std::optional<double> valid_pin(std::optional<double> pin)
{
    if (!pin || !(*pin > 0)) return std::nullopt;
    return std::clamp(*pin, 0.05, 1.0);
}
// Records a spot in zone `z`, with the pin the window has there now (or its absence).
inline void remember_spot(window_memory& memory, zone z, point spot, std::optional<double> pin)
{
    memory.positions[size_t(z)] = spot;
    memory.pins[size_t(z)] = zone_keeps_pin(z) ? valid_pin(pin) : std::nullopt;
    if (z != zone::center) memory.last_side = (z == zone::left_periphery || z == zone::left_rail) ? -1 : 1;
}
// The pin to restore when the window returns to zone `z`'s remembered spot: never another zone's.
inline std::optional<double> remembered_pin(const window_memory& memory, zone z)
{
    if (!zone_keeps_pin(z) || !memory.positions[size_t(z)]) return std::nullopt;
    return memory.pins[size_t(z)];
}
}
