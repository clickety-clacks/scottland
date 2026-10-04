#include "drag-presentation.hpp"
#include "rail-make-room.hpp"
#include "eased-move.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <functional>
#include <iterator>
#include <limits>
#include <random>
#include <utility>
#include <vector>

using scottland::rail::interval_t;
using scottland::rail::solver_t;
using scottland::rail::status_t;

static int passed = 0, failed = 0;

static void check(bool ok, const char *name)
{
    std::cout << (ok ? "PASS  " : "FAIL  ") << name << '\n';
    if (ok) ++passed; else ++failed;
}

struct count_only_range_t
{
    struct iterator
    {
        using value_type = interval_t;
        using difference_type = std::ptrdiff_t;
        using pointer = const interval_t*;
        using reference = const interval_t&;
        using iterator_category = std::forward_iterator_tag;
        reference operator*() const { throw 1; }
        iterator& operator++() { throw 1; }
        bool operator!=(const iterator&) const { throw 1; }
    };

    size_t reported = 0;
    mutable int size_calls = 0;
    size_t size() const { ++size_calls; return reported; }
    iterator begin() const { throw 1; }
    iterator end() const { throw 1; }
};

static bool legal(const std::vector<interval_t>& items, const std::vector<double>& offsets,
    double top, double bottom)
{
    if (items.size() != offsets.size()) return false;
    for (size_t i = 0; i < items.size(); ++i)
        if (items[i].lo + offsets[i] < top - 1e-8 || items[i].hi + offsets[i] > bottom + 1e-8)
            return false;
    return true;
}

static std::vector<interval_t> top_card_items() { return {{0, 24, 120}}; }

static bool sweep(const std::vector<interval_t>& items, double top, double bottom,
    double drag_height, const std::vector<int>& ys, int& direction_changes,
    int& over_one_pixel, double& largest_step)
{
    solver_t solver;
    if (!solver.begin(items)) return false;
    std::vector<int8_t> previous_directions;
    std::vector<double> previous_offsets;
    bool have_previous = false;
    direction_changes = over_one_pixel = 0;
    largest_step = 0;
    for (int y : ys)
    {
        auto status = solver.solve(y, y + drag_height, top, bottom);
        if (status == status_t::skipped || !legal(items, solver.shifts(), top, bottom)) return false;
        const auto& directions = solver.latched_directions();
        const auto& offsets = solver.shifts();
        if (have_previous)
        {
            bool flipped = directions != previous_directions;
            if (flipped) ++direction_changes;
            double step = 0;
            for (size_t i = 0; i < offsets.size(); ++i)
                step = std::max(step, std::abs(offsets[i] - previous_offsets[i]));
            if (!flipped)
            {
                largest_step = std::max(largest_step, step);
                if (step > 1 + 1e-8) ++over_one_pixel;
            }
        }
        previous_directions = directions;
        previous_offsets = offsets;
        have_previous = true;
    }
    return true;
}

struct fuzz_t
{
    long solves = 0, illegal = 0, disordered = 0, pushed_in = 0, unstable = 0, back_forth = 0;
    long intruded = 0, aim_broken = 0, overlap_though_aim_fits = 0, overlap_with_room = 0, overlap_past_half = 0;
    long kept_order_overlap = 0;  // P11 kept the order with a small overlap though another fit
    double worst_kept_order_overlap = 0;
    double worst_overlap_with_room = 0;
};

