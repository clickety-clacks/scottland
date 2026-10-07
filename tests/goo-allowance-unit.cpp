// The goo's collection allowance (goo-allowance.hpp) on a real wl_event_loop: at most two slots
// are examined between two waits of the loop, whatever mixture of timer, descriptor, idle and
// post-dispatch-check callbacks collects. The boundaries are observed independently of the
// allowance: this binary interposes libwayland's epoll calls and counts them, and every examined
// slot is charged to the interval of the wait before it.
#include "goo-allowance.hpp"
#include <algorithm>
#include <cstdio>
#include <dlfcn.h>
#include <fcntl.h>
#include <map>
#include <memory>
#include <sys/epoll.h>
#include <sys/resource.h>
#include <vector>

using scottland::goo::collect_allowance_t;

static uint64_t waits = 0;  // epoll waits made by the event loop so far
extern "C" int epoll_wait(int fd, epoll_event *events, int max, int timeout)
{
    static auto real = (int (*)(int, epoll_event *, int, int))dlsym(RTLD_NEXT, "epoll_wait");
    waits++;
    return real(fd, events, max, timeout);
}
extern "C" int epoll_pwait(int fd, epoll_event *events, int max, int timeout, const sigset_t *mask)
{
    static auto real = (int (*)(int, epoll_event *, int, int, const sigset_t *))dlsym(RTLD_NEXT, "epoll_pwait");
    waits++;
    return real(fd, events, max, timeout, mask);
}
extern "C" int epoll_pwait2(int fd, epoll_event *events, int max, const timespec *timeout, const sigset_t *mask)
{
    static auto real =
        (int (*)(int, epoll_event *, int, const timespec *, const sigset_t *))dlsym(RTLD_NEXT, "epoll_pwait2");
    waits++;
    return real(fd, events, max, timeout, mask);
}

static int failures = 0;
static void check(const char *what, bool ok)
{
    printf("%s %s\n", ok ? "PASS" : "FAIL", what);
    if (!ok) failures++;
}

// One collecting world: `busy` slots are waiting, and each collector examines what the
// allowance lets it, as renderer_t::collect() does, then reports the collection.
struct world_t
{
    wl_event_loop *loop = wl_event_loop_create();
    std::unique_ptr<collect_allowance_t> allowance = std::make_unique<collect_allowance_t>(loop);
    int busy = 1 << 20;
    std::map<uint64_t, int> examined;  // per wait interval
    uint64_t total = 0;
    ~world_t()
    {
        allowance.reset();
        wl_event_loop_destroy(loop);
    }
    void collect()
    {
        int n = std::min(allowance->left, busy);
        allowance->left -= n;
        busy -= n;
        examined[waits] += n;
        total += n;
        allowance->spent();
    }
    int most() const
    {
        int m = 0;
        for (auto &[k, n] : examined) m = std::max(m, n);
        return m;
    }
};

static void collect_idle(void *data) { static_cast<world_t *>(data)->collect(); }
struct chain_t { world_t *w; int left; };
static void chained(void *data)
{
    auto *c = static_cast<chain_t *>(data);
    c->w->collect();
    if (--c->left > 0) wl_event_loop_add_idle(c->w->loop, chained, c);
}

// The review's reproduction: a timer collects and adds another idle collector, which runs after
// the allowance's own idle in the same dispatch. With the old idle refill it examined four.
static void review_case()
{
    world_t w;
    static world_t *current;
    current = &w;
    auto timer = wl_event_loop_add_timer(w.loop, [](void *) -> int
    {
        current->collect();
        wl_event_loop_add_idle(current->loop, collect_idle, current);
        return 0;
    }, nullptr);
    for (int i = 0; i < 20; i++)
    {
        wl_event_source_timer_update(timer, 1);
        wl_event_loop_dispatch(w.loop, 100);
    }
    wl_event_source_remove(timer);
    char what[160];
    snprintf(what, sizeof what, "timer then a later idle collector: at most 2 slots per wait interval (most %d, %llu total)",
             w.most(), (unsigned long long)w.total);
    check(what, w.most() <= 2 && w.total > 0);
}

