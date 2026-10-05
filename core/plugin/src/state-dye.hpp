#pragma once

#include <algorithm>
#include <glm/glm.hpp>

namespace scottland::state_dye
{
inline float mix(float focus, float attention, bool hinted)
{
    if (hinted) return 1.f;
    focus = std::clamp(focus, 0.f, 1.f);
    attention = std::clamp(attention, 0.f, 1.f);
    return 1.f - (1.f - focus) * (1.f - attention);
}

// Keep the neutral part at its A16 setting while scaling only state dye.
// Values above one can make the intermediate weight stronger, but the shader
// clamps its final dye blend to full opacity.
inline float weight(float neutral_strength, float state_mix, float strength)
{
    neutral_strength = std::clamp(neutral_strength, 0.f, 1.f);
    state_mix = std::clamp(state_mix, 0.f, 1.f);
    strength = std::clamp(strength, 0.f, 1.5f);
    return neutral_strength * (1.f - state_mix) + strength * state_mix;
}

inline glm::vec3 tone(glm::vec3 neutral, glm::vec3 color, float strength)
{
    strength = std::clamp(strength, 0.f, 1.5f);
    if (strength == 1.f) return color;
    return glm::clamp(neutral + (color - neutral) * strength, glm::vec3{0.f}, glm::vec3{1.f});
}
} // namespace scottland::state_dye
