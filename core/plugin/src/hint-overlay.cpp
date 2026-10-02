#include "hint-overlay.hpp"
#include <cairo.h>
#include <cmath>
#include <sstream>
#include <drm_fourcc.h>
extern "C" {
#include <wlr/render/wlr_texture.h>
}
namespace scottland::windowing
{
hint_node::hint_node() : node_t(false) {}
void hint_node::update(double x, double y, const std::string& text, double size, const std::string& family,
    hint_rgb color, double scale, std::optional<hint_rgb> background)
{
    wf::scene::damage_node(this, box);
    int logical_size = int(std::round(size));
    box = {std::round(x - logical_size / 2.0), std::round(y - logical_size / 2.0),
        double(logical_size), double(logical_size)};
    std::ostringstream key;
    key << text << ':' << family << ':' << logical_size << ':' << color.r << ',' << color.g << ',' << color.b << ':' << scale;
    if (background) key << ':' << background->r << ',' << background->g << ',' << background->b;
    if (key.str() != appearance)
    {
        appearance = key.str(); texture.reset();
        pixel_size = int(std::ceil(logical_size * std::max(1.0, scale)));
        pixels.assign(pixel_size * pixel_size * 4, 0);
        auto surface = cairo_image_surface_create_for_data(pixels.data(), CAIRO_FORMAT_ARGB32,
            pixel_size, pixel_size, pixel_size * 4);
        auto cr = cairo_create(surface);
        cairo_scale(cr, double(pixel_size) / logical_size, double(pixel_size) / logical_size);
        double mid = logical_size / 2.0;
        cairo_arc(cr, mid, mid, mid, 0, 2 * 3.141592653589793);
        // Exterior widget hints sit over arbitrary wallpaper, not the themed app surface.
        // Give their circle the palette background under its usual tint (WK26).
        if (background)
        {
            auto fill = hint_mix(*background, color, hint_badge_opacity);
            cairo_set_source_rgb(cr, fill.r, fill.g, fill.b);
        } else cairo_set_source_rgba(cr, color.r, color.g, color.b, hint_badge_opacity);
        cairo_fill(cr);
        cairo_select_font_face(cr, family.empty() ? "sans-serif" : family.c_str(), CAIRO_FONT_SLANT_NORMAL,
            CAIRO_FONT_WEIGHT_BOLD);
        cairo_set_font_size(cr, std::round(logical_size * (text.size() > 1 ? 0.46 : 0.62)));
        cairo_text_extents_t ext; cairo_text_extents(cr, text.c_str(), &ext);
        cairo_move_to(cr, mid - ext.width / 2 - ext.x_bearing, mid - ext.height / 2 - ext.y_bearing);
        cairo_set_source_rgb(cr, color.r, color.g, color.b);
        cairo_show_text(cr, text.c_str());
        cairo_destroy(cr); cairo_surface_flush(surface); cairo_surface_destroy(surface);
    }
    wf::scene::damage_node(this, box);
    wf::scene::update(shared_from_this(), wf::scene::update_flag::GEOMETRY);
}
class hint_render : public wf::scene::simple_render_instance_t<hint_node>
{
  public:
    using simple_render_instance_t::simple_render_instance_t;
    void render(const wf::scene::render_instruction_t& data) override
    {
        if (!self->texture && !self->pixels.empty())
        {
            auto tex = wlr_texture_from_pixels(data.pass->get_wlr_renderer(), DRM_FORMAT_ARGB8888,
                self->pixel_size * 4, self->pixel_size, self->pixel_size, self->pixels.data());
            if (tex) self->texture = wf::texture_t::from_texture(tex);
        }
        if (self->texture) data.pass->add_texture(self->texture, data.target, self->box, data.damage);
    }
};
void hint_node::gen_render_instances(std::vector<wf::scene::render_instance_uptr>& instances,
    wf::scene::damage_callback damage, wf::output_t *output)
{
    instances.push_back(std::make_unique<hint_render>(this, damage, output));
}
void fullscreen_hint_node::update(wf::geometry_t geometry, hint_rgb dye)
{
    wf::scene::damage_node(this, box);
    box = geometry; color = dye;
    wf::scene::damage_node(this, box);
    wf::scene::update(shared_from_this(), wf::scene::update_flag::GEOMETRY);
}
class fullscreen_hint_render : public wf::scene::simple_render_instance_t<fullscreen_hint_node>
{
  public:
    using simple_render_instance_t::simple_render_instance_t;
    void render(const wf::scene::render_instruction_t& data) override
    {
        auto r = self->box; auto c = self->color;
        // The render pass takes premultiplied colors (the frame shader composites explicitly).
        data.pass->add_rect({c.r * hint_window_opacity, c.g * hint_window_opacity,
            c.b * hint_window_opacity, hint_window_opacity}, data.target, r, data.damage);
        double line = hint_border_width;
        wf::color_t rim{c.r, c.g, c.b, 1};
        for (auto strip : std::vector<wf::geometry_t>{{r.x, r.y, r.width, line},
            {r.x, r.y + r.height - line, r.width, line},
            {r.x, r.y + line, line, r.height - 2 * line},
            {r.x + r.width - line, r.y + line, line, r.height - 2 * line}})
            data.pass->add_rect(rim, data.target, strip, data.damage);
    }
};
void fullscreen_hint_node::gen_render_instances(std::vector<wf::scene::render_instance_uptr>& instances,
    wf::scene::damage_callback damage, wf::output_t *output)
{
    instances.push_back(std::make_unique<fullscreen_hint_render>(this, damage, output));
}

}
