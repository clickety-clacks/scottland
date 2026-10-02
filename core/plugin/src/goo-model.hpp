#pragma once

#include <array>
#include <cstdint>
#include <glm/glm.hpp>
#include <string>
#include <vector>

namespace scottland::goo
{
// Logical output coordinates, y down. These snapshots are independent of desktop state.
struct source_t
{
    uint64_t id = 0;
    glm::vec4 rect{};              // center and half extent
    glm::vec4 liquid{1, 10, 1, 1}; // amount, radius, seed, release multiplier
    glm::vec3 dye{};
    glm::vec4 corners{};
    glm::vec4 dot{}; // center, glow, radius
    float scale = 1, swell = 0;
    float hint_border = 0; // transient logical-pixel rim; dye carries its hint color
    bool attention = false, grabbed = false, light = false, emitter = true;
};

struct settings_t
{
    float thickness = 13, reach = 24, thinning = .45, swell = .7;
    float noise = .32, lump = 190, drift = .12;
    float wave_speed = .28, wave_damp = .985, wave_height = .55;
    float spread = .45, swirl = .9, release = .06, shine = .75, relief = 5;
    // Empty means the prototype's exact exponential; custom curves span four reaches.
    std::array<float, 256> falloff{};
    settings_t();
    bool curve(const std::string &text);
    float fall(float distance) const;
    float threshold() const;
};

float distance(glm::vec2 p, const source_t &source);
float noise(glm::vec2 p);
float density(glm::vec2 p, const std::vector<source_t> &sources, const settings_t &settings, float time);
float union_distance(glm::vec2 p, const std::vector<source_t> &sources);
void amounts(std::vector<source_t> &sources, const settings_t &settings);
} // namespace scottland::goo
