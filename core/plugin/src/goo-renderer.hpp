#pragma once
#include <functional>
#include "goo-allowance.hpp"
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
    // Test probe only (goo-state): a waiting GPU read. Input never calls it (main-loop Phase 3).
    glm::vec4 sample_at(glm::vec2 point);
    float energy = 1, wave_energy = 1, dye_energy = 1;
    // The settle check (GO10) without waiting (main-loop Phase 3): every 30th step issues the
    // energy reduction into a pixel buffer with a fence; readings are collected later, without
    // waiting, inside a render pass or from the goo's collection timer. A reading applies only
    // if nothing changed since it was issued (generation, invalidation counter, size) and it is newer than
    // the last applied one. Without pixel buffers and fences (GLES 2), after any readback
    // failure, or with the test switch, the simulation sleeps on time alone (timed_sleep()).
    uint64_t invalidation = 0;
    void invalidate() { invalidation++; }
    bool timed_sleep() const;
    bool force_timed_sleep = false;  // tests (goo-state), SCOTTLAND_TEST_MODEL only
    bool readback_pending() const;
    int readback_in_flight() const;
    uint64_t oldest_in_flight_step() const;  // 0 when none is in flight
    /** Examine at most `budget` busy slots (shared across outputs). In a GL context. */
    void collect(int &budget);
    /** The collection allowance shared by every output's renderer and the collection timer
     *  (goo-allowance.hpp); without one a renderer examines at most two slots per step. */
    collect_allowance_t *allowance = nullptr;
    /** The output changed (mode, scale, transform) or was recreated: readings in flight are
     *  retired and none issued before applies, even at equal dimensions. In a GL context. */
    void new_generation();
    uint64_t generation() const;
    // Tests only: "hold" (nothing is collected), "hold-oldest" (all but the oldest reading in
    // flight are collected), "incoming-pack-state", "prior-value",
    // "wait-failed", "map-failed", "unmap-failed"; "" also leaves the readback-failed fallback.
    void set_readback_fault(const std::string &fault);
    std::string readback_mode() const;
    std::string gl_description() const;  // GL version and renderer, once GL has been checked
    uint64_t readings_issued = 0, readings_applied = 0, readings_stale = 0, readings_skipped = 0;
    uint64_t last_applied_step = 0;
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
