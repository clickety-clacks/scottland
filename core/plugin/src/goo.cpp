#include "goo.hpp"
#include "attention-breath.hpp"
#include "frame.hpp"
#include "goo-runtime.hpp"
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
    bool foreign_damage = true, own_damage = false, reuse_enabled = true, dry_enabled = true;
    int reuse_streak = 0;
    bool deaf = false;  // tests only: ignore other damage, to prove the exactness test can fail
    std::string reuse_blocked;
    std::unique_ptr<wf::scene::render_instance_manager_t> scene_observer;
    wf::signal::connection_t<wf::scene::root_node_update_signal> on_scene_update =
        [this] (wf::scene::root_node_update_signal *) { foreign_damage = true; };
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
        wf::regionf_t reuse;
        // Why the last frame took the normal path (goo-state, for tests and live reading).
        reuse_blocked = foreign ? "other damage" : !scene_observer || !reuse_enabled ? "off" :
            !state.sleeping || whole || breath_area.empty() ? "not a sleeping breath" :
            reuse_streak >= 25 ? "periodic refresh" :
            !state.renderer.backdrop_ready(target) ? "backdrop not ready for this target" : "";
        if (reuse_blocked.empty() && attached && goo_enabled() && wf::get_core().is_gles2() &&
            !(state.sources.size() == 1 && !state.sources[0].emitter))
        {
            auto pixels = target.framebuffer_region_from_geometry_region(damage);
            auto strips = target.framebuffer_region_from_geometry_region(breath_area);
            if ((pixels ^ strips).empty())
                reuse = target.geometry_region_from_framebuffer_region(pixels);
            else
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
    goo_t::source_provider_t snapshot;
    goo_node_t(wf::output_t *o, goo_t::source_provider_t provider) : node_t(false), snapshot(std::move(provider))
    {
        state.output = o;
        state.settings = goo::current_settings;
        state.wake = [this] { wake("frame"); };
        pre = [this] { prepare(); };
        o->render->add_effect(&pre, wf::OUTPUT_EFFECT_PRE);
    }
    ~goo_node_t() { detach(); }
    void detach()
    {
        wallpaper_instances.clear();
        wallpaper_nodes.clear();
        if (attached)
            state.output->render->rem_effect(&pre);
        attached = false;
        tick.disconnect();
        breath_tick.disconnect();
        settle_tick.disconnect();
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
    // adds the least area until they fit. The strips may then cover a little dry content,
    // so the backdrop is kept current there too (dry_capture).
    static constexpr size_t max_breath_rects = 16;
    wf::regionf_t dry_capture;
    void set_breath_area(const wf::regionf_t &exact)
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
        breath_area = exact;
        // Joining boxes can make the union band into more rectangles than were joined,
        // so aim lower until the banded result fits. One box always does.
        for (size_t target = max_breath_rects; count(breath_area) > max_breath_rects && target >= 1; target--)
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
            breath_area.clear();
            for (auto &r : rects) breath_area |= r;
        }
        breath_area &= get_bounding_box();
        dry_capture = dry ^ breath_area;
    }
    void update_breathing()
    {
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
    // The conservative GO17 support includes dry liquid reach. Once the field
    // is asleep, shrink each rectangle to its widest-breath wet density plus a
    // reconstruction margin. Masked alpha contours use a denser lattice so a
    // thin lobe is covered by the same margin rather than disabling tightening.
    //
    // Sampling the field is slow (about a second for one large window in a debug build),
    // so it runs in slices of a couple of milliseconds per breath tick; the conservative
    // strips stay in use until it finishes. A long strip is sampled coarsely along its
    // length: the shore there changes over the liquid's reach, not from pixel to pixel.
    struct tighten_t
    {
        std::vector<wf::geometry_t> rects;
        size_t index = 0;
        double y = 0, x1 = 1e9, y1 = 1e9, x2 = -1e9, y2 = -1e9;
        bool row_started = false;
        wf::regionf_t tight;
    };
    std::optional<tighten_t> tightening;
    // GO20: the same shrinking for every band, not only the breathing strips. While the
    // goo sleeps, drawing, the backdrop copy and the composite use this region, so an app
    // frame that touches the goo works on the liquid, not on the dry reach around it.
    // Any wake returns to the conservative bands (waves can push the shore out).
    wf::regionf_t settled_area, breath_support;
    bool settled_ready = false;
    wf::wl_timer<true> settle_tick;
    void start_settling()
    {
        settled_ready = false;
        tightening.reset();
        settle_tick.disconnect();
        if (!breath_tight || !attached)
            return;
        settle_tick.set_timeout(20, [this]
        {
            if (!state.sleeping || !breath_tight)
                return false;
            tighten_breathing();
            return !settled_ready;
        });
    }
    void tighten_breathing(double budget_ms = 2)
    {
        double tighten_start = now();
        if (!tightening)
        {
            tightening.emplace();
            tightening->rects = bands();
            tighten_ms = 0;
        }
        auto &job = *tightening;
        const float wet = .5f * state.settings.threshold();
        const double support_padding = 5 + 1. / state.output->handle->scale;
        const double reach = 4 * state.settings.reach + support_padding;
        while (job.index < job.rects.size())
        {
            auto &b = job.rects[job.index];
            double bx2 = b.x + b.width, by2 = b.y + b.height;
            bool masked = false;
            for (auto &source : state.sources)
            {
                if (!source.shape) continue;
                auto body = source.shape_body.z > 0 && source.shape_body.w > 0 ? source.shape_body : source.rect;
                if (body.x - body.z - reach < bx2 && body.x + body.z + reach > b.x &&
                    body.y - body.w - reach < by2 && body.y + body.w + reach > b.y)
                {
                    masked = true;
                    break;
                }
            }
            const double fine = masked ? 2 : 4, coarse = 4 * fine;
            const double step_x = b.width > 2 * b.height ? coarse : fine;
            const double step_y = b.height > 2 * b.width ? coarse : fine;
            if (!job.row_started)
            {
                job.y = b.y;
                job.x1 = job.y1 = 1e9;
                job.x2 = job.y2 = -1e9;
                job.row_started = true;
            }
            for (; job.y < by2 + step_y; job.y += step_y)
            {
                if ((now() - tighten_start) * 1000 > budget_ms)
                {
                    tighten_ms += (now() - tighten_start) * 1000;
                    return;  // resume at this row on the next tick
                }
                for (double x = b.x; x < bx2 + step_x; x += step_x)
                {
                    glm::vec2 point{std::min<double>(x, bx2), std::min<double>(job.y, by2)};
                    if (goo::density(point, state.sources, state.settings, state.time, 1) < wet)
                        continue;
                    job.x1 = std::min<double>(job.x1, point.x);
                    job.y1 = std::min<double>(job.y1, point.y);
                    job.x2 = std::max<double>(job.x2, point.x);
                    job.y2 = std::max<double>(job.y2, point.y);
                }
            }
            if (job.x2 >= job.x1)
            {
                const double pad = 5 + 1. / state.output->handle->scale;
                double x1 = std::max<double>(std::floor(job.x1 - step_x - pad), b.x);
                double y1 = std::max<double>(std::floor(job.y1 - step_y - pad), b.y);
                double x2 = std::min<double>(std::ceil(job.x2 + step_x + pad), bx2);
                double y2 = std::min<double>(std::ceil(job.y2 + step_y + pad), by2);
                job.tight |= wf::geometry_t{x1, y1, x2 - x1, y2 - y1};
            } else
                job.tight |= b;  // nothing sampled wet: keep the whole band, never drop goo
            job.index++;
            job.row_started = false;
        }
        breath_loose = false;
        ++breath_tightens;
        settled_area = whole_pixels(job.tight);
        settled_ready = true;
        set_breath_area(whole_pixels(breath_support & settled_area));
        tighten_ms += (now() - tighten_start) * 1000;
        tightening.reset();
    }
    // Wakes of a sleeping simulation by cause, for goo-state: each one runs the full
    // simulation and redraws every band for at least three seconds.
    std::map<std::string, uint64_t> wake_counts;
    std::string last_wake;
    uint64_t wake_step = 0, breath_tightens = 0;
    double tick_ms = 0, tighten_ms = 0;
    void wake(const char *reason)
    {
        if (state.sleeping)
        {
            wake_counts[reason]++;
            last_wake = reason;
            wake_step = state.renderer.steps;
            settled_ready = false;
            tightening.reset();
            settle_tick.disconnect();
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
    // GO15: render only background children, excluding this goo node. The
    // quarter-resolution cache has its own damage callbacks: app redraws and
    // goo damage cannot feed back into dye or keep the simulation awake.
    wf::auxilliary_buffer_t wallpaper;
    std::vector<wf::scene::node_ptr> wallpaper_nodes;
    std::vector<wf::scene::render_instance_uptr> wallpaper_instances;
    bool wallpaper_dirty = true;
    // The last capture that woke the dye, and counters for goo-state: background damage
    // callbacks, captures taken, captures that differed.
    std::vector<uint8_t> wallpaper_pixels;
    static constexpr size_t wallpaper_tolerated = 16;
    uint64_t wallpaper_damages = 0, wallpaper_captures = 0, wallpaper_changes = 0, wallpaper_node_changes = 0;
    wf::geometry_t wallpaper_damage_box{};
    glm::mat4 wallpaper_map{1};
    void prepare_wallpaper()
    {
        if (state.settings.soak <= 0) return;
        std::vector<wf::scene::node_ptr> next;
        for (auto &child : state.output->node_for_layer(wf::scene::layer::BACKGROUND)->get_children())
            if (child.get() != this && child->is_enabled()) next.push_back(child);
        if (next != wallpaper_nodes)
        {
            wallpaper_instances.clear();
            wallpaper_nodes = next;
            for (auto &child : wallpaper_nodes)
                child->gen_render_instances(wallpaper_instances, [this](const wf::regionf_t &region) {
                    // Only a recapture: prepare_wallpaper() wakes the simulation if the
                    // captured pixels changed. The damage repaints the output, so it runs.
                    wallpaper_dirty = true;
                    ++wallpaper_damages;
                    double x1 = 1e9, y1 = 1e9, x2 = -1e9, y2 = -1e9;
                    for (auto &b : region)
                    {
                        x1 = std::min<double>(x1, b.x1); y1 = std::min<double>(y1, b.y1);
                        x2 = std::max<double>(x2, b.x2); y2 = std::max<double>(y2, b.y2);
                    }
                    if (x2 > x1)
                        wallpaper_damage_box = wf::geometry_t{x1, y1, x2 - x1, y2 - y1};
                }, state.output);
            wallpaper_dirty = true;
            wallpaper_pixels.clear();
            ++wallpaper_node_changes;
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
            wake("wallpaper"); // also remove old wallpaper dye when the last background disappears
        }
        // No wallpaper client means no color source, not an implicit black dye.
        if (wallpaper_nodes.empty()) return;
        auto g = get_bounding_box();
        auto allocation = wallpaper.allocate(wf::dimensions(g), .25);
        if (allocation == wf::buffer_reallocation_result_t::FAILED) return;
        if (allocation == wf::buffer_reallocation_result_t::SAME && !wallpaper_dirty) return;
        wf::render_target_t target{wallpaper};
        target.geometry = g;
        target.scale = .25;
        wf::render_pass_params_t params;
        params.instances = &wallpaper_instances;
        params.target = target;
        params.damage = g;
        params.flags = wf::RPASS_CLEAR_BACKGROUND;
        params.background_color = {0, 0, 0, 1};
        wf::render_pass_t::run(params);
        wallpaper_map = wf::gles::render_target_orthographic_projection(target);
        wallpaper_dirty = false;
        // GO20: a background client can commit again without changing a pixel (a shell
        // that keeps its render loop running, a re-attached buffer). Only a capture that
        // differs wakes the dye: more than a few pixels, by more than rounding or dither.
        ++wallpaper_captures;
        std::vector<uint8_t> pixels;
        auto size = wallpaper.get_size();
        wf::gles::run_in_context_if_gles([&]
        {
            GLint previous = 0;
            glGetIntegerv(GL_FRAMEBUFFER_BINDING, &previous);
            glBindFramebuffer(GL_FRAMEBUFFER, wf::gles::ensure_render_buffer_fb_id(target));
            pixels.resize(size_t(size.width) * size.height * 4);
            glReadPixels(0, 0, size.width, size.height, GL_RGBA, GL_UNSIGNED_BYTE, pixels.data());
            glBindFramebuffer(GL_FRAMEBUFFER, previous);
        });
        size_t changed = 0;
        if (pixels.empty() || pixels.size() != wallpaper_pixels.size())
            changed = SIZE_MAX;
        else
            for (size_t i = 0; i < pixels.size() && changed <= wallpaper_tolerated; i += 4)
                changed += std::abs(pixels[i] - wallpaper_pixels[i]) > 4 ||
                    std::abs(pixels[i + 1] - wallpaper_pixels[i + 1]) > 4 ||
                    std::abs(pixels[i + 2] - wallpaper_pixels[i + 2]) > 4;
        if (changed <= wallpaper_tolerated)
            return;
        wallpaper_pixels = std::move(pixels);
        ++wallpaper_changes;
        last_change = now();
        wake("wallpaper");
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
        observe_scene(reuse_enabled && !breath_area.empty());
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
        // Drift and curl freeze after the response, then actual GPU energy decides sleep.
        if (t - last_change < 2)
            state.time += std::min(.05, t - last_step);
        last_step = t;
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
                if (!state.sleeping)
                {
                    goo::amounts(state.sources, state.settings);
                    bool ok = state.renderer.update(state.sources, state.settings, g.width, g.height,
                                                    state.time, state.impulses, sim_tiles(band),
                                                    !wallpaper_nodes.empty() && wallpaper.get_buffer() ? &wallpaper : nullptr, wallpaper_map);
                    state.impulses.clear();

                    if (!ok)
                    {
                        state.sleeping = true;
                        LOGE("scottland goo: simulation unavailable; retaining halo");
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
                        if (now() - last_change > 3 && state.renderer.energy <= sleep_energy &&
                            state.renderer.steps - wake_step >= 30)
                        {
                            state.sleeping = true;
                            tick.disconnect();
                            start_settling();
                        }
                    }
                }
                wf::regionf_t area;
                if (state.sleeping && settled_ready)
                    area = settled_area;
                else
                    for (auto &b : band)
                        area |= b;
                if (dry_enabled)
                    area ^= dry;
                // The backdrop is never copied in dry content, whether or not the test
                // switch keeps it in the drawn area.
                state.renderer.draw(data, area, breath_area, state.breath, state.sleeping, breath_keys,
                                    reuse_backdrop, &dry_capture);
            });
    }
};
goo_instance_t::goo_instance_t(goo_node_t *s, wf::scene::damage_callback d, wf::output_t *o)
    : simple_render_instance_t(s, d, o)
{
}
void goo_instance_t::render(const wf::scene::render_instruction_t &data) { self->render(data, !reuse.empty()); }
void goo_instance_t::schedule_instructions(std::vector<wf::scene::render_instruction_t> &instructions,
                                           const wf::render_target_t &target, wf::regionf_t &damage)
{
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
        {"dye_strength", &goo::settings_t::dye_strength},
        {"thinning", &goo::settings_t::thinning},   {"swell", &goo::settings_t::swell},
        {"noise", &goo::settings_t::noise},         {"lump", &goo::settings_t::lump},
        {"drift", &goo::settings_t::drift},         {"wave_speed", &goo::settings_t::wave_speed},
        {"wave_damp", &goo::settings_t::wave_damp}, {"wave_height", &goo::settings_t::wave_height},
        {"spread", &goo::settings_t::spread},       {"swirl", &goo::settings_t::swirl},
        {"release", &goo::settings_t::release},     {"shine", &goo::settings_t::shine},
        {"relief", &goo::settings_t::relief},
        {"depth", &goo::settings_t::depth}, {"profile", &goo::settings_t::profile},
        {"soak", &goo::settings_t::soak},
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
            n->breath_keys = breath_keys;
            n->state.settings = goo::current_settings;
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
        n->breath_keys = breath_keys;
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
    wf::ipc::method_callback state = [this](const wf::json_t &data)
    {
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
            s["output"] = o->handle->name;
            s["sleeping"] = n->state.sleeping;
            wf::json_t wakes;
            for (auto &[reason, count] : n->wake_counts)
                wakes[reason] = (int64_t)count;
            s["wakes"] = wakes;
            s["last_wake"] = n->last_wake;
            s["wallpaper_damages"] = (int64_t)n->wallpaper_damages;
            s["wallpaper_captures"] = (int64_t)n->wallpaper_captures;
            s["wallpaper_changes"] = (int64_t)n->wallpaper_changes;
            s["wallpaper_node_changes"] = (int64_t)n->wallpaper_node_changes;
            wf::json_t box;
            box["x"] = n->wallpaper_damage_box.x; box["y"] = n->wallpaper_damage_box.y;
            box["width"] = n->wallpaper_damage_box.width; box["height"] = n->wallpaper_damage_box.height;
            s["wallpaper_last_damage"] = box;
            s["breath_keys_enabled"] = n->breath_keys;
            s["steps"] = (int64_t)n->state.renderer.steps;
            s["step_ms"] = n->state.renderer.last_step_ms;
            s["gpu_ms"] = n->state.renderer.last_gpu_ms;
            s["draw_gpu_ms"] = n->state.renderer.last_draw_gpu_ms;
            s["draws"] = (int64_t)n->state.renderer.draws;
            s["breath_tightens"] = (int64_t)n->breath_tightens;
            s["tick_ms"] = n->tick_ms;
            s["tighten_ms"] = n->tighten_ms;
            // The settled (tight) region is still being worked out; conservative bands in use.
            s["breath_loose"] = n->state.sleeping && n->breath_tight && !n->settled_ready &&
                n->settle_tick.is_connected();
            double settled = 0, loose = 0;
            for (auto &b : n->settled_area) settled += double(b.x2 - b.x1) * (b.y2 - b.y1);
            wf::regionf_t all;
            for (auto &b : n->bands()) all |= b;
            for (auto &b : all) loose += double(b.x2 - b.x1) * (b.y2 - b.y1);
            double dry_pixels = 0;
            for (auto &b : n->dry) dry_pixels += double(b.x2 - b.x1) * (b.y2 - b.y1);
            s["dry_pixels"] = dry_pixels;
            s["band_pixels"] = loose;
            s["settled_pixels"] = n->settled_ready ? settled : 0.;
            s["reuse_blocked"] = n->reuse_blocked;
            s["backdrop_reuses"] = (int64_t)n->state.renderer.backdrop_reuses;
            s["surface_pixels"] = (int64_t)n->state.renderer.surface_pixels;
            s["capture_pixels"] = (int64_t)n->state.renderer.capture_pixels;
            s["composite_pixels"] = (int64_t)n->state.renderer.composite_pixels;
            s["breath_refreshes"] = (int64_t)n->state.renderer.breath_refreshes;
            s["breath_keys"] = (int64_t)n->state.renderer.breath_key_values.size();
            s["breath_keyframes_active"] = n->state.renderer.breath_keyframes_active;
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
        return out;
    };
};
goo_t::goo_t() : p(std::make_unique<impl>()) {}
goo_t::~goo_t() = default;
void goo_t::start(source_provider_t snapshot, std::function<void(wf::output_t *, bool)> screen_changed)
{
    goo::shape_cache_t::prepare();
    p->snapshot = std::move(snapshot);
    p->screen_changed = std::move(screen_changed);
    for (auto &field : p->fields)
    {
        auto o = std::make_unique<wf::option_wrapper_t<double>>(std::string("scottland/goo_") + field.name);
        o->set_callback([this] { p->config(); });
        p->options.push_back(std::move(o));
    }
    p->enabled.set_callback([this] { p->config(); });
    p->breath_keys.set_callback([this] {
        for (auto &[o, n] : p->nodes)
        {
            n->breath_keys = p->breath_keys;
            n->damage();
        }
    });
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
