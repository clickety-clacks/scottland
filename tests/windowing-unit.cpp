#include "placement.hpp"
#include "declutter.hpp"
#include "alt-mode.hpp"
#include "hint-style.hpp"
#include "widget-spring.hpp"
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
    std::vector<exposure_window> easy_deadline_case{{{150,100,220,160},72,32}};
    auto deadline_result = expose_window_hints(easy_deadline_case, region, {}, tiny_deadline, &deadline_hit);
    auto repeated_deadline_result = expose_window_hints(easy_deadline_case, region, {}, tiny_deadline,
        &repeat_deadline_hit);
    check(deadline_hit && repeat_deadline_hit && deadline_result.size() == 1 &&
        deadline_result[0].diameter >= 72 && std::isfinite(deadline_result[0].spot.center.x) &&
        near(deadline_result[0].offset, repeated_deadline_result[0].offset) &&
        near(deadline_result[0].spot.center, repeated_deadline_result[0].spot.center),
        "a forced tiny solve budget keeps a valid, deterministic no-move badge position");
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
    auto enough = expose_window_hints({{{320,160,700,440},132,32},{{100,160,700,440},132,32}},desktop);
    check(near(enough[0].offset,{}) && near(enough[1].offset,{}) && enough[1].diameter == 132,
        "a rear window with an already wide enough left strip stays exactly put");
    auto narrow = expose_window_hints({{{280,160,700,440},132,32},{{200,160,700,440},132,32}},desktop);
    check(near(narrow[0].offset,{}) && narrow[1].offset.x < -60 && narrow[1].offset.x > -65 &&
        std::abs(narrow[1].offset.y) < 1 && narrow[1].diameter == 132,
        "narrow left strip moves the rear window only far enough for its proportional circle");
    check(visible_clearance(narrow[1].spot.center,
        {200+narrow[1].offset.x,160,700,440},desktop,{{280,160,700,440}}) >= 132*1.06/2,
        "rear label circle stays inside the exposed strip and outside the front window");
    auto same = expose_window_hints({{{280,160,700,440},132,32},{{200,160,700,440},132,32}},desktop);
    check(near(narrow[0].offset,same[0].offset) && near(narrow[1].offset,same[1].offset),
        "least-exposure displacement is deterministic");
    auto hidden = expose_window_hints({{{220,180,360,270},72,32},{{250,200,300,220},72,32}},desktop);
    check(hidden[1].diameter == 72 && hidden[1].spot.clearance >= 72*1.06/2 &&
        (std::hypot(hidden[0].offset.x,hidden[0].offset.y)>1 ||
         std::hypot(hidden[1].offset.x,hidden[1].offset.y)>1),
        "fully covered ordinary window is revealed by visual movement");
    auto full = expose_window_hints({{{0,0,1280,720},132,32},{{0,0,1280,720},132,32}},desktop);
    check(full[0].diameter == 132 && full[1].diameter == 132 &&
        std::hypot(full[0].offset.x,full[0].offset.y) > 140 && near(full[1].offset,{}),
        "an output-sized front window moves to reveal the wholly covered rear window");
    auto anchored = expose_window_hints({{{0,0,1280,720},132,32,{},true},
        {{0,0,1280,720},132,32}},desktop);
    check(near(anchored[0].offset,{}) && anchored[0].diameter == 132 &&
        anchored[1].diameter == 0,
        "focused output-covering window never shifts; impossible rear hint waits");
    auto movable_rear = expose_window_hints({{{280,160,700,440},132,32,{},true},
        {{200,160,700,440},132,32}},desktop);
    check(near(movable_rear[0].offset,{}) && movable_rear[1].offset.x < -60,
        "focused front anchors a covered rear window's exposure movement");
    auto three = expose_window_hints({{{0,0,1280,720},132,32},{{0,0,1280,720},132,32},
        {{0,0,1280,720},132,32}},desktop);
    check(three.size() == 3 && three[0].diameter == 132 && three[1].diameter == 132 &&
        three[2].diameter == 132,
        "three output-sized windows each expose enough interior for a circle");
    auto tiny = expose_window_hints({{{140,100,64,36},72,32}},desktop);
    check(near(tiny[0].offset,{}) && tiny[0].diameter == 32,
        "tiny displayed window keeps a 32px interior badge without moving");
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
    auto press = [&](char letter) { clock += 500; mode.letter(letter, clock); };
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
    mode.letter('d', clock + 3000);
    check(moves==std::vector<D>{D::center},"a repeated hint during a collapsed-widget peek takes the center step even inside double-tap timing");
    peek_active=false; mode.double_tap_delay=300;
    for (unsigned slot=0; slot<3; ++slot)
    {
        mode.end(); moves.clear(); unsigned before = selections;
        mode.begin(entries,slot+1); press("asd"[slot]);
        check(selections==before && moves.size()==1 && moves[0]==cycle_order(D(slot))[0], "selected window skips redundant select in each start zone");
    }
    mode.end(); moves.clear(); mode.begin(entries,1);
    press('a'); mode.letter('a',clock+300);
    check(moves==std::vector<D>{D::periphery,D::widget},"double tap at 300 ms sends to rail immediately");
    mode.refresh({{1,0,zone::left_rail,true}});
    mode.letter('a',clock+400);
    check(moves.size()==2,"double tap on widget issues no move");
    clock += 400; press('a'); check(moves.back()==D::center,"slow press after rail shortcut resumes original loop");
    mode.end(); moves.clear(); mode.begin(entries,3);
    press('d'); mode.letter('d',clock+301);
    check(moves==std::vector<D>{D::center,D::periphery},"press past interval advances ordinary cycle");
    mode.double_tap_delay=50; moves.clear(); mode.end(); mode.begin(entries,2);
    press('s'); mode.letter('s',clock+51); press('s');
    check(moves==std::vector<D>{D::center,D::widget,D::periphery},"configured interval leaves slow presses in periphery loop");
    mode.end(); moves.clear(); mode.begin(entries,2); press('s'); mode.letter('s',clock+50);
    check(moves==std::vector<D>{D::center,D::widget},"configured interval recognizes its inclusive boundary");
    mode.end(); moves.clear(); mode.begin(entries,0); press('a'); press('s'); mode.letter('a',clock+1);
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
    moves.clear(); mode.letter('s',clock+1);
    check(moves.empty(), "repeated multi-letter prefix alone does not move");
    mode.letter('a',clock+2);
    check(moves == std::vector<D>{D::widget}, "repeating complete two-letter hint double-taps to rail");
    mode.refresh({{1,0,zone::center,false},{677,676,zone::center,false}});
    check(mode.label(0)=="aaa" && mode.label(676)=="saa", "overflow grows hint width without dropping any window");
    mode.refresh({}); check(mode.hint_width==1,"empty desktop resets hint width");
    mode.end(); press('a');check(!mode.active,"inactive controller does nothing");
    std::cout<<passed<<" passed, "<<failed<<" failed\n";return failed?1:0;
}
