// Private integration fragment for scottland_plugin_t. The pure controller, placement, solver,
// renderer live separately; this bridge reads and updates the plugin-owned desktop model.
    scottland::windowing::alt_mode window_keys;
    wf::option_wrapper_t<int> alt_hold_delay{"scottland/alt_hold_delay"};
    std::set<uint32_t> swallowed_keys, alt_keys, held_keys;
    wf::wl_timer<false> alt_hold;
    bool capture_chord = false;
    bool alt_bypassed = false;
    wf::wl_timer<true> hints_tick;
    struct hint_visual
    {
        std::weak_ptr<wf::view_interface_t> view;
        std::shared_ptr<scottland::windowing::hint_node> hint;
        wf::output_t *hint_output = nullptr;
        std::shared_ptr<wf::scene::view_2d_transformer_t> offset;
        scottland::windowing::point target;
    };
    std::map<uint64_t, hint_visual> hint_visuals; // by represented application id
    std::string declutter_signature;
    wf::wl_idle_call hint_registration;

    scottland::windowing::zone window_zone(wayfire_toplevel_view view)
    {
        using Z = scottland::windowing::zone;
        if (auto link = link_of_window(view); link && link->docked())
            return link->rail == "left" ? Z::left_rail : Z::right_rail;
        if (auto link = link_of_widget(view)) return link->rail == "left" ? Z::left_rail : Z::right_rail;
        if (placement_of(view).zone == zone_t::center) return Z::center;
        auto g = view->get_geometry();
        return g.x + g.width / 2.0 < view->get_output()->get_relative_geometry().width / 2.0 ?
            Z::left_periphery : Z::right_periphery;
    }
    wayfire_toplevel_view represented_view(uint64_t id)
    {
        auto window = wf::toplevel_cast(view_by_id(id));
        if (auto link = link_of_window(window); link && link->docked())
            return wf::toplevel_cast(link->widget.lock());
        return window;
    }
    scottland::windowing::window_memory& ensure_window_memory(uint64_t id)
    {
        auto& state = model.windows.at(id);
        if (!state.placement)
        {
            std::set<unsigned> used;
            for (auto& [other, record] : model.windows)
                if (record.placement) used.insert(record.placement->hint_slot);
            unsigned slot = 0; while (used.count(slot)) ++slot;
            state.placement.emplace(); state.placement->hint_slot = slot;
        }
        return *state.placement;
    }
    static wf::json_t memory_snapshot(const scottland::windowing::window_memory& memory)
    {
        wf::json_t r; r["slot"] = int(memory.hint_slot); r["side"] = memory.last_side;
        r["positions"] = wf::json_t::array();
        for (auto p : memory.positions)
        { wf::json_t spot; spot["set"] = bool(p); if (p) { spot["x"] = p->x; spot["y"] = p->y; } r["positions"].append(spot); }
        return r;
    }
    static scottland::windowing::window_memory read_memory(wf::json_t r)
    {
        scottland::windowing::window_memory memory;
        memory.hint_slot = r["slot"].as_int(); memory.last_side = r["side"].as_int();
        for (size_t z = 0; z < memory.positions.size(); ++z) if (r["positions"][z]["set"].as_bool())
            memory.positions[z] = scottland::windowing::point{
                r["positions"][z]["x"].as_double(), r["positions"][z]["y"].as_double()};
        return memory;
    }
    void bypass_window_keys()
    {
        alt_bypassed = true; alt_hold.disconnect();
        if (capture_chord) { end_window_keys(); capture_chord = false; }
    }
    void remember_window(wayfire_toplevel_view view)
    {
        using Z = scottland::windowing::zone;
        if (!view || !view->is_mapped() || !view->get_output() || view->pending_fullscreen()) return;
        auto link = link_of_widget(view);
        uint64_t id = link ? link->window_id : view->get_id();
        if (!model.windows.count(id)) return;
        auto z = window_zone(view);
        auto g = view->get_geometry(); auto screen = view->get_output()->get_relative_geometry();
        auto& memory = ensure_window_memory(id);
        memory.positions[size_t(z)] = scottland::windowing::point{
            (g.x + g.width / 2.0) / screen.width, (g.y + g.height / 2.0) / screen.height};
        if (z != Z::center) memory.last_side = (z == Z::left_periphery || z == Z::left_rail) ? -1 : 1;
        publish_model();
    }
    std::vector<scottland::windowing::hint_entry> window_entries()
    {
        std::vector<scottland::windowing::hint_entry> entries;
        for (auto& [id, state] : model.windows)
        {
            auto view = wf::toplevel_cast(state.view.lock());
            if (!view || !view->is_mapped() || !view->get_output() ||
                view->role != wf::VIEW_ROLE_TOPLEVEL || is_widget(view) || runs_as_widget(state.pid))
            {
                state.placement.reset();
                continue;
            }
            if (!state.placement) { ensure_window_memory(id); remember_window(view); }
            auto link = link_of_window(view);
            entries.push_back({id, state.placement->hint_slot, window_zone(view), link && link->docked()});
        }
        window_keys.hint_width = model.hint_width;
        window_keys.refresh(entries);
        model.hint_width = window_keys.hint_width;
        publish_model();
        return entries;
    }
    std::vector<scottland::windowing::rectangle> placement_obstacles(wf::output_t *output, uint64_t excluded)
    {
        std::vector<scottland::windowing::rectangle> rectangles;
        for (auto e : window_entries())
        {
            auto view = represented_view(e.id);
            if (e.id == excluded || !view || view->get_output() != output) continue;
            auto g = view->get_geometry();
            double scale = is_widget(view) ? 1 : scale_for(view);
            rectangles.push_back({g.x + g.width * (1 - scale) / 2,
                g.y + g.height * (1 - scale) / 2, g.width * scale, g.height * scale});
        }
        return rectangles;
    }
    scottland::windowing::rectangle side_region(wf::output_t *output, bool left, bool rail)
    {
        auto screen = output->get_relative_geometry(); auto a = output->workarea->get_workarea();
        double edge = screen.width * std::clamp(double(rail_width) / 100, 0.0, 0.25);
        double center_edge = screen.width * (1 - std::clamp(double(center_width) / 100, 0.0, 1.0)) / 2;
        double lo = rail ? 0 : edge + 1, hi = rail ? edge : center_edge - 1;
        if (hi < lo) hi = lo;
        return {left ? lo : screen.width - hi, double(a.y), hi - lo, double(a.height)};
    }
    bool placement_side(wayfire_toplevel_view window, scottland::windowing::point current, bool rail)
    {
        auto& memory = ensure_window_memory(window->get_id());
        if (memory.last_side) return memory.last_side < 0;
        auto output = window->get_output();
        auto obstacles = placement_obstacles(output, window->get_id());
        auto left = side_region(output, true, rail), right = side_region(output, false, rail);
        double l = scottland::windowing::largest_opening(left, obstacles);
        double r = scottland::windowing::largest_opening(right, obstacles);
        // Within five percent of screen height, the risks are about equal: favor nearby.
        if (std::abs(l - r) <= output->get_relative_geometry().height * 0.05)
            return current.x < output->get_relative_geometry().width / 2.0;
        return l > r;
    }
    wf::pointf_t zone_spot(wayfire_toplevel_view window, scottland::windowing::zone z,
        scottland::windowing::point current, wayfire_toplevel_view footprint = nullptr,
        wf::output_t *destination_output = nullptr)
    {
        using Z = scottland::windowing::zone;
        auto output = destination_output ? destination_output : window->get_output();
        auto screen = output->get_relative_geometry();
        auto a = output->workarea->get_workarea(); auto g = window->get_geometry();
        auto& memory = ensure_window_memory(window->get_id());
        std::optional<scottland::windowing::point> remembered;
        if (auto p = memory.positions[size_t(z)]) remembered = {p->x * screen.width, p->y * screen.height};
        double w = g.width, h = g.height;
        scottland::windowing::rectangle region{double(a.x), double(a.y), double(a.width), double(a.height)};
        if (z == Z::center)
        {
            // The zone constrains the window's CENTER; content stays full size, even if wider.
            double edge = screen.width * (1 - std::clamp(double(center_width) / 100, 0.0, 1.0)) / 2;
            double lo = std::max(edge, a.x + std::min(w, double(a.width)) / 2);
            double hi = std::min(screen.width - edge, a.x + a.width - std::min(w, double(a.width)) / 2);
            if (hi < lo) lo = hi = screen.width / 2.0;
            region.x = lo - w / 2; region.width = hi - lo + w;
        } else
        {
            bool rail = z == Z::left_rail || z == Z::right_rail;
            bool left = z == Z::left_rail || z == Z::left_periphery;
            auto side = side_region(output, left, rail);
            if (rail)
            {
                // Provisional footprint; refine on adoption with the actual widget size.
                auto card = footprint ? footprint->toplevel()->pending().geometry : wf::geometry_t{0, 0, 320, 90};
                w = card.width; h = card.height;
                double cx = left ? WIDGET_INSET + w / 2 : screen.width - WIDGET_INSET - w / 2;
                region.x = cx - w / 2; region.width = w;
                region.y += WIDGET_INSET; region.height = std::max(h, region.height - 2 * WIDGET_INSET);
            } else
            {
                // The whole side zone is eligible, not a fixed landing column. Re-evaluate
                // the rectangle footprint as its natural zone scale changes at the chosen x.
                // All contention decisions still go through the same pure placement routine.
                auto obstacles = placement_obstacles(output, window->get_id());
                double x = std::clamp(current.x, side.x, side.x + side.width);
                scottland::windowing::point spot;
                for (int iteration = 0; iteration < 16; ++iteration)
                {
                    double scale = place_at(x, screen.width).scale;
                    w = g.width * scale; h = g.height * scale;
                    region.x = side.x - w / 2; region.width = side.width + w;
                    spot = scottland::windowing::place_rectangle(w, h, region, obstacles, current, remembered);
                    if (std::abs(spot.x - x) < 0.01) break;
                    x = spot.x;
                }
                return {spot.x, spot.y};
            }
        }
        auto spot = scottland::windowing::place_rectangle(w, h, region,
            placement_obstacles(output, window->get_id()), current, remembered);
        return {spot.x, spot.y};
    }
    void cycle_window(uint64_t id, scottland::windowing::destination destination)
    {
        using Z = scottland::windowing::zone; using D = scottland::windowing::destination;
        if (cycle_waiting)
        { deferred_moves.emplace_back(id, destination); return; }
        auto window = wf::toplevel_cast(view_by_id(id)); auto visible = represented_view(id);
        if (!window || !window->get_output()) return;
        auto g = (visible ? visible : window)->get_geometry();
        scottland::windowing::point current{g.x + g.width / 2.0, g.y + g.height / 2.0};
        if (visible) remember_window(visible);
        if (destination == D::center && link_of_window(window)) { open_widget(*link_of_window(window)); return; }
        // Fullscreen geometry is owned by its transaction: selection works, cycling waits until
        // the requested exit commits, then proceeds with the restored size.
        if (window->pending_fullscreen())
        {
            cycle_waiting = true;
            deferred_moves.emplace_back(id, destination);
            wf::get_core().default_wm->fullscreen_request(window, window->get_output(), false);
            deferred_cycle.set_timeout(100, [=] () {
                // Drain from idle, after the one-shot timer has disconnected. A later queued
                // window may itself need a fullscreen exit and a new timer on this same object.
                deferred_ready.run_once([=] () {
                    cycle_waiting = false;
                    auto moves = std::move(deferred_moves); deferred_moves.clear();
                    for (auto [window, to] : moves) cycle_window(window, to);
                });
            }); return;
        }
        bool rail = destination == D::widget;
        bool left = destination != D::center && placement_side(window, current, rail);
        Z z = destination == D::center ? Z::center : rail ?
            (left ? Z::left_rail : Z::right_rail) : (left ? Z::left_periphery : Z::right_periphery);
        auto at = zone_spot(window, z, current); auto real = window->get_geometry();
        pin_scale(window, std::nullopt); // explicit zone cycling follows the zone, including center at 100%
        move_window(window, std::round(at.x - real.width / 2.0), std::round(at.y - real.height / 2.0));
        if (rail)
        {
            model.windows[id].pending_rail = current;
            widgetize(window, false, left ? "left" : "right");
            if (!link_of_window(window)) model.windows[id].pending_rail.reset();
            auto& memory = ensure_window_memory(id);
            memory.last_side = left ? -1 : 1;
            publish_model();
        } else { remember_window(window); start_glide(window, current.x - at.x, current.y - at.y); }
        declutter_signature.clear();
    }
    bool place_cycled_widget(wayfire_toplevel_view widget, uint64_t id, const std::string& rail)
    {
        auto found = model.windows.find(id);
        if (found == model.windows.end() || !found->second.pending_rail) return false;
        auto current = *found->second.pending_rail;
        auto window = wf::toplevel_cast(found->second.view.lock());
        if (!window) { found->second.pending_rail.reset(); publish_model(); return false; }
        using Z = scottland::windowing::zone;
        auto at = zone_spot(window, rail == "left" ? Z::left_rail : Z::right_rail, current, widget);
        found->second.pending_rail.reset();
        if (auto link = link_of_widget(widget)) link->drop = at;
        // The caller places drop and gravity in one pending transaction (WG4/WG16).
        return true;
    }
    wf::wl_timer<false> deferred_cycle;
    wf::wl_idle_call deferred_ready;
    bool cycle_waiting = false;
    std::vector<std::pair<uint64_t, scottland::windowing::destination>> deferred_moves;

    bool step_hints()
    {
        auto entries = window_entries();
        if (window_keys.active) window_keys.refresh(entries);
        std::set<uint64_t> represented;
        std::map<wf::output_t*, std::vector<uint64_t>> by_output;
        std::ostringstream signature;
        for (auto e : entries)
        {
            auto view = represented_view(e.id);
            if (!view || !view->is_mapped() || !view->get_output()) continue;
            represented.insert(e.id);
            if (!hint_visuals.count(e.id) && window_keys.active)
            {
                hint_visual visual; visual.view = view->weak_from_this();
                visual.offset = std::make_shared<wf::scene::view_2d_transformer_t>(view);
                view->get_transformed_node()->add_transformer(visual.offset, wf::TRANSFORMER_HIGHLEVEL - 1,
                    "scottland-hint-offset"); hint_visuals[e.id] = std::move(visual);
            }
            auto found = hint_visuals.find(e.id);
            if (found == hint_visuals.end()) continue;
            auto& visual = found->second;
            if (visual.view.lock().get() != view.get())
            {
                if (auto old = visual.view.lock()) old->get_transformed_node()->rem_transformer("scottland-hint-offset");
                if (visual.hint) wf::scene::remove_child(visual.hint);
                visual.hint.reset(); visual.view = view->weak_from_this();
                visual.offset = std::make_shared<wf::scene::view_2d_transformer_t>(view);
                view->get_transformed_node()->add_transformer(visual.offset, wf::TRANSFORMER_HIGHLEVEL - 1,
                    "scottland-hint-offset");
            }
            auto g = view->get_geometry();
            signature << e.id << ':' << g.x << ',' << g.y << ',' << g.width << ',' << g.height << ',' << view->get_output()->to_string() << ';';
            by_output[view->get_output()].push_back(e.id);
        }
        if (window_keys.active && signature.str() != declutter_signature)
        {
            declutter_signature = signature.str();
            for (auto& [output, ids] : by_output)
            {
                std::vector<scottland::windowing::point> anchors;
                for (auto id : ids) { auto g = represented_view(id)->get_geometry();
                    anchors.push_back({g.x + g.width / 2.0, g.y + g.height / 2.0}); }
                auto screen = output->get_relative_geometry();
                auto displaced = scottland::windowing::declutter(anchors,
                    {40, 40, std::max(0.0, screen.width - 80), std::max(0.0, screen.height - 80)});
                for (size_t i = 0; i < ids.size(); ++i)
                    hint_visuals[ids[i]].target = {displaced[i].x - anchors[i].x, displaced[i].y - anchors[i].y};
            }
        }
        bool moving = false;
        for (auto it = hint_visuals.begin(); it != hint_visuals.end();)
        {
            auto& visual = it->second; auto view = wf::toplevel_cast(visual.view.lock());
            if (!view || !represented.count(it->first))
            {
                if (view) view->get_transformed_node()->rem_transformer("scottland-hint-offset");
                if (visual.hint) wf::scene::remove_child(visual.hint);
                it = hint_visuals.erase(it); continue;
            }
            auto offset = visual.offset;
            auto target = window_keys.active ? visual.target : scottland::windowing::point{};
            view->damage();
            view->get_transformed_node()->begin_transform_update();
            offset->translation_x += (target.x - offset->translation_x) * 0.18;
            offset->translation_y += (target.y - offset->translation_y) * 0.18;
            bool unsettled = std::hypot(target.x - offset->translation_x, target.y - offset->translation_y) > 0.1;
            moving |= unsettled;
            if (!unsettled) { offset->translation_x = target.x; offset->translation_y = target.y; }
            view->get_transformed_node()->end_transform_update();
            view->damage();
            if (window_keys.active)
            {
                if (visual.hint && visual.hint_output != view->get_output())
                { wf::scene::remove_child(visual.hint); visual.hint.reset(); }
                if (!visual.hint)
                {
                    visual.hint_output = view->get_output();
                    visual.hint = std::make_shared<scottland::windowing::hint_node>();
                    wf::scene::add_front(view->get_output()->node_for_layer(wf::scene::layer::OVERLAY), visual.hint);
                }
                auto g = view->get_geometry(); double x = g.x + g.width / 2.0, y = g.y + g.height / 2.0;
                if (auto frame = frame_of(view, false)) { auto r = frame->screen_rect(); x = (r.x1 + r.x2) / 2; y = (r.y1 + r.y2) / 2; }
                auto text = upper(window_keys.label(ensure_window_memory(it->first).hint_slot));
                auto accent = scottland::palette.accent;
                visual.hint->update(x + offset->translation_x, y + offset->translation_y, text,
                    window_keys.selected == it->first, scottland::palette.light, accent.r, accent.g, accent.b,
                    view->get_output()->get_scale());
            } else
            {
                if (visual.hint) { wf::scene::remove_child(visual.hint); visual.hint.reset(); }
                if (!unsettled) { view->get_transformed_node()->rem_transformer("scottland-hint-offset");
                    it = hint_visuals.erase(it); continue; }
            }
            ++it;
        }
        return window_keys.active || moving;
    }
    void end_window_keys()
    {
        window_keys.end(); declutter_signature.clear();
        for (auto& [id, visual] : hint_visuals)
            if (visual.hint) { wf::scene::remove_child(visual.hint); visual.hint.reset(); }
        for (auto& [id, link] : model.widgets)
            if (link.docked() && in_focus_mode(link.output)) slide_widget(link, true);
    }
    void begin_window_keys()
    {
        if (alt_bypassed || alt_keys.empty() || held_keys.size() != 1 || drag->view) return;
        capture_chord = true;
        auto active = wf::get_core().seat->get_active_view();
        auto link = link_of_widget(active);
        window_keys.begin(window_entries(), link ? link->window_id : active ? active->get_id() : 0);
        for (auto& [id, widget] : model.widgets)
            if (widget.docked() && in_focus_mode(widget.output)) slide_widget(widget, false);
        declutter_signature.clear(); step_hints();
        hints_tick.set_timeout(8, [=] () { return step_hints(); });
    }
    wf::signal::connection_t<wf::input_event_signal<wlr_keyboard_key_event>> on_window_key =
        [=] (wf::input_event_signal<wlr_keyboard_key_event> *ev)
    {
        auto code = ev->event->keycode;
        bool down = ev->event->state == WL_KEYBOARD_KEY_STATE_PRESSED;
        bool alt = code == KEY_LEFTALT || code == KEY_RIGHTALT;
        auto keyboard = wlr_seat_get_keyboard(wf::get_core().get_current_seat());
        if (!keyboard || !keyboard->keymap) return;
        if (down) held_keys.insert(code); else held_keys.erase(code);
        if (!down && swallowed_keys.erase(code))
        {
            ev->mode = wf::input_event_processing_mode_t::IGNORE;
            return; // finish our own pair; this is not a new compositor grab
        }
        bool claimed = key_layers.handles(ev);
        if (claimed && down) { alt_bypassed = true; alt_hold.disconnect(); }
        if (ev->mode == wf::input_event_processing_mode_t::IGNORE)
        {
            if (alt) { if (down) alt_keys.insert(code); else alt_keys.erase(code); }
            bypass_window_keys();
            return;
        }
        if (alt)
        {
            if (down && alt_keys.empty())
            {
                uint32_t blockers = modifier_mask(keyboard->keymap, "CTRL SHIFT SUPER");
                alt_bypassed = claimed || drag->view || held_keys.size() != 1 || (keyboard->modifiers.depressed & blockers);
                if (!alt_bypassed)
                    alt_hold.set_timeout(std::max(1, int(alt_hold_delay)), [=] () { begin_window_keys(); });
            }
            if (down) alt_keys.insert(code); else alt_keys.erase(code);
            if (!capture_chord && held_keys.size() > 1) { alt_bypassed = true; alt_hold.disconnect(); }
            if (alt_keys.empty())
            {
                alt_hold.disconnect();
                if (capture_chord) end_window_keys();
                capture_chord = false;
            }
            // Alt-down was delivered immediately, so its matching release also belongs to the
            // app. No synthetic replay, delayed accelerator, or stuck modifier for quick chords.
            return;
        }
        if (claimed) return; // exact focused-surface claims also bleed through active hints (KL7)
        if (!capture_chord)
        {
            if (down && !alt_keys.empty()) { alt_bypassed = true; alt_hold.disconnect(); }
            return;
        }
        ev->mode = wf::input_event_processing_mode_t::IGNORE;
        if (!down || !swallowed_keys.insert(code).second) return;
        if (!window_keys.active) return; // Esc cancels, but this whole Alt chord remains ours.
        if (code == KEY_ESC) end_window_keys();
        else if (code == KEY_TAB)
            window_keys.tab(held_keys.count(KEY_LEFTSHIFT) || held_keys.count(KEY_RIGHTSHIFT));
        else if (code == KEY_F4) window_keys.close_selected();
        else
        {
            const xkb_keysym_t *syms = nullptr;
            auto layout = xkb_state_key_get_layout(keyboard->xkb_state, code + 8);
            if (xkb_keymap_key_get_syms_by_level(keyboard->keymap, code + 8, layout, 0, &syms) == 1 &&
                syms[0] >= XKB_KEY_a && syms[0] <= XKB_KEY_z)
                window_keys.letter(char('a' + syms[0] - XKB_KEY_a));
        }
    };
    wf::ipc::method_callback hints_state = [=] (wf::json_t) -> wf::json_t
    {
        auto reply = wf::ipc::json_ok(); reply["active"] = window_keys.active;
        reply["selected"] = int64_t(window_keys.selected); reply["hints"] = wf::json_t::array();
        window_keys.refresh(window_entries());
        for (auto e : window_keys.entries)
        {
            wf::json_t item; item["window"] = int64_t(e.id); item["hint"] = window_keys.label(e.slot);
            item["visible"] = window_keys.active && hint_visuals.count(e.id) && bool(hint_visuals[e.id].hint);
            auto visible = represented_view(e.id);
            if (visible) { auto g = visible->get_geometry(); item["x"] = g.x; item["y"] = g.y; }
            item["dx"] = hint_visuals.count(e.id) ? double(hint_visuals[e.id].offset->translation_x) : 0.0;
            item["dy"] = hint_visuals.count(e.id) ? double(hint_visuals[e.id].offset->translation_y) : 0.0;
            item["memories"] = wf::json_t::array();
            for (auto p : ensure_window_memory(e.id).positions)
            { wf::json_t spot; spot["set"] = bool(p); if (p) { spot["x"] = p->x; spot["y"] = p->y; } item["memories"].append(spot); }
            reply["hints"].append(item);
        }
        return reply;
    };
    void init_window_keys()
    {
        // Upgrade from the pre-model Alt branch: read its handover once, never write it again.
        auto path = runtime_file(".window-positions.json"); std::ifstream in(path);
        if (in)
        {
            wf::json_t records; std::string contents((std::istreambuf_iterator<char>(in)), {});
            if (!wf::json_t::parse_string(contents, records) && records.is_array())
                for (size_t i = 0; i < records.size(); ++i)
                {
                    auto r = records[i]; auto found = model.windows.find(uint64_t(r["window"].as_int64()));
                    if (found == model.windows.end() || found->second.placement) continue;
                    found->second.placement = read_memory(r);
                    model.hint_width = std::max(model.hint_width,
                        unsigned(std::clamp(r["hint_width"].as_int(), 1, 7)));
                }
            in.close(); std::remove(path.c_str());
        }
        window_entries();
        window_keys.select = [=] (uint64_t id, bool restore) {
            auto view = wf::toplevel_cast(view_by_id(id));
            if (restore && link_of_window(view)) open_widget(*link_of_window(view));
            else if (auto visible = represented_view(id)) wf::get_core().default_wm->focus_raise_view(visible);
        };
        window_keys.move = [=] (uint64_t id, auto to) { cycle_window(id, to); };
        window_keys.close = [=] (uint64_t id) { auto view = wf::toplevel_cast(view_by_id(id));
            if (auto link = link_of_window(view)) close_linked(*link); else if (view) view->close(); };
        wf::get_core().connect(&on_window_key);
        ipc_repo->register_method("scottland/hints", hints_state);
    }
    void fini_window_keys()
    {
        on_window_key.disconnect(); alt_hold.disconnect(); hints_tick.disconnect(); deferred_cycle.disconnect();
        hint_registration.disconnect(); deferred_ready.disconnect();
        window_keys.end();
        ipc_repo->unregister_method("scottland/hints");
        for (auto& [id, visual] : hint_visuals)
        {
            if (visual.hint) wf::scene::remove_child(visual.hint);
            if (auto view = visual.view.lock()) view->get_transformed_node()->rem_transformer("scottland-hint-offset");
        }
        hint_visuals.clear();
    }
