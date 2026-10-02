#include "declutter.hpp"
#include <algorithm>
#include <cmath>
namespace scottland::windowing
{
std::vector<point> declutter_windows(const std::vector<rectangle>& windows, rectangle bounds,
    const std::vector<rectangle>& fixed)
{
    auto nodes = windows;
    auto clip = [&] (rectangle r) {
        double right = std::min(r.x + r.width, bounds.x + bounds.width);
        double bottom = std::min(r.y + r.height, bounds.y + bounds.height);
        r.x = std::max(r.x, bounds.x); r.y = std::max(r.y, bounds.y);
        r.width = std::max(0.0, right - r.x); r.height = std::max(0.0, bottom - r.y);
        return r;
    };
    for (int pass = 0; pass < 12; ++pass)
    {
        bool changed = false;
        for (size_t i = 0; i < nodes.size(); ++i)
        {
            auto r = nodes[i]; auto origin = windows[i];
            // Keep fitted dimensions on screen; oversized dimensions may translate only
            // while still covering the output. Never hide content to manufacture less overlap.
            double xlo = bounds.x + std::min(0.0, bounds.width - r.width);
            double xhi = bounds.x + std::max(0.0, bounds.width - r.width);
            double ylo = bounds.y + std::min(0.0, bounds.height - r.height);
            double yhi = bounds.y + std::max(0.0, bounds.height - r.height);
            std::vector<rectangle> obstacles = fixed;
            for (size_t j = 0; j < nodes.size(); ++j) if (i != j) obstacles.push_back(nodes[j]);
            std::vector<double> xs{r.x, std::clamp(origin.x, xlo, xhi), xlo, xhi};
            std::vector<double> ys{r.y, std::clamp(origin.y, ylo, yhi), ylo, yhi};
            for (auto o : obstacles)
            {
                for (double x : {o.x - r.width, o.x + o.width}) xs.push_back(std::clamp(x, xlo, xhi));
                for (double y : {o.y - r.height, o.y + o.height}) ys.push_back(std::clamp(y, ylo, yhi));
            }
            auto unique = [] (auto& v) { std::sort(v.begin(), v.end());
                v.erase(std::unique(v.begin(), v.end()), v.end()); };
            unique(xs); unique(ys);
            auto cost = [&] (double x, double y) {
                auto candidate = clip({x, y, r.width, r.height}); double total = 0;
                for (auto o : obstacles) total += overlap(candidate, clip(o));
                return total;
            };
            double least = cost(r.x, r.y), travel = std::hypot(r.x - origin.x, r.y - origin.y);
            point best{r.x, r.y};
            for (double x : xs) for (double y : ys)
            {
                double area = cost(x, y), distance = std::hypot(x - origin.x, y - origin.y);
                if (area < least - 0.01 || (std::abs(area - least) <= 0.01 && distance < travel - 0.01))
                { least = area; travel = distance; best = {x, y}; }
            }
            changed |= std::hypot(best.x - r.x, best.y - r.y) > 0.01;
            nodes[i].x = best.x; nodes[i].y = best.y;
        }
        if (!changed) break;
    }
    std::vector<point> offsets;
    for (size_t i = 0; i < nodes.size(); ++i)
        offsets.push_back({nodes[i].x - windows[i].x, nodes[i].y - windows[i].y});
    return offsets;
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
        if (i < constraints.size() && constraints[i].immovable) return anchors[i];
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
