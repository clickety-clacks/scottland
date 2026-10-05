#pragma once

// WG16 presentation resources. Logical collapse/peek intent belongs to the desktop model;
// these images and the clock only describe the pixels currently shown by a frame.
#include "loop.hpp"
#include <wayfire/opengl.hpp>
#include <wayfire/core.hpp>
#include <wayfire/toplevel-view.hpp>
#include <wayfire/output.hpp>
#include <wayfire/scene-render.hpp>
#include <wayfire/view-transform.hpp>
#include <glm/gtc/type_ptr.hpp>
#include <algorithm>
#include <cmath>
#include <memory>
#include "widget-spring.hpp"

namespace scottland
{
struct widget_image_t
{
    std::shared_ptr<wf::auxilliary_buffer_t> buffer;
    std::shared_ptr<wf::texture_t> texture; // retains the applied client buffer until the blend releases it
    double width = 0, height = 0;
    double inset = 0;
    double edge_fill = 0; // built-in card background radius, not generic client content
    wf::geometry_t box{}; // relative to the window geometry, including surface extents

    static widget_image_t capture(wayfire_toplevel_view view, double inset, wf::auxilliary_buffer_t *drawn = nullptr)
    {
        SCOTTLAND_LOOP_SCOPE(widget_capture);
        widget_image_t image;
        // A simple surface already has the exact pixels we need. Keep its buffer locked,
        // rather than allocate/render/submit a second GPU pass during an input callback.
        // Multiple surfaces, viewports, rotations and explicit sync retain the scene capture.
        wf::scene::node_ptr node = view->get_surface_root_node();
        while (node && node->get_children().size() == 1 &&
            dynamic_cast<wf::scene::floating_inner_node_t*>(node.get()) &&
            !dynamic_cast<wf::scene::transformer_base_node_t*>(node.get()) &&
            !dynamic_cast<wf::scene::zero_copy_texturable_node_t*>(node.get()))
            node = node->get_children().front();
        auto leaf = node ? dynamic_cast<wf::scene::zero_copy_texturable_node_t*>(node.get()) : nullptr;
        wf::dimensionsf_t size{};
        auto texture = leaf ? leaf->to_texture(&size) : nullptr;
        auto bounds = view->get_surface_root_node()->get_bounding_box();
        if (wf::get_core().is_gles2() && texture && texture->get_transform() == WL_OUTPUT_TRANSFORM_NORMAL &&
            !texture->get_source_box() && !texture->get_wait_timeline() &&
            std::abs(size.width - bounds.width) < .01 && std::abs(size.height - bounds.height) < .01)
        {
            auto gl = wf::gles_texture_t(texture);
            if (gl.target == GL_TEXTURE_2D) image.texture = std::move(texture);
        }
        if (!image.texture)
        {
            auto scale = view->get_output()->handle->scale;
            auto size = drawn ? drawn->get_size() : wf::dimensions_t{};
            bool same_size = size.width == std::ceil(bounds.width * scale) &&
                size.height == std::ceil(bounds.height * scale);
            if (drawn && drawn->get_buffer() && same_size)
            {
                // A complex surface was already composed for the last visible frame.
                // Transfer that buffer, rather than redraw it or hold a mutable cache.
                // Wayfire allocates and fully damages a replacement on its next render.
                image.buffer = std::make_shared<wf::auxilliary_buffer_t>(std::move(*drawn));
            } else
            {
                image.buffer = std::make_shared<wf::auxilliary_buffer_t>();
                view->take_snapshot(*image.buffer);
            }
        }
        auto g = view->get_geometry();
        image.width = g.width;
        image.height = g.height;
        image.box = view->get_surface_root_node()->get_bounding_box();
        image.box.x -= g.x;
        image.box.y -= g.y;
        image.inset = inset;
        image.edge_fill = inset > 0 ? 16 : 0;
        return image;
    }

    wf::gles_texture_t gl_texture() const
    {
        return texture ? wf::gles_texture_t(texture) : wf::gles_texture_t::from_aux(*buffer);
    }
    explicit operator bool() const { return texture || (buffer && buffer->get_buffer()); }
};

struct widget_morph_t
{
    widget_image_t from, to;
    double width = 0, height = 0, inset = 0, fade = 0;
    double from_width = 0, from_height = 0;
    double bounce = 0;
    double from_scale = 1, scale = 1, shape = 0;
    double dx = 0, dy = 0, from_dx = 0, from_dy = 0, to_dx = 0, to_dy = 0;
    bool right = false;
    bool cover = false; // window/card forms scale evenly to cover the changing frame
    uint32_t duration_ms = duration;
    bool waiting = true;
    bool fallback = false;
    uint32_t response_ms = 0;
    uint32_t requested = 0, started = 0;
    uint64_t steps = 0;
    static constexpr uint32_t duration = 200, response_timeout = 300;

