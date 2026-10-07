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
    bool alpha = true;
    void release()
    {
        if (texture)
            glDeleteTextures(1, &texture);
        if (fb)
            glDeleteFramebuffers(1, &fb);
        texture = fb = 0;
        width = height = 0;
    }
    // `with_alpha` false makes a packed RGB texture: GLES copies from a framebuffer with no
    // alpha only into one (ES 3.2 §8.6), which strict drivers enforce.
    bool allocate(int w, int h, bool packed, bool es3, bool framebuffer = true, bool with_alpha = true)
    {
        release();
        width = w;
        height = h;
        alpha = with_alpha || !packed;
        GLenum format = alpha ? GL_RGBA : GL_RGB;
        glGenTextures(1, &texture);
        glBindTexture(GL_TEXTURE_2D, texture);
        glTexImage2D(GL_TEXTURE_2D, 0, packed || !es3 ? format : GL_RGBA16F, w, h, 0, format,
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
    GLint fb, read_fb = 0, read_buffer = GL_COLOR_ATTACHMENT0;
    GLint viewport[4], scissor_box[4], program, active, binding[8], blend_src, blend_dst;
    GLfloat clear_color[4];
    GLboolean scissor, blend;
    bool separate_read;
    explicit state_t(bool separate_read = false) : separate_read(separate_read)
    {
        glGetIntegerv(GL_FRAMEBUFFER_BINDING, &fb);
        if (separate_read)
        {
            glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING, &read_fb);
            glGetIntegerv(GL_READ_BUFFER, &read_buffer);
        }
        glGetIntegerv(GL_VIEWPORT, viewport);
        glGetIntegerv(GL_SCISSOR_BOX, scissor_box);
        glGetIntegerv(GL_CURRENT_PROGRAM, &program);
        glGetIntegerv(GL_ACTIVE_TEXTURE, &active);
        glGetIntegerv(GL_BLEND_SRC_RGB, &blend_src);
        glGetIntegerv(GL_BLEND_DST_RGB, &blend_dst);
        glGetFloatv(GL_COLOR_CLEAR_VALUE, clear_color);
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
        if (separate_read)
        {
            glBindFramebuffer(GL_READ_FRAMEBUFFER, read_fb);
            glReadBuffer(read_buffer);
        }
        glViewport(viewport[0], viewport[1], viewport[2], viewport[3]);
        glScissor(scissor_box[0], scissor_box[1], scissor_box[2], scissor_box[3]);
        glUseProgram(program);
        glBlendFunc(blend_src, blend_dst);
        glClearColor(clear_color[0], clear_color[1], clear_color[2], clear_color[3]);
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
