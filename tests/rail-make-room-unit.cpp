#include "drag-presentation.hpp"
#include "rail-make-room.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <iostream>
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
        solver_t solver;
        std::vector<interval_t> items{{0,0,60},{1,20,80},{2,40,100}};
        solver.begin(items);
        check(solver.solve(50,60,0,100) == status_t::overlap &&
            solver.shifts() == std::vector<double>({0,0,0}) && legal(items, solver.shifts(),0,100),
            "a full rail retains a legal ordered overlap when no chain space remains");
    }
    {
        solver_t solver;
        std::vector<interval_t> items{{0,150,200},{1,201,251},{2,252,292}};
        solver.begin(items);
        check(solver.solve(100,200,0,300) == status_t::overlap &&
            solver.shifts() == std::vector<double>({8,8,8}) && legal(items,solver.shifts(),0,300) &&
            items.back().hi + solver.shifts().back() == 300,
            "a crowded causal chain advances only until its last member reaches the rail end");
    }
    {
        solver_t solver;
        std::vector<interval_t> items{{0,20,90},{1,40,50}};
        solver.begin(items);
        std::vector<status_t> statuses;
        std::vector<std::vector<double>> offsets;
        for (auto d : {std::pair<double,double>{19,29}, {20,30}, {19,29}})
        {
            statuses.push_back(solver.solve(d.first,d.second,0,100));
            offsets.push_back(solver.shifts());
        }
        check(offsets == std::vector<std::vector<double>>({{10,10},{10,10},{10,10}}) &&
            statuses == std::vector<status_t>({status_t::clear,status_t::overlap,status_t::clear}),
            "nested different-height end clamp is continuous across a one-pixel wobble");
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
        far_solver.begin(ordinary);
        auto far = far_solver.solve(-500,-410,24,1416);
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

    std::cout << "rail-make-room unit: " << passed << " passed, " << failed << " failed\n";
    return failed ? 1 : 0;
}