    void step(uint32_t now)
    {
        double t = std::clamp(double(now - started) / duration_ms, 0.0, 1.0);
        shape = cover ? wf::animation::smoothing::circle(t) : t * t * (3 - 2 * t);
        // Only size bounces. Keep the established 180/200 ms content handoff.
        double content = std::clamp(double(now - started) / (cover ? 180 : duration), 0.0, 1.0);
        fade = cover ? wf::animation::smoothing::circle(content) : content * content * (3 - 2 * content);
        double w = from_width > 0 ? from_width : from.width;
        double h = from_height > 0 ? from_height : from.height;
        double size_progress = bounce > 0 ? widget_spring(t, bounce) : shape;
        width = widget_spring_size(w, to.width, size_progress);
        height = widget_spring_size(h, to.height, size_progress);
        scale = from_scale + (1 - from_scale) * shape;
        inset = from.inset + (to.inset - from.inset) * fade;
        dx = from_dx * (1 - shape) + to_dx * shape;
        dy = from_dy * (1 - shape) + to_dy * shape;
        ++steps;
    }

    void geometry_applied(const wf::geometry_t& old, const wf::geometry_t& current)
    {
        if (old.width == current.width && old.height == current.height) return;
        // A resize can also reposition a clamped widget (e.g. one near the bottom).
        // Preserve the OLD rectangle until the blend advances. Ordinary moves and
        // glide translation remain independent of this size-induced correction.
        double shift_x = right ? old.x + old.width - current.x - current.width : old.x - current.x;
        double shift_y = old.y + old.height / 2 - current.y - current.height / 2;
        dx += shift_x; dy += shift_y;
        if (!to && to.width > 0) // the provisional destination remains at its screen position
        {
            to_dx += shift_x;
            to_dy += shift_y;
        }
        double left = std::max(.0001, 1 - shape);
        from_dx = (dx - to_dx * shape) / left;
        from_dy = (dy - to_dy * shape) / left;
    }
};

// Both premultiplied images are mixed in ONE shader invocation. Source-over of two
// independently faded layers would expose the wallpaper even between opaque cards.
// Images keep their natural pixel scale and rail alignment; the frame clips them.
// Extend the inside edge pixel when the old (narrower) image cannot fill the new frame.
// A card's 20px/16px icon inset is interpolated before sampling, so icons stay registered.
class widget_morph_renderer_t
{
    OpenGL::program_t program;
    bool ready = false;

  public:
    void release()
    {
        if (ready) wf::gles::run_in_context_if_gles([&] { program.free_resources(); });
        ready = false;
    }

    void prepare()
    {
        if (!ready)
        {
            program.compile(R"(#version 100
attribute highp vec2 position;
varying highp vec2 pos;
uniform mat4 MVP;
void main() { pos = position; gl_Position = MVP * vec4(position, 0.0, 1.0); }
)", R"(#version 100
precision highp float;
varying highp vec2 pos;
uniform sampler2D first, second;
uniform vec4 rect, box0, box1;
uniform vec2 size0, size1;
uniform vec2 shift, fill, flip, force_alpha;
uniform float right, fade, radius, aa, alpha, cover;
vec2 coordinate(vec2 p, vec2 size, vec4 box, float offset, float inside, float invert) {
    if (cover > 0.5) {
        float scale = max(rect.z / size.x, rect.w / size.y);
        p = (p - rect.zw * 0.5) / scale + size * 0.5;
        vec2 uv = (p - box.xy) / box.zw;
        return vec2(uv.x, mix(uv.y, 1.0 - uv.y, invert));
    }
    p.x += right * (size.x - rect.z) + offset;
    p.y += (size.y - rect.w) * 0.5;
    // Sampling never stretches the icon. The inner edge extends only the background.
    p = clamp(p, vec2(0.5), size - vec2(0.5));
    // A narrower card's former inner rounded corner is now INSIDE the frame.
    // Extend its opaque background from just inside that corner, not its transparent
    // antialiased edge. Generic widgets retain their own transparency unchanged.
    inside = mix(0.5, inside, clamp((rect.z - size.x) / max(inside, 1.0), 0.0, 1.0));
    if (rect.z > size.x && inside > 0.5)
        p.x = clamp(p.x, mix(0.5, inside, right), mix(size.x - inside, size.x - 0.5, right));
    vec2 uv = (p - box.xy) / box.zw;
    return vec2(uv.x, mix(uv.y, 1.0 - uv.y, invert));
}
void main() {
    vec2 p = pos - rect.xy;
    vec4 a = texture2D(first, coordinate(p, size0, box0, shift.x, fill.x, flip.x));
    vec4 b = texture2D(second, coordinate(p, size1, box1, shift.y, fill.y, flip.y));
    // XRGB/XBGR clients have no alpha channel; its stored bits are undefined.
    a.a = mix(a.a, 1.0, force_alpha.x);
    b.a = mix(b.a, 1.0, force_alpha.y);
    vec2 q = abs(p - rect.zw * 0.5) - rect.zw * 0.5 + radius;
    float d = length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - radius;
    float mask = radius < 0.0 ? 1.0 : clamp(0.5 - d / aa, 0.0, 1.0);
    gl_FragColor = mix(a, b, fade) * alpha * mask;
})");
            ready = true;
        }
    }

