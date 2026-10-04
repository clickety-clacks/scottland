#include "spread.hpp"
#include <array>
#include <cmath>
#include <limits>

namespace scottland::spread
{
const char *status_name(status_t status)
{
    switch (status)
    {
      case status_t::clear: return "clear";
      case status_t::overlap_exhausted: return "overlap: search exhausted";
      case status_t::overlap_budget: return "overlap: budget";
      case status_t::unchanged_exhausted: return "unchanged: search exhausted";
      case status_t::unchanged_budget: return "unchanged: budget";
      case status_t::unavailable: return "unavailable";
    }
    return "";
}

double normalized_overlap(const box& a, const box& b)
{
    double ox = std::min(a.x1, b.x1) - std::max(a.x0, b.x0);
    double oy = std::min(a.y1, b.y1) - std::max(a.y0, b.y0);
    if (ox <= TOLERANCE || oy <= TOLERANCE) return 0;
    double smaller = std::min(a.width() * a.height(), b.width() * b.height());
    return smaller > 0 ? ox * oy / smaller : 0;
}

double representable(double c, double size)
{
    return std::round(c - size / 2) + size / 2;
}

int compare(const score_t& a, const score_t& b)
{
    if (a.legal != b.legal) return a.legal ? -1 : 1;
    auto real = [] (double x, double y, double eps) { return x < y - eps ? -1 : (x > y + eps ? 1 : 0); };
    if (int c = real(a.solo_overlap, b.solo_overlap, 1e-6)) return c;
    if (int c = real(a.overlap, b.overlap, 1e-6)) return c;
    if (a.below_band != b.below_band) return a.below_band < b.below_band ? -1 : 1;
    if (a.residents_moved != b.residents_moved) return a.residents_moved < b.residents_moved ? -1 : 1;
    if (int c = real(a.resident_outward, b.resident_outward, 0.5)) return c;
    if (int c = real(a.resident_travel, b.resident_travel, 0.5)) return c;
    return real(a.arrival_travel, b.arrival_travel, 0.5);
}

namespace
{
constexpr double INF = std::numeric_limits<double>::infinity();
constexpr double EPS = 1e-7;

struct pos_t
{
    double cx = 0, cy = 0, s = 1;
    bool pin = false;  // keeps the snapshot's pinned scale (same x, or an exact restore)
};

bool same_pos(const pos_t& a, const pos_t& b)
{
    return std::abs(a.cx - b.cx) < 1e-6 && std::abs(a.cy - b.cy) < 1e-6 &&
        std::abs(a.s - b.s) < 1e-9 && a.pin == b.pin;
}

// Signed overlap with clearance: > 0 means closer than `gap` on this axis.
inline bool conflicts(const box& a, const box& b, double gap)
{
    return std::min(a.x1, b.x1) - std::max(a.x0, b.x0) + gap > EPS &&
        std::min(a.y1, b.y1) - std::max(a.y0, b.y0) + gap > EPS;
}

// Distance between two rectangles (negative: overlap depth).
double clearance(const box& a, const box& b)
{
    double dx = std::max(a.x0, b.x0) - std::min(a.x1, b.x1);
    double dy = std::max(a.y0, b.y0) - std::min(a.y1, b.y1);
    if (dx < 0 && dy < 0) return std::max(dx, dy);
    return std::hypot(std::max(0.0, dx), std::max(0.0, dy));
}

struct spot_t
{
    bool ok = false;
    double cx = 0, cy = 0, s = 0;
    bool pin = false;
    double outward = 0, travel = 0;
    double overlap = 0;              // least-overlap rung: weighted normalized overlap
    int conflicts = 0;               // push rung: soft obstacles in the way
    double conflict_area = 0;
};

struct soft_t { box r; size_t owner; };

struct request_t
{
    size_t win = 0;
    int side = 0;                    // 0 left, 1 right
    double xlo = 0, xhi = 0;         // center domain for this placement
    double max_scale = INF;          // a resident never grows
    double band = 0;                 // in band: s >= band
    double ox = 0, oy = 0;           // origin: travel is measured from here
    double base = 0;                 // outward = max(0, |x - W/2| - base)
    bool want_push = false, want_overlap = false;
    const std::vector<box> *hard = nullptr;
    const std::vector<soft_t> *soft = nullptr;
    size_t solo_index = SIZE_MAX;    // index in *hard of the solo target (weighted in least overlap)
};

struct picked_t
{
    spot_t inband, any, least;       // rungs 1, 3 and 4
    std::vector<spot_t> push;        // rung 2 candidates, best first (at most 4)
    double max_clear_scale = 0, max_legal_scale = 0;
};

// Outward distances closer than this are equal for "least outward, then least travel": a few
// pixels change the scale invisibly, while travel is what the eye follows (P11: never across the
// screen when a nearby spot works).
constexpr double OUTWARD_TIE = HALO;

bool better_clear(const spot_t& a, const spot_t& b)
{
    if (!b.ok) return a.ok;
    if (!a.ok) return false;
    if (std::abs(a.outward - b.outward) > OUTWARD_TIE) return a.outward < b.outward;
    if (std::abs(a.travel - b.travel) > 1e-9) return a.travel < b.travel;
    return false;  // keep the earlier candidate: deterministic
}

bool better_push(const spot_t& a, const spot_t& b)
{
    if (!b.ok) return a.ok;
    if (!a.ok) return false;
    if (a.conflicts != b.conflicts) return a.conflicts < b.conflicts;
    if (std::abs(a.conflict_area - b.conflict_area) > 1e-6) return a.conflict_area < b.conflict_area;
    return a.travel < b.travel - 1e-9;
}

bool better_least(const spot_t& a, const spot_t& b)
{
    if (!b.ok) return a.ok;
    if (!a.ok) return false;
    if (std::abs(a.overlap - b.overlap) > 1e-9) return a.overlap < b.overlap;
    if (std::abs(a.outward - b.outward) > OUTWARD_TIE) return a.outward < b.outward;
    return a.travel < b.travel - 1e-9;
}

struct layout_t
{
    std::vector<pos_t> pos;          // per window index
    std::vector<uint8_t> placed;     // arrivals placed in this arrangement
    std::vector<uint8_t> pushed;     // residents moved by an optional push (rung 2)
    std::vector<uint8_t> forced;     // residents moved because they were under the solo target
    std::vector<size_t> moved_order; // arrivals and residents in the order they were moved
};

struct checkpoint_t
{
    layout_t layout;
    score_t score;
    std::string name;
    double spacing = 0;
};

class solver_t
{
  public:
    solver_t(const snapshot_t& snapshot, work_t& work,
        const std::function<void(const result_t&)>& publish) :
        S(snapshot), work(work), publish(publish)
    {}

    result_t run()
    {
        result_t result;
        try
        {
            if (!prepare(result)) return finish(result);
            schedule();
            complete = true;
        } catch (const stopped&)
        {
            complete = false;
        }
        return finish(result);
    }

  private:
    const snapshot_t& S;
    work_t& work;
    const std::function<void(const result_t&)>& publish;
    double W = 0, mid = 0;
    std::vector<size_t> arrivals, residents;
    std::vector<box> base_hard;      // solo first, then fixed boxes and fixed windows
    std::vector<double> s_ref;       // per window
    double dom_lo[2]{}, dom_hi[2]{}, arr_lo[2]{}, arr_hi[2]{};
    // Per arrival and side: its innermost allowed center (ruling 10-04: hanging into the center
    // zone at most a little), from which its outward distance is measured.
    std::vector<std::array<double, 2>> inner_x;
    layout_t original, baseline;
    std::optional<checkpoint_t> best;
    unsigned arrangements = 0;
    bool complete = false;
    std::string why;

