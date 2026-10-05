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
    handover::seen_t seen;
    fail_after = 0;
    bool valid = handover::valid_descriptor(fd, getpid(), seen);
    bool twice = handover::valid_descriptor(fd, getpid(), seen);
    bool other = handover::valid_descriptor(fd, getpid() + 1, seen);
    fail_after = -1;
    check("descriptor validation with every allocation failing: the live pidfd of the recorded pid is valid once", valid && !twice && !other);

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

// 4. Adoption: the destination takes the lease before the pending owner lets go (the order
// take_handover() uses), so a failing insert leaves it with an owner that returns it.
static void adoption_order()
{
    for (bool fail : {true, false})
    {
        int disables = 1;  // the outgoing copy's disable on the window's root node
        std::set<uint64_t> disabled_nodes;
        handover::pending_t pending;
        pending.own_lease(77);
        try
        {
            if (fail) fail_after = 0;
            disabled_nodes.insert(77);
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

// 5. The worker: framework allocation failures never leave its thread.
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
    adoption_order();
    worker();
    printf("%s (%ld injected allocation failures)\n", failures ? "FAILED" : "all fault checks passed", failed_allocations.load());
    return failures ? 1 : 0;
}
