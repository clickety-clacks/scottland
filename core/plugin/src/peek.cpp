#include "peek.hpp"
#include <algorithm>
#include <array>
#include <cmath>
#include <limits>
namespace scottland::windowing
{
const char *to_string(peek_rung rung)
{
    switch (rung)
    {
      case peek_rung::full: return "full";
      case peek_rung::minimum: return "minimum";
      case peek_rung::peek: return "peek";
      default: return "none";
    }
}
const char *to_string(peek_outcome outcome)
{
    switch (outcome)
    {
      case peek_outcome::visible: return "visible";
      case peek_outcome::moved: return "moved";
      case peek_outcome::no_room: return "no_room";
      default: return "pending";
    }
}
namespace
{
constexpr double epsilon = 1e-7;
constexpr double infinity = std::numeric_limits<double>::infinity();
double right(rectangle r) { return r.x + r.width; }
double bottom(rectangle r) { return r.y + r.height; }
rectangle moved(rectangle r, point d) { r.x += d.x; r.y += d.y; return r; }
rectangle on_screen(rectangle r, rectangle screen)
{
    double x2 = std::min(right(r), right(screen)), y2 = std::min(bottom(r), bottom(screen));
    r.x = std::max(r.x, screen.x); r.y = std::max(r.y, screen.y);
    r.width = std::max(0.0, x2 - r.x); r.height = std::max(0.0, y2 - r.y);
    return r;
}
bool overlaps(rectangle a, rectangle b)
{
    return a.x < right(b) - epsilon && b.x < right(a) - epsilon &&
        a.y < bottom(b) - epsilon && b.y < bottom(a) - epsilon;
}
// WK31: nothing in front overlaps the window's on-screen part. A window wholly off screen
// counts as uncovered: there is nothing of it to show.
bool uncovered(rectangle r, rectangle screen, const std::vector<rectangle>& obstacles)
{
    r = on_screen(r, screen);
    if (r.width <= 0 || r.height <= 0) return true;
    return std::none_of(obstacles.begin(), obstacles.end(),
        [&] (rectangle o) { return overlaps(r, o); });
}
offset_box intersect(offset_box a, offset_box b)
{
    return {std::max(a.x1, b.x1), std::min(a.x2, b.x2), std::max(a.y1, b.y1), std::min(a.y2, b.y2)};
}
offset_box around(point p, double radius)
{
    return {p.x - radius, p.x + radius, p.y - radius, p.y + radius};
}
bool empty(offset_box b) { return b.x1 > b.x2 + epsilon || b.y1 > b.y2 + epsilon; }
double weighted(point o, const double *w)
{
    double x = o.x * (o.x < 0 ? w[0] : w[1]), y = o.y * (o.y < 0 ? w[2] : w[3]);
    return std::hypot(x, y);
}
// Exact tie order after cost: nearer to where the window is drawn, then the smaller offset,
// then y, x, then the placement. Mike chose no preference for the top; this only decides ties.
bool better(const peek_placement& a, const peek_placement& b, point displayed)
{
    if (std::abs(a.cost - b.cost) > epsilon) return a.cost < b.cost;
    auto key = [&] (const peek_placement& p) {
        return std::array<double, 6>{std::hypot(p.offset.x - displayed.x, p.offset.y - displayed.y),
            std::abs(p.offset.x) + std::abs(p.offset.y), p.offset.y, p.offset.x, p.corner.y, p.corner.x};
    };
    auto ka = key(a), kb = key(b);
    for (size_t i = 0; i < ka.size(); ++i)
        if (std::abs(ka[i] - kb[i]) > epsilon) return ka[i] < kb[i];
    return false;
}
// One axis of the search. The test rectangle's corner coordinate q lies in [lo, hi] (screen and
// reach); the window's offset o must put q inside the window: o in [q - span_end, q - frame_start]
// intersected with the allowed range. For each candidate q, the best o is `from` clamped into that.
struct axis
{
    std::vector<double> q;      // candidate corner coordinates, sorted
    std::vector<double> offset; // best offset for each
    std::vector<double> cost;   // weighted |offset - from|
};
axis build_axis(double frame_start, double frame_size, double size, double screen_start,
    double screen_size, double allowed_lo, double allowed_hi, double from, double weight_lo,
    double weight_hi, const std::vector<double>& obstacle_edges, size_t& units)
{
    axis a;
    // Placements the window can reach at all, intersected with the screen.
    double lo = std::max(screen_start, frame_start + allowed_lo);
    double hi = std::min(screen_start + screen_size - size, frame_start + frame_size - size + allowed_hi);
    if (lo > hi + epsilon) return a;
    hi = std::max(lo, hi);
    std::vector<double> values{lo, hi, frame_start + from, frame_start + from + frame_size - size};
    values.insert(values.end(), obstacle_edges.begin(), obstacle_edges.end());
    std::sort(values.begin(), values.end());
    for (double v : values)
    {
        ++units;
        if (v < lo - epsilon || v > hi + epsilon) continue;
        v = std::clamp(v, lo, hi);
        if (!a.q.empty() && std::abs(a.q.back() - v) <= epsilon) continue;
        double o_lo = std::max(allowed_lo, v - frame_start - (frame_size - size));
        double o_hi = std::min(allowed_hi, v - frame_start);
        if (o_lo > o_hi + epsilon) continue;
        double o = std::clamp(from, o_lo, std::max(o_lo, o_hi));
        a.q.push_back(v); a.offset.push_back(o);
        a.cost.push_back((o < from ? weight_lo : weight_hi) * std::abs(o - from));
    }
    return a;
}
}

std::optional<peek_placement> nearest_offset(rectangle frame, double w, double h, rectangle screen,
    const std::vector<rectangle>& obstacles, point from, offset_box allowed, point displayed,
    const double *weights, size_t *units)
{
    static const double unit_weights[4] = {1, 1, 1, 1};
    if (!weights) weights = unit_weights;
    size_t spent = 0;
    auto finish = [&] (std::optional<peek_placement> found) {
        if (units) *units += spent;
        return found;
    };
    if (empty(allowed) || w > screen.width + epsilon || h > screen.height + epsilon ||
        w > frame.width + epsilon || h > frame.height + epsilon) return finish({});
    // Placements the window can reach, as a box, to skip obstacles that cannot matter.
    rectangle reach{std::max(screen.x, frame.x + allowed.x1), std::max(screen.y, frame.y + allowed.y1), 0, 0};
    reach.width = std::min(right(screen) - w, right(frame) - w + allowed.x2) - reach.x;
    reach.height = std::min(bottom(screen) - h, bottom(frame) - h + allowed.y2) - reach.y;
    if (reach.width < -epsilon || reach.height < -epsilon) return finish({});
    // G_j: corners whose rectangle overlaps obstacle j (open: touching is allowed).
    std::vector<rectangle> blocked;
    std::vector<double> xs, ys;
    for (auto o : obstacles)
    {
        ++spent;
        rectangle g{o.x - w, o.y - h, o.width + w, o.height + h};
        if (g.width <= epsilon || g.height <= epsilon) continue;
        if (right(g) <= reach.x + epsilon || g.x >= right(reach) - epsilon ||
            bottom(g) <= reach.y + epsilon || g.y >= bottom(reach) - epsilon) continue;
        // A region already inside another adds nothing (exact).
        bool contained = false;
        for (auto& b : blocked)
            if (b.x <= g.x + epsilon && b.y <= g.y + epsilon && right(g) <= right(b) + epsilon &&
                bottom(g) <= bottom(b) + epsilon) { contained = true; break; }
        if (contained) continue;
        blocked.push_back(g);
        xs.push_back(g.x); xs.push_back(right(g));
        ys.push_back(g.y); ys.push_back(bottom(g));
    }
    auto ax = build_axis(frame.x, frame.width, w, screen.x, screen.width, allowed.x1, allowed.x2,
        from.x, weights[0], weights[1], xs, spent);
    auto ay = build_axis(frame.y, frame.height, h, screen.y, screen.height, allowed.y1, allowed.y2,
        from.y, weights[2], weights[3], ys, spent);
    const size_t nx = ax.q.size(), ny = ay.q.size();
    if (!nx || !ny) return finish({});
    // Coverage of every candidate corner by the open regions, as a 2D difference array.
    std::vector<int> cover((nx + 1) * (ny + 1), 0);
    auto at = [&] (size_t i, size_t j) -> int& { return cover[j * (nx + 1) + i]; };
    for (auto& g : blocked)
    {
        ++spent;
        size_t i1 = std::upper_bound(ax.q.begin(), ax.q.end(), g.x + epsilon) - ax.q.begin();
        size_t i2 = std::lower_bound(ax.q.begin(), ax.q.end(), right(g) - epsilon) - ax.q.begin();
        size_t j1 = std::upper_bound(ay.q.begin(), ay.q.end(), g.y + epsilon) - ay.q.begin();
        size_t j2 = std::lower_bound(ay.q.begin(), ay.q.end(), bottom(g) - epsilon) - ay.q.begin();
        if (i1 >= i2 || j1 >= j2) continue;
        ++at(i1, j1); --at(i2, j1); --at(i1, j2); ++at(i2, j2);
    }
    std::optional<peek_placement> best;
    std::vector<int> row(nx + 1, 0);
    for (size_t j = 0; j < ny; ++j)
    {
        int running = 0;
        for (size_t i = 0; i < nx; ++i)
        {
            ++spent;
            running += at(i, j);
            row[i] += running;
            if (row[i] != 0) continue;
            peek_placement p{{ax.offset[i], ay.offset[j]}, {ax.q[i], ay.q[j]},
                std::hypot(ax.cost[i], ay.cost[j])};
            if (!best || better(p, *best, displayed)) best = p;
        }
    }
    return finish(best);
}

peek_pass::peek_pass(peek_request r) : request(std::move(r)), results(request.windows.size()) {}

namespace
{
// The calm rules' stages for one rung (section 5 of the design).
enum stage_t : size_t { keep_stage, near_stage, any_stage, home_stage, decide_stage };
struct size2 { double w, h; };
std::vector<size2> rung_sizes(const peek_request& request, const peek_window& window, peek_rung rung)
{
    auto fit = [&] (double w, double h) {
        return size2{std::min(w, window.frame.width), std::min(h, window.frame.height)};
    };
    if (rung == peek_rung::full || rung == peek_rung::minimum)
    {
        double side = 2 * hint_room_radius(rung == peek_rung::full ?
            std::max(window.full_hint, window.minimum_hint) : window.minimum_hint);
        return {fit(side, side)};
    }
    auto a = fit(request.strip_length, request.strip_depth), b = fit(request.strip_depth, request.strip_length);
    if (std::abs(a.w - b.w) < epsilon && std::abs(a.h - b.h) < epsilon) return {a};
    return {a, b};
}
std::vector<peek_rung> ladder(const peek_request& request)
{
    if (request.window_mode) return {peek_rung::full, peek_rung::minimum, peek_rung::peek};
    return {peek_rung::peek};
}
// Decision 8: prefer peeking toward the window's position relative to the window covering it
// most. Moves in the other three directions cost peek_direction_weight times their length.
void preference(const rectangle frame, const std::vector<rectangle>& obstacles, double *weights)
{
    std::fill(weights, weights + 4, 1.0);
    double most = 0; std::optional<rectangle> cover;
    for (auto o : obstacles)
    {
        double w = std::min(right(frame), right(o)) - std::max(frame.x, o.x);
        double h = std::min(bottom(frame), bottom(o)) - std::max(frame.y, o.y);
        if (w > 0 && h > 0 && w * h > most + epsilon) { most = w * h; cover = o; }
    }
    if (!cover) return;
    double dx = (frame.x + frame.width / 2) - (cover->x + cover->width / 2);
    double dy = (frame.y + frame.height / 2) - (cover->y + cover->height / 2);
    double nx = dx / std::max(1.0, frame.width + cover->width);
    double ny = dy / std::max(1.0, frame.height + cover->height);
    if (std::abs(nx) < 1e-4 && std::abs(ny) < 1e-4) return;
    size_t preferred = std::abs(nx) > std::abs(ny) ? (dx < 0 ? 0 : 1) : (dy < 0 ? 2 : 3);
    for (size_t k = 0; k < 4; ++k) weights[k] = k == preferred ? 1.0 : peek_direction_weight;
}
// The largest free rectangle grown from the proof rectangle: left, right, up, down, in that
// order, each to the nearest obstacle, the window's own edge or the screen edge.
rectangle grow(rectangle q, rectangle window, rectangle screen, const std::vector<rectangle>& obstacles)
{
    rectangle bound = on_screen(window, screen);
    double x1 = q.x, x2 = right(q), y1 = q.y, y2 = bottom(q);
    auto spans_y = [&] (rectangle o) { return o.y < y2 - epsilon && bottom(o) > y1 + epsilon; };
    auto spans_x = [&] (rectangle o) { return o.x < x2 - epsilon && right(o) > x1 + epsilon; };
    double edge = bound.x;
    for (auto o : obstacles) if (spans_y(o) && right(o) <= x1 + epsilon) edge = std::max(edge, right(o));
    x1 = std::min(x1, edge);
    edge = right(bound);
    for (auto o : obstacles) if (spans_y(o) && o.x >= x2 - epsilon) edge = std::min(edge, o.x);
    x2 = std::max(x2, edge);
    edge = bound.y;
    for (auto o : obstacles) if (spans_x(o) && bottom(o) <= y1 + epsilon) edge = std::max(edge, bottom(o));
    y1 = std::min(y1, edge);
    edge = bottom(bound);
    for (auto o : obstacles) if (spans_x(o) && o.y >= y2 - epsilon) edge = std::min(edge, o.y);
    y2 = std::max(y2, edge);
    return {x1, y1, x2 - x1, y2 - y1};
}
double clamp_into(double v, double lo, double hi)
{
    return lo <= hi ? std::clamp(v, lo, hi) : (lo + hi) / 2;
}
// Section 7: the hint for window i, after its target is final. Hints of windows in front are
// final too, so collisions are resolved here, front to back.
void place_hint(const peek_pass& pass, size_t i, const std::vector<rectangle>& obstacles, peek_result& result)
{
    const auto& request = pass.request;
    const auto& window = request.windows[i];
    const auto screen = request.screen;
    const auto drawn = moved(window.frame, result.target);
    const auto shown = on_screen(drawn, screen);
    const double minimum = window.minimum_hint, wanted = std::max(window.full_hint, minimum);
    auto fits = [] (rectangle r) {
        return std::max(0.0, std::floor((std::min(r.width, r.height) / 2 - 1) * 2 / hint_pop_scale));
    };
    point center{shown.x + shown.width / 2, shown.y + shown.height / 2};
    double diameter = minimum;
    rectangle span = shown; // where a colliding hint may slide
    bool slide_x = false, slide = false;
    if (!result.covered)
    {
        // WK31: nothing covers it: the exact center of its on-screen part.
        diameter = std::clamp(fits(shown), minimum, wanted);
    } else if (result.rung != peek_rung::none && result.room.width > 0)
    {
        auto room = grow(result.room, drawn, screen, obstacles);
        center = {room.x + room.width / 2, room.y + room.height / 2};
        if (result.rung == peek_rung::peek)
        {
            // Decision 5: the hint sits on the strip and overlaps the front window's edge.
            slide = true; slide_x = room.width >= room.height; span = room;
        } else diameter = std::clamp(fits(room), minimum, wanted);
    } else slide = true; // no room: hint only, where it can be seen
    // Keep the circle on its own window where it is big enough (a strip hint is centered on
    // its strip instead: the letter on the window's color, the circle over both edges), and on
    // the screen.
    double radius = diameter * hint_pop_scale / 2;
    if (result.rung != peek_rung::peek)
    {
        center.x = clamp_into(center.x, shown.x + radius, right(shown) - radius);
        center.y = clamp_into(center.y, shown.y + radius, bottom(shown) - radius);
    }
    center.x = clamp_into(center.x, screen.x + radius, right(screen) - radius);
    center.y = clamp_into(center.y, screen.y + radius, bottom(screen) - radius);
    // Collisions with hints in front: a strip hint slides along its strip, a no-room hint along
    // the shorter screen axis, the least distance that clears them all; else it stays.
    auto hits = [&] (point p, size_t j) {
        const auto& other = pass.results[j];
        return std::hypot(p.x - other.hint.center.x, p.y - other.hint.center.y) <
            (diameter + other.hint_diameter) / 2 * hint_pop_scale + hint_collision_gap;
    };
    auto clear = [&] (point p) {
        for (size_t j = 0; j < i; ++j) if (hits(p, j)) return false;
        return true;
    };
    if (slide && !clear(center))
    {
        if (result.rung != peek_rung::peek) slide_x = screen.width < screen.height;
        double lo = slide_x ? std::max(span.x, screen.x) + radius : std::max(span.y, screen.y) + radius;
        double hi = slide_x ? std::min(right(span), right(screen)) - radius :
            std::min(bottom(span), bottom(screen)) - radius;
        if (result.rung != peek_rung::peek)
        {
            lo = slide_x ? screen.x + radius : screen.y + radius;
            hi = slide_x ? right(screen) - radius : bottom(screen) - radius;
        }
        std::vector<double> moves;
        for (size_t j = 0; j < i; ++j)
        {
            const auto& other = pass.results[j];
            double d = (diameter + other.hint_diameter) / 2 * hint_pop_scale + hint_collision_gap + .5;
            double across = slide_x ? center.y - other.hint.center.y : center.x - other.hint.center.x;
            if (std::abs(across) >= d) continue;
            double along = std::sqrt(d * d - across * across);
            double at = slide_x ? other.hint.center.x : other.hint.center.y;
            double now = slide_x ? center.x : center.y;
            moves.push_back(at - along - now); moves.push_back(at + along - now);
        }
        std::sort(moves.begin(), moves.end(), [] (double a, double b) {
            return std::abs(a) != std::abs(b) ? std::abs(a) < std::abs(b) : a < b; });
        for (double m : moves)
        {
            point p = center;
            (slide_x ? p.x : p.y) += m;
            double v = slide_x ? p.x : p.y;
            if (v < lo - epsilon || v > hi + epsilon || !clear(p)) continue;
            center = p;
            break;
        }
    }
    // Clearance: how far the circle may grow at this point inside the visible region.
    double clearance = std::min({center.x - shown.x, right(shown) - center.x,
        center.y - shown.y, bottom(shown) - center.y});
    for (auto o : obstacles)
    {
        double dx = std::max({o.x - center.x, 0.0, center.x - right(o)});
        double dy = std::max({o.y - center.y, 0.0, center.y - bottom(o)});
        clearance = std::min(clearance, dx == 0 && dy == 0 ? 0.0 : std::hypot(dx, dy));
    }
    result.hint = {center, std::max(0.0, clearance)};
    result.hint_diameter = diameter;
}
}

bool peek_step(peek_pass& pass, size_t budget, std::chrono::steady_clock::time_point deadline)
{
    const auto& request = pass.request;
    const size_t start = pass.units;
    if (!pass.complete()) ++pass.slices;
    auto out_of_budget = [&] () {
        return pass.units - start >= budget || (deadline != std::chrono::steady_clock::time_point::max() &&
            std::chrono::steady_clock::now() >= deadline);
    };
    while (!pass.complete())
    {
        const size_t i = pass.next;
        const auto& window = request.windows[i];
        auto& result = pass.results[i];
        auto& c = pass.cursor;
        auto spend = [&] (size_t n) { pass.units += n; c.units += n; };
        if (!c.started)
        {
            if (out_of_budget()) return false;
            c = {};
            c.started = true;
            c.obstacles = window.fixed_foreground;
            for (size_t j = 0; j < i; ++j)
                c.obstacles.push_back(moved(request.windows[j].frame, pass.results[j].target));
            spend(c.obstacles.size());
            const double cx = window.frame.x + window.frame.width / 2;
            const double cy = window.frame.y + window.frame.height / 2;
            // P13: the displayed center stays in its own zone; vertically, on the screen. The true
            // position is always allowed: it is where the user put the window.
            c.limits = window.anchored ? offset_box{0, 0, 0, 0} : offset_box{
                std::min(0.0, window.zone_x1 - cx), std::max(0.0, window.zone_x2 - cx),
                std::min(0.0, window.center_y1 - cy), std::max(0.0, window.center_y2 - cy)};
            preference(window.frame, c.obstacles, c.weights);
            const bool home = uncovered(window.frame, request.screen, c.obstacles);
            if (home && (!request.live || (std::abs(window.target.x) < epsilon &&
                std::abs(window.target.y) < epsilon)))
            {
                // Nothing in front of it at its true position: it stays (or goes) home.
                result.outcome = peek_outcome::visible; result.rung = peek_rung::none;
                result.target = {}; result.room = {0, 0, 0, 0}; result.covered = false;
                place_hint(pass, i, c.obstacles, result);
                result.units = c.units;
                c.started = false; ++pass.next;
                continue;
            }
        }
        const auto rungs = ladder(request);
        if (c.rung >= rungs.size() && request.live && !c.any_phase)
        {
            // Live, no rung has a kept or near way: only now a larger move, down the ladder
            // again (P11: a smaller hint nearby beats a full one across the screen).
            c.any_phase = true; c.rung = 0; c.stage = any_stage; c.size = 0;
            c.best.reset(); c.keep.reset(); c.near.reset();
            continue;
        }
        if (c.rung >= rungs.size())
        {
            // No rung has room inside the limits: the window does not move (P2); hint only.
            result.outcome = peek_outcome::no_room; result.rung = peek_rung::none;
            result.target = {}; result.room = {0, 0, 0, 0};
            result.covered = !uncovered(window.frame, request.screen, c.obstacles);
            place_hint(pass, i, c.obstacles, result);
            result.units = c.units;
            c.started = false; ++pass.next;
            continue;
        }
        const auto rung = rungs[c.rung];
        const auto sizes = rung_sizes(request, window, rung);
        if (c.stage == decide_stage)
        {
            std::optional<peek_placement> chosen; point chosen_size{}; char rule = '-';
            if (request.live)
            {
                if (c.keep && (!c.near || c.keep->cost <= c.near->cost + peek_switch_margin))
                { chosen = c.keep; chosen_size = c.keep_size; rule = 'K'; }
                else if (c.near) { chosen = c.near; chosen_size = c.near_size; rule = 'N'; }
                else { chosen = c.best; chosen_size = c.best_size; rule = 'Y'; } // the any-stage answer
            } else
            {
                if (c.keep && c.best && c.keep->cost <= c.best->cost + peek_switch_margin)
                { chosen = c.keep; chosen_size = c.keep_size; rule = 'K'; }
                else { chosen = c.best; chosen_size = c.best_size; rule = 'H'; } // the home-stage answer
            }
            if (!chosen)
            {
                ++c.rung; c.stage = c.any_phase ? any_stage : keep_stage; c.size = 0;
                c.best.reset(); c.keep.reset(); c.near.reset();
                continue;
            }
            result.rung = rung; result.rule = rule;
            result.target = chosen->offset;
            result.outcome = std::hypot(result.target.x, result.target.y) < epsilon ?
                peek_outcome::visible : peek_outcome::moved;
            result.room = {chosen->corner.x, chosen->corner.y, chosen_size.x, chosen_size.y};
            result.covered = !uncovered(moved(window.frame, result.target), request.screen, c.obstacles);
            place_hint(pass, i, c.obstacles, result);
            result.units = c.units;
            c.started = false; ++pass.next;
            continue;
        }
        // Skip the stages the current mode does not use: live, keep and near on every rung first,
        // then the any stage on every rung; at rest, keep and home.
        if (request.live ? (c.stage == home_stage || (c.stage == any_stage && !c.any_phase)) :
            (c.stage == near_stage || c.stage == any_stage))
        { ++c.stage; c.size = 0; continue; }
        if (out_of_budget()) return false;
        // One query: one rectangle size for the current stage.
        point from{}; offset_box allowed = c.limits;
        if (c.stage == keep_stage) allowed = intersect(allowed, around(window.target, peek_keep_radius));
        if (c.stage == near_stage) allowed = intersect(allowed, around(window.displayed, peek_reach));
        if (c.stage == any_stage) from = window.displayed;
        const auto size = sizes[c.size];
        size_t units = 0;
        auto found = nearest_offset(window.frame, size.w, size.h, request.screen, c.obstacles, from,
            allowed, window.displayed, c.weights, &units);
        spend(units + 1);
        auto& slot = c.stage == keep_stage ? c.keep : c.stage == near_stage ? c.near : c.best;
        auto& slot_size = c.stage == keep_stage ? c.keep_size : c.stage == near_stage ? c.near_size : c.best_size;
        if (found && (!slot || better(*found, *slot, window.displayed)))
        { slot = found; slot_size = {size.w, size.h}; }
        if (++c.size >= sizes.size())
        {
            // Costs compare from home: the any-stage measured from the displayed offset.
            if (c.stage == any_stage && c.best)
                c.best->cost = weighted(c.best->offset, c.weights);
            ++c.stage; c.size = 0;
        }
    }
    return true;
}
}