// Review fuzz (Fable's generator): 20,000 random rails, 30 pauses each, mostly small wobbles
// around a spot. P14: the item is where the user put it; every check measures against that
// exact interval.
static fuzz_t run_fuzz()
{
    fuzz_t f;
    std::mt19937 rng(5);
    std::uniform_real_distribution<double> u(0, 1);
    for (int trial = 0; trial < 20000; ++trial)
    {
        double top = 24, bottom = 24 + 600 + u(rng) * 800;
        int n = 1 + int(u(rng) * 7);
        std::vector<interval_t> w;
        double y = top + u(rng) * 60;
        for (int i = 0; i < n; ++i)
        {
            double h = u(rng) < .3 ? 48 : 96;
            double gap = u(rng) < .5 ? 1 + u(rng) * 3 : u(rng) * 160;
            y += gap;
            if (y + h > bottom) break;
            w.push_back({uint64_t(i + 1), y, y + h});
            y += h;
        }
        if (w.empty()) continue;
        double dh = u(rng) < .3 ? 48 : 96;
        solver_t solver;
        solver.begin(w);
        // Offset of each card when packed tight (already-overlapping cards stay so).
        std::vector<double> packed(w.size(), 0);
        for (size_t i = 1; i < w.size(); ++i)
            packed[i] = packed[i - 1] + (w[i - 1].hi - w[i - 1].lo) + std::min(1.0, w[i].lo - w[i - 1].hi);
        auto above_end = [&] (size_t k)
        {
            double end = top;
            for (size_t i = 0; i < k; ++i) end = std::max(end, top + packed[i] + (w[i].hi - w[i].lo));
            return end;
        };
        auto below_span = [&] (size_t k)
        {
            double span = 0;
            for (size_t i = k; i < w.size(); ++i) span = std::max(span, packed[i] - packed[k] + (w[i].hi - w[i].lo));
            return span;
        };
        // Split k fits around the item where it is: cards before k packed from the top end
        // above it, the rest packed to the bottom start below it (touching allowed).
        auto fits = [&] (size_t k, double lo, double hi)
        {
            return (!k || above_end(k) <= lo + 1e-6) && (k == w.size() || hi + below_span(k) <= bottom + 1e-6);
        };
        std::vector<int8_t> previous, two_ago;
        double recent[2] = {0, 0};
        double start = top + u(rng) * (bottom - top - dh);
        for (int step = 0; step < 30; ++step)
        {
            double lo = step % 6 == 0 ? top + u(rng) * (bottom - top - dh) : start + (u(rng) - .5) * 6;
            start = lo;
            lo = std::max(top, std::min(lo, bottom - dh));
            double hi = lo + dh, center = (lo + hi) / 2;
            auto status = solver.solve(lo, hi, top, bottom);
            ++f.solves;
            auto shifts = solver.shifts();
            auto sides = solver.latched_directions();
            double covered = 0;
            for (size_t i = 0; i < w.size(); ++i)
            {
                double a = w[i].lo + shifts[i], b = w[i].hi + shifts[i];
                if (a < top - 1e-6 || b > bottom + 1e-6) ++f.illegal;
                covered = std::max(covered, std::min(b, hi) - std::max(a, lo));
                // Where the user put it decides the order: a card the center is past stays on
                // its side of the item (once past by its P11 direction margin).
                const double margin = std::max(6.0, 0.10 * (w[i].hi - w[i].lo));
                if ((w[i].hi < center - margin && sides[i] > 0) || (w[i].lo > center + margin && sides[i] < 0))
                    ++f.aim_broken;
                if (i)
                {
                    double a0 = w[i - 1].lo + shifts[i - 1], b0 = w[i - 1].hi + shifts[i - 1];
                    if (a < a0 - 1e-6) ++f.disordered;
                    if (a - b0 < std::min(w[i].lo - w[i - 1].hi, 1.0) - 1e-6) ++f.pushed_in;
                }
            }
            size_t first = 0, last = w.size();
            for (size_t i = 0; i < w.size(); ++i)
            {
                if (w[i].hi < center) first = std::max(first, i + 1);
                if (w[i].lo > center) last = std::min(last, i);
            }
            bool aim_fits = false, any_fits = false;
            for (size_t k = 0; k <= w.size(); ++k)
            {
                bool ok = fits(k, lo, hi);
                any_fits |= ok;
                aim_fits |= ok && k >= first && k <= last;
            }
            if (status == status_t::clear && covered > 1e-6) ++f.intruded;
            // P11 may keep the current order on a nearly full rail with up to its direction
            // margin (at most 9.6 px for these cards) of overlap, rather than send a card
            // across the item for a fit within that margin; beyond that, never.
            if (status == status_t::overlap && aim_fits && covered > 9.6 + 1e-6) ++f.overlap_though_aim_fits;
            if (status == status_t::overlap && aim_fits)
            {
                ++f.kept_order_overlap;
                f.worst_kept_order_overlap = std::max(f.worst_kept_order_overlap, covered);
            }
            if (status == status_t::overlap && any_fits)
            {
                ++f.overlap_with_room;
                f.worst_overlap_with_room = std::max(f.worst_overlap_with_room, covered);
                if (covered > dh / 2 + 1e-6) ++f.overlap_past_half;
            }
            solver.solve(lo, hi, top, bottom);
            for (size_t i = 0; i < w.size(); ++i)
                if (std::abs(solver.shifts()[i] - shifts[i]) > 1e-9) { ++f.unstable; break; }
            if (step % 6 >= 2 && !two_ago.empty())
                for (size_t i = 0; i < w.size(); ++i)
                    if (two_ago[i] == sides[i] && previous[i] != sides[i] && previous[i] != 0)
                    {
                        ++f.back_forth;
                        if (std::getenv("FUZZ_WITNESS") && f.back_forth <= 5)
                        {
                            std::cout << "WITNESS back-and-forth card " << i << " rail " << top << "," << bottom
                                << " drops " << recent[0] << " " << recent[1] << " " << lo << " (h " << dh << ") cards";
                            for (auto& c : w) std::cout << " [" << c.lo << "," << c.hi << "]";
                            std::cout << " status " << int(status) << "\n";
                        }
                    }
            two_ago = step % 6 == 0 ? std::vector<int8_t>() : previous;
            previous = solver.latched_directions();
            recent[0] = recent[1];
            recent[1] = lo;
        }
    }
    return f;
}

