#pragma once

#include <wayfire/core.hpp>
#include <wayfire/util.hpp>

#include <gio/gio.h>
#include <dlfcn.h>
#include <fcntl.h>
#include <poll.h>
#include <pthread.h>
#include <spawn.h>
#include <signal.h>
#include <sys/eventfd.h>
#include <sys/ioctl.h>
// glibc declares these functions without C linkage for C++.
extern "C" {
#include <sys/pidfd.h>
}
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <deque>
#include <map>
#include <memory>
#include <mutex>
#include <new>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

extern char **environ;

namespace scottland::window_names
{
struct request_t
{
    uint64_t window = 0;
    uint64_t mode_generation = 0;
    uint64_t object_generation = 0;
    uint64_t eligibility_generation = 0;
    uint64_t request_generation = 0;
};

struct completion_t
{
    request_t request;
    bool has_row = false;
    bool suppress_caption = false;
    std::string name;
    pid_t pid = -1;
    uint64_t settlement_target_ns = 0;
};

namespace detail
{
struct queued_completion_t
{
    uint64_t serial = 0;
    completion_t completion;
};

struct invocation_t
{
    uint64_t serial = 0;
    request_t request;
    pthread_t worker{};
    void *module_pin = nullptr;
    bool worker_joinable = false; // compositor-thread owned
    bool join_error_logged = false;
    std::atomic<bool> late_settlement_logged{false};
};

struct state_t
{
    int event_fd = -1;
    std::mutex completed_mutex;
    std::deque<queued_completion_t> completed;
    std::atomic<uint64_t> mpris_generation{0};
    std::atomic<bool> mpris_started{false};
    std::atomic<bool> mpris_bus_request_done{false};
    std::mutex mpris_mutex;
    bool mpris_stop_requested = false; // protected by mpris_mutex
    GMainContext *mpris_context = nullptr; // state-owned references, protected by mutex
    GMainLoop *mpris_loop = nullptr;
    GCancellable *mpris_cancellable = nullptr;

