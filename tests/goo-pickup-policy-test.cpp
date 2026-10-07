// Exact policy boundaries use supplied timestamps. No compositor or wall-clock gates.
#include "goo-pickup-policy.hpp"
#include <iostream>
using scottland::goo::pickup_policy_t;
int main()
{
    int failures = 0;
    auto check = [&](const char *name, bool ok) {
        std::cout << (ok ? "PASS " : "FAIL ") << name << '\n';
        failures += !ok;
    };
    pickup_policy_t p;
    check("first content change is ready", p.change(100));
    p.start(100);
    check("cooldown defers until its exact boundary", !p.change(119.999) && !p.expire(119.999) && p.pending);
    check("deadline releases the waiting change and doubles the next gap", p.expire(120) && !p.pending && p.gap == 40);
    p.start(120);
    check("a change exactly at the deadline is immediately ready", p.change(160));
    p.start(160);
    p.last_activity = 160;
    p.change(170);
    check("twenty seconds of activity silence does not reset until exceeded", !p.activity(180) && p.pending && p.gap == 40);
    check("new activity after a quiet interval ends a waiting cooldown", p.activity(200.001) && !p.pending && p.gap == 20 && p.next == 200);
    p.gap = 160; p.start(300); p.change(301); p.expire(460); p.start(460); p.change(461); p.expire(760);
    check("doubling stops at five minutes", p.gap == 300);
    p.last_check = 800;
    check("checks are limited at fractional boundaries", p.check_wait(800.25) == .25 && p.check_wait(800.5) == 0);
    return failures ? 1 : 0;
}
