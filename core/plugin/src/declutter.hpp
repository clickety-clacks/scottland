#pragma once
#include "placement.hpp"
namespace scottland::windowing
{
struct hint_constraint
{
    bool vertical_only = false;
    double half_height = 0; // keep a widget's displayed frame on screen as well as its badge
    bool immovable = false; // reserve already-placed interior hints while packing edge fallbacks
};
// Deterministic collision graph relaxation: overlapping nodes repel, springs preserve locations.
// Bounds keep hint centers readable on screen; the caller applies offsets as visual transforms.
std::vector<point> declutter(const std::vector<point>& anchors, rectangle bounds,
    double separation = 86.0, const std::vector<double>& diameters = {},
    const std::vector<hint_constraint>& constraints = {});
// With diameters, separation is the padding between badges and bounds is the whole screen.
// Each center is constrained by its own badge radius; legacy callers retain fixed separation.

// Bounded coordinate descent on actual on-screen rectangle overlap. Returns visual offsets;
// fixed obstacles include the already-decluttered widgets and their exterior circles.
std::vector<point> declutter_windows(const std::vector<rectangle>& windows, rectangle bounds,
    const std::vector<rectangle>& fixed = {});
}
