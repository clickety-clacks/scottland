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
    // WK28 circles are front overlay islands in the same liquid. Their round
    // field survives a disabled window film, and their film follows the pop scale.
    source_t circle, island;
    circle.id = uint64_t(1) << 63; circle.hint_circle = circle.hinted = true;
    circle.rect = {300, 300, 36, 36}; circle.liquid.y = 36;
    island.id = 42; island.rect = {300, 300, 150, 100};
    std::vector<source_t> hinted{circle, island}; amounts(hinted, s);
    assert(std::abs(distance({336,300}, circle)) < .001);
    assert(distance({336,336}, circle) > 14);
    s.overlap_film = 0;
    assert(density({340,300}, hinted, s, 0) > s.threshold());
    assert(density({300,300}, hinted, s, 0) == 0); // letter/content island stays dry
    circle.scale = .25;
    assert(std::abs(overlap_film_width(circle,s) - s.thickness*.25) < .001);
    s.overlap_film = 4;
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
    float resting_film = overlap_film_width(overlap[0], s);
    assert(std::abs(resting_film - 4) < 1e-5);
    assert(density({393, 300}, overlap, s, 0) < s.threshold());
    overlap[0].swell = 1;
    s.swell = .7;
    amounts(overlap, s);
    assert(std::abs(overlap_film_width(overlap[0], s) - 8) < 1e-5);
    assert(density({393, 300}, overlap, s, 0) > s.threshold());
    auto film_radii = support_radii(overlap, s, true);
    assert(film_radii[0] * 8 / s.thickness > 7);
    s.swell = 0;
    amounts(overlap, s);
    assert(std::abs(overlap_film_width(overlap[0], s) - resting_film) < 1e-5);
    assert(density({393, 300}, overlap, s, 0) < s.threshold());
    // A small window with a wider user film setting can push the swollen film
    // farther over back content than its open-desktop goo. Damage must include it.
    overlap[0].scale = .25;
    overlap[0].swell = 1;
    s.swell = .7;
    s.overlap_film = 12;
    amounts(overlap, s);
    assert(std::abs(overlap_film_width(overlap[0], s) - 96) < 1e-4);
    auto open_radii = support_radii(overlap, s);
    film_radii = support_radii(overlap, s, true);
    float old_out = std::max(open_radii[0], film_radii[0]) + 3;
    float swollen_out = std::max(open_radii[0],
        film_radii[0] * std::max(1.f, overlap_film_width(overlap[0], s) / s.thickness)) + 3;
    bool beyond_old_bounds = false;
    for (int x = 300; x < 400; x++)
        if (density({float(x), 300}, overlap, s, 0) > s.threshold() * .97f)
        {
            assert(400 - x <= swollen_out);
            beyond_old_bounds |= 400 - x > old_out;
        }
    assert(beyond_old_bounds);
    overlap[0].scale = 1;
    overlap[0].swell = 0;
    s.overlap_film = 4;
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
                 "validation, swell and overlap-film damage bounds, fullscreen islands\n";
}