    // Scratch, reused by every placement (no per-call allocation in the hot path).
    std::vector<std::pair<double, double>> blocked, soft_blocked, free_segments;
    std::vector<std::pair<double, double>> events;
    std::vector<double> xs, starts, ends;
    std::vector<box> hard_scratch;
    std::vector<soft_t> soft_scratch;

    const window_t& win(size_t i) const { return S.windows[i]; }
    int side_of(double x) const { return x < mid ? 0 : 1; }
    double from_middle(double x) const { return std::abs(x - mid); }

    double natural(double x) const
    {
        double s = S.scale ? S.scale(x) : 1.0;
        if (!std::isfinite(s)) s = 1;
        return std::clamp(s, 0.05, 1.0);
    }

    // The scale a window shows at center x: its pin while it keeps its x, else the zone's.
    double scale_at(size_t i, double x) const
    {
        const auto& w = win(i);
        if (w.role == role_t::resident && w.pinned && std::abs(x - w.cx) < 1e-6) return w.scale;
        return natural(x);
    }

    box rect(size_t i, const pos_t& p) const
    {
        const auto& w = win(i);
        double hw = w.width * p.s / 2, hh = w.height * p.s / 2;
        return {p.cx - hw, p.cy - hh, p.cx + hw, p.cy + hh};
    }

    static box rect_of(double w, double h, double cx, double cy, double s)
    {
        return {cx - w * s / 2, cy - h * s / 2, cx + w * s / 2, cy + h * s / 2};
    }


    // WP7: inside the workarea, padded when the footprint fits with the padding.
    bool x_legal(double width, double cx) const
    {
        const auto& a = S.workarea;
        double pad = width + 2 * S.padding <= a.width() ? S.padding : 0;
        return width <= a.width() - 2 * pad + EPS && cx - width / 2 >= a.x0 + pad - EPS &&
            cx + width / 2 <= a.x1 - pad + EPS;
    }

    std::optional<std::pair<double, double>> y_domain(double height) const
    {
        const auto& a = S.workarea;
        double pad = height + 2 * S.padding <= a.height() ? S.padding : 0;
        if (height > a.height() - 2 * pad + EPS) return std::nullopt;
        return std::make_pair(a.y0 + pad + height / 2, a.y1 - pad - height / 2);
    }

    double arrival_lo(size_t i, int side) const { return side == 0 ? arr_lo[0] : inner_x[i][1]; }
    double arrival_hi(size_t i, int side) const { return side == 0 ? inner_x[i][0] : arr_hi[1]; }

    bool center_legal(size_t i, double cx) const
    {
        int side = side_of(cx);
        bool arrival = win(i).role == role_t::arrival;
        double lo = arrival ? arrival_lo(i, side) : dom_lo[side], hi = arrival ? arrival_hi(i, side) : dom_hi[side];
        return cx >= lo - EPS && cx <= hi + EPS;
    }

    // Ruling 10-04 (WP4/WP8): a window sent to the periphery goes as close to the center as it
    // can while hanging into the center zone at most a little (HANG). Where it is too wide for
    // that, its innermost spot is the legal one hanging least (best effort).
    static constexpr double HANG = 16;
    double innermost(size_t i, int side)
    {
        const auto& w = win(i);
        double inner = side == 0 ? arr_hi[0] : arr_lo[1], outer = side == 0 ? arr_lo[0] : arr_hi[1];
        double edge = side == 0 ? mid - S.center_half + HANG : mid + S.center_half - HANG;
        // hang(x) > 0: the footprint reaches further into the center zone than allowed.
        auto hang = [&] (double x) {
            double half = w.width * natural(x) / 2;
            return side == 0 ? x + half - edge : edge - (x - half);
        };
        const int samples = 64;
        double previous = inner, least = INF, least_x = inner;
        for (int k = 0; k <= samples; ++k)
        {
            work.charge();
            double x = inner + (outer - inner) * k / samples;
            double h = hang(x);
            if (h <= 0)
            {
                if (k == 0) return inner;
                double lo = previous, hi = x;  // hang(lo) > 0 >= hang(hi)
                for (int it = 0; it < 20; ++it)
                {
                    work.charge();
                    double m = (lo + hi) / 2;
                    if (hang(m) > 0) lo = m; else hi = m;
                }
                return hi;
            }
            if (h < least && x_legal(w.width * natural(x), x)) { least = h; least_x = x; }
            previous = x;
        }
        return least_x;
    }

    bool legal(size_t i, const pos_t& p) const
    {
        const auto& w = win(i);
        if (!center_legal(i, p.cx)) return false;
        if (!x_legal(w.width * p.s, p.cx)) return false;
        auto yd = y_domain(w.height * p.s);
        return yd && p.cy >= yd->first - EPS && p.cy <= yd->second + EPS;
    }

    bool is_moved(size_t i, const layout_t& L) const { return !same_pos(L.pos[i], original.pos[i]); }

    // ------------------------------------------------------------------ preparation
    bool prepare(result_t& result)
    {
        W = S.screen_width; mid = W / 2;
        auto fail = [&] (status_t status, const char *reason) {
            result.status = status; result.reason = reason; return false;
        };
        if (!(W > 0) || !(S.screen_height > 0) || !S.scale)
            return fail(status_t::unavailable, "no screen");
        size_t movable = 0;
        for (const auto& w : S.windows) movable += w.role != role_t::fixed;
        if (movable > MAX_MOVABLE || S.windows.size() + S.fixed.size() + 1 > MAX_OBSTACLES)
            return fail(status_t::unavailable, "more windows than spread considers");
        double inner = S.center_half + EDGE_MARGIN, outer = W / 2 - S.rail_width - EDGE_MARGIN;
        if (outer <= inner) return fail(status_t::unavailable, "no periphery");
        double arrival_inner = S.center_half + std::max(EDGE_MARGIN, S.arrival_inset);
        if (arrival_inner > outer) arrival_inner = inner;
        dom_lo[0] = mid - outer; dom_hi[0] = mid - inner;
        dom_lo[1] = mid + inner; dom_hi[1] = mid + outer;
        arr_lo[0] = mid - outer; arr_hi[0] = mid - arrival_inner;
        arr_lo[1] = mid + arrival_inner; arr_hi[1] = mid + outer;

        original.pos.resize(S.windows.size());
        base_hard.clear();
        base_hard.push_back(S.solo);
        for (const auto& b : S.fixed) base_hard.push_back(b);
        for (size_t i = 0; i < S.windows.size(); ++i)
        {
            const auto& w = S.windows[i];
            original.pos[i] = {w.cx, w.cy, w.scale, w.pinned};
            if (w.role == role_t::arrival) arrivals.push_back(i);
            else if (w.role == role_t::resident) residents.push_back(i);
            else base_hard.push_back(rect(i, original.pos[i]));
        }
        inner_x.assign(S.windows.size(), {mid, mid});
        for (size_t i : arrivals) inner_x[i] = {innermost(i, 0), innermost(i, 1)};
        original.placed.assign(S.windows.size(), 0);
        original.pushed.assign(S.windows.size(), 0);
        original.forced.assign(S.windows.size(), 0);
        s_ref.assign(S.windows.size(), 0);

        // Reference scale: the largest scale this arrival could have at any legal spot clear of
        // the fixed things only. Computed once; gates pushes and scores results (P6).
        for (size_t i : arrivals)
        {
            double best = 0, legal_best = 0;
            for (int side : {0, 1})
            {
                request_t q = arrival_request(i, side, base_hard);
                auto p = pick(q);
                best = std::max(best, p.max_clear_scale);
                legal_best = std::max(legal_best, p.max_legal_scale);
            }
            s_ref[i] = best > 0 ? best : legal_best;
        }

        // C0: a legal seed for every arrival, residents untouched.
        layout_t seed = original;
        for (size_t i : arrivals)
        {
            auto spot = seed_spot(i);
            if (!spot) return fail(status_t::unchanged_exhausted, "no legal periphery spot for an arrival");
            seed.pos[i] = *spot;
            seed.placed[i] = 1;
            seed.moved_order.push_back(i);
        }
        offer(seed, "seed");

        // C1: residents under the solo target move first (they have to); arrivals are leaving
        // and are not obstacles here.
        baseline = seed;
        auto order = residents;
        std::stable_sort(order.begin(), order.end(), [&] (size_t a, size_t b) { return larger(a, b); });
        for (size_t i : order)
        {
            work.charge();
            if (!real_overlap(rect(i, baseline.pos[i]), S.solo)) continue;
            hard_scratch = base_hard;
            for (size_t j : residents) if (j != i) hard_scratch.push_back(rect(j, baseline.pos[j]));
            auto q = resident_request(i, hard_scratch);
            q.want_overlap = true;
            auto p = pick(q);
            const spot_t& s = p.any.ok ? p.any : p.least;
            if (!s.ok) continue;  // no legal spot on its side: it stays (overlap is reported)
            baseline.pos[i] = {s.cx, s.cy, s.s, s.pin};
            baseline.forced[i] = 1;
            baseline.moved_order.push_back(i);
        }
        offer(baseline, "baseline");
        return true;
    }

