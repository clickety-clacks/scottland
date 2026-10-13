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
    // Set up the output-sized simulation before a source appears on this output.
    bool prepare(int width, int height);
    bool prepared_for(int width, int height) const;
    void poll_timing();
    // `area` (output-logical rects) limits the expensive field pass; empty means everywhere.
    bool update(const std::vector<source_t> &sources, const settings_t &settings, int width, int height,
                float time, const std::vector<glm::vec4> &impulses,
                const std::vector<wf::geometry_t> &area = {}, float flow = 0);
    // GO24: move only the dye (wet texels), for the watercolor's slow motion while the
    // simulation sleeps. `step` is how many ordinary steps each pass stands for.
    void flow_dye(float flow, float step, int passes = 1);
    uint64_t dye_flows = 0;
    // GO28: open desktop has a background-layer client to pick color up from (film over a
    // window always has that window).
    bool open_pickup = false;
    // GO28: liquid texels whose backdrop differs from what the dye last saw (more than 4
    // levels), counted up to 255; and taking the current backdrop as seen.
    int backdrop_changes(); // -1: GPU reduction submitted, completion is not ready yet
    void backdrop_seen();
    uint64_t under_pixels = 0, backdrop_checks = 0;
    bool under_waiting() const;  // a frame copied backdrop the pickup texture has not taken yet
    // Draws only where `area` (output-logical) meets the damage: the goo never leaves its bands.
    void draw(const wf::scene::render_instruction_t &data, const wf::regionf_t &area,
              const wf::regionf_t &breath_area, float breath, bool settled, bool breath_keys = true,
              bool reuse_backdrop = false, const wf::regionf_t *dry = nullptr,
              const wf::regionf_t *dry_content = nullptr, const wf::regionf_t *reuse_area = nullptr);
    // The settled surface and the backdrop under it are cached: a breath can be drawn
    // over the cached backdrop without the scene beneath being repainted first.
    bool backdrop_ready(const wf::render_target_t &target) const;
    bool overlapping() const;
    bool highlighting() const;
    float wave_at(glm::vec2 point);
    glm::vec4 sample_at(glm::vec2 point);
    float energy = 1, wave_energy = 1, dye_energy = 1;
    double last_step_ms = 0, last_gpu_ms = 0, last_draw_gpu_ms = 0;
    // Complete dye-only and backdrop check scopes, including pickup refresh and reduction.
    double flow_gpu_ms = 0, check_gpu_ms = 0, seen_gpu_ms = 0;
    uint64_t flow_gpu_samples = 0, check_gpu_samples = 0, seen_gpu_samples = 0;
    double flow_wall_ms = 0, check_wall_ms = 0, seen_wall_ms = 0;
    double flow_wall_max_ms = 0, check_wall_max_ms = 0, seen_wall_max_ms = 0;
    uint64_t seen_calls = 0;
    bool gpu_timing = false;
    uint64_t steps = 0;
    // Submitted device-pixel work and GO18 breathing cache diagnostics.
    uint64_t draws = 0, surface_pixels = 0, capture_pixels = 0, composite_pixels = 0;
    uint64_t backdrop_reuses = 0;
    uint64_t breath_refreshes = 0;
    std::vector<float> breath_key_values;
    bool breath_keyframes_active = false;
    bool breath_exact = false; // test-only exact-path override, gated by SCOTTLAND_TEST_MODEL
    bool breath_layer_fail = false; // test-only: the second cache layer fails to allocate
    bool surface_cache_fail = false; // test-only: the settled surface cache fails to allocate
    // GO26: why breathing is on the exact path ("" when keyframes are in use or nothing
    // breathes), the spacing of the keys in device pixels, and the ceiling in force.
    std::string breath_exact_reason;
    float breath_key_spacing = 0;
    int breath_key_ceiling = 0;
    bool packed = false;

  private:
    struct impl;
    std::unique_ptr<impl> p;
};
} // namespace scottland::goo
