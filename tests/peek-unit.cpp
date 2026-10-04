// Window avoidance, the peeking strip (WK13, P12, P13): the pure engine in core/plugin/src/peek.cpp.
// Numbered as in the peek-strip design's test list; the engram case is the osanwe regression of
// 2026-10-04.
#include "peek.hpp"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <iostream>
#include <map>
#include <random>
#include <string>
using namespace scottland::windowing;
int passed = 0, failed = 0;
void check(bool ok, const std::string& name)
{
    std::cout << (ok ? "PASS  " : "FAIL  ") << name << '\n';
    if (ok) ++passed; else ++failed;
}
bool near(point a, point b, double e = 1e-6) { return std::hypot(a.x - b.x, a.y - b.y) < e; }
double len(point a) { return std::hypot(a.x, a.y); }
const offset_box everywhere{-1e6, 1e6, -1e6, 1e6};

// Independent of the engine: does the window at offset o show a free w x h rectangle? A free
// rectangle can slide left and up until it touches an obstacle or a bound, so its corner can be
// taken from the bounds and the obstacles' far edges.
bool shows(rectangle frame, point o, double w, double h, rectangle screen, const std::vector<rectangle>& obstacles)
{
    double x1 = std::max(frame.x + o.x, screen.x), y1 = std::max(frame.y + o.y, screen.y);
    double x2 = std::min(frame.x + o.x + frame.width, screen.x + screen.width);
    double y2 = std::min(frame.y + o.y + frame.height, screen.y + screen.height);
    if (x2 - x1 < w - 1e-9 || y2 - y1 < h - 1e-9) return false;
    std::vector<double> xs{x1}, ys{y1};
    for (auto r : obstacles) { xs.push_back(r.x + r.width); ys.push_back(r.y + r.height); }
    for (double x : xs) for (double y : ys)
    {
        if (x < x1 - 1e-9 || y < y1 - 1e-9 || x + w > x2 + 1e-9 || y + h > y2 + 1e-9) continue;
        bool free = true;
        for (auto r : obstacles)
            if (x < r.x + r.width - 1e-9 && r.x < x + w - 1e-9 && y < r.y + r.height - 1e-9 && r.y < y + h - 1e-9)
            { free = false; break; }
        if (free) return true;
    }
    return false;
}
std::vector<peek_result> solve(const peek_request& request, size_t budget = SIZE_MAX, size_t *slices = nullptr,
    size_t *units = nullptr)
{
    peek_pass pass(request);
    while (!peek_step(pass, budget)) {}
    if (slices) *slices = pass.slices;
    if (units) *units = pass.units;
    return pass.results;
}
peek_window window(rectangle frame, double zone_x1 = -1e6, double zone_x2 = 1e6, bool anchored = false)
{
    peek_window w;
    w.frame = frame; w.zone_x1 = zone_x1; w.zone_x2 = zone_x2;
    w.center_y1 = -1e6; w.center_y2 = 1e6; w.anchored = anchored;
    w.full_hint = 72; w.minimum_hint = 48;
    return w;
}
// The bridge's lifecycle: one slice per tick; a pass finishes on its snapshot, then the next one
// starts at once from the newest layout with the targets so far; displayed offsets ease 0.18 per
// tick toward the targets.
struct driver
{
    std::optional<peek_pass> pass;
    std::vector<point> target, displayed;
    std::vector<size_t> result_pass;
    size_t passes = 0;
    peek_request snapshot(peek_request r)
    {
        for (size_t i = 0; i < r.windows.size(); ++i)
        { r.windows[i].target = target[i]; r.windows[i].displayed = displayed[i]; }
        return r;
    }
    void tick(const peek_request& layout, size_t budget = peek_slice_units, bool ease = true)
    {
        size_t n = layout.windows.size();
        target.resize(n); displayed.resize(n); result_pass.resize(n);
        if (!pass) { pass.emplace(snapshot(layout)); ++passes; }
        size_t before = pass->next;
        bool done = peek_step(*pass, budget);
        for (size_t i = before; i < pass->next; ++i) { target[i] = pass->results[i].target; result_pass[i] = passes; }
        if (done) pass.reset();
        if (ease) for (size_t i = 0; i < n; ++i)
        {
            displayed[i].x += (target[i].x - displayed[i].x) * .18;
            displayed[i].y += (target[i].y - displayed[i].y) * .18;
        }
    }
    void settle(const peek_request& layout, size_t ticks = 200) { for (size_t k = 0; k < ticks; ++k) tick(layout); }
};

