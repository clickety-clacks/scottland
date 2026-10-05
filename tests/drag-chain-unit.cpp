// WG14/L27 re-grab chain: which re-grab continues a move, at its exact time boundaries.
#include "drag-chain.hpp"

#include <iostream>

using namespace scottland::drag_chain;

static int passed = 0, failed = 0;

static void check(bool ok, const char *name)
{
    std::cout << (ok ? "PASS  " : "FAIL  ") << name << '\n';
    if (ok) ++passed; else ++failed;
}

int main()
{
    // Same form (fingers reset mid-move, out of room): 2.5 s.
    check(continues(7, 7, 0, false) && continues(7, 7, SAME_FORM_MS - 1, false),
        "a re-grab of the same form continues the move up to 2.5 s");
    check(!continues(7, 7, SAME_FORM_MS, false), "at 2.5 s it is a new move");
    // Through a form change (window became its widget, or the reverse): only a finger reset.
    check(continues(9, 9, 0, true) && continues(9, 9, FORM_CHANGE_MS - 1, true),
        "a re-grab of the new form within 1 s continues the move (finger reset)");
    check(!continues(9, 9, FORM_CHANGE_MS, true) && !continues(9, 9, 1800, true),
        "grabbing the new form at 1 s or later (Mike's 1.7-1.9 s) is a new move");
    // Only what stands for the dropped item continues it; nothing continues an empty chain.
    check(!continues(8, 9, 10, true) && !continues(8, 9, 10, false), "another window never continues the move");
    check(!continues(0, 0, 10, false), "no drop yet: nothing continues");
    // L29 holds a dropped window above the widgets for exactly the chain's length.
    check(window_ms(false) == SAME_FORM_MS && window_ms(true) == FORM_CHANGE_MS,
        "the above-the-widgets hold lasts as long as the chain");
    std::cout << "drag-chain unit: " << passed << " passed, " << failed << " failed\n";
    return failed ? 1 : 0;
}
