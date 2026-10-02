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
    // Ordered visibility: a front edge remains wet over back content, but the
    // reverse stacking and front content itself cannot expose that edge.
    source_t front = a, back = b;
    front.rect = {500, 300, 100, 100}; back.rect = {400, 300, 150, 150};
    std::vector<source_t> overlap{front, back};
    s.swell = 0; amounts(overlap, s);
    assert(density({398, 300}, overlap, s, 0) > s.threshold());
    assert(density({390, 300}, overlap, s, 0) < s.threshold());
    assert(density({450, 300}, overlap, s, 0) == 0);
    std::swap(overlap[0], overlap[1]);
    assert(density({398, 300}, overlap, s, 0) == 0);
    std::swap(overlap[0], overlap[1]);
    s.overlap_film = 0;
    assert(density({398, 300}, overlap, s, 0) == 0);
    s.overlap_film = 4;
    front.sides = {0, 1, 0, 0};
    assert(control_cloud({605, 265}, front) == 1);
    assert(control_cloud({605, 335}, front) == 1);
    assert(control_cloud({395, 300}, front) == 0);
    front.corners = {0, 0, 0, 1};
    assert(control_cloud({605, 392}, front) > .99);
    assert(control_cloud({592, 405}, front) > .99);
    // The conservative radii must contain every potentially visible field sample,
    // including distant tails, many overlapping islands, deposits and custom curves.
    for (auto curve : {"", "0:1 .5:.8 1:.4", "0:1 .5:.25 1:0"})
    {
        assert(s.curve(curve));
        s.noise = .9; s.wave_height = 2;
        std::vector<source_t> crowd;
        for (int k = 0; k < 12; k++)
        {
            auto w = a;
            w.id = k; w.rect = {float(100 + k % 4 * 120), float(100 + k / 4 * 140), 45, 55};
            w.corners = {.7, .5, .8, .9}; w.dot = {w.rect.x, w.rect.y + 55, 1, 5};
            w.swell = 1; w.hinted = true;
            crowd.push_back(w);
        }
        auto radii = support_radii(crowd, s);
        amounts(crowd, s);
        for (int y = -500; y < 1000; y += 7)
            for (int x = -500; x < 1100; x += 7)
            {
                glm::vec2 p{float(x), float(y)};
                float f = density(p, crowd, s, .6) * (1 + 3.9f * s.wave_height);
                if (f < s.threshold() * .97f) continue;
                bool covered = false;
                for (size_t k = 0; k < crowd.size(); k++)
                {
                    auto q = glm::max(glm::abs(p - glm::vec2(crowd[k].rect)) -
                        glm::vec2(crowd[k].rect.z, crowd[k].rect.w), glm::vec2{0});
                    covered |= glm::length(q) <= radii[k] + .01f;
                }
                assert(covered);
            }
    }
    assert(s.curve(""));
    assert(s.fall(s.reach * 5) > 0); // performance work preserves the shipped tail
    std::cout << "PASS goo model: falloff, bridge/snap, volume draw, union clipping, finite input, curve "
                 "validation, swell control, fullscreen islands\n";
}
