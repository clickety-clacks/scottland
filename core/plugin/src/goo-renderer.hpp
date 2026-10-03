#pragma once
#include "goo-model.hpp"
#include <glm/gtc/matrix_transform.hpp>
#include <memory>
#include <wayfire/opengl.hpp>
#include <wayfire/scene-render.hpp>

namespace scottland::goo
{
class renderer_t
{
  public:
    renderer_t();
    ~renderer_t();
    bool supported();
    // `area` (output-logical rects) limits the expensive field pass; empty means everywhere.
    bool update(const std::vector<source_t> &sources, const settings_t &settings, int width, int height,
                float time, const std::vector<glm::vec4> &impulses,
                const std::vector<wf::geometry_t> &area = {},
                wf::auxilliary_buffer_t *wallpaper = nullptr, const glm::mat4 &wallpaper_map = glm::mat4{1});
    // Draws only where `area` (output-logical) meets the damage: the goo never leaves its bands.
    void draw(const wf::scene::render_instruction_t &data, const wf::regionf_t &area,
              const wf::regionf_t &breath_area, float breath, bool settled);
    bool overlapping() const;
    bool highlighting() const;
    float wave_at(glm::vec2 point);
    glm::vec4 sample_at(glm::vec2 point);
    float energy = 1, wave_energy = 1, dye_energy = 1;
    double last_step_ms = 0, last_gpu_ms = 0, last_draw_gpu_ms = 0;
    uint64_t steps = 0;
    // Monotonic work counters, including device pixels before fragment discard.
    // Unlike the last GPU query, deltas distinguish idle from ongoing work.
    uint64_t draws = 0, surface_pixels = 0, capture_pixels = 0, composite_pixels = 0;
    bool packed = false;

  private:
    struct impl;
    std::unique_ptr<impl> p;
};
} // namespace scottland::goo
