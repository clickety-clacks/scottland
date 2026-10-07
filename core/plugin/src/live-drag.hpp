#pragma once

#include <wayfire/plugins/common/input-grab.hpp>
#include <wayfire/plugins/common/shared-core-data.hpp>
#include <wayfire/plugins/common/move-drag-interface.hpp>
#include <wayfire/output-layout.hpp>
#include <wayfire/view-helpers.hpp>
#include <wayfire/window-manager.hpp>

namespace scottland
{
// Keep the real scene subtree enabled and attached. Normal scene traversal owns
// visibility, damage and frame callbacks, including subsurfaces, without a second
// overlay render-instance manager or a renderer-disable lease.
class live_drag_transform_t : public wf::scene::transformer_base_node_t
{
  public:
    wf::pointf_t position, relative;
    // While a drag is suspended (a hold's offer, WK39), or a window is lifted for one, draw the
    // window exactly where it really is: the offset is just its output's origin in the layout.
    std::optional<wf::pointf_t> pinned;
    live_drag_transform_t() : transformer_base_node_t(false) {}
    std::string stringify() const override { return "scottland-live-drag"; }
    std::optional<wf::scene::input_node_t> find_node_at(const wf::pointf_t&) override { return {}; }
    wf::pointf_t offset()
    {
        if (pinned) return *pinned;
        auto b = get_children_bounding_box();
        return {position.x - b.x - relative.x * b.width,
            position.y - b.y - relative.y * b.height};
    }
    wf::pointf_t to_global(const wf::pointf_t& p) override { return p + offset(); }
    wf::pointf_t to_local(const wf::pointf_t& p) override { return p - offset(); }
    wf::geometry_t get_bounding_box() override { return get_children_bounding_box() + offset(); }
    class instance_t : public wf::scene::transformer_render_instance_t<live_drag_transform_t>
    {
      public:
        using transformer_render_instance_t::transformer_render_instance_t;
        void transform_damage_region(wf::regionf_t& damage) override
        {
            damage += self->offset();
        }
        void render(const wf::scene::render_instruction_t& data) override
        {
            data.pass->add_texture(get_texture(data.target.scale), data.target,
                self->get_bounding_box(), data.damage);
        }
    };
    void gen_render_instances(std::vector<wf::scene::render_instance_uptr>& instances,
        wf::scene::damage_callback damage, wf::output_t *output) override
    {
        instances.push_back(std::make_unique<instance_t>(this, damage, output));
    }
};

// Draws a window in front of everything, in layout coordinates, exactly where it really is, as a
// suspended drag does: an offset on its frame may then show it over another screen (a hold's pair
// offer joining a window from another screen, WK39). Nothing about the window changes. It goes
// back into its own place on destruction, drop(), unmap, or its output's removal.
class view_lift_t
{
  public:
    explicit view_lift_t(wayfire_toplevel_view target)
    {
        if (!target || !target->is_mapped() || !target->get_output()) return;
        auto node = target->get_transformed_node();
        auto inner = node->parent() ? std::dynamic_pointer_cast<wf::scene::floating_inner_node_t>(
            node->parent()->shared_from_this()) : nullptr;
        if (!inner) return;
        view = target;
        parent = inner;
        output = target->get_output();
        transform = std::make_shared<live_drag_transform_t>();
        transform->pinned = wf::origin(output->get_layout_geometry());
        view->damage();
        node->add_transformer(transform, wf::TRANSFORMER_HIGHLEVEL - 1, "scottland-offer-lift");
        wf::scene::readd_front(wf::get_core().scene(), node);
        view->connect(&unmap);
        wf::get_core().output_layout->connect(&removed);
        view->damage();
    }
    ~view_lift_t() { drop(); }
    view_lift_t(const view_lift_t&) = delete;
    view_lift_t& operator=(const view_lift_t&) = delete;
    bool lifted() const { return bool(view); }
    void drop()
    {
        if (!view) return;
        unmap.disconnect();
        removed.disconnect();
        auto node = view->get_transformed_node();
        view->damage();
        wf::scene::readd_front(parent, node);
        node->rem_transformer(transform);
        view->damage();
        view.reset();
        parent.reset();
        transform.reset();
        output = nullptr;
    }

