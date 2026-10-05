#include "goo-renderer.hpp"
#include "loop.hpp"
#include "goo-shaders.hpp"
#include "goo-gl.hpp"
#include "attention-breath.hpp"
#include <algorithm>
#include <array>
#include <cmath>
#include <chrono>
#include <cstring>
#include <wayfire/scene-render.hpp>
#include <wayfire/util/log.hpp>
#include <drm_fourcc.h>
extern "C" {
#include <wlr/types/wlr_buffer.h>
#include <wlr/render/dmabuf.h>
}

namespace scottland::goo
{
using gl::bind;
using gl::quad;
using gl::state_t;
using gl::target_t;
namespace
{
int breath_key_count(const settings_t &s, float scale)
{
    // The approximation is bounded to half a device pixel per interval. Extreme
    // settings use the exact direct strip path instead of silently widening it.
    float swell = breath_swell(s.thickness, s.reach, s.swell);
    float travel = s.reach * std::log1p(std::max(swell, 0.f)) * std::max(scale, 1.f);
    int required = std::max(1, int(std::ceil(travel / .5f)));
    return required <= 16 ? required : 0;
}
float breath_key_value(int key, int keys, float swell)
{
    float t = float(key) / keys;
    return swell > 1e-4f ? std::expm1(t * std::log1p(swell)) / swell : t;
}
bool extension(const char *list, const char *name)
{
    if (!list)
        return false;
    const char *at = list;
    while ((at = strstr(at, name)))
    {
        const char *end = at + strlen(name);
        if ((at == list || at[-1] == ' ') && (!*end || *end == ' '))
            return true;
        at = end;
    }
    return false;
}
} // namespace
struct renderer_t::impl
{
    bool checked = false, available = false, es3 = false, packed = false, ready = false;
    int width = 0, height = 0;
    GLuint timer = 0;
    bool timing = false, timer_pending = false, timer_open = false, timer_draw = false;
    float time = 0;
    uint64_t sampled_step = UINT64_MAX;
    glm::vec2 sampled_point{};
    glm::vec4 sampled_value{};
    settings_t settings;
    bool overlap = false, controls = false, fast = true, has_wallpaper = false;
    bool cache_valid = false, cache_dirty = true, cache_available = true;
    wf::geometry_t backdrop_geometry{};
    float backdrop_scale = 0;
    wl_output_transform backdrop_transform = WL_OUTPUT_TRANSFORM_NORMAL;
    static constexpr int exact_key = -2;
    int layer_key[2] = {-1, -1};
    bool layer_b_available = true, requested_keyframes = false, use_keyframes = false;
    std::vector<source_t> sources;
    OpenGL::program_t field_p, mask_p, wave_p, dye_p, render_p, energy_p, query_p, copy_p, backdrop_p;
    OpenGL::program_t intrinsic_p, refraction_p, composite_p, composite_mix_p;
    OpenGL::program_t field_fast, mask_fast, wave_fast, dye_fast, render_fast;
    target_t field, mask, wave[2], dye[2], source, curve, background, query, atlas;
    target_t intrinsic, refraction, intrinsic_b, refraction_b;
    std::vector<std::shared_ptr<const shape_t>> atlas_shapes;
    std::vector<glm::vec4> shape_tiles;
    bool upload_shapes()
    {
        std::vector<std::shared_ptr<const shape_t>> next;
        for (auto &s : sources) next.push_back(s.shape);
        if (atlas.texture && next == atlas_shapes) return true;
        std::vector<glm::vec4> tiles;
        GLint max_size = 0;
        glGetIntegerv(GL_MAX_TEXTURE_SIZE, &max_size);
        int aw = std::min(1024, max_size), x = 0, y = 0, row = 0;
        for (auto &s : next)
        {
            if (!s) { tiles.emplace_back(0); continue; }
            if (x + s->width > aw) { x = 0; y += row; row = 0; }
            if (s->width > aw || y + s->height > max_size)
            {
                // Retain the per-widget masked fallback rather than diverging
                // from CPU input by substituting a rectangle for one widget.
                loop::note(loop::note_id::goo_atlas_too_large);
                return false;
            }
            tiles.emplace_back(x, y, s->width, s->height);
            x += s->width;
            row = std::max(row, s->height);
        }
        if (!atlas.allocate(aw, std::max(1, y + row), true, es3, false)) return false;
        for (size_t i = 0; i < next.size(); i++)
            if (tiles[i].z > 0)
            {
                auto &tile = tiles[i];
                glTexSubImage2D(GL_TEXTURE_2D, 0, tile.x, tile.y, tile.z, tile.w,
                    GL_RGBA, GL_UNSIGNED_BYTE, next[i]->pixels.data());
            }
        atlas_shapes = std::move(next);
        shape_tiles = std::move(tiles);
        cache_dirty = true;
        return true;
    }
    std::vector<target_t> reduction;

