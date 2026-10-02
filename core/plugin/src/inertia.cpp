#include "inertia.hpp"
#include <algorithm>
#include <cmath>
namespace scottland::windowing
{
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
