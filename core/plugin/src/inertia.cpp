#include "inertia.hpp"
#include <algorithm>
#include <cmath>
#include <sstream>
namespace scottland::windowing
{
void friction_curve::parse(const std::string& text)
{
    xs.clear(); ys.clear(); slopes.clear();
    std::istringstream words(text);
    std::string word;
    std::vector<std::pair<double, double>> points;
    while (words >> word)
    {
        auto colon = word.find(':');
        if (colon == std::string::npos) return;
        try {
            size_t nx, ny;
            auto a = word.substr(0, colon), b = word.substr(colon + 1);
            double x = std::stod(a, &nx), y = std::stod(b, &ny);
            if (nx != a.size() || ny != b.size() || !std::isfinite(x) || !std::isfinite(y) ||
                x < 0 || x > 1 || y < 0.05 || y > 4) return;
            points.emplace_back(x, y);
        } catch (...) { return; }
    }
    std::sort(points.begin(), points.end());
    if (points.size() < 2 || points.front().first != 0 || points.back().first != 1) return;
    for (size_t i = 1; i < points.size(); ++i)
        if (points[i].first - points[i-1].first < 1e-6) return;
    for (auto [x, y] : points) { xs.push_back(x); ys.push_back(y); }
    size_t n = xs.size();
    std::vector<double> delta(n-1);
    for (size_t i = 0; i+1 < n; ++i) delta[i] = (ys[i+1]-ys[i])/(xs[i+1]-xs[i]);
    slopes.assign(n, 0);
    slopes.front() = delta.front(); slopes.back() = delta.back();
    for (size_t i = 1; i+1 < n; ++i) if (delta[i-1]*delta[i] > 0)
    {
        double h0 = xs[i]-xs[i-1], h1 = xs[i+1]-xs[i];
        double w1 = 2*h1+h0, w2 = h1+2*h0;
        slopes[i] = (w1+w2)/(w1/delta[i-1]+w2/delta[i]);
    }
}
double friction_curve::at(double t) const
{
    if (xs.empty()) return 1;
    t = std::clamp(t, 0.0, 1.0);
    size_t i = 0;
    while (i+2 < xs.size() && t > xs[i+1]) ++i;
    double h = xs[i+1]-xs[i], u = (t-xs[i])/h, u2 = u*u, u3 = u2*u;
    return std::clamp((2*u3-3*u2+1)*ys[i] + (u3-2*u2+u)*h*slopes[i] +
        (-2*u3+3*u2)*ys[i+1] + (u3-u2)*h*slopes[i+1], 0.05, 4.0);
}
double inertial_axis::step(double seconds, double deceleration, const friction_curve& curve, double maximum)
{
    // Bounded integration slices keep nonlinear laws stable across refresh rates. Constant
    // laws still use the exact partial-stop solution, retaining the old default distance.
    double distance = 0;
    while (seconds > 0 && velocity != 0)
    {
        double dt = std::min(seconds, 1.0/240);
        distance += step(dt, deceleration * curve.at(std::abs(velocity)/std::max(1.0, maximum)));
        seconds -= dt;
    }
    return distance;
}
void release_velocity::add(uint32_t time, double x, double y)
{
    if (!samples.empty() && samples.back().time == time) samples.pop_back();
    samples.push_back({time, x, y});
    while (!samples.empty() && uint32_t(time - samples.front().time) > 100) samples.pop_front();
}
std::pair<double, double> release_velocity::estimate(uint32_t release) const
{
    if (samples.size() < 2 || uint32_t(release - samples.back().time) >= 50) return {};
    auto recent = samples;
    while (!recent.empty() && uint32_t(release - recent.front().time) > 100) recent.pop_front();
    if (recent.size() < 2 || uint32_t(recent.back().time - recent.front().time) < 20) return {};
    if (recent.back().time != release)
        recent.push_back({release, recent.back().x, recent.back().y});
    double mt = 0, mx = 0, my = 0;
    for (auto s : recent) { mt -= uint32_t(release - s.time) / 1000.0; mx += s.x; my += s.y; }
    mt /= recent.size(); mx /= recent.size(); my /= recent.size();
    double variance = 0, vx = 0, vy = 0;
    for (auto s : recent)
    {
        double t = -double(uint32_t(release - s.time)) / 1000.0 - mt;
        variance += t * t; vx += t * (s.x - mx); vy += t * (s.y - my);
    }
    if (variance <= 0) return {};
    vx /= variance; vy /= variance;
    // Under 60 px/s the default stopping distance is under three pixels: precise drops stay put.
    if (std::hypot(vx, vy) < 60) return {};
    return {vx, vy};
}
void inertial_axis::impulse(double amount, double maximum)
{
    maximum = std::max(0.0, maximum);
    velocity = std::clamp(velocity + amount, -maximum, maximum);
}
double inertial_axis::step(double seconds, double deceleration)
{
    if (seconds <= 0 || velocity == 0) return 0;
    deceleration = std::max(0.001, deceleration);
    double speed = std::abs(velocity), t = std::min(seconds, speed / deceleration);
    double distance = std::copysign(speed * t - deceleration * t * t / 2, velocity);
    velocity = std::copysign(std::max(0.0, speed - deceleration * t), velocity);
    return distance;
}
double inertial_axis::bounce(double position, double minimum, double maximum, double restitution)
{
    maximum = std::max(minimum, maximum);
    if ((position <= minimum && velocity < 0) || (position >= maximum && velocity > 0))
        velocity = -velocity * std::clamp(restitution, 0.0, 1.0);
    return std::clamp(position, minimum, maximum);
}
double inertial_axis::constrain(double position, double minimum, double maximum)
{
    maximum = std::max(minimum, maximum);
    if ((position <= minimum && velocity < 0) || (position >= maximum && velocity > 0)) velocity = 0;
    return std::clamp(position, minimum, maximum);
}
}