    ~state_t()
    {
        if (mpris_context) g_main_context_unref(mpris_context);
        if (mpris_loop) g_main_loop_unref(mpris_loop);
        if (mpris_cancellable) g_object_unref(mpris_cancellable);
        if (event_fd >= 0) close(event_fd);
    }
};

// A core-owned consumer can outlive a plugin reload. Keep the defining module mapped while that
// object or any thread using its code remains live. The versioned custom-data key must change if
// this stored object's layout changes.
static __attribute__((noinline)) void *pin_current_module()
{
    Dl_info info{};
    if (!dladdr(reinterpret_cast<void*>(&pin_current_module), &info) || !info.dli_fname)
        return nullptr;
    return dlopen(info.dli_fname, RTLD_NOW | RTLD_NOLOAD | RTLD_LOCAL);
}

inline void release_module_pin(void *&pin)
{
    if (!pin) return;
    if (dlclose(pin) != 0)
        std::fprintf(stderr, "scottland-window-name: could not release a worker module reference\n");
    pin = nullptr;
}

inline uint64_t monotonic_ns()
{
    timespec ts{};
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) return 0;
    return uint64_t(ts.tv_sec) * 1000000000ULL + uint64_t(ts.tv_nsec);
}

inline void signal_event_fd(const std::shared_ptr<state_t>& state)
{
    if (state->event_fd < 0) return;
    const uint64_t one = 1;
    ssize_t written;
    do { written = write(state->event_fd, &one, sizeof(one)); }
    while (written < 0 && errno == EINTR);
    if (written < 0 && errno != EAGAIN)
        std::fprintf(stderr, "scottland-window-name: cannot notify the main loop: %s\n",
            std::strerror(errno));
    // EAGAIN means the eventfd is already readable. Workers never call Wayfire or wait for the
    // main loop.
}

inline void close_fd(int& fd)
{
    if (fd >= 0) close(std::exchange(fd, -1));
}

inline bool make_nonblocking(int fd)
{
    int flags = fcntl(fd, F_GETFL);
    return flags >= 0 && fcntl(fd, F_SETFL, flags | O_NONBLOCK) == 0;
}

// Read only the bytes visible in one FIONREAD snapshot. A descendant that inherited a pipe can
// keep writing after the direct child exits; repeatedly draining to EAGAIN would then have no
// finite endpoint. The one-byte nonblocking probe distinguishes EOF from an open, empty pipe.
inline void drain_available(int& fd, std::string *capture)
{
    if (fd < 0) return;
    int available = 0;
    const bool have_snapshot = ioctl(fd, FIONREAD, &available) == 0;
    size_t remaining = have_snapshot && available > 0 ? static_cast<size_t>(available) : 0;
    char buffer[8192];
    if (remaining == 0)
    {
        char byte = 0;
        ssize_t size;
        do { size = read(fd, &byte, 1); }
        while (size < 0 && errno == EINTR);
        if (size == 0)
        {
            close_fd(fd);
        } else if (size > 0)
        {
            if (capture) capture->push_back(byte);
        } else if (errno != EAGAIN && errno != EWOULDBLOCK)
        {
            close_fd(fd);
        }
        return;
    }
    while (remaining > 0)
    {
        const size_t request = std::min(remaining, sizeof(buffer));
        ssize_t size = read(fd, buffer, request);
        if (size > 0)
        {
            if (capture) capture->append(buffer, static_cast<size_t>(size));
            remaining -= static_cast<size_t>(size);
            continue;
        }
        if (size < 0 && errno == EINTR) continue;
        if (size < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) return;
        if (size == 0 || (size < 0 && errno != EINTR)) close_fd(fd);
        return;
    }
}

enum class child_state_t { running, terminated, unknown };

inline child_state_t observe_child(pid_t pid, int pidfd, int& status)
{
    int result;
    do { result = waitpid(pid, &status, WNOHANG); }
    while (result < 0 && errno == EINTR);
    if (result == pid || (result < 0 && errno == ECHILD)) return child_state_t::terminated;
    if (result < 0) return child_state_t::unknown;
    if (pidfd >= 0)
    {
        pollfd descriptor{pidfd, POLLIN, 0};
        int ready;
        do { ready = poll(&descriptor, 1, 0); }
        while (ready < 0 && errno == EINTR);
        if (ready < 0) return child_state_t::unknown;
        if (ready > 0 && (descriptor.revents & (POLLIN | POLLHUP | POLLERR)))
        {
            // Reap only after the direct child is observable through waitpid. A pidfd-ready
            // event alone does not release the slot or consumer-side settlement work.
            do { result = waitpid(pid, &status, WNOHANG); }
            while (result < 0 && errno == EINTR);
            if (result == pid || (result < 0 && errno == ECHILD))
                return child_state_t::terminated;
            if (result < 0) return child_state_t::unknown;
        }
    }
    // A zero WNOHANG result means this worker still owns an unreaped direct child. It is the
    // only waitpid caller for this PID, so a PID signal remains safe when pidfd_open is absent.
    return child_state_t::running;
}

inline void parse_rows(std::string_view output, uint64_t window, completion_t& completion)
{
    size_t begin = 0;
    while (begin < output.size())
    {
        size_t newline = output.find('\n', begin);
        if (newline == std::string_view::npos) break; // incomplete final row is not usable
        auto line = output.substr(begin, newline - begin);
        begin = newline + 1;
        if (line.empty()) continue;

        wf::json_t row;
        if (wf::json_t::parse_string(line, row) || !row.is_object() ||
            !row.has_member("window") || !row["window"].is_int64() ||
            uint64_t(row["window"].as_int64()) != window || row.has_member("error") ||
            !row.has_member("name"))
            continue;

        if (row["name"].is_string())
        {
            completion.has_row = true;
            completion.name = row["name"].as_string();
            return;
        }
        if (row["name"].is_null())
        {
            completion.has_row = true;
            completion.suppress_caption = true;
            return;
        }
    }
}

inline void log_late_settlement(pid_t pid)
{
    std::fprintf(stderr,
        "scottland-window-name: caption invocation settlement target missed for pid=%ld\n",
        long(pid));
}

inline void log_late_settlement_once(const std::shared_ptr<invocation_t>& invocation, pid_t pid)
{
    bool expected = false;
    if (invocation->late_settlement_logged.compare_exchange_strong(expected, true,
        std::memory_order_acq_rel))
        log_late_settlement(pid);
}

inline void enqueue_completion(const std::shared_ptr<state_t>& state, uint64_t serial,
    completion_t completion)
{
    {
        std::lock_guard lock(state->completed_mutex);
        state->completed.push_back({serial, std::move(completion)});
    }
    signal_event_fd(state);
}

inline void run_invocation(const std::shared_ptr<state_t>& state,
    const std::shared_ptr<invocation_t>& invocation)
{
    completion_t completion;
    completion.request = invocation->request;
    int out_pipe[2]{-1, -1}, err_pipe[2]{-1, -1};
    if (pipe2(out_pipe, O_CLOEXEC) != 0 ||
        pipe2(err_pipe, O_CLOEXEC) != 0 ||
        !make_nonblocking(out_pipe[0]) || !make_nonblocking(err_pipe[0]))
    {
        close_fd(out_pipe[0]); close_fd(out_pipe[1]);
        close_fd(err_pipe[0]); close_fd(err_pipe[1]);
        enqueue_completion(state, invocation->serial, std::move(completion));
        return;
    }

    posix_spawn_file_actions_t actions;
    int action_result = posix_spawn_file_actions_init(&actions);
    const bool actions_initialized = action_result == 0;
    if (action_result == 0)
    {
        action_result = posix_spawn_file_actions_addopen(&actions, STDIN_FILENO,
            "/dev/null", O_RDONLY, 0);
        if (action_result == 0) action_result = posix_spawn_file_actions_adddup2(&actions,
            out_pipe[1], STDOUT_FILENO);
        if (action_result == 0) action_result = posix_spawn_file_actions_adddup2(&actions,
            err_pipe[1], STDERR_FILENO);
        if (action_result == 0) action_result = posix_spawn_file_actions_addclose(&actions, out_pipe[0]);
        if (action_result == 0) action_result = posix_spawn_file_actions_addclose(&actions, err_pipe[0]);
        if (action_result == 0) action_result = posix_spawn_file_actions_addclose(&actions, out_pipe[1]);
        if (action_result == 0) action_result = posix_spawn_file_actions_addclose(&actions, err_pipe[1]);
    }
    if (action_result != 0)
    {
        if (actions_initialized) posix_spawn_file_actions_destroy(&actions);
        close_fd(out_pipe[0]); close_fd(out_pipe[1]);
        close_fd(err_pipe[0]); close_fd(err_pipe[1]);
        enqueue_completion(state, invocation->serial, std::move(completion));
        return;
    }

    const std::string window = std::to_string(invocation->request.window);
    std::string executable = "scottland-window-name";
    char budget_option[] = "--invocation-budget-ms";
    char budget_value[] = "2600";
    char origin_option[] = "--invocation-start-monotonic-ns";
    char origin[32]{};
    char *argv[]{executable.data(), budget_option, budget_value, origin_option,
        origin, const_cast<char*>(window.c_str()), nullptr};
    const uint64_t t0 = monotonic_ns();
    if (t0 == 0 || t0 > UINT64_MAX - 3000000000ULL)
    {
        posix_spawn_file_actions_destroy(&actions);
        close_fd(out_pipe[0]); close_fd(out_pipe[1]);
        close_fd(err_pipe[0]); close_fd(err_pipe[1]);
        enqueue_completion(state, invocation->serial, std::move(completion));
        return;
    }
    const uint64_t dk = t0 + 2800000000ULL;
    const uint64_t d = t0 + 3000000000ULL;
    completion.settlement_target_ns = d;
    const int origin_length = std::snprintf(origin, sizeof(origin), "%llu",
        static_cast<unsigned long long>(t0));
    if (origin_length < 0 || static_cast<size_t>(origin_length) >= sizeof(origin))
    {
        posix_spawn_file_actions_destroy(&actions);
        close_fd(out_pipe[0]); close_fd(out_pipe[1]);
        close_fd(err_pipe[0]); close_fd(err_pipe[1]);
        enqueue_completion(state, invocation->serial, std::move(completion));
        return;
    }

    pid_t pid = -1;
    int spawn_result = posix_spawnp(&pid, executable.c_str(), &actions, nullptr, argv, environ);
    posix_spawn_file_actions_destroy(&actions);
    close_fd(out_pipe[1]); close_fd(err_pipe[1]);
    if (spawn_result != 0)
    {
        close_fd(out_pipe[0]); close_fd(err_pipe[0]);
        enqueue_completion(state, invocation->serial, std::move(completion));
        return;
    }

    completion.pid = pid;
    const int pidfd = pidfd_open(pid, 0);
    if (pidfd < 0)
        std::fprintf(stderr,
            "scottland-window-name: pidfd unavailable for caption CLI pid=%ld; using observed unreaped-child fallback at Dk: %s\n",
            long(pid), std::strerror(errno));
    std::string output;
    bool terminated = false, endpoints_closed = false, kill_attempted = false;
    int status = 0;
    while (!terminated)
    {
        uint64_t now = monotonic_ns();
        if (now >= dk)
        {
            const auto child_state = observe_child(pid, pidfd, status);
            terminated = child_state == child_state_t::terminated;
            if (!endpoints_closed)
            {
                drain_available(out_pipe[0], &output);
                drain_available(err_pipe[0], nullptr);
                close_fd(out_pipe[0]); close_fd(err_pipe[0]);
                endpoints_closed = true;
            }
            if (!terminated && !kill_attempted)
            {
                kill_attempted = true;
                int signal_result = -1;
                bool signal_attempted = false;
                if (pidfd >= 0)
                {
                    do { signal_result = pidfd_send_signal(pidfd, SIGKILL, nullptr, 0); }
                    while (signal_result != 0 && errno == EINTR);
                    signal_attempted = true;
                } else
                {
                    if (child_state == child_state_t::running)
                    {
                        do { signal_result = kill(pid, SIGKILL); }
                        while (signal_result != 0 && errno == EINTR);
                        signal_attempted = true;
                    }
                    else
                        std::fprintf(stderr,
                            "scottland-window-name: cannot verify caption CLI pid=%ld ownership at Dk\n",
                            long(pid));
                }
                if (signal_attempted && signal_result != 0 && errno != ESRCH)
                    std::fprintf(stderr,
                        "scottland-window-name: could not kill caption CLI pid=%ld: %s\n",
                        long(pid), std::strerror(errno));
            }
            if (terminated) break;
            poll(nullptr, 0, 10);
        } else
        {
            int remaining_ms = int(std::min<uint64_t>((dk - now + 999999ULL) / 1000000ULL, 100ULL));
            pollfd descriptors[3];
            nfds_t count = 0;
            if (out_pipe[0] >= 0) descriptors[count++] = {out_pipe[0], POLLIN | POLLHUP | POLLERR, 0};
            if (err_pipe[0] >= 0) descriptors[count++] = {err_pipe[0], POLLIN | POLLHUP | POLLERR, 0};
            if (pidfd >= 0) descriptors[count++] = {pidfd, POLLIN | POLLHUP | POLLERR, 0};
            int ready;
            do { ready = poll(descriptors, count, remaining_ms); }
            while (ready < 0 && errno == EINTR);
            if (ready > 0)
            {
                if (out_pipe[0] >= 0) drain_available(out_pipe[0], &output);
                if (err_pipe[0] >= 0) drain_available(err_pipe[0], nullptr);
            }
            terminated = observe_child(pid, pidfd, status) == child_state_t::terminated;
            if (terminated && !endpoints_closed)
            {
                drain_available(out_pipe[0], &output);
                drain_available(err_pipe[0], nullptr);
                close_fd(out_pipe[0]); close_fd(err_pipe[0]);
                endpoints_closed = true;
            }
        }

        if (monotonic_ns() >= d && !terminated)
            log_late_settlement_once(invocation, pid);
    }

    if (!endpoints_closed)
    {
        drain_available(out_pipe[0], &output);
        drain_available(err_pipe[0], nullptr);
        close_fd(out_pipe[0]); close_fd(err_pipe[0]);
        endpoints_closed = true;
    }
    parse_rows(output, invocation->request.window, completion);
    if (monotonic_ns() >= d) log_late_settlement_once(invocation, pid);
    if (pidfd >= 0) close(pidfd);
    enqueue_completion(state, invocation->serial, std::move(completion));
}

inline void *invocation_entry(void *opaque)
{
    std::unique_ptr<std::pair<std::shared_ptr<state_t>, std::shared_ptr<invocation_t>>> args(
        static_cast<std::pair<std::shared_ptr<state_t>, std::shared_ptr<invocation_t>>*>(opaque));
    run_invocation(args->first, args->second);
    return nullptr;
}

struct mpris_loop_t
{
    std::shared_ptr<state_t> state;
    GMainContext *context = nullptr;
    GMainLoop *loop = nullptr;
    GCancellable *cancellable = nullptr;
    GDBusConnection *connection = nullptr;
    guint subscriptions[3]{};
};

inline void mpris_signal(GDBusConnection*, const gchar*, const gchar*, const gchar*,
    const gchar *signal_name, GVariant *parameters, gpointer data)
{
    auto state = *static_cast<std::shared_ptr<state_t>*>(data);
    if (g_strcmp0(signal_name, "NameOwnerChanged") == 0)
    {
        const gchar *name = nullptr, *old_owner = nullptr, *new_owner = nullptr;
        g_variant_get(parameters, "(&s&s&s)", &name, &old_owner, &new_owner);
        (void)old_owner; (void)new_owner;
        if (!name || !g_str_has_prefix(name, "org.mpris.MediaPlayer2.")) return;
    } else if (g_strcmp0(signal_name, "PropertiesChanged") == 0)
    {
        const gchar *interface_name = nullptr;
        GVariant *changed = nullptr, *invalidated = nullptr;
        g_variant_get(parameters, "(&s@a{sv}@as)", &interface_name, &changed, &invalidated);
        const bool relevant = interface_name &&
            (g_str_equal(interface_name, "org.mpris.MediaPlayer2") ||
             g_str_equal(interface_name, "org.mpris.MediaPlayer2.Player"));
        if (changed) g_variant_unref(changed);
        if (invalidated) g_variant_unref(invalidated);
        if (!relevant) return;
    } else
    {
        return;
    }
    state->mpris_generation.fetch_add(1, std::memory_order_release);
    signal_event_fd(state);
}

inline void destroy_shared_state(gpointer data)
{
    delete static_cast<std::shared_ptr<state_t>*>(data);
}

struct mpris_quit_data_t
{
    std::shared_ptr<state_t> state;
    GMainLoop *loop = nullptr;
};

inline gboolean mpris_quit_source(gpointer data)
{
    auto *quit = static_cast<mpris_quit_data_t*>(data);
    if (quit->state->mpris_bus_request_done.load(std::memory_order_acquire))
        g_main_loop_quit(quit->loop);
    return G_SOURCE_REMOVE;
}

inline void destroy_mpris_quit_data(gpointer data)
{
    auto *quit = static_cast<mpris_quit_data_t*>(data);
    if (quit->loop) g_main_loop_unref(quit->loop);
    delete quit;
}

inline void queue_mpris_quit(const std::shared_ptr<state_t>& state,
    GMainContext *context, GMainLoop *loop)
{
    if (!context || !loop) return;
    // Attaching an idle source avoids g_main_context_invoke() running g_main_loop_quit before
    // the loop has entered g_main_loop_run().
    GSource *source = g_idle_source_new();
    if (!source) return;
    auto *quit = new (std::nothrow) mpris_quit_data_t{state, g_main_loop_ref(loop)};
    if (!quit)
    {
        g_source_unref(source);
        return;
    }
    g_source_set_callback(source, mpris_quit_source, quit, destroy_mpris_quit_data);
    g_source_attach(source, context);
    g_source_unref(source);
    g_main_context_wakeup(context);
}

inline void mpris_bus_ready(GObject*, GAsyncResult *result, gpointer data)
{
    auto *owner = static_cast<mpris_loop_t*>(data);
    GError *error = nullptr;
    owner->connection = g_bus_get_finish(result, &error);
    owner->state->mpris_bus_request_done.store(true, std::memory_order_release);
    bool stop_requested = false;
    {
        std::lock_guard lock(owner->state->mpris_mutex);
        stop_requested = owner->state->mpris_stop_requested;
    }
    if (owner->connection)
    {
        auto make_data = [&] { return new std::shared_ptr<state_t>(owner->state); };
        owner->subscriptions[0] = g_dbus_connection_signal_subscribe(owner->connection,
            "org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
            "/org/freedesktop/DBus", nullptr, G_DBUS_SIGNAL_FLAGS_NONE,
            mpris_signal, make_data(), destroy_shared_state);
        owner->subscriptions[1] = g_dbus_connection_signal_subscribe(owner->connection,
            nullptr, "org.freedesktop.DBus.Properties", "PropertiesChanged", nullptr,
            "org.mpris.MediaPlayer2", G_DBUS_SIGNAL_FLAGS_NONE,
            mpris_signal, make_data(), destroy_shared_state);
        owner->subscriptions[2] = g_dbus_connection_signal_subscribe(owner->connection,
            nullptr, "org.freedesktop.DBus.Properties", "PropertiesChanged", nullptr,
            "org.mpris.MediaPlayer2.Player", G_DBUS_SIGNAL_FLAGS_NONE,
            mpris_signal, make_data(), destroy_shared_state);
    } else
    {
        if (!g_cancellable_is_cancelled(owner->cancellable))
            std::fprintf(stderr, "scottland-window-name: MPRIS change observation unavailable\n");
        if (error) g_error_free(error);
    }
    if (!owner->connection || stop_requested) g_main_loop_quit(owner->loop);
}

inline void *mpris_entry(void *opaque)
{
    std::unique_ptr<std::shared_ptr<state_t>> state_holder(
        static_cast<std::shared_ptr<state_t>*>(opaque));
    mpris_loop_t owner;
    owner.state = *state_holder;
    owner.context = g_main_context_new();
    if (!owner.context)
    {
        owner.state->mpris_started.store(false, std::memory_order_release);
        signal_event_fd(owner.state);
        return nullptr;
    }
    g_main_context_push_thread_default(owner.context);
    owner.loop = g_main_loop_new(owner.context, FALSE);
    owner.cancellable = g_cancellable_new();
    if (!owner.loop || !owner.cancellable)
    {
        if (owner.loop) g_main_loop_unref(owner.loop);
        if (owner.cancellable) g_object_unref(owner.cancellable);
        g_main_context_pop_thread_default(owner.context);
        g_main_context_unref(owner.context);
        owner.state->mpris_started.store(false, std::memory_order_release);
        signal_event_fd(owner.state);
        return nullptr;
    }

    bool stop_requested = false;
    {
        std::lock_guard lock(owner.state->mpris_mutex);
        owner.state->mpris_context = g_main_context_ref(owner.context);
        owner.state->mpris_loop = g_main_loop_ref(owner.loop);
        owner.state->mpris_cancellable = G_CANCELLABLE(g_object_ref(owner.cancellable));
        stop_requested = owner.state->mpris_stop_requested;
    }
    g_bus_get(G_BUS_TYPE_SESSION, owner.cancellable, mpris_bus_ready, &owner);
    if (stop_requested) g_cancellable_cancel(owner.cancellable);
    g_main_loop_run(owner.loop);

    if (owner.connection)
    {
        for (guint subscription : owner.subscriptions)
            if (subscription) g_dbus_connection_signal_unsubscribe(owner.connection, subscription);
        g_object_unref(owner.connection);
    }
    {
        std::lock_guard lock(owner.state->mpris_mutex);
        if (owner.state->mpris_context) g_main_context_unref(owner.state->mpris_context);
        if (owner.state->mpris_loop) g_main_loop_unref(owner.state->mpris_loop);
        if (owner.state->mpris_cancellable) g_object_unref(owner.state->mpris_cancellable);
        owner.state->mpris_context = nullptr;
        owner.state->mpris_loop = nullptr;
        owner.state->mpris_cancellable = nullptr;
        owner.state->mpris_stop_requested = false;
    }
    g_object_unref(owner.cancellable);
    g_main_loop_unref(owner.loop);
    g_main_context_pop_thread_default(owner.context);
    g_main_context_unref(owner.context);
    owner.state->mpris_started.store(false, std::memory_order_release);
    signal_event_fd(owner.state);
    return nullptr;
}
} // namespace detail

