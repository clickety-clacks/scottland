#include "placement.hpp"
#include <algorithm>
#include <cmath>
#include <limits>
#include <utility>
#include <tuple>
#include <queue>

namespace scottland::windowing
{
double overlap(rectangle a, rectangle b)
{
    return std::max(0.0, std::min(a.x + a.width, b.x + b.width) - std::max(a.x, b.x)) *
        std::max(0.0, std::min(a.y + a.height, b.y + b.height) - std::max(a.y, b.y));
}
double visible_fraction(rectangle window, rectangle screen, const std::vector<rectangle>& foreground)
{
    double x1 = std::max(window.x, screen.x), y1 = std::max(window.y, screen.y);
    double x2 = std::min(window.x + window.width, screen.x + screen.width);
    double y2 = std::min(window.y + window.height, screen.y + screen.height);
    if (x2 <= x1 || y2 <= y1) return 1;
    std::vector<rectangle> covers;
    std::vector<double> xs{x1, x2};
    for (auto f : foreground)
    {
        double a = std::max(f.x, x1), b = std::max(f.y, y1);
        double c = std::min(f.x + f.width, x2), d = std::min(f.y + f.height, y2);
        if (c <= a || d <= b) continue;
        covers.push_back({a, b, c - a, d - b});
        xs.insert(xs.end(), {a, c});
    }
    std::sort(xs.begin(), xs.end()); xs.erase(std::unique(xs.begin(), xs.end()), xs.end());
    double covered = 0;
    std::vector<std::pair<double, double>> spans;
    for (size_t i = 0; i + 1 < xs.size(); ++i)
    {
        // Union of the covers' vertical spans across this strip.
        spans.clear();
        for (auto f : covers)
            if (f.x <= xs[i] && f.x + f.width >= xs[i + 1]) spans.emplace_back(f.y, f.y + f.height);
        std::sort(spans.begin(), spans.end());
        double length = 0, top = 0, bottom = -std::numeric_limits<double>::infinity();
        for (auto [a, b] : spans)
        {
            if (a > bottom) { length += std::max(0.0, bottom - top); top = a; bottom = b; }
            else bottom = std::max(bottom, b);
        }
        if (!spans.empty()) length += bottom - top;
        covered += length * (xs[i + 1] - xs[i]);
    }
    return std::clamp(1 - covered / ((x2 - x1) * (y2 - y1)), 0.0, 1.0);
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

std::optional<double> visible_clearance_before(point p, rectangle r, rectangle screen,
    const std::vector<rectangle>& foreground,
    std::chrono::steady_clock::time_point deadline)
{
    bool bounded = deadline != std::chrono::steady_clock::time_point::max();
    double right = std::min(r.x + r.width, screen.x + screen.width);
    double bottom = std::min(r.y + r.height, screen.y + screen.height);
    r.x = std::max(r.x, screen.x); r.y = std::max(r.y, screen.y);
    double d = std::min({p.x - r.x, right - p.x, p.y - r.y, bottom - p.y});
    for (auto o : foreground)
    {
        if (bounded && std::chrono::steady_clock::now() >= deadline) return {};
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
}

double visible_clearance(point p, rectangle r, rectangle screen,
    const std::vector<rectangle>& foreground)
{
    return *visible_clearance_before(p, r, screen, foreground,
        std::chrono::steady_clock::time_point::max());
}

label_spot visible_label(rectangle r, rectangle screen, const std::vector<rectangle>& foreground,
    double precision, std::chrono::steady_clock::time_point deadline, double sufficient_clearance,
    size_t inspection_budget, size_t *inspection_count)
{
    size_t inspections = 0;
    double right = std::min(r.x + r.width, screen.x + screen.width);
    double bottom = std::min(r.y + r.height, screen.y + screen.height);
    r.x = std::max(r.x, screen.x); r.y = std::max(r.y, screen.y);
    r.width = right - r.x; r.height = bottom - r.y;
    if (r.width <= 0 || r.height <= 0)
    {
        if (inspection_count) *inspection_count = inspections;
        return {{r.x, r.y}, 0};
    }
    auto distance = [&] (point p) {
        if (inspections >= inspection_budget) return std::optional<double>{};
        ++inspections;
        return visible_clearance_before(p, r, screen, foreground, deadline);
    };
    struct cell { point p; double half, d, upper, spread; size_t order; };
    auto less = [] (const cell& a, const cell& b) {
        if (a.upper != b.upper) return a.upper < b.upper;
        // Expand the coarsest tied region first, and spread equal-size plateau probes apart.
        if (a.half != b.half) return a.half < b.half;
        if (a.spread != b.spread) return a.spread < b.spread;
        return a.order > b.order;
    };
    std::priority_queue<cell, std::vector<cell>, decltype(less)> queue(less);
    point preferred{r.x + r.width / 2, r.y + r.height / 2};
    auto initial = distance(preferred);
    label_spot best{preferred, initial.value_or(0)};
    size_t order = 0;
    bool interrupted = !initial;
    bool sufficient = sufficient_clearance >= 0 && best.clearance >= sufficient_clearance;
    precision = std::max(0.1, precision);
    auto add = [&] (double x, double y, double half) {
        if (interrupted || sufficient) return;
        auto measured = distance({x, y});
        if (!measured) { interrupted = true; return; }
        point p{x, y}; double d = *measured;
        if (d > best.clearance + 1e-9 || (std::abs(d - best.clearance) < 1e-9 &&
            std::hypot(x - preferred.x, y - preferred.y) <
            std::hypot(best.center.x - preferred.x, best.center.y - preferred.y))) best = {p, d};
        double upper = d + half * std::sqrt(2.0); // distance is 1-Lipschitz
        if (upper > best.clearance + precision)
            queue.push({p, half, d, upper, std::hypot(x - preferred.x, y - preferred.y), order++});
        sufficient = sufficient_clearance >= 0 && best.clearance >= sufficient_clearance;
    };
    // Begin with a small, spatially broad grid. Opposite corners and edges are sampled
    // before any one region is refined, so a short deadline still has a useful candidate.
    double size = std::max({precision, std::min(r.width, r.height) / 2,
        std::max(r.width, r.height) / 4});
    std::vector<point> coarse;
    for (double x = r.x; x < right; x += size)
        for (double y = r.y; y < bottom; y += size)
            coarse.push_back({x + size / 2, y + size / 2});
    std::stable_sort(coarse.begin(), coarse.end(), [&] (point a, point b) {
        auto da = std::hypot(a.x - preferred.x, a.y - preferred.y);
        auto db = std::hypot(b.x - preferred.x, b.y - preferred.y);
        return da == db ? std::tie(a.x, a.y) < std::tie(b.x, b.y) : da > db;
    });
    for (auto p : coarse)
    {
        if (interrupted || sufficient || std::chrono::steady_clock::now() >= deadline) break;
        add(p.x, p.y, size / 2);
    }
    while (!queue.empty() && !interrupted && !sufficient &&
        std::chrono::steady_clock::now() < deadline)
    {
        auto c = queue.top(); queue.pop();
        if (c.upper <= best.clearance + precision) break;
        double h = c.half / 2;
        for (double dx : {-h, h}) for (double dy : {-h, h}) add(c.p.x + dx, c.p.y + dy, h);
    }
    best.clearance = std::max(0.0, best.clearance);
    if (inspection_count) *inspection_count = inspections;
    return best;
}
}
