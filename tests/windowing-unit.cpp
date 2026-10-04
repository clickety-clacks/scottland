#include "placement.hpp"
#include "declutter.hpp"
#include "alt-mode.hpp"
#include "hint-style.hpp"
#include "widget-spring.hpp"
#include <array>
#include <chrono>
#include <set>
#include <tuple>
#include <cmath>
#include <iostream>
#include <random>
using namespace scottland::windowing;
int passed = 0, failed = 0;
void check(bool ok, const char* name) { std::cout << (ok ? "PASS  " : "FAIL  ") << name << '\n';
    if (ok) ++passed; else ++failed; }
bool near(point a, point b, double epsilon = 1e-6) { return std::hypot(a.x - b.x, a.y - b.y) < epsilon; }
double score(point p, double w, double h, const std::vector<rectangle>& others)
{ double s = 0; for (auto r : others) s += overlap({p.x-w/2,p.y-h/2,w,h}, r); return s; }
int main()
{
    for (double amount : {0.0, 0.01, 0.04, 0.1})
    {
        std::vector<double> path;
        for (int i = 0; i <= 1000; ++i) path.push_back(scottland::widget_spring(i / 1000.0, amount));
        auto peak = std::max_element(path.begin(), path.end());
        check(path.front() == 0 && path.back() == 1, "widget spring endpoints exact");
        check(std::is_sorted(path.begin(), peak + 1) && std::is_sorted(peak, path.end(), std::greater<double>()),
            "widget spring has only one peak and no wobble");
        check(std::abs(*peak - 1 - amount) < 1e-8, "widget bounce option controls overshoot; zero disables it");
    }
    rectangle region{0,0,500,400};
    check(near(place_rectangle(100,80,region,{}, {150,90}), {150,90}), "empty region retains preferred position");
    check(near(place_rectangle(100,80,region,{}, {-100,-100}), {50,40}), "screen edges constrain a new position");
    check(near(place_rectangle(700,500,region,{}, {10,10}), {250,200}), "oversized rectangle stays full size and centers");
    std::vector<rectangle> obstacles{{100,50,200,200}};
    point memory{160,90};
    check(near(place_rectangle(100,80,region,obstacles, {400,300}, memory), memory), "occupied memory is authoritative");
    check(near(place_rectangle(100,80,region,obstacles, {400,300}, point{-20,40}), {-20,40}), "remembered coordinates are exact");
    auto p = place_rectangle(100,80,region,obstacles,{180,130});
    check(score(p,100,80,obstacles) == 0, "contended placement finds free space");
    check(near(p,{50,130}), "free-space tie follows closest preferred position");
    check(overlap({0,0,100,100},{50,50,100,100}) == 2500, "intersection area");
    check(overlap({0,0,100,100},{100,0,100,100}) == 0, "touching edges do not overlap");
    check(largest_opening({0,0,100,500},{{0,100,100,100},{0,150,100,100}}) == 250,
        "side free space unions overlapping obstacles");
    check(largest_opening({0,0,100,500},{{200,0,100,500}}) == 500, "other-side obstacles do not block openings");
    check(largest_opening({0,0,100,500},{{0,-20,100,600}}) == 0, "fully blocked side has no opening");
    check(largest_opening({0,0,100,500},{{0,40,100,20},{0,140,100,360}}) == 80,
        "largest contiguous opening rather than total free area");
    std::mt19937 rng(42); bool optimum = true, deterministic = true;
    for (int trial = 0; trial < 100; ++trial)
    {
        std::vector<rectangle> others;
        for (int i = 0; i < 7; ++i) others.push_back({double(rng()%450),double(rng()%350),80,100});
        point pref{double(rng()%500),double(rng()%400)};
        auto actual = place_rectangle(80,60,region,others,pref,{},0);
        deterministic &= near(actual,place_rectangle(80,60,region,others,pref,{},0));
        for (int x = 40; x <= 460; x+=10) for (int y=30;y<=370;y+=10)
            optimum &= score(actual,80,60,others) <= score({double(x),double(y)},80,60,others)+1e-6;
    }
    check(optimum,"100 randomized placements beat every point in a dense reference grid");
    check(deterministic,"placement is deterministic");
    auto pole = visible_label({0,0,400,300}, {0,0,500,400}, {{100,0,300,300}});
    check(pole.clearance > 49.4 && pole.clearance <= 50 && pole.center.x <= 50.6,
        "visible-region circle fits the exposed strip rather than the covered center");
    auto split = visible_label({0,0,400,300}, region, {{80,0,240,300},{80,0,240,300}});
    check(split.clearance > 39.4 && split.clearance <= 40,
        "union subtraction handles duplicate blockers and disconnected regions");
    auto diagonal = visible_label({0,0,200,200}, region, {{100,100,100,100}});
    check(diagonal.clearance > 58 && diagonal.clearance < 59,
        "circle search uses Euclidean corner clearance in a non-convex visible region");
    auto covered = visible_label({100,100,80,60}, region, {{0,0,500,400}});
    check(covered.clearance == 0,
        "a fully hidden rectangle has no visible interior before window movement");
    auto tiny_deadline = std::chrono::steady_clock::now() + std::chrono::nanoseconds(1);
    bool deadline_hit = false, repeat_deadline_hit = false;
    std::vector<exposure_window> easy_deadline_case{{{150,100,220,160},72,48,{},false,
        {23,-17},{23,-17}}};
    auto deadline_result = expose_window_hints(easy_deadline_case, region, {}, tiny_deadline, &deadline_hit);
    auto repeated_deadline_result = expose_window_hints(easy_deadline_case, region, {}, tiny_deadline,
        &repeat_deadline_hit);
    std::cout << "tiny-deadline: hit=" << deadline_hit << '/' << repeat_deadline_hit
        << " size=" << deadline_result.at(0).diameter << '/' << repeated_deadline_result.at(0).diameter
        << " offset=" << deadline_result.at(0).offset.x << ',' << deadline_result.at(0).offset.y
        << " spot=" << deadline_result.at(0).spot.center.x << ',' << deadline_result.at(0).spot.center.y
        << " repeat-spot=" << repeated_deadline_result.at(0).spot.center.x << ','
        << repeated_deadline_result.at(0).spot.center.y << '\n';
    check(deadline_hit && repeat_deadline_hit && deadline_result.size() == 1 &&
        deadline_result[0].diameter >= 48 && deadline_result[0].diameter <= 72 &&
        std::isfinite(deadline_result[0].spot.center.x) &&
        near(deadline_result[0].offset, repeated_deadline_result[0].offset) &&
        near(deadline_result[0].spot.center, repeated_deadline_result[0].spot.center) &&
        near(deadline_result[0].offset,{23,-17}) &&
        near(repeated_deadline_result[0].offset,{23,-17}) &&
        near(deadline_result[0].spot.center,{283,163}),
        "a forced tiny solve budget returns a finite, stable held target");
    auto stale_budget = std::chrono::steady_clock::now() - std::chrono::milliseconds(1);
    // A widget overlaps a corner, so the window is covered and keeps its label (WK31).
    std::vector<exposure_window> easing_deadline_case{{{150,100,220,160},72,48,{{360,250,100,40}},false,
        {23,-17},{80,24},{35,0},90}};
    auto easing_deadline = expose_window_hints(easing_deadline_case, region, {}, stale_budget);
    check(near(easing_deadline[0].offset,{80,24}) &&
        near(easing_deadline[0].spot.center,{375,204}),
        "a deadline holds the prior target for one frame while unfinished work retries");
    auto uncovered_deadline_case = easing_deadline_case;
    uncovered_deadline_case[0].fixed_foreground.clear();
    auto uncovered_deadline = expose_window_hints(uncovered_deadline_case, region, {}, stale_budget);
    check(near(uncovered_deadline[0].offset,{80,24}) &&
        near(uncovered_deadline[0].spot.center,{340,204}),
        "WK31: a deadline holds an uncovered window's target with its hint at its center");
    std::vector<exposure_window> no_room_deadline_case{{{150,100,220,160},72,48,
        {{140,90,240,180}},true}};
    auto no_room_deadline = std::chrono::steady_clock::now() + std::chrono::nanoseconds(1);
    bool no_room_deadline_hit = false;
    auto no_room_result = expose_window_hints(no_room_deadline_case, region, {},
        no_room_deadline, &no_room_deadline_hit);
    auto no_room_center = point{260,180};
    check(no_room_deadline_hit && no_room_result.size() == 1 &&
        no_room_result[0].diameter == 48 && near(no_room_result[0].spot.center, no_room_center),
        "an expired search with no checked opening still returns a centered minimum-size hint");
    bool circle_optimum = true;
    for (int trial = 0; trial < 30; ++trial)
    {
        std::vector<rectangle> blockers;
        for (int i = 0; i < 4; ++i) blockers.push_back({double(rng()%300),double(rng()%240),100,80});
        auto result = visible_label({0,0,400,300},region,blockers);
        for (int x = 0; x <= 400; x += 5) for (int y = 0; y <= 300; y += 5)
        {
            double d = std::min({double(x),400.0-x,double(y),300.0-y});
            for (auto o : blockers)
            {
                double dx = std::max({o.x-x,0.0,x-o.x-o.width});
                double dy = std::max({o.y-y,0.0,y-o.y-o.height});
                d = std::min(d,std::hypot(dx,dy));
            }
            circle_optimum &= result.clearance + .51 >= d;
        }
    }
    check(circle_optimum,"visible label beats a dense independent circle-clearance reference grid");
    rectangle desktop{0,0,1280,720};
    auto enough = expose_window_hints({{{320,160,700,440},132,48},{{100,160,700,440},132,48}},desktop);
    check(near(enough[0].offset,{}) && near(enough[1].offset,{}) && enough[1].diameter == 132,
        "a rear window with an already wide enough left strip stays exactly put");
    auto narrow = expose_window_hints({{{280,160,700,440},132,48},{{200,160,700,440},132,48}},desktop);
    const double upgrade_travel_limit = std::max(12.0, 132.0 * .2);
    const bool intermediate_upgrade = narrow[1].diameter > 48.01 && narrow[1].diameter < 131.99;
    std::cout << "intermediate-upgrade: size=" << narrow[1].diameter << " offset="
        << narrow[1].offset.x << ',' << narrow[1].offset.y << " travel-limit="
        << upgrade_travel_limit << '\n';
    check(near(narrow[0].offset,{}) && intermediate_upgrade &&
        std::hypot(narrow[1].offset.x,narrow[1].offset.y) <= upgrade_travel_limit + .1 &&
        narrow[1].spot.clearance + .25 >= narrow[1].diameter*1.06/2,
        "an intermediate-size hint upgrade stays within its travel cap and has checked clearance");
    check(visible_clearance(narrow[1].spot.center,
        {200+narrow[1].offset.x,160,700,440},desktop,{{280,160,700,440}}) >=
            narrow[1].diameter*1.06/2,
        "the narrow-strip hint circle stays visible at its checked size");
    auto same = expose_window_hints({{{280,160,700,440},132,48},{{200,160,700,440},132,48}},desktop);
    check(near(narrow[0].offset,same[0].offset) && near(narrow[1].offset,same[1].offset),
        "least-exposure displacement is deterministic");
    auto retained_start = expose_window_hints({{{240,160,700,440},132,48},
        {{200,160,700,440},132,48}},desktop);
    check(retained_start[1].offset.x < -10 && retained_start[1].diameter >= 48,
        "a covered minimum-size hint opens with the smallest necessary displacement");
    auto half_incumbent = retained_start[1].offset;
    half_incumbent.x /= 2;
    exposure_window incumbent_window{{200,160,700,440},132,48,{},false,
        half_incumbent,retained_start[1].offset};
    incumbent_window.branch_owner = retained_start[1].branch_owner;
    incumbent_window.branch_axis = retained_start[1].branch_axis;
    incumbent_window.branch_sign = retained_start[1].branch_sign;
    std::vector<exposure_window> incumbent_case{{{240,160,700,440},132,48}, incumbent_window};
    auto incumbent = expose_window_hints(incumbent_case,desktop);
    check(near(incumbent[0].offset,retained_start[0].offset) &&
        near(incumbent[1].offset,retained_start[1].offset),
        "a feasible owner/axis/direction branch stays while its least offset is recomputed from true geometry");
    const point retained_label_offset{
        retained_start[1].spot.center.x - retained_start[1].offset.x - 550,
        retained_start[1].spot.center.y - retained_start[1].offset.y - 380};
    exposure_window retained_label_window{{200,160,700,440},132,48,{},false,
        retained_start[1].offset,retained_start[1].offset,retained_label_offset,
        retained_start[1].spot.clearance};
    retained_label_window.branch_owner = retained_start[1].branch_owner;
    retained_label_window.branch_axis = retained_start[1].branch_axis;
    retained_label_window.branch_sign = retained_start[1].branch_sign;
    auto retained_label = expose_window_hints({{{240,160,700,440},132,48},
        retained_label_window},desktop);
    const point rest_label{550 + retained_label_offset.x,380 + retained_label_offset.y};
    const double rest_clearance=visible_clearance(rest_label,{200,160,700,440},desktop,
        {{240,160,700,440}});
    std::cout << "retained-label: expected=" << rest_clearance << " reported="
        << retained_label[1].retained_clearance << " target=" << retained_label[1].offset.x << ','
        << retained_label[1].offset.y << " spot=" << retained_label[1].spot.center.x << ','
        << retained_label[1].spot.center.y << " size=" << retained_label[1].diameter << '\n';
    check(std::abs(retained_label[1].retained_clearance-rest_clearance)<.01 &&
        retained_label[1].diameter>=48 &&
        std::isfinite(retained_label[1].spot.center.x) &&
        std::isfinite(retained_label[1].spot.center.y),
        "the incumbent badge point is rechecked from true geometry without feeding back its old transform");
    incumbent_window.incumbent_offset = retained_start[1].offset;
    incumbent_window.target_offset = retained_start[1].offset;
    double previous_ray_length = std::hypot(retained_start[1].offset.x,retained_start[1].offset.y);
    bool ray_contracts = true;
    exposure_result ray_result = retained_start[1];
    int ray_growth_at = -1;
    double max_ray_step = 0;
    int ray_branch_changes = 0;
    for (int front_x = 241; front_x <= 270; ++front_x)
    {
        std::vector<exposure_window> relaxed_case{{{double(front_x),160,700,440},132,48,{},true},
            incumbent_window};
        auto relaxed = expose_window_hints(relaxed_case,desktop);
        ray_result = relaxed[1];
        const double length = std::hypot(ray_result.offset.x,ray_result.offset.y);
        max_ray_step = std::max(max_ray_step,std::hypot(
            ray_result.offset.x-incumbent_window.target_offset.x,
            ray_result.offset.y-incumbent_window.target_offset.y));
        ray_branch_changes += ray_result.branch_owner != incumbent_window.branch_owner ||
            ray_result.branch_axis != incumbent_window.branch_axis ||
            ray_result.branch_sign != incumbent_window.branch_sign;
        if (length > previous_ray_length + .01 && ray_growth_at < 0)
        {
            ray_growth_at = front_x;
            std::cout << "retained-ray growth at=" << front_x << " target="
                << ray_result.offset.x << ',' << ray_result.offset.y << " axis="
                << ray_result.branch_axis << " sign=" << ray_result.branch_sign << '\n';
        }
        ray_contracts &= length <= previous_ray_length + .01;
        previous_ray_length = length;
        incumbent_window.incumbent_offset = ray_result.offset;
        incumbent_window.target_offset = ray_result.offset;
        incumbent_window.branch_owner = ray_result.branch_owner;
        incumbent_window.branch_axis = ray_result.branch_axis;
        incumbent_window.branch_sign = ray_result.branch_sign;
    }
    std::cout << "retained-ray: start=" << std::hypot(retained_start[1].offset.x,
        retained_start[1].offset.y)
        << " end=" << std::hypot(ray_result.offset.x,ray_result.offset.y)
        << " growth-at=" << ray_growth_at << " max-step=" << max_ray_step
        << " branch-changes=" << ray_branch_changes << " branch=" << ray_result.branch_owner << '\n';
    check(ray_contracts && max_ray_step <= 4 && ray_branch_changes == 0 &&
        near(ray_result.offset,{}) && ray_result.branch_owner == 1 &&
        ray_result.branch_axis == retained_start[1].branch_axis &&
        ray_result.branch_sign == retained_start[1].branch_sign,
        "a 1 px obstruction retreat shrinks the least offset smoothly to exact zero on the retained way");
    auto label_offset = retained_label_offset;
    std::vector<exposure_window> clear_after_drag{{{900,160,700,440},132,48},
        {{200,160,700,440},132,48,{},false,retained_start[1].offset,retained_start[1].offset,
            label_offset,retained_start[1].spot.clearance,false,0,retained_start[1].branch_owner,
            retained_start[1].branch_axis,retained_start[1].branch_sign}};
    auto cleared = expose_window_hints(clear_after_drag, desktop);
    check(near(cleared[0].offset,{}) && near(cleared[1].offset,{}),
        "a moved window returns exactly to its true frame when the obstruction leaves");
    auto same_half_left = expose_window_hints({{{280,160,700,440},132,48},
        {{200,160,700,440},132,48}},desktop);
    auto same_half_right = expose_window_hints({{{680,160,700,440},132,48},
        {{760,160,700,440},132,48}},desktop);
    bool side_preserved = true;
    const std::array<rectangle, 2> left_frames{{{280,160,700,440},{200,160,700,440}}};
    const std::array<rectangle, 2> right_frames{{{680,160,700,440},{760,160,700,440}}};
    for (size_t i = 0; i < same_half_left.size(); ++i)
        side_preserved &= left_frames[i].x + left_frames[i].width / 2 + same_half_left[i].offset.x <=
            desktop.width / 2 + .01;
    for (size_t i = 0; i < same_half_right.size(); ++i)
        side_preserved &= right_frames[i].x + right_frames[i].width / 2 + same_half_right[i].offset.x >=
            desktop.width / 2 - .01;
    check(side_preserved,
        "automatic avoidance stays on the true-center side of the screen");
    bool one_pixel_sides = true;
    for (double center : {desktop.width / 2 - 1, desktop.width / 2 + 1})
    {
        exposure_window near_center{{center - 200,160,400,400},132,48};
        auto guarded = expose_window_hints({near_center},desktop,{{center - 200,0,400,720}});
        const double moved_center = center + guarded[0].offset.x;
        one_pixel_sides &= center < desktop.width / 2 ? moved_center <= desktop.width / 2 + .01 :
            moved_center >= desktop.width / 2 - .01;
    }
    check(one_pixel_sides,
        "windows one pixel either side of the periphery boundary never cross the centerline");
    exposure_window center_window{{440,160,400,400},132,48};
    center_window.center_zone = true;
    center_window.center_zone_half_width = 300;
    auto centerline = expose_window_hints({center_window},desktop,{{440,0,400,720}});
    auto centerline_repeat_input = center_window;
    centerline_repeat_input.incumbent_offset = centerline[0].offset;
    centerline_repeat_input.target_offset = centerline[0].offset;
    centerline_repeat_input.branch_owner = centerline[0].branch_owner;
    centerline_repeat_input.branch_axis = centerline[0].branch_axis;
    centerline_repeat_input.branch_sign = centerline[0].branch_sign;
    auto centerline_repeat = expose_window_hints({centerline_repeat_input},desktop,{{440,0,400,720}});
    check(std::abs(centerline[0].offset.x) > 1 &&
        centerline[0].offset.x * centerline_repeat[0].offset.x > 0 &&
        near(centerline[0].offset,centerline_repeat[0].offset) &&
        std::abs(centerline[0].offset.x) <= center_window.center_zone_half_width,
        "a center-zone window retains its way inside Scottland's center-zone limits");
    size_t tiny_work = 0, repeat_tiny_work = 0;
    bool tiny_work_hit = false, repeat_tiny_work_hit = false;
    auto tiny_work_case = clear_after_drag;
    tiny_work_case[0].frame.x = 240; // the covering layout: an uncovered window needs no search (WK31)
    tiny_work_case[1].incumbent_offset = retained_start[1].offset;
    tiny_work_case[1].target_offset = retained_start[1].offset;
    exposure_limits tiny_limits{4, &tiny_work, true};
    auto tiny_work_result = expose_window_hints(tiny_work_case, desktop, {},
        std::chrono::steady_clock::time_point::max(), &tiny_work_hit, nullptr, tiny_limits);
    tiny_limits.inspection_count = &repeat_tiny_work;
    auto repeat_tiny_work_result = expose_window_hints(tiny_work_case, desktop, {},
        std::chrono::steady_clock::time_point::max(), &repeat_tiny_work_hit, nullptr, tiny_limits);
    check(tiny_work_hit && repeat_tiny_work_hit && tiny_work <= 4 && repeat_tiny_work <= 4 &&
        near(tiny_work_result[1].offset, retained_start[1].offset) &&
        near(repeat_tiny_work_result[1].offset, tiny_work_result[1].offset) &&
        tiny_work_result[1].diameter >= 48,
        "a forced tiny inspection budget holds a valid, stable minimum-size target");
    auto failing_way = retained_label_window;
    const point incumbent_badge{550 + retained_label_offset.x,380 + retained_label_offset.y};
    failing_way.fixed_foreground.push_back({incumbent_badge.x - 80, incumbent_badge.y - 80,160,160});
    std::vector<exposure_window> failing_way_case{{{240,160,700,440},132,48,{},true},failing_way};
    bool truncated_lost_way_held = false;
    size_t lost_way_budget = 0, lost_way_work = 0;
    for (size_t budget : {size_t(1),size_t(2),size_t(4),size_t(8),size_t(16),
        size_t(32),size_t(64),size_t(128),size_t(256)})
    {
        size_t work = 0;
        exposure_limits limits{budget,&work,false};
        exposure_profile profile;
        bool truncated = false;
        auto result = expose_window_hints(failing_way_case,desktop,{},
            std::chrono::steady_clock::time_point::max(),&truncated,&profile,limits);
        if (profile.truncated_searches && truncated &&
            near(result[1].offset,retained_start[1].offset,.01))
        {
            truncated_lost_way_held = true;
            lost_way_budget = budget;
            lost_way_work = work;
            break;
        }
    }
    std::cout << "lost-way hold: work-budget=" << lost_way_budget << " inspections="
        << lost_way_work << '\n';
    check(truncated_lost_way_held,
        "a deadline-truncated solve holds the prior target when its old badge point loses clearance");

    rectangle pile_screen{0,0,1280,720};
    std::vector<exposure_window> large_pile;
    for (int i = 0; i < 5; ++i)
    {
        exposure_window item{{192,101,894,510},72,48};
        item.anchored = i == 0;
        item.center_zone = true;
        item.center_zone_half_width = 256;
        large_pile.push_back(item);
    }
    size_t pile_work = 0;
    exposure_limits pile_limits{std::numeric_limits<size_t>::max(),&pile_work,false};
    auto pile_result = expose_window_hints(large_pile,pile_screen,{},
        std::chrono::steady_clock::time_point::max(),nullptr,nullptr,pile_limits);
    bool pile_visible = pile_result.size() == large_pile.size();
    bool pile_shifted = false;
    for (size_t i = 0; i < pile_result.size(); ++i)
    {
        pile_visible &= pile_result[i].diameter >= 48 &&
            std::isfinite(pile_result[i].spot.center.x) &&
            std::isfinite(pile_result[i].spot.center.y);
        if (i) pile_shifted |= std::hypot(pile_result[i].offset.x,pile_result[i].offset.y) > 40;
    }
    std::cout << "large-pile: work=" << pile_work << " shifted=" << pile_shifted << '\n';
    check(pile_visible && pile_shifted,
        "five overlapping large windows find a nearby minimum-hint placement");

    {
        const rectangle screen{0,0,1920,1080};
        std::vector<exposure_window> dense;
        for (int i = 0; i < 16; ++i)
        {
            exposure_window item{{513,285,894,510},72,48,{},i == 0};
            item.center_zone = true; item.center_zone_half_width = 320;
            dense.push_back(item);
        }
        exposure_progress progress;
        exposure_limits limits; limits.allow_size_upgrades = false;
        limits.inspection_budget = 1800;
        size_t largest_work = 0; int solves = 0; bool done = false;
        size_t first_slice_completed = 0;
        while (!done && solves < int(dense.size() * avoidance_attempts_per_window + 2))
        {
            exposure_profile profile; size_t work = 0;
            limits.inspection_count = &work;
            done = expose_window_hints_progressively(dense,screen,{},progress,
                std::chrono::steady_clock::time_point::max(),nullptr,&profile,limits);
            largest_work = std::max(largest_work,work); ++solves;
            if (solves == 1)
                first_slice_completed = std::count(progress.complete.begin(), progress.complete.end(), true);
            check(work <= limits.inspection_budget,
                "progressive dense-layout slice respects its deterministic work budget");
        }
        const double required = 48 * 1.06 / 2 + 1;
        bool all_hints_published = done && progress.complete.size() == dense.size();
        size_t visible_patches = 0;
        for (size_t i = 0; i < progress.results.size(); ++i)
        {
            const auto& result = progress.results[i];
            all_hints_published &= progress.has_result[i] && result.diameter >= 48 &&
                std::isfinite(result.spot.center.x) && std::isfinite(result.spot.center.y);
            if (result.spot.clearance + .25 >= required)
            {
                ++visible_patches;
                all_hints_published &= result.diameter >= 48;
            }
        }
        size_t attempts = 0;
        for (auto count : progress.attempts) attempts += count;
        std::cout << "progressive-16: slices=" << solves << " largest-work=" << largest_work
            << " first-slice-complete=" << first_slice_completed
            << " attempts=" << attempts << " patches=" << visible_patches
            << " no-room=" << progress.fallback_count << '\n';
        check(first_slice_completed > 0 && first_slice_completed < dense.size(),
            "a bounded dense-layout slice commits some, but not all, validated windows");
        check(done && solves > 1 && attempts <= dense.size() * (avoidance_attempts_per_window - 1),
            "a static 16-window layout converges across bounded retries");
        check(all_hints_published,
            "every dense-layout window finishes with a finite minimum-size hint placement");
        check(visible_patches + progress.fallback_count == dense.size(),
            "each dense-layout window ends with a checked patch or the isolated P1 no-room result");
        exposure_profile idle_profile; size_t idle_work = 0;
        limits.inspection_count = &idle_work;
        done = expose_window_hints_progressively(dense,screen,{},progress,
            std::chrono::steady_clock::time_point::max(),nullptr,&idle_profile,limits);
        check(done && idle_work == 0 && idle_profile.work_count == 0,
            "a converged unchanged layout does no further avoidance work");
        std::vector<exposure_window> no_zone_room{{{210,60,1500,960},72,48,{},true},
            {{760,340,400,400},72,48}};
        no_zone_room[1].center_zone = true;
        no_zone_room[1].center_zone_half_width = 0;
        exposure_progress p1_choice;
        exposure_limits p1_limits; p1_limits.allow_size_upgrades = false;
        bool p1_done = expose_window_hints_progressively(no_zone_room,screen,{},p1_choice,
            std::chrono::steady_clock::time_point::max(),nullptr,nullptr,p1_limits);
        check(p1_done && near(p1_choice.results[1].offset,{}) &&
            p1_choice.results[1].spot.clearance + .25 < 48 * 1.06 / 2 + 1,
            "the isolated P1/P12 choice preserves the center zone by default");
        exposure_progress p12_choice;
        auto p12_limits = p1_limits;
        p12_limits.allow_minimum_patch_zone_overshoot = true;
        bool p12_done = expose_window_hints_progressively(no_zone_room,screen,{},p12_choice,
            std::chrono::steady_clock::time_point::max(),nullptr,nullptr,p12_limits);
        const double p12_required = 48 * 1.06 / 2 + 1;
        const double p12_center_y = no_zone_room[1].frame.y + no_zone_room[1].frame.height / 2 +
            p12_choice.results[1].offset.y;
        const double p12_band = screen.height * .25;
        const bool beyond_zone = std::abs(no_zone_room[1].frame.x +
            no_zone_room[1].frame.width / 2 + p12_choice.results[1].offset.x - screen.width / 2) >
                no_zone_room[1].center_zone_half_width + .01 ||
            std::abs(p12_center_y - screen.height / 2) > p12_band + .01;
        check(p12_done && beyond_zone &&
            p12_choice.results[1].spot.clearance + .25 >= p12_required &&
            std::hypot(p12_choice.results[1].offset.x,p12_choice.results[1].offset.y) > 0,
            "the isolated P12 alternative crosses a zone edge only far enough to reveal a minimum patch");

        auto retained_no_room = no_zone_room;
        retained_no_room[1].center_zone_half_width = 200;
        retained_no_room[1].incumbent_offset = {16, 0};
        retained_no_room[1].target_offset = {16, 0};
        exposure_progress held_no_room;
        bool held_no_room_done = expose_window_hints_progressively(retained_no_room,screen,{},
            held_no_room,std::chrono::steady_clock::time_point::max(),nullptr,nullptr,p1_limits);
        check(held_no_room_done && near(held_no_room.results[1].offset,{16, 0}) &&
            held_no_room.results[1].diameter >= 48 &&
            std::isfinite(held_no_room.results[1].spot.center.x),
            "when no patch fits in-zone, the minimum hint stays at the held way instead of snapping to zero");

    }

    auto drag_sweep = [&] (size_t budget, int repeats) {
        const rectangle screen{0,0,1920,1080};
        const std::vector<rectangle> base{{100,250,700,500},{300,200,500,400},
            {900,500,500,400},{800,100,500,300}};
        double largest_one_px_target_change = 0, largest_unbranched_target_change = 0,
            largest_frame_jump = 0;
        size_t largest_work = 0; int max_branch_switches = 0;
        bool boundaries = true, visible = true, held = true, cleared_matches_fresh = true;
        bool any_shift = false;
        std::vector<std::vector<point>> settled_repeats;
        auto frames = base;
        std::vector<point> target(base.size()), shown(base.size()), labels(base.size());
        std::vector<double> clearance(base.size());
        std::vector<int> owner(base.size(), -1), axis(base.size()), direction(base.size());
        std::vector<point> branch_base(base.size());
        std::vector<exposure_window> seed_inputs;
        for (size_t i = 0; i < frames.size(); ++i)
            seed_inputs.push_back({frames[i],132,48,{},i == 0});
        const auto seed = expose_window_hints(seed_inputs,screen);
        for (size_t i = 0; i < seed.size(); ++i)
        {
            target[i] = shown[i] = seed[i].offset;
            labels[i] = {seed[i].spot.center.x - seed[i].offset.x -
                    (frames[i].x + frames[i].width/2),
                seed[i].spot.center.y - seed[i].offset.y -
                    (frames[i].y + frames[i].height/2)};
            clearance[i] = seed[i].spot.clearance;
            owner[i] = seed[i].branch_owner; axis[i] = seed[i].branch_axis;
            direction[i] = seed[i].branch_sign; branch_base[i] = seed[i].branch_base_offset;
        }
        for (int repeat = 0; repeat < repeats; ++repeat)
        {
            auto frames = base;
            std::vector<int> branch_switches(base.size());
            std::vector<point> path;
            for (int x = 100; x <= 1100; ++x) path.push_back({double(x),250});
            for (int x = 1099; x >= 100; --x) path.push_back({double(x),250});
            for (int i = 0; i < 50; ++i) path.push_back({100,250});
            path.push_back({1210,1000}); // A leaves the cluster and parks bottom-right.
            for (int i = 0; i < 50; ++i) path.push_back({1210,1000});
            const size_t return_hold_start = 2001, park_frame = 2051;
            point previous_drag{}; bool have_previous = false;
            std::vector<point> after_return, held_target;
            std::vector<int> held_owner, held_axis, held_direction;
            for (size_t frame_number = 0; frame_number < path.size(); ++frame_number)
            {
                frames = base; frames[0].x = path[frame_number].x; frames[0].y = path[frame_number].y;
                std::vector<exposure_window> inputs;
                for (size_t i = 0; i < frames.size(); ++i)
                {
                    exposure_window input{frames[i],132,48,{},i == 0};
                    input.incumbent_offset = shown[i]; input.target_offset = target[i];
                    input.prior_label_offset = labels[i]; input.prior_clearance = clearance[i];
                    input.branch_owner = owner[i]; input.branch_axis = axis[i];
                    input.branch_sign = direction[i];
                    input.branch_base_offset = branch_base[i];
                    inputs.push_back(input);
                }
                size_t work = 0; exposure_limits limits{budget,&work,false}; bool truncated = false;
                auto result = expose_window_hints(inputs,screen,{},
                    std::chrono::steady_clock::time_point::max(),&truncated,nullptr,limits);
                largest_work = std::max(largest_work, work);
                visible &= work <= budget && result.size() == frames.size();
                point input_now = path[frame_number];
                double input_step = have_previous ? std::hypot(input_now.x-previous_drag.x,
                    input_now.y-previous_drag.y) : 1000;
                previous_drag = input_now; have_previous = true;
                std::vector<point> next_target(base.size());
                for (size_t i = 0; i < frames.size(); ++i)
                {
                    next_target[i] = result[i].offset;
                    visible &= result[i].diameter >= 48 && std::isfinite(result[i].spot.center.x) &&
                        std::isfinite(result[i].spot.center.y);
                    const double jump = std::hypot(next_target[i].x-target[i].x,
                        next_target[i].y-target[i].y);
                    const bool new_way = i && result[i].branch_owner >= 0 &&
                        (owner[i] != result[i].branch_owner || axis[i] != result[i].branch_axis ||
                            direction[i] != result[i].branch_sign);
                    if (i && input_step <= 1.01)
                    {
                        largest_one_px_target_change = std::max(largest_one_px_target_change,jump);
                        if (!new_way) largest_unbranched_target_change =
                            std::max(largest_unbranched_target_change,jump);
                        if (jump > 20)
                            std::cout << "drag-target jump x=" << input_now.x << " w=" << i
                                << " jump=" << jump << " new-way=" << new_way << " old="
                                << target[i].x << ',' << target[i].y << " way=" << owner[i] << '/'
                                << axis[i] << '/' << direction[i] << " new=" << next_target[i].x
                                << ',' << next_target[i].y << " way=" << result[i].branch_owner << '/'
                                << result[i].branch_axis << '/' << result[i].branch_sign << '\n';
                    }
                    largest_frame_jump = std::max(largest_frame_jump,jump);
                    any_shift |= i && std::hypot(next_target[i].x,next_target[i].y)>2;
                    if (i && result[i].branch_owner >= 0 &&
                        (owner[i] != result[i].branch_owner || axis[i] != result[i].branch_axis ||
                            direction[i] != result[i].branch_sign)) ++branch_switches[i];
                    if (i && frame_number > return_hold_start + 5 && frame_number < park_frame)
                        held &= held_owner.size() == owner.size() &&
                            held_owner[i] == result[i].branch_owner &&
                            held_axis[i] == result[i].branch_axis &&
                            held_direction[i] == result[i].branch_sign;
                    auto before_center = frames[i].x + frames[i].width/2;
                    auto after_center = before_center + next_target[i].x;
                    auto vertical_center = frames[i].y + frames[i].height/2 + next_target[i].y;
                    if (i)
                    {
                        if (before_center < screen.width/2-.01)
                            boundaries &= after_center <= screen.width/2+.01;
                        else boundaries &= after_center >= screen.width/2-.01;
                        double cy = frames[i].y + frames[i].height/2, middle = screen.height/2;
                        double band = screen.height*.25;
                        if (std::abs(cy-middle) <= band)
                            boundaries &= vertical_center >= middle-band-.01 && vertical_center <= middle+band+.01;
                        else if (cy < middle) boundaries &= vertical_center <= middle+.01;
                        else boundaries &= vertical_center >= middle-.01;
                    }
                    labels[i] = {result[i].spot.center.x-next_target[i].x-(frames[i].x+frames[i].width/2),
                        result[i].spot.center.y-next_target[i].y-(frames[i].y+frames[i].height/2)};
                    clearance[i] = result[i].spot.clearance;
                    owner[i] = result[i].branch_owner; axis[i] = result[i].branch_axis;
                    direction[i] = result[i].branch_sign; branch_base[i] = result[i].branch_base_offset;
                    for (int tick = 0; tick < 2; ++tick)
                    {
                        shown[i].x += (next_target[i].x-shown[i].x)*.18;
                        shown[i].y += (next_target[i].y-shown[i].y)*.18;
                    }
                }
                target = next_target;
                if (frame_number == return_hold_start + 5)
                { held_target = target; held_owner = owner; held_axis = axis; held_direction = direction; }
                if (frame_number > return_hold_start + 5 && frame_number < park_frame)
                    for (size_t i=1; i<target.size(); ++i)
                        held &= near(target[i],held_target[i],.01);
                if (frame_number == path.size()-1) after_return = target;
            }
            max_branch_switches = std::max(max_branch_switches,
                *std::max_element(branch_switches.begin()+1,branch_switches.end()));
            auto final_frames = base; final_frames[0].x = 1210; final_frames[0].y = 1000;
            auto fresh = expose_window_hints({{final_frames[0],132,48,{},true},{final_frames[1],132,48},
                {final_frames[2],132,48},{final_frames[3],132,48}},screen);
            // The held input layout is A parked bottom-right; it leaves the three
            // otherwise separated windows at their stateless zero-offset answer.
            if (budget == std::numeric_limits<size_t>::max())
            {
                cleared_matches_fresh &= after_return.size() == fresh.size();
                for (size_t i=1; i<fresh.size() && i<after_return.size(); ++i)
                    cleared_matches_fresh &= near(after_return[i],fresh[i].offset,.5);
            }
            settled_repeats.push_back(after_return);
        }
        if (budget == std::numeric_limits<size_t>::max())
            for (size_t r=1;r<settled_repeats.size();++r)
                for (size_t i=1;i<settled_repeats[r].size();++i)
                    cleared_matches_fresh &= near(settled_repeats[r][i],settled_repeats[0][i],.5);
        std::cout << "avoidance sweep budget=" << budget << " work=" << largest_work
            << " max1px=" << largest_one_px_target_change
            << " max-unbranched=" << largest_unbranched_target_change
            << " maxjump=" << largest_frame_jump
            << " branch-switches=" << max_branch_switches << '\n';
        check(largest_unbranched_target_change <= 4,
            "1 px drag steps produce at most 4 px target changes unless a new way is declared");
        check(largest_one_px_target_change <= 64,
            "even a necessary way change cannot retarget farther than 64 px in one solve");
        check(max_branch_switches <= 1,
            "three repeated sweeps switch each window's way at most once");
        check(boundaries,"avoidance targets stay in the original horizontal and vertical zone");
        check(held,"a stationary drag produces stable targets");
        if (budget == std::numeric_limits<size_t>::max())
            check(cleared_matches_fresh,
                "three sweeps return to the fresh final-layout solution with no ratchet");
        else
            check(held && largest_unbranched_target_change <= 4 &&
                largest_one_px_target_change <= 64 && max_branch_switches <= 1,
                "a truncated sweep holds stable checked targets while staying within its work cap");
        check(visible,"work-count budget is respected and every hint retains its minimum size");
        check(any_shift,"the sweep starts from a displaced, checked incumbent");
    };
    for (size_t budget : {size_t(25),size_t(50),size_t(100),size_t(200)})
        drag_sweep(budget,1);
    drag_sweep(std::numeric_limits<size_t>::max(),3);
    {
        const rectangle screen{0,0,1280,720};
        std::vector<rectangle> base{{290,110,700,500},{289,110,700,500},
            {290,110,700,500},{291,110,700,500}};
        std::vector<point> target(base.size()), shown(base.size()), labels(base.size());
        std::vector<double> clearances(base.size());
        std::vector<int> owner(base.size(),-1), axis(base.size()), direction(base.size());
        std::vector<exposure_window> seed_inputs;
        for (size_t i=0;i<base.size();++i)
        {
            exposure_window input{base[i],72,48,{},i==0};
            input.center_zone = i != 0; input.center_zone_half_width = 300;
            seed_inputs.push_back(input);
        }
        const auto seed = expose_window_hints(seed_inputs,screen);
        for (size_t i=0;i<seed.size();++i)
        {
            target[i]=shown[i]=seed[i].offset;
            labels[i]={seed[i].spot.center.x-seed[i].offset.x-(base[i].x+base[i].width/2),
                seed[i].spot.center.y-seed[i].offset.y-(base[i].y+base[i].height/2)};
            clearances[i]=seed[i].spot.clearance; owner[i]=seed[i].branch_owner;
            axis[i]=seed[i].branch_axis; direction[i]=seed[i].branch_sign;
        }
        // The three rear centers begin one pixel left of, exactly on, and one pixel
        // right of the screen center. Move the foreground one pixel at a time and
        // feed back the same branch/label/display state as the compositor bridge.
        bool zones=true, held=true, visible=true, actual_label_clearance=true;
        double max_step=0, max_nonrelease_step=0, max_held_step=0, max_clearance_error=0;
        int zero_releases=0; size_t max_work=0;
        auto frames=base;
        int prior_sign[4]{};
        std::vector<point> stationary;
        for (int x=290; x<=410; ++x)
        {
            frames=base; frames[0].x=x;
            // Feed back the complete compositor state: true frames, displayed and target
            // offsets, the label point, and the beneficiary/axis/direction of each way.
            // Omitting way identity makes a repeat appear to ratchet because every pass
            // then looks like a new branch instead of the same ray returning toward zero.
            std::vector<exposure_window> inputs;
            for (size_t i=0;i<frames.size();++i)
            {
                exposure_window input{frames[i],72,48,{},i==0};
                input.center_zone=i!=0; input.center_zone_half_width=300;
                input.incumbent_offset=shown[i]; input.target_offset=target[i];
                input.prior_label_offset=labels[i]; input.prior_clearance=clearances[i];
                input.branch_owner=owner[i]; input.branch_axis=axis[i]; input.branch_sign=direction[i];
                inputs.push_back(input);
            }
            size_t work=0; exposure_limits limits{std::numeric_limits<size_t>::max(),&work,false};
            auto result=expose_window_hints(inputs,screen,{},
                std::chrono::steady_clock::time_point::max(),nullptr,nullptr,limits);
            max_work=std::max(max_work,work);
            for (size_t i=0;i<frames.size();++i)
            {
                std::vector<rectangle> foreground;
                for (size_t j=0;j<i;++j)
                    foreground.push_back({frames[j].x+result[j].offset.x,
                        frames[j].y+result[j].offset.y,frames[j].width,frames[j].height});
                const rectangle moved_frame{frames[i].x+result[i].offset.x,
                    frames[i].y+result[i].offset.y,frames[i].width,frames[i].height};
                const double actual=visible_clearance(result[i].spot.center,moved_frame,screen,foreground);
                const double error=std::abs(actual-result[i].spot.clearance);
                max_clearance_error=std::max(max_clearance_error,error);
                actual_label_clearance &= error<=.75;
            }
            for (size_t i=1;i<frames.size();++i)
            {
                const auto old=target[i]; target[i]=result[i].offset;
                const auto step=std::hypot(target[i].x-old.x,target[i].y-old.y);
                max_step=std::max(max_step,step);
                const bool release_to_zero = step > 4 && std::hypot(target[i].x,target[i].y) < .01 &&
                    result[i].spot.clearance + .25 >= 48 * 1.06 / 2 + 1;
                if (release_to_zero) ++zero_releases;
                else max_nonrelease_step=std::max(max_nonrelease_step,step);
                const double center_x=frames[i].x+frames[i].width/2+target[i].x;
                zones &= center_x>=screen.width/2-300-.01 && center_x<=screen.width/2+300+.01;
                visible &= result[i].diameter>=48;
                if (std::abs(target[i].x)>1)
                {
                    const int sign=target[i].x<0?-1:1;
                    if (prior_sign[i] && sign!=prior_sign[i]) zones=false;
                    prior_sign[i]=sign;
                }
                labels[i]={result[i].spot.center.x-target[i].x-(frames[i].x+frames[i].width/2),
                    result[i].spot.center.y-target[i].y-(frames[i].y+frames[i].height/2)};
                clearances[i]=result[i].spot.clearance; owner[i]=result[i].branch_owner;
                axis[i]=result[i].branch_axis; direction[i]=result[i].branch_sign;
                for (int tick=0;tick<2;++tick)
                { shown[i].x+=(target[i].x-shown[i].x)*.18; shown[i].y+=(target[i].y-shown[i].y)*.18; }
            }
        }
        stationary=target;
        for (int frame=0;frame<12;++frame)
        {
            std::vector<exposure_window> inputs;
            for (size_t i=0;i<frames.size();++i)
            {
                exposure_window input{frames[i],72,48,{},i==0};
                input.center_zone=i!=0; input.center_zone_half_width=300;
                input.incumbent_offset=shown[i]; input.target_offset=target[i];
                input.prior_label_offset=labels[i]; input.prior_clearance=clearances[i];
                input.branch_owner=owner[i]; input.branch_axis=axis[i]; input.branch_sign=direction[i];
                inputs.push_back(input);
            }
            auto result=expose_window_hints(inputs,screen);
            for (size_t i=1;i<frames.size();++i)
            {
                const double step=std::hypot(result[i].offset.x-target[i].x,
                    result[i].offset.y-target[i].y);
                max_held_step=std::max(max_held_step,step);
                held &= step <= .01;
                held &= owner[i] == result[i].branch_owner && axis[i] == result[i].branch_axis &&
                    direction[i] == result[i].branch_sign;
                target[i]=stationary[i]=result[i].offset;
                labels[i]={result[i].spot.center.x-target[i].x-(frames[i].x+frames[i].width/2),
                    result[i].spot.center.y-target[i].y-(frames[i].y+frames[i].height/2)};
                clearances[i]=result[i].spot.clearance; owner[i]=result[i].branch_owner;
                axis[i]=result[i].branch_axis; direction[i]=result[i].branch_sign;
                shown[i]=target[i];
            }
        }
        std::cout << "center-zone sweep: max1px=" << max_step << " max-nonrelease="
            << max_nonrelease_step << " zero-releases=" << zero_releases << " max-held="
            << max_held_step << " max-work=" << max_work << " clearance-error="
            << max_clearance_error << '\n';
        check(zones && visible && actual_label_clearance && max_nonrelease_step<=4 && zero_releases<=1,
            "a 1 px drag through a centerline pile stays in the center zone without a jump");
        check(held,"an exactly centered window's way stays stable while held");
    }
    rectangle chain_screen{0,0,1280,720};
    auto chain = expose_window_hints({{{200,110,700,500},132,48,{},true},
        {{200,110,700,500},132,48},{{200,110,700,500},132,48}},chain_screen);
    bool chain_keeps_badges = chain.size() == 3;
    bool chain_moves_cause = false;
    for (size_t i = 0; i < chain.size(); ++i)
    {
        chain_keeps_badges &= chain[i].diameter >= 48 &&
            std::isfinite(chain[i].spot.center.x) && std::isfinite(chain[i].spot.center.y);
        if (i) chain_moves_cause |= std::hypot(chain[i].offset.x,chain[i].offset.y) > 1;
    }
    check(chain_keeps_badges && chain_moves_cause && near(chain[0].offset,{}),
        "a foreground-concession chain preserves hints and keeps its anchored front in place");
    exposure_window widget_obstacle{{200,160,700,440},132,48,
        {{200,160,700,440}},false};
    auto beside_widget = expose_window_hints({widget_obstacle},chain_screen);
    const rectangle widget_frame{200,160,700,440};
    const rectangle widget_window{200+beside_widget[0].offset.x,
        160+beside_widget[0].offset.y,700,440};
    check(std::hypot(beside_widget[0].offset.x,beside_widget[0].offset.y) > 1 &&
        beside_widget[0].diameter >= 48 &&
        visible_clearance(beside_widget[0].spot.center,widget_window,chain_screen,
            {widget_frame}) >= 48*1.06/2,
        "window exposure moves around a fixed widget foreground rectangle");
    auto hidden = expose_window_hints({{{220,180,360,270},72,48},{{250,200,300,220},72,48}},desktop);
    check(hidden[1].diameter >= 48 && hidden[1].spot.clearance >= 48*1.06/2 &&
        (std::hypot(hidden[0].offset.x,hidden[0].offset.y)>1 ||
         std::hypot(hidden[1].offset.x,hidden[1].offset.y)>1),
        "fully covered ordinary window is revealed by visual movement");
    auto full = expose_window_hints({{{0,0,1280,720},132,48},{{0,0,1280,720},132,48}},desktop);
    check(full[0].diameter >= 48 && full[1].diameter >= 48 &&
        full[0].spot.clearance >= 48*1.06/2 && full[1].spot.clearance >= 48*1.06/2 &&
        std::hypot(full[0].offset.x,full[0].offset.y) > 40 && near(full[1].offset,{}),
        "an output-sized front window moves to reveal the wholly covered rear window");
    auto anchored = expose_window_hints({{{0,0,1280,720},132,48,{},true},
        {{0,0,1280,720},132,48}},desktop);
    // The impossible rear hint would land on the front's centered hint (WK31), so the
    // collision stopgap moves it about one diameter off; it stays at the minimum.
    check(near(anchored[0].offset,{}) && anchored[0].diameter == 132 &&
        near(anchored[0].spot.center,{640,360}) && anchored[1].diameter == 48 &&
        std::hypot(anchored[1].spot.center.x - 640, anchored[1].spot.center.y - 360) >=
            (132 + 48) / 2 * 1.06 + hint_collision_gap - 1e-6,
        "focused output-covering window stays put; impossible rear hint stays at minimum, off the front hint");
    auto movable_rear = expose_window_hints({{{240,160,700,440},132,48,{},true},
        {{200,160,700,440},132,48}},desktop);
    check(near(movable_rear[0].offset,{}) && movable_rear[1].offset.x < -10 &&
        movable_rear[1].diameter >= 48,
        "focused front anchors a covered rear window's exposure movement");
    auto three = expose_window_hints({{{0,0,1280,720},132,48},{{0,0,1280,720},132,48},
        {{0,0,1280,720},132,48}},desktop);
    bool three_visible = three.size() == 3;
    bool three_move = false;
    for (size_t i = 0; i < three.size(); ++i)
    {
        three_visible &= three[i].diameter >= 48 && three[i].spot.clearance >= 48*1.06/2;
        if (i) three_move |= std::hypot(three[i].offset.x,three[i].offset.y) > 40;
    }
    check(three_visible && three_move,
        "three output-sized windows each retain a visible minimum hint as needed");
    auto tiny = expose_window_hints({{{140,100,64,36},72,48}},desktop);
    check(near(tiny[0].offset,{}) && tiny[0].diameter == 48,
        "tiny displayed window retains the 48px minimum hint even when it cannot fit");
    auto unchanged = declutter({{100,100},{300,200}},region);
    auto widget_anchor = declutter({{250,200},{250,200}},region,6,{72,72},
        {{true,48,true},{true,48,false}});
    check(near(widget_anchor[0],{250,200}) &&
        std::abs(widget_anchor[1].y-200) > 70,
        "focused widget keeps its rail position while the other exterior hint moves");
    check(near(unchanged[0],{100,100}) && near(unchanged[1],{300,200}), "non-overlapping centers stay put");
    auto separated = declutter({{250,200},{250,200}},region);
    check(std::hypot(separated[0].x-separated[1].x,separated[0].y-separated[1].y) >= 85,
        "identical centers separate deterministically");
    check(near({(separated[0].x+separated[1].x)/2,(separated[0].y+separated[1].y)/2},{250,200},0.1),
        "springs preserve the cluster's original center");
    check(near(separated[0],declutter({{250,200},{250,200}},region)[0]),"declutter is deterministic");
    std::vector<point> dense(30,{600,350}); auto spread = declutter(dense,{40,40,1200,640});
    bool bounded=true,separate=true;
    for(size_t i=0;i<spread.size();++i) { bounded &= spread[i].x>=40 && spread[i].x<=1240 && spread[i].y>=40 && spread[i].y<=680;
        for(size_t j=0;j<i;++j) separate &= std::hypot(spread[i].x-spread[j].x,spread[i].y-spread[j].y)>=85; }
    check(bounded,"dense declutter remains inside hint bounds");
    check(separate,"30 coincident centers produce readable distinct hints");
    check(declutter({},region).empty(),"empty declutter");
    check(near(declutter({{100,100}},region)[0],{100,100}),"single node unchanged");
    auto mixed = declutter({{250,200},{250,200}},region,6,{72,132});
    check(std::hypot(mixed[0].x-mixed[1].x,mixed[0].y-mixed[1].y) >= 108-0.001,
        "mixed badge diameters separate by their radii plus padding");
    auto edge = declutter({{0,0},{500,400}},region,6,{72,132});
    check(near(edge[0],{36,36}) && near(edge[1],{434,334}), "each badge stays inside screen edges by its own radius");
    auto small_separate = declutter({{100,100},{180,100}},region,6,{72,72});
    check(near(small_separate[0],{100,100}) && near(small_separate[1],{180,100}),
        "separated small badges are not displaced by the large badge maximum");
    for (double y : {0.0, 200.0, 400.0})
    {
        auto rail = declutter({{100,y},{100,y},{104,y}}, region, 6, {72,108,72},
            {{true,48},{true,48},{true,48}});
        bool ok = true;
        for (size_t i = 0; i < rail.size(); ++i)
        {
            ok &= rail[i].x == (i == 2 ? 104 : 100) && rail[i].y >= (i == 1 ? 54 : 48)
                && rail[i].y <= 400 - (i == 1 ? 54 : 48);
            for (size_t j = 0; j < i; ++j)
                ok &= std::hypot(rail[i].x-rail[j].x, rail[i].y-rail[j].y) >= (i == 1 || j == 1 ? 96 : 78) - .02;
        }
        check(ok, "stacked mixed-size rail hints separate vertically, including at screen ends");
    }
    auto rail_window = declutter({{100,200},{100,200}}, region, 6, {72,132}, {{true,48},{}});
    check(rail_window[0].x == 100 && std::hypot(rail_window[0].x-rail_window[1].x,
        rail_window[0].y-rail_window[1].y) >= 108-.02,
        "window hint separates from a widget without detaching its rail hint");
    auto pinned_window = declutter({{144,200},{-200,200}}, region, 6, {72,132}, {{true,48},{}});
    check(pinned_window[0].x == 144 && std::hypot(pinned_window[0].x-pinned_window[1].x,
        pinned_window[0].y-pinned_window[1].y) >= 108-.02,
        "a screen-clamped window hint can separate vertically from a rail hint");
    for (double diameter : {72.0, 108.0, 216.0})
    {
        double overlap = widget_hint_overlap(diameter, 96);
        check(overlap > 0 && overlap <= diameter * .15 &&
            std::hypot(diameter/2-overlap+11-.5, 37-.5) > diameter/2+11,
            "widget circle retains slight overlap and clears the upper count corner after rounding");
    }
    for (auto palette : std::vector<hint_palette>{hint_palette{},
        {true,{0.957,0.961,0.969},{0.137,0.165,0.208},{0.231,0.431,0.659}},
        {false,{0.05,0.05,0.05},{0.9,0.9,0.9},{0.8,0.2,0.3}},
        {true,{0.9,0.85,0.7},{0.1,0.1,0.1},{0.2,0.6,0.2}},
        {false,{0.5,0.5,0.5},{0.9,0.9,0.9},{0.5,0.5,0.5}}})
    {
        bool readable = true, adjacent = true, complementary = true;
        std::set<std::tuple<double,double,double>> colors;
        for (unsigned slot = 0; slot < 676; ++slot)
        {
            auto c = hint_color(slot,palette);
            colors.insert({c.r,c.g,c.b});
            readable &= hint_contrast(c,palette.background) >= 3 && hint_badge_contrast(c,palette.background) >= 3 &&
                hint_badge_contrast(c,hint_mix(palette.background,palette.foreground,0.05)) >= 3;
            double h = hint_hue(c), accent = hint_hue(palette.accent);
            complementary &= std::abs(std::fmod(h-accent+540,360)-180) >= 100-1e-6;
            if (slot) adjacent &= std::abs(std::fmod(h-hint_hue(hint_color(slot-1,palette))+540,360)-180) >= 60;
        }
        check(readable,"676 colors reach 3:1 against background and tinted typical surfaces");
        check(adjacent && complementary,"adjacent hues are far apart inside the complementary arc");
        check(colors.size()==676,"two-letter capacity has distinct colors without a repeated fixed palette");
    }
    using D = destination;
    check(cycle_order(D::center) == std::array<D,3>{D::periphery,D::widget,D::center}, "pure center cycle order");
    check(cycle_order(D::periphery) == std::array<D,3>{D::center,D::widget,D::periphery}, "pure periphery cycle order");
    check(cycle_order(D::widget) == std::array<D,3>{D::center,D::periphery,D::widget}, "pure widget cycle order");
    alt_mode mode; std::vector<destination> moves; uint64_t selected=0,closed=0; bool restore=false;
    unsigned selections = 0, widget_hint_selections = 0; uint64_t peeked_widget = 0;
    bool peek_active = false; uint32_t clock = 0;
    auto tap_at = [&](char letter, uint32_t at, unsigned dwell = 80) {
        mode.letter(letter, at); mode.release(letter, at + dwell);
    };
    auto press = [&](char letter) { clock += 500; tap_at(letter, clock); };
    mode.select=[&](uint64_t id,bool r){selected=id;restore=r;++selections;};
    mode.hint_select=[&](uint64_t id){peeked_widget=id;peek_active=true;++widget_hint_selections;};
    mode.hint_peek_active=[&](uint64_t id){return peek_active && id==peeked_widget;};
    mode.move=[&](uint64_t,destination d){moves.push_back(d);}; mode.close=[&](uint64_t id){closed=id;};
    const std::vector<hint_entry> entries{{1,0,zone::center,false},{2,1,zone::right_periphery,false},{3,2,zone::left_rail,true}};
    mode.begin(entries,0);
    press('a'); check(selected==1 && moves.empty(),"unselected center first press only selects");
    for(int i=0;i<6;++i) press('a');
    check(moves==std::vector<D>{D::periphery,D::widget,D::center,D::periphery,D::widget,D::center},"center repeats its full loop");
    moves.clear();press('s');check(selected==2 && !restore,"unselected periphery first press only selects");
    for(int i=0;i<6;++i)press('s');
    check(moves==std::vector<D>{D::center,D::widget,D::periphery,D::center,D::widget,D::periphery},"periphery repeats its full loop");
    moves.clear(); press('d');check(selected==3 && !restore && moves.empty() &&
        widget_hint_selections==1 && peeked_widget==3,"unselected widget hint selects and requests a temporary peek without opening");
    for(int i=0;i<5;++i) press('d');
    check(moves==std::vector<D>{D::center,D::periphery,D::widget,D::center,D::periphery} &&
        widget_hint_selections==1,"widget next press starts its full center-first loop without starting another peek");
    mode.end(); moves.clear(); mode.double_tap_delay=3000; mode.begin(entries,0); press('d');
    tap_at('d', clock + 3080);
    check(moves==std::vector<D>{D::center},"a repeated hint during a collapsed-widget peek takes the center step even inside double-tap timing");
    peek_active=false; mode.double_tap_delay=300;
    for (unsigned slot=0; slot<3; ++slot)
    {
        mode.end(); moves.clear(); unsigned before = selections;
        mode.begin(entries,slot+1); press("asd"[slot]);
        check(selections==before && moves.size()==1 && moves[0]==cycle_order(D(slot))[0], "selected window skips redundant select in each start zone");
    }
    mode.end(); moves.clear(); mode.begin(entries,1);
    press('a'); tap_at('a',clock+380);
    check(moves==std::vector<D>{D::periphery,D::widget},"double tap at 300 ms after release sends to rail immediately");
    mode.refresh({{1,0,zone::left_rail,true}});
    tap_at('a',clock+500);
    check(moves.size()==2,"double tap on widget issues no move");
    clock += 500; press('a'); check(moves.back()==D::center,"slow press after rail shortcut resumes original loop");
    mode.end(); moves.clear(); mode.begin(entries,3);
    press('d'); tap_at('d',clock+381);
    check(moves==std::vector<D>{D::center,D::periphery},"press past interval advances ordinary cycle");
    mode.double_tap_delay=50; moves.clear(); mode.end(); mode.begin(entries,2);
    press('s'); tap_at('s',clock+131); press('s');
    check(moves==std::vector<D>{D::center,D::widget,D::periphery},"configured interval leaves slow presses in periphery loop");
    mode.end(); moves.clear(); mode.begin(entries,2); press('s'); tap_at('s',clock+130);
    check(moves==std::vector<D>{D::center,D::widget},"configured interval recognizes its inclusive boundary");
    mode.end(); moves.clear(); mode.begin(entries,0); press('a'); press('s'); tap_at('a',clock+100);
    check(moves.empty() && selected==1,"another hint resets cycle and double-tap identity");
    mode.end(); moves.clear(); mode.begin({{1,0,zone::right_periphery,false}},1); press('a');
    check(moves==std::vector<D>{D::center},"release resets loop but selected window still skips select");
    mode.refresh({{1,0,zone::center,false},{2,1,zone::center,false},{3,2,zone::center,false}});
    mode.tab(false); check(selected==2,"Tab follows assignment order");
    mode.tab(true); check(selected==1,"Shift Tab reverses assignment order");
    mode.tab(true);check(selected==3,"backwards Tab wraps");
    unsigned before_tab_hint = selections; moves.clear(); press('d');
    check(selections == before_tab_hint && moves == std::vector<D>{D::periphery}, "Tab-selected window skips redundant select");
    mode.close_selected();check(closed==3,"close acts on selection");
    mode.refresh({{1,0,zone::center,false},{2,1,zone::center,false}});
    check(mode.selected==0,"closed selection is cleared safely");
    mode.refresh({{1,0,zone::center,false},{27,26,zone::center,false}});
    check(mode.label(0)=="aa" && mode.label(26)=="sa","two-letter mode is prefix-free");
    press('s');check(selected==3,"partial two-letter hint does not select");
    press('a');check(selected==27,"complete two-letter hint selects");
    moves.clear(); tap_at('s',clock+81);
    check(moves.empty(), "repeated multi-letter prefix alone does not move");
    tap_at('a',clock+221);
    check(moves == std::vector<D>{D::widget}, "repeating complete two-letter hint double-taps to rail");
    mode.refresh({{1,0,zone::center,false},{677,676,zone::center,false}});
    check(mode.label(0)=="aaa" && mode.label(676)=="saa", "overflow grows hint width without dropping any window");
    mode.refresh({}); check(mode.hint_width==1,"empty desktop resets hint width");
    mode.end(); press('a');check(!mode.active,"inactive controller does nothing");
    mode.double_tap_delay=300; moves.clear(); mode.begin(entries,0);
    tap_at('a',1000,120); tap_at('a',1370,120);
    check(moves==std::vector<D>{D::widget},"120 ms dwell plus 250 ms gap double-taps an unselected window to rail");
    mode.end(); moves.clear(); mode.begin({{1,1,zone::center,false},{27,26,zone::center,false}},0);
    tap_at('a',2000,120); tap_at('s',2140,120);
    tap_at('a',2510,120);
    check(moves.empty(),"human-timed repeated prefix alone cannot move the selection");
    tap_at('s',2650,120);
    check(moves==std::vector<D>{D::widget},"multi-letter repeat begins within the release gap and acts only on completion");
    mode.end(); mode.refresh({}); moves.clear(); mode.begin(entries,0);
    mode.letter('a',3000); mode.letter('a',3100); mode.release('a',3180); // the selected window acts on release (WK35)
    check(moves==std::vector<D>{D::periphery},"no final-key release means no double-tap candidate");
    mode.end(); moves.clear(); mode.begin(entries,0);
    tap_at('a',4000,120); mode.release('s',4400); tap_at('a',4500,120);
    check(moves==std::vector<D>{D::periphery},"an unrelated release cannot extend the repeat interval");
    mode.end(); moves.clear(); mode.begin(entries,0);
    tap_at('a',UINT32_MAX-40,80); tap_at('a',139,80);
    check(moves==std::vector<D>{D::widget},"release-based repeat timing survives the monotonic timestamp wrap");
    {
        // WP1: each zone memory keeps the Shift scale pin there, or its absence.
        window_memory memory;
        remember_spot(memory, zone::left_periphery, {.1,.4}, .62);
        check(remembered_pin(memory, zone::left_periphery) == .62, "periphery memory keeps its Shift pin");
        check(memory.last_side == -1, "remembering a pinned spot still records its side");
        check(!remembered_pin(memory, zone::right_periphery), "a left periphery pin never applies on the right");
        remember_spot(memory, zone::right_periphery, {.9,.5}, std::nullopt);
        check(!remembered_pin(memory, zone::right_periphery) && remembered_pin(memory, zone::left_periphery) == .62,
            "an unpinned spot in one zone leaves another zone's pin alone");
        remember_spot(memory, zone::left_periphery, {.12,.3}, std::nullopt);
        check(!remembered_pin(memory, zone::left_periphery) && near(*memory.positions[1], {.12,.3}),
            "a later unpinned drop in the same zone clears that zone's pin");
        remember_spot(memory, zone::left_periphery, {.12,.3}, .8);
        remember_spot(memory, zone::left_periphery, {.12,.3}, .7);
        check(remembered_pin(memory, zone::left_periphery) == .7, "the newest pin in a zone replaces the older one");
        remember_spot(memory, zone::center, {.5,.5}, .5);
        check(!memory.pins[0] && !remembered_pin(memory, zone::center) && memory.positions[0],
            "center memory never keeps a pin: center is full scale (tenet 4)");
        check(memory.last_side == -1, "a center memory leaves the last side alone");
        remember_spot(memory, zone::right_rail, {.97,.2}, .5);
        check(!memory.pins[4] && !remembered_pin(memory, zone::right_rail) && memory.last_side == 1,
            "rail memory never keeps a pin: widgets don't scale (WG4)");
        remember_spot(memory, zone::right_periphery, {.85,.6}, 0.0);
        check(!remembered_pin(memory, zone::right_periphery), "a zero or negative scale is no pin");
        remember_spot(memory, zone::right_periphery, {.85,.6}, 7.5);
        check(remembered_pin(memory, zone::right_periphery) == 1.0, "a pin read back above full scale is clamped to 1");
        remember_spot(memory, zone::right_periphery, {.85,.6}, .001);
        check(remembered_pin(memory, zone::right_periphery) == .05, "a pin read back below the minimum scale is clamped");
        remember_spot(memory, zone::right_periphery, {.85,.6}, std::nan(""));
        check(!remembered_pin(memory, zone::right_periphery), "a NaN scale is no pin");
        window_memory empty; empty.pins[1] = .5;
        check(!remembered_pin(empty, zone::left_periphery), "a pin without a remembered spot is never restored");
    }
    // WK37 occlusion share.
    const rectangle fraction_screen{0,0,1000,800};
    check(visible_fraction({100,100,400,200},fraction_screen,{})==1,"uncovered window is fully visible");
    check(std::abs(visible_fraction({100,100,400,200},fraction_screen,{{300,0,600,800}})-.5)<1e-9,
        "half-covered window is half visible");
    check(std::abs(visible_fraction({100,100,400,200},fraction_screen,
        {{100,100,300,200},{200,100,300,100}})-.125)<1e-9,"overlapping covers count once");
    check(visible_fraction({100,100,400,200},fraction_screen,{{0,0,1000,800}})==0,"fully covered window");
    check(std::abs(visible_fraction({-200,100,400,200},fraction_screen,{{0,100,100,200}})-.5)<1e-9,
        "only the on-screen part counts");
    check(visible_fraction({1200,100,400,200},fraction_screen,{{0,0,1000,800}})==1,
        "off-screen window never counts as occluded");
    check(visible_fraction({100,100,400,200},fraction_screen,{{600,0,100,100}})==1,"disjoint cover ignored");
    check(hint_outline_visible_fraction==.5,"outline threshold is less than half visible");
    {
        // A whole front-to-back pass over 50 overlapping windows stays far inside the 2 ms
        // solve budget the plugin also bounds it by (P8).
        std::mt19937 rng(37);
        std::uniform_real_distribution<double> px(0,1600),py(0,900),size(200,900);
        std::vector<rectangle> stack;
        for(int i=0;i<50;++i) stack.push_back({px(rng),py(rng),size(rng),size(rng)*.6});
        const rectangle screen{0,0,1920,1080};
        auto started=std::chrono::steady_clock::now();
        double sum=0;
        for(size_t i=0;i<stack.size();++i)
            sum+=visible_fraction(stack[i],screen,std::vector<rectangle>(stack.begin(),stack.begin()+i));
        double ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-started).count();
        std::cout<<"occlusion pass, 50 windows: "<<ms<<" ms\n";
        check(ms<2 && sum>0,"occlusion pass over 50 windows stays under 2 ms");
    }
    {
        // WK31: a window nothing covers has its hint at its exact center, with no search
        // and no avoidance on its behalf; a prior off-center label point is not retained.
        rectangle screen{0,0,1280,720};
        auto centered = [] (const exposure_result& r, point c) {
            return std::abs(r.spot.center.x - c.x) < 1e-6 && std::abs(r.spot.center.y - c.y) < 1e-6; };
        exposure_window front{{190,60,900,600},132,48,{},true};
        front.prior_label_offset = {-300,-150}; front.prior_clearance = 75;
        exposure_window behind{{460,240,480,320},132,48};
        auto raised = expose_window_hints({front, behind}, screen);
        check(centered(raised[0], {640,360}) && near(raised[0].offset, {}),
            "WK31: a raised front window drops its old off-center label for its exact center");
        auto unanchored = front; unanchored.anchored = false;
        auto unfocused = expose_window_hints({unanchored, behind}, screen);
        // Unanchored, it may move to expose the window behind it; its hint stays at the
        // center of its on-screen part.
        const double top = std::max(0.0, 60 + unfocused[0].offset.y);
        const double bottom = std::min(720.0, 660 + unfocused[0].offset.y);
        check(centered(unfocused[0], {640 + unfocused[0].offset.x, (top + bottom) / 2}),
            "WK31: an unfocused front window is centered too, even when moved for a window behind");
        exposure_window small{{600,330,80,60},132,48};
        small.prior_label_offset = {10,0}; small.prior_clearance = 30;
        auto tiny = expose_window_hints({small}, screen);
        check(centered(tiny[0], {640,360}) && near(tiny[0].offset, {}) && tiny[0].diameter == 54,
            "WK31: no size-upgrade search or move for a front window too small for its wanted size");
        exposure_window offscreen{{-200,100,600,400},132,48};
        auto partial = expose_window_hints({offscreen}, screen);
        check(centered(partial[0], {200,300}),
            "WK31: a partly off-screen window is centered on its on-screen part");
        exposure_window side{{1000,500,200,150},132,48};
        side.prior_label_offset = {-40,-20}; side.prior_clearance = 30;
        auto apart = expose_window_hints({{{100,100,600,400},132,48,{},true}, side}, screen);
        check(centered(apart[1], {1100,575}),
            "WK31: a rear window that nothing overlaps is centered like the frontmost");
        exposure_window cornered{{100,100,600,400},132,48};
        cornered.fixed_foreground = {{100,100,120,120}};
        cornered.prior_label_offset = {-150,80}; cornered.prior_clearance = 60;
        auto strict = expose_window_hints({cornered}, screen);
        check(centered(strict[0], {250,380}),
            "WK31 strict: a widget over one corner keeps the covered window's checked label");
        bool hit = false;
        auto expired_deadline = expose_window_hints({front, behind}, screen, {},
            std::chrono::steady_clock::now() - std::chrono::milliseconds(1), &hit);
        check(centered(expired_deadline[0], {640,360}),
            "WK31: an expired solve deadline still centers the front window");
        // Raising Back over a window that has no legal room: its held hint is the honest
        // minimum on this layout, not the size it had while it was in front.
        exposure_window buried{{400,240,480,320},109,48};
        buried.prior_clearance = 160; buried.center_zone = true; buried.center_zone_half_width = 50;
        auto held = expose_window_hints({front, buried}, screen);
        check(held[1].diameter == 48 && held[1].spot.clearance < 26,
            "a covered window with no room is not reported visible at its pre-raise size");
        // Stopgap pending P1/P12: a hint that would land on a hint in front of it moves about
        // one diameter off it, staying on its own window, so both letters read.
        auto apart_from = [] (const exposure_result& a, const exposure_result& b) {
            return std::hypot(a.spot.center.x - b.spot.center.x, a.spot.center.y - b.spot.center.y) >=
                (a.diameter + b.diameter) / 2 * 1.06 + hint_collision_gap - 1e-6; };
        auto on_own = [] (const exposure_result& r, rectangle w) {
            const double e = r.diameter / 2 * 1.06;
            return r.spot.center.x - e >= w.x && r.spot.center.x + e <= w.x + w.width &&
                r.spot.center.y - e >= w.y && r.spot.center.y + e <= w.y + w.height; };
        exposure_window concentric{{400,200,480,320},132,48}; // no legal room in its zone
        concentric.center_zone = true; concentric.center_zone_half_width = 50;
        auto stacked = expose_window_hints({front, concentric}, screen);
        auto stacked_again = expose_window_hints({front, concentric}, screen);
        check(centered(stacked[0], {640,360}) && near(stacked[1].offset, {}) &&
            apart_from(stacked[0], stacked[1]) &&
            on_own(stacked[1], concentric.frame) &&
            std::hypot(stacked[1].spot.center.x - 640, stacked[1].spot.center.y - 360) < 2 * 132,
            "a buried hint concentric with the front hint moves about one diameter, onto its own window");
        check(near(stacked[1].spot.center, stacked_again[1].spot.center),
            "the collision offset is deterministic");
        exposure_window concentric2{{420,220,440,280},132,48};
        concentric2.center_zone = true; concentric2.center_zone_half_width = 50;
        auto three = expose_window_hints({front, concentric, concentric2}, screen);
        check(centered(three[0], {640,360}) && apart_from(three[0], three[1]) &&
            apart_from(three[0], three[2]) && apart_from(three[1], three[2]),
            "three concentric hints all read: each rear hint clears every hint in front of it");
        exposure_progress collision_progress;
        expose_window_hints_progressively({front, concentric, concentric2}, screen, {},
            collision_progress, std::chrono::steady_clock::time_point::max());
        const auto& pr = collision_progress.results;
        check(centered(pr[0], {640,360}) && apart_from(pr[0], pr[1]) && apart_from(pr[0], pr[2]) &&
            apart_from(pr[1], pr[2]), "the progressive solver keeps concentric hints apart");
        exposure_progress progress;
        expose_window_hints_progressively({front, behind}, screen, {}, progress,
            std::chrono::steady_clock::time_point::max());
        check(progress.has_result[0] && centered(progress.results[0], {640,360}),
            "WK31: the progressive solver centers the front window");
    }
    std::cout<<passed<<" passed, "<<failed<<" failed\n";return failed?1:0;
}