// This data belongs to Wayfire core rather than a plugin instance. Completed work is joined on
// the compositor thread only with pthread_tryjoin_np; while it is still returning, its slot and
// module reference remain counted. During plugin teardown, unfinished state remains core-owned
// so a reloaded instance can finish settlement without callbacks into the old UI object.
class consumer_t : public wf::custom_data_t
{
  public:
    static constexpr const char *DATA_KEY = "scottland/window-name-consumer-v1";
    static constexpr size_t ACTIVE_CAP = 4;

    consumer_t() : state(std::make_shared<detail::state_t>()), module_pin(detail::pin_current_module())
    {
        state->event_fd = eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
        ready = state->event_fd >= 0 && module_pin;
    }

    bool ready = false;
    std::shared_ptr<detail::state_t> state;
    void *module_pin = nullptr; // released after this custom-data object is erased, or leaked at shutdown
    uint64_t mpris_seen = 0;

    uint64_t next_generation()
    {
        ++generation;
        if (generation == 0) ++generation;
        return generation;
    }

    bool start_mpris_observer()
    {
        if (!ready) return false;
        if (mpris_joinable && !reap_mpris_observer())
        {
            mpris_restart_requested = true;
            return false;
        }
        if (state->mpris_started.load(std::memory_order_acquire)) return true;
        mpris_restart_requested = false;

        void *worker_pin = detail::pin_current_module();
        if (!worker_pin) return false;
        auto *argument = new (std::nothrow) std::shared_ptr<detail::state_t>(state);
        if (!argument)
        {
            detail::release_module_pin(worker_pin);
            return false;
        }
        {
            std::lock_guard lock(state->mpris_mutex);
            state->mpris_stop_requested = false;
        }
        state->mpris_bus_request_done.store(false, std::memory_order_release);
        state->mpris_started.store(true, std::memory_order_release);
        pthread_t thread{};
        int error = pthread_create(&thread, nullptr, detail::mpris_entry, argument);
        if (error != 0)
        {
            delete argument;
            state->mpris_started.store(false, std::memory_order_release);
            detail::release_module_pin(worker_pin);
            std::fprintf(stderr, "scottland-window-name: could not start MPRIS observer: %s\n",
                std::strerror(error));
            return false;
        }
        mpris_thread = thread;
        mpris_module_pin = worker_pin;
        mpris_joinable = true;
        return true;
    }

