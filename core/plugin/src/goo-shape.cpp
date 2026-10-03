#include "goo-shape.hpp"
#include "goo-gl.hpp"
#include "goo-shaders.hpp"
#include <chrono>

namespace scottland::goo
{
namespace
{
// RGBA8 throughout, including on GPUs which cannot run the goo simulation.
// Seed coordinates have 1/16-texel precision. 65535 denotes no boundary seed.
const std::string prefix = R"(
precision highp float;
uniform sampler2D image, alpha;
uniform vec2 size;
vec4 pack(vec2 p) {
    vec2 n=floor(p*16.+.5);
    return vec4(mod(n.x,256.),floor(n.x/256.),mod(n.y,256.),floor(n.y/256.))/255.;
}
vec2 unpack(vec4 c) { return vec2(c.r+c.g*256.,c.b+c.a*256.)*255./16.; }
float opacity(vec2 p) { return texture2D(alpha,p/size).a; }
)";
const std::string seed = prefix + R"(
void main() {
    vec2 p=gl_FragCoord.xy,best=vec2(4095.9375);float d=1e9,a=opacity(p);
    for(int k=0;k<4;k++) {
        vec2 off=k==0?vec2(1,0):k==1?vec2(-1,0):k==2?vec2(0,1):vec2(0,-1);
        float b=opacity(p+off);
        if((a>=.5)!=(b>=.5)) {
            vec2 edge=p+off*clamp((.5-a)/(b-a),0.,1.);
            float e=distance(edge,p);if(e<d){d=e;best=edge;}
        }
    }
    gl_FragColor=pack(best);
})";
const std::string jump = prefix + R"(
uniform float stride;
void main() {
    vec2 p=gl_FragCoord.xy,best=vec2(4095.9375);float d=1e9;
    for(int y=-1;y<=1;y++)for(int x=-1;x<=1;x++) {
        vec2 q=p+vec2(float(x),float(y))*stride;
        if(any(lessThan(q,vec2(.5)))||any(greaterThan(q,size-.5)))continue;
        vec2 edge=unpack(texture2D(image,q/size));
        if(edge.x>4095.)continue;
        float e=dot(edge-p,edge-p);if(e<d){d=e;best=edge;}
    }
    gl_FragColor=pack(best);
})";
const std::string finish = prefix + R"(
void main() {
    vec2 p=gl_FragCoord.xy,edge=unpack(texture2D(image,p/size));
    float d=min(distance(p,edge),2047.);
    if(opacity(p)>=.5)d=-d;
    float n=floor(d*16.+32768.+.5);
    gl_FragColor=vec4(mod(n,256.),floor(n/256.),0.,1.)/vec4(255.,255.,1.,1.);
})";
struct programs_t
{
    OpenGL::program_t seed, jump, finish;
    bool ready = false;
};
programs_t &programs()
{
    static programs_t p;
    return p;
}
} // namespace
struct shape_cache_t::impl
{
    wf::auxilliary_buffer_t capture;
    gl::target_t mask, seeds[2], sdf;
    std::vector<uint8_t> alpha;
    void draw(OpenGL::program_t &program, gl::target_t &target, GLuint input)
    {
        glDisable(GL_BLEND);
        glDisable(GL_SCISSOR_TEST);
        glBindFramebuffer(GL_FRAMEBUFFER, target.fb);
        glViewport(0, 0, target.width, target.height);
        program.use(wf::TEXTURE_TYPE_RGBA);
        program.uniformMatrix4f("MVP",
                                glm::ortho(0.f, float(target.width), 0.f, float(target.height), -1.f, 1.f));
        program.uniform2f("size", target.width, target.height);
        gl::bind(program, "image", 0, input);
        gl::bind(program, "alpha", 1, mask.texture);
        gl::quad(program, target.width, target.height);
    }
};
void shape_cache_t::prepare()
{
    wf::gles::run_in_context_if_gles(
        []
        {
            auto &p = programs();
            if (p.ready)
                return;
            gl::state_t guard;
            p.seed.compile("#version 100\n" + vertex, "#version 100\n" + seed);
            p.jump.compile("#version 100\n" + vertex, "#version 100\n" + jump);
            p.finish.compile("#version 100\n" + vertex, "#version 100\n" + finish);
            for (auto program : {&p.seed, &p.jump, &p.finish})
            {
                GLint linked = 0;
                glGetProgramiv(program->get_program_id(wf::TEXTURE_TYPE_RGBA), GL_LINK_STATUS, &linked);
                if (!linked)
                    return;
            }
            p.ready = true;
        });
}
void shape_cache_t::release_programs()
{
    wf::gles::run_in_context_if_gles(
        []
        {
            gl::state_t guard;
            auto &p = programs();
            for (auto program : {&p.seed, &p.jump, &p.finish})
                program->free_resources();
            p.ready = false;
        });
}
shape_cache_t::shape_cache_t() : p(std::make_unique<impl>()) {}
shape_cache_t::~shape_cache_t()
{
    wf::gles::run_in_context_if_gles(
        [&]
        {
            gl::state_t guard;
            for (auto t : {&p->mask, &p->seeds[0], &p->seeds[1], &p->sdf})
                t->release();
        });
}
GLuint shape_cache_t::texture() const { return p->sdf.texture; }
bool shape_cache_t::update(glm::vec4 bounds, const std::function<void(const wf::render_target_t &)> &draw)
{
    auto start = std::chrono::steady_clock::now();
    gl::state_t guard;
    // Half-resolution like the field, bounded for unusually large custom widgets.
    int cw = std::clamp(int(std::ceil(bounds.z / 2)), 1, 512),
        ch = std::clamp(int(std::ceil(bounds.w / 2)), 1, 512);
    int w = cw + 2 * shape_t::padding, h = ch + 2 * shape_t::padding;
    if (p->capture.allocate({w, h}, 1) == wf::buffer_reallocation_result_t::FAILED)
        return false;
    wf::render_target_t target{p->capture};
    float sx = bounds.z / cw, sy = bounds.w / ch;
    target.geometry = {bounds.x - shape_t::padding * sx, bounds.y - shape_t::padding * sy, w * sx, h * sy};
    target.scale = 1 / std::max(sx, sy);
    wf::gles::bind_render_buffer(target);
    glDisable(GL_SCISSOR_TEST);
    glClearColor(0, 0, 0, 0);
    glClear(GL_COLOR_BUFFER_BIT);
    draw(target);
    // Read only this small alpha image; RGB never enters the shape comparison.
    wf::gles::bind_render_buffer(target);
    glDisable(GL_SCISSOR_TEST);
    glBindFramebuffer(GL_FRAMEBUFFER, wf::gles::ensure_render_buffer_fb_id(target));
    std::vector<uint8_t> rgba(w * h * 4), alpha(w * h);
    glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE, rgba.data());
    auto texture = wf::gles_texture_t::from_aux(p->capture);
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++)
            alpha[y * w + x] = (rgba[4 * ((texture.invert_y ? y : h - 1 - y) * w + x) + 3] + 8) / 17;
    checks++;
    // The distance field is built from the 50% contour, placed within a texel by the two
    // alphas that straddle it. Alpha changing anywhere else (a card's text over its own
    // translucent body) leaves the shape, and so the goo around it, exactly as it was.
    auto same_contour = [&]
    {
        if (p->alpha.size() != alpha.size())
            return false;
        for (int y = 0; y < h; y++)
            for (int x = 0; x < w; x++)
            {
                int i = y * w + x;
                bool inside = alpha[i] >= 8;
                if (inside != (p->alpha[i] >= 8))
                    return false;
                if (alpha[i] == p->alpha[i])
                    continue;
                if ((x > 0 && (alpha[i - 1] >= 8) != inside) || (x + 1 < w && (alpha[i + 1] >= 8) != inside) ||
                    (y > 0 && (alpha[i - w] >= 8) != inside) || (y + 1 < h && (alpha[i + w] >= 8) != inside))
                    return false;
            }
        return true;
    };
    if (shape && shape->width == w && shape->height == h && same_contour())
    {
        check_ms =
            std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
        return false;
    }
    if (!programs().ready)
        return false;
    if (p->mask.width != w || p->mask.height != h)
        for (auto t : {&p->mask, &p->seeds[0], &p->seeds[1], &p->sdf})
        {
            if (!t->allocate(w, h, true, false))
                return false;
            glBindTexture(GL_TEXTURE_2D, t->texture);
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
        }
    for (size_t i = 0; i < alpha.size(); i++)
    {
        rgba[4 * i] = rgba[4 * i + 1] = rgba[4 * i + 2] = 0;
        rgba[4 * i + 3] = alpha[i] * 17;
    }
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, p->mask.texture);
    glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE, rgba.data());
    p->draw(programs().seed, p->seeds[0], 0);
    int stride = 1;
    while (stride < std::max(w, h))
        stride *= 2;
    for (stride /= 2; stride >= 1; stride /= 2)
    {
        programs().jump.use(wf::TEXTURE_TYPE_RGBA);
        programs().jump.uniform1f("stride", stride);
        p->draw(programs().jump, p->seeds[1], p->seeds[0].texture);
        std::swap(p->seeds[0], p->seeds[1]);
    }
    // JFA+1 corrects the near-boundary approximation before final distances.
    programs().jump.use(wf::TEXTURE_TYPE_RGBA);
    programs().jump.uniform1f("stride", 1);
    p->draw(programs().jump, p->seeds[1], p->seeds[0].texture);
    p->draw(programs().finish, p->sdf, p->seeds[1].texture);
    auto result = std::make_shared<shape_t>();
    int left = w, top = h, right = 0, bottom = 0;
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++)
            if (alpha[y * w + x] >= 8)
            {
                left = std::min(left, x);
                top = std::min(top, y);
                right = std::max(right, x + 1);
                bottom = std::max(bottom, y + 1);
            }
    if (left >= right || top >= bottom)
    {
        left = top = 0;
        right = w;
        bottom = h;
    }
    result->bounds = {left, top, right, bottom};
    float closest = w;
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++)
            if (alpha[y * w + x] >= 8)
            {
                float dx = std::abs(x + .5f - (left + right) / 2.f);
                if (dx <= closest)
                {
                    closest = dx;
                    result->bottom_hint = {x + .5f, y + .5f};
                }
            }
    result->logical_size = {bounds.z, bounds.w};
    result->width = w;
    result->height = h;
    result->revision = ++builds;
    result->pixels.resize(w * h * 4);
    glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE, result->pixels.data());
    glBindTexture(GL_TEXTURE_2D, p->sdf.texture);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    p->alpha = std::move(alpha);
    shape = std::move(result);
    rebuild_ms = check_ms =
        std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
    return true;
}
} // namespace scottland::goo
