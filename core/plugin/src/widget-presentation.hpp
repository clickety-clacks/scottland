// Included inside scottland_plugin_t: presentation transitions are per-widget renderer
// resources, deliberately separate from the one model-owned window/widget drag morph.
struct widget_transition_t
{
    std::weak_ptr<wf::view_interface_t> view;
    std::shared_ptr<scottland::widget_morph_t> pixels;
    std::vector<std::unique_ptr<wf::signal::connection_t<wf::scene::node_damage_signal>>> applied;
    wf::wl_listener_wrapper surface_destroyed;
    bool buffer_applied = false;
    bool target_collapsed = false;
    bool card = false;
};
std::map<uint64_t, std::unique_ptr<widget_transition_t>> widget_transitions;
wf::wl_timer<true> widget_transition_tick;
uint64_t widget_transition_steps = 0;

void stop_widget_transition(wayfire_toplevel_view view)
{
    if (!view) return;
    if (auto frame = frame_of(view, false); frame && frame->presentation)
    {
        frame->damage();
        frame->presentation.reset();
        frame->damage();
        wf::scene::update(frame, wf::scene::update_flag::GEOMETRY);
    }
    widget_transitions.erase(view->get_id());
    if (widget_transitions.empty()) widget_transition_tick.disconnect();
}

void begin_widget_transition(widget_link_t& link, bool target)
{
    auto view = wf::toplevel_cast(link.widget.lock());
    if (!view || !view->is_mapped() || !view->get_output() || !wf::get_core().is_gles2()) return;
    auto frame = frame_of(view);
    if (!frame) return;
    auto transition = std::make_unique<widget_transition_t>();
    transition->view = view->weak_from_this();
    transition->target_collapsed = target;
    transition->card = link.card;
    auto pixels = std::make_shared<scottland::widget_morph_t>();
    pixels->from = frame->presentation ? scottland::widget_morph_renderer().freeze(
        *frame->presentation, view->get_output()->handle->scale) :
        scottland::widget_image_t::capture(view, link.card ? (view->get_geometry().width == 96 ? 20 : 16) : 0);
    if (!pixels->from) return;
    pixels->width = pixels->from.width;
    pixels->height = pixels->from.height;
    pixels->inset = pixels->from.inset;
    if (frame->presentation)
    {
        pixels->dx = pixels->from_dx = frame->presentation->dx;
        pixels->dy = pixels->from_dy = frame->presentation->dy;
    }
    pixels->right = link.rail == "right";
    pixels->requested = now_msec();
    transition->pixels = pixels;

    // Listen at the leaf, not at a transformer or transaction. wlr_surface_node_t::apply_state
    // first installs current_state (buffer, size, texture), then emits this damage, even when
    // the damage is empty or the size is unchanged. Capturing on the next tick also lets its
    // enclosing transaction finish updating the view geometry. A pre-commit hook is not enough.
    auto *observer = transition.get();
    auto surface = view->get_wlr_surface();
    uint32_t sequence = surface ? surface->current.seq : 0;
    std::function<void(wf::scene::node_ptr)> watch = [&] (wf::scene::node_ptr node)
    {
        auto leaf = dynamic_cast<wf::scene::zero_copy_texturable_node_t*>(node.get());
        auto texture = leaf ? leaf->to_texture() : nullptr;
        // Identify the main surface through its public texture interface. This avoids
        // depending on Wayfire's unstable surface class layout (and unrelated Vulkan headers).
        if (texture && surface && surface->buffer &&
            texture->get_wlr_texture() == surface->buffer->texture)
        {
            auto listener = std::make_unique<wf::signal::connection_t<wf::scene::node_damage_signal>>(
                [observer, surface, sequence, leaf, texture] (auto*)
                {
                    auto applied = leaf->to_texture();
                    // Keep the original buffer locked and compare the APPLIED texture. A
                    // later ack-only commit can clear current.committed's BUFFER bit while
                    // the transaction still applies an earlier, newly drawn client buffer.
                    if (applied && applied->get_wlr_texture() != texture->get_wlr_texture())
                        observer->buffer_applied = true;
                    else if (surface && surface->current.seq != sequence &&
                        (surface->current.committed & WLR_SURFACE_STATE_BUFFER) && applied &&
                        surface->buffer && applied->get_wlr_texture() == surface->buffer->texture)
                        observer->buffer_applied = true;
                });
            node->connect(listener.get());
            observer->applied.push_back(std::move(listener));
        }
        for (auto& child : node->get_children()) watch(child);
    };
    watch(view->get_surface_root_node());
    if (surface)
    {
        observer->surface_destroyed.set_callback([observer] (void*) { observer->applied.clear(); });
        observer->surface_destroyed.connect(&surface->events.destroy);
    }
    frame->damage();
    frame->presentation = pixels;
    widget_transitions[view->get_id()] = std::move(transition);
    if (!widget_transition_tick.is_connected())
        widget_transition_tick.set_timeout(8, [=] { return step_widget_transitions(); });
}

// All presentation changes come through here, before announcing the model. A future hover
// or attention peek uses the same operation with peek=true, without changing collapsed intent.
void set_widget_presentation(widget_link_t& link, bool collapsed, bool peek = false)
{
    bool target = collapsed && !peek;
    if (target != link.minimized()) begin_widget_transition(link, target);
    link.collapsed = collapsed;
    link.peek = peek;
}

bool step_widget_transitions()
{
    uint32_t now = now_msec();
    for (auto it = widget_transitions.begin(); it != widget_transitions.end();)
    {
        auto& transition = *it->second;
        auto view = wf::toplevel_cast(transition.view.lock());
        auto frame = view && view->is_mapped() ? frame_of(view, false) : nullptr;
        auto link = link_of_widget(view);
        if (!frame || !link || (!link->docked() && !link->previewing()))
        {
            if (frame) { frame->damage(); frame->presentation.reset(); frame->damage(); }
            it = widget_transitions.erase(it);
            continue;
        }
        auto& p = *transition.pixels;
        if (p.waiting)
        {
            // Hidden previews and fullscreen widgets still need a chance to draw the new state.
            if (auto surface = view->get_wlr_surface())
            {
                timespec stamp;
                clock_gettime(CLOCK_MONOTONIC, &stamp);
                wlr_surface_send_frame_done(surface, &stamp);
            }
            auto g = view->get_geometry();
            bool size_ready = !transition.card || (transition.target_collapsed ?
                g.width == 96 : g.width > 96);
            bool expired = now - p.requested >= scottland::widget_morph_t::response_timeout;
            if (!(transition.buffer_applied && size_ready) && !expired) { ++it; continue; }
            p.fallback = !(transition.buffer_applied && size_ready);
            p.response_ms = now - p.requested;
            p.to = scottland::widget_image_t::capture(view,
                transition.card ? (g.width == 96 ? 20 : 16) : 0);
            if (!p.to) { frame->presentation.reset(); it = widget_transitions.erase(it); continue; }
            p.waiting = false;
            p.started = now;
            transition.applied.clear();
            transition.surface_destroyed.disconnect();
        }
        frame->damage();
        p.step(now);
        ++widget_transition_steps;
        if (p.fade >= 1)
        {
            frame->presentation.reset();
            it = widget_transitions.erase(it); // release snapshots and all observers
        } else
        {
            ++it;
        }
        frame->damage();
        wf::scene::update(frame, wf::scene::update_flag::GEOMETRY);
        update_neighbors(view->get_output());
    }
    return !widget_transitions.empty();
}
