// GO28: the backdrop change count through the renderer's actual change_shader reduction chain and
// copy_shader, sized and formatted as goo-renderer.cpp allocates them (grid (w+3)/4, levels halved
// with (n+1)/2 until 1x1; under/seen/mask RGBA8, reduction RGBA8 packed or RGBA16F on GLES 3).
// The fixture knows how many texels it changed; the count must equal it exactly, wherever they
// are, including grids that halve through odd sizes. Pickup wakes above 16 (goo.cpp).
#include <EGL/egl.h>
#include <GLES3/gl3.h>
#include <cstdio>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
#include "goo-shaders.hpp"

static GLuint shader(GLenum kind, const std::string &source)
{
    GLuint result = glCreateShader(kind);
    const char *text = source.c_str();
    glShaderSource(result, 1, &text, nullptr);
    glCompileShader(result);
    GLint ok = 0; glGetShaderiv(result, GL_COMPILE_STATUS, &ok);
    if (!ok) { char log[8192]; glGetShaderInfoLog(result, sizeof(log), nullptr, log); throw std::runtime_error(log); }
    return result;
}
static GLuint program(const std::string *fragment, bool es3)
{
    scottland::goo::program_variant v{"test", fragment, ""};
    GLuint p = glCreateProgram();
    glAttachShader(p, shader(GL_VERTEX_SHADER, scottland::goo::vertex_source(es3)));
    glAttachShader(p, shader(GL_FRAGMENT_SHADER, scottland::goo::fragment_source(v, es3)));
    glLinkProgram(p);
    GLint ok = 0; glGetProgramiv(p, GL_LINK_STATUS, &ok);
    if (!ok) throw std::runtime_error("link failed");
    return p;
}
struct target { GLuint tex = 0, fb = 0; int w = 0, h = 0; };
static target make(int w, int h, bool packed, bool es3, const void *data = nullptr)
{
    target t; t.w = w; t.h = h;
    glGenTextures(1, &t.tex); glBindTexture(GL_TEXTURE_2D, t.tex);
    glTexImage2D(GL_TEXTURE_2D, 0, packed || !es3 ? GL_RGBA : GL_RGBA16F, w, h, 0, GL_RGBA,
                 packed ? GL_UNSIGNED_BYTE : GL_HALF_FLOAT, data);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glGenFramebuffers(1, &t.fb); glBindFramebuffer(GL_FRAMEBUFFER, t.fb);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, t.tex, 0);
    if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) throw std::runtime_error("framebuffer incomplete");
    return t;
}
static void release(target &t) { glDeleteTextures(1, &t.tex); glDeleteFramebuffers(1, &t.fb); }
static void ortho(GLuint p, float w, float h)
{
    float m[] = {2 / w, 0, 0, 0, 0, 2 / h, 0, 0, 0, 0, -1, 0, -1, -1, 0, 1};
    glUniformMatrix4fv(glGetUniformLocation(p, "MVP"), 1, GL_FALSE, m);
}
static void quad(GLuint p, float w, float h)
{
    float v[] = {0, 0, w, 0, 0, h, w, h};
    GLint pos = glGetAttribLocation(p, "position");
    glBindBuffer(GL_ARRAY_BUFFER, 0);
    glEnableVertexAttribArray(pos); glVertexAttribPointer(pos, 2, GL_FLOAT, GL_FALSE, 0, v);
    glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
}
// The count the renderer reads back for one under/seen pair.
static int count(GLuint change, GLuint copy, int W, int H, const std::vector<std::pair<int, int>> &changed, bool packed, bool es3)
{
    std::vector<unsigned char> under(size_t(W) * H * 4), mask(size_t(W) * H * 4, 255);
    for (size_t i = 0; i < under.size(); i += 4) { under[i] = 100; under[i + 1] = 120; under[i + 2] = 140; under[i + 3] = 255; }
    auto seen = under;
    for (auto [x, y] : changed) under[(size_t(y) * W + x) * 4] = 200;
    target tu = make(W, H, true, es3, under.data()), ts = make(W, H, true, es3, seen.data()),
           tm = make(W, H, true, es3, mask.data());
    std::vector<target> reduction;
    int rw = W, rh = H;
    do { rw = (rw + 1) / 2; rh = (rh + 1) / 2; reduction.push_back(make(rw, rh, packed, es3)); } while (rw > 1 || rh > 1);
    GLuint input = 0; int iw = W, ih = H;
    glUseProgram(change);
    for (size_t i = 0; i < reduction.size(); ++i)
    {
        auto &t = reduction[i];
        glBindFramebuffer(GL_FRAMEBUFFER, t.fb); glViewport(0, 0, t.w, t.h);
        ortho(change, t.w, t.h);
        glUniform1i(glGetUniformLocation(change, "uFirst"), i == 0);
        glUniform2f(glGetUniformLocation(change, "uInputSize"), iw, ih);
        GLuint tex[] = {tu.tex, ts.tex, tm.tex, input};
        const char *names[] = {"uUnder", "uSeen", "uMask", "uReduce"};
        for (int k = 0; k < 4; ++k)
        {
            glActiveTexture(GL_TEXTURE0 + k); glBindTexture(GL_TEXTURE_2D, tex[k]);
            glUniform1i(glGetUniformLocation(change, names[k]), k);
        }
        quad(change, t.w, t.h);
        input = t.tex; iw = t.w; ih = t.h;
    }
    target read = make(1, 1, true, es3);
    glUseProgram(copy); glBindFramebuffer(GL_FRAMEBUFFER, read.fb); glViewport(0, 0, 1, 1);
    ortho(copy, 1, 1);
    glActiveTexture(GL_TEXTURE0); glBindTexture(GL_TEXTURE_2D, input);
    glUniform1i(glGetUniformLocation(copy, "image"), 0);
    quad(copy, 1, 1);
    unsigned char px[4]; glReadPixels(0, 0, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, px);
    for (auto &t : reduction) release(t);
    for (auto *t : {&tu, &ts, &tm, &read}) release(*t);
    return px[0];
}
int main(int argc, char **argv)
{
    bool es3 = argc > 1 && std::string(argv[1]) == "3";
    bool packed = !es3;  // the renderer packs on GLES 2 only
    EGLDisplay display = eglGetDisplay(EGL_DEFAULT_DISPLAY);
    if (!eglInitialize(display, nullptr, nullptr)) return 2;
    eglBindAPI(EGL_OPENGL_ES_API);
    EGLint attributes[] = {EGL_SURFACE_TYPE, EGL_PBUFFER_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT,
        EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8, EGL_NONE};
    EGLConfig config; EGLint found = 0;
    if (!eglChooseConfig(display, attributes, &config, 1, &found) || !found) { eglTerminate(display); return 2; }
    EGLint context_attributes[] = {EGL_CONTEXT_CLIENT_VERSION, es3 ? 3 : 2, EGL_NONE};
    EGLint surface_attributes[] = {EGL_WIDTH, 1, EGL_HEIGHT, 1, EGL_NONE};
    EGLContext context = eglCreateContext(display, config, EGL_NO_CONTEXT, context_attributes);
    EGLSurface surface = eglCreatePbufferSurface(display, config, surface_attributes);
    if (!eglMakeCurrent(display, surface, surface, context)) { eglTerminate(display); return 2; }
    int failures = 0, checks = 0;
    try
    {
        std::cout << "renderer=" << glGetString(GL_RENDERER) << " dialect=" << (es3 ? 3 : 2)
                  << (packed ? " packed" : " half-float") << '\n';
        GLuint change = program(&scottland::goo::change_shader, es3), copy = program(&scottland::goo::copy_shader, es3);
        // Outputs whose grids halve through odd sizes in either axis, and one that stays even longer.
        struct output_t { int w, h; } outputs[] = {{1280, 720}, {1920, 1080}, {2560, 1600}, {1366, 768}, {1001, 601}};
        for (auto o : outputs)
        {
            int W = (o.w + 3) / 4, H = (o.h + 3) / 4;
            auto row = [&] (int n, int y) { std::vector<std::pair<int, int>> r; for (int i = 0; i < n; ++i) r.push_back({W / 4 + i, y}); return r; };
            struct scenario_t { std::string name; std::vector<std::pair<int, int>> changed; };
            std::vector<scenario_t> scenarios = {
                {"one texel, top-left corner", {{0, 0}}},
                {"one texel, bottom-right corner", {{W - 1, H - 1}}},
                {"one texel, bottom row middle", {{W / 2, H - 1}}},
                {"one texel, right column middle", {{W - 1, H / 2}}},
                {"one texel, center", {{W / 2, H / 2}}},
                {"16 texels, bottom row (tolerated)", row(16, H - 1)},
                {"16 texels, center row (tolerated)", row(16, H / 2)},
                {"17 texels, bottom row (wakes pickup)", row(17, H - 1)},
                {"17 texels, center row (wakes pickup)", row(17, H / 2)},
            };
            for (auto &s : scenarios)
            {
                int expected = int(s.changed.size()), got = count(change, copy, W, H, s.changed, packed, es3);
                bool ok = got == expected && (got > 16) == (expected > 16);
                ++checks; failures += !ok;
                std::printf("%s output %dx%d grid %dx%d %-38s expected=%2d counted=%3d\n", ok ? "PASS" : "FAIL",
                            o.w, o.h, W, H, s.name.c_str(), expected, got);
            }
        }
        glDeleteProgram(change); glDeleteProgram(copy);
    }
    catch (const std::exception &error) { std::cerr << error.what() << '\n'; ++failures; }
    std::printf("%d passed, %d failed\n", checks - failures, failures);
    eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
    eglDestroySurface(display, surface); eglDestroyContext(display, context); eglTerminate(display);
    return failures ? 1 : 0;
}
