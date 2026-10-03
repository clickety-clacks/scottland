#include "declutter.hpp"
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <limits>
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
struct move_bounds { double xlo, xhi, ylo, yhi; };
move_bounds bounds_for(rectangle r, rectangle screen, double diameter)
{
    double visible = 2 * needed_radius(diameter);
    // Keep the badge-sized portion on screen, but permit the minimal off-screen movement
    // needed when a screen-sized front window covers another window completely.
    return {std::min(r.x, screen.x - r.width + visible),
        std::max(r.x, screen.x + screen.width - visible),
        std::min(r.y, screen.y - r.height + visible),
        std::max(r.y, screen.y + screen.height - visible)};
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
    point label_center;
    double clearance;
};
std::optional<candidate> least_exposure_move(size_t index, double diameter,
    const std::vector<rectangle>& nodes, const std::vector<exposure_window>& windows,
    rectangle screen, const std::vector<rectangle>& fixed,
    std::chrono::steady_clock::time_point deadline,
    const std::vector<label_spot>& known_spots, const std::vector<bool>& has_spot)
{
    if (expired(deadline)) return {};
    auto r = nodes[index]; double radius = needed_radius(diameter);
    std::vector<rectangle> static_here = fixed;
    static_here.reserve(fixed.size() + windows[index].fixed_foreground.size());
    static_here.insert(static_here.end(), windows[index].fixed_foreground.begin(),
        windows[index].fixed_foreground.end());
    std::vector<rectangle> obstacles = static_here;
    obstacles.reserve(static_here.size() + index);
    for (size_t j = 0; j < index; ++j) obstacles.push_back(nodes[j]);
    auto xs = candidate_axis(r, screen, obstacles, radius, true, deadline);
    auto ys = candidate_axis(r, screen, obstacles, radius, false, deadline);
    std::optional<candidate> best;
    struct protected_label { size_t index; point center; double radius; };
    std::vector<protected_label> protected_labels;
    protected_labels.reserve(nodes.size() - index - 1);
    for (size_t k = index + 1; k < nodes.size(); ++k)
    {
        if (expired(deadline)) return best;
        if (has_spot[k] && known_spots[k].clearance + .25 >= needed_radius(windows[k].minimum))
            protected_labels.push_back({k, known_spots[k].center,
                std::min(known_spots[k].clearance - .25,
                    needed_radius(std::max(windows[k].wanted, windows[k].minimum)))});
    }
    std::vector<rectangle> one_obstacle;
    one_obstacle.reserve(1);
    auto offer = [&] (size_t moved_index, point delta, point label_center, double clearance) {
        if (expired(deadline)) return false;
        if (windows[moved_index].anchored) return true;
        double distance = std::hypot(delta.x, delta.y);
        // A rear circle already uncovered in an earlier pass must remain uncovered.
        // This prevents two equally cheap moves from undoing each other in a stack.
        for (auto label : protected_labels)
        {
            if (expired(deadline)) return false;
            auto rear = moved_index == label.index ? moved(nodes[label.index], delta) : nodes[label.index];
            auto shifted = moved(nodes[moved_index], delta);
            one_obstacle.assign(1, shifted);
            auto clearance = visible_clearance_before(label.center, rear, screen, one_obstacle, deadline);
            if (!clearance) return false;
            if (*clearance < label.radius - .01) return true;
        }
        if (!best || distance < best->travel - .01 ||
            (std::abs(distance - best->travel) < .01 && moved_index == index &&
             best->moved_index != index))
            best = candidate{moved_index, delta, distance, label_center, clearance};
        return true;
    };
    auto current_bounds = bounds_for(r, screen, diameter);
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
        if (expired(deadline) || !tried.emplace(xi, yi).second) return !expired(deadline);
        double x = xs[xi], y = ys[yi];
        point center{x, y};
        if (x < screen.x + radius || x > screen.x + screen.width - radius ||
            y < screen.y + radius || y > screen.y + screen.height - radius) return true;
        auto unobscured = visible_clearance_before(center, screen, screen, obstacles, deadline);
        if (!unobscured) return false;
        // First try moving only the covered window. Its foreground stays put.
        if (*unobscured >= radius - .01)
        {
            double xlo = std::max(x + radius - r.x - r.width, current_bounds.xlo - r.x);
            double xhi = std::min(x - radius - r.x, current_bounds.xhi - r.x);
            double ylo = std::max(y + radius - r.y - r.height, current_bounds.ylo - r.y);
            double yhi = std::min(y - radius - r.y, current_bounds.yhi - r.y);
            if (xlo <= xhi && ylo <= yhi &&
                !offer(index, {std::clamp(0.0, xlo, xhi), std::clamp(0.0, ylo, yhi)},
                    center, *unobscured)) return false;
        }
        // A fully covering foreground surface may have to move to reveal a rear circle.
        auto this_clearance = visible_clearance_before(center, r, screen, static_here, deadline);
        if (!this_clearance) return false;
        if (*this_clearance < radius - .01) return true;
        for (size_t j = 0; j < index; ++j)
        {
            if (expired(deadline)) return false;
            other_foreground.assign(static_here.begin(), static_here.end());
            for (size_t k = 0; k < index; ++k) if (k != j) other_foreground.push_back(nodes[k]);
            auto open = visible_clearance_before(center, r, screen, other_foreground, deadline);
            if (!open) return false;
            if (*open < radius - .01) continue;
            auto front = nodes[j]; auto limit = bounds_for(front, screen, diameter);
            std::array<point, 4> deltas{{
                {x + radius + .5 - front.x, 0},
                {x - radius - .5 - front.x - front.width, 0},
                {0, y + radius + .5 - front.y},
                {0, y - radius - .5 - front.y - front.height}}};
            for (point delta : deltas)
            {
                if (expired(deadline)) return false;
                auto shifted = moved(front, delta);
                if (shifted.x < limit.xlo - .01 || shifted.x > limit.xhi + .01 ||
                    shifted.y < limit.ylo - .01 || shifted.y > limit.yhi + .01) continue;
                one_obstacle.assign(1, shifted);
                auto clearance = visible_clearance_before(center, r, screen, one_obstacle, deadline);
                if (!clearance) return false;
                if (*clearance >= radius - .01 &&
                    !offer(j, delta, center, std::min(*open, *clearance))) return false;
            }
        }
        return true;
    };

    // First test the left/right/top/bottom edge slides, then the four opposite corners.
    // These are the coarse exact-contact candidates that can expose a wanted circle quickly.
    auto spread = [] (size_t count) {
        std::vector<size_t> result;
        size_t lo = 0, hi = count ? count - 1 : 0;
        while (lo <= hi && count)
        {
            result.push_back(lo++);
            if (lo <= hi) result.push_back(hi--);
        }
        return result;
    };
    auto x_order = spread(xs.size()), y_order = spread(ys.size());
    for (auto xi : x_order) if (!inspect(xi, center_y)) return best;
    for (auto yi : y_order) if (!inspect(center_x, yi)) return best;
    for (auto xi : {size_t(0), xs.size() - 1})
        for (auto yi : {size_t(0), ys.size() - 1})
            if (!inspect(xi, yi)) return best;

    // Refine the coarse result by examining remaining contact pairs in increasing
    // lower-bound travel. Include moves of either the covered window or a foreground
    // window in the bound, so the first satisfactory minimum is safe to stop at.
    auto movement_lower_bound = [&] (double x, double y) {
        double lower = std::numeric_limits<double>::infinity();
        if (x < screen.x + radius || x > screen.x + screen.width - radius ||
            y < screen.y + radius || y > screen.y + screen.height - radius) return lower;
        if (!windows[index].anchored)
        {
            double xlo = std::max(x + radius - r.x - r.width, current_bounds.xlo - r.x);
            double xhi = std::min(x - radius - r.x, current_bounds.xhi - r.x);
            double ylo = std::max(y + radius - r.y - r.height, current_bounds.ylo - r.y);
            double yhi = std::min(y - radius - r.y, current_bounds.yhi - r.y);
            if (xlo <= xhi && ylo <= yhi)
                lower = std::hypot(std::clamp(0.0, xlo, xhi), std::clamp(0.0, ylo, yhi));
        }
        for (size_t j = 0; j < index && !expired(deadline); ++j)
        {
            if (windows[j].anchored) continue;
            auto front = nodes[j];
            auto limit = bounds_for(front, screen, diameter);
            std::array<point, 4> deltas{{
                {x + radius + .5 - front.x, 0},
                {x - radius - .5 - front.x - front.width, 0},
                {0, y + radius + .5 - front.y},
                {0, y - radius - .5 - front.y - front.height}}};
            for (point delta : deltas)
            {
                auto shifted = moved(front, delta);
                if (shifted.x < limit.xlo - .01 || shifted.x > limit.xhi + .01 ||
                    shifted.y < limit.ylo - .01 || shifted.y > limit.yhi + .01) continue;
                lower = std::min(lower, std::hypot(delta.x, delta.y));
            }
        }
        return lower;
    };
    std::vector<std::tuple<double, size_t, size_t>> refinement;
    bool refinement_complete = true;
    for (size_t xi = 0; xi < xs.size() && refinement_complete; ++xi)
        for (size_t yi = 0; yi < ys.size(); ++yi)
            if (!tried.count({xi, yi}))
            {
                if (expired(deadline)) { refinement_complete = false; break; }
                double lower = movement_lower_bound(xs[xi], ys[yi]);
                if (expired(deadline)) { refinement_complete = false; break; }
                if (std::isfinite(lower)) refinement.emplace_back(lower, xi, yi);
            }
    if (!refinement_complete) return best;
    std::sort(refinement.begin(), refinement.end());
    for (auto [travel, xi, yi] : refinement)
    {
        // Remaining candidates have at least this much travel. Once a satisfactory minimum
        // is known, farther cells cannot improve it.
        if (best && travel > best->travel + .01) break;
        if (!inspect(xi, yi)) return best;
    }
    return best;
}
}