    bool larger(size_t a, size_t b) const
    {
        double aa = win(a).width * win(a).height, ab = win(b).width * win(b).height;
        if (aa != ab) return aa > ab;
        return win(a).id < win(b).id;
    }

    bool more_recent(size_t a, size_t b) const
    {
        if (win(a).recency != win(b).recency) return win(a).recency < win(b).recency;
        return win(a).id < win(b).id;
    }

    std::optional<pos_t> seed_spot(size_t i)
    {
        const auto& w = win(i);
        int nearer = w.cx < mid ? 0 : (w.cx > mid ? 1 : (w.side_memory < 0 ? 0 : 1));
        for (int side : {nearer, 1 - nearer})
        {
            double lo = arrival_lo(i, side), hi = arrival_hi(i, side);
            double inner = side == 0 ? hi : lo, outer = side == 0 ? lo : hi;
            for (int k = 0; k <= 16; ++k)
            {
                work.charge();
                auto sx = snap_into(inner + (outer - inner) * k / 16.0, w.width, lo, hi);
                if (!sx) continue;
                double x = *sx, s = natural(x);
                if (!center_legal(i, x) || !x_legal(w.width * s, x)) continue;
                auto yd = y_domain(w.height * s);
                if (!yd) continue;
                auto y = snap_into(std::clamp(w.cy, yd->first, yd->second), w.height, yd->first, yd->second);
                if (!y) continue;
                pos_t p{x, *y, s, false};
                if (legal(i, p)) return p;
            }
        }
        return std::nullopt;
    }

    // Nearest representable center to `c` inside [lo, hi].
    static std::optional<double> snap_into(double c, double size, double lo, double hi)
    {
        double a = std::floor(c - size / 2) + size / 2, b = a + 1;
        bool ia = a >= lo - EPS && a <= hi + EPS, ib = b >= lo - EPS && b <= hi + EPS;
        if (ia && ib) return std::abs(a - c) <= std::abs(b - c) ? a : b;
        if (ia) return a;
        if (ib) return b;
        double c2 = std::ceil(lo - size / 2 - EPS) + size / 2;
        if (c2 <= hi + EPS) return c2;
        return std::nullopt;
    }

    request_t arrival_request(size_t i, int side, const std::vector<box>& hard)
    {
        const auto& w = win(i);
        request_t q;
        q.win = i; q.side = side;
        q.xlo = arrival_lo(i, side); q.xhi = arrival_hi(i, side);
        q.ox = w.cx; q.oy = w.cy; q.base = from_middle(inner_x[i][side]);
        q.band = S.noticeable * s_ref[i] - 1e-9;
        q.hard = &hard; q.solo_index = 0;
        return q;
    }

    // Own side only, outward from where it was (decision 3 and 4), never to a larger scale.
    request_t resident_request(size_t i, const std::vector<box>& hard)
    {
        const auto& w = win(i);
        int side = side_of(w.cx);
        request_t q;
        q.win = i; q.side = side;
        if (side == 0) { q.xlo = dom_lo[0]; q.xhi = std::min(dom_hi[0], w.cx); }
        else { q.xlo = std::max(dom_lo[1], w.cx); q.xhi = dom_hi[1]; }
        q.max_scale = w.scale + 1e-9;
        q.ox = w.cx; q.oy = w.cy; q.base = from_middle(w.cx);
        q.hard = &hard; q.solo_index = 0;
        return q;
    }

    // ------------------------------------------------------------------ the placement primitive
    picked_t pick(const request_t& q)
    {
        picked_t out;
        const auto& w = win(q.win);
        if (q.xhi < q.xlo - EPS) return out;
        xs.clear();
        auto add = [&] (double x) {
            x = representable(x, w.width);
            if (x < q.xlo - EPS || x > q.xhi + EPS)
            {
                // Pull it inside by one pixel when representability pushed it out.
                if (x < q.xlo) x += 1; else x -= 1;
                if (x < q.xlo - EPS || x > q.xhi + EPS) return;
            }
            xs.push_back(x);
        };
        // Its own x first (same scale, vertical: decision 4), the domain ends, legality bounds,
        // contact positions against obstacles (bracketed bisection), then a uniform ladder.
        add(std::clamp(q.ox, q.xlo, q.xhi));
        add(q.side == 0 ? q.xhi : q.xlo);
        add(q.side == 0 ? q.xlo : q.xhi);
        contact_candidates(q);
        const int ladder = 16;
        for (int k = 1; k < ladder; ++k) add(q.xlo + (q.xhi - q.xlo) * k / ladder);
        // Dedupe, keeping first occurrence order (priority), then cap.
        size_t kept = 0;
        for (size_t k = 0; k < xs.size(); ++k)
        {
            work.charge();
            bool dup = false;
            for (size_t j = 0; j < kept && !dup; ++j) dup = std::abs(xs[j] - xs[k]) < 0.25;
            if (!dup) xs[kept++] = xs[k];
            if (kept >= S.probe_cap) break;
        }
        xs.resize(kept);
        auto candidates = xs;  // probe() reuses scratch vectors, not xs
        for (double x : candidates) probe(q, x, out);
        // A few refinement steps toward the origin around the best clear candidate: the ladder
        // may have stepped further out than needed.
        for (spot_t *best : {&out.inband, &out.any})
        {
            if (!best->ok) continue;
            double target = std::clamp(q.ox, q.xlo, q.xhi);
            double from = best->cx;
            for (int step = 0; step < 4; ++step)
            {
                double x = representable((from + target) / 2, w.width);
                if (std::abs(x - best->cx) < 0.5 || std::abs(x - target) < 0.5) break;
                picked_t trial;
                probe(q, x, trial);
                const spot_t& t = best == &out.inband ? trial.inband : trial.any;
                if (t.ok && better_clear(t, *best)) { *best = t; from = x; }
                else target = x;
            }
        }
        return out;
    }

