#include "placement.hpp"
#include <algorithm>
#include <cmath>
#include <limits>
#include <utility>
#include <queue>

namespace scottland::windowing
{
double overlap(rectangle a, rectangle b)
{
    return std::max(0.0, std::min(a.x + a.width, b.x + b.width) - std::max(a.x, b.x)) *
        std::max(0.0, std::min(a.y + a.height, b.y + b.height) - std::max(a.y, b.y));
}
point place_rectangle(double width, double height, rectangle r,
    const std::vector<rectangle>& obstacles, point preferred, std::optional<point> memory,
    double tolerance)
{
    if (memory) return *memory;
    double xlo = r.x + std::min(width, r.width) / 2;
    double xhi = r.x + r.width - std::min(width, r.width) / 2;
    double ylo = r.y + std::min(height, r.height) / 2;
    double yhi = r.y + r.height - std::min(height, r.height) / 2;
    std::vector<double> xs{xlo, xhi, std::clamp(preferred.x, xlo, xhi)};
    std::vector<double> ys{ylo, yhi, std::clamp(preferred.y, ylo, yhi)};
    for (auto o : obstacles)
    {
        for (double edge : {o.x, o.x + o.width})
            for (double half : {-width / 2, width / 2}) xs.push_back(std::clamp(edge + half, xlo, xhi));
        for (double edge : {o.y, o.y + o.height})
            for (double half : {-height / 2, height / 2}) ys.push_back(std::clamp(edge + half, ylo, yhi));
    }
    auto unique = [] (auto& values) { std::sort(values.begin(), values.end());
        values.erase(std::unique(values.begin(), values.end()), values.end()); };
    unique(xs); unique(ys);
    auto cost = [&] (double x, double y) { double sum = 0;
        for (auto o : obstacles) sum += overlap({x - width / 2, y - height / 2, width, height}, o);
        return sum; };
    double least = std::numeric_limits<double>::infinity();
    for (double x : xs) for (double y : ys) least = std::min(least, cost(x, y));
    double distance = std::numeric_limits<double>::infinity();
    point best{xs.front(), ys.front()};
    for (double x : xs) for (double y : ys)
    {
        double d = std::hypot(x - preferred.x, y - preferred.y);
        if (cost(x, y) <= least + std::max(0.0, tolerance) * width * height + 1e-8 && d < distance)
        { best = {x, y}; distance = d; }
    }
    return best;
}
double largest_opening(rectangle r, const std::vector<rectangle>& obstacles)
{
    std::vector<std::pair<double, double>> blocked;
    for (auto o : obstacles)
        if (o.x < r.x + r.width && o.x + o.width > r.x && o.y < r.y + r.height && o.y + o.height > r.y)
            blocked.emplace_back(std::max(r.y, o.y), std::min(r.y + r.height, o.y + o.height));
    std::sort(blocked.begin(), blocked.end());
    double end = r.y, best = 0;
    for (auto [a, b] : blocked) { best = std::max(best, a - end); end = std::max(end, b); }
    return std::max(best, r.y + r.height - end);
}

label_spot visible_label(rectangle r, rectangle screen, const std::vector<rectangle>& foreground,
    double precision)
{
    double right = std::min(r.x + r.width, screen.x + screen.width);
    double bottom = std::min(r.y + r.height, screen.y + screen.height);
    r.x = std::max(r.x, screen.x); r.y = std::max(r.y, screen.y);
    r.width = right - r.x; r.height = bottom - r.y;
    if (r.width <= 0 || r.height <= 0) return {{r.x, r.y}, 0};
    auto distance = [&] (point p) {
        double d = std::min({p.x - r.x, right - p.x, p.y - r.y, bottom - p.y});
        for (auto o : foreground)
        {
            // Signed distance to the exterior of an occluder. Taking the minimum implements
            // subtraction of their union without constructing polygon rings or raster masks.
            double dx = std::max({o.x - p.x, 0.0, p.x - o.x - o.width});
            double dy = std::max({o.y - p.y, 0.0, p.y - o.y - o.height});
            double outside = std::hypot(dx, dy);
            if (outside == 0) outside = -std::min({p.x - o.x, o.x + o.width - p.x,
                p.y - o.y, o.y + o.height - p.y});
            d = std::min(d, outside);
        }
        return d;
    };
    struct cell { point p; double half, d, upper; size_t order; };
    auto less = [] (const cell& a, const cell& b) {
        return a.upper == b.upper ? a.order > b.order : a.upper < b.upper;
    };
    std::priority_queue<cell, std::vector<cell>, decltype(less)> queue(less);
    point preferred{r.x + r.width / 2, r.y + r.height / 2};
    label_spot best{preferred, distance(preferred)};
    size_t order = 0;
    precision = std::max(0.1, precision);
    auto add = [&] (double x, double y, double half) {
        point p{x, y}; double d = distance(p);
        if (d > best.clearance + 1e-9 || (std::abs(d - best.clearance) < 1e-9 &&
            std::hypot(x - preferred.x, y - preferred.y) <
            std::hypot(best.center.x - preferred.x, best.center.y - preferred.y))) best = {p, d};
        double upper = d + half * std::sqrt(2.0); // distance is 1-Lipschitz
        if (upper > best.clearance + precision) queue.push({p, half, d, upper, order++});
    };
    double size = std::max(precision, std::min(r.width, r.height));
    for (double x = r.x; x < right; x += size) for (double y = r.y; y < bottom; y += size)
        add(x + size / 2, y + size / 2, size / 2);
    while (!queue.empty())
    {
        auto c = queue.top(); queue.pop();
        if (c.upper <= best.clearance + precision) break;
        double h = c.half / 2;
        for (double dx : {-h, h}) for (double dy : {-h, h}) add(c.p.x + dx, c.p.y + dy, h);
    }
    best.clearance = std::max(0.0, best.clearance);
    // A fully covered window cannot supply an interior label point. Keep a deterministic
    // screen-visible edge attachment rather than silently losing its selectable identity.
    if (best.clearance == 0) best.center = {r.x, preferred.y};
    return best;
}
}
