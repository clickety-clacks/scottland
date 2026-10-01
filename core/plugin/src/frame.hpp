#pragma once

// Window frame: every window is drawn as a rounded rectangle (A2) inside a liquid halo (A3-A11).
// The halo is always visible; it tints with focus, moves the window when dragged, resizes it
// from its corners (which cloud up as the cursor nears), swells like goo after a hover, merges
// with neighboring windows' halos under surface tension, and carries a close dot.
//
// The frame is the window's scale transformer too (it extends view_2d_transformer_t), so the
// rounding, the scale and the halo are one node in the window's transformer chain: the halo
// stacks with its window, follows it exactly through scale animations and drags, and a window
// in front covers the window behind and its halo, for drawing and for input.
//
// Shapes are signed distance fields, evaluated identically here (hit testing) and in the
// shaders (drawing). Distances are in the coordinates the window is drawn in (after scaling).

#include <wayfire/view-transform.hpp>
#include <wayfire/opengl.hpp>
#include <wayfire/core.hpp>
#include <wayfire/toplevel-view.hpp>
#include <wayfire/region.hpp>
#include <wayfire/scene-input.hpp>
#include <wayfire/scene-render.hpp>
#include <wayfire/touch/touch.hpp>
#include <wayfire/util.hpp>
#include <wayfire/window-manager.hpp>
#include <wayfire/util/duration.hpp>
#include <wayfire/config/types.hpp>
#include <glm/gtc/matrix_transform.hpp>
#include <glm/gtc/type_ptr.hpp>
#include <linux/input-event-codes.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <ctime>
#include <functional>
#include <vector>

namespace scottland
{
// Window space (scales with the window):
constexpr double CORNER_RADIUS = 10.0;  // double Omarchy's 5
constexpr double HALO = 32.0 / 3.0;     // halo thickness at rest (10.7 pt)
// On screen (constant size):
constexpr double MIN_GRAB     = 12.0;   // the halo's grab area is never thinner than this
constexpr double MERGE        = 10.0;   // surface-tension reach: halos ~5 pt apart bridge
constexpr double CORNER_EXTRA = 16.0;   // a corner's resize part runs this far past its curve
constexpr double NEAR_RANGE   = 64.0;   // corners cloud and the close dot shows within this
constexpr double DOT_RADIUS   = 7.0;
constexpr int MAX_NEIGHBORS   = 8;
constexpr double SWOLLEN = 2 * HALO;    // swollen halo thickness, on screen (doesn't scale)
constexpr double SWELL_VICINITY = 50.0; // the cursor pausing this near the halo swells it
constexpr double FOCUS_NUDGE = 2.2;
constexpr double LIFT_BULGE  = 0.0;     // the bulge pops out and pulls back to the normal size
constexpr double LIFT_KICK   = 1.6;     // bulge velocity at the lift: overshoots, then pulls back     // swell velocity given to a newly focused window's halo
constexpr int DWELL_MS  = 500;          // pause this long to swell
constexpr int LINGER_MS = 500;          // stay swollen this long after the cursor leaves

/** The halo's colors, set from the plugin's options. */
struct palette_t
{
    bool light = false;                 // light desktop: dark neutral tone; dark desktop: light
    glm::vec3 accent{0.506, 0.631, 0.757};
};

static palette_t palette;  // per loaded plugin copy (see meson.build)

enum class handle_t
{
    none, halo, top_left, top_right, bottom_left, bottom_right, close,
};

inline const char *handle_name(handle_t h)
{
    switch (h)
    {
      case handle_t::halo:         return "halo";
      case handle_t::top_left:     return "top-left";
      case handle_t::top_right:    return "top-right";
      case handle_t::bottom_left:  return "bottom-left";
      case handle_t::bottom_right: return "bottom-right";
      case handle_t::close:        return "close";
      case handle_t::none:         return "none";
    }

    return "none";
}

inline bool is_corner(handle_t h)
{
    return h == handle_t::top_left || h == handle_t::top_right || h == handle_t::bottom_left ||
           h == handle_t::bottom_right;
}

struct rectf_t
{
    double x1, y1, x2, y2;
    double width() const { return x2 - x1; }
    double height() const { return y2 - y1; }
    rectf_t grown(double by) const { return {x1 - by, y1 - by, x2 + by, y2 + by}; }
};

/** Signed distance from p to the rectangle's outline (negative inside). */
inline double box_distance(wf::pointf_t p, const rectf_t& r)
{
    double dx = std::max(r.x1 - p.x, p.x - r.x2);
    double dy = std::max(r.y1 - p.y, p.y - r.y2);
    double outside = std::hypot(std::max(dx, 0.0), std::max(dy, 0.0));
    return outside + std::min(std::max(dx, dy), 0.0);
}

/** Signed distance to a rounded rectangle. */
inline double round_box_distance(wf::pointf_t p, const rectf_t& r, double radius)
{
    radius = std::min({radius, r.width() / 2, r.height() / 2});
    double hx = r.width() / 2, hy = r.height() / 2;
    double qx = std::abs(p.x - (r.x1 + hx)) - hx + radius;
    double qy = std::abs(p.y - (r.y1 + hy)) - hy + radius;
    return std::hypot(std::max(qx, 0.0), std::max(qy, 0.0)) + std::min(std::max(qx, qy), 0.0) - radius;
}

/** Polynomial smooth minimum: joins two shapes with a rounded fillet about k wide. */
inline double smooth_min(double a, double b, double k)
{
    double h = std::max(k - std::abs(a - b), 0.0) / k;
    return std::min(a, b) - h * h * k / 4.0;
}

/** Brightness for a cursor `d` from something: zero `range` away, rising quickly as the cursor
 *  closes in (squared), full on it. */
inline double nearness(double d, double range)
{
    double t = std::clamp(1.0 - std::max(0.0, d) / range, 0.0, 1.0);
    return t * t;
}

/** Another window's liquid, as seen by this one. */
struct neighbor_t
{
    rectf_t window;     // its rounded rectangle on screen
    double radius;      // its corner radius on screen
    double thickness;   // its halo thickness on screen
    bool in_front;      // above this window (its liquid wins where they meet)

