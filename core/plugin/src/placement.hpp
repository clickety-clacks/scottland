#pragma once
#include <optional>
#include <vector>

namespace scottland::windowing
{
struct point { double x = 0, y = 0; };
struct rectangle { double x, y, width, height; };
// Rectangle fits inside region when possible; oversized dimensions stay full size and centered.
// A memory is authoritative, including when occupied or outside the current region.
point place_rectangle(double width, double height, rectangle region,
    const std::vector<rectangle>& obstacles, point preferred,
    std::optional<point> remembered = {}, double overlap_tolerance = 0.01);
double overlap(rectangle a, rectangle b);
// Largest contiguous vertical opening in a side region (union, not sum, of blocked intervals).
double largest_opening(rectangle region, const std::vector<rectangle>& obstacles);

struct label_spot { point center; double clearance = 0; };
// Pole of the visible region (rectangle minus the union of foreground rectangles).
// The priority search is accurate to precision logical pixels, including disconnected regions.
label_spot visible_label(rectangle window, rectangle screen,
    const std::vector<rectangle>& foreground, double precision = 0.5);
}
