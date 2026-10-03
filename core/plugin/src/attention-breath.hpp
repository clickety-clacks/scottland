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
// Before GO17 the local swell ranged from .55 to 1: a .45 excursion.
// GO17 reduced that to .12 (and used .12 as a density multiplier in goo),
// leaving mostly emission. Restore the visible excursion without animating
// the simulation. Density is exponential in distance, so convert the desired
// full-size shore travel into its render-only density multiplier.
constexpr float attention_swell = .45f;
inline float breath_swell(float thickness, float reach, float swell)
{
    return std::expm1(attention_swell * thickness / reach * swell / .7f);
}
} // namespace scottland::goo
