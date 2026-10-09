#pragma once

#include <cairo.h>
#include <wayfire/scene.hpp>
#include <wayfire/scene-render.hpp>
#include <wayfire/render.hpp>
#include "hint-style.hpp"
#include <string>
#include <vector>

namespace scottland::windowing
{
struct note_panel_layout_t
{
    bool drawable = false;
    bool show_writer = true;
    double width = 0, height = 0;
    double writer_font = 0, text_font = 0;
    double writer_line_height = 0, text_line_height = 0;
    double padding = 0, border = 0, radius = 0, writer_gap = 0;
    std::vector<std::string> writer_lines;
    std::vector<std::string> text_lines;
};

class note_layout_t
{
  public:
    note_layout_t();
    ~note_layout_t();
    note_layout_t(const note_layout_t&) = delete;
    note_layout_t& operator =(const note_layout_t&) = delete;

    note_panel_layout_t panel(const std::string& writer, const std::string& text,
        const hint_palette& palette, double width_limit, double height_limit,
        bool include_writer = true);
    double marker_width(const std::string& label, const hint_palette& palette);

  private:
    cairo_surface_t *surface = nullptr;
    cairo_t *cr = nullptr;
    double measure(const std::string& text, const std::string& family, double size,
        cairo_font_weight_t weight = CAIRO_FONT_WEIGHT_NORMAL);
    std::vector<std::string> wrap(const std::string& text, const std::string& family,
        double size, double width);
    std::string ellipsize(std::string text, const std::string& family, double size, double width);
};

struct note_overlay_item_t
{
    enum class kind_t { dot, marker, panel } kind = kind_t::dot;
    wf::geometry_t box{0, 0, 0, 0};
    std::string label;
    note_panel_layout_t panel;
};

class note_overlay_node_t : public wf::scene::node_t
{
  public:
    note_overlay_node_t() : node_t(false) {}
    void update(const std::vector<note_overlay_item_t>& items, const hint_palette& palette,
        double output_scale);
    wf::geometry_t get_bounding_box() override { return box; }
    void gen_render_instances(std::vector<wf::scene::render_instance_uptr>& instances,
        wf::scene::damage_callback damage, wf::output_t *output) override;

    wf::geometry_t box{0, 0, 0, 0};
    std::shared_ptr<wf::texture_t> texture;
    std::vector<unsigned char> pixels;
    int pixel_width = 0, pixel_height = 0;

  private:
    std::string signature;
};
} // namespace scottland::windowing
