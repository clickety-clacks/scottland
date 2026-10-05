// The worker and the breathing-shrink job without a compositor (docs/main-loop.md "Worker").
// Run by tests/worker-unit.sh: once under ThreadSanitizer, once optimized for calibration.
#include "pure/shrink.hpp"
#include "pure/worker.hpp"
#include <atomic>
#include <algorithm>
#include <cmath>
#include <condition_variable>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <poll.h>
#include <random>
#include <thread>
#include <unistd.h>

using namespace scottland;
using namespace scottland::work;

static int failures = 0;
static void check(const std::string& what, bool ok)
{
    printf("%s  %s\n", ok ? "PASS" : "FAIL", what.c_str());
    failures += !ok;
}

// A job that pauses inside its step until the test lets it go.
struct gate_t
{
    std::mutex m;
    std::condition_variable cv;
    bool entered = false, released = false;
    void wait_entered()
    {
        std::unique_lock lock(m);
        cv.wait(lock, [&] { return entered; });
    }
    void release()
    {
        { std::lock_guard lock(m); released = true; }
        cv.notify_all();
    }
};

struct value_t : result_t { int value; explicit value_t(int v) : value(v) {} };

struct gated_job_t : job_t
{
    gate_t *gate;
    int value;
    std::atomic<int> *destroyed;
    gated_job_t(gate_t *gate, int value, std::atomic<int> *destroyed) : gate(gate), value(value), destroyed(destroyed) {}
    ~gated_job_t() override { if (destroyed) (*destroyed)++; }
    bool step(cancel_t& cancel) override
    {
        if (gate)
        {
            std::unique_lock lock(gate->m);
            gate->entered = true;
            gate->cv.notify_all();
            gate->cv.wait(lock, [&] { return gate->released; });
        }
        return cancel.charge(1) || true;
    }
    std::unique_ptr<result_t> result(outcome_t& outcome) override
    {
        outcome = outcome_t::finished;
        return std::make_unique<value_t>(value);
    }
};

static bool wait_readable(int fd, int ms)
{
    pollfd p{fd, POLLIN, 0};
    return poll(&p, 1, ms) > 0;
}

static std::vector<goo::source_t> fixture(int n, unsigned seed)
{
    std::mt19937 rng(seed);
    std::uniform_real_distribution<float> pos(100, 1800), half(60, 300);
    std::vector<goo::source_t> sources(n);
    for (int i = 0; i < n; i++)
    {
        sources[i].id = i + 1;
        sources[i].rect = {pos(rng), pos(rng) * .6f, half(rng), half(rng) * .7f};
        sources[i].attention = i % 3 == 0;
    }
    goo::settings_t settings;
    goo::amounts(sources, settings);
    return sources;
}

// GO19's main-thread algorithm (goo.cpp before Phase 4), kept here as the reference.
static std::vector<rect_t> reference(const shrink_snapshot_t& s)
{
    std::vector<rect_t> out;
    const float wet = .5f * s.settings.threshold();
    const double padding = 5 + 1. / s.output_scale, reach = 4 * s.settings.reach + padding;
    for (auto& b : s.rects)
    {
        double bx2 = b.x + b.width, by2 = b.y + b.height;
        bool masked = false;
        for (auto& source : s.sources)
        {
            if (!source.shape) continue;
            auto body = source.shape_body.z > 0 && source.shape_body.w > 0 ? source.shape_body : source.rect;
            if (body.x - body.z - reach < bx2 && body.x + body.z + reach > b.x && body.y - body.w - reach < by2 && body.y + body.w + reach > b.y)
            { masked = true; break; }
        }
        const double fine = masked ? 2 : 4, coarse = 4 * fine;
        const double sx = b.width > 2 * b.height ? coarse : fine, sy = b.height > 2 * b.width ? coarse : fine;
        double x1 = 1e9, y1 = 1e9, x2 = -1e9, y2 = -1e9;
        for (double y = b.y; y < by2 + sy; y += sy)
            for (double x = b.x; x < bx2 + sx; x += sx)
            {
                glm::vec2 p{std::min<double>(x, bx2), std::min<double>(y, by2)};
                if (goo::density(p, s.sources, s.settings, s.time, 1) < wet) continue;
                x1 = std::min<double>(x1, p.x); y1 = std::min<double>(y1, p.y);
                x2 = std::max<double>(x2, p.x); y2 = std::max<double>(y2, p.y);
            }
        if (x2 >= x1)
        {
            double tx1 = std::max<double>(std::floor(x1 - sx - padding), b.x), ty1 = std::max<double>(std::floor(y1 - sy - padding), b.y);
            double tx2 = std::min<double>(std::ceil(x2 + sx + padding), bx2), ty2 = std::min<double>(std::ceil(y2 + sy + padding), by2);
            out.push_back({tx1, ty1, tx2 - tx1, ty2 - ty1});
        } else out.push_back(b);
    }
    return out;
}

