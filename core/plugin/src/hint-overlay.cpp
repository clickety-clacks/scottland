#include "hint-overlay.hpp"
#include <cairo.h>
#include <algorithm>
#include <cmath>
#include <wayfire/opengl.hpp>
#include <sstream>
#include <drm_fourcc.h>
extern "C" {
#include <wlr/render/wlr_texture.h>
}
namespace scottland::windowing
{
hint_node::hint_node() : node_t(false) {}
bool hint_node::update(double x, double y, const std::string& text, double size, const std::string& family,
    hint_rgb color, double scale, hint_rgb background, double background_opacity,
    bool goo, bool reduced_motion)
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
    background_opacity = std::clamp(background_opacity, 0.0, 1.0);
    key << ':' << background_opacity << ':' << background.r << ',' << background.g << ',' << background.b;
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
        // One backing for window and exterior widget hints alike: the palette background under
        // the usual hint-color tint, at the hint background opacity (WK42: 1 is solid, 0 none).
        auto fill = hint_mix(background, color, hint_badge_opacity);
        cairo_set_source_rgba(cr, fill.r, fill.g, fill.b, background_opacity);
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
    const bool popping = animate();
    return popping || (!reduced && (t < 1.0 || resize_t < 1.0));
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
void center_switcher_node::update(double output_width, const std::string& title, unsigned position,
    unsigned count, const hint_palette& palette, double scale)
{
    wf::scene::damage_node(this, box);
    texture.reset();
    std::string caption = count ? std::to_string(position) + " / " + std::to_string(count) + "   " +
        (title.empty() ? "Window" : title) : "No center windows";
    double font_size = std::clamp(15.0 * palette.text_scale, 12.0, 27.0);
    double height = std::ceil(font_size + 22);
    double max_text_width = std::max(80.0, output_width - 76.0);
    auto measure = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, 1, 1);
    auto cr = cairo_create(measure);
    cairo_select_font_face(cr, palette.font_family.c_str(), CAIRO_FONT_SLANT_NORMAL, CAIRO_FONT_WEIGHT_BOLD);
    cairo_set_font_size(cr, font_size);
    cairo_text_extents_t ext;
    cairo_text_extents(cr, caption.c_str(), &ext);
    bool shortened = false;
    while (ext.width > max_text_width && !caption.empty())
    {
        // Drop one UTF-8 codepoint before adding an ellipsis; titles may be localized.
        size_t start = caption.size() - 1;
        while (start && (static_cast<unsigned char>(caption[start]) & 0xc0) == 0x80) --start;
        caption.erase(start);
        shortened = true;
        std::string shown = caption + "…";
        cairo_text_extents(cr, shown.c_str(), &ext);
    }
    if (shortened) caption += "…";
    double width = std::min(output_width - 24.0, std::ceil(ext.width + 34));
    width = std::max(40.0, width);
    box = {std::round((output_width - width) / 2.0), 18, std::round(width), std::round(height)};
    double raster_scale = std::max(1.0, scale);
    pixel_width = std::max(1, int(std::ceil(box.width * raster_scale)));
    pixel_height = std::max(1, int(std::ceil(box.height * raster_scale)));
    pixels.assign(pixel_width * pixel_height * 4, 0);
    auto surface = cairo_image_surface_create_for_data(pixels.data(), CAIRO_FORMAT_ARGB32,
        pixel_width, pixel_height, pixel_width * 4);
    auto draw = cairo_create(surface);
    cairo_scale(draw, double(pixel_width) / box.width, double(pixel_height) / box.height);
    double w = box.width, h = box.height, r = std::min(12.0, h / 2.0);
    cairo_new_sub_path(draw);
    cairo_arc(draw, w - r, r, r, -M_PI / 2, 0);
    cairo_arc(draw, w - r, h - r, r, 0, M_PI / 2);
    cairo_arc(draw, r, h - r, r, M_PI / 2, M_PI);
    cairo_arc(draw, r, r, r, M_PI, 3 * M_PI / 2);
    cairo_close_path(draw);
    cairo_set_source_rgba(draw, palette.background.r, palette.background.g, palette.background.b, .94);
    cairo_fill_preserve(draw);
    cairo_set_source_rgba(draw, palette.accent.r, palette.accent.g, palette.accent.b, .80);
    cairo_set_line_width(draw, 1.5);
    cairo_stroke(draw);
    cairo_select_font_face(draw, palette.font_family.c_str(), CAIRO_FONT_SLANT_NORMAL, CAIRO_FONT_WEIGHT_BOLD);
    cairo_set_font_size(draw, font_size);
    cairo_text_extents(draw, caption.c_str(), &ext);
    cairo_move_to(draw, (w - ext.width) / 2 - ext.x_bearing, (h - ext.height) / 2 - ext.y_bearing);
    cairo_set_source_rgb(draw, palette.foreground.r, palette.foreground.g, palette.foreground.b);
    cairo_show_text(draw, caption.c_str());
    cairo_destroy(draw);
    cairo_surface_flush(surface);
    cairo_surface_destroy(surface);
    cairo_destroy(cr);
    cairo_surface_destroy(measure);
    wf::scene::damage_node(this, box);
    wf::scene::update(shared_from_this(), wf::scene::update_flag::GEOMETRY);
}

