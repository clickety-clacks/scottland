#pragma once
#include <cmath>

namespace scottland::goo
{
// Apple-inspired sleep LED approximation, 12 breaths/minute. See GO17.
inline float attention_breath(double seconds)
{
    return (std::exp(-std::cos(std::fmod(seconds, 5.) * (2. * M_PI / 5.))) - std::exp(-1.)) /
           (std::exp(1.) - std::exp(-1.));
}
constexpr float breath_swell = .12f;
} // namespace scottland::goo
