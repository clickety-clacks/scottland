#include "declutter.hpp"
#include <algorithm>
#include <cmath>
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
    const std::vector<rectangle>& obstacles, double radius, bool horizontal)
{
    auto begin = [&] (rectangle b) { return horizontal ? b.x : b.y; };
    auto end = [&] (rectangle b) { return begin(b) + (horizontal ? b.width : b.height); };
    std::vector<double> values{begin(r) + radius, end(r) - radius,
        (begin(r) + end(r)) / 2, begin(screen) + radius, end(screen) - radius};
    for (auto o : obstacles)
    {
        values.push_back(begin(o) - radius - .5);
        values.push_back(end(o) + radius + .5);
        // Corner contacts may reveal a circle before a whole rectangular strip opens.
        for (double edge : {begin(o), end(o)})
            for (double fraction : {0.382683432365, 0.707106781187, 0.923879532511})
            { values.push_back(edge - fraction * radius - .5);
              values.push_back(edge + fraction * radius + .5); }
    }
    std::sort(values.begin(), values.end());
    values.erase(std::unique(values.begin(), values.end()), values.end());
    return values;
}
struct candidate { size_t moved_index; point delta; double travel; };
std::optional<candidate> least_exposure_move(size_t index, double diameter,
    const std::vector<rectangle>& nodes, const std::vector<exposure_window>& windows,
    rectangle screen, const std::vector<rectangle>& fixed)
{
    auto r = nodes[index]; double radius = needed_radius(diameter);
    std::vector<rectangle> static_here = fixed;
    static_here.insert(static_here.end(), windows[index].fixed_foreground.begin(),
        windows[index].fixed_foreground.end());
    std::vector<rectangle> obstacles = static_here;
    for (size_t j = 0; j < index; ++j) obstacles.push_back(nodes[j]);
    auto xs = candidate_axis(r, screen, obstacles, radius, true);
    auto ys = candidate_axis(r, screen, obstacles, radius, false);
    std::optional<candidate> best;
    struct protected_label { size_t index; point center; double radius; };
    std::vector<protected_label> protected_labels;
    for (size_t k = index + 1; k < nodes.size(); ++k)
    {
        std::vector<rectangle> before = fixed;
        before.insert(before.end(), windows[k].fixed_foreground.begin(),
            windows[k].fixed_foreground.end());
        for (size_t j = 0; j < k; ++j) before.push_back(nodes[j]);
        auto spot = visible_label(nodes[k], screen, before);
        if (spot.clearance + .25 >= needed_radius(windows[k].minimum))
            protected_labels.push_back({k, spot.center,
                std::min(spot.clearance - .25, needed_radius(windows[k].wanted))});
    }
    auto offer = [&] (size_t moved_index, point delta) {
        double distance = std::hypot(delta.x, delta.y);
        // A rear circle already uncovered in an earlier pass must remain uncovered.
        // This prevents two equally cheap moves from undoing each other in a stack.
        for (auto label : protected_labels)
        {
            auto rear = moved_index == label.index ? moved(nodes[label.index], delta) : nodes[label.index];
            auto shifted = moved(nodes[moved_index], delta);
            if (visible_clearance(label.center, rear, screen, {shifted}) < label.radius - .01)
                return;
        }
        if (!best || distance < best->travel - .01 ||
            (std::abs(distance - best->travel) < .01 && moved_index == index &&
             best->moved_index != index)) best = candidate{moved_index, delta, distance};
    };
    auto current_bounds = bounds_for(r, screen, diameter);
    for (double x : xs) for (double y : ys)
    {
        point center{x, y};
        if (x < screen.x + radius || x > screen.x + screen.width - radius ||
            y < screen.y + radius || y > screen.y + screen.height - radius) continue;
        // First try moving only the covered window. Its foreground stays put.
        if (visible_clearance(center, screen, screen, obstacles) >= radius - .01)
        {
            double xlo = std::max(x + radius - r.x - r.width, current_bounds.xlo - r.x);
            double xhi = std::min(x - radius - r.x, current_bounds.xhi - r.x);
            double ylo = std::max(y + radius - r.y - r.height, current_bounds.ylo - r.y);
            double yhi = std::min(y - radius - r.y, current_bounds.yhi - r.y);
            if (xlo <= xhi && ylo <= yhi)
                offer(index, {std::clamp(0.0, xlo, xhi), std::clamp(0.0, ylo, yhi)});
        }
        // When the foreground covers every possible rear label center (an output-sized
        // window is the simplest case), expose the rear by shifting one covering window.
        if (visible_clearance(center, r, screen, static_here) < radius - .01) continue;
        for (size_t j = 0; j < index; ++j)
        {
            std::vector<rectangle> other = static_here;
            for (size_t k = 0; k < index; ++k) if (k != j) other.push_back(nodes[k]);
            if (visible_clearance(center, r, screen, other) < radius - .01) continue;
            auto front = nodes[j]; auto limit = bounds_for(front, screen, diameter);
            for (point delta : std::vector<point>{
                {x + radius + .5 - front.x, 0},
                {x - radius - .5 - front.x - front.width, 0},
                {0, y + radius + .5 - front.y},
                {0, y - radius - .5 - front.y - front.height}})
            {
                auto shifted = moved(front, delta);
                if (shifted.x < limit.xlo - .01 || shifted.x > limit.xhi + .01 ||
                    shifted.y < limit.ylo - .01 || shifted.y > limit.yhi + .01) continue;
                if (visible_clearance(center, r, screen, {shifted}) >= radius - .01)
                    offer(j, delta);
            }
        }
    }
    return best;
}
}