    void poll_timer(double &step_ms, double &draw_ms)
    {
        if (timer_pending)
        {
            GLuint available = 0;
            glGetQueryObjectuiv(timer, GL_QUERY_RESULT_AVAILABLE, &available);
            if (available)
            {
                GLuint ns = 0;
                glGetQueryObjectuiv(timer, GL_QUERY_RESULT, &ns);
                GLint disjoint = 0;
                glGetIntegerv(0x8FBB /* GPU_DISJOINT_EXT */, &disjoint);
                if (!disjoint)
                    (timer_draw ? draw_ms : step_ms) = ns / 1e6;
                timer_pending = false;
            }
        }
    }
    void release()
    {
        release_readback();
        if (timer)
            glDeleteQueries(1, &timer);
        for (auto p : {&field_p, &mask_p, &wave_p, &dye_p, &render_p, &energy_p, &query_p, &copy_p, &backdrop_p,
                       &intrinsic_p, &refraction_p, &composite_p, &composite_mix_p,
                       &field_fast, &mask_fast, &wave_fast, &dye_fast, &render_fast})
            p->free_resources();
        for (auto p : {&field, &mask, &wave[0], &wave[1], &dye[0], &dye[1], &source, &curve, &background, &query,
                       &intrinsic, &refraction, &intrinsic_b, &refraction_b, &atlas})
            p->release();
        for (auto &t : reduction)
            t.release();
    }
    void compile(OpenGL::program_t &program, std::string vs, std::string fs, bool derivatives = false)
    {
        auto replace = [](std::string &s, const std::string &from, const std::string &to)
        {
            size_t at = 0;
            while ((at = s.find(from, at)) != std::string::npos)
            {
                s.replace(at, from.size(), to);
                at += to.size();
            }
        };
        if (es3)
        {
            replace(vs, "attribute ", "in ");
            replace(vs, "varying ", "out ");
            replace(fs, "varying ", "in ");
            replace(fs, "texture2D(", "texture(");
            replace(fs, "gl_FragColor", "goo_color");
            replace(fs, "i<1024", "i<uCount");
            program.compile("#version 300 es\n" + vs,
                            "#version 300 es\nprecision highp float; out vec4 goo_color;\n" + fs);
        }
        else
            program.compile("#version 100\n" + vs, std::string("#version 100\n") +
                (derivatives ? "#extension GL_OES_standard_derivatives : require\n" : "") + fs);
    }
    bool support()
    {
        if (checked)
            return available;
        checked = true;
        const char *version = (const char *)glGetString(GL_VERSION),
                   *extensions = (const char *)glGetString(GL_EXTENSIONS);
        es3 = version && (strstr(version, "OpenGL ES 3") || strstr(version, "OpenGL ES 4"));
        available = es3 || (extension(extensions, "GL_OES_texture_float") &&
            extension(extensions, "GL_OES_standard_derivatives"));
        loop::note(loop::note_id::goo_gl, version != nullptr, es3, available);
        if (!available)
            return false;
        timing = es3 && extension(extensions, "GL_EXT_disjoint_timer_query");
        if (timing)
            glGenQueries(1, &timer);
        state_t guard;
        bool half_filter = es3 || extension(extensions, "GL_OES_texture_half_float_linear");
        packed = !half_filter || !field.allocate(2, 2, false, es3);
        if (packed && !field.allocate(2, 2, true, es3))
        {
            available = false;
            return false;
        }
        field.release();
        loop::note(loop::note_id::goo_targets, packed);
        const std::array programs{std::make_pair(&field_p, &field_shader), std::make_pair(&mask_p, &mask_shader), std::make_pair(&wave_p, &wave_shader),
                          std::make_pair(&dye_p, &dye_shader), std::make_pair(&render_p, &render_shader),
                          std::make_pair(&energy_p, &energy_shader), std::make_pair(&query_p, &query_shader)};
        for (auto pair : programs)
        {
            compile(*pair.first, vertex, *pair.second, pair.second == &render_shader);
            GLint linked = 0;
            glGetProgramiv(pair.first->get_program_id(wf::TEXTURE_TYPE_RGBA), GL_LINK_STATUS, &linked);
            if (!linked)
                available = false;
        }
        // Specialize the common resting/breathing path. Uniform branches alone
        // retain the overlap/hover loop state on Xe, even when both are absent.
        const std::array fast_programs{std::make_pair(&field_fast, &field_shader),
            std::make_pair(&mask_fast, &mask_shader), std::make_pair(&wave_fast, &wave_shader),
            std::make_pair(&dye_fast, &dye_shader), std::make_pair(&render_fast, &render_shader)};
        for (auto pair : fast_programs)
        {
            auto shader = *pair.second;
            const std::string decl = "uniform float uOverlap,uFilm,uCloudiness,uEmissivity,uControls;";
            shader.replace(shader.find(decl), decl.size(),
                "uniform float uFilm,uCloudiness,uEmissivity; const float uOverlap=0.,uControls=0.;");
            // A float round-trip of uCount keeps a second dynamic loop bound in
            // some GLES compilers. In this specialization every source is eligible.
            auto replace = [&](const std::string &from, const std::string &to)
            {
                size_t at = 0;
                while ((at = shader.find(from, at)) != std::string::npos)
                {
                    shader.replace(at, from.size(), to);
                    at += to.size();
                }
            };
            replace("if(back.x==0.)back=vec2(1.,0.);", "");
            replace("if(i>=int(back.x))break;", "if(i>=uCount)break;");
            replace("if(i>=int(hintBack.x))break;", "if(i>=uCount)break;");
            compile(*pair.first, vertex, shader, pair.second == &render_shader);
            GLint linked = 0;
            glGetProgramiv(pair.first->get_program_id(wf::TEXTURE_TYPE_RGBA), GL_LINK_STATUS, &linked);
            if (!linked) available = false;
        }
        auto cached_shader = [&](bool params)
        {
            std::string shader = render_shader;
            const std::string background = "vec3 bg=texture2D(uBackground,bgUV).rgb,dye=";
            shader.replace(shader.find(background), background.size(), "vec3 bg=vec3(0.),dye=");
            const std::string result = "gl_FragColor=vec4(clamp(color,0.,1.)*a,a);";
            shader.replace(shader.find(result), result.size(), params ?
                "gl_FragColor=vec4(clamp((refr-p)/32.+.5,0.,1.),"
                "clamp((1.-dyeBlend)*(film?1.:1.4)*diff/1.5,0.,1.),1.);" :
                // The background term is nonnegative, so clamping intrinsic
                // light before compositing gives the same final clamp.
                "gl_FragColor=vec4(clamp(color,0.,1.),a);");
            return shader;
        };
        compile(intrinsic_p, vertex, cached_shader(false), true);
        compile(refraction_p, vertex, cached_shader(true), true);
        compile(composite_p, vertex, cached_composite_shader);
        compile(composite_mix_p, vertex, cached_composite_mix_shader);
        for (auto p : {&intrinsic_p, &refraction_p, &composite_p, &composite_mix_p})
        {
            GLint linked = 0;
            glGetProgramiv(p->get_program_id(wf::TEXTURE_TYPE_RGBA), GL_LINK_STATUS, &linked);
            if (!linked) available = false;
        }
        compile(backdrop_p, vertex, backdrop_shader);
        compile(copy_p, vertex,
                "precision highp float; uniform sampler2D image; void "
                "main(){gl_FragColor=texture2D(image,vec2(.5));}");
        if (!available)
            loop::note(loop::note_id::goo_shader_unavailable);
        return available;
    }
    void common(OpenGL::program_t &program, int w, int h)
    {
        program.use(wf::TEXTURE_TYPE_RGBA);
        program.uniformMatrix4f("MVP", glm::ortho(0.f, float(w), 0.f, float(h), -1.f, 1.f));
        GLuint id = program.get_program_id(wf::TEXTURE_TYPE_RGBA);
        auto one = [id](const char *name, float v) { glUniform1f(glGetUniformLocation(id, name), v); };
        glUniform1i(glGetUniformLocation(id, "uCount"), sources.size());
        glUniform2f(glGetUniformLocation(id, "uRes"), width, height);
        glUniform2f(glGetUniformLocation(id, "uSize"), w, h);
        one("uTime", time);
        one("uReach", settings.reach);
        one("uThickness", settings.thickness);
        one("uOverlap", overlap ? 1 : 0);
        one("uControls", controls ? 1 : 0);
        one("uFilm", settings.overlap_film);
        one("uCloudiness", settings.hover_cloudiness);
        one("uEmissivity", settings.hover_emissivity);
        one("uNoise", settings.noise);
        one("uNoiseScale", 1 / settings.lump);
        one("uNoiseSpeed", settings.drift);
        one("uT", settings.threshold());
        one("uPacked", packed ? 1 : 0);
        bind(program, "uSources", 0, source.texture);
        bind(program, "uShapes", 6, atlas.texture);
        glUniform2f(glGetUniformLocation(program.get_program_id(wf::TEXTURE_TYPE_RGBA), "uAtlasSize"),
            atlas.width, atlas.height);
        bind(program, "uFalloff", 1, curve.texture);
        bind(program, "uField", 2, field.texture);
        bind(program, "uWave", 3, wave[0].texture);
        bind(program, "uDyeTex", 4, dye[0].texture);
        bind(program, "uMask", 7, mask.texture);
    }
    void draw_to(OpenGL::program_t &program, target_t &target)
    {
        glBindFramebuffer(GL_FRAMEBUFFER, target.fb);
        glViewport(0, 0, target.width, target.height);
        quad(program, target.width, target.height);
    }
    // A simulation pass over only the given output-logical rects (texels elsewhere keep their
    // values), drawn as one batch of quads. Rows run with y, as the passes map texels back to
    // positions.
    // A conservative occupancy map: every tile that could have received a wave.
    // Keep it until resize, rather than freezing nonzero waves after a fixed age.
    // Its size is bounded by the output, and needs no GPU-to-CPU readback.
    wf::region_t wave_tiles;
    std::vector<GLfloat> tiles;
    void simulate(OpenGL::program_t &program, target_t &target, const std::vector<wf::geometry_t> &area)
    {
        if (area.empty())
            return draw_to(program, target);
        tiles.clear();
        double sx = double(target.width) / width, sy = double(target.height) / height;
        for (auto &r : area)
        {
            float x1 = std::max(0.0, std::floor(r.x * sx)), y1 = std::max(0.0, std::floor(r.y * sy));
            float x2 = std::min<double>(target.width, std::ceil((r.x + r.width) * sx));
            float y2 = std::min<double>(target.height, std::ceil((r.y + r.height) * sy));
            if (x2 <= x1 || y2 <= y1)
                continue;
            tiles.insert(tiles.end(), {x1, y1, x2, y1, x2, y2, x1, y1, x2, y2, x1, y2});
        }
        if (tiles.empty())
            return;
        glBindFramebuffer(GL_FRAMEBUFFER, target.fb);
        glViewport(0, 0, target.width, target.height);
        program.attrib_pointer("position", 2, 0, tiles.data());
        glDrawArrays(GL_TRIANGLES, 0, tiles.size() / 2);
        program.deactivate();
    }
    bool resize(int w, int h)
    {
        width = w;
        height = h;
        wave_tiles.clear();
        bool ok = field.allocate((w + 1) / 2, (h + 1) / 2, packed, es3);
        ok = mask.allocate((w + 3) / 4, (h + 3) / 4, true, es3) && ok;
        glBindTexture(GL_TEXTURE_2D, mask.texture);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
        for (auto t : {&wave[0], &wave[1], &dye[0], &dye[1]})
        {
            ok = t->allocate((w + 3) / 4, (h + 3) / 4, packed, es3) && ok;
            if (t == &wave[0] || t == &wave[1])
                glClearColor(0, packed ? 128.f / 255 : 0, 0, packed ? 128.f / 255 : 1);
            else
                glClearColor(.6, .7, .8, 1);
            glClear(GL_COLOR_BUFFER_BIT);
        }
        query.allocate(1, 1, true, es3);
        for (auto &t : reduction)
            t.release();
        reduction.clear();
        int rw = wave[0].width, rh = wave[0].height;
        do
        {
            rw = (rw + 1) / 2;
            rh = (rh + 1) / 2;
            reduction.emplace_back();
            ok = reduction.back().allocate(rw, rh, packed, es3) && ok;
        } while (rw > 1 || rh > 1);
        ready = ok;
        return ok;
    }
    bool upload()
    {
        std::vector<glm::vec4> data;
        if (!upload_shapes()) return false;
        for (size_t i = 0; i < sources.size(); i++)
        {
            auto &s = sources[i];
            data.push_back(s.rect);
            auto liquid = s.liquid;
            if (s.shape) liquid.y = -liquid.y - 1; // sourceSdf's widget flag
            data.push_back(liquid);
            data.push_back(glm::vec4{s.dye, s.light ? s.scale : -s.scale});
            data.push_back(s.corners);
            data.push_back(s.dot);
            data.push_back(glm::vec4{s.hinted ? (s.hint_circle ? std::min(s.scale, 1.f) : 1.f) : 0.f, s.control_extent,
                overlap_film_width(s, settings), s.hint_circle ? 1.f : 0.f});
            data.push_back(s.sides);
            data.emplace_back(s.attention && s.emitter ? 1.f : 0.f, s.dye_strength, 0, 0);
            data.push_back(shape_tiles[i]);
            data.push_back(s.shape ? s.shape->bounds : glm::vec4{});
            data.push_back(s.shape_body);
        }
        if (data.empty())
            data.resize(11);
        const std::array uploads{std::make_pair(&source, std::make_pair(11, std::max(1, int(sources.size())))),
                                std::make_pair(&curve, std::make_pair(256, 1))};
        for (auto pair : uploads)
        {
            auto &t = *pair.first;
            if (!t.texture)
                glGenTextures(1, &t.texture);
            glBindTexture(GL_TEXTURE_2D, t.texture);
            std::vector<glm::vec4> curve_data;
            if (&t == &curve)
                for (float v : settings.falloff)
                    curve_data.emplace_back(v, 0, 0, 1);
            glTexImage2D(GL_TEXTURE_2D, 0, es3 ? GL_RGBA32F : GL_RGBA, pair.second.first, pair.second.second,
                         0, GL_RGBA, GL_FLOAT, &t == &curve ? curve_data.data() : data.data());
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
        }
        return true;
    }
    // Asynchronous energy readings (main-loop Phase 3): a ring of four pixel buffers with fences.
    struct energy_slot_t
    {
        GLuint pbo = 0;
        GLsync fence = nullptr;
        bool busy = false;
        uint64_t issued_ns = 0, step = 0, invalidation = 0;
        int w = 0, h = 0;
    };
    std::array<energy_slot_t, 4> energy_slots;
    bool readback_failed = false;
    struct reading_t { unsigned char value[4]; uint64_t step, invalidation; int w, h; };
    static uint64_t mono_ns()
    {
        return std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now().time_since_epoch()).count();
    }
    void retire(energy_slot_t &slot)
    {
        if (slot.fence) glDeleteSync(slot.fence);
        slot.fence = nullptr;
        slot.busy = false;
    }
    void release_readback()
    {
        for (auto &slot : energy_slots)
        {
            retire(slot);
            if (slot.pbo) glDeleteBuffers(1, &slot.pbo);
            slot.pbo = 0;
        }
    }
    void reduce()
    {
        SCOTTLAND_LOOP_SCOPE(goo_energy_reduce);
        GLuint input = 0;
        int iw = wave[0].width, ih = wave[0].height;
        for (size_t i = 0; i < reduction.size(); i++)
        {
            auto &t = reduction[i];
            common(energy_p, t.width, t.height);
            energy_p.uniform1i("uFirst", i == 0 ? 1 : 0);
            // WK14 renders immediate contribution-weighted hint dye instead of the
            // simulated dye while every emitter is hinted. Invisible half-float dye
            // oscillations must not prevent sleep; visible waves still have to settle.
            // Removing/changing hints wakes the normal dye simulation via source state.
            energy_p.uniform1f("uDyeVisible", std::all_of(sources.begin(), sources.end(),
                [](const source_t &s) { return !s.emitter || s.hinted; }) ? 0.f : 1.f);
            energy_p.uniform2f("uInputSize", iw, ih);
            bind(energy_p, "uPrevious", 5, dye[1].texture);
            bind(energy_p, "uReduce", 6, input);
            draw_to(energy_p, t);
            input = t.texture;
            iw = t.width;
            ih = t.height;
        }
        // Into an RGBA8 target: the read format GLES guarantees.
        copy_p.use(wf::TEXTURE_TYPE_RGBA);
        copy_p.uniformMatrix4f("MVP", glm::ortho(0.f, 1.f, 0.f, 1.f, -1.f, 1.f));
        bind(copy_p, "image", 0, input);
        draw_to(copy_p, query);
    }
    /** Issue a reading into a free slot (else skip: false). Never waits. */
    bool issue(uint64_t step, uint64_t invalidation)
    {
        SCOTTLAND_LOOP_SCOPE(goo_energy_issue);
        auto free = std::find_if(energy_slots.begin(), energy_slots.end(), [](auto &s) { return !s.busy; });
        if (free == energy_slots.end()) return false;
        reduce();
        auto &slot = *free;
        // Wayfire's own pack state is restored whatever happens here.
        GLint previous_buffer = 0, previous_alignment = 4;
        glGetIntegerv(GL_PIXEL_PACK_BUFFER_BINDING, &previous_buffer);
        glGetIntegerv(GL_PACK_ALIGNMENT, &previous_alignment);
        struct restore_t { GLint b, a; ~restore_t() { glBindBuffer(GL_PIXEL_PACK_BUFFER, b); glPixelStorei(GL_PACK_ALIGNMENT, a); } }
            restore{previous_buffer, previous_alignment};
        if (!slot.pbo)
        {
            glGenBuffers(1, &slot.pbo);
            if (!slot.pbo) { readback_failed = true; return false; }
            glBindBuffer(GL_PIXEL_PACK_BUFFER, slot.pbo);
            glBufferData(GL_PIXEL_PACK_BUFFER, 4, nullptr, GL_STREAM_READ);
        } else
            glBindBuffer(GL_PIXEL_PACK_BUFFER, slot.pbo);
        glPixelStorei(GL_PACK_ALIGNMENT, 1);
        glReadPixels(0, 0, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, nullptr);
        slot.fence = glFenceSync(GL_SYNC_GPU_COMMANDS_COMPLETE, 0);
        if (!slot.fence) { readback_failed = true; return false; }
        // Submitted now, even if no further frame is drawn.
        {
            SCOTTLAND_LOOP_SCOPE(goo_energy_flush);
            glFlush();
        }
        slot.busy = true;
        slot.issued_ns = mono_ns();
        slot.step = step;
        slot.invalidation = invalidation;
        slot.w = width;
        slot.h = height;
        return true;
    }
    /** Examine at most `budget` busy slots, oldest first, without waiting. */
    void collect(int &budget, std::vector<reading_t> &out)
    {
        SCOTTLAND_LOOP_SCOPE(goo_energy_collect);
        GLint previous_buffer = 0;
        glGetIntegerv(GL_PIXEL_PACK_BUFFER_BINDING, &previous_buffer);
        struct restore_t { GLint b; ~restore_t() { glBindBuffer(GL_PIXEL_PACK_BUFFER, b); } } restore{previous_buffer};
        std::array<energy_slot_t *, 4> order{};
        int n = 0;
        for (auto &slot : energy_slots) if (slot.busy) order[n++] = &slot;
        std::sort(order.begin(), order.begin() + n, [](auto a, auto b) { return a->step < b->step; });
        for (int i = 0; i < n && budget > 0; i++)
        {
            auto &slot = *order[i];
            budget--;
            GLenum status = glClientWaitSync(slot.fence, 0, 0);
            if (status == GL_ALREADY_SIGNALED || status == GL_CONDITION_SATISFIED)
            {
                glBindBuffer(GL_PIXEL_PACK_BUFFER, slot.pbo);
                auto mapped = static_cast<const unsigned char *>(glMapBufferRange(GL_PIXEL_PACK_BUFFER, 0, 4, GL_MAP_READ_BIT));
                if (!mapped)
                {
                    retire(slot);
                    readback_failed = true;  // a missing value is never read as zero energy
                    continue;
                }
                reading_t r{{mapped[0], mapped[1], mapped[2], mapped[3]}, slot.step, slot.invalidation, slot.w, slot.h};
                glUnmapBuffer(GL_PIXEL_PACK_BUFFER);
                retire(slot);
                out.push_back(r);
            } else if (status == GL_WAIT_FAILED || mono_ns() - slot.issued_ns > 1000000000ull)
            {
                retire(slot);  // an idle desktop never keeps the collection timer
                readback_failed = true;
            }
        }
    }
};
renderer_t::renderer_t() : p(std::make_unique<impl>()) {}
renderer_t::~renderer_t()
{
    wf::gles::run_in_context_if_gles(
        [&]
        {
            state_t guard;
            p->release();
        });
}
bool renderer_t::supported()
{
    bool ok = false;
    wf::gles::run_in_context_if_gles([&] { ok = p->support(); });
    packed = p->packed;
    return ok;
}
bool renderer_t::update(const std::vector<source_t> &sources, const settings_t &s, int w, int h, float time,
                        const std::vector<glm::vec4> &impulses, const std::vector<wf::geometry_t> &area,
                        wf::auxilliary_buffer_t *wallpaper, const glm::mat4 &wallpaper_map)
{
    state_t guard;
    if (!p->support())
        return false;
    p->poll_timer(last_gpu_ms, last_draw_gpu_ms);
    if (!p->es3 && sources.size() > 1024)
    {
        loop::note(loop::note_id::goo_gles2_sources, sources.size());
        return false;
    }
    bool measure_gpu = p->timing && !p->timer_pending;
    auto start = std::chrono::steady_clock::now();
    glDisable(GL_SCISSOR_TEST);
    glDisable(GL_BLEND);
    if ((!p->ready || w != p->width || h != p->height) && !p->resize(w, h))
        return false;
    if (measure_gpu)
    {
        p->timer_draw = false;
        glBeginQuery(0x88BF /* TIME_ELAPSED_EXT */, p->timer);
    }
    p->sources = sources;
    p->cache_dirty = true;
    p->controls = std::any_of(sources.begin(), sources.end(), [](auto &s) {
        return glm::length(s.corners) + glm::length(s.sides) > .001f;
    });
    p->overlap = overlaps(sources);
    p->fast = !p->overlap && !p->controls;
    auto &field_program = p->fast ? p->field_fast : p->field_p;
    auto &mask_program = p->fast ? p->mask_fast : p->mask_p;
    auto &wave_program = p->fast ? p->wave_fast : p->wave_p;
    auto &dye_program = p->fast ? p->dye_fast : p->dye_p;
    p->settings = s;
    p->has_wallpaper = wallpaper && s.soak > 0;
    p->time = time;
    if (!p->upload())
    {
        if (measure_gpu) { glEndQuery(0x88BF); p->timer_pending = true; }
        return false;
    }
    glBindFramebuffer(GL_FRAMEBUFFER, p->field.fb);
    glClearColor(0, 0, 0, 0);
    glClear(GL_COLOR_BUFFER_BIT);
    p->common(field_program, p->field.width, p->field.height);
    p->simulate(field_program, p->field, area);
    p->common(mask_program, p->mask.width, p->mask.height);
    p->draw_to(mask_program, p->mask);
    for (auto &r : area)
        p->wave_tiles |= r;
    std::vector<wf::geometry_t> wave_area;
    for (auto &r : p->wave_tiles)
        wave_area.push_back(wf::geometry_t{double(r.x1), double(r.y1),
            double(r.x2 - r.x1), double(r.y2 - r.y1)});
    for (int k = 0; k < 2; k++)
    {
        p->common(wave_program, p->wave[1].width, p->wave[1].height);
        wave_program.uniform1f("uC2", s.wave_speed);
        wave_program.uniform1f("uDamp", s.wave_damp);
        std::array<glm::vec4, 8> imp{};
        int n = k == 0 ? std::min<size_t>(impulses.size(), 8) : 0;
        for (int i = 0; i < n; i++)
            imp[i] = impulses[i];
        auto id = wave_program.get_program_id(wf::TEXTURE_TYPE_RGBA);
        glUniform4fv(glGetUniformLocation(id, "uImp[0]"), 8, &imp[0].x);
        wave_program.uniform1i("uImpN", n);
        p->simulate(wave_program, p->wave[1], wave_area);
        std::swap(p->wave[0], p->wave[1]);
    }
    p->common(dye_program, p->dye[1].width, p->dye[1].height);
    dye_program.uniform1f("uSpread", s.spread);
    dye_program.uniform1f("uSwirl", s.swirl);
    dye_program.uniform1f("uRelease", s.release);
    dye_program.uniform1f("uSoak", wallpaper ? s.soak : 0);
    dye_program.uniformMatrix4f("uWallpaperMap", wallpaper_map);
    bind(dye_program, "uWallpaper", 5, wallpaper ? wf::gles_texture_t::from_aux(*wallpaper).tex_id : 0);
    p->draw_to(dye_program, p->dye[1]);
    std::swap(p->dye[0], p->dye[1]);
    packed = p->packed;
    steps++;
    if (!impulses.empty()) invalidation++;
    if (!timed_sleep())
    {
        int budget = 2;
        collect(budget);
        if (steps % 30 == 0)
        {
            if (p->issue(steps, invalidation)) readings_issued++;
            else if (!p->readback_failed) readings_skipped++;  // every slot busy: skip, never wait
        }
    }
    p->timer_open = measure_gpu;
    last_step_ms =
        std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
    return true;
}
void renderer_t::draw(const wf::scene::render_instruction_t &data, const wf::regionf_t &area,
                      const wf::regionf_t &breath_area, float breath, bool settled, bool breath_keys,
              bool reuse_backdrop, const wf::regionf_t *dry, const wf::regionf_t *dry_content)
{
    if (!p->ready)
        return;
    breath_keyframes_active = false;
    // Damage can arrive as one bounding box (the output collapses many small rects), so clip
    // the goo's work to its own bands rather than shading the whole box.
    auto damage = data.damage & area;
    // The background texture is only sampled by visible liquid. Refraction may
    // look up to 16 logical pixels outside a band (2px slope * 8), so keep that
    // margin current; copying every app-damaged window interior at its frame
    // rate is unnecessary when the settled goo itself does not change.
    wf::regionf_t capture_area;
    constexpr double refract_margin = 17;
    for (auto &r : area)
        capture_area |= wf::geometry_t{double(r.x1) - refract_margin, double(r.y1) - refract_margin,
            double(r.x2 - r.x1) + 2 * refract_margin,
            double(r.y2 - r.y1) + 2 * refract_margin};
    auto capture = data.damage & capture_area;
    // Window content no goo lies on is never sampled as backdrop either.
    if (dry)
        capture ^= *dry;
    if (capture.empty())
        return;
    ++draws;
    state_t guard(p->es3);
    p->poll_timer(last_gpu_ms, last_draw_gpu_ms);
    if (!p->timer_open && p->timing && !p->timer_pending)
    {
        p->timer_draw = true;
        p->timer_open = true;
        glBeginQuery(0x88BF, p->timer);
    }
    wf::gles::bind_render_buffer(data.target);
    // Wayfire binds the draw target only on GLES 3. Backdrop copies read it too.
    glBindFramebuffer(GL_FRAMEBUFFER, wf::gles::ensure_render_buffer_fb_id(data.target));
    GLint viewport[4];
    glGetIntegerv(GL_VIEWPORT, viewport);
    GLint draw_fbo = 0;
    if (p->es3)
    {
        glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &draw_fbo);
        // Wayfire binds the output for drawing but can leave a different FBO
        // bound for reading. The state guard restores both bindings.
        glBindFramebuffer(GL_READ_FRAMEBUFFER, draw_fbo);
        glReadBuffer(draw_fbo ? GL_COLOR_ATTACHMENT0 : GL_BACK);
    }
    // Real scene beneath the shared visible liquid, including overlapped window content.
    auto &bg = p->background;
    if (!bg.texture || bg.width != viewport[2] || bg.height != viewport[3])
        bg.allocate(viewport[2], viewport[3], true, p->es3, false);
    p->backdrop_geometry = data.target.geometry;
    p->backdrop_scale = data.target.scale;
    p->backdrop_transform = data.target.wl_transform;
    glBindTexture(GL_TEXTURE_2D, bg.texture);
    // Keep a backdrop cache: outside this pass's damage the framebuffer still contains
    // last frame's goo/windows. Copying all of it would feed those colors back into refraction.
    if (!reuse_backdrop)
    wf::gles::for_each_scissor_rect(data.target, capture,
                                    [&]
                                    {
                                        GLint box[4];
                                        glGetIntegerv(GL_SCISSOR_BOX, box);
                                        int x = std::max(box[0], viewport[0]),
                                            y = std::max(box[1], viewport[1]);
                                        int right = std::min(box[0] + box[2], viewport[0] + viewport[2]);
                                        int top = std::min(box[1] + box[3], viewport[1] + viewport[3]);
                                        if (right > x && top > y)
                                        {
                                            capture_pixels += uint64_t(right - x) * (top - y);
                                            glCopyTexSubImage2D(GL_TEXTURE_2D, 0, x - viewport[0],
                                                                y - viewport[1], x, y, right - x, top - y);
                                        }
                                    });
    auto ortho = wf::gles::render_target_orthographic_projection(data.target);
    const GLfloat vertices[] = {0, 0, float(p->width), 0, float(p->width),
                                float(p->height), 0, float(p->height)};
    if (reuse_backdrop)
    {
        // Nothing under the breathing strips changed, so nothing under them was
        // repainted this frame: put the cached backdrop back, then draw the breath on it.
        ++backdrop_reuses;
        auto &program = p->backdrop_p;
        program.use(wf::TEXTURE_TYPE_RGBA);
        program.uniformMatrix4f("MVP", ortho);
        program.uniformMatrix4f("uBackgroundMap", ortho);
        bind(program, "uBackground", 5, bg.texture);
        glDisable(GL_BLEND);
        glEnable(GL_SCISSOR_TEST);
        // Only inside the goo's own area and never over dry window content (GO27): no
        // backdrop is kept elsewhere, and the scene beneath painted it this frame.
        auto restore = data.target.framebuffer_region_from_geometry_region(data.damage) &
            data.target.framebuffer_region_from_geometry_region(breath_area) &
            data.target.framebuffer_region_from_geometry_region(area);
        if (dry_content)
            restore ^= data.target.framebuffer_region_from_geometry_region(*dry_content);
        for (auto &box : restore)
        {
            wf::gles::scissor_render_buffer(data.target, wlr_box_from_pixman_box(box));
            program.attrib_pointer("position", 2, 0, vertices);
            glDrawArrays(GL_TRIANGLE_FAN, 0, 4);
        }
        program.deactivate();
    }
    if (damage.empty())
    {
        if (p->timer_open)
        {
            glEndQuery(0x88BF);
            p->timer_open = false;
            p->timer_pending = true;
        }
        return;
    }
    auto setup_surface = [&](OpenGL::program_t &program, float surface_breath)
    {
        p->common(program, p->width, p->height);
        program.uniform2f("uFieldSize", p->field.width, p->field.height);
        program.uniform1f("uBreath", surface_breath);
        program.uniform1f("uBreathSwell", breath_swell(p->settings.thickness, p->settings.reach, p->settings.swell));
        program.uniformMatrix4f("MVP", ortho);
        program.uniformMatrix4f("uBackgroundMap", ortho);
        bind(program, "uBackground", 5, bg.texture);
        program.uniform1f("uWaveAmp", p->settings.wave_height);
        program.uniform1f("uShine", p->settings.shine);
        program.uniform1f("uRelief", p->settings.relief);
        program.uniform1f("uDepth", p->settings.depth);
        program.uniform1f("uProfile", p->settings.profile);
        program.uniform1f("uSoak", p->has_wallpaper ? p->settings.soak : 0);
        program.uniform1f("uAlpha", 1);
        program.uniform1f("uHints", std::any_of(p->sources.begin(), p->sources.end(),
            [](const source_t &s) { return s.hinted; }));
        program.uniform1f("uNeutralTint", std::any_of(p->sources.begin(), p->sources.end(),
            [](const source_t &s) { return s.dye_strength < .999999f; }));
    };
    // An active simulation already redraws the surface for a new field every
    // step. Keep that path direct; populate the cache once it settles.
    if (settled && p->cache_available &&
        (p->intrinsic.width != viewport[2] || p->intrinsic.height != viewport[3]))
    {
        p->cache_valid = false;
        bool ok = p->intrinsic.allocate(viewport[2], viewport[3], true, p->es3);
        ok = p->refraction.allocate(viewport[2], viewport[3], true, p->es3) && ok;
        if (!ok)
        {
            p->intrinsic.release();
            p->refraction.release();
            p->cache_available = false;
            loop::note(loop::note_id::goo_cache_unavailable);
        }
    }
    if (settled && p->cache_available)
    {
        auto strips = breath_area & area;
        int key_intervals = strips.empty() ? 0 : breath_key_count(p->settings, data.target.scale);
        bool requested_keys = breath_keys && !breath_exact && key_intervals > 0;
        if (requested_keys != p->requested_keyframes)
        {
            p->requested_keyframes = requested_keys;
            p->layer_b_available = true;
            p->layer_key[0] = p->layer_key[1] = -1;
            if (!requested_keys)
            {
                p->intrinsic_b.release();
                p->refraction_b.release();
            }
        }
        if (requested_keys && p->layer_b_available &&
            (p->intrinsic_b.width != viewport[2] || p->intrinsic_b.height != viewport[3]))
        {
            bool a_ok = p->intrinsic_b.allocate(viewport[2], viewport[3], true, p->es3);
            bool b_ok = p->refraction_b.allocate(viewport[2], viewport[3], true, p->es3);
            if (!a_ok || !b_ok)
            {
                p->intrinsic_b.release();
                p->refraction_b.release();
                p->layer_b_available = false;
                loop::note(loop::note_id::goo_keyframes_unavailable);
            }
        }
        int keys = requested_keys && p->layer_b_available ? key_intervals : 0;
        breath_keyframes_active = keys > 0;
        if ((keys > 0) != p->use_keyframes)
        {
            p->layer_key[0] = p->layer_key[1] = -1;
            p->use_keyframes = keys > 0;
        }
        breath_key_values.clear();
        if (keys)
        {
            const float swell = breath_swell(p->settings.thickness, p->settings.reach, p->settings.swell);
            for (int j = 0; j <= keys; ++j)
                breath_key_values.push_back(breath_key_value(j, keys, swell));
        }

        auto fb_area = data.target.framebuffer_region_from_geometry_region(area);
        auto fb_damage = data.target.framebuffer_region_from_geometry_region(damage);
        auto fb_strips = data.target.framebuffer_region_from_geometry_region(strips);
        auto static_area = fb_area ^ fb_strips;
        bool whole = !p->cache_valid || p->cache_dirty;
        if (whole)
            p->layer_key[0] = p->layer_key[1] = -1;

        auto each_pixel_rect = [&](const wf::region_t &region, auto draw)
        {
            glEnable(GL_SCISSOR_TEST);
            for (auto &r : region)
            {
                wf::gles::scissor_render_buffer(data.target, wlr_box_from_pixman_box(r));
                draw();
            }
        };
        auto render_layer = [&](int layer, const wf::region_t &region, float value)
        {
            if (region.empty()) return;
            glDisable(GL_BLEND);
            const std::array passes{
                std::make_pair(layer ? &p->intrinsic_b : &p->intrinsic, &p->intrinsic_p),
                std::make_pair(layer ? &p->refraction_b : &p->refraction, &p->refraction_p)};
            for (const auto &entry : passes)
            {
                auto &target = *entry.first;
                auto &program = *entry.second;
                bool params = entry.second == &p->refraction_p;
                glBindFramebuffer(GL_FRAMEBUFFER, target.fb);
                glViewport(0, 0, target.width, target.height);
                setup_surface(program, value);
                each_pixel_rect(region, [&]
                {
                    GLint box[4];
                    glGetIntegerv(GL_SCISSOR_BOX, box);
                    surface_pixels += uint64_t(box[2]) * box[3];
                    glScissor(box[0] - viewport[0], box[1] - viewport[1], box[2], box[3]);
                    glClearColor(params ? .5f : 0.f, params ? .5f : 0.f, 0, 0);
                    glClear(GL_COLOR_BUFFER_BIT);
                    program.attrib_pointer("position", 2, 0, vertices);
                    glDrawArrays(GL_TRIANGLE_FAN, 0, 4);
                });
                program.deactivate();
            }
        };
        if (whole)
            render_layer(0, static_area, breath);

        float mix = 0;
        if (keys)
        {
            int lo = 0;
            while (lo < keys - 1 && breath > breath_key_values[lo + 1])
                ++lo;
            int want[2] = {lo, lo + 1};
            if (p->layer_key[0] != lo && p->layer_key[1] != lo + 1 &&
                (p->layer_key[0] == lo + 1 || p->layer_key[1] == lo))
                std::swap(want[0], want[1]);
            if (whole || p->layer_key[0] != want[0])
            {
                render_layer(0, fb_strips, breath_key_values[want[0]]);
                if (!whole) ++breath_refreshes;
            }
            if (p->layer_key[1] != want[1])
            {
                render_layer(1, fb_strips, breath_key_values[want[1]]);
                if (!whole) ++breath_refreshes;
            }
            p->layer_key[0] = want[0];
            p->layer_key[1] = want[1];
            float a = breath_key_values[want[0]], b = breath_key_values[want[1]];
            mix = std::clamp((breath - a) / (b - a), 0.f, 1.f);
        } else
        {
            p->layer_key[0] = impl::exact_key;
            p->layer_key[1] = -1;
            // Exact fallback: only the wet strips run the full shader. Static goo
            // remains in the GO10 cache; no breathing offset enters that cache.
        }

        p->cache_valid = true;
        p->cache_dirty = false;
        wf::gles::bind_render_buffer(data.target);
        glEnable(GL_BLEND);
        glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);
        auto composite = [&](OpenGL::program_t &program, const wf::region_t &region)
        {
            if (region.empty()) return;
            program.use(wf::TEXTURE_TYPE_RGBA);
            program.uniformMatrix4f("MVP", ortho);
            program.uniformMatrix4f("uBackgroundMap", ortho);
            bind(program, "uIntrinsic", 0, p->intrinsic.texture);
            bind(program, "uRefraction", 1, p->refraction.texture);
            bind(program, "uBackground", 5, bg.texture);
            if (&program == &p->composite_mix_p)
            {
                bind(program, "uIntrinsicB", 2, p->intrinsic_b.texture);
                bind(program, "uRefractionB", 3, p->refraction_b.texture);
                program.uniform1f("uMix", mix);
            }
            each_pixel_rect(region, [&]
            {
                GLint box[4];
                glGetIntegerv(GL_SCISSOR_BOX, box);
                composite_pixels += uint64_t(box[2]) * box[3];
                program.attrib_pointer("position", 2, 0, vertices);
                glDrawArrays(GL_TRIANGLE_FAN, 0, 4);
            });
            program.deactivate();
        };
        composite(p->composite_p, fb_damage ^ fb_strips);
        auto animated = fb_damage & fb_strips;
        if (keys)
            composite(p->composite_mix_p, animated);
        else if (!animated.empty())
        {
            auto &program = p->fast ? p->render_fast : p->render_p;
            setup_surface(program, breath);
            each_pixel_rect(animated, [&]
            {
                GLint box[4];
                glGetIntegerv(GL_SCISSOR_BOX, box);
                surface_pixels += uint64_t(box[2]) * box[3];
                program.attrib_pointer("position", 2, 0, vertices);
                glDrawArrays(GL_TRIANGLE_FAN, 0, 4);
            });
            program.deactivate();
        }
    }
    else
    {
        auto &program = p->fast ? p->render_fast : p->render_p;
        setup_surface(program, breath);
        glEnable(GL_BLEND);
        glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);
        wf::gles::for_each_scissor_rect(data.target, damage, [&]
        {
            GLint box[4];
            glGetIntegerv(GL_SCISSOR_BOX, box);
            surface_pixels += uint64_t(box[2]) * box[3];
            program.attrib_pointer("position", 2, 0, vertices);
            glDrawArrays(GL_TRIANGLE_FAN, 0, 4);
        });
        program.deactivate();
    }
    if (p->timer_open)
    {
        glEndQuery(0x88BF);
        p->timer_open = false;
        p->timer_pending = true;
    }
}
bool renderer_t::timed_sleep() const
{
    return force_timed_sleep || !p->es3 || p->readback_failed;
}
bool renderer_t::readback_pending() const
{
    return std::any_of(p->energy_slots.begin(), p->energy_slots.end(), [](auto &s) { return s.busy; });
}
std::string renderer_t::readback_mode() const
{
    return force_timed_sleep ? "timed (test)" : !p->es3 ? "timed (GLES 2)" : p->readback_failed ? "timed (readback failed)" : "async";
}
void renderer_t::collect(int &budget)
{
    std::vector<impl::reading_t> readings;
    p->collect(budget, readings);
    if (p->readback_failed) return;
    for (auto &r : readings)
    {
        // Applied only if nothing changed since it was issued, in step order.
        if (r.invalidation != invalidation || r.w != p->width || r.h != p->height || r.step <= last_applied_step)
        {
            readings_stale++;
            continue;
        }
        last_applied_step = r.step;
        energy = r.value[0] / 255.f;
        wave_energy = r.value[1] / 255.f;
        dye_energy = r.value[2] / 255.f;
        readings_applied++;
    }
}
glm::vec4 renderer_t::sample_at(glm::vec2 point)
{
    SCOTTLAND_LOOP_SCOPE(goo_sample_at);
    if (!p->ready)
        return {};
    // The scene can test several candidate frames at the same pointer position. One
    // readback serves them all; the retained height/dye only changes on a simulation step.
    if (p->sampled_step == steps && p->sampled_point == point)
        return p->sampled_value;
    glm::vec4 result{};
    wf::gles::run_in_context_if_gles(
        [&]
        {
            state_t guard;
            glDisable(GL_SCISSOR_TEST);
            glDisable(GL_BLEND);
            p->common(p->query_p, 1, 1);
            p->query_p.uniform2f("uPoint", point.x, point.y);
            p->draw_to(p->query_p, p->query);
            unsigned char pixel[4];
            glReadPixels(0, 0, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, pixel);
            result = {pixel[0] / 255.f, pixel[1] / 255.f, pixel[2] / 255.f,
                      (pixel[3] / 255.f - 128.f / 255.f) * 8};
        });
    p->sampled_step = steps;
    p->sampled_point = point;
    p->sampled_value = result;
    return result;
}
bool renderer_t::overlapping() const { return p->overlap; }
bool renderer_t::backdrop_ready(const wf::render_target_t &target) const
{
    // The backdrop cache is RGBA8: it stands in for scene pixels losslessly only on an
    // ordinary 8-bit SDR target, and only one with the mapping it was copied under.
    wlr_dmabuf_attributes attrs{};
    if (target.get_output_transfer_function() != WLR_COLOR_TRANSFER_FUNCTION_SRGB ||
        !wlr_buffer_get_dmabuf(target.get_buffer(), &attrs) ||
        (attrs.format != DRM_FORMAT_XRGB8888 && attrs.format != DRM_FORMAT_ARGB8888 &&
         attrs.format != DRM_FORMAT_XBGR8888 && attrs.format != DRM_FORMAT_ABGR8888))
        return false;
    auto size = target.get_size();
    return p->ready && p->background.texture && p->cache_valid && !p->cache_dirty &&
        p->background.width == size.width && p->background.height == size.height &&
        p->backdrop_geometry == target.geometry && p->backdrop_scale == target.scale &&
        p->backdrop_transform == target.wl_transform;
}
bool renderer_t::highlighting() const { return p->controls; }

} // namespace scottland::goo
