// Spread solver (docs/spread.md "Spread and solo"): fixtures, invariants under fuzz, determinism,
// sliced runs equal to synchronous ones, starved budgets, and timing. Pure code: no compositor.
#include "spread-job.hpp"
#include "spread.hpp"
#include <cassert>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <algorithm>
#include <map>
#include <memory>
#include <random>
#include <set>
#include <string>
#include <vector>

using namespace scottland::spread;

static int failures = 0, checks = 0, hanging = 0;
#define CHECK(cond, ...) do { ++checks; if (!(cond)) { ++failures; std::printf("FAIL %s:%d: %s ", __FILE__, __LINE__, #cond); \
    std::printf(__VA_ARGS__); std::printf("\n"); } } while (0)

// The plugin's place() with no custom curve (scottland.cpp), for a screen width.
struct zones_t
{
    double W = 2560, center_pct = 33.333, rail_pct = 2, min_scale = 0.2, max_scale = 1, blend = 40;
    double scale(double x) const
    {
        double center_half = W * center_pct / 200, rail = W * rail_pct / 100;
        double from_middle = std::abs(x - W / 2), to_rail = W / 2 - rail;
        if (from_middle <= center_half) return 1;
        if (from_middle >= to_rail) return min_scale;
        double span = std::max(1.0, to_rail - center_half);
        double b = std::clamp(blend, 0.0, span * 0.5);
        auto curve = [&] (double t) { return max_scale - t * (max_scale - min_scale); };
        double into = from_middle - center_half;
        if (into < b)
        {
            double u = into / b, p1 = curve(0), m1 = -(max_scale - min_scale) * b / (span - b);
            double u2 = u * u, u3 = u2 * u;
            double s = (2 * u3 - 3 * u2 + 1) + (-2 * u3 + 3 * u2) * p1 + (u3 - u2) * m1;
            return std::clamp(s, 0.05, 1.0);
        }
        return curve((into - b) / std::max(1.0, span - b));
    }
};

static snapshot_t screen(double W, double H, zones_t z = {}, double bar = 0)
{
    z.W = W;
    snapshot_t s;
    s.screen_width = W; s.screen_height = H;
    s.workarea = {0, bar, W, H};
    s.padding = 16;
    s.center_half = W * z.center_pct / 200;
    s.rail_width = W * z.rail_pct / 100;
    s.scale = [z] (double x) { return z.scale(x); };
    // WP4: the first center whose scale reads as scaled (zone_spot's search).
    double inner = W / 2 - s.center_half - 1, outer = s.rail_width + 1;
    double outer_scale = z.scale(outer);
    double threshold = 1 - std::min(0.05, std::max(0.0, (1 - outer_scale) / 2));
    for (int step = 1; step <= 128; ++step)
    {
        double trial = inner + (outer - inner) * step / 128;
        if (z.scale(trial) > threshold) continue;
        s.arrival_inset = (W / 2 - trial) - s.center_half;
        break;
    }
    return s;
}

static void set_solo(snapshot_t& s, double w, double h, double cx, double cy)
{
    s.solo = {cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2};
}

static window_t window(uint64_t id, role_t role, double w, double h, double cx, double cy, double scale = 1,
    bool pinned = false, uint32_t recency = 0)
{
    window_t win;
    win.id = id; win.role = role; win.width = w; win.height = h;
    win.cx = representable(cx, w); win.cy = representable(cy, h);
    win.scale = scale; win.pinned = pinned; win.recency = recency;
    return win;
}

static void add_resident(snapshot_t& s, uint64_t id, double w, double h, double cx, double cy)
{
    double x = representable(cx, w);
    s.windows.push_back(window(id, role_t::resident, w, h, x, cy, std::clamp(s.scale(x), 0.05, 1.0)));
}

struct final_t { double cx, cy, s; bool moved; };

static std::map<uint64_t, final_t> final_layout(const snapshot_t& s, const result_t& r)
{
    std::map<uint64_t, final_t> out;
    for (const auto& w : s.windows) out[w.id] = {w.cx, w.cy, w.scale, false};
    for (const auto& m : r.moves) out[m.id] = {m.cx, m.cy, m.scale, true};
    return out;
}

static box rect_of(const window_t& w, const final_t& f)
{
    return {f.cx - w.width * f.s / 2, f.cy - w.height * f.s / 2, f.cx + w.width * f.s / 2, f.cy + w.height * f.s / 2};
}

static bool conflicts(const box& a, const box& b, double gap)
{
    return std::min(a.x1, b.x1) - std::max(a.x0, b.x0) + gap > 1e-7 &&
        std::min(a.y1, b.y1) - std::max(a.y0, b.y0) + gap > 1e-7;
}