    double liquid(wf::pointf_t p) const
    {
        return round_box_distance(p, window.grown(thickness), radius + thickness);
    }

    bool operator ==(const neighbor_t& other) const
    {
        return std::abs(window.x1 - other.window.x1) < 0.25 && std::abs(window.y1 - other.window.y1) < 0.25 &&
               std::abs(window.x2 - other.window.x2) < 0.25 && std::abs(window.y2 - other.window.y2) < 0.25 &&
               std::abs(thickness - other.thickness) < 0.1 && in_front == other.in_front;
    }
};

// ---------------------------------------------------------------------------------------------
// GL programs

static const char *frame_vertex_source =
    R"(#version 100
attribute highp vec2 position;
attribute highp vec2 uvPosition;
varying highp vec2 uvpos;
varying highp vec2 pos;
uniform mat4 MVP;

void main() {
    gl_Position = MVP * vec4(position.xy, 0.0, 1.0);
    uvpos = uvPosition;
    pos = position;
})";

// The window's texture, masked to its rounded geometry (which also trims client-drawn shadows).
static const char *window_fragment_source =
    R"(#version 100
@builtin_ext@
@builtin@

varying highp vec2 uvpos;
varying highp vec2 pos;
uniform highp vec4 color;
uniform highp vec4 rect;
uniform highp float radius;
uniform highp float aa;

void main()
{
    highp vec4 c = get_pixel(uvpos);
    c.rgb = c.rgb * color.a;
    c = c * color;
    highp vec2 hs = rect.zw * 0.5;
    highp vec2 q = abs(pos - (rect.xy + hs)) - hs + vec2(radius);
    highp float d = length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - radius;
    gl_FragColor = c * clamp(0.5 - d / aa, 0.0, 1.0);
})";

// The halo: liquid made of this window's rounded band, joined to the neighbors behind it with a
// smooth minimum (meniscus and bridges), shaded as a rounded surface lit from the upper left.
static const char *halo_fragment_source =
    R"(#version 100
varying highp vec2 pos;
uniform highp vec4 window;       // x1, y1, x2, y2 on screen
uniform highp float radius;      // window corner radius on screen
uniform highp float thickness;   // halo thickness on screen (with the swell)
uniform highp float ripple;      // goo ripple amplitude (px)
uniform highp float phase;       // animation clock
uniform highp float aa;          // px per fragment
uniform highp float merge;       // smooth-min width
uniform highp vec3 tone;         // base color
uniform highp float density;     // base opacity
uniform highp vec4 cloud;        // cloudiness per corner: tl, tr, bl, br
uniform highp float corner_extra;
uniform highp float count;
uniform highp vec4 nb_window[8];
uniform highp vec4 nb_param[8];  // radius, thickness, in front (0/1)

highp float round_box(highp vec2 p, highp vec4 r, highp float rad)
{
    highp vec2 hs = (r.zw - r.xy) * 0.5;
    rad = min(rad, min(hs.x, hs.y));
    highp vec2 q = abs(p - (r.xy + hs)) - hs + vec2(rad);
    return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - rad;
}

highp float smin(highp float a, highp float b, highp float k)
{
    highp float h = max(k - abs(a - b), 0.0) / k;
    return min(a, b) - h * h * k * 0.25;
}

highp float hash(highp vec2 p)
{
    return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
}

highp float noise(highp vec2 p)
{
    highp vec2 i = floor(p);
    highp vec2 f = fract(p);
    highp vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x),
               mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x), u.y);
}

highp float own_liquid(highp vec2 p)
{
    // Lazy, irregular undulation: two octaves of slowly drifting noise, long wavelengths.
    highp float wave = (noise(p * 0.013 + vec2(phase * 0.45, -phase * 0.3)) - 0.5) * 1.6
                     + (noise(p * 0.029 - vec2(phase * 0.2, phase * 0.35)) - 0.5) * 0.8;
    return round_box(p, window + vec4(-thickness, -thickness, thickness, thickness), radius + thickness)
        - ripple * wave;
}

// The liquid this window draws: its own band joined to the neighbors behind it.
highp float liquid(highp vec2 p)
{
    highp float d = own_liquid(p);
    for (int i = 0; i < 8; i++) {
        if (float(i) >= count) break;
        if (nb_param[i].z < 0.5) {
            highp float t = nb_param[i].y;
            highp float n = round_box(p, nb_window[i] + vec4(-t, -t, t, t), nb_param[i].x + t);
            d = smin(d, n, merge);
        }
    }
    return d;
}

highp float cover(highp float d)
{
    return clamp(0.5 - d / aa, 0.0, 1.0);
}