int main()
{
    const rectangle screen{0, 0, 1000, 800};
    // 1. nearest_offset basics.
    {
        rectangle rear{300, 300, 200, 150};
        auto alone = nearest_offset(rear, 100, 24, screen, {}, {}, everywhere);
        check(alone && near(alone->offset, {}), "1 uncovered window: offset 0");
        // The front window covers the rear one with margins 10 (left), 20, 30, 40: whichever
        // edge is smallest wins, for each of the four.
        bool each = true;
        for (int smallest = 0; smallest < 4; ++smallest)
        {
            double m[4] = {40, 40, 40, 40};
            m[smallest] = 10 + smallest;
            rectangle front{rear.x - m[0], rear.y - m[2], rear.width + m[0] + m[1], rear.height + m[2] + m[3]};
            point best{};
            double depth = 24;
            if (smallest == 0) best = {-(m[0] + depth), 0};
            if (smallest == 1) best = {m[1] + depth, 0};
            if (smallest == 2) best = {0, -(m[2] + depth)};
            if (smallest == 3) best = {0, m[3] + depth};
            double cost = 1e9;
            for (auto [w, h] : {std::pair{100.0, 24.0}, std::pair{24.0, 100.0}})
                if (auto p = nearest_offset(rear, w, h, screen, {front}, {}, everywhere); p && p->cost < cost)
                    cost = p->cost;
            each &= std::abs(cost - len(best)) < 1e-6;
        }
        check(each, "1 fully covered: the move equals the smallest of the four edge moves, for each edge");
        // Decision 4: an interior gap between two front windows counts.
        rectangle a{250, 250, 125, 250}, b{399, 250, 151, 250}; // gap 24 wide at x 375..399
        auto gap = nearest_offset(rear, 24, 100, screen, {a, b}, {}, everywhere);
        check(gap && near(gap->offset, {}), "1 an interior gap exactly one strip deep: offset 0");
        rectangle b2{398, 250, 152, 250};
        auto narrow = nearest_offset(rear, 24, 100, screen, {a, b2}, {}, everywhere);
        check(narrow && len(narrow->offset) > 1, "1 the same gap 1 px too narrow: the window moves");
    }
    // 2. Both orientations; a window narrower than the strip; text scale 1 and 1.64.
    {
        rectangle tall{300, 100, 150, 400};
        rectangle front{250, 40, 260, 520}; // covers it; left margin 50, right 60, top 60, bottom 60
        auto vertical = nearest_offset(tall, 24, 100, screen, {front}, {}, everywhere);
        check(vertical && near(vertical->offset, {-74, 0}), "2 a vertical strip along the left edge");
        rectangle wide{300, 300, 400, 150};
        rectangle front2{280, 200, 440, 300}; // left/right 20, top 100, bottom 50
        auto horizontal = nearest_offset(wide, 24, 100, screen, {front2}, {}, everywhere);
        check(horizontal && near(horizontal->offset, {-44, 0}), "2 the cheaper strip may be vertical on a wide window");
        auto down = nearest_offset(wide, 100, 24, screen, {front2}, {}, {-30, 30, -1e6, 1e6});
        check(down && near(down->offset, {0, 74}), "2 within limits, a horizontal strip along the bottom");
        for (double s : {1.0, 1.64})
        {
            peek_request r; r.screen = screen;
            r.strip_depth = peek_strip_depth * s; r.strip_length = peek_strip_length * s;
            r.windows = {window({200, 200, 500, 400}), window({240, 180, 60, 300})};
            auto res = solve(r);
            double depth = 24 * s;
            // Narrower than the strip: its whole 60 px width must show, depth 24*s.
            bool ok = res[1].outcome == peek_outcome::moved && std::abs(res[1].room.width - 60) < 1e-6 &&
                std::abs(res[1].room.height - depth) < 1e-6 &&
                shows(r.windows[1].frame, res[1].target, 60, depth, screen, {r.windows[0].frame});
            check(ok, "2 a 60 px wide window peeks with its whole width, depth 24 x text scale " + std::to_string(s));
        }
    }
    // 3. Exactness against a brute force over integer offsets.
    {
        std::mt19937 rng(7);
        std::uniform_real_distribution<double> unit(0, 1);
        const rectangle small{0, 0, 160, 120};
        bool feasible = true, nearest = true, none_proven = true;
        size_t found = 0, none = 0;
        for (int trial = 0; trial < 2000; ++trial)
        {
            auto ri = [&] (double lo, double hi) { return std::round(lo + unit(rng) * (hi - lo)); };
            rectangle frame{ri(10, 90), ri(10, 70), ri(20, 70), ri(20, 50)};
            std::vector<rectangle> obstacles;
            int k = int(ri(0, 12));
            for (int j = 0; j < k; ++j) obstacles.push_back({ri(-10, 150), ri(-10, 110), ri(5, 60), ri(5, 50)});
            double w = ri(4, 20), h = ri(4, 20);
            w = std::min(w, frame.width); h = std::min(h, frame.height);
            offset_box box{-ri(0, 40), ri(0, 40), -ri(0, 40), ri(0, 40)};
            double weights[4] = {1, 1, 1, 1};
            if (trial % 2) weights[int(ri(0, 3))] = 1, weights[(trial / 2) % 4] = 2;
            auto p = nearest_offset(frame, w, h, small, obstacles, {}, box, {}, weights);
            auto cost = [&] (point o) {
                return std::hypot(o.x * (o.x < 0 ? weights[0] : weights[1]), o.y * (o.y < 0 ? weights[2] : weights[3]));
            };
            if (p)
            {
                ++found;
                feasible &= shows(frame, p->offset, w, h, small, obstacles) &&
                    p->offset.x >= box.x1 - 1e-9 && p->offset.x <= box.x2 + 1e-9 &&
                    p->offset.y >= box.y1 - 1e-9 && p->offset.y <= box.y2 + 1e-9;
                feasible &= std::abs(cost(p->offset) - p->cost) < 1e-6;
            } else ++none;
            for (double x = box.x1; x <= box.x2; ++x) for (double y = box.y1; y <= box.y2; ++y)
            {
                if (p && cost({x, y}) >= p->cost - 1) continue;
                if (shows(frame, {x, y}, w, h, small, obstacles)) { if (p) nearest = false; else none_proven = false; }
            }
        }
        check(feasible, "3 2,000 random layouts: every returned offset is feasible, inside its limits, at its cost");
        check(nearest, "3 no integer offset is more than 1 px nearer than the returned one (" +
            std::to_string(found) + " found)");
        check(none_proven, "3 'no offset' is true: the brute force finds none either (" + std::to_string(none) + " cases)");
    }
    // 4. Limits: zone extent, P13 property, anchored.
    {
        // The rear window's zone is x in [300, 640] for its center (now 450); front covers it.
        rectangle front{200, 200, 600, 400};
        peek_request r; r.screen = screen;
        r.windows = {window(front), window({300, 300, 300, 200}, 300, 640)};
        r.windows[1].center_y1 = 0; r.windows[1].center_y2 = 800;
        auto res = solve(r);
        // Right: needs +224 (center 674, outside). Left: -124 (center 326, inside). Up: -124 too.
        double cx = 450 + res[1].target.x;
        check(res[1].outcome == peek_outcome::moved && cx >= 300 - 1e-6 && cx <= 640 + 1e-6 &&
            len(res[1].target) <= 124 + 1e-6, "4 the zone limits the displayed center, not the edges");
        r.windows[1].zone_x1 = 400; r.windows[1].zone_x2 = 500;
        r.windows[1].center_y1 = 350; r.windows[1].center_y2 = 450;
        res = solve(r);
        check(res[1].outcome == peek_outcome::no_room && near(res[1].target, {}),
            "4 no room inside the zone: the window does not move; no_room");
        // Hanging past the zone edge: the window may cross it while its center stays inside.
        r.windows[1].zone_x1 = 300; r.windows[1].zone_x2 = 500; r.windows[1].center_y1 = 400; r.windows[1].center_y2 = 400;
        res = solve(r);
        check(res[1].outcome == peek_outcome::moved && 300 + res[1].target.x < 300 &&
            450 + res[1].target.x >= 300 - 1e-6, "4 a window may hang past its zone edge, its center inside");
        // Property: random stacks never put a center outside its zone.
        std::mt19937 rng(11);
        std::uniform_real_distribution<double> x(0, 900), y(0, 700), s(100, 500);
        const double zones[3][2] = {{20, 350}, {350, 650}, {650, 980}};
        bool inside = true, anchored_still = true;
        for (int trial = 0; trial < 300; ++trial)
        {
            peek_request q; q.screen = screen; q.window_mode = trial % 2;
            for (int i = 0; i < 8; ++i)
            {
                rectangle f{x(rng), y(rng), s(rng), s(rng) * .7};
                double c = f.x + f.width / 2;
                int z = c < 350 ? 0 : c < 650 ? 1 : 2;
                auto w = window(f, zones[z][0], zones[z][1], i == 3);
                w.center_y1 = 0; w.center_y2 = 800;
                q.windows.push_back(w);
            }
            auto out = solve(q);
            for (size_t i = 0; i < out.size(); ++i)
            {
                const auto& w = q.windows[i];
                double c = w.frame.x + w.frame.width / 2 + out[i].target.x;
                double cy = w.frame.y + w.frame.height / 2 + out[i].target.y;
                // The true position is always allowed (where the user put it), even off limits.
                double tx = w.frame.x + w.frame.width / 2, ty = w.frame.y + w.frame.height / 2;
                inside &= c >= std::min(tx, w.zone_x1) - 1e-6 && c <= std::max(tx, w.zone_x2) + 1e-6 &&
                    cy >= std::min(ty, 0.0) - 1e-6 && cy <= std::max(ty, 800.0) + 1e-6;
                if (w.anchored) anchored_still &= near(out[i].target, {});
            }
        }
        check(inside, "4 300 random stacks: no displayed center ever leaves its zone or the screen (P13)");
        check(anchored_still, "4 an anchored window never moves");
    }
    // 5. The ladder.
    {
        rectangle front{200, 200, 600, 400};
        peek_request r; r.screen = screen; r.window_mode = true;
        auto rear = window({250, 250, 500, 300});
        rear.full_hint = 100; rear.minimum_hint = 48;
        r.windows = {window(front), rear};
        auto res = solve(r);
        check(res[1].rung == peek_rung::full && res[1].room.width >= 2 * hint_room_radius(100) - 1e-6 &&
            shows(rear.frame, res[1].target, 108, 108, screen, {front}), "5 Window mode takes full hint room when it fits");
        // Limit the moves so only the minimum room fits.
        r.windows[1].zone_x1 = 500 - 110; r.windows[1].zone_x2 = 500 + 110;
        r.windows[1].center_y1 = 400 - 1; r.windows[1].center_y2 = 400 + 1;
        res = solve(r);
        check(res[1].rung == peek_rung::minimum, "5 only the minimum hint fits: rung minimum");
        r.windows[1].zone_x1 = 500 - 80; r.windows[1].zone_x2 = 500 + 80;
        res = solve(r);
        check(res[1].rung == peek_rung::peek && res[1].outcome == peek_outcome::moved, "5 only a strip fits: rung peek");
        r.windows[1].zone_x1 = 500 - 30; r.windows[1].zone_x2 = 500 + 30;
        res = solve(r);
        check(res[1].outcome == peek_outcome::no_room && near(res[1].target, {}),
            "5 nothing fits: no_room, target 0");
        // Leaving Window mode returns to the peek target.
        r.windows[1].zone_x1 = -1e6; r.windows[1].zone_x2 = 1e6;
        r.windows[1].center_y1 = -1e6; r.windows[1].center_y2 = 1e6;
        r.window_mode = false;
        auto peek_only = solve(r);
        r.window_mode = true;
        auto hinted = solve(r);
        r.window_mode = false;
        r.windows[1].target = hinted[1].target; r.windows[1].displayed = hinted[1].target;
        auto back = solve(r);
        check(len(hinted[1].target) > len(peek_only[1].target) + 20 && near(back[1].target, peek_only[1].target),
            "5 leaving Window mode returns to the peek target");
    }
    // 5b. Live, a nearby smaller hint beats a full one across the screen (P11); at rest, full.
    {
        rectangle front{200, 200, 600, 400};
        peek_request r; r.screen = screen; r.window_mode = true; r.live = true;
        auto rear = window({250, 250, 500, 300});
        rear.full_hint = 100; rear.minimum_hint = 48;
        rear.target = rear.displayed = {-74, 0}; // its peek offset: a strip along its left edge
        r.windows = {window(front, -1e6, 1e6, true), rear};
        auto res = solve(r);
        check(res[1].rung == peek_rung::minimum && res[1].rule == 'N' && near(res[1].target, {-50 - hint_room_radius(48) * 2, 0}, 1e-6),
            "5b live Window mode: the near minimum room wins over full room 84 px away");
        r.live = false;
        auto rest = solve(r);
        check(rest[1].rung == peek_rung::full, "5b at rest the same window takes full room");
    }
    // 6. Calm while live: an obstacle swept 1 px at a time across a three-window stack. Every
    // target is the current way continued (within r of the old target) or within R of where the
    // window is drawn, per axis; the larger "any" move is never needed after the first solve.
    {
        const rectangle wide{0, 0, 1920, 1080};
        bool reach = true, no_flip = true, no_any = true;
        double largest = 0;
        for (bool mode : {false, true})
        {
            peek_request r; r.screen = wide; r.live = true; r.window_mode = mode;
            r.windows = {window({300, 300, 500, 350}, -1e6, 1e6, true), window({700, 280, 600, 400}, 640, 1280),
                window({760, 350, 560, 380}, 640, 1280)};
            for (auto& w : r.windows) { w.center_y1 = 0; w.center_y2 = 1080; w.full_hint = 130; }
            std::vector<std::vector<point>> history(3);
            std::vector<point> target(3), displayed(3);
            for (int step = 0; step <= 600; ++step)
            {
                r.windows[0].frame.x = 300 + step;
                for (size_t i = 0; i < 3; ++i) { r.windows[i].target = target[i]; r.windows[i].displayed = displayed[i]; }
                auto res = solve(r);
                for (size_t i = 0; i < 3; ++i)
                {
                    auto t = res[i].target;
                    if (step > 0)
                    {
                        largest = std::max(largest, len({t.x - target[i].x, t.y - target[i].y}));
                        bool kept = std::max(std::abs(t.x - target[i].x), std::abs(t.y - target[i].y)) <= peek_keep_radius + 1;
                        bool near_drawn = std::max(std::abs(t.x - displayed[i].x), std::abs(t.y - displayed[i].y)) <= peek_reach + 1;
                        reach &= kept || near_drawn;
                        no_any &= res[i].rule != 'Y';
                    }
                    auto& h = history[i];
                    if (h.size() >= 2 && near(t, h[h.size() - 2], .01) && !near(t, h.back(), .5)) no_flip = false;
                    h.push_back(t);
                    target[i] = t;
                    for (int k = 0; k < 2; ++k)
                    { displayed[i].x += (target[i].x - displayed[i].x) * .18; displayed[i].y += (target[i].y - displayed[i].y) * .18; }
                }
            }
        }
        check(reach, "6 live 1 px sweep (always-on and Window mode): every target is within r of the old one or R of "
            "the drawn offset, per axis (largest change " + std::to_string(largest) + " px)");
        check(no_any, "6 the larger 'any' move is never needed after the first solve");
        check(no_flip, "6 no target alternates between two values on consecutive steps");
    }
    // 7. Review-3's s1 fixture (F-B): the dragged window passes a pushed one.
    {
        const rectangle wide{0, 0, 1920, 1080};
        const double zone_half = 1920 * 33.333 / 200;
        auto zone = [&] (rectangle f, double& z1, double& z2) {
            double c = f.x + f.width / 2;
            if (std::abs(c - 960) <= zone_half) { z1 = 960 - zone_half; z2 = 960 + zone_half; }
            else if (c < 960) { z1 = 0; z2 = 960 - zone_half - .5; }
            else { z1 = 960 + zone_half + .5; z2 = 1920; }
        };
        bool calm = true, rest_least = true;
        double largest = 0;
        for (bool mode : {false, true})
        {
            std::vector<rectangle> frames{{100, 250, 700, 500}, {560, 140, 800, 700}, {600, 200, 760, 640},
                {520, 260, 880, 600}};
            peek_request r; r.screen = wide; r.window_mode = mode;
            for (size_t i = 0; i < frames.size(); ++i)
            {
                auto w = window(frames[i], 0, 0, i == 0);
                zone(frames[i], w.zone_x1, w.zone_x2);
                w.center_y1 = 0; w.center_y2 = 1080;
                w.full_hint = std::clamp(std::min(frames[i].width, frames[i].height) * .34, 72.0, 132.0);
                r.windows.push_back(w);
            }
            driver d;
            std::vector<point> last(4);
            int steps = 0;
            auto sweep = [&] (int x) {
                r.windows[0].frame.x = x; r.live = true;
                auto drawn = d.displayed;
                d.tick(r, SIZE_MAX);
                // The first solve starts the avoidance (in Window mode a full-room move).
                for (size_t i = 1; i < 4 && steps++ > 0; ++i)
                {
                    auto t = d.target[i];
                    largest = std::max(largest, len({t.x - last[i].x, t.y - last[i].y}));
                    bool kept = std::max(std::abs(t.x - last[i].x), std::abs(t.y - last[i].y)) <= peek_keep_radius + 1;
                    bool near_drawn = std::max(std::abs(t.x - drawn[i].x), std::abs(t.y - drawn[i].y)) <= peek_reach + 1;
                    calm &= kept || near_drawn;
                }
                for (size_t i = 1; i < 4; ++i) last[i] = d.target[i];
            };
            for (int x = 100; x <= 1100; ++x) sweep(x);
            for (int x = 1100; x >= 100; --x) sweep(x);
            r.live = false;
            d.settle(r);
            auto rest = r; for (auto& w : rest.windows) w.target = {}, w.displayed = {};
            auto fresh = solve(rest);
            for (size_t i = 1; i < 4; ++i)
            {
                // At rest the least offset wins unless the current way is within the switch margin.
                double cost_now = len(d.target[i]), cost_fresh = len(fresh[i].target);
                rest_least &= cost_now <= cost_fresh + peek_switch_margin * peek_direction_weight + 1e-6;
            }
        }
        check(calm, "7 s1: while the drag is live every target stays within r of its way or R of where it is drawn "
            "(largest change " + std::to_string(largest) + " px); no 306 px slide");
        check(rest_least, "7 s1: on the first rest pass every window takes the least offset (within the switch margin)");
    }
    // 8. Tie flip: two alternatives within 6 px, with 1 px jitter.
    {
        rectangle rear{300, 300, 300, 200};
        peek_request r; r.screen = screen;
        bool steady = true;
        point first{};
        std::mt19937 rng(3);
        for (int step = 0; step < 200; ++step)
        {
            int jitter = int(rng() % 3) - 1;
            // Left needs 30 + jitter, up needs 30 - jitter.
            rectangle front{rear.x - 6 + jitter, rear.y - 6 - jitter, 400, 300};
            r.windows = {window(front), window(rear)};
            r.windows[1].target = first; r.windows[1].displayed = first;
            auto res = solve(r);
            if (step == 0) first = res[1].target;
            else steady &= (first.x < 0) == (res[1].target.x < 0) && (first.y < 0) == (res[1].target.y < 0);
            first = res[1].target;
        }
        check(steady, "8 two ways within 6 px under 1 px jitter: the chosen way never changes");
    }
    // 9. Return to zero.
    {
        rectangle rear{300, 300, 300, 200};
        peek_request r; r.screen = screen;
        r.windows = {window({200, 200, 600, 400}), window(rear)};
        auto res = solve(r);
        point t = res[1].target;
        r.windows[0].frame = {900, 700, 50, 50};
        r.windows[1].target = t; r.windows[1].displayed = t;
        auto home = solve(r);
        check(len(t) > 50 && near(home[1].target, {}), "9 obstruction removed at rest: target exactly 0");
        r.live = true;
        int passes = 0;
        point now = t;
        while (len(now) > 1e-9 && passes < 50)
        {
            r.windows[1].target = now; r.windows[1].displayed = now;
            now = solve(r)[1].target; ++passes;
        }
        check(passes <= int(std::ceil(len(t) / peek_reach)), "9 live: home within ceil(|t|/64) passes (" +
            std::to_string(passes) + ")");
    }
    // 10. Progress: dense stacks complete; no_room is real; slices do not change results.
    {
        const rectangle wide{0, 0, 1920, 1080};
        auto dense = [&] (bool random, size_t count, bool mode) {
            peek_request r; r.screen = wide; r.window_mode = mode;
            std::mt19937 rng(42);
            std::uniform_real_distribution<double> x(0, 1500), y(0, 700), s(300, 900);
            for (size_t i = 0; i < count; ++i)
            {
                rectangle f = random ? rectangle{x(rng), y(rng), s(rng), s(rng) * .6} : rectangle{513, 285, 894, 510};
                double c = f.x + f.width / 2;
                double z1 = 640, z2 = 1280;
                if (c < 640) z1 = 0, z2 = 639.5; else if (c > 1280) z1 = 1280.5, z2 = 1920;
                auto w = window(f, z1, z2, i == 0);
                w.center_y1 = 0; w.center_y2 = 1080;
                w.full_hint = std::clamp(std::min(f.width, f.height) * .34, 72.0, 132.0);
                r.windows.push_back(w);
            }
            return r;
        };
        for (auto [random, count, mode] : {std::tuple{false, size_t(16), false}, std::tuple{true, size_t(30), false},
            std::tuple{false, size_t(16), true}, std::tuple{true, size_t(30), true}})
        {
            auto r = dense(random, count, mode);
            size_t slices = 0, units = 0, big_slices = 0, big_units = 0;
            auto sliced = solve(r, 1800, &slices, &units);
            auto started = std::chrono::steady_clock::now();
            auto whole = solve(r, peek_slice_units, &big_slices, &big_units);
            double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - started).count();
            std::string name = std::string(random ? "30 random" : "16 identical") + (mode ? ", Window mode" : ", always-on");
            bool decided = true, same = true, honest = true;
            size_t no_room = 0, moved = 0;
            for (size_t i = 0; i < count; ++i)
            {
                decided &= sliced[i].outcome != peek_outcome::pending;
                same &= sliced[i].target.x == whole[i].target.x && sliced[i].target.y == whole[i].target.y &&
                    sliced[i].rung == whole[i].rung;
                moved += whole[i].outcome == peek_outcome::moved;
                if (whole[i].outcome != peek_outcome::no_room) continue;
                if (++no_room > 3) continue; // the brute force is slow; three per layout
                // Brute force on a 4 px grid inside its limits: no offset shows a strip.
                const auto& w = r.windows[i];
                std::vector<rectangle> obstacles;
                for (size_t j = 0; j < i; ++j)
                    obstacles.push_back({r.windows[j].frame.x + whole[j].target.x, r.windows[j].frame.y + whole[j].target.y,
                        r.windows[j].frame.width, r.windows[j].frame.height});
                double cx = w.frame.x + w.frame.width / 2, cy = w.frame.y + w.frame.height / 2;
                if (w.anchored) continue;
                double sw = std::min(100.0, w.frame.width), sh = std::min(24.0, w.frame.height);
                for (double ox = w.zone_x1 - cx; ox <= w.zone_x2 - cx && honest; ox += 8)
                    for (double oy = w.center_y1 - cy; oy <= w.center_y2 - cy && honest; oy += 8)
                        honest &= !shows(w.frame, {ox, oy}, sw, sh, wide, obstacles) &&
                            !shows(w.frame, {ox, oy}, sh, sw, wide, obstacles);
            }
            std::cout << "      " << name << ": " << units << " units; " << slices << " slices at 1800 units, " <<
                big_slices << " at the shipped " << peek_slice_units << "; " << ms << " ms whole pass; moved " <<
                moved << ", no_room " << no_room << "\n";
            check(decided, "10 " + name + ": every window ends visible, moved or no_room");
            check(same, "10 " + name + ": a 1,800-unit slice budget gives bit-identical targets to one slice");
            check(honest, "10 " + name + ": every no_room window has no strip anywhere in its limits (8 px brute force, first three)");
            if (!mode) check(big_slices <= (random ? 12u : 3u), "10 " + name + ": completes within the design's slice target (" +
                std::to_string(big_slices) + " slices)");
        }
    }
    // Calibration: work units per millisecond on this machine, over dense passes in both modes.
    {
        const rectangle wide{0, 0, 1920, 1080};
        std::mt19937 rng(9);
        std::uniform_real_distribution<double> x(0, 1500), y(0, 700), s(300, 900);
        size_t units = 0;
        auto started = std::chrono::steady_clock::now();
        for (int round = 0; round < 40; ++round)
        {
            peek_request r; r.screen = wide; r.window_mode = round % 2;
            for (int i = 0; i < 30; ++i)
            {
                auto w = window({x(rng), y(rng), s(rng), s(rng) * .6}, 0, 1920, i == 0);
                w.center_y1 = 0; w.center_y2 = 1080; w.full_hint = 120;
                r.windows.push_back(w);
            }
            size_t u = 0;
            solve(r, SIZE_MAX, nullptr, &u);
            units += u;
        }
        double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - started).count();
        std::cout << "      calibration: " << units << " units in " << ms << " ms = " << units / ms <<
            " units/ms; the shipped slice of " << peek_slice_units << " units = " << peek_slice_units / (units / ms) <<
            " ms here\n";
    }
    // 11. Sustained change: one front frame changes every slice; every window keeps getting results.
    {
        const rectangle wide{0, 0, 1920, 1080};
        peek_request r; r.screen = wide;
        for (int i = 0; i < 12; ++i)
        {
            auto w = window({400.0 + 20 * i, 200.0 + 10 * i, 800, 500}, 0, 1920, i == 0);
            w.center_y1 = 0; w.center_y2 = 1080;
            r.windows.push_back(w);
        }
        driver d;
        size_t stale = 0;
        for (int k = 0; k < 100; ++k)
        {
            r.windows[0].frame.x = 400 + k;
            d.tick(r, 1800);
            if (d.passes > 2)
                for (size_t i = 0; i < r.windows.size(); ++i) stale = std::max(stale, d.passes - d.result_pass[i]);
        }
        check(stale <= 1, "11 a front frame changing every slice: no window's result is more than one pass old (passes " +
            std::to_string(d.passes) + ")");
    }
    // 12. Determinism over budgets.
    {
        const rectangle wide{0, 0, 1920, 1080};
        peek_request r; r.screen = wide; r.window_mode = true;
        std::mt19937 rng(5);
        std::uniform_real_distribution<double> x(0, 1500), y(0, 700), s(300, 900);
        for (int i = 0; i < 14; ++i)
        {
            auto w = window({x(rng), y(rng), s(rng), s(rng) * .6}, 0, 1920, i == 0);
            w.center_y1 = 0; w.center_y2 = 1080; w.full_hint = 120;
            r.windows.push_back(w);
        }
        auto a = solve(r, 1), b = solve(r, 100), c = solve(r);
        bool same = true;
        for (size_t i = 0; i < a.size(); ++i)
            same &= a[i].target.x == b[i].target.x && a[i].target.y == b[i].target.y &&
                a[i].target.x == c[i].target.x && a[i].target.y == c[i].target.y &&
                a[i].hint.center.x == c[i].hint.center.x && a[i].hint.center.y == c[i].hint.center.y &&
                a[i].rung == c[i].rung && b[i].rung == c[i].rung;
        check(same, "12 budgets 1, 100 and unlimited give identical results");
    }
    // 13. Hint placement and collisions.
    {
        peek_request r; r.screen = screen; r.window_mode = true;
        r.windows = {window({200, 100, 600, 500}, -1e6, 1e6, true)};
        auto res = solve(r);
        check(near(res[0].hint.center, {500, 350}), "13 the frontmost window's hint is at its exact center");
        // A strip: the window can only show a 30 px strip above the front one.
        auto rear = window({250, 70, 500, 400}, 499, 501);
        rear.center_y1 = 270; rear.center_y2 = 270;
        r.windows.push_back(rear);
        res = solve(r);
        double radius = 48 * hint_pop_scale / 2;
        check(res[1].rung == peek_rung::peek && res[1].hint.center.y > 70 && res[1].hint.center.y - radius < 100 &&
            res[1].hint.center.y + radius > 100 && res[1].hint_diameter == 48,
            "13 a strip hint sits on the strip and overlaps the front window's edge");
        // Two no-room hints at the same point end apart.
        auto buried = window({400, 250, 200, 200}, 499, 501); buried.center_y1 = 350; buried.center_y2 = 350;
        auto buried2 = buried;
        r.windows = {window({100, 50, 800, 650}, -1e6, 1e6, true), buried, buried2};
        res = solve(r);
        double d12 = std::hypot(res[1].hint.center.x - res[2].hint.center.x, res[1].hint.center.y - res[2].hint.center.y);
        double d01 = std::hypot(res[0].hint.center.x - res[1].hint.center.x, res[0].hint.center.y - res[1].hint.center.y);
        double d02 = std::hypot(res[0].hint.center.x - res[2].hint.center.x, res[0].hint.center.y - res[2].hint.center.y);
        double need = 48 * hint_pop_scale + hint_collision_gap;
        double need0 = (res[0].hint_diameter + 48) / 2 * hint_pop_scale + hint_collision_gap;
        check(res[1].outcome == peek_outcome::no_room && res[2].outcome == peek_outcome::no_room &&
            d12 >= need - 1e-6 && d01 >= need0 - 1e-6 && d02 >= need0 - 1e-6,
            "13 concentric no_room hints clear each other and the front hint by the collision gap");
    }
    // The osanwe engram case (2026-10-04, osanwe-engram-sliver-20261004.png): in Window mode the
    // engram window is covered except a sliver along its top (it is at the top of the screen; the
    // focused front window covers the rest). Its hint was drawn near its center, under the front
    // window. Decision 5: make room; if there is none, the hint goes on the sliver, overlapping the
    // front window's edge. Logical coordinates of the screenshot (1600 x 1000, text scale 1.64,
    // center zone 30%).
    {
        // The work area: the top bar's 30 px are not somewhere a window can show.
        const rectangle desk{0, 30, 1600, 970};
        const double s = 1.64;
        rectangle front{481, 87, 738, 760}, engram{512, 31, 694, 794};
        peek_request r; r.screen = desk; r.window_mode = true;
        r.strip_depth = peek_strip_depth * s; r.strip_length = peek_strip_length * s;
        auto a = window(front, 560, 1040, true);
        a.full_hint = 132 * s; a.minimum_hint = 48 * s;
        auto d = window(engram, 560, 1040);
        d.center_y1 = 30; d.center_y2 = 1000;
        d.full_hint = std::clamp(std::min(engram.width, engram.height) * .34 * s, 72 * s, 132 * s);
        d.minimum_hint = 48 * s;
        r.windows = {a, d};
        auto res = solve(r);
        auto hint_clear = [&] (const peek_result& p) {
            double rr = p.hint_diameter / 2;
            auto c = p.hint.center;
            double dx = std::max({front.x - c.x, 0.0, c.x - front.x - front.width});
            double dy = std::max({front.y - c.y, 0.0, c.y - front.y - front.height});
            return std::hypot(dx, dy) >= rr;
        };
        check(res[1].rung == peek_rung::full && res[1].outcome == peek_outcome::moved && hint_clear(res[1]) &&
            res[1].hint_diameter > 48 * s,
            "engram: Window mode makes full room for its hint, clear of the front window");
        // Always on: the sliver is already a strip, so it stays put.
        r.window_mode = false;
        auto always = solve(r);
        check(always[1].outcome == peek_outcome::visible && near(always[1].target, {}),
            "engram: outside Window mode the sliver is a peek strip; it does not move");
        // No room to make: the hint goes on the visible sliver, overlapping the front window's edge.
        r.window_mode = true;
        r.windows[1].zone_x1 = 840; r.windows[1].zone_x2 = 880;
        auto down = solve(r);
        check(down[1].rung == peek_rung::minimum && down[1].target.y > 0 && std::abs(down[1].target.x) <= 20,
            "engram: with no room sideways, it makes minimum room downward (decision 8: up is impossible)");
        r.windows[1].center_y1 = r.windows[1].center_y2 = engram.y + engram.height / 2;
        auto tight = solve(r);
        auto c = tight[1].hint.center;
        double radius = tight[1].hint_diameter / 2 * hint_pop_scale;
        check(tight[1].rung == peek_rung::peek && near(tight[1].target, {}) && c.y < front.y &&
            c.y - radius >= desk.y - 1e-6 && c.y + radius > front.y && c.x > front.x && c.x < front.x + front.width,
            "engram: with no room the hint sits on the sliver, overlapping the front window's top edge");
    }
    std::cout << passed << " passed, " << failed << " failed\n";
    return failed ? 1 : 0;
}