int main()
{
    {
        solver_t solver;
        std::vector<interval_t> items{{0,100,196},{1,201,297}};
        solver.begin(items);
        check(solver.solve(10,50,0,400) == status_t::clear &&
            solver.shifts() == std::vector<double>({0,0}),
            "causal push stops before an unrelated close pair");
    }
    {
        solver_t solver;
        std::vector<interval_t> items{{0,100,196}};
        solver.begin(items);
        check(solver.solve(50,95,0,400) == status_t::clear && solver.shifts()[0] == 0,
            "a nearby widget outside CONTACT stays put");
    }
    {
        solver_t solver;
        std::vector<interval_t> items{{0,70,90}};
        solver.begin(items);
        auto first = solver.solve(59,69,0,100);
        const double before = solver.shifts()[0];
        auto sides = solver.latched_directions();
        auto second = solver.solve(60,70,0,100);
        const double after = solver.shifts()[0];
        check(first == status_t::clear && second == status_t::clear && before == 0 && after == 1 &&
            sides == solver.latched_directions(), "required contact ripple moves continuously by one pixel");
    }
    {
        // WG26 + P14: the rail spreads with the least total movement around the dropped card,
        // which stays exactly where it was put. Cards on both sides each move just clear.
        solver_t solver;
        std::vector<interval_t> items{{0,100,196},{1,250,346}};
        solver.begin(items);
        solver.solve(180,276,0,600);
        auto& s = solver.shifts();
        check(solver.status() == status_t::clear && std::abs(s[0] - (179 - 196)) < 1e-9 &&
            std::abs(s[1] - (277 - 250)) < 1e-9, "cards on both sides of the drop each move just clear of it");
    }
    {
        // A tight run below the drop moves as a unit the least that fits; a card far away
        // that nothing pushes stays home.
        solver_t solver;
        std::vector<interval_t> items{{0,200,296},{1,297,393},{2,394,490},{3,700,796}};
        solver.begin(items);
        solver.solve(150,246,0,900);
        auto& s = solver.shifts();
        check(std::abs(s[0] - 47) < 1e-9 && std::abs(s[1] - 47) < 1e-9 && std::abs(s[2] - 47) < 1e-9 &&
            s[3] == 0, "a packed run moves as a unit; an untouched card stays home");
    }
    {
        // Review F2 under P14: five cards packed at the top, a card dropped at the top with
        // its center on the top card. Either order matches that placement and the top card
        // can't fit above, so it crosses below with the rest; the drop stays at 32..128.
        solver_t solver;
        std::vector<interval_t> items;
        for (int i = 0; i < 5; ++i) items.push_back({(uint64_t)i, 24.0 + i * 97, 120.0 + i * 97});
        solver.begin(items);
        auto status = solver.solve(32, 128, 24, 696);
        bool clear = status == status_t::clear && legal(items, solver.shifts(), 24, 696);
        for (size_t i = 0; i < items.size(); ++i) clear &= items[i].lo + solver.shifts()[i] >= 129 - 1e-9;
        for (size_t i = 1; i < items.size(); ++i)
            clear &= items[i].lo + solver.shifts()[i] >= items[i - 1].hi + solver.shifts()[i - 1];
        check(clear && std::abs(solver.shifts()[0] - 105) < 1e-9,
            "a drop onto the card at a rail end goes first; the others make room below it");
    }
    {
        // Touching a pinned card (within the 1 px contact gap) is not overlap; clipping it
        // by 3 px with the drop's center below it keeps the aimed order: it stays above and
        // overlaps the drop by those 3 px rather than flying across it (for Mike to look at:
        // room exists below, but crossing would break the order the drop chose).
        solver_t touching;
        touching.begin(top_card_items());
        auto touch = touching.solve(120, 216, 24, 696);
        solver_t clipped;
        clipped.begin(top_card_items());
        auto clip = clipped.solve(117, 213, 24, 696);
        check(touch == status_t::clear && touching.shifts()[0] == 0 &&
            clip == status_t::overlap && clipped.shifts()[0] == 0 && clipped.latched_directions()[0] < 0,
            "touching a pinned card is clear; clipping it keeps the aimed order and overlaps");
    }
    {
        // Exactly full around where the card was put: six cards 1 px apart fit around the
        // drop at the one spot packing leaves for it; seven touching cards fill the rail and
        // then the cards overlap the drop (it doesn't move), staying on the rail.
        solver_t exact;
        std::vector<interval_t> six;
        for (int i = 0; i < 6; ++i) six.push_back({(uint64_t)i, 24.0 + i * 97, 120.0 + i * 97});
        exact.begin(six);
        const double lo = 24 + 3 * 97, height = 696 - 24 - (6 * 96 + 4) - 2;
        auto fits = exact.solve(lo, lo + height, 24, 696);
        solver_t full;
        std::vector<interval_t> seven;
        for (int i = 0; i < 7; ++i) seven.push_back({(uint64_t)i, 24.0 + i * 96, 120.0 + i * 96});
        full.begin(seven);
        check(fits == status_t::clear && full.solve(32, 128, 24, 696) == status_t::overlap &&
            legal(seven, full.shifts(), 24, 696), "overlap only when the rail is truly full");
    }
    {
        // Cards that already overlap (an earlier full rail) are never pulled further apart
        // than needed, and a full rail stays legal and ordered.
        solver_t solver;
        std::vector<interval_t> items{{0,0,60},{1,20,80},{2,40,100}};
        solver.begin(items);
        check(solver.solve(50,60,0,100) == status_t::overlap && legal(items, solver.shifts(),0,100),
            "a full rail of already overlapping cards stays legal");
    }
    {
        // Nested cards with a one-pixel wobble at the rail end: positions are continuous and
        // the last pixel is a touch, not an overlap.
        solver_t solver;
        std::vector<interval_t> items{{0,20,90},{1,40,50}};
        solver.begin(items);
        std::vector<std::vector<double>> offsets;
        std::vector<status_t> statuses;
        for (auto d : {std::pair<double,double>{19,29}, {20,30}, {19,29}})
        {
            statuses.push_back(solver.solve(d.first,d.second,0,100));
            offsets.push_back(solver.shifts());
        }
        check(offsets == std::vector<std::vector<double>>({{10,10},{10,10},{10,10}}) &&
            statuses == std::vector<status_t>(3, status_t::clear),
            "nested end clamp is continuous across a one-pixel wobble");
    }
    {
        // Brute force on small integer rails: around the drop where it was put, among the
        // orders that placement allows, no in-order placement on the pixel grid (1 px apart)
        // moves less than the solver.
        std::mt19937 random(17);
        bool optimal = true;
        int cases = 0;
        for (int trial = 0; trial < 3000 && optimal; ++trial)
        {
            const int length = 30 + int(random() % 20), n = 1 + int(random() % 3);
            std::vector<interval_t> items;
            int y = int(random() % 6);
            for (int i = 0; i < n; ++i)
            {
                int h = 3 + int(random() % 8);
                if (y + h > length) break;
                items.push_back({uint64_t(i), double(y), double(y + h)});
                y += h + int(random() % 6);
            }
            if (items.empty()) continue;
            int dh = 3 + int(random() % 8), dlo = int(random() % (length - dh + 1));
            const double center = dlo + dh / 2.0;
            solver_t solver;
            solver.begin(items);
            auto status = solver.solve(dlo, dlo + dh, 0, length);
            double cost = 0;
            for (double s : solver.shifts()) cost += s * s;
            const int m = int(items.size());
            double brute = std::numeric_limits<double>::infinity();
            for (int k = 0; k <= m; ++k)
            {
                bool allowed = true;
                for (int i = 0; i < m; ++i)
                    allowed &= !(items[i].hi < center && i >= k) && !(items[i].lo > center && i < k);
                if (!allowed) continue;
                std::vector<int> pos(m, 0);
                std::function<void(int, int)> go = [&] (int i, int from)
                {
                    if (i == m)
                    {
                        double c = 0;
                        for (int j = 0; j < m; ++j) c += std::pow(pos[j] - items[j].lo, 2);
                        brute = std::min(brute, c);
                        return;
                    }
                    const int h = int(items[i].hi - items[i].lo);
                    const int gap = i ? std::min(1, int(items[i].lo - items[i - 1].hi)) : 0;
                    for (int p = i ? from + gap : 0; p + h <= length; ++p)
                    {
                        if (i < k ? p + h > dlo - 1 : p < dlo + dh + 1) continue;
                        pos[i] = p;
                        go(i + 1, p + h);
                    }
                };
                go(0, 0);
            }
            if (!std::isfinite(brute)) continue;  // only touching or overlapping layouts exist
            ++cases;
            optimal &= status == status_t::clear && cost <= brute + 1e-6;
            if (!(status == status_t::clear && cost <= brute + 1e-6))
                std::cout << "INFO  brute mismatch trial " << trial << " cost " << cost << " grid " << brute << "\n";
        }
        check(optimal && cases > 1000, "least total movement around the placed card matches a brute-force grid search");
        std::cout << "INFO  brute-force cases=" << cases << "\n";
    }
    {
        solver_t solver;
        std::vector<interval_t> items{{0,100,196},{1,201,297}};
        solver.begin(items);
        solver.solve(10,50,0,400);
        auto directions = solver.latched_directions();
        for (size_t count : {scottland::rail::MAX_ACTORS + 1, size_t{1000000000}})
        {
            count_only_range_t huge{count};
            check(!solver.begin(huge) && huge.size_calls == 1 && solver.is_skipped() &&
                directions == solver.latched_directions(),
                "over-cap input checks its count once without iterating or changing direction state");
        }
    }
    {
        scottland::drag_presentation_t presentation;
        const std::vector<scottland::drag_actor_position_t> origins{{1,20,30},{2,40,50}};
        bool begun = presentation.begin(origins, scottland::rail::MAX_ACTORS);
        presentation.set_offset(0,0,15);
        presentation.set_offset(1,0,-4);
        bool live = begun && presentation.actor(0).origin_y + presentation.actor(0).dy == 45 &&
            presentation.actor(1).origin_y + presentation.actor(1).dy == 46;
        presentation.cancel();
        bool cancelled = presentation.empty() && origins[0].y == 30 && origins[1].y == 50;
        presentation.begin(origins, scottland::rail::MAX_ACTORS);
        presentation.set_offset(0,0,15);
        presentation.commit();
        presentation.set_target(0,20,45);
        presentation.finalize_targets();
        bool retained = presentation.is_committing() && presentation.actor(0).dy == 15;
        bool acknowledged = presentation.acknowledge(2,40,50) &&
            presentation.acknowledge(1,20,45) && presentation.empty();
        check(live, "generic drag presentation shows additive offsets from saved origins");
        check(cancelled, "generic drag presentation cancel clears offsets without changing origins");
        check(retained, "generic drag presentation holds offsets until real geometry applies");
        check(acknowledged, "generic drag presentation completes only after targets apply");
    }

    {
        // Port of rail.py's 300-rail randomized legal-interval sweep. Rails include nested and
        // overlapping intervals; every result stays in bounds and changes by at most one pixel
        // on frames without a latched direction change.
        std::mt19937_64 random(11);
        int frame_count = 0;
        double largest_step = 0;
        bool ok = true;
        const int lengths[] = {100,300,900};
        for (int trial = 0; trial < 300 && ok; ++trial)
        {
            double length = lengths[random() % 3];
            size_t n = 1 + random() % 7;
            std::vector<interval_t> items;
            for (size_t i = 0; i < n; ++i)
            {
                double height = 8 + (random() % 1000000) / 1000000.0 * (length * 0.7 - 8);
                double lo = (random() % 1000000) / 1000000.0 * (length - height);
                items.push_back({i,lo,lo+height});
            }
            double drag_height = 5 + (random() % 1000000) / 1000000.0 * (length * 0.3 - 5);
            std::vector<int> ys;
            for (int y = -int(drag_height) - 5; y <= int(length) + 5; ++y) ys.push_back(y);
            for (int y = int(length) + 5; y >= -int(drag_height) - 5; --y) ys.push_back(y);
            int flips = 0, jumps = 0;
            double worst = 0;
            ok &= sweep(items,0,length,drag_height,ys,flips,jumps,worst) && jumps == 0;
            frame_count += (int)ys.size();
            largest_step = std::max(largest_step,worst);
        }
        check(ok && frame_count > 250000 && largest_step <= 1 + 1e-8,
            "300 randomized legal rails pass every one-pixel sweep and rail-bound check");
        std::cout << "INFO  randomized frames=" << frame_count << " largest same-direction step="
            << largest_step << " px\n";
    }

    {
        std::vector<interval_t> ordinary;
        std::mt19937 random(3);
        const int heights[] = {90,90,120,48};
        for (int i = 0; i < 9; ++i)
        {
            double h = heights[random() % 4];
            double y = 60 + i * 140 + (int(random() % 41) - 20);
            ordinary.push_back({(uint64_t)i,y,y+h});
        }
        int flips = 0, jumps = 0;
        double worst = 0;
        std::vector<int> ys;
        for (int k = 30; k <= 1320; ++k) ys.push_back(k);
        for (int k = 1320; k >= 30; --k) ys.push_back(k);
        bool regular = sweep(ordinary,24,1416,90,ys,flips,jumps,worst) && jumps == 0;
        std::vector<interval_t> crowded;
        for (int i = 0; i < 14; ++i) crowded.push_back({(uint64_t)i,30.0+i*99,126.0+i*99});
        bool crowded_ok = sweep(crowded,24,1420,90,ys,flips,jumps,worst) && jumps == 0;

        solver_t far_solver;
        std::vector<interval_t> near_top{{0,40,136},{1,140,236},{2,300,396}};
        far_solver.begin(near_top);
        auto far = far_solver.solve(1300,1390,24,1416);  // on the rail, far below every card
        bool no_remote_push = far == status_t::clear && std::all_of(far_solver.shifts().begin(),
            far_solver.shifts().end(), [] (double shift) { return shift == 0; });

        check(regular && crowded_ok && no_remote_push,
            "nine-item and crowded-rail sweeps stay bounded, preserve order, and ignore remote items");
    }

    {
        std::vector<interval_t> items{{0,100,196},{1,201,297},{2,320,416},{3,450,546}};
        bool stable = true;
        for (double center : {250.0, 399.0})
        {
            solver_t solver;
            solver.begin(items);
            auto before_sides = solver.latched_directions();
            solver.solve(center-45,center+45,0,600);
            auto previous = solver.shifts();
            for (int k = 0; k < 400; ++k)
            {
                double jitter = (int(k % 7) - 3);
                solver.solve(center+jitter-45,center+jitter+45,0,600);
                const auto& sides = solver.latched_directions();
                if (k == 0) before_sides = sides;
                if (sides == before_sides)
                    for (size_t i = 0; i < previous.size(); ++i)
                        stable &= std::abs(solver.shifts()[i]-previous[i]) <= 6.0 + 1e-8;
                previous = solver.shifts();
            }
        }
        check(stable, "direction margin absorbs small center and rail-end pointer wobble");
    }

    {
        // Review fuzz (Fable's generator), P14.
        auto f = run_fuzz();
        check(f.solves == 600000 && !f.illegal && !f.disordered && !f.pushed_in && !f.unstable,
            "fuzz: 600,000 pause solves are legal, ordered, never push cards together, deterministic");
        check(f.intruded == 0, "fuzz: a clear result never reaches into the dropped card where it was put (P14)");
        check(f.aim_broken == 0, "fuzz: every card the drop's center is past stays on its side (order by placement)");
        check(f.overlap_though_aim_fits == 0, "fuzz: overlap only when no order the placement allows can fit (beyond the P11 margin)");
        check(f.overlap_past_half == 0, "fuzz: with room elsewhere, a pinned card overlaps at most half the drop");
        check(f.back_forth == 0, "fuzz: no card switches side and back on small re-pauses (P11)");
        std::cout << "INFO  fuzz: overlap reported with room elsewhere (a clipped pinned card) "
            << f.overlap_with_room << " of " << f.solves << ", worst " << f.worst_overlap_with_room << " px; "
            << "kept order with a small overlap though another order fit " << f.kept_order_overlap
            << ", worst " << f.worst_kept_order_overlap << " px\n";
    }

    {
        using scottland::motion::plan_eased_move;
        using scottland::motion::eased_move_t;
        const double cap = scottland::motion::AUTOMATIC_MAX_SPEED / 1000.0;
        auto monotone = [] (const eased_move_t& m)
        {
            double previous = m.from.y, sign = m.to.y > m.from.y ? 1 : -1;
            for (double e = 0; e <= m.duration + 1; e += 0.5)
            {
                double y = m.position(e).y;
                if ((y - previous) * sign < -1e-9 || (y - m.to.y) * sign > 1e-9) return false;
                previous = y;
            }
            return true;
        };

        auto small = plan_eased_move({0,0}, {0,97}, {0,0}, 190, 360, 0.32);
        check(small.duration >= 190 && small.duration <= 360 && small.velocity(0).y == 0 &&
            small.velocity(small.duration * 0.999).y < 0.01 && small.peak_speed() <= cap * 1.001 &&
            monotone(small), "a 97 px rail shift eases in and out under the shared speed cap");
        bool capped = true;
        for (double d : {5.0, 50.0, 240.0, 400.0, 900.0})
        {
            auto m = plan_eased_move({0,0}, {0,d}, {0,0}, 190, 360, 0.32);
            capped &= m.peak_speed() <= cap * 1.001 && monotone(m) && m.duration >= 190 &&
                (d > 240 || m.duration <= 360);
        }
        check(capped, "every distance stays under 1000 px/s; only long moves exceed 360 ms");

        // Retarget in mid-move: the new move starts at the old one's speed and never
        // overshoots its new target, even when that target is close.
        auto first = plan_eased_move({0,0}, {0,200}, {0,0}, 190, 360, 0.32);
        bool continuous = true;
        for (double frac : {0.2, 0.5, 0.8})
        {
            double at = first.duration * frac;
            auto v = first.velocity(at);
            auto here = first.position(at);
            for (double target : {here.y + 3, here.y + 60, 260.0})
            {
                auto next = plan_eased_move(here, {0, target}, v, 190, 360, 0.32);
                continuous &= std::abs(next.velocity(0).y - v.y) < 1e-9 && monotone(next) &&
                    next.peak_speed() <= cap * 1.001;
            }
            // Reversal: speed against the new direction is dropped, never carried the wrong way.
            auto back = plan_eased_move(here, {0, 0}, v, 190, 360, 0.32);
            continuous &= back.v0.y == 0 && monotone(back);
        }
        check(continuous, "retargets keep velocity, stay capped and never overshoot");
    }

    std::cout << "rail-make-room unit: " << passed << " passed, " << failed << " failed\n";
    return failed ? 1 : 0;
}