// The invariants of final.md section 6 that can be read from a snapshot and its result.
static void check_invariants(const snapshot_t& s, const result_t& r, const char *name)
{
    double mid = s.screen_width / 2;
    auto layout = final_layout(s, r);
    std::map<uint64_t, const window_t*> by_id;
    for (const auto& w : s.windows) by_id[w.id] = &w;
    bool unchanged = r.status == status_t::unchanged_budget || r.status == status_t::unchanged_exhausted ||
        r.status == status_t::unavailable;
    if (unchanged) { CHECK(r.moves.empty(), "%s: unchanged result has moves", name); return; }
    std::set<uint64_t> moved;
    for (const auto& m : r.moves)
    {
        CHECK(by_id.count(m.id), "%s: move of unknown window %llu", name, (unsigned long long)m.id);
        if (!by_id.count(m.id)) continue;
        const auto& w = *by_id[m.id];
        moved.insert(m.id);
        CHECK(w.role != role_t::fixed, "%s: fixed window %llu moved", name, (unsigned long long)w.id);
        // Representable: integer top-left.
        CHECK(std::abs((m.cx - w.width / 2) - std::round(m.cx - w.width / 2)) < 1e-6 &&
            std::abs((m.cy - w.height / 2) - std::round(m.cy - w.height / 2)) < 1e-6,
            "%s: %llu not representable", name, (unsigned long long)w.id);
        // Periphery only: never a rail, never the center zone (P13), never off screen (WP7).
        double fm = std::abs(m.cx - mid);
        CHECK(fm > s.center_half && fm < mid - s.rail_width, "%s: %llu center %.1f not in the periphery", name,
            (unsigned long long)w.id, m.cx);
        box b = rect_of(w, {m.cx, m.cy, m.scale, true});
        CHECK(b.x0 >= s.workarea.x0 - 1e-6 && b.x1 <= s.workarea.x1 + 1e-6 && b.y0 >= s.workarea.y0 - 1e-6 &&
            b.y1 <= s.workarea.y1 + 1e-6, "%s: %llu off the workarea", name, (unsigned long long)w.id);
        if (w.role == role_t::resident)
        {
            // P1: same side; never larger; never inward of where the user put it.
            CHECK((m.cx < mid) == (w.cx < mid), "%s: resident %llu changed side", name, (unsigned long long)w.id);
            CHECK(m.scale <= w.scale + 1e-9, "%s: resident %llu grew %.4f -> %.4f", name, (unsigned long long)w.id,
                w.scale, m.scale);
            CHECK(std::abs(m.cx - mid) >= std::abs(w.cx - mid) - 1e-6, "%s: resident %llu moved inward", name,
                (unsigned long long)w.id);
            CHECK(!m.pin || (std::abs(m.cx - w.cx) < 1e-6 && w.pinned), "%s: pin kept away from its x", name);
        }
    }
    for (const auto& w : s.windows)
        if (w.role == role_t::arrival)
        {
            CHECK(moved.count(w.id), "%s: arrival %llu stayed in the center", name, (unsigned long long)w.id);
            // Ruling 10-04: hangs into the center zone at most a little (16 pt), unless too wide.
            const auto& f = layout[w.id];
            double inner_edge = f.cx < mid ? f.cx + w.width * f.s / 2 : f.cx - w.width * f.s / 2;
            double hang = f.cx < mid ? inner_edge - (mid - s.center_half) : (mid + s.center_half) - inner_edge;
            if (hang > 16 + 1.5) ++hanging;
        }
    // A resident moves only if it was under the solo target or a pushed arrangement won.
    bool pushed_won = r.checkpoint.find("pushed") != std::string::npos;
    for (const auto& w : s.windows)
    {
        if (w.role != role_t::resident || !moved.count(w.id)) continue;
        bool under = real_overlap(rect_of(w, {w.cx, w.cy, w.scale, false}), s.solo);
        CHECK(under || pushed_won, "%s: resident %llu moved in %s without a push", name,
            (unsigned long long)w.id, r.checkpoint.c_str());
        // Every optionally pushed resident is clear of everything in the final layout.
        if (!under)
        {
            box me = rect_of(w, layout[w.id]);
            bool hit = real_overlap(me, s.solo);
            for (const auto& f : s.fixed) hit |= real_overlap(me, f);
            for (const auto& o : s.windows) if (o.id != w.id) hit |= real_overlap(me, rect_of(o, layout[o.id]));
            CHECK(!hit, "%s: pushed resident %llu overlaps in the final layout", name, (unsigned long long)w.id);
        }
        // Return pass: its exact original spot is not clear (contact) in the final layout.
        if (r.checkpoint != "seed" && r.checkpoint != "baseline")
        {
            box home = rect_of(w, {w.cx, w.cy, w.scale, false});
            bool blocked = conflicts(home, s.solo, s.contact);
            for (const auto& f : s.fixed) blocked |= conflicts(home, f, s.contact);
            for (const auto& o : s.windows) if (o.id != w.id) blocked |= conflicts(home, rect_of(o, layout[o.id]), s.contact);
            CHECK(blocked, "%s: moved resident %llu's original spot is clear", name, (unsigned long long)w.id);
        }
    }
    // Status: clear means no affected window overlaps anything.
    if (r.status == status_t::clear) CHECK(r.overlaps.empty(), "%s: clear with overlaps", name);
}

static bool same_moves(const result_t& a, const result_t& b)
{
    if (a.status != b.status || a.moves.size() != b.moves.size() || a.checkpoint != b.checkpoint) return false;
    for (size_t i = 0; i < a.moves.size(); ++i)
    {
        const auto& x = a.moves[i], &y = b.moves[i];
        if (x.id != y.id || x.cx != y.cx || x.cy != y.cy || x.scale != y.scale || x.pin != y.pin) return false;
    }
    return true;
}

// ---------------------------------------------------------------- random scenes
static snapshot_t random_scene(std::mt19937& rng, int arrivals, int residents, bool odd_curve = false)
{
    std::uniform_int_distribution<int> pickscreen(0, 2);
    double sizes[3][2] = {{1920, 1080}, {2560, 1440}, {3840, 2160}};
    int k = pickscreen(rng);
    double W = sizes[k][0], H = sizes[k][1];
    std::uniform_real_distribution<double> u(0, 1);
    zones_t z;
    snapshot_t s = screen(W, H, z, u(rng) < 0.5 ? 32 : 0);
    if (odd_curve)
    {
        // A user curve that rises again toward the rail (non-monotone): residents must never grow.
        double base_half = s.center_half, rail = s.rail_width;
        s.scale = [W, base_half, rail] (double x) {
            double fm = std::abs(x - W / 2);
            if (fm <= base_half) return 1.0;
            double t = (fm - base_half) / std::max(1.0, W / 2 - rail - base_half);
            return std::clamp(0.9 - 0.9 * t + 0.6 * t * t, 0.2, 1.0);
        };
    }
    double sw = std::round(W * (0.25 + 0.2 * u(rng))), sh = std::round(H * (0.4 + 0.4 * u(rng)));
    set_solo(s, sw, sh, W / 2 + (u(rng) - 0.5) * W * 0.05, s.workarea.cy() + (u(rng) - 0.5) * H * 0.1);
    uint64_t id = 1;
    for (int i = 0; i < arrivals; ++i)
    {
        double w = std::round(W * (0.15 + 0.3 * u(rng))), h = std::round(H * (0.2 + 0.5 * u(rng)));
        double cx = W / 2 + (u(rng) - 0.5) * 2 * s.center_half * 0.95;
        double cy = s.workarea.y0 + h / 2 + u(rng) * std::max(1.0, s.workarea.height() - h);
        s.windows.push_back(window(id++, role_t::arrival, w, h, cx, cy, 1, u(rng) < 0.1, uint32_t(u(rng) * 50)));
    }
    for (int i = 0; i < residents; ++i)
    {
        double w = std::round(W * (0.15 + 0.3 * u(rng))), h = std::round(H * (0.2 + 0.5 * u(rng)));
        bool left = u(rng) < 0.5;
        double lo = s.center_half + 3, hi = W / 2 - s.rail_width - 3;
        double fm = lo + u(rng) * (hi - lo);
        double cx = representable(left ? W / 2 - fm : W / 2 + fm, w);
        double sc = std::clamp(s.scale(cx), 0.05, 1.0);
        bool pinned = u(rng) < 0.15;
        if (pinned) sc = std::clamp(sc * (0.6 + 0.6 * u(rng)), 0.1, 1.0);
        double cy = s.workarea.y0 + u(rng) * s.workarea.height();
        s.windows.push_back(window(id++, role_t::resident, w, h, cx, cy, sc, pinned, uint32_t(u(rng) * 50)));
    }
    // Docked widgets on the rails, and now and then one protruding into the periphery.
    int widgets = int(u(rng) * 5);
    for (int i = 0; i < widgets; ++i)
    {
        bool left = u(rng) < 0.5;
        double ww = u(rng) < 0.2 ? 420 : 300, wh = 90;
        double y = s.workarea.y0 + 20 + u(rng) * (s.workarea.height() - 130);
        double x0 = left ? 18 : W - 18 - ww;
        s.fixed.push_back({x0, y, x0 + ww, y + wh});
    }
    return s;
}

