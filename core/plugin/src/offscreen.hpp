#pragma once
// Offscreen rendering without Wayfire 0.11's leak. Its render_target_t(const auxilliary_buffer_t&)
// creates a linear color transform, takes a second reference to it and releases only one, so every
// offscreen render leaked one (2026-10-05: a 47-hour session grew to 4 GB). Every offscreen render
// Scottland causes builds its target here instead, with the same linear encoding and otherwise the
// same steps as the Wayfire code it replaces, so the pixels are unchanged.
#include <wayfire/output.hpp>
#include <wayfire/region.hpp>
#include <wayfire/render.hpp>
#include <wayfire/scene-render.hpp>
#include <wayfire/view.hpp>
#include <wayfire/view-transform.hpp>
#include "loop.hpp"

namespace scottland
{
// The plugin's own reference to the one linear transform its targets share; each target takes and
// drops its own. Per plugin copy (statics are not shared between copies); released in fini().
inline wlr_color_transform*& aux_linear_transform()
{
    static wlr_color_transform *linear = nullptr;
    return linear;
}

inline void release_aux_color_transform()
{
    if (auto& linear = aux_linear_transform())
    {
        wlr_color_transform_unref(linear);
        linear = nullptr;
    }
}

/** A render target on an auxiliary buffer, encoded linearly as Wayfire's own are. */
inline wf::render_target_t aux_target(const wf::auxilliary_buffer_t& buffer)
{
    auto& linear = aux_linear_transform();
    if (!linear)
    {
        linear = wlr_color_transform_init_linear_to_inverse_eotf(WLR_COLOR_TRANSFER_FUNCTION_EXT_LINEAR);
    }

    wf::render_target_t target{buffer.get_renderbuffer()};
    target.set_color_transform(linear, WLR_COLOR_TRANSFER_FUNCTION_EXT_LINEAR);
    return target;
}

/** view_interface_t::take_snapshot, rendering through aux_target. */
inline void take_snapshot(wayfire_view view, wf::auxilliary_buffer_t& buffer)
{
    auto root_node = view->get_surface_root_node();
    const wf::geometry_t bbox = root_node->get_bounding_box();
    float scale = view->get_output()->handle->scale;
    buffer.allocate(wf::dimensions(bbox), scale,
        wf::buffer_allocation_hints_t{.hdr_linear = view->get_output() && view->get_output()->is_hdr()});

    auto target = aux_target(buffer);
    target.geometry = bbox;
    target.scale    = scale;

    std::vector<wf::scene::render_instance_uptr> instances;
    root_node->gen_render_instances(instances, [] (auto) {}, view->get_output());

    wf::render_pass_params_t params;
    params.background_color = {0, 0, 0, 0};
    params.damage    = bbox;
    params.target    = target;
    params.instances = &instances;
    params.flags     = wf::RPASS_CLEAR_BACKGROUND;
    wf::render_pass_t::run(params);
}

/** transformer_render_instance_t::get_texture, rendering through aux_target. */
inline std::shared_ptr<wf::texture_t> transformer_texture(wf::scene::transformer_base_node_t *self, float scale,
    std::vector<wf::scene::render_instance_uptr>& children, wf::output_t *output,
    wf::dimensionsf_t *out_logical_size = nullptr)
{
    if (auto tex = self->zero_copy_texture(out_logical_size))
    {
        self->release_buffers();
        return tex;
    }

    auto bbox = self->get_children_bounding_box();
    if (self->inner_content.allocate(wf::dimensions(bbox), scale,
        wf::buffer_allocation_hints_t{.hdr_linear = output && output->is_hdr()}) !=
        wf::buffer_reallocation_result_t::SAME)
    {
        self->cached_damage |= bbox;
    }

    auto target = aux_target(self->inner_content);
    target.scale    = scale;
    target.geometry = bbox;
    wf::render_pass_params_t params;
    params.instances = &children;
    params.target    = target;
    params.damage    = self->cached_damage & bbox;
    params.background_color = {0.0f, 0.0f, 0.0f, 0.0f};
    params.flags = wf::RPASS_CLEAR_BACKGROUND;
    wf::render_pass_t::run(params);
    self->cached_damage.clear();
    if (out_logical_size)
    {
        *out_logical_size = {self->inner_content.get_size().width / scale,
            self->inner_content.get_size().height / scale};
    }

    return wf::texture_t::from_aux(self->inner_content);
}

/** A view_2d transformer that renders exactly as Wayfire's own does (an offscreen copy of its children,
 *  drawn at its transformed bounding box on the output's pixel grid), through aux_target. Translation,
 *  scale and alpha only: Scottland never rotates the views it uses this for. */
class view_2d_t : public wf::scene::view_2d_transformer_t
{
  public:
    using view_2d_transformer_t::view_2d_transformer_t;

    class instance_t : public wf::scene::transformer_render_instance_t<view_2d_transformer_t>
    {
      public:
        using transformer_render_instance_t::transformer_render_instance_t;

        void transform_damage_region(wf::regionf_t& damage) override
        {
            SCOTTLAND_LOOP_SCOPE(offset_transform_damage);
            auto copy = damage;
            damage.clear();
            for (auto& box : copy)
            {
                damage |= wf::get_bbox_for_node(self, ::geometry_from_pixman_box(box));
            }
        }

        void render(const wf::scene::render_instruction_t& data) override
        {
            SCOTTLAND_LOOP_SCOPE(offset_render);
            auto tex = transformer_texture(self.get(), data.target.scale, children, _shown_on);
            tex->set_filter_mode(WLR_SCALE_FILTER_BILINEAR);
            data.pass->add_texture(tex, data.target, self->get_bounding_box(), data.damage, self->get_alpha());
        }
    };

    void gen_render_instances(std::vector<wf::scene::render_instance_uptr>& instances,
        wf::scene::damage_callback push_damage, wf::output_t *shown_on) override
    {
        SCOTTLAND_LOOP_SCOPE(offset_gen_render_instances);
        auto instance = std::make_unique<instance_t>(this, push_damage, shown_on);
        if (instance->has_instances())
        {
            instances.push_back(std::move(instance));
        }
    }
};
}