    // x where an edge of the window, at its scale there, touches `target`; brackets from a
    // ladder of sample points (a non-monotone curve can have several), bisection inside each.
    void contact_candidates(const request_t& q)
    {
        const auto& w = win(q.win);
        const int samples = 16;
        double sx[samples + 1], hw[samples + 1];
        for (int k = 0; k <= samples; ++k)
        {
            work.charge();
            sx[k] = q.xlo + (q.xhi - q.xlo) * k / samples;
            hw[k] = w.width * scale_at(q.win, sx[k]) / 2;
        }
        struct bracket_t { int k; double target; double sign; double dist; };
        std::vector<bracket_t> brackets;
        auto edges = [&] (double target) {
            // sign +1: right edge (x + hw) meets target; -1: left edge (x - hw) meets target.
            for (double sign : {1.0, -1.0})
                for (int k = 0; k < samples; ++k)
                {
                    double f0 = sx[k] + sign * hw[k] - target, f1 = sx[k + 1] + sign * hw[k + 1] - target;
                    if ((f0 <= 0) != (f1 <= 0))
                        brackets.push_back({k, target, sign, std::abs((sx[k] + sx[k + 1]) / 2 - q.ox)});
                }
        };
        const auto& a = S.workarea;
        edges(a.x0 + S.padding); edges(a.x1 - S.padding); edges(a.x0); edges(a.x1);
        auto obstacle_edges = [&] (const box& b) {
            work.charge(samples);
            edges(b.x0 - S.contact); edges(b.x1 + S.contact);
        };
        if (q.hard) for (const auto& b : *q.hard) obstacle_edges(b);
        if (q.soft) for (const auto& s : *q.soft) obstacle_edges(s.r);
        work.charge(brackets.size());
        std::stable_sort(brackets.begin(), brackets.end(), [] (auto& x, auto& y) { return x.dist < y.dist; });
        size_t budget = S.probe_cap > 24 ? S.probe_cap - 24 : 8;
        for (size_t n = 0; n < brackets.size() && n < budget / 2; ++n)
        {
            const auto& br = brackets[n];
            double lo = sx[br.k], hi = sx[br.k + 1];
            double flo = lo + br.sign * hw[br.k] - br.target;
            for (int it = 0; it < 18; ++it)
            {
                work.charge();
                double m = (lo + hi) / 2;
                double fm = m + br.sign * w.width * scale_at(q.win, m) / 2 - br.target;
                if ((fm <= 0) == (flo <= 0)) { lo = m; flo = fm; } else hi = m;
            }
            // Both representable neighbours: one of them is on the clear side.
            double c = (lo + hi) / 2;
            double a0 = std::floor(c - w.width / 2) + w.width / 2;
            for (double x : {a0, a0 + 1})
                if (x >= q.xlo - EPS && x <= q.xhi + EPS) xs.push_back(x);
        }
    }

