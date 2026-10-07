// When the goo may sleep and which energy readings may decide it (goo-settle.hpp). Exact
// boundaries use supplied times and step counts; no compositor and no wall clock.
#include "goo-settle.hpp"
#include <cstdio>
#include <initializer_list>

using namespace scottland::goo;

static int failures = 0;
static void check(const char *what, bool ok)
{
    printf("%s %s\n", ok ? "PASS" : "FAIL", what);
    if (!ok) failures++;
}

int main()
{
    const float floor = .012f;
    // Timed fallback: time alone decides, strictly after 6 s.
    check("timed: not asleep at exactly 6 s", !may_sleep(true, 6.0, 0, floor, 1000));
    check("timed: asleep just after 6 s", may_sleep(true, 6.001, 0, floor, 1000));
    check("timed: energy and steps since the wake don't matter", may_sleep(true, 6.001, 1.f, floor, 0));
    check("timed: not at 5.99 s even when settled", !may_sleep(true, 5.99, 0, floor, 1000));
    // Asynchronous reading: more than 3 s, energy at or below the floor, a full reading interval.
    check("async: asleep after 3 s, settled, 30 steps", may_sleep(false, 3.001, floor, floor, 30));
    check("async: not at exactly 3 s", !may_sleep(false, 3.0, 0, floor, 30));
    check("async: not with energy above the floor", !may_sleep(false, 10, floor + .001f, floor, 30));
    check("async: not 29 steps after a wake", !may_sleep(false, 10, 0, floor, 29));
    check("async: sleeps long before the timed fallback would", may_sleep(false, 3.5, 0, floor, 30) && !may_sleep(true, 3.5, 0, floor, 30));

    // Readings: applied only against the state they were issued in, newer than the last applied.
    const reading_tag_t now{120, 4, 2, 800, 600};
    auto with = [&](uint64_t step) { reading_tag_t r = now; r.step = step; return r; };
    check("a newer reading of the current state applies", reading_applies(with(90), now, 60));
    check("a reading of the last applied step doesn't", !reading_applies(with(60), now, 60));
    check("an older reading doesn't", !reading_applies(with(30), now, 60));
    reading_tag_t r = with(90);
    r.invalidation = 3;
    check("a reading issued before an impulse, source or wake doesn't", !reading_applies(r, now, 60));
    r = with(90);
    r.generation = 1;
    check("a reading from an earlier generation doesn't", !reading_applies(r, now, 0));
    r = with(90);
    r.w = 799;
    check("a reading of another width doesn't", !reading_applies(r, now, 60));
    r = with(90);
    r.h = 601;
    check("a reading of another height doesn't", !reading_applies(r, now, 60));

    // Completion out of order: readings issued at 30, 60 and 90 complete 60, 90, 30. The two newer
    // apply, the late oldest is refused, and the last applied step never goes back.
    uint64_t last = 0;
    int applied = 0, stale = 0;
    bool monotonic = true;
    for (uint64_t step : {60u, 90u, 30u})
    {
        if (reading_applies(with(step), now, last))
        {
            monotonic = monotonic && step > last;
            last = step;
            applied++;
        }
        else stale++;
    }
    check("out of order: the newer two apply, the late oldest is stale", applied == 2 && stale == 1 && last == 90 && monotonic);

    printf("%d failure(s)\n", failures);
    return failures ? 1 : 0;
}