// ---------------------------------------------------------------- fixtures
static void fixtures()
{
    // An already clear set is unchanged: nothing in the center but the solo, no resident under it.
    {
        auto s = screen(2560, 1440);
        set_solo(s, 1000, 800, 1280, 720);
        add_resident(s, 1, 800, 600, 400, 400);
        add_resident(s, 2, 800, 600, 2160, 1000);
        auto r = solve(s);
        CHECK(r.status == status_t::clear && r.moves.empty(), "clear set moved: %s, %zu moves", status_name(r.status), r.moves.size());
        check_invariants(s, r, "clear set");
    }
    // Simple solo: three center windows leave, each on its nearer side, clear, in band; the
    // residents nothing lands on are bit-for-bit unmoved.
    {
        auto s = screen(2560, 1440);
        set_solo(s, 1000, 800, 1280, 720);
        s.windows.push_back(window(1, role_t::arrival, 800, 600, 1100, 400));
        s.windows.push_back(window(2, role_t::arrival, 700, 500, 1500, 900));
        s.windows.push_back(window(3, role_t::arrival, 600, 400, 1250, 1100));
        add_resident(s, 4, 700, 500, 300, 300);
        auto r = solve(s);
        check_invariants(s, r, "simple solo");
        CHECK(r.status == status_t::clear, "simple solo: %s", status_name(r.status));
        CHECK(r.score.below_band == 0, "simple solo: %d below band", r.score.below_band);
        for (const auto& m : r.moves) CHECK(m.id != 4, "simple solo: resident 4 moved");
        for (const auto& m : r.moves)
            if (m.id == 1) CHECK(m.cx < 1280, "arrival 1 went right");
            else if (m.id == 2) CHECK(m.cx > 1280, "arrival 2 went left");
        std::printf("simple solo: %s via %s, work %llu, spacing %.2f\n", status_name(r.status), r.checkpoint.c_str(),
            (unsigned long long)r.work, r.spacing);
    }
    // Empty periphery domain: a fact, reported as unavailable.
    {
        zones_t z; z.center_pct = 96;
        auto s = screen(1920, 1080, z);
        set_solo(s, 600, 400, 960, 540);
        s.windows.push_back(window(1, role_t::arrival, 500, 300, 900, 300));
        auto r = solve(s);
        CHECK(r.status == status_t::unavailable && r.moves.empty(), "no periphery: %s", status_name(r.status));
    }
    // An arrival too large for any legal periphery spot: nothing moves (no legal seed).
    {
        zones_t z; z.min_scale = 0.9;
        auto s = screen(1920, 1080, z);
        set_solo(s, 600, 400, 960, 540);
        s.windows.push_back(window(1, role_t::arrival, 1900, 1070, 960, 540));
        auto r = solve(s);
        CHECK(r.status == status_t::unchanged_exhausted && r.moves.empty(), "oversized: %s", status_name(r.status));
    }
    // A resident under the solo target has to move; it goes on its own side, same scale
    // (vertical) when a gap fits (decision 4), else outward; never larger.
    {
        auto s = screen(2560, 1440);
        set_solo(s, 1300, 600, 1280, 500);
        // Resident hanging under the solo's left edge.
        add_resident(s, 1, 700, 400, 700, 500);
        auto r = solve(s);
        check_invariants(s, r, "under solo");
        bool moved = false;
        for (const auto& m : r.moves) if (m.id == 1) { moved = true; CHECK(m.cx < 1280, "went right"); }
        CHECK(moved, "resident under the solo stayed");
        CHECK(r.status == status_t::clear, "under solo: %s", status_name(r.status));
    }
    // Same-scale vertical before outward (Astra's draft-3 witness): a wide solo target covers a
    // small resident; walls leave a 40 px opening in its column where it fits at contact. It
    // takes that opening at its own x and scale rather than shrinking outward.
    {
        auto s = screen(2560, 1440);
        double x = representable(600, 100);
        double sc = std::clamp(s.scale(x), 0.05, 1.0);
        add_resident(s, 1, 100, std::round(20 / sc), x, 200);
        const window_t res = s.windows.back();
        s.fixed.push_back({300, 0, 900, 300});
        s.fixed.push_back({300, 340, 900, 1440});
        s.solo = {500, 150, 2060, 260};
        auto r = solve(s);
        check_invariants(s, r, "same-scale first");
        bool moved = false;
        for (const auto& m : r.moves)
            if (m.id == 1)
            {
                moved = true;
                CHECK(std::abs(m.cx - res.cx) < 1e-6, "same-scale first: moved outward to x=%.1f (from %.1f)", m.cx, res.cx);
                CHECK(m.cy - res.height * m.scale / 2 >= 300 && m.cy + res.height * m.scale / 2 <= 340,
                    "same-scale first: not in the opening (y=%.1f)", m.cy);
            }
        CHECK(moved, "same-scale first: the resident under the solo stayed");
    }
    // Contact before decoration (Astra's deadline witness): two arrivals fit above a resident at
    // contact spacing; the resident stays untouched whatever the spacing pass does.
    {
        auto s = screen(2560, 1440);
        set_solo(s, 1200, 1300, 1280, 720);
        // A shelf of periphery: fixed walls leave a column region on the left.
        s.fixed.push_back({0, 0, 860, 40});
        s.windows.push_back(window(1, role_t::arrival, 500, 300, 900, 300));
        s.windows.push_back(window(2, role_t::arrival, 500, 300, 900, 700));
        add_resident(s, 3, 500, 300, 300, 1200);
        auto r = solve(s);
        check_invariants(s, r, "contact first");
        for (const auto& m : r.moves) CHECK(m.id != 3, "contact first: the resident moved");
    }
    // A pinned resident keeps its pin when it moves vertically at its own x.
    {
        auto s = screen(2560, 1440);
        set_solo(s, 1300, 500, 1280, 400);
        double x = representable(700, 800);
        s.windows.push_back(window(1, role_t::resident, 800, 600, x, 400, 0.5, true));
        auto r = solve(s);
        check_invariants(s, r, "pinned");
        for (const auto& m : r.moves)
            if (m.id == 1 && std::abs(m.cx - x) < 1e-6) CHECK(m.pin && std::abs(*m.pin - 0.5) < 1e-9, "pin lost at same x");
    }
    // Fable's W1/W1b (decision 4): a resident 4 pt under the solo target moves vertically at its
    // own x and scale (keeping a pin), never a few points outward, though that travels less.
    for (bool pinned : {false, true})
    {
        auto s = screen(2560, 1440);
        double x = representable(700, 600);
        double sc = pinned ? 0.9 : std::clamp(s.scale(x), 0.05, 1.0);
        s.windows.push_back(window(1, role_t::resident, 600, 400, x, 500, sc, pinned));
        double right = x + 600 * sc / 2;
        s.solo = {right - 4, 600, right + 660, 1300};
        auto r = solve(s);
        check_invariants(s, r, pinned ? "W1b" : "W1");
        bool ok = false;
        for (const auto& m : r.moves)
            if (m.id == 1) ok = std::abs(m.cx - x) < 1e-6 && std::abs(m.scale - sc) < 1e-9 && (!pinned || m.pin);
        CHECK(ok, "%s: the resident did not move vertically at its own x and scale", pinned ? "W1b" : "W1");
    }
    // Fable's W2: a resident that has no legal spot stays under the solo target; the status
    // says so (it is not "clear").
    {
        zones_t z; z.min_scale = 0.9;
        auto s = screen(2560, 1440, z);
        double x = representable(600, 900);
        s.windows.push_back(window(1, role_t::resident, 900, 1700, x, 720, std::clamp(s.scale(x), 0.05, 1.0)));
        double right = x + 900 * s.windows[0].scale / 2;
        s.solo = {right - 60, 300, right + 900, 1100};
        auto r = solve(s);
        CHECK(r.status != status_t::clear, "W2: an immovable resident under the solo reported %s", status_name(r.status));
        bool reported = false;
        for (auto [a, b] : r.overlaps) reported |= a == 1 && b == 0;
        CHECK(reported, "W2: the overlap with the solo target is not listed");
    }
    // Astra's witnesses (implementation review): a step curve where one pixel outward halves the
    // scale; a pinned full-scale resident must move vertically at its own x, keeping its pin.
    {
        snapshot_t s;
        s.screen_width = 1000; s.screen_height = 600; s.workarea = {0, 0, 1000, 600};
        s.padding = 16; s.center_half = 100; s.rail_width = 40;
        s.scale = [] (double x) { return std::abs(x - 500) <= 100 ? 1.0 : 0.5; };
        s.solo = {398, 250, 750, 350};
        s.windows = {window(1, role_t::resident, 200, 200, 300, 300, 1, true)};
        auto r = solve(s);
        check_invariants(s, r, "Astra same-x");
        bool ok = false;
        for (const auto& m : r.moves) ok = m.id == 1 && m.cx == 300 && m.scale == 1 && m.pin;
        CHECK(ok, "Astra same-x: the pinned resident did not move vertically at full scale");
        // A resident spanning the side vertically cannot leave a larger solo: not "clear".
        s.solo = {100, 0, 850, 600};
        s.windows = {window(1, role_t::resident, 200, 1136, 66, 300, 0.5)};
        r = solve(s);
        CHECK(r.status != status_t::clear && !r.overlaps.empty(), "Astra status: %s with %zu overlaps",
            status_name(r.status), r.overlaps.size());
    }
    // A widget protruding into the periphery is avoided like any fixed thing.
    {
        auto s = screen(2560, 1440);
        set_solo(s, 1000, 800, 1280, 720);
        s.fixed.push_back({20, 300, 700, 700});
        s.windows.push_back(window(1, role_t::arrival, 600, 500, 1000, 500));
        auto r = solve(s);
        check_invariants(s, r, "widget");
        CHECK(r.status == status_t::clear, "widget: %s", status_name(r.status));
    }
}

