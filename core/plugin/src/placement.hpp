#pragma once
#include <chrono>
#include <cstddef>
#include <limits>
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
// The coarse-to-fine search returns its best checked spot at deadline. A sufficient clearance
// stops the search early when the caller only needs a requested badge diameter to fit.
label_spot visible_label(rectangle window, rectangle screen,
    const std::vector<rectangle>& foreground, double precision = 0.5,
    std::chrono::steady_clock::time_point deadline = std::chrono::steady_clock::time_point::max(),
    double sufficient_clearance = -1,
    size_t inspection_budget = std::numeric_limits<size_t>::max(),
    size_t *inspection_count = nullptr);
// Signed radius available for a circle at this exact point, including occlusion.
double visible_clearance(point center, rectangle window, rectangle screen,
    const std::vector<rectangle>& foreground);
// Share of the window's on-screen area left uncovered by the union of foreground rectangles
// (exact sweep; O(n² log n) for n foreground rectangles). A window
// wholly off screen counts as fully visible: an outline there would show nothing.
double visible_fraction(rectangle window, rectangle screen, const std::vector<rectangle>& foreground);
// As above, but returns empty when an exposure search deadline expires mid-check.
std::optional<double> visible_clearance_before(point center, rectangle window, rectangle screen,
    const std::vector<rectangle>& foreground,
    std::chrono::steady_clock::time_point deadline);
}
