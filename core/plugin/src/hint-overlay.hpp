#pragma once
#include <wayfire/scene.hpp>
#include <wayfire/scene-render.hpp>
#include <wayfire/render.hpp>
#include "hint-style.hpp"
#include <string>
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
        hint_rgb color, double scale, hint_rgb background, double background_opacity,
        bool goo, bool reduced_motion);
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
    void update(wf::geometry_t geometry, hint_rgb dye, double tint);
    wf::geometry_t get_bounding_box() override { return box; }
    void gen_render_instances(std::vector<wf::scene::render_instance_uptr>& instances,
        wf::scene::damage_callback damage, wf::output_t *output) override;
    wf::geometry_t box{0, 0, 0, 0};
    hint_rgb color{0, 0, 0};
    double alpha = hint_window_opacity;
};

// WK37: an opaque rounded outline of a mostly occluded window's drawn frame, at the front of
// the overlay layer (above all windows, below the hint circles). Only the ring is drawn, by a
// small shader that antialiases it at the output's device pixels (strips without GLES).
class hint_outline_node : public wf::scene::node_t
{
  public:
    hint_outline_node() : node_t(false) {}
    void update(double x, double y, double width, double height, double corner_radius,
        hint_rgb dye, double line_width);
    wf::geometry_t get_bounding_box() override { return box; }
    void gen_render_instances(std::vector<wf::scene::render_instance_uptr>& instances,
        wf::scene::damage_callback damage, wf::output_t *output) override;
    wf::geometry_t box{0, 0, 0, 0};
    double x = 0, y = 0, width = 0, height = 0;
    hint_rgb color{0, 0, 0};
    double radius = 0, line = hint_outline_width;
};
// WK39: a hold in progress, as a stroked arc that fills clockwise from the top around a hint
// badge or the pointer (hold_ring_progress). One small shader quad; dots without GLES.
class hold_ring_node : public wf::scene::node_t
{
  public:
    hold_ring_node() : node_t(false) {}
    void update(double cx, double cy, double radius, double line, hint_rgb color, double progress);
    wf::geometry_t get_bounding_box() override { return box; }
    void gen_render_instances(std::vector<wf::scene::render_instance_uptr>& instances,
        wf::scene::damage_callback damage, wf::output_t *output) override;
    wf::geometry_t box{0, 0, 0, 0};
    double cx = 0, cy = 0, radius = 0, line = hold_ring_width, progress = 0;
    hint_rgb color{0, 0, 0};
};
// Compiles the outline and ring shaders up front, so the first hold ring or outline costs no
// compile on an input path (P8). Call in a GL context (wf::gles::run_in_context_if_gles).
void prepare_hint_gl();
// Frees the outline and ring shaders; call with the plugin's other GL resources on unload.
void release_hint_gl();

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
