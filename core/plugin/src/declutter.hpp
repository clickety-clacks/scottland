#pragma once
#include "placement.hpp"
namespace scottland::windowing
{
struct hint_constraint
{
    bool vertical_only = false;
    double half_height = 0; // keep a widget's displayed frame on screen as well as its badge
};
// Deterministic collision graph relaxation: overlapping nodes repel, springs preserve locations.
// Bounds keep hint centers readable on screen; the caller applies offsets as visual transforms.
std::vector<point> declutter(const std::vector<point>& anchors, rectangle bounds,
    double separation = 86.0, const std::vector<double>& diameters = {},
    const std::vector<hint_constraint>& constraints = {});
// With diameters, separation is the padding between badges and bounds is the whole screen.
// Each center is constrained by its own badge radius; legacy callers retain fixed separation.

struct exposure_window
{
    rectangle frame;
    double wanted = 72, minimum = 32;
    std::vector<rectangle> fixed_foreground = {}; // widgets above this window, after rail declutter
};
struct exposure_result { point offset; label_spot spot; double diameter = 32; };
// In front-to-back order, expose room for each badge with the least visual travel.
// Fixed rectangles are widget frames already placed in their rail. A foreground
// window moves only when that is the least bounded way to reveal a covered one.
std::vector<exposure_result> expose_window_hints(const std::vector<exposure_window>& windows,
    rectangle screen, const std::vector<rectangle>& fixed = {});
}
