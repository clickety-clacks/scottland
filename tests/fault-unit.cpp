// Allocation failures where an exception would lose ownership or end the compositor
// (docs/main-loop.md "Reload" and "Worker"; Astra's implementation review, findings 1 and 3).
// operator new is replaced: a thread can make its allocations fail, all of them or from the Nth
// on. Run by tests/fault-unit.sh (not under ThreadSanitizer, which has its own operator new).
#include "handover.hpp"
#include "pure/shrink.hpp"
#include "pure/worker.hpp"
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <exception>
#include <map>
#include <memory>
#include <new>
#include <poll.h>
#include <set>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>

// -1: allocations succeed. 0: this thread's allocations fail from now on. N: after N more.
static thread_local long fail_after = -1;
static std::atomic<long> failed_allocations{0};
void *operator new(std::size_t n)
{
    if (fail_after == 0)
    {
        failed_allocations++;
        throw std::bad_alloc();
    }
    if (fail_after > 0) fail_after--;
    if (void *p = std::malloc(n ? n : 1)) return p;
    throw std::bad_alloc();
}
void *operator new[](std::size_t n) { return operator new(n); }
void operator delete(void *p) noexcept { std::free(p); }
void operator delete(void *p, std::size_t) noexcept { std::free(p); }
void operator delete[](void *p) noexcept { std::free(p); }
void operator delete[](void *p, std::size_t) noexcept { std::free(p); }

using namespace scottland;

static int failures = 0;
static void check(const char *what, bool ok)
{
    printf("%s  %s\n", ok ? "PASS" : "FAIL", what);
    fflush(stdout);
    failures += !ok;
}

static bool open_fd(int fd) { return fcntl(fd, F_GETFD) >= 0; }
static int pidfd_self() { return (int)syscall(SYS_pidfd_open, getpid(), 0); }

// A failure here would be std::terminate: the test reports it instead of dying silently.
static void on_terminate()
{
    printf("FAIL  std::terminate reached (an exception escaped a noexcept boundary or a thread)\n");
    fflush(stdout);
    std::_Exit(86);
}

// 1. Acquisition owns what the list names with every allocation failing.
static void acquisition()
{
    int fd = pidfd_self();
    handover::list_t list;
    snprintf(list.id, sizeof(list.id), "%s", "0123456789abcdef");
    list.version = 42;
    list.add_fd(fd);
    list.add_lease(123);
    setenv(handover::environment, handover::format_list(list).c_str(), 1);
    setenv("SCOTTLAND_INTERNAL_MODEL_VERSION", "42", 1);

    handover::pending_t pending;
    handover::list_t found;
    fail_after = 0;
    bool listed = handover::acquire(pending, found);
    fail_after = -1;
    check("acquire with every allocation failing: the list is consumed and named this reload", listed && !getenv(handover::environment) &&
        strcmp(found.id, "0123456789abcdef") == 0);
    check("... its handle and lease are owned (2 resources), the handle still open",
        pending.size() == 2 && pending.holds_fd(fd) && pending.holds_lease(123) && open_fd(fd));

    // Validation of an entry's number, also without allocating.
    int file = open("/proc/self/stat", O_RDONLY | O_CLOEXEC);
    fail_after = 0;
    bool valid = handover::valid_descriptor(fd, getpid());
    bool other = handover::valid_descriptor(fd, getpid() + 1);
    bool unrecorded = handover::valid_descriptor(fd, 0);
    bool not_pidfd = handover::valid_descriptor(file, getpid());
    fail_after = -1;
    close(file);
    check("descriptor validation with every allocation failing: only the live pidfd of the recorded pid is valid",
        valid && !other && !unrecorded && !not_pidfd);

    // An entry that is never adopted: one close, one enable.
    std::set<uint64_t> enabled;
    pending.dispose([] (void *c, uint64_t w) { static_cast<std::set<uint64_t>*>(c)->insert(w); }, &enabled);
    check("disposal closes the handle and returns the lease exactly once",
        !open_fd(fd) && enabled == std::set<uint64_t>{123} && pending.returned_leases == 1 && pending.size() == 0);
    pending.dispose(nullptr, nullptr);
    check("... and a second disposal returns nothing again", pending.returned_leases == 1);
}