void main()
{
    highp float d = liquid(pos);
    highp float mine = own_liquid(pos);
    highp float a = cover(d);
    a *= 1.0 - cover(round_box(pos, window, radius));       // not under the window itself

    // Pixels of a neighbor's own liquid belong to it (unless they're also mine: I'm in front);
    // pixels of a neighbor in front belong to it outright.
    for (int i = 0; i < 8; i++) {
        if (float(i) >= count) break;
        highp float t = nb_param[i].y;
        highp float n = round_box(pos, nb_window[i] + vec4(-t, -t, t, t), nb_param[i].x + t);
        if (nb_param[i].z > 0.5) {
            a *= 1.0 - cover(n);
        } else {
            a *= 1.0 - cover(n) * (1.0 - cover(mine));
        }
    }

    if (a <= 0.0) {
        discard;
    }

    // Shape the band like a rounded bead: flat on top, sloping at the outer edge.
    highp float e = max(aa, 0.5);
    highp vec2 grad = vec2(liquid(pos + vec2(e, 0.0)) - d, liquid(pos + vec2(0.0, e)) - d) / e;
    highp float depth = clamp(-d / max(thickness * 0.8, 1.0), 0.0, 1.0);
    highp vec3 n = normalize(vec3(grad * (1.0 - depth) * 1.8, 1.0));

    // Which corner's resize area this pixel is in, and how cloudy that corner is.
    highp float reach = radius + thickness + corner_extra;
    highp vec2 wx = vec2(window.x - thickness, window.z + thickness);
    highp vec2 wy = vec2(window.y - thickness, window.w + thickness);
    highp float cl = 0.0;
    cl = max(cl, cloud.x * (1.0 - smoothstep(reach - 6.0, reach, max(pos.x - wx.x, pos.y - wy.x))));
    cl = max(cl, cloud.y * (1.0 - smoothstep(reach - 6.0, reach, max(wx.y - pos.x, pos.y - wy.x))));
    cl = max(cl, cloud.z * (1.0 - smoothstep(reach - 6.0, reach, max(pos.x - wx.x, wy.y - pos.y))));
    cl = max(cl, cloud.w * (1.0 - smoothstep(reach - 6.0, reach, max(wx.y - pos.x, wy.y - pos.y))));

    // Light passing through liquid: soft internal variation, a bright rim, glints.
    highp float flow = noise(pos * 0.045 + vec2(phase * 0.35, -phase * 0.22));
    highp float swirl = noise(pos * 0.11 - vec2(phase * 0.5, phase * 0.3));
    highp vec3 light = normalize(vec3(-0.45, -0.65, 0.9));
    highp float spec = pow(max(dot(reflect(-light, n), vec3(0.0, 0.0, 1.0)), 0.0), 18.0);
    highp float rim = pow(1.0 - n.z, 1.5);

    highp float body = density * (0.8 + 0.4 * flow);
    body = mix(body, 0.82 * (0.75 + 0.35 * swirl), cl);        // the goo clouds up
    highp float glint = spec * (0.35 + 1.4 * cl) + rim * (0.22 + 0.5 * cl);

    highp float alpha = clamp(body + glint * 0.6, 0.0, 1.0) * a;
    highp vec3 rgb = tone * body * a + vec3(1.0) * glint * a;
    gl_FragColor = vec4(min(rgb, vec3(alpha)), alpha);
})";

// The close dot, with an x.
static const char *dot_fragment_source =
    R"(#version 100
varying highp vec2 pos;
uniform highp vec4 fill;
uniform highp vec3 mark_color;
uniform highp vec4 rim_color;
uniform highp vec2 center;
uniform highp float radius;
uniform highp float aa;
uniform highp float opacity;

highp float segment(highp vec2 p, highp vec2 a, highp vec2 b)
{
    highp vec2 pa = p - a;
    highp vec2 ba = b - a;
    highp float h = clamp(dot(pa, ba) / max(dot(ba, ba), 0.0001), 0.0, 1.0);
    return length(pa - ba * h);
}

void main()
{
    highp float d = length(pos - center) - radius;
    highp float k = radius * 0.38;
    highp float mark = min(segment(pos, center - vec2(k, k), center + vec2(k, k)),
                           segment(pos, center + vec2(-k, k), center + vec2(k, -k))) - 0.9;
    highp float body = clamp(0.5 - d / aa, 0.0, 1.0);
    highp float rim = clamp(0.5 - (d - 1.5) / (aa + 1.5), 0.0, 1.0) * rim_color.a;
    highp float x = clamp(0.5 - mark / aa, 0.0, 1.0);
    highp vec3 rgb = mix(fill.rgb, mark_color, x * 0.9);
    highp float a = fill.a * body;
    highp float r = rim * (1.0 - body);
    gl_FragColor = (vec4(rgb * a, a) + vec4(rim_color.rgb * r, r)) * opacity;
})";

struct gl_programs_t
{
    OpenGL::program_t window, halo, dot;
    bool ready = false;

    void ensure()
    {
        if (!ready)
        {
            window.compile(frame_vertex_source, window_fragment_source);
            halo.compile(frame_vertex_source, halo_fragment_source);
            dot.compile(frame_vertex_source, dot_fragment_source);
            ready = true;
        }
    }

    void release()
    {
        if (ready)
        {
            wf::gles::run_in_context_if_gles([&]
            {
                window.free_resources();
                halo.free_resources();
                dot.free_resources();
            });
            ready = false;
        }
    }
};

static gl_programs_t& gl_programs()
{
    static gl_programs_t programs;
    return programs;
}

// ---------------------------------------------------------------------------------------------