class center_switcher_render : public wf::scene::simple_render_instance_t<center_switcher_node>
{
  public:
    using simple_render_instance_t::simple_render_instance_t;
    void render(const wf::scene::render_instruction_t& data) override
    {
        if (!self->texture && !self->pixels.empty())
        {
            auto tex = wlr_texture_from_pixels(data.pass->get_wlr_renderer(), DRM_FORMAT_ARGB8888,
                self->pixel_width * 4, self->pixel_width, self->pixel_height, self->pixels.data());
            if (tex) self->texture = wf::texture_t::from_texture(tex);
        }
        if (self->texture) data.pass->add_texture(self->texture, data.target, self->box, data.damage, 1);
    }
};

void center_switcher_node::gen_render_instances(std::vector<wf::scene::render_instance_uptr>& instances,
    wf::scene::damage_callback damage, wf::output_t *output)
{
    instances.push_back(std::make_unique<center_switcher_render>(this, damage, output));
}

void hint_flash_node::update(wf::geometry_t geometry, double corner_radius, hint_rgb dye, double strength)
{
    wf::scene::damage_node(this, box);
    box = geometry;
    radius = std::max(0.0, corner_radius);
    color = dye;
    alpha = std::clamp(strength, 0.0, 1.0);
    wf::scene::damage_node(this, box);
    wf::scene::update(shared_from_this(), wf::scene::update_flag::GEOMETRY);
}

class hint_flash_render : public wf::scene::simple_render_instance_t<hint_flash_node>
{
  public:
    using simple_render_instance_t::simple_render_instance_t;
    void render(const wf::scene::render_instruction_t& data) override
    {
        auto b = self->box;
        if (self->alpha <= 0 || b.width <= 0 || b.height <= 0) return;
        double r = std::min({self->radius, b.width / 2.0, b.height / 2.0});
        double a = self->alpha;
        wf::color_t tint{self->color.r * a, self->color.g * a, self->color.b * a, a};
        auto strip = [&] (double x, double y, double w, double h)
        {
            if (w > 0 && h > 0)
                data.pass->add_rect(tint, data.target, {x, y, w, h}, data.damage);
        };
        int rows = int(std::ceil(r));
        strip(b.x, b.y + rows, b.width, std::max(0.0, b.height - 2 * rows));
        for (int row = 0; row < rows; ++row)
        {
            double distance = std::max(0.0, r - row - .5);
            double inset = r - std::sqrt(std::max(0.0, r * r - distance * distance));
            strip(b.x + inset, b.y + row, b.width - 2 * inset, 1);
            strip(b.x + inset, b.y + b.height - row - 1, b.width - 2 * inset, 1);
        }
    }
};

void hint_flash_node::gen_render_instances(std::vector<wf::scene::render_instance_uptr>& instances,
    wf::scene::damage_callback damage, wf::output_t *output)
{
    instances.push_back(std::make_unique<hint_flash_render>(this, damage, output));
}

namespace
{
const char *tint_vertex_source = R"(#version 100
attribute highp vec2 position;
varying highp vec2 pos;
uniform mat4 MVP;
void main() {
    gl_Position = MVP * vec4(position, 0.0, 1.0);
    pos = position;
})";
const char *tint_fragment_source = R"(#version 100
varying highp vec2 pos;
uniform highp vec4 rect;
uniform highp float radius;
uniform highp float aa;
uniform highp vec4 color;
void main() {
    highp vec2 half_size = rect.zw * 0.5;
    highp vec2 q = abs(pos - rect.xy - half_size) - half_size + radius;
    highp float d = length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - radius;
    highp float coverage = clamp(0.5 - d / aa, 0.0, 1.0);
    highp float alpha = color.a * coverage;
    gl_FragColor = vec4(color.rgb * alpha, alpha);
})";
struct tint_program_t
{
    OpenGL::program_t program;
    bool ready = false;
};
tint_program_t& tint_program()
{
    static tint_program_t program;  // per loaded plugin copy (see meson.build)
    return program;
}
}

void release_window_tint_gl()
{
    auto& p = tint_program();
    if (!p.ready) return;
    wf::gles::run_in_context_if_gles([&] { p.program.free_resources(); });
    p.ready = false;
}

