#pragma once
#include <memory>
#include <wayfire/geometry.hpp>

namespace scottland
{
class frame_t;
enum class handle_t;
// The desktop supplies existing frame geometry/state. Goo owns no widget or drag state.
class goo_t
{
  public:
    goo_t();
    ~goo_t();
    void start();
    void stop();

  private:
    struct impl;
    std::unique_ptr<impl> p;
};
bool goo_enabled();
handle_t goo_handle(const frame_t &frame, wf::pointf_t point);
double goo_thickness(double scale, double swell);
void goo_impulse(const frame_t &frame, float strength);
} // namespace scottland
