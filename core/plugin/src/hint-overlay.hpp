#pragma once
#include <wayfire/scene.hpp>
#include <wayfire/scene-render.hpp>
#include <wayfire/render.hpp>
#include "hint-style.hpp"
#include <string>
#include <optional>
#include <vector>
#include <chrono>
namespace scottland::windowing
{
// Click-through compositor overlay; its caller supplies the stable visible-region attachment.
class hint_node : public wf::scene::node_t
{
  public:
    hint_node();
    bool update(double x, double y, const std::string& text, double size, const std::string& family,
        hint_rgb color, double scale, std::optional<hint_rgb> background, bool goo, bool reduced_motion);
    void hide(bool reduced_motion);
    bool animate();
    void relocate() { relocating = true; }
    wf::geometry_t circle{0, 0, 0, 0};
    hint_rgb dye{};
    double pop = 0, opacity = 0;
    wf::geometry_t get_bounding_box() override { return box; }
    void gen_render_instances(std::vector<wf::scene::render_instance_uptr>& instances,
        wf::scene::damage_callback damage, wf::output_t *output) override;
    wf::geometry_t box{0, 0, 72, 72};
    std::shared_ptr<wf::texture_t> texture;
    std::vector<unsigned char> pixels;
    int pixel_size = 72;
  private:
    std::string appearance;
    using clock = std::chrono::steady_clock;
    clock::time_point started = clock::now(), moved = started, resized = started;
    bool hiding = false, reduced = false, relocating = false;
    double from = 0, cx = 0, cy = 0, diameter = 72, padding = 0;
    double move_x = 0, move_y = 0, drawn_opacity = -1, size_from = 0, size_to = 0;
    void geometry();
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

// A small, click-through name plate while a quick Alt+Tab chord previews a center window.
class center_switcher_node : public wf::scene::node_t
{
  public:
    center_switcher_node() : node_t(false) {}
    void update(double output_width, const std::string& title, unsigned position, unsigned count,
        const hint_palette& palette, double scale);
    wf::geometry_t get_bounding_box() override { return box; }
    void gen_render_instances(std::vector<wf::scene::render_instance_uptr>& instances,
        wf::scene::damage_callback damage, wf::output_t *output) override;
    wf::geometry_t box{0, 0, 0, 0};
    std::shared_ptr<wf::texture_t> texture;
    std::vector<unsigned char> pixels;
    int pixel_width = 0, pixel_height = 0;
};

// A short visual acknowledgement of a completed hint press, on windows and cards alike.
class hint_flash_node : public wf::scene::node_t
{
  public:
    hint_flash_node() : node_t(false) {}
    void update(wf::geometry_t geometry, double corner_radius, hint_rgb dye, double strength);
    wf::geometry_t get_bounding_box() override { return box; }
    void gen_render_instances(std::vector<wf::scene::render_instance_uptr>& instances,
        wf::scene::damage_callback damage, wf::output_t *output) override;
    wf::geometry_t box{0, 0, 0, 0};
    hint_rgb color{0, 0, 0};
    double radius = 0, alpha = 0;
};

}
