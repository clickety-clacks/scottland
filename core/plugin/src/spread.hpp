#pragma once
// Spread (docs/spread.md, "Spread and solo"): the pure solver for a solo. One window takes a
// fixed spot in the center zone; every other center-zone window (an arrival) moves into the
// periphery, at the real zone scale of wherever it lands; periphery windows (residents) move
// only when they must, on their own side, never to a larger scale. Standard library only: no
// Wayfire, wlroots or plugin state, so it runs in unit tests, in main-loop slices and later on
// the plugin's worker.
//
// The schedule is finite (a seed, a baseline, at most four arrangements, one spacing pass) and
// every unit operation is charged to one work counter before it runs. A charge may stop the
// solve (work cap, cancellation): the result is then the best complete, validated checkpoint.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace scottland::spread
{
inline constexpr double CONTACT = 1.0;          // clearance every arrangement is solved at
inline constexpr double HALO = 32.0 / 3.0;      // the spacing pass's goal (P7), frame.hpp HALO
inline constexpr double NOTICEABLE = 0.85;      // in band: scale >= NOTICEABLE * s_ref (P6)
inline constexpr double TOLERANCE = 1.0;        // overlap deeper than this is real intersection
inline constexpr double EDGE_MARGIN = 2.0;      // rounding-safe distance of a center from a zone edge
inline constexpr size_t MAX_MOVABLE = 128;      // arrivals plus residents
inline constexpr size_t MAX_OBSTACLES = 256;    // fixed rectangles plus every window
// About 12 ms of solving on the x86 test machine at -O2 (90 units per microsecond, tests/spread-unit.sh
// timing); final.md started at 150,000 with a prototype that counted coarser units.
inline constexpr uint64_t WORK_CAP = 1000000;

struct box
{
    double x0 = 0, y0 = 0, x1 = 0, y1 = 0;
    double width() const { return x1 - x0; }
    double height() const { return y1 - y0; }
    double cx() const { return (x0 + x1) / 2; }
    double cy() const { return (y0 + y1) / 2; }
};

enum class role_t : uint8_t
{
    arrival,   // center is in the center zone: leaves for the periphery
    resident,  // center is in the periphery: moves only when it must, outward, same side
    fixed,     // never moved by spread (a window on a rail, outside the request)
};

struct window_t
{
    uint64_t id = 0;
    role_t role = role_t::resident;
    double width = 0, height = 0;  // native size (integer pixels): the window is scaled, never resized
    double cx = 0, cy = 0;         // true center; never a hint offset or an animation sample
    double scale = 1;              // the scale shown now: its pin, else the zone's
    bool pinned = false;           // `scale` is a pin (L31): kept only while it stays at this x
    uint32_t recency = 0;          // 0 = most recently used
    int8_t side_memory = 0;        // -1 left, +1 right, 0 none (ties only)
};

struct snapshot_t
{
    double screen_width = 0, screen_height = 0;
    box workarea;                  // output-local; panels are outside it
    double padding = 0;            // WP7 screen-edge padding, kept whenever the window fits with it
    double center_half = 0;        // |x - W/2| <= center_half is the center zone
    double rail_width = 0;         // |x - W/2| >= W/2 - rail_width is a rail
    // An arrival's center must lie at least this far beyond the center zone's edge, where its
    // natural scale reads as scaled (WP4). 0: the periphery's own edge.
    double arrival_inset = 0;
    std::function<double(double)> scale; // natural scale for a center x (any curve, not assumed monotone)
    box solo;                      // the solo window's target footprint
    std::vector<box> fixed;        // docked widgets and anything else nothing may move
    std::vector<window_t> windows; // arrivals, residents and fixed windows (never the solo window)

    double contact = CONTACT, halo = HALO, noticeable = NOTICEABLE;
    uint64_t work_cap = WORK_CAP;
    unsigned probe_cap = 64;       // candidate x per placement
};

enum class status_t
{
    clear,              // nothing overlaps an arrival, a moved window or the solo target
    overlap_exhausted,  // the completed search found no clear layout (not a proof that none exists)
    overlap_budget,     // the work cap or a deadline ended the search with overlap left
    unchanged_exhausted,// no legal seed was found: nothing moves
    unchanged_budget,   // stopped before any checkpoint existed: nothing moves
    unavailable,        // a fact, not a search: no periphery, or more windows than the caps
};
const char *status_name(status_t status);

struct move_t
{
    uint64_t id = 0;
    double cx = 0, cy = 0;          // representable: center minus half the native size is an integer
    double scale = 1;               // shown at the destination
    std::optional<double> pin;      // kept pin (a same-x move or an exact restore), else none
    bool arrival = false;
};

// Lexicographic, from the final layout only (final.md, "Comparison order").
struct score_t
{
    bool legal = true;
    double solo_overlap = 0;        // normalized overlap with the solo target
    double overlap = 0;             // normalized overlap in pairs with at least one affected window
    int below_band = 0;             // arrivals below NOTICEABLE * s_ref
    int residents_moved = 0;
    double resident_outward = 0, resident_travel = 0, arrival_travel = 0;
};
// <0: a is better, >0: b is better, 0: equal within the tolerances.
int compare(const score_t& a, const score_t& b);

struct result_t
{
    status_t status = status_t::unchanged_budget;
    std::vector<move_t> moves;      // only windows whose position, scale or pin changes
    std::vector<std::pair<uint64_t, uint64_t>> overlaps; // affected overlapping pairs (0: the solo)
    score_t score;
    std::string checkpoint;         // which checkpoint was delivered ("" if none)
    std::string reason;             // why unchanged/unavailable
    uint64_t work = 0;              // units charged
    unsigned arrangements = 0;      // completed arrangements
    double spacing = 0;             // common clearance the spacing pass reached (0: none applied)
    bool complete = false;          // the whole schedule ran
};

// The work counter. Every unit operation charges before it runs. `yield`, when set, is called at
// charges (every `yield_every` units): it may suspend the solve (main-loop slices) or throw
// `stopped` to cancel it. Reaching the cap throws `stopped` too.
struct stopped {};
class work_t
{
  public:
    uint64_t cap = WORK_CAP, used = 0;
    void (*yield)(void*) = nullptr;
    void *context = nullptr;
    unsigned yield_every = 32;

    void charge(uint64_t units = 1)
    {
        used += units;
        if (used > cap) { exhausted = true; throw stopped{}; }
        since += units;
        if (yield && since >= yield_every) { since = 0; yield(context); }
    }
    bool exhausted = false;

  private:
    unsigned since = 0;
};

// Run the whole schedule; returns the best checkpoint. `publish`, when set, is told about every
// checkpoint that became the best so far (a slice driver delivers from it at a deadline).
result_t solve(const snapshot_t& snapshot, work_t& work,
    const std::function<void(const result_t&)>& publish = {});
result_t solve(const snapshot_t& snapshot);

// Helpers the plugin and the tests share.
inline bool real_overlap(const box& a, const box& b, double tolerance = TOLERANCE)
{
    double ox = std::min(a.x1, b.x1) - std::max(a.x0, b.x0);
    double oy = std::min(a.y1, b.y1) - std::max(a.y0, b.y0);
    return ox > tolerance && oy > tolerance;
}
double normalized_overlap(const box& a, const box& b);
// Center of a native extent of `size` nearest `c` whose start is an integer pixel.
double representable(double c, double size);
}
