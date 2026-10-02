#include "goo-renderer.hpp"
#include "goo-shaders.hpp"
#include <chrono>
#include <cstring>
#include <wayfire/scene-render.hpp>
#include <wayfire/util/log.hpp>

namespace scottland::goo
{
namespace
{
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
struct target_t
{
    GLuint texture = 0, fb = 0;
    int width = 0, height = 0;
    void release()
    {
        if (texture)
            glDeleteTextures(1, &texture);
        if (fb)
            glDeleteFramebuffers(1, &fb);
        texture = fb = 0;
        width = height = 0;
    }
    bool allocate(int w, int h, bool packed, bool es3, bool framebuffer = true)
    {
        release();
        width = w;
        height = h;
        glGenTextures(1, &texture);
        glBindTexture(GL_TEXTURE_2D, texture);
        glTexImage2D(GL_TEXTURE_2D, 0, packed || !es3 ? GL_RGBA : GL_RGBA16F, w, h, 0, GL_RGBA,
                     packed ? GL_UNSIGNED_BYTE
                     : es3  ? GL_HALF_FLOAT
                            : 0x8D61 /* HALF_FLOAT_OES */,
                     nullptr);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
        if (!framebuffer)
            return true;
        glGenFramebuffers(1, &fb);
        glBindFramebuffer(GL_FRAMEBUFFER, fb);
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, texture, 0);
        return glCheckFramebufferStatus(GL_FRAMEBUFFER) == GL_FRAMEBUFFER_COMPLETE;
    }
};
// All calls occur inside Wayfire's GLES subpass, or run_in_context_if_gles for input queries.
// Restore state also on allocation/compile failures; do not leave a simulation FB bound.
struct state_t
{
    GLint fb, viewport[4], scissor_box[4], program, active, binding[8], blend_src, blend_dst;
    GLboolean scissor, blend;
    state_t()
    {
        glGetIntegerv(GL_FRAMEBUFFER_BINDING, &fb);
        glGetIntegerv(GL_VIEWPORT, viewport);
        glGetIntegerv(GL_SCISSOR_BOX, scissor_box);
        glGetIntegerv(GL_CURRENT_PROGRAM, &program);
        glGetIntegerv(GL_ACTIVE_TEXTURE, &active);
        glGetIntegerv(GL_BLEND_SRC_RGB, &blend_src);
        glGetIntegerv(GL_BLEND_DST_RGB, &blend_dst);
        scissor = glIsEnabled(GL_SCISSOR_TEST);
        blend = glIsEnabled(GL_BLEND);
        for (int i = 0; i < 8; i++)
        {
            glActiveTexture(GL_TEXTURE0 + i);
            glGetIntegerv(GL_TEXTURE_BINDING_2D, &binding[i]);
        }
        glActiveTexture(GL_TEXTURE0);
    }
    ~state_t()
    {
        glBindFramebuffer(GL_FRAMEBUFFER, fb);
        glViewport(viewport[0], viewport[1], viewport[2], viewport[3]);
        glScissor(scissor_box[0], scissor_box[1], scissor_box[2], scissor_box[3]);
        glUseProgram(program);
        glBlendFunc(blend_src, blend_dst);
        if (scissor)
            glEnable(GL_SCISSOR_TEST);
        else
            glDisable(GL_SCISSOR_TEST);
        if (blend)
            glEnable(GL_BLEND);
        else
            glDisable(GL_BLEND);
        for (int i = 0; i < 8; i++)
        {
            glActiveTexture(GL_TEXTURE0 + i);
            glBindTexture(GL_TEXTURE_2D, binding[i]);
        }
        glActiveTexture(active);
    }
};
void bind(OpenGL::program_t &program, const char *name, int unit, GLuint tex)
{
    glActiveTexture(GL_TEXTURE0 + unit);
    glBindTexture(GL_TEXTURE_2D, tex);
    glUniform1i(glGetUniformLocation(program.get_program_id(wf::TEXTURE_TYPE_RGBA), name), unit);
}
void quad(OpenGL::program_t &program, float w, float h)
{
    GLfloat vertices[] = {0, 0, w, 0, w, h, 0, h};
    program.attrib_pointer("position", 2, 0, vertices);
    glDrawArrays(GL_TRIANGLE_FAN, 0, 4);
    program.deactivate();
}
} // namespace
struct renderer_t::impl
{
    bool checked = false, available = false, es3 = false, packed = false, ready = false;
    int width = 0, height = 0;
    GLuint timer = 0;
    bool timing = false, timer_pending = false, timer_open = false;
    float time = 0;
    uint64_t sampled_step = UINT64_MAX;
    glm::vec2 sampled_point{};
    glm::vec4 sampled_value{};
    settings_t settings;
    bool overlap = false, controls = false, fast = true;
    std::vector<source_t> sources;
    OpenGL::program_t field_p, mask_p, wave_p, dye_p, render_p, energy_p, query_p, copy_p;
    OpenGL::program_t field_fast, mask_fast, wave_fast, dye_fast, render_fast;
    target_t field, mask, wave[2], dye[2], source, curve, background, query;
    std::vector<target_t> reduction;

