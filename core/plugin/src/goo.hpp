#pragma once
#include "goo-model.hpp"
#include <functional>
#include <memory>
#include <wayfire/output.hpp>
#include <wayfire/geometry.hpp>

namespace scottland
{
namespace work { class worker_t; }
class frame_t;
enum class handle_t;
// The desktop model supplies logical state and samples its frame presentation.
// Goo owns GPU resources and derived field snapshots, never widget or drag state.
class goo_t
{
  public:
    goo_t();
    ~goo_t();
    using source_provider_t = std::function<std::vector<goo::source_t>(wf::output_t *)>;
    void start(source_provider_t snapshot, std::function<void(wf::output_t *, bool)> screen_changed);
    void stop();
    /** The worker that shrinks breathing strips off the main loop (GO19). Without one (it could
     *  not start, or broke), the conservative strips stay in use. Set before start(). */
    void set_worker(work::worker_t *worker);

  private:
    struct impl;
    std::unique_ptr<impl> p;
};
bool goo_enabled();
double goo_hover_distance();
handle_t goo_handle(const frame_t &frame, wf::pointf_t point);
double goo_thickness(double scale, double swell);
void goo_impulse(const frame_t &frame, float strength);
void goo_wake(const frame_t &frame);
} // namespace scottland
