#pragma once
#include <cstdint>
#include <deque>
#include <utility>
namespace scottland::windowing
{
// Timestamped input positions in layout pixels. A recent linear fit rejects event jitter;
// a stationary tail represents the time between the last motion and release.
class release_velocity
{
    struct sample { uint32_t time; double x, y; };
    std::deque<sample> samples;
  public:
    void clear() { samples.clear(); }
    void add(uint32_t time, double x, double y);
    std::pair<double, double> estimate(uint32_t release) const;
};
// One independent axis, in logical px and seconds. Integrate only until the stop time,
// including half the acceleration term: a single impulse travels v²/(2a), at any frame rate.
struct inertial_axis
{
    double velocity = 0;
    void impulse(double amount, double maximum);
    double step(double seconds, double deceleration);
    double bounce(double position, double minimum, double maximum, double restitution);
    double constrain(double position, double minimum, double maximum);
};
}