    void draw(const widget_morph_t& m, const wf::render_target_t& target,
        double x, double y, double width, double height, float radius, float alpha)
    {
        if (!m.from) return;
        prepare();
        auto& b = m.to ? m.to : m.from;
        // from_aux creates/imports textures and temporarily binds/unbinds on the current
        // texture unit. Resolve BOTH before binding either sampler, or the second import
        // would silently unbind the first image.
        auto first = m.from.gl_texture();
        auto second = b.gl_texture();
        program.use(wf::TEXTURE_TYPE_RGBA);
        auto bind = [&] (const wf::gles_texture_t& tex, int unit, const char *name)
        {
            glActiveTexture(GL_TEXTURE0 + unit);
            glBindTexture(GL_TEXTURE_2D, tex.tex_id);
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
            program.uniform1i(name, unit);
        };
        bind(first, 0, "first");
        bind(second, 1, "second");
        program.uniformMatrix4f("MVP", wf::gles::render_target_orthographic_projection(target));
        program.uniform4f("rect", glm::vec4{x, y, width, height});
        auto box = [&] (const char *name, const widget_image_t& image)
        {
            program.uniform4f(name, glm::vec4{image.box.x, image.box.y, image.box.width, image.box.height});
        };
        box("box0", m.from); box("box1", b);
        program.uniform2f("size0", m.from.width, m.from.height);
        program.uniform2f("size1", b.width, b.height);
        double sign = m.right ? 1 : -1;
        program.uniform2f("shift", sign * (m.inset - m.from.inset), sign * (m.inset - b.inset));
        program.uniform2f("fill", m.from.edge_fill, b.edge_fill);
        // Logical image coordinates are top-down. Honor Wayfire's wlroots texture
        // inversion just as its built-in get_pixel() does (raw GL images are bottom-up).
        program.uniform2f("flip", first.invert_y ? 0 : 1, second.invert_y ? 0 : 1);
        program.uniform2f("force_alpha", first.type == wf::TEXTURE_TYPE_RGBX ? 1 : 0,
            second.type == wf::TEXTURE_TYPE_RGBX ? 1 : 0);
        program.uniform1f("right", m.right ? 1 : 0);
        program.uniform1f("fade", m.fade);
        program.uniform1f("cover", m.cover ? 1 : 0);
        program.uniform1f("radius", radius);
        program.uniform1f("aa", 1.0 / target.scale);
        program.uniform1f("alpha", alpha);
        GLfloat points[] = {(float)x, (float)y, (float)(x + width), (float)y,
            (float)(x + width), (float)(y + height), (float)x, (float)(y + height)};
        program.attrib_pointer("position", 2, 0, points);
        glEnable(GL_BLEND);
        glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);
        glDrawArrays(GL_TRIANGLE_FAN, 0, 4);
        glActiveTexture(GL_TEXTURE1);
        glBindTexture(GL_TEXTURE_2D, 0);
        glActiveTexture(GL_TEXTURE0);
        program.deactivate();
    }

    // Freeze the exact current blend for an interruption. take_snapshot(view) only
    // sees the client surface and would jump to the new presentation on reversal.
    widget_image_t freeze(const widget_morph_t& m, float scale)
    {
        widget_image_t image;
        image.width = m.width; image.height = m.height; image.inset = m.inset;
        image.edge_fill = m.from.edge_fill;
        image.box = {0, 0, std::ceil(m.width), std::ceil(m.height)};
        image.buffer = std::make_shared<wf::auxilliary_buffer_t>();
        if (image.buffer->allocate(wf::dimensions(image.box), scale) == wf::buffer_reallocation_result_t::FAILED)
            return image;
        wf::render_target_t target{*image.buffer};
        target.geometry = image.box; target.scale = scale;
        wf::render_pass_params_t params;
        params.target = target;
        params.damage = image.box;
        std::vector<wf::scene::render_instance_uptr> instances;
        params.instances = &instances;
        wf::render_pass_t pass(params);
        pass.run_partial();
        pass.clear(image.box, {0, 0, 0, 0});
        pass.custom_gles_subpass([&]
        {
            wf::gles::for_each_scissor_rect(target, image.box, [&]
            {
                draw(m, target, 0, 0, m.width, m.height, -1, 1);
            });
        });
        pass.submit();
        return image;
    }
};

static widget_morph_renderer_t& widget_morph_renderer()
{
    static widget_morph_renderer_t renderer;
    return renderer;
}
}