    void probe(const request_t& q, double x, picked_t& out)
    {
        const auto& w = win(q.win);
        work.charge();
        double s = scale_at(q.win, x);
        if (s > q.max_scale) return;
        double ww = w.width * s, hh = w.height * s;
        if (!x_legal(ww, x)) return;
        auto yd = y_domain(hh);
        if (!yd) return;
        out.max_legal_scale = std::max(out.max_legal_scale, s);
        double gap = S.contact;
        double ylo = yd->first, yhi = yd->second;
        double outward = std::max(0.0, from_middle(x) - q.base);
        bool pin = win(q.win).role == role_t::resident && w.pinned && std::abs(x - w.cx) < 1e-6;
        auto make = [&] (double y) {
            spot_t sp; sp.ok = true; sp.cx = x; sp.cy = y; sp.s = s; sp.pin = pin;
            sp.outward = outward; sp.travel = std::hypot(x - q.ox, y - q.oy);
            return sp;
        };
        // Hard obstacles: exact clear intervals in y.
        blocked.clear();
        for (const auto& b : *q.hard)
        {
            work.charge();
            if (std::min(b.x1, x + ww / 2) - std::max(b.x0, x - ww / 2) + gap > EPS)
                blocked.emplace_back(b.y0 - hh / 2 - gap, b.y1 + hh / 2 + gap);
        }
        work.charge(blocked.size());
        std::sort(blocked.begin(), blocked.end());
        free_segments.clear();
        double cur = ylo;
        for (auto [p, r] : blocked)
        {
            if (r <= cur) continue;
            if (p >= yhi) break;
            if (p >= cur) free_segments.emplace_back(cur, p);
            cur = std::max(cur, r);
            if (cur > yhi) break;
        }
        if (cur <= yhi) free_segments.emplace_back(cur, yhi);

        // Soft obstacles (residents a push may move) block a clear spot too.
        soft_blocked.clear();
        if (q.soft)
            for (const auto& sb : *q.soft)
            {
                work.charge();
                if (std::min(sb.r.x1, x + ww / 2) - std::max(sb.r.x0, x - ww / 2) + gap > EPS)
                    soft_blocked.emplace_back(sb.r.y0 - hh / 2 - gap, sb.r.y1 + hh / 2 + gap);
            }
        work.charge(soft_blocked.size());
        std::sort(soft_blocked.begin(), soft_blocked.end());

        // Nearest clear y (of hard and soft) to the origin.
        std::optional<double> clear_y;
        for (auto [a, b] : free_segments)
        {
            // Subtract the soft intervals from this free segment.
            double c = a;
            auto consider = [&] (double lo, double hi) {
                if (hi < lo - EPS) return;
                auto y = snap_into(std::clamp(q.oy, lo, hi), w.height, lo, hi);
                // Keep the snapped center strictly inside the clear interval.
                if (y && (!clear_y || std::abs(*y - q.oy) < std::abs(*clear_y - q.oy))) clear_y = y;
            };
            for (auto [p, r] : soft_blocked)
            {
                work.charge();
                if (r <= c) continue;
                if (p >= b) break;
                if (p >= c) consider(c, p);
                c = std::max(c, r);
                if (c > b) break;
            }
            if (c <= b) consider(c, b);
        }
        if (clear_y)
        {
            out.max_clear_scale = std::max(out.max_clear_scale, s);
            auto sp = make(*clear_y);
            if (better_clear(sp, out.any)) out.any = sp;
            if (s >= q.band && better_clear(sp, out.inband)) out.inband = sp;
        }

        // Rung 2: within the hard-clear segments, the y meeting the fewest soft obstacles.
        if (q.want_push && q.soft && s >= q.band && !free_segments.empty())
        {
            starts.clear(); ends.clear();
            for (auto [p, r] : soft_blocked) { starts.push_back(p); ends.push_back(r); }
            std::sort(ends.begin(), ends.end());
            work.charge(starts.size());
            auto count_at = [&] (double y) {
                // open intervals: p < y < r
                size_t started = std::lower_bound(starts.begin(), starts.end(), y - EPS) - starts.begin();
                size_t ended = std::upper_bound(ends.begin(), ends.end(), y + EPS) - ends.begin();
                return int(started) - int(std::min(started, ended));
            };
            struct cand_t { int count; double dist; double y; };
            cand_t best_c{INT32_MAX, INF, 0};
            auto try_y = [&] (double y, double a, double b) {
                work.charge();
                auto sy = snap_into(y, w.height, a, b);
                if (!sy) return;
                int c = count_at(*sy);
                double d = std::abs(*sy - q.oy);
                if (c < best_c.count || (c == best_c.count && d < best_c.dist)) best_c = {c, d, *sy};
            };
            for (auto [a, b] : free_segments)
            {
                try_y(std::clamp(q.oy, a, b), a, b);
                try_y(a, a, b); try_y(b, a, b);
                for (auto [p, r] : soft_blocked)
                {
                    if (p >= a && p <= b) try_y(p, a, b);
                    if (r >= a && r <= b) try_y(r, a, b);
                }
            }
            if (best_c.count != INT32_MAX)
            {
                auto sp = make(best_c.y);
                box me = rect_of(w.width, w.height, x, best_c.y, s);
                sp.conflicts = 0; sp.conflict_area = 0;
                for (const auto& sb : *q.soft)
                {
                    work.charge();
                    if (conflicts(me, sb.r, gap))
                    {
                        ++sp.conflicts;
                        double ox = std::min(me.x1, sb.r.x1) - std::max(me.x0, sb.r.x0);
                        double oy = std::min(me.y1, sb.r.y1) - std::max(me.y0, sb.r.y0);
                        sp.conflict_area += std::max(0.0, ox) * std::max(0.0, oy);
                    }
                }
                insert_push(out.push, sp);
            }
        }

        // Rung 4: the least normalized overlap with everything (the solo target weighted far
        // above the rest), exact over y by an event sweep of the piecewise-linear overlap.
        if (q.want_overlap)
        {
            events.clear();
            double self_area = ww * hh;
            double f_lo = 0;
            auto add_box = [&] (const box& b, double weight) {
                work.charge();
                double xo = std::min(b.x1, x + ww / 2) - std::max(b.x0, x - ww / 2);
                if (xo <= 0) return;
                double norm = std::max(1.0, std::min(self_area, b.width() * b.height()));
                double k = weight * xo / norm;
                double bh = b.height(), m = std::min(hh, bh);
                double e0 = b.y0 - hh / 2, e1 = e0 + m, e3 = b.y1 + hh / 2, e2 = e3 - m;
                events.emplace_back(e0, k); events.emplace_back(e1, -k);
                events.emplace_back(e2, -k); events.emplace_back(e3, k);
                double len = std::max(0.0, std::min(ylo + hh / 2, b.y1) - std::max(ylo - hh / 2, b.y0));
                f_lo += k * len;
            };
            for (size_t n = 0; n < q.hard->size(); ++n)
                add_box((*q.hard)[n], n == q.solo_index ? 1000.0 : 1.0);
            if (q.soft) for (const auto& sb : *q.soft) add_box(sb.r, 1.0);
            work.charge(events.size());
            std::sort(events.begin(), events.end());
            double slope = 0, at = ylo, f = f_lo;
            double best_f = INF, best_y = ylo;
            auto consider = [&] (double y, double value) {
                if (value < best_f - 1e-9 ||
                    (value < best_f + 1e-9 && std::abs(y - q.oy) < std::abs(best_y - q.oy)))
                { best_f = value; best_y = y; }
            };
            // The value at the origin's clamp lies on a segment; evaluated as the sweep passes.
            double oy = std::clamp(q.oy, ylo, yhi);
            bool oy_done = false;
            consider(ylo, f_lo);
            for (auto [e, d] : events)
            {
                if (e <= ylo) { slope += d; continue; }
                double y = std::min(e, yhi);
                if (!oy_done && oy <= y) { consider(oy, f + slope * (oy - at)); oy_done = true; }
                f += slope * (y - at); at = y;
                consider(y, f);
                if (e >= yhi) break;
                slope += d;
            }
            if (at < yhi)
            {
                if (!oy_done && oy <= yhi) { consider(oy, f + slope * (oy - at)); oy_done = true; }
                f += slope * (yhi - at); at = yhi; consider(yhi, f);
            }
            if (!oy_done) consider(oy, f + slope * (oy - at));
            auto sy = snap_into(best_y, w.height, ylo, yhi);
            if (sy)
            {
                auto sp = make(*sy);
                sp.overlap = best_f;
                if (better_least(sp, out.least)) out.least = sp;
            }
        }
    }

    static void insert_push(std::vector<spot_t>& list, const spot_t& sp)
    {
        auto it = list.begin();
        while (it != list.end() && !better_push(sp, *it)) ++it;
        list.insert(it, sp);
        if (list.size() > 4) list.pop_back();
    }

    // ------------------------------------------------------------------ arrangements
    void schedule()
    {
        auto by_size = arrivals, by_recency = arrivals;
        std::stable_sort(by_size.begin(), by_size.end(), [&] (size_t a, size_t b) { return larger(a, b); });
        std::stable_sort(by_recency.begin(), by_recency.end(), [&] (size_t a, size_t b) { return more_recent(a, b); });
        auto overlap_reason = [] (const std::optional<checkpoint_t>& c) {
            return c && (c->score.overlap > 1e-6 || c->score.solo_overlap > 1e-6);
        };
        auto frozen_large = arrange(false, by_size, "frozen, largest first");
        std::optional<checkpoint_t> frozen_best = frozen_large;
        if (overlap_reason(frozen_large) && by_recency != by_size)
        {
            auto frozen_mru = arrange(false, by_recency, "frozen, most recent first");
            if (frozen_mru && (!frozen_best || compare(frozen_mru->score, frozen_best->score) < 0))
                frozen_best = frozen_mru;
        }
        // Privilege (P6) only with a size or overlap reason to improve the frozen result.
        bool reason = !frozen_best || frozen_best->score.below_band > 0 || overlap_reason(frozen_best);
        bool any_resident = !residents.empty();
        if (reason && any_resident)
        {
            auto pushed_large = arrange(true, by_size, "pushed, largest first");
            if (overlap_reason(pushed_large) && by_recency != by_size)
                arrange(true, by_recency, "pushed, most recent first");
        }
        if (best) spacing_pass();
    }

    void hard_for(size_t i, const layout_t& L, bool privileged, std::vector<box>& hard, std::vector<soft_t>& soft,
        std::optional<std::pair<size_t, pos_t>> extra = std::nullopt)
    {
        hard = base_hard;
        soft.clear();
        for (size_t j : arrivals)
        {
            work.charge();
            if (j != i && L.placed[j]) hard.push_back(rect(j, L.pos[j]));
        }
        for (size_t j : residents)
        {
            work.charge();
            if (j == i) continue;
            box r = rect(j, L.pos[j]);
            if (privileged && !L.pushed[j]) soft.push_back({r, j});
            else hard.push_back(r);
        }
        if (extra) hard.push_back(rect(extra->first, extra->second));
    }

