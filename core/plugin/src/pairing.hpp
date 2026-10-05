#pragma once
#include "placement.hpp"
namespace scottland::windowing
{
// WK36 pairing: two windows side by side, vertically centered on the area's center line, the
// pair centered horizontally as a unit. Sizes are never changed and never scaled up. Concessions,
// cheapest first: the halo gap (P7), then the edge padding (WP7), then one shared scale factor
// just small enough for the pair to span the area edge to edge (tenet 4: scaling is the costly
// concession, granted only because pairing was explicitly asked for). Heights never force
// scaling: a window taller than the area stays centered on the center line, as WP5/WP7 keep
// oversized windows full size.
struct pair_size { double width = 0, height = 0; };
struct pair_layout
{
    double scale = 1;
    point left, right; // centers of the scaled footprints (scaling is around the center, L5)
    double gap = 0;    // between the two scaled footprints
    double margin = 0; // between each scaled footprint and its area edge (negative: overflows)
};
// Scaling never goes below this, the smallest supported window scale (WK29).
inline constexpr double pair_min_scale = 0.05;
pair_layout fit_pair(pair_size left, pair_size right, rectangle area, double gap, double padding);
}
