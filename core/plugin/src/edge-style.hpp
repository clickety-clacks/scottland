#pragma once

#include <algorithm>
#include <glm/glm.hpp>

namespace scottland::edge_style
{
// Keep each shipped default exactly on the old light/dark color. Other slider
// values are true neutral grays from black to white.
inline glm::vec3 neutral_color(bool light, float tone)
{
    tone = std::clamp(tone, 0.f, 1.f);
    const float original_tone = light ? .08f : .92f;
    const glm::vec3 original = light ? glm::vec3{.08f, .08f, .1f} : glm::vec3{.9f, .92f, .95f};
    if (tone == original_tone) return original;
    return glm::vec3{tone};
}

// State colors retain their full dye weight as focus, attention or a hint takes
// over. Only the remaining neutral portion is controlled by the user's strength.
inline float tint_strength(float strength, float focus, float attention, bool hinted)
{
    if (hinted) return 1.f;
    strength = std::clamp(strength, 0.f, 1.f);
    focus = std::clamp(focus, 0.f, 1.f);
    attention = std::clamp(attention, 0.f, 1.f);
    const float state_mix = 1.f - (1.f - focus) * (1.f - attention);
    return strength + (1.f - strength) * state_mix;
}

inline float halo_neutral_density(float strength, float focus)
{
    const float unfocused = .16f * std::clamp(strength, 0.f, 1.f);
    return unfocused + (.44f - unfocused) * std::clamp(focus, 0.f, 1.f);
}
} // namespace scottland::edge_style