    std::optional<checkpoint_t> arrange(bool privileged, const std::vector<size_t>& order, const char *name)
    {
        layout_t L = baseline;
        // Arrivals are re-placed from the baseline; the seed spots are only the fallback.
        for (size_t i : arrivals) L.placed[i] = 0;
        L.moved_order.clear();
        for (size_t j : residents) if (L.forced[j]) L.moved_order.push_back(j);
        std::vector<box> hard;
        std::vector<soft_t> soft;
        for (size_t i : order)
        {
            // Rungs 1, 3, 4 treat residents as hard.
            hard_for(i, L, false, hard, soft);
            picked_t sides[2];
            for (int side : {0, 1}) sides[side] = pick(arrival_request(i, side, hard));
            auto prefer = [&] (const spot_t& a, const spot_t& b, int sa, int sb) {
                // Equal on the rung's terms: its nearer side, then its side memory.
                if (better_clear(a, b)) return true;
                if (better_clear(b, a)) return false;
                if (!a.ok) return false;
                if (!b.ok) return true;
                int nearer = win(i).cx < mid ? 0 : 1;
                if (sa != sb) return sa == nearer;
                return true;
            };
            auto choose = [&] (const spot_t& l, const spot_t& r) -> const spot_t& {
                return prefer(l, r, 0, 1) ? l : r;
            };
            const spot_t& r1 = choose(sides[0].inband, sides[1].inband);
            if (r1.ok) { place(L, i, r1); continue; }
            if (privileged && try_push(L, i)) continue;
            const spot_t& r3 = choose(sides[0].any, sides[1].any);
            if (r3.ok) { place(L, i, r3); continue; }
            for (int side : {0, 1})
            {
                auto q = arrival_request(i, side, hard);
                q.want_overlap = true;
                sides[side] = pick(q);
            }
            const spot_t& l = sides[0].least, &r = sides[1].least;
            const spot_t& r4 = better_least(l, r) ? l : (better_least(r, l) ? r : (l.ok ? l : r));
            if (r4.ok) { place(L, i, r4); continue; }
            // No legal spot in this search: keep the validated seed spot.
            L.pos[i] = baseline.pos[i];
            L.placed[i] = 1;
            L.moved_order.push_back(i);
        }
        return_pass(L);
        // Final-layout validation: an optionally pushed resident must still be clear.
        for (size_t j : residents)
        {
            if (!L.pushed[j] || !is_moved(j, L)) continue;
            box r = rect(j, L.pos[j]);
            if (overlaps_any(j, r, L)) return std::nullopt;
        }
        ++arrangements;
        return offer(L, name);
    }

    void place(layout_t& L, size_t i, const spot_t& s)
    {
        L.pos[i] = {s.cx, s.cy, s.s, s.pin};
        L.placed[i] = 1;
        L.moved_order.push_back(i);
    }

    // Rung 2: the in-band spot meeting the fewest residents, accepted only if every one of
    // them has a clear place to go by the resident rule (depth one: it pushes nobody).
    bool try_push(layout_t& L, size_t i)
    {
        std::vector<box> hard;
        std::vector<soft_t> soft;
        hard_for(i, L, true, hard, soft);
        std::vector<spot_t> candidates;
        for (int side : {0, 1})
        {
            auto q = arrival_request(i, side, hard);
            q.soft = &soft;
            q.want_push = true;
            auto p = pick(q);
            for (const auto& c : p.push) insert_push(candidates, c);
        }
        for (const auto& c : candidates)
        {
            pos_t at{c.cx, c.cy, c.s, false};
            box me = rect(i, at);
            std::vector<std::pair<size_t, pos_t>> moves;
            bool ok = true;
            for (const auto& sb : soft)
            {
                work.charge();
                if (!conflicts(me, sb.r, S.contact)) continue;
                size_t j = sb.owner;
                // Everything else stays where it is; residents already moved in this trial
                // are at their new spots.
                std::vector<box> rh = base_hard;
                for (size_t k : arrivals) if (L.placed[k]) rh.push_back(rect(k, L.pos[k]));
                rh.push_back(me);
                for (size_t k : residents)
                {
                    work.charge();
                    if (k == j) continue;
                    auto moved = std::find_if(moves.begin(), moves.end(), [&] (auto& m) { return m.first == k; });
                    rh.push_back(rect(k, moved != moves.end() ? moved->second : L.pos[k]));
                }
                auto q = resident_request(j, rh);
                auto p = pick(q);
                if (!p.any.ok) { ok = false; break; }
                moves.emplace_back(j, pos_t{p.any.cx, p.any.cy, p.any.s, p.any.pin});
            }
            if (!ok) continue;
            if (moves.empty() && c.conflicts > 0) continue;
            place(L, i, c);
            for (auto& [j, p] : moves)
            {
                L.pos[j] = p;
                L.pushed[j] = 1;
                L.moved_order.push_back(j);
            }
            return true;
        }
        return false;
    }

    bool overlaps_any(size_t i, const box& r, const layout_t& L, double gap = -1)
    {
        auto hit = [&] (const box& b) {
            work.charge();
            return gap < 0 ? real_overlap(r, b) : conflicts(r, b, gap);
        };
        for (const auto& b : base_hard) if (hit(b)) return true;
        for (size_t j : arrivals) if (j != i && L.placed[j] && hit(rect(j, L.pos[j]))) return true;
        for (size_t j : residents) if (j != i && hit(rect(j, L.pos[j]))) return true;
        return false;
    }

    // Undo resident moves that turned out not to be needed (final.md, schedule step 5).
    void return_pass(layout_t& L)
    {
        auto moved_residents = [&] () {
            std::vector<size_t> out;
            for (auto it = L.moved_order.rbegin(); it != L.moved_order.rend(); ++it)
                if (win(*it).role == role_t::resident && is_moved(*it, L) &&
                    std::find(out.begin(), out.end(), *it) == out.end()) out.push_back(*it);
            return out;
        };
        auto restore_closure = [&] () {
            for (size_t pass = 0; pass <= residents.size(); ++pass)
            {
                bool restored = false;
                for (size_t j : moved_residents())
                {
                    box r = rect(j, original.pos[j]);
                    if (overlaps_any(j, r, L, S.contact)) continue;
                    L.pos[j] = original.pos[j];
                    L.pushed[j] = 0;
                    L.forced[j] = 0;
                    restored = true;
                }
                if (!restored) break;
            }
        };
        restore_closure();
        // Shorten: four samples back toward the original, the nearest clear one.
        for (size_t j : moved_residents())
        {
            const auto& o = original.pos[j];
            pos_t cur = L.pos[j];
            for (int k = 1; k <= 4; ++k)
            {
                double t = k / 5.0;
                double x = representable(o.cx + (cur.cx - o.cx) * t, win(j).width);
                double y = representable(o.cy + (cur.cy - o.cy) * t, win(j).height);
                pos_t p{x, y, scale_at(j, x), win(j).pinned && std::abs(x - o.cx) < 1e-6};
                if (p.s > win(j).scale + 1e-9 || side_of(x) != side_of(o.cx) || !legal(j, p)) continue;
                if (from_middle(x) < from_middle(o.cx) - EPS) continue;  // never inward of its start
                if (overlaps_any(j, rect(j, p), L, S.contact)) continue;
                L.pos[j] = p;
                break;
            }
        }
        restore_closure();
    }