    void stop_mpris_observer()
    {
        GMainContext *context = nullptr;
        GMainLoop *loop = nullptr;
        GCancellable *cancellable = nullptr;
        {
            std::lock_guard lock(state->mpris_mutex);
            state->mpris_stop_requested = true;
            if (state->mpris_context) context = g_main_context_ref(state->mpris_context);
            if (state->mpris_loop) loop = g_main_loop_ref(state->mpris_loop);
            if (state->mpris_cancellable)
                cancellable = G_CANCELLABLE(g_object_ref(state->mpris_cancellable));
        }
        if (cancellable)
        {
            g_cancellable_cancel(cancellable);
            g_object_unref(cancellable);
        }
        if (context && loop) detail::queue_mpris_quit(state, context, loop);
        if (context) g_main_context_unref(context);
        if (loop) g_main_loop_unref(loop);
    }

    bool reap_mpris_observer()
    {
        if (!mpris_joinable) return true;
        int result = pthread_tryjoin_np(mpris_thread, nullptr);
        if (result == EBUSY) return false;
        if (result != 0)
        {
            std::fprintf(stderr, "scottland-window-name: could not join MPRIS observer: %s\n",
                std::strerror(result));
            return false;
        }
        mpris_joinable = false;
        detail::release_module_pin(mpris_module_pin);
        return true;
    }

