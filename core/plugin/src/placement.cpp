#include "placement.hpp"
#include <algorithm>
#include <cmath>
#include <limits>
#include <utility>

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
}
