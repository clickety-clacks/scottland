// WK40 neighbor choice for Super+arrows, without Wayfire.
#include "navigation.hpp"
#include <iostream>
using namespace scottland::windowing;
int passed = 0, failed = 0;
void check(bool ok, const char* name) { std::cout << (ok ? "PASS  " : "FAIL  ") << name << '\n';
    if (ok) ++passed; else ++failed; }
// A 200 x 100 rectangle centered at (x, y).
rectangle at(double x, double y, double w = 200, double h = 100) { return {x - w / 2, y - h / 2, w, h}; }
bool picks(rectangle origin, nav_direction d, const std::vector<nav_candidate>& c, std::optional<uint64_t> want)
{ return neighbor(origin, d, c) == want; }

int main()
{
    using D = nav_direction;
    auto origin = at(800, 500);
    std::vector<nav_candidate> cross{{1, at(400, 500)}, {2, at(1200, 500)}, {3, at(800, 200)}, {4, at(800, 800)}};
    check(picks(origin, D::left, cross, 1) && picks(origin, D::right, cross, 2) &&
        picks(origin, D::up, cross, 3) && picks(origin, D::down, cross, 4), "each arrow picks the window on that side");

    check(picks(origin, D::right, {{1, at(400, 500)}}, std::nullopt), "nothing in that direction: no wrap-around");
    check(picks(origin, D::right, {}, std::nullopt), "no other windows: nothing");
    check(picks(origin, D::right, {{1, at(800, 500)}}, std::nullopt), "a window centered on the origin is in no direction");

    // The cone: 45° each side of the direction, edges included; beyond it the window is not there.
    check(picks(origin, D::right, {{1, at(1000, 700)}}, 1), "a center exactly on the cone's edge counts");
    check(picks(origin, D::right, {{1, at(1000, 701)}}, std::nullopt), "a center just outside the cone does not");
    check(picks(origin, D::down, {{1, at(1000, 701)}}, 1), "that window is down instead");

    // Alignment first: a window sharing the row wins over a nearer one off to the side.
    std::vector<nav_candidate> mixed{{1, at(1050, 680)}, {2, at(1500, 530)}};
    check(picks(origin, D::right, mixed, 2), "an aligned window beats a nearer unaligned one");
    // Aligned the other way: the origin's center inside a tall candidate's row.
    std::vector<nav_candidate> tall{{1, at(1000, 640)}, {2, at(1300, 700, 200, 500)}};
    check(picks(origin, D::right, tall, 2), "a tall window whose row contains the origin's center is aligned");
    // Then distance, among the aligned and among the unaligned.
    std::vector<nav_candidate> row{{1, at(1500, 500)}, {2, at(1100, 520)}, {3, at(1300, 480)}};
    check(picks(origin, D::right, row, 2), "among aligned windows the nearest center wins");
    std::vector<nav_candidate> off{{1, at(1300, 900)}, {2, at(1100, 700)}};
    check(picks(origin, D::right, off, 2), "among unaligned windows the nearest center wins");

    // Exact ties keep the window further in front (earlier in the list).
    std::vector<nav_candidate> tie{{7, at(1200, 400)}, {8, at(1200, 600)}};
    check(picks(origin, D::right, tie, 7) && picks(origin, D::right, {tie[1], tie[0]}, 8),
        "an exact tie goes to the window in front");

    // Drawn rectangles, not true ones: a periphery window shown at 30% has its drawn row, so it is
    // not aligned; at full size the same center's row would contain the origin's and it would win.
    std::vector<nav_candidate> drawn{{1, at(1500, 300, 180, 150)}, {2, at(1700, 520)}};
    std::vector<nav_candidate> full{{1, at(1500, 300, 600, 500)}, {2, at(1700, 520)}};
    check(picks(origin, D::right, drawn, 2) && picks(origin, D::right, full, 1),
        "alignment uses the drawn size: a scaled-down window's row is its drawn row");

    // Overlapping windows: centers decide, not overlap.
    check(picks(origin, D::left, {{1, at(760, 510)}}, 1), "an overlapping window slightly to the left is to the left");

    // Every candidate set in a grid: the pick is always inside the cone and never the origin.
    bool sound = true;
    for (int x = 0; x <= 1600; x += 100) for (int y = 0; y <= 1000; y += 100)
        for (auto d : {D::left, D::right, D::up, D::down})
        {
            auto got = neighbor(origin, d, {{1, at(x, y)}});
            double dx = x - 800, dy = y - 500;
            double along = d == D::left ? -dx : d == D::right ? dx : d == D::up ? -dy : dy;
            double across = d == D::left || d == D::right ? dy : dx;
            sound &= bool(got) == (along > 0 && std::abs(across) <= along);
        }
    check(sound, "a single window is chosen exactly when its center is in the cone (grid sweep)");

    std::cout << passed << " passed, " << failed << " failed\n";
    return failed ? 1 : 0;
}
