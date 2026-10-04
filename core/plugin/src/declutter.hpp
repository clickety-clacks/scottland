#pragma once
#include "placement.hpp"
#include <limits>
namespace scottland::windowing
{
// One avoidance refresh may spend at most this much synchronous search time on the compositor
// thread. Unfinished per-window work resumes on the next bounded tick; a true-layout change
// discards it and starts a fresh solve.
inline constexpr int avoidance_solve_budget_us = 2000;
inline constexpr unsigned avoidance_attempts_per_window = 2;
// A necessary change of way advances from the displayed incumbent in short steps.
inline constexpr double avoidance_replacement_step_limit = 64.0;
// Avoid a one-pixel layout change toggling a retained way on and off at its fit edge.
inline constexpr double avoidance_way_release_margin = 4.0;

// If P1's zone bound leaves no legal visible patch, this is the single policy
// seam for Mike's pending P1/P12 decision. False preserves the user's side/zone;
// true permits the nearest minimum-patch placement beyond that bound. Mike's
// choice is pending, so the shipped value remains P1-preserving.
inline constexpr bool allow_minimum_patch_zone_overshoot = false;

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
    point branch_base_offset = {}; // frozen first leg of a two-segment replacement way
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
    point branch_base_offset = {};
};
struct exposure_progress
{
    std::vector<exposure_result> results;
    std::vector<bool> has_result;
    std::vector<bool> complete;
    std::vector<unsigned> attempts;
    size_t next_window = 0;
    size_t fallback_count = 0;
    bool way_recheck_pending = false;
    bool way_recheck_done = false;
    unsigned way_recheck_attempts = 0;
    unsigned way_recheck_adoptions = 0;
    unsigned way_recheck_rejections = 0;
};
struct exposure_limits
{
    size_t inspection_budget = std::numeric_limits<size_t>::max();
    size_t *inspection_count = nullptr;
    bool allow_size_upgrades = true;
    bool allow_minimum_patch_zone_overshoot = scottland::windowing::allow_minimum_patch_zone_overshoot;
    bool reconsider_ways_when_idle = false;
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
// Resume the full stack solver in bounded slices. Checked per-window results and
// their ways persist between slices, while foreground windows remain movable if
// a rear window needs a patch. The caller resets state on true-layout changes.
bool expose_window_hints_progressively(const std::vector<exposure_window>& windows,
    rectangle screen, const std::vector<rectangle>& fixed, exposure_progress& progress,
    std::chrono::steady_clock::time_point deadline, bool *deadline_hit = nullptr,
    exposure_profile *profile = nullptr, exposure_limits limits = {});
}