// 2. Lists that own nothing: malformed, stale against the model version, over capacity.
static void untrusted_lists()
{
    int fd = pidfd_self();
    char text[128];
    for (auto [what, manifest, version] : {
             std::tuple{"malformed", "abc;version=x;fds=;leases=", "1"},
             std::tuple{"stale (an older build loaded in between)", "abc;version=7;fds=FD;leases=5", "8"},
             std::tuple{"past-INT_MAX fd", "abc;version=8;fds=99999999999;leases=5", "8"}})
    {
        std::string m = manifest;
        auto at = m.find("FD");
        if (at != std::string::npos) m.replace(at, 2, std::to_string(fd));
        snprintf(text, sizeof(text), "%s", m.c_str());
        setenv(handover::environment, text, 1);
        setenv("SCOTTLAND_INTERNAL_MODEL_VERSION", version, 1);
        handover::pending_t pending;
        handover::list_t list;
        bool listed = handover::acquire(pending, list);
        check((std::string("a ") + what + " list owns nothing and is removed").c_str(),
            !listed && pending.size() == 0 && !getenv(handover::environment) && open_fd(fd));
    }
    std::string many = "abc;version=1;fds=;leases=";
    for (size_t i = 0; i <= handover::max_transfer; i++) many += (i ? "," : "") + std::to_string(i + 1);
    handover::list_t list;
    check("a list naming more than the transfer limit is rejected whole", !handover::parse_list(many.c_str(), list));
    close(fd);
}

// 3. Export: every duplicated handle has an owner from the moment it exists.
static void export_duplicates()
{
    int source = pidfd_self();
    int a = -1, b = -1;
    try
    {
        handover::duplicates_t duplicates;
        a = duplicates.dup(source);
        b = duplicates.dup(source);
        fail_after = 0;
        std::string grows(64, 'x');  // the exporter's next allocation (a JSON entry) fails
        fail_after = -1;
        duplicates.release();
    } catch (const std::bad_alloc&)
    {
        fail_after = -1;
    }
    check("an allocation failure while exporting closes every handle duplicated so far", a >= 0 && b >= 0 && !open_fd(a) && !open_fd(b));
    int kept = -1;
    {
        handover::duplicates_t duplicates;
        kept = duplicates.dup(source);
        duplicates.release();  // published: the next copy owns it
    }
    check("a published duplicate stays open for the next copy", open_fd(kept));
    close(kept);
    close(source);
}

// ... and a failed export is undone with allocation still failing (Astra's re-review, A).
static void export_failure()
{
    char dir[] = "/tmp/scottland-fault-export.XXXXXX";
    if (!mkdtemp(dir))
    {
        check("export failure: a scratch directory", false);
        return;
    }
    int source = pidfd_self();
    int made[3] = {-1, -1, -1};
    bool escaped = false;
    long before = failed_allocations.load();
    handover::export_t out;
    out.path = std::string(dir) + "/.widget-handover.json";  // prepared before the first handle
    out.tmp = out.path + ".tmp";
    try
    {
        for (auto& fd : made) out.list.add_fd(fd = out.duplicates.dup(source));
        FILE *tmp = fopen(out.tmp.c_str(), "w");
        if (tmp) fclose(tmp);
        FILE *published = fopen(out.path.c_str(), "w");
        if (published) fclose(published);
        setenv(handover::environment, "partial", 1);
        fail_after = 0;  // from here on every allocation fails, the undoing included
        std::string entry(64, 'x');
        out.publish();
    } catch (const std::bad_alloc&)
    {
        out.fail();
    } catch (...)
    {
        escaped = true;
    }
    long refused = failed_allocations.load() - before;
    fail_after = -1;
    bool closed = true;
    for (int fd : made) closed = closed && fd >= 0 && !open_fd(fd);
    check("export failing with allocation failing throughout: every duplicate closed, both files and the list removed",
        !escaped && closed && access(out.tmp.c_str(), F_OK) != 0 && access(out.path.c_str(), F_OK) != 0 &&
        !getenv(handover::environment) && !out.published && refused > 0);
    check("... and the failed export hands over no window", !out.hands_over(0) && !out.hands_over(1));
    rmdir(dir);
    close(source);
}