// ---------------------------------------------------------------- Gather fixtures (GA7-GA12, GA15, GA18)
// Keep these named and small so the runner can execute the Gather contract without the much
// broader solo fuzz, crowding, and timing suite.
static snapshot_t gather_screen()
{
    auto s = screen(1920, 1080);
    s.mode = scottland::spread::mode_t::gather;
    s.screen_width = 1920; s.screen_height = 1080;
    s.workarea = {0, 0, 1920, 1080};
    s.padding = 16; s.center_half = 320; s.rail_width = 40;
    s.scale = [] (double) { return 1.0; };
    s.windows.clear(); s.fixed.clear();
    return s;
}

static void add_gather_resident(snapshot_t& s, uint64_t id, double w, double h, double inner_distance,
    double cy, bool left = true)
{
    double border = left ? s.screen_width / 2 - s.center_half : s.screen_width / 2 + s.center_half;
    double cx = left ? border - inner_distance - w / 2 : border + inner_distance + w / 2;
    s.windows.push_back(window(id, role_t::resident, w, h, cx, cy));
}

static final_t gather_final(const snapshot_t& s, const result_t& r, uint64_t id)
{
    return final_layout(s, r).at(id);
}

static const window_t& gather_input(const snapshot_t& s, uint64_t id)
{
    auto i = std::find_if(s.windows.begin(), s.windows.end(), [=] (const window_t& w) { return w.id == id; });
    if (i == s.windows.end()) std::abort();
    return *i;
}

static double gather_inner_distance(const snapshot_t& s, const window_t& w, const final_t& f)
{
    bool left = w.cx < s.screen_width / 2;
    double border = left ? s.screen_width / 2 - s.center_half : s.screen_width / 2 + s.center_half;
    double inner = left ? f.cx + w.width * f.s / 2 : f.cx - w.width * f.s / 2;
    return std::max(0.0, left ? border - inner : inner - border);
}

static bool same_gather_result(const result_t& a, const result_t& b)
{
    if (a.status != b.status || a.complete != b.complete || a.checkpoint != b.checkpoint || a.reason != b.reason ||
        a.work != b.work || a.arrangements != b.arrangements || a.overlaps != b.overlaps ||
        a.score.overlap != b.score.overlap || a.moves.size() != b.moves.size()) return false;
    for (size_t i = 0; i < a.moves.size(); ++i)
    {
        const auto& x = a.moves[i], &y = b.moves[i];
        if (x.id != y.id || x.cx != y.cx || x.cy != y.cy || x.scale != y.scale || x.pin != y.pin) return false;
    }
    return true;
}

static snapshot_t gather_column()
{
    auto s = gather_screen();
    // All start on the border, so the tie order is midline distance then id.
    add_gather_resident(s, 20, 300, 300, 0, 540);
    add_gather_resident(s, 30, 300, 300, 0, 800);
    add_gather_resident(s, 10, 300, 300, 0, 200);
    return s;
}

