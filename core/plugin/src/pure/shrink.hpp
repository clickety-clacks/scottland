// The goo's breathing shrink (GO19/GO20) as a worker job: while the goo sleeps, shrink each band
// rectangle to its wet field plus a reconstruction margin. Pure: a value snapshot in, rectangles
// out; the goo node builds regions and installs the result on the main thread.
#pragma once
#include "../goo-model.hpp"
#include "worker.hpp"
#include <atomic>
#include <memory>
#include <vector>

namespace scottland::work
{
struct rect_t { double x = 0, y = 0, width = 0, height = 0; };

struct shrink_snapshot_t
{
    std::vector<goo::source_t> sources;  // shapes are shared, immutable: their pixels count toward the size
    goo::settings_t settings;
    float time = 0;
    std::vector<rect_t> rects;           // the loose bands being shrunk
    double output_scale = 1;
    uint64_t incarnation = 0;            // the output's, at capture
};

struct shrink_result_t : result_t
{
    // One per snapshot rectangle: tight where it was fully refined, else the loose rectangle.
    // A rectangle is never partly shrunk (GO19's rule, per rectangle).
    std::vector<rect_t> rects;
    size_t refined = 0;
    uint64_t incarnation = 0;
};

/**
 * Work units: one per density term (one source at one sample point), one per source tested for a
 * mask; charged before each operation. Calibrated by tests/worker-unit.sh (the aarch64 test machine, aarch64,
 * optimized, 2026-10-04): one density term costs about 35 ns, so a step allowance of 60,000 units
 * is about 2 ms and the whole-job cap of 9,000,000 units about 300 ms. One operation (a density
 * call) is bounded by the source count: about 9 us at the 256-source snapshot limit.
 */
constexpr uint64_t shrink_step_units = 60000;
constexpr uint64_t shrink_cap_units = 9000000;

/**
 * Memory limits (design 3.2), checked before a snapshot is taken and again at submit. The
 * snapshot counts allocated storage: the source and rectangle arrays and every distinct shape's
 * pixels once (shapes are shared, but a retained shape is kept alive by the job). Scratch and
 * result are the job's rectangle output and its result copy, both bounded by the rectangle
 * count. Past any limit the goo keeps its loose bands (conservative: never less goo).
 */
constexpr size_t shrink_snapshot_limit = 8u << 20;
constexpr size_t shrink_result_limit = 1u << 20;
constexpr size_t shrink_max_sources = 256;
constexpr size_t shrink_max_rects = 4096;

class shrink_job_t : public job_t
{
  public:
    explicit shrink_job_t(shrink_snapshot_t snapshot, uint64_t cap_units = shrink_cap_units);
    bool step(cancel_t& cancel) override;
    std::unique_ptr<result_t> result(outcome_t& outcome) override;
    // Tests (goo-state "shrink_hold"): while set, a step blocks the worker without working (until
    // released or the worker stops), so a job can be observed running with a newer one pending.
    std::shared_ptr<const std::atomic<bool>> hold;
    bool admissible() const noexcept override { return within_limits(s.sources, s.rects.size(), s.rects.capacity()); }
    /** Snapshot bytes, for the 8 MB limit: allocated storage, each distinct shape's pixels
     *  once. No allocation (a linear scan: at most 256 sources). */
    static size_t snapshot_bytes(const shrink_snapshot_t& snapshot) noexcept;
    static size_t snapshot_bytes(const std::vector<goo::source_t>& sources, size_t rect_capacity) noexcept;
    /** Scratch and result bytes for `rects` rectangles: the job's output and the result's copy. */
    static size_t result_bytes(size_t rects) noexcept { return 2 * rects * sizeof(rect_t) + sizeof(shrink_result_t); }
    static bool within_limits(const std::vector<goo::source_t>& sources, size_t rects, size_t rect_capacity) noexcept
    {
        return sources.size() <= shrink_max_sources && rects <= shrink_max_rects &&
            result_bytes(rects) <= shrink_result_limit && snapshot_bytes(sources, rect_capacity) <= shrink_snapshot_limit;
    }

  private:
    shrink_snapshot_t s;
    uint64_t cap, spent = 0;
    bool capped = false;
    std::vector<rect_t> out;
    size_t refined = 0;
    // Resumable position.
    size_t index = 0;
    double y = 0, x = 0, x1 = 1e9, y1 = 1e9, x2 = -1e9, y2 = -1e9, step_x = 4, step_y = 4;
    bool row_started = false, in_row = false;
};
}
