#pragma once
#include "placement.hpp"
#include <limits>
namespace scottland::windowing
{
// One avoidance refresh may spend at most this much synchronous search time on the compositor
// thread. A later layout change starts a fresh solve; unfinished search state is never resumed.
inline constexpr int avoidance_solve_budget_us = 2000;

struct hint_constraint
{
    bool vertical_only = false;
    double half_height = 0; // keep a widget's displayed frame on screen as well as its badge
    bool anchored = false;
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
    double wanted = 72, minimum = 48;
    std::vector<rectangle> fixed_foreground = {}; // widgets above this window, after rail declutter
    bool anchored = false; // focused/selected surface stays at its real geometry
    point incumbent_offset = {}; // current displayed scene-only displacement; frame remains true geometry
    point target_offset = {}; // prior destination, used only to hold a checked target on timeout
    point prior_label_offset = {}; // last visible label point relative to the target window center
    double prior_clearance = 0; // checked clearance from the preceding solve, for deadline fallback
    bool center_zone = false;
    double center_zone_half_width = 0;
    int branch_owner = -1; // index of the hint whose exposure this window's retained way serves
    int branch_axis = 0; // 1 = horizontal, 2 = vertical
    int branch_sign = 0;
};
struct exposure_result
{
    point offset;
    label_spot spot;
    double diameter = 48;
    int branch_owner = -1;
    int branch_axis = 0;
    int branch_sign = 0;
    double retained_clearance = -1; // old target's badge point after this layout change
};
struct exposure_limits
{
    size_t inspection_budget = std::numeric_limits<size_t>::max();
    size_t *inspection_count = nullptr;
    bool allow_size_upgrades = true;
};
struct exposure_profile
{
    double initialization_ms = 0;
    double placement_ms = 0;
    double finalization_ms = 0;
    size_t work_count = 0;
    size_t label_work_count = 0;
    size_t movement_work_count = 0;
    size_t movement_searches = 0;
    size_t truncated_searches = 0;
    size_t last_search_window = 0;
};
// In front-to-back order, expose room for each badge with the least visual travel.
// Fixed rectangles are widget frames already placed in their rail. A foreground
// window moves only when that is the least bounded way to reveal a covered one.
std::vector<exposure_result> expose_window_hints(const std::vector<exposure_window>& windows,
    rectangle screen, const std::vector<rectangle>& fixed = {},
    std::chrono::steady_clock::time_point deadline = std::chrono::steady_clock::time_point::max(),
    bool *deadline_hit = nullptr, exposure_profile *profile = nullptr,
    exposure_limits limits = {});
}