// Every kind of callback collects in every round: idles queued outside a dispatch (run before
// its wait), a timer, three ready pipes, a post-dispatch-check source, and a chain of idles each
// adding the next.
static void mixed_case()
{
    world_t w;
    static world_t *current;
    current = &w;
    auto timer = wl_event_loop_add_timer(w.loop, [](void *) -> int { current->collect(); return 0; }, nullptr);
    int pipes[3][2];
    std::vector<wl_event_source *> sources;
    for (auto &p : pipes)
    {
        if (pipe2(p, O_CLOEXEC | O_NONBLOCK) != 0) { check("pipes for the mixed case", false); return; }
        sources.push_back(wl_event_loop_add_fd(w.loop, p[0], WL_EVENT_READABLE, [](int fd, uint32_t, void *) -> int
        {
            char c;
            while (read(fd, &c, 1) == 1) {}
            current->collect();
            wl_event_loop_add_idle(current->loop, collect_idle, current);
            return 0;
        }, nullptr));
    }
    static int checks_left;
    int quiet[2];  // never written: its source runs only as a post-dispatch check
    if (pipe2(quiet, O_CLOEXEC | O_NONBLOCK) != 0) { check("pipe for the check source", false); return; }
    auto checked = wl_event_loop_add_fd(w.loop, quiet[0], WL_EVENT_READABLE, [](int, uint32_t, void *) -> int
    {
        current->collect();
        return --checks_left > 0;  // nonzero: dispatched again in the same check loop
    }, nullptr);
    if (checked) wl_event_source_check(checked);
    chain_t chain{&w, 0};
    const int rounds = 30;
    for (int i = 0; i < rounds; i++)
    {
        for (auto &p : pipes)
        {
            ssize_t ignored = write(p[1], "x", 1);
            (void)ignored;
        }
        wl_event_source_timer_update(timer, 1);
        wl_event_loop_add_idle(w.loop, collect_idle, &w);
        chain = {&w, 6};
        wl_event_loop_add_idle(w.loop, chained, &chain);
        checks_left = 3;
        wl_event_loop_dispatch(w.loop, 100);
    }
    uint64_t intervals = w.examined.size();
    if (checked) wl_event_source_remove(checked);
    for (auto *s : sources) wl_event_source_remove(s);
    wl_event_source_remove(timer);
    for (auto &p : pipes) { close(p[0]); close(p[1]); }
    close(quiet[0]);
    close(quiet[1]);
    char what[200];
    snprintf(what, sizeof what, "timer, pipes, check source and idle chains: at most 2 slots per wait interval (most %d over %llu intervals)",
             w.most(), (unsigned long long)intervals);
    check(what, w.most() <= 2 && intervals >= (uint64_t)rounds);
    // Every interval collects after the allowance closed in the one before, so its refill is
    // ready at the wait, and the check source collects after every refill: two per interval.
    snprintf(what, sizeof what, "... and collection keeps going: %llu slots in %d rounds", (unsigned long long)w.total, rounds);
    check(what, w.total >= 2 * (uint64_t)rounds);
}

// A collection that examined nothing leaves the allowance open; once used, it stays closed until
// a dispatch has waited, and the next collection then gets both slots.
static void refill_case()
{
    world_t w;
    w.busy = 0;
    w.collect();
    wl_event_loop_dispatch(w.loop, 0);
    wl_event_loop_dispatch(w.loop, 0);
    w.busy = 1 << 20;
    uint64_t before = w.total;
    w.collect();
    check("a collection that examined nothing leaves both slots", w.total - before == 2);
    w.collect();
    check("... after which nothing more is examined before a wait", w.total - before == 2);
    // The closing idle was queued outside a dispatch, so this one runs it before its wait and
    // the refill after it.
    wl_event_loop_dispatch(w.loop, 0);
    before = w.total;
    w.collect();
    check("one dispatch refills it: two more slots", w.total - before == 2);
}

// Destroyed with its closing idle still queued (a goo stopped mid-collection), then the loop runs.
static void teardown_case()
{
    world_t w;
    w.collect();
    w.allowance.reset();
    wl_event_loop_dispatch(w.loop, 0);
    check("destroyed with its closing idle queued: the loop runs on", true);
}

// No descriptor available: nothing may be collected, and the goo uses its timed fallback.
static void unusable_case()
{
    auto loop = wl_event_loop_create();
    int next = open("/dev/null", O_RDONLY | O_CLOEXEC);  // the descriptor eventfd would take
    if (!loop || next < 0) { check("descriptor limit fixture", false); return; }
    close(next);
    rlimit saved{};
    getrlimit(RLIMIT_NOFILE, &saved);
    rlimit low = saved;
    low.rlim_cur = next;
    bool unusable = false, closed = false;
    if (setrlimit(RLIMIT_NOFILE, &low) == 0)
    {
        collect_allowance_t a(loop);
        unusable = !a.usable();
        closed = a.left == 0;
        setrlimit(RLIMIT_NOFILE, &saved);
    }
    check("no eventfd: the allowance is unusable and lends no slot", unusable && closed);
    wl_event_loop_destroy(loop);
}

int main()
{
    review_case();
    mixed_case();
    refill_case();
    teardown_case();
    unusable_case();
    check("the event loop's waits were observed", waits > 0);
    printf("%d failure(s)\n", failures);
    return failures ? 1 : 0;
}