// 4. Unload returns every lease but the handed-over ones, in place, with allocation failing.
static void lease_return()
{
    std::map<uint64_t, std::weak_ptr<int>> leases;
    std::vector<std::shared_ptr<int>> windows;  // each window's disable count: -1 while leased
    for (uint64_t id = 1; id <= 300; id++)
    {
        windows.push_back(std::make_shared<int>(-1));
        leases.emplace(id, windows.back());
    }
    windows[9].reset();  // window 10 was destroyed with its lease: nothing to enable
    handover::list_t kept;
    for (uint64_t id : {3, 4, 250}) kept.add_lease(id);
    std::vector<uint64_t> failed;
    failed.reserve(8);
    fail_after = 0;
    handover::return_leases(leases,
        [&] (uint64_t w) noexcept { for (size_t i = 0; i < kept.lease_count; i++) if (kept.leases[i] == w) return true; return false; },
        [&] (const std::shared_ptr<int>& count)
        {
            ++*count;
            if (count == windows[4]) throw std::bad_alloc();  // window 5: fails after Wayfire counted it
            if (count == windows[19])  // window 20's enable releases window 21's lease itself (re-entry)
            {
                leases.erase(21);
                ++*windows[20];
            }
        },
        [&] (uint64_t w) noexcept { if (failed.size() < failed.capacity()) failed.push_back(w); });
    fail_after = -1;
    bool balanced = true;
    for (uint64_t id = 1; id <= 300; id++)
    {
        if (!windows[id - 1]) continue;
        bool keep = id == 3 || id == 4 || id == 250;
        balanced = balanced && *windows[id - 1] == (keep ? -1 : 0);
    }
    check("lease return with every allocation failing: each lease returned exactly once, the handed-over three kept",
        balanced && leases.size() == 3 && leases.count(3) && leases.count(4) && leases.count(250));
    check("... a failing enable is reported once and the rest still return", failed == std::vector<uint64_t>{5});
}

// 5. Ownership of a parsed file's entries on both sides of the capacity, with real pidfds
// (Astra's re-review, B). Every lease and handle is held, or returned and closed at once, once.
struct entry_t
{
    uint64_t window = 0;
    int64_t pid = 0;
    int pidfd = -1;
    bool descriptor = false;
};

