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
    mode.letter('a',3000); mode.letter('a',3100);
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
    std::cout<<passed<<" passed, "<<failed<<" failed\n";return failed?1:0;
}