    // ------------------------------------------------------------------ scoring and checkpoints
    score_t score_of(const layout_t& L)
    {
        score_t s;
        std::vector<box> rects(S.windows.size());
        std::vector<uint8_t> affected(S.windows.size(), 0);
        for (size_t i = 0; i < S.windows.size(); ++i)
        {
            work.charge();
            rects[i] = rect(i, L.pos[i]);
            bool moved = is_moved(i, L);
            affected[i] = win(i).role == role_t::arrival || moved;
            if (moved && !legal(i, L.pos[i])) s.legal = false;
            if (win(i).role == role_t::arrival)
            {
                if (L.pos[i].s < S.noticeable * s_ref[i] - 1e-9) ++s.below_band;
                s.arrival_travel += std::hypot(L.pos[i].cx - original.pos[i].cx, L.pos[i].cy - original.pos[i].cy);
            } else if (win(i).role == role_t::resident && moved)
            {
                ++s.residents_moved;
                s.resident_outward += std::max(0.0, from_middle(L.pos[i].cx) - from_middle(original.pos[i].cx));
                s.resident_travel += std::hypot(L.pos[i].cx - original.pos[i].cx, L.pos[i].cy - original.pos[i].cy);
            }
            if (win(i).role != role_t::fixed) s.solo_overlap += normalized_overlap(rects[i], S.solo);
        }
        for (size_t i = 0; i < S.windows.size(); ++i)
        {
            if (!affected[i]) continue;
            for (size_t j = 0; j < S.windows.size(); ++j)
            {
                if (j == i || (affected[j] && j < i)) continue;
                work.charge();
                s.overlap += normalized_overlap(rects[i], rects[j]);
            }
            for (size_t b = 1; b < base_hard.size(); ++b)
            {
                work.charge();
                if (b - 1 < S.fixed.size()) s.overlap += normalized_overlap(rects[i], base_hard[b]);
            }
        }
        return s;
    }

    // The result slot keeps the best checkpoint, not the latest. A validated spacing pass
    // replaces the layout it started from (it is not ranked: it changes only travel).
    std::optional<checkpoint_t> offer(const layout_t& L, const std::string& name, double spacing = 0)
    {
        checkpoint_t c{L, score_of(L), name, spacing};
        if (!c.score.legal) return std::nullopt;
        if (!best || compare(c.score, best->score) < 0 || spacing > 0)
        {
            best = c;
            if (publish)
            {
                result_t r;
                fill(r);
                publish(r);
            }
        }
        return c;
    }

    void fill(result_t& r) const
    {
        r.moves.clear();
        r.overlaps.clear();
        if (!best) return;
        const auto& L = best->layout;
        r.score = best->score;
        r.checkpoint = best->name;
        r.spacing = best->spacing;
        r.arrangements = arrangements;
        r.work = work.used;
        std::vector<box> rects(S.windows.size());
        std::vector<uint8_t> affected(S.windows.size(), 0);
        for (size_t i = 0; i < S.windows.size(); ++i)
        {
            rects[i] = rect(i, L.pos[i]);
            bool moved = !same_pos(L.pos[i], original.pos[i]);
            affected[i] = win(i).role == role_t::arrival || moved;
            if (!moved) continue;
            move_t m;
            m.id = win(i).id;
            m.cx = L.pos[i].cx; m.cy = L.pos[i].cy; m.scale = L.pos[i].s;
            if (L.pos[i].pin) m.pin = L.pos[i].s;
            m.arrival = win(i).role == role_t::arrival;
            r.moves.push_back(m);
        }
        for (size_t i = 0; i < S.windows.size(); ++i)
        {
            if (!affected[i]) continue;
            if (real_overlap(rects[i], S.solo)) r.overlaps.emplace_back(win(i).id, 0);
            for (size_t j = 0; j < S.windows.size(); ++j)
                if (j != i && !(affected[j] && j < i) && real_overlap(rects[i], rects[j]))
                    r.overlaps.emplace_back(win(i).id, win(j).id);
            for (const auto& b : S.fixed)
                if (real_overlap(rects[i], b)) r.overlaps.emplace_back(win(i).id, UINT64_MAX);
        }
    }

    result_t finish(result_t& result)
    {
        result.work = work.used;
        result.complete = complete;
        result.arrangements = arrangements;
        if (!best)
        {
            if (result.reason.empty())
            {
                result.status = complete ? status_t::unchanged_exhausted : status_t::unchanged_budget;
                result.reason = complete ? "no checkpoint" : "stopped before a checkpoint";
            }
            return result;
        }
        fill(result);
        result.complete = complete;
        bool clear = result.overlaps.empty();
        result.status = clear ? status_t::clear :
            (complete ? status_t::overlap_exhausted : status_t::overlap_budget);
        return result;
    }

    // ------------------------------------------------------------------ the spacing pass
    // A bounded cosmetic pass on the winning contact layout (decision 6 and Mike's round 2):
    // try a halo for every close affected pair; failing that, the first common smaller
    // clearance that validates for all of them; else the contact layout stands.
    void spacing_pass()
    {
        const checkpoint_t contact = *best;
        const auto& L0 = contact.layout;
        std::vector<size_t> actors;
        for (size_t i : L0.moved_order)
            if (is_moved(i, L0) && std::find(actors.begin(), actors.end(), i) == actors.end())
                actors.push_back(i);
        if (actors.empty()) return;
        std::vector<uint8_t> is_actor(S.windows.size(), 0);
        for (size_t i : actors) is_actor[i] = 1;
        // Close affected pairs at the contact checkpoint (window-window; a fixed rectangle or
        // the solo target is a partner that never moves).
        struct pair_t { size_t a; size_t b; bool fixed; double c0; };  // b: window or base_hard index
        std::vector<pair_t> pairs;
        for (size_t a : actors)
        {
            box ra = rect(a, L0.pos[a]);
            for (size_t j = 0; j < S.windows.size(); ++j)
            {
                work.charge();
                if (j == a || (is_actor[j] && j < a)) continue;
                double c = clearance(ra, rect(j, L0.pos[j]));
                if (c >= -TOLERANCE && c < S.halo - 1e-9) pairs.push_back({a, j, false, std::max(0.0, c)});
            }
            for (size_t b = 0; b < base_hard.size(); ++b)
            {
                work.charge();
                double c = clearance(ra, base_hard[b]);
                if (c >= -TOLERANCE && c < S.halo - 1e-9) pairs.push_back({a, b, true, std::max(0.0, c)});
            }
        }
        if (pairs.empty()) return;
        auto min_clearance = [&] (const layout_t& L) {
            double m = INF;
            for (const auto& p : pairs)
            {
                work.charge();
                box ra = rect(p.a, L.pos[p.a]);
                box rb = p.fixed ? base_hard[p.b] : rect(p.b, L.pos[p.b]);
                m = std::min(m, clearance(ra, rb));
            }
            return m;
        };
        double before = min_clearance(L0);
        for (double g : {S.halo, 0.75 * S.halo, 0.5 * S.halo, 0.25 * S.halo})
        {
            if (g <= before + 1e-9) break;  // no improvement possible below what is there
            layout_t L = L0;
            try_spacing(L, actors, is_actor, g);
            double after = min_clearance(L);
            if (after + 1e-6 < g) continue;
            if (!spacing_valid(contact, L)) continue;
            offer(L, contact.name + " + spacing", g);
            return;
        }
    }

    // Clearance of window i at p from every other window/rectangle (in the layout L).
    double nearest(size_t i, const box& r, const layout_t& L, size_t ignore = SIZE_MAX)
    {
        double m = INF;
        for (const auto& b : base_hard) { work.charge(); m = std::min(m, clearance(r, b)); }
        for (size_t j = 0; j < S.windows.size(); ++j)
        {
            if (j == i || j == ignore) continue;
            work.charge();
            m = std::min(m, clearance(r, rect(j, L.pos[j])));
        }
        return m;
    }

