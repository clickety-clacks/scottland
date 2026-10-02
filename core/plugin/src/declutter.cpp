#include "declutter.hpp"
#include <algorithm>
#include <cmath>
namespace scottland::windowing
{
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
