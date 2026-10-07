// Diagnostic ring and watchdog without a compositor (docs/main-loop.md). Run by tests/loop-unit.sh,
// once under ThreadSanitizer (no suppressions) and once optimized for timing. A real
// wl_event_loop stands in for Wayfire's; an in-process reader thread reads the rings throughout.
#include "loop.hpp"
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <fcntl.h>
#include <fstream>
#include <string>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>
#include <vector>
#include <wayland-server-core.h>

namespace loop = scottland::loop;
namespace abi = scottland::loop::abi;

static int failures = 0;
static void check(const char *what, bool ok)
{
    printf("%s  %s\n", ok ? "PASS" : "FAIL", what);
    failures += !ok;
}

static void busy(uint64_t ms)
{
    auto until = loop::now_ns() + ms * loop::ms;
    while (loop::now_ns() < until) {}
}

static void pump(wl_event_loop *events, uint64_t ms)
{
    auto until = loop::now_ns() + ms * loop::ms;
    while (loop::now_ns() < until) wl_event_loop_dispatch(events, 5);
}

struct mapping_t
{
    void *base = nullptr;
    explicit mapping_t(const std::string& path)
    {
        int fd = open(path.c_str(), O_RDONLY | O_CLOEXEC);
        if (fd >= 0) base = mmap(nullptr, abi::file_size, PROT_READ, MAP_SHARED, fd, 0), close(fd);
        if (base == MAP_FAILED) base = nullptr;
    }
    ~mapping_t() { if (base) munmap(base, abi::file_size); }
};

static std::vector<abi::record_t> records(const void *base, size_t ring, size_t head_word, uint64_t *lost = nullptr)
{
    std::vector<abi::record_t> out;
    auto head = abi::word(base, head_word).load();
    for (auto n = head > abi::slots ? head - abi::slots : 0; n < head; n++)
    {
        abi::record_t r;
        if (abi::read_record(base, ring, n, r)) out.push_back(r);
        else if (lost) ++*lost;
    }
    return out;
}

static double json_number(const std::string& json, const std::string& key)
{
    auto at = json.find("\"" + key + "\":");
    return at == std::string::npos ? -1 : std::strtod(json.c_str() + at + key.size() + 3, nullptr);
}

