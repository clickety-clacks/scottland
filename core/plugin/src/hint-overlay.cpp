#include "hint-overlay.hpp"
#include "loop.hpp"
#include <cairo.h>
#include <array>
#include <vector>
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
    hint_rgb color, double scale, std::optional<hint_rgb> background, double background_opacity,
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
    key << ':' << background_opacity;
    if (background) key << ':' << background->r << ',' << background->g << ',' << background->b;
    if (key.str() != appearance)
    {
        SCOTTLAND_LOOP_SCOPE(hint_raster);
        wf::scene::damage_node(this, box);
        drawn_opacity = -1;
        appearance = key.str(); texture.reset();
        // At most 512x512 pixels, whatever the text size and output scale.
        pixel_size = std::min(512, int(std::ceil(canvas_size * std::max(1.0, scale))));
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
            cairo_set_source_rgba(cr, fill.r, fill.g, fill.b, background_opacity);
        } else cairo_set_source_rgba(cr, color.r, color.g, color.b, hint_badge_opacity * background_opacity);
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
void warm_hint_text(const std::string& family)
{
    auto surface = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, 64, 64);
    auto cr = cairo_create(surface);
    cairo_select_font_face(cr, family.empty() ? "sans-serif" : family.c_str(), CAIRO_FONT_SLANT_NORMAL,
        CAIRO_FONT_WEIGHT_BOLD);
    for (double size : {9.0, 15.0, 22.0})
    {
        cairo_set_font_size(cr, size);
        cairo_move_to(cr, 2, 40);
        cairo_show_text(cr, "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/ \u2026");
    }
    cairo_destroy(cr);
    cairo_surface_destroy(surface);
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
        SCOTTLAND_LOOP_SCOPE(hint_render);
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
    SCOTTLAND_LOOP_SCOPE(hint_gen_render_instances);
    instances.push_back(std::make_unique<hint_render>(this, damage, output));
}
void fullscreen_hint_node::update(wf::geometry_t geometry, hint_rgb dye, double tint)
{
    if (geometry == box && dye.r == color.r && dye.g == color.g && dye.b == color.b && tint == alpha) return;
    wf::scene::damage_node(this, box);
    box = geometry; color = dye; alpha = std::clamp(tint, 0.0, 1.0);
    wf::scene::damage_node(this, box);
    wf::scene::update(shared_from_this(), wf::scene::update_flag::GEOMETRY);
}
class fullscreen_hint_render : public wf::scene::simple_render_instance_t<fullscreen_hint_node>
{
  public:
    using simple_render_instance_t::simple_render_instance_t;
    void render(const wf::scene::render_instruction_t& data) override
    {
        SCOTTLAND_LOOP_SCOPE(hint_render);
        auto r = self->box; auto c = self->color; double a = self->alpha;
        // The render pass takes premultiplied colors (the frame shader composites explicitly).
        if (a > 0)
            data.pass->add_rect({c.r * a, c.g * a, c.b * a, a}, data.target, r, data.damage);
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
    SCOTTLAND_LOOP_SCOPE(hint_gen_render_instances);
    instances.push_back(std::make_unique<fullscreen_hint_render>(this, damage, output));
}

void center_switcher_node::update(double output_width, const std::string& title, unsigned position,
    unsigned count, const hint_palette& palette, double scale)
{
    SCOTTLAND_LOOP_SCOPE(switcher_update);
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
    // At most 256 codepoints are shown, and the longest prefix that fits is found by binary
    // search: at most 9 measurements whatever the title (a 4,096-character title once took
    // 141 ms here, one measurement per removed codepoint).
    std::vector<size_t> ends;  // byte length of each codepoint prefix
    for (size_t at = 0; at < caption.size() && ends.size() < 256;)
    {
        do ++at; while (at < caption.size() && (static_cast<unsigned char>(caption[at]) & 0xc0) == 0x80);
        ends.push_back(at);
    }
    bool shortened = ends.size() && ends.back() < caption.size();
    if (shortened) caption.erase(ends.back());
    cairo_text_extents_t ext;
    cairo_text_extents(cr, caption.c_str(), &ext);
    if (ext.width > max_text_width)
    {
        size_t low = 0, high = ends.size();  // prefixes of `low` codepoints fit (with the ellipsis)
        while (low + 1 < high)
        {
            size_t mid = (low + high) / 2;
            std::string shown = caption.substr(0, ends[mid - 1]) + "…";
            cairo_text_extents(cr, shown.c_str(), &ext);
            if (ext.width <= max_text_width) low = mid; else high = mid;
        }
        caption.erase(low ? ends[low - 1] : 0);
        shortened = true;
        cairo_text_extents(cr, (caption + "…").c_str(), &ext);
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
        SCOTTLAND_LOOP_SCOPE(hint_render);
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
    SCOTTLAND_LOOP_SCOPE(hint_gen_render_instances);
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
        SCOTTLAND_LOOP_SCOPE(hint_render);
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
    SCOTTLAND_LOOP_SCOPE(hint_gen_render_instances);
    instances.push_back(std::make_unique<hint_flash_render>(this, damage, output));
}

namespace
{
const char *outline_vertex_source = R"(#version 100
attribute highp vec2 position;
varying highp vec2 pos;
uniform mat4 MVP;
void main() {
    gl_Position = MVP * vec4(position, 0.0, 1.0);
    pos = position;
})";
// Coverage of the band between the rounded rectangle's edge and `line` inside it, from its
// signed distance, antialiased over one device pixel (`aa` logical px).
const char *outline_fragment_source = R"(#version 100
varying highp vec2 pos;
uniform highp vec4 rect;
uniform highp float radius;
uniform highp float line;
uniform highp float aa;
uniform highp vec4 color;
void main() {
    highp vec2 half_size = rect.zw * 0.5;
    highp vec2 q = abs(pos - rect.xy - half_size) - half_size + radius;
    highp float d = length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - radius;
    highp float outer = clamp(0.5 - d / aa, 0.0, 1.0);
    highp float inner = clamp(0.5 - (d + line) / aa, 0.0, 1.0);
    gl_FragColor = color * (outer - inner);
})";
struct outline_program_t
{
    OpenGL::program_t program;
    bool ready = false;
};
outline_program_t& outline_program()
{
    static outline_program_t program;  // per loaded plugin copy (see meson.build)
    return program;
}
}

