#include "frame.hpp"
#include "goo-runtime.hpp"
#include "goo.hpp"

namespace scottland::goo
{
bool enabled = false;
settings_t current_settings;
std::map<wf::output_t *, screen_t *> screens;
} // namespace scottland::goo
namespace scottland
{
bool goo_enabled() { return goo::enabled; }
double goo_hover_distance() { return goo::current_settings.hover_distance; }
double goo_thickness(double scale, double swell)
{
    auto &s = goo::current_settings;
    double rest = s.thickness * scale;
    return std::max(rest * .7, rest + (2 * s.thickness - rest) * swell * (s.swell / .7));
}
handle_t goo_handle(const frame_t &frame, wf::pointf_t point)
{
    auto v = frame.toplevel();
    if (!v || v->pending_fullscreen())
        return handle_t::none;
    auto it = goo::screens.find(v->get_output());
    if (it == goo::screens.end())
        return handle_t::none;
    auto &screen = *it->second;
    // Hint circles are click-through: neither their islands nor their liquid
    // may mask app content or acquire a window's grab/resize ownership.
    // Built once per source or settings change (design 2.4), not per hit test.
    if (screen.input_revision != screen.revision)
    {
        screen.input_sources.clear();
        if (std::any_of(screen.sources.begin(), screen.sources.end(), [](auto &s) { return s.hint_circle; }))
            std::copy_if(screen.sources.begin(), screen.sources.end(), std::back_inserter(screen.input_sources),
                [](auto &s) { return !s.hint_circle; });
        if (!screen.input_sources.empty()) goo::amounts(screen.input_sources, screen.settings);
        screen.input_revision = screen.revision;
    }
    const auto &sources = screen.input_sources.empty() ? screen.sources : screen.input_sources;
    auto source = std::find_if(sources.begin(), sources.end(),
        [&](const auto& s) { return s.id == v->get_id(); });
    if (source == sources.end() || !source->emitter) return handle_t::none;
    rectf_t rect{source->rect.x - source->rect.z, source->rect.y - source->rect.w,
                 source->rect.x + source->rect.z, source->rect.y + source->rect.w};
    double radius = source->liquid.y;
    if (goo::distance({point.x, point.y}, *source) <= 0)
        return handle_t::none;
    glm::vec2 p{point.x, point.y};
    // A film over a back window belongs to a source ahead of it. Foreground
    // content still blocks input; the target never reaches through it.
    size_t back = goo::content_index(p, sources);
    if (size_t(source - sources.begin()) >= back) return handle_t::none;
    if (back < sources.size() && screen.settings.overlap_film <= 0) return handle_t::none;
    float f = goo::density(p, sources, screen.settings, screen.time, screen.breath);
    float threshold = screen.settings.threshold();
    float wave = 0;
    // Read the retained surface too: sleeping preserves its tiny residual height. Derive
    // the possible boundary from the live wave-height control, including its maximum.
    if (screen.settings.wave_height > 0 &&
        f > threshold / (1 + screen.settings.wave_height * 3.9f))
        wave = screen.renderer.wave_at(p);
    glm::vec2 probe = p;
    if (f * (1 + screen.settings.wave_height * wave) < threshold)
    {
        // A5's 12 pt target is a dilation of the same field, not a second handle shape.
        float edge = goo::distance({point.x, point.y}, *source);
        if (edge > MIN_GRAB)
            return handle_t::none;
        // Follow the rounded edge's normal rather than the center ray: that keeps the
        // target 12 pt wide on long edges and works for custom, non-exponential falloffs.
        auto d = [&](double x, double y) { return goo::distance({x, y}, *source); };
        glm::vec2 normal{d(point.x + .1, point.y) - d(point.x - .1, point.y),
                         d(point.x, point.y + .1) - d(point.x, point.y - .1)};
        if (glm::length(normal) < .001f)
            return handle_t::none;
        probe -= glm::normalize(normal) * (edge - .1f);
        if (goo::density(probe, sources, screen.settings, screen.time, screen.breath) < threshold)
            return handle_t::none;
    }
    // A shared bridge belongs to its strongest contributing edge. Keep the original
    // stable window-id tie break, independently of the renderer's stacking order.
    const goo::source_t *owner = nullptr;
    float contribution = -1;
    for (size_t i = 0; i < back; i++)
    {
        auto &s = sources[i];
        float c = s.liquid.x * screen.settings.fall(std::max(goo::distance(probe, s), 0.f));
        if (c > contribution + .00001f ||
            (std::abs(c - contribution) <= .00001f && owner && s.id < owner->id))
        {
            contribution = c;
            owner = &s;
        }
    }
    if (!owner || owner->id != v->get_id())
        return handle_t::none;
    wf::pointf_t dot{source->dot.x, source->dot.y};
    if (frame.dot_glow > .2 && std::hypot(point.x - dot.x, point.y - dot.y) <= DOT_RADIUS + 3)
        return handle_t::close;
    if (!frame.can_resize()) return handle_t::halo;
    auto r = rect;
    double grab = std::max(frame.thickness(), MIN_GRAB), reach = radius + grab + CORNER_EXTRA;
    bool left = point.x<r.x1 - grab + reach, right = point.x> r.x2 + grab - reach;
    bool top = point.y<r.y1 - grab + reach, bottom = point.y> r.y2 + grab - reach;
    if (top && left)
        return handle_t::top_left;
    if (top && right)
        return handle_t::top_right;
    if (bottom && left)
        return handle_t::bottom_left;
    if (bottom && right)
        return handle_t::bottom_right;
    return handle_t::halo;
}
void goo_wake(const frame_t &frame)
{
    auto v = frame.toplevel();
    auto it = v ? goo::screens.find(v->get_output()) : goo::screens.end();
    // An awake goo already repaints every frame and picks the change up from the sources.
    if (it != goo::screens.end() && it->second->sleeping && it->second->wake)
        it->second->wake();
}
void goo_impulse(const frame_t &frame, float strength)
{
    if (!goo_enabled())
        return;
    auto v = frame.toplevel();
    auto it = v ? goo::screens.find(v->get_output()) : goo::screens.end();
    if (it == goo::screens.end())
        return;
    auto &s = *it->second;
    auto r = frame.screen_rect();
    if (s.impulses.size() < 8)
        s.impulses.push_back({(r.x1 + r.x2) / 2, r.y2 + frame.thickness() / 2, strength, 26});
    if (s.wake)
        s.wake();
}
} // namespace scottland
