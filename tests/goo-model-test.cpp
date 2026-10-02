#include "../core/plugin/src/goo-model.hpp"
#include <cassert>
#include <cmath>
#include <iostream>
#include <limits>
using namespace scottland::goo;
int main()
{
    settings_t s;
    s.noise = 0;
    assert(std::abs(s.fall(s.thickness) - s.threshold()) < 1e-6);
    assert(s.fall(std::numeric_limits<float>::quiet_NaN()) == 0);
    assert(s.fall(std::numeric_limits<float>::infinity()) == 0);
    source_t a, b;
    a.id = 1;
    b.id = 2;
    a.rect = {300, 300, 150, 100};
    b.rect = {640, 300, 150, 100};
    std::vector<source_t> windows{a, b};
    amounts(windows, s);
    assert(density({470, 300}, windows, s, 0) > s.threshold());
    assert(union_distance({300, 300}, windows) < 0);
    auto amount = windows[0].liquid.x;
    s.thinning = 0;
    amounts(windows, s);
    assert(windows[0].liquid.x > amount);
    windows[1].rect.x += 200;
    amounts(windows, s);
    assert(density({470, 300}, windows, s, 0) < s.threshold());
    assert(density({std::numeric_limits<float>::quiet_NaN(), 0}, windows, s, 0) == 0);
    assert(s.curve("0:1 .5:.25 1:0"));
    assert(s.fall(s.reach * 4) == 0);
    assert(std::abs(s.fall(s.reach * 2) - .25) < .0001);
    auto old = s.falloff;
    assert(!s.curve("0:1 .5:.2 .5:.3 1:0"));
    assert(s.falloff == old);
    assert(!s.curve("0:0 1:1"));
    assert(s.falloff == old);
    assert(!s.curve("0:nan 1:0"));
    assert(s.falloff == old);
    assert(s.curve(""));
    windows[0].swell = 1;
    windows[1].emitter = false;
    s.swell = 0;
    amounts(windows, s);
    auto unswollen = windows[0].liquid.x;
    s.swell = .7;
    amounts(windows, s);
    assert(windows[0].liquid.x > unswollen && windows[1].liquid.x == 0);
    std::cout << "PASS goo model: falloff, bridge/snap, volume draw, union clipping, finite input, curve "
                 "validation, swell control, fullscreen islands\n";
}
