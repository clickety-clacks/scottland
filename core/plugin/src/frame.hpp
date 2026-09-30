#pragma once

// Window frame: every window is drawn as a rounded rectangle (A2), and handles appear outside it
// when the cursor comes near an edge or corner (A3-A7): pill bars on the edges move the window,
// arcs at the corners resize it around its center, and a close dot sits beside the edge bar.
//
// The frame is the window's scale transformer too (it extends view_2d_transformer_t), so the
// rounding, the scale and the handles are one node in the window's transformer chain: handles
// stack with their window, follow it exactly through scale animations and drags, and a window in
// front covers both the window behind and its handles, for drawing and for input.

#include <wayfire/view-transform.hpp>
#include <wayfire/opengl.hpp>
#include <wayfire/core.hpp>
#include <wayfire/toplevel-view.hpp>
#include <wayfire/region.hpp>
#include <wayfire/scene-input.hpp>
#include <wayfire/scene-render.hpp>
#include <wayfire/util.hpp>
#include <wayfire/window-manager.hpp>
#include <wayfire/util/duration.hpp>
#include <wayfire/config/types.hpp>
#include <glm/gtc/matrix_transform.hpp>
#include <linux/input-event-codes.h>

#include <algorithm>
#include <cmath>
#include <functional>
#include <vector>

