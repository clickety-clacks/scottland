// Widget handover across scottland-reload (docs/main-loop.md, "Reload"; design 3.8).
//
// Two kinds of resource cross a reload: duplicated launcher pidfds, and leases (Scottland's one
// disable on an application's root node, held per window id). A number or lease id is acted on
// only when its ownership is established outside the handover file: by the environment list the
// outgoing copy of this process published, or for the legacy format by the reload receipt.
// Standard library and POSIX only; the plugin supplies the scene operations.
#pragma once
#include <algorithm>
#include <climits>
#include <cstdio>
#include <cstring>
#include <array>
#include <cerrno>
#include <cstdint>
#include <cstdlib>
#include <fcntl.h>
#include <fstream>
#include <optional>
#include <random>
#include <sstream>
#include <string>
#include <unistd.h>
#include <vector>

namespace scottland::handover
{
constexpr const char *environment = "SCOTTLAND_INTERNAL_HANDOVER";
constexpr int format = 2;

/** Is fd an open pidfd? No allocation, no exception (it decides ownership). */
inline bool is_pidfd(int fd) noexcept
{
    if (fd < 0 || fcntl(fd, F_GETFD) < 0) return false;
    char link[64], target[64];
    snprintf(link, sizeof(link), "/proc/self/fd/%d", fd);
    auto size = readlink(link, target, sizeof(target) - 1);
    static const char pidfd[] = "anon_inode:[pidfd]";
    return size == (ssize_t)(sizeof(pidfd) - 1) && memcmp(target, pidfd, size) == 0;
}

/** The Pid: line of a pidfd's fdinfo: the live pid, -1 once the process is reaped, nullopt if
 *  unreadable. No allocation, no exception. */
inline std::optional<long> pidfd_pid(int fd) noexcept
{
    char path[64], text[1024];
    snprintf(path, sizeof(path), "/proc/self/fdinfo/%d", fd);
    int info = open(path, O_RDONLY | O_CLOEXEC);
    if (info < 0) return std::nullopt;
    ssize_t size = read(info, text, sizeof(text) - 1);
    close(info);
    if (size <= 0) return std::nullopt;
    text[size] = 0;
    for (const char *line = text; line && *line; line = strchr(line, '\n') ? strchr(line, '\n') + 1 : nullptr)
        if (strncmp(line, "Pid:", 4) == 0) return strtol(line + 4, nullptr, 10);
    return std::nullopt;
}

/** Clock ticks since boot at which this process started (/proc/self/stat field 22). */
inline uint64_t process_start_time(const std::string& pid = "self")
{
    std::ifstream in("/proc/" + pid + "/stat");
    std::string text((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    auto at = text.rfind(')');
    if (at == std::string::npos) return 0;
    std::istringstream fields(text.substr(at + 2));
    std::string field;
    for (int i = 3; i <= 22 && (fields >> field); i++)
        if (i == 22) return std::strtoull(field.c_str(), nullptr, 10);
    return 0;
}

inline std::string random_id()
{
    std::random_device device;
    std::ostringstream out;
    out << std::hex << device() << device() << device() << device();
    return out.str();
}

/** Most widgets one reload transfers; the writer hands over no more (the rest unload normally). */
constexpr size_t max_transfer = 256;

/**
 * The environment list: "<id>;version=<model version>;fds=<n,…>;leases=<window id,…>". The
 * version is the model version the outgoing copy also leaves in SCOTTLAND_INTERNAL_MODEL_VERSION:
 * an older plugin build loaded in between (a rollback) publishes and changes it without consuming
 * the list, and a list that no longer matches is stale and owns nothing. Fixed capacity: reading
 * it allocates nothing, so taking ownership of what it names can't fail half way.
 */
struct list_t
{
    char id[65] = {};
    uint64_t version = 0;
    std::array<int, max_transfer> fds{};
    size_t fd_count = 0;
    std::array<uint64_t, max_transfer> leases{};
    size_t lease_count = 0;
    bool add_fd(int fd) noexcept { if (fd_count == max_transfer) return false; fds[fd_count++] = fd; return true; }
    bool add_lease(uint64_t w) noexcept { if (lease_count == max_transfer) return false; leases[lease_count++] = w; return true; }
};

inline std::string format_list(const list_t& list)
{
    std::ostringstream out;
    out << list.id << ";version=" << list.version << ";fds=";
    for (size_t i = 0; i < list.fd_count; i++) out << (i ? "," : "") << list.fds[i];
    out << ";leases=";
    for (size_t i = 0; i < list.lease_count; i++) out << (i ? "," : "") << list.leases[i];
    return out.str();
}

/** Parse without allocating. False for anything malformed (then nothing in it is trusted). */
inline bool parse_list(const char *text, list_t& list) noexcept
{
    list = list_t{};
    const char *at = strstr(text, ";version=");
    if (!at || at == text || size_t(at - text) >= sizeof(list.id)) return false;
    memcpy(list.id, text, at - text);
    list.id[at - text] = 0;
    auto number = [] (const char *&p, uint64_t& value) noexcept
    {
        if (*p < '0' || *p > '9') return false;
        value = 0;
        for (; *p >= '0' && *p <= '9'; p++)
        {
            if (value > (UINT64_MAX - 9) / 10) return false;
            value = value * 10 + uint64_t(*p - '0');
        }
        return true;
    };
    const char *p = at + 9;
    if (!number(p, list.version) || strncmp(p, ";fds=", 5) != 0) return false;
    p += 5;
    while (*p != ';')
    {
        uint64_t fd;
        if (!number(p, fd) || fd > INT32_MAX || !list.add_fd(int(fd))) return false;
        if (*p == ',') p++;
        else if (*p != ';') return false;
    }
    if (strncmp(p, ";leases=", 8) != 0) return false;
    p += 8;
    while (*p)
    {
        uint64_t window;
        if (!number(p, window) || !list.add_lease(window)) return false;
        if (*p == ',') p++;
        else if (*p) return false;
    }
    return true;
}

/**
 * What a reload transferred to this copy and it has not adopted yet. Whatever remains when
 * import ends, for any reason (a rejected receipt, a bad file, a failed init()), is disposed of
 * exactly once: each handle closed, each lease returned with one enable. Nothing here throws.
 */
class pending_t
{
  public:
    pending_t() = default;
    pending_t(const pending_t&) = delete;
    pending_t& operator =(const pending_t&) = delete;
    // fini() disposes with the scene's enable; this only closes handles a failed load left.
    ~pending_t() { dispose(nullptr, nullptr); }

    /** Take ownership of an open pidfd (refused if it isn't one, is already held, or the owner
     *  is full). No allocation. */
    bool own_fd(int fd) noexcept
    {
        if (fd_count == max_transfer || !is_pidfd(fd) || holds_fd(fd)) return false;
        fds[fd_count++] = fd;
        return true;
    }

    bool own_lease(uint64_t window) noexcept
    {
        if (lease_count == max_transfer || holds_lease(window)) return false;
        leases[lease_count++] = window;
        return true;
    }

    bool holds_fd(int fd) const noexcept
    {
        for (size_t i = 0; i < fd_count; i++) if (fds[i] == fd) return true;
        return false;
    }
    bool holds_lease(uint64_t window) const noexcept
    {
        for (size_t i = 0; i < lease_count; i++) if (leases[i] == window) return true;
        return false;
    }

    /** An entry claims what this owner holds, once: the caller owns it afterwards. */
    int claim_fd(int fd) noexcept
    {
        for (size_t i = 0; i < fd_count; i++)
            if (fds[i] == fd)
            {
                fds[i] = fds[--fd_count];
                return fd;
            }
        return -1;
    }

    bool claim_lease(uint64_t window) noexcept
    {
        for (size_t i = 0; i < lease_count; i++)
            if (leases[i] == window)
            {
                leases[i] = leases[--lease_count];
                return true;
            }
        return false;
    }

    /** Close every held handle and return every held lease with `enable` (once each). */
    void dispose(void (*enable)(void *, uint64_t), void *context) noexcept
    {
        for (size_t i = 0; i < fd_count; i++) ::close(fds[i]);
        fd_count = 0;
        auto count = lease_count;
        lease_count = 0;  // each lease is returned once, even if `enable` re-enters
        for (size_t i = 0; i < count; i++)
        {
            ++returned_leases;
            if (enable) enable(context, leases[i]);
        }
    }

    size_t size() const noexcept { return fd_count + lease_count; }
    size_t returned_leases = 0;

  private:
    std::array<int, max_transfer> fds{};
    size_t fd_count = 0;
    std::array<uint64_t, max_transfer> leases{};
    size_t lease_count = 0;
};

/**
 * First thing in init(): own what the environment list names, then remove it. Nothing here
 * allocates or throws, and the list is removed only once its contents are owned (or known not to
 * be ours: malformed, or stale against the model version). Returns false if nothing was taken.
 */
inline bool acquire(pending_t& pending, list_t& list) noexcept
{
    const char *found = getenv(environment);
    if (!found) return false;
    bool parsed = parse_list(found, list);
    const char *version = getenv("SCOTTLAND_INTERNAL_MODEL_VERSION");
    bool current = parsed && version && strtoull(version, nullptr, 10) == list.version;
    if (current)
    {
        for (size_t i = 0; i < list.fd_count; i++) pending.own_fd(list.fds[i]);
        for (size_t i = 0; i < list.lease_count; i++) pending.own_lease(list.leases[i]);
    }
    unsetenv(environment);
    return current;
}

/** Numbers already named by an entry of one handover file. */
struct seen_t
{
    std::array<int, max_transfer> fds{};
    size_t count = 0;
};

/** Both formats: a number names a handle only if it is an open pidfd that no other entry named,
 *  and a live process behind it must be the recorded one (a recorded 0 never authorizes a live
 *  handle). A reaped launcher (Pid: -1) is owned-dead. No allocation, no exception. */
inline bool valid_descriptor(int fd, int64_t pid, seen_t& seen) noexcept
{
    if (fd < 0 || seen.count == seen.fds.size()) return false;
    for (size_t i = 0; i < seen.count; i++) if (seen.fds[i] == fd) return false;
    seen.fds[seen.count++] = fd;
    if (!is_pidfd(fd)) return false;
    auto live = pidfd_pid(fd);
    return live && (*live == -1 || (*live > 0 && pid > 0 && *live == pid));
}

/** Duplicated handles the outgoing copy has made and not yet published: closed unless released. */
struct duplicates_t
{
    std::array<int, max_transfer> fds{};
    size_t count = 0;
    duplicates_t() = default;
    duplicates_t(const duplicates_t&) = delete;
    duplicates_t& operator =(const duplicates_t&) = delete;
    ~duplicates_t() { close_all(); }
    /** Duplicate `fd` straight into this owner; -1 if the owner is full or dup failed. */
    int dup(int fd) noexcept
    {
        if (count == max_transfer) return -1;
        int copy = fcntl(fd, F_DUPFD_CLOEXEC, 0);
        if (copy >= 0) fds[count++] = copy;
        return copy;
    }
    void close_all() noexcept
    {
        for (size_t i = 0; i < count; i++) ::close(fds[i]);
        count = 0;
    }
    /** Published: the next copy owns them now. */
    void release() noexcept { count = 0; }
};

}
