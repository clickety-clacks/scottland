#include "inertia.hpp"
#include <algorithm>
#include <cmath>
namespace scottland::windowing
{
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