class frame_t : public wf::scene::view_2d_transformer_t, public wf::pointer_interaction_t,
    public wf::touch_interaction_t
{
  public:
    /** Called when the halo or a corner is pressed (by the pointer, or by finger touch_id >= 0);
     *  the plugin starts the move or resize. The close dot is handled here. */
    std::function<void(wayfire_toplevel_view, handle_t, int touch_id)> on_press;

    frame_t(wayfire_toplevel_view view) : view_2d_transformer_t(view)
    {
        focus_mix.set(0, 0);
    }

    ~frame_t()
    {
        tick.disconnect();
        dwell.disconnect();
        linger.disconnect();
        dot_hide.disconnect();
    }

    wayfire_toplevel_view toplevel() const
    {
        return wf::toplevel_cast(view.lock());
    }

    /** The window's geometry before scaling. */
    wf::geometry_t window_geometry() const
    {
        auto v = toplevel();
        return v ? v->get_geometry() : wf::geometry_t{0, 0, 0, 0};
    }

    /** The window's rounded rectangle as drawn (scaled around its center). */
    rectf_t screen_rect() const
    {
        auto g  = window_geometry();
        double cx = g.x + g.width / 2.0 + get_translation_x();
        double cy = g.y + g.height / 2.0 + get_translation_y();
        double hw = g.width * get_scale_x() / 2.0, hh = g.height * get_scale_y() / 2.0;
        return {cx - hw, cy - hh, cx + hw, cy + hh};
    }

    double screen_radius() const
    {
        return CORNER_RADIUS * get_scale_x();
    }

    // The elastic bulge of a lifted window rides on top of the layout's scale (scale_x/scale_y,
    // which the plugin sets): everything that draws or hit-tests asks these.
    float get_scale_x() const override
    {
        return scale_x * (1.0 + bulge);
    }

    float get_scale_y() const override
    {
        return scale_y * (1.0 + bulge);
    }

    /** The window was lifted by a long press: it bulges out elastically and its halo swells. */
    void lift()
    {
        lifted = true;
        bulge_target = LIFT_BULGE;
        bulge_velocity += LIFT_KICK;
        dwell.disconnect();
        linger.disconnect();
        set_swell(1.0);
    }

    /** Dropped: the bulge settles back, and the halo sinks after the usual linger. */
    void drop()
    {
        if (!lifted)
        {
            return;
        }

        lifted = false;
        bulge_target = 0.0;
        hovering = false;
        linger.set_timeout(LINGER_MS, [=] ()
        {
            if (!is_pressed() && !hovering && !lifted)
            {
                set_swell(0.0);
            }
        });
        start_ticking();
    }

    bool is_lifted() const
    {
        return lifted;
    }

    /** Halo thickness on screen. At rest it scales with the window; swollen it's a fixed size on
     *  screen (twice the full-size halo), however small the window, so it's always easy to grab. */
    double thickness() const
    {
        double rest = HALO * get_scale_x();
        return std::max(rest * 0.7, rest + (SWOLLEN - rest) * swell);
    }

    /** Distance from p to the edge of this window's liquid (negative inside it). */
    double liquid_distance(wf::pointf_t p) const
    {
        return round_box_distance(p, screen_rect().grown(thickness()), screen_radius() + thickness());
    }

    /** Distance from p to the halo band itself: outside the liquid, or inside the window, or 0
     *  on the band. */
    double band_distance(wf::pointf_t p) const
    {
        return std::max(liquid_distance(p), -round_box_distance(p, screen_rect(), screen_radius()));
    }

    bool is_pressed() const
    {
        return pressed != handle_t::none;
    }

    handle_t hovered_handle() const
    {
        return is_pressed() ? pressed : hovered;
    }

    // --- state from the plugin ---

    void set_focused(bool focused)
    {
        if (focused != is_focused)
        {
            is_focused = focused;
            focus_mix.animate(focused ? 1.0 : 0.0);
            if (focused)
            {
                // A newly focused window's liquid is disturbed: it bulges and settles in waves.
                swell_velocity += FOCUS_NUDGE;
            }

            start_ticking();
        }
    }

    void set_neighbors(std::vector<neighbor_t> list)
    {
        if (list != neighbors)
        {
            neighbors = std::move(list);
            damage();
        }
    }

    const std::vector<neighbor_t>& get_neighbors() const
    {
        return neighbors;
    }

    /** The cursor moved to p. Only the frame nearest the cursor gets this; others get leave(). */
    void track(wf::pointf_t p)
    {
        last_track = p;
        auto r = screen_rect();
        double t = thickness();
        double reach = screen_radius() + t + CORNER_EXTRA;
        std::array<rectf_t, 4> corners = {{
            {r.x1 - t, r.y1 - t, r.x1 - t + reach, r.y1 - t + reach},
            {r.x2 + t - reach, r.y1 - t, r.x2 + t, r.y1 - t + reach},
            {r.x1 - t, r.y2 + t - reach, r.x1 - t + reach, r.y2 + t},
            {r.x2 + t - reach, r.y2 + t - reach, r.x2 + t, r.y2 + t},
        }};
        for (int i = 0; i < 4; i++)
        {
            cloud_target[i] = nearness(box_distance(p, corners[i]), NEAR_RANGE);
        }

        auto dot = dot_center();
        dot_target = nearness(std::hypot(p.x - dot.x, p.y - dot.y) - DOT_RADIUS, NEAR_RANGE);
        if (!is_pressed())
        {
            set_hovered(handle_at(p));
        }

        near_halo(band_distance(p) <= SWELL_VICINITY);
        start_ticking();
    }

    void leave()
    {
        cloud_target = {0, 0, 0, 0};
        dot_target = 0;
        if (!is_pressed())
        {
            set_hovered(handle_t::none);
        }

        near_halo(false);
        start_ticking();
    }

    void release()
    {
        if (is_pressed())
        {
            pressed = handle_t::none;
            if (!hovering)
            {
                near_halo(false, true);
            }

            damage();
        }
    }

    /** What's under p: the close dot, a corner, the halo, or nothing. */
    handle_t handle_at(wf::pointf_t p) const
    {
        auto v = toplevel();
        if (!v || v->pending_fullscreen())
        {
            return handle_t::none;
        }

        auto dot = dot_center();
        if ((dot_glow > 0.2) && (std::hypot(p.x - dot.x, p.y - dot.y) <= DOT_RADIUS + 3))
        {
            return handle_t::close;
        }

        auto r = screen_rect();
        double radius = screen_radius();
        if (round_box_distance(p, r, radius) <= 0)
        {
            return handle_t::none;  // the window itself
        }

        // The grab area: this window's band (at least MIN_GRAB thick) joined to the liquid it
        // shares with the neighbors behind it, minus what belongs to neighbors.
        double grab = std::max(thickness(), MIN_GRAB);
        double own  = round_box_distance(p, r.grown(grab), radius + grab);
        double joined = own;
        for (auto& n : neighbors)
        {
            double d = n.liquid(p);
            if (n.in_front && (d <= 0))
            {
                return handle_t::none;
            }

            if (!n.in_front)
            {
                if ((d <= 0) && (own > 0))
                {
                    return handle_t::none;
                }

                joined = smooth_min(joined, d, MERGE);
            }
        }

        if (joined > 0)
        {
            return handle_t::none;
        }

        double reach = radius + grab + CORNER_EXTRA;
        bool left = p.x < r.x1 - grab + reach, right = p.x > r.x2 + grab - reach;
        bool top  = p.y < r.y1 - grab + reach, bottom = p.y > r.y2 + grab - reach;
        if (top && left)
        {
            return handle_t::top_left;
        }

        if (top && right)
        {
            return handle_t::top_right;
        }

        if (bottom && left)
        {
            return handle_t::bottom_left;
        }

        if (bottom && right)
        {
            return handle_t::bottom_right;
        }

        return handle_t::halo;
    }

    /** The close dot: on the middle of the halo's bottom edge. */
    wf::pointf_t dot_center() const
    {
        auto r = screen_rect();
        return {(r.x1 + r.x2) / 2, r.y2 + thickness() / 2};
    }

    /** How far the drawn halo, its swell and its merging can reach outside the window. */
    double margin() const
    {
        return std::max(SWOLLEN * 1.35, MIN_GRAB) + MERGE + DOT_RADIUS + 4;
    }

    // --- scene node ---

    wf::geometry_t get_bounding_box() override
    {
        auto base = view_2d_transformer_t::get_bounding_box();
        auto r    = screen_rect().grown(margin());
        double x1 = std::min<double>(base.x, std::floor(r.x1));
        double y1 = std::min<double>(base.y, std::floor(r.y1));
        double x2 = std::max<double>(base.x + base.width, std::ceil(r.x2));
        double y2 = std::max<double>(base.y + base.height, std::ceil(r.y2));
        return {x1, y1, x2 - x1, y2 - y1};
    }

    std::optional<wf::scene::input_node_t> find_node_at(const wf::pointf_t& at) override
    {
        if (handle_at(at) != handle_t::none)
        {
            return wf::scene::input_node_t{.node = this, .local_coords = at};
        }

        return view_2d_transformer_t::find_node_at(at);
    }

    wf::pointer_interaction_t& pointer_interaction() override
    {
        return *this;
    }

    wf::touch_interaction_t& touch_interaction() override
    {
        return *this;
    }

    std::string stringify() const override
    {
        return "scottland-frame";
    }

    void gen_render_instances(std::vector<wf::scene::render_instance_uptr>& instances,
        wf::scene::damage_callback push_damage, wf::output_t *shown_on) override;

    // --- pointer interaction (the cursor is on the halo) ---

    // Wayfire hands these the cursor in this node's local coordinates, i.e. with the scale
    // undone (to_local); the halo lives in the scaled coordinates, so map it back.
    void handle_pointer_enter(wf::pointf_t position) override
    {
        last_pointer = to_global(position);
    }

    void handle_pointer_motion(wf::pointf_t position, uint32_t) override
    {
        last_pointer = to_global(position);
    }

    void handle_pointer_button(const wlr_pointer_button_event& event) override
    {
        auto v = toplevel();
        if (!v || (event.button != BTN_LEFT))
        {
            return;
        }

        if (event.state == WL_POINTER_BUTTON_STATE_PRESSED)
        {
            pressed = handle_at(last_pointer);
            if (pressed == handle_t::none)
            {
                return;
            }

            // The swell waits for a pause after the drag ends, not during it: a dragged window is
            // drawn by the move tool, so a swell mid-drag would pop in unanimated at the drop.
            dwell.disconnect();
            damage();
            wf::get_core().default_wm->focus_raise_view(v);
            if ((pressed != handle_t::close) && on_press)
            {
                on_press(v, pressed, -1);
            }
        } else if (pressed == handle_t::close)
        {
            // The close dot acts on release, like a button, if the cursor is still on it.
            bool close = handle_at(last_pointer) == handle_t::close;
            release();
            if (close)
            {
                v->close();
            }
        }

        // Other presses end on the real button release (the plugin calls release()): when a move
        // or resize takes the pointer, this node gets a synthetic release first.
    }

    // --- touch interaction (a finger on the halo): no long press needed here ---

    void handle_touch_down(uint32_t, int finger_id, wf::pointf_t position) override
    {
        auto v = toplevel();
        last_touch = to_global(position);
        if ((touch_finger >= 0) && !wf::get_core().get_touch_state().fingers.count(touch_finger))
        {
            touch_finger = -1;  // its lift went to a grab (a move or resize took the finger)
        }

        if (!v || (touch_finger >= 0))
        {
            return;
        }

        // Fingers can't hover: touching the halo shows the close dot for a while.
        dot_target = 1.0;
        dot_hide.set_timeout(3000, [=] () { dot_target = 0.0; start_ticking(); });
        start_ticking();

        touch_finger = finger_id;
        touch_pressed = handle_at(last_touch);
        if ((touch_pressed == handle_t::none) || (touch_pressed == handle_t::close))
        {
            return;
        }

        wf::get_core().default_wm->focus_raise_view(v);
        // A move or resize grab takes the finger from here on; only the close dot needs its lift.
        touch_finger = -1;
        if (on_press)
        {
            on_press(v, touch_pressed, finger_id);
        }
    }

    void handle_touch_motion(uint32_t, int finger_id, wf::pointf_t position) override
    {
        if (finger_id == touch_finger)
        {
            last_touch = to_global(position);
        }
    }

    void handle_touch_up(uint32_t, int finger_id, wf::pointf_t) override
    {
        if (finger_id != touch_finger)
        {
            return;
        }

        bool close = (touch_pressed == handle_t::close) && (handle_at(last_touch) == handle_t::close);
        touch_finger  = -1;
        touch_pressed = handle_t::none;
        if (close)
        {
            if (auto v = toplevel())
            {
                v->close();
            }
        }
    }

    /** Repaint the window and its halo. (view->damage() covers only the window.) */
    void damage()
    {
        if (parent())
        {
            wf::scene::damage_node(parent(), get_bounding_box());
        } else if (auto v = view.lock())
        {
            v->damage();
        }
    }

    /** Repaint where the halo was drawn for the window at `old` (after it moved). */
    void damage_previous(const wf::geometry_t& old)
    {
        if (!parent())
        {
            return;
        }

        double cx = old.x + old.width / 2.0, cy = old.y + old.height / 2.0;
        double hw = old.width * get_scale_x() / 2.0 + margin(), hh = old.height * get_scale_y() / 2.0 + margin();
        wf::scene::damage_node(parent(), wf::geometry_t{std::floor(cx - hw), std::floor(cy - hh),
            std::ceil(2 * hw) + 1, std::ceil(2 * hh) + 1});
    }

    // Animation state, read by the render instance and layout-state.
    double phase = 0.0;
    double swell = 0.0;          // 0 at rest, 1 swollen (overshoots while moving)
    double swell_velocity = 0.0;
    std::array<double, 4> cloud{};
    double dot_glow = 0.0;
    double bulge = 0.0;          // extra scale of a lifted window (springs, overshoots)
    double bulge_velocity = 0.0;
    wf::animation::simple_animation_t focus_mix{wf::create_option<int>(150)};

  private:
    bool is_focused = false;
    std::vector<neighbor_t> neighbors;
    handle_t hovered = handle_t::none;
    handle_t pressed = handle_t::none;
    wf::pointf_t last_pointer{0, 0};
    wf::pointf_t last_track{-1e6, -1e6};
    std::array<double, 4> cloud_target{};
    double dot_target   = 0.0;
    double swell_target = 0.0;
    bool hovering = false;
    bool lifted = false;
    double bulge_target = 0.0;
    int touch_finger = -1;
    handle_t touch_pressed = handle_t::none;
    wf::pointf_t last_touch{0, 0};
    wf::wl_timer<false> dot_hide;
    uint32_t last_tick = 0;
    wf::wl_timer<true> tick;
    wf::wl_timer<false> dwell;
    wf::wl_timer<false> linger;

    /** The cursor moved, near the halo or not. Near it, a pause of DWELL_MS swells the halo (any
     *  motion restarts the wait); away from it, the halo sinks back after LINGER_MS. */
    void near_halo(bool near, bool force = false)
    {
        bool was = hovering;
        hovering = near;
        if (near)
        {
            linger.disconnect();
            if ((swell_target < 1.0) && !is_pressed())
            {
                dwell.set_timeout(DWELL_MS, [=] () { set_swell(1.0); });
            }
        } else if (was || force)
        {
            dwell.disconnect();
            if (swell_target > 0.0)
            {
                linger.set_timeout(LINGER_MS, [=] ()
                {
                    if (!is_pressed() && !hovering && !lifted)
                    {
                        set_swell(0.0);
                    }
                });
            }
        }
    }

    void set_swell(double target)
    {
        swell_target = target;
        start_ticking();
    }

    void set_hovered(handle_t h)
    {
        if (h == hovered)
        {
            return;
        }

        hovered = h;
        damage();
        const char *cursor = nullptr;
        switch (h)
        {
          case handle_t::halo:
            cursor = "grab";
            break;

          case handle_t::top_left: case handle_t::bottom_right:
            cursor = "nwse-resize";
            break;

          case handle_t::top_right: case handle_t::bottom_left:
            cursor = "nesw-resize";
            break;

          case handle_t::close:
            cursor = "pointer";
            break;

          default:
            break;  // off the halo: the surface under the cursor sets its own
        }

        if (cursor)
        {
            wf::get_core().set_cursor(cursor);
        }
    }

    bool settled()
    {
        // A cloudy corner keeps moving (its light shifts), so it keeps ticking.
        for (int i = 0; i < 4; i++)
        {
            if ((std::abs(cloud[i] - cloud_target[i]) > 0.002) || (cloud[i] > 0.002))
            {
                return false;
            }
        }

        return (std::abs(dot_glow - dot_target) < 0.002) && !focus_mix.running() &&
               (std::abs(bulge - bulge_target) < 0.0005) && (std::abs(bulge_velocity) < 0.001) &&
               (std::abs(swell - swell_target) < 0.001) && (std::abs(swell_velocity) < 0.001);
    }

    static uint32_t now_ms()
    {
        timespec ts;
        clock_gettime(CLOCK_MONOTONIC, &ts);
        return ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
    }

    void start_ticking()
    {
        if (tick.is_connected())
        {
            return;
        }

        last_tick = now_ms();
        tick.set_timeout(16, [=] ()
        {
            uint32_t now = now_ms();
            double dt = std::clamp((now - last_tick) / 1000.0, 0.001, 0.05);
            last_tick = now;
            damage();  // where it was: a shrinking bulge must not leave its old outline behind
            step(dt);
            damage();
            if (settled())
            {
                swell = swell_target;
                swell_velocity = 0;
                dot_glow = dot_target;
                bulge = bulge_target;
                bulge_velocity = 0;
                return false;
            }

            return true;
        });
    }

    void step(double dt)
    {
        phase += dt;
        // Goo: an underdamped spring, so the swell overshoots and wobbles before settling.
        const double stiffness = 30.0, damping = 4.6;
        double accel = stiffness * (swell_target - swell) - damping * swell_velocity;
        swell_velocity += accel * dt;
        swell += swell_velocity * dt;
        // The lift bulge: a snappier spring, so it pops out, overshoots and pulls back.
        const double b_stiffness = 160.0, b_damping = 11.0;
        double b_accel = b_stiffness * (bulge_target - bulge) - b_damping * bulge_velocity;
        bulge_velocity += b_accel * dt;
        bulge += bulge_velocity * dt;
        // Clouds and the dot ease toward their targets.
        double ease = 1.0 - std::exp(-dt * 12.0);
        for (int i = 0; i < 4; i++)
        {
            cloud[i] += (cloud_target[i] - cloud[i]) * ease;
            if (cloud[i] < 0.002 && cloud_target[i] == 0)
            {
                cloud[i] = 0;
            }
        }

        dot_glow += (dot_target - dot_glow) * ease;
    }
};

