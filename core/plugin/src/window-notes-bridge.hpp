    struct note_anchor_visual_t
    {
        wf::output_t *output = nullptr;
        std::vector<wf::geometry_t> dots;
        wf::geometry_t marker{0, 0, 0, 0};
        bool count_marker = false;
        bool widget = false;
    };
    std::map<uint64_t, std::shared_ptr<scottland::windowing::note_overlay_node_t>> note_dot_nodes;
    std::set<uint64_t> note_widget_dot_windows;
    std::map<wf::output_t*, std::shared_ptr<scottland::windowing::note_overlay_node_t>> note_panel_nodes;
    std::set<wf::scene::floating_inner_node_t*> note_overlay_order_dirty;
    scottland::windowing::note_layout_t note_layout;
    bool note_visuals_updating = false;
    struct note_panel_cache_entry_t
    {
        int64_t id = 0;
        std::string writer, text;
        bool full_ready = false, limited_ready = false;
        double limited_height = 0;
        scottland::windowing::note_panel_layout_t full, limited;
    };
    struct note_panel_cache_t
    {
        bool configured = false, marker_ready = false;
        std::string font_family, marker_text;
        double scale = 0, width_limit = 0, area_width = 0, area_height = 0;
        scottland::windowing::note_panel_layout_t marker;
        std::vector<note_panel_cache_entry_t> panels;
    };
    std::map<uint64_t, note_panel_cache_t> note_panel_layout_cache;

    wf::json_t notes_windows_snapshot() const
    {
        auto result = wf::json_t::array();
        for (const auto& [window_id, notes] : model.notes)
        {
            if (notes.empty()) continue;
            wf::json_t window;
            window["id"] = (int64_t)window_id;
            auto entries = wf::json_t::array();
            for (const auto& note : notes)
            {
                wf::json_t entry;
                entry["id"] = note.id;
                entry["writer"] = note.writer;
                entry["text"] = note.text;
                entries.append(entry);
            }

            window["notes"] = entries;
            result.append(window);
        }

        return result;
    }

    wf::json_t notes_slice_snapshot() const
    {
        auto result = wf::ipc::json_ok();
        result["session"] = model.session;
        result["windows"] = notes_windows_snapshot();
        return result;
    }

    void clear_note_dot(uint64_t window_id)
    {
        const bool owns_update_guard = !note_visuals_updating;
        note_visuals_updating = true;
        const bool was_widget = note_widget_dot_windows.erase(window_id) != 0;
        auto found = note_dot_nodes.find(window_id);
        if (found != note_dot_nodes.end())
        {
            if (found->second && found->second->parent())
            {
                if (!owns_update_guard && was_widget)
                    note_overlay_order_dirty.insert(found->second->parent());
                wf::scene::remove_child(found->second);
            }
            note_dot_nodes.erase(found);
        }
        if (owns_update_guard) note_visuals_updating = false;
    }

    void clear_note_overlays()
    {
        const bool owns_update_guard = !note_visuals_updating;
        note_visuals_updating = true;
        for (auto& [window_id, node] : note_dot_nodes)
            if (node && node->parent()) wf::scene::remove_child(node);
        note_dot_nodes.clear();
        note_widget_dot_windows.clear();
        for (auto& [output, node] : note_panel_nodes)
            if (node && node->parent()) wf::scene::remove_child(node);
        note_panel_nodes.clear();
        note_overlay_order_dirty.clear();
        if (owns_update_guard) note_visuals_updating = false;
    }

    void sync_note_overlay_order(bool all_parents = false)
    {
        if (!all_parents && note_overlay_order_dirty.empty()) return;
        // Reconcile each affected parent once after membership changes, or all parents once
        // after an external scene-order change. Pointer-driven hover refreshes keep ordering
        // incrementally and do not scan every note node.
        std::map<wf::scene::floating_inner_node_t*, std::vector<wf::scene::node_ptr>> notes_by_parent;
        auto append = [&] (const auto& nodes, const auto& include)
        {
            for (const auto& entry : nodes)
            {
                if (!include(entry.first)) continue;
                const auto& node = entry.second;
                if (node)
                    if (auto parent = node->parent())
                        if (all_parents || note_overlay_order_dirty.count(parent))
                        {
                            notes_by_parent[parent].push_back(node);
                        }
            }
        };
        // Panels and widget dots are overlays. Ordinary window dots stay immediately above
        // their own root, where foreground windows can correctly occlude them (NT-9).
        append(note_panel_nodes, [] (const auto&) { return true; });
        append(note_dot_nodes, [&] (uint64_t window_id)
        {
            return note_widget_dot_windows.count(window_id) != 0;
        });

        for (auto& [parent, ordered_notes] : notes_by_parent)
        {
            std::set<const wf::scene::node_t*> note_nodes;
            for (const auto& node : ordered_notes) note_nodes.insert(node.get());
            auto children = parent->get_children();
            children.erase(std::remove_if(children.begin(), children.end(), [&] (const auto& child)
            {
                return note_nodes.count(child.get()) != 0;
            }), children.end());
            children.insert(children.begin(), ordered_notes.begin(), ordered_notes.end());

            auto current = parent->get_children();
            if (children == current) continue;
            parent->set_children_list(children);
            wf::scene::update(parent, wf::scene::update_flag::CHILDREN_LIST);
            for (const auto& node : ordered_notes)
                if (node->parent() == parent) wf::scene::damage_node(node, node->get_bounding_box());
        }
        note_overlay_order_dirty.clear();
    }

    static wf::geometry_t note_clamp(wf::geometry_t rect, wf::geometry_t area)
    {
        double x = rect.x, y = rect.y;
        if (rect.width >= area.width) x = area.x;
        else if (x < area.x) x = area.x;
        else if (x + rect.width > area.x + area.width) x = area.x + area.width - rect.width;
        if (rect.height >= area.height) y = area.y;
        else if (y < area.y) y = area.y;
        else if (y + rect.height > area.y + area.height) y = area.y + area.height - rect.height;
        return {std::round(x), std::round(y), rect.width, rect.height};
    }

    static bool note_hit(const wf::geometry_t& box, double x, double y, double margin)
    {
        const double dx = std::max({double(box.x) - x, 0.0, x - (box.x + box.width)});
        const double dy = std::max({double(box.y) - y, 0.0, y - (box.y + box.height)});
        return dx * dx + dy * dy <= margin * margin;
    }

    static bool note_dot_hit(const wf::geometry_t& box, double x, double y, double margin)
    {
        const double radius = box.width / 2.0 + margin;
        const double dx = x - (box.x + box.width / 2.0), dy = y - (box.y + box.height / 2.0);
        return dx * dx + dy * dy <= radius * radius;
    }

    note_panel_cache_t& note_panel_cache_for(uint64_t window_id, wf::geometry_t area,
        double width_limit, double scale)
    {
        auto& cache = note_panel_layout_cache[window_id];
        const std::string family = hints_palette.font_family.empty() ?
            "sans-serif" : hints_palette.font_family;
        if (cache.configured && (cache.font_family != family || cache.scale != scale ||
            cache.width_limit != width_limit || cache.area_width != area.width ||
            cache.area_height != area.height))
        {
            cache = {};
        }

        if (!cache.configured)
        {
            cache.configured = true;
            cache.font_family = family;
            cache.scale = scale;
            cache.width_limit = width_limit;
            cache.area_width = area.width;
            cache.area_height = area.height;
        }

        return cache;
    }

    scottland::windowing::note_panel_layout_t cached_note_panel(note_panel_cache_t& cache,
        size_t index, const scottland::window_notes::note_t& note, double height_limit)
    {
        if (cache.panels.size() <= index) cache.panels.resize(index + 1);
        auto& entry = cache.panels[index];
        if (entry.id != note.id || entry.writer != note.writer || entry.text != note.text)
        {
            entry = {};
            entry.id = note.id;
            entry.writer = note.writer;
            entry.text = note.text;
        }

        if (height_limit == cache.area_height)
        {
            if (!entry.full_ready)
            {
                entry.full = note_layout.panel(note.writer, note.text, hints_palette,
                    cache.width_limit, height_limit);
                entry.full_ready = true;
            }

            return entry.full;
        }

        if (!entry.limited_ready || entry.limited_height != height_limit)
        {
            entry.limited = note_layout.panel(note.writer, note.text, hints_palette,
                cache.width_limit, height_limit);
            entry.limited_height = height_limit;
            entry.limited_ready = true;
        }

        return entry.limited;
    }

    scottland::windowing::note_panel_layout_t cached_note_more(note_panel_cache_t& cache,
        const std::string& label)
    {
        if (!cache.marker_ready || cache.marker_text != label)
        {
            cache.marker = note_layout.panel("", label, hints_palette,
                cache.width_limit, cache.area_height, false);
            cache.marker_text = label;
            cache.marker_ready = true;
        }

        return cache.marker;
    }

    wf::output_t *note_output_for_point(wf::pointf_t point)
    {
        auto outputs = wf::get_core().output_layout->get_outputs();
        for (auto output : outputs)
        {
            const auto geometry = output->get_layout_geometry();
            if (point.x >= geometry.x && point.x < geometry.x + geometry.width &&
                point.y >= geometry.y && point.y < geometry.y + geometry.height)
                return output;
        }

        return nullptr;
    }

    void position_window_dot(uint64_t window_id, wayfire_toplevel_view view,
        wf::output_t *view_output, wf::output_t *output,
        const std::vector<scottland::windowing::note_overlay_item_t>& items)
    {
        if (items.empty() || !view || !view_output || !output)
        {
            clear_note_dot(window_id);
            return;
        }

        auto widget = link_of_widget(view);
        const bool is_widget = bool(widget);
        const bool was_widget = note_widget_dot_windows.count(window_id) != 0;
        auto positioned = items;
        if (!widget && view_output != output)
        {
            const auto view_origin = wf::origin(view_output->get_layout_geometry());
            const auto output_origin = wf::origin(output->get_layout_geometry());
            const double dx = output_origin.x - view_origin.x;
            const double dy = output_origin.y - view_origin.y;
            for (auto& item : positioned)
            {
                item.box.x += dx;
                item.box.y += dy;
            }
        }

        auto& node = note_dot_nodes[window_id];
        if (!node) node = std::make_shared<scottland::windowing::note_overlay_node_t>();
        node->update(positioned, hints_palette, output->get_scale());
        if (is_widget) note_widget_dot_windows.insert(window_id);
        else note_widget_dot_windows.erase(window_id);

        std::shared_ptr<wf::scene::floating_inner_node_t> parent;
        auto root = view->get_root_node();
        if (widget)
        {
            parent = output->node_for_layer(wf::scene::layer::OVERLAY);
        } else if (root)
        {
            if (auto raw_parent = root->parent())
                parent = std::dynamic_pointer_cast<wf::scene::floating_inner_node_t>(raw_parent->shared_from_this());
        }

        if (!parent)
        {
            clear_note_dot(window_id);
            return;
        }

        if (node->parent() != parent.get())
        {
            auto previous_parent = node->parent();
            if (node->parent()) wf::scene::remove_child(node);
            wf::scene::add_front(parent, node);
            if (was_widget && previous_parent) note_overlay_order_dirty.insert(previous_parent);
            if (is_widget) note_overlay_order_dirty.insert(parent.get());
        } else if (was_widget != is_widget)
        {
            note_overlay_order_dirty.insert(parent.get());
        }

        if (!widget && root)
        {
            auto children = parent->get_children();
            auto existing = std::find(children.begin(), children.end(), node);
            if (existing != children.end()) children.erase(existing);
            auto owner = std::find(children.begin(), children.end(), root);
            if (owner != children.end())
            {
                children.insert(owner, node);
                auto current = parent->get_children();
                if (children != current)
                {
                    parent->set_children_list(children);
                    wf::scene::update(parent, wf::scene::update_flag::CHILDREN_LIST);
                    wf::scene::damage_node(node, node->get_bounding_box());
                }
            }
        }
    }

    void position_panel_overlay(wf::output_t *output,
        const std::vector<scottland::windowing::note_overlay_item_t>& items)
    {
        auto found = note_panel_nodes.find(output);
        if (items.empty())
        {
            if (found != note_panel_nodes.end())
            {
                if (found->second && found->second->parent()) wf::scene::remove_child(found->second);
                note_panel_nodes.erase(found);
            }

            return;
        }

        auto& node = note_panel_nodes[output];
        if (!node) node = std::make_shared<scottland::windowing::note_overlay_node_t>();
        node->update(items, hints_palette, output->get_scale());
        auto parent = output->node_for_layer(wf::scene::layer::OVERLAY);
        if (node->parent() != parent.get())
        {
            if (node->parent()) wf::scene::remove_child(node);
            wf::scene::add_front(parent, node);
        }
    }

    void append_note_panel_stack(std::vector<scottland::windowing::note_overlay_item_t>& items,
        uint64_t window_id, const std::vector<scottland::window_notes::note_t>& notes,
        wf::geometry_t area, double left, double top)
    {
        if (notes.empty()) return;
        const double scale = std::clamp(hints_palette.text_scale, 0.5, 3.0);
        const double width_limit = std::max(1.0, std::min(320 * scale, double(area.width)));
        const double gap = 6 * scale;
        auto& cache = note_panel_cache_for(window_id, area, width_limit, scale);
        std::vector<scottland::windowing::note_panel_layout_t> chosen;
        size_t shown = 0, attempted = 0;
        double used_height = 0;
        for (size_t index = 0; index < notes.size(); ++index)
        {
            attempted = index + 1;
            auto full = cached_note_panel(cache, index, notes[index], area.height);
            if (!full.drawable) break;

            const double before_panel = used_height + (shown ? gap : 0);
            const double available = area.height - before_panel;
            if (index + 1 == notes.size())
            {
                if (full.height <= available)
                {
                    chosen.push_back(std::move(full));
                    used_height = before_panel + chosen.back().height;
                    ++shown;
                } else if (available > 0)
                {
                    auto shortened = cached_note_panel(cache, index, notes[index], available);
                    if (!shortened.drawable) break;
                    chosen.push_back(std::move(shortened));
                    used_height = before_panel + chosen.back().height;
                    ++shown;
                }

                break;
            }

            const size_t remaining_after = notes.size() - index - 1;
            auto more = cached_note_more(cache, "+" + std::to_string(remaining_after) + " more");
            if (!more.drawable) break;
            const double panel_limit = available - gap - more.height;
            if (panel_limit <= 0) break;
            if (full.height <= panel_limit)
            {
                chosen.push_back(std::move(full));
                used_height = before_panel + chosen.back().height;
                ++shown;
                continue;
            }

            auto shortened = cached_note_panel(cache, index, notes[index], panel_limit);
            if (shortened.drawable)
            {
                chosen.push_back(std::move(shortened));
                used_height = before_panel + chosen.back().height;
                ++shown;
            }

            break;
        }

        if (shown < notes.size())
        {
            auto more = cached_note_more(cache, "+" + std::to_string(notes.size() - shown) + " more");
            if (!more.drawable)
            {
                cache.panels.resize(attempted);
                return;
            }

            chosen.push_back(std::move(more));
        }

        cache.panels.resize(attempted);
        if (chosen.empty()) return;
        double stack_height = 0, stack_width = 0;
        for (size_t i = 0; i < chosen.size(); ++i)
        {
            stack_width = std::max(stack_width, chosen[i].width);
            stack_height += chosen[i].height;
            if (i) stack_height += gap;
        }

        wf::geometry_t stack{left, top, std::ceil(stack_width), std::ceil(stack_height)};
        const auto placed = note_clamp(stack, area);
        const double dx = placed.x - left, dy = placed.y - top;
        double y = top + dy;
        for (size_t i = 0; i < chosen.size(); ++i)
        {
            scottland::windowing::note_overlay_item_t item;
            item.kind = scottland::windowing::note_overlay_item_t::kind_t::panel;
            item.panel = chosen[i];
            item.box = {left + dx, y, std::ceil(chosen[i].width), std::ceil(chosen[i].height)};
            items.push_back(std::move(item));
            y += chosen[i].height + gap;
        }
    }

    void step_note_visuals()
    {
        if (model.notes.empty())
        {
            clear_note_overlays();
            return;
        }

        refresh_hint_palette();
        const double scale = std::clamp(hints_palette.text_scale, 0.5, 3.0);
        const double dot_size = 8 * scale, dot_gap = 4 * scale, margin = 4 * scale;
        std::set<uint64_t> seen_dots;
        std::map<wf::output_t*, std::vector<scottland::windowing::note_overlay_item_t>> panels_by_output;
        std::map<wf::output_t*, std::vector<scottland::windowing::note_overlay_item_t>> hover_by_output;
        auto cursor = wf::get_core().get_cursor_position();

        for (const auto& [window_id, notes] : model.notes)
        {
            if (notes.empty()) continue;
            auto view = represented_view(window_id);
            if (!view || !view->is_mapped() || !view->get_output() || !view->get_root_node()->is_enabled())
                continue;
            auto view_output = view->get_output();
            auto link = link_of_widget(view);
            auto rect = scene_rectangle(view, view_output);
            if (rect.width() <= 0 || rect.height() <= 0) continue;
            const auto view_geometry = view_output->get_layout_geometry();
            wf::pointf_t visual_anchor = link ?
                wf::pointf_t{view_geometry.x + (link->rail == "left" ? rect.x2 : rect.x1),
                    view_geometry.y + rect.y2} :
                wf::pointf_t{view_geometry.x + rect.x2, view_geometry.y + rect.y1};
            const wf::pointf_t window_center{view_geometry.x + (rect.x1 + rect.x2) / 2,
                view_geometry.y + (rect.y1 + rect.y2) / 2};
            auto output = note_output_for_point(visual_anchor);
            if (!output) output = note_output_for_point(window_center);
            if (!output) output = wf::get_core().output_layout->find_closest_output(visual_anchor);
            if (!output) output = view_output;
            if (window_keys.active || in_focus_mode(view_output) || in_focus_mode(output)) continue;
            auto area = output->workarea->get_workarea();
            auto active = wf::toplevel_cast(wf::get_core().seat->get_active_view());
            const bool focused = !link && active == view;
            note_anchor_visual_t anchor;
            anchor.output = output;
            anchor.widget = link != nullptr;

            const auto output_origin = wf::origin(output->get_layout_geometry());
            const double anchor_x = visual_anchor.x - output_origin.x;
            const double anchor_y = visual_anchor.y - output_origin.y;
            std::vector<scottland::windowing::note_overlay_item_t> dot_items;
            if (link)
            {
                if (model.widget_mode == widget_mode_t::hidden || link->away) continue;
                auto box = note_clamp({anchor_x - dot_size / 2, anchor_y - dot_size / 2,
                    dot_size, dot_size}, area);
                scottland::windowing::note_overlay_item_t item;
                item.kind = scottland::windowing::note_overlay_item_t::kind_t::dot;
                item.box = box;
                dot_items.push_back(item);
            } else if (!focused)
            {
                const double x = anchor_x + 4 * scale;
                const double y = anchor_y;
                if (notes.size() <= 5)
                {
                    wf::geometry_t column{x, y, dot_size, notes.size() * dot_size + (notes.size() - 1) * dot_gap};
                    column = note_clamp(column, area);
                    for (size_t i = 0; i < notes.size(); ++i)
                    {
                        wf::geometry_t box{column.x, column.y + i * (dot_size + dot_gap), dot_size, dot_size};
                        anchor.dots.push_back(box);
                        scottland::windowing::note_overlay_item_t item;
                        item.kind = scottland::windowing::note_overlay_item_t::kind_t::dot;
                        item.box = box;
                        dot_items.push_back(item);
                    }
                } else
                {
                    const auto label = std::to_string(notes.size());
                    const auto width = note_layout.marker_width(label, hints_palette);
                    const auto height = 12 * scale;
                    auto box = note_clamp({x, y - (height - dot_size) / 2, std::ceil(width), height}, area);
                    anchor.count_marker = true;
                    anchor.marker = box;
                    scottland::windowing::note_overlay_item_t item;
                    item.kind = scottland::windowing::note_overlay_item_t::kind_t::marker;
                    item.box = box;
                    item.label = label;
                    dot_items.push_back(item);
                }
            }

            if (focused)
            {
                clear_note_dot(window_id);
                append_note_panel_stack(panels_by_output[output], window_id, notes, area,
                    anchor_x + 4 * scale, anchor_y);
                continue;
            }

            if (dot_items.empty()) continue;
            seen_dots.insert(window_id);
            position_window_dot(window_id, view, view_output, output, dot_items);
            if (link) continue;

            auto origin = wf::origin(output->get_layout_geometry());
            const double px = cursor.x - origin.x, py = cursor.y - origin.y;
            if (anchor.count_marker && note_hit(anchor.marker, px, py, margin))
            {
                append_note_panel_stack(hover_by_output[output], window_id, notes, area,
                    anchor.marker.x + anchor.marker.width + 4 * scale, anchor.marker.y);
            } else
            {
                for (size_t i = 0; i < anchor.dots.size(); ++i)
                {
                    if (!note_dot_hit(anchor.dots[i], px, py, margin)) continue;
                    const double width_limit = std::max(1.0, std::min(320 * scale, double(area.width)));
                    auto& cache = note_panel_cache_for(window_id, area, width_limit, scale);
                    auto panel = cached_note_panel(cache, i, notes[i], area.height);
                    if (panel.drawable)
                    {
                        auto box = note_clamp({anchor.dots[i].x + dot_size + 4 * scale,
                            anchor.dots[i].y, std::ceil(panel.width), std::ceil(panel.height)}, area);
                        scottland::windowing::note_overlay_item_t item;
                        item.kind = scottland::windowing::note_overlay_item_t::kind_t::panel;
                        item.box = box;
                        item.panel = std::move(panel);
                        hover_by_output[output].push_back(std::move(item));
                    }

                    break;
                }
            }
        }

        for (auto it = note_dot_nodes.begin(); it != note_dot_nodes.end();)
        {
            if (!seen_dots.count(it->first))
            {
                if (it->second && it->second->parent())
                {
                    if (note_widget_dot_windows.count(it->first))
                        note_overlay_order_dirty.insert(it->second->parent());
                    wf::scene::remove_child(it->second);
                }
                note_widget_dot_windows.erase(it->first);
                it = note_dot_nodes.erase(it);
            } else ++it;
        }

        for (auto& [output, hovered] : hover_by_output)
        {
            auto& items = panels_by_output[output];
            items.insert(items.end(), hovered.begin(), hovered.end());
        }
        for (auto& [output, items] : panels_by_output) position_panel_overlay(output, items);
        for (auto it = note_panel_nodes.begin(); it != note_panel_nodes.end();)
        {
            if (!panels_by_output.count(it->first))
            {
                if (it->second && it->second->parent()) wf::scene::remove_child(it->second);
                it = note_panel_nodes.erase(it);
            } else ++it;
        }

    }

    void update_note_visuals(bool reconcile_scene_order = false)
    {
        if (model.notes.empty() && note_dot_nodes.empty() && note_panel_nodes.empty()) return;
        if (note_visuals_updating) return;
        note_visuals_updating = true;
        if (model.notes.empty())
        {
            clear_note_overlays();
        } else
        {
            step_note_visuals();
        }
        sync_note_overlay_order(reconcile_scene_order);
        note_visuals_updating = false;
    }

    wf::ipc::method_callback notes_method = [=] (wf::json_t data) -> wf::json_t
    {
        const std::string action = data.has_member("action") && data["action"].is_string() ?
            data["action"].as_string() : "";
        if (action == "list")
        {
            auto reply = notes_slice_snapshot();
            auto entries = wf::json_t::array();
            bool filter_window = false;
            int64_t wanted_window = -1;
            if (data.has_member("window"))
            {
                if (data["window"].is_int() && data["window"].as_int64() > 0)
                {
                    filter_window = true;
                    wanted_window = data["window"].as_int64();
                } else if (data["window"].is_string() && data["window"].as_string() == "focused")
                {
                    auto active = wf::toplevel_cast(wf::get_core().seat->get_active_view());
                    if (auto link = link_of_widget(active)) wanted_window = (int64_t)link->window_id;
                    else if (active) wanted_window = active->get_id();
                    if (wanted_window <= 0)
                        return wf::ipc::json_error("focused window does not exist");
                    filter_window = true;
                } else
                {
                    return wf::ipc::json_error("window must be a positive integer or 'focused'");
                }
            }
            const bool filter_writer = data.has_member("writer") && data["writer"].is_string();
            const std::string wanted_writer = filter_writer ? data["writer"].as_string() : "";
            for (const auto& [window_id, notes] : model.notes)
            {
                if (filter_window && wanted_window != (int64_t)window_id) continue;
                wf::json_t window;
                window["id"] = (int64_t)window_id;
                auto selected = wf::json_t::array();
                for (const auto& note : notes)
                {
                    if (filter_writer && note.writer != wanted_writer) continue;
                    wf::json_t entry;
                    entry["id"] = note.id;
                    entry["writer"] = note.writer;
                    entry["text"] = note.text;
                    selected.append(entry);
                }

                if (selected.size() == 0) continue;
                window["notes"] = selected;
                entries.append(window);
            }

            reply["windows"] = entries;
            reply["version"] = (int64_t)published_version("notes");
            return reply;
        }

        if (action == "stage")
        {
            if (!data.has_member("token") || !data["token"].is_string() || data["token"].as_string().empty())
                return wf::ipc::json_error("notes stage needs a pending provenance token, writer and text");
            const std::string token = data["token"].as_string();
            auto pending = pending_provenance.find(token);
            if (pending == pending_provenance.end())
                return wf::ipc::json_error("provenance token is not pending");
            auto reject_stage = [=] (const std::string& error)
            {
                discard_pending_provenance(token);
                return wf::ipc::json_error(error);
            };
            if (data.size() != 4)
                return reject_stage("notes stage accepts only token, writer and text");
            if (!data.has_member("writer") || !data["writer"].is_string() ||
                !scottland::window_notes::valid_writer(data["writer"].as_string()))
                return reject_stage("writer must contain 1 to 64 Unicode scalar values without controls");
            if (!data.has_member("text") || !data["text"].is_string() ||
                !scottland::window_notes::valid_text(data["text"].as_string()))
                return reject_stage("text must contain 1 to 1000 Unicode scalar values; only line feed and tab controls are allowed");
            if (model.next_note_id <= 0 || model.next_note_id == std::numeric_limits<int64_t>::max())
                return reject_stage("note id space is exhausted");

            scottland::window_notes::note_t note;
            note.id = model.next_note_id++;
            note.writer = data["writer"].as_string();
            note.text = data["text"].as_string();
            pending->second.staged_notes.push_back(std::move(note));
            auto reply = wf::ipc::json_ok();
            reply["id"] = pending->second.staged_notes.back().id;
            return reply;
        }

        if (action == "add")
        {
            if (!data.has_member("writer") || !data["writer"].is_string() ||
                !scottland::window_notes::valid_writer(data["writer"].as_string()))
                return wf::ipc::json_error("writer must contain 1 to 64 Unicode scalar values without controls");
            if (!data.has_member("text") || !data["text"].is_string() ||
                !scottland::window_notes::valid_text(data["text"].as_string()))
                return wf::ipc::json_error("text must contain 1 to 1000 Unicode scalar values; only line feed and tab controls are allowed");

            int64_t window_id = 0;
            if (data.has_member("window") && data["window"].is_int())
                window_id = data["window"].as_int64();
            else if (data.has_member("window") && data["window"].is_string() &&
                data["window"].as_string() == "focused")
            {
                auto active = wf::toplevel_cast(wf::get_core().seat->get_active_view());
                if (auto link = link_of_widget(active)) window_id = link->window_id;
                else if (active) window_id = active->get_id();
            } else return wf::ipc::json_error("window must be a window id or 'focused'");

            auto window = model.windows.find(uint64_t(std::max<int64_t>(0, window_id)));
            auto target = window == model.windows.end() ? nullptr : wf::toplevel_cast(window->second.view.lock());
            if (window_id <= 0 || !target || !target->is_mapped())
                return wf::ipc::json_error("window does not exist");
            if (model.next_note_id <= 0 || model.next_note_id == std::numeric_limits<int64_t>::max())
                return wf::ipc::json_error("note id space is exhausted");

            scottland::window_notes::note_t note;
            note.id = model.next_note_id++;
            note.writer = data["writer"].as_string();
            note.text = data["text"].as_string();
            model.notes[uint64_t(window_id)].push_back(note);
            note_panel_layout_cache.erase(uint64_t(window_id));
            publish_model();
            update_note_visuals();
            auto reply = wf::ipc::json_ok();
            reply["id"] = note.id;
            return reply;
        }

        if (action == "update" || action == "remove")
        {
            if (!data.has_member("id") || !data["id"].is_int() || data["id"].as_int64() <= 0)
                return wf::ipc::json_error("note-id must be a positive integer");
            const int64_t id = data["id"].as_int64();
            if (action == "update" && (!data.has_member("text") || !data["text"].is_string() ||
                !scottland::window_notes::valid_text(data["text"].as_string())))
                return wf::ipc::json_error("text must contain 1 to 1000 Unicode scalar values; only line feed and tab controls are allowed");

            for (auto window_it = model.notes.begin(); window_it != model.notes.end(); ++window_it)
            {
                auto& notes = window_it->second;
                auto found = std::find_if(notes.begin(), notes.end(), [=] (const auto& note) { return note.id == id; });
                if (found == notes.end()) continue;
                const auto window_id = window_it->first;
                if (action == "update") found->text = data["text"].as_string();
                else notes.erase(found);
                note_panel_layout_cache.erase(window_id);
                if (notes.empty()) model.notes.erase(window_it);
                publish_model(action == "update");
                update_note_visuals();
                return wf::ipc::json_ok();
            }

            return wf::ipc::json_error("note-id does not exist");
        }

        return wf::ipc::json_error("notes action must be add, update, remove or list");
    };
