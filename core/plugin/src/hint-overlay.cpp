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
    hint_rgb color, double scale, std::optional<hint_rgb> background, bool goo, bool reduced_motion)
{
    reduced = reduced_motion;
    if (hiding) { from = pop; hiding = false; started = clock::now(); }
    if (relocating) { move_x = cx - x; move_y = cy - y; moved = clock::now(); relocating = false; }
    double t = std::clamp(std::chrono::duration<double>(clock::now() - moved).count() / .16, 0., 1.);
    double remaining = reduced ? 0 : 1 - t * t * (3 - 2 * t);
    cx = x + move_x * remaining; cy = y + move_y * remaining;
    if (size_to != std::round(size))
    {
        size_from = size_to == 0 ? std::round(size) : diameter;
        size_to = std::round(size); resized = clock::now();
    }
    double resize_t = reduced ? 1 : std::clamp(
        std::chrono::duration<double>(clock::now() - resized).count() / .16, 0., 1.);
    diameter = size_from + (size_to - size_from) * resize_t * resize_t * (3 - 2 * resize_t);
    dye = color; padding = goo ? 0 : 8;
    int logical_size = int(std::round(size));
    int canvas_size = logical_size + 2 * padding;
    std::ostringstream key;
    key << text << ':' << family << ':' << logical_size << ':' << color.r << ',' << color.g << ',' << color.b << ':' << scale << ':' << goo;
    if (background) key << ':' << background->r << ',' << background->g << ',' << background->b;
    if (key.str() != appearance)
    {
        wf::scene::damage_node(this, box);
        drawn_opacity = -1;
        appearance = key.str(); texture.reset();
        pixel_size = int(std::ceil(canvas_size * std::max(1.0, scale)));
        pixels.assign(pixel_size * pixel_size * 4, 0);
        auto surface = cairo_image_surface_create_for_data(pixels.data(), CAIRO_FORMAT_ARGB32,
            pixel_size, pixel_size, pixel_size * 4);
        auto cr = cairo_create(surface);
        cairo_scale(cr, double(pixel_size) / canvas_size, double(pixel_size) / canvas_size);
        double mid = canvas_size / 2.0, radius = logical_size / 2.0;
        if (!goo)
        {
            // Soft fallback halo plus a crisp dyed edge, outside the unchanged badge.
            for (int band = 7; band >= 1; --band)
            {
                cairo_arc(cr, mid, mid, radius + band / 2.0, 0, 2 * M_PI);
                cairo_set_line_width(cr, band);
                cairo_set_source_rgba(cr, color.r, color.g, color.b, .035);
                cairo_stroke(cr);
            }
            cairo_arc(cr, mid, mid, radius + 1, 0, 2 * M_PI);
            cairo_set_line_width(cr, 2);
            cairo_set_source_rgb(cr, color.r, color.g, color.b); cairo_stroke(cr);
        }
        cairo_arc(cr, mid, mid, radius, 0, 2 * 3.141592653589793);
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
    animate();
}
void hint_node::geometry()
{
    double size = diameter * pop;
    circle = {cx - size / 2, cy - size / 2, size, size};
    double pad = padding * pop;
    wf::geometry_t next{circle.x - pad, circle.y - pad, size + 2 * pad, size + 2 * pad};
    if (next == box && opacity == drawn_opacity) return;
    wf::scene::damage_node(this, box);
    box = next; drawn_opacity = opacity;
    wf::scene::damage_node(this, box);
    wf::scene::update(shared_from_this(), wf::scene::update_flag::GEOMETRY);
}
void hint_node::hide(bool reduced_motion)
{
    if (hiding) return;
    reduced = reduced_motion; from = pop; hiding = true; started = clock::now();
}
bool hint_node::animate()
{
    double t = reduced ? 1 : std::clamp(std::chrono::duration<double>(clock::now() - started).count() /
        (hiding ? .1 : .2), 0., 1.);
    // One small spring overshoot (6%), with zero velocity at settlement; no ringing.
    double u = t - 1, spring = 1 + 2.2 * u * u * u + 1.2 * u * u;
    pop = hiding ? from * (1 - t * t * (3 - 2 * t)) : from + (1 - from) * spring;
    opacity = std::clamp(pop, 0., 1.);
    geometry();
    return t < 1;
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
        if (self->texture && self->opacity > 0 && self->box.width > 0)
            data.pass->add_texture(self->texture, data.target, self->box, data.damage, self->opacity);
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
