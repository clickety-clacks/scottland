// Main-loop timing, the diagnostic ring and the watchdog (docs/main-loop.md, ML1-ML7).
//
// Every entry from Wayfire into Scottland opens a scope:
//     SCOTTLAND_LOOP_SCOPE(on_key);
// Scopes nest; only the outermost counts toward ML2 and the ring. Nothing here writes to stderr
// or Wayfire's log after init(): diagnostics go to a file in the runtime directory that an
// external reader (scottland-loop-read) prints. Main thread only, except the watchdog.
#pragma once
#include "loop-abi.hpp"
#include <algorithm>
#include <array>
#include <atomic>
#include <condition_variable>
#include <functional>
#include <cstdint>
#include <mutex>
#include <string>
#include <thread>
#include <time.h>

struct wl_event_loop;
struct wl_event_source;

namespace scottland::loop
{
enum class kind_t : uint8_t { work, diagnostic };

enum class scope_id : uint32_t
{
    none = 0,
#define SCOPE(name, kind) name,
#define NOTE(name, text)
#include "loop-table.def"
#undef SCOPE
#undef NOTE
    count,
};

enum class note_id : uint32_t
{
    none = 0,
#define SCOPE(name, kind)
#define NOTE(name, text) name,
#include "loop-table.def"
#undef SCOPE
#undef NOTE
    count,
};

struct scope_info_t { const char *name; kind_t kind; };
struct note_info_t { const char *name; const char *text; };
extern const scope_info_t scope_info[(size_t)scope_id::count];
extern const note_info_t note_info[(size_t)note_id::count];

inline uint64_t now_ns()
{
    timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000000000ull + (uint64_t)t.tv_nsec;
}

constexpr uint64_t ms = 1000000;
constexpr uint64_t slow_callback_ns = 8 * ms;     // a ring record
constexpr uint64_t budget_ns = 2 * ms;            // ML1
constexpr uint64_t window_ns = 16700000;          // ML2's interval
constexpr uint64_t ml2_budget_ns = 4 * ms;         // ML2: callbacks in any such interval
constexpr uint64_t active_window_ns = 1000 * ms;  // the watchdog's fast rate lasts this long after work

struct scope_stats_t { uint64_t calls = 0, total_ns = 0, max_ns = 0, over_budget = 0, outermost = 0; };
struct history_t { uint32_t id; uint64_t start_ns, duration_ns; };

/** Failure injection for tests (SCOTTLAND_TEST_MODEL only), read once in start(). */
struct faults_t { bool ring = false, mlock = false, eventfd = false, thread = false, event_source = false; };

class monitor_t
{
  public:
    // Start in init(): maps the ring (re-mapping a previous copy's), writes the names file,
    // starts the watchdog. Every failure leaves a working monitor with fewer channels.
    void start(wl_event_loop *loop, const std::string& ring_path, uint64_t session, const faults_t& faults);
    // In fini(), last: stops the watchdog, removes the event source, unmaps. Idempotent.
    void stop(bool remove_files);

    void enter(scope_id id, uint64_t at);
    void exit(scope_id id, uint64_t at, uint64_t started);
    void note(note_id id, uint64_t a1, uint64_t a2, uint64_t a3, uint64_t a4);
    // brief: counters only, without the history and the ML2 episodes (a caller that resets and discards).
    std::string stats_json(bool reset, bool brief = false);
    // The scopes whose time ML2's unexcused occupancy leaves out (the latency test's open
    // exceptions), replacing any earlier set; false for a name no scope has.
    void clear_excused();
    bool excuse(const std::string& name);

    uint32_t depth = 0;
    uint64_t build_id = 0;
    // Main thread, from the heartbeat handler: a periodic check that runs whatever the loop is
    // doing (the worker's `broken` flag is observed here as well as on submit).
    std::function<void()> heartbeat_hook;
    bool ring_on() const { return ring != nullptr; }
    bool watchdog_on() const { return watchdog.joinable(); }

  private:
    void *ring = nullptr;
    uint64_t instance = 0;
    bool mlock_failed = false;
    std::string ring_file;

    // Per scope id; only the outermost scope counts toward ML2 and the history.
    std::array<scope_stats_t, (size_t)scope_id::count> stats{};
    uint32_t slowest_child = 0;
    uint64_t slowest_child_ns = 0;
    uint64_t last_work_end_ns = 0;   // main-thread copy
    uint64_t notes = 0, slow_records = 0;

