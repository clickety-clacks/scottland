// Private integration fragment for scottland_plugin_t: spread and solo (docs/spread.md).
// The solver is pure (spread.hpp). This bridge builds its snapshot from the desktop model, runs it
// in measured slices on the event loop (spread-job.hpp) until the main-loop worker takes the job
// over, and applies a result: committed outright for a hint hold or three-finger hold on the
// focused window (WK35), or offered as a presentation layer while a drag rests in the center and
// committed by the drop (the audition). Nothing else ever spreads (P4).

    wf::option_wrapper_t<int> solo_audition_delay{"scottland/solo_audition_delay"};
    wf::option_wrapper_t<double> solo_audition_hotspot{"scottland/solo_audition_hotspot"};

    // P8: 2 ms of solving per slice, then the event loop runs (input, frames) for at least 1 ms.
    // A keyboard solo commits the best checkpoint after 12 ms of solving or 30 ms of waiting.
    static constexpr auto SPREAD_SLICE = std::chrono::microseconds(2000);
    static constexpr auto SOLO_COMPUTE = std::chrono::milliseconds(12);
    static constexpr auto SOLO_WALL = std::chrono::milliseconds(30);
    static constexpr double AUDITION_SETTLE = 8;       // stillness radius, logical px (final.md 3)
    static constexpr int AUDITION_PRECOMPUTE_MS = 1000;
    static constexpr int AUDITION_EASE_MS = 240;

    // CPU time of the compositor's main thread: what Scottland itself spent, apart from any
    // time the machine gave to other processes (wall-clock figures on a shared host include it).
    static double thread_cpu_ms()
    {
        timespec t;
        clock_gettime(CLOCK_THREAD_CPUTIME_ID, &t);
        return t.tv_sec * 1e3 + t.tv_nsec / 1e6;
    }

    struct spread_run_t
    {
        std::unique_ptr<scottland::spread::job_t> job;
        std::function<void(const scottland::spread::result_t&)> deliver;
        std::chrono::steady_clock::time_point started;
        std::chrono::nanoseconds compute_limit{0}, wall_limit{0}; // 0: until finished
        std::string purpose;
        // The event loop's own time between two slices (frames, input): reported, since delivery
        // can only happen when the loop comes back to the solve.
        std::chrono::steady_clock::time_point last_slice_end;
        std::chrono::nanoseconds longest_gap{0};
    };
    std::optional<spread_run_t> spread_run;
    wf::wl_timer<true> spread_tick;
    wf::json_t spread_last;  // the last delivered solve (scottland/spread-state)
    uint64_t spread_solves = 0;
    // Test sessions only (SCOTTLAND_TEST_MODEL): stretch a solve over many event-loop turns, so a
    // reload or an input can land while it is in flight.
    bool spread_test_slow = false;

    // ------------------------------------------------------------------ snapshot
    // Everything a result depends on besides the solo window: zone settings, the output, and
    // every window and widget on it. A result is applied only while this is unchanged.
    std::string spread_signature(wf::output_t *output, uint64_t excluded)
    {
        std::ostringstream out;
        if (!output_alive(output)) return "";
        auto screen = output->get_relative_geometry(); auto a = output->workarea->get_workarea();
        out << double(center_width) << ',' << double(rail_width) << ',' << double(min_scale) << ',' <<
            double(max_scale) << ',' << double(blend_width) << ',' << std::string(scale_curve_text) << ';' <<
            screen.width << 'x' << screen.height << ';' << a.x << ',' << a.y << ',' << a.width << ',' << a.height << ';';
        for (auto& view : output->wset()->get_views(wf::WSET_MAPPED_ONLY))
        {
            if (view->get_id() == excluded) continue;
            if (auto link = link_of_widget(view); link && link->window_id == excluded) continue;
            auto g = view->get_geometry();
            auto found = model.windows.find(view->get_id());
            out << view->get_id() << ':' << g.x << ',' << g.y << ',' << g.width << ',' << g.height << ',' <<
                view->pending_fullscreen() << ',' << is_widget(view) << ',' <<
                (found != model.windows.end() && found->second.pinned_scale ? *found->second.pinned_scale : 0.0) << ';';
        }
        return out.str();
    }

    std::optional<scottland::spread::snapshot_t> spread_snapshot(wf::output_t *output, uint64_t solo,
        scottland::spread::box solo_box)
    {
        namespace sp = scottland::spread;
        if (!output_alive(output)) return std::nullopt;
        // The solver's caps, checked in constant time before anything is collected.
        if (model.windows.size() + 1 > sp::MAX_OBSTACLES)
        {
            LOGI("scottland: spread: ", model.windows.size(), " windows, more than spread considers");
            return std::nullopt;
        }
        auto screen = output->get_relative_geometry(); auto a = output->workarea->get_workarea();
        sp::snapshot_t s;
        double W = screen.width;
        s.screen_width = W; s.screen_height = screen.height;
        s.workarea = {double(a.x), double(a.y), double(a.x + a.width), double(a.y + a.height)};
        s.padding = std::ceil(SCREEN_PADDING);
        double cw = center_width, rw = rail_width, mn = std::clamp((double)min_scale, 0.05, 1.0);
        double mx = std::clamp((double)max_scale, 0.05, 1.0), bl = std::max(0.0, (double)blend_width);
        s.center_half = W * std::clamp(cw, 0.0, 100.0) / 200.0;
        s.rail_width = W * std::clamp(rw, 0.0, 50.0) / 100.0;
        // An immutable copy of the scale function: the snapshot never reads the plugin.
        s.scale = scale_function(W, cw, rw, mn, mx, scale_curve, bl);
        // WP4: the first center whose natural scale reads as scaled (zone_spot's search).
        double inner = W / 2 - s.center_half - 1, outer = s.rail_width + 1;
        double outer_scale = s.scale(outer);
        double threshold = 1 - std::min(0.05, std::max(0.0, (1 - outer_scale) / 2));
        for (int step = 1; step <= 128; ++step)
        {
            double trial = inner + (outer - inner) * step / 128;
            if (s.scale(trial) > threshold) continue;
            s.arrival_inset = (W / 2 - trial) - s.center_half;
            break;
        }
        s.solo = solo_box;
        auto rect_of = [] (scottland::rectf_t r) { return sp::box{r.x1, r.y1, r.x2, r.y2}; };
        for (auto e : window_entries())
        {
            if (e.id == solo) continue;
            auto window = wf::toplevel_cast(view_by_id(e.id));
            auto shown = represented_view(e.id);
            if (!window || !shown || !shown->is_mapped() || shown->get_output() != output) continue;
            if (shown != window)
            {
                // A widget (or one launching) is a fixed thing on its rail.
                if (auto frame = frame_of(shown, false)) s.fixed.push_back(rect_of(frame->screen_rect()));
                continue;
            }
            if (window->pending_fullscreen() || drag->view == window) continue;
            auto g = window->get_geometry();
            sp::window_t w;
            w.id = e.id;
            w.width = g.width; w.height = g.height;
            w.cx = g.x + g.width / 2.0; w.cy = g.y + g.height / 2.0;
            auto found = model.windows.find(e.id);
            w.pinned = found != model.windows.end() && found->second.pinned_scale.has_value();
            w.scale = std::clamp(scale_for(window), 0.05, 1.0);
            auto zone = place_at(w.cx, W).zone;
            w.role = zone == zone_t::center ? sp::role_t::arrival :
                (zone == zone_t::continuous ? sp::role_t::resident : sp::role_t::fixed);
            auto rank = std::find(focus_recency.begin(), focus_recency.end(), e.id);
            w.recency = uint32_t(rank - focus_recency.begin());
            w.side_memory = int8_t(ensure_window_memory(e.id).last_side);
            s.windows.push_back(w);
        }
        return s;
    }

    // ------------------------------------------------------------------ the sliced runner
    void spread_start(scottland::spread::snapshot_t snapshot, std::string purpose,
        std::chrono::nanoseconds compute_limit, std::chrono::nanoseconds wall_limit,
        std::function<void(const scottland::spread::result_t&)> deliver)
    {
        spread_cancel();
        spread_run.emplace();
        auto& run = *spread_run;
        run.job = std::make_unique<scottland::spread::job_t>(std::move(snapshot));
        run.deliver = std::move(deliver);
        run.started = std::chrono::steady_clock::now();
        run.compute_limit = spread_test_slow ? std::chrono::nanoseconds(0) : compute_limit;
        run.wall_limit = spread_test_slow ? std::chrono::nanoseconds(0) : wall_limit;
        run.purpose = std::move(purpose);
        // The first slice runs now (the key press or the pause); the rest from a timer, so input
        // and frames are served between slices.
        if (spread_step()) spread_tick.set_timeout(1, [=] () { SCOTTLAND_LOOP_SCOPE(spread_tick); return spread_step(); });
    }

    double spread_cancel_ms = 0;  // the last cancellation, unwinding included

    void spread_cancel()
    {
        spread_tick.disconnect();
        auto started = std::chrono::steady_clock::now();
        bool had = spread_run.has_value();
        spread_run.reset();  // an unfinished job unwinds its solve and frees its stack
        if (had) spread_cancel_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - started).count();
    }

    // One slice. False when the run was delivered (or is gone).
    bool spread_step()
    {
        if (!spread_run) return false;
        auto& run = *spread_run;
        auto now = std::chrono::steady_clock::now();
        if (run.job->slices)
            run.longest_gap = std::max(run.longest_gap,
                std::chrono::duration_cast<std::chrono::nanoseconds>(now - run.last_slice_end));
        // At the wall limit, deliver what exists now; a further slice would only make it later.
        auto waited = now - run.started;
        if (run.wall_limit.count() && waited >= run.wall_limit) { spread_deliver(); return false; }
        std::chrono::nanoseconds allowance = spread_test_slow ? std::chrono::nanoseconds(1) :
            std::chrono::nanoseconds(SPREAD_SLICE);
        if (run.wall_limit.count()) allowance = std::min(allowance, run.wall_limit - waited);
        bool done = run.job->step(allowance);
        run.last_slice_end = std::chrono::steady_clock::now();
        waited = run.last_slice_end - run.started;
        bool out = done || (run.compute_limit.count() && run.job->total >= run.compute_limit) ||
            (run.wall_limit.count() && waited >= run.wall_limit);
        if (!out) return true;
        spread_deliver();
        return false;
    }

    // Deliver now: the complete result, or the best validated checkpoint so far.
    void spread_deliver()
    {
        if (!spread_run) return;
        auto entered = std::chrono::steady_clock::now();
        double entered_cpu = thread_cpu_ms();
        spread_tick.disconnect();
        auto run = std::move(*spread_run);
        spread_run.reset();
        auto result = run.job->current();
        double waited = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - run.started).count();
        double solving = std::chrono::duration<double, std::milli>(run.job->total).count();
        double longest = std::chrono::duration<double, std::milli>(run.job->longest).count();
        ++spread_solves;
        LOGI("scottland: spread (", run.purpose, "): ", scottland::spread::status_name(result.status),
            " via ", result.checkpoint.empty() ? "nothing" : result.checkpoint, ", ", result.moves.size(), " moves, ",
            run.job->input().windows.size(), " windows, work ", result.work, ", ", run.job->slices, " slices, longest ",
            longest, " ms, solving ", solving, " ms, waited ", waited, " ms",
            result.reason.empty() ? "" : "; ", result.reason);
        wf::json_t record;
        record["purpose"] = run.purpose;
        record["status"] = std::string(scottland::spread::status_name(result.status));
        record["checkpoint"] = result.checkpoint;
        record["reason"] = result.reason;
        record["complete"] = result.complete;
        record["work"] = (int64_t)result.work;
        record["windows"] = (int64_t)run.job->input().windows.size();
        record["slices"] = (int64_t)run.job->slices;
        record["longest_slice_ms"] = longest;
        record["solving_ms"] = solving;
        record["waited_ms"] = waited;
        record["longest_gap_ms"] = std::chrono::duration<double, std::milli>(run.longest_gap).count();
        record["wall_limit_ms"] = std::chrono::duration<double, std::milli>(run.wall_limit).count();
        record["spacing"] = result.spacing;
        record["sequence"] = (int64_t)spread_solves;
        wf::json_t moves = wf::json_t::array();
        for (const auto& m : result.moves)
        {
            wf::json_t move;
            move["id"] = (int64_t)m.id; move["x"] = m.cx; move["y"] = m.cy; move["scale"] = m.scale;
            move["arrival"] = m.arrival;
            if (m.pin) move["pin"] = *m.pin;
            moves.append(move);
        }
        record["moves"] = moves;
        wf::json_t overlaps = wf::json_t::array();
        for (auto [a, b] : result.overlaps)
        {
            wf::json_t pair = wf::json_t::array();
            pair.append((int64_t)a); pair.append(b == UINT64_MAX ? (int64_t)-1 : (int64_t)b);
            overlaps.append(pair);
        }
        record["overlaps"] = overlaps;
        spread_last = record;
        run.job.reset();
        if (run.deliver) run.deliver(result);
        // The whole delivery callback on the event loop: result copy, record, the job's
        // destruction and the caller's commit (apart from the slices themselves).
        spread_last["deliver_ms"] = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - entered).count();
        spread_last["deliver_cpu_ms"] = thread_cpu_ms() - entered_cpu;
    }

    // ------------------------------------------------------------------ committing moves
    // One transaction: every moved window glides from where it is drawn now (mid-glide or
    // mid-audition included) to its destination and destination scale. Pins follow the result
    // (cleared unless kept); memories are recorded for moved windows only.
    void commit_spread_moves(const std::vector<scottland::spread::move_t>& moves)
    {
        publish_batch_t batch(this);
        for (const auto& m : moves)
        {
            auto view = wf::toplevel_cast(view_by_id(m.id));
            if (!view || !view->is_mapped() || link_of_window(view) || is_widget(view)) continue;
            auto frame = frame_of(view, false);
            auto g = view->get_geometry();
            wf::pointf_t from{g.x + g.width / 2.0, g.y + g.height / 2.0};
            double from_scale = displayed_scale(view);
            if (frame)
            {
                auto drawn = frame->screen_rect();
                from = {(drawn.x1 + drawn.x2) / 2, (drawn.y1 + drawn.y2) / 2};
                from_scale = frame->scale_x * frame->drag_layout_scale;
            }
            set_drag_layout_offset(view, 0, 0, 1);
            keyboard_motions.erase(m.id);
            stop_glide(view);
            pin_scale(view, m.pin);
            move_window(view, std::round(m.cx - g.width / 2.0), std::round(m.cy - g.height / 2.0));
            remember_window(view);
            start_cycle_glide(view, from, from_scale, {m.cx, m.cy}, m.scale);
        }
        declutter_signature.clear();
    }

    // The solo window holds still for window avoidance, as a pair does (WK36): the windows behind it
    // peek out around it, until it moves, is resized or becomes a widget.
    std::optional<std::pair<uint64_t, wf::geometry_t>> solo_anchor;
    bool solo_anchored(wayfire_toplevel_view view)
    {
        if (!view || !solo_anchor) return false;
        auto [id, geometry] = *solo_anchor;
        auto window = wf::toplevel_cast(view_by_id(id));
        if (!window || !window->is_mapped() || link_of_window(window) || window->toplevel()->pending().geometry != geometry)
        { solo_anchor.reset(); return false; }
        return view->get_id() == id;
    }
    void anchor_solo(wayfire_toplevel_view window)
    {
        if (window && window->is_mapped() && !link_of_window(window))
            solo_anchor = std::make_pair(window->get_id(), window->toplevel()->pending().geometry);
        declutter_signature.clear();
    }

    // ------------------------------------------------------------------ keyboard solo (WK35)
    // Where the solo window goes: where it is if it is already in the center zone (P2, P14);
    // else its remembered center spot (WP2); else the middle of the screen. Whole and padded
    // on screen when it fits (WP7).
    wf::pointf_t solo_target(wayfire_toplevel_view window, wf::output_t *output)
    {
        auto screen = output->get_relative_geometry();
        auto g = window->get_geometry();
        wf::pointf_t at{g.x + g.width / 2.0, g.y + g.height / 2.0};
        bool shown_as_window = !link_of_window(window);
        if (!(shown_as_window && window->get_output() == output && place_at(at.x, screen.width).zone == zone_t::center))
        {
            auto& memory = ensure_window_memory(window->get_id());
            if (auto p = memory.positions[size_t(scottland::windowing::zone::center)])
                at = {p->x * screen.width, p->y * screen.height};
            else
            {
                auto a = output->workarea->get_workarea();
                at = {screen.width / 2.0, a.y + a.height / 2.0};
            }
            auto pa = padded(output->workarea->get_workarea(), g.width, g.height);
            if (g.width <= pa.width) at.x = std::clamp(at.x, pa.x + g.width / 2.0, pa.x + pa.width - g.width / 2.0);
            if (g.height <= pa.height) at.y = std::clamp(at.y, pa.y + g.height / 2.0, pa.y + pa.height - g.height / 2.0);
        }
        return {scottland::spread::representable(at.x, g.width), scottland::spread::representable(at.y, g.height)};
    }

    void solo_window(uint64_t id)
    {
        auto window = wf::toplevel_cast(view_by_id(id));
        auto shown = represented_view(id);
        if (!window || !shown || !shown->get_output() || drag->view) return;
        if (window->pending_fullscreen())
        {
            LOGI("scottland: solo ", id, ": full screen, nothing to spread");
            return;
        }
        auto output = shown->get_output();
        auto at = solo_target(window, output);
        auto g = window->get_geometry();
        scottland::spread::box solo{at.x - g.width / 2.0, at.y - g.height / 2.0, at.x + g.width / 2.0, at.y + g.height / 2.0};
        auto captured = std::chrono::steady_clock::now();
        auto snapshot = spread_snapshot(output, id, solo);
        if (!snapshot) return;
        auto signature = spread_signature(output, id);
        double snapshot_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - captured).count();
        bool widget_before = bool(link_of_window(window));
        LOGI("scottland: solo ", id, " to ", at.x, ",", at.y, " (", snapshot->windows.size(), " windows)");
        spread_start(std::move(*snapshot), "solo", SOLO_COMPUTE, SOLO_WALL,
            [=] (const scottland::spread::result_t& result) {
                // The snapshot still holds: the desktop and the solo window itself (its size, its
                // form, its output) are what they were when the key was held.
                auto now_window = wf::toplevel_cast(view_by_id(id));
                auto shown_now = represented_view(id);
                if (spread_signature(output, id) != signature || !now_window || !shown_now ||
                    shown_now->get_output() != output || bool(link_of_window(now_window)) != widget_before ||
                    now_window->get_geometry().width != g.width || now_window->get_geometry().height != g.height)
                {
                    LOGI("scottland: solo ", id, ": the desktop changed while solving; not applied");
                    return;
                }
                auto started = std::chrono::steady_clock::now();
                double started_cpu = thread_cpu_ms();
                commit_solo(id, at, result);
                spread_last["commit_cpu_ms"] = thread_cpu_ms() - started_cpu;
                spread_last["snapshot_ms"] = snapshot_ms;
                spread_last["commit_ms"] = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - started).count();
            });
    }

    // Committed outright, no undo (P5, decision 5): the other windows' moves and the solo
    // window's own move to the center, in one transaction. A result that moves nothing
    // (unchanged, unavailable) still takes the solo window where it was asked to go.
    void commit_solo(uint64_t id, wf::pointf_t at, const scottland::spread::result_t& result)
    {
        publish_batch_t batch(this);
        auto window = wf::toplevel_cast(view_by_id(id));
        if (!window || !window->is_mapped()) return;
        commit_spread_moves(result.moves);
        if (auto link = link_of_window(window))
        {
            restore_window(*link, at, true);
        } else
        {
            auto g = window->get_geometry();
            auto frame = frame_of(window, false);
            wf::pointf_t from{g.x + g.width / 2.0, g.y + g.height / 2.0};
            double from_scale = displayed_scale(window);
            if (frame)
            {
                auto drawn = frame->screen_rect();
                from = {(drawn.x1 + drawn.x2) / 2, (drawn.y1 + drawn.y2) / 2};
                from_scale = frame->scale_x * frame->drag_layout_scale;
            }
            keyboard_motions.erase(id);
            stop_glide(window);
            pin_scale(window, std::nullopt);  // the center is full scale (tenet 4)
            if (std::abs(g.x + g.width / 2.0 - at.x) > 0.25 || std::abs(g.y + g.height / 2.0 - at.y) > 0.25)
            {
                move_window(window, std::round(at.x - g.width / 2.0), std::round(at.y - g.height / 2.0));
                remember_window(window);
            }
            start_cycle_glide(window, from, from_scale, at, 1.0);
        }
        // The solo window is in front of everything it may still overlap, and holds still for
        // peeking while it stays where it was put.
        wf::get_core().default_wm->focus_raise_view(window);
        anchor_solo(window);
        refresh_layout_avoidance();
        publish_model();
    }

    // ------------------------------------------------------------------ the drag audition
    struct audition_actor_t
    {
        uint64_t id = 0;
        double ox = 0, oy = 0, os = 1;  // true center and scale (never an animation sample)
        double tx = 0, ty = 0, ts = 1;  // where the solve puts it
    };
    struct audition_t
    {
        bool watching = false;              // a drag that may audition: stillness is being timed
        wf::pointf_t still{0, 0};           // where the pointer stopped (layout coordinates)
        std::chrono::steady_clock::time_point still_since;
        wf::output_t *output = nullptr;
        uint64_t dragged = 0;
        bool solving = false, offered = false;
        wf::pointf_t anchor{0, 0};          // pointer when the solve was armed (layout)
        std::string signature;              // everything but the dragged window
        wf::dimensions_t dragged_size{0, 0};  // the dragged window's size when it was armed
        double radius = 50;                 // the hotspot radius the reservation was built with
        scottland::spread::box reserved;
        std::optional<scottland::spread::result_t> result;
        std::string cache_key;              // an identical snapshot and reservation reuse it
        std::optional<scottland::spread::result_t> cached;
        std::vector<audition_actor_t> actors;
        struct suspended_t { uint64_t id; glide_t glide; std::chrono::steady_clock::time_point at; };
        std::vector<suspended_t> suspended;   // glides paused under the offer
        double progress = 0, ease_from = 0, ease_to = 0;
        std::chrono::steady_clock::time_point ease_started;
        unsigned offers = 0, denials = 0, accepts = 0;
        std::string last_event;
    } audition;
    wf::wl_timer<false> audition_timer;
    wf::wl_timer<true> audition_tick;
    wf::wl_timer<true> audition_watch;  // validity while an offer shows, with or without motion

    double audition_hotspot() const { return std::clamp((double)solo_audition_hotspot, 8.0, 400.0); }

    bool audition_holds(wayfire_toplevel_view view) const
    {
        if (!view || audition.actors.empty()) return false;
        return std::any_of(audition.actors.begin(), audition.actors.end(),
            [&] (const auto& a) { return a.id == view->get_id(); });
    }

    // Draw the audition-owned layer at `progress` (0: true layout, 1: the offered one).
    // Draw the audition-owned layer at `progress` (0: as it is, 1: the offered layout). It is
    // computed every frame against what lies under it (the true geometry plus any glide or
    // scale animation), so at 1 each window is drawn exactly at its offered spot and scale
    // whatever runs underneath, and easing back ends on the live state with no jump.
    void audition_present()
    {
        double p = audition.progress;
        for (const auto& a : audition.actors)
        {
            auto view = wf::toplevel_cast(view_by_id(a.id));
            auto frame = view ? frame_of(view, false) : nullptr;
            if (!frame) continue;
            auto g = view->get_geometry();
            double ux = g.x + g.width / 2.0 + frame->translation_x, uy = g.y + g.height / 2.0 + frame->translation_y;
            double us = std::max(0.05, (double)frame->scale_x);
            set_drag_layout_offset(view, p * (a.tx - ux), p * (a.ty - uy), (us + p * (a.ts - us)) / us);
        }
    }

    bool audition_step()
    {
        double elapsed = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() -
            audition.ease_started).count();
        double t = std::clamp(elapsed / AUDITION_EASE_MS, 0.0, 1.0);
        double eased = t * t * (3 - 2 * t);
        audition.progress = audition.ease_from + (audition.ease_to - audition.ease_from) * eased;
        audition_present();
        if (t < 1 || (audition.ease_to > 0 && audition.offered)) return true;  // keep tracking while shown
        if (audition.ease_to == 0)
        {
            for (const auto& a : audition.actors) set_drag_layout_offset(wf::toplevel_cast(view_by_id(a.id)), 0, 0, 1);
            audition.actors.clear();
            refresh_layout_avoidance();
        }
        return false;
    }

    // Resume glides the offer suspended, from the sample where they stopped, to their own
    // (unchanged) destination.
    void audition_resume_glides()
    {
        for (auto& paused : audition.suspended)
        {
            auto view = wf::toplevel_cast(view_by_id(paused.id));
            if (!view || !view->is_mapped() || glides.count(paused.id)) continue;
            auto glide = paused.glide;
            if (glide.cycle)
                glide.started += std::chrono::steady_clock::now() - paused.at;
            else if (auto frame = frame_of(view, false))
            {
                glide.dx = frame->translation_x; glide.dy = frame->translation_y;
                glide.progress.animate(0.0, 1.0);
            }
            glides[paused.id] = glide;
            if (!glide_tick.is_connected()) glide_tick.set_timeout(8, [=] () { SCOTTLAND_LOOP_SCOPE(spread_glide_tick); return step_glides(); });
        }
        audition.suspended.clear();
    }

    void audition_ease(double to)
    {
        audition.ease_from = audition.progress;
        audition.ease_to = to;
        audition.ease_started = std::chrono::steady_clock::now();
        if (!audition_tick.is_connected()) audition_tick.set_timeout(8, [=] () { SCOTTLAND_LOOP_SCOPE(audition_tick); return audition_step(); });
    }

    // Stop solving and forget the result (the windows return if an offer was showing).
    void audition_reset(const char *why)
    {
        if (audition.solving) spread_cancel();
        audition.solving = false;
        audition.result.reset();
        audition_timer.disconnect();
        if (audition.offered)
        {
            audition.offered = false;
            ++audition.denials;
            audition.last_event = why;
            LOGI("scottland: solo audition refused (", why, "): ", audition.actors.size(), " windows return");
            audition_resume_glides();
            audition_ease(0);
        }
    }

    // The drag ended or can no longer audition: nothing is offered or pending.
    void audition_end(const char *why)
    {
        audition_reset(why);
        audition.watching = false;
        audition.output = nullptr;
        audition.dragged = 0;
    }

    void audition_restart(wf::pointf_t pointer)
    {
        audition.still = pointer;
        audition.still_since = std::chrono::steady_clock::now();
        audition_schedule();
    }

    void audition_schedule()
    {
        int delay = solo_audition_delay;
        if (delay <= 0 || !audition.watching || audition.offered) return;
        int precompute = std::min(AUDITION_PRECOMPUTE_MS, delay / 3);
        int elapsed = int(std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() -
            audition.still_since).count());
        int next = (!audition.solving && !audition.result) ? precompute : delay;
        audition_timer.set_timeout(std::max(1, next - elapsed), [=] () { SCOTTLAND_LOOP_SCOPE(audition_timer); audition_due(); });
    }

    // The dragged window's drawn center, output-local, and whether it may audition now.
    std::optional<wf::pointf_t> audition_center(wayfire_toplevel_view view, wf::output_t *output)
    {
        if (!view || !output || view->get_output() != output || is_widget(view) || view->pending_fullscreen()) return {};
        if (model.drag.morph && model.drag.morph->dragged.lock().get() == view.get() && model.drag.morph->toward) return {};
        if (shift_held()) return {};  // a Shift drag keeps its scale (L31): no audition
        auto r = scene_rectangle(view, output);
        wf::pointf_t c{(r.x1 + r.x2) / 2, (r.y1 + r.y2) / 2};
        if (place_at(c.x, output->get_relative_geometry().width).zone != zone_t::center) return {};
        return c;
    }

    // Drag motion (on_drag_motion): time the pause, and refuse an offer when the pointer leaves
    // the hotspot or the window leaves the center zone.
    void audition_motion(wayfire_toplevel_view view, wf::output_t *output, wf::pointf_t pointer)
    {
        if (solo_audition_delay <= 0) { audition_end("off"); return; }
        auto center = audition_center(view, output);
        if (audition.offered)
        {
            if (!center || view->get_id() != audition.dragged || output != audition.output)
            {
                audition_reset("left the center zone");
                if (center) audition_restart(pointer);
                return;
            }
            if (std::hypot(pointer.x - audition.anchor.x, pointer.y - audition.anchor.y) > audition.radius)
            {
                audition_reset("left the hotspot");
                audition_restart(pointer);
            } else if (spread_signature(output, audition.dragged) != audition.signature)
            {
                audition_reset("the desktop changed");  // e.g. zone settings: no signal reaches here
                audition_restart(pointer);
            }
            return;
        }
        if (!center) { audition_end("not eligible"); return; }
        if (!audition.watching || audition.output != output || audition.dragged != view->get_id() ||
            std::hypot(pointer.x - audition.still.x, pointer.y - audition.still.y) > AUDITION_SETTLE)
        {
            audition_reset("moved");
            audition.watching = true;
            audition.output = output;
            audition.dragged = view->get_id();
            audition_restart(pointer);
        }
    }

    void audition_due()
    {
        auto view = drag->view ? wf::toplevel_cast(drag->view) : nullptr;
        auto output = drag->current_output;
        if (!audition.watching || !view || view->get_id() != audition.dragged || output != audition.output)
        { audition_end("drag ended"); return; }
        auto center = audition_center(view, output);
        if (!center) { audition_end("not eligible"); return; }
        int delay = solo_audition_delay;
        int elapsed = int(std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() -
            audition.still_since).count());
        if (!audition.solving && !audition.result)
        {
            // Arm: freeze the anchor and reserve the union of the window's full-scale footprints
            // over every position the hotspot allows, so any accepted drop is honest (P14).
            audition.anchor = model.drag.input_override.value_or(wf::get_core().get_cursor_position());
            auto g = view->get_geometry();
            double r = audition.radius = audition_hotspot();  // the reservation's radius, fixed for this offer
            audition.reserved = {center->x - g.width / 2.0 - r, center->y - g.height / 2.0 - r,
                center->x + g.width / 2.0 + r, center->y + g.height / 2.0 + r};
            audition.signature = spread_signature(output, audition.dragged);
            audition.dragged_size = {g.width, g.height};
            std::ostringstream key;
            key << audition.signature << '|' << audition.reserved.x0 << ',' << audition.reserved.y0 << ',' <<
                audition.reserved.x1 << ',' << audition.reserved.y1;
            if (audition.cached && audition.cache_key == key.str())
            {
                audition.result = audition.cached;
            } else if (auto snapshot = spread_snapshot(output, audition.dragged, audition.reserved))
            {
                audition.solving = true;
                auto wall = std::chrono::milliseconds(std::max(1, delay - elapsed));
                spread_start(std::move(*snapshot), "audition", std::chrono::nanoseconds(0), wall,
                    [=, k = key.str()] (const scottland::spread::result_t& result) {
                        audition.solving = false;
                        audition.result = result;
                        audition.cache_key = k;
                        audition.cached = result;
                    });
            }
        }
        if (elapsed >= delay)
        {
            if (audition.solving) spread_deliver();  // the best checkpoint so far
            audition_offer();
            return;
        }
        audition_schedule();
    }

    void audition_offer()
    {
        if (!audition.result || audition.offered) return;
        const auto& result = *audition.result;
        if (spread_signature(audition.output, audition.dragged) != audition.signature)
        {
            audition.result.reset();
            audition.still_since = std::chrono::steady_clock::now();
            audition_schedule();
            return;
        }
        using S = scottland::spread::status_t;
        if (result.moves.empty() || result.status == S::unchanged_budget || result.status == S::unchanged_exhausted ||
            result.status == S::unavailable)
        {
            LOGI("scottland: solo audition: nothing to offer (", scottland::spread::status_name(result.status), ")");
            return;
        }
        // A window still coasting or gliding away is changing its true state: not an offer yet.
        for (const auto& m : result.moves)
        {
            auto found = glides.find(m.id);
            if (keyboard_motions.count(m.id) || (found != glides.end() && found->second.outward))
            {
                audition.result.reset();
                audition.still_since = std::chrono::steady_clock::now();
                audition_schedule();
                return;
            }
        }
        if (!audition.actors.empty()) for (const auto& a : audition.actors)
            set_drag_layout_offset(wf::toplevel_cast(view_by_id(a.id)), 0, 0, 1);
        audition.actors.clear();
        audition_resume_glides();
        for (const auto& m : result.moves)
        {
            auto view = wf::toplevel_cast(view_by_id(m.id));
            if (!view || !view->is_mapped()) continue;
            auto g = view->get_geometry();
            audition_actor_t actor{m.id, g.x + g.width / 2.0, g.y + g.height / 2.0,
                std::clamp(scale_for(view), 0.05, 1.0), m.cx, m.cy, m.scale};
            // A running glide is suspended at its current sample (true destination kept) and the
            // offer starts from what is drawn, so the offered spot is exact (final.md section 3).
            auto frame = frame_of(view, false);
            auto glide = glides.find(m.id);
            if (frame && glide != glides.end())
            {
                actor.ox += frame->translation_x;
                actor.oy += frame->translation_y;
                actor.os = std::max(0.05, (double)frame->scale_x);
                audition.suspended.push_back({m.id, glide->second, std::chrono::steady_clock::now()});
                glides.erase(glide);
            }
            audition.actors.push_back(actor);
        }
        audition.progress = 0;
        audition.offered = true;
        ++audition.offers;
        audition.last_event = "offered";
        LOGI("scottland: solo audition offered: ", audition.actors.size(), " windows move (",
            scottland::spread::status_name(result.status), ")");
        audition_ease(1);
        refresh_layout_avoidance();
        audition_watch.set_timeout(100, [=] () { SCOTTLAND_LOOP_SCOPE(audition_watch); return audition_valid_tick(); });
    }

    // The offer stays only while it is still the offer it was: the same dragged window, size,
    // output and drag mode (no Shift), and an unchanged desktop and zone configuration. Checked
    // every 100 ms as well as on motion, since a key press or a setting moves no pointer.
    bool audition_valid_tick()
    {
        if (!audition.offered) return false;
        auto view = drag->view ? wf::toplevel_cast(drag->view) : nullptr;
        const char *why = nullptr;
        if (!view || view->get_id() != audition.dragged || drag->current_output != audition.output) why = "the drag changed";
        else if (shift_held()) why = "Shift";
        else if (audition_hotspot() != audition.radius) why = "the hotspot setting changed";
        else if (view->get_geometry().width != audition.dragged_size.width ||
            view->get_geometry().height != audition.dragged_size.height) why = "the window was resized";
        else if (spread_signature(audition.output, audition.dragged) != audition.signature) why = "the desktop changed";
        if (!why) return true;
        audition_reset(why);
        if (view) audition_restart(model.drag.input_override.value_or(wf::get_core().get_cursor_position()));
        return false;
    }

    // The drop (on_drag_done): inside the hotspot with the window still in the center zone, the
    // offer is accepted and committed where it stands; anything else refuses it. True: accepted
    // (the dropped window stays exactly where it was dropped, so it does not coast).
    bool audition_drop(wayfire_toplevel_view main, wf::pointf_t released)
    {
        // Still the offer it was: inside the hotspot, not a Shift drop (L31), nothing changed since.
        bool accept = audition.offered && main && main->get_id() == audition.dragged && main->get_output() == audition.output &&
            std::hypot(released.x - audition.anchor.x, released.y - audition.anchor.y) <= audition.radius &&
            audition_hotspot() == audition.radius &&
            !shift_held() && spread_signature(audition.output, audition.dragged) == audition.signature &&
            main->get_geometry().width == audition.dragged_size.width &&
            main->get_geometry().height == audition.dragged_size.height;
        if (accept)
        {
            // Defensively: the dropped window, at full scale, lies inside the reservation the
            // offer was solved for (P14: the drop stays put, so the room must be there).
            auto g = main->get_geometry();
            const auto& rv = audition.reserved;
            accept = place_at(g.x + g.width / 2.0, main->get_output()->get_relative_geometry().width).zone == zone_t::center &&
                g.x >= rv.x0 - 0.5 && g.y >= rv.y0 - 0.5 && g.x + g.width <= rv.x1 + 0.5 && g.y + g.height <= rv.y1 + 0.5;
        }
        if (!accept) { audition_end("dropped outside the offer"); return false; }
        ++audition.accepts;
        audition.last_event = "accepted";
        LOGI("scottland: solo audition accepted: ", audition.result->moves.size(), " windows move");
        auto moves = audition.result->moves;
        audition.suspended.clear();  // the commit rebases each window from where it is drawn
        audition_tick.disconnect();
        audition.actors.clear();
        audition.offered = false;
        audition_end("accepted");
        commit_spread_moves(moves);
        wf::get_core().default_wm->focus_raise_view(main);
        anchor_solo(main);
        refresh_layout_avoidance();
        publish_model();
        return true;
    }

    // A client mapped, closed or resized, or the zones changed: an offer ends as a refusal (an
    // independent change is never rolled back) and the pause is timed again.
    void audition_invalidate(wayfire_view view, bool resized = false)
    {
        if (!audition.watching || (!audition.solving && !audition.result && !audition.offered)) return;
        // The dragged window moving is the drag itself; resizing it voids the reservation.
        if (view && view->get_id() == audition.dragged && !resized) return;
        audition_reset("the desktop changed");
        audition.still_since = std::chrono::steady_clock::now();
        audition_schedule();
    }

    wf::json_t audition_state()
    {
        wf::json_t a;
        a["watching"] = audition.watching;
        a["solving"] = audition.solving;
        a["offered"] = audition.offered;
        a["has_result"] = audition.result.has_value();
        a["anchor"] = wf::json_t::array();
        a["anchor"].append(audition.anchor.x); a["anchor"].append(audition.anchor.y);
        a["reserved"] = wf::json_t::array();
        for (double v : {audition.reserved.x0, audition.reserved.y0, audition.reserved.x1, audition.reserved.y1})
            a["reserved"].append(v);
        a["progress"] = audition.progress;
        a["offers"] = (int64_t)audition.offers;
        a["denials"] = (int64_t)audition.denials;
        a["accepts"] = (int64_t)audition.accepts;
        a["last_event"] = audition.last_event;
        a["suspended"] = (int64_t)audition.suspended.size();
        wf::json_t actors = wf::json_t::array();
        for (const auto& x : audition.actors)
        {
            wf::json_t item;
            item["id"] = (int64_t)x.id;
            item["from"] = wf::json_t::array(); item["from"].append(x.ox); item["from"].append(x.oy); item["from"].append(x.os);
            item["to"] = wf::json_t::array(); item["to"].append(x.tx); item["to"].append(x.ty); item["to"].append(x.ts);
            actors.append(item);
        }
        a["actors"] = actors;
        return a;
    }

    wf::ipc::method_callback spread_state = [=] (wf::json_t data) -> wf::json_t
    {
        SCOTTLAND_LOOP_SCOPE(spread_state);
        if (getenv("SCOTTLAND_TEST_MODEL") && data.has_member("slow") && data["slow"].is_bool())
            spread_test_slow = data["slow"].as_bool();
        wf::json_t reply = wf::ipc::json_ok();
        reply["last"] = spread_last;
        reply["running"] = spread_run.has_value();
        reply["running_slices"] = spread_run ? (int64_t)spread_run->job->slices : (int64_t)0;
        reply["solves"] = (int64_t)spread_solves;
        reply["cancel_ms"] = spread_cancel_ms;
        reply["audition"] = audition_state();
        return reply;
    };

    void init_spread()
    {
        ipc_repo->register_method("scottland/spread-state", spread_state);
    }

    void fini_spread()
    {
        ipc_repo->unregister_method("scottland/spread-state");
        audition_end("unloading");  // refused: only the user's drop accepts (P5)
        audition_timer.disconnect();
        audition_tick.disconnect();
        audition_watch.disconnect();
        for (const auto& a : audition.actors) set_drag_layout_offset(wf::toplevel_cast(view_by_id(a.id)), 0, 0, 1);
        audition.actors.clear();
        spread_cancel();  // joins nothing: the job runs only inside step(); this unwinds it
    }
