// Included inside scottland_plugin_t. WG19 trigger state belongs to model.widgets;
// this timer is only an input resource and never survives a plugin reload.
wf::option_wrapper_t<int> widget_peek_enter_delay{"scottland/widget_peek_enter_delay"};
wf::option_wrapper_t<int> widget_peek_leave_delay{"scottland/widget_peek_leave_delay"};
wf::option_wrapper_t<int> widget_attention_peek_duration{"scottland/widget_attention_peek_duration"};
wf::wl_timer<true> widget_peek_tick;

void reset_widget_peek(widget_link_t& link)
{
    link.peek_pointer = link.peek_hover = false;
    link.peek_hover_due.reset();
    link.peek_attention_due.reset(); link.peek_hint_due.reset();
}

bool step_widget_peeks()
{
    auto now = now_msec();
    auto cursor = wf::get_core().get_cursor_position();
    auto output = wf::get_core().output_layout->find_closest_output(cursor);
    uint64_t hit = 0;
    if (output && !in_focus_mode(output))
    {
        auto local = cursor - wf::origin(output->get_layout_geometry());
        // Share the visible frame and actual control hit regions with both halo renderers.
        // Proximity lighting alone is not hover intent. Respect scene stacking/occlusion.
        for (auto& [view, frame] : frames_on(output))
        {
            if (frame->liquid_distance(local) <= 0 ||
                frame->handle_at(local) != scottland::handle_t::none)
            {
                if (auto link = link_of_widget(view)) hit = link->window_id;
                break;
            }
        }
    }

    bool active = false, changed = false;
    for (auto& [id, link] : model.widgets)
    {
        bool eligible = link.collapsed && link.docked() && !link.away && !in_focus_mode(link.output);
        auto widget = wf::toplevel_cast(link.widget.lock());
        auto frame = widget ? frame_of(widget, false) : nullptr;
        // Wayfire owns the grab before the first motion records its model origin.
        bool held = (widget && drag->view == widget) ||
            (model.drag.started && ((widget && model.drag.widget == widget->get_id()) ||
                model.drag.origin.view == id)) || (frame && frame->is_pressed());
        if (!eligible)
        {
            reset_widget_peek(link);
        } else if (held)
        {
            // Never resize beneath a grab, including dragging across the rail boundary.
            // Expiry is reconciled from the current pointer after the release.
            active = true;
            continue;
        } else
        {
            bool inside = id == hit;
            if (inside != link.peek_pointer)
            {
                link.peek_pointer = inside;
                link.peek_hover_due = now + uint32_t(inside ? int(widget_peek_enter_delay) : int(widget_peek_leave_delay));
            }
            if (link.peek_hover_due && int32_t(now - *link.peek_hover_due) >= 0)
            {
                link.peek_hover = inside;
                link.peek_hover_due.reset();
            }
            if (link.peek_attention_due && int32_t(now - *link.peek_attention_due) >= 0)
            {
                // Attention hands off to hover even when the pointer just entered.
                if (inside) link.peek_hover = true;
                link.peek_attention_due.reset();
            }
            if (link.peek_hint_due && int32_t(now - *link.peek_hint_due) >= 0)
                link.peek_hint_due.reset();
        }
        bool peek = eligible && (link.peek_hover || link.peek_attention_due.has_value() ||
            link.peek_hint_due.has_value());
        if (peek != link.peek)
        {
            set_widget_presentation(link, link.collapsed, peek);
            changed = true;
        }
        // Track moving/morphing frames as well as pointer events while a peek is in play.
        // No timer runs when no widget is hovered, pending, grabbed or attention-peeking.
        active |= link.peek_pointer || link.peek_hover || link.peek_hover_due.has_value() ||
            link.peek_attention_due.has_value() || link.peek_hint_due.has_value();
    }
    if (changed) publish_model();
    return active;
}

void update_widget_peeks()
{
    if (step_widget_peeks() && !widget_peek_tick.is_connected())
        widget_peek_tick.set_timeout(16, [=] { return step_widget_peeks(); });
}

void peek_widget_for_hint(uint64_t id)
{
    auto found = model.widgets.find(id);
    if (found == model.widgets.end() || !found->second.collapsed || !found->second.docked()) return;
    found->second.peek_hint_due = now_msec() + 5000;
    update_widget_peeks();
}