void release_hint_gl()
{
    auto& p = outline_program();
    if (!p.ready) return;
    wf::gles::run_in_context_if_gles([&] { p.program.free_resources(); });
    p.ready = false;
}

void hint_outline_node::update(double nx, double ny, double nwidth, double nheight, double corner_radius,
    hint_rgb dye, double line_width)
{
    corner_radius = std::max(0.0, corner_radius);
    line_width = std::max(1.0, line_width);
    if (nx == x && ny == y && nwidth == width && nheight == height && corner_radius == radius &&
        line_width == line && dye.r == color.r && dye.g == color.g && dye.b == color.b) return;
    wf::scene::damage_node(this, box);
    x = nx; y = ny; width = std::max(0.0, nwidth); height = std::max(0.0, nheight);
    radius = corner_radius; color = dye; line = line_width;
    // One logical px of margin holds the antialiased outer edge.
    double x1 = std::floor(x) - 1, y1 = std::floor(y) - 1;
    box = {x1, y1, std::ceil(x + width) + 1 - x1, std::ceil(y + height) + 1 - y1};
    wf::scene::damage_node(this, box);
    wf::scene::update(shared_from_this(), wf::scene::update_flag::GEOMETRY);
}

class hint_outline_render : public wf::scene::simple_render_instance_t<hint_outline_node>
{
  public:
    using simple_render_instance_t::simple_render_instance_t;
    void render(const wf::scene::render_instruction_t& data) override
    {
        SCOTTLAND_LOOP_SCOPE(hint_render);
        double x = self->x, y = self->y, w = self->width, h = self->height;
        if (w <= 0 || h <= 0) return;
        double line = std::min({self->line, w / 2, h / 2});
        double r = std::min({self->radius, w / 2, h / 2});
        auto c = self->color;
        bool drawn = data.pass->custom_gles_subpass([&]
        {
            auto& p = outline_program();
            if (!p.ready) { p.program.compile(outline_vertex_source, outline_fragment_source); p.ready = true; }
            wf::gles::bind_render_buffer(data.target);
            p.program.use(wf::TEXTURE_TYPE_RGBA);
            p.program.uniformMatrix4f("MVP", wf::gles::render_target_orthographic_projection(data.target));
            p.program.uniform4f("rect", glm::vec4{x, y, w, h});
            p.program.uniform1f("radius", r);
            p.program.uniform1f("line", line);
            p.program.uniform1f("aa", 1.0f / std::max(0.01f, float(data.target.scale)));
            p.program.uniform4f("color", glm::vec4{c.r, c.g, c.b, 1.0});
            glEnable(GL_BLEND);
            glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);
            // Only the ring's bands: top and bottom across the corners, then the sides between,
            // so no fragment is covered twice.
            double band = std::min({std::max(r, line) + 1, h / 2, w / 2});
            std::vector<std::array<float, 4>> quads{
                {float(x - 1), float(y - 1), float(x + w + 1), float(y + band)},
                {float(x - 1), float(y + h - band), float(x + w + 1), float(y + h + 1)},
                {float(x - 1), float(y + band), float(x + band), float(y + h - band)},
                {float(x + w - band), float(y + band), float(x + w + 1), float(y + h - band)}};
            wf::gles::for_each_scissor_rect(data.target, data.damage, [&]
            {
                for (auto [x1, y1, x2, y2] : quads)
                {
                    if (x2 <= x1 || y2 <= y1) continue;
                    GLfloat vertices[] = {x1, y2, x2, y2, x2, y1, x1, y1};
                    p.program.attrib_pointer("position", 2, 0, vertices);
                    glDrawArrays(GL_TRIANGLE_FAN, 0, 4);
                }
            });
            p.program.deactivate();
        });
        if (drawn) return;
        // Without GLES: whole-pixel rows (corners stair-stepped).
        wf::geometry_t b{std::round(x), std::round(y), std::round(w), std::round(h)};
        line = std::round(line);
        double inner = std::max(0.0, r - line);
        wf::color_t rim{c.r, c.g, c.b, 1};
        auto strip = [&] (double sx, double sy, double sw, double sh)
        {
            if (sw > 0 && sh > 0) data.pass->add_rect(rim, data.target, {sx, sy, sw, sh}, data.damage);
        };
        auto inset = [] (double radius, double row)
        {
            if (row >= radius) return 0.0;
            double distance = std::max(0.0, radius - row - .5);
            return radius - std::sqrt(std::max(0.0, radius * radius - distance * distance));
        };
        int rows = int(std::ceil(std::max(r, line)));
        for (int row = 0; row < rows; ++row)
        {
            double outer = inset(r, row), k = row - line;
            for (double ry : {double(b.y + row), double(b.y + b.height - row - 1)})
            {
                if (k < 0) { strip(b.x + outer, ry, b.width - 2 * outer, 1); continue; }
                double in = line + inset(inner, k);
                strip(b.x + outer, ry, in - outer, 1);
                strip(b.x + b.width - in, ry, in - outer, 1);
            }
        }
        strip(b.x, b.y + rows, line, b.height - 2 * rows);
        strip(b.x + b.width - line, b.y + rows, line, b.height - 2 * rows);
    }
};

void hint_outline_node::gen_render_instances(std::vector<wf::scene::render_instance_uptr>& instances,
    wf::scene::damage_callback damage, wf::output_t *output)
{
    SCOTTLAND_LOOP_SCOPE(hint_gen_render_instances);
    instances.push_back(std::make_unique<hint_outline_render>(this, damage, output));
}

}