static void legacy_capacity()
{
    for (auto [limit, count] : {std::pair{handover::max_transfer, handover::max_transfer},
             std::pair{handover::max_transfer, handover::max_transfer + 1}, std::pair{handover::max_transfer, size_t(300)},
             std::pair{size_t(1), size_t(3)}})
    {
        std::vector<entry_t> entries;
        for (size_t i = 0; i < count; i++) entries.push_back({i + 1, getpid(), pidfd_self(), false});
        entries.push_back(entries[0]);  // a window and number named twice are one resource
        size_t held = std::min(count, limit);
        std::vector<int> returned(count + 1, 0), excess(count + 1, 0);  // per window; sized now
        handover::pending_t pending;
        pending.limit = limit;
        long before = failed_allocations.load();
        fail_after = 0;
        handover::own_entries(pending, entries, true,
            [&] (uint64_t w) noexcept { returned[w]++; },
            [&] (uint64_t w) noexcept { excess[w]++; });
        fail_after = -1;
        bool exact = failed_allocations.load() == before;  // nothing even tried to allocate
        for (size_t i = 0; i < count; i++)
        {
            uint64_t w = entries[i].window;
            bool kept = i < held;
            exact = exact && entries[i].descriptor == kept && open_fd(entries[i].pidfd) == kept &&
                returned[w] == (kept ? 0 : 1) && excess[w] == (kept ? 0 : 2);
        }
        exact = exact && !entries[count].descriptor && pending.size() == 2 * held;
        char what[200];
        snprintf(what, sizeof(what), "legacy ownership of %zu entries (capacity %zu) with every allocation failing: "
            "%zu held, %zu returned and closed at once, a repeat is no resource", count, limit, held, count - held);
        check(what, exact);

        pending.dispose([] (void *c, uint64_t w) { (*static_cast<std::vector<int>*>(c))[w]++; }, &returned);
        bool once = true;
        for (size_t i = 0; i < count; i++) once = once && returned[i + 1] == 1 && !open_fd(entries[i].pidfd);
        check("... then disposal returns each held lease and closes each held handle: every window once", once && pending.size() == 0);
    }

    // The new format owns only the handles the environment list transferred, and no lease here.
    std::vector<entry_t> entries;
    for (uint64_t w : {1, 2}) entries.push_back({w, getpid(), pidfd_self(), false});
    handover::pending_t pending;
    pending.own_fd(entries[0].pidfd);
    int gave = 0;
    fail_after = 0;
    handover::own_entries(pending, entries, false, [&] (uint64_t) noexcept { gave++; }, [&] (uint64_t) noexcept { gave++; });
    fail_after = -1;
    check("new format: an entry's handle is the transferred one or none, and nothing is returned or closed",
        entries[0].descriptor && !entries[1].descriptor && gave == 0 && open_fd(entries[1].pidfd) && pending.size() == 1);
    pending.dispose(nullptr, nullptr);
    close(entries[1].pidfd);
}

// 6. Adoption: the destination takes the lease before the pending owner lets go (the order
// take_handover() uses), so a failing insert leaves it with an owner that returns it.
static void adoption_order()
{
    for (bool fail : {true, false})
    {
        int disables = 1;  // the outgoing copy's disable on the window's root node
        auto view = std::make_shared<int>(0);
        std::map<uint64_t, std::weak_ptr<int>> disabled_nodes;
        handover::pending_t pending;
        pending.own_lease(77);
        try
        {
            if (fail) fail_after = 0;
            disabled_nodes.emplace(77, view);
            fail_after = -1;
            pending.claim_lease(77);
        } catch (const std::bad_alloc&)
        {
            fail_after = -1;
        }
        pending.dispose([] (void *c, uint64_t) { --*static_cast<int*>(c); }, &disables);
        if (fail) check("adoption failing at the lease insert: the lease is returned once (disables 1 -> 0)", disables == 0 && disabled_nodes.empty());
        else check("adoption succeeding: the destination holds the one disable", disables == 1 && disabled_nodes.count(77));
    }
}

// 7. The worker: framework allocation failures never leave its thread.
namespace scottland::work
{
struct value_t : result_t { int value; explicit value_t(int v) : value(v) {} };
// Arms this worker thread's allocation failure: from the Nth allocation after its first step.
struct arming_job_t : job_t
{
    long after;
    bool armed = false;
    explicit arming_job_t(long after) : after(after) {}
    bool step(cancel_t& cancel) override
    {
        cancel.charge(1);
        if (!armed)
        {
            armed = true;
            fail_after = after;
            return false;  // yield: the framework runs between this step and the next
        }
        return true;
    }
    std::unique_ptr<result_t> result(outcome_t& outcome) override
    {
        outcome = outcome_t::finished;
        return std::make_unique<value_t>(1);
    }
};
struct healing_job_t : job_t
{
    bool step(cancel_t& cancel) override { fail_after = -1; cancel.charge(1); return true; }
    std::unique_ptr<result_t> result(outcome_t& outcome) override
    {
        outcome = outcome_t::finished;
        return std::make_unique<value_t>(2);
    }
};
}