    bool mpris_needs_join() const
    {
        return mpris_joinable && !state->mpris_started.load(std::memory_order_acquire);
    }

    bool mpris_restart_requested = false; // compositor-thread owned

    uint64_t mpris_generation() const
    {
        return state->mpris_generation.load(std::memory_order_acquire);
    }

    void request(request_t request)
    {
        if (!ready) return;
        if (!pending.count(request.window)) pending_order.push_back(request.window);
        pending[request.window] = request;
        schedule();
    }

    void cancel_pending(uint64_t window)
    {
        pending.erase(window);
        pending_order.erase(std::remove(pending_order.begin(), pending_order.end(), window),
            pending_order.end());
    }

    void cancel_all_pending()
    {
        pending.clear();
        pending_order.clear();
    }

    std::vector<completion_t> take_settled()
    {
        std::deque<detail::queued_completion_t> queued;
        {
            std::lock_guard lock(state->completed_mutex);
            queued.swap(state->completed);
        }
        std::vector<completion_t> result;
        std::deque<detail::queued_completion_t> retry;
        for (auto& item : queued)
        {
            auto active_invocation = active.find(item.serial);
            if (active_invocation == active.end()) continue; // already consumed once
            auto& invocation = active_invocation->second;
            if (item.completion.settlement_target_ns != 0 &&
                detail::monotonic_ns() >= item.completion.settlement_target_ns)
                detail::log_late_settlement_once(invocation, item.completion.pid);
            if (invocation->worker_joinable)
            {
                int join_result = pthread_tryjoin_np(invocation->worker, nullptr);
                if (join_result == EBUSY)
                {
                    retry.push_back(std::move(item));
                    continue;
                }
                if (join_result != 0)
                {
                    if (!invocation->join_error_logged)
                    {
                        std::fprintf(stderr,
                            "scottland-window-name: could not join caption worker serial=%llu: %s\n",
                            static_cast<unsigned long long>(invocation->serial), std::strerror(join_result));
                        invocation->join_error_logged = true;
                    }
                    retry.push_back(std::move(item));
                    continue;
                }
                invocation->worker_joinable = false;
            }
            detail::release_module_pin(invocation->module_pin);
            auto active_window = active_by_window.find(invocation->request.window);
            if (active_window != active_by_window.end() && active_window->second == invocation->serial)
                active_by_window.erase(active_window);
            active.erase(active_invocation);
            result.push_back(std::move(item.completion));
        }
        if (!retry.empty())
        {
            std::lock_guard lock(state->completed_mutex);
            while (!retry.empty())
            {
                state->completed.push_back(std::move(retry.front()));
                retry.pop_front();
            }
        }
        schedule();
        return result;
    }