namespace scottland
{
// Sizes in logical points. The corner radius is in window space (it scales with the window);
// everything else is on screen (constant size, A6).
constexpr double CORNER_RADIUS = 10.0;  // double Omarchy's 5
constexpr double PROXIMITY     = 48.0;  // how near the cursor must be for a handle to show
constexpr double GAP       = 6.0;       // between the window and its handles
constexpr double THICKNESS = 12.0;      // bar and arc stroke
constexpr double BAR_LENGTH = 128.0;    // at most 80% of the edge it sits on
constexpr double DOT_RADIUS = 7.0;
constexpr double DOT_GAP    = 8.0;      // between the bar's end and the close dot
constexpr double HIT_SLOP   = 8.0;      // grab tolerance around a bar or arc
constexpr double OUTLINE    = 1.5;      // soft dark rim so handles show on light content
constexpr double MARGIN     = GAP + 2 * DOT_RADIUS + OUTLINE + 4;  // drawn area outside the window

enum class handle_t
{
    none, top, bottom, left, right, top_left, top_right, bottom_left, bottom_right, close,
};

inline const char *handle_name(handle_t h)
{
    switch (h)
    {
      case handle_t::top:          return "top";
      case handle_t::bottom:       return "bottom";
      case handle_t::left:         return "left";
      case handle_t::right:        return "right";
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
};

/** Signed distance from p to the rectangle's outline (negative inside). */
inline double box_distance(wf::pointf_t p, const rectf_t& r)
{
    double dx = std::max(r.x1 - p.x, p.x - r.x2);
    double dy = std::max(r.y1 - p.y, p.y - r.y2);
    double outside = std::hypot(std::max(dx, 0.0), std::max(dy, 0.0));
    return outside + std::min(std::max(dx, dy), 0.0);
}

inline double segment_distance(wf::pointf_t p, wf::pointf_t a, wf::pointf_t b)
{
    double bx = b.x - a.x, by = b.y - a.y;
    double len2 = bx * bx + by * by;
    double h = len2 > 0 ? std::clamp(((p.x - a.x) * bx + (p.y - a.y) * by) / len2, 0.0, 1.0) : 0.0;
    return std::hypot(p.x - a.x - bx * h, p.y - a.y - by * h);
}

/** One handle shape, in the coordinates the window is drawn in (after its scale). */
struct shape_t
{
    enum kind_t { BAR = 0, ARC = 1, DOT = 2 } kind;
    handle_t id;
    wf::pointf_t a, b;  // bar: end points; arc: circle center, b = outward direction (+-1, +-1); dot: a
    double radius = 0;  // arc: centerline radius; dot: radius

    double distance(wf::pointf_t p) const
    {
        switch (kind)
        {
          case BAR:
            return segment_distance(p, a, b) - THICKNESS / 2;

          case DOT:
            return std::hypot(p.x - a.x, p.y - a.y) - radius;

          case ARC:
          {
            double qx = (p.x - a.x) * b.x, qy = (p.y - a.y) * b.y;
            if ((qx >= 0) && (qy >= 0))
            {
                return std::abs(std::hypot(qx, qy) - radius) - THICKNESS / 2;
            }

            return std::min(std::hypot(qx - radius, qy), std::hypot(qx, qy - radius)) - THICKNESS / 2;
          }
        }

        return 1e9;
    }

    bool hit(wf::pointf_t p) const
    {
        return distance(p) <= (kind == DOT ? 3.0 : HIT_SLOP);
    }

    rectf_t bounds() const
    {
        double pad = THICKNESS / 2 + OUTLINE + 2;
        switch (kind)
        {
          case BAR:
            return {std::min(a.x, b.x) - pad, std::min(a.y, b.y) - pad, std::max(a.x, b.x) + pad,
                std::max(a.y, b.y) + pad};

          case DOT:
            return {a.x - radius - pad, a.y - radius - pad, a.x + radius + pad, a.y + radius + pad};

          case ARC:
          {
            double far = radius + pad;
            double x_out = a.x + b.x * far, y_out = a.y + b.y * far;
            return {std::min(a.x - b.x * pad, x_out), std::min(a.y - b.y * pad, y_out),
                std::max(a.x - b.x * pad, x_out), std::max(a.y - b.y * pad, y_out)};
          }
        }

        return {0, 0, 0, 0};
    }
};

/** The shapes of one handle group: an edge (its bar and the close dot) or a corner (its arc).
 *  `r` is the window's rounded rectangle on screen, `radius` its corner radius on screen. */
inline std::vector<shape_t> shapes_for(handle_t group, const rectf_t& r, double radius)
{
    std::vector<shape_t> shapes;
    double off = GAP + THICKNESS / 2;
    double cx = (r.x1 + r.x2) / 2, cy = (r.y1 + r.y2) / 2;
    auto bar_length = [&] (double edge)
    {
        return std::min(BAR_LENGTH, 0.8 * edge);
    };
    double dot_offset = THICKNESS / 2 + DOT_GAP + DOT_RADIUS;

    auto horizontal = [&] (handle_t id, double y)
    {
        double half = bar_length(r.width()) / 2;
        shapes.push_back({shape_t::BAR, id, {cx - half, y}, {cx + half, y}});
        shapes.push_back({shape_t::DOT, handle_t::close, {cx - half - dot_offset, y}, {}, DOT_RADIUS});
    };
    auto vertical = [&] (handle_t id, double x)
    {
        double half = bar_length(r.height()) / 2;
        shapes.push_back({shape_t::BAR, id, {x, cy - half}, {x, cy + half}});
        shapes.push_back({shape_t::DOT, handle_t::close, {x, cy - half - dot_offset}, {}, DOT_RADIUS});
    };
    auto corner = [&] (handle_t id, double x, double y, double dx, double dy)
    {
        shapes.push_back({shape_t::ARC, id, {x - dx * radius, y - dy * radius}, {dx, dy}, radius + off});
    };

    switch (group)
    {
      case handle_t::top:          horizontal(group, r.y1 - off); break;
      case handle_t::bottom:       horizontal(group, r.y2 + off); break;
      case handle_t::left:         vertical(group, r.x1 - off); break;
      case handle_t::right:        vertical(group, r.x2 + off); break;
      case handle_t::top_left:     corner(group, r.x1, r.y1, -1, -1); break;
      case handle_t::top_right:    corner(group, r.x2, r.y1, 1, -1); break;
      case handle_t::bottom_left:  corner(group, r.x1, r.y2, -1, 1); break;
      case handle_t::bottom_right: corner(group, r.x2, r.y2, 1, 1); break;
      default: break;
    }

    return shapes;
}

/** Which handle group the cursor at p calls up for a window at r (on screen), if any. */
inline handle_t group_near(wf::pointf_t p, const rectf_t& r, double radius)
{
    double d = box_distance(p, r);
    if ((d > PROXIMITY) || (d < -PROXIMITY))
    {
        return handle_t::none;
    }

    double zone_x = std::min(radius + PROXIMITY, r.width() / 2);
    double zone_y = std::min(radius + PROXIMITY, r.height() / 2);
    int h = p.x < r.x1 + zone_x ? -1 : (p.x > r.x2 - zone_x ? 1 : 0);
    int v = p.y < r.y1 + zone_y ? -1 : (p.y > r.y2 - zone_y ? 1 : 0);
    if (h && v)
    {
        return h < 0 ? (v < 0 ? handle_t::top_left : handle_t::bottom_left) :
               (v < 0 ? handle_t::top_right : handle_t::bottom_right);
    }

    if (h)
    {
        return h < 0 ? handle_t::left : handle_t::right;
    }

    if (v)
    {
        return v < 0 ? handle_t::top : handle_t::bottom;
    }

    return handle_t::none;
}

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

// Handle shapes as signed distance fields: bar (capsule), arc (quarter ring), dot (with an x).
static const char *shape_fragment_source =
    R"(#version 100
varying highp vec2 pos;
uniform highp vec4 fill;
uniform highp float kind;
uniform highp vec4 geom;
uniform highp float radius;
uniform highp float thickness;
uniform highp float aa;
uniform highp float opacity;
uniform highp float outline;

highp float segment(highp vec2 p, highp vec2 a, highp vec2 b)
{
    highp vec2 pa = p - a;
    highp vec2 ba = b - a;
    highp float h = clamp(dot(pa, ba) / max(dot(ba, ba), 0.0001), 0.0, 1.0);
    return length(pa - ba * h);
}

void main()
{
    highp float d;
    highp float mark = 1e9;
    if (kind < 0.5) {
        d = segment(pos, geom.xy, geom.zw) - thickness * 0.5;
    } else if (kind < 1.5) {
        highp vec2 q = (pos - geom.xy) * geom.zw;
        if (q.x >= 0.0 && q.y >= 0.0) {
            d = abs(length(q) - radius) - thickness * 0.5;
        } else {
            d = min(length(q - vec2(radius, 0.0)), length(q - vec2(0.0, radius))) - thickness * 0.5;
        }
    } else {
        d = length(pos - geom.xy) - radius;
        highp float k = radius * 0.38;
        mark = min(segment(pos, geom.xy - vec2(k, k), geom.xy + vec2(k, k)),
                   segment(pos, geom.xy + vec2(-k, k), geom.xy + vec2(k, -k))) - 0.9;
    }

    highp float body = clamp(0.5 - d / aa, 0.0, 1.0);
    highp float rim = clamp(0.5 - (d - outline) / (aa + outline), 0.0, 1.0) * 0.28;
    highp float x = clamp(0.5 - mark / aa, 0.0, 1.0);
    highp vec3 rgb = mix(fill.rgb, vec3(0.12), x * 0.85);
    highp float a = fill.a * body;
    highp vec4 shape = vec4(rgb * a, a);
    highp vec4 shadow = vec4(0.0, 0.0, 0.0, rim * (1.0 - body));
    gl_FragColor = (shape + shadow) * opacity;
})";

struct gl_programs_t
{
    OpenGL::program_t window, shape;
    bool ready = false;

