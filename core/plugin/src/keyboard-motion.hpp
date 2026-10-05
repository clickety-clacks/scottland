// Shared Wayfire motion integration for keyboard impulses and drag releases. Axis math is in inertia.*.
    wf::option_wrapper_t<double> key_impulse{"scottland/key_impulse"};
    wf::option_wrapper_t<double> key_friction{"scottland/key_friction"};
    wf::option_wrapper_t<double> resize_impulse{"scottland/resize_impulse"};
    wf::option_wrapper_t<double> resize_friction{"scottland/resize_friction"};
    wf::option_wrapper_t<double> key_max_velocity{"scottland/key_max_velocity"};
    using motion_clock = std::chrono::steady_clock;
    struct keyboard_motion
    {
        std::weak_ptr<wf::view_interface_t> view;
        double x = 0, y = 0, width = 0, height = 0;
        scottland::windowing::inertial_axis vx, vy, vw, vh;
        motion_clock::time_point settle_until;
        bool drag_coast = false;
        bool resizing = false;
        bool settling_resize = false;
        std::string released_rail; // confine the first widget-undock coast to its side
    };
    struct deferred_impulse { uint32_t code; uint64_t id; bool resize; };
    std::vector<deferred_impulse> fullscreen_impulses;
    std::map<uint64_t, keyboard_motion> keyboard_motions;
    struct arrow_repeat { motion_clock::time_point next; double interval; };
    std::map<uint32_t, arrow_repeat> arrow_repeats;
    wf::wl_timer<true> keyboard_tick;
    motion_clock::time_point keyboard_sample = motion_clock::now();
    bool recentering_keyboard = false;
    bool keyboard_selection = false;

    scottland::windowing::release_velocity drag_velocity;
    bool inertia_active() const
    {
        for (auto& [id, m] : keyboard_motions)
            if (m.vx.velocity || m.vy.velocity || m.vw.velocity || m.vh.velocity) return true;
        return false;
    }
    void start_drag_coast(wayfire_toplevel_view view)
    {
        auto [vx, vy] = drag_velocity.estimate(now_msec());
        if ((!vx && !vy) || !view || !view->is_mapped() || !view->get_output() ||
            is_widget(view) || link_of_window(view) || view->pending_fullscreen()) return;
        // Bring other coasts to now before adding this one to the shared clock.
        step_keyboard_motion();
        auto& m = keyboard_motions[view->get_id()]; m = {};
        m.view = view->weak_from_this(); m.drag_coast = true;
        auto g = view->get_geometry(); m.x = g.x + g.width / 2.0; m.y = g.y + g.height / 2.0;
        m.width = g.width; m.height = g.height;
        m.vx.impulse(vx, key_max_velocity); m.vy.impulse(vy, key_max_velocity);
        if (!keyboard_tick.is_connected()) keyboard_tick.set_timeout(8, [=] () { SCOTTLAND_LOOP_SCOPE(keyboard_tick); return step_keyboard_motion(); });
    }

    static bool arrow_key(uint32_t code)
    { return code == KEY_LEFT || code == KEY_RIGHT || code == KEY_UP || code == KEY_DOWN; }

    void stop_keyboard_motion()
    {
        arrow_repeats.clear(); fullscreen_impulses.clear(); keyboard_motions.clear(); keyboard_tick.disconnect();
    }
    // A seam is open only where the center's orthogonal coordinate meets a touching
    // output and the workarea reaches that physical edge. Gaps and reserved edges remain closed.
    std::array<wf::output_t*, 4> keyboard_neighbors(wf::output_t *output, double x, double y)
    {
        std::array<wf::output_t*, 4> neighbors{}; // left, right, top, bottom
        auto box = output->get_layout_geometry();
        auto area = output->workarea->get_workarea();
        double gx = box.x + x, gy = box.y + y;
        for (auto other : wf::get_core().output_layout->get_outputs())
        {
            if (other == output) continue;
            auto b = other->get_layout_geometry();
            if (gy >= b.y && gy < b.y + b.height)
            {
                if (area.x == 0 && b.x + b.width == box.x) neighbors[0] = other;
                if (area.x + area.width == box.width && b.x == box.x + box.width) neighbors[1] = other;
            }
            if (gx >= b.x && gx < b.x + b.width)
            {
                if (area.y == 0 && b.y + b.height == box.y) neighbors[2] = other;
                if (area.y + area.height == box.height && b.y == box.y + box.height) neighbors[3] = other;
            }
        }
        return neighbors;
    }
    void keyboard_resize_bounds(wayfire_toplevel_view view, keyboard_motion& m)
    {
        auto screen = view->get_output()->get_relative_geometry();
        auto area = view->get_output()->workarea->get_workarea();
        auto g = view->get_geometry();
        auto neighbors = keyboard_neighbors(view->get_output(), m.x, m.y);
        double rail = screen.width * std::clamp(double(rail_width) / 100, 0.0, 0.25);
        auto fits = [&] (double x, bool left) {
            double scale = model.windows[view->get_id()].pinned_scale ?
                *model.windows[view->get_id()].pinned_scale : place_at(x, screen.width).scale;
            double w = g.width * scale;
            auto pa = padded(area, w, g.height * scale);
            return left ? x - w / 2 >= std::max(double(pa.x), rail) :
                x + w / 2 <= std::min(double(pa.x + pa.width), screen.width - rail);
        };
        double lo = rail + 0.01, hi = screen.width - rail - 0.01;
        double a = lo, b = screen.width / 2.0;
        if (fits(b, true)) { for (int n = 0; n < 40; ++n) { double x=(a+b)/2; if (fits(x,true)) b=x; else a=x; } lo=b; }
        a = screen.width / 2.0; b = hi;
        if (fits(a, false)) { for (int n = 0; n < 40; ++n) { double x=(a+b)/2; if (fits(x,false)) a=x; else b=x; } hi=a; }
        if (neighbors[0]) lo = -std::numeric_limits<double>::infinity();
        if (neighbors[1]) hi = std::numeric_limits<double>::infinity();
        m.x = std::clamp(m.x, lo, std::max(lo, hi));
        double scale = model.windows[view->get_id()].pinned_scale ?
            *model.windows[view->get_id()].pinned_scale : place_at(m.x, screen.width).scale;
        auto pa = padded(area, g.width * scale, g.height * scale);
        double half = std::min(g.height * scale, double(pa.height)) / 2;
        lo = neighbors[2] ? -std::numeric_limits<double>::infinity() : pa.y + half;
        hi = neighbors[3] ? std::numeric_limits<double>::infinity() : pa.y + pa.height - half;
        m.y = std::clamp(m.y, lo, std::max(lo, hi));
    }
    // WK20: the scaled footprint touching the inner rail edge is the visible
    // arrival (tenets 2/3). Only outward travel docks; an old drop may move away.
    // Keep WK21's fully-contained resize recovery separate from movement bounds.
    std::optional<std::string> inertial_edges(wayfire_toplevel_view view,
        keyboard_motion& m, double dx, double dy)
    {
        if (dx == 0 && dy == 0) return {};
        auto output = view->get_output();
        auto screen = output->get_relative_geometry();
        auto area = output->workarea->get_workarea();
        auto g = view->get_geometry();
        auto neighbors = keyboard_neighbors(output, m.x, m.y);
        auto scale_at = [&] (double x) {
            auto pin = model.windows[view->get_id()].pinned_scale;
            return pin ? *pin : place_at(x, screen.width).scale;
        };
        double rail = screen.width * std::clamp(double(rail_width) / 100, 0.0, 0.25);
        double left = std::max(double(area.x), rail);
        double right = std::min(double(area.x + area.width), screen.width - rail);
        bool hit_left = dx < 0 && !neighbors[0] && m.x - g.width * scale_at(m.x) / 2 <= left;
        bool hit_right = dx > 0 && !neighbors[1] && m.x + g.width * scale_at(m.x) / 2 >= right;
        if (hit_left || hit_right)
        {
            // Locate contact using the same live scale as the renderer; a late tick
            // must not carry the source image through the rail before the morph starts.
            double lo = left, hi = right;
            for (int n = 0; n < 40; ++n)
            {
                double x = (lo + hi) / 2;
                bool inside = hit_left ? x - g.width * scale_at(x) / 2 > left :
                    x + g.width * scale_at(x) / 2 < right;
                if (inside == hit_left) hi = x; else lo = x;
            }
            double contact = (lo + hi) / 2;
            // A precise drop can already overlap the rail. Dock from that image,
            // rather than snapping it back inward to a contact it has passed.
            m.x = hit_left ? std::min(m.x - dx, contact) : std::max(m.x - dx, contact);
            m.vx.velocity = 0;
        }
        double height = g.height * scale_at(m.x);
        // Small windows stay wholly visible when their entire footprint is <100 pt.
        double visible = std::min(100.0, height);
        double lo = neighbors[2] ? -std::numeric_limits<double>::infinity() : area.y + visible - height / 2;
        double hi = neighbors[3] ? std::numeric_limits<double>::infinity() : area.y + area.height - visible + height / 2;
        m.y = m.vy.constrain(m.y, lo, hi);
        if ((hit_left || hit_right) && can_widgetize(view)) return hit_left ? "left" : "right";
        return {};
    }
    void recenter_keyboard_resize(wayfire_toplevel_view view)
    {
        if (recentering_keyboard) return;
        auto found = keyboard_motions.find(view->get_id());
        if (found == keyboard_motions.end() || !found->second.resizing) return;
        auto& motion = found->second;
        // A late transaction starts its own quiet period; do not drop the anchor while
        // a client is still committing rejected/minimum or cell-snapped sizes.
        motion.settle_until = motion_clock::now() + std::chrono::milliseconds(300);
        keyboard_resize_bounds(view, motion);
        auto g = view->get_geometry();
        double x = std::round(motion.x - g.width / 2.0), y = std::round(motion.y - g.height / 2.0);
        if (std::abs(g.x - x) > 0.001 || std::abs(g.y - y) > 0.001)
        {
            recentering_keyboard = true;
            move_window(view, x, y);
            recentering_keyboard = false;
        }
    }
    void cancel_keyboard_motion()
    {
        // Esc ends the gesture, not the user's completed movement. Geometry already
        // committed by the pushes/coast remains; record its landing and stop further travel.
        // Keep a resize anchor briefly so late client size commits still land centered.
        arrow_repeats.clear(); fullscreen_impulses.clear();
        bool settling = false;
        auto now = motion_clock::now();
        for (auto it = keyboard_motions.begin(); it != keyboard_motions.end();)
        {
            auto view = wf::toplevel_cast(it->second.view.lock());
            if (view && view->is_mapped() && view->get_output() && it->second.resizing)
            {
                auto& motion = it->second;
                motion.vx.velocity = motion.vy.velocity = motion.vw.velocity = motion.vh.velocity = 0;
                motion.settling_resize = true;
                motion.settle_until = now + std::chrono::milliseconds(300);
                settling = true; ++it;
            } else
            {
                if (view && view->is_mapped() && view->get_output()) remember_window(view);
                it = keyboard_motions.erase(it);
            }
        }
        keyboard_sample = now;
        if (settling) keyboard_tick.set_timeout(8, [=] () { SCOTTLAND_LOOP_SCOPE(keyboard_tick); return step_keyboard_motion(); });
        else keyboard_tick.disconnect();
    }

    void keyboard_impulse(uint32_t code, uint64_t destination = 0, std::optional<bool> requested_resize = std::nullopt)
    {
        uint64_t id = destination ? destination : window_keys.selected;
        if (!destination && !keyboard_selection)
        {
            auto active = wf::get_core().seat->get_active_view();
            auto link = link_of_widget(active);
            id = link ? link->window_id : active ? active->get_id() : 0;
        }
        auto view = represented_view(id);
        if (!view || !view->is_mapped() || !view->get_output()) return;
        if (link_of_window(view))
        {
            // A pending WG22 handoff still represents this app. Consume its impulse
            // without moving the captured image.
            return;
        }
        bool resize = requested_resize.value_or(held_keys.count(KEY_LEFTCTRL) || held_keys.count(KEY_RIGHTCTRL));
        if (view->pending_fullscreen() || view->toplevel()->current().fullscreen)
        {
            fullscreen_impulses.push_back({code, id, resize});
            if (view->pending_fullscreen())
                wf::get_core().default_wm->fullscreen_request(view, view->get_output(), false);
            return;
        }
        if (resize && (is_widget(view) || !(view->get_allowed_actions() & wf::VIEW_ALLOW_RESIZE))) return;
        if (!resize && !(view->get_allowed_actions() & wf::VIEW_ALLOW_MOVE)) return;
        bool opened_widget = false;
        std::optional<wf::pointf_t> restored_destination;
        std::string released_rail;
        if (!resize && is_widget(view) && (code == KEY_LEFT || code == KEY_RIGHT))
        {
            auto link = link_of_widget(view);
            bool away = link && ((link->rail == "right" && code == KEY_LEFT) ||
                (link->rail == "left" && code == KEY_RIGHT));
            if (!away) return; // a push into the attached rail has no travel
            auto widget = view;
            released_rail = link->rail;
            auto from = widget->get_geometry();
            wf::pointf_t start{from.x + from.width / 2.0, from.y + from.height / 2.0};
            auto window = wf::toplevel_cast(link->window.lock());
            if (!window) return;
            auto destination = zone_spot(window, link->rail == "right" ?
                scottland::windowing::zone::right_periphery :
                scottland::windowing::zone::left_periphery,
                {start.x, start.y}, nullptr, widget->get_output());
            keyboard_motions.erase(widget->get_id());
            remember_window(widget);
            restore_window(*link, destination, true);
            view = window;
            if (!view->is_mapped() || !view->get_output()) return;
            start_glide(view, start.x - destination.x, start.y - destination.y);
            opened_widget = true;
            restored_destination = destination;
        }
        auto& motion = keyboard_motions[view->get_id()];
        if (motion.view.lock().get() != view.get())
        {
            motion = {}; motion.view = view->weak_from_this();
            auto g = view->get_geometry(); motion.x = g.x + g.width / 2.0; motion.y = g.y + g.height / 2.0;
            motion.width = g.width; motion.height = g.height;
        }
        // Restoring the app schedules a Wayfire geometry transaction. Its current geometry can
        // still be the old hidden-window position on this tick; coast from the chosen same-side
        // destination instead of letting that stale center hit the opposite rail.
        if (restored_destination)
        {
            motion.x = restored_destination->x;
            motion.y = restored_destination->y;
        }
        if (opened_widget) motion.released_rail = released_rail;
        motion.drag_coast = false; motion.settling_resize = false;
        if (resize)
        {
            auto g = view->get_geometry();
            if (motion.vw.velocity == 0) motion.width = g.width;
            if (motion.vh.velocity == 0) motion.height = g.height;
        }
        if (!opened_widget) stop_glide(view);
        if (auto resizer = output_instance[view->get_output()].get()) resizer->stop_settling();
        if (!is_widget(view) && !resize)
        {
            if (shift_held())
            {
                if (!model.windows[view->get_id()].pinned_scale)
                    pin_scale(view, displayed_scale(view));
            } else pin_scale(view, std::nullopt);
        }
        if (resize && !is_widget(view)) pin_scale(view, std::nullopt);
        double sign = (code == KEY_RIGHT || code == KEY_DOWN) ? 1 : -1;
        auto& axis = resize ? ((code == KEY_LEFT || code == KEY_RIGHT) ? motion.vw : motion.vh) :
            ((code == KEY_LEFT || code == KEY_RIGHT) ? motion.vx : motion.vy);
        if (resize && (code == KEY_UP || code == KEY_DOWN)) sign = -sign;
        if (resize) motion.resizing = true;
        axis.impulse(sign * double(resize ? resize_impulse : key_impulse), double(key_max_velocity));
        motion.settle_until = motion_clock::now() + std::chrono::milliseconds(300);
    }

    bool step_keyboard_motion()
    {
        auto now = motion_clock::now();
        double dt = std::chrono::duration<double>(now - keyboard_sample).count(); keyboard_sample = now;
        for (auto it = keyboard_motions.begin(); it != keyboard_motions.end();)
        {
            auto view = wf::toplevel_cast(it->second.view.lock());
            if (!view || !view->is_mapped() || !view->get_output() || drag->view == view || view->pending_fullscreen() || link_of_window(view))
            { it = keyboard_motions.erase(it); continue; }
            auto& m = it->second; auto g = view->get_geometry();
            if (m.settling_resize)
            {
                if (now >= m.settle_until) { remember_window(view); it = keyboard_motions.erase(it); }
                else ++it;
                continue;
            }
            double dx = m.vx.step(dt, key_friction), dy = m.vy.step(dt, key_friction);
            m.x += dx; m.y += dy;
            if (!m.released_rail.empty() && !is_widget(view))
            {
                auto screen = view->get_output()->get_relative_geometry();
                double edge = screen.width * (1 - std::clamp(double(center_width) / 100, 0.0, 1.0)) / 2;
                if (m.released_rail == "left" && m.x >= edge - 1)
                { m.x = edge - 1; m.vx.velocity = 0; }
                if (m.released_rail == "right" && m.x <= screen.width - edge + 1)
                { m.x = screen.width - edge + 1; m.vx.velocity = 0; }
            }
            // Transfer only when the center crosses the physical seam. Keep velocity and
            // the global center; crossing an adjoining output never changes form.
            if (!is_widget(view))
            {
                auto source = view->get_output();
                auto box = source->get_layout_geometry();
                auto neighbors = keyboard_neighbors(source, m.x, m.y);
                auto next = m.x < 0 ? neighbors[0] : m.x >= box.width ? neighbors[1] :
                    m.y < 0 ? neighbors[2] : m.y >= box.height ? neighbors[3] : nullptr;
                if (next)
                {
                    auto target = next->get_layout_geometry();
                    m.x += box.x - target.x; m.y += box.y - target.y;
                    bool focused = wf::get_core().seat->get_active_view() == view;
                    wf::move_view_to_output(view, next, false);
                    if (focused)
                    {
                        wf::get_core().seat->focus_output(next);
                        wf::get_core().default_wm->focus_raise_view(view);
                    }
                }
            }
            auto area = view->get_output()->workarea->get_workarea();
            if (auto link = link_of_widget(view))
            {
                m.y = m.vy.constrain(m.y, area.y + WIDGET_INSET + g.height / 2.0,
                    area.y + area.height - WIDGET_INSET - g.height / 2.0);
                link->drop = {m.x, m.y}; place_widget(view, view->get_output(), *link);
            } else
            {
                double dw = m.vw.step(dt, resize_friction), dh = m.vh.step(dt, resize_friction);
                auto minimum = view->toplevel()->get_min_size(), maximum = view->toplevel()->get_max_size();
                // Client sizes are integer pixels: round the cap down, never the requested
                // size up past padding (which also changes parity and shifts a later anchor).
                double maxw = std::floor(area.width - 2 * SCREEN_PADDING);
                double maxh = std::floor(area.height - 2 * SCREEN_PADDING);
                if (maximum.width > 0) maxw = std::min(maxw, double(maximum.width));
                if (maximum.height > 0) maxh = std::min(maxh, double(maximum.height));
                if (dw != 0) m.width = m.vw.constrain(m.width + dw, std::max(1, minimum.width), maxw);
                if (dh != 0) m.height = m.vh.constrain(m.height + dh, std::max(1, minimum.height), maxh);
                if (dw != 0 || dh != 0)
                {
                    auto& pending = view->toplevel()->pending();
                    pending.gravity = 0; pending.tiled_edges = 0;
                    // Request size at the current position; recenter only the committed size (L20).
                    pending.geometry = {g.x, g.y, std::round(m.width), std::round(m.height)};
                    wf::get_core().tx_manager->schedule_object(view->toplevel());
                }
                auto rail = inertial_edges(view, m, dx, dy);
                auto actual = view->get_geometry();
                double x = m.x - actual.width / 2.0, y = m.y - actual.height / 2.0;
                // Match L20's centering at the client's pixel grid, including odd dimensions.
                // Both commit and coast paths must choose the same position.
                if (m.resizing) { x = std::round(x); y = std::round(y); }
                if (std::abs(actual.x - x) > 0.001 || std::abs(actual.y - y) > 0.001)
                    move_window(view, x, y);
                set_scale_now(view, scale_for(view));
                if (rail)
                {
                    // End all axes before starting WG22, including pending resize
                    // recovery. Launching cards must not receive more app impulses.
                    it = keyboard_motions.erase(it);
                    widgetize(view, false, *rail, "inertial-contact");
                    continue;
                }
            }
            bool moving = m.vx.velocity || m.vy.velocity || m.vw.velocity || m.vh.velocity;
            if (moving) m.settle_until = now + std::chrono::milliseconds(300);
            if (!moving && now >= m.settle_until)
            { remember_window(view); it = keyboard_motions.erase(it); }
            else ++it;
        }
        auto pending_impulses = std::move(fullscreen_impulses); fullscreen_impulses.clear();
        for (auto impulse : pending_impulses) keyboard_impulse(impulse.code, impulse.id, impulse.resize);
        for (auto& [code, repeat] : arrow_repeats)
            if (window_keys.active && now >= repeat.next)
            {
                // One repeat per due tick: after a stall the missed repeats are not replayed as a
                // burst (main-loop design 2.6); the next one follows at the keyboard's rate.
                keyboard_impulse(code);
                auto interval = std::chrono::duration_cast<motion_clock::duration>(
                    std::chrono::duration<double>(repeat.interval));
                repeat.next += interval;
                if (repeat.next <= now) repeat.next = now + interval;
            }
        return !keyboard_motions.empty() || !arrow_repeats.empty() || !fullscreen_impulses.empty();
    }
    void press_arrow(uint32_t code, wlr_keyboard *keyboard, bool first)
    {
        step_keyboard_motion(); keyboard_impulse(code);
        if (first && keyboard->repeat_info.rate > 0)
            arrow_repeats[code] = {motion_clock::now() + std::chrono::milliseconds(keyboard->repeat_info.delay),
                1.0 / keyboard->repeat_info.rate};
        if (!keyboard_tick.is_connected()) keyboard_tick.set_timeout(8, [=] () { SCOTTLAND_LOOP_SCOPE(keyboard_tick); return step_keyboard_motion(); });
    }
