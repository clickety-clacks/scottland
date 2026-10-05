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
}
