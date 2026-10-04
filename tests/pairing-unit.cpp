// WK36 pairing fit math and WK35/WK36 hint-hold detection, without Wayfire.
#include "alt-mode.hpp"
#include "pairing.hpp"
#include <cmath>
#include <iostream>
#include <random>
#include <vector>
using namespace scottland::windowing;
int passed = 0, failed = 0;
void check(bool ok, const char* name) { std::cout << (ok ? "PASS  " : "FAIL  ") << name << '\n';
    if (ok) ++passed; else ++failed; }
bool near(double a, double b, double e = 1e-6) { return std::abs(a - b) <= e; }

int main()
{
    const double gap = 32.0 / 3.0, pad = 32.0 / 3.0 + 5; // HALO and WP7's SCREEN_PADDING
    const rectangle screen{0, 0, 1600, 1000};
    auto left_edge = [] (pair_size s, pair_layout f) { return f.left.x - s.width * f.scale / 2; };
    auto right_edge = [] (pair_size s, pair_layout f) { return f.right.x + s.width * f.scale / 2; };

    // Fits at 100% with the halo gap: centered as a unit, both on the center line.
    pair_size a{600, 400}, b{500, 300};
    auto f = fit_pair(a, b, screen, gap, pad);
    check(f.scale == 1 && near(f.gap, gap), "pair that fits keeps 100% and the halo gap");
    check(near(f.left.y, 500) && near(f.right.y, 500), "both windows are centered on the screen's center line");
    check(near((left_edge(a, f) + right_edge(b, f)) / 2, 800), "pair is centered horizontally as a unit");
    check(near(f.right.x - b.width / 2 - (f.left.x + a.width / 2), gap), "halo gap separates the two footprints");
    check(near(f.margin, (1600 - 1100 - gap) / 2), "the rest of the width is split evenly at both edges");

    // The gap gives way first (P7), then the edge padding (WP7), before any scaling.
    pair_size c{800, 500}, c2{760, 500};
    f = fit_pair(c, c2, screen, gap, pad);
    check(f.scale == 1 && near(f.gap, 1600 - 2 * pad - 1560) && f.gap < gap && near(f.margin, pad),
        "tight pair shrinks the gap before the edge padding, still at 100%");
    pair_size d{800, 500}, d2{790, 500};
    f = fit_pair(d, d2, screen, gap, pad);
    check(f.scale == 1 && f.gap == 0 && near(f.margin, 5), "tighter pair gives up the padding next, still at 100%");
    pair_size e{800, 800};
    f = fit_pair(e, e, screen, gap, pad);
    check(f.scale == 1 && f.gap == 0 && near(f.margin, 0), "exactly the screen's width stays at 100%, edge to edge");

    // Too wide: one shared factor, just enough to go edge to edge.
    pair_size wide{1200, 700}, other{1000, 500};
    f = fit_pair(wide, other, screen, gap, pad);
    check(near(f.scale, 1600.0 / 2200), "too wide: scaled by one shared factor just enough to fit");
    check(near(left_edge(wide, f), 0) && near(right_edge(other, f), 1600) && f.gap == 0,
        "scaled pair spans the screen edge to edge with no gap");
    check(wide.width * f.scale > other.width * f.scale, "the larger window keeps the larger share");
    check(near(f.left.y, 500) && near(f.right.y, 500), "scaled windows stay on the center line");

    // Heights never force scaling; the usable area may be offset (a bar at the top).
    pair_size tall{600, 2400};
    f = fit_pair(tall, b, {0, 30, 1600, 970}, gap, pad);
    check(f.scale == 1 && near(f.left.y, 30 + 485) && near(f.right.y, 515),
        "a window taller than the screen stays full size, centered on the work area's center line");
    f = fit_pair(a, b, {2560, 0, 1600, 1000}, gap, pad);
    check(near((left_edge(a, f) + right_edge(b, f)) / 2, 2560 + 800), "pair centers on the area it is given");

    // The smallest supported scale bounds absurd sizes; the overflow stays symmetric.
    pair_size huge{40000, 1000};
    f = fit_pair(huge, huge, screen, gap, pad);
    check(f.scale == pair_min_scale && f.margin < 0 &&
        near((left_edge(huge, f) + right_edge(huge, f)) / 2, 800), "scale never drops below 5%; overflow stays centered");

    // Properties over random sizes and screens.
    std::mt19937 random(36);
    std::uniform_real_distribution<double> size(40, 3000), screen_width(640, 5120);
    bool ordered = true, centered = true, inside = true, minimal = true, never_up = true, gap_rule = true;
    for (int i = 0; i < 20000; ++i)
    {
        pair_size l{std::round(size(random)), std::round(size(random))}, r{std::round(size(random)), std::round(size(random))};
        rectangle area{std::round(size(random)), 24, std::round(screen_width(random)), 1000};
        f = fit_pair(l, r, area, gap, pad);
        double total = l.width + r.width;
        ordered &= f.left.x < f.right.x &&
            near((f.right.x - r.width * f.scale / 2) - (f.left.x + l.width * f.scale / 2), f.gap, 1e-6);
        centered &= near((left_edge(l, f) + right_edge(r, f)) / 2, area.x + area.width / 2, 1e-6);
        never_up &= f.scale <= 1 && f.scale >= pair_min_scale;
        if (f.scale > pair_min_scale)
            inside &= left_edge(l, f) >= area.x - 1e-6 && right_edge(r, f) <= area.x + area.width + 1e-6 && f.gap >= 0;
        // 100% exactly when the widths fit; otherwise exactly edge to edge (just enough).
        minimal &= total <= area.width ? f.scale == 1 :
            (f.scale == pair_min_scale || near(total * f.scale, area.width, 1e-6));
        gap_rule &= total + gap + 2 * pad <= area.width ? near(f.gap, gap) : f.gap < gap + 1e-9;
    }
    check(ordered, "20000 random pairs keep their order with the chosen gap between them");
    check(centered, "20000 random pairs are centered as a unit");
    check(inside, "20000 random pairs stay inside the usable width without overlapping");
    check(never_up, "scale is never above 100% (sizes are never scaled up)");
    check(minimal, "100% whenever the widths fit; otherwise scaled exactly to the width");
    check(gap_rule, "the full halo gap is kept whenever it fits with the padding, and never exceeded");

    // Hint holds (WK35/WK36). A press acts immediately; only a held press past the delay pairs.
    alt_mode mode; uint64_t focus = 1, selected = 0; std::vector<destination> moves;
    std::vector<std::pair<uint64_t, uint64_t>> pairs; std::vector<uint64_t> solos, flashes;
    mode.select = [&] (uint64_t id, bool) { selected = id; focus = id; };
    mode.move = [&] (uint64_t, destination d) { moves.push_back(d); };
    mode.close = [] (uint64_t) {};
    mode.hint_action = [&] (uint64_t id) { flashes.push_back(id); };
    mode.focused = [&] () { return focus; };
    mode.pair = [&] (uint64_t held, uint64_t partner) { pairs.emplace_back(held, partner); };
    mode.solo = [&] (uint64_t id) { solos.push_back(id); };
    const std::vector<hint_entry> entries{{1, 0, zone::center, false}, {2, 1, zone::center, false},
        {3, 2, zone::left_rail, true}};
    auto reset = [&] (uint64_t focused) { mode.end(); focus = focused; selected = 0; moves.clear();
        pairs.clear(); solos.clear(); flashes.clear(); mode.begin(entries, focused); };
    using D = destination;

    reset(1); mode.letter('s', 1000);
    check(selected == 2 && moves.empty() && mode.hold_waiting(), "held unfocused hint selects at once (WK6) and waits");
    check(!mode.hold_due(1499) && pairs.empty(), "no pairing before the 500 ms default hold");
    check(mode.hold_due(1500) && pairs == std::vector<std::pair<uint64_t, uint64_t>>{{2, 1}} && !mode.hold_waiting(),
        "holding past the delay pairs the held window with the window focused before the press");
    check(flashes.back() == 2, "the hold pulses the held hint (WK33)");
    check(!mode.hold_due(5000) && pairs.size() == 1, "one hold pairs once");

    reset(1); mode.letter('s', 1000); mode.release('s', 1200);
    check(!mode.hold_due(2000) && pairs.empty() && selected == 2, "a tap released before the delay never pairs");
    reset(1); mode.letter('s', 1000); mode.release('a', 1100);
    check(mode.hold_due(1500) && pairs.size() == 1, "releasing another key does not end the hold");
    reset(1); mode.hold_delay = 800; mode.letter('s', 1000);
    check(!mode.hold_due(1700) && mode.hold_due(1800) && pairs.size() == 1, "the hold delay is a setting");
    mode.hold_delay = 500;

    // WK35 (Mike, 2026-10-04): the focused window's press acts on release, so a hold never moves it first.
    reset(1); mode.letter('a', 1000);
    check(moves.empty() && mode.hold_waiting(), "focused hint press waits for its release");
    mode.release('a', 1100);
    check(moves == std::vector<D>{D::periphery} && flashes == std::vector<uint64_t>{1} && !mode.hold_due(2000),
        "focused hint tap acts on release (WK6 next zone), and is not a hold");
    reset(1); mode.letter('a', 1000);
    check(!mode.hold_due(1499) && mode.hold_due(1500) && solos == std::vector<uint64_t>{1} && pairs.empty() && moves.empty(),
        "holding the focused window's hint reaches the WK35 solo hook without moving it first");
    mode.release('a', 1700);
    check(moves.empty(), "releasing after a focused hold does not also tap");
    mode.letter('a', 1800); mode.release('a', 1850);
    check(moves == std::vector<D>{D::periphery}, "a quick tap after a focused hold is a tap, not a double-tap");
    reset(1); mode.letter('a', 1000); mode.release('a', 1080); mode.letter('a', 1200);
    check(moves == std::vector<D>{D::periphery, D::widget} && !mode.hold_waiting(),
        "focused double-tap: first tap acts on release, the repeat acts at once to the rail");
    reset(1); mode.letter('a', 1000); mode.interrupt();
    check(moves == std::vector<D>{D::periphery} && !mode.hold_due(2000), "another key acts a waiting focused tap first");
    reset(1); mode.letter('a', 1000); mode.letter('s', 1100);
    check(moves == std::vector<D>{D::periphery} && selected == 2, "rolling onto another hint acts the focused tap, then the next");
    reset(1); mode.letter('a', 1000); mode.cancel_pending(); mode.release('a', 1100);
    check(moves.empty() && !mode.hold_due(2000) && solos.empty(), "Esc drops a waiting focused tap");
    reset(1); mode.letter('a', 1000); mode.interrupt(); mode.end(); mode.release('a', 1100);
    check(moves == std::vector<D>{D::periphery}, "Alt release before the key acts the focused tap once");
    reset(1); mode.letter('a', 1000);
    check(mode.hold_remaining(1000) == 500 && mode.hold_remaining(1300) == 200 && mode.hold_remaining(1600) == 0 &&
        mode.hold_remaining(900) == 500, "hold timing re-arms for the remainder, never past the delay");

    reset(1); mode.letter('s', 1000); mode.interrupt();
    check(!mode.hold_due(2000) && pairs.empty(), "another key during the hold cancels it");
    reset(1); mode.letter('s', 1000); mode.end();
    check(!mode.hold_due(2000) && pairs.empty(), "Esc or Alt release during the hold cancels it");
    reset(1); mode.letter('s', 1000); mode.letter('a', 1100);
    check(!mode.hold_due(1599) && mode.hold_due(1600) && pairs == std::vector<std::pair<uint64_t, uint64_t>>{{1, 2}},
        "a second hint replaces the first hold and is timed from its own press");
    reset(1); mode.letter('s', 1000); mode.tab(false);
    check(!mode.hold_due(2000) && pairs.empty(), "Tab during the hold cancels it");
    reset(1); mode.letter('s', 1000); mode.refresh({{1, 0, zone::center, false}});
    check(!mode.hold_due(2000) && pairs.empty(), "a held window that closes cancels its hold");
    reset(0); focus = 0; mode.select = [&] (uint64_t id, bool) { selected = id; };
    mode.letter('s', 1000);
    check(mode.hold_due(1500) && pairs.empty() && solos.empty(), "with nothing focused a hold pairs nothing");
    mode.select = [&] (uint64_t id, bool) { selected = id; focus = id; };

    // A double-tap press already acted; after a hold, the next press is never its double-tap.
    reset(1); mode.double_tap_delay = 300; mode.letter('s', 1000); mode.release('s', 1050); mode.letter('s', 1100);
    check(moves == std::vector<D>{D::widget} && !mode.hold_waiting() && !mode.hold_due(2000) && pairs.empty(),
        "a double-tap press never becomes a hold");
    reset(1); mode.double_tap_delay = 3000; mode.letter('s', 1000); mode.hold_due(1500);
    mode.release('s', 1600); mode.letter('s', 1700); mode.release('s', 1750);
    check(moves == std::vector<D>{D::periphery}, "after a hold the next press cycles from the pair, never a double-tap");
    mode.double_tap_delay = 300;

    // Widgets pair as their app; multi-letter hints hold on their final letter.
    reset(1); mode.letter('d', 1000);
    check(mode.hold_due(1500) && pairs == std::vector<std::pair<uint64_t, uint64_t>>{{3, 1}},
        "holding an unfocused widget's hint pairs its app");
    std::vector<hint_entry> many = entries; many.push_back({27, 26, zone::center, false});
    mode.end(); focus = 1; pairs.clear(); mode.begin(many, 1);
    mode.letter('s', 1000); mode.release('s', 1050); mode.letter('a', 1100); mode.release('s', 1150);
    check(!mode.hold_due(1599) && mode.hold_due(1600) && pairs == std::vector<std::pair<uint64_t, uint64_t>>{{27, 1}},
        "a two-letter hint holds on its final letter, timed from that press");
    mode.end(); pairs.clear(); mode.begin(many, 1); mode.letter('s', 1000);
    check(!mode.hold_waiting() && !mode.hold_due(3000) && pairs.empty(), "an incomplete prefix never holds");

    std::cout << passed << " passed, " << failed << " failed\n";
    return failed ? 1 : 0;
}