int main(int argc, char **argv)
{
    bool timing = argc > 1 && std::string(argv[1]) == "--timing";
    char dir_template[] = "/tmp/scottland-loop-unit.XXXXXX";
    std::string dir = argc > 2 ? argv[2] : mkdtemp(dir_template);
    std::string path = dir + "/ring.loop";
    auto events = wl_event_loop_create();

    // 1. Producer, watchdog and an in-process reader together.
    {
        loop::monitor_t monitor;
        monitor.start(events, path, 42, {});
        loop::current = &monitor;
        check("ring and watchdog start", monitor.ring_on() && monitor.watchdog_on());
        mapping_t map(path);
        std::atomic<bool> reading{true};
        std::atomic<uint64_t> accepted{0}, lost{0};
        std::atomic<uint64_t> stuck_seen_ns{0};
        std::thread reader([&] {
            while (reading.load())
            {
                uint64_t l = 0;
                auto a = records(map.base, abi::ring_a, abi::w_head_a, &l);
                auto b = records(map.base, abi::ring_b, abi::w_head_b, &l);
                accepted += a.size() + b.size();
                lost += l;
                for (auto& r : b)
                    if (r.kind == abi::k_stuck && r.id == (uint32_t)loop::scope_id::test_loop && !stuck_seen_ns.load())
                        stuck_seen_ns = loop::now_ns();
                abi::word(map.base, abi::w_sample_seq).load();
            }
        });

        // Wraparound under a concurrent reader: many more records than slots.
        for (int i = 0; i < 3000; i++)
        {
            SCOTTLAND_LOOP_SCOPE(on_key);
            { SCOTTLAND_LOOP_SCOPE(publish_model); }
            loop::note(loop::note_id::drop, i, loop::xy(i, -i));
            if (i % 100 == 0) wl_event_loop_dispatch(events, 0);
        }
        auto a = records(map.base, abi::ring_a, abi::w_head_a);
        check("wraparound: the last 512 records of ring A are readable", a.size() == abi::slots);
        check("record order and numbering survive wraparound", a.back().n == abi::word(map.base, abi::w_head_a).load() - 1);
        check("each record carries the instance that wrote it", a.back().instance == 1);

        // A first 300 ms work scope after idle heartbeats: stuck is readable before it returns.
        pump(events, 2500);
        uint64_t scope_end;
        {
            SCOTTLAND_LOOP_SCOPE(test_loop);
            busy(300);
            scope_end = loop::now_ns();
        }
        pump(events, 300);
        check("stuck was read by the reader before the scope returned",
            stuck_seen_ns.load() && stuck_seen_ns.load() < scope_end);
        auto b = records(map.base, abi::ring_b, abi::w_head_b);
        bool stuck = false;
        for (auto& r : b) stuck |= r.kind == abi::k_stuck && r.id == (uint32_t)loop::scope_id::test_loop;
        check("a busy loop inside a scope is stuck", stuck);
        a = records(map.base, abi::ring_a, abi::w_head_a);
        check("the 300 ms callback has a slow record", a.back().kind == abi::k_slow &&
            a.back().id == (uint32_t)loop::scope_id::test_loop && a.back().duration_ns >= 300 * loop::ms);

        // A busy loop outside any scope: unresponsive, not stuck.
        auto unscoped = loop::now_ns();
        busy(1200);
        pump(events, 300);
        b = records(map.base, abi::ring_b, abi::w_head_b);
        int late = 0, stuck_after = 0;
        for (auto& r : b)
        {
            late += r.kind == abi::k_unresponsive;
            stuck_after += r.kind == abi::k_stuck && r.time_ns >= unscoped;
        }
        check("a busy loop outside every scope is unresponsive", late >= 1);
        check("... and not as stuck", stuck_after == 0);

        // A scope lasting 5 s: reports at 100 ms, 1 s and 5 s.
        if (timing)
        {
            auto long_start = loop::now_ns();
            {
                SCOTTLAND_LOOP_SCOPE(test_loop);
                busy(5200);
            }
            pump(events, 200);
            b = records(map.base, abi::ring_b, abi::w_head_b);
            std::vector<uint64_t> reports;
            for (auto& r : b)
                if (r.kind == abi::k_stuck && r.time_ns >= long_start) reports.push_back(r.value / loop::ms);
            check("a 5 s scope is reported at 100 ms, 1 s and 5 s", reports.size() == 3 &&
                reports[0] >= 100 && reports[0] < 300 && reports[1] >= 1000 && reports[1] < 1300 &&
                reports[2] >= 5000 && reports[2] < 5300);
            bool sane = true;
            for (auto& r : b) sane &= r.kind != abi::k_stuck || r.value < 60000 * loop::ms;
            check("every stuck record has a plausible age", sane);

            // Idle: only heartbeats, rounds 2 s apart.
            pump(events, 9000);
            auto stats = monitor.stats_json(false);
            auto intervals = stats.substr(stats.find("round_intervals_ms"));
            intervals = intervals.substr(intervals.find('[') + 1, intervals.find(']') - intervals.find('[') - 1);
            std::vector<double> values;
            for (size_t at = 0; at < intervals.size();)
            {
                values.push_back(std::strtod(intervals.c_str() + at, nullptr));
                auto comma = intervals.find(',', at);
                if (comma == std::string::npos) break;
                at = comma + 1;
            }
            check("idle with only heartbeats: rounds are 2 s apart", values.size() >= 3 &&
                values[values.size() - 1] > 1900 && values[values.size() - 2] > 1900 && values[values.size() - 3] > 1900);
            check("heartbeat scopes are diagnostic: they do not keep the watchdog fast",
                json_number(stats, "heartbeats_acked") > 0);
        }

        if (timing)
        {
            // Overhead of a scope: an outermost scope with a nested one inside, a million times.
            auto t0 = loop::now_ns();
            for (int i = 0; i < 1000000; i++)
            {
                SCOTTLAND_LOOP_SCOPE(frame_find_node_at);
                SCOTTLAND_LOOP_SCOPE(goo_sample_at);
            }
            auto per = double(loop::now_ns() - t0) / 1000000;
            printf("measured: an outermost scope with one nested scope costs %.0f ns\n", per);
            check("scope overhead stays under 1 us per outermost+nested pair", per < 1000);
        }

        // Paused producer: a slot being written is skipped by every reader.
        auto n = abi::word(map.base, abi::w_head_a).load();
        auto at = abi::slot_index(abi::ring_a, n);
        auto writable = mmap(nullptr, abi::file_size, PROT_READ | PROT_WRITE, MAP_SHARED, open(path.c_str(), O_RDWR | O_CLOEXEC), 0);
        abi::word(writable, at + abi::s_seq).store(2 * n + 1);
        abi::word(writable, abi::w_head_a).store(n + 1);  // a reader racing the head
        abi::record_t half;
        check("a record paused mid-write is rejected", !abi::read_record(map.base, abi::ring_a, n, half));
        abi::word(writable, abi::w_head_a).store(n);
        munmap(writable, abi::file_size);

        reading = false;
        reader.join();
        check("the in-process reader read records concurrently", accepted.load() > 0);
        loop::current = nullptr;
        // Stop right after work (the watchdog in its fast wait), twice.
        { loop::current = &monitor; SCOTTLAND_LOOP_SCOPE(on_key); }
        loop::current = nullptr;
        auto stopping = loop::now_ns();
        monitor.stop(false);
        monitor.stop(false);
        check("stop joins promptly and is idempotent", loop::now_ns() - stopping < 150 * loop::ms);
    }

    // 2. A second copy (reload) continues the ring with the next instance; the names file is its own.
    {
        loop::monitor_t monitor;
        monitor.start(events, path, 42, {});
        mapping_t map(path);
        check("a reloaded copy continues the ring as instance 2", abi::word(map.base, abi::w_instance).load() == 2);
        auto a = records(map.base, abi::ring_a, abi::w_head_a);
        bool previous = false, loaded = false;
        for (auto& r : a) { previous |= r.instance == 1; loaded |= r.kind == abi::k_loaded && r.instance == 2; }
        check("earlier records keep their instance; the new copy records its load", previous && loaded);
        std::ifstream names(path + ".names");
        uint64_t instance = 0;
        names >> instance;
        check("the names file belongs to the current instance", instance == 2);
        auto stopping = loop::now_ns();
        monitor.stop(true);  // idle wait
        check("stop from the idle wait joins promptly", loop::now_ns() - stopping < 150 * loop::ms);
        check("an unload without reload removes the ring and its names", access(path.c_str(), F_OK) != 0 &&
            access((path + ".names").c_str(), F_OK) != 0);
    }

    // 3. Failure injection: each channel fails alone and leaves the rest working.
    {
        loop::faults_t ring; ring.ring = true;
        loop::monitor_t m1; m1.start(events, path, 1, ring);
        check("ring file failure: no ring, no watchdog, counters still work", !m1.ring_on() && !m1.watchdog_on());
        loop::current = &m1; { SCOTTLAND_LOOP_SCOPE(on_key); } loop::current = nullptr;
        check("... and the scope was counted", m1.stats_json(false).find("\"on_key\"") != std::string::npos);
        m1.stop(true);
        for (auto [name, which] : {std::pair{"eventfd", 0}, std::pair{"event source", 1}, std::pair{"thread", 2}})
        {
            loop::faults_t f;
            if (which == 0) f.eventfd = true;
            if (which == 1) f.event_source = true;
            if (which == 2) f.thread = true;
            loop::monitor_t m; m.start(events, path, 1, f);
            check((std::string(name) + " failure: the ring stays on, no watchdog").c_str(), m.ring_on() && !m.watchdog_on());
            m.stop(true);
            m.stop(true);
        }
        loop::faults_t lock; lock.mlock = true;
        loop::monitor_t m2; m2.start(events, path, 1, lock);
        mapping_t map(path);
        check("mlock failure is recorded in the header", (abi::word(map.base, abi::w_flags).load() & abi::flag_mlock_failed) != 0);
        m2.stop(true);
    }

    // 4. Another compositor process on the same display gets a new file (a new inode): a reader
    // still mapping the old one keeps that stream intact (Astra's implementation review, 10).
    {
        loop::monitor_t first;
        first.start(events, path, 7, {});
        loop::current = &first; { SCOTTLAND_LOOP_SCOPE(on_key); } loop::current = nullptr;
        first.stop(false);  // the compositor died: its files stay
        struct stat before{};
        stat(path.c_str(), &before);
        mapping_t paused(path);  // a reader paused on the old file
        auto old_head = abi::word(paused.base, abi::w_head_a).load();
        auto old_pid = abi::word(paused.base, abi::w_pid).load();
        fflush(stdout);
        pid_t child = fork();
        if (child == 0)
        {
            auto own = wl_event_loop_create();
            loop::monitor_t next;
            loop::faults_t nothread;  // no thread after fork (ThreadSanitizer); the ring is the point
            nothread.thread = true;
            next.start(own, path, 8, nothread);
            next.stop(false);
            wl_event_loop_destroy(own);
            _exit(0);
        }
        int status = 0;
        waitpid(child, &status, 0);
        struct stat after{};
        stat(path.c_str(), &after);
        mapping_t current(path);
        check("a new compositor process publishes a new ring file (new inode)", after.st_ino != before.st_ino &&
            abi::word(current.base, abi::w_pid).load() == (uint64_t)child && abi::word(current.base, abi::w_instance).load() == 1);
        check("... the paused reader's mapping keeps the old stream unchanged", abi::word(paused.base, abi::w_pid).load() == old_pid &&
            abi::word(paused.base, abi::w_head_a).load() == old_head && old_head > 0);
        check("... and no partial file is left beside it", access((path + ".new").c_str(), F_OK) != 0);
        loop::monitor_t same;
        same.start(events, path, 9, {});
        struct stat again{};
        stat(path.c_str(), &again);
        check("this process again: a new file too (another process wrote the current one)", again.st_ino != after.st_ino);
        same.stop(false);
        loop::monitor_t reload;
        reload.start(events, path, 9, {});
        struct stat reloaded{};
        stat(path.c_str(), &reloaded);
        check("a reload in the same process continues the same file", reloaded.st_ino == again.st_ino);
        reload.stop(true);
    }

    // 5. The external reader's header reads the running scope as one coherent sample.
    if (argc > 3)
    {
        std::string reader = argv[3], fixture = dir + "/fixture.loop";
        int fd = open(fixture.c_str(), O_RDWR | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
        bool made = fd >= 0 && ftruncate(fd, abi::file_size) == 0;
        void *base = made ? mmap(nullptr, abi::file_size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0) : MAP_FAILED;
        if (fd >= 0) close(fd);
        if (base != MAP_FAILED)
        {
            abi::word(base, abi::w_abi).store(abi::version);
            abi::word(base, abi::w_pid).store(1);
            abi::word(base, abi::w_instance).store(1);
            abi::word(base, abi::w_magic).store(abi::magic);
            auto run = [&]
            {
                std::string out;
                if (FILE *p = popen((reader + " --file " + fixture + " --json").c_str(), "r"))
                {
                    char line[4096];
                    while (fgets(line, sizeof(line), p)) out += line;
                    pclose(p);
                }
                return out;
            };
            // Paused between its two stores: the new scope stored, the start still the old zero.
            abi::word(base, abi::w_sample_seq).store(1);
            abi::word(base, abi::w_current_scope).store((uint64_t)loop::scope_id::on_key);
            auto paused = run();
            check("a producer paused mid-sample: the header reports the running scope unavailable, not a huge age",
                paused.find("\"current\":null") != std::string::npos && paused.find("\"current_ms\":null") != std::string::npos);
            abi::word(base, abi::w_current_start_ns).store(loop::now_ns() - 5 * loop::ms);
            abi::word(base, abi::w_sample_seq).store(2);
            auto steady = run();
            auto age = json_number(steady, "current_ms");
            check("a coherent sample: the running scope with its real age", steady.find("\"current\":\"") != std::string::npos &&
                age >= 5 && age < 5000);
            munmap(base, abi::file_size);
        } else check("reader fixture created", false);
        unlink(fixture.c_str());
    }

    // 6. ML2 with supplied timestamps (no clock, no ring): unexcused occupancy is evaluated at
    // every window, not only at each episode's largest one.
    {
        const uint64_t base = 1000 * loop::ms;
        auto emit = [] (loop::monitor_t& m, loop::scope_id id, uint64_t start, uint64_t duration)
        {
            m.enter(id, start);
            m.exit(id, start + duration, start);
        };
        auto episodes = [] (const std::string& json)
        {
            size_t n = 0;
            for (auto at = json.find("\"peak_ms\":"); at != std::string::npos; at = json.find("\"peak_ms\":", at + 1)) n++;
            return n;
        };

        // The review's case: an excused 16 ms goo_render, then 1.8 ms option_layout every 4 ms.
        // Each later callback is under ML1, but every 16.7 ms window ending at one holds four of
        // them and 0.7 ms of a fifth: 7.9 ms unexcused, while occupancy never drops under 4 ms.
        loop::monitor_t m;
        check("an exception's scope can be excused by name", m.excuse("goo_render") && !m.excuse("no_such_scope"));
        emit(m, loop::scope_id::goo_render, base, 16 * loop::ms);
        for (uint64_t i = 0; i < 20; i++) emit(m, loop::scope_id::option_layout, base + (18 + 4 * i) * loop::ms, 1800000);
        auto stats = m.stats_json(false);
        auto peak = json_number(stats, "peak_ms");
        check("an excused peak does not hide later unexcused windows over 4 ms (7.9 ms)",
              episodes(stats) == 1 && peak > 7.89 && peak < 7.91 && stats.find("\"option_layout\":") != std::string::npos);
        check("... and the unexcused maximum is reported as such", json_number(stats, "ml2_unexcused_max_ms") > 7.89 &&
              stats.find("\"ml2_excused\":[\"goo_render\"]") != std::string::npos);

        // The same excused peak followed by sparse work: no episode. Without the excuse, one.
        for (bool excuse : {true, false})
        {
            loop::monitor_t sparse;
            if (excuse) sparse.excuse("goo_render");
            emit(sparse, loop::scope_id::goo_render, base, 16 * loop::ms);
            for (uint64_t i = 0; i < 5; i++) emit(sparse, loop::scope_id::option_layout, base + (20 + 20 * i) * loop::ms, 1800000);
            auto s = sparse.stats_json(false);
            if (excuse)
                check("an excused peak with sparse work after it: no episode, 1.8 ms unexcused at most",
                      episodes(s) == 0 && json_number(s, "ml2_unexcused_max_ms") < 1.81);
            else
                check("... and the same work with nothing excused: one 16 ms episode", episodes(s) == 1 && json_number(s, "peak_ms") > 15.99);
        }

        // Excusing after intervals are queued (with a reset, as loop-stats does) takes them out of
        // the window that is still open.
        loop::monitor_t late;
        emit(late, loop::scope_id::goo_render, base, 3 * loop::ms);
        late.excuse("goo_render");
        late.stats_json(true);
        emit(late, loop::scope_id::option_layout, base + 4 * loop::ms, 2 * loop::ms);
        auto l = late.stats_json(false);
        check("excusing a queued scope takes it out of the open window (2 ms unexcused of 5)",
              json_number(l, "ml2_unexcused_max_ms") < 2.01 && json_number(l, "ml2_max_ms") > 4.99);

        // More intervals in one window than the queue holds: reported lost, never a low sum.
        loop::monitor_t dense;
        for (uint64_t i = 0; i < 8200; i++) emit(dense, loop::scope_id::option_layout, base + i * 2000, 1000);
        auto d = dense.stats_json(false);
        check("a full ML2 queue dropping an interval inside the window counts it lost", json_number(d, "ml2_lost") >= 8);
        check("... and a reset clears the count", json_number(dense.stats_json(true), "ml2_lost") >= 8 &&
              json_number(dense.stats_json(false), "ml2_lost") == 0);
    }

    wl_event_loop_destroy(events);
    rmdir(dir.c_str());
    printf("%s\n", failures ? "FAILED" : "all loop checks passed");
    return failures ? 1 : 0;
}