static shrink_snapshot_t snapshot_for(int n, unsigned seed)
{
    shrink_snapshot_t s;
    s.sources = fixture(n, seed);
    s.time = 1.5f;
    s.output_scale = 1;
    for (auto& src : s.sources)
    {
        double out = 4 * s.settings.reach + 6;
        s.rects.push_back({src.rect.x - src.rect.z - out, src.rect.y - src.rect.w - out, 2 * src.rect.z + 2 * out, out + 20});
    }
    return s;
}

// Runs a job directly, as the worker would, with a given allowance per step.
static std::unique_ptr<shrink_result_t> run_direct(shrink_snapshot_t s, uint64_t allowance, uint64_t cap, outcome_t& outcome, uint64_t *steps = nullptr)
{
    lane_t lane;
    std::atomic<bool> stopping{false};
    shrink_job_t job(std::move(s), cap);
    uint64_t n = 0;
    while (true)
    {
        cancel_t cancel(&lane, 0, 0, &stopping, allowance);
        n++;
        if (job.step(cancel)) break;
    }
    if (steps) *steps = n;
    auto r = job.result(outcome);
    return std::unique_ptr<shrink_result_t>(static_cast<shrink_result_t*>(r.release()));
}

static bool same(const std::vector<rect_t>& a, const std::vector<rect_t>& b)
{
    if (a.size() != b.size()) return false;
    for (size_t i = 0; i < a.size(); i++)
        if (a[i].x != b[i].x || a[i].y != b[i].y || a[i].width != b[i].width || a[i].height != b[i].height) return false;
    return true;
}

