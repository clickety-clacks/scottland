#pragma once
// Window avoidance: the peeking strip (P12, P13; docs/windowing-keys.md WK13, WK31).
// One engine, two tests. Every window behind others asks one question: what is the nearest
// offset at which it shows a free rectangle of a given size? Always on, the size is a strip
// (peek_strip_depth x peek_strip_length, x text scale); in Window mode it is first the room for
// the window's full hint, then for the minimum hint, then the strip. Only the covered window
// moves; front windows never move for rear ones, so a pass runs front to back and each window's
// result is final as soon as it is computed.
#include "placement.hpp"
#include <chrono>
#include <cstdint>
#include <optional>
#include <vector>

namespace scottland::windowing
{
// Decision 1 (Mike, 2026-10-04): a strip at least 24 pt deep and about 100 pt long, in logical
// points, times the desktop text scale; not shrunk by a window's zone scale.
inline constexpr double peek_strip_depth = 24, peek_strip_length = 100;
// Calm rules (P11), in logical pixels; they do not scale with text size.
inline constexpr double peek_reach = 64;        // R: most target change per solve while live
inline constexpr double peek_keep_radius = 16;  // r: the current way continued
inline constexpr double peek_switch_margin = 6; // m: another way must be this much better
// Decision 8: a window prefers to peek toward its position relative to the window covering it.
// Soft: a move in any other direction counts this many times its length.
inline constexpr double peek_direction_weight = 2;
// One slice of a pass spends at most this many work units (one obstacle range, one candidate
// coordinate or one candidate cell). Calibrated 2026-10-04 with tests/peek-unit.sh: the x86 test machine
// (loaded) 117,600 units/ms, so 1.45 ms; the ARM test machine 138,600 units/ms, 1.23 ms.
// A slice stops between queries, so it may overrun by one query: at most (2k + 6)^2 cells.
inline constexpr size_t peek_slice_units = 170000;
// P8: one refresh stays within avoidance_solve_budget_us on the compositor thread. Its peek
// slice pauses at peek_pause_us, leaving room for the one query a slice may overrun by and the
// occlusion pass. Pausing never changes a result.
inline constexpr int avoidance_solve_budget_us = 2000;
inline constexpr int peek_pause_us = 1500;
// The hint pass keeps hint circles this far apart (after the WK28 pop).
inline constexpr double hint_collision_gap = 6.0;
inline constexpr double hint_pop_scale = 1.06;
// Radius a hint of this diameter needs: its WK28 pop peak and one logical pixel of clearance.
inline double hint_room_radius(double diameter) { return diameter * hint_pop_scale / 2 + 1; }

enum class peek_rung : uint8_t { none, full, minimum, peek };
enum class peek_outcome : uint8_t { pending, visible, moved, no_room };
const char *to_string(peek_rung);
const char *to_string(peek_outcome);

struct offset_box { double x1, x2, y1, y2; }; // closed box of allowed offsets

struct peek_window                     // one per ordinary window, front to back
{
    rectangle frame{0, 0, 0, 0};       // true drawn rectangle (zone scale, no avoidance offset)
    double zone_x1 = 0, zone_x2 = 0;   // P13: the displayed center x stays inside
    double center_y1 = 0, center_y2 = 0; // the displayed center y stays on screen
    bool anchored = false;             // focused, grabbed, pair member: never moves
    double full_hint = 72, minimum_hint = 48; // hint diameters
    point target;                      // previous pass's target
    point displayed;                   // current eased offset
    std::vector<rectangle> fixed_foreground = {}; // widgets above it
};
struct peek_request
{
    rectangle screen{0, 0, 0, 0};      // the output's work area: what windows can show on
    double strip_depth = peek_strip_depth, strip_length = peek_strip_length;
    bool window_mode = false;          // ladder full -> minimum -> peek; else peek only
    bool live = false;                 // a drag, coast, keyboard motion or glide is in progress
    std::vector<peek_window> windows;
};
struct peek_result
{
    peek_outcome outcome = peek_outcome::pending;
    peek_rung rung = peek_rung::none;
    point target;                      // {0,0} for no_room
    rectangle room{0, 0, 0, 0};        // proof rectangle at the target, screen coordinates
    bool covered = false;              // something in front overlaps it at its target
    label_spot hint;                   // hint center (screen, at target) and its clearance
    double hint_diameter = 48;
    char rule = '-';                   // calm rule that chose the target: K keep, N near, Y any, H home
    size_t units = 0;                  // work spent on this window
};
struct peek_placement { point offset, corner; double cost = 0; };

struct peek_pass                       // resumable state; opaque to the bridge
{
    peek_request request;              // the snapshot
    std::vector<peek_result> results;  // results[i] is final once i < next
    size_t next = 0;
    size_t units = 0;                  // total spent, for telemetry
    size_t slices = 0;
    struct cursor_t
    {
        bool started = false;
        std::vector<rectangle> obstacles;
        offset_box limits{0, 0, 0, 0};
        double weights[4] = {1, 1, 1, 1}; // -x, +x, -y, +y
        size_t rung = 0, stage = 0, size = 0;
        bool any_phase = false; // live: no rung kept or found a near way; the any-stage sweep
        std::optional<peek_placement> best, keep, near;
        point best_size, keep_size, near_size;
        size_t units = 0;
    } cursor;
    explicit peek_pass(peek_request r = {});
    bool complete() const { return next >= request.windows.size(); }
};
// Spend at most `units` (and stop at `deadline`, which only pauses); true when the pass is
// complete. Results never depend on where a slice stopped.
bool peek_step(peek_pass&, size_t units = peek_slice_units,
    std::chrono::steady_clock::time_point deadline = std::chrono::steady_clock::time_point::max());

// The primitive, exposed for unit tests: the feasible offset inside `allowed` nearest to `from`
// (weighted per direction: -x, +x, -y, +y), at which a w x h rectangle inside the window (frame
// moved by the offset) lies on the screen and clear of every obstacle (touching is allowed).
// Exact; ties go to the offset nearer `displayed`, then the smaller offset.
std::optional<peek_placement> nearest_offset(rectangle frame, double w, double h, rectangle screen,
    const std::vector<rectangle>& obstacles, point from, offset_box allowed,
    point displayed = {}, const double *weights = nullptr, size_t *units = nullptr);
}
