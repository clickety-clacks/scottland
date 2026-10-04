#include "declutter.hpp"
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <functional>
#include <limits>
#include <queue>
#include <set>
#include <tuple>
namespace scottland::windowing
{
namespace
{
constexpr double pop_scale = 1.06;
double needed_radius(double diameter)
{
    return diameter * pop_scale / 2 + 1; // WK28 peak and one logical pixel of clearance
}
rectangle moved(rectangle r, point delta)
{
    r.x += delta.x; r.y += delta.y; return r;
}
bool fully_occluded(rectangle r, const std::vector<rectangle>& foreground)
{
    return std::any_of(foreground.begin(), foreground.end(), [&] (rectangle cover) {
        return cover.x <= r.x && cover.y <= r.y &&
            cover.x + cover.width >= r.x + r.width &&
            cover.y + cover.height >= r.y + r.height;
    });
}
rectangle on_screen(rectangle r, rectangle screen)
{
    const double right = std::min(r.x + r.width, screen.x + screen.width);
    const double bottom = std::min(r.y + r.height, screen.y + screen.height);
    r.x = std::max(r.x, screen.x); r.y = std::max(r.y, screen.y);
    r.width = right - r.x; r.height = bottom - r.y;
    return r;
}
// WK31: nothing in front (window or widget) overlaps the window's on-screen part. Strict:
// a cover that leaves the center clear still counts, so that window keeps the search.
bool uncovered(rectangle r, rectangle screen, const std::vector<rectangle>& foreground)
{
    r = on_screen(r, screen);
    if (r.width <= 0 || r.height <= 0) return false;
    return std::none_of(foreground.begin(), foreground.end(),
        [&] (rectangle cover) { return overlap(r, cover) > 0; });
}
// An uncovered window's hint goes at the exact center of what is on screen of it,
// with no search: its clearance is the distance to the nearest window edge.
label_spot centered_spot(rectangle r, rectangle screen)
{
    r = on_screen(r, screen);
    const point center{r.x + r.width / 2, r.y + r.height / 2};
    return {center, std::max(0.0, std::min(r.width, r.height) / 2)};
}
double badge_diameter(const exposure_window& window, double clearance)
{
    return std::max(window.minimum, std::min(std::max(window.wanted, window.minimum),
        std::floor(std::max(0.0, 2 * (clearance - 1) / pop_scale))));
}
// Reserved out of a solve's deadline for finish_hints, so the whole solve stays in budget.
std::chrono::nanoseconds finish_reserve(size_t windows)
{
    return std::chrono::nanoseconds(2000 + 25 * windows * windows);
}
// The one end-of-solve pass, front to back over a whole layout, with the foreground list
// built as it goes:
// - WK31: a window nothing covers at its final position has its hint at its center,
//   whatever path produced its result (search, held target, deadline fallback, way recheck).
// - A held label keeps its point, but its clearance is measured on this layout: the
//   previous layout's (e.g. before a raise buried the window) would report a covered hint
//   as visible at its old size.
// - Stopgap pending P1/P12: a hint that would land on a hint in front of it moves about one
//   diameter off it, onto its own window's on-screen part where possible, so both letters
//   read. Front hints never move for rear ones. The choice is deterministic.
// Work is bounded: per window, one clearance check and at most 8 candidates per hint it hits.
void finish_hints(const std::vector<exposure_window>& windows, rectangle screen,
    const std::vector<rectangle>& fixed, std::vector<exposure_result>& results, size_t& work)
{
    struct drawn_hint { point center; double radius; };
    std::vector<drawn_hint> placed;
    std::vector<rectangle> foreground = fixed;
    const size_t count = std::min(windows.size(), results.size());
    auto clearance_at = [&] (point p, rectangle frame, const exposure_window& window) {
        work += foreground.size() + window.fixed_foreground.size();
        return std::min(visible_clearance(p, frame, screen, foreground),
            visible_clearance(p, frame, screen, window.fixed_foreground));
    };
    auto hits = [&] (point p, double radius, const drawn_hint& other) {
        return std::hypot(p.x - other.center.x, p.y - other.center.y) <
            (radius + other.radius) * pop_scale + hint_collision_gap;
    };
    for (size_t i = 0; i < count; ++i)
    {
        const auto& window = windows[i];
        auto& result = results[i];
        const auto frame = moved(window.frame, result.offset);
        work += foreground.size() + window.fixed_foreground.size();
        if (uncovered(frame, screen, foreground) && uncovered(frame, screen, window.fixed_foreground))
        {
            result.spot = centered_spot(frame, screen);
            result.diameter = badge_diameter(window, result.spot.clearance);
        } else if (result.held)
        {
            const double checked = clearance_at(result.spot.center, frame, window);
            if (checked < result.spot.clearance)
            {
                result.spot.clearance = std::max(0.0, checked);
                if (checked <= 0)
                    result.spot.center = {frame.x + frame.width / 2, frame.y + frame.height / 2};
                result.diameter = badge_diameter(window, result.spot.clearance);
            }
        }
        const double radius = result.diameter / 2;
        work += placed.size();
        if (std::any_of(placed.begin(), placed.end(),
            [&] (const drawn_hint& other) { return hits(result.spot.center, radius, other); }))
        {
            const auto own = on_screen(frame, screen);
            auto inside = [&] (point p, rectangle r) {
                const double edge = radius * pop_scale;
                return p.x - edge >= r.x && p.x + edge <= r.x + r.width &&
                    p.y - edge >= r.y && p.y + edge <= r.y + r.height;
            };
            std::optional<std::tuple<int, double, double, size_t>> best_score;
            point best{};
            size_t order = 0;
            for (const auto& other : placed)
            {
                if (!hits(result.spot.center, radius, other)) continue;
                const double distance = (radius + other.radius) * pop_scale + hint_collision_gap + .5;
                for (int k = 0; k < 8; ++k, ++order)
                {
                    const double angle = k * 0.7853981633974483; // 45 degrees
                    const point p{other.center.x + distance * std::cos(angle),
                        other.center.y + distance * std::sin(angle)};
                    if (!inside(p, screen)) continue;
                    work += placed.size();
                    if (std::any_of(placed.begin(), placed.end(),
                        [&] (const drawn_hint& h) { return hits(p, radius, h); })) continue;
                    // Own window first, then the most visible spot, then the nearest.
                    const std::tuple<int, double, double, size_t> score{inside(p, own) ? 0 : 1,
                        -std::max(0.0, clearance_at(p, frame, window)),
                        std::hypot(p.x - result.spot.center.x, p.y - result.spot.center.y), order};
                    if (!best_score || score < *best_score) { best_score = score; best = p; }
                }
            }
            if (best_score)
            {
                result.spot.center = best;
                result.spot.clearance = std::max(0.0, -std::get<1>(*best_score));
                // Never grow: the collision checks above used the current diameter.
                result.diameter = std::min(result.diameter,
                    badge_diameter(window, result.spot.clearance));
            }
        }
        placed.push_back({result.spot.center, result.diameter / 2});
        foreground.push_back(frame);
    }
}
int original_side(rectangle frame, rectangle screen)
{
    const double center = screen.x + screen.width / 2;
    const double x = frame.x + frame.width / 2;
    return x < center ? -1 : 1;
}
int avoidance_side(const exposure_window& window, rectangle screen)
{
    return window.center_zone ? 0 : original_side(window.frame, screen);
}
std::pair<double, double> horizontal_center_limits(const exposure_window& window, rectangle screen)
{
    const double center = screen.x + screen.width / 2;
    if (window.center_zone)
    {
        const double half = std::max(0.0, window.center_zone_half_width);
        return {center - half, center + half};
    }
    return avoidance_side(window, screen) < 0 ?
        std::pair{-std::numeric_limits<double>::infinity(), center} :
        std::pair{center, std::numeric_limits<double>::infinity()};
}
std::pair<double, double> vertical_center_limits(rectangle frame, rectangle screen)
{
    const double center = screen.y + screen.height / 2;
    const double band = screen.height * .25;
    const double original = frame.y + frame.height / 2;
    if (std::abs(original - center) <= band)
        return {center - band, center + band};
    return original < center ? std::pair{-std::numeric_limits<double>::infinity(), center} :
        std::pair{center, std::numeric_limits<double>::infinity()};
}
bool remains_in_avoidance_zone(const exposure_window& window, rectangle position, rectangle screen,
    bool allow_minimum_patch_zone_overshoot = false)
{
    if (allow_minimum_patch_zone_overshoot) return true;
    const auto [xlo, xhi] = horizontal_center_limits(window, screen);
    const auto [ylo, yhi] = vertical_center_limits(window.frame, screen);
    const double x = position.x + position.width / 2, y = position.y + position.height / 2;
    return x >= xlo - .01 && x <= xhi + .01 && y >= ylo - .01 && y <= yhi + .01;
}
void constrain_zone_delta(const exposure_window& window, rectangle current, rectangle screen,
    double& xlo, double& xhi, double& ylo, double& yhi,
    bool allow_minimum_patch_zone_overshoot = false)
{
    if (allow_minimum_patch_zone_overshoot) return;
    const auto [cxlo, cxhi] = horizontal_center_limits(window, screen);
    const auto [cylo, cyhi] = vertical_center_limits(window.frame, screen);
    const double x = current.x + current.width / 2, y = current.y + current.height / 2;
    xlo = std::max(xlo, cxlo - x); xhi = std::min(xhi, cxhi - x);
    ylo = std::max(ylo, cylo - y); yhi = std::min(yhi, cyhi - y);
}
struct move_bounds { double xlo, xhi, ylo, yhi; };
move_bounds bounds_for(const exposure_window& window, rectangle r, rectangle screen, double diameter,
    bool allow_minimum_patch_zone_overshoot = false)
{
    double visible = 2 * needed_radius(diameter);
    // Keep the badge-sized portion on screen, but permit the minimal off-screen movement
    // needed when a screen-sized front window covers another window completely.
    move_bounds bounds{std::min(r.x, screen.x - r.width + visible),
        std::max(r.x, screen.x + screen.width - visible),
        std::min(r.y, screen.y - r.height + visible),
        std::max(r.y, screen.y + screen.height - visible)};
    if (!allow_minimum_patch_zone_overshoot)
    {
        const auto [cxlo, cxhi] = horizontal_center_limits(window, screen);
        const auto [cylo, cyhi] = vertical_center_limits(window.frame, screen);
        bounds.xlo = std::max(bounds.xlo, cxlo - r.width / 2);
        bounds.xhi = std::min(bounds.xhi, cxhi - r.width / 2);
        bounds.ylo = std::max(bounds.ylo, cylo - r.height / 2);
        bounds.yhi = std::min(bounds.yhi, cyhi - r.height / 2);
    }
    return bounds;
}
std::vector<double> candidate_axis(rectangle r, rectangle screen,
    const std::vector<rectangle>& obstacles, double radius, bool horizontal,
    std::chrono::steady_clock::time_point deadline)
{
    auto begin = [&] (rectangle b) { return horizontal ? b.x : b.y; };
    auto end = [&] (rectangle b) { return begin(b) + (horizontal ? b.width : b.height); };
    std::vector<double> values{begin(r) + radius, end(r) - radius,
        (begin(r) + end(r)) / 2, begin(screen) + radius, end(screen) - radius};
    size_t considered = 0;
    for (auto o : obstacles)
    {
        // Preserve exact obstacle-edge candidates while keeping coarse work bounded.
        if (std::chrono::steady_clock::now() >= deadline || considered++ >= 7) break;
        values.push_back(begin(o) - radius - .5);
        values.push_back(end(o) + radius + .5);
        // Midpoints between the screen and an occluder give coarse seeds at the
        // center of a broad left/right opening, independent of the requested radius.
        values.push_back((begin(screen) + begin(o)) / 2);
        values.push_back((end(o) + end(screen)) / 2);
        // Corner contacts may reveal a circle before a whole rectangular strip opens.
        for (double edge : {begin(o), end(o)})
        { values.push_back(edge - 0.707106781187 * radius - .5);
          values.push_back(edge + 0.707106781187 * radius + .5); }
    }
    std::sort(values.begin(), values.end());
    values.erase(std::unique(values.begin(), values.end()), values.end());
    return values;
}
bool expired(std::chrono::steady_clock::time_point deadline)
{
    return deadline != std::chrono::steady_clock::time_point::max() &&
        std::chrono::steady_clock::now() >= deadline;
}
bool take_inspection(size_t& count, size_t budget)
{
    if (count >= budget) return false;
    ++count;
    return true;
}
bool stopped(std::chrono::steady_clock::time_point deadline, size_t count, size_t budget)
{
    return expired(deadline) || count >= budget;
}
std::optional<double> checked_clearance(point center, rectangle window, rectangle screen,
    const std::vector<rectangle>& foreground, std::chrono::steady_clock::time_point deadline,
    size_t& count, size_t budget)
{
    if (!take_inspection(count, budget)) return {};
    return visible_clearance_before(center, window, screen, foreground, deadline);
}
std::chrono::steady_clock::time_point label_deadline(std::chrono::steady_clock::time_point solve_deadline)
{
    if (solve_deadline == std::chrono::steady_clock::time_point::max()) return solve_deadline;
    return std::min(solve_deadline, std::chrono::steady_clock::now() + std::chrono::microseconds(250));
}
struct candidate
{
    size_t moved_index;
    point delta;
    double travel;
    double displayed_travel;
    point label_center;
    double clearance;
    int branch_axis;
    int branch_sign;
    bool branch_match;
    point branch_base_offset;
};
std::optional<candidate> least_exposure_move(size_t index, double diameter,
    const std::vector<rectangle>& nodes, const std::vector<exposure_window>& windows,
    rectangle screen, const std::vector<rectangle>& fixed,
    std::chrono::steady_clock::time_point deadline,
    const std::vector<label_spot>& known_spots, const std::vector<bool>& has_spot,
    size_t& work_count, size_t work_budget, bool *search_truncated,
    bool allow_minimum_patch_zone_overshoot = false, bool replacement_probe = false)
{
    if (search_truncated) *search_truncated = false;
    std::optional<candidate> best;
    auto partial_result = [&] () {
        if (search_truncated) *search_truncated = true;
        return best;
    };
    if (stopped(deadline, work_count, work_budget)) return partial_result();
    auto route_base = [&] (size_t moved_index) {
        const auto& window = windows[moved_index];
        if (replacement_probe && window.branch_axis && window.branch_owner >= 0)
            return window.incumbent_offset;
        // Stay on a retained way's stable base while testing its least true-frame
        // offset. A separate replacement probe starts from the displayed incumbent.
        return window.branch_owner >= 0 && window.branch_axis ?
            window.branch_base_offset : window.incumbent_offset;
    };
    auto scene_delta_for = [&] (size_t moved_index, point target_offset) {
        const auto& frame = windows[moved_index].frame;
        return point{frame.x + target_offset.x - nodes[moved_index].x,
            frame.y + target_offset.y - nodes[moved_index].y};
    };
    auto r = moved(windows[index].frame, route_base(index)); double radius = needed_radius(diameter);
    std::vector<rectangle> static_here = fixed;
    static_here.reserve(fixed.size() + windows[index].fixed_foreground.size());
    static_here.insert(static_here.end(), windows[index].fixed_foreground.begin(),
        windows[index].fixed_foreground.end());
    std::vector<rectangle> obstacles = static_here;
    obstacles.reserve(static_here.size() + index);
    for (size_t j = 0; j < index; ++j) obstacles.push_back(nodes[j]);
    auto xs = candidate_axis(r, screen, obstacles, radius, true, deadline);
    auto ys = candidate_axis(r, screen, obstacles, radius, false, deadline);
    struct protected_label { size_t index; point center; double radius; };
    std::vector<protected_label> protected_labels;
    protected_labels.reserve(nodes.size() - (nodes.empty() ? 0 : 1));
    for (size_t k = 0; k < nodes.size(); ++k)
    {
        if (stopped(deadline, work_count, work_budget)) return partial_result();
        if (k != index && has_spot[k] &&
            known_spots[k].clearance + .25 >= needed_radius(windows[k].minimum))
            protected_labels.push_back({k, known_spots[k].center,
                std::min(known_spots[k].clearance - .25,
                    needed_radius(std::max(windows[k].wanted, windows[k].minimum)))});
    }
    std::vector<rectangle> one_obstacle;
    one_obstacle.reserve(1);
    std::vector<rectangle> label_foreground;
    size_t label_foreground_capacity = fixed.size() + nodes.size();
    for (const auto& window : windows)
        label_foreground_capacity += window.fixed_foreground.size();
    label_foreground.reserve(label_foreground_capacity);
    auto same_displacement_side = [&] (size_t moved_index, point delta) {
        // Prefer the direction already on screen and retain a prior destination while
        // it is easing in. A new solve must not reverse either incumbent by tie order.
        const point old = windows[moved_index].incumbent_offset;
        const point old_target = windows[moved_index].target_offset;
        const point target{nodes[moved_index].x + delta.x - windows[moved_index].frame.x,
            nodes[moved_index].y + delta.y - windows[moved_index].frame.y};
        if (std::abs(old.x) > .01 && target.x * old.x < -.01) return false;
        if (std::abs(old.y) > .01 && target.y * old.y < -.01) return false;
        if (std::abs(old_target.x) > .01 && target.x * old_target.x < -.01) return false;
        if (std::abs(old_target.y) > .01 && target.y * old_target.y < -.01) return false;
        return true;
    };
    auto branch_of = [] (point offset, point base) {
        offset.x -= base.x; offset.y -= base.y;
        const bool x = std::abs(offset.x) >= .01, y = std::abs(offset.y) >= .01;
        if (!x && !y) return std::pair{0, 0};
        // A way is a one-dimensional ray from true geometry. Combining x and y
        // offsets creates a diagonal route whose identity changes as it eases.
        if (x && y) return std::pair{-1, 0};
        const int axis = x ? 1 : 2;
        const double value = x ? offset.x : offset.y;
        return std::pair{axis, value < 0 ? -1 : 1};
    };
    auto matches_branch = [&] (size_t moved_index, point delta) {
        point offset{nodes[moved_index].x + delta.x - windows[moved_index].frame.x,
            nodes[moved_index].y + delta.y - windows[moved_index].frame.y};
        if (std::hypot(offset.x, offset.y) < .01) return true;
        const auto& window = windows[moved_index];
        if (window.branch_owner == int(index) && window.branch_axis &&
            std::hypot(offset.x - window.branch_base_offset.x,
                offset.y - window.branch_base_offset.y) < .01) return true;
        auto [axis, direction] = branch_of(offset, windows[moved_index].branch_base_offset);
        return window.branch_owner == int(index) &&
            window.branch_axis == axis && window.branch_sign == direction;
    };
    auto offer = [&] (size_t moved_index, point delta, point label_center, double clearance) {
        if (stopped(deadline, work_count, work_budget)) return false;
        const bool is_zero = std::hypot(delta.x, delta.y) < .01;
        if (windows[moved_index].anchored && !is_zero) return true;
        if (!remains_in_avoidance_zone(windows[moved_index],
            moved(nodes[moved_index], delta), screen, allow_minimum_patch_zone_overshoot)) return true;
        const point final_offset{nodes[moved_index].x + delta.x - windows[moved_index].frame.x,
            nodes[moved_index].y + delta.y - windows[moved_index].frame.y};
        const bool branch_match = matches_branch(moved_index, delta);
        const point branch_base = branch_match ? windows[moved_index].branch_base_offset :
            windows[moved_index].incumbent_offset;
        const point branch_delta{final_offset.x - branch_base.x, final_offset.y - branch_base.y};
        if (std::abs(branch_delta.x) >= .01 && std::abs(branch_delta.y) >= .01) return true;
        double displayed_distance = std::hypot(final_offset.x - windows[moved_index].incumbent_offset.x,
            final_offset.y - windows[moved_index].incumbent_offset.y);
        if (windows[moved_index].branch_axis && windows[moved_index].branch_owner >= 0 &&
            !branch_match)
        {
            if (displayed_distance > avoidance_replacement_step_limit + .01 ||
                !same_displacement_side(moved_index, delta)) return true;
        }
        auto [axis, direction] = branch_of(final_offset, branch_base);
        if (branch_match && axis == 0)
        {
            axis = windows[moved_index].branch_axis;
            direction = windows[moved_index].branch_sign;
        }
        // A retained way wins while feasible; inside it the solve returns to the
        // smallest offset from true geometry. A new way is ranked from the displayed
        // incumbent, so a necessary branch change starts nearby.
        double distance = branch_match ? std::hypot(final_offset.x, final_offset.y) : displayed_distance;
        // A rear circle already uncovered in an earlier pass must remain uncovered.
        // A visible foreground circle must remain visible too. Otherwise moving a front
        // window for a rear one can retarget it back and forth on every small input change.
        for (auto label : protected_labels)
        {
            if (stopped(deadline, work_count, work_budget)) return false;
            if (moved_index > label.index) continue; // a rear move cannot cover this label
            auto rear = moved_index == label.index ? moved(nodes[label.index], delta) : nodes[label.index];
            auto center = label.center;
            if (moved_index == label.index)
            { center.x += delta.x; center.y += delta.y; }
            label_foreground.assign(fixed.begin(), fixed.end());
            label_foreground.insert(label_foreground.end(),
                windows[label.index].fixed_foreground.begin(),
                windows[label.index].fixed_foreground.end());
            for (size_t k = 0; k < label.index; ++k)
                label_foreground.push_back(k == moved_index ? moved(nodes[k], delta) : nodes[k]);
            auto clearance = checked_clearance(center, rear, screen, label_foreground, deadline,
                work_count, work_budget);
            if (!clearance) return false;
            if (*clearance < label.radius - .01) return true;
        }
        // A feasible branch already wins on rank. If it has no candidate, a replacement
        // branch is allowed and is ranked from the displayed offset below.
        bool wins_tie = false;
        if (best && branch_match != best->branch_match)
            wins_tie = branch_match;
        else if (best && std::abs(distance - best->travel) < .01)
        {
            const bool follows = same_displacement_side(moved_index, delta);
            const bool best_follows = same_displacement_side(best->moved_index, best->delta);
            wins_tie = (follows && !best_follows) || (follows == best_follows &&
                moved_index == index && best->moved_index != index);
        }
        if (!best || (branch_match == best->branch_match && distance < best->travel - .01) || wins_tie)
            best = candidate{moved_index, delta, distance, displayed_distance, label_center,
                clearance, axis, direction, branch_match, branch_base};
        return true;
    };
    auto current_bounds = bounds_for(windows[index], r, screen, diameter,
        allow_minimum_patch_zone_overshoot);
    std::vector<rectangle> other_foreground;
    other_foreground.reserve(static_here.size() + index);
    size_t center_x = std::min_element(xs.begin(), xs.end(), [&] (double a, double b) {
        return std::abs(a - (r.x + r.width / 2)) < std::abs(b - (r.x + r.width / 2));
    }) - xs.begin();
    size_t center_y = std::min_element(ys.begin(), ys.end(), [&] (double a, double b) {
        return std::abs(a - (r.y + r.height / 2)) < std::abs(b - (r.y + r.height / 2));
    }) - ys.begin();
    std::set<std::pair<size_t, size_t>> tried;
    auto inspect = [&] (size_t xi, size_t yi) {
        if (stopped(deadline, work_count, work_budget) || !tried.emplace(xi, yi).second)
            return !stopped(deadline, work_count, work_budget);
        if (!take_inspection(work_count, work_budget)) return false;
        double x = xs[xi], y = ys[yi];
        point center{x, y};
        if (x < screen.x + radius || x > screen.x + screen.width - radius ||
            y < screen.y + radius || y > screen.y + screen.height - radius) return true;
        auto unobscured = checked_clearance(center, screen, screen, obstacles, deadline,
            work_count, work_budget);
        if (!unobscured) return false;
        // First try moving only the covered window. Its foreground stays put.
        if (*unobscured >= radius - .01)
        {
            double xlo = std::max(x + radius - r.x - r.width, current_bounds.xlo - r.x);
            double xhi = std::min(x - radius - r.x, current_bounds.xhi - r.x);
            double ylo = std::max(y + radius - r.y - r.height, current_bounds.ylo - r.y);
            double yhi = std::min(y - radius - r.y, current_bounds.yhi - r.y);
            constrain_zone_delta(windows[index], r, screen, xlo, xhi, ylo, yhi,
                allow_minimum_patch_zone_overshoot);
            if (xlo <= xhi && ylo <= yhi)
            {
                const point own{std::clamp(0.0, xlo, xhi), std::clamp(0.0, ylo, yhi)};
                const point own_target{route_base(index).x + own.x,
                    route_base(index).y + own.y};
                if (!offer(index, scene_delta_for(index, own_target), center, *unobscured))
                    return false;
                // Each candidate is the nearest point on one ray that contains this
                // circle. Among all visible-label candidates, the solver selects the
                // smallest true-position offset on the retained ray. A diagonal move
                // is not a branch: it would have no stable way identity.
                if (std::abs(own.x) > .01 && ylo <= .01 && yhi >= -.01 &&
                    !offer(index, scene_delta_for(index,
                        {route_base(index).x + own.x, route_base(index).y}),
                        center, *unobscured)) return false;
                if (std::abs(own.y) > .01 && xlo <= .01 && xhi >= -.01 &&
                    !offer(index, scene_delta_for(index,
                        {route_base(index).x, route_base(index).y + own.y}),
                        center, *unobscured)) return false;
            }
        }
        // A fully covering foreground surface may have to move to reveal a rear circle.
        auto this_clearance = checked_clearance(center, r, screen, static_here, deadline,
            work_count, work_budget);
        if (!this_clearance) return false;
        if (*this_clearance < radius - .01) return true;
        for (size_t j = 0; j < index; ++j)
        {
            if (stopped(deadline, work_count, work_budget)) return false;
            other_foreground.assign(static_here.begin(), static_here.end());
            for (size_t k = 0; k < index; ++k) if (k != j) other_foreground.push_back(nodes[k]);
            auto open = checked_clearance(center, r, screen, other_foreground, deadline,
                work_count, work_budget);
            if (!open) return false;
            if (*open < radius - .01) continue;
            const auto base = route_base(j);
            auto front = moved(windows[j].frame, base);
            auto limit = bounds_for(windows[j], front, screen, diameter,
                allow_minimum_patch_zone_overshoot);
            std::array<point, 4> deltas{{
                {x + radius + .5 - front.x, 0},
                {x - radius - .5 - front.x - front.width, 0},
                {0, y + radius + .5 - front.y},
                {0, y - radius - .5 - front.y - front.height}}};
            for (point delta : deltas)
            {
                if (stopped(deadline, work_count, work_budget)) return false;
                auto shifted = moved(front, delta);
                if (shifted.x < limit.xlo - .01 || shifted.x > limit.xhi + .01 ||
                    shifted.y < limit.ylo - .01 || shifted.y > limit.yhi + .01 ||
                    !remains_in_avoidance_zone(windows[j], shifted, screen,
                        allow_minimum_patch_zone_overshoot)) continue;
                one_obstacle.assign(1, shifted);
                auto clearance = checked_clearance(center, r, screen, one_obstacle, deadline,
                    work_count, work_budget);
                if (!clearance) return false;
                if (*clearance >= radius - .01 &&
                    !offer(j, scene_delta_for(j, {base.x + delta.x, base.y + delta.y}),
                        center, std::min(*open, *clearance))) return false;
            }
        }
        return true;
    };

    // Estimate the least movement that each visible-label candidate could require.
    auto movement_lower_bound = [&] (double x, double y) {
        std::pair<int, double> lower{2, std::numeric_limits<double>::infinity()};
        if (stopped(deadline, work_count, work_budget)) return lower;
        auto offer_lower = [&] (size_t moved_index, point delta) {
            if (stopped(deadline, work_count, work_budget)) return;
            // A zero-distance lower bound is useful only when the proposed badge
            // center is actually visible after this move. Previously, covered centers
            // inside a window's own frame sorted ahead of every real opening at cost
            // zero; a stack then spent the entire deadline proving those impossible.
            auto lower_foreground = obstacles;
            if (moved_index != index)
                lower_foreground[static_here.size() + moved_index] = moved(nodes[moved_index], delta);
            auto visible = checked_clearance({x, y}, screen, screen, lower_foreground, deadline,
                work_count, work_budget);
            if (!visible || *visible < radius - .01) return;
            const auto final_offset = point{nodes[moved_index].x + delta.x - windows[moved_index].frame.x,
                nodes[moved_index].y + delta.y - windows[moved_index].frame.y};
            const bool branch_match = matches_branch(moved_index, delta);
            const double distance = branch_match ? std::hypot(final_offset.x, final_offset.y) :
                std::hypot(final_offset.x - windows[moved_index].incumbent_offset.x,
                    final_offset.y - windows[moved_index].incumbent_offset.y);
            std::pair<int, double> candidate_lower{branch_match ? 0 : 1, distance};
            if (candidate_lower < lower) lower = candidate_lower;
        };
        if (x < screen.x + radius || x > screen.x + screen.width - radius ||
            y < screen.y + radius || y > screen.y + screen.height - radius) return lower;
        if (!windows[index].anchored)
        {
            double xlo = std::max(x + radius - r.x - r.width, current_bounds.xlo - r.x);
            double xhi = std::min(x - radius - r.x, current_bounds.xhi - r.x);
            double ylo = std::max(y + radius - r.y - r.height, current_bounds.ylo - r.y);
            double yhi = std::min(y - radius - r.y, current_bounds.yhi - r.y);
            constrain_zone_delta(windows[index], r, screen, xlo, xhi, ylo, yhi,
                allow_minimum_patch_zone_overshoot);
            if (xlo <= xhi && ylo <= yhi)
            {
                const point own{std::clamp(0.0, xlo, xhi), std::clamp(0.0, ylo, yhi)};
                const point own_target{route_base(index).x + own.x,
                    route_base(index).y + own.y};
                offer_lower(index, scene_delta_for(index, own_target));
                if (std::abs(own.x) > .01 && ylo <= .01 && yhi >= -.01)
                    offer_lower(index, scene_delta_for(index,
                        {route_base(index).x + own.x, route_base(index).y}));
                if (std::abs(own.y) > .01 && xlo <= .01 && xhi >= -.01)
                    offer_lower(index, scene_delta_for(index,
                        {route_base(index).x, route_base(index).y + own.y}));
            }
        }
        auto owner_clearance = checked_clearance({x, y}, r, screen, static_here, deadline,
            work_count, work_budget);
        const bool owner_can_stay = owner_clearance && *owner_clearance >= radius - .01;
        for (size_t j = 0; owner_can_stay && j < index &&
            !stopped(deadline, work_count, work_budget); ++j)
        {
            if (windows[j].anchored) continue;
            const auto base = route_base(j);
            auto front = moved(windows[j].frame, base);
            auto limit = bounds_for(windows[j], front, screen, diameter,
                allow_minimum_patch_zone_overshoot);
            std::array<point, 4> deltas{{
                {x + radius + .5 - front.x, 0},
                {x - radius - .5 - front.x - front.width, 0},
                {0, y + radius + .5 - front.y},
                {0, y - radius - .5 - front.y - front.height}}};
            for (point delta : deltas)
            {
                auto shifted = moved(front, delta);
                if (shifted.x < limit.xlo - .01 || shifted.x > limit.xhi + .01 ||
                    shifted.y < limit.ylo - .01 || shifted.y > limit.yhi + .01 ||
                    !remains_in_avoidance_zone(windows[j], shifted, screen,
                        allow_minimum_patch_zone_overshoot)) continue;
                offer_lower(j, scene_delta_for(j, {base.x + delta.x, base.y + delta.y}));
            }
        }
        return lower;
    };
    using candidate_entry = std::tuple<int, double, double, size_t, size_t>;
    auto label_distance = [&] (size_t xi, size_t yi) {
        if (!has_spot[index]) return 0.0;
        return std::hypot(xs[xi] - known_spots[index].center.x,
            ys[yi] - known_spots[index].center.y);
    };
    auto add_candidate = [&] (std::vector<candidate_entry>& entries, size_t xi, size_t yi) {
        if (stopped(deadline, work_count, work_budget) || !take_inspection(work_count, work_budget)) return;
        const auto lower = movement_lower_bound(xs[xi], ys[yi]);
        if (!stopped(deadline, work_count, work_budget) && std::isfinite(lower.second))
            entries.emplace_back(lower.first, lower.second, label_distance(xi, yi), xi, yi);
    };
    // Window-move candidates are ordered by travel from the displayed incumbent;
    // opposite screen edges are never privileged by their coordinate or array index.
    std::vector<candidate_entry> coarse;
    for (size_t xi = 0; xi < xs.size() && !stopped(deadline, work_count, work_budget); ++xi)
        add_candidate(coarse, xi, center_y);
    for (size_t yi = 0; yi < ys.size() && !stopped(deadline, work_count, work_budget); ++yi)
        add_candidate(coarse, center_x, yi);
    for (auto xi : {size_t(0), xs.size() - 1})
        for (auto yi : {size_t(0), ys.size() - 1}) add_candidate(coarse, xi, yi);
    std::sort(coarse.begin(), coarse.end());
    for (const auto& [branch_penalty, travel, label_travel, xi, yi] : coarse)
    {
        // The coarse set is ordered by a lower bound on window travel. Once the
        // nearest checked branch has a feasible placement, farther coarse points
        // cannot improve it. Do not build/refine the full x-by-y matrix on every
        // drag tick: that repeated matrix build was consuming the whole frame
        // budget before a useful answer could be committed.
        if (best && (branch_penalty > (best->branch_match ? 0 : 1) ||
            (branch_penalty == (best->branch_match ? 0 : 1) && travel > best->travel + .01)))
            return best;
        (void)label_travel;
        if (!inspect(xi, yi)) return partial_result();
    }
    // Window moves are nearest-travel first. If the coarse pass only found a
    // replacement way, refine the remaining label positions before declaring the
    // retained way impossible. A deadline here leaves the bridge on its current
    // target for this tick; it must never switch ways just because the coarse set
    // missed a same-way opening.

    // Refine over all remaining contact pairs in increasing lower-bound travel. Candidate
    // generation is deadline checked, so a truncated search keeps its checked coarse answer.
    auto later = [] (const candidate_entry& a, const candidate_entry& b) { return a > b; };
    std::priority_queue<candidate_entry, std::vector<candidate_entry>, decltype(later)> refinement(later);
    for (size_t xi = 0; xi < xs.size(); ++xi)
        for (size_t yi = 0; yi < ys.size(); ++yi)
            if (!tried.count({xi, yi}))
            {
                if (stopped(deadline, work_count, work_budget)) return partial_result();
                if (!take_inspection(work_count, work_budget)) return partial_result();
                const auto lower = movement_lower_bound(xs[xi], ys[yi]);
                if (stopped(deadline, work_count, work_budget)) return partial_result();
                if (std::isfinite(lower.second)) refinement.emplace(lower.first, lower.second,
                    label_distance(xi, yi), xi, yi);
            }
    while (!refinement.empty() && !stopped(deadline, work_count, work_budget))
    {
        auto [branch_penalty, travel, label_travel, xi, yi] = refinement.top();
        refinement.pop();
        (void)label_travel;
        // Within a way, remaining candidates have at least this much rest-relative travel.
        // Exhaust that way before considering any branch switch.
        if (best && branch_penalty > (best->branch_match ? 0 : 1)) break;
        if (best && branch_penalty == (best->branch_match ? 0 : 1) && travel > best->travel + .01) break;
        if (!inspect(xi, yi)) return partial_result();
    }
    if (stopped(deadline, work_count, work_budget)) return partial_result();
    return best;
}
}

std::vector<exposure_result> expose_window_hints(const std::vector<exposure_window>& windows,
    rectangle screen, const std::vector<rectangle>& fixed,
    std::chrono::steady_clock::time_point deadline, bool *deadline_hit, exposure_profile *profile,
    exposure_limits limits)
{
    const auto profile_start = std::chrono::steady_clock::now();
    if (deadline_hit) *deadline_hit = false;
    size_t work_count = 0;
    if (limits.finish && deadline != std::chrono::steady_clock::time_point::max())
        deadline -= finish_reserve(windows.size());
    if (profile)
    {
        profile->work_count = 0;
        profile->label_work_count = 0;
        profile->movement_work_count = 0;
        profile->movement_searches = 0;
        profile->truncated_searches = 0;
        profile->last_search_window = 0;
    }
    auto do_clearance = [&] (point center, rectangle window, const std::vector<rectangle>& foreground,
        auto until) { return checked_clearance(center, window, screen, foreground, until,
            work_count, limits.inspection_budget); };
    auto do_label = [&] (rectangle window, const std::vector<rectangle>& foreground,
        double precision, auto until, double sufficient = -1.0) {
        if (!take_inspection(work_count, limits.inspection_budget)) return label_spot{};
        size_t local_inspections = 0;
        const size_t remaining = limits.inspection_budget - work_count;
        auto result = visible_label(window, screen, foreground, precision, until, sufficient,
            remaining, &local_inspections);
        work_count += local_inspections;
        if (profile) profile->label_work_count += local_inspections;
        return result;
    };
    auto active_windows = windows;
    std::vector<double> retained_clearances(windows.size(), -1);
    auto held_result = [&] (size_t i) {
            const auto& window = windows[i];
            auto offset = window.anchored ? point{} : window.target_offset;
            auto center = point{window.frame.x + window.frame.width / 2 + offset.x +
                    window.prior_label_offset.x,
                window.frame.y + window.frame.height / 2 + offset.y +
                    window.prior_label_offset.y};
            double clearance = std::max(0.0, window.prior_clearance);
            if (clearance <= 0)
            {
                center = {window.frame.x + window.frame.width / 2 + offset.x,
                    window.frame.y + window.frame.height / 2 + offset.y};
                clearance = 0;
            }
            const double wanted = std::max(window.wanted, window.minimum);
            const double available = std::floor(std::max(0.0, 2 * (clearance - 1) / pop_scale));
            return exposure_result{offset, {center, clearance},
                std::max(window.minimum, std::min(wanted, available)),
                window.branch_owner, window.branch_axis, window.branch_sign,
                retained_clearances[i], window.branch_base_offset};
    };
    std::vector<rectangle> nodes;
    for (auto w : active_windows)
    {
        // Every layout solve starts from true geometry. Stored offsets describe a
        // branch and deadline fallback only; a drawn transformer never becomes input.
        nodes.push_back(w.frame);
    }
    std::vector<label_spot> best_spots(nodes.size());
    std::vector<bool> has_spot(nodes.size(), false);
    // Keep a cheap checked no-move answer so a deadline still leaves unobscured badges usable.
    for (size_t i = 0; i < nodes.size(); ++i)
    {
        std::vector<rectangle> foreground = fixed;
        foreground.insert(foreground.end(), active_windows[i].fixed_foreground.begin(),
            active_windows[i].fixed_foreground.end());
        for (size_t j = 0; j < i; ++j) foreground.push_back(nodes[j]);
        if (uncovered(nodes[i], screen, foreground))
        {
            // WK31: nothing covers it, so its hint is at its center; a prior label
            // point from when it was covered is not retained.
            best_spots[i] = centered_spot(nodes[i], screen);
            has_spot[i] = true;
            continue;
        }
        point center{nodes[i].x + nodes[i].width / 2, nodes[i].y + nodes[i].height / 2};
        // The stored label point is relative to true geometry: the previous target
        // was subtracted when the bridge recorded it. Rechecking it from the true
        // frame lets this solve recompute the least offset along the retained way;
        // adding the prior target here feeds yesterday's visual transform back into
        // today's solver and can falsely invalidate the way by putting the badge
        // outside the new, undisplaced frame.
        point previous_label{center.x + active_windows[i].prior_label_offset.x,
            center.y + active_windows[i].prior_label_offset.y};
        auto prior_clearance = stopped(deadline, work_count, limits.inspection_budget) ?
            std::optional<double>{} : do_clearance(previous_label, nodes[i], foreground, deadline);
        if (prior_clearance) retained_clearances[i] = *prior_clearance;
        if (prior_clearance && *prior_clearance + .25 >= needed_radius(active_windows[i].minimum))
        {
            best_spots[i] = {previous_label, *prior_clearance};
            has_spot[i] = true;
        } else if (!stopped(deadline, work_count, limits.inspection_budget))
        {
            if (auto clearance = do_clearance(center, nodes[i], foreground, deadline))
            {
                best_spots[i] = {center, *clearance};
                has_spot[i] = true;
            }
        }
    }
    const auto placement_started = std::chrono::steady_clock::now();
    if (profile) profile->initialization_ms = std::chrono::duration<double, std::milli>(
        placement_started - profile_start).count();
    std::vector<bool> hold_targets(nodes.size(), false);
    std::vector<bool> visited(nodes.size(), false);
    std::vector<bool> moved_this_solve(nodes.size(), false);
    bool incomplete = false;
    // A later rear window may require moving a foreground window, so revisit all
    // constraints until each one has room. No move is proposed for an already legible
    // window unless exposing a covered window requires that foreground concession.
    for (size_t pass = 0; pass < 2 * active_windows.size() + 4; ++pass)
    {
        if (stopped(deadline, work_count, limits.inspection_budget)) break;
        bool changed = false;
        for (size_t order = 0; order < nodes.size(); ++order)
        {
            const size_t i = order; // fixed front-to-back order; retries never rotate
            if (hold_targets[i]) continue;
            const double wanted = std::max(active_windows[i].wanted, active_windows[i].minimum);
            if (stopped(deadline, work_count, limits.inspection_budget)) break;
            visited[i] = true;
            // Share remaining time with the windows still waiting in this pass. A single
            // difficult mid-stack window must not consume the whole refresh before a
            // completely covered rear window gets any checked placement.
            auto window_deadline = deadline;
            if (deadline != std::chrono::steady_clock::time_point::max())
            {
                auto now = std::chrono::steady_clock::now();
                window_deadline = now + (deadline - now) / (nodes.size() - order);
            }
            std::vector<rectangle> foreground = fixed;
            foreground.insert(foreground.end(), active_windows[i].fixed_foreground.begin(),
                active_windows[i].fixed_foreground.end());
            for (size_t j = 0; j < i; ++j) foreground.push_back(nodes[j]);
            if (uncovered(nodes[i], screen, foreground))
            {
                // WK31: no search and no avoidance for an uncovered window's own hint.
                // It may still move to expose a window behind it, unless anchored.
                best_spots[i] = centered_spot(nodes[i], screen);
                has_spot[i] = true;
                continue;
            }
            // First look for the widget-size minimum in the current visible region.
            // Preferred-size enlargement is a later, separately bounded movement step;
            // asking visible_label to certify the wanted diameter before that check
            // turns every overlapped window into a full 250 us maximization pass.
            const double solve_diameter = active_windows[i].minimum;
            const bool covered = fully_occluded(nodes[i], foreground);
            const bool incumbent_fits_request = has_spot[i] && best_spots[i].clearance + .25 >=
                needed_radius(solve_diameter);
            auto spot = incumbent_fits_request ? best_spots[i] :
                (covered ? label_spot{{nodes[i].x + nodes[i].width / 2,
                    nodes[i].y + nodes[i].height / 2}, 0} :
                    do_label(nodes[i], foreground, .5, label_deadline(window_deadline),
                        needed_radius(solve_diameter)));
            if (!has_spot[i] || spot.clearance > best_spots[i].clearance)
            { best_spots[i] = spot; has_spot[i] = true; }
            else spot = best_spots[i]; // an expired label refinement cannot eclipse its checked incumbent
            const double fit_request = limits.allow_size_upgrades ? wanted :
                active_windows[i].minimum;
            if (spot.clearance + .25 >= needed_radius(fit_request))
            {
                const bool retained_way = active_windows[i].branch_owner >= 0 &&
                    active_windows[i].branch_axis;
                if (!retained_way || spot.clearance + .25 >=
                    needed_radius(fit_request) + avoidance_way_release_margin) continue;
                // Release only after the true frame clears the current wanted size
                // by a small margin. This keeps a one-pixel clearance fluctuation
                // from toggling a window between its retained way and zero.
                hold_targets[i] = true;
                if (!moved_this_solve[i]) nodes[i] = moved(windows[i].frame,
                    windows[i].anchored ? point{} : windows[i].target_offset);
                continue;
            }
            if (spot.clearance + .25 >= needed_radius(active_windows[i].minimum) &&
                !limits.allow_size_upgrades) continue;
            std::optional<candidate> choice;
            bool movement_search_truncated = false;
            auto find_move = [&] (double diameter, bool allow_zone_overshoot = false) {
                auto run_search = [&] (bool replacement_probe) {
                    bool this_search_truncated = false;
                    const auto work_before = work_count;
                    auto result = least_exposure_move(i, diameter, nodes, active_windows, screen, fixed,
                        window_deadline, best_spots, has_spot, work_count,
                        limits.inspection_budget, &this_search_truncated, allow_zone_overshoot,
                        replacement_probe);
                    if (profile)
                    {
                        ++profile->movement_searches;
                        profile->movement_work_count += work_count - work_before;
                        profile->truncated_searches += this_search_truncated;
                        profile->last_search_window = i;
                    }
                    movement_search_truncated |= this_search_truncated;
                    return result;
                };
                auto found = run_search(false);
                const bool has_retained_way = std::any_of(active_windows.begin(), active_windows.end(),
                    [] (const auto& window) { return window.branch_owner >= 0 && window.branch_axis; });
                if (has_retained_way && (!found || !found->branch_match) &&
                    !stopped(window_deadline, work_count, limits.inspection_budget))
                {
                    auto nearby = run_search(true);
                    if (nearby && (!found || (nearby->branch_match && !found->branch_match) ||
                        (nearby->branch_match == found->branch_match &&
                            nearby->travel < found->travel - .01))) found = nearby;
                }
                return found;
            };
            const bool minimum_fits_here = spot.clearance + .25 >=
                needed_radius(active_windows[i].minimum);
            if (!minimum_fits_here)
            {
                // First solve only for the visible minimum. Looking for the largest
                // free strip before moving a covered window spent several thousand
                // label inspections and could starve every solve. A nearby size upgrade
                // is considered after the minimum badge has a checked home.
                choice = find_move(active_windows[i].minimum);
            } else if (limits.allow_size_upgrades &&
                spot.clearance + .25 < needed_radius(wanted))
            {
                choice = find_move(wanted);
            }
            if (!choice)
            {
                if (!movement_search_truncated && !minimum_fits_here &&
                    limits.allow_minimum_patch_zone_overshoot)
                {
                    // This is the only P1/P12 policy seam. First exhaust legal
                    // placements inside the window's own zone; only then, if the
                    // configured policy permits it, find the nearest minimum badge
                    // just beyond that bound. Default P1 keeps this path disabled.
                    choice = find_move(active_windows[i].minimum, true);
                }
                if (!choice && movement_search_truncated)
                {
                    if (!minimum_fits_here)
                    {
                        hold_targets[i] = true;
                        if (!moved_this_solve[i])
                            nodes[i] = moved(windows[i].frame, windows[i].anchored ? point{} :
                                windows[i].target_offset);
                        incomplete = true;
                    }
                    continue; // keep the checked incumbent for this tick
                }
                if (!choice && !minimum_fits_here)
                {
                    // If this changed layout has no checked legal move, do not
                    // reinterpret the true frame as a new zero target. Hold the
                    // displayed route for this frame and keep the minimum hint.
                    hold_targets[i] = true;
                    if (!moved_this_solve[i])
                        nodes[i] = moved(windows[i].frame, windows[i].anchored ? point{} :
                            windows[i].target_offset);
                    continue;
                }
                if (!choice && !limits.allow_size_upgrades)
                    continue; // no larger-size request

                // If the wanted circle cannot fit, refine upward from the least-travel
                // readable placement while budget remains.
                if (!choice)
                {
                    double lo = active_windows[i].minimum, hi = wanted;
                    if (!stopped(window_deadline, work_count, limits.inspection_budget))
                        choice = find_move(lo);
                    if (!choice)
                    {
                        if (movement_search_truncated && !minimum_fits_here)
                        {
                            hold_targets[i] = true;
                            if (!moved_this_solve[i])
                                nodes[i] = moved(windows[i].frame, windows[i].anchored ? point{} :
                                    windows[i].target_offset);
                            incomplete = true;
                        }
                        continue;
                    }
                    while (hi - lo > 1)
                    {
                        if (stopped(window_deadline, work_count, limits.inspection_budget)) break;
                        double mid = std::floor((lo + hi) / 2);
                        if (auto possible = find_move(mid))
                        { lo = mid; choice = possible; }
                        else hi = mid;
                    }
                }
            }
            auto readable_diameter = [&] (double clearance) {
                return std::max(active_windows[i].minimum, std::min(wanted,
                    std::floor(std::max(0.0, 2 * (clearance - 1) / pop_scale))));
            };
            // A way is a one-axis ray from the window's true frame. Candidate
            // generation finds a checked point on that ray; tighten it toward zero
            // before committing. This is what lets an old concession shrink as its
            // obstruction moves away instead of ratcheting at the previous target.
            // The fixed iteration count makes the extra work deterministic.
            const bool incumbent_fits_minimum = spot.clearance + .25 >=
                needed_radius(active_windows[i].minimum);
            const double movement_diameter = incumbent_fits_minimum ? wanted :
                active_windows[i].minimum;
            bool ray_search_truncated = false;
            auto minimize_on_way = [&] (candidate& selected) {
                const size_t moved_index = selected.moved_index;
                if (selected.branch_axis != 1 && selected.branch_axis != 2) return;
                const auto& moved_window = active_windows[moved_index];
                const point selected_offset{
                    nodes[moved_index].x + selected.delta.x - moved_window.frame.x,
                    nodes[moved_index].y + selected.delta.y - moved_window.frame.y};
                const point base = selected.branch_base_offset;
                const point active_leg{selected_offset.x - base.x, selected_offset.y - base.y};
                const double endpoint = selected.branch_axis == 1 ? active_leg.x : active_leg.y;
                if (std::abs(endpoint) >= .01 &&
                    (endpoint < 0 ? -1 : 1) != selected.branch_sign) return;
                const double limit = std::abs(endpoint);
                const double radius = needed_radius(movement_diameter);
                const auto owner_true = active_windows[i].frame;
                const point owner_base = active_windows[i].branch_owner == int(i) &&
                    active_windows[i].branch_axis ? active_windows[i].branch_base_offset :
                    active_windows[i].incumbent_offset;
                const auto selected_owner = moved_index == i ? moved(nodes[i], selected.delta) :
                    moved(active_windows[i].frame, owner_base);
                const point label_relative{selected.label_center.x -
                        (selected_owner.x + selected_owner.width / 2),
                    selected.label_center.y - (selected_owner.y + selected_owner.height / 2)};
                auto target_at = [&] (double base_fraction, double amount) {
                    point target_offset{base.x * base_fraction, base.y * base_fraction};
                    if (selected.branch_axis == 1)
                        target_offset.x += selected.branch_sign * amount;
                    else target_offset.y += selected.branch_sign * amount;
                    return target_offset;
                };
                auto placement_at = [&] (double base_fraction, double amount,
                    double *clearance_out) -> std::optional<bool> {
                    if (stopped(window_deadline, work_count, limits.inspection_budget))
                    { ray_search_truncated = true; return {}; }
                    const point target_offset = target_at(base_fraction, amount);
                    const auto moved_frame = moved(moved_window.frame, target_offset);
                    const auto owner_frame = moved_index == i ? moved_frame :
                        moved(active_windows[i].frame, owner_base);
                    point center = selected.label_center;
                    if (moved_index == i)
                    {
                        center = {owner_frame.x + owner_frame.width / 2 + label_relative.x,
                            owner_frame.y + owner_frame.height / 2 + label_relative.y};
                    }
                    std::vector<rectangle> foreground = fixed;
                    foreground.insert(foreground.end(), active_windows[i].fixed_foreground.begin(),
                        active_windows[i].fixed_foreground.end());
                    for (size_t k = 0; k < i; ++k)
                        foreground.push_back(k == moved_index ? moved_frame : nodes[k]);
                    auto clearance = do_clearance(center, owner_frame, foreground, window_deadline);
                    if (!clearance) { ray_search_truncated = true; return {}; }
                    if (clearance_out) *clearance_out = *clearance;
                    return *clearance + .25 >= radius;
                };
                // A closer offset is useful only if it keeps the other visible hints
                // already protected by the candidate. Check those labels at each ray
                // sample too, so minimizing this way cannot steal their clearance.
                auto preserves_labels = [&] (double base_fraction, double amount) -> std::optional<bool> {
                    if (moved_index >= nodes.size()) return false;
                    const point target_offset = target_at(base_fraction, amount);
                    const auto shifted = moved(active_windows[moved_index].frame, target_offset);
                    const point moved_label_relative{
                        best_spots[moved_index].center.x -
                            (nodes[moved_index].x + nodes[moved_index].width / 2),
                        best_spots[moved_index].center.y -
                            (nodes[moved_index].y + nodes[moved_index].height / 2)};
                    for (size_t k = moved_index; k < nodes.size(); ++k)
                    {
                        if (k == i) continue; // placement_at checks the beneficiary label itself
                        if (!has_spot[k] || best_spots[k].clearance + .25 <
                            needed_radius(active_windows[k].minimum)) continue;
                        if (stopped(window_deadline, work_count, limits.inspection_budget))
                        { ray_search_truncated = true; return {}; }
                        const auto rear = k == moved_index ? shifted : nodes[k];
                        const auto center = k == moved_index ? point{
                            shifted.x + shifted.width / 2 + moved_label_relative.x,
                            shifted.y + shifted.height / 2 + moved_label_relative.y} :
                            best_spots[k].center;
                        std::vector<rectangle> foreground = fixed;
                        foreground.insert(foreground.end(), active_windows[k].fixed_foreground.begin(),
                            active_windows[k].fixed_foreground.end());
                        for (size_t f = 0; f < k; ++f)
                            foreground.push_back(f == moved_index ? shifted : nodes[f]);
                        const double required = std::min(best_spots[k].clearance - .25,
                            needed_radius(std::max(active_windows[k].wanted,
                                active_windows[k].minimum)));
                        auto clearance = do_clearance(center, rear, foreground, window_deadline);
                        if (!clearance) { ray_search_truncated = true; return {}; }
                        if (*clearance + .01 < required) return false;
                    }
                    return true;
                };
                double endpoint_clearance = 0;
                auto endpoint_fits = placement_at(1, limit, &endpoint_clearance);
                if (!endpoint_fits || !*endpoint_fits) return;
                auto endpoint_preserves = preserves_labels(1, limit);
                if (!endpoint_preserves || !*endpoint_preserves) return;
                double zero_clearance = 0;
                auto zero_fits = placement_at(1, 0, &zero_clearance);
                if (!zero_fits) return;
                bool zero_works = *zero_fits;
                if (zero_works)
                {
                    auto zero_preserves = preserves_labels(1, 0);
                    if (!zero_preserves) return;
                    zero_works = *zero_preserves;
                }
                if (zero_works)
                {
                    double base_low = 0, base_high = 1;
                    double base_clearance = zero_clearance;
                    double rest_clearance = 0;
                    auto rest_fits = placement_at(0, 0, &rest_clearance);
                    if (!rest_fits) return;
                    bool rest_works = *rest_fits;
                    if (rest_works)
                    {
                        auto rest_preserves = preserves_labels(0, 0);
                        if (!rest_preserves) return;
                        rest_works = *rest_preserves;
                    }
                    if (rest_works)
                    {
                        base_high = 0;
                        base_clearance = rest_clearance;
                    } else
                    {
                        for (int probe = 0; probe < 8 && base_high - base_low > .01; ++probe)
                        {
                            const double middle = (base_low + base_high) / 2;
                            double clearance = 0;
                            auto fits = placement_at(middle, 0, &clearance);
                            if (!fits) return;
                            bool works = *fits;
                            if (works)
                            {
                                auto preserves = preserves_labels(middle, 0);
                                if (!preserves) return;
                                works = *preserves;
                            }
                            if (works) { base_high = middle; base_clearance = clearance; }
                            else base_low = middle;
                        }
                    }
                    const auto final_offset = target_at(base_high, 0);
                    selected.delta = {active_windows[moved_index].frame.x + final_offset.x - nodes[moved_index].x,
                        active_windows[moved_index].frame.y + final_offset.y - nodes[moved_index].y};
                    selected.label_center = moved_index == i ? point{
                        owner_true.x + owner_true.width / 2 + label_relative.x,
                        owner_true.y + owner_true.height / 2 + label_relative.y} : selected.label_center;
                    selected.clearance = base_clearance;
                    selected.branch_base_offset = final_offset;
                    selected.travel = selected.branch_match ? std::hypot(final_offset.x, final_offset.y) :
                        std::hypot(final_offset.x - moved_window.incumbent_offset.x,
                            final_offset.y - moved_window.incumbent_offset.y);
                    selected.displayed_travel = std::hypot(final_offset.x - moved_window.incumbent_offset.x,
                        final_offset.y - moved_window.incumbent_offset.y);
                    return;
                }
                // Zero is the failing lower bound when any part of this way is still
                // needed. Search inward from the checked endpoint so the chosen target
                // is the smallest true-geometry offset that keeps this hint and every
                // already protected hint readable. This also lets a window glide back
                // continuously as the obstruction recedes instead of holding a large
                // endpoint and snapping home only after the obstruction fully clears.
                double low = 0, high = limit, high_clearance = endpoint_clearance;
                for (int probe = 0; probe < 8 && high - low > .5; ++probe)
                {
                    const double middle = (low + high) / 2;
                    double clearance = 0;
                    auto fits = placement_at(1, middle, &clearance);
                    if (!fits) return; // the already checked endpoint remains the safe result
                    bool works = *fits;
                    if (works)
                    {
                        auto preserves = preserves_labels(1, middle);
                        if (!preserves) return;
                        works = *preserves;
                    }
                    if (works)
                    { high = middle; high_clearance = clearance; }
                    else low = middle;
                }
                const point final_offset = target_at(1, high);
                selected.delta = {active_windows[moved_index].frame.x + final_offset.x - nodes[moved_index].x,
                    active_windows[moved_index].frame.y + final_offset.y - nodes[moved_index].y};
                selected.clearance = high_clearance;
                selected.travel = selected.branch_match ? std::hypot(final_offset.x, final_offset.y) :
                    std::hypot(final_offset.x - moved_window.incumbent_offset.x,
                        final_offset.y - moved_window.incumbent_offset.y);
                selected.displayed_travel = std::hypot(
                    final_offset.x - moved_window.incumbent_offset.x,
                    final_offset.y - moved_window.incumbent_offset.y);
            };
            if (!movement_search_truncated)
            {
                const size_t ray_work_before = work_count;
                minimize_on_way(*choice);
                if (profile) profile->movement_work_count += work_count - ray_work_before;
                if (ray_search_truncated)
                {
                    movement_search_truncated = true;
                    if (profile) ++profile->truncated_searches;
                }
            }
            const bool candidate_fits_wanted = choice->clearance + .25 >= needed_radius(wanted);
            if (movement_search_truncated && (!choice ||
                choice->clearance + .25 < needed_radius(active_windows[i].minimum)))
            {
                if (!incumbent_fits_minimum)
                {
                    hold_targets[i] = true;
                    if (!moved_this_solve[i])
                        nodes[i] = moved(windows[i].frame, windows[i].anchored ? point{} :
                            windows[i].target_offset);
                    incomplete = true;
                }
                continue;
            }
            // A wanted-size improvement is opportunistic: it may move at most one badge
            // radius and is deferred while the user is moving a window or coasting.
            if (incumbent_fits_minimum && limits.allow_size_upgrades &&
                choice->displayed_travel > std::max(12.0, wanted * .2))
                continue;
            if (incumbent_fits_minimum && !candidate_fits_wanted &&
                readable_diameter(choice->clearance) < readable_diameter(spot.clearance) + 12)
                continue;
            if (std::hypot(choice->delta.x, choice->delta.y) < .01)
            {
                if (choice->moved_index == i)
                {
                    best_spots[i] = {choice->label_center, choice->clearance};
                    has_spot[i] = true;
                }
                continue;
            }
            nodes[choice->moved_index] = moved(nodes[choice->moved_index], choice->delta);
            moved_this_solve[choice->moved_index] = true;
            active_windows[choice->moved_index].target_offset = {
                nodes[choice->moved_index].x - active_windows[choice->moved_index].frame.x,
                nodes[choice->moved_index].y - active_windows[choice->moved_index].frame.y};
            active_windows[choice->moved_index].branch_owner = int(i);
            active_windows[choice->moved_index].branch_axis = choice->branch_axis;
            active_windows[choice->moved_index].branch_sign = choice->branch_sign;
            active_windows[choice->moved_index].branch_base_offset = choice->branch_base_offset;
            if (choice->moved_index != i && has_spot[choice->moved_index])
            {
                best_spots[choice->moved_index].center.x += choice->delta.x;
                best_spots[choice->moved_index].center.y += choice->delta.y;
            }
            for (size_t rear = choice->moved_index + 1; rear < has_spot.size(); ++rear)
            {
                // offer() checked that every readable rear label survived this move.
                // Keep those checked incumbents instead of forcing a fresh, potentially
                // deadline-truncated visible_label search later in the same solve.
                if (!has_spot[rear] || best_spots[rear].clearance + .25 <
                    needed_radius(active_windows[rear].minimum)) has_spot[rear] = false;
            }
            best_spots[i] = {choice->label_center, choice->clearance};
            has_spot[i] = true;
            changed = true;
            if (stopped(deadline, work_count, limits.inspection_budget)) break;
        }
        if (!changed) break;
        if (stopped(deadline, work_count, limits.inspection_budget)) break;
    }
    const auto finalization_started = std::chrono::steady_clock::now();
    if (profile) profile->placement_ms = std::chrono::duration<double, std::milli>(
        finalization_started - placement_started).count();
    for (size_t i = 0; i < nodes.size(); ++i)
    {
        if (!visited[i])
        {
            hold_targets[i] = true;
            if (!moved_this_solve[i])
                nodes[i] = moved(windows[i].frame, windows[i].anchored ? point{} :
                    windows[i].target_offset);
            incomplete = true;
        }
    }
    std::vector<exposure_result> result;
    for (size_t i = 0; i < nodes.size(); ++i)
    {
        const double wanted = std::max(active_windows[i].wanted, active_windows[i].minimum);
        label_spot spot;
        if (has_spot[i]) spot = best_spots[i];
        else
        {
            // The search budget may expire before reaching a later window. Its hint
            // remains at the true target center until its own checked placement runs.
            spot = {{nodes[i].x + nodes[i].width / 2,
                nodes[i].y + nodes[i].height / 2}, 0};
        }
        // A non-positive clearance means there is no point in the window's visible
        // region at all (for example, an output-sized foreground window covers it).
        // Treat that as no spot and anchor the always-visible minimum hint to the
        // window center instead of a search boundary just outside the covered area.
        if (spot.clearance <= 0)
        {
            spot.center = {nodes[i].x + nodes[i].width / 2, nodes[i].y + nodes[i].height / 2};
            spot.clearance = 0;
        }
        best_spots[i] = spot;
        double available = std::floor(std::max(0.0, 2 * (spot.clearance - 1) / pop_scale));
        // Attention remains present even when no unobscured circle can fit. Use the
        // best checked label center (or the window center if the deadline found none).
        double diameter = std::max(active_windows[i].minimum, std::min(wanted, available));
        point offset{nodes[i].x - active_windows[i].frame.x,
            nodes[i].y - active_windows[i].frame.y};
        if (spot.clearance <= 0 && !moved_this_solve[i] && !active_windows[i].anchored)
        {
            // No legal patch was found in this zone on this layout. Keep the last
            // checked visual position for this frame and show the minimum hint there;
            // falling back to zero would make a retained way snap across the window.
            offset = active_windows[i].target_offset;
            spot.center = {active_windows[i].frame.x + active_windows[i].frame.width / 2 + offset.x,
                active_windows[i].frame.y + active_windows[i].frame.height / 2 + offset.y};
        }
        int owner = active_windows[i].branch_owner;
        // Keep the selected way dormant at zero. It has no geometric effect, but if
        // the same obstruction returns during a drag it resumes on that way instead
        // of counting as a new, potentially discontinuous choice.
        result.push_back({offset, spot, diameter, owner,
            owner < 0 ? 0 : active_windows[i].branch_axis,
            owner < 0 ? 0 : active_windows[i].branch_sign,
            -1, active_windows[i].branch_base_offset});
        result.back().retained_clearance = retained_clearances[i];
    }
    for (size_t i = 0; i < result.size(); ++i)
        if (hold_targets[i] && !moved_this_solve[i])
        {
            result[i] = held_result(i);
            result[i].held = true;
        }
    if (limits.finish)
    {
        size_t finish_work = 0;
        finish_hints(windows, screen, fixed, result, finish_work);
        if (profile) profile->finish_work_count = finish_work;
    }
    if (profile) profile->finalization_ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - finalization_started).count();
    if (profile) profile->work_count = work_count;
    if (limits.inspection_count) *limits.inspection_count = work_count;
    // A truncated minimum search needs a stable retry. An optional size upgrade
    // that ran out of time does not: the checked minimum placement is complete.
    if (deadline_hit) *deadline_hit = incomplete;
    return result;
}