    // ML2: outermost intervals intersecting the last 16.7 ms; the sum of their clipped lengths,
    // and the same sum without the excused scopes, evaluated at every outermost exit. Outermost
    // intervals do not overlap, so only the oldest one can start before the window.
    struct ml2_interval_t { uint64_t start, end; uint32_t id; };
    static constexpr size_t ml2_capacity = 8192;
    std::array<ml2_interval_t, ml2_capacity> ml2{};
    size_t ml2_head = 0, ml2_size = 0;
    uint64_t ml2_sum = 0, ml2_max = 0, ml2_max_at = 0;
    std::array<bool, (size_t)scope_id::count> excused{};
    uint64_t ml2_unexcused_sum = 0, ml2_unexcused_max = 0, ml2_unexcused_max_at = 0;
    // Intervals dropped from a full queue while still inside the window: the sums at those
    // exits are too low, so ML2 is unknown for them.
    uint64_t ml2_lost = 0;
    // Each run of windows whose unexcused occupancy is over ML2's budget (an episode), with the
    // scopes in its worst window (tests/mainloop-latency-test fails on every episode).
    struct ml2_episode_t
    {
        uint64_t peak = 0, total = 0, at = 0;                   // unexcused and all, ns
        std::array<std::pair<uint32_t, uint64_t>, 8> scopes{};  // the largest contributors, ns
        uint64_t other = 0;                                     // the rest of the window, ns
    };
    bool ml2_over = false;
    ml2_episode_t episode;
    std::array<ml2_episode_t, 1024> episodes{};  // per reset: a latency scenario fits
    uint64_t episode_count = 0;
    void ml2_compose(uint64_t window_start, ml2_episode_t& into) const;

    std::array<history_t, 256> history{};
    uint64_t history_count = 0;

    void sample_begin();
    void sample_end();
    void record_a(uint32_t kind, uint32_t id, uint64_t child, uint64_t time, uint64_t duration, uint64_t value);

    // Watchdog (4.3). Its own mutex guards stopping and mode_changed.
    std::thread watchdog;
    std::mutex watch_mutex;
    std::condition_variable watch_cv;
    bool watch_stopping = false, mode_changed = false;
    int heartbeat_fd = -1;
    wl_event_source *heartbeat_source = nullptr;
    void watch();
    void heartbeat();
    static int on_heartbeat(int fd, uint32_t mask, void *data);
    // Watchdog-private.
    uint64_t hb_sent = 0, hb_sent_ns = 0;
    uint64_t stuck_start = 0, stuck_next = 0, late_since = 0, late_next = 0;
    // Watchdog counters, read by loop-stats.
    std::atomic<uint64_t> rounds{0}, fast_rounds{0}, heartbeats{0}, stuck_records{0},
        unresponsive_records{0}, unavailable_rounds{0};
    std::array<std::atomic<uint64_t>, 32> round_ns{};
    std::atomic<uint64_t> last_round_ns{0};
    // Main-thread counters.
    uint64_t heartbeats_acked = 0;
};

/** This plugin copy's monitor (each copy has its own: -fno-gnu-unique). Null outside init..fini. */
inline monitor_t *current = nullptr;

class scope_t
{
  public:
    explicit scope_t(scope_id id) : id(id), monitor(current)
    {
        if (monitor)
        {
            started = now_ns();
            monitor->enter(id, started);
        }
    }

    ~scope_t()
    {
        if (monitor) monitor->exit(id, now_ns(), started);
    }

    scope_t(const scope_t&) = delete;
    scope_t& operator =(const scope_t&) = delete;

  private:
    scope_id id;
    monitor_t *monitor;
    uint64_t started = 0;
};

/** A diagnostic that would once have gone to the log: a ring record with up to four numbers. */
inline void note(note_id id, uint64_t a1 = 0, uint64_t a2 = 0, uint64_t a3 = 0, uint64_t a4 = 0)
{
    if (current) current->note(id, a1, a2, a3, a4);
}

/** Two signed 32-bit halves (a position or a size) in one note argument. */
inline uint64_t xy(double x, double y)
{
    auto half = [] (double v) { return (uint64_t)(uint32_t)(int32_t)std::clamp(v, -2147483648.0, 2147483647.0); };
    return half(x) | half(y) << 32;
}

/** First 8 bytes of this library's GNU build id (0 if it has none). */
uint64_t own_build_id();
/** The names file beside the ring: "<instance> <build id>" then one line per scope and note. */
std::string names_text(uint64_t instance, uint64_t build);
uint64_t hash(const std::string& text);
}

#define SCOTTLAND_LOOP_CAT2(a, b) a##b
#define SCOTTLAND_LOOP_CAT(a, b) SCOTTLAND_LOOP_CAT2(a, b)
#define SCOTTLAND_LOOP_SCOPE(name) \
    ::scottland::loop::scope_t SCOTTLAND_LOOP_CAT(loop_scope_, __LINE__)(::scottland::loop::scope_id::name)
