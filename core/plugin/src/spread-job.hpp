#pragma once
// Runs one spread solve in bounded slices on the thread that calls step() (P8). Until the
// plugin's main-loop worker exists (branch mainloop-impl), the compositor calls step() from its
// event loop with a short allowance; the worker can later call the same step() on its own
// thread instead (one thread per job: a job must not change threads between steps).
//
// The solver is written straight through; it is suspended inside its unit operations
// (candidate construction, sorts, sweeps, validation), wherever a work charge finds the slice's
// time used up. The suspension point is a context switch onto the job's own stack (ucontext),
// so no solver state has to be rebuilt between slices and a solve cut at a fixed amount of work
// is the same whatever the slice length. Destroying an unfinished job resumes it once with a
// cancellation, which unwinds the solve normally (its destructors run) before the stack is freed.
#include "spread.hpp"
#include <chrono>
#include <cstdint>
#include <optional>
#include <sys/mman.h>
#include <ucontext.h>
#include <unistd.h>

namespace scottland::spread
{
class job_t
{
  public:
    using clock = std::chrono::steady_clock;

    explicit job_t(snapshot_t snap) : snapshot(std::move(snap))
    {
        page = size_t(sysconf(_SC_PAGESIZE));
        void *memory = mmap(nullptr, STACK + page, PROT_READ | PROT_WRITE,
            MAP_PRIVATE | MAP_ANONYMOUS | MAP_STACK, -1, 0);
        if (memory == MAP_FAILED) { failed = true; done = true; return; }
        stack = static_cast<char*>(memory);
        mprotect(stack, page, PROT_NONE);  // overflow faults instead of corrupting the heap
        work.yield = &job_t::yield;
        work.context = this;
        work.yield_every = 16;
    }

    ~job_t()
    {
        if (started && !done)
        {
            cancelling = true;
            swapcontext(&caller, &callee);
        }
        if (stack) munmap(stack, STACK + page);
    }

    job_t(const job_t&) = delete;
    job_t& operator =(const job_t&) = delete;

    // Run for about `allowance` (the clock is read every few work units). True when finished.
    bool step(std::chrono::nanoseconds allowance)
    {
        if (done) return true;
        auto begin = clock::now();
        deadline = begin + allowance;
        if (!started)
        {
            started = true;
            getcontext(&callee);
            callee.uc_stack.ss_sp = stack + page;
            callee.uc_stack.ss_size = STACK;
            callee.uc_link = &caller;
            auto self = reinterpret_cast<uintptr_t>(this);
            makecontext(&callee, reinterpret_cast<void(*)()>(&job_t::trampoline), 2,
                uint32_t(self >> 32), uint32_t(self & 0xffffffffu));
        }
        swapcontext(&caller, &callee);
        auto spent = clock::now() - begin;
        ++slices;
        total += spent;
        longest = std::max(longest, std::chrono::duration_cast<std::chrono::nanoseconds>(spent));
        return done;
    }

    bool finished() const { return done; }

    // The complete result when finished; before that, the best checkpoint so far, labelled as
    // stopped by the budget (never a half-evaluated arrangement).
    result_t current() const
    {
        if (done && final) return *final;
        result_t r;
        if (best)
        {
            r = *best;
            r.status = r.overlaps.empty() ? status_t::clear : status_t::overlap_budget;
        } else
        {
            r.status = failed ? status_t::unavailable : status_t::unchanged_budget;
            r.reason = failed ? "no stack for the solve" : "stopped before a checkpoint";
        }
        r.complete = false;
        r.work = work.used;
        return r;
    }

    const snapshot_t& input() const { return snapshot; }
    uint64_t work_used() const { return work.used; }

    // Measured slice lengths (P8 evidence; logged by the plugin).
    unsigned slices = 0;
    clock::duration total{0};
    std::chrono::nanoseconds longest{0};

  private:
    static constexpr size_t STACK = 512 * 1024;
    snapshot_t snapshot;
    work_t work;
    std::optional<result_t> best, final;
    ucontext_t caller{}, callee{};
    char *stack = nullptr;
    size_t page = 4096;
    clock::time_point deadline;
    bool started = false, done = false, cancelling = false, failed = false;

    static void trampoline(uint32_t high, uint32_t low)
    {
        auto self = reinterpret_cast<job_t*>((uintptr_t(high) << 32) | uintptr_t(low));
        self->body();
        // Returning switches to uc_link: the caller of the last step().
    }

    void body()
    {
        try
        {
            final = solve(snapshot, work, [this] (const result_t& r) { best = r; });
        } catch (...)
        {
            failed = true;
            final = current();
        }
        done = true;
    }

    static void yield(void *context)
    {
        auto self = static_cast<job_t*>(context);
        if (self->cancelling) throw stopped{};
        if (clock::now() < self->deadline) return;
        swapcontext(&self->callee, &self->caller);
        if (self->cancelling) throw stopped{};
    }
};
}
