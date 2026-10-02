#pragma once
#include <cstring>
#include <glm/gtc/matrix_transform.hpp>
#include <wayfire/opengl.hpp>

namespace scottland::goo::gl
{
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
// All calls occur inside Wayfire's GLES subpass, or run_in_context_if_gles for
// input queries. Restore state also on allocation/compile failures; do not
// leave a simulation FB bound.
struct state_t
{
    GLint fb, viewport[4], scissor_box[4], program, active, binding[8], blend_src, blend_dst;
    GLboolean scissor, blend;
    GLint read_fb = 0;
    bool es3 = false;
    state_t()
    {
        const char *version = (const char *)glGetString(GL_VERSION);
        es3 = version && (strstr(version, "OpenGL ES 3") || strstr(version, "OpenGL ES 4"));
        glGetIntegerv(GL_FRAMEBUFFER_BINDING, &fb);
        if (es3)
            glGetIntegerv(0x8CAA /* READ_FRAMEBUFFER_BINDING */, &read_fb);
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
        if (es3)
            glBindFramebuffer(0x8CA8 /* READ_FRAMEBUFFER */, read_fb);
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
inline void bind(OpenGL::program_t &program, const char *name, int unit, GLuint tex)
{
    glActiveTexture(GL_TEXTURE0 + unit);
    glBindTexture(GL_TEXTURE_2D, tex);
    glUniform1i(glGetUniformLocation(program.get_program_id(wf::TEXTURE_TYPE_RGBA), name), unit);
}
inline void quad(OpenGL::program_t &program, float w, float h)
{
    GLfloat vertices[] = {0, 0, w, 0, w, h, 0, h};
    program.attrib_pointer("position", 2, 0, vertices);
    glDrawArrays(GL_TRIANGLE_FAN, 0, 4);
    program.deactivate();
}
} // namespace scottland::goo::gl
