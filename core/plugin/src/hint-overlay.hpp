#pragma once
#include <wayfire/scene.hpp>
#include <wayfire/scene-render.hpp>
#include <wayfire/render.hpp>
#include "hint-style.hpp"
#include <string>
#include <vector>
namespace scottland::windowing
{
// Click-through compositor overlay; its caller follows the actual transformed window center.
class hint_node : public wf::scene::node_t
{
  public:
    hint_node();
    void update(double x, double y, const std::string& text, double size,
        hint_rgb color, double scale = 1);
    wf::geometry_t get_bounding_box() override { return box; }
    void gen_render_instances(std::vector<wf::scene::render_instance_uptr>& instances,
        wf::scene::damage_callback damage, wf::output_t *output) override;
    wf::geometry_t box{0, 0, 72, 72};
    std::shared_ptr<wf::texture_t> texture;
    std::vector<unsigned char> pixels;
    int pixel_size = 72;
  private:
    std::string appearance;
};
// Fullscreen surfaces have no Scottland frame. Keep their tint in their own scene subtree
// (below the badges and other windows), with the square, inset rim fullscreen requires.
class fullscreen_hint_node : public wf::scene::node_t
{
  public:
    fullscreen_hint_node() : node_t(false) {}
    void update(wf::geometry_t geometry, hint_rgb dye);
    wf::geometry_t get_bounding_box() override { return box; }
    void gen_render_instances(std::vector<wf::scene::render_instance_uptr>& instances,
        wf::scene::damage_callback damage, wf::output_t *output) override;
    wf::geometry_t box{0, 0, 0, 0};
    hint_rgb color{0, 0, 0};
};

}
