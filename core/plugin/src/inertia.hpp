#pragma once
namespace scottland::windowing
{
// One independent axis, in logical px and seconds. Integrate only until the stop time,
// including half the acceleration term: a single impulse travels v²/(2a), at any frame rate.
struct inertial_axis
{
    double velocity = 0;
    void impulse(double amount, double maximum);
    double step(double seconds, double deceleration);
    double constrain(double position, double minimum, double maximum);
};
}
