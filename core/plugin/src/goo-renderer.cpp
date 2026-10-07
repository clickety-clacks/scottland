#include "goo-renderer.hpp"
#include "loop.hpp"
#include "goo-shaders.hpp"
#include "goo-gl.hpp"
#include "attention-breath.hpp"
#include <algorithm>
#include <array>
#include <cmath>
#include <chrono>
#include <map>
#include <cstring>
#include <wayfire/scene-render.hpp>
#include <wayfire/util/log.hpp>
#include <drm_fourcc.h>
#include <EGL/egl.h>
#include <EGL/eglext.h>
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
// GO26: how many keys of the breath to cache. Only as many as the swing needs at half a
// device pixel of shore travel per interval; never more than the ceiling. A swing that
// needs more keeps the ceiling and widens the interval just enough to cover it: the key
// count alone never sends the breath to the exact path.
//
// The ceiling is not a memory limit. Whatever the count, only the two keys around the
// current breath are held, in two layers of two RGBA8 output-sized textures (127 MiB in
// all for a 3840x2160 framebuffer, 4K at any scale; 63 MiB of that is the second layer).
// What grows with the count is work: each key the breath crosses re-renders one layer
// over the strips, and a breath crosses every key twice, against 125 exact renders in
// the same five seconds. With both cache textures written in one pass (GLES 3), 48 keys
// cost at most 96 renders a breath; where each refresh takes two passes (GLES 2), 24 do.
constexpr int breath_key_ceiling_one_pass = 48, breath_key_ceiling_two_pass = 24;
struct breath_plan_t
{
    int keys = 1;
    float spacing = 0;  // device pixels of shore travel per interval
};
breath_plan_t breath_key_plan(const settings_t &s, float scale, int ceiling)
{
    float swell = breath_swell(s.thickness, s.reach, s.swell);
    float travel = s.reach * std::log1p(std::max(swell, 0.f)) * std::max(scale, 1.f);
    breath_plan_t plan;
    plan.keys = std::clamp(int(std::ceil(travel / .5f)), 1, std::max(ceiling, 1));
    plan.spacing = travel / plan.keys;
    return plan;
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
    std::string gl_version, gl_renderer;  // read once by support(), reported by goo-state
    int width = 0, height = 0;
    GLuint timer = 0, auxiliary_timer[3] = {};
    GLsync check_fence = nullptr;
    EGLSyncKHR check_egl_fence = EGL_NO_SYNC_KHR;
    PFNEGLCREATESYNCKHRPROC create_egl_sync = nullptr;
    PFNEGLCLIENTWAITSYNCKHRPROC wait_egl_sync = nullptr;
    PFNEGLDESTROYSYNCKHRPROC destroy_egl_sync = nullptr;
    target_t change_read;
    void cancel_check()
    {
        if (check_fence) { glDeleteSync(check_fence); check_fence = nullptr; }
        if (check_egl_fence != EGL_NO_SYNC_KHR)
        {
            destroy_egl_sync(eglGetCurrentDisplay(), check_egl_fence);
            check_egl_fence = EGL_NO_SYNC_KHR;
        }
    }
    bool auxiliary_pending[3] = {};
    bool timing = false, timer_pending = false, timer_open = false, timer_draw = false;
    float time = 0;
    uint64_t sampled_step = UINT64_MAX;
    glm::vec2 sampled_point{};
    glm::vec4 sampled_value{};
    settings_t settings;
    bool overlap = false, controls = false, fast = true, open_pickup = false;
    bool cache_valid = false, cache_dirty = true, cache_available = true;
    wf::geometry_t backdrop_geometry{};
    float backdrop_scale = 0;
    wl_output_transform backdrop_transform = WL_OUTPUT_TRANSFORM_NORMAL;
    static constexpr int exact_key = -2;
    int layer_key[2] = {-1, -1};
    bool layer_b_available = true, requested_keyframes = false, use_keyframes = false;
    std::vector<source_t> sources;
    OpenGL::program_t field_p, mask_p, wave_p, dye_p, render_p, energy_p, query_p, copy_p, backdrop_p;
    OpenGL::program_t under_p, change_p;
    // GO26: both cache textures of a layer as two attachments of one framebuffer.
    bool mrt = false, layer_fail_seen = false, cache_fail_seen = false;
    OpenGL::program_t cache_p;
    GLuint cache_fb[2] = {0, 0}, cache_fb_tex[2][2] = {{0, 0}, {0, 0}};
    GLuint cache_framebuffer(int layer)
    {
        if (!mrt)
            return 0;
        GLuint color = (layer ? intrinsic_b : intrinsic).texture, params = (layer ? refraction_b : refraction).texture;
        if (!color || !params)
            return 0;
        if (cache_fb[layer] && cache_fb_tex[layer][0] == color && cache_fb_tex[layer][1] == params)
            return cache_fb[layer];
        if (!cache_fb[layer])
            glGenFramebuffers(1, &cache_fb[layer]);
        glBindFramebuffer(GL_FRAMEBUFFER, cache_fb[layer]);
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, color, 0);
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT1, GL_TEXTURE_2D, params, 0);
        if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE)
        {
            LOGI("scottland goo: single-pass cache refresh unavailable; using two passes");
            mrt = false;
            return 0;
        }
        cache_fb_tex[layer][0] = color;
        cache_fb_tex[layer][1] = params;
        return cache_fb[layer];
    }
    OpenGL::program_t intrinsic_p, refraction_p, composite_p, composite_mix_p;
    OpenGL::program_t field_fast, mask_fast, wave_fast, dye_fast, render_fast;
    target_t field, mask, wave[2], dye[2], source, curve, background, query, atlas;
    // GO28: the backdrop at the dye grid's resolution, and the copy of it the dye last saw.
    // Frames only note what they copied (`under_pending`, output-logical, with the mapping it
    // was copied under); the pickup texture is refreshed when it is next needed, outside the
    // frame where possible, so a frame never switches framebuffers for it.
    target_t under, seen;
    wf::regionf_t under_pending;
    glm::mat4 under_map{1};
    uint64_t flush_under()
    {
        if (under_pending.empty() || !under.fb || !background.texture)
            return 0;
        auto texels = refresh_under(under_pending, under_map);
        under_pending.clear();
        return texels;
    }
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
    void poll_auxiliary(renderer_t &owner)
    {
        for (int i = 0; i < 3; ++i)
        {
            if (!auxiliary_pending[i]) continue;
            GLuint available = 0;
            glGetQueryObjectuiv(auxiliary_timer[i], GL_QUERY_RESULT_AVAILABLE, &available);
            if (!available) continue;
            GLuint ns = 0;
            GLint disjoint = 0;
            glGetQueryObjectuiv(auxiliary_timer[i], GL_QUERY_RESULT, &ns);
            glGetIntegerv(0x8FBB, &disjoint);
            if (!disjoint)
            {
                (i == 0 ? owner.flow_gpu_ms : i == 1 ? owner.check_gpu_ms : owner.seen_gpu_ms) += ns / 1e6;
                ++(i == 0 ? owner.flow_gpu_samples : i == 1 ? owner.check_gpu_samples : owner.seen_gpu_samples);
            }
            auxiliary_pending[i] = false;
        }
    }
    bool begin_auxiliary(int kind, renderer_t &owner)
    {
        poll_auxiliary(owner);
        if (!timing || timer_open || auxiliary_pending[kind]) return false;
        glBeginQuery(0x88BF, auxiliary_timer[kind]);
        return true;
    }
    void end_auxiliary(int kind, bool measured)
    {
        if (!measured) return;
        glEndQuery(0x88BF);
        auxiliary_pending[kind] = true;
    }
    void release()
    {
        release_readback();
        cancel_check();
        change_read.release();
        if (auxiliary_timer[0]) glDeleteQueries(3, auxiliary_timer);
        if (timer)
            glDeleteQueries(1, &timer);
        for (auto &fb : cache_fb)
        {
            if (fb)
                glDeleteFramebuffers(1, &fb);
            fb = 0;
        }
        for (auto p : {&field_p, &mask_p, &wave_p, &dye_p, &render_p, &energy_p, &query_p, &copy_p, &backdrop_p,
                       &under_p, &change_p,
                       &intrinsic_p, &refraction_p, &composite_p, &composite_mix_p, &cache_p,
                       &field_fast, &mask_fast, &wave_fast, &dye_fast, &render_fast})
            p->free_resources();
        for (auto p : {&field, &mask, &wave[0], &wave[1], &dye[0], &dye[1], &source, &curve, &background, &query,
                       &under, &seen,
                       &intrinsic, &refraction, &intrinsic_b, &refraction_b, &atlas})
            p->release();
        for (auto &t : reduction)
            t.release();
    }
    // Compiles one program variant (goo-shaders.hpp) and reports, by name, a variant that
    // does not link.
    bool build(OpenGL::program_t &program, const program_variant &variant)
    {
        program.compile(vertex_source(es3), fragment_source(variant, es3));
        GLint linked = 0;
        glGetProgramiv(program.get_program_id(wf::TEXTURE_TYPE_RGBA), GL_LINK_STATUS, &linked);
        if (!linked)
            LOGE("scottland goo: the ", variant.name, " shader did not link");
        return linked;
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
        // Kept for goo-state, which identifies the renderer for benchmarks (no log after init()).
        const char *renderer = (const char *)glGetString(GL_RENDERER);
        gl_version = version ? version : "";
        gl_renderer = renderer ? renderer : "";
        if (!available)
            return false;
        // GLES 2 uses the EGL fence extension where available; completion is still
        // polled with timeout zero. No worker may migrate the compositor's GL context.
        if (extension(eglQueryString(eglGetCurrentDisplay(), EGL_EXTENSIONS), "EGL_KHR_fence_sync"))
        {
            create_egl_sync = reinterpret_cast<PFNEGLCREATESYNCKHRPROC>(eglGetProcAddress("eglCreateSyncKHR"));
            wait_egl_sync = reinterpret_cast<PFNEGLCLIENTWAITSYNCKHRPROC>(eglGetProcAddress("eglClientWaitSyncKHR"));
            destroy_egl_sync = reinterpret_cast<PFNEGLDESTROYSYNCKHRPROC>(eglGetProcAddress("eglDestroySyncKHR"));
            if (!wait_egl_sync || !destroy_egl_sync) create_egl_sync = nullptr;
        }
        timing = es3 && extension(extensions, "GL_EXT_disjoint_timer_query");
        if (timing)
        {
            glGenQueries(1, &timer);
            glGenQueries(3, auxiliary_timer);
        }
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
        const std::map<std::string, OpenGL::program_t*> programs{
            {"field", &field_p}, {"mask", &mask_p}, {"wave", &wave_p}, {"dye", &dye_p}, {"render", &render_p},
            {"energy", &energy_p}, {"query", &query_p}, {"field_fast", &field_fast}, {"mask_fast", &mask_fast},
            {"wave_fast", &wave_fast}, {"dye_fast", &dye_fast}, {"render_fast", &render_fast},
            {"intrinsic", &intrinsic_p}, {"refraction", &refraction_p}, {"cache_both", &cache_p},
            {"composite", &composite_p}, {"composite_mix", &composite_mix_p},
            {"backdrop", &backdrop_p}, {"copy", &copy_p}, {"under", &under_p}, {"change", &change_p}};
        mrt = false;
        for (auto &variant : program_variants())
        {
            auto program = programs.find(variant.name);
            if (program == programs.end())
            {
                LOGE("scottland goo: no program for the ", variant.name, " shader");
                available = false;
                continue;
            }
            if (variant.es3_only && !es3)
                continue;
            bool linked = build(*program->second, variant);
            if (program->second == &cache_p)
            {
                // GO26: one pass writes both caches; otherwise each refresh takes two passes.
                mrt = linked;
                if (!mrt)
                    LOGI("scottland goo: single-pass cache refresh unavailable; using two passes");
            } else if (!linked && variant.required)
                available = false;
        }
        if (programs.size() != program_variants().size())
        {
            LOGE("scottland goo: shader table and programs differ");
            available = false;
        }
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
        glUniform2f(glGetUniformLocation(id, "uDyeSize"), dye[0].width, dye[0].height);
        glUniform2f(glGetUniformLocation(id, "uSize"), w, h);
        one("uTime", time);
        one("uDyeStrength", settings.dye_density);
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
        // GO28: pickup relative to release; the dye mixes it subtractively with what is there.
        // Pickup balance (Mike, 2026-10-05: "just give me a slider") is the share of picked-up
        // color at full pickup, in the middle of an ordinary band: there a window releases at
        // about 0.75 and pickup lands at about 0.8 of its rate, so this rate gives that share.
        // Wallpaper pickup follows soak^0.25 below that (GO24's curve), so the shipped 0.12 stays
        // clearly visible.
        float balance = std::clamp(settings.pickup_balance, 0.f, .99f);
        float soak = settings.soak > 0 ? std::pow(settings.soak, .25f) : 0;
        one("uPickup", .94f * soak * balance / (1 - balance));
        one("uPickupRich", soak);
        one("uOpenPickup", open_pickup ? 1 : 0);
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
        new_generation();
        width = w;
        height = h;
        wave_tiles.clear();
        under_pending.clear();
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
            else  // the clear color (.6, .7, .8) as stored absorbance (GO28)
                glClearColor(.2918, .2439, .1928, 1);
            glClear(GL_COLOR_BUFFER_BIT);
        }
        // Nothing beneath is known until a frame copies it.
        for (auto t : {&under, &seen})
        {
            ok = t->allocate((w + 3) / 4, (h + 3) / 4, true, es3) && ok;
            glClearColor(0, 0, 0, 0);
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
            data.emplace_back(s.attention && s.emitter ? 1.f : 0.f, s.dye_strength,
                s.state_mix, s.neutral_strength);
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
    // One pass of the dye: advection, spread, state release and pickup of what lies beneath.
    void dye_pass(OpenGL::program_t &dye_program, float flow, float step, bool wet_only)
    {
        common(dye_program, dye[1].width, dye[1].height);
        dye_program.uniform1f("uSpread", settings.spread);
        dye_program.uniform1f("uSwirl", settings.swirl);
        dye_program.uniform1f("uRelease", settings.release);
        dye_program.uniform1f("uFlow", flow);
        dye_program.uniform1f("uStep", step);
        dye_program.uniform1f("uWetOnly", wet_only ? 1 : 0);
        bind(dye_program, "uUnder", 5, under.texture);
        draw_to(dye_program, dye[1]);
        std::swap(dye[0], dye[1]);
    }
    // GO28: average the backdrop just copied into the dye grid, over texels whose whole footprint
    // lies in `copied` (output-logical). Elsewhere `under` keeps what it last knew.
    uint64_t refresh_under(const wf::regionf_t &copied, const glm::mat4 &map)
    {
        double sx = double(under.width) / width, sy = double(under.height) / height;
        tiles.clear();
        uint64_t texels = 0;
        for (auto &r : copied)
        {
            float x1 = std::max(0.0, std::ceil(r.x1 * sx)), y1 = std::max(0.0, std::ceil(r.y1 * sy));
            float x2 = std::min<double>(under.width, std::floor(r.x2 * sx));
            float y2 = std::min<double>(under.height, std::floor(r.y2 * sy));
            if (x2 <= x1 || y2 <= y1)
                continue;
            texels += uint64_t(x2 - x1) * (y2 - y1);
            tiles.insert(tiles.end(), {x1, y1, x2, y1, x2, y2, x1, y1, x2, y2, x1, y2});
        }
        if (tiles.empty())
            return 0;
        under_p.use(wf::TEXTURE_TYPE_RGBA);
        under_p.uniformMatrix4f("MVP", glm::ortho(0.f, float(under.width), 0.f, float(under.height), -1.f, 1.f));
        under_p.uniformMatrix4f("uBackgroundMap", map);
        under_p.uniform2f("uRes", width, height);
        under_p.uniform2f("uSize", under.width, under.height);
        bind(under_p, "uBackground", 5, background.texture);
        glDisable(GL_BLEND);
        glDisable(GL_SCISSOR_TEST);
        glBindFramebuffer(GL_FRAMEBUFFER, under.fb);
        glViewport(0, 0, under.width, under.height);
        under_p.attrib_pointer("position", 2, 0, tiles.data());
        glDrawArrays(GL_TRIANGLES, 0, tiles.size() / 2);
        under_p.deactivate();
        return texels;
    }
    // Asynchronous energy readings (main-loop Phase 3): a ring of four pixel buffers with fences.
    struct energy_slot_t
    {
        GLuint pbo = 0;
        GLsync fence = nullptr;
        bool busy = false;
        uint64_t issued_ns = 0, step = 0, invalidation = 0, generation = 0;
        int w = 0, h = 0;
    };
    std::array<energy_slot_t, 4> energy_slots;
    bool readback_failed = false;
    // The simulation's incarnation: every resize (and output change) starts a new one, retiring
    // the readings in flight, so a reading never applies across one even at equal dimensions.
    uint64_t generation = 1;
    std::string readback_fault;  // tests only (goo-state, SCOTTLAND_TEST_MODEL)
    struct reading_t { unsigned char value[4]; uint64_t step, invalidation, generation; int w, h; };
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
    /** A new simulation incarnation: nothing issued before it applies. In a GL context. */
    void new_generation()
    {
        generation++;
        for (auto &slot : energy_slots)
            if (slot.busy) retire(slot);
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
    /** Clear GL's error flags (bounded: a context that keeps failing is a failure). */
    static bool clear_errors()
    {
        for (int i = 0; i < 8; i++)
            if (glGetError() == GL_NO_ERROR) return true;
        return false;
    }
    /** Issue a reading into a free slot (else skip: false). Never waits. A failure to allocate,
     *  read or fence enters the timed fallback (GO10) and applies nothing. */
    bool issue(uint64_t step, uint64_t invalidation)
    {
        SCOTTLAND_LOOP_SCOPE(goo_energy_issue);
        auto free = std::find_if(energy_slots.begin(), energy_slots.end(), [](auto &s) { return !s.busy; });
        if (free == energy_slots.end()) return false;
        reduce();
        auto &slot = *free;
        // The pack state the read depends on is normalized for a four-byte destination and
        // Wayfire's is restored whatever happens here (any of it may legally be set).
        struct pack_t { GLint buffer = 0, alignment = 4, row_length = 0, skip_pixels = 0, skip_rows = 0; } previous;
        glGetIntegerv(GL_PIXEL_PACK_BUFFER_BINDING, &previous.buffer);
        glGetIntegerv(GL_PACK_ALIGNMENT, &previous.alignment);
        glGetIntegerv(GL_PACK_ROW_LENGTH, &previous.row_length);
        glGetIntegerv(GL_PACK_SKIP_PIXELS, &previous.skip_pixels);
        glGetIntegerv(GL_PACK_SKIP_ROWS, &previous.skip_rows);
        struct restore_t
        {
            pack_t p;
            ~restore_t()
            {
                glBindBuffer(GL_PIXEL_PACK_BUFFER, p.buffer);
                glPixelStorei(GL_PACK_ALIGNMENT, p.alignment);
                glPixelStorei(GL_PACK_ROW_LENGTH, p.row_length);
                glPixelStorei(GL_PACK_SKIP_PIXELS, p.skip_pixels);
                glPixelStorei(GL_PACK_SKIP_ROWS, p.skip_rows);
            }
        } restore{previous};
        auto fail = [&]
        {
            readback_failed = true;
            loop::note(loop::note_id::goo_readback_failed, step);
            return false;
        };
        if (!clear_errors()) return fail();
        if (!slot.pbo)
        {
            glGenBuffers(1, &slot.pbo);
            if (!slot.pbo) return fail();
            glBindBuffer(GL_PIXEL_PACK_BUFFER, slot.pbo);
            glBufferData(GL_PIXEL_PACK_BUFFER, 4, nullptr, GL_STREAM_READ);
            if (glGetError() != GL_NO_ERROR) return fail();
        } else
            glBindBuffer(GL_PIXEL_PACK_BUFFER, slot.pbo);
        if (readback_fault == "prior-value")
        {
            // Tests: what an unwritten buffer would show (a settled reading), then a failing read.
            const unsigned char settled[4] = {0, 0, 0, 0};
            glBufferSubData(GL_PIXEL_PACK_BUFFER, 0, 4, settled);
            glPixelStorei(GL_PACK_SKIP_PIXELS, 1);
            glReadPixels(0, 0, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, nullptr);  // past the 4-byte buffer
            glPixelStorei(GL_PACK_SKIP_PIXELS, 0);
        } else
        {
            if (readback_fault == "incoming-pack-state")
            {
                // Tests: legal incoming pack state (Astra's probe) that the normalization below undoes.
                glPixelStorei(GL_PACK_SKIP_PIXELS, 1);
                glPixelStorei(GL_PACK_SKIP_ROWS, 2);
                glPixelStorei(GL_PACK_ROW_LENGTH, 7);
                glPixelStorei(GL_PACK_ALIGNMENT, 8);
            }
            glPixelStorei(GL_PACK_ALIGNMENT, 1);
            glPixelStorei(GL_PACK_ROW_LENGTH, 0);
            glPixelStorei(GL_PACK_SKIP_PIXELS, 0);
            glPixelStorei(GL_PACK_SKIP_ROWS, 0);
            glReadPixels(0, 0, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, nullptr);
        }
        // A fence only says the commands completed, not that the read succeeded.
        if (glGetError() != GL_NO_ERROR) return fail();
        slot.fence = glFenceSync(GL_SYNC_GPU_COMMANDS_COMPLETE, 0);
        if (!slot.fence) return fail();
        // Submitted now, even if no further frame is drawn.
        {
            SCOTTLAND_LOOP_SCOPE(goo_energy_flush);
            glFlush();
        }
        slot.busy = true;
        slot.issued_ns = mono_ns();
        slot.step = step;
        slot.invalidation = invalidation;
        slot.generation = generation;
        slot.w = width;
        slot.h = height;
        return true;
    }
    /** Examine at most `budget` busy slots, oldest first, without waiting. */
    void collect(int &budget, std::vector<reading_t> &out)
    {
        SCOTTLAND_LOOP_SCOPE(goo_energy_collect);
        if (readback_fault == "hold") return;  // tests: readings stay in flight until released
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
            GLenum status = readback_fault == "wait-failed" ? GL_WAIT_FAILED : glClientWaitSync(slot.fence, 0, 0);
            if (status == GL_ALREADY_SIGNALED || status == GL_CONDITION_SATISFIED)
            {
                // A missing or corrupted value is never read as zero energy: any failure here
                // retires the slot and enters the timed fallback without applying anything.
                clear_errors();
                glBindBuffer(GL_PIXEL_PACK_BUFFER, slot.pbo);
                auto mapped = readback_fault == "map-failed" ? nullptr :
                    static_cast<const unsigned char *>(glMapBufferRange(GL_PIXEL_PACK_BUFFER, 0, 4, GL_MAP_READ_BIT));
                if (!mapped)
                {
                    retire(slot);
                    readback_failed = true;
                    loop::note(loop::note_id::goo_readback_failed, slot.step);
                    continue;
                }
                reading_t r{{mapped[0], mapped[1], mapped[2], mapped[3]}, slot.step, slot.invalidation, slot.generation, slot.w, slot.h};
                bool intact = glUnmapBuffer(GL_PIXEL_PACK_BUFFER) == GL_TRUE && readback_fault != "unmap-failed";
                retire(slot);
                if (!intact || glGetError() != GL_NO_ERROR)
                {
                    readback_failed = true;
                    loop::note(loop::note_id::goo_readback_failed, slot.step);
                    continue;
                }
                out.push_back(r);
            } else if (status == GL_WAIT_FAILED || mono_ns() - slot.issued_ns > 1000000000ull)
            {
                retire(slot);  // an idle desktop never keeps the collection timer
                readback_failed = true;
                loop::note(loop::note_id::goo_readback_failed, slot.step);
            }
        }
    }
};
// The ring's code for a GO26 exact-path reason (NOTE goo_breath_exact names them).
static uint64_t breath_reason_code(const std::string &reason)
{
    static const char *reasons[] = {"test override", "keyframes are switched off (scottland/goo_breath_keys)",
        "the second cache layer could not be allocated", "the surface cache is unavailable"};
    for (uint64_t i = 0; i < 4; i++)
        if (reason == reasons[i]) return i;
    return 4;
}
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
void renderer_t::poll_timing()
{
    if (!p->timing) return;
    wf::gles::run_in_context_if_gles([&] {
        p->poll_timer(last_gpu_ms, last_draw_gpu_ms);
        p->poll_auxiliary(*this);
    });
}
bool renderer_t::supported()
{
    bool ok = false;
    wf::gles::run_in_context_if_gles([&] { ok = p->support(); });
    packed = p->packed;
    gpu_timing = p->timing;
    return ok;
}
bool renderer_t::update(const std::vector<source_t> &sources, const settings_t &s, int w, int h, float time,
                        const std::vector<glm::vec4> &impulses, const std::vector<wf::geometry_t> &area, float flow)
{
    state_t guard;
    if (!p->support())
        return false;
    gpu_timing = p->timing;
    p->poll_timer(last_gpu_ms, last_draw_gpu_ms);
    p->poll_auxiliary(*this);
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
    p->open_pickup = open_pickup;
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
    under_pixels += p->flush_under();
    p->dye_pass(dye_program, flow, 1, false);
    packed = p->packed;
    steps++;
    if (!impulses.empty()) invalidation++;
    if (!timed_sleep())
    {
        // The dispatch's one allowance, shared by every output (main-loop Phase 3).
        int own = 2;
        collect(allowance ? allowance->left : own);
        if (allowance && allowance->spent) allowance->spent();
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
void renderer_t::flow_dye(float flow, float step, int passes)
{
    if (!p->ready)
        return;
    state_t guard;
    auto started = std::chrono::steady_clock::now();
    bool measured = p->begin_auxiliary(0, *this);
    glDisable(GL_SCISSOR_TEST);
    glDisable(GL_BLEND);
    p->open_pickup = open_pickup;
    under_pixels += p->flush_under();
    for (int i = 0; i < passes; i++)
        p->dye_pass(p->fast ? p->dye_fast : p->dye_p, flow, step, true);
    p->sampled_step = UINT64_MAX;  // a dye readback is stale now
    ++dye_flows;
    p->end_auxiliary(0, measured);
    double elapsed = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - started).count();
    flow_wall_ms += elapsed;
    flow_wall_max_ms = std::max(flow_wall_max_ms, elapsed);
}
void renderer_t::draw(const wf::scene::render_instruction_t &data, const wf::regionf_t &area,
                      const wf::regionf_t &breath_area, float breath, bool settled, bool breath_keys,
              bool reuse_backdrop, const wf::regionf_t *dry, const wf::regionf_t *dry_content,
              const wf::regionf_t *reuse_area)
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
    // A reused frame restores only inside `area` (GO27), which this already covers, so the
    // reuse region (`reuse_area`: the breathing strips or GO24's motion area) needs no copy of its
    // own: its parts in dry content or open desktop are never restored.
    auto capture = data.damage & capture_area;
    // Window content no goo lies on is never sampled as backdrop either.
    if (dry)
        capture ^= *dry;
    if (capture.empty())
        return;
    ++draws;
    state_t guard(p->es3);
    p->poll_timer(last_gpu_ms, last_draw_gpu_ms);
    p->poll_auxiliary(*this);
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
    {
        bg.allocate(viewport[2], viewport[3], true, p->es3, false);
        p->under_pending.clear();  // what it noted was copied into the old texture
    }
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
    if (!reuse_backdrop && p->settings.soak > 0 && p->under.fb)
    {
        // GO28: what the goo picks up is what was just copied from beneath it. Only noted here.
        p->under_pending |= capture;
        p->under_map = ortho;
    }
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
            data.target.framebuffer_region_from_geometry_region(reuse_area ? *reuse_area : breath_area) &
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
        program.uniform1f("uSoak", p->settings.soak);
        program.uniform1f("uAlpha", 1);
        program.uniform1f("uHints", std::any_of(p->sources.begin(), p->sources.end(),
            [](const source_t &s) { return s.hinted; }));
    };
    // An active simulation already redraws the surface for a new field every
    // step. Keep that path direct; populate the cache once it settles.
    if (surface_cache_fail != p->cache_fail_seen)
    {
        // Tests: lose the surface cache, or try for it again.
        p->cache_fail_seen = surface_cache_fail;
        p->intrinsic.release();
        p->refraction.release();
        p->intrinsic_b.release();
        p->refraction_b.release();
        p->cache_available = true;
        p->cache_valid = false;
        p->layer_key[0] = p->layer_key[1] = -1;
        p->cache_fb_tex[0][0] = p->cache_fb_tex[0][1] = p->cache_fb_tex[1][0] = p->cache_fb_tex[1][1] = 0;
    }
    // New cache storage has undefined contents (zero on some drivers, not on others). Give
    // it the value of "no goo here", so a pixel composited before it was ever shaded draws nothing.
    auto clear_cache = [&] (target_t &color, target_t &params)
    {
        GLboolean scissor = glIsEnabled(GL_SCISSOR_TEST);
        glDisable(GL_SCISSOR_TEST);
        glBindFramebuffer(GL_FRAMEBUFFER, color.fb);
        glClearColor(0, 0, 0, 0);
        glClear(GL_COLOR_BUFFER_BIT);
        glBindFramebuffer(GL_FRAMEBUFFER, params.fb);
        glClearColor(.5f, .5f, 0, 0);
        glClear(GL_COLOR_BUFFER_BIT);
        if (scissor)
            glEnable(GL_SCISSOR_TEST);
    };
    if (settled && p->cache_available &&
        (p->intrinsic.width != viewport[2] || p->intrinsic.height != viewport[3]))
    {
        p->cache_valid = false;
        bool ok = !surface_cache_fail && p->intrinsic.allocate(viewport[2], viewport[3], true, p->es3);
        ok = !surface_cache_fail && p->refraction.allocate(viewport[2], viewport[3], true, p->es3) && ok;
        // New storage, possibly under a reused texture name: attach it again.
        p->cache_fb_tex[0][0] = p->cache_fb_tex[0][1] = 0;
        if (!ok)
        {
            p->intrinsic.release();
            p->refraction.release();
            p->cache_available = false;
            loop::note(loop::note_id::goo_cache_unavailable);
        }
        else
            clear_cache(p->intrinsic, p->refraction);
    }
    if (settled && !p->cache_available)
    {
        // GO26: the whole goo draws directly; say so where the other exact-path reasons are.
        breath_keyframes_active = false;
        std::string reason = (breath_area & area).empty() ? "" : "the surface cache is unavailable";
        if (reason != breath_exact_reason)
        {
            if (!reason.empty())
                loop::note(loop::note_id::goo_breath_exact, breath_reason_code(reason));
            breath_exact_reason = reason;
        }
    }
    if (settled && p->cache_available)
    {
        auto strips = breath_area & area;
        breath_key_ceiling = p->mrt ? breath_key_ceiling_one_pass : breath_key_ceiling_two_pass;
        auto plan = breath_key_plan(p->settings, data.target.scale, breath_key_ceiling);
        int key_intervals = strips.empty() ? 0 : plan.keys;
        breath_key_spacing = strips.empty() ? 0 : plan.spacing;
        bool requested_keys = breath_keys && !breath_exact && key_intervals > 0;
        if (breath_layer_fail != p->layer_fail_seen)
        {
            // Tests: lose the second layer, or try for it again.
            p->layer_fail_seen = breath_layer_fail;
            p->intrinsic_b.release();
            p->refraction_b.release();
            p->layer_b_available = true;
            p->layer_key[0] = p->layer_key[1] = -1;
        }
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
            bool a_ok = !breath_layer_fail && p->intrinsic_b.allocate(viewport[2], viewport[3], true, p->es3);
            bool b_ok = !breath_layer_fail && p->refraction_b.allocate(viewport[2], viewport[3], true, p->es3);
            p->cache_fb_tex[1][0] = p->cache_fb_tex[1][1] = 0;
            if (!a_ok || !b_ok)
            {
                p->intrinsic_b.release();
                p->refraction_b.release();
                p->layer_b_available = false;
            }
            else
                clear_cache(p->intrinsic_b, p->refraction_b);
        }
        int keys = requested_keys && p->layer_b_available ? key_intervals : 0;
        breath_keyframes_active = keys > 0;
        // The exact path (the full surface shader over the strips on every tick) is for
        // these cases only, never for the number of keys. Say which, in goo-state always
        // and in the log once each time it changes.
        std::string reason = strips.empty() || keys > 0 ? "" :
            breath_exact ? "test override" :
            !breath_keys ? "keyframes are switched off (scottland/goo_breath_keys)" :
            !p->layer_b_available ? "the second cache layer could not be allocated" : "unknown";
        if (reason != breath_exact_reason)
        {
            if (!reason.empty())
                loop::note(loop::note_id::goo_breath_exact, breath_reason_code(reason));
            else if (!strips.empty())
                loop::note(loop::note_id::goo_breath_keyframes, keys, uint64_t(std::lround(breath_key_spacing)),
                    uint64_t(std::lround(breath_key_ceiling)));
            breath_exact_reason = reason;
        }
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
            if (GLuint fb = p->cache_framebuffer(layer))
            {
                // Both cache textures in one pass of the surface shader.
                const GLenum both[] = {GL_COLOR_ATTACHMENT0, GL_COLOR_ATTACHMENT1};
                const GLfloat clear_color[] = {0, 0, 0, 0}, clear_params[] = {.5f, .5f, 0, 0};
                auto &target = layer ? p->intrinsic_b : p->intrinsic;
                glBindFramebuffer(GL_FRAMEBUFFER, fb);
                glDrawBuffers(2, both);
                glViewport(0, 0, target.width, target.height);
                setup_surface(p->cache_p, value);
                each_pixel_rect(region, [&]
                {
                    GLint box[4];
                    glGetIntegerv(GL_SCISSOR_BOX, box);
                    surface_pixels += uint64_t(box[2]) * box[3];
                    glScissor(box[0] - viewport[0], box[1] - viewport[1], box[2], box[3]);
                    glClearBufferfv(GL_COLOR, 0, clear_color);
                    glClearBufferfv(GL_COLOR, 1, clear_params);
                    p->cache_p.attrib_pointer("position", 2, 0, vertices);
                    glDrawArrays(GL_TRIANGLE_FAN, 0, 4);
                });
                p->cache_p.deactivate();
                return;
            }
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
            bind(program, "uDyeTex", 4, p->dye[0].texture);
            program.uniform2f("uRes", p->width, p->height);
            program.uniform2f("uDyeSize", p->dye[0].width, p->dye[0].height);
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
void renderer_t::new_generation()
{
    p->new_generation();  // deletes fences only: no GL state to restore
    last_applied_step = 0;
}
uint64_t renderer_t::generation() const { return p->generation; }
void renderer_t::set_readback_fault(const std::string &fault)
{
    p->readback_fault = fault;
    if (fault.empty()) p->readback_failed = false;  // tests: back to the asynchronous reading
}
int renderer_t::readback_in_flight() const
{
    return (int)std::count_if(p->energy_slots.begin(), p->energy_slots.end(), [](auto &s) { return s.busy; });
}
std::string renderer_t::gl_description() const
{
    return p->checked ? p->gl_version + ", renderer " + p->gl_renderer : "";
}
std::string renderer_t::readback_mode() const
{
    return force_timed_sleep ? "timed (test)" : !p->es3 ? "timed (GLES 2)" : p->readback_failed ? "timed (readback failed)" : "async";
}
void renderer_t::collect(int &budget)
{
    std::vector<impl::reading_t> readings;
    int before = budget;
    p->collect(budget, readings);
    if (allowance) allowance->examined += before - budget;  // tests: slots examined per dispatch
    if (p->readback_failed) return;
    for (auto &r : readings)
    {
        // Applied only if nothing changed since it was issued, in step order.
        if (r.generation != p->generation || r.invalidation != invalidation || r.w != p->width || r.h != p->height ||
            r.step <= last_applied_step)
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
int renderer_t::backdrop_changes()
{
    if (!p->ready || !p->under.fb)
        return 0;
    int count = 0;
    auto started = std::chrono::steady_clock::now();
    wf::gles::run_in_context_if_gles([&]
    {
        state_t guard;
        // ES3: completion is polled without waiting on the compositor main loop. The
        // dedicated one-pixel target cannot be overwritten by diagnostic dye queries.
        if (p->check_fence || p->check_egl_fence != EGL_NO_SYNC_KHR)
        {
            bool ready = false, failed = false;
            if (p->check_fence)
            {
                GLenum status = glClientWaitSync(p->check_fence, 0, 0);
                ready = status == GL_ALREADY_SIGNALED || status == GL_CONDITION_SATISFIED;
                failed = status == GL_WAIT_FAILED;
            }
            else
            {
                EGLint status = p->wait_egl_sync(eglGetCurrentDisplay(), p->check_egl_fence, 0, 0);
                ready = status == EGL_CONDITION_SATISFIED_KHR;
                failed = status == EGL_FALSE;
            }
            if (!ready && !failed) { count = -1; return; }
            p->cancel_check();
            if (failed) { count = 255; return; }
            glBindFramebuffer(GL_FRAMEBUFFER, p->change_read.fb);
            unsigned char value[4];
            glReadPixels(0, 0, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, value);
            count = value[0];
            p->poll_auxiliary(*this);
            return;
        }
        bool measured = p->begin_auxiliary(1, *this);
        glDisable(GL_SCISSOR_TEST);
        glDisable(GL_BLEND);
        under_pixels += p->flush_under();
        GLuint input = 0;
        int iw = p->under.width, ih = p->under.height;
        for (size_t i = 0; i < p->reduction.size(); i++)
        {
            auto &t = p->reduction[i];
            auto &program = p->change_p;
            program.use(wf::TEXTURE_TYPE_RGBA);
            program.uniformMatrix4f("MVP", glm::ortho(0.f, float(t.width), 0.f, float(t.height), -1.f, 1.f));
            program.uniform1i("uFirst", i == 0 ? 1 : 0);
            program.uniform2f("uInputSize", iw, ih);
            bind(program, "uUnder", 0, p->under.texture);
            bind(program, "uSeen", 1, p->seen.texture);
            bind(program, "uMask", 2, p->mask.texture);
            bind(program, "uReduce", 3, input);
            p->draw_to(program, t);
            input = t.texture;
            iw = t.width;
            ih = t.height;
        }
        p->copy_p.use(wf::TEXTURE_TYPE_RGBA);
        p->copy_p.uniformMatrix4f("MVP", glm::ortho(0.f, 1.f, 0.f, 1.f, -1.f, 1.f));
        if (!p->change_read.fb && !p->change_read.allocate(1, 1, true, p->es3))
        {
            p->end_auxiliary(1, measured);
            count = 255;
            return;
        }
        bind(p->copy_p, "image", 0, input);
        p->draw_to(p->copy_p, p->change_read);
        ++backdrop_checks;
        if (p->es3)
        {
            p->end_auxiliary(1, measured);
            p->check_fence = glFenceSync(GL_SYNC_GPU_COMMANDS_COMPLETE, 0);
            if (p->check_fence)
            {
                glFlush();
                count = -1;
                return;
            }
        }
        if (!p->es3 && p->create_egl_sync)
        {
            p->end_auxiliary(1, measured);
            measured = false;
            p->check_egl_fence = p->create_egl_sync(eglGetCurrentDisplay(), EGL_SYNC_FENCE_KHR, nullptr);
            if (p->check_egl_fence != EGL_NO_SYNC_KHR)
            {
                glFlush();
                count = -1;
                return;
            }
        }
        // Drivers without a fence facility retain the synchronous compatibility path;
        // benchmark wall time includes its completion wait. P8 must be measured there.
        unsigned char value[4];
        glReadPixels(0, 0, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, value);
        count = value[0];
        if (!p->es3) p->end_auxiliary(1, measured);
    });
    double elapsed = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - started).count();
    check_wall_ms += elapsed;
    check_wall_max_ms = std::max(check_wall_max_ms, elapsed);
    p->sampled_step = UINT64_MAX;
    return count;
}
void renderer_t::backdrop_seen()
{
    if (!p->ready || !p->under.fb)
        return;
    auto started = std::chrono::steady_clock::now();
    wf::gles::run_in_context_if_gles([&]
    {
        state_t guard;
        p->cancel_check();
        bool measured = p->begin_auxiliary(2, *this);
        glDisable(GL_SCISSOR_TEST);
        glDisable(GL_BLEND);
        under_pixels += p->flush_under();
        glBindFramebuffer(GL_FRAMEBUFFER, p->under.fb);
        glBindTexture(GL_TEXTURE_2D, p->seen.texture);
        glCopyTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, 0, 0, p->under.width, p->under.height);
        p->end_auxiliary(2, measured);
    });
    double elapsed = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - started).count();
    seen_wall_ms += elapsed;
    seen_wall_max_ms = std::max(seen_wall_max_ms, elapsed);
    ++seen_calls;
}
bool renderer_t::overlapping() const { return p->overlap; }
bool renderer_t::under_waiting() const { return !p->under_pending.empty(); }
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