std::vector<exposure_result> expose_window_hints(const std::vector<exposure_window>& windows,
    rectangle screen, const std::vector<rectangle>& fixed,
    std::chrono::steady_clock::time_point deadline, bool *deadline_hit)
{
    if (deadline_hit) *deadline_hit = false;
    std::vector<rectangle> nodes;
    for (auto w : windows) nodes.push_back(w.frame);
    std::vector<label_spot> best_spots(nodes.size());
    std::vector<bool> has_spot(nodes.size(), false);
    // Keep a cheap checked no-move answer so a deadline still leaves unobscured badges usable.
    for (size_t i = 0; i < nodes.size(); ++i)
    {
        if (i && expired(deadline)) break;
        std::vector<rectangle> foreground = fixed;
        foreground.insert(foreground.end(), windows[i].fixed_foreground.begin(),
            windows[i].fixed_foreground.end());
        for (size_t j = 0; j < i; ++j) foreground.push_back(nodes[j]);
        point center{nodes[i].x + nodes[i].width / 2, nodes[i].y + nodes[i].height / 2};
        if (auto clearance = visible_clearance_before(center, nodes[i], screen, foreground, deadline))
        {
            best_spots[i] = {center, *clearance};
            has_spot[i] = true;
        }
    }
    // A later rear window may require moving a foreground window, so revisit all
    // constraints until each one has room. No move is proposed for an already legible
    // window unless exposing a covered window requires that foreground concession.
    for (size_t pass = 0; pass < 2 * windows.size() + 4; ++pass)
    {
        if (expired(deadline)) break;
        bool changed = false;
        for (size_t i = 0; i < nodes.size(); ++i)
        {
            const double wanted = std::max(windows[i].wanted, windows[i].minimum);
            if (expired(deadline)) break;
            // Share remaining time with the windows still waiting in this pass. A single
            // difficult mid-stack window must not consume the whole refresh before a
            // completely covered rear window gets any checked placement.
            auto window_deadline = deadline;
            if (deadline != std::chrono::steady_clock::time_point::max())
            {
                auto now = std::chrono::steady_clock::now();
                window_deadline = now + (deadline - now) / (nodes.size() - i);
            }
            std::vector<rectangle> foreground = fixed;
            foreground.insert(foreground.end(), windows[i].fixed_foreground.begin(),
                windows[i].fixed_foreground.end());
            for (size_t j = 0; j < i; ++j) foreground.push_back(nodes[j]);
            auto spot = has_spot[i] && best_spots[i].clearance <
                needed_radius(windows[i].minimum) ? best_spots[i] :
                visible_label(nodes[i], screen, foreground, .5, label_deadline(window_deadline),
                    needed_radius(wanted));
            if (!has_spot[i] || spot.clearance > best_spots[i].clearance)
            { best_spots[i] = spot; has_spot[i] = true; }
            if (spot.clearance + .25 >= needed_radius(wanted)) continue;
            std::optional<candidate> choice;
            if (spot.clearance < needed_radius(windows[i].minimum))
            {
                // A fully covered window may still move into a broad free strip. Find
                // that opening once, derive its actual readable diameter, and search
                // movement for that size directly instead of binary-searching upward.
                auto open = visible_label(screen, screen, foreground, .5,
                    label_deadline(window_deadline));
                double available = std::floor(std::max(0.0,
                    2 * (open.clearance - 1) / pop_scale));
                available = std::min(available, wanted);
                if (available >= windows[i].minimum && !expired(window_deadline))
                    choice = least_exposure_move(i, available, nodes, windows, screen, fixed,
                        window_deadline, best_spots, has_spot);
            }
            if (!choice && !expired(window_deadline))
                choice = least_exposure_move(i, wanted,
                    nodes, windows, screen, fixed, window_deadline, best_spots, has_spot);
            if (!choice)
            {
                // If the wanted circle cannot fit, refine upward from the least-travel
                // readable placement while budget remains.
                double lo = windows[i].minimum, hi = wanted;
                if (!expired(window_deadline))
                    choice = least_exposure_move(i, lo, nodes, windows, screen, fixed, window_deadline,
                        best_spots, has_spot);
                if (!choice) continue; // no on-screen circle exists in this configuration
                while (hi - lo > 1)
                {
                    if (expired(window_deadline)) break;
                    double mid = std::floor((lo + hi) / 2);
                    if (auto possible = least_exposure_move(i, mid, nodes, windows, screen, fixed,
                        window_deadline, best_spots, has_spot))
                    { lo = mid; choice = possible; }
                    else hi = mid;
                }
            }
            if (choice->travel < .01) continue;
            nodes[choice->moved_index] = moved(nodes[choice->moved_index], choice->delta);
            if (choice->moved_index != i && has_spot[choice->moved_index])
            {
                best_spots[choice->moved_index].center.x += choice->delta.x;
                best_spots[choice->moved_index].center.y += choice->delta.y;
            }
            for (size_t rear = choice->moved_index + 1; rear < has_spot.size(); ++rear)
                has_spot[rear] = false;
            best_spots[i] = {choice->label_center, choice->clearance};
            has_spot[i] = true;
            changed = true;
            if (expired(deadline)) break;
        }
        if (!changed) break;
        if (expired(deadline)) break;
    }
    std::vector<exposure_result> result;
    for (size_t i = 0; i < nodes.size(); ++i)
    {
        const double wanted = std::max(windows[i].wanted, windows[i].minimum);
        label_spot spot;
        if (!expired(deadline))
        {
            std::vector<rectangle> foreground = fixed;
            foreground.insert(foreground.end(), windows[i].fixed_foreground.begin(),
                windows[i].fixed_foreground.end());
            for (size_t j = 0; j < i; ++j) foreground.push_back(nodes[j]);
            auto refined = visible_label(nodes[i], screen, foreground, .5, label_deadline(deadline),
                needed_radius(wanted));
            if (!has_spot[i] || refined.clearance > best_spots[i].clearance)
            { best_spots[i] = refined; has_spot[i] = true; }
            spot = best_spots[i];
            if (spot.clearance < needed_radius(windows[i].minimum) && !expired(deadline))
            {
                auto fallback = visible_label(nodes[i], screen, foreground, .5, label_deadline(deadline));
                if (fallback.clearance > best_spots[i].clearance)
                    best_spots[i] = spot = fallback;
            }
        } else if (has_spot[i]) spot = best_spots[i];
        else spot = {{nodes[i].x + nodes[i].width / 2, nodes[i].y + nodes[i].height / 2}, 0};
        best_spots[i] = spot;
        double available = std::floor(std::max(0.0, 2 * (spot.clearance - 1) / pop_scale));
        // Attention remains present even when no unobscured circle can fit. Use the
        // best checked label center (or the window center if the deadline found none).
        double diameter = std::max(windows[i].minimum, std::min(wanted, available));
        result.push_back({{nodes[i].x - windows[i].frame.x,
            nodes[i].y - windows[i].frame.y}, spot, diameter});
    }
    if (deadline_hit) *deadline_hit = expired(deadline);
    return result;
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
