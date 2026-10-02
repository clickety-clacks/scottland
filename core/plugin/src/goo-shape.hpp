#pragma once
#include "goo-model.hpp"
#include <functional>
#include <wayfire/opengl.hpp>

namespace scottland::goo
{
// Per-frame presentation resource, independent of widget lifecycle/model state.
// Called in a GLES subpass with the same content drawing used by the frame.
class shape_cache_t
{
  public:
    static void prepare();
    static void release_programs();
    shape_cache_t();
    ~shape_cache_t();
    std::shared_ptr<const shape_t> shape;
    uint64_t checks = 0, builds = 0;
    double check_ms = 0, rebuild_ms = 0;
    bool update(glm::vec4 bounds, const std::function<void(const wf::render_target_t &)> &draw);
    GLuint texture() const;

  private:
    struct impl;
    std::unique_ptr<impl> p;
};
} // namespace scottland::goo
