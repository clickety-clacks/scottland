// WK40 neighbor choice for Super+arrows, without Wayfire.
#include "navigation.hpp"
#include <cmath>
#include <iostream>
using namespace scottland::windowing;
int passed = 0, failed = 0;
void check(bool ok, const char* name) { std::cout << (ok ? "PASS  " : "FAIL  ") << name << '\n';
    if (ok) ++passed; else ++failed; }
// A w x h rectangle centered at (x, y).
rectangle at(double x, double y, double w = 200, double h = 100) { return {x - w / 2, y - h / 2, w, h}; }
bool picks(nav_candidate origin, nav_direction d, const std::vector<nav_candidate>& c, std::optional<uint64_t> want)
{ return neighbor(origin, d, c) == want; }

int main()
{
    using D = nav_direction;
    nav_candidate origin{100, at(800, 500)};
    std::vector<nav_candidate> cross{{1, at(400, 500)}, {2, at(1200, 500)}, {3, at(800, 200)}, {4, at(800, 800)}};
    check(picks(origin, D::left, cross, 1) && picks(origin, D::right, cross, 2) &&
        picks(origin, D::up, cross, 3) && picks(origin, D::down, cross, 4), "each arrow picks the window on that side");

    check(picks(origin, D::right, {{1, at(400, 500)}}, std::nullopt), "nothing on that side: nothing (no wrap-around)");
    check(picks(origin, D::right, {}, std::nullopt), "no other windows: nothing");
    check(picks(origin, D::right, {origin}, std::nullopt), "the focused one is never its own neighbor");

    // That side is a half-plane: any center to the right counts, however far up or down.
    check(picks(origin, D::right, {{1, at(801, 100)}}, 1), "a center barely right of the origin's, far above, is to the right");
    check(picks(origin, D::up, {{1, at(801, 100)}}, 1), "and above");
    check(picks(origin, D::right, {{1, at(800, 100)}}, std::nullopt), "a center exactly above is not to the right");

    // The nearest center wins, with no preference for alignment.
    std::vector<nav_candidate> mixed{{1, at(1050, 680)}, {2, at(1500, 530)}};
    check(picks(origin, D::right, mixed, 1), "the nearer center wins over a farther one in the same row");
    std::vector<nav_candidate> row{{1, at(1500, 500)}, {2, at(1100, 520)}, {3, at(1300, 480)}};
    check(picks(origin, D::right, row, 2), "among several, the nearest center");

    // Exact ties keep the window further in front (earlier in the list).
    std::vector<nav_candidate> tie{{7, at(1200, 400)}, {8, at(1200, 600)}};
    check(picks(origin, D::right, tie, 7) && picks(origin, D::right, {tie[1], tie[0]}, 8),
        "equal distances go to the window in front");

    // Coincident centers: ordered by id, so every one stays reachable and none traps the arrow.
    nav_candidate front{10, at(960, 540, 600, 400)}, rear{20, at(960, 540, 800, 600)};
    check(picks(front, D::right, {rear}, 20) && picks(front, D::down, {rear}, 20) &&
        picks(front, D::left, {rear}, std::nullopt) && picks(front, D::up, {rear}, std::nullopt),
        "a window on the same center with a higher id is reached by Right and Down");
    check(picks(rear, D::left, {front}, 10) && picks(rear, D::up, {front}, 10) &&
        picks(rear, D::right, {front}, std::nullopt), "and the lower id back by Left and Up");
    // With a window further right too, the coincident one comes first (distance 0), then onward.
    nav_candidate beyond{5, at(1500, 540)};
    check(picks(front, D::right, {rear, beyond}, 20) && picks(rear, D::right, {front, beyond}, 5),
        "Right steps through coincident windows by id, then on to the next window");
    // Three on one center: Right walks them in id order; Left walks back.
    nav_candidate third{30, at(960, 540, 300, 200)};
    check(picks(front, D::right, {rear, third}, 20) && picks(rear, D::right, {front, third}, 30) &&
        picks(third, D::right, {front, rear}, std::nullopt) && picks(third, D::left, {front, rear}, 20),
        "three on one center are walked in id order both ways");
    check(picks(front, D::right, {third, rear}, 20) &&
        picks(third, D::left, {rear, front}, 20),
        "coincident navigation order does not depend on stacking order");

    // Drawn rectangles are what count: the caller passes drawn frames, so a scaled window's center is
    // where it shows. (A window whose true center is the origin's but which peeks out is reachable.)
    check(picks(front, D::up, {{20, at(960, 420, 520, 360)}}, 20) && picks(front, D::down, {{20, at(960, 420, 520, 360)}}, std::nullopt),
        "a concentric window drawn peeking upward is above");

    // A single candidate is chosen exactly when its center is on that side (generated grid).
    bool sound = true;
    for (int x = 0; x <= 1600; x += 100) for (int y = 0; y <= 1000; y += 100)
        for (auto d : {D::left, D::right, D::up, D::down})
        {
            if (x == 800 && y == 500) continue;
            auto got = neighbor(origin, d, {{1, at(x, y)}});
            double dx = x - 800, dy = y - 500;
            double along = d == D::left ? -dx : d == D::right ? dx : d == D::up ? -dy : dy;
            sound &= bool(got) == (along > 0);
        }
    check(sound, "a single window is chosen exactly when its center is on that side (grid of 17 x 11 spots)");

    std::cout << passed << " passed, " << failed << " failed\n";
    return failed ? 1 : 0;
}