static void worker()
{
    using namespace scottland::work;
    bool all_safe = true, some_failed = false;
    for (long after = 0; after < 6; after++)
    {
        worker_t w("fault", 1000);
        w.start();
        auto lane = w.open_lane("fault", policy_t::exact);
        int delivered = 0, failed = 0;
        lane->accept = [&] (const done_t& d) { failed += d.outcome == outcome_t::failed; return d.outcome != outcome_t::failed; };
        lane->deliver = [&] (done_t& d) { delivered += static_cast<value_t&>(*d.result).value; };
        lane->submit(std::make_unique<arming_job_t>(after), now_ns());
        for (int i = 0; i < 100 && !failed && !delivered && !w.is_broken(); i++)
        {
            pollfd p{w.event_fd(), POLLIN, 0};
            if (poll(&p, 1, 10) > 0) w.deliver();
        }
        // The worker either delivered (failing a job is the job's), or failed the job, or made
        // itself unavailable. In every case its thread is alive or joinable, and a later job runs
        // or is refused, never crashes.
        bool next = lane->submit(std::make_unique<healing_job_t>(), now_ns());
        for (int i = 0; next && i < 100 && delivered < 2; i++)
        {
            pollfd p{w.event_fd(), POLLIN, 0};
            if (poll(&p, 1, 10) > 0) w.deliver();
        }
        bool outcome_known = failed == 1 || delivered >= 1 || w.is_broken();
        bool afterwards = next ? delivered >= 2 : !w.available();
        all_safe &= outcome_known && afterwards;
        some_failed |= failed == 1;
        w.stop();
        all_safe &= !w.available();
        printf("      worker thread allocations failing from #%ld: failed=%d delivered=%d broken=%d next=%s\n",
            after, failed, delivered, (int)w.is_broken(), next ? "ran" : "refused");
    }
    check("allocation failures on the worker thread fail a job or the worker, never the process; stop joins", all_safe);
    check("... an allocation failing in a job's result is delivered as that job's failure", some_failed);

    // A failure of the framework itself (injected inside its iteration, outside any job): the
    // worker marks itself broken and refuses work; its thread ends and stop joins and cleans up.
    {
        worker_t w("fault", 1000);
        w.start({false, false, true});
        auto lane = w.open_lane("a", policy_t::exact);
        lane->accept = [] (const done_t&) { return true; };
        bool first = lane->submit(std::make_unique<healing_job_t>(), now_ns());
        for (int i = 0; i < 200 && !w.is_broken(); i++) usleep(5000);
        bool refused = !lane->submit(std::make_unique<healing_job_t>(), now_ns());
        w.stop();
        check("a framework failure inside the worker's loop: broken, refuses work, joined", first && w.is_broken() && refused && !w.available());
    }

    // The main thread's start: creating the thread itself fails.
    worker_t w("fault", 1000);
    fail_after = 0;
    bool started = w.start();
    fail_after = -1;
    auto lane = w.open_lane("a", policy_t::exact);
    check("thread creation failing for memory: an unavailable worker that refuses work",
        !started && !w.available() && !lane->submit(std::make_unique<healing_job_t>(), now_ns()));
    w.stop();
}

int main()
{
    std::set_terminate(on_terminate);
    acquisition();
    untrusted_lists();
    export_duplicates();
    export_failure();
    lease_return();
    legacy_capacity();
    adoption_order();
    worker();
    printf("%s (%ld injected allocation failures)\n", failures ? "FAILED" : "all fault checks passed", failed_allocations.load());
    return failures ? 1 : 0;
}