  private:
    wayfire_toplevel_view view;
    std::shared_ptr<wf::scene::floating_inner_node_t> parent;
    std::shared_ptr<live_drag_transform_t> transform;
    wf::output_t *output = nullptr;
    wf::signal::connection_t<wf::view_unmapped_signal> unmap = [this] (auto *) { drop(); };
    wf::signal::connection_t<wf::output_removed_signal> removed =
        [this] (wf::output_removed_signal *ev) { if (ev->output == output) drop(); };
};

class live_drag_t : public wf::signal::provider_t, public wf::pointer_interaction_t,
    public wf::touch_interaction_t
{
    // Stock move remains available for client titlebar requests. Observe its signals
    // so those requests retain Scottland scaling/morph/drop behavior. Our bindings
    // and touch gestures never start this controller.
    wf::shared_data::ref_ptr_t<wf::move_drag::core_drag_t> legacy;
    wf::signal::connection_t<wf::move_drag::drag_focus_output_signal> legacy_output =
        [this] (auto *ev)
    {
        view = legacy->view;
        current_output = legacy->current_output;
        emit(ev);
    };
    wf::signal::connection_t<wf::move_drag::drag_motion_signal> legacy_motion =
        [this] (auto *ev) { emit(ev); };
    wf::signal::connection_t<wf::move_drag::drag_done_signal> legacy_done =
        [this] (auto *ev)
    {
        view = nullptr;
        current_output = nullptr;
        emit(ev);
    };
    wf::pointf_t pending, position;
    std::shared_ptr<live_drag_transform_t> transform;
    wf::scene::floating_inner_ptr parent;
    std::unique_ptr<wf::input_grab_t> grab;
    wf::output_t *grab_output = nullptr;
    int finger = -1;
    bool gesture = false;
    uint32_t button = BTN_LEFT;
    bool finishing = false;
    bool transferring = false;
    wf::plugin_activation_data_t activation = {
        .name = "scottland-move",
        .capabilities = wf::CAPABILITY_GRAB_INPUT | wf::CAPABILITY_MANAGE_DESKTOP,
    };
    wf::signal::connection_t<wf::view_unmapped_signal> unmap =
        [this] (auto *) { handle_input_released(); };
    wf::signal::connection_t<wf::output_removed_signal> removed =
        [this] (auto *ev)
    {
        if (ev->output == current_output || ev->output == grab_output ||
            (view && view->get_output() == ev->output)) handle_input_released();
    };
    bool grab_on(wf::output_t *output)
    {
        if (!output) return false;
        if (grab_output == output) return true;
        if (!output->activate_plugin(&activation)) return false;
        transferring = true;
        if (grab) grab->ungrab_input();
        if (grab_output) grab_output->deactivate_plugin(&activation);
        grab_output = output;
        grab = std::make_unique<wf::input_grab_t>("scottland-move", output, nullptr, this, this);
        grab->set_wants_raw_input(true);
        grab->grab_input(wf::scene::layer::OVERLAY);
        transferring = false;
        return true;
    }

  public:
    wayfire_toplevel_view view;
    wf::output_t *current_output = nullptr;
    live_drag_t()
    {
        activation.cancel = [this] { handle_input_released(); };
        legacy->connect(&legacy_output);
        legacy->connect(&legacy_motion);
        legacy->connect(&legacy_done);
        wf::get_core().output_layout->connect(&removed);
    }
    ~live_drag_t() { handle_input_released(); }
    bool is_live() const { return bool(transform); }
    void set_pending_drag(wf::pointf_t p) { pending = p; }
    // device: the pointer whose button started the drag; only its release ends it (WK39:
    // another device must not end or take an offer). Null accepts any.
    void set_input(int touch_finger = -1, bool swipe = false, uint32_t pointer_button = BTN_LEFT,
        wlr_input_device *device = nullptr)
    {
        finger = touch_finger;
        gesture = swipe;
        button = pointer_button;
        owner = device;
    }
    wlr_input_device *owner = nullptr;
    void start_drag(wayfire_toplevel_view target)
    {
        if (view || !target || !target->is_mapped() || !target->get_output() ||
            !(target->get_allowed_actions() & wf::VIEW_ALLOW_MOVE) || !grab_on(target->get_output())) return;
        view = target;
        current_output = target->get_output();
        position = pending;
        wf::get_core().default_wm->focus_raise_view(view);
        wf::get_core().default_wm->set_view_grabbed(view, true);
        auto node = view->get_transformed_node();
        auto box = node->get_bounding_box() + wf::origin(current_output->get_layout_geometry());
        transform = std::make_shared<live_drag_transform_t>();
        transform->relative = wf::move_drag::find_relative_grab(box, position);
        transform->position = position;
        node->add_transformer(transform, wf::TRANSFORMER_HIGHLEVEL - 1, "scottland-live-drag");
        parent = std::dynamic_pointer_cast<wf::scene::floating_inner_node_t>(node->parent()->shared_from_this());
        wf::scene::readd_front(wf::get_core().scene(), node);
        view->connect(&unmap);
        wf::get_core().set_cursor("grabbing");
        wf::move_drag::drag_focus_output_signal ev{nullptr, current_output};
        emit(&ev);
    }
    // Suspended: the window is drawn where it really is, still in front of everything in layout
    // coordinates, while the grab, its motion and its release keep coming here. A fired hold is
    // suspended for as long as it offers (WK39). Resuming carries on as the ordinary drag: the
    // window rejoins the pointer at its original grab point.
    bool suspended = false;
    void suspend(bool on)
    {
        if (!view || !transform || on == suspended || !view->get_output()) return;
        suspended = on;
        auto node = view->get_transformed_node();
        node->begin_transform_update();
        if (on) transform->pinned = wf::origin(view->get_output()->get_layout_geometry());
        else { transform->pinned.reset(); transform->position = position; }
        node->end_transform_update();
        view->damage();
    }
    void handle_motion(wf::pointf_t to)
    {
        if (!view || finishing || transferring) return;
        if (suspended)
        {
            position = to;
            wf::move_drag::drag_motion_signal ev{to};
            emit(&ev);
            return;
        }
        if (!transform)
        {
            legacy->handle_motion(to);
            return;
        }
        auto output = wf::get_core().output_layout->find_closest_output(to);
        if (output != current_output)
        {
            if (!grab_on(output)) return;
            auto previous = current_output;
            current_output = output;
            wf::get_core().seat->focus_output(output);
            wf::move_drag::drag_focus_output_signal ev{previous, output};
            emit(&ev);
        }
        position = to;
        auto node = view->get_transformed_node();
        node->begin_transform_update();
        transform->position = to;
        node->end_transform_update();
        wf::move_drag::drag_motion_signal ev{to};
        emit(&ev);
    }
    // commit = false ends the drag where the window really is, dropping whatever the drag drew
    // (a hold form, WK35/WK36: the gesture was a hold, never a move). Only the live renderer can
    // do that; the legacy move plugin always commits, so callers check is_live() first.
    void handle_input_released(bool commit = true)
    {
        if (!view || finishing || transferring) return;
        if (!transform)
        {
            legacy->handle_input_released();
            return;
        }
        finishing = true;
        auto target = view;
        auto output = current_output;
        auto relative = transform->relative;
        auto offset = transform->offset();
        auto geometry = target->get_geometry();
        auto node = target->get_transformed_node();
        unmap.disconnect();
        wf::scene::readd_front(parent, node);
        if (suspended) commit = false;  // a hold or an offer ends where the window really is
        node->rem_transformer(transform);
        suspended = false;
        transform.reset();
        parent.reset();
        if (grab) grab->ungrab_input();
        grab.reset();
        if (grab_output) grab_output->deactivate_plugin(&activation);
        grab_output = nullptr;
        // Commit the shown global position before the common drop/morph/physics path.
        if (commit && target->is_mapped() && output)
        {
            if (target->get_output() != output) wf::move_view_to_output(target, output, false);
            auto origin = wf::origin(output->get_layout_geometry());
            target->move(geometry.x + offset.x - origin.x, geometry.y + offset.y - origin.y);
            wf::get_core().default_wm->focus_raise_view(target);
        }
        wf::get_core().default_wm->set_view_grabbed(target, false);
        view = nullptr;
        current_output = nullptr;
        wf::get_core().set_cursor("default");
        wf::move_drag::drag_done_signal ev;
        ev.main_view = target;
        ev.focused_output = output;
        ev.grab_position = position;
        ev.join_views = false;
        ev.all_views.push_back({target, relative});
        emit(&ev);
        finishing = false;
    }
    void handle_pointer_motion(wf::pointf_t, uint32_t) override
    {
        if (finger < 0 && !gesture) handle_motion(wf::get_core().get_cursor_position());
    }
    void handle_pointer_button(const wlr_pointer_button_event& ev) override
    {
        if (finger < 0 && ev.button == button && ev.state == WL_POINTER_BUTTON_STATE_RELEASED &&
            (!owner || !ev.pointer || &ev.pointer->base == owner))
            handle_input_released();
    }
    void handle_touch_motion(uint32_t, int id, wf::pointf_t) override
    {
        if (id == finger) handle_motion(wf::get_core().get_touch_position(id));
    }
    void handle_touch_up(uint32_t, int id, wf::pointf_t) override
    {
        if (id == finger) handle_input_released();
    }
};
}