    std::vector<pos_t> spacing_candidates(size_t i, const layout_t& L, const pos_t& contact_pos, double g)
    {
        const auto& w = win(i);
        std::vector<pos_t> out{L.pos[i]};
        box me = rect(i, L.pos[i]);
        auto near_contact = [&] (const pos_t& p) {
            return std::hypot(p.cx - contact_pos.cx, p.cy - contact_pos.cy) <= S.halo + 1e-6;
        };
        auto add_vertical = [&] (const pos_t& at) {
            box m = rect(i, at);
            auto try_y = [&] (double y) {
                pos_t p = at; p.cy = representable(y, w.height);
                // Round away from the neighbour: both representable neighbours.
                for (double d : {0.0, 1.0, -1.0})
                {
                    pos_t q = p; q.cy += d;
                    if (near_contact(q) && legal(i, q)) { out.push_back(q); break; }
                }
            };
            auto neighbour = [&] (const box& b) {
                work.charge();
                if (std::min(b.x1, m.x1) - std::max(b.x0, m.x0) + g <= 0) return;
                try_y(b.y0 - g - (m.y1 - m.y0) / 2);
                try_y(b.y1 + g + (m.y1 - m.y0) / 2);
            };
            for (const auto& b : base_hard) neighbour(b);
            for (size_t j = 0; j < S.windows.size(); ++j) if (j != i) neighbour(rect(j, L.pos[j]));
        };
        add_vertical(L.pos[i]);
        if (w.role == role_t::arrival)
        {
            int side = side_of(L.pos[i].cx);
            double dir = side == 0 ? -1 : 1;
            bool in_band = contact_pos.s >= S.noticeable * s_ref[i] - 1e-9;
            for (int k = 1; k <= 4; ++k)
            {
                work.charge();
                double x = representable(L.pos[i].cx + dir * k * S.halo / 4, w.width);
                pos_t p{x, L.pos[i].cy, natural(x), false};
                if (in_band && p.s < S.noticeable * s_ref[i] - 1e-9) break;
                if (!near_contact(p) || !legal(i, p)) continue;
                out.push_back(p);
                add_vertical(p);
            }
        }
        (void)me;
        return out;
    }

    // Evaluate a position for an actor: clearance to its close partners (capped at g), and
    // whether it keeps every other window at least as far as it was (or g).
    struct eval_t { bool ok; int reached; double least; double moved; };
    eval_t evaluate(size_t i, const pos_t& p, const layout_t& L, const layout_t& L0,
        const std::vector<uint8_t>& is_actor, double g)
    {
        eval_t e{true, 0, INF, std::hypot(p.cx - L0.pos[i].cx, p.cy - L0.pos[i].cy)};
        box r = rect(i, p), r0 = rect(i, L0.pos[i]);
        auto check = [&] (const box& b, const box& b0, bool actor) {
            work.charge();
            double c = clearance(r, b), c0 = clearance(r0, b0);
            if (c < -TOLERANCE && c0 >= -TOLERANCE) { e.ok = false; return; }   // no new overlap
            if (c < std::min(g, c0) - 1e-6 && !actor) { e.ok = false; return; } // no crowding
            if (c0 < S.halo - 1e-9 && c0 >= -TOLERANCE)
            {
                if (c >= g - 1e-6) ++e.reached;
                e.least = std::min(e.least, std::min(c, g));
            }
        };
        for (const auto& b : base_hard) check(b, b, false);
        for (size_t j = 0; j < S.windows.size() && e.ok; ++j)
        {
            if (j == i) continue;
            check(rect(j, L.pos[j]), rect(j, L0.pos[j]), is_actor[j]);
        }
        return e;
    }

    static bool better_eval(const eval_t& a, const eval_t& b)
    {
        if (a.ok != b.ok) return a.ok;
        if (a.reached != b.reached) return a.reached > b.reached;
        if (std::abs(a.least - b.least) > 1e-6) return a.least > b.least;
        return a.moved < b.moved - 1e-9;
    }

    void try_spacing(layout_t& L, const std::vector<size_t>& actors, const std::vector<uint8_t>& is_actor, double g)
    {
        const layout_t L0 = L;
        for (size_t i : actors)
        {
            auto cands = spacing_candidates(i, L, L0.pos[i], g);
            pos_t best_p = L.pos[i];
            eval_t best_e = evaluate(i, best_p, L, L0, is_actor, g);
            for (const auto& p : cands)
            {
                auto e = evaluate(i, p, L, L0, is_actor, g);
                if (better_eval(e, best_e)) { best_e = e; best_p = p; }
            }
            if (!best_e.ok) continue;
            L.pos[i] = best_p;
            // One partner trial: an actor this leaves closer than g gets one nudge by the same
            // rules; the pair is kept only if both end with clearance g. No further chaining.
            box r = rect(i, best_p);
            for (size_t j : actors)
            {
                if (j == i) continue;
                double c = clearance(r, rect(j, L.pos[j]));
                if (c >= g - 1e-6 || c < -TOLERANCE) continue;
                auto pc = spacing_candidates(j, L, L0.pos[j], g);
                bool kept = false;
                for (const auto& p : pc)
                {
                    auto e = evaluate(j, p, L, L0, is_actor, g);
                    if (!e.ok) continue;
                    if (clearance(rect(j, p), r) >= g - 1e-6) { L.pos[j] = p; kept = true; break; }
                }
                (void)kept;
                break;
            }
        }
    }

    // The spaced layout replaces the contact one only if every comparison term but travel is
    // unchanged, each nudged window moved at most one halo, arrivals stay in band, residents
    // keep x and scale, nothing new overlaps and no moved resident's original spot is clear.
    bool spacing_valid(const checkpoint_t& contact, const layout_t& L)
    {
        const auto& L0 = contact.layout;
        for (size_t i = 0; i < S.windows.size(); ++i)
        {
            work.charge();
            const auto& p = L.pos[i], &p0 = L0.pos[i];
            if (same_pos(p, p0)) continue;
            if (!is_moved(i, L0)) return false;                                  // untouched stays
            if (std::hypot(p.cx - p0.cx, p.cy - p0.cy) > S.halo + 1e-6) return false;
            if (!legal(i, p)) return false;
            if (win(i).role == role_t::resident && (std::abs(p.cx - p0.cx) > 1e-6 || std::abs(p.s - p0.s) > 1e-9))
                return false;
            if (win(i).role == role_t::arrival && p0.s >= S.noticeable * s_ref[i] - 1e-9 &&
                p.s < S.noticeable * s_ref[i] - 1e-9) return false;
        }
        auto s = score_of(L);
        const auto& c = contact.score;
        if (!s.legal || std::abs(s.solo_overlap - c.solo_overlap) > 1e-6 || std::abs(s.overlap - c.overlap) > 1e-6 ||
            s.below_band != c.below_band || s.residents_moved != c.residents_moved ||
            std::abs(s.resident_outward - c.resident_outward) > 0.5) return false;
        for (size_t j : residents)
        {
            if (!is_moved(j, L)) continue;
            if (!overlaps_any(j, rect(j, original.pos[j]), L, S.contact)) return false;
        }
        return true;
    }
};
}

result_t solve(const snapshot_t& snapshot, work_t& work, const std::function<void(const result_t&)>& publish)
{
    work.cap = snapshot.work_cap;
    solver_t solver(snapshot, work, publish);
    return solver.run();
}

result_t solve(const snapshot_t& snapshot)
{
    work_t work;
    return solve(snapshot, work);
}
}
