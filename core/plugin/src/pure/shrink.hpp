// The goo's breathing shrink (GO19/GO20) as a worker job: while the goo sleeps, shrink each band
// rectangle to its wet field plus a reconstruction margin. Pure: a value snapshot in, rectangles
// out; the goo node builds regions and installs the result on the main thread.
#pragma once
#include "../goo-model.hpp"
#include "worker.hpp"
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
 * mask; charged before each operation. Calibrated by tests/worker-unit.sh (nacelle, aarch64,
 * optimized, 2026-10-04): one density term costs about 35 ns, so a step allowance of 60,000 units
 * is about 2 ms and the whole-job cap of 9,000,000 units about 300 ms. One operation (a density
 * call) is bounded by the source count: about 9 us at the 256-source snapshot limit.
 */
constexpr uint64_t shrink_step_units = 60000;
constexpr uint64_t shrink_cap_units = 9000000;

class shrink_job_t : public job_t
{
  public:
    explicit shrink_job_t(shrink_snapshot_t snapshot, uint64_t cap_units = shrink_cap_units);
    bool step(cancel_t& cancel) override;
    std::unique_ptr<result_t> result(outcome_t& outcome) override;
    /** Snapshot bytes, for the 8 MB limit (shared shape pixels included). */
    static size_t snapshot_bytes(const shrink_snapshot_t& snapshot);

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
