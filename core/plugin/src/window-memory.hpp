#pragma once
#include "placement.hpp"
#include <array>
#include <cstdint>
namespace scottland::windowing
{
enum class zone { center, left_periphery, right_periphery, left_rail, right_rail };
// The desktop model window_state_t owns this record. Positions are output-relative
// normalized centers, never compositor pointers. last_side records the most recently visited side.
struct window_memory
{
    std::array<std::optional<point>, 5> positions;
    int last_side = 0; // -1 left, +1 right, 0 neither
    unsigned hint_slot = 0;
};
}