class frame_render_instance_t : public wf::scene::transformer_render_instance_t<frame_t>
{
  public:
    using transformer_render_instance_t::transformer_render_instance_t;

    void transform_damage_region(wf::regionf_t& damage) override
    {
        auto copy = damage;
        damage.clear();
        for (auto& box : copy)
        {
            damage |= wf::get_bbox_for_node(self, ::geometry_from_pixman_box(box));
        }
    }

    void render(const wf::scene::render_instruction_t& data) override
    {
        if (!wf::get_core().is_gles2())
        {
            // Other renderers: plain scaled texture, no rounding or halo.
            auto tex = this->get_texture(data.target.scale);
            tex->set_filter_mode(WLR_SCALE_FILTER_BILINEAR);
            data.pass->add_texture(tex, data.target, self->view_2d_transformer_t::get_bounding_box(),
                data.damage, self->get_alpha());
            return;
        }

        auto bbox     = self->get_children_bounding_box();
        auto geometry = self->window_geometry();
        wf::pointf_t mid{geometry.x + geometry.width / 2.0, geometry.y + geometry.height / 2.0};
        auto flat = glm::translate(glm::mat4(1.0), glm::vec3{self->get_translation_x() + mid.x,
            self->get_translation_y() + mid.y, 0.0}) *
            glm::scale(glm::mat4(1.0), glm::vec3{self->get_scale_x(), self->get_scale_y(), 1.0}) *
            glm::translate(glm::mat4(1.0), glm::vec3{-mid.x, -mid.y, 0.0});
        float pixel     = 1.0f / std::max(0.01f, data.target.scale);
        float window_aa = pixel / std::max(0.01f, self->get_scale_x());
        float alpha     = self->get_alpha();
        auto v = self->toplevel();
        bool halo = v && !v->pending_fullscreen();

        data.pass->custom_gles_subpass([&]
        {
            auto& programs = gl_programs();
            programs.ensure();
            auto tex = wf::gles_texture_t{this->get_texture(data.target.scale)};
            wf::gles::bind_render_buffer(data.target);
            auto ortho = wf::gles::render_target_orthographic_projection(data.target);

            wf::gles::for_each_scissor_rect(data.target, data.damage, [&]
            {
                if (halo)
                {
                    draw_halo(programs.halo, ortho, pixel, alpha);
                }

                draw_window(programs.window, tex, bbox, ortho * flat, geometry, window_aa, alpha);
                if (halo && (self->dot_glow > 0.003))
                {
                    draw_dot(programs.dot, ortho, pixel, self->dot_glow * alpha,
                        self->hovered_handle() == handle_t::close);
                }
            });
        });
    }

