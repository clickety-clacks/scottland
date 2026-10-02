#include "placement.hpp"
#include "declutter.hpp"
#include "alt-mode.hpp"
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
    using D = destination;
    check(cycle_order(D::center) == std::array<D,3>{D::periphery,D::widget,D::center}, "pure center cycle order");
    check(cycle_order(D::periphery) == std::array<D,3>{D::center,D::widget,D::periphery}, "pure periphery cycle order");
    check(cycle_order(D::widget) == std::array<D,3>{D::center,D::periphery,D::widget}, "pure widget cycle order");
    alt_mode mode; std::vector<destination> moves; uint64_t selected=0,closed=0; bool restore=false;
    unsigned selections = 0; uint32_t clock = 0;
    auto press = [&](char letter) { clock += 500; mode.letter(letter, clock); };
    mode.select=[&](uint64_t id,bool r){selected=id;restore=r;++selections;};
    mode.move=[&](uint64_t,destination d){moves.push_back(d);}; mode.close=[&](uint64_t id){closed=id;};
    const std::vector<hint_entry> entries{{1,0,zone::center,false},{2,1,zone::right_periphery,false},{3,2,zone::left_rail,true}};
    mode.begin(entries,0);
    press('a'); check(selected==1 && moves.empty(),"unselected center first press only selects");
    for(int i=0;i<6;++i) press('a');
    check(moves==std::vector<D>{D::periphery,D::widget,D::center,D::periphery,D::widget,D::center},"center repeats its full loop");
    moves.clear();press('s');check(selected==2 && !restore,"unselected periphery first press only selects");
    for(int i=0;i<6;++i)press('s');
    check(moves==std::vector<D>{D::center,D::widget,D::periphery,D::center,D::widget,D::periphery},"periphery repeats its full loop");
    moves.clear(); press('d');check(selected==3 && restore && moves.empty(),"unselected widget first press restores");
    for(int i=0;i<5;++i) press('d');
    check(moves==std::vector<D>{D::periphery,D::widget,D::center,D::periphery,D::widget},"widget restore consumes first center step of its loop");
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
