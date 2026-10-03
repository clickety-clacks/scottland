#pragma once

#include <array>
#include <cstdint>
#include <glm/glm.hpp>
#include <string>
#include <memory>
#include <vector>

namespace scottland::goo
{
// A widget's half-resolution alpha contour. GPU jump flooding produces these
// signed 16-bit distances (1/16 texel); retain the same image for CPU input.
struct shape_t
{
    static constexpr int padding = 2;
    int width = 0, height = 0;
    uint64_t revision = 0;
    glm::vec4 bounds{}; // opaque bounds in mask texels: left, top, right, bottom
    glm::vec2 logical_size{};
    glm::vec2 bottom_hint{}; // inside the lowest body pixel nearest its bounds' center
    std::vector<uint8_t> pixels;
    glm::vec4 presented_bounds(glm::vec4 rect) const; // center / half extent
    float sample(glm::vec2 point, glm::vec4 rect, glm::vec4 body = {}) const;
};
// Logical output coordinates, y down. These snapshots are independent of desktop state.
struct source_t
{
    uint64_t id = 0;
    glm::vec4 rect{};              // center and half extent
    glm::vec4 liquid{1, 10, 1, 1}; // amount, radius, seed, release multiplier
    glm::vec3 dye{};
    glm::vec4 corners{};
    glm::vec4 sides{}; // top, right, bottom, left: eased whole-control proximity
    float control_extent = 26;
    glm::vec4 dot{}; // center, glow, radius
    float scale = 1, swell = 0;
    bool hint_circle = false; // visual-only round overlay; never an input island
    bool hinted = false; // window mode: the goo shows the hint color (dye) at once
    bool attention = false, grabbed = false, light = false, emitter = true;
    std::shared_ptr<const shape_t> shape;
    glm::vec4 shape_body{};
};

struct settings_t
{
    float thickness = 13, reach = 24, thinning = .45, swell = .7;
    float noise = .32, lump = 190, drift = .12;
    float wave_speed = .28, wave_damp = .985, wave_height = .55;
    float spread = .45, swirl = .9, release = .06, shine = .75, relief = 5;
    float depth = 6, profile = .65, soak = .12;
    float overlap_film = 4, hover_cloudiness = .65, hover_emissivity = .35, hover_distance = 48;
    // Empty means the prototype's exact exponential; custom curves span four reaches.
    std::array<float, 256> falloff{};
    settings_t();
    bool curve(const std::string &text);
    float fall(float distance) const;
    float threshold() const;
};

float distance(glm::vec2 p, const source_t &source);
float noise(glm::vec2 p);
bool overlaps(const std::vector<source_t> &sources);
size_t content_index(glm::vec2 p, const std::vector<source_t> &sources);
float control_cloud(glm::vec2 p, const source_t &source);
float overlap_film_width(const source_t &source, const settings_t &settings);
float density(glm::vec2 p, const std::vector<source_t> &sources, const settings_t &settings, float time, float breath = 0);
// Conservative outer radii for the rendered field, including quantization and wave headroom.
std::vector<float> support_radii(std::vector<source_t> sources, const settings_t &settings, bool film = false);
float union_distance(glm::vec2 p, const std::vector<source_t> &sources);
void amounts(std::vector<source_t> &sources, const settings_t &settings);
} // namespace scottland::goo