  private:
    static void quad(OpenGL::program_t& program, const rectf_t& b)
    {
        GLfloat vertices[] = {
            (float)b.x1, (float)b.y2, (float)b.x2, (float)b.y2, (float)b.x2, (float)b.y1, (float)b.x1,
            (float)b.y1,
        };
        program.attrib_pointer("position", 2, 0, vertices);
        glDrawArrays(GL_TRIANGLE_FAN, 0, 4);
    }

    void draw_halo(OpenGL::program_t& program, const glm::mat4& mvp, float aa, float alpha)
    {
        auto r = self->screen_rect();
        double t = self->thickness();
        double radius = self->screen_radius();
        float focus   = self->focus_mix;
        glm::vec3 neutral = palette.light ? glm::vec3{0.08, 0.08, 0.1} : glm::vec3{0.9, 0.92, 0.95};
        glm::vec3 tone    = glm::mix(neutral, palette.accent, focus);
        float density     = (0.16f + (0.44f - 0.16f) * focus) * alpha;

        program.use(wf::TEXTURE_TYPE_RGBA);
        program.uniformMatrix4f("MVP", mvp);
        program.uniform4f("window", glm::vec4{r.x1, r.y1, r.x2, r.y2});
        program.uniform1f("radius", radius);
        program.uniform1f("thickness", t);
        // Ripples along the edge while the goo moves, in proportion to how fast it's moving.
        double travel = SWOLLEN - HALO * self->get_scale_x();
        program.uniform1f("ripple", std::min(4.0, std::abs(self->swell_velocity) * travel * 0.3));
        program.uniform1f("phase", self->phase);
        program.uniform1f("aa", aa);
        program.uniform1f("merge", MERGE);
        program.uniform3f("tone", tone.r, tone.g, tone.b);
        program.uniform1f("density", density);
        program.uniform4f("cloud", glm::vec4{self->cloud[0], self->cloud[1], self->cloud[2], self->cloud[3]});
        program.uniform1f("corner_extra", CORNER_EXTRA);

        std::array<glm::vec4, MAX_NEIGHBORS> nb_window{}, nb_param{};
        auto& list = self->get_neighbors();
        int count  = std::min<int>(list.size(), MAX_NEIGHBORS);
        for (int i = 0; i < count; i++)
        {
            nb_window[i] = {list[i].window.x1, list[i].window.y1, list[i].window.x2, list[i].window.y2};
            nb_param[i]  = {list[i].radius, list[i].thickness, list[i].in_front ? 1.0 : 0.0, 0.0};
        }

        program.uniform1f("count", count);
        GLuint id = program.get_program_id(wf::TEXTURE_TYPE_RGBA);
        glUniform4fv(glGetUniformLocation(id, "nb_window"), MAX_NEIGHBORS, glm::value_ptr(nb_window[0]));
        glUniform4fv(glGetUniformLocation(id, "nb_param"), MAX_NEIGHBORS, glm::value_ptr(nb_param[0]));

        glEnable(GL_BLEND);
        glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);
        // Four strips around the window (not one big quad under it). Each reaches inside the
        // window by its corner radius so the corners are covered.
        double m = self->margin(), in = radius + 1;
        quad(program, {r.x1 - m, r.y1 - m, r.x2 + m, r.y1 + in});                 // top
        quad(program, {r.x1 - m, r.y2 - in, r.x2 + m, r.y2 + m});                 // bottom
        quad(program, {r.x1 - m, r.y1 + in, r.x1 + in, r.y2 - in});               // left
        quad(program, {r.x2 - in, r.y1 + in, r.x2 + m, r.y2 - in});               // right
        program.deactivate();
    }

    static void draw_window(OpenGL::program_t& program, const wf::gles_texture_t& tex,
        const wf::geometry_t& bbox, const glm::mat4& mvp, const wf::geometry_t& geometry, float aa,
        float alpha)
    {
        program.use(tex.type);
        float x1 = bbox.x, y1 = bbox.y, x2 = bbox.x + bbox.width, y2 = bbox.y + bbox.height;
        GLfloat vertices[] = {x1, y2, x2, y2, x2, y1, x1, y1};
        GLfloat uvs[] = {0, 0, 1, 0, 1, 1, 0, 1};
        program.set_active_texture(tex);
        glTexParameteri(tex.target, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
        glTexParameteri(tex.target, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
        program.attrib_pointer("position", 2, 0, vertices);
        program.attrib_pointer("uvPosition", 2, 0, uvs);
        program.uniformMatrix4f("MVP", mvp);
        program.uniform4f("color", glm::vec4{1.0, 1.0, 1.0, alpha});
        program.uniform4f("rect", glm::vec4{geometry.x, geometry.y, geometry.width, geometry.height});
        program.uniform1f("radius", std::min<float>(CORNER_RADIUS,
            std::min(geometry.width, geometry.height) / 2.0f));
        program.uniform1f("aa", aa);
        glEnable(GL_BLEND);
        glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);
        glDrawArrays(GL_TRIANGLE_FAN, 0, 4);
        program.deactivate();
    }

    void draw_dot(OpenGL::program_t& program, const glm::mat4& mvp, float aa, double opacity, bool lit)
    {
        auto c = self->dot_center();
        program.use(wf::TEXTURE_TYPE_RGBA);
        program.uniformMatrix4f("MVP", mvp);
        bool light = palette.light;
        glm::vec3 fill = light ? glm::vec3{0.13, 0.13, 0.15} : glm::vec3{1.0, 1.0, 1.0};
        glm::vec4 rim  = light ? glm::vec4{1.0, 1.0, 1.0, 0.45} : glm::vec4{0.0, 0.0, 0.0, 0.28};
        glm::vec3 mark = light ? glm::vec3{0.95, 0.95, 0.95} : glm::vec3{0.12, 0.12, 0.12};
        program.uniform4f("fill", glm::vec4{fill, lit ? 0.96f : 0.8f});
        program.uniform3f("mark_color", mark.r, mark.g, mark.b);
        program.uniform4f("rim_color", rim);
        program.uniform2f("center", c.x, c.y);
        program.uniform1f("radius", DOT_RADIUS);
        program.uniform1f("aa", aa);
        program.uniform1f("opacity", opacity);
        glEnable(GL_BLEND);
        glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);
        double e = DOT_RADIUS + 4;
        quad(program, {c.x - e, c.y - e, c.x + e, c.y + e});
        program.deactivate();
    }
};

inline void frame_t::gen_render_instances(std::vector<wf::scene::render_instance_uptr>& instances,
    wf::scene::damage_callback push_damage, wf::output_t *shown_on)
{
    auto instance = std::make_unique<frame_render_instance_t>(this, push_damage, shown_on);
    if (instance->has_instances())
    {
        instances.push_back(std::move(instance));
    }
}
}