static void gather_ga7_ordering()
{
    // The smaller original inner distance wins even when the other window has the lower id and
    // is nearer the midline. Once distances tie, midline distance wins over id; an exact tie uses id.
    {
        auto s = gather_screen();
        add_gather_resident(s, 1, 300, 200, 100, 540);
        add_gather_resident(s, 2, 300, 200, 0, 540);
        auto r = solve(s);
        CHECK(r.complete && r.status == status_t::clear, "GA7 distance order: complete clear gather");
        CHECK(std::abs(gather_final(s, r, 2).cy - 540) < 1, "GA7 distance order: nearer border window placed first");
        CHECK(gather_final(s, r, 1).cy < 540 &&
            std::abs(gather_inner_distance(s, gather_input(s, 1), gather_final(s, r, 1))) < 1,
            "GA7 distance order: farther window fits at the border after it");
    }
    {
        auto s = gather_screen();
        add_gather_resident(s, 1, 300, 200, 0, 400);
        add_gather_resident(s, 2, 300, 200, 0, 540);
        auto r = solve(s);
        CHECK(std::abs(gather_final(s, r, 2).cy - 540) < 1 && gather_final(s, r, 1).cy < 540,
            "GA7 tie order: nearer-midline window precedes lower id");
    }
    {
        auto s = gather_screen();
        add_gather_resident(s, 2, 300, 200, 0, 540);
        add_gather_resident(s, 1, 300, 200, 0, 540);
        auto r = solve(s);
        CHECK(std::abs(gather_final(s, r, 1).cy - 540) < 1 && gather_final(s, r, 2).cy < 540,
            "GA7 tie order: lower id wins the exact tie");
    }
    std::printf("Gather GA7: distance, midline, and id order\n");
}

static void gather_ga8_border_and_overhang()
{
    auto s = gather_screen();
    add_gather_resident(s, 1, 400, 300, 80, 540);
    auto r = solve(s);
    auto f = gather_final(s, r, 1);
    double border = s.screen_width / 2 - s.center_half;
    CHECK(std::abs(f.cx + 400 * f.s / 2 - border) <= 1.1, "GA8: a resident reaches the border when clear");
    CHECK(gather_inner_distance(s, gather_input(s, 1), f) < 1.1, "GA8: inner distance reaches zero");
    CHECK(gather_inner_distance(s, gather_input(s, 1), f) <= 80.1, "GA8: regular placement never moves outward");

    auto overhang = gather_screen();
    overhang.windows.push_back(window(2, role_t::resident, 400, 300, 550, 540));
    auto oh = solve(overhang);
    auto of = gather_final(overhang, oh, 2);
    CHECK(of.cx < 550 && std::abs(of.cx + 400 * of.s / 2 - border) <= 1.1,
        "GA8 overhang: move outward only far enough to put the inner edge on the border");
    std::printf("Gather GA8: innermost border and overhang\n");
}

static void gather_ga9_midline_and_overlap_limit()
{
    auto s = gather_column();
    auto r = solve(s);
    auto center = gather_final(s, r, 20), below = gather_final(s, r, 30), above = gather_final(s, r, 10);
    CHECK(std::abs(center.cy - 540) < 1 && std::abs(below.cy - 810) < 1 && std::abs(above.cy - 270) < 1,
        "GA9: each equal-height window takes its nearest legal position to the midline");
    auto a = rect_of(gather_input(s, 20), center), b = rect_of(gather_input(s, 30), below),
        c = rect_of(gather_input(s, 10), above);
    CHECK(std::abs(normalized_overlap(a, b) - 0.10) < 1e-9 &&
        std::abs(normalized_overlap(a, c) - 0.10) < 1e-9 && normalized_overlap(b, c) == 0,
        "GA9: adjacent column pairs meet the 10%% limit and the outer pair is clear");
    std::printf("Gather GA9: midline tie and 10%% overlap\n");
}

static void gather_ga10_larger_band_exception()
{
    auto s = gather_screen();
    add_gather_resident(s, 1, 300, 900, 0, 540);   // tall first window fills the border column
    add_gather_resident(s, 2, 300, 200, 100, 540); // cannot fit at the later short window's border distance
    add_gather_resident(s, 3, 300, 50, 200, 540);  // short enough to take the border after it
    auto r = solve(s);
    auto tall = gather_final(s, r, 1), middle = gather_final(s, r, 2), short_one = gather_final(s, r, 3);
    double middle_min_overlap = normalized_overlap(box{340, 16, 640, 216}, box{340, 90, 640, 990});
    CHECK(middle_min_overlap > 0.10, "GA10 fixture: the middle window cannot fit at the border beside the tall one");
    CHECK(gather_inner_distance(s, gather_input(s, 3), short_one) <
        gather_inner_distance(s, gather_input(s, 2), middle) - 1,
        "GA10: only the later window that fits can end inward of the earlier one");
    CHECK(gather_inner_distance(s, gather_input(s, 3), short_one) < 1 &&
        gather_inner_distance(s, gather_input(s, 2), middle) <= 100.1,
        "GA10: short window takes the border while the middle window stays within its old range");
    CHECK(gather_inner_distance(s, gather_input(s, 1), tall) < 1,
        "GA10: first window owns the border column");
    std::printf("Gather GA10: larger-band exception\n");
}

static void gather_ga11_best_effort_and_too_wide()
{
    auto crowded = gather_screen();
    crowded.fixed.push_back({40, 0, 640, 1080});
    add_gather_resident(crowded, 1, 100, 100, 190, 540);
    auto r = solve(crowded);
    auto f = gather_final(crowded, r, 1);
    CHECK(r.status == status_t::overlap_exhausted && !r.overlaps.empty(),
        "GA11: no legal spot reports the remaining overlap");
    CHECK(gather_inner_distance(crowded, gather_input(crowded, 1), f) < 1,
        "GA11: best effort ties go to the innermost spot without leaving the side");
    CHECK(f.cy == 540, "GA11: best-effort tie takes the spot nearest the midline");

    auto wide = gather_screen();
    wide.windows.push_back(window(2, role_t::resident, 1200, 300, 400, 540));
    auto too_wide = solve(wide);
    CHECK(too_wide.moves.empty() && too_wide.complete, "GA11: too-wide resident stays exactly put");

    for (bool left : {true, false})
    {
        auto side = gather_screen();
        double border = side.screen_width / 2 + (left ? -side.center_half : side.center_half);
        double band = left ? border - (side.rail_width + EDGE_MARGIN) :
            (side.screen_width - side.rail_width - EDGE_MARGIN) - border;
        double width = band + 12; // wider than the whole band, but no more than twice its width
        double half = width / 2;
        double wide_cx = left ? border - 5 - half : border + 5 + half;
        side.windows.push_back(window(3, role_t::resident, width, 300, wide_cx, 540));
        add_gather_resident(side, 4, 200, 200, 100, 540, left);
        auto placed = solve(side);
        auto wide_final = gather_final(side, placed, 3), small_final = gather_final(side, placed, 4);
        const auto& wide_input = gather_input(side, 3);
        box wide_box = rect_of(wide_input, wide_final), small_box = rect_of(gather_input(side, 4), small_final);
        CHECK(width > band && width <= 2 * band, "GA11 wide fixture: width is in the disputed interval");
        CHECK(wide_final.cx == wide_input.cx && wide_final.cy == wide_input.cy && wide_final.s == wide_input.scale,
            "GA11 wide fixture: too-wide resident stays position- and scale-identical (%s side)", left ? "left" : "right");
        CHECK(normalized_overlap(wide_box, small_box) <= 0.10 + 1e-9,
            "GA11 wide fixture: later resident treats the fixed wide resident as an obstacle (%s side)",
            left ? "left" : "right");
    }
    std::printf("Gather GA11: best effort and too-wide resident\n");
}

