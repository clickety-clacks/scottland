#pragma once
#include "placement.hpp"
namespace scottland::windowing
{
// Deterministic collision graph relaxation: overlapping nodes repel, springs preserve locations.
// Bounds keep hint centers readable on screen; the caller applies offsets as visual transforms.
std::vector<point> declutter(const std::vector<point>& anchors, rectangle bounds,
    double separation = 86.0);
}