std::vector<exposure_result> expose_window_hints(const std::vector<exposure_window>& windows,
    rectangle screen, const std::vector<rectangle>& fixed)
{
    std::vector<rectangle> nodes;
    for (auto w : windows) nodes.push_back(w.frame);
    // A later rear window may require moving a foreground window, so revisit all
    // constraints until each one has room. No move is proposed for an already legible
    // window unless exposing a covered window requires that foreground concession.
    for (size_t pass = 0; pass < 2 * windows.size() + 4; ++pass)
    {
        bool changed = false;
        for (size_t i = 0; i < nodes.size(); ++i)
        {
            std::vector<rectangle> foreground = fixed;
            foreground.insert(foreground.end(), windows[i].fixed_foreground.begin(),
                windows[i].fixed_foreground.end());
            for (size_t j = 0; j < i; ++j) foreground.push_back(nodes[j]);
            auto spot = visible_label(nodes[i], screen, foreground);
            if (spot.clearance + .25 >= needed_radius(windows[i].wanted)) continue;
            std::optional<candidate> choice = least_exposure_move(i, windows[i].wanted,
                nodes, windows, screen, fixed);
            if (!choice)
            {
                // Prefer the largest circle the bounded layout can reveal. Search in
                // logical pixels, then retain the readable minimum when necessary.
                double lo = windows[i].minimum, hi = windows[i].wanted;
                choice = least_exposure_move(i, lo, nodes, windows, screen, fixed);
                if (!choice) continue; // no on-screen circle exists in this configuration
                while (hi - lo > 1)
                {
                    double mid = std::floor((lo + hi) / 2);
                    if (auto possible = least_exposure_move(i, mid, nodes, windows, screen, fixed))
                    { lo = mid; choice = possible; }
                    else hi = mid;
                }
            }
            if (choice->travel < .01) continue;
            nodes[choice->moved_index] = moved(nodes[choice->moved_index], choice->delta);
            changed = true;
        }
        if (!changed) break;
    }
    std::vector<exposure_result> result;
    for (size_t i = 0; i < nodes.size(); ++i)
    {
        std::vector<rectangle> foreground = fixed;
        foreground.insert(foreground.end(), windows[i].fixed_foreground.begin(),
            windows[i].fixed_foreground.end());
        for (size_t j = 0; j < i; ++j) foreground.push_back(nodes[j]);
        auto spot = visible_label(nodes[i], screen, foreground);
        double available = std::floor(std::max(0.0, 2 * (spot.clearance - 1) / pop_scale));
        double diameter = std::min(windows[i].wanted, available);
        if (diameter < windows[i].minimum) diameter = 0; // no external window hint
        result.push_back({{nodes[i].x - windows[i].frame.x,
            nodes[i].y - windows[i].frame.y}, spot, diameter});
    }
    return result;
}

std::vector<point> declutter(const std::vector<point>& anchors, rectangle bounds, double gap,
    const std::vector<double>& diameters, const std::vector<hint_constraint>& constraints)
{
    auto nodes = anchors;
    if (gap <= 0) return nodes;
    auto radius = [&] (size_t i) { return i < diameters.size() ? diameters[i] / 2 : 0; };
    auto vertical = [&] (size_t i) { return i < constraints.size() && constraints[i].vertical_only; };
    auto separation = [&] (size_t i, size_t j) { return gap + radius(i) + radius(j); };
    auto constrain = [&] (point p, size_t i) {
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
                double correction = (required - std::abs(dy)) / 2;
                double direction = dy < 0 ? -1 : 1;
                nodes[i] = constrain({nodes[i].x, nodes[i].y + direction * correction}, i);
                nodes[j] = constrain({nodes[j].x, nodes[j].y - direction * correction}, j);
                continue;
            }
            if (distance < 1e-6) { dx = 1; dy = 0; distance = 1; }
            double correction = (gap + 0.01 - distance) / (2 * distance);
            nodes[i] = constrain({nodes[i].x + dx * correction, nodes[i].y + dy * correction}, i);
            nodes[j] = constrain({nodes[j].x - dx * correction, nodes[j].y - dy * correction}, j);
        }
        if (!collision) break;
    }
    return nodes;
}
}