static void gather_ga12_calm_column()
{
    auto first_input = gather_column();
    auto first = solve(first_input);
    auto second_input = first_input;
    for (auto& w : second_input.windows)
    {
        auto f = gather_final(first_input, first, w.id);
        w.cx = f.cx; w.cy = f.cy; w.scale = f.s;
    }
    auto second = solve(second_input);
    CHECK(first.complete && second.complete && second.moves.empty(),
        "GA12: a tidy equal-height border column is calm on the second gather");
    std::printf("Gather GA12: repeated tidy column\n");
}

static void gather_ga15_caps()
{
    auto empty = gather_screen();
    auto no_periphery = solve(empty);
    CHECK(no_periphery.status == status_t::unavailable && no_periphery.moves.empty() && no_periphery.complete,
        "GA15: a screen with no periphery remains unchanged");

    auto movable = gather_screen();
    for (size_t i = 0; i < MAX_MOVABLE + 1; ++i)
        movable.windows.push_back(window(i + 1, role_t::resident, 10, 10, 300, 100));
    auto too_many = solve(movable);
    CHECK(too_many.status == status_t::unavailable && too_many.moves.empty() && too_many.complete,
        "GA15: more than 128 movable windows is unavailable without changes");

    auto obstacles = gather_screen();
    obstacles.windows.push_back(window(1, role_t::resident, 100, 100, 300, 540));
    obstacles.fixed.resize(MAX_OBSTACLES, box{0, 0, 1, 1});
    auto too_many_obstacles = solve(obstacles);
    CHECK(too_many_obstacles.status == status_t::unavailable && too_many_obstacles.moves.empty() &&
        too_many_obstacles.complete, "GA15: more than 256 obstacles is unavailable without changes");
    std::printf("Gather GA15: per-screen caps\n");
}

static void gather_ga18_cap_and_determinism()
{
    auto s = gather_column();
    auto synchronous = solve(s);
    auto repeated = solve(s);
    CHECK(synchronous.complete && same_gather_result(synchronous, repeated),
        "GA18: repeated synchronous solves produce the same complete result");

    job_t job(s);
    int steps = 0;
    while (!job.step(std::chrono::nanoseconds(0)) && steps++ < 1000000) {}
    CHECK(job.finished() && same_gather_result(synchronous, job.current()),
        "GA18: zero-allowance slices produce the same complete result");

    CHECK(synchronous.work > 1, "GA18: fixture spends enough work for a one-unit cap boundary");
    if (synchronous.work > 1)
    {
        auto cut = s; cut.work_cap = synchronous.work - 1;
        auto clipped = solve(cut), clipped_again = solve(cut);
        CHECK(clipped.status == status_t::unchanged_budget && !clipped.complete && clipped.moves.empty(),
            "GA18: a cap just short of completion discards the whole gathered arrangement");
        CHECK(same_gather_result(clipped, clipped_again), "GA18: the capped outcome is deterministic");
    }
    std::printf("Gather GA18: completion cap and deterministic slices\n");
}

static void gather_fixtures()
{
    gather_ga7_ordering();
    gather_ga8_border_and_overhang();
    gather_ga9_midline_and_overlap_limit();
    gather_ga10_larger_band_exception();
    gather_ga11_best_effort_and_too_wide();
    gather_ga12_calm_column();
    gather_ga15_caps();
    gather_ga18_cap_and_determinism();
}

// The spacing pass, read through the published checkpoints: the one before "+ spacing" is the
// contact layout it started from.
static void check_spacing(const snapshot_t& s, const std::vector<result_t>& published, const result_t& r, const char *name)
{
    if (r.spacing <= 0) return;
    const result_t *contact = nullptr;
    for (const auto& p : published) if (p.checkpoint == r.checkpoint.substr(0, r.checkpoint.find(" + spacing"))) contact = &p;
    CHECK(contact, "%s: no contact checkpoint before spacing", name);
    if (!contact) return;
    auto a = final_layout(s, *contact), b = final_layout(s, r);
    for (const auto& w : s.windows)
    {
        const auto& p = a[w.id], &q = b[w.id];
        bool changed = std::abs(p.cx - q.cx) > 1e-6 || std::abs(p.cy - q.cy) > 1e-6;
        if (!changed) continue;
        CHECK(p.moved, "%s: spacing touched %llu, which spread did not move", name, (unsigned long long)w.id);
        CHECK(std::hypot(p.cx - q.cx, p.cy - q.cy) <= s.halo + 1e-6, "%s: spacing moved %llu by more than a halo", name,
            (unsigned long long)w.id);
        if (w.role == role_t::resident)
            CHECK(std::abs(p.cx - q.cx) < 1e-6 && std::abs(p.s - q.s) < 1e-9, "%s: spacing moved resident %llu sideways", name,
                (unsigned long long)w.id);
    }
    for (const auto& w : s.windows)
        for (const auto& o : s.windows)
        {
            if (o.id <= w.id) continue;
            auto gap = [&] (const std::map<uint64_t, final_t>& L) {
                box x = rect_of(w, L.at(w.id)), y = rect_of(o, L.at(o.id));
                double dx = std::max(x.x0, y.x0) - std::min(x.x1, y.x1), dy = std::max(x.y0, y.y0) - std::min(x.y1, y.y1);
                if (dx < 0 && dy < 0) return std::max(dx, dy);
                return std::hypot(std::max(0.0, dx), std::max(0.0, dy));
            };
            double before = gap(a), after = gap(b);
            CHECK(after >= std::min(r.spacing, before) - 1e-6, "%s: spacing brought %llu and %llu from %.2f to %.2f", name,
                (unsigned long long)w.id, (unsigned long long)o.id, before, after);
        }
    CHECK(contact->overlaps.size() == r.overlaps.size(), "%s: spacing changed overlap", name);
    CHECK(contact->score.below_band == r.score.below_band, "%s: spacing changed the band", name);
}

