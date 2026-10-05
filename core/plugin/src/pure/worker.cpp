#include "worker.hpp"
#include <algorithm>
#include <cerrno>
#include <csignal>
#include <ctime>
#include <pthread.h>
#include <sstream>
#include <sys/eventfd.h>
#include <unistd.h>

namespace scottland::work
{
uint64_t now_ns()
{
    timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000000000ull + (uint64_t)t.tv_nsec;
}

bool cancel_t::cancelled() const
{
    if (stopping->load() || lane->closed.load() || lane->epoch.load() != epoch) return true;
    return lane->policy == policy_t::exact && lane->ticket.load() != ticket;
}

bool cancel_t::charge(uint64_t units)
{
    if (used >= allowance || cancelled()) return false;
    used += units;
    return true;
}

void lane_handle_t::age_t::add(uint64_t ns)
{
    last = ns;
    max = std::max(max, ns);
    recent[count++ % recent.size()] = ns;
}

uint64_t lane_handle_t::age_t::p95() const
{
    auto n = std::min<uint64_t>(count, recent.size());
    if (!n) return 0;
    std::vector<uint64_t> sorted(recent.begin(), recent.begin() + n);
    std::sort(sorted.begin(), sorted.end());
    return sorted[std::min<uint64_t>(n - 1, n * 95 / 100)];
}

worker_t::worker_t(std::string name, uint64_t step_units) : name(std::move(name)), step_units(step_units) {}

worker_t::~worker_t() { stop(); }

bool worker_t::start(const worker_faults_t& faults)
{
    fd = faults.eventfd ? -1 : eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
    if (fd < 0) return false;
    if (faults.thread) return false;
    // Signals stay with the compositor's thread.
    sigset_t all, previous;
    sigfillset(&all);
    pthread_sigmask(SIG_SETMASK, &all, &previous);
    try
    {
        thread = std::thread([this] { run(); });
        pthread_setname_np(thread.native_handle(), ("scottland-" + name).substr(0, 15).c_str());
    } catch (...)
    {
    }
    pthread_sigmask(SIG_SETMASK, &previous, nullptr);
    return thread.joinable();
}

uint64_t worker_t::stop()
{
    auto started = now_ns();
    {
        std::lock_guard lock(m);
        stopping = true;
    }
    cv.notify_all();
    if (thread.joinable()) thread.join();
    last_join_ns = now_ns() - started;
    // Undelivered results and pending jobs are destroyed here, on the main thread, outside m.
    std::vector<std::shared_ptr<lane_t>> all;
    {
        std::lock_guard lock(m);
        all.swap(lanes);
    }
    for (auto& lane : all)
    {
        lane->pending.reset();
        lane->done.reset();
        lane->running.reset();  // the thread has exited: nothing else touches it
    }
    for (auto *handle : handles) handle->worker = nullptr;
    handles.clear();
    if (fd >= 0) ::close(fd);
    fd = -1;
    return last_join_ns;
}

std::unique_ptr<lane_handle_t> worker_t::open_lane(const std::string& lane_name, policy_t policy)
{
    auto handle = std::make_unique<lane_handle_t>();
    handle->lane = std::make_shared<lane_t>();
    handle->lane->policy = policy;
    handle->name = lane_name;
    {
        std::lock_guard lock(m);
        if (lanes.size() >= 16 || stopping) handle->lane->closed = true;
        else lanes.push_back(handle->lane);
    }
    if (!handle->lane->closed)
    {
        handle->worker = this;
        handles.push_back(handle.get());
    }
    return handle;
}

void worker_t::close(lane_handle_t& handle)
{
    handle.lane->closed = true;
    handle.lane->ticket++;
    std::optional<lane_t::slot_t> pending;
    std::unique_ptr<done_t> done;
    {
        std::lock_guard lock(m);
        pending.swap(handle.lane->pending);
        done.swap(handle.lane->done);
        lanes.erase(std::remove(lanes.begin(), lanes.end(), handle.lane), lanes.end());
    }
    cv.notify_all();
    handles.erase(std::remove(handles.begin(), handles.end(), &handle), handles.end());
    // pending and done are destroyed here, outside m. A running job is the worker's: it sees
    // `closed` at its next charge and is destroyed there; its result is never delivered.
}

void lane_handle_t::close()
{
    if (worker) worker->close(*this);
    worker = nullptr;
    if (lane) lane->closed = true;
}

bool lane_handle_t::submit(std::unique_ptr<job_t> job, uint64_t snapshot_ns)
{
    if (!worker || lane->closed || !worker->available()) return false;
    lane_t::slot_t slot;
    slot.job = std::move(job);
    slot.ticket = ++lane->ticket;
    slot.epoch = lane->epoch.load();
    slot.snapshot_ns = snapshot_ns;
    std::optional<lane_t::slot_t> replaced;
    {
        std::lock_guard lock(worker->m);
        replaced.swap(lane->pending);
        lane->pending = std::move(slot);
    }
    worker->cv.notify_all();
    lane->stats.submitted++;
    if (replaced) lane->stats.superseded++;
    return true;  // a replaced job is destroyed here, outside the mutex
}

void lane_handle_t::cancel()
{
    lane->ticket++;
    std::optional<lane_t::slot_t> dropped;
    if (worker)
    {
        std::lock_guard lock(worker->m);
        dropped.swap(lane->pending);
    }
    if (worker) worker->cv.notify_all();
}

void lane_handle_t::bump_epoch()
{
    lane->epoch++;
    if (worker) worker->cv.notify_all();
}

void worker_t::signal()
{
    uint64_t one = 1;
    while (true)
    {
        if (write(fd, &one, sizeof(one)) == sizeof(one)) return;
        if (errno == EINTR) continue;
        if (errno == EAGAIN) return;  // the counter is already nonzero: a delivery is due anyway
        broken = true;  // observed on the main thread by submit and the watchdog's heartbeat
        return;
    }
}

void worker_t::run()
{
    std::unique_lock lock(m);
    while (true)
    {
        bool busy = false;
        for (auto& lane : lanes)
        {
            if (!lane->running && lane->pending)
            {
                lane->running = std::move(lane->pending);
                lane->pending.reset();
                lane->running->start_ns = now_ns();
            }
            busy |= bool(lane->running) || bool(lane->pending);
        }
        if (stopping) break;
        if (!busy)
        {
            // Waits only when no lane has a running or pending job.
            cv.wait(lock, [&] {
                if (stopping) return true;
                for (auto& lane : lanes) if (lane->pending || lane->running) return true;
                return false;
            });
            continue;
        }
        auto active = lanes;  // owning copies: a lane closed meanwhile stays valid until we drop it
        lock.unlock();
        for (auto& lane : active)
        {
            if (!lane->running) continue;
            auto& slot = *lane->running;
            cancel_t cancel(lane.get(), slot.ticket, slot.epoch, &stopping, step_units);
            if (cancel.cancelled())
            {
                lane->stats.cancelled++;
                lane->running.reset();  // destroyed here, outside m
                continue;
            }
            bool finished = false;
            auto done = std::make_unique<done_t>();
            try
            {
                finished = slot.job->step(cancel);
                if (finished && !cancel.cancelled()) done->result = slot.job->result(done->outcome);
                else if (finished) { lane->stats.cancelled++; lane->running.reset(); continue; }
            } catch (...)
            {
                finished = true;
                done->outcome = outcome_t::failed;
                done->result.reset();
            }
            if (!finished) continue;
            if (done->outcome == outcome_t::capped) lane->stats.capped++;
            if (done->outcome == outcome_t::failed) lane->stats.failed++;
            done->ticket = slot.ticket;
            done->epoch = slot.epoch;
            done->snapshot_ns = slot.snapshot_ns;
            done->start_ns = slot.start_ns;
            done->finish_ns = now_ns();
            std::unique_ptr<done_t> replaced;
            {
                std::lock_guard swap(m);
                replaced = std::move(lane->done);
                lane->done = std::move(done);
            }
            lane->running.reset();
            replaced.reset();  // an undelivered older result, destroyed outside m
            signal();
        }
        active.clear();
        lock.lock();
    }
    // Stopping: the running jobs are the worker's to destroy.
    auto all = lanes;
    lock.unlock();
    for (auto& lane : all) lane->running.reset();
}

void worker_t::deliver(uint64_t budget_ns)
{
    if (fd < 0) return;
    uint64_t tokens;
    while (read(fd, &tokens, sizeof(tokens)) < 0 && errno == EINTR) {}
    auto started = now_ns();
    auto list = handles;
    for (size_t i = 0; i < list.size(); i++)
    {
        if (now_ns() - started > budget_ns)
        {
            signal();  // the rest on a later iteration
            return;
        }
        auto *handle = list[i];
        if (std::find(handles.begin(), handles.end(), handle) == handles.end()) continue;  // closed by a delivery
        std::unique_ptr<done_t> done;
        {
            std::lock_guard lock(m);
            done = std::move(handle->lane->done);
        }
        if (!done) continue;
        auto lane = handle->lane;  // keeps the lane while a delivery may close the handle
        if (lane->closed || !handle->accept || !handle->accept(*done))
        {
            lane->stats.stale++;
            continue;
        }
        auto now = now_ns();
        handle->queue_age.add(done->start_ns - done->snapshot_ns);
        handle->run_age.add(done->finish_ns - done->start_ns);
        handle->deliver_age.add(now - done->finish_ns);
        handle->total_age.add(now - done->snapshot_ns);
        lane->stats.delivered++;
        if (handle->deliver) handle->deliver(*done);
    }
}

std::string worker_t::stats_json() const
{
    std::ostringstream out;
    auto ms = [] (uint64_t ns) { return (double)ns / 1e6; };
    out << "{\"name\":\"" << name << "\",\"available\":" << (available() ? "true" : "false")
        << ",\"broken\":" << (broken.load() ? "true" : "false") << ",\"last_join_ms\":" << ms(last_join_ns) << ",\"lanes\":[";
    bool first = true;
    for (auto *h : handles)
    {
        auto& s = h->lane->stats;
        out << (first ? "" : ",") << "{\"name\":\"" << h->name << "\",\"submitted\":" << s.submitted.load()
            << ",\"superseded\":" << s.superseded.load() << ",\"cancelled\":" << s.cancelled.load()
            << ",\"capped\":" << s.capped.load() << ",\"failed\":" << s.failed.load()
            << ",\"delivered\":" << s.delivered.load() << ",\"stale\":" << s.stale.load();
        for (auto [label, age] : {std::pair{"snapshot_to_start", &h->queue_age}, std::pair{"start_to_finish", &h->run_age},
             std::pair{"finish_to_deliver", &h->deliver_age}, std::pair{"snapshot_to_deliver", &h->total_age}})
            out << ",\"" << label << "_ms\":{\"last\":" << ms(age->last) << ",\"max\":" << ms(age->max)
                << ",\"p95\":" << ms(age->p95()) << "}";
        out << "}";
        first = false;
    }
    out << "]}";
    return out.str();
}
}
