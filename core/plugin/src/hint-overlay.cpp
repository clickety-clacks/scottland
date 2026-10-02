#include "hint-overlay.hpp"
#include <cairo.h>
#include <cmath>
#include <sstream>
#include <drm_fourcc.h>
extern "C" {
#include <wlr/render/wlr_texture.h>
}
namespace scottland::windowing
{
hint_node::hint_node() : node_t(false) {}
void hint_node::update(double x, double y, const std::string& text, bool selected,
    bool light, double red, double green, double blue, double scale)
{
    wf::scene::damage_node(this, box);
    box = {std::round(x - 40), std::round(y - 40), 80, 80};
    std::ostringstream key;
    key << text << selected << light << red << green << blue << scale;
    if (key.str() != appearance)
    {
        appearance = key.str(); texture.reset();
        pixel_size = int(std::ceil(80 * std::max(1.0, scale)));
        pixels.assign(pixel_size * pixel_size * 4, 0);
        auto surface = cairo_image_surface_create_for_data(pixels.data(), CAIRO_FORMAT_ARGB32,
            pixel_size, pixel_size, pixel_size * 4);
        auto cr = cairo_create(surface);
        cairo_scale(cr, pixel_size / 80.0, pixel_size / 80.0);
        cairo_arc(cr, 40, 40, 36, 0, 2 * 3.141592653589793);
        double neutral = light ? 0.98 : 0.08;
        cairo_set_source_rgba(cr, neutral, neutral, neutral, 0.94); cairo_fill_preserve(cr);
        cairo_set_source_rgb(cr, red, green, blue); cairo_set_line_width(cr, selected ? 5 : 2.5);
        cairo_stroke(cr);
        cairo_select_font_face(cr, "sans-serif", CAIRO_FONT_SLANT_NORMAL, CAIRO_FONT_WEIGHT_BOLD);
        cairo_set_font_size(cr, text.size() > 1 ? 32 : 46);
        cairo_text_extents_t ext; cairo_text_extents(cr, text.c_str(), &ext);
        cairo_move_to(cr, 40 - ext.width / 2 - ext.x_bearing, 40 - ext.height / 2 - ext.y_bearing);
        cairo_show_text(cr, text.c_str());
        cairo_destroy(cr); cairo_surface_flush(surface); cairo_surface_destroy(surface);
    }
    wf::scene::damage_node(this, box);
    wf::scene::update(shared_from_this(), wf::scene::update_flag::GEOMETRY);
}
class hint_render : public wf::scene::simple_render_instance_t<hint_node>
{
  public:
    using simple_render_instance_t::simple_render_instance_t;
    void render(const wf::scene::render_instruction_t& data) override
    {
        if (!self->texture && !self->pixels.empty())
        {
            auto tex = wlr_texture_from_pixels(data.pass->get_wlr_renderer(), DRM_FORMAT_ARGB8888,
                self->pixel_size * 4, self->pixel_size, self->pixel_size, self->pixels.data());
            if (tex) self->texture = wf::texture_t::from_texture(tex);
        }
        if (self->texture) data.pass->add_texture(self->texture, data.target, self->box, data.damage);
    }
};
void hint_node::gen_render_instances(std::vector<wf::scene::render_instance_uptr>& instances,
    wf::scene::damage_callback damage, wf::output_t *output)
{
    instances.push_back(std::make_unique<hint_render>(this, damage, output));
}
}