static void fuzz(int cases)
{
    std::mt19937 rng(20261004);
    std::map<std::string, int> statuses;
    int pushes = 0, spacings = 0, sliced_checked = 0;
    for (int c = 0; c < cases; ++c)
    {
        std::uniform_int_distribution<int> na(0, 7), nr(0, 14);
        bool odd = c % 7 == 3;
        auto s = random_scene(rng, na(rng), nr(rng), odd);
        char name[64]; std::snprintf(name, sizeof name, "fuzz %d", c);
        std::vector<result_t> published;
        work_t work;
        auto r = solve(s, work, [&] (const result_t& p) { published.push_back(p); });
        check_invariants(s, r, name);
        check_spacing(s, published, r, name);
        ++statuses[status_name(r.status)];
        pushes += r.checkpoint.find("pushed") != std::string::npos;
        spacings += r.spacing > 0;
        // Determinism of a completed solve.
        auto again = solve(s);
        CHECK(same_moves(r, again), "%s: second solve differs", name);
        // A cut at a fixed amount of work is repeatable, and still satisfies the invariants.
        if (c % 3 == 0 && r.work > 10)
        {
            auto cut = s; cut.work_cap = r.work / 3;
            auto a = solve(cut), b = solve(cut);
            CHECK(same_moves(a, b), "%s: cut at %llu not repeatable", name, (unsigned long long)cut.work_cap);
            check_invariants(cut, a, name);
        }
        // Slices: suspending at every opportunity gives exactly the synchronous result.
        if (c % 5 == 0)
        {
            job_t job(s);
            int steps = 0;
            while (!job.step(std::chrono::nanoseconds(0)) && steps < 10000000) ++steps;
            CHECK(job.finished() && same_moves(job.current(), r), "%s: sliced result differs", name);
            ++sliced_checked;
        }
    }
    std::printf("fuzz: %d cases, %d pushed arrangements won, %d spaced, %d sliced runs compared;", cases, pushes,
        spacings, sliced_checked);
    for (auto& [k, v] : statuses) std::printf(" %s=%d", k.c_str(), v);
    std::printf("\n");
}

// Starved budgets deliver a validated checkpoint or leave everything unchanged.
static void starved()
{
    std::mt19937 rng(7);
    for (int c = 0; c < 200; ++c)
    {
        auto s = random_scene(rng, 1 + c % 6, c % 12);
        for (uint64_t cap : {1ull, 10ull, 100ull, 1000ull, 5000ull, 20000ull})
        {
            s.work_cap = cap;
            auto r = solve(s);
            char name[64]; std::snprintf(name, sizeof name, "starved %d/%llu", c, (unsigned long long)cap);
            check_invariants(s, r, name);
            if (r.moves.empty() && r.status != status_t::clear)
                CHECK(r.status == status_t::unchanged_budget || r.status == status_t::unchanged_exhausted ||
                    r.status == status_t::unavailable, "%s: %s with no moves", name, status_name(r.status));
            // A delivered push only comes from a complete, validated pushed arrangement: never
            // from a frozen or partial one (it would be named "pushed").
            for (const auto& m : r.moves)
                if (!m.arrival && r.checkpoint.find("pushed") == std::string::npos)
                {
                    const window_t *w = nullptr;
                    for (const auto& x : s.windows) if (x.id == m.id) w = &x;
                    CHECK(w && real_overlap(rect_of(*w, {w->cx, w->cy, w->scale, false}), s.solo),
                        "%s: optional push delivered by %s", name, r.checkpoint.c_str());
                }
        }
    }
}

// Astra's crowding search: the spacing pass never leaves a pair closer than it was at contact
// or than the clearance it reports (scene 3030 of this stream once went from 11.7 to 3.8 pt).
static void crowding(int scenes)
{
    std::mt19937 rng(900);
    int spaced = 0;
    for (int t = 0; t < scenes; ++t)
    {
        auto s = random_scene(rng, 2 + t % 5, 2 + (t / 5) % 6);
        auto contact = s; contact.halo = 0;
        auto r0 = solve(contact), r = solve(s);
        if (r.spacing <= 0) continue;
        ++spaced;
        auto before = final_layout(s, r0), after = final_layout(s, r);
        for (size_t i = 0; i < s.windows.size(); ++i)
            for (size_t j = i + 1; j < s.windows.size(); ++j)
            {
                const auto& a = s.windows[i], &b = s.windows[j];
                auto gap = [] (box x, box y) {
                    double dx = std::max(x.x0, y.x0) - std::min(x.x1, y.x1), dy = std::max(x.y0, y.y0) - std::min(x.y1, y.y1);
                    return dx < 0 && dy < 0 ? std::max(dx, dy) : std::hypot(std::max(0.0, dx), std::max(0.0, dy));
                };
                double c0 = gap(rect_of(a, before[a.id]), rect_of(b, before[b.id]));
                double c = gap(rect_of(a, after[a.id]), rect_of(b, after[b.id]));
                CHECK(!(c0 >= 0 && c < std::min(r.spacing, c0) - 1e-6), "crowding scene %d: %llu-%llu %.3f -> %.3f (spacing %.2f)",
                    t, (unsigned long long)a.id, (unsigned long long)b.id, c0, c, r.spacing);
            }
    }
    std::printf("crowding search: %d scenes, %d spaced, none crowded\n", scenes, spaced);
}

// A cut during the first arrangement delivers the arrivals placed so far, not the seed pile.
static void anytime()
{
    std::mt19937 rng(31);
    int partial = 0, scenes = 0;
    for (int c = 0; c < 40; ++c)
    {
        auto s = random_scene(rng, 5 + c % 3, 6);
        auto full = solve(s);
        if (full.checkpoint.rfind("seed", 0) == 0) continue;
        ++scenes;
        bool seen = false;
        for (uint64_t cap = 500; cap < full.work && !seen; cap = cap * 5 / 4)
        {
            auto cut = s; cut.work_cap = cap;
            auto r = solve(cut);
            seen = r.checkpoint.find("placed)") != std::string::npos;
            if (seen) check_invariants(cut, r, "anytime");
        }
        partial += seen;
    }
    CHECK(partial * 2 > scenes, "anytime: a cut delivered placed arrivals in only %d of %d scenes", partial, scenes);
    std::printf("anytime: %d of %d scenes delivered partial progress at some cut\n", partial, scenes);
}