    void ensure()
    {
        if (!ready)
        {
            window.compile(frame_vertex_source, window_fragment_source);
            shape.compile(frame_vertex_source, shape_fragment_source);
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
                shape.free_resources();
            });
            ready = false;
        }
    }
};

inline gl_programs_t& gl_programs()
{
    static gl_programs_t programs;
    return programs;
}

// ---------------------------------------------------------------------------------------------

class frame_t : public wf::scene::view_2d_transformer_t, public wf::pointer_interaction_t
{
  public:
    /** Called when a handle is pressed (edge bars and corners); the plugin starts the move or
     *  resize. The close dot is handled here. */
    std::function<void(wayfire_toplevel_view, handle_t)> on_press;

    frame_t(wayfire_toplevel_view view) : view_2d_transformer_t(view)
    {
        fade.set(0, 0);
    }

    ~frame_t()
    {
        fade_tick.disconnect();
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

    handle_t group() const
    {
        return shown;
    }

    bool is_pressed() const
    {
        return pressed != handle_t::none;
    }

    /** How visible the handles are: the fade in/out, times how near the cursor is. */
    double opacity() const
    {
        return fade * (is_pressed() ? 1.0 : nearness);
    }

    /** Show the handles for `g` (or fade out with none). */
    void show(handle_t g)
    {
        if (g == shown)
        {
            return;
        }

        if (g != handle_t::none)
        {
            drawn = g;
        } else
        {
            hovered = handle_t::none;
        }

        shown = g;
        damage();
        fade.animate(g == handle_t::none ? 0.0 : 1.0);
        start_ticking();
    }

    /** The cursor moved to p (window coordinates): pick the handle group it calls up. */
    void track(wf::pointf_t p)
    {
        if (is_pressed())
        {
            return;
        }

        // Stay on the current group while the cursor is on one of its handles.
        if ((shown != handle_t::none) && (handle_at(p) != handle_t::none))
        {
            set_hovered(handle_at(p));
            update_nearness(p);
            return;
        }

        show(group_near(p, screen_rect(), screen_radius()));
        set_hovered(handle_at(p));
        update_nearness(p);
    }

    /** Brightness follows the cursor's distance to the handles on a curve: faint at the edge of
     *  the zone, brightening quickly as the cursor closes in, full on the handle. */
    void update_nearness(wf::pointf_t p)
    {
        // Zero where the zone ends outside the window (PROXIMITY from the window, which is this
        // far from the handle), so the handle doesn't pop in.
        constexpr double range = PROXIMITY - GAP - THICKNESS;
        double d = range;
        for (auto& shape : shapes_for(shown, screen_rect(), screen_radius()))
        {
            d = std::min(d, std::max(0.0, shape.distance(p)));
        }

        double t = 1.0 - d / range;
        double value = t * t;
        if (std::abs(value - nearness) > 0.005)
        {
            nearness = value;
            damage();
        }
    }

    void release()
    {
        if (is_pressed())
        {
            pressed = handle_t::none;
            damage();
        }
    }

    std::vector<shape_t> shapes() const
    {
        return shapes_for(drawn, screen_rect(), screen_radius());
    }

    handle_t handle_at(wf::pointf_t p) const
    {
        if ((shown == handle_t::none) || (fade.end < 0.5))
        {
            return handle_t::none;
        }

        auto list = shapes_for(shown, screen_rect(), screen_radius());
        // The dot sits beside the bar; test it first so its small target wins.
        for (auto it = list.rbegin(); it != list.rend(); ++it)
        {
            if (it->hit(p))
            {
                return it->id;
            }
        }

        return handle_t::none;
    }

    // --- scene node ---

    wf::geometry_t get_bounding_box() override
    {
        auto base = view_2d_transformer_t::get_bounding_box();
        auto r    = screen_rect();
        double x1 = std::min<double>(base.x, std::floor(r.x1 - MARGIN));
        double y1 = std::min<double>(base.y, std::floor(r.y1 - MARGIN));
        double x2 = std::max<double>(base.x + base.width, std::ceil(r.x2 + MARGIN));
        double y2 = std::max<double>(base.y + base.height, std::ceil(r.y2 + MARGIN));
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

    std::string stringify() const override
    {
        return "scottland-frame";
    }

    void gen_render_instances(std::vector<wf::scene::render_instance_uptr>& instances,
        wf::scene::damage_callback push_damage, wf::output_t *shown_on) override;

    // --- pointer interaction (the cursor is on a handle) ---

    void handle_pointer_enter(wf::pointf_t position) override
    {
        last_pointer = position;
        set_hovered(handle_at(position));
    }

    void handle_pointer_motion(wf::pointf_t position, uint32_t) override
    {
        last_pointer = position;
        if (!is_pressed())
        {
            set_hovered(handle_at(position));
        }
    }

    void handle_pointer_leave() override
    {
        if (!is_pressed())
        {
            set_hovered(handle_t::none);
        }
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
            pressed = hovered != handle_t::none ? hovered : handle_at(last_pointer);
            if (pressed == handle_t::none)
            {
                return;
            }

            damage();
            wf::get_core().default_wm->focus_raise_view(v);
            if ((pressed != handle_t::close) && on_press)
            {
                on_press(v, pressed);
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

    handle_t hovered_handle() const
    {
        return is_pressed() ? pressed : hovered;
    }

    /** Repaint the window and its handle margin. (view->damage() covers only the window: the
     *  handles are outside it.) */
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

    /** Repaint where the handles were drawn for the window at `old` (after it moved). */
    void damage_previous(const wf::geometry_t& old)
    {
        if ((drawn == handle_t::none) || !parent())
        {
            return;
        }

        double cx = old.x + old.width / 2.0, cy = old.y + old.height / 2.0;
        double hw = old.width * get_scale_x() / 2.0 + MARGIN, hh = old.height * get_scale_y() / 2.0 + MARGIN;
        wf::scene::damage_node(parent(), wf::geometry_t{std::floor(cx - hw), std::floor(cy - hh),
            std::ceil(2 * hw) + 1, std::ceil(2 * hh) + 1});
    }

  private:
    handle_t shown   = handle_t::none;  // group called up by the cursor
    handle_t drawn   = handle_t::none;  // group being drawn (kept while fading out)
    handle_t hovered = handle_t::none;
    handle_t pressed = handle_t::none;
    double nearness  = 0.0;
    wf::pointf_t last_pointer{0, 0};
    wf::animation::simple_animation_t fade{wf::create_option<int>(140)};
    wf::wl_timer<true> fade_tick;

    void set_hovered(handle_t h)
    {
        if (h == hovered)
        {
            return;
        }

        hovered = h;
        damage();
        const char *cursor = "default";
        switch (h)
        {
          case handle_t::top: case handle_t::bottom: case handle_t::left: case handle_t::right:
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
            return;  // off the handles: the surface under the cursor sets its own
        }

        wf::get_core().set_cursor(cursor);
    }

    void start_ticking()
    {
        if (!fade_tick.is_connected())
        {
            fade_tick.set_timeout(8, [=] ()
            {
                damage();
                if (fade.running())
                {
                    return true;
                }

                if (shown == handle_t::none)
                {
                    drawn = handle_t::none;
                }

                return false;
            });
        }
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
            // Other renderers: plain scaled texture, no rounding or handles.
            auto tex = this->get_texture(data.target.scale);
            tex->set_filter_mode(WLR_SCALE_FILTER_BILINEAR);
            data.pass->add_texture(tex, data.target, view_2d_bbox(), data.damage, self->get_alpha());
            return;
        }

        auto bbox     = self->get_children_bounding_box();
        auto geometry = self->window_geometry();
        wf::pointf_t mid{geometry.x + geometry.width / 2.0, geometry.y + geometry.height / 2.0};
        auto flat = glm::translate(glm::mat4(1.0), glm::vec3{self->get_translation_x() + mid.x,
            self->get_translation_y() + mid.y, 0.0}) *
            glm::scale(glm::mat4(1.0), glm::vec3{self->get_scale_x(), self->get_scale_y(), 1.0}) *
            glm::translate(glm::mat4(1.0), glm::vec3{-mid.x, -mid.y, 0.0});
        float pixel  = 1.0f / std::max(0.01f, data.target.scale);
        float window_aa = pixel / std::max(0.01f, self->get_scale_x());
        double opacity  = self->opacity();
        auto shapes     = opacity > 0.001 ? self->shapes() : std::vector<shape_t>{};
        auto hovered    = self->hovered_handle();
        float alpha     = self->get_alpha();

        data.pass->custom_gles_subpass([&]
        {
            auto& programs = gl_programs();
            programs.ensure();
            auto tex = wf::gles_texture_t{this->get_texture(data.target.scale)};
            wf::gles::bind_render_buffer(data.target);
            auto ortho = wf::gles::render_target_orthographic_projection(data.target);

            wf::gles::for_each_scissor_rect(data.target, data.damage, [&]
            {
                draw_window(programs.window, tex, bbox, ortho * flat, geometry, window_aa, alpha);
                for (auto& shape : shapes)
                {
                    bool lit = shape.id == hovered;
                    draw_shape(programs.shape, shape, ortho, pixel, opacity * alpha, lit);
                }
            });
        });
    }

  private:
    wf::geometry_t view_2d_bbox()
    {
        return self->view_2d_transformer_t::get_bounding_box();
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

    static void draw_shape(OpenGL::program_t& program, const shape_t& shape, const glm::mat4& mvp,
        float aa, double opacity, bool lit)
    {
        program.use(wf::TEXTURE_TYPE_RGBA);
        auto b = shape.bounds();
        GLfloat vertices[] = {
            (float)b.x1, (float)b.y2, (float)b.x2, (float)b.y2, (float)b.x2, (float)b.y1, (float)b.x1,
            (float)b.y1,
        };
        program.attrib_pointer("position", 2, 0, vertices);
        program.uniformMatrix4f("MVP", mvp);
        float base = shape.kind == shape_t::DOT ? 0.78f : 0.62f;
        program.uniform4f("fill", glm::vec4{1.0, 1.0, 1.0, lit ? 0.96f : base});
        program.uniform1f("kind", (float)shape.kind);
        program.uniform4f("geom", glm::vec4{shape.a.x, shape.a.y, shape.b.x, shape.b.y});
        program.uniform1f("radius", shape.radius);
        program.uniform1f("thickness", THICKNESS);
        program.uniform1f("aa", aa);
        program.uniform1f("opacity", opacity);
        program.uniform1f("outline", OUTLINE);
        glEnable(GL_BLEND);
        glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);
        glDrawArrays(GL_TRIANGLE_FAN, 0, 4);
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
