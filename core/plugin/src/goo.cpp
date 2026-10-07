#include "goo.hpp"
#include "loop.hpp"
#include "goo-gl.hpp"
#include "pure/shrink.hpp"
#include "attention-breath.hpp"
#include "frame.hpp"
#include "goo-runtime.hpp"
#include "goo-pickup-policy.hpp"
#include <chrono>
#include <cstdlib>
#include <cstring>
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
// What changed between two source lists (nullptr: nothing the goo responds to). The name
// is reported as the reason the simulation woke, so the cost of a wake can be traced.
const char *difference(const std::vector<goo::source_t> &a, const std::vector<goo::source_t> &b)
{
    if (a.size() != b.size())
        return "windows";
    // Loud causes first: the last two can be a quiet wake (see prepare()).
    for (size_t i = 0; i < a.size(); i++)
    {
        if (a[i].id != b[i].id) return "stacking";
        if (glm::length(a[i].dye - b[i].dye) > .001f ||
            std::abs(a[i].dye_strength - b[i].dye_strength) > .001f ||
            std::abs(a[i].state_mix - b[i].state_mix) > .001f ||
            std::abs(a[i].neutral_strength - b[i].neutral_strength) > .001f || a[i].light != b[i].light)
            return "color";
        if (glm::length(a[i].corners - b[i].corners) > .001f || glm::length(a[i].sides - b[i].sides) > .001f ||
            std::abs(a[i].dot.z - b[i].dot.z) > .001f || std::abs(a[i].dot.w - b[i].dot.w) > .001f)
            return "hover";
        if (std::abs(a[i].swell - b[i].swell) > .001f || a[i].grabbed != b[i].grabbed) return "swell";
        if (a[i].hinted != b[i].hinted) return "hints";
        if (a[i].attention != b[i].attention || a[i].emitter != b[i].emitter) return "attention";
    }
    for (size_t i = 0; i < a.size(); i++)
    {
        if (a[i].shape != b[i].shape || glm::length(a[i].shape_body - b[i].shape_body) > .03f) return "shape";
        // The close dot's place and the controls' extent follow the outline.
        if (glm::length(a[i].rect - b[i].rect) > .03f || std::abs(a[i].scale - b[i].scale) > .001f ||
            std::abs(a[i].control_extent - b[i].control_extent) > .03f ||
            glm::length(glm::vec2(a[i].dot) - glm::vec2(b[i].dot)) > .001f)
            return "geometry";
    }
    return nullptr;
}
class goo_node_t;
class goo_instance_t : public wf::scene::simple_render_instance_t<goo_node_t>
{
  public:
    goo_instance_t(goo_node_t *, wf::scene::damage_callback, wf::output_t *);
    void render(const wf::scene::render_instruction_t &data) override;
    void schedule_instructions(std::vector<wf::scene::render_instruction_t> &instructions,
                               const wf::render_target_t &target, wf::regionf_t &damage) override;
    wf::regionf_t reuse;
};
class goo_node_t : public wf::scene::node_t
{
  public:
    goo::screen_t state;
    wf::wl_timer<true> tick, breath_tick;
    uint64_t breath_ticks = 0;
    float breath_hold = -1;
    // A breath-only frame: nothing but this node's breathing tick damaged the output since
    // the last frame. The scene under the strips is then exactly the cached backdrop, so
    // the goo repaints the strips itself and the windows and wallpaper beneath are left
    // alone. Any other damage on this output, or damage outside the strips, takes the
    // normal path; one frame a second does too, so a change that arrived without scene
    // damage cannot leave a stale backdrop for longer than that.
    //
    // GO21: other damage is heard through render instances of this output's own layers
    // (damage signals do not travel to the scene root, so listening there heard nothing),
    // and a restructured scene counts as damage. The test is made in device pixels: the
    // output rounds damage out to whole pixels, which at a fractional scale or under
    // rotation reach past the logical strips. The pixels the goo claims are exactly the
    // pixels it restores, so none is repainted beneath and reused as well, and none is
    // left unpainted.
    bool foreign_damage = true, own_damage = false, reuse_enabled = true, dry_enabled = true, frame_foreign = false;
    int reuse_streak = 0;
    // GO24: the dye's own clock and the sleeping watercolor tick.
    double flow_time = 0, last_flow = 0;
    bool water_due = false;
    uint64_t water_ticks = 0;
    wf::wl_timer<true> water_tick;
    bool deaf = false;  // tests only: ignore other damage, to prove the exactness test can fail
    std::string reuse_blocked;
    std::unique_ptr<wf::scene::render_instance_manager_t> scene_observer;
    wf::signal::connection_t<wf::scene::root_node_update_signal> on_scene_update =
        [this] (wf::scene::root_node_update_signal *) { SCOTTLAND_LOOP_SCOPE(goo_scene_update); foreign_damage = true; };
    void observe_scene(bool on)
    {
        if (on == bool(scene_observer))
            return;
        foreign_damage = true;
        if (!on)
        {
            scene_observer.reset();
            on_scene_update.disconnect();
            return;
        }
        std::vector<wf::scene::node_ptr> layers;
        for (auto layer : {wf::scene::layer::BACKGROUND, wf::scene::layer::BOTTOM, wf::scene::layer::WORKSPACE,
                 wf::scene::layer::TOP, wf::scene::layer::UNMANAGED})
            layers.push_back(state.output->node_for_layer(layer));
        scene_observer = std::make_unique<wf::scene::render_instance_manager_t>(layers,
            [this] (const wf::regionf_t &) { if (!own_damage && !deaf) foreign_damage = true; }, state.output);
        wf::get_core().scene()->connect(&on_scene_update);
    }
    // The region (logical, covering whole device pixels) this frame restores from the
    // cached backdrop; empty for a normal frame.
    wf::regionf_t breath_only_frame(const wf::render_target_t &target, const wf::regionf_t &damage)
    {
        bool foreign = std::exchange(foreign_damage, false);
        frame_foreign = foreign;
        wf::regionf_t reuse;
        // Why the last frame took the normal path (goo-state, for tests and live reading).
        reuse_blocked = foreign ? "other damage" : !scene_observer || !reuse_enabled ? "off" :
            !state.sleeping || whole || own_area().empty() ? "not a sleeping breath" :
            reuse_streak >= 25 ? "periodic refresh" :
            !state.renderer.backdrop_ready(target) ? "backdrop not ready for this target" : "";
        if (reuse_blocked.empty() && attached && goo_enabled() && wf::get_core().is_gles2() &&
            !(state.sources.size() == 1 && !state.sources[0].emitter))
        {
            auto pixels = target.framebuffer_region_from_geometry_region(damage);
            auto strips = target.framebuffer_region_from_geometry_region(own_area());
            if ((pixels ^ strips).empty())
            {
                // GO27: merged strips can cover dry window content and places outside the
                // goo's own area, where no backdrop is kept. The goo restores only the
                // pixels it keeps current: the rest stay in the frame's damage and the
                // scene beneath paints them.
                reuse = target.geometry_region_from_framebuffer_region(
                    (pixels & target.framebuffer_region_from_geometry_region(drawn_area())) ^
                    target.framebuffer_region_from_geometry_region(dry));
                if (reuse.empty())
                    reuse_blocked = "strips outside the goo only";
            } else
                reuse_blocked = "damage outside the strips";
        }
        reuse_streak = reuse.empty() ? 0 : reuse_streak + 1;
        return reuse;
    }
    bool breath_tight = true, breath_loose = false, breath_keys = true;
    wf::regionf_t breath_area;
    wf::effect_hook_t pre;
    double last_change = now(), last_step = 0;
    std::map<uint64_t, double> motion_pulse;
    bool attached = true, above_windows = false;
    std::function<void()> failed;
    std::function<void()> readback_issued;  // arms the shared collection timer
    goo_t::source_provider_t snapshot;
    goo_node_t(wf::output_t *o, goo_t::source_provider_t provider) : node_t(false), snapshot(std::move(provider))
    {
        state.output = o;
        state.settings = goo::current_settings;
        state.revision++;
        state.wake = [this] { wake("frame"); };
        pre = [this] { SCOTTLAND_LOOP_SCOPE(goo_prepare); prepare(); };
        o->render->add_effect(&pre, wf::OUTPUT_EFFECT_PRE);
    }
    ~goo_node_t() { detach(); }
    void detach()
    {
        wallpaper_nodes.clear();
        pickup_timer.disconnect();
        check_timer.disconnect();
        idle_check.disconnect();
        if (attached)
            state.output->render->rem_effect(&pre);
        attached = false;
        tick.disconnect();
        breath_tick.disconnect();
        shrink_lane.reset();  // closed: nothing of it is delivered to a detached node
        shrink_pending = false;
        water_tick.disconnect();
        observe_scene(false);
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
        SCOTTLAND_LOOP_SCOPE(goo_gen_render_instances);
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
        {
            band_cache = compute_bands();
            compute_dry();
        }
        return *band_cache;
    }
    // GO20: window content no goo can lie on. A window's interior (inside its rounded
    // corners) is dry unless a source in front of it can lay film there, which is bounded
    // by that source's outer band. Damage that stays in dry content (a front terminal
    // redrawing itself) then costs the goo nothing: no backdrop copy, no composite.
    std::vector<double> band_outs;
    wf::regionf_t dry;
    void compute_dry()
    {
        dry.clear();
        for (size_t i = 0; i < state.sources.size() && i < band_outs.size(); i++)
        {
            auto &s = state.sources[i];
            if (s.shape || s.hint_circle)
                continue;  // content follows an alpha contour, or there is none
            double inset = s.liquid.y + 2;
            double x1 = std::ceil(s.rect.x - s.rect.z + inset), x2 = std::floor(s.rect.x + s.rect.z - inset);
            double y1 = std::ceil(s.rect.y - s.rect.w + inset), y2 = std::floor(s.rect.y + s.rect.w - inset);
            if (x2 <= x1 || y2 <= y1)
                continue;
            wf::regionf_t core;
            core |= wf::geometry_t{x1, y1, x2 - x1, y2 - y1};
            for (size_t j = 0; j < i; j++)  // sources are front to back
            {
                auto &f = state.sources[j];
                double out = band_outs[j];
                core ^= wf::geometry_t{std::floor(f.rect.x - f.rect.z - out), std::floor(f.rect.y - f.rect.w - out),
                    std::ceil(2 * f.rect.z + 2 * out) + 1, std::ceil(2 * f.rect.w + 2 * out) + 1};
            }
            dry |= core;
        }
    }
    std::vector<wf::geometry_t> compute_bands()
    {
        band_outs.clear();
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
            band_outs.push_back(out);
            double x1 = s.rect.x - s.rect.z, x2 = s.rect.x + s.rect.z;
            double y1 = s.rect.y - s.rect.w, y2 = s.rect.y + s.rect.w;
            auto box = [&](double a, double b, double c, double d)
            {
                if (c > a && d > b)
                    list.push_back(wf::geometry_t{std::floor(a), std::floor(b),
                                                  std::ceil(c - std::floor(a)), std::ceil(d - std::floor(b))});
            };
            if (s.shape || x2 - x1 <= 2 * in || y2 - y1 <= 2 * in)
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
    // Whole logical pixels, so a frame's damage (rounded out by the output) stays inside
    // the guard below at integer scales.
    static wf::regionf_t whole_pixels(const wf::regionf_t &region)
    {
        wf::regionf_t out;
        for (auto &b : region)
            out |= wf::geometry_t{std::floor(b.x1), std::floor(b.y1),
                std::ceil(b.x2) - std::floor(b.x1), std::ceil(b.y2) - std::floor(b.y1)};
        return out;
    }
    // GO21: the output's damage ring collapses more than twenty rectangles into their
    // bounding box, which would repaint whole windows on every breath and rule out
    // backdrop reuse. Keep the strips to a dozen rectangles: merge the pair whose joint box
    // adds the least area until they fit. The strips may then cover dry content; a reused
    // frame never restores there (GO27), so the backdrop is never copied there either.
    size_t max_breath_rects = 16;  // tests lower it to force merging
    wf::regionf_t few_rects(const wf::regionf_t &exact) const
    {
        std::vector<wf::geometry_t> rects;
        for (auto &b : exact)
            rects.push_back(wf::geometry_t{double(b.x1), double(b.y1), double(b.x2 - b.x1), double(b.y2 - b.y1)});
        auto joined = [] (const wf::geometry_t &a, const wf::geometry_t &b)
        {
            double x1 = std::min<double>(a.x, b.x), y1 = std::min<double>(a.y, b.y);
            double x2 = std::max<double>(a.x + a.width, b.x + b.width);
            double y2 = std::max<double>(a.y + a.height, b.y + b.height);
            return wf::geometry_t{x1, y1, x2 - x1, y2 - y1};
        };
        auto count = [] (const wf::regionf_t &region) { size_t n = 0; for (auto &b : region) { (void)b; n++; } return n; };
        auto result = exact;
        // Joining boxes can make the union band into more rectangles than were joined,
        // so aim lower until the banded result fits. One box always does.
        for (size_t target = max_breath_rects; count(result) > max_breath_rects && target >= 1; target--)
        {
            while (rects.size() > target)
            {
                size_t first = 0, second = 1;
                double least = 1e30;
                for (size_t i = 0; i < rects.size(); i++)
                    for (size_t j = i + 1; j < rects.size(); j++)
                    {
                        auto box = joined(rects[i], rects[j]);
                        double added = double(box.width) * box.height - double(rects[i].width) * rects[i].height -
                            double(rects[j].width) * rects[j].height;
                        if (added < least) { least = added; first = i; second = j; }
                    }
                rects[first] = joined(rects[first], rects[second]);
                rects.erase(rects.begin() + second);  // one fewer each round: always ends
            }
            result.clear();
            for (auto &r : rects) result |= r;
        }
        return result;
    }
    // GO24: where the sleeping goo's watercolor moves: the settled liquid, as few
    // rectangles (the breathing strips lie inside it, so the two kinds of tick add up to
    // the same damage shape). Empty until the settled region is known, or with no soak.
    wf::regionf_t motion_area;
    bool water_enabled = true;
    bool water_frozen = false;  // tests: tick and repaint, but leave the dye as it is
    // Pickup is on and there is something to pick up: a background-layer client, or windows
    // under overlap film (GO28). Without either, nothing changes from GO21 (GO24).
    bool watercolor()
    {
        return water_enabled && state.settings.soak > 0 && (!wallpaper_nodes.empty() || state.renderer.overlapping());
    }
    const wf::regionf_t &own_area() const { return motion_area.empty() ? breath_area : motion_area; }
    void set_breath_area(const wf::regionf_t &exact)
    {
        breath_area = few_rects(exact) & get_bounding_box();
        motion_area.clear();
        if (settled_ready && state.sleeping && water_running && watercolor())
            motion_area = few_rects((settled_area ^ dry) | breath_area) & get_bounding_box();
        // Content newly inside the region has no backdrop kept yet: one ordinary repaint
        // there (this damage is not the goo's own tick) copies it before any reuse.
        if (attached)
            for (auto &b : own_area())
                wf::scene::damage_node(shared_from_this(), wf::geometry_t{
                    double(b.x1), double(b.y1), double(b.x2-b.x1), double(b.y2-b.y1)});
        foreign_damage = true;
    }
    void update_breathing()
    {
        if (shrink_lane) shrink_lane->bump_epoch();  // sources, shapes, settings or strips changed
        breath_area.clear();
        breath_loose = !settled_ready;
        // The cached influence is exactly zero past four reaches. Include cubic
        // reconstruction/AA padding and intersect the actual drawable goo bands:
        // joined goo inside this support breathes too; distant strips never do.
        wf::regionf_t support, visible;
        double padding = 5 + 1. / state.output->handle->scale;
        for (auto &s : state.sources)
        {
            if (!s.attention || !s.emitter) continue;
            double out = 4 * state.settings.reach + padding;
            // A mask may have a hollow or deeply inset contour. Every part of
            // that body can breathe; rectangle edge strips would omit it.
            if (s.shape)
            {
                // body bounds already contain badge overhang; the full box also
                // covers internal holes whose shores are away from its edges.
                auto body = s.shape_body.z > 0 && s.shape_body.w > 0 ? s.shape_body : s.rect;
                double x = body.x-body.z, y = body.y-body.w;
                support |= wf::geometry_t{x-out, y-out, 2*body.z+2*out, 2*body.w+2*out};
                continue;
            }
            double in = s.liquid.y + padding;
            double x = s.rect.x-s.rect.z, y = s.rect.y-s.rect.w;
            double w = 2*s.rect.z, h = 2*s.rect.w;
            support |= wf::geometry_t{x-out, y-out, w+2*out, out+in};
            support |= wf::geometry_t{x-out, y+h-in, w+2*out, out+in};
            if (h > 2*in)
            {
                support |= wf::geometry_t{x-out, y+in, out+in, h-2*in};
                support |= wf::geometry_t{x+w-in, y+in, out+in, h-2*in};
            }
        }
        for (auto &b : bands()) visible |= b;
        // Dry content is neither damaged nor repainted by a breath (its backdrop is not kept).
        breath_support = whole_pixels(support & visible & get_bounding_box()) ^ dry;
        set_breath_area(settled_ready ? whole_pixels(breath_support & settled_area) : breath_support);
        breath_tick.disconnect();
        state.breath = 0;
        if (breath_area.empty()) return;
        state.breath = goo::attention_breath(now());
        // 125 samples per breath. The maximum light step is below 0.8% and the
        // preset's moving contour advances less than 0.09 logical pixels/tick.
        breath_tick.set_timeout(40, [this] {
            SCOTTLAND_LOOP_SCOPE(goo_breath_tick);
            double tick_start = now();
            state.breath = breath_hold >= 0 ? breath_hold : goo::attention_breath(now());
            if (state.sleeping)
            {
                own_damage = true;
                for (auto &b : breath_area)
                    wf::scene::damage_node(shared_from_this(), wf::geometry_t{
                        double(b.x1), double(b.y1), double(b.x2-b.x1), double(b.y2-b.y1)});
                own_damage = false;
            }
            ++breath_ticks;
            tick_ms = std::max(tick_ms * .98, (now() - tick_start) * 1000);
            return true;
        });
    }
    // The conservative GO17 support includes dry liquid reach. Once the field is asleep, each
    // band rectangle shrinks to its widest-breath wet density plus a reconstruction margin
    // (masked alpha contours use a denser lattice so a thin lobe keeps the same margin). The
    // sampling is slow, so it runs as a job on the shrink worker (main-loop Phase 4): the
    // conservative strips stay in use until a result arrives that still describes the current
    // sources, settings and output. A capped job tightens only the rectangles it finished; a
    // rectangle is never partly shrunk.
    //
    // GO20: the same shrinking for every band, not only the breathing strips. While the
    // goo sleeps, drawing, the backdrop copy and the composite use this region, so an app
    // frame that touches the goo works on the liquid, not on the dry reach around it.
    // Any wake returns to the conservative bands (waves can push the shore out).
    wf::regionf_t settled_area, breath_support;
    bool settled_ready = false;
    std::unique_ptr<work::lane_handle_t> shrink_lane;
    bool shrink_pending = false;
    uint64_t readback_incarnation = 0;
    std::shared_ptr<std::atomic<bool>> shrink_hold;  // tests: the goo's one switch, shared by every output
    uint64_t shrink_incarnation() const
    {
        // Mode, scale or transform of the output: a result for another incarnation is dropped.
        auto g = state.output->get_relative_geometry();
        double scale = state.output->handle->scale;
        uint64_t h = uint64_t(g.width) * 1000003ull + uint64_t(g.height);
        h = h * 1000003ull + uint64_t(scale * 1000) + uint64_t(state.output->handle->transform) * 7;
        return h;
    }
    void open_shrink_lane(work::worker_t *worker)
    {
        if (!worker) return;
        shrink_lane = worker->open_lane("shrink:" + state.output->to_string(), work::policy_t::exact);
        shrink_lane->accept = [this] (const work::done_t &done)
        {
            auto result = dynamic_cast<const work::shrink_result_t *>(done.result.get());
            // Exactly the current sources (ticket and epoch), still asleep, same output.
            return result && done.outcome != work::outcome_t::failed && done.ticket == shrink_lane->ticket() &&
                done.epoch == shrink_lane->epoch() && state.sleeping && breath_tight && attached &&
                result->incarnation == shrink_incarnation();
        };
        shrink_lane->deliver = [this] (work::done_t &done)
        {
            SCOTTLAND_LOOP_SCOPE(shrink_install);
            shrink_pending = false;
            auto &result = static_cast<work::shrink_result_t &>(*done.result);
            tighten_ms += double(done.finish_ns - done.start_ns) / 1e6;
            if (!result.refined)
                return;  // capped before any rectangle finished: no change, the loose strips stay
            wf::regionf_t tight;
            for (auto &r : result.rects)
                tight |= wf::geometry_t{r.x, r.y, r.width, r.height};
            breath_loose = false;
            ++breath_tightens;
            settled_area = whole_pixels(tight);
            settled_ready = true;
            set_breath_area(whole_pixels(breath_support & settled_area));
        };
    }
    // GO24: when the goo falls asleep the watercolor coasts to a stop. For a while a
    // dye-only pass runs at a slow tick, drawn by the cheap composite over the settled
    // liquid (no simulation, no surface shading), each pass standing for less and less
    // until it stands for nothing. Then the tick ends and the dye stays exactly as it lies:
    // the picked-up, smeared color is kept, costs nothing, and changes only when the goo
    // is stirred awake again or the wallpaper changes (Mike, 2026-10-03).
    static constexpr double water_coast = 14;
    double water_started = 0, water_length = 0;
    bool water_running = false;
    double water_pace() const
    {
        double left = 1 - (now() - water_started) / std::max(water_length, .001);
        return std::clamp(left, 0., 1.);
    }
    void start_water(double seconds)
    {
        water_tick.disconnect();
        water_started = now();
        water_length = seconds;
        water_running = seconds > 0;
        water_tick.set_timeout(200, [this]
        {
            SCOTTLAND_LOOP_SCOPE(goo_water_tick);
            if (!state.sleeping || !attached)
            {
                water_running = false;
                return false;
            }
            if (water_pace() <= 0)
            {
                // Came to rest: back to the breathing strips alone (or to no damage at all).
                // Compare against the backdrop at coast START. A new final frame may
                // have arrived after useful dye work; never acknowledge it here.
                water_running = false;
                // A synchronous compatibility check may immediately start another coast.
                // Run it after this repeating timer has retired, never from its callback.
                if (!idle_check.is_connected())
                    idle_check.run_once([this] { SCOTTLAND_LOOP_SCOPE(goo_pickup_check); backdrop_copied(std::exchange(idle_foreign, false)); });
                set_breath_area(settled_ready ? whole_pixels(breath_support & settled_area) : breath_support);
                return false;
            }
            if (motion_area.empty())
                return true;
            water_due = true;
            ++water_ticks;
            own_damage = true;
            for (auto &b : motion_area)
                wf::scene::damage_node(shared_from_this(), wf::geometry_t{
                    double(b.x1), double(b.y1), double(b.x2-b.x1), double(b.y2-b.y1)});
            own_damage = false;
            return true;
        });
    }
    void start_settling()
    {
        settled_ready = false;
        shrink_pending = false;
        if (shrink_lane) shrink_lane->bump_epoch();  // whatever runs now is for older sources
        if (!breath_tight || !attached)
            return;
        start_water(water_coast);
        if (!shrink_lane)
            return;  // no worker: the loose strips (correct, more damage per breath)
        SCOTTLAND_LOOP_SCOPE(shrink_snapshot);
        // Past the snapshot, scratch or result limits (design 3.2) the loose strips stay. Checked
        // before copying anything, and again by the lane at submit.
        auto &loose = bands();
        if (!work::shrink_job_t::within_limits(state.sources, loose.size(), loose.size()))
        {
            shrink_rejected++;
            return;
        }
        try
        {
            work::shrink_snapshot_t snapshot;
            snapshot.sources = state.sources;
            snapshot.settings = state.settings;
            snapshot.time = state.time;
            snapshot.rects.reserve(loose.size());
            for (auto &b : loose)
                snapshot.rects.push_back({double(b.x), double(b.y), double(b.width), double(b.height)});
            snapshot.output_scale = state.output->handle->scale;
            snapshot.incarnation = shrink_incarnation();
            auto job = std::make_unique<work::shrink_job_t>(std::move(snapshot));
            job->hold = shrink_hold;  // false unless a test holds jobs
            shrink_pending = shrink_lane->submit(std::move(job), work::now_ns());
        } catch (...)
        {
            shrink_pending = false;  // an allocation failed: the loose strips stay
        }
        if (!shrink_pending)
            loop::note(loop::note_id::worker_unavailable, 0, 3);
    }
    // Wakes of a sleeping simulation by cause, for goo-state: each one runs the full
    // simulation and redraws every band for at least three seconds.
    std::map<std::string, uint64_t> wake_counts;
    std::string last_wake;
    uint64_t wake_step = 0, breath_tightens = 0, shrink_rejected = 0;
    double tick_ms = 0, tighten_ms = 0;
    void wake(const char *reason)
    {
        if (state.sleeping)
        {
            wake_counts[reason]++;
            last_wake = reason;
            wake_step = state.renderer.steps;
            settled_ready = false;
            shrink_pending = false;
            if (shrink_lane) shrink_lane->bump_epoch();
            water_tick.disconnect();
            water_due = false;
            water_running = false;
            // Awake, the dye picks up what lies beneath on every step: nothing waits (GO28).
            pickup.pending = false;
            pickup_timer.disconnect();
            set_breath_area(breath_support);
            breath_loose = true;
            }
        // A hidden window's attention timer can wake us after prepare() suspended
        // fullscreen goo. No repaint may follow that occluded damage, so preserve
        // the suspension here too. Leaving fullscreen replaces sources in prepare.
        if (state.sources.size() == 1 && !state.sources[0].emitter)
            return;
        if (state.sleeping || !state.impulses.empty())
            state.renderer.energy = 1; // an old settled readback cannot swallow a new impulse
        state.renderer.invalidate();   // and no reading issued before this change applies
        state.sleeping = false;
        if (!attached)
            return;
        damage();
        if (!tick.is_connected())
            tick.set_timeout(16,
                             [this]
                             {
                                 SCOTTLAND_LOOP_SCOPE(goo_tick);
                                 if (state.sleeping)
                                     return false;
                                 damage();
                                 return true;
                             });
    }
    // Background-layer clients: the goo stays above them, and with none open desktop has
    // nothing to pick up (GO28). What the goo picks up is the backdrop the renderer copies.
    std::vector<wf::scene::node_ptr> wallpaper_nodes;
    uint64_t wallpaper_node_changes = 0;
    void prepare_wallpaper()
    {
        std::vector<wf::scene::node_ptr> next;
        for (auto &child : state.output->node_for_layer(wf::scene::layer::BACKGROUND)->get_children())
            if (child.get() != this && child->is_enabled()) next.push_back(child);
        if (next != wallpaper_nodes)
        {
            wallpaper_nodes = next;
            ++wallpaper_node_changes;
            state.renderer.open_pickup = !wallpaper_nodes.empty();
            if (!above_windows)
            {
                // A new background-layer surface may have been inserted in front
                // of goo. Keep the shared liquid above every wallpaper client.
                auto node = shared_from_this();
                wf::scene::remove_child(node);
                wf::scene::add_front(state.output->node_for_layer(wf::scene::layer::BACKGROUND), node);
                whole = true;
            }
            last_change = now();
            wake("wallpaper"); // a new client, or the last one gone: its pickup comes or goes
        }
    }
    // GO28: a change beneath sleeping liquid restarts only the dye's coast, never the waves or
    // field, and at most once per cool-down: 20 s, doubling each time a change has waited out a
    // cool-down (to 5 minutes), back to 20 s (and over at once) when nothing else has repainted
    // under the liquid for 20 s. A change inside a cool-down waits for its end. Checks run at
    // most twice a second, after frames that copied backdrop under the liquid, and not while
    // one is already waiting.
    static constexpr double pickup_coast = 6;
    static constexpr int pickup_tolerated = 16;
    goo::pickup_policy_t pickup;
    bool seen_due = false;
    uint64_t pickup_coasts = 0, pickup_deferred = 0, backdrop_changes = 0;
    wf::wl_timer<false> pickup_timer, check_timer;
    wf::wl_idle_call idle_check;
    bool idle_foreign = false;
    // `repainted`: something other than the goo repainted under the liquid this frame.
    double pickup_callback_ms = 0, pickup_callback_max_ms = 0;
    uint64_t pickup_callbacks = 0;
    void record_pickup_callback(double started)
    {
        double elapsed = (now() - started) * 1000;
        pickup_callback_ms += elapsed;
        pickup_callback_max_ms = std::max(pickup_callback_max_ms, elapsed);
        ++pickup_callbacks;
    }
    void backdrop_copied(bool repainted = false)
    {
        double started = now();
        handle_backdrop_copied(repainted);
        record_pickup_callback(started);
    }
    void handle_backdrop_copied(bool repainted)
    {
        if (repainted)
        {
            double t = now();
            if (pickup.activity(t))
            {
                pickup_timer.disconnect();
                if (state.sleeping && watercolor() && !water_running)
                    start_pickup();
            }
        }
        if (!state.sleeping || !watercolor() || water_running || pickup.pending)
            return;
        if (pickup.check_wait(now()) > 0)
        {
            if (!check_timer.is_connected())
                check_timer.set_timeout(std::max(1, int(pickup.check_wait(now()) * 1000) + 1),
                    [this] { SCOTTLAND_LOOP_SCOPE(goo_pickup_check); backdrop_copied(); });
            return;
        }
        pickup.last_check = now();
        collect_backdrop_result();
    }
    void collect_backdrop_check()
    {
        double started = now();
        collect_backdrop_result();
        record_pickup_callback(started);
    }
    void collect_backdrop_result()
    {
        int changed = state.renderer.backdrop_changes();
        if (changed < 0)
        {
            check_timer.set_timeout(10, [this] {
                SCOTTLAND_LOOP_SCOPE(goo_pickup_check);
                if (state.sleeping && watercolor() && !water_running && !pickup.pending)
                    collect_backdrop_check();
            });
        }
        else if (changed > pickup_tolerated && state.sleeping && watercolor() && !water_running && !pickup.pending)
            backdrop_changed();
    }
    void backdrop_changed()
    {
        ++backdrop_changes;
        double t = now();
        if (!pickup.change(t))
        {
            pickup.pending = true;
            ++pickup_deferred;
            if (!pickup_timer.is_connected())
                arm_pickup_wait();
            return;
        }
        start_pickup();
    }
    void arm_pickup_wait()
    {
        pickup_timer.set_timeout(std::max(1, int((pickup.next - now()) * 1000) + 1), [this]
        {
            SCOTTLAND_LOOP_SCOPE(goo_pickup_wait);
            double started = now();
            // Timers may fire early: preserve the deadline and rearm, not the action.
            if (!pickup.expire(now())) arm_pickup_wait();
            else if (state.sleeping && watercolor() && attached) start_pickup();
            record_pickup_callback(started);
        });
    }
    void start_pickup()
    {
        pickup.start(now());
        ++pickup_coasts;
        state.renderer.backdrop_seen();
        start_water(pickup_coast);
        set_breath_area(settled_ready ? whole_pixels(breath_support & settled_area) : breath_support);
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
        if (auto changed = difference(next, state.sources))
        {
            size_t impulses = state.impulses.size();
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
            state.revision++;
            band_cache.reset();
            update_breathing();
            // A quiet change: an outline that shifted without anything moving enough to
            // raise a wave (a widget card re-fitting its text by a pixel). The field and dye
            // take it up, but it does not restart the drift or the three-second response
            // window, so the simulation sleeps again as soon as its energy reads settled.
            bool quiet = state.impulses.size() == impulses &&
                (!strcmp(changed, "shape") || !strcmp(changed, "geometry"));
            if (!quiet)
                last_change = now();
            wake(changed);
        }
        observe_scene(reuse_enabled && (!breath_area.empty() || watercolor()));
        if (state.sources.empty() || (state.sources.size() == 1 && !state.sources[0].emitter))
        {
            state.sleeping = true;
            state.impulses.clear();
            tick.disconnect();
            breath_tick.disconnect();
            state.breath = 0;
            return;
        }
        double t = now();
        prepare_wallpaper();
        state.renderer.open_pickup = !wallpaper_nodes.empty();
        // Drift and curl freeze after the response, then actual GPU energy decides sleep.
        if (t - last_change < 2)
            state.time += std::min(.05, t - last_step);
        last_step = t;
    }
    // Where the goo draws and keeps its backdrop: the settled liquid when asleep, the
    // bands otherwise, without dry window content.
    wf::regionf_t drawn_area()
    {
        wf::regionf_t area;
        if (state.sleeping && settled_ready)
            area = settled_area;
        else
            for (auto &b : bands())
                area |= b;
        if (dry_enabled)
            area ^= dry;
        return area;
    }
    void render(const wf::scene::render_instruction_t &data, bool reuse_backdrop)
    {
        if (!attached || !goo_enabled() || !wf::get_core().is_gles2() ||
            (state.sources.size() == 1 && !state.sources[0].emitter))
            return;
        data.pass->custom_gles_subpass(
            [&]
            {
                auto g = get_bounding_box();
                auto &band = bands();
                // A new output incarnation (mode, scale, transform): no reading in flight applies.
                if (auto incarnation = shrink_incarnation(); incarnation != readback_incarnation)
                {
                    if (readback_incarnation) state.renderer.new_generation();
                    readback_incarnation = incarnation;
                }
                if (!state.sleeping)
                {
                    goo::amounts(state.sources, state.settings);
                    double flow_now = now();
                    flow_time += std::clamp(flow_now - last_flow, 0., .05);
                    last_flow = flow_now;
                    bool ok = state.renderer.update(state.sources, state.settings, g.width, g.height,
                                                    state.time, state.impulses, sim_tiles(band),
                                                    // Without watercolor the swirl stops with the drift, as
                                                    // it always has, so the dye can come to rest.
                                                    watercolor() ? flow_time : state.time);
                    state.impulses.clear();

                    if (!ok)
                    {
                        state.sleeping = true;
                        loop::note(loop::note_id::goo_simulation_unavailable);
                        if (failed)
                            failed();
                    }
                    else
                    {
                        // The packed RGBA8 energy readback scales dye deltas by
                        // sixteen before reducing them into an 8-bit pixel. One
                        // source-level color step is therefore 16/255 even when
                        // every remaining change is only one RGBA8 LSB. Treat
                        // that quantization floor as settled; larger wave/dye
                        // deltas still exceed this bound and keep simulating.
                        const float sleep_energy = state.renderer.packed ? 16.f / 255.f + .0001f : .012f;
                        // The energy is read every 30 steps; one full interval after a wake
                        // makes the reading describe the response to it.
                        // Where the settle check can't be read without waiting (GLES 2, a readback
                        // failure, the test switch), sleep on time alone: 6 s after the last change
                        // (Mike, 2026-10-03; GO10).
                        // GO24: watercolor dye never comes to rest, and need not: it goes on
                        // moving in the sleeping goo. Then only the waves decide sleep.
                        float energy = watercolor() ? state.renderer.wave_energy : state.renderer.energy;
                        bool settled_now = state.renderer.timed_sleep() ? now() - last_change > 6 :
                            now() - last_change > 3 && energy <= sleep_energy &&
                            state.renderer.steps - wake_step >= 30;
                        if (state.renderer.readback_pending() && readback_issued)
                            readback_issued();
                        if (settled_now)
                        {
                            state.sleeping = true;
                            tick.disconnect();
                            start_settling();
                            seen_due = true;
                        }
                    }
                }
                if (state.sleeping && water_due)
                {
                    // About nine ordinary steps a second at first (a slow drift, well under
                    // the awake liquid's pace), easing to none. Flow, pickup and release
                    // all scale with the step, so the smear is kept as it slows, not
                    // pulled back to the paper under it.
                    water_due = false;
                    double flow_now = now(), dt = std::clamp(flow_now - last_flow, 0., .5);
                    last_flow = flow_now;
                    double pace = water_pace();
                    flow_time += dt * pace;
                    if (!water_frozen && pace > 0)
                        state.renderer.flow_dye(flow_time, dt * 9. * pace);
                }
                auto area = drawn_area();
                // The backdrop is never copied in dry content, whether or not the test
                // switch keeps it in the drawn area.
                state.renderer.draw(data, area, breath_area, state.breath, state.sleeping, breath_keys,
                                    reuse_backdrop, &dry, &dry, &own_area());
                // GO28: the first frame asleep takes what lies beneath as seen; later frames
                // that copied backdrop under the liquid ask whether it changed.
                if (state.sleeping && seen_due)
                {
                    seen_due = false;
                    state.renderer.backdrop_seen();
                } else if (state.renderer.under_waiting())
                {
                    // After the frame: the check (and the pickup texture's refresh) never
                    // interrupts a frame's rendering (GO28, GO10).
                    idle_foreign = idle_foreign || frame_foreign;
                    if (!idle_check.is_connected())
                        idle_check.run_once([this] { SCOTTLAND_LOOP_SCOPE(goo_pickup_check); backdrop_copied(std::exchange(idle_foreign, false)); });
                }
            });
    }
};
goo_instance_t::goo_instance_t(goo_node_t *s, wf::scene::damage_callback d, wf::output_t *o)
    : simple_render_instance_t(s, d, o)
{
}
void goo_instance_t::render(const wf::scene::render_instruction_t &data) { SCOTTLAND_LOOP_SCOPE(goo_render); self->render(data, !reuse.empty()); }
void goo_instance_t::schedule_instructions(std::vector<wf::scene::render_instruction_t> &instructions,
                                           const wf::render_target_t &target, wf::regionf_t &damage)
{
    SCOTTLAND_LOOP_SCOPE(goo_schedule);
    // Scheduled even with no damage of its own, as before: render() also steps the simulation.
    auto ours = damage & self->get_bounding_box();
    reuse = ours.empty() ? wf::regionf_t{} : self->breath_only_frame(target, damage);
    instructions.push_back(wf::scene::render_instruction_t{.instance = this, .target = target, .damage = ours});
    // Those pixels are fully painted by the goo this frame: nothing behind draws there.
    damage ^= reuse;
}
} // namespace
struct goo_t::impl
{
    work::worker_t *worker = nullptr;
    // Energy readings are collected inside each output's render pass and otherwise by this one
    // timer for all outputs, armed only while some reading is in flight; at most two slots are
    // examined per run across all outputs (main-loop Phase 3).
    wf::wl_timer<true> collect_tick;
    // One allowance per main-loop dispatch, whichever outputs render or the timer collects in
    // it: refilled by an idle callback, which Wayfire's loop runs once the dispatch's events are
    // handled. The timer starts at a rotating output, so an unsignalled reading on one output
    // doesn't keep deferring another's.
    goo::renderer_t::allowance_t allowance;
    wf::wl_idle_call refill;
    size_t collect_cursor = 0;
    uint64_t examined_max = 0;  // the most slots examined in one dispatch (goo-state)
    void spent()
    {
        if (allowance.left < 2 && !refill.is_connected())
            refill.run_once([this]
            {
                SCOTTLAND_LOOP_SCOPE(goo_collect_refill);
                examined_max = std::max(examined_max, allowance.examined);
                allowance.examined = 0;
                allowance.left = 2;
            });
    }
    void arm_collect()
    {
        if (collect_tick.is_connected()) return;
        collect_tick.set_timeout(16, [this]
        {
            SCOTTLAND_LOOP_SCOPE(goo_collect_tick);
            bool pending = false;
            wf::gles::run_in_context_if_gles([&]
            {
                goo::gl::state_t guard(true);
                std::vector<goo_node_t *> order;
                for (auto &[o, n] : nodes)
                    order.push_back(n.get());
                size_t first = order.empty() ? 0 : collect_cursor++ % order.size();
                for (size_t i = 0; i < order.size(); i++)
                    order[(first + i) % order.size()]->state.renderer.collect(allowance.left);
                spent();
                for (auto *n : order)
                    pending |= n->state.renderer.readback_pending();
            });
            return pending;
        });
    }
    wf::wl_idle_call fallback;
    goo_t::source_provider_t snapshot;
    std::function<void(wf::output_t *, bool)> screen_changed;
    wf::option_wrapper_t<bool> enabled{"scottland/goo"};
    wf::option_wrapper_t<bool> breath_keys{"scottland/goo_breath_keys"};
    wf::option_wrapper_t<std::string> curve{"scottland/goo_falloff"};
    std::vector<std::unique_ptr<wf::option_wrapper_t<double>>> options;
    std::map<wf::output_t *, std::shared_ptr<goo_node_t>> nodes;
    wf::shared_data::ref_ptr_t<wf::ipc::method_repository_t> ipc;
    wf::signal::connection_t<wf::output_added_signal> added = [this](wf::output_added_signal *e)
    { SCOTTLAND_LOOP_SCOPE(goo_output_added); add(e->output); };
    wf::signal::connection_t<wf::output_removed_signal> removed = [this](wf::output_removed_signal *e)
    { SCOTTLAND_LOOP_SCOPE(goo_output_removed); remove(e->output); };
    struct option_t
    {
        const char *name;
        float goo::settings_t::*field;
    };
    const std::vector<option_t> fields = {
        {"thickness", &goo::settings_t::thickness}, {"reach", &goo::settings_t::reach},
        {"dye_density", &goo::settings_t::dye_density},
        {"thinning", &goo::settings_t::thinning},   {"swell", &goo::settings_t::swell},
        {"noise", &goo::settings_t::noise},         {"lump", &goo::settings_t::lump},
        {"drift", &goo::settings_t::drift},         {"wave_speed", &goo::settings_t::wave_speed},
        {"wave_damp", &goo::settings_t::wave_damp}, {"wave_height", &goo::settings_t::wave_height},
        {"spread", &goo::settings_t::spread},       {"swirl", &goo::settings_t::swirl},
        {"release", &goo::settings_t::release},     {"shine", &goo::settings_t::shine},
        {"relief", &goo::settings_t::relief},
        {"depth", &goo::settings_t::depth}, {"profile", &goo::settings_t::profile},
        {"soak", &goo::settings_t::soak}, {"pickup_balance", &goo::settings_t::pickup_balance},
        {"overlap_film", &goo::settings_t::overlap_film},
        {"hover_cloudiness", &goo::settings_t::hover_cloudiness},
        {"hover_emissivity", &goo::settings_t::hover_emissivity},
        {"hover_distance", &goo::settings_t::hover_distance}};
    void config()
    {
        for (size_t i = 0; i < fields.size(); i++)
            goo::current_settings.*fields[i].field = options[i]->value();
        if (!goo::current_settings.curve(curve.value()))
            loop::note(loop::note_id::goo_invalid_falloff);
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
            n->breath_keys = breath_keys;
            n->state.settings = goo::current_settings;
            n->state.renderer.invalidate();
            n->state.revision++;
            n->band_cache.reset();
            n->update_breathing();
            n->whole = true;
            n->last_change = now();
            n->wake("settings");
        }
        for (auto &v : wf::get_core().get_all_views())
            v->damage();
    }
    void add(wf::output_t *o)
    {
        if (!goo::enabled || nodes.count(o))
            return;
        auto n = std::make_shared<goo_node_t>(o, snapshot);
        n->open_shrink_lane(worker);
        n->readback_issued = [this] { arm_collect(); };
        n->state.renderer.allowance = &allowance;
        n->shrink_hold = shrink_hold;
        allowance.spent = [this] { spent(); };
        n->breath_keys = breath_keys;
        n->failed = [this]
        {
            fallback.run_once(
                [this]
                {
                    SCOTTLAND_LOOP_SCOPE(goo_fallback);
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
        n->wake("start");
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
    std::shared_ptr<std::atomic<bool>> shrink_hold = std::make_shared<std::atomic<bool>>(false);
    wf::ipc::method_callback state = [this](const wf::json_t &data)
    {
        SCOTTLAND_LOOP_SCOPE(goo_state);
        if (getenv("SCOTTLAND_TEST_MODEL") && data.has_member("shrink_hold") && data["shrink_hold"].is_bool())
            *shrink_hold = data["shrink_hold"].as_bool();
        wf::json_t out;
        out["enabled"] = goo::enabled;
        out["breath_keys_enabled"] = bool(breath_keys);
        auto list = wf::json_t::array();
        for (auto &[o, n] : nodes)
        {
            wf::json_t s;
            bool test_changed = false;
            if (getenv("SCOTTLAND_TEST_MODEL"))
            {
                if (data.has_member("breath_hold") &&
                    (data["breath_hold"].is_int() || data["breath_hold"].is_double()))
                {
                    n->breath_hold = data["breath_hold"].as_double();
                    n->state.breath = n->breath_hold >= 0 ? n->breath_hold : goo::attention_breath(now());
                    test_changed = true;
                }
                if (data.has_member("surface_cache_fail") && data["surface_cache_fail"].is_bool())
                {
                    n->state.renderer.surface_cache_fail = data["surface_cache_fail"].as_bool();
                    test_changed = true;
                }
                if (data.has_member("breath_layer_fail") && data["breath_layer_fail"].is_bool())
                {
                    n->state.renderer.breath_layer_fail = data["breath_layer_fail"].as_bool();
                    test_changed = true;
                }
                if (data.has_member("breath_exact") && data["breath_exact"].is_bool())
                {
                    n->state.renderer.breath_exact = data["breath_exact"].as_bool();
                    test_changed = true;
                }
                if (data.has_member("dry_content") && data["dry_content"].is_bool())
                {
                    n->dry_enabled = data["dry_content"].as_bool();
                    test_changed = true;
                }
                if (data.has_member("breath_max_rects") && data["breath_max_rects"].is_int())
                {
                    n->max_breath_rects = std::clamp<int>(data["breath_max_rects"].as_int(), 1, 16);
                    n->update_breathing();
                    if (n->state.sleeping)
                        n->start_settling();
                    test_changed = true;
                }
                if (data.has_member("timed_sleep") && data["timed_sleep"].is_bool())
                {
                    n->state.renderer.force_timed_sleep = data["timed_sleep"].as_bool();
                    test_changed = true;
                }
                if (data.has_member("readback_fault") && data["readback_fault"].is_string())
                {
                    n->state.renderer.set_readback_fault(data["readback_fault"].as_string());
                    test_changed = true;
                }
                if (data.has_member("water_motion") && data["water_motion"].is_bool())
                {
                    n->water_enabled = data["water_motion"].as_bool();
                    n->set_breath_area(n->settled_ready ? n->whole_pixels(n->breath_support & n->settled_area) : n->breath_support);
                }
                if (data.has_member("water_coast") && (data["water_coast"].is_int() || data["water_coast"].is_double()) &&
                    n->state.sleeping)
                {
                    // Tests restart (or lengthen, or end) the coast in a sleeping goo.
                    n->start_water(data["water_coast"].as_double());
                    n->set_breath_area(n->settled_ready ? n->whole_pixels(n->breath_support & n->settled_area) : n->breath_support);
                }
                if (data.has_member("pickup_reset") && data["pickup_reset"].is_bool() && data["pickup_reset"].as_bool())
                {
                    n->pickup_timer.disconnect();
                    n->check_timer.disconnect();
                    n->pickup = goo::pickup_policy_t{};
                }
                if (data.has_member("water_elapsed") && (data["water_elapsed"].is_int() || data["water_elapsed"].is_double()))
                    n->water_started = now() - data["water_elapsed"].as_double();
                if (data.has_member("water_freeze") && data["water_freeze"].is_bool())
                    n->water_frozen = data["water_freeze"].as_bool();
                if (data.has_member("reuse_deaf") && data["reuse_deaf"].is_bool())
                    n->deaf = data["reuse_deaf"].as_bool();
                if (data.has_member("breath_reuse") && data["breath_reuse"].is_bool())
                    n->reuse_enabled = data["breath_reuse"].as_bool();
                if (data.has_member("breath_tight") && data["breath_tight"].is_bool() &&
                    n->breath_tight != data["breath_tight"].as_bool())
                {
                    n->breath_tight = data["breath_tight"].as_bool();
                    n->settled_ready = false;
                    n->update_breathing();
                    if (n->state.sleeping)
                        n->start_settling();
                    test_changed = true;
                }
            }
            if (test_changed)
                n->damage();
            n->state.renderer.poll_timing();
            s["output"] = o->handle->name;
            s["sleeping"] = n->state.sleeping;
            wf::json_t wakes;
            for (auto &[reason, count] : n->wake_counts)
                wakes[reason] = (int64_t)count;
            s["wakes"] = wakes;
            s["last_wake"] = n->last_wake;
            s["wallpaper_node_changes"] = (int64_t)n->wallpaper_node_changes;
            s["open_pickup"] = n->state.renderer.open_pickup;
            // GO28: pickup of what lies beneath, and its bounded wakes.
            s["under_pixels"] = (int64_t)n->state.renderer.under_pixels;
            s["backdrop_checks"] = (int64_t)n->state.renderer.backdrop_checks;
            s["backdrop_changes"] = (int64_t)n->backdrop_changes;
            s["pickup_coasts"] = (int64_t)n->pickup_coasts;
            s["pickup_deferred"] = (int64_t)n->pickup_deferred;
            s["pickup_pending"] = n->pickup.pending;
            s["pickup_gap"] = n->pickup.gap;
            s["pickup_wait"] = std::max(0., n->pickup.next - now());
            s["breath_keys_enabled"] = n->breath_keys;
            s["steps"] = (int64_t)n->state.renderer.steps;
            s["step_ms"] = n->state.renderer.last_step_ms;
            s["gpu_ms"] = n->state.renderer.last_gpu_ms;
            s["draw_gpu_ms"] = n->state.renderer.last_draw_gpu_ms;
            s["gpu_timing"] = n->state.renderer.gpu_timing;
            s["flow_gpu_ms"] = n->state.renderer.flow_gpu_ms;
            s["check_gpu_ms"] = n->state.renderer.check_gpu_ms;
            s["seen_gpu_ms"] = n->state.renderer.seen_gpu_ms;
            s["seen_gpu_samples"] = (int64_t)n->state.renderer.seen_gpu_samples;
            s["flow_gpu_samples"] = (int64_t)n->state.renderer.flow_gpu_samples;
            s["check_gpu_samples"] = (int64_t)n->state.renderer.check_gpu_samples;
            s["flow_wall_ms"] = n->state.renderer.flow_wall_ms;
            s["flow_wall_max_ms"] = n->state.renderer.flow_wall_max_ms;
            s["check_wall_ms"] = n->state.renderer.check_wall_ms;
            s["check_wall_max_ms"] = n->state.renderer.check_wall_max_ms;
            s["seen_wall_ms"] = n->state.renderer.seen_wall_ms;
            s["seen_wall_max_ms"] = n->state.renderer.seen_wall_max_ms;
            s["seen_calls"] = (int64_t)n->state.renderer.seen_calls;
            s["pickup_callback_ms"] = n->pickup_callback_ms;
            s["pickup_callback_max_ms"] = n->pickup_callback_max_ms;
            s["pickup_callbacks"] = (int64_t)n->pickup_callbacks;
            s["draws"] = (int64_t)n->state.renderer.draws;
            s["breath_tightens"] = (int64_t)n->breath_tightens;
            s["shrink_rejected"] = (int64_t)n->shrink_rejected;
            s["readback"] = n->state.renderer.readback_mode();
            s["gl"] = n->state.renderer.gl_description();
            s["readback_pending"] = n->state.renderer.readback_pending();
            s["readback_generation"] = (int64_t)n->state.renderer.generation();
            s["readings_issued"] = (int64_t)n->state.renderer.readings_issued;
            s["readings_applied"] = (int64_t)n->state.renderer.readings_applied;
            s["readings_stale"] = (int64_t)n->state.renderer.readings_stale;
            s["readings_skipped"] = (int64_t)n->state.renderer.readings_skipped;
            s["last_applied_step"] = (int64_t)n->state.renderer.last_applied_step;
            s["readings_in_flight"] = (int64_t)n->state.renderer.readback_in_flight();
            s["tick_ms"] = n->tick_ms;
            s["tighten_ms"] = n->tighten_ms;
            // The settled (tight) region is still being worked out; conservative bands in use.
            s["breath_loose"] = n->state.sleeping && n->breath_tight && !n->settled_ready && n->shrink_pending;
            double settled = 0, loose = 0;
            for (auto &b : n->settled_area) settled += double(b.x2 - b.x1) * (b.y2 - b.y1);
            wf::regionf_t all;
            for (auto &b : n->bands()) all |= b;
            for (auto &b : all) loose += double(b.x2 - b.x1) * (b.y2 - b.y1);
            double dry_pixels = 0;
            for (auto &b : n->dry) dry_pixels += double(b.x2 - b.x1) * (b.y2 - b.y1);
            s["dry_pixels"] = dry_pixels;
            // Dry window content the (merged) strips cover: restored from the backdrop on a breath.
            double strip_dry = 0;
            for (auto &b : n->breath_area & n->dry) strip_dry += double(b.x2 - b.x1) * (b.y2 - b.y1);
            s["strip_dry_pixels"] = strip_dry;
            s["band_pixels"] = loose;
            s["settled_pixels"] = n->settled_ready ? settled : 0.;
            s["reuse_blocked"] = n->reuse_blocked;
            s["water_ticks"] = (int64_t)n->water_ticks;
            s["water_running"] = n->water_running;
            s["dye_flows"] = (int64_t)n->state.renderer.dye_flows;
            double motion = 0; int motion_rects = 0;
            for (auto &b : n->motion_area) { motion += double(b.x2 - b.x1) * (b.y2 - b.y1); motion_rects++; }
            s["motion_pixels"] = motion;
            s["motion_rects"] = motion_rects;
            s["backdrop_reuses"] = (int64_t)n->state.renderer.backdrop_reuses;
            s["surface_pixels"] = (int64_t)n->state.renderer.surface_pixels;
            s["capture_pixels"] = (int64_t)n->state.renderer.capture_pixels;
            s["composite_pixels"] = (int64_t)n->state.renderer.composite_pixels;
            s["breath_refreshes"] = (int64_t)n->state.renderer.breath_refreshes;
            s["breath_keys"] = (int64_t)n->state.renderer.breath_key_values.size();
            s["breath_keyframes_active"] = n->state.renderer.breath_keyframes_active;
            s["breath_exact_reason"] = n->state.renderer.breath_exact_reason;
            s["breath_key_spacing"] = n->state.renderer.breath_key_spacing;
            s["breath_key_ceiling"] = (int64_t)n->state.renderer.breath_key_ceiling;
            auto key_values = wf::json_t::array();
            for (float value : n->state.renderer.breath_key_values)
                key_values.append((double)value);
            s["breath_key_values"] = key_values;
            s["energy"] = n->state.renderer.energy;
            s["wave_energy"] = n->state.renderer.wave_energy;
            s["dye_energy"] = n->state.renderer.dye_energy;
            s["breath"] = n->state.breath;
            s["breath_ticks"] = (int64_t)n->breath_ticks;
            auto damage = wf::json_t::array();
            for (auto &b : n->breath_area)
            {
                wf::json_t r;
                r["x"] = b.x1; r["y"] = b.y1;
                r["width"] = b.x2-b.x1; r["height"] = b.y2-b.y1;
                damage.append(r);
            }
            s["breath_damage"] = damage;
            s["packed"] = n->state.renderer.packed;
            s["overlapping"] = n->state.renderer.overlapping();
            s["highlighting"] = n->state.renderer.highlighting();
            s["sources"] = (int64_t)n->state.sources.size();
            if (getenv("SCOTTLAND_TEST_MODEL"))
            {
                // Where the goo puts each island, for tests comparing it with the drawn window.
                auto rects = wf::json_t::array();
                for (auto &source : n->state.sources)
                {
                    wf::json_t r;
                    r["id"] = (int64_t)source.id;
                    r["x"] = source.rect.x - source.rect.z; r["y"] = source.rect.y - source.rect.w;
                    r["width"] = 2 * source.rect.z; r["height"] = 2 * source.rect.w;
                    r["hint_circle"] = source.hint_circle;
                    rects.append(r);
                }
                s["source_rects"] = rects;
            }
            if (data.has_member("x") && data.has_member("y") &&
                (data["x"].is_int() || data["x"].is_double()) &&
                (data["y"].is_int() || data["y"].is_double()))
            {
                glm::vec2 point{data["x"].as_double(), data["y"].as_double()};
                auto color = n->state.renderer.sample_at(point);
                s["density"] = goo::density(point, n->state.sources, n->state.settings, n->state.time, n->state.breath);
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
        out["collect_max_per_dispatch"] = (int64_t)examined_max;
        if (data.has_member("reset_collect") && data["reset_collect"].is_bool() && data["reset_collect"].as_bool())
            examined_max = 0;
        return out;
    };
};
goo_t::goo_t() : p(std::make_unique<impl>()) {}
goo_t::~goo_t() = default;
void goo_t::set_worker(work::worker_t *worker) { p->worker = worker; }
void goo_t::start(source_provider_t snapshot, std::function<void(wf::output_t *, bool)> screen_changed)
{
    goo::shape_cache_t::prepare();
    p->snapshot = std::move(snapshot);
    p->screen_changed = std::move(screen_changed);
    for (auto &field : p->fields)
    {
        auto o = std::make_unique<wf::option_wrapper_t<double>>(std::string("scottland/goo_") + field.name);
        o->set_callback([this] { SCOTTLAND_LOOP_SCOPE(goo_option); p->config(); });
        p->options.push_back(std::move(o));
    }
    p->enabled.set_callback([this] { SCOTTLAND_LOOP_SCOPE(goo_option); p->config(); });
    p->breath_keys.set_callback([this] {
        SCOTTLAND_LOOP_SCOPE(goo_option);
        for (auto &[o, n] : p->nodes)
        {
            n->breath_keys = p->breath_keys;
            n->damage();
        }
    });
    p->curve.set_callback([this] { SCOTTLAND_LOOP_SCOPE(goo_option); p->config(); });
    wf::get_core().output_layout->connect(&p->added);
    wf::get_core().output_layout->connect(&p->removed);
    p->ipc->register_method("scottland/goo-state", p->state);
    p->config();
}
void goo_t::stop()
{
    p->fallback.disconnect();
    p->collect_tick.disconnect();
    p->added.disconnect();
    p->removed.disconnect();
    p->ipc->unregister_method("scottland/goo-state");
    while (!p->nodes.empty())
        p->remove(p->nodes.begin()->first);
    goo::enabled = false;
}
} // namespace scottland