void window_tint_layer_node::update(wf::geometry_t bounds, double alpha, shape_provider_t provider)
{
    bounds.width = std::max(0.0, bounds.width);
    bounds.height = std::max(0.0, bounds.height);
    alpha = std::clamp(alpha, 0.0, 1.0);
    bool geometry_changed = bounds != box;
    bool strength_changed = alpha != strength;
    if (geometry_changed || strength_changed)
    {
        wf::scene::damage_node(this, box);
        box = bounds;
        strength = alpha;
        wf::scene::damage_node(this, box);
        wf::scene::update(shared_from_this(), wf::scene::update_flag::GEOMETRY);
    }
    shapes = std::move(provider);
}

class window_tint_render : public wf::scene::simple_render_instance_t<window_tint_layer_node>
{
  public:
    using simple_render_instance_t::simple_render_instance_t;
    void render(const wf::scene::render_instruction_t& data) override
    {
        if (self->strength <= 0 || !self->shapes) return;
        auto shapes = self->shapes();
        if (shapes.empty()) return;
        bool drawn = data.pass->custom_gles_subpass([&]
        {
            auto& p = tint_program();
            if (!p.ready) { p.program.compile(tint_vertex_source, tint_fragment_source); p.ready = true; }
            wf::gles::bind_render_buffer(data.target);
            p.program.use(wf::TEXTURE_TYPE_RGBA);
            p.program.uniformMatrix4f("MVP", wf::gles::render_target_orthographic_projection(data.target));
            p.program.uniform1f("aa", 1.0f / std::max(0.01f, float(data.target.scale)));
            glEnable(GL_BLEND);
            glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);
            wf::gles::for_each_scissor_rect(data.target, data.damage, [&]
            {
                for (const auto& shape : shapes)
                {
                    if (shape.width <= 0 || shape.height <= 0) continue;
                    double radius = std::clamp(shape.radius, 0.0, std::min(shape.width, shape.height) / 2);
                    GLfloat vertices[] = {float(shape.x), float(shape.y + shape.height),
                        float(shape.x + shape.width), float(shape.y + shape.height),
                        float(shape.x + shape.width), float(shape.y), float(shape.x), float(shape.y)};
                    p.program.uniform4f("rect", glm::vec4{shape.x, shape.y, shape.width, shape.height});
                    p.program.uniform1f("radius", radius);
                    p.program.uniform4f("color", glm::vec4{shape.color.r, shape.color.g,
                        shape.color.b, self->strength});
                    p.program.attrib_pointer("position", 2, 0, vertices);
                    glDrawArrays(GL_TRIANGLE_FAN, 0, 4);
                }
            });
            p.program.deactivate();
        });
        if (drawn) return;
        // The software path keeps the rounded silhouette with device-pixel scanlines.
        const double pixel = 1.0 / std::max(0.01f, float(data.target.scale));
        for (const auto& shape : shapes)
        {
            if (shape.width <= 0 || shape.height <= 0) continue;
            const double radius = std::clamp(shape.radius, 0.0, std::min(shape.width, shape.height) / 2);
            auto fill = wf::color_t{shape.color.r * self->strength, shape.color.g * self->strength,
                shape.color.b * self->strength, self->strength};
            auto strip = [&] (double x, double y, double width, double height)
            {
                int x1 = int(std::floor(x)), y1 = int(std::floor(y));
                int x2 = int(std::ceil(x + width)), y2 = int(std::ceil(y + height));
                if (x2 > x1 && y2 > y1)
                    data.pass->add_rect(fill, data.target, {x1, y1, x2 - x1, y2 - y1}, data.damage);
            };
            if (radius <= 0)
            {
                strip(shape.x, shape.y, shape.width, shape.height);
                continue;
            }
            auto cap = [&] (bool bottom)
            {
                for (double offset = 0; offset < radius; offset += pixel)
                {
                    const double height = std::min(pixel, radius - offset);
                    const double depth = offset + height / 2;
                    const double inset = radius - std::sqrt(std::max(0.0,
                        radius * radius - std::pow(radius - depth, 2)));
                    const double y = bottom ? shape.y + shape.height - radius + offset : shape.y + offset;
                    strip(shape.x + inset, y, std::max(0.0, shape.width - 2 * inset), height);
                }
            };
            cap(false);
            cap(true);
            strip(shape.x, shape.y + radius, shape.width, std::max(0.0, shape.height - 2 * radius));
        }
    }
};

void window_tint_layer_node::gen_render_instances(std::vector<wf::scene::render_instance_uptr>& instances,
    wf::scene::damage_callback damage, wf::output_t *output)
{
    instances.push_back(std::make_unique<window_tint_render>(this, damage, output));
}

}
