#include "loop.hpp"
#include <algorithm>
#include <cerrno>
#include <csignal>
#include <cstdio>
#include <cstring>
#include <dlfcn.h>
#include <elf.h>
#include <fcntl.h>
#include <link.h>
#include <pthread.h>
#include <sstream>
#include <sys/eventfd.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#include <wayland-server-core.h>

namespace scottland::loop
{
const scope_info_t scope_info[(size_t)scope_id::count] = {
    {"none", kind_t::work},
#define SCOPE(name, kind) {#name, kind_t::kind},
#define NOTE(name, text)
#include "loop-table.def"
#undef SCOPE
#undef NOTE
};

const note_info_t note_info[(size_t)note_id::count] = {
    {"none", ""},
#define SCOPE(name, kind)
#define NOTE(name, text) {#name, text},
#include "loop-table.def"
#undef SCOPE
#undef NOTE
};

uint64_t hash(const std::string& text)
{
    uint64_t h = 1469598103934665603ull;
    for (unsigned char c : text) h = (h ^ c) * 1099511628211ull;
    return h;
}

namespace
{
struct build_search_t { uintptr_t address; uint64_t id; };

int find_build_id(dl_phdr_info *info, size_t, void *data)
{
    auto search = static_cast<build_search_t*>(data);
    bool ours = false;
    for (int i = 0; i < info->dlpi_phnum; i++)
    {
        auto& h = info->dlpi_phdr[i];
        auto start = info->dlpi_addr + h.p_vaddr;
        if (h.p_type == PT_LOAD && search->address >= start && search->address < start + h.p_memsz) ours = true;
    }
    if (!ours) return 0;
    for (int i = 0; i < info->dlpi_phnum; i++)
    {
        auto& h = info->dlpi_phdr[i];
        if (h.p_type != PT_NOTE) continue;
        auto at = (const char*)(info->dlpi_addr + h.p_vaddr), end = at + h.p_memsz;
        while (at + sizeof(ElfW(Nhdr)) <= end)
        {
            auto note = (const ElfW(Nhdr)*)at;
            auto name = at + sizeof(ElfW(Nhdr));
            auto desc = name + ((note->n_namesz + 3) & ~3u);
            if (note->n_type == NT_GNU_BUILD_ID && note->n_descsz >= 8 && desc + 8 <= end)
            {
                memcpy(&search->id, desc, 8);
                return 1;
            }
            at = desc + ((note->n_descsz + 3) & ~3u);
        }
    }
    return 1;
}

uint64_t process_start_time()
{
    // Field 22 of /proc/self/stat, in clock ticks since boot (init() only).
    char text[1024] = {};
    int fd = open("/proc/self/stat", O_RDONLY | O_CLOEXEC);
    if (fd < 0) return 0;
    auto size = read(fd, text, sizeof(text) - 1);
    close(fd);
    if (size <= 0) return 0;
    auto at = strrchr(text, ')');
    if (!at) return 0;
    unsigned long long value = 0;
    std::istringstream in(at + 2);
    std::string field;
    for (int i = 3; i <= 22 && (in >> field); i++)
        if (i == 22) value = std::strtoull(field.c_str(), nullptr, 10);
    return value;
}
}

uint64_t own_build_id()
{
    build_search_t search{(uintptr_t)&own_build_id, 0};
    dl_iterate_phdr(find_build_id, &search);
    return search.id;
}

std::string names_text(uint64_t instance, uint64_t build)
{
    std::ostringstream out;
    out << instance << " " << std::hex << build << std::dec << "\n";
    for (size_t i = 1; i < (size_t)scope_id::count; i++)
        out << "scope " << i << " " << scope_info[i].name << "\n";
    for (size_t i = 1; i < (size_t)note_id::count; i++)
        out << "note " << i << " " << note_info[i].name << " " << note_info[i].text << "\n";
    return out.str();
}

void monitor_t::start(wl_event_loop *loop, const std::string& ring_path, uint64_t session, const faults_t& faults)
{
    build_id = own_build_id();
    ring_file = ring_path;
    if constexpr (abi::supported)
    {
        // The previous copy's ring in this compositor process continues in place. Anything else
        // (another process that used this display name, another ABI) gets a new file, renamed
        // over the old one: a reader still mapping the old inode keeps reading that old stream
        // intact, never reset sequence numbers or a file being resized under it.
        auto pid = (uint64_t)getpid(), started = process_start_time();
        void *base = MAP_FAILED;
        bool reuse = false;
        int fd = faults.ring ? -1 : open(ring_path.c_str(), O_RDWR | O_CLOEXEC);
        struct stat info{};
        if (fd >= 0 && fstat(fd, &info) == 0 && info.st_size == (off_t)abi::file_size)
        {
            base = mmap(nullptr, abi::file_size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
            reuse = base != MAP_FAILED && abi::word(base, abi::w_magic).load() == abi::magic &&
                abi::word(base, abi::w_abi).load() == abi::version &&
                abi::word(base, abi::w_pid).load() == pid &&
                abi::word(base, abi::w_start_time).load() == started;
            if (!reuse && base != MAP_FAILED) munmap(base, abi::file_size);
            if (!reuse) base = MAP_FAILED;
        }
        if (fd >= 0) close(fd);
        if (!reuse && !faults.ring)
        {
            auto partial = ring_path + ".new";
            fd = open(partial.c_str(), O_RDWR | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
            if (fd >= 0 && ftruncate(fd, abi::file_size) == 0)
                base = mmap(nullptr, abi::file_size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
            if (fd >= 0) close(fd);
            if (base != MAP_FAILED)
            {
                // A new file is zero: the identity first, the magic last, then it replaces the old.
                abi::word(base, abi::w_abi).store(abi::version);
                abi::word(base, abi::w_pid).store(pid);
                abi::word(base, abi::w_start_time).store(started);
                abi::word(base, abi::w_magic).store(abi::magic);
                if (rename(partial.c_str(), ring_path.c_str()) != 0)
                {
                    munmap(base, abi::file_size);
                    base = MAP_FAILED;
                }
            }
            if (base == MAP_FAILED) unlink(partial.c_str());
        }
        {
            if (base != MAP_FAILED)
            {
                if (reuse)
                {
                    // Touch every page: the next write must not fault on first use.
                    for (size_t i = 0; i < abi::total_words; i += 512)
                        abi::word(base, i).store(abi::word(base, i).load());
                }
                mlock_failed = faults.mlock || mlock(base, abi::file_size) != 0;
                ring = base;
                abi::word(ring, abi::w_session).store(session);
                instance = abi::word(ring, abi::w_instance).load() + 1;
                abi::word(ring, abi::w_instance).store(instance);
                abi::word(ring, abi::w_build).store(build_id);
                abi::word(ring, abi::w_flags).store(mlock_failed ? abi::flag_mlock_failed : 0);
                // A previous copy may have been stopped inside a scope (it can't have been: fini
                // runs outside them) or with a heartbeat outstanding: start from a clean sample.
                sample_begin();
                abi::word(ring, abi::w_current_scope).store(0);
                abi::word(ring, abi::w_current_work).store(0);
                abi::word(ring, abi::w_hb_acked).store(0);
                abi::word(ring, abi::w_last_work_end_ns).store(0);
                sample_end();
                abi::word(ring, abi::w_hb_requested).store(0);
                record_a(abi::k_loaded, 0, instance, now_ns(), 0, build_id);
                auto names = ring_path + ".names", partial = names + ".tmp";
                if (FILE *out = fopen(partial.c_str(), "we"))
                {
                    auto text = names_text(instance, build_id);
                    bool written = fwrite(text.data(), 1, text.size(), out) == text.size();
                    if ((fclose(out) == 0) && written) rename(partial.c_str(), names.c_str());
                    else unlink(partial.c_str());
                }
            }
        }
        if (!ring) return;  // no ring: only the in-memory counters

        heartbeat_fd = faults.eventfd ? -1 : eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
        if (heartbeat_fd < 0) return;
        heartbeat_source = faults.event_source ? nullptr :
            wl_event_loop_add_fd(loop, heartbeat_fd, WL_EVENT_READABLE, on_heartbeat, this);
        if (!heartbeat_source)
        {
            close(heartbeat_fd);
            heartbeat_fd = -1;
            return;
        }

        if (faults.thread) return;  // as if std::thread had thrown: no watchdog, ring stays on
        // The thread starts with every signal blocked: signals stay with the compositor's thread.
        sigset_t all, previous;
        sigfillset(&all);
        pthread_sigmask(SIG_SETMASK, &all, &previous);
        try
        {
            watch_stopping = false;
            watchdog = std::thread([this] { watch(); });
            pthread_setname_np(watchdog.native_handle(), "scottland-wd");
        } catch (...)
        {
        }
        pthread_sigmask(SIG_SETMASK, &previous, nullptr);
    }
}

void monitor_t::stop(bool remove_files)
{
    if (watchdog.joinable())
    {
        {
            std::lock_guard lock(watch_mutex);
            watch_stopping = true;
        }
        watch_cv.notify_all();
        watchdog.join();
    }
    if (heartbeat_source) wl_event_source_remove(heartbeat_source);
    heartbeat_source = nullptr;
    if (heartbeat_fd >= 0) close(heartbeat_fd);
    heartbeat_fd = -1;
    if (ring)
    {
        munmap(ring, abi::file_size);
        ring = nullptr;
        if (remove_files)
        {
            unlink(ring_file.c_str());
            unlink((ring_file + ".names").c_str());
        }
    }
}

void monitor_t::sample_begin()
{
    auto& seq = abi::word(ring, abi::w_sample_seq);
    seq.store(seq.load() + 1);
}

void monitor_t::sample_end()
{
    auto& seq = abi::word(ring, abi::w_sample_seq);
    seq.store(seq.load() + 1);
}

void monitor_t::record_a(uint32_t kind, uint32_t id, uint64_t child, uint64_t time, uint64_t duration, uint64_t value)
{
    if (ring) abi::write_record(ring, abi::ring_a, abi::w_head_a, instance, kind, id, child, time, duration, value);
}

void monitor_t::enter(scope_id id, uint64_t at)
{
    if (depth++ > 0) return;
    slowest_child = 0;
    slowest_child_ns = 0;
    bool work = scope_info[(size_t)id].kind == kind_t::work;
    if (!ring) return;
    sample_begin();
    abi::word(ring, abi::w_current_scope).store((uint64_t)id);
    abi::word(ring, abi::w_current_start_ns).store(at);
    abi::word(ring, abi::w_current_work).store(work ? 1 : 0);
    sample_end();
    // Idle to work: the watchdog goes to its fast rate now, not at its next 2 s round. This
    // thread alone knows both facts, so the transition has one owner.
    if (work && (at - last_work_end_ns > active_window_ns) && watchdog.joinable())
    {
        {
            std::lock_guard lock(watch_mutex);
            mode_changed = true;
        }
        watch_cv.notify_one();
    }
}

void monitor_t::ml2_compose(uint64_t window_start, ml2_episode_t& into) const
{
    // Per scope, clipped to the window; the largest eight kept, the rest summed. No allocation.
    std::array<std::pair<uint32_t, uint64_t>, 64> sums{};
    size_t distinct = 0;
    into.other = 0;
    for (size_t i = 0; i < ml2_size; i++)
    {
        auto& interval = ml2[(ml2_head + i) % ml2_capacity];
        auto length = interval.end - std::max(interval.start, window_start);
        size_t k = 0;
        while (k < distinct && sums[k].first != interval.id) k++;
        if (k == distinct)
        {
            if (distinct == sums.size()) { into.other += length; continue; }
            sums[distinct++] = {interval.id, 0};
        }
        sums[k].second += length;
    }
    std::sort(sums.begin(), sums.begin() + distinct, [] (auto& a, auto& b) { return a.second > b.second; });
    into.scopes = {};
    for (size_t k = 0; k < distinct; k++)
    {
        if (k < into.scopes.size()) into.scopes[k] = sums[k];
        else into.other += sums[k].second;
    }
}

void monitor_t::exit(scope_id id, uint64_t at, uint64_t started)
{
    auto duration = at - started;
    auto& s = stats[(size_t)id];
    s.calls++;
    s.total_ns += duration;
    s.max_ns = std::max(s.max_ns, duration);
    if (duration > budget_ns) s.over_budget++;
    if (--depth > 0)
    {
        if (duration > slowest_child_ns)
        {
            slowest_child_ns = duration;
            slowest_child = (uint32_t)id;
        }
        return;
    }

    s.outermost++;
    // ML2: drop intervals that ended before the window, clip the oldest that remains.
    if (ml2_size == ml2_capacity)
    {
        ml2_sum -= ml2[ml2_head].end - ml2[ml2_head].start;
        ml2_head = (ml2_head + 1) % ml2_capacity;
        ml2_size--;
    }
    ml2[(ml2_head + ml2_size) % ml2_capacity] = {started, at, (uint32_t)id};
    ml2_size++;
    ml2_sum += duration;
    auto window_start = at > window_ns ? at - window_ns : 0;
    while (ml2_size && ml2[ml2_head].end <= window_start)
    {
        ml2_sum -= ml2[ml2_head].end - ml2[ml2_head].start;
        ml2_head = (ml2_head + 1) % ml2_capacity;
        ml2_size--;
    }
    auto clipped = ml2_sum;
    if (ml2_size && ml2[ml2_head].start < window_start) clipped -= window_start - ml2[ml2_head].start;
    if (clipped > ml2_max)
    {
        ml2_max = clipped;
        ml2_max_at = at;
    }
    if (clipped > ml2_budget_ns)
    {
        if (!ml2_over) episode = {};
        ml2_over = true;
        if (clipped > episode.peak)
        {
            episode.peak = clipped;
            episode.at = at;
            ml2_compose(window_start, episode);
        }
    } else if (ml2_over)
    {
        ml2_over = false;
        episodes[episode_count++ % episodes.size()] = episode;
    }

    history[history_count++ % history.size()] = {(uint32_t)id, started, duration};
    bool work = scope_info[(size_t)id].kind == kind_t::work;
    if (work) last_work_end_ns = at;
    if (!ring) return;
    if (duration > slow_callback_ns)
    {
        slow_records++;
        record_a(abi::k_slow, (uint32_t)id, slowest_child, started, duration, slowest_child_ns);
    }
    sample_begin();
    abi::word(ring, abi::w_current_scope).store(0);
    abi::word(ring, abi::w_current_work).store(0);
    abi::word(ring, abi::w_last_scope).store((uint64_t)id);
    abi::word(ring, abi::w_last_end_ns).store(at);
    if (work) abi::word(ring, abi::w_last_work_end_ns).store(at);
    sample_end();
}

void monitor_t::note(note_id id, uint64_t a1, uint64_t a2, uint64_t a3, uint64_t a4)
{
    notes++;
    if (ring)
        abi::write_record(ring, abi::ring_a, abi::w_head_a, instance, abi::k_note, (uint32_t)id, a1, now_ns(), a2, a3, a4);
}

int monitor_t::on_heartbeat(int fd, uint32_t, void *data)
{
    SCOTTLAND_LOOP_SCOPE(heartbeat);
    uint64_t tokens;
    while (read(fd, &tokens, sizeof(tokens)) < 0 && errno == EINTR) {}
    static_cast<monitor_t*>(data)->heartbeat();
    return 0;
}

void monitor_t::heartbeat()
{
    if (!ring) return;
    heartbeats_acked++;
    auto requested = abi::word(ring, abi::w_hb_requested).load();
    sample_begin();
    abi::word(ring, abi::w_hb_acked).store(requested);
    abi::word(ring, abi::w_hb_ack_ns).store(now_ns());
    sample_end();
    if (heartbeat_hook) heartbeat_hook();
}

void monitor_t::watch()
{
    struct sample_t { uint64_t current, start, last, last_work_end, acked, work; } last{};
    auto load = [&] (sample_t& out)
    {
        for (int attempt = 0; attempt < 4; attempt++)
        {
            auto seq = abi::word(ring, abi::w_sample_seq).load();
            sample_t s{abi::word(ring, abi::w_current_scope).load(), abi::word(ring, abi::w_current_start_ns).load(),
                abi::word(ring, abi::w_last_scope).load(), abi::word(ring, abi::w_last_work_end_ns).load(),
                abi::word(ring, abi::w_hb_acked).load(), abi::word(ring, abi::w_current_work).load()};
            if (!(seq & 1) && abi::word(ring, abi::w_sample_seq).load() == seq)
            {
                out = s;
                return true;
            }
        }
        return false;
    };
    auto record = [&] (uint32_t kind, uint32_t id, uint64_t time, uint64_t value)
    {
        abi::write_record(ring, abi::ring_b, abi::w_head_b, instance, kind, id, 0, time, 0, value);
    };
    // Reported at 100 ms, 1 s and 5 s, then every 10 s.
    auto next_report = [] (uint64_t elapsed)
    {
        if (elapsed < 1000 * ms) return 1000 * ms;
        if (elapsed < 5000 * ms) return 5000 * ms;
        return (elapsed / (10000 * ms) + 1) * 10000 * ms;
    };

    std::unique_lock lock(watch_mutex);
    uint64_t previous_round = now_ns();
    while (true)
    {
        auto now = now_ns();
        bool active = last.work || (now - last.last_work_end < active_window_ns && last.last_work_end);
        watch_cv.wait_for(lock, std::chrono::milliseconds(active ? 100 : 2000),
            [&] { return watch_stopping || mode_changed; });
        mode_changed = false;
        if (watch_stopping) return;
        lock.unlock();

        now = now_ns();
        auto n = rounds.fetch_add(1);
        round_ns[n % round_ns.size()].store(now - previous_round);
        last_round_ns.store(now);
        previous_round = now;
        if (active) fast_rounds.fetch_add(1);
        if (!load(last))
        {
            unavailable_rounds.fetch_add(1);
            record(abi::k_unavailable, 0, now, 0);
        } else
        {
            // The clock after the sample: a scope that began after an earlier reading would
            // otherwise have a negative age.
            now = std::max(now_ns(), last.start);
            if (last.current && now - last.start >= 100 * ms)
            {
                if (stuck_start != last.start)
                {
                    stuck_start = last.start;
                    stuck_next = 100 * ms;
                }
                if (now - last.start >= stuck_next)
                {
                    record(abi::k_stuck, (uint32_t)last.current, last.start, now - last.start);
                    stuck_records.fetch_add(1);
                    stuck_next = next_report(now - last.start);
                }
            }
            bool outstanding = last.acked != hb_sent;
            if (outstanding && !last.current && now - hb_sent_ns >= 100 * ms)
            {
                if (late_since != hb_sent_ns)
                {
                    late_since = hb_sent_ns;
                    late_next = 100 * ms;
                }
                if (now - hb_sent_ns >= late_next)
                {
                    record(abi::k_unresponsive, (uint32_t)last.last, hb_sent_ns, now - hb_sent_ns);
                    unresponsive_records.fetch_add(1);
                    late_next = next_report(now - hb_sent_ns);
                }
            }
            if (!outstanding)
            {
                // One heartbeat at a time, timed from when it was first sent. The eventfd only
                // wakes the main loop; its counter means nothing.
                hb_sent++;
                abi::word(ring, abi::w_hb_requested).store(hb_sent);
                hb_sent_ns = now;
                heartbeats.fetch_add(1);
                uint64_t one = 1;
                while (write(heartbeat_fd, &one, sizeof(one)) < 0 && errno == EINTR) {}
            }
        }
        lock.lock();
    }
}

std::string monitor_t::stats_json(bool reset)
{
    std::ostringstream out;
    auto msf = [] (uint64_t ns) { return (double)ns / 1e6; };
    out << "{\"scopes\":{";
    bool first = true;
    for (size_t i = 1; i < stats.size(); i++)
    {
        auto& s = stats[i];
        if (!s.calls) continue;
        out << (first ? "" : ",") << "\"" << scope_info[i].name << "\":{\"calls\":" << s.calls
            << ",\"outermost\":" << s.outermost << ",\"total_ms\":" << msf(s.total_ns)
            << ",\"max_ms\":" << msf(s.max_ns) << ",\"over_2ms\":" << s.over_budget << "}";
        first = false;
    }
    out << "},\"ml2_max_ms\":" << msf(ml2_max) << ",\"ml2_max_at_ns\":" << ml2_max_at << ",\"ml2_episodes\":[";
    {
        // Closed episodes, and one still open, each with the scopes in its worst window.
        auto shown = std::min<uint64_t>(episode_count, episodes.size());
        bool any = false;
        auto emit = [&] (const ml2_episode_t& e)
        {
            out << (any ? "," : "") << "{\"peak_ms\":" << msf(e.peak) << ",\"at_ns\":" << e.at << ",\"other_ms\":" << msf(e.other)
                << ",\"scopes\":{";
            bool comma = false;
            for (auto& [sid, ns] : e.scopes)
            {
                if (!sid) continue;
                out << (comma ? "," : "") << "\"" << scope_info[sid].name << "\":" << msf(ns);
                comma = true;
            }
            out << "}}";
            any = true;
        };
        for (uint64_t i = 0; i < shown; i++) emit(episodes[(episode_count - shown + i) % episodes.size()]);
        if (ml2_over) emit(episode);
    }
    out << "],\"ml2_episodes_dropped\":" << (episode_count > episodes.size() ? episode_count - episodes.size() : 0)
        << ",\"history\":[";
    auto count = std::min<uint64_t>(history_count, history.size());
    for (uint64_t i = 0; i < count; i++)
    {
        auto& h = history[(history_count - count + i) % history.size()];
        out << (i ? "," : "") << "[\"" << scope_info[h.id].name << "\"," << h.start_ns << "," << msf(h.duration_ns) << "]";
    }
    out << "],\"ring\":{\"on\":" << (ring ? "true" : "false") << ",\"instance\":" << instance
        << ",\"mlock_failed\":" << (mlock_failed ? "true" : "false") << ",\"slow_records\":" << slow_records
        << ",\"notes\":" << notes << "}";
    out << ",\"watchdog\":{\"on\":" << (watchdog.joinable() ? "true" : "false") << ",\"rounds\":" << rounds.load()
        << ",\"fast_rounds\":" << fast_rounds.load() << ",\"heartbeats\":" << heartbeats.load()
        << ",\"heartbeats_acked\":" << heartbeats_acked << ",\"stuck\":" << stuck_records.load()
        << ",\"unresponsive\":" << unresponsive_records.load() << ",\"unavailable\":" << unavailable_rounds.load()
        << ",\"round_intervals_ms\":[";
    auto total_rounds = rounds.load();
    auto shown = std::min<uint64_t>(total_rounds, round_ns.size());
    for (uint64_t i = 0; i < shown; i++)
        out << (i ? "," : "") << msf(round_ns[(total_rounds - shown + i) % round_ns.size()].load());
    out << "]},\"build_id\":\"" << std::hex << build_id << std::dec << "\",\"now_ns\":" << now_ns() << "}";
    if (reset)
    {
        stats = {};
        ml2_max = 0;
        ml2_max_at = 0;
        episode_count = 0;
        ml2_over = false;
        history_count = 0;
    }
    return out.str();
}
}