bool expose_window_hints_progressively(const std::vector<exposure_window>& windows,
    rectangle screen, const std::vector<rectangle>& fixed, exposure_progress& progress,
    std::chrono::steady_clock::time_point deadline, bool *deadline_hit,
    exposure_profile *profile, exposure_limits limits)
{
    if (deadline_hit) *deadline_hit = false;
    if (progress.results.size() != windows.size() || progress.has_result.size() != windows.size() ||
        progress.complete.size() != windows.size() || progress.attempts.size() != windows.size())
    {
        progress = {};
        progress.results.resize(windows.size());
        progress.has_result.resize(windows.size(), false);
        progress.complete.resize(windows.size(), false);
        progress.attempts.resize(windows.size(), 0);
    }
    if (profile) *profile = {};
    // Each call runs finish_hints exactly once, over the results it publishes; its time is
    // reserved out of this call's deadline.
    auto inner_limits = limits;
    inner_limits.finish = false;
    const auto inner_deadline = deadline == std::chrono::steady_clock::time_point::max() ?
        deadline : deadline - finish_reserve(windows.size());
    auto finish = [&] (std::vector<exposure_result>& results) {
        size_t finish_work = 0;
        finish_hints(windows, screen, fixed, results, finish_work);
        if (profile) profile->finish_work_count = finish_work;
    };
    auto all_complete = [&] {
        return std::all_of(progress.complete.begin(), progress.complete.end(), [] (bool value) { return value; });
    };
    auto has_active_way = [&] {
        for (size_t i = 0; i < progress.results.size(); ++i)
            if (progress.has_result[i] && progress.results[i].branch_axis &&
                std::hypot(progress.results[i].offset.x, progress.results[i].offset.y) > .01)
                return true;
        return false;
    };
    auto recheck_from_rest = [&] {
        progress.way_recheck_pending = false;
        progress.way_recheck_done = true; // one bounded reconsideration per settled layout
        ++progress.way_recheck_attempts;
        auto fresh_windows = windows;
        for (auto& window : fresh_windows)
        {
            window.incumbent_offset = {};
            window.target_offset = {};
            window.prior_label_offset = {};
            window.prior_clearance = 0;
            window.branch_owner = -1;
            window.branch_axis = window.branch_sign = 0;
            window.branch_base_offset = {};
        }
        exposure_profile fresh_profile;
        size_t work = 0;
        auto fresh_limits = inner_limits;
        fresh_limits.inspection_count = &work;
        bool truncated = false;
        auto fresh = expose_window_hints(fresh_windows, screen, fixed, inner_deadline,
            &truncated, &fresh_profile, fresh_limits);
        if (profile) *profile = fresh_profile;
        if (fresh.size() != windows.size() || truncated)
        {
            ++progress.way_recheck_rejections;
            if (deadline_hit) *deadline_hit = true;
            if (limits.inspection_count) *limits.inspection_count = work;
            return true;
        }

        auto proposal = progress.results;
        std::vector<std::pair<double, size_t>> replacements;
        for (size_t i = 0; i < fresh.size(); ++i)
        {
            const double minimum_clearance = needed_radius(windows[i].minimum);
            const bool old_visible = progress.has_result[i] &&
                progress.results[i].spot.clearance + .25 >= minimum_clearance;
            const bool fresh_visible = fresh[i].spot.clearance + .25 >= minimum_clearance;
            if (!fresh_visible) continue;
            if (!old_visible)
            {
                replacements.emplace_back(std::numeric_limits<double>::infinity(), i);
                continue;
            }
            const double old_travel = std::hypot(progress.results[i].offset.x,
                progress.results[i].offset.y);
            const double fresh_travel = std::hypot(fresh[i].offset.x, fresh[i].offset.y);
            const double improvement = old_travel - fresh_travel;
            // A calm way change at rest must save at least one badge and halve
            // that window's remaining displacement. Similar alternatives keep the
            // existing direction, avoiding a gratuitous flip on an equal-cost tie.
            if (improvement + .01 >= std::max(windows[i].minimum, old_travel * .5))
                replacements.emplace_back(improvement, i);
        }
        std::sort(replacements.begin(), replacements.end(), std::greater<>());
        bool adopted = false;
        for (const auto& [improvement, index] : replacements)
        {
            (void)improvement;
            auto candidate = proposal;
            candidate[index] = fresh[index];
            bool proposal_valid = true;
            for (size_t i = 0; i < candidate.size(); ++i)
            {
                if (candidate[i].spot.clearance + .25 < needed_radius(windows[i].minimum))
                    continue; // preserve an isolated P1 no-room result
                if (expired(deadline) || work >= limits.inspection_budget)
                { proposal_valid = false; break; }
                std::vector<rectangle> foreground = fixed;
                foreground.insert(foreground.end(), windows[i].fixed_foreground.begin(),
                    windows[i].fixed_foreground.end());
                for (size_t j = 0; j < i; ++j)
                    foreground.push_back(moved(windows[j].frame, candidate[j].offset));
                const auto frame = moved(windows[i].frame, candidate[i].offset);
                auto clearance = checked_clearance(candidate[i].spot.center, frame, screen,
                    foreground, deadline, work, limits.inspection_budget);
                if (!clearance || *clearance + .25 < needed_radius(windows[i].minimum))
                { proposal_valid = false; break; }
                candidate[i].spot.clearance = *clearance;
            }
            if (proposal_valid)
            { proposal = std::move(candidate); adopted = true; }
            else ++progress.way_recheck_rejections;
        }
        if (!adopted)
        {
            // Some necessary route changes are only valid as a group: changing one
            // window at a time can temporarily steal another's patch. Accept the
            // fresh global solution only when every previously visible window still
            // has a patch, no window's travel grows, and the whole layout saves at
            // least one minimum badge of movement. Easing handles the resulting glide.
            double old_total = 0, fresh_total = 0, minimum_badge = 0;
            bool no_growth = true;
            for (size_t i = 0; i < fresh.size(); ++i)
            {
                const double required = needed_radius(windows[i].minimum);
                const bool old_visible = progress.has_result[i] &&
                    progress.results[i].spot.clearance + .25 >= required;
                const bool fresh_visible = fresh[i].spot.clearance + .25 >= required;
                if (!old_visible || !fresh_visible) { no_growth = false; break; }
                const double old_travel = std::hypot(progress.results[i].offset.x,
                    progress.results[i].offset.y);
                const double fresh_travel = std::hypot(fresh[i].offset.x, fresh[i].offset.y);
                if (fresh_travel > old_travel + .01) { no_growth = false; break; }
                old_total += old_travel;
                fresh_total += fresh_travel;
                minimum_badge = std::max(minimum_badge, windows[i].minimum);
            }
            if (no_growth && old_total - fresh_total >= minimum_badge)
            {
                proposal = fresh;
                adopted = true;
            }
        }
        if (!adopted && replacements.empty()) ++progress.way_recheck_rejections;
        if (adopted) ++progress.way_recheck_adoptions;
        // Adopting one window's fresh way can uncover or collide with another window.
        finish(proposal);
        progress.results = std::move(proposal);
        if (profile) profile->work_count = work;
        if (limits.inspection_count) *limits.inspection_count = work;
        return true;
    };
    if (all_complete())
    {
        progress.next_window = windows.size();
        if (limits.reconsider_ways_when_idle && !progress.way_recheck_done && has_active_way())
        {
            if (progress.way_recheck_pending) return recheck_from_rest();
            progress.way_recheck_pending = true;
            return false;
        }
        if (limits.inspection_count) *limits.inspection_count = 0;
        return true;
    }
    // Carry every validated result into the next slice, but re-run the shared
    // layout solve over the full stack. A per-window slice that treats all prior
    // results as immovable foreground can strand a rear window even when moving a
    // checked foreground window a little would expose its minimum patch.
    auto working = windows;
    for (size_t i = 0; i < working.size(); ++i)
    {
        if (!progress.has_result[i]) continue;
        auto& current = working[i];
        const auto& previous = progress.results[i];
        current.target_offset = previous.offset;
        current.branch_owner = previous.branch_owner;
        current.branch_axis = previous.branch_axis;
        current.branch_sign = previous.branch_sign;
        current.branch_base_offset = previous.branch_base_offset;
        current.prior_label_offset = {
            previous.spot.center.x - current.frame.x - current.frame.width / 2 - previous.offset.x,
            previous.spot.center.y - current.frame.y - current.frame.height / 2 - previous.offset.y};
        current.prior_clearance = previous.spot.clearance;
    }

    bool truncated = false;
    auto results = expose_window_hints(working, screen, fixed, inner_deadline,
        &truncated, profile, inner_limits);
    if (results.size() != windows.size())
    {
        if (deadline_hit) *deadline_hit = true;
        return false;
    }
    // Finish before deciding completion, so a held label that is now buried counts as not
    // visible and its window gets its retry. A result retained from an earlier slice of this
    // same layout was finished in that slice.
    finish(results);

    progress.next_window = windows.size();
    for (size_t i = 0; i < results.size(); ++i)
    {
        const auto& current = working[i];
        const auto& value = results[i];
        const bool minimum_visible = value.spot.clearance + .25 >=
            needed_radius(current.minimum);
        auto better_fallback = [&] (const exposure_result& candidate,
            const exposure_result& incumbent) {
            if (candidate.spot.clearance > incumbent.spot.clearance + .01) return true;
            if (incumbent.spot.clearance > candidate.spot.clearance + .01) return false;
            const double candidate_travel = std::hypot(
                candidate.offset.x - windows[i].incumbent_offset.x,
                candidate.offset.y - windows[i].incumbent_offset.y);
            const double incumbent_travel = std::hypot(
                incumbent.offset.x - windows[i].incumbent_offset.x,
                incumbent.offset.y - windows[i].incumbent_offset.y);
            return candidate_travel < incumbent_travel - .01;
        };
        if (minimum_visible)
        {
            progress.results[i] = value;
            progress.has_result[i] = true;
            progress.complete[i] = true;
            continue;
        }
        if (progress.complete[i]) continue; // retain its last validated/no-room result
        if (!progress.has_result[i] || better_fallback(value, progress.results[i]))
        {
            progress.results[i] = value;
            progress.has_result[i] = true;
        }
        if (truncated && progress.attempts[i] + 1 < avoidance_attempts_per_window)
        {
            ++progress.attempts[i];
            progress.complete[i] = false;
            progress.next_window = std::min(progress.next_window, i);
            continue;
        }
        // The only non-visible completion is a fully searched no-room case. This
        // is the isolated P1/P12 policy seam: the default preserves the zone; when
        // Mike chooses P12, the caller enables the minimum overshoot path above.
        progress.complete[i] = true;
        ++progress.fallback_count;
    }
    for (size_t i = 0; i < progress.complete.size(); ++i)
    {
        if (!progress.complete[i])
        {
            progress.next_window = std::min(progress.next_window, i);
        }
    }
    if (deadline_hit) *deadline_hit = truncated;
    const bool complete = progress.next_window >= windows.size();
    if (complete && limits.reconsider_ways_when_idle && !progress.way_recheck_done &&
        has_active_way())
    {
        progress.way_recheck_pending = true;
        return false;
    }
    return complete;
}