// Destroying an unfinished job unwinds it (no leak, no crash) and its result stays usable.
static void cancellation()
{
    std::mt19937 rng(11);
    for (int c = 0; c < 50; ++c)
    {
        auto s = random_scene(rng, 6, 12);
        auto job = std::make_unique<job_t>(s);
        for (int k = 0; k < c % 7; ++k) job->step(std::chrono::nanoseconds(0));
        auto r = job->current();
        char name[32]; std::snprintf(name, sizeof name, "cancelled %d", c);
        check_invariants(s, r, name);
        job.reset();
    }
}

static void timing()
{
    std::printf("timing (this build; final.md asks for these numbers):\n");
    for (int n : {5, 20, 50})
    {
        std::mt19937 rng(100 + n);
        std::vector<double> ms; std::vector<uint64_t> units; int complete = 0;
        double slice_max = 0;
        for (int c = 0; c < 30; ++c)
        {
            int arrivals = std::max(1, n / 4), residents = n - arrivals;
            auto s = random_scene(rng, arrivals, residents);
            s.work_cap = UINT64_MAX / 4;
            auto t0 = std::chrono::steady_clock::now();
            auto r = solve(s);
            double t = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
            ms.push_back(t); units.push_back(r.work); complete += r.complete;
            job_t job(s);
            while (!job.step(std::chrono::microseconds(1000))) {}
            slice_max = std::max(slice_max, std::chrono::duration<double, std::milli>(job.longest).count());
        }
        std::sort(ms.begin(), ms.end()); std::sort(units.begin(), units.end());
        std::printf("  %2d windows: uncapped median %.2f ms, max %.2f ms; work median %llu, max %llu; "
            "longest 1 ms slice %.3f ms; %d/30 complete\n", n, ms[ms.size() / 2], ms.back(),
            (unsigned long long)units[units.size() / 2], (unsigned long long)units.back(), slice_max, complete);
    }
}


// Example layouts for a human to look at: before (dashed) and after, the solo target in grey.
static void write_svg(const snapshot_t& s, const result_t& r, const std::string& path)
{
    FILE *f = std::fopen(path.c_str(), "w");
    if (!f) return;
    double k = 800.0 / s.screen_width;
    std::fprintf(f, "<svg xmlns='http://www.w3.org/2000/svg' width='%.0f' height='%.0f'>\n", s.screen_width * k + 10,
        s.screen_height * k + 40);
    std::fprintf(f, "<rect width='100%%' height='100%%' fill='white'/>\n");
    double mid = s.screen_width / 2;
    auto band = [&] (double x0, double x1, const char *fill) {
        std::fprintf(f, "<rect x='%.1f' y='0' width='%.1f' height='%.1f' fill='%s'/>\n", x0 * k, (x1 - x0) * k,
            s.screen_height * k, fill);
    };
    band(mid - s.center_half, mid + s.center_half, "#eef6ff");
    band(0, s.rail_width, "#f4f4f4"); band(s.screen_width - s.rail_width, s.screen_width, "#f4f4f4");
    auto rect = [&] (const box& b, const char *style) {
        std::fprintf(f, "<rect x='%.1f' y='%.1f' width='%.1f' height='%.1f' %s/>\n", b.x0 * k, b.y0 * k,
            b.width() * k, b.height() * k, style);
    };
    rect(s.solo, "fill='#999' fill-opacity='0.5' stroke='black'");
    for (const auto& b : s.fixed) rect(b, "fill='#c9a' fill-opacity='0.6'");
    auto layout = final_layout(s, r);
    for (const auto& w : s.windows)
    {
        box before = rect_of(w, {w.cx, w.cy, w.scale, false});
        box after = rect_of(w, layout[w.id]);
        const char *color = w.role == role_t::arrival ? "#d33" : (layout[w.id].moved ? "#d80" : "#36c");
        char style[160];
        if (layout[w.id].moved)
        {
            std::snprintf(style, sizeof style, "fill='none' stroke='%s' stroke-dasharray='4 3' stroke-opacity='0.6'", color);
            rect(before, style);
            std::fprintf(f, "<line x1='%.1f' y1='%.1f' x2='%.1f' y2='%.1f' stroke='%s' stroke-opacity='0.5'/>\n",
                w.cx * k, w.cy * k, layout[w.id].cx * k, layout[w.id].cy * k, color);
        }
        std::snprintf(style, sizeof style, "fill='%s' fill-opacity='0.25' stroke='%s'", color, color);
        rect(after, style);
        std::fprintf(f, "<text x='%.1f' y='%.1f' font-size='11' fill='%s'>%llu</text>\n", after.x0 * k + 2,
            after.y0 * k + 12, color, (unsigned long long)w.id);
    }
    std::fprintf(f, "<text x='4' y='%.1f' font-size='12'>%s via %s; spacing %.1f; work %llu</text>\n",
        s.screen_height * k + 20, status_name(r.status), r.checkpoint.c_str(), r.spacing, (unsigned long long)r.work);
    std::fprintf(f, "</svg>\n");
    std::fclose(f);
}

static void examples(const std::string& dir)
{
    std::mt19937 rng(424242);
    for (int c = 0; c < 8; ++c)
    {
        auto s = random_scene(rng, 2 + c % 5, 3 + (c * 3) % 11);
        auto r = solve(s);
        write_svg(s, r, dir + "/example-" + std::to_string(c) + ".svg");
        check_invariants(s, r, ("example " + std::to_string(c)).c_str());
        if (c == 4)
            for (const auto& w : s.windows)
            {
                auto f = final_layout(s, r)[w.id];
                std::printf("  %llu %s %gx%g at (%g,%g) s=%.3f%s -> (%g,%g) s=%.3f\n", (unsigned long long)w.id,
                    w.role == role_t::arrival ? "arrival" : "resident", w.width, w.height, w.cx, w.cy, w.scale,
                    w.pinned ? " pinned" : "", f.cx, f.cy, f.s);
            }
    }
}

int main(int argc, char **argv)
{
    if (argc > 2 && !std::strcmp(argv[1], "--svg")) { examples(argv[2]); std::printf("%d failures\n", failures); return 0; }
    if (argc == 2 && !std::strcmp(argv[1], "--gather-only"))
    {
        gather_fixtures();
        std::printf("%d Gather checks, %d failures\n", checks, failures);
        return failures ? 1 : 0;
    }
    bool bench = argc > 1 && !std::strcmp(argv[1], "--bench");
    fixtures();
    gather_fixtures();
    fuzz(bench ? 200 : 600);
    starved();
    crowding(bench ? 500 : 4000);
    anytime();
    cancellation();
    timing();
    std::printf("arrivals hanging more than 16 pt into the center zone (too wide to avoid it): %d\n", hanging);
    std::printf("%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
