// Pixel-level fractional-texel transport through the renderer's actual query shader.
// Expected colors are calculated independently from K = (1-t)*0 + t*6.
#include <EGL/egl.h>
#include <GLES2/gl2.h>
#include <cmath>
#include <iostream>
#include <stdexcept>
#include "goo-shaders.hpp"

GLuint shader(GLenum kind, const std::string &source)
{
    GLuint result = glCreateShader(kind);
    const char *text = source.c_str();
    glShaderSource(result, 1, &text, nullptr);
    glCompileShader(result);
    GLint ok = 0; glGetShaderiv(result, GL_COMPILE_STATUS, &ok);
    if (!ok) { char log[8192]; glGetShaderInfoLog(result, sizeof(log), nullptr, log); throw std::runtime_error(log); }
    return result;
}
int main(int argc, char **argv)
{
    bool es3 = argc > 1 && std::string(argv[1]) == "3";
    EGLDisplay display = eglGetDisplay(EGL_DEFAULT_DISPLAY);
    if (!eglInitialize(display, nullptr, nullptr)) return 2;
    eglBindAPI(EGL_OPENGL_ES_API);
    EGLint attributes[] = {EGL_SURFACE_TYPE, EGL_PBUFFER_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT,
        EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8, EGL_NONE};
    EGLConfig config; EGLint count = 0;
    if (!eglChooseConfig(display, attributes, &config, 1, &count) || !count) { eglTerminate(display); return 2; }
    EGLint context_attributes[] = {EGL_CONTEXT_CLIENT_VERSION, es3 ? 3 : 2, EGL_NONE};
    EGLint surface_attributes[] = {EGL_WIDTH, 1, EGL_HEIGHT, 1, EGL_NONE};
    EGLContext context = eglCreateContext(display, config, EGL_NO_CONTEXT, context_attributes);
    EGLSurface surface = eglCreatePbufferSurface(display, config, surface_attributes);
    if (!eglMakeCurrent(display, surface, surface, context)) { eglTerminate(display); return 2; }
    int failures = 0;
    try
    {
        GLuint program = glCreateProgram();
        GLuint vert = shader(GL_VERTEX_SHADER, scottland::goo::vertex_source(es3));
        scottland::goo::program_variant query{"query", &scottland::goo::query_shader, ""};
        GLuint frag = shader(GL_FRAGMENT_SHADER, scottland::goo::fragment_source(query, es3));
        glAttachShader(program, vert); glAttachShader(program, frag); glLinkProgram(program);
        GLint ok = 0; glGetProgramiv(program, GL_LINK_STATUS, &ok);
        if (!ok) throw std::runtime_error("query shader link failed");
        glUseProgram(program);
        unsigned char colors[] = {0, 0, 0, 255, 255, 255, 255, 255};
        GLuint texture = 0; glGenTextures(1, &texture); glBindTexture(GL_TEXTURE_2D, texture);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, 2, 1, 0, GL_RGBA, GL_UNSIGNED_BYTE, colors);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
        glUniform1i(glGetUniformLocation(program, "uDyeTex"), 0);
        glUniform1i(glGetUniformLocation(program, "uWave"), 0);
        glUniform2f(glGetUniformLocation(program, "uRes"), 2, 1);
        glUniform2f(glGetUniformLocation(program, "uDyeSize"), 2, 1);
        float identity[] = {1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1};
        glUniformMatrix4fv(glGetUniformLocation(program, "MVP"), 1, GL_FALSE, identity);
        float vertices[] = {-1,-1, 1,-1, -1,1, 1,1};
        GLuint buffer = 0; glGenBuffers(1, &buffer); glBindBuffer(GL_ARRAY_BUFFER, buffer);
        glBufferData(GL_ARRAY_BUFFER, sizeof(vertices), vertices, GL_STATIC_DRAW);
        GLint position = glGetAttribLocation(program, "position");
        glEnableVertexAttribArray(position); glVertexAttribPointer(position, 2, GL_FLOAT, GL_FALSE, 0, nullptr);
        glViewport(0, 0, 1, 1);
        std::cout << "renderer=" << glGetString(GL_RENDERER) << " dialect=" << (es3 ? 3 : 2) << '\n';
        for (float t : {0.f, .25f, .5f, .75f, 1.f})
        {
            glUniform2f(glGetUniformLocation(program, "uPoint"), .5f + t, .5f);
            glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
            unsigned char pixel[4]; glReadPixels(0, 0, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, pixel);
            int expected = std::lround(255 * std::exp(-6. * t));
            bool matches = true;
            for (int channel = 0; channel < 3; ++channel) matches &= std::abs(int(pixel[channel]) - expected) <= 1;
            failures += !matches;
            std::cout << (matches ? "PASS " : "FAIL ") << "fraction " << t << " pixel=" << int(pixel[0]) << " expected=" << expected << '\n';
        }
        glDeleteBuffers(1, &buffer); glDeleteTextures(1, &texture); glDeleteProgram(program); glDeleteShader(vert); glDeleteShader(frag);
    }
    catch (const std::exception &error) { std::cerr << error.what() << '\n'; ++failures; }
    eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
    eglDestroySurface(display, surface); eglDestroyContext(display, context); eglTerminate(display);
    return failures ? 1 : 0;
}