    void release()
    {
        if (timer)
            glDeleteQueries(1, &timer);
        for (auto p : {&field_p, &mask_p, &wave_p, &dye_p, &render_p, &energy_p, &query_p, &copy_p,
                       &field_fast, &mask_fast, &wave_fast, &dye_fast, &render_fast})
            p->free_resources();
        for (auto p : {&field, &mask, &wave[0], &wave[1], &dye[0], &dye[1], &source, &curve, &background, &query})
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
        LOGI("scottland goo: ", version ? version : "no GL context", ", float textures and derivatives ", available);
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
        LOGI("scottland goo: ", packed ? "packed RGBA8" : "RGBA16F", " simulation targets");
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
        compile(copy_p, vertex,
                "precision highp float; uniform sampler2D image; void "
                "main(){gl_FragColor=texture2D(image,vec2(.5));}");
        if (!available)
            LOGE("scottland goo: shader unavailable; retaining halo");
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
    void upload()
    {
        std::vector<glm::vec4> data;
        for (auto &s : sources)
        {
            data.push_back(s.rect);
            data.push_back(s.liquid);
            data.push_back(glm::vec4{s.dye, s.light ? s.scale : -s.scale});
            data.push_back(s.corners);
            data.push_back(s.dot);
            data.push_back(glm::vec4{s.hinted ? (s.hint_circle ? std::min(s.scale, 1.f) : 1.f) : 0.f, s.control_extent,
                overlap_film_width(s, settings), s.hint_circle ? 1.f : 0.f});
            data.push_back(s.sides);
        }
        if (data.empty())
            data.resize(7);
        const std::array uploads{std::make_pair(&source, std::make_pair(7, std::max(1, int(sources.size())))),
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
    }
    float measure()
    {
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
        // Read via an RGBA8 target (GLES 2 guarantees this read format).
        copy_p.use(wf::TEXTURE_TYPE_RGBA);
        copy_p.uniformMatrix4f("MVP", glm::ortho(0.f, 1.f, 0.f, 1.f, -1.f, 1.f));
        bind(copy_p, "image", 0, input);
        draw_to(copy_p, query);
        unsigned char value[4];
        glReadPixels(0, 0, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, value);
        return value[0] / 255.f;
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
                        const std::vector<glm::vec4> &impulses, const std::vector<wf::geometry_t> &area)
{
    state_t guard;
    if (!p->support())
        return false;
    if (p->timer_pending)
    {
        GLuint available = 0;
        glGetQueryObjectuiv(p->timer, GL_QUERY_RESULT_AVAILABLE, &available);
        if (available)
        {
            GLuint ns = 0;
            glGetQueryObjectuiv(p->timer, GL_QUERY_RESULT, &ns);
            GLint disjoint = 0;
            glGetIntegerv(0x8FBB /* GPU_DISJOINT_EXT */, &disjoint);
            if (!disjoint)
                last_gpu_ms = ns / 1e6;
            p->timer_pending = false;
        }
    }
    if (!p->es3 && sources.size() > 1024)
    {
        LOGE("scottland goo: too many sources for GLES 2; retaining halo");
        return false;
    }
    bool measure_gpu = p->timing && !p->timer_pending;
    auto start = std::chrono::steady_clock::now();
    glDisable(GL_SCISSOR_TEST);
    glDisable(GL_BLEND);
    if ((!p->ready || w != p->width || h != p->height) && !p->resize(w, h))
        return false;
    if (measure_gpu)
        glBeginQuery(0x88BF /* TIME_ELAPSED_EXT */, p->timer);
    p->sources = sources;
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
    p->time = time;
    p->upload();
    glBindFramebuffer(GL_FRAMEBUFFER, p->field.fb);
    glClearColor(0, 0, 0, 1);
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
        wave_program.uniform1f("uHintCircles", std::any_of(sources.begin(), sources.end(),
            [](const source_t &source) { return source.hint_circle; }) ? 1.f : 0.f);
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
    p->draw_to(dye_program, p->dye[1]);
    std::swap(p->dye[0], p->dye[1]);
    packed = p->packed;
    steps++;
    if (steps % 30 == 0)
        energy = p->measure();
    p->timer_open = measure_gpu;
    last_step_ms =
        std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
    return true;
}
void renderer_t::draw(const wf::scene::render_instruction_t &data, const wf::regionf_t &area)
{
    if (!p->ready)
        return;
    // Damage can arrive as one bounding box (the output collapses many small rects), so clip
    // the goo's work to its own bands rather than shading the whole box.
    auto damage = data.damage & area;
    state_t guard;
    wf::gles::bind_render_buffer(data.target);
    GLint viewport[4];
    glGetIntegerv(GL_VIEWPORT, viewport);
    // Real scene beneath the shared visible liquid, including overlapped window content.
    auto &bg = p->background;
    if (!bg.texture || bg.width != viewport[2] || bg.height != viewport[3])
        bg.allocate(viewport[2], viewport[3], true, p->es3, false);
    glBindTexture(GL_TEXTURE_2D, bg.texture);
    // Keep a backdrop cache: outside this pass's damage the framebuffer still contains
    // last frame's goo/windows. Copying all of it would feed those colors back into refraction.
    wf::gles::for_each_scissor_rect(data.target, data.damage,
                                    [&]
                                    {
                                        GLint box[4];
                                        glGetIntegerv(GL_SCISSOR_BOX, box);
                                        int x = std::max(box[0], viewport[0]),
                                            y = std::max(box[1], viewport[1]);
                                        int right = std::min(box[0] + box[2], viewport[0] + viewport[2]);
                                        int top = std::min(box[1] + box[3], viewport[1] + viewport[3]);
                                        if (right > x && top > y)
                                            glCopyTexSubImage2D(GL_TEXTURE_2D, 0, x - viewport[0],
                                                                y - viewport[1], x, y, right - x, top - y);
                                    });
    auto &program = p->fast ? p->render_fast : p->render_p;
    p->common(program, p->width, p->height);
    program.uniform2f("uFieldSize", p->field.width, p->field.height);
    auto ortho = wf::gles::render_target_orthographic_projection(data.target);
    program.uniformMatrix4f("MVP", ortho);
    program.uniformMatrix4f("uBackgroundMap", ortho);
    bind(program, "uBackground", 5, bg.texture);
    program.uniform1f("uWaveAmp", p->settings.wave_height);
    program.uniform1f("uShine", p->settings.shine);
    program.uniform1f("uRelief", p->settings.relief);
    program.uniform1f("uAlpha", 1);
    program.uniform1f("uHints", std::any_of(p->sources.begin(), p->sources.end(),
        [](const source_t &s) { return s.hinted; }));
    glEnable(GL_BLEND);
    glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);
    wf::gles::for_each_scissor_rect(
        data.target, damage,
        [&]
        {
            GLfloat vertices[] = {
                0, 0, float(p->width), 0, float(p->width), float(p->height), 0, float(p->height)};
            program.attrib_pointer("position", 2, 0, vertices);
            glDrawArrays(GL_TRIANGLE_FAN, 0, 4);
        });
    program.deactivate();
    if (p->timer_open)
    {
        glEndQuery(0x88BF);
        p->timer_open = false;
        p->timer_pending = true;
    }
}
glm::vec4 renderer_t::sample_at(glm::vec2 point)
{
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
bool renderer_t::highlighting() const { return p->controls; }
float renderer_t::wave_at(glm::vec2 point) { return sample_at(point).w; }
} // namespace scottland::goo