std::vector<point> declutter(const std::vector<point>& anchors, rectangle bounds, double gap,
    const std::vector<double>& diameters, const std::vector<hint_constraint>& constraints)
{
    auto nodes = anchors;
    if (gap <= 0) return nodes;
    auto radius = [&] (size_t i) { return i < diameters.size() ? diameters[i] / 2 : 0; };
    auto vertical = [&] (size_t i) { return i < constraints.size() && constraints[i].vertical_only; };
    auto anchored = [&] (size_t i) { return i < constraints.size() && constraints[i].anchored; };
    auto separation = [&] (size_t i, size_t j) { return gap + radius(i) + radius(j); };
    auto constrain = [&] (point p, size_t i) {
        if (anchored(i)) return anchors[i];
        double height = i < constraints.size() ? constraints[i].half_height : 0;
        double x = std::min(radius(i), bounds.width / 2), y = std::min(std::max(radius(i), height), bounds.height / 2);
        if (vertical(i)) p.x = anchors[i].x;
        return point{std::clamp(p.x, bounds.x + x, bounds.x + bounds.width - x),
            std::clamp(p.y, bounds.y + y, bounds.y + bounds.height - y)}; };
    for (size_t i = 0; i < nodes.size(); ++i) nodes[i] = constrain(nodes[i], i);
    for (int step = 0; step < 500; ++step)
    {
        std::vector<point> force(nodes.size());
        for (size_t i = 0; i < nodes.size(); ++i)
        {
            force[i] = {(anchors[i].x - nodes[i].x) * 0.025, (anchors[i].y - nodes[i].y) * 0.025};
            for (size_t j = 0; j < i; ++j)
            {
                double dx = nodes[i].x - nodes[j].x, dy = nodes[i].y - nodes[j].y;
                double distance = std::hypot(dx, dy);
                double gap = separation(i, j);
                if (distance >= gap) continue;
                if (distance < 1e-6)
                { double angle = (i * 17 + j * 31) * 2.399963229728653;
                    dx = std::cos(angle); dy = std::sin(angle); distance = 1; }
                double strength = (gap + 2 - distance) * 0.55 / distance;
                force[i].x += dx * strength; force[i].y += dy * strength;
                force[j].x -= dx * strength; force[j].y -= dy * strength;
            }
        }
        double motion = 0;
        for (size_t i = 0; i < nodes.size(); ++i)
        {
            double len = std::hypot(force[i].x, force[i].y);
            double cap = len > 12 ? 12 / len : 1;
            auto next = constrain({nodes[i].x + force[i].x * cap, nodes[i].y + force[i].y * cap}, i);
            motion = std::max(motion, std::hypot(next.x - nodes[i].x, next.y - nodes[i].y));
            nodes[i] = next;
        }
        if (motion < 0.001) break;
    }
    // Springs choose the smallest useful displacement; hard collision projection then removes
    // their small equilibrium penetration. No effect on already separate centers.
    for (int step = 0; step < 400; ++step)
    {
        bool collision = false;
        for (size_t i = 0; i < nodes.size(); ++i) for (size_t j = 0; j < i; ++j)
        {
            double dx = nodes[i].x - nodes[j].x, dy = nodes[i].y - nodes[j].y;
            double distance = std::hypot(dx, dy);
            double gap = separation(i, j);
            if (distance >= gap - 0.001) continue;
            collision = true;
            if (vertical(i) || vertical(j))
            {
                // Resolve residual collisions involving a rail vertically, preserving its
                // attachment even when a neighboring window is pinned to a screen edge.
                // Springs already let free windows move on both axes. Input order breaks ties.
                double required = std::sqrt(std::max(0.0, (gap + 0.01) * (gap + 0.01) - dx * dx));
                double correction = (required - std::abs(dy)) / (anchored(i) || anchored(j) ? 1 : 2);
                double direction = dy < 0 ? -1 : 1;
                nodes[i] = constrain({nodes[i].x, nodes[i].y + direction * correction}, i);
                nodes[j] = constrain({nodes[j].x, nodes[j].y - direction * correction}, j);
                continue;
            }
            if (distance < 1e-6) { dx = 1; dy = 0; distance = 1; }
            double correction = (gap + 0.01 - distance) / ((anchored(i) || anchored(j) ? 1 : 2) * distance);
            nodes[i] = constrain({nodes[i].x + dx * correction, nodes[i].y + dy * correction}, i);
            nodes[j] = constrain({nodes[j].x - dx * correction, nodes[j].y - dy * correction}, j);
        }
        if (!collision) break;
    }
    return nodes;
}
}
