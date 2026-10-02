#include "goo.hpp"
#include "frame.hpp"
#include "goo-runtime.hpp"
#include <chrono>
#include <optional>
#include <cmath>
#include <wayfire/config/option-wrapper.hpp>
#include <wayfire/output-layout.hpp>
#include <wayfire/plugins/common/shared-core-data.hpp>
#include <wayfire/plugins/ipc/ipc-method-repository.hpp>
#include <wayfire/render-manager.hpp>
#include <wayfire/scene-operations.hpp>

namespace scottland
{
namespace
{
double now()
{
    return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
}
bool same(const std::vector<goo::source_t> &a, const std::vector<goo::source_t> &b)
{
    if (a.size() != b.size())
        return false;
    for (size_t i = 0; i < a.size(); i++)
        if (a[i].id != b[i].id || glm::length(a[i].rect - b[i].rect) > .03f ||
            glm::length(a[i].dye - b[i].dye) > .001f || glm::length(a[i].corners - b[i].corners) > .001f ||
            glm::length(a[i].sides - b[i].sides) > .001f ||
            std::abs(a[i].control_extent - b[i].control_extent) > .03f ||
            glm::length(a[i].dot - b[i].dot) > .001f || std::abs(a[i].swell - b[i].swell) > .001f ||
            a[i].hinted != b[i].hinted || a[i].grabbed != b[i].grabbed || std::abs(a[i].scale - b[i].scale) > .001f ||
            a[i].attention != b[i].attention || a[i].emitter != b[i].emitter || a[i].light != b[i].light)
            return false;
    return true;
}
class goo_node_t;
class goo_instance_t : public wf::scene::simple_render_instance_t<goo_node_t>
{
  public:
    goo_instance_t(goo_node_t *, wf::scene::damage_callback, wf::output_t *);
    void render(const wf::scene::render_instruction_t &data) override;
};
class goo_node_t : public wf::scene::node_t
{
  public:
    goo::screen_t state;
    wf::wl_timer<true> tick;
    wf::effect_hook_t pre;
    double last_change = now(), last_step = 0, last_pulse = 0;
    std::map<uint64_t, double> motion_pulse;
    bool attached = true, above_windows = false;
    std::function<void()> failed;
    goo_t::source_provider_t snapshot;
    goo_node_t(wf::output_t *o, goo_t::source_provider_t provider) : node_t(false), snapshot(std::move(provider))
    {
        state.output = o;
        state.settings = goo::current_settings;
        state.wake = [this] { wake(); };
        pre = [this] { prepare(); };
        o->render->add_effect(&pre, wf::OUTPUT_EFFECT_PRE);
    }
    ~goo_node_t() { detach(); }
    void detach()
    {
        if (attached)
            state.output->render->rem_effect(&pre);
        attached = false;
        tick.disconnect();
        auto it = goo::screens.find(state.output);
        if (it != goo::screens.end() && it->second == &state)
            goo::screens.erase(it);
    }
    wf::geometry_t get_bounding_box() override { return state.output->get_relative_geometry(); }
    std::optional<wf::scene::input_node_t> find_node_at(const wf::pointf_t &) override
    {
        return {};
    } // frame owns input
    std::string stringify() const override { return "scottland-goo"; }
    void gen_render_instances(std::vector<wf::scene::render_instance_uptr> &out,
                              wf::scene::damage_callback damage, wf::output_t *o) override
    {
        out.push_back(std::make_unique<goo_instance_t>(this, damage, o));
    }
    // Goo exists only in a band around each window (and in the gaps it bridges),
    // including film over windows behind. Damage, field evaluation and drawing use these bands;
    // wave history and dry dye retain their existing evolution (GO10).
    std::vector<wf::geometry_t> last_bands;
    bool whole = true;
    // Recomputed only when the sources or settings change (every caller in a frame shares it).
    std::optional<std::vector<wf::geometry_t>> band_cache;
    const std::vector<wf::geometry_t> &bands()
    {
        if (!band_cache)
            band_cache = compute_bands();
        return *band_cache;
    }
    std::vector<wf::geometry_t> compute_bands() const
    {
        auto reach = goo::support_radii(state.sources, state.settings);
        std::vector<float> film_reach;
        if (goo::overlaps(state.sources) && state.settings.overlap_film > 0)
            film_reach = goo::support_radii(state.sources, state.settings, true);
        std::vector<wf::geometry_t> list;
        for (size_t i = 0; i < state.sources.size(); i++)
        {
            auto &s = state.sources[i];
            // Four logical pixels cover cubic half-resolution reconstruction;
            // one covers the normal's forward difference, and a device pixel
            // covers the derivative AA quad beyond the threshold contour.
            double padding = 5 + 1. / state.output->handle->scale;
            double out = reach[i] + padding, in = s.liquid.y + padding;
            if (!film_reach.empty())
                out = std::max(out, film_reach[i] * std::max(1.f,
                    goo::overlap_film_width(s, state.settings) / state.settings.thickness) + padding);
            double x1 = s.rect.x - s.rect.z, x2 = s.rect.x + s.rect.z;
            double y1 = s.rect.y - s.rect.w, y2 = s.rect.y + s.rect.w;
            auto box = [&](double a, double b, double c, double d)
            {
                if (c > a && d > b)
                    list.push_back(wf::geometry_t{std::floor(a), std::floor(b),
                                                  std::ceil(c - std::floor(a)), std::ceil(d - std::floor(b))});
            };
            if (x2 - x1 <= 2 * in || y2 - y1 <= 2 * in)
            {
                box(x1 - out, y1 - out, x2 + out, y2 + out);
                continue;
            }
            box(x1 - out, y1 - out, x2 + out, y1 + in);    // top
            box(x1 - out, y2 - in, x2 + out, y2 + out);    // bottom
            box(x1 - out, y1 + in, x1 + in, y2 - in);      // left
            box(x2 - in, y1 + in, x2 + out, y2 - in);      // right
        }
        return list;
    }
    // Field tiles cover current geometry with one 32px tile of sampling halo.
    // The renderer clears the field first, so old tiles cannot leave stale density.
    std::vector<wf::geometry_t> sim_tiles(const std::vector<wf::geometry_t> &bands)
    {
        constexpr double tile = 32;
        wf::region_t current;
        for (auto &b : bands)
        {
            double x1 = (std::floor(b.x / tile) - 1) * tile;
            double y1 = (std::floor(b.y / tile) - 1) * tile;
            double x2 = (std::ceil((b.x + b.width) / tile) + 1) * tile;
            double y2 = (std::ceil((b.y + b.height) / tile) + 1) * tile;
            current |= wf::geometry_t{x1, y1, x2 - x1, y2 - y1};
        }

        std::vector<wf::geometry_t> rects;
        for (auto &b : current)
            rects.push_back(wf::geometry_t{double(b.x1), double(b.y1), double(b.x2 - b.x1), double(b.y2 - b.y1)});
        return rects;
    }
    void damage()
    {
        auto node = shared_from_this();
        if (whole)
        {
            whole = false;
            wf::scene::damage_node(node, get_bounding_box());
            last_bands = bands();
            return;
        }
        auto &next = bands();
        for (auto &b : last_bands)
            wf::scene::damage_node(node, b);
        for (auto &b : next)
            wf::scene::damage_node(node, b);
        last_bands = next;
    }
    void wake()
    {
        // A hidden window's attention timer can wake us after prepare() suspended
        // fullscreen goo. No repaint may follow that occluded damage, so preserve
        // the suspension here too. Leaving fullscreen replaces sources in prepare.
        if (state.sources.size() == 1 && !state.sources[0].emitter)
            return;
        state.sleeping = false;
        if (!attached)
            return;
        damage();
        if (!tick.is_connected())
            tick.set_timeout(16,
                             [this]
                             {
                                 if (state.sleeping)
                                     return false;
                                 damage();
                                 return true;
                             });
    }
    void prepare()
    {
        auto next = snapshot(state.output);
        bool overlap = goo::overlaps(next);
        if (overlap != above_windows)
        {
            // With no overlap keep GO10's original placement: goo-only damage
            // need not repaint window contents. Move the one shared surface above
            // the windows only while it has film to composite there.
            above_windows = overlap;
            auto node = shared_from_this();
            wf::scene::remove_child(node);
            if (overlap)
                wf::scene::add_back(state.output->node_for_layer(wf::scene::layer::OVERLAY), node);
            else
                wf::scene::add_front(state.output->node_for_layer(wf::scene::layer::BACKGROUND), node);
            whole = true; // refresh the backdrop cache after changing its scene position
        }
        if (!same(next, state.sources))
        {
            // Movement, state blooms, swells and drops excite the shared surface.
            for (auto &s : next)
            {
                auto old = std::find_if(state.sources.begin(), state.sources.end(),
                                        [&](auto &w) { return w.id == s.id; });
                bool transition = old == state.sources.end() || s.grabbed != old->grabbed ||
                                  std::abs(s.swell - old->swell) > .05;
                bool moving = old != state.sources.end() && glm::length(s.rect - old->rect) > 1;
                if (s.emitter && (transition || (moving && now() - motion_pulse[s.id] > .14)) &&
                    state.impulses.size() < 8)
                {
                    state.impulses.push_back({s.rect.x, s.rect.y + s.rect.w + 6, transition ? .8f : .5f, 24});
                    motion_pulse[s.id] = now();
                }
            }
            state.sources = std::move(next);
            band_cache.reset();
            last_change = now();
            wake();
        }
        if (state.sources.empty() || (state.sources.size() == 1 && !state.sources[0].emitter))
        {
            state.sleeping = true;
            state.impulses.clear();
            tick.disconnect();
            return;
        }
        double t = now();
        bool attention = false;
        for (auto &s : state.sources)
            if (s.attention)
            {
                attention = true;
                if (t - last_pulse > 1.5 && state.impulses.size() < 8)
                    state.impulses.push_back({s.rect.x, s.rect.y - s.rect.w - 6, .9, 26});
            }
        if (attention && t - last_pulse > 1.5)
        {
            last_pulse = t;
            wake();
        }
        // Drift and curl freeze after the response, then actual GPU energy decides sleep.
        if (t - last_change < 2 || attention)
            state.time += std::min(.05, t - last_step);
        last_step = t;
    }
    void render(const wf::scene::render_instruction_t &data)
    {
        if (!attached || !goo_enabled() || !wf::get_core().is_gles2() ||
            (state.sources.size() == 1 && !state.sources[0].emitter))
            return;
        data.pass->custom_gles_subpass(
            [&]
            {
                auto g = get_bounding_box();
                auto &band = bands();
                if (!state.sleeping)
                {
                    goo::amounts(state.sources, state.settings);
                    bool ok = state.renderer.update(state.sources, state.settings, g.width, g.height,
                                                    state.time, state.impulses, sim_tiles(band));
                    state.impulses.clear();

                    if (!ok)
                    {
                        state.sleeping = true;
                        LOGE("scottland goo: simulation unavailable; retaining halo");
                        if (failed)
                            failed();
                    }
                    else if (now() - last_change > 3 && state.renderer.energy < .012f)
                    {
                        state.sleeping = true;
                        tick.disconnect();
                    }
                }
                wf::regionf_t area;
                for (auto &b : band)
                    area |= b;
                state.renderer.draw(data, area);
            });
    }
};
goo_instance_t::goo_instance_t(goo_node_t *s, wf::scene::damage_callback d, wf::output_t *o)
    : simple_render_instance_t(s, d, o)
{
}
void goo_instance_t::render(const wf::scene::render_instruction_t &data) { self->render(data); }
} // namespace
struct goo_t::impl
{
    wf::wl_idle_call fallback;
    goo_t::source_provider_t snapshot;
    std::function<void(wf::output_t *, bool)> screen_changed;
    wf::option_wrapper_t<bool> enabled{"scottland/goo"};
    wf::option_wrapper_t<std::string> curve{"scottland/goo_falloff"};
    std::vector<std::unique_ptr<wf::option_wrapper_t<double>>> options;
    std::map<wf::output_t *, std::shared_ptr<goo_node_t>> nodes;
    wf::shared_data::ref_ptr_t<wf::ipc::method_repository_t> ipc;
    wf::signal::connection_t<wf::output_added_signal> added = [this](wf::output_added_signal *e)
    { add(e->output); };
    wf::signal::connection_t<wf::output_removed_signal> removed = [this](wf::output_removed_signal *e)
    { remove(e->output); };
    struct option_t
    {
        const char *name;
        float goo::settings_t::*field;
    };
    const std::vector<option_t> fields = {
        {"thickness", &goo::settings_t::thickness}, {"reach", &goo::settings_t::reach},
        {"thinning", &goo::settings_t::thinning},   {"swell", &goo::settings_t::swell},
        {"noise", &goo::settings_t::noise},         {"lump", &goo::settings_t::lump},
        {"drift", &goo::settings_t::drift},         {"wave_speed", &goo::settings_t::wave_speed},
        {"wave_damp", &goo::settings_t::wave_damp}, {"wave_height", &goo::settings_t::wave_height},
        {"spread", &goo::settings_t::spread},       {"swirl", &goo::settings_t::swirl},
        {"release", &goo::settings_t::release},     {"shine", &goo::settings_t::shine},
        {"relief", &goo::settings_t::relief},
        {"overlap_film", &goo::settings_t::overlap_film},
        {"hover_cloudiness", &goo::settings_t::hover_cloudiness},
        {"hover_emissivity", &goo::settings_t::hover_emissivity},
        {"hover_distance", &goo::settings_t::hover_distance}};
    void config()
    {
        for (size_t i = 0; i < fields.size(); i++)
            goo::current_settings.*fields[i].field = options[i]->value();
        if (!goo::current_settings.curve(curve.value()))
            LOGE("scottland goo: invalid falloff, keeping last valid curve");
        bool on = enabled;
        if (on && !goo::enabled)
        {
            goo::renderer_t probe;
            on = probe.supported();
        }
        goo::enabled = on;
        for (auto o : wf::get_core().output_layout->get_outputs())
            if (on)
                add(o);
            else
                remove(o);
        for (auto &[o, n] : nodes)
        {
            n->state.settings = goo::current_settings;
            n->band_cache.reset();
            n->whole = true;
            n->last_change = now();
            n->wake();
        }
        for (auto &v : wf::get_core().get_all_views())
            v->damage();
    }
    void add(wf::output_t *o)
    {
        if (!goo::enabled || nodes.count(o))
            return;
        auto n = std::make_shared<goo_node_t>(o, snapshot);
        n->failed = [this]
        {
            fallback.run_once(
                [this]
                {
                    goo::enabled = false;
                    while (!nodes.empty())
                        remove(nodes.begin()->first);
                    for (auto &v : wf::get_core().get_all_views())
                        v->damage();
                });
        };
        nodes[o] = n;
        goo::screens[o] = &n->state;
        // prepare() moves this same surface above windows when overlap requires it.
        wf::scene::add_front(o->node_for_layer(wf::scene::layer::BACKGROUND), n);
        n->wake();
        screen_changed(o, true);
    }
    void remove(wf::output_t *o)
    {
        auto it = nodes.find(o);
        if (it == nodes.end())
            return;
        it->second->whole = true;
        it->second->damage();
        it->second->detach();
        wf::scene::remove_child(it->second);
        nodes.erase(it);
        screen_changed(o, false);
        o->render->damage_whole_idle();
    }
    wf::ipc::method_callback state = [this](const wf::json_t &data)
    {
        wf::json_t out;
        out["enabled"] = goo::enabled;
        auto list = wf::json_t::array();
        for (auto &[o, n] : nodes)
        {
            wf::json_t s;
            s["output"] = o->handle->name;
            s["sleeping"] = n->state.sleeping;
            s["steps"] = (int64_t)n->state.renderer.steps;
            s["step_ms"] = n->state.renderer.last_step_ms;
            s["gpu_ms"] = n->state.renderer.last_gpu_ms;
            s["energy"] = n->state.renderer.energy;
            s["packed"] = n->state.renderer.packed;
            s["overlapping"] = n->state.renderer.overlapping();
            s["highlighting"] = n->state.renderer.highlighting();
            s["sources"] = (int64_t)n->state.sources.size();
            if (data.has_member("x") && data.has_member("y") &&
                (data["x"].is_int() || data["x"].is_double()) &&
                (data["y"].is_int() || data["y"].is_double()))
            {
                glm::vec2 point{data["x"].as_double(), data["y"].as_double()};
                auto color = n->state.renderer.sample_at(point);
                s["density"] = goo::density(point, n->state.sources, n->state.settings, n->state.time);
                s["threshold"] = n->state.settings.threshold();
                s["window_distance"] = goo::union_distance(point, n->state.sources);
                s["red"] = color.r;
                s["green"] = color.g;
                s["blue"] = color.b;
                s["wave"] = color.w;
            }
            list.append(s);
        }
        out["screens"] = list;
        return out;
    };
};
goo_t::goo_t() : p(std::make_unique<impl>()) {}
goo_t::~goo_t() = default;
void goo_t::start(source_provider_t snapshot, std::function<void(wf::output_t *, bool)> screen_changed)
{
    p->snapshot = std::move(snapshot);
    p->screen_changed = std::move(screen_changed);
    for (auto &field : p->fields)
    {
        auto o = std::make_unique<wf::option_wrapper_t<double>>(std::string("scottland/goo_") + field.name);
        o->set_callback([this] { p->config(); });
        p->options.push_back(std::move(o));
    }
    p->enabled.set_callback([this] { p->config(); });
    p->curve.set_callback([this] { p->config(); });
    wf::get_core().output_layout->connect(&p->added);
    wf::get_core().output_layout->connect(&p->removed);
    p->ipc->register_method("scottland/goo-state", p->state);
    p->config();
}
void goo_t::stop()
{
    p->fallback.disconnect();
    p->added.disconnect();
    p->removed.disconnect();
    p->ipc->unregister_method("scottland/goo-state");
    while (!p->nodes.empty())
        p->remove(p->nodes.begin()->first);
    goo::enabled = false;
}
} // namespace scottland