    bool has_unjoined_completions() const
    {
        std::lock_guard lock(state->completed_mutex);
        return !state->completed.empty();
    }

    bool idle_for_removal() const
    {
        return active.empty() && pending.empty() && !mpris_joinable &&
            !state->mpris_started.load(std::memory_order_acquire);
    }

    void *take_module_pin_for_removal()
    {
        return std::exchange(module_pin, nullptr);
    }

  private:
    uint64_t generation = 0, serial = 0;
    std::map<uint64_t, request_t> pending;
    std::deque<uint64_t> pending_order;
    std::map<uint64_t, std::shared_ptr<detail::invocation_t>> active;
    std::map<uint64_t, uint64_t> active_by_window;
    pthread_t mpris_thread{};
    void *mpris_module_pin = nullptr;
    bool mpris_joinable = false; // compositor-thread owned

    void start(const request_t& request)
    {
        auto invocation = std::make_shared<detail::invocation_t>();
        invocation->serial = ++serial;
        invocation->request = request;
        active.emplace(invocation->serial, invocation);
        active_by_window[request.window] = invocation->serial;
        auto *argument = new (std::nothrow) std::pair<std::shared_ptr<detail::state_t>,
            std::shared_ptr<detail::invocation_t>>(state, invocation);
        if (!argument)
        {
            completion_t failure; failure.request = request;
            detail::enqueue_completion(state, invocation->serial, std::move(failure));
            return;
        }
        invocation->module_pin = detail::pin_current_module();
        if (!invocation->module_pin)
        {
            delete argument;
            completion_t failure; failure.request = request;
            detail::enqueue_completion(state, invocation->serial, std::move(failure));
            return;
        }
        int error = pthread_create(&invocation->worker, nullptr, detail::invocation_entry, argument);
        if (error != 0)
        {
            delete argument;
            detail::release_module_pin(invocation->module_pin);
            completion_t failure; failure.request = request;
            detail::enqueue_completion(state, invocation->serial, std::move(failure));
            return;
        }
        invocation->worker_joinable = true;
    }

    void schedule()
    {
        size_t candidates = pending_order.size();
        while (active.size() < ACTIVE_CAP && candidates-- > 0 && !pending_order.empty())
        {
            const uint64_t window = pending_order.front();
            pending_order.pop_front();
            auto request = pending.find(window);
            if (request == pending.end()) continue;
            if (active_by_window.count(window))
            {
                pending_order.push_back(window);
                continue;
            }
            auto value = request->second;
            pending.erase(request);
            start(value);
        }
    }
};
} // namespace scottland::window_names
