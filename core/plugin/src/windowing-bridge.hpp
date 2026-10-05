// Private integration fragment for scottland_plugin_t. The pure controller, placement, solver,
// renderer live separately; this bridge reads and updates the plugin-owned desktop model.
    scottland::windowing::alt_mode window_keys;
    wf::option_wrapper_t<int> alt_hold_delay{"scottland/alt_hold_delay"};
    wf::option_wrapper_t<int> window_double_tap_delay{"scottland/window_double_tap_delay"};
    wf::option_wrapper_t<int> window_hold_delay{"scottland/window_hold_delay"};
    wf::wl_timer<false> hint_hold;
    wf::wl_idle_call hint_hold_retry;
    wf::option_wrapper_t<wf::keybinding_t> center_switcher_next{"scottland/center_switcher_next"};
    wf::option_wrapper_t<wf::keybinding_t> center_switcher_previous{"scottland/center_switcher_previous"};
    // WK40: Super+arrows by default.
    wf::option_wrapper_t<wf::keybinding_t> navigate_left{"scottland/navigate_left"};
    wf::option_wrapper_t<wf::keybinding_t> navigate_right{"scottland/navigate_right"};
    wf::option_wrapper_t<wf::keybinding_t> navigate_up{"scottland/navigate_up"};
    wf::option_wrapper_t<wf::keybinding_t> navigate_down{"scottland/navigate_down"};
    std::set<uint32_t> swallowed_keys, alt_keys, held_keys;
    // Return opens a focused widget once per physical press, before the widget or its key layer
    // can receive it. Keep its release from reaching the app window that replaced the widget.
    std::set<std::pair<wlr_input_device*, uint32_t>> widget_return_keys;
    wf::wl_timer<false> alt_hold;
    bool capture_chord = false;
    bool alt_bypassed = false;
    wf::wl_timer<true> hints_tick;
    std::vector<uint64_t> focus_recency; // all app windows; center eligibility is checked at use
    struct center_switcher_t
    {
        bool active = false;
        std::vector<uint64_t> candidates;
        size_t selected = 0;
        std::shared_ptr<scottland::windowing::center_switcher_node> preview;
        wf::output_t *output = nullptr;
    } center_switcher;
    struct hint_flash_t
    {
        std::shared_ptr<scottland::windowing::hint_flash_node> node;
        wf::output_t *output = nullptr;
        std::chrono::steady_clock::time_point started;
        scottland::windowing::hint_rgb color{};
    };
    std::map<uint64_t, hint_flash_t> hint_flashes;
    wf::wl_timer<true> hint_flash_tick;
    struct hint_visual
    {
        std::weak_ptr<wf::view_interface_t> view;
        std::shared_ptr<scottland::windowing::hint_node> hint;
        wf::output_t *hint_output = nullptr;
        std::shared_ptr<scottland::windowing::fullscreen_hint_node> fullscreen_tint;
        std::shared_ptr<scottland::windowing::hint_outline_node> outline; // WK37
        wf::output_t *outline_output = nullptr;
        double visible_fraction = 1; // of its on-screen area at the settled avoidance layout
        std::shared_ptr<wf::scene::view_2d_transformer_t> offset;
        bool offset_attached = false;
        scottland::windowing::point target;
        scottland::windowing::point label_offset;
        double label_size = 72, clearance = 0;
        bool edge_label = false;
        // WK13 telemetry: how the last pass decided this window.
        scottland::windowing::peek_outcome outcome = scottland::windowing::peek_outcome::pending;
        scottland::windowing::peek_rung rung = scottland::windowing::peek_rung::none;
        char rule = '-';
        scottland::windowing::rectangle room{0, 0, 0, 0};
        std::chrono::steady_clock::time_point last_offset_step{};
    };
    std::map<uint64_t, hint_visual> hint_visuals; // by represented application id
    std::string declutter_signature;
    // Hint draw order needs recomputing: stacking changed or a hint node was (re)added.
    bool hint_order_dirty = true;
    std::chrono::steady_clock::time_point last_exposure_solve{};
    double exposure_solve_ms = 0;
    double exposure_solve_max_ms = 0;
    double exposure_search_ms = 0;
    double exposure_search_max_ms = 0;
    double exposure_easing_speed_max = 0;
    uint64_t exposure_solve_count = 0;
    uint64_t exposure_solve_deadline_count = 0;
    bool exposure_solve_pending = false;
    uint64_t hint_step_count = 0;
    bool hint_step_animation = false, hint_step_offset = false;
    // WK13: one resumable peek pass per output. `newer`: the layout changed while it ran; the
    // next pass starts from the newest layout as soon as this one completes.
    struct peek_output_t
    {
        std::optional<scottland::windowing::peek_pass> pass;
        std::string order;
        std::vector<uint64_t> ids;
        bool newer = false;
    };
    std::map<std::string, peek_output_t> peek_by_output;
    uint64_t avoidance_pass_count = 0;
    size_t avoidance_pass_units = 0, avoidance_pass_slices = 0, avoidance_pass_slices_max = 0;
    size_t avoidance_slice_units = 0, avoidance_slice_units_max = 0;
    // WK37 occlusion pass, resumable per output like the solve it follows: the next window (in
    // front-to-back order) still to measure; absent means start over, SIZE_MAX means done.
    std::map<std::string, size_t> occlusion_next_by_output;
    double occlusion_pass_ms = 0, occlusion_pass_max_ms = 0;
    uint64_t occlusion_deferrals = 0;
    int hint_palette_watch_fd = -1;
    wl_event_source *hint_palette_watch = nullptr;
    std::string hint_palette_watch_name;
    wf::wl_idle_call hint_registration;

    scottland::windowing::hint_palette hints_palette;
    std::chrono::steady_clock::time_point palette_read;
    bool hints_reduced_motion = false;
    std::map<unsigned, scottland::windowing::hint_rgb> hint_colors;
    scottland::windowing::hint_rgb color_for_hint(unsigned slot)
    {
        auto [it, inserted] = hint_colors.try_emplace(slot);
        if (inserted) it->second = scottland::windowing::hint_color(slot, hints_palette);
        return it->second;
    }
    void refresh_hint_palette()
    {
        auto now = std::chrono::steady_clock::now();
        if (now - palette_read < std::chrono::milliseconds(250)) return;
        palette_read = now;
        hint_colors.clear();
        hints_reduced_motion = false;
        hints_palette.light = scottland::palette.light;
        hints_palette.text_scale = 1.0; hints_palette.font_family = "sans-serif";  // unless the file says
        hints_palette.accent = {scottland::palette.accent.r, scottland::palette.accent.g,
            scottland::palette.accent.b};
        hints_palette.background = hints_palette.light ? scottland::windowing::hint_rgb{0.957, 0.961, 0.969} :
            scottland::windowing::hint_rgb{0.122, 0.137, 0.173};
        hints_palette.foreground = hints_palette.light ? scottland::windowing::hint_rgb{0.137, 0.165, 0.208} :
            scottland::windowing::hint_rgb{0.847, 0.871, 0.914};
        const char *override_path = std::getenv("SCOTTLAND_PALETTE");
        std::ifstream in(override_path ? override_path : runtime_file(".palette.json"));
        if (!in) return;
        wf::json_t colors; std::string contents((std::istreambuf_iterator<char>(in)), {});
        if (wf::json_t::parse_string(contents, colors) || !colors.is_object()) return;
        hints_reduced_motion = colors.has_member("reduced_motion") && colors["reduced_motion"].as_bool();
        if (colors["scheme"].as_string() == "light") hints_palette.light = true;
        else if (colors["scheme"].as_string() == "dark") hints_palette.light = false;
        auto read = [&] (const char *name, scottland::windowing::hint_rgb& color) {
            auto value = colors[name].as_string();
            if (value.size() != 7 || value[0] != '#' ||
                value.find_first_not_of("0123456789abcdefABCDEF", 1) != std::string::npos) return;
            unsigned rgb = std::stoul(value.substr(1), nullptr, 16);
            color = {double((rgb >> 16) & 255) / 255, double((rgb >> 8) & 255) / 255, double(rgb & 255) / 255};
        };
        read("background", hints_palette.background); read("foreground", hints_palette.foreground);
        read("accent", hints_palette.accent);
        if (colors.has_member("text_scale") && (colors["text_scale"].is_double() || colors["text_scale"].is_int()))
            hints_palette.text_scale = std::clamp(colors["text_scale"].is_double() ? colors["text_scale"].as_double() : double(colors["text_scale"].as_int()), 0.5, 3.0);
        if (colors.has_member("font_family") && colors["font_family"].is_string() &&
            !colors["font_family"].as_string().empty())
            hints_palette.font_family = colors["font_family"].as_string();
    }
    void init_hint_palette_watch()
    {
        // Settled Window mode no longer has a frame tick. Wake the existing palette and hint
        // refresh path only when its atomic-replaced runtime file changes.
        if (getenv("SCOTTLAND_PALETTE")) return;
        auto path = runtime_file(".palette.json");
        auto slash = path.rfind('/');
        if (slash == std::string::npos) return;
        auto directory = path.substr(0, slash);
        hint_palette_watch_name = path.substr(slash + 1);
        hint_palette_watch_fd = inotify_init1(IN_NONBLOCK | IN_CLOEXEC);
        if (hint_palette_watch_fd < 0) return;
        if (inotify_add_watch(hint_palette_watch_fd, directory.c_str(),
            IN_CLOSE_WRITE | IN_MOVED_TO | IN_CREATE | IN_DELETE) < 0)
        {
            close(hint_palette_watch_fd);
            hint_palette_watch_fd = -1;
            return;
        }
        hint_palette_watch = wl_event_loop_add_fd(wf::get_core().ev_loop, hint_palette_watch_fd,
            WL_EVENT_READABLE, [] (int, uint32_t mask, void *data)
        {
            auto self = static_cast<scottland_plugin_t*>(data);
            if (mask & (WL_EVENT_HANGUP | WL_EVENT_ERROR))
            {
                self->fini_hint_palette_watch();
                return 0;
            }
            char buffer[4096];
            bool changed = false;
            ssize_t size;
            while ((size = read(self->hint_palette_watch_fd, buffer, sizeof(buffer))) > 0)
            {
                for (size_t offset = 0; offset < static_cast<size_t>(size);)
                {
                    auto event = reinterpret_cast<const inotify_event*>(buffer + offset);
                    if (event->len && self->hint_palette_watch_name == event->name &&
                        (event->mask & (IN_CLOSE_WRITE | IN_MOVED_TO | IN_CREATE | IN_DELETE)))
                        changed = true;
                    offset += sizeof(inotify_event) + event->len;
                }
            }
            if (changed)
            {
                self->palette_read = {};
                if (self->window_keys.active || self->window_avoidance_always ||
                    self->hint_avoidance_always)
                    self->refresh_layout_avoidance();
            }
            return 0;
        }, this);
        if (!hint_palette_watch)
        {
            close(hint_palette_watch_fd);
            hint_palette_watch_fd = -1;
        }
    }
    void fini_hint_palette_watch()
    {
        if (hint_palette_watch) wl_event_source_remove(hint_palette_watch);
        hint_palette_watch = nullptr;
        if (hint_palette_watch_fd >= 0) close(hint_palette_watch_fd);
        hint_palette_watch_fd = -1;
    }
    scottland::rectf_t hint_rectangle(wayfire_toplevel_view view, bool for_avoidance_solve = false)
    {
        if (drag->view == view && view->get_output())
        {
            auto r = scene_rectangle(view, view->get_output());
            // Follow the user's live drag, but never feed the avoidance displacement back
            // into its own input. The scene rectangle includes this attached translation.
            if (for_avoidance_solve)
            {
                auto found = hint_visuals.find(view->get_id());
                if (found != hint_visuals.end() && found->second.offset_attached)
                {
                    r.x1 -= found->second.offset->translation_x;
                    r.x2 -= found->second.offset->translation_x;
                    r.y1 -= found->second.offset->translation_y;
                    r.y2 -= found->second.offset->translation_y;
                }
            }
            return r;
        }
        if (auto frame = frame_of(view, false))
        {
            auto r = frame->screen_rect();
            // A rail make-room ease (WG26) runs for a few hundred ms. Avoidance plans against
            // where those widgets are going, so it re-solves when that target changes rather
            // than on every frame of the ease.
            if (for_avoidance_solve)
            {
                auto remaining = rail_layout_remaining(view);
                r.x1 += remaining.x; r.x2 += remaining.x;
                r.y1 += remaining.y; r.y2 += remaining.y;
            }
            return r;
        }
        auto g = view->get_geometry();
        return {double(g.x), double(g.y), double(g.x + g.width), double(g.y + g.height)};
    }
    // Peek-strip decision 9: a grabbed window stays exactly where it is drawn, and that becomes
    // its real position. Fold its avoidance offset into its true geometry, so window, goo, halo
    // and input agree for the whole drag. The drag keeps the grabbed point under the input from
    // the drawn box, so removing the offset moves nothing on screen. P13: the commit never takes
    // it into another zone (a peek that hangs its center past the zone edge commits to the edge).
    void commit_grab_offset(wayfire_toplevel_view view)
    {
        auto found = hint_visuals.find(view->get_id());
        if (found == hint_visuals.end() || !found->second.offset_attached || !view->get_output() ||
            link_of_widget(view)) return;
        auto& visual = found->second;
        double dx = visual.offset->translation_x, dy = visual.offset->translation_y;
        if (std::abs(dx) < .5 && std::abs(dy) < .5) return;
        auto g = placed_geometry(view);
        double width = view->get_output()->get_relative_geometry().width;
        auto zone_at = [&] (double shift) {
            double x = g.x + g.width / 2.0 + shift; auto z = place_at(x, width).zone;
            return z == zone_t::center ? 0 : x < width / 2 ? 1 : 2;
        };
        if (zone_at(dx) != zone_at(0))
        {
            double inside = 0, outside = dx;
            for (int i = 0; i < 24; i++)
            {
                double mid = (inside + outside) / 2;
                (zone_at(mid) == zone_at(0) ? inside : outside) = mid;
            }
            dx = inside;
        }
        LOGI("scottland: grab commits window ", view->get_id(), "'s avoidance offset ", dx, ",", dy,
            " into its position");
        view->damage(); view->get_transformed_node()->begin_transform_update();
        visual.offset->translation_x = visual.offset->translation_y = 0;
        view->get_transformed_node()->end_transform_update(); view->damage();
        visual.target = {};
        // A pass in flight holds this window's old offset: start over from the committed geometry.
        peek_by_output.erase(view->get_output()->to_string());
        move_window(view, std::round(g.x + dx), std::round(g.y + dy));
    }
    double hint_size(wayfire_toplevel_view view)
    {
        // WK31 retains WK30's default widget circle at one consistent size, regardless
        // of the widget client's dimensions or expanded/collapsed presentation.
        if (link_of_widget(view)) return 48 * hints_palette.text_scale;
        auto r = hint_rectangle(view);
        return scottland::windowing::hint_badge_size(r.width(), r.height(), hints_palette.text_scale);
    }
    scottland::windowing::point hint_anchor(wayfire_toplevel_view view, bool for_avoidance_solve = false)
    {
        auto r = hint_rectangle(view, for_avoidance_solve);
        double x = (r.x1 + r.x2) / 2;
        if (auto link = link_of_widget(view))
        {
            double diameter = std::round(hint_size(view));
            // WK26: attach to the center-facing edge; retain space at the count corner.
            double outside = diameter / 2 - scottland::windowing::widget_hint_overlap(diameter, r.height());
            x = link->rail == "left" ? r.x2 + outside : r.x1 - outside;
        }
        return {x, (r.y1 + r.y2) / 2};
    }
    double window_mode_tint_strength()
    {
        return std::clamp(double(window_mode_tint), 0.0, 100.0) / 100;
    }
    void remove_hint_outline(hint_visual& visual)
    {
        if (visual.outline) wf::scene::remove_child(visual.outline);
        visual.outline.reset(); visual.outline_output = nullptr;
    }
    void clear_hint_dye(wf::view_interface_t *view)
    {
        if (auto frame = frame_of(wf::toplevel_cast(view), false)) frame->set_hint_dye(std::nullopt);
    }

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
    // A side spot reads as lower priority than the center once its scale has visibly fallen: by 5%,
    // or halfway to the rail's scale when the curve has less range than that (WP4, WP8).
    double periphery_threshold(double screen_width)
    {
        double rail = screen_width * std::clamp(double(rail_width) / 100, 0.0, 0.25);
        double outer_scale = place_at(rail + 1, screen_width).scale;
        return 1 - std::min(0.05, std::max(0.0, (1 - outer_scale) / 2));
    }
    // Whether a spot at x reads as zone z (WP8): the center inside the center zone, and just past its
    // edge (the softness band) while an unpinned window there still shows its zone's full-looking
    // scale; a periphery anywhere else in the side zone. A Shift pin makes a side spot a periphery
    // spot wherever it is: the user put it there at that scale, even at full size (L31). A pin
    // never applies inside the center zone (tenet 4). Rails are what they are.
    bool reads_as(scottland::windowing::zone z, double x, std::optional<double> pin, double screen_width)
    {
        using Z = scottland::windowing::zone;
        auto place = place_at(x, screen_width);
        bool center = place.zone == zone_t::center ||
            (place.zone == zone_t::continuous && !pin && place.scale > periphery_threshold(screen_width));
        if (z == Z::center) return center;
        if (z == Z::left_periphery || z == Z::right_periphery)
            return place.zone == zone_t::continuous && !center && ((x < screen_width / 2) == (z == Z::left_periphery));
        return true;
    }
    // The zone a window counts as for its zone memories and cycles (WP8): its zone, except that an
    // unpinned side window that still reads as full scale counts as the center, where the user sees it.
    scottland::windowing::zone memory_zone(wayfire_toplevel_view view)
    {
        using Z = scottland::windowing::zone;
        auto z = window_zone(view);
        if (z != Z::left_periphery && z != Z::right_periphery) return z;
        auto g = placed_geometry(view);
        auto found = model.windows.find(view->get_id());
        auto pin = found == model.windows.end() ? std::nullopt : found->second.pinned_scale;
        return reads_as(Z::center, g.x + g.width / 2.0, pin, view->get_output()->get_relative_geometry().width) ?
            Z::center : z;
    }
    wayfire_toplevel_view represented_view(uint64_t id)
    {
        auto window = wf::toplevel_cast(view_by_id(id));
        if (auto link = link_of_window(window); link && link->docked())
        {
            if (widget_transitions.count(id) && entering_widget(id)) return window;
            return wf::toplevel_cast(link->widget.lock());
        }
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
    static wf::json_t memory_spots(const scottland::windowing::window_memory& memory)
    {
        auto spots = wf::json_t::array();
        for (size_t z = 0; z < memory.positions.size(); ++z)
        {
            auto p = memory.positions[z];
            wf::json_t spot; spot["set"] = bool(p);
            if (p) { spot["x"] = p->x; spot["y"] = p->y; }
            if (p && memory.pins[z]) spot["pin"] = *memory.pins[z];
            spots.append(spot);
        }
        return spots;
    }
    static wf::json_t memory_snapshot(const scottland::windowing::window_memory& memory)
    {
        wf::json_t r; r["slot"] = int(memory.hint_slot); r["side"] = memory.last_side;
        r["positions"] = memory_spots(memory);
        return r;
    }
    static scottland::windowing::window_memory read_memory(wf::json_t r)
    {
        scottland::windowing::window_memory memory;
        memory.hint_slot = r["slot"].as_int();
        for (size_t z = 0; z < memory.positions.size(); ++z) if (r["positions"][z]["set"].as_bool())
        {
            auto spot = r["positions"][z];
            std::optional<double> pin;
            if (spot.has_member("pin") && (spot["pin"].is_double() || spot["pin"].is_int()))
                pin = spot["pin"].is_double() ? spot["pin"].as_double() : double(spot["pin"].as_int());
            scottland::windowing::remember_spot(memory, scottland::windowing::zone(z),
                {spot["x"].as_double(), spot["y"].as_double()}, pin);
        }
        // remember_spot tracks the side as it goes; the record's own side is authoritative.
        memory.last_side = r["side"].as_int();
        return memory;
    }
    #include "keyboard-motion.hpp"

    void bypass_window_keys()
    {
        alt_bypassed = true; alt_hold.disconnect();
        arrow_repeats.clear(); fullscreen_impulses.clear();
        if (capture_chord) { end_window_keys(); capture_chord = false; }
        if (center_switcher.active) end_center_switcher(false);
    }
    void remember_window(wayfire_toplevel_view view)
    {
        if (!view || !view->is_mapped() || !view->get_output() || view->pending_fullscreen()) return;
        auto link = link_of_widget(view);
        uint64_t id = link ? link->window_id : view->get_id();
        if (!model.windows.count(id)) return;
        auto z = memory_zone(view);
        auto g = placed_geometry(view); auto screen = view->get_output()->get_relative_geometry();
        // The pin goes with the spot (WP1): a later return to this zone restores both.
        scottland::windowing::remember_spot(ensure_window_memory(id), z, {
            (g.x + g.width / 2.0) / screen.width, (g.y + g.height / 2.0) / screen.height},
            model.windows[id].pinned_scale);
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
            entries.push_back({id, state.placement->hint_slot, memory_zone(view), link && link->docked()});
        }
        window_keys.hint_width = model.hint_width;
        window_keys.refresh(entries);
        model.hint_width = window_keys.hint_width;
        publish_model();
        return entries;
    }
    void record_focus_recency(wayfire_view active)
    {
        focus_recency.erase(std::remove_if(focus_recency.begin(), focus_recency.end(), [=] (auto known) {
            return !model.windows.count(known); }), focus_recency.end());
        auto link = link_of_widget(active);
        uint64_t id = link ? link->window_id : active ? active->get_id() : 0;
        if (!id || !model.windows.count(id)) return;
        focus_recency.erase(std::remove(focus_recency.begin(), focus_recency.end(), id), focus_recency.end());
        focus_recency.insert(focus_recency.begin(), id);
    }
    bool center_switcher_eligible(uint64_t id)
    {
        auto found = model.windows.find(id);
        if (found == model.windows.end()) return false;
        auto view = wf::toplevel_cast(found->second.view.lock());
        return view && view->is_mapped() && view->get_output() && view->role == wf::VIEW_ROLE_TOPLEVEL &&
            !is_widget(view) && !runs_as_widget(found->second.pid) &&
            window_zone(view) == scottland::windowing::zone::center;
    }
    std::vector<uint64_t> center_switcher_candidates()
    {
        std::vector<uint64_t> result;
        std::set<uint64_t> seen;
        for (auto id : focus_recency)
            if (center_switcher_eligible(id) && seen.insert(id).second) result.push_back(id);
        // A window adopted after a reload may never have sent this plugin a focus event.
        // Fill those gaps in newest-opening order after the known recency history.
        for (auto it = model.windows.rbegin(); it != model.windows.rend(); ++it)
            if (center_switcher_eligible(it->first) && seen.insert(it->first).second)
                result.push_back(it->first);
        return result;
    }
    void show_center_switcher_preview()
    {
        wf::output_t *output = wf::get_core().seat->get_active_output();
        std::string title;
        unsigned position = 0, count = center_switcher.candidates.size();
        if (count && center_switcher.selected < count)
        {
            auto id = center_switcher.candidates[center_switcher.selected];
            if (auto view = wf::toplevel_cast(view_by_id(id)))
            {
                output = view->get_output();
                title = view->get_title().empty() ? view->get_app_id() : view->get_title();
                position = center_switcher.selected + 1;
            }
        }
        if (!output) return;
        if (center_switcher.preview && center_switcher.output != output)
        {
            wf::scene::remove_child(center_switcher.preview);
            center_switcher.preview.reset();
        }
        if (!center_switcher.preview)
        {
            center_switcher.preview = std::make_shared<scottland::windowing::center_switcher_node>();
            wf::scene::add_front(output->node_for_layer(wf::scene::layer::OVERLAY), center_switcher.preview);
        }
        center_switcher.output = output;
        palette_read = {};
        refresh_hint_palette();
        center_switcher.preview->update(output->get_relative_geometry().width, title, position, count,
            hints_palette, output->get_scale());
    }
    void step_center_switcher(bool backwards)
    {
        if (!center_switcher.active)
        {
            center_switcher.active = true;
            center_switcher.candidates = center_switcher_candidates();
            auto active = wf::get_core().seat->get_active_view();
            auto link = link_of_widget(active);
            uint64_t current = link ? link->window_id : active ? active->get_id() : 0;
            auto& candidates = center_switcher.candidates;
            if (!candidates.empty())
            {
                auto it = std::find(candidates.begin(), candidates.end(), current);
                center_switcher.selected = it == candidates.end() ?
                    (backwards ? candidates.size() - 1 : 0) :
                    (size_t(it - candidates.begin()) + (backwards ? candidates.size() - 1 : 1)) %
                        candidates.size();
            }
        } else
        {
            auto& candidates = center_switcher.candidates;
            candidates.erase(std::remove_if(candidates.begin(), candidates.end(), [=] (auto id) {
                return !center_switcher_eligible(id); }), candidates.end());
            if (!candidates.empty())
                center_switcher.selected = (center_switcher.selected +
                    (backwards ? candidates.size() - 1 : 1)) % candidates.size();
        }
        show_center_switcher_preview();
    }
    void end_center_switcher(bool commit)
    {
        uint64_t selected = center_switcher.active && !center_switcher.candidates.empty() &&
            center_switcher.selected < center_switcher.candidates.size() ?
            center_switcher.candidates[center_switcher.selected] : 0;
        if (center_switcher.preview) wf::scene::remove_child(center_switcher.preview);
        center_switcher = {};
        if (commit && center_switcher_eligible(selected))
            if (auto view = wf::toplevel_cast(view_by_id(selected)))
                wf::get_core().default_wm->focus_raise_view(view);
    }
    // WK40: focus and raise the nearest window or widget in a direction, judged by where each is
    // drawn (zone scale, avoidance offsets), across screens in layout coordinates. The pointer is
    // untouched.
    void navigate_windows(scottland::windowing::nav_direction direction)
    {
        auto focused = wf::toplevel_cast(wf::get_core().seat->get_active_view());
        auto drawn = [=] (wayfire_toplevel_view view) {
            auto output = view->get_output();
            auto r = scene_rectangle(view, output);
            auto at = output->get_layout_geometry();
            return scottland::windowing::rectangle{r.x1 + at.x, r.y1 + at.y, r.width(), r.height()};
        };
        std::optional<scottland::windowing::rectangle> origin;
        if (focused && focused->get_output()) origin = drawn(focused);
        if (!origin || origin->width <= 0 || origin->height <= 0)
        {
            // Nothing focused (or only a panel or launcher): start from the active screen's center.
            auto output = wf::get_core().seat->get_active_output();
            if (!output) return;
            auto g = output->get_layout_geometry();
            origin = scottland::windowing::rectangle{g.x + g.width / 2.0, g.y + g.height / 2.0, 0, 0};
        }
        std::vector<scottland::windowing::nav_candidate> candidates;
        for (auto output : wf::get_core().output_layout->get_outputs())
            for (auto view : output->wset()->get_views(
                wf::WSET_MAPPED_ONLY | wf::WSET_EXCLUDE_MINIMIZED | wf::WSET_SORT_STACKING))
            {
                // Windows and widgets that are drawn somewhere (not a desktop-role surface, not
                // an app window whose widget stands in for it, not minimized).
                if (view == focused || view->role != wf::VIEW_ROLE_TOPLEVEL ||
                    !view->get_root_node()->is_enabled()) continue;
                auto r = drawn(view);
                if (r.width > 0 && r.height > 0) candidates.push_back({view->get_id(), r});
            }
        auto target = scottland::windowing::neighbor(*origin, direction, candidates);
        LOGI("scottland: navigate direction=", int(direction), " from=", focused ? focused->get_id() : 0,
            " candidates=", candidates.size(), " target=", target ? *target : 0);
        if (target) if (auto view = wf::toplevel_cast(view_by_id(*target)))
            wf::get_core().default_wm->focus_raise_view(view);
    }
    wf::key_callback on_navigate_left = [=] (const wf::keybinding_t&)
    { navigate_windows(scottland::windowing::nav_direction::left); return true; };
    wf::key_callback on_navigate_right = [=] (const wf::keybinding_t&)
    { navigate_windows(scottland::windowing::nav_direction::right); return true; };
    wf::key_callback on_navigate_up = [=] (const wf::keybinding_t&)
    { navigate_windows(scottland::windowing::nav_direction::up); return true; };
    wf::key_callback on_navigate_down = [=] (const wf::keybinding_t&)
    { navigate_windows(scottland::windowing::nav_direction::down); return true; };
    std::optional<bool> center_switcher_direction(uint32_t code, wlr_keyboard *keyboard)
    {
        uint32_t relevant = modifier_mask(keyboard->keymap, "CTRL SHIFT ALT SUPER");
        uint32_t held = keyboard->modifiers.depressed & relevant;
        auto previous = center_switcher_previous.value(), next = center_switcher_next.value();
        if (code == previous.get_key() && held == (previous.get_modifiers() & relevant)) return true;
        if (code == next.get_key() && held == (next.get_modifiers() & relevant)) return false;
        return {};
    }
    bool step_hint_flashes()
    {
        auto now = std::chrono::steady_clock::now();
        for (auto it = hint_flashes.begin(); it != hint_flashes.end();)
        {
            auto& flash = it->second;
            auto view = represented_view(it->first);
            double age = std::chrono::duration<double>(now - flash.started).count();
            if (age >= .22 || !view || !view->is_mapped() || !view->get_output())
            {
                if (flash.node) wf::scene::remove_child(flash.node);
                it = hint_flashes.erase(it);
                continue;
            }
            auto output = view->get_output();
            if (flash.node && flash.output != output)
            { wf::scene::remove_child(flash.node); flash.node.reset(); }
            if (!flash.node)
            {
                flash.node = std::make_shared<scottland::windowing::hint_flash_node>();
                wf::scene::add_front(output->node_for_layer(wf::scene::layer::OVERLAY), flash.node);
            }
            flash.output = output;
            auto r = scene_rectangle(view, output);
            double radius = 0;
            if (auto frame = frame_of(view, false)) radius = frame->screen_radius();
            double strength = age < .035 ? .16 + .18 * age / .035 : .34 * (1 - (age - .035) / .185);
            flash.node->update({std::floor(r.x1), std::floor(r.y1), std::ceil(r.width()),
                std::ceil(r.height())}, radius, flash.color, strength);
            ++it;
        }
        return !hint_flashes.empty();
    }
    void flash_hint(uint64_t id)
    {
        auto found = model.windows.find(id);
        if (found == model.windows.end()) return;
        auto slot = ensure_window_memory(id).hint_slot;
        hint_flashes[id].started = std::chrono::steady_clock::now();
        hint_flashes[id].color = color_for_hint(slot);
        step_hint_flashes();
        if (!hint_flash_tick.is_connected())
            hint_flash_tick.set_timeout(16, [=] () { return step_hint_flashes(); });
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
    // Windows Scottland places keep off the screen's edges by the halo's width plus 5 pt, whenever
    // they fit (WP7); one bigger than the screen in a dimension isn't padded in that dimension.
    static constexpr double SCREEN_PADDING = scottland::HALO + 5;
    static wf::geometry_t padded(wf::geometry_t a, double w, double h)
    {
        int px = (w + 2 * SCREEN_PADDING <= a.width) ? int(std::ceil(SCREEN_PADDING)) : 0;
        int py = (h + 2 * SCREEN_PADDING <= a.height) ? int(std::ceil(SCREEN_PADDING)) : 0;
        return {a.x + px, a.y + py, a.width - 2 * px, a.height - 2 * py};
    }
    scottland::windowing::rectangle side_region(wf::output_t *output, bool left, bool rail)
    {
        auto screen = output->get_relative_geometry(); auto a = output->workarea->get_workarea();
        double edge = screen.width * std::clamp(double(rail_width) / 100, 0.0, 0.25);
        double center_edge = screen.width * (1 - std::clamp(double(center_width) / 100, 0.0, 1.0)) / 2;
        double lo = rail ? 0 : edge + 1, hi = rail ? edge : center_edge - 1;
        if (hi < lo) hi = lo;
        if (!rail)  // (rails keep their own inset, WIDGET_INSET, which is wider)
        {
            a = padded(a, 0, 0);
        }

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
        // A memory counts only while its spot still reads as its zone (WP8).
        if (remembered && !reads_as(z, remembered->x, scottland::windowing::remembered_pin(memory, z), screen.width))
            remembered.reset();
        double w = g.width, h = g.height;
        if (z == Z::center)
        {
            a = padded(a, w, h);
        }

        scottland::windowing::rectangle region{double(a.x), double(a.y), double(a.width), double(a.height)};
        if (z == Z::center)
        {
            // The zone constrains the window's CENTER; content stays full size, even if wider.
            double edge = screen.width * (1 - std::clamp(double(center_width) / 100, 0.0, 1.0)) / 2;
            // Strictly inside: a center exactly on the zone's edge is already the periphery's
            // (the softness band starts there), so it would be scaled (tenet 4).
            double lo = std::max(edge + 1.0, a.x + std::min(w, double(a.width)) / 2);
            double hi = std::min(screen.width - edge - 1.0, a.x + a.width - std::min(w, double(a.width)) / 2);
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
                // An unremembered side destination goes as close to the center as it may (WP4): its
                // center past where the zone scale has visibly fallen (WP8), and at most a quarter
                // of its scaled width hanging into the center zone, never half of it (Mike,
                // 2026-10-04), where it fits on screen (WP7); else as far out as fits. From there
                // the placement routine below keeps it off center windows (and any other) and
                // nearest the center. Remembered positions remain exact (WP2).
                if (!remembered && side.width > 0)
                {
                    double inner = left ? side.x + side.width : side.x;
                    double outer = left ? side.x : side.x + side.width;
                    double threshold = periphery_threshold(screen.width);
                    double center_edge = screen.width * (1 - std::clamp(double(center_width) / 100, 0.0, 1.0)) / 2;
                    double boundary = left ? center_edge : screen.width - center_edge;
                    auto area = output->workarea->get_workarea();
                    std::optional<double> chosen;
                    for (int step = 0; step <= 512; ++step)
                    {
                        double trial = inner + (outer - inner) * step / 512;
                        // A pixel inward too, so rounding the landing can't bring it back.
                        double inward = trial + (left ? 1 : -1);
                        double scale = place_at(trial, screen.width).scale;
                        if (std::max(scale, place_at(inward, screen.width).scale) > threshold) continue;
                        double half = g.width * scale / 2;
                        auto pa = padded(area, g.width * scale, g.height * scale);
                        if (left ? trial - half < pa.x : trial + half > pa.x + pa.width) continue;
                        chosen = trial;
                        double hang = half / 2;   // a quarter of its scaled width
                        if (left ? trial + half - hang <= boundary : trial - half + hang >= boundary) break;
                    }
                    if (chosen)
                    {
                        if (left) side.width = *chosen - side.x;
                        else { double right = side.x + side.width;
                            side.x = *chosen; side.width = right - *chosen; }
                    }
                }
                // Re-evaluate the rectangle footprint as its natural zone scale
                // changes at the chosen x. Contention uses the pure placement routine.
                // All contention decisions still go through the same pure placement routine.
                auto obstacles = placement_obstacles(output, window->get_id());
                double x = std::clamp(current.x, side.x, side.x + side.width);
                scottland::windowing::point spot;
                for (int iteration = 0; iteration < 16; ++iteration)
                {
                    double scale = place_at(x, screen.width).scale;
                    w = g.width * scale; h = g.height * scale;
                    region.x = side.x - w / 2; region.width = side.width + w;
                    // ...and wholly on screen with its padding (WP7), when it fits.
                    auto pa = padded(output->workarea->get_workarea(), w, h);
                    double right = std::min(region.x + region.width, double(pa.x + pa.width));
                    region.x = std::max(region.x, double(pa.x));
                    region.width = std::max(w, right - region.x);
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
        if (visible) keyboard_motions.erase(visible->get_id());
        fullscreen_impulses.erase(std::remove_if(fullscreen_impulses.begin(), fullscreen_impulses.end(),
            [=] (auto impulse) { return impulse.id == id; }), fullscreen_impulses.end());
        if (!window || !window->get_output()) return;
        // A rail request is idempotent, including while its widget is still launching.
        if (destination == D::widget && link_of_window(window)) return;
        auto g = (visible ? visible : window)->get_geometry();
        scottland::windowing::point current{g.x + g.width / 2.0, g.y + g.height / 2.0};
        if (visible) remember_window(visible);
        if (destination == D::center && link_of_window(window)) { open_widget(*link_of_window(window), true); return; }
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
        auto drawn = hint_rectangle(visible ? visible : window);
        wf::pointf_t from{(drawn.x1 + drawn.x2) / 2, (drawn.y1 + drawn.y2) / 2};
        double from_scale = displayed_scale(window);
        // Ordinary placement takes over now. Docking first captures the drawn
        // frame, then its existing widget handoff stops the glide itself.
        if (!rail) stop_glide(window);
        // Explicit zone cycling follows the zone, including center at 100%, unless the user pinned
        // a scale at this zone's remembered spot (WP1/WP5). The pin comes back only with that spot,
        // and only while it still reads as its zone (WP8; inside the center zone, never: tenet 4).
        auto screen = window->get_output()->get_relative_geometry();
        auto& memory = ensure_window_memory(id);
        auto pin = scottland::windowing::remembered_pin(memory, z);
        if (auto p = memory.positions[size_t(z)]; pin && !(p && reads_as(z, p->x * screen.width, pin, screen.width)))
            pin.reset();
        pin_scale(window, pin);
        // Where its center actually lands, after pixel rounding: the drawn scale must be that
        // spot's zone scale, or it would stay off by the rounding on a steep curve (WP5).
        wf::pointf_t landed{std::round(at.x - real.width / 2.0) + real.width / 2.0,
            std::round(at.y - real.height / 2.0) + real.height / 2.0};
        if (destination == D::periphery && link_of_window(window))
            restore_window(*link_of_window(window), at, true);
        else if (!rail) move_window(window, landed.x - real.width / 2.0, landed.y - real.height / 2.0);
        if (rail)
        {
            model.windows[id].pending_rail = current;
            widgetize(window, false, left ? "left" : "right", "hint-rail");
            if (auto link = link_of_window(window)) link->drop = {at.x, at.y};
            if (!link_of_window(window)) model.windows[id].pending_rail.reset();
            ensure_window_memory(id).last_side = left ? -1 : 1;
            publish_model();
        } else { remember_window(window); start_cycle_glide(window, from, from_scale,
            landed, pin ? *pin : place_at(landed.x, screen.width).scale); }
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

    // WK36: holding an unfocused window's hint pairs it with the window that had focus.
    static constexpr double PAIR_GAP = scottland::HALO; // a halo-sized gap (P7)
    wf::wl_timer<false> deferred_pair;
    wf::wl_idle_call deferred_pair_ready;
    // The pair holds still for window avoidance, so the windows behind it peek out instead,
    // until either member moves or is resized. Nothing else about the pair is kept.
    std::vector<std::pair<uint64_t, wf::geometry_t>> pair_anchors;
    bool pair_anchored(wayfire_toplevel_view view)
    {
        if (!view) return false;
        uint64_t id = view->get_id();
        if (auto link = link_of_widget(view)) id = link->window_id;
        bool member = false;
        for (auto [window, geometry] : pair_anchors)
        {
            auto shown = represented_view(window);
            if (!shown || link_of_window(wf::toplevel_cast(view_by_id(window))) ||
                shown->toplevel()->pending().geometry != geometry)
            { pair_anchors.clear(); return false; }
            member |= window == id;
        }
        return member;
    }
    void pair_windows(uint64_t held_id, uint64_t partner_id, int attempts = 0)
    {
        auto held = wf::toplevel_cast(view_by_id(held_id));
        auto partner = wf::toplevel_cast(view_by_id(partner_id));
        if (!held || !partner || held == partner || deferred_pair.is_connected()) return;
        auto held_shown = represented_view(held_id), partner_shown = represented_view(partner_id);
        if (!held_shown || !partner_shown || !held_shown->get_output() || !partner_shown->get_output()) return;
        // Full screen ends first, as for an explicit cycle (WK12); the pair uses restored sizes.
        bool waiting = false;
        for (auto window : {held, partner})
            if (window->pending_fullscreen())
            {
                waiting = true;
                wf::get_core().default_wm->fullscreen_request(window, window->get_output(), false);
            } else if (window->toplevel()->current().fullscreen) waiting = true; // until its size commits
        if (waiting)
        {
            if (attempts < 10) deferred_pair.set_timeout(100, [=] () {
                deferred_pair_ready.run_once([=] () { pair_windows(held_id, partner_id, attempts + 1); }); });
            return;
        }
        // The pair forms on the focused window's screen; the held one joins it there.
        auto output = partner_shown->get_output();
        // Keep the current left/right order across the whole layout (P1).
        auto global_x = [] (wayfire_toplevel_view view) {
            auto g = view->get_geometry();
            return view->get_output()->get_layout_geometry().x + g.x + g.width / 2.0;
        };
        bool held_left = global_x(held_shown) < global_x(partner_shown);
        auto left = held_left ? held : partner, right = held_left ? partner : held;
        auto lg = left->get_geometry(), rg = right->get_geometry();
        auto a = output->workarea->get_workarea();
        auto fit = scottland::windowing::fit_pair({double(lg.width), double(lg.height)},
            {double(rg.width), double(rg.height)},
            {double(a.x), double(a.y), double(a.width), double(a.height)}, PAIR_GAP, SCREEN_PADDING);
        LOGI("scottland: pairing ", held_id, " with ", partner_id, " at ", fit.scale * 100,
            "%, gap ", fit.gap, ", margin ", fit.margin);
        place_paired(left, fit.left, fit.scale, output);
        place_paired(right, fit.right, fit.scale, output);
        pair_anchors.clear();
        for (auto window : {left, right})
            pair_anchors.emplace_back(window->get_id(), window->toplevel()->pending().geometry);
        // Both in front of the other center windows, which stay put and peek out (avoidance);
        // the held window was selected and focused by its press (WK6) and stays so.
        wf::view_bring_to_front(partner);
        wf::get_core().default_wm->focus_raise_view(held);
        declutter_signature.clear();
        refresh_layout_avoidance();
    }
    void place_paired(wayfire_toplevel_view window, scottland::windowing::point at, double scale,
        wf::output_t *output)
    {
        auto shown = represented_view(window->get_id());
        keyboard_motions.erase(window->get_id());
        if (shown) keyboard_motions.erase(shown->get_id());
        fullscreen_impulses.erase(std::remove_if(fullscreen_impulses.begin(), fullscreen_impulses.end(),
            [&] (auto impulse) { return impulse.id == window->get_id(); }), fullscreen_impulses.end());
        // The pair's scale holds wherever its center lands, even outside the center zone; it is
        // an ordinary scale pin (L31), so the next drag, push or cycle clears it.
        pin_scale(window, scale);
        auto place = [&] () {
            if (window->get_output() != output) wf::move_view_to_output(window, output, false);
            auto g = window->get_geometry();
            move_window(window, std::round(at.x - g.width / 2.0), std::round(at.y - g.height / 2.0));
        };
        if (auto link = link_of_window(window))
        {
            // A widget joins as its app window, opened from the rail into its place (tenet 3).
            restore_window(*link, wf::pointf_t{at.x, at.y}, true);
            if (window->get_output() != output) place();
            remember_window(window);
            return;
        }
        auto drawn = hint_rectangle(shown ? shown : window);
        wf::pointf_t from{(drawn.x1 + drawn.x2) / 2, (drawn.y1 + drawn.y2) / 2};
        double from_scale = displayed_scale(window);
        if (window->get_output() != output)
        {
            auto was = window->get_output()->get_layout_geometry(), now = output->get_layout_geometry();
            from.x += was.x - now.x; from.y += was.y - now.y;
        }
        stop_glide(window);
        place();
        remember_window(window);
        start_cycle_glide(window, from, from_scale, {at.x, at.y}, scale);
    }
    // One clock for hint timing: presses and releases carry their input event time (WK15), and
    // holds compare it with the same monotonic clock, so a hold is timed from the physical press.
    static uint32_t key_event_time(wlr_keyboard_key_event *event)
    {
        auto now = now_msec();
        // An event stamped by another clock (in the future or implausibly old) counts as now.
        return uint32_t(now - event->time_msec) > 1000 ? now : event->time_msec;
    }
    void arm_hint_hold()
    {
        hint_hold.set_timeout(std::max(1u, window_keys.hold_remaining(now_msec())), [=] () {
            if (!window_keys.hold_due(now_msec()) && window_keys.hold_waiting())
                hint_hold_retry.run_once([=] () { if (window_keys.hold_waiting()) arm_hint_hold(); });
        });
    }

    // Overlay circles precede window islands in the same ordered liquid field.
    // Presentation-only IDs cannot collide with Wayfire's view IDs.
    void append_hint_goo(wf::output_t *output, std::vector<scottland::goo::source_t>& sources)
    {
        std::vector<scottland::goo::source_t> circles;
        for (auto& [id, visual] : hint_visuals)
        {
            auto hint = visual.hint;
            if (!hint || visual.hint_output != output || hint->pop <= .001) continue;
            auto r = hint->circle;
            scottland::goo::source_t s;
            s.id = (uint64_t(1) << 63) | id;
            s.hint_circle = true; s.hinted = true;
            s.rect = {r.x + r.width / 2, r.y + r.height / 2, r.width / 2, r.height / 2};
            s.liquid = {1, r.width / 2, float(id) * 1.618f, 1};
            s.scale = hint->pop;
            s.dye = {hint->dye.r, hint->dye.g, hint->dye.b};
            s.light = hints_palette.light;
            circles.push_back(s);
        }
        sources.insert(sources.begin(), circles.begin(), circles.end());
    }

    bool step_hints(bool force_solve = false)
    {
        ++hint_step_count;
        refresh_hint_palette();
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
            if (!hint_visuals.count(e.id))
            {
                hint_visual visual; visual.view = view->weak_from_this();
                visual.offset = std::make_shared<wf::scene::view_2d_transformer_t>(view);
                hint_visuals[e.id] = std::move(visual);
            }
            auto found = hint_visuals.find(e.id);
            if (found == hint_visuals.end()) continue;
            auto& visual = found->second;
            if (visual.view.lock().get() != view.get())
            {
                if (auto old = visual.view.lock()) { clear_hint_dye(old.get());
                    if (visual.offset_attached)
                        old->get_transformed_node()->rem_transformer("scottland-hint-offset"); }
                if (visual.hint) visual.hint->relocate();
                if (visual.fullscreen_tint) wf::scene::remove_child(visual.fullscreen_tint);
                visual.fullscreen_tint.reset(); visual.view = view->weak_from_this();
                visual.offset = std::make_shared<wf::scene::view_2d_transformer_t>(view);
                visual.offset_attached = false;
            }
            // Window avoidance is a visual-only reservation for hint circles. It normally
            // runs only with visible hints; the opt-in setting also keeps it active between
            // requests. The legacy option remains an alias for existing user config.
            bool avoidance_active = window_keys.active || bool(window_avoidance_always) ||
                bool(hint_avoidance_always);
            if (avoidance_active && !visual.offset_attached)
            {
                view->get_transformed_node()->add_transformer(visual.offset, wf::TRANSFORMER_HIGHLEVEL - 1,
                    "scottland-hint-offset");
                visual.offset_attached = true;
            }
            auto g = view->get_geometry();
            signature << e.id << ':' << g.x << ',' << g.y << ',' << g.width << ',' << g.height << ',' << view->get_output()->to_string() << ';';
            auto r = hint_rectangle(view, true);
            auto anchor = hint_anchor(view, true);
            signature << ':' << std::round(anchor.x) << ',' << std::round(anchor.y) << ',' << std::round(r.height())
                << ',' << std::round(hint_size(view)) << ';';
            by_output[view->get_output()].push_back(e.id);
        }
        auto focused = drag->view ? drag->view : wf::toplevel_cast(wf::get_core().seat->get_active_view());
        // Opening order owns letters; scene order alone owns occlusion (including dialogs
        // and fullscreen). Include it in the solve key so an explicit raise refreshes visibility.
        std::map<uint64_t, size_t> stacking;
        for (auto& [output, ids] : by_output)
            for (auto view : output->wset()->get_views(
                wf::WSET_MAPPED_ONLY | wf::WSET_EXCLUDE_MINIMIZED | wf::WSET_SORT_STACKING))
            {
                if (!view->get_root_node()->is_enabled()) continue;
                for (auto id : ids) if (represented_view(id) == view)
                { stacking[id] = stacking.size(); signature << "z:" << id << ';'; }
            }
        bool avoidance_active = window_keys.active || bool(window_avoidance_always) ||
            bool(hint_avoidance_always);
        // Live: something is moving under the user's hand or by inertia. The calm rules differ
        // (WK13), so live to rest is a layout change that requests one rest pass.
        const bool live = bool(drag->view) || inertia_active() || !glides.empty();
        signature << "active:" << window_keys.active << ";avoidance:" << avoidance_active
            << ";anchor:" << (focused ? focused->get_id() : 0) << ";live:" << live
            << ";text:" << hints_palette.text_scale;
        for (auto [id, geometry] : pair_anchors) signature << ";pair:" << id;
        // Spread holds still for peeking too: the solo window, and the windows an audition shows.
        if (solo_anchor) signature << ";solo:" << solo_anchor->first;
        for (const auto& actor : audition.actors) signature << ";audition:" << actor.id;
        auto current_signature = signature.str();
        auto solve_now = std::chrono::steady_clock::now();
        bool within_tick_budget = last_exposure_solve.time_since_epoch().count() &&
            solve_now - last_exposure_solve < std::chrono::milliseconds(16);
        bool signature_changed = current_signature != declutter_signature;
        hint_order_dirty |= signature_changed;
        bool retry_pending = exposure_solve_pending && !signature_changed && avoidance_active;
        bool solve_requested = signature_changed || retry_pending;
        bool deferred_solve = solve_requested && avoidance_active && !force_solve && within_tick_budget;
        if (signature_changed)
        {
            // A pass in progress finishes on its snapshot; the newest layout gets the next one
            // (WK13: rear windows lag a moving front window by at most one pass). The WK37
            // occlusion measurement restarts.
            for (auto& [name, state] : peek_by_output) state.newer = true;
            occlusion_next_by_output.clear();
        }
        if (solve_requested && (!avoidance_active || force_solve || !within_tick_budget))
        {
            declutter_signature = std::move(current_signature);
            std::map<uint64_t, scottland::windowing::point> previous_labels;
            for (auto& [id, visual] : hint_visuals)
                previous_labels[id] = visual.label_offset;
            if (!avoidance_active)
            {
                // End of the hint request: the true geometry is the only target.
                // Offsets remain attached until the animation reaches this target.
                exposure_solve_pending = false;
                peek_by_output.clear();
                occlusion_next_by_output.clear();
                for (auto& [id, visual] : hint_visuals)
                {
                    visual.target = {};
                    visual.label_offset = {};
                    visual.visible_fraction = 1;
                    visual.clearance = 0;
                    visual.edge_label = false;
                    visual.outcome = scottland::windowing::peek_outcome::pending;
                    visual.rung = scottland::windowing::peek_rung::none;
                    visual.rule = '-'; visual.room = {0, 0, 0, 0};
                }
        } else
        {
            auto exposure_started = std::chrono::steady_clock::now();
                // P8: the wall clock only pauses a pass; results never depend on it.
                auto exposure_deadline = exposure_started + std::chrono::microseconds(
                    scottland::windowing::avoidance_solve_budget_us);
                exposure_solve_pending = false;
                for (auto& [output, ids] : by_output)
                {
                    std::vector<scottland::windowing::point> anchors;
                    std::vector<double> diameters;
                    std::vector<scottland::windowing::hint_constraint> constraints;
                    for (auto id : ids) { auto view = represented_view(id); auto r = hint_rectangle(view, true);
                        anchors.push_back(hint_anchor(view, true));
                        bool widget = bool(link_of_widget(view));
                        constraints.push_back({widget, widget ? r.height() / 2 : 0,
                            view == focused});
                        diameters.push_back(std::round(hint_size(view))); }
                    auto screen = output->get_relative_geometry();
                    auto displaced = std::any_of(constraints.begin(), constraints.end(),
                        [] (auto constraint) { return constraint.vertical_only; }) ?
                        scottland::windowing::declutter(anchors,
                            {0, 0, double(screen.width), double(screen.height)}, 6, diameters, constraints) : anchors;
                    for (size_t i = 0; i < ids.size(); ++i)
                    {
                        // Widget circles use the vertical-only declutter pass.
                        if (constraints[i].vertical_only)
                            hint_visuals[ids[i]].target = {
                                displaced[i].x - anchors[i].x, displaced[i].y - anchors[i].y};
                    }

                    using rectangle = scottland::windowing::rectangle;
                    rectangle bounds{0, 0, double(screen.width), double(screen.height)};
                    auto area = output->workarea->get_workarea();
                    const double text = hints_palette.text_scale;
                    // WK13: the peek test's rectangle must lie where a window can be seen: the
                    // work area, not under a panel.
                    scottland::windowing::peek_request request;
                    request.screen = {double(area.x), double(area.y), double(area.width), double(area.height)};
                    request.strip_depth = scottland::windowing::peek_strip_depth * text;
                    request.strip_length = scottland::windowing::peek_strip_length * text;
                    request.window_mode = window_keys.active;
                    request.live = live;
                    std::vector<uint64_t> window_ids;
                    auto ordered = ids;
                    std::stable_sort(ordered.begin(), ordered.end(), [&] (auto a, auto b) {
                        auto rank = [&] (auto id) { auto found = stacking.find(id);
                            return found == stacking.end() ? stacking.size() : found->second; };
                        return rank(a) < rank(b);
                    });
                    // P13: a displayed center stays in its own zone. Zone extents as the layout
                    // model draws them (center_width, rail_width); a periphery excludes its rail.
                    const double width = screen.width;
                    const double center_half = width * std::clamp(double(center_width), 0.0, 100.0) / 200.0;
                    const double rail = width * std::clamp(double(rail_width), 0.0, 50.0) / 100.0;
                    std::vector<rectangle> fixed_above;
                    std::ostringstream order;
                    for (auto id : ordered)
                    {
                        auto view = represented_view(id);
                        auto r = hint_rectangle(view, true);
                        auto& visual = hint_visuals[id];
                        if (link_of_widget(view))
                        {
                            auto anchor = hint_anchor(view, true); double radius = hint_size(view) / 2;
                            fixed_above.push_back({r.x1 + visual.target.x, r.y1 + visual.target.y,
                                r.width(), r.height()});
                            fixed_above.push_back({anchor.x + visual.target.x - radius - 6,
                                anchor.y + visual.target.y - radius - 6, 2 * radius + 12, 2 * radius + 12});
                            continue;
                        }
                        scottland::windowing::peek_window input;
                        input.frame = {r.x1, r.y1, r.width(), r.height()};
                        using Z = scottland::windowing::zone;
                        auto z = window_zone(view);
                        if (z == Z::center) { input.zone_x1 = width / 2 - center_half; input.zone_x2 = width / 2 + center_half; }
                        else if (z == Z::left_periphery) { input.zone_x1 = rail; input.zone_x2 = width / 2 - center_half - .5; }
                        else { input.zone_x1 = width / 2 + center_half + .5; input.zone_x2 = width - rail; }
                        input.center_y1 = area.y; input.center_y2 = area.y + area.height;
                        // Focused and paired windows (WK36) are fixed: others peek around them. So
                        // are the solo window and the windows a solo audition shows (docs/spread.md).
                        input.anchored = view == focused || pair_anchored(view) || solo_anchored(view) ||
                            audition_holds(view);
                        input.full_hint = hint_size(view);
                        input.minimum_hint = 48 * text;
                        input.target = visual.target;
                        input.displayed = {double(visual.offset->translation_x), double(visual.offset->translation_y)};
                        input.fixed_foreground = fixed_above;
                        request.windows.push_back(std::move(input));
                        window_ids.push_back(id);
                        order << id << ';';
                    }
                    auto& state = peek_by_output[output->to_string()];
                    // A new pass when none is running, or when the windows or their stacking
                    // changed: its indices would be stale (previous targets hold meanwhile).
                    if (!state.pass || state.order != order.str() ||
                        (state.pass->complete() && state.newer))
                    {
                        state.pass.emplace(std::move(request));
                        state.order = order.str(); state.ids = window_ids; state.newer = false;
                        occlusion_next_by_output.erase(output->to_string());
                        ++avoidance_pass_count;
                    }
                    auto& pass = *state.pass;
                    const size_t before = pass.units;
                    auto slice_started = std::chrono::steady_clock::now();
                    bool output_complete = scottland::windowing::peek_step(pass,
                        scottland::windowing::peek_slice_units, std::min(exposure_deadline, exposure_started +
                            std::chrono::microseconds(scottland::windowing::peek_pause_us)));
                    exposure_search_ms = std::chrono::duration<double, std::milli>(
                        std::chrono::steady_clock::now() - slice_started).count();
                    exposure_search_max_ms = std::max(exposure_search_max_ms, exposure_search_ms);
                    avoidance_slice_units = pass.units - before;
                    avoidance_slice_units_max = std::max(avoidance_slice_units_max, avoidance_slice_units);
                    if (output_complete)
                    {
                        avoidance_pass_units = pass.units; avoidance_pass_slices = pass.slices;
                        avoidance_pass_slices_max = std::max(avoidance_pass_slices_max, pass.slices);
                    }
                    // Each window's result is final once the pass has reached it.
                    for (size_t i = 0; i < pass.next && i < state.ids.size(); ++i)
                    {
                        auto found = hint_visuals.find(state.ids[i]);
                        auto view = represented_view(state.ids[i]);
                        if (found == hint_visuals.end() || !view) continue;
                        const auto& result = pass.results[i];
                        auto& visual = found->second;
                        visual.target = result.target;
                        visual.outcome = result.outcome; visual.rung = result.rung;
                        visual.rule = result.rule; visual.room = result.room;
                        visual.label_size = result.hint_diameter;
                        visual.clearance = result.hint.clearance;
                        visual.edge_label = false;
                        auto anchor = hint_anchor(view, true);
                        visual.label_offset = {result.hint.center.x - result.target.x - anchor.x,
                            result.hint.center.y - result.target.y - anchor.y};
                    }
                    if (!output_complete || state.newer) exposure_solve_pending = true;
                    for (auto id : ordered)
                    {
                        auto view = represented_view(id);
                        if (link_of_widget(view))
                        {
                            auto& visual = hint_visuals[id];
                            visual.label_offset = {}; visual.label_size = hint_size(view);
                            visual.edge_label = false; visual.clearance = 0;
                        }
                    }
                    // WK37 occlusion, front to back, from the frames and targets this solve
                    // settled. Only in Window mode (outlines draw nowhere else), only once the
                    // output's pass is complete, and within the same deadline as the solve (P8):
                    // an unfinished pass resumes on the next tick; a layout change restarts it.
                    if (!window_keys.active)
                    {
                        for (auto id : ordered) hint_visuals[id].visible_fraction = 1;
                    } else if (output_complete)
                    {
                        auto pass_started = std::chrono::steady_clock::now();
                        auto& next = occlusion_next_by_output[output->to_string()];
                        std::vector<rectangle> in_front;
                        for (size_t i = 0; i < ordered.size() && next != SIZE_MAX; ++i)
                        {
                            auto view = represented_view(ordered[i]);
                            auto& visual = hint_visuals[ordered[i]];
                            auto r = hint_rectangle(view, true);
                            rectangle drawn{r.x1 + visual.target.x, r.y1 + visual.target.y,
                                r.width(), r.height()};
                            if (i >= next)
                            {
                                if (std::chrono::steady_clock::now() >= exposure_deadline)
                                {
                                    next = i; exposure_solve_pending = true; ++occlusion_deferrals;
                                    break;
                                }
                                visual.visible_fraction = link_of_widget(view) ? 1 :
                                    scottland::windowing::visible_fraction(drawn, bounds, in_front);
                            }
                            in_front.push_back(drawn);
                            if (i + 1 == ordered.size()) next = SIZE_MAX;
                        }
                        if (ordered.empty()) next = SIZE_MAX;
                        occlusion_pass_ms = std::chrono::duration<double, std::milli>(
                            std::chrono::steady_clock::now() - pass_started).count();
                        occlusion_pass_max_ms = std::max(occlusion_pass_max_ms, occlusion_pass_ms);
                    }
                }
                // Drop passes of outputs that no longer have windows.
                for (auto it = peek_by_output.begin(); it != peek_by_output.end();)
                {
                    bool present = std::any_of(by_output.begin(), by_output.end(),
                        [&] (const auto& item) { return item.first->to_string() == it->first; });
                    it = present ? std::next(it) : peek_by_output.erase(it);
                }
                exposure_solve_ms = std::chrono::duration<double, std::milli>(
                    std::chrono::steady_clock::now() - exposure_started).count();
                exposure_solve_max_ms = std::max(exposure_solve_max_ms, exposure_solve_ms);
                ++exposure_solve_count;
                exposure_solve_deadline_count += std::chrono::steady_clock::now() >= exposure_deadline;
            }
            for (auto& [id, visual] : hint_visuals)
            {
                auto before = previous_labels[id];
                if (visual.hint && std::hypot(visual.label_offset.x - before.x,
                    visual.label_offset.y - before.y) > 0.5) visual.hint->relocate();
            }
            if (avoidance_active) last_exposure_solve = std::chrono::steady_clock::now();
        }
        bool moving = false, animation_moving = false;
        for (auto it = hint_visuals.begin(); it != hint_visuals.end();)
        {
            auto& visual = it->second; auto view = wf::toplevel_cast(visual.view.lock());
            if (!view || !represented.count(it->first))
            {
                if (view) { clear_hint_dye(view.get());
                    if (visual.offset_attached)
                        view->get_transformed_node()->rem_transformer("scottland-hint-offset"); }
                if (visual.hint) wf::scene::remove_child(visual.hint);
                if (visual.fullscreen_tint) wf::scene::remove_child(visual.fullscreen_tint);
                remove_hint_outline(visual);
                it = hint_visuals.erase(it); continue;
            }
            auto offset = visual.offset;
            // A touch hold may become a lift: until it resolves, the window stays where it is
            // drawn, so the lift grabs it there (decision 9); a tap then eases it home as focused.
            const bool grabbed = drag->view == view || (hold_finger >= 0 && hold_view.lock().get() == view.get());
            auto target = grabbed ? scottland::windowing::point{
                double(offset->translation_x), double(offset->translation_y)} : visual.target;
            bool offset_changed = false;
            if (std::hypot(target.x - offset->translation_x, target.y - offset->translation_y) > .001)
            {
                view->damage(); view->get_transformed_node()->begin_transform_update();
                if (hints_reduced_motion || (view == focused && inertia_active()))
                {
                    offset->translation_x = target.x;
                    offset->translation_y = target.y;
                } else
                {
                    const auto now = std::chrono::steady_clock::now();
                    double dt = visual.last_offset_step.time_since_epoch().count() == 0 ? .008 :
                        std::chrono::duration<double>(now - visual.last_offset_step).count();
                    // Keep a stalled compositor from converting elapsed wall time
                    // into one visible jump when it resumes. Capping each update at
                    // a display interval gives the temporal-coherence speed limit a
                    // frame-independent maximum.
                    dt = std::clamp(dt, 0.0, 1.0 / 60.0);
                    double dx = (target.x - offset->translation_x) * .18;
                    double dy = (target.y - offset->translation_y) * .18;
                    const double step = std::hypot(dx, dy), cap = scottland::motion::AUTOMATIC_MAX_SPEED * dt;
                    if (step > cap && step > .001) { dx *= cap / step; dy *= cap / step; }
                    exposure_easing_speed_max = std::max(exposure_easing_speed_max,
                        std::hypot(dx, dy) / std::max(dt, .0001));
                    offset->translation_x += dx;
                    offset->translation_y += dy;
                }
                visual.last_offset_step = std::chrono::steady_clock::now();
                offset_changed = true;
            }
            bool unsettled = !grabbed &&
                std::hypot(target.x - offset->translation_x, target.y - offset->translation_y) > 0.1;
            moving |= unsettled;
            if (!unsettled) { offset->translation_x = target.x; offset->translation_y = target.y; }
            if (offset_changed) { view->get_transformed_node()->end_transform_update(); view->damage(); }
            if (window_keys.active)
            {
                if (visual.hint && visual.hint_output != view->get_output())
                { wf::scene::remove_child(visual.hint); visual.hint.reset(); }
                bool widget = bool(link_of_widget(view));
                // Exposure always supplies the readable minimum at its best available spot.
                // Lack of clear space is never a reason to suppress an ordinary window hint.
                bool badge_ready = true;
                if (!widget)
                {
                    // A rear window can start fully covered. Wait for its and foreground
                    // offsets to settle; the minimum-size fallback appears even if the
                    // settled region still cannot contain the circle.
                    for (auto other_id : by_output[view->get_output()])
                    {
                        auto& other = hint_visuals[other_id];
                        if (other_id != it->first && !link_of_widget(represented_view(other_id)) &&
                            stacking[other_id] >= stacking[it->first]) continue;
                        badge_ready &= std::hypot(other.target.x - other.offset->translation_x,
                            other.target.y - other.offset->translation_y) < .1;
                    }
                }
                if (!badge_ready && visual.hint)
                { wf::scene::remove_child(visual.hint); visual.hint.reset(); }
                if (badge_ready && !visual.hint)
                {
                    visual.hint_output = view->get_output();
                    visual.hint = std::make_shared<scottland::windowing::hint_node>();
                    wf::scene::add_front(view->get_output()->node_for_layer(wf::scene::layer::OVERLAY), visual.hint);
                    hint_order_dirty = true;
                }
                auto anchor = hint_anchor(view);
                unsigned slot = ensure_window_memory(it->first).hint_slot;
                auto text = upper(window_keys.label(slot));
                auto color = color_for_hint(slot);
                if (auto frame = frame_of(view, false))
                {
                    frame->set_hint_dye(glm::vec3{color.r, color.g, color.b});
                    if (visual.fullscreen_tint) wf::scene::remove_child(visual.fullscreen_tint);
                    visual.fullscreen_tint.reset();
                } else if (view->pending_fullscreen())
                {
                    if (!visual.fullscreen_tint)
                    {
                        visual.fullscreen_tint = std::make_shared<scottland::windowing::fullscreen_hint_node>();
                        wf::scene::add_front(visual.offset, visual.fullscreen_tint);
                    }
                    visual.fullscreen_tint->update(view->get_geometry(), color, window_mode_tint_strength());
                }
                // WK37: a mostly occluded window shows its whole outline, above all windows.
                if (!widget && !view->pending_fullscreen() &&
                    visual.visible_fraction < scottland::windowing::hint_outline_visible_fraction)
                {
                    auto output = view->get_output();
                    if (visual.outline && visual.outline_output != output) remove_hint_outline(visual);
                    if (!visual.outline)
                    {
                        // Like the hint circles, at the front of the overlay layer: above every
                        // window and every surface already shown there, so nothing occludes it.
                        // The circles and flashes on this output are re-raised to stay on top.
                        auto overlay = output->node_for_layer(wf::scene::layer::OVERLAY);
                        visual.outline = std::make_shared<scottland::windowing::hint_outline_node>();
                        visual.outline_output = output;
                        wf::scene::add_front(overlay, visual.outline);
                        for (auto& [other_id, other] : hint_visuals)
                            if (other.hint && other.hint_output == output) wf::scene::readd_front(overlay, other.hint);
                        for (auto& [flash_id, flash] : hint_flashes)
                            if (flash.node && flash.output == output) wf::scene::readd_front(overlay, flash.node);
                        hint_order_dirty = true; // WK31: put the re-added circles back in stacking order
                    }
                    auto r = scene_rectangle(view, output);
                    auto frame = frame_of(view, false);
                    visual.outline->update(r.x1, r.y1, r.width(), r.height(),
                        frame ? frame->screen_radius() : 0.0, color,
                        scottland::windowing::hint_outline_width);
                } else remove_hint_outline(visual);
                if (visual.hint)
                {
                    const bool hint_moving = visual.hint->update(anchor.x + offset->translation_x + visual.label_offset.x,
                        anchor.y + offset->translation_y + visual.label_offset.y, text,
                        widget ? hint_size(view) : visual.label_size,
                        hints_palette.font_family, color,
                        view->get_output()->get_scale(), widget ?
                            std::optional{hints_palette.background} : std::nullopt,
                        model.goo_outputs.count(view->get_output()), hints_reduced_motion);
                    moving |= hint_moving;
                    animation_moving |= hint_moving;
                }
            } else
            {
                remove_hint_outline(visual);
                bool popping = visual.hint && visual.hint->animate();
                moving |= popping;
                if (!popping && visual.hint) { wf::scene::remove_child(visual.hint); visual.hint.reset(); }
                if (visual.offset_attached && !avoidance_active && !unsettled && !popping)
                {
                    view->get_transformed_node()->rem_transformer("scottland-hint-offset");
                    visual.offset_attached = false;
                }
            }
            ++it;
        }
        // WK31: hints draw in window stacking order, so a covered window's fallback hint
        // never draws over the front window's centered one. Only the hints' own slots in the
        // overlay layer are reordered; other overlay nodes keep their places. Anything that
        // re-adds a hint node to the overlay sets hint_order_dirty.
        if (window_keys.active && std::exchange(hint_order_dirty, false))
            for (auto& [output, ids] : by_output)
            {
                auto parent = output->node_for_layer(wf::scene::layer::OVERLAY);
                std::vector<std::pair<size_t, wf::scene::node_ptr>> drawn;
                for (auto id : ids)
                {
                    auto found = hint_visuals.find(id);
                    if (found == hint_visuals.end() || !found->second.hint ||
                        found->second.hint->parent() != parent.get()) continue;
                    auto rank = stacking.find(id);
                    drawn.emplace_back(rank == stacking.end() ? stacking.size() : rank->second,
                        found->second.hint);
                }
                std::stable_sort(drawn.begin(), drawn.end(),
                    [] (const auto& a, const auto& b) { return a.first < b.first; });
                auto children = parent->get_children();
                std::vector<size_t> slots;
                for (size_t k = 0; k < children.size(); ++k)
                    if (std::any_of(drawn.begin(), drawn.end(),
                        [&] (const auto& hint) { return hint.second == children[k]; })) slots.push_back(k);
                if (slots.size() != drawn.size()) continue;
                bool reordered = false;
                for (size_t k = 0; k < slots.size(); ++k)
                    if (children[slots[k]] != drawn[k].second)
                    { children[slots[k]] = drawn[k].second; reordered = true; }
                if (!reordered) continue;
                parent->set_children_list(children);
                wf::scene::update(parent, wf::scene::update_flag::CHILDREN_LIST);
                for (auto& [rank, hint] : drawn) wf::scene::damage_node(hint, hint->get_bounding_box());
            }
        hint_step_animation = animation_moving;
        hint_step_offset = moving && !animation_moving;
        const bool widget_presentation_moving = window_keys.active && widget_transition_tick.is_connected();
        return moving || bool(drag->view) || inertia_active() || deferred_solve ||
            exposure_solve_pending || widget_presentation_moving;
    }
    void refresh_layout_avoidance(bool immediate = false)
    {
        // Coalesce focus and geometry signals onto the next compositor tick.
        // Hint entry still computes its first frame immediately.
        if (immediate) step_hints(true);
        if (!hints_tick.is_connected())
            hints_tick.set_timeout(8, [=] () { return step_hints(); });
    }
    void end_window_keys()
    {
        window_keys.interrupt(); // Alt release: a focused window's waiting press acts first
        arrow_repeats.clear();
        window_keys.end(); declutter_signature.clear();
        apply_all_opacity();
        for (auto& [id, visual] : hint_visuals)
        {
            if (auto view = visual.view.lock()) clear_hint_dye(view.get());
            if (visual.fullscreen_tint) wf::scene::remove_child(visual.fullscreen_tint);
            visual.fullscreen_tint.reset();
            remove_hint_outline(visual);
            if (visual.hint) visual.hint->hide(hints_reduced_motion);
        }
        apply_widget_mode();  // collapsed and hidden widgets go back (WG16), and away in full screen (WK12)
        refresh_layout_avoidance();
    }
    void begin_window_keys()
    {
        if (alt_bypassed || alt_keys.empty() || held_keys.size() != 1 || drag->view) return;
        capture_chord = true; keyboard_selection = false;
        auto active = wf::get_core().seat->get_active_view();
        auto link = link_of_widget(active);
        window_keys.begin(window_entries(), link ? link->window_id : active ? active->get_id() : 0);
        apply_all_opacity();
        apply_widget_mode();  // every widget comes back expanded while asking (WG16, WK12)
        palette_read = {}; // always read the current theme on entry
        declutter_signature.clear(); refresh_layout_avoidance(true);
    }

    static std::optional<char> window_hint_letter(wlr_keyboard *keyboard, uint32_t code)
    {
        const xkb_keysym_t *syms = nullptr;
        auto layout = xkb_state_key_get_layout(keyboard->xkb_state, code + 8);
        if (xkb_keymap_key_get_syms_by_level(keyboard->keymap, code + 8, layout, 0, &syms) == 1 &&
            syms[0] >= XKB_KEY_a && syms[0] <= XKB_KEY_z)
            return char('a' + syms[0] - XKB_KEY_a);
        return std::nullopt;
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
        if (!down) arrow_repeats.erase(code);
        auto return_token = std::make_pair(ev->device, code);
        if (widget_return_keys.count(return_token))
        {
            if (!down) widget_return_keys.erase(return_token);
            ev->mode = wf::input_event_processing_mode_t::IGNORE;
            return; // don't deliver the release (or autorepeat) to the newly opened app window
        }
        if (!down && swallowed_keys.erase(code))
        {
            if (auto letter = window_hint_letter(keyboard, code))
                window_keys.release(*letter, key_event_time(ev->event));
            ev->mode = wf::input_event_processing_mode_t::IGNORE;
            return; // finish our own pair; this is not a new compositor grab
        }
        // WG25 owns Return on a focused widget outright. This check precedes key-layer handling,
        // so even a widget's Return claim cannot deliver either half of the key to the client.
        if (down && ev->mode == wf::input_event_processing_mode_t::FULL &&
            (code == KEY_ENTER || code == KEY_KPENTER))
        {
            auto active = wf::get_core().seat->get_active_view();
            auto widget = wf::toplevel_cast(active);
            auto link = link_of_widget(active);
            if (widget && link && link->docked() &&
                !wlr_seat_keyboard_has_grab(wf::get_core().get_current_seat()))
            {
                open_widget(*link);
                widget_return_keys.insert(return_token);
                ev->mode = wf::input_event_processing_mode_t::IGNORE;
                return;
            }
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
                if (center_switcher.active) end_center_switcher(true);
                capture_chord = false;
            }
            // Alt-down was delivered immediately, so its matching release also belongs to the
            // app. No synthetic replay, delayed accelerator, or stuck modifier for quick chords.
            return;
        }
        if (claimed) return; // exact focused-surface claims also bleed through active hints (KL7)
        if (!capture_chord && down && !alt_keys.empty())
        {
            if (auto backwards = center_switcher_direction(code, keyboard))
            {
                alt_bypassed = true; alt_hold.disconnect();
                ev->mode = wf::input_event_processing_mode_t::IGNORE;
                if (swallowed_keys.insert(code).second) step_center_switcher(*backwards);
                return;
            }
            if (code == KEY_ESC && center_switcher.active)
            {
                end_center_switcher(false);
                swallowed_keys.insert(code);
                ev->mode = wf::input_event_processing_mode_t::IGNORE;
                return;
            }
        }
        if (down && code == KEY_ESC && !drag->view && !capture_chord && inertia_active())
        {
            cancel_keyboard_motion(); swallowed_keys.insert(code);
            ev->mode = wf::input_event_processing_mode_t::IGNORE;
            return;
        }
        if (!capture_chord)
        {
            if (down && !alt_keys.empty()) { alt_bypassed = true; alt_hold.disconnect(); }
            return;
        }
        ev->mode = wf::input_event_processing_mode_t::IGNORE;
        if (!down) return;
        bool first = swallowed_keys.insert(code).second;
        if (!window_keys.active) return; // Esc cancels, but this whole Alt chord remains ours.
        // Another key ends a hint hold; Esc also drops a focused window's waiting press (WK35).
        if (code == KEY_ESC && first) window_keys.cancel_pending();
        else if (first || arrow_key(code)) window_keys.interrupt();
        if (arrow_key(code)) { press_arrow(code, keyboard, first); return; }
        if (!first) return;
        if (code == KEY_ESC) { cancel_keyboard_motion(); end_window_keys(); }
        else if (code == KEY_TAB)
            window_keys.tab(held_keys.count(KEY_LEFTSHIFT) || held_keys.count(KEY_RIGHTSHIFT));
        else if (code == KEY_F4) window_keys.close_selected();
        else
        {
            if (auto letter = window_hint_letter(keyboard, code))
            {
                window_keys.double_tap_delay = std::clamp(int(window_double_tap_delay), 1, 3000);
                window_keys.refresh(window_entries());
                window_keys.hold_delay = std::clamp(int(window_hold_delay), 1, 3000);
                window_keys.letter(*letter, key_event_time(ev->event));
                if (window_keys.hold_waiting()) arm_hint_hold();
            }
        }
    };
    wf::ipc::method_callback hints_state = [=] (wf::json_t) -> wf::json_t
    {
        auto reply = wf::ipc::json_ok(); reply["active"] = window_keys.active;
        reply["hint_text_scale"] = hints_palette.text_scale;
        reply["minimum_window_hint_size"] = 48 * hints_palette.text_scale;
        reply["size_upgrades_enabled"] = window_keys.active && !drag->view && !inertia_active();
        reply["avoidance_solve_ms"] = exposure_solve_ms;
        reply["avoidance_solve_max_ms"] = exposure_solve_max_ms;
        reply["avoidance_search_ms"] = exposure_search_ms;
        reply["avoidance_search_max_ms"] = exposure_search_max_ms;
        reply["avoidance_max_easing_speed_px_s"] = exposure_easing_speed_max;
        reply["avoidance_solve_budget_ms"] =
            scottland::windowing::avoidance_solve_budget_us / 1000.0;
        // WK13 peek passes: work units of the last slice and the last completed pass.
        reply["avoidance_slice_budget_units"] = int64_t(scottland::windowing::peek_slice_units);
        reply["avoidance_slice_units"] = int64_t(avoidance_slice_units);
        reply["avoidance_slice_units_max"] = int64_t(avoidance_slice_units_max);
        reply["avoidance_pass_units"] = int64_t(avoidance_pass_units);
        reply["avoidance_pass_slices"] = int64_t(avoidance_pass_slices);
        reply["avoidance_pass_slices_max"] = int64_t(avoidance_pass_slices_max);
        reply["avoidance_pass_count"] = int64_t(avoidance_pass_count);
        reply["avoidance_windows_pending"] = int64_t([&] () {
            size_t total = 0;
            for (const auto& [output, state] : peek_by_output)
                if (state.pass) total += state.pass->request.windows.size() - std::min(state.pass->next,
                    state.pass->request.windows.size());
            return total;
        }());
        reply["avoidance_solve_count"] = int64_t(exposure_solve_count);
        reply["avoidance_solve_deadline_count"] = int64_t(exposure_solve_deadline_count);
        reply["avoidance_solve_pending"] = exposure_solve_pending;
        reply["occlusion_pass_ms"] = occlusion_pass_ms;
        reply["occlusion_pass_max_ms"] = occlusion_pass_max_ms;
        reply["occlusion_deferrals"] = int64_t(occlusion_deferrals);
        reply["occlusion_pending"] = std::any_of(occlusion_next_by_output.begin(),
            occlusion_next_by_output.end(), [] (const auto& item) { return item.second != SIZE_MAX; });
        reply["hint_step_count"] = int64_t(hint_step_count);
        reply["hint_step_animation"] = hint_step_animation;
        reply["hint_step_offset"] = hint_step_offset;
        reply["selected"] = int64_t(window_keys.selected); reply["hints"] = wf::json_t::array();
        window_keys.refresh(window_entries());
        for (auto e : window_keys.entries)
        {
            wf::json_t item; item["window"] = int64_t(e.id); item["hint"] = window_keys.label(e.slot);
            item["visible"] = window_keys.active && hint_visuals.count(e.id) && bool(hint_visuals[e.id].hint);
            if (hint_visuals.count(e.id) && hint_visuals[e.id].hint)
            {
                auto hint = hint_visuals[e.id].hint;
                item["pop"] = hint->pop;
                item["rendered"] = hint->opacity > 0;
                item["circle"] = wf::json_t();
                item["circle"]["x"] = hint->circle.x; item["circle"]["y"] = hint->circle.y;
                item["circle"]["size"] = hint->circle.width;
            }
            auto visible = represented_view(e.id);
            if (visible)
            {
                auto g = visible->get_geometry(); item["x"] = g.x; item["y"] = g.y;
                // Test telemetry distinguishes the dragged scene rectangle from true
                // geometry and from the hint-offset transformer. This is also the
                // exact rectangle passed to the avoidance solve.
                auto solve_frame = hint_rectangle(visible, true);
                item["solve_frame"] = wf::json_t();
                item["solve_frame"]["x"] = solve_frame.x1;
                item["solve_frame"]["y"] = solve_frame.y1;
                item["solve_frame"]["width"] = solve_frame.width();
                item["solve_frame"]["height"] = solve_frame.height();
                // Where it is drawn: the true frame plus the avoidance offset.
                double dx = 0, dy = 0;
                if (auto found = hint_visuals.find(e.id); found != hint_visuals.end() && found->second.offset_attached)
                { dx = found->second.offset->translation_x; dy = found->second.offset->translation_y; }
                item["drawn"] = wf::json_t();
                item["drawn"]["x"] = solve_frame.x1 + dx; item["drawn"]["y"] = solve_frame.y1 + dy;
                item["drawn"]["width"] = solve_frame.width(); item["drawn"]["height"] = solve_frame.height();
                if (item["visible"].as_bool())
                {
                    auto& badge = hint_visuals[e.id].hint->circle;
                    item["badge"] = wf::json_t(); item["badge"]["x"] = badge.x; item["badge"]["y"] = badge.y;
                    item["badge"]["size"] = badge.width;
                    auto color = color_for_hint(e.slot);
                    item["color"] = wf::json_t::array();
                    item["color"].append(color.r); item["color"].append(color.g); item["color"].append(color.b);
                }
            }
            item["dx"] = hint_visuals.count(e.id) ? double(hint_visuals[e.id].offset->translation_x) : 0.0;
            item["dy"] = hint_visuals.count(e.id) ? double(hint_visuals[e.id].offset->translation_y) : 0.0;
            // Whether the window holds still for avoidance as a pair member (WK36).
            item["pair_anchored"] = std::any_of(pair_anchors.begin(), pair_anchors.end(),
                [&] (auto& anchor) { return anchor.first == e.id; });
            // ... or as the solo window, or as a window a solo audition shows (docs/spread.md).
            item["solo_anchored"] = solo_anchor && solo_anchor->first == e.id;
            item["audition_held"] = std::any_of(audition.actors.begin(), audition.actors.end(),
                [&] (auto& actor) { return actor.id == e.id; });
            item["target_dx"] = hint_visuals.count(e.id) ? hint_visuals[e.id].target.x : 0.0;
            item["target_dy"] = hint_visuals.count(e.id) ? hint_visuals[e.id].target.y : 0.0;
            item["label_dx"] = hint_visuals.count(e.id) ? hint_visuals[e.id].label_offset.x : 0.0;
            item["label_dy"] = hint_visuals.count(e.id) ? hint_visuals[e.id].label_offset.y : 0.0;
            item["clearance"] = hint_visuals.count(e.id) ? hint_visuals[e.id].clearance : 0.0;
            if (hint_visuals.count(e.id))
            {
                const auto& visual = hint_visuals[e.id];
                item["outcome"] = scottland::windowing::to_string(visual.outcome);
                item["rung"] = scottland::windowing::to_string(visual.rung);
                item["rule"] = std::string(1, visual.rule);
                item["room"] = wf::json_t();
                item["room"]["x"] = visual.room.x; item["room"]["y"] = visual.room.y;
                item["room"]["width"] = visual.room.width; item["room"]["height"] = visual.room.height;
                item["hint_size"] = visual.label_size;
            }
            int64_t avoidance_order = -1;
            for (const auto& [output, state] : peek_by_output)
                if (auto at = std::find(state.ids.begin(), state.ids.end(), e.id); at != state.ids.end())
                    avoidance_order = at - state.ids.begin();
            item["avoidance_order"] = avoidance_order;
            item["edge_label"] = hint_visuals.count(e.id) && hint_visuals[e.id].edge_label;
            item["visible_fraction"] = hint_visuals.count(e.id) ? hint_visuals[e.id].visible_fraction : 1.0;
            item["outline"] = hint_visuals.count(e.id) && hint_visuals[e.id].outline;
            item["outline_frame"] = wf::json_t();
            if (hint_visuals.count(e.id) && hint_visuals[e.id].outline)
            {
                auto& o = *hint_visuals[e.id].outline;
                item["outline_frame"]["x"] = o.x; item["outline_frame"]["y"] = o.y;
                item["outline_frame"]["width"] = o.width; item["outline_frame"]["height"] = o.height;
                item["outline_frame"]["radius"] = o.radius;
            }
            item["flash"] = hint_flashes.count(e.id) && hint_flashes[e.id].node ?
                hint_flashes[e.id].node->alpha : 0.0;
            item["memories"] = memory_spots(ensure_window_memory(e.id));
            reply["hints"].append(item);
        }
        return reply;
    };
    wf::ipc::method_callback center_switcher_state = [=] (wf::json_t) -> wf::json_t
    {
        auto reply = wf::ipc::json_ok();
        reply["active"] = center_switcher.active;
        reply["count"] = int(center_switcher.candidates.size());
        reply["selected"] = center_switcher.active && !center_switcher.candidates.empty() ?
            int64_t(center_switcher.candidates[center_switcher.selected]) : int64_t(0);
        reply["preview"] = bool(center_switcher.preview);
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
            keyboard_selection = true;
            auto view = wf::toplevel_cast(view_by_id(id));
            if (restore && link_of_window(view)) open_widget(*link_of_window(view));
            else if (auto visible = represented_view(id)) wf::get_core().default_wm->focus_raise_view(visible);
        };
        window_keys.move = [=] (uint64_t id, auto to) { keyboard_selection = true; cycle_window(id, to); };
        window_keys.hint_select = [=] (uint64_t id) { peek_widget_for_hint(id); };
        window_keys.hint_peek_active = [=] (uint64_t id) {
            auto found = model.widgets.find(id);
            return found != model.widgets.end() && found->second.collapsed &&
                found->second.peek_hint_due && int32_t(now_msec() - *found->second.peek_hint_due) < 0;
        };
        window_keys.hint_action = [=] (uint64_t id) { flash_hint(id); };
        window_keys.focused = [=] () -> uint64_t {
            auto active = wf::get_core().seat->get_active_view();
            if (auto link = link_of_widget(active)) return link->window_id;
            return active && model.windows.count(active->get_id()) ? active->get_id() : 0;
        };
        window_keys.pair = [=] (uint64_t held, uint64_t partner) {
            keyboard_selection = true; pair_windows(held, partner); };
        window_keys.solo = [=] (uint64_t id) { solo_window(id); };
        window_keys.close = [=] (uint64_t id) { auto view = wf::toplevel_cast(view_by_id(id));
            if (auto link = link_of_window(view)) close_linked(*link); else if (view) view->close(); };
        wf::get_core().connect(&on_window_key);
        ipc_repo->register_method("scottland/hints", hints_state);
        ipc_repo->register_method("scottland/center-switcher", center_switcher_state);
        record_focus_recency(wf::get_core().seat->get_active_view());
    }
    void fini_window_keys()
    {
        on_window_key.disconnect(); alt_hold.disconnect(); hints_tick.disconnect(); deferred_cycle.disconnect();
        hint_hold.disconnect(); hint_hold_retry.disconnect(); deferred_pair.disconnect(); deferred_pair_ready.disconnect();
        hint_registration.disconnect(); deferred_ready.disconnect();
        stop_keyboard_motion();
        end_center_switcher(false); hint_flash_tick.disconnect();
        for (auto& [id, flash] : hint_flashes) if (flash.node) wf::scene::remove_child(flash.node);
        hint_flashes.clear();
        window_keys.end();
        ipc_repo->unregister_method("scottland/hints");
        ipc_repo->unregister_method("scottland/center-switcher");
        for (auto& [id, visual] : hint_visuals)
        {
            if (visual.hint) wf::scene::remove_child(visual.hint);
            if (visual.fullscreen_tint) wf::scene::remove_child(visual.fullscreen_tint);
            remove_hint_outline(visual);
            if (auto view = visual.view.lock()) { clear_hint_dye(view.get());
                if (visual.offset_attached)
                    view->get_transformed_node()->rem_transformer("scottland-hint-offset"); }
        }
        hint_visuals.clear();
    }