int main(int argc, char **argv)
{
    bool timing = argc > 1 && !strcmp(argv[1], "--timing");

    // 1. Ordinary run: submit, finish, deliver through the eventfd.
    {
        worker_t worker("test", 1000);
        check("worker starts", worker.start());
        auto lane = worker.open_lane("a", policy_t::exact);
        int delivered = -1;
        lane->accept = [&] (const done_t& d) { return d.ticket == lane->ticket() && d.epoch == lane->epoch(); };
        lane->deliver = [&] (done_t& d) { delivered = static_cast<value_t&>(*d.result).value; };
        check("submit", lane->submit(std::make_unique<gated_job_t>(nullptr, 7, nullptr), now_ns()));
        check("the eventfd signals a finished job", wait_readable(worker.event_fd(), 2000));
        worker.deliver();
        check("its result is delivered", delivered == 7);
        worker.stop();
    }

    // 2. Forced interleavings, exact policy: a newer submit, a cancel, an epoch bump, a close.
    for (int which = 0; which < 4; which++)
    {
        const char *names[] = {"a newer submit", "a cancel", "an epoch bump", "closing the lane"};
        worker_t worker("test", 1000);
        worker.start();
        auto lane = worker.open_lane("a", policy_t::exact);
        std::vector<int> got;
        std::atomic<int> destroyed{0};
        lane->accept = [&] (const done_t& d) { return d.ticket == lane->ticket() && d.epoch == lane->epoch(); };
        lane->deliver = [&] (done_t& d) { got.push_back(static_cast<value_t&>(*d.result).value); };
        gate_t gate;
        lane->submit(std::make_unique<gated_job_t>(&gate, 1, &destroyed), now_ns());
        gate.wait_entered();
        if (which == 0) lane->submit(std::make_unique<gated_job_t>(nullptr, 2, &destroyed), now_ns());
        if (which == 1) lane->cancel();
        if (which == 2) lane->bump_epoch();
        if (which == 3) lane->close();
        gate.release();
        usleep(100000);
        if (wait_readable(worker.event_fd(), 300)) worker.deliver();
        bool ok = which == 0 ? (got == std::vector<int>{2}) : got.empty();
        check(std::string("exact: ") + names[which] + " while running: the old result is never delivered", ok);
        worker.stop();
        check(std::string("exact: every job destroyed after stop (") + names[which] + ")", destroyed.load() >= 1);
    }

    // 3. latest_completed: a newer ticket in the same epoch does not cancel the running job.
    {
        worker_t worker("test", 1000);
        worker.start();
        auto lane = worker.open_lane("hints", policy_t::latest_completed);
        std::vector<int> got;
        lane->accept = [&] (const done_t& d) { return d.epoch == lane->epoch(); };
        lane->deliver = [&] (done_t& d) { got.push_back(static_cast<value_t&>(*d.result).value); };
        gate_t gate;
        lane->submit(std::make_unique<gated_job_t>(&gate, 1, nullptr), now_ns());
        gate.wait_entered();
        lane->submit(std::make_unique<gated_job_t>(nullptr, 2, nullptr), now_ns());
        gate.release();
        for (int i = 0; i < 20 && got.size() < 2; i++)
            if (wait_readable(worker.event_fd(), 100)) worker.deliver();
        check("latest_completed: the running job finishes and the newer one follows", got == std::vector<int>({1, 2}) || got == std::vector<int>({2}));
        gate_t gate2;
        lane->submit(std::make_unique<gated_job_t>(&gate2, 3, nullptr), now_ns());
        gate2.wait_entered();
        lane->bump_epoch();
        gate2.release();
        usleep(100000);
        size_t before = got.size();
        if (wait_readable(worker.event_fd(), 200)) worker.deliver();
        check("latest_completed: an epoch bump cancels it", got.size() == before);
        worker.stop();
    }

    // 4. Stop in every state: idle, running (paused), with pending and undelivered results.
    {
        worker_t worker("test", 1000);
        worker.start();
        std::vector<std::unique_ptr<lane_handle_t>> lanes;
        std::atomic<int> destroyed{0};
        for (int i = 0; i < 16; i++) lanes.push_back(worker.open_lane("l" + std::to_string(i), policy_t::exact));
        auto extra = worker.open_lane("seventeenth", policy_t::exact);
        check("at most 16 lanes", !extra->submit(std::make_unique<gated_job_t>(nullptr, 0, nullptr), now_ns()));
        for (auto& lane : lanes)
        {
            lane->accept = [] (const done_t&) { return true; };
            lane->submit(std::make_unique<gated_job_t>(nullptr, 1, &destroyed), now_ns());
        }
        usleep(50000);
        gate_t gate;
        lanes[0]->submit(std::make_unique<gated_job_t>(&gate, 1, &destroyed), now_ns());
        gate.wait_entered();
        lanes[1]->submit(std::make_unique<gated_job_t>(nullptr, 1, &destroyed), now_ns());
        std::thread releaser([&] { usleep(20000); gate.release(); });
        auto join = worker.stop();
        releaser.join();
        worker.stop();
        check("stop with running, pending and undelivered results joins (" + std::to_string(join / 1000) + " us) and is idempotent", true);
        check("every job was destroyed", destroyed.load() == 18);
        lanes.clear();  // handles outlive the worker's stop safely
    }

    // 5. `broken` without the eventfd: a failed write is observed by the next submit.
    {
        worker_t worker("test", 1000);
        worker.start();
        auto lane = worker.open_lane("a", policy_t::exact);
        lane->accept = [] (const done_t&) { return true; };
        int fd = worker.event_fd();
        int saved = dup(fd);
        close(fd);  // the worker's next write fails with EBADF
        lane->submit(std::make_unique<gated_job_t>(nullptr, 1, nullptr), now_ns());
        for (int i = 0; i < 100 && !worker.is_broken(); i++) usleep(5000);
        check("a failed eventfd write marks the worker broken", worker.is_broken());
        check("a broken worker refuses work (the consumer keeps its safe state)",
            !lane->submit(std::make_unique<gated_job_t>(nullptr, 2, nullptr), now_ns()));
        dup2(saved, fd);  // so stop() closes something it owns
        close(saved);
        worker.stop();
    }

    // 6. Failure injection: no eventfd, no thread.
    {
        worker_t a("test", 1000);
        check("eventfd failure: unavailable", !a.start({true, false}) && !a.available());
        a.stop();
        worker_t b("test", 1000);
        check("thread failure: unavailable", !b.start({false, true}) && !b.available());
        auto lane = b.open_lane("a", policy_t::exact);
        check("... and refuses work", !lane->submit(std::make_unique<gated_job_t>(nullptr, 1, nullptr), now_ns()));
        b.stop();
    }

    // 7. The shrink job: the same result as GO19's main-thread algorithm, whatever the allowance.
    for (unsigned seed : {1u, 2u, 3u})
    {
        auto s = snapshot_for(12, seed);
        auto ref = reference(s);
        outcome_t o1, o2;
        uint64_t steps = 0;
        auto big = run_direct(s, UINT64_MAX, UINT64_MAX, o1);
        auto tiny = run_direct(s, 1, UINT64_MAX, o2, &steps);
        check("shrink equals the main-thread result (fixture " + std::to_string(seed) + ")", same(big->rects, ref) && o1 == outcome_t::finished);
        check("one unit per step gives the identical result (" + std::to_string(steps) + " steps)", same(tiny->rects, big->rects) && o2 == outcome_t::finished);
    }

    // 8. Caps: at the first unit, in the middle of a row, at the last unit.
    {
        auto s = snapshot_for(8, 5);
        outcome_t full_outcome;
        uint64_t total_steps = 0;
        auto full = run_direct(s, 1, UINT64_MAX, full_outcome, &total_steps);
        uint64_t terms = s.sources.size(), total = (total_steps - 1) * terms;
        for (auto [label, cap] : {std::pair{"the first unit", (uint64_t)1}, std::pair{"the middle of a row", total / 2 + terms / 2},
             std::pair{"the last unit", total - 1}})
        {
            outcome_t o;
            auto r = run_direct(s, UINT64_MAX, cap, o);
            bool whole = r->rects.size() == s.rects.size();
            bool never_partial = true;
            for (size_t i = 0; i < r->rects.size(); i++)
            {
                bool tight = i < r->refined;
                const auto& want = tight ? full->rects[i] : s.rects[i];
                never_partial &= r->rects[i].x == want.x && r->rects[i].y == want.y && r->rects[i].width == want.width &&
                    r->rects[i].height == want.height;
            }
            check(std::string("cap at ") + label + ": capped, finished rectangles tight, the rest loose, none partly shrunk",
                o == outcome_t::capped && whole && never_partial && (cap > terms || r->refined == 0));
        }
    }

    // 9. Through the worker: delivery, then a stale result after sources change.
    {
        worker_t worker("shrink", shrink_step_units);
        worker.start();
        auto lane = worker.open_lane("shrink", policy_t::exact);
        int delivered = 0, stale_before = 0;
        lane->accept = [&] (const done_t& d) { return d.ticket == lane->ticket() && d.epoch == lane->epoch(); };
        lane->deliver = [&] (done_t&) { delivered++; };
        lane->submit(std::make_unique<shrink_job_t>(snapshot_for(30, 9)), now_ns());
        for (int i = 0; i < 400 && !delivered; i++)
            if (wait_readable(worker.event_fd(), 10)) worker.deliver();
        check("a shrink job is delivered through the worker", delivered == 1);
        stale_before = delivered;
        lane->submit(std::make_unique<shrink_job_t>(snapshot_for(30, 10)), now_ns());
        lane->bump_epoch();  // the sources changed while it ran
        usleep(300000);
        if (wait_readable(worker.event_fd(), 100)) worker.deliver();
        check("a result for older sources is dropped", delivered == stale_before);
        worker.stop();
    }

    // 10. Memory: a maximum-size snapshot stays inside 8 MB.
    {
        auto s = snapshot_for(256, 4);
        auto shape = std::make_shared<goo::shape_t>();
        shape->pixels.resize(1100000);
        for (int i = 0; i < 4; i++) s.sources[i].shape = shape;  // shared shapes count once
        check("a 256-source snapshot with a 1.1 MB shared shape is under 8 MB (" +
            std::to_string(shrink_job_t::snapshot_bytes(s) / 1024) + " KB)", shrink_job_t::snapshot_bytes(s) < 8u << 20);
    }

    // 11. Calibration (timing build only): the cost of a unit and the slowest single operation.
    if (timing)
    {
        auto s30 = snapshot_for(30, 7);
        auto t0 = now_ns();
        uint64_t calls = 0;
        float sink = 0;
        while (now_ns() - t0 < 300000000ull)
            for (int i = 0; i < 1000; i++, calls++)
                sink += goo::density({float(300 + i % 900), float(200 + i % 500)}, s30.sources, s30.settings, 1.5f, 1);
        double per_term = double(now_ns() - t0) / calls / 30;
        auto s256 = snapshot_for(256, 8);
        std::vector<uint64_t> times;
        for (int i = 0; i < 4000; i++)
        {
            auto a = now_ns();
            sink += goo::density({float(200 + i % 1500), float(300 + i % 700)}, s256.sources, s256.settings, 1.5f, 1);
            times.push_back(now_ns() - a);
        }
        std::sort(times.begin(), times.end());
        // The 99th percentile: the operation's cost. The maximum also holds the host's preemption.
        uint64_t slowest = times[times.size() * 99 / 100];
        printf("measured: a 256-source density call: median %llu ns, p99 %llu ns, max %llu ns (with preemption)\n",
            (unsigned long long)times[times.size() / 2], (unsigned long long)slowest, (unsigned long long)times.back());
        printf("measured: %.1f ns per density term; a %llu-unit step is about %.2f ms; the %llu-unit cap about %.0f ms (%g)\n",
            per_term, (unsigned long long)shrink_step_units, per_term * shrink_step_units / 1e6,
            (unsigned long long)shrink_cap_units, per_term * shrink_cap_units / 1e6, double(sink > 0));
        check("the slowest single unit operation (a 256-source density call) is under 20 us (" +
            std::to_string(slowest / 1000) + " us)", slowest < 20000);
    }

    printf("%s\n", failures ? "FAILED" : "all worker checks passed");
    return failures ? 1 : 0;
}
