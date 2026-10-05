// Worker threads for pure computation off the main loop (docs/main-loop.md "Worker"; design
// 3.1-3.8). Standard library only: no Wayfire, wlroots or GL object ever reaches a worker. The
// plugin adds the event fd to Wayfire's loop and calls deliver() from it.
//
// A job runs in steps of at most one allowance of work units, charged before each unit
// operation, so cancellation is checked between any two. Each consumer owns a lane: one pending,
// one running and one finished slot. Tickets (every submit and cancel) and epochs (the
// consumer's invalidating events) decide whether a running job is cancelled (by the lane's
// policy) and whether a finished result is still wanted (the consumer's acceptance rule).
#pragma once
#include <array>
#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <functional>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <vector>

namespace scottland::work
{
uint64_t now_ns();

enum class policy_t { exact, latest_completed };
enum class outcome_t { finished, capped, failed };

struct lane_t;

/** What a running job asks between unit operations. */
class cancel_t
{
  public:
    cancel_t(const lane_t *lane, uint64_t ticket, uint64_t epoch, const std::atomic<bool> *stopping, uint64_t allowance)
        : allowance(allowance), lane(lane), ticket(ticket), epoch(epoch), stopping(stopping) {}
    /** Charge `units` operations before doing them: false = stop here (cancelled, or this
     *  step's allowance is spent; the job resumes at its next step). */
    bool charge(uint64_t units = 1);
    bool cancelled() const;
    uint64_t charged() const { return used; }
    // Tests only: an allowance of 1 unit per step must give the same result as an unlimited one.
    uint64_t allowance;

  private:
    const lane_t *lane;
    uint64_t ticket, epoch;
    const std::atomic<bool> *stopping;
    uint64_t used = 0;
};

struct result_t
{
    virtual ~result_t() = default;
};

class job_t
{
  public:
    virtual ~job_t() = default;
    /** At most one step allowance of work. True when finished, completely or capped. */
    virtual bool step(cancel_t& cancel) = 0;
    /** Once, after step() returned true. */
    virtual std::unique_ptr<result_t> result(outcome_t& outcome) = 0;
};

struct done_t
{
    std::unique_ptr<result_t> result;
    outcome_t outcome = outcome_t::finished;
    uint64_t ticket = 0, epoch = 0;
    uint64_t snapshot_ns = 0, start_ns = 0, finish_ns = 0;
};

struct lane_stats_t
{
    std::atomic<uint64_t> submitted{0}, superseded{0}, cancelled{0}, capped{0}, failed{0}, delivered{0}, stale{0};
};

struct lane_t
{
    policy_t policy = policy_t::exact;
    std::atomic<uint64_t> ticket{0}, epoch{0};
    std::atomic<bool> closed{false};
    lane_stats_t stats;
    // Under the worker's mutex.
    struct slot_t { std::unique_ptr<job_t> job; uint64_t ticket = 0, epoch = 0, snapshot_ns = 0, start_ns = 0; };
    std::optional<slot_t> pending;
    std::unique_ptr<done_t> done;
    // The worker's own (never touched by the main thread).
    std::optional<slot_t> running;
};

class worker_t;

/** A consumer's lane, main thread only. Accept and deliver are never visible to the worker. */
class lane_handle_t
{
  public:
    ~lane_handle_t() { close(); }
    /** Replace the pending job. False if the worker is unavailable (the consumer keeps its
     *  safe state; nothing runs on the main loop). */
    bool submit(std::unique_ptr<job_t> job, uint64_t snapshot_ns);
    void cancel();
    /** The consumer's invalidating event: a running or finished result of an older epoch is not wanted. */
    void bump_epoch();
    uint64_t ticket() const { return lane->ticket.load(); }
    uint64_t epoch() const { return lane->epoch.load(); }
    /** Idempotent. After it nothing of this lane is delivered; its results are destroyed. */
    void close();

    std::function<bool(const done_t&)> accept;
    std::function<void(done_t&)> deliver;
    std::string name;
    // Ages, main thread: snapshot to start, start to finish, finish to deliver, snapshot to deliver.
    struct age_t { uint64_t last = 0, max = 0; std::array<uint64_t, 256> recent{}; uint64_t count = 0; void add(uint64_t ns); uint64_t p95() const; };
    age_t queue_age, run_age, deliver_age, total_age;

  private:
    friend class worker_t;
    std::shared_ptr<lane_t> lane;
    worker_t *worker = nullptr;
};

struct worker_faults_t { bool eventfd = false, thread = false; };

class worker_t
{
  public:
    explicit worker_t(std::string name, uint64_t step_units);
    ~worker_t();
    /** Starts the thread. False leaves an unavailable worker: every submit fails. */
    bool start(const worker_faults_t& faults);
    bool start() { return start(worker_faults_t{}); }
    /** Idempotent and correct in every partial state: stopping, notify, join; results of every
     *  lane are destroyed on this thread. Returns the join time. */
    uint64_t stop();
    std::unique_ptr<lane_handle_t> open_lane(const std::string& name, policy_t policy);
    int event_fd() const { return fd; }
    /** On the main loop, when event_fd() is readable: at most `budget_ns` of deliveries, then
     *  the eventfd is signalled again for the rest. */
    void deliver(uint64_t budget_ns = 1000000);
    bool available() const { return thread.joinable() && !broken.load() && !stopping.load(); }
    bool is_broken() const { return broken.load(); }
    std::string stats_json() const;
    uint64_t last_join_ns = 0;
    const std::string name;

  private:
    friend class lane_handle_t;
    void run();
    void signal();
    void close(lane_handle_t& handle);
    uint64_t step_units;
    int fd = -1;
    std::thread thread;
    mutable std::mutex m;
    std::condition_variable cv;
    std::atomic<bool> stopping{false}, broken{false};
    std::vector<std::shared_ptr<lane_t>> lanes;  // under m; at most 16
    std::vector<lane_handle_t*> handles;         // main thread only
};
}
