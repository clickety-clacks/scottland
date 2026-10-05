// Widget handover across scottland-reload (docs/main-loop.md, "Reload"; design 3.8).
//
// Two kinds of resource cross a reload: duplicated launcher pidfds, and leases (Scottland's one
// disable on an application's root node, held per window id). A number or lease id is acted on
// only when its ownership is established outside the handover file: by the environment list the
// outgoing copy of this process published, or for the legacy format by the reload receipt.
// Standard library and POSIX only; the plugin supplies the scene operations.
#pragma once
#include <algorithm>
#include <cerrno>
#include <cstdint>
#include <cstdlib>
#include <fcntl.h>
#include <fstream>
#include <functional>
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

/** Is fd an open pidfd? */
inline bool is_pidfd(int fd)
{
    if (fd < 0 || fcntl(fd, F_GETFD) < 0) return false;
    char target[64] = {};
    auto link = "/proc/self/fd/" + std::to_string(fd);
    auto size = readlink(link.c_str(), target, sizeof(target) - 1);
    return size > 0 && std::string(target, size) == "anon_inode:[pidfd]";
}

/** The Pid: line of a pidfd's fdinfo: the live pid, -1 once the process is reaped, nullopt if unreadable. */
inline std::optional<long> pidfd_pid(int fd)
{
    std::ifstream in("/proc/self/fdinfo/" + std::to_string(fd));
    std::string line;
    while (std::getline(in, line))
        if (line.rfind("Pid:", 0) == 0) return std::strtol(line.c_str() + 4, nullptr, 10);
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

/**
 * The environment list: "<id>;version=<model version>;fds=<n,…>;leases=<window id,…>". The
 * version is the model version the outgoing copy also leaves in SCOTTLAND_INTERNAL_MODEL_VERSION:
 * an older plugin build loaded in between (a rollback) publishes and changes it without consuming
 * the list, and a list that no longer matches is stale and owns nothing.
 */
struct list_t
{
    std::string id;
    uint64_t version = 0;
    std::vector<int> fds;
    std::vector<uint64_t> leases;
};

inline std::string format_list(const list_t& list)
{
    std::ostringstream out;
    out << list.id << ";version=" << list.version << ";fds=";
    for (size_t i = 0; i < list.fds.size(); i++) out << (i ? "," : "") << list.fds[i];
    out << ";leases=";
    for (size_t i = 0; i < list.leases.size(); i++) out << (i ? "," : "") << list.leases[i];
    return out.str();
}

inline std::optional<list_t> parse_list(const std::string& text)
{
    list_t list;
    auto version = text.find(";version="), first = text.find(";fds="), second = text.find(";leases=");
    if (version == std::string::npos || first == std::string::npos || second == std::string::npos ||
        version == 0 || first < version || second < first) return std::nullopt;
    list.id = text.substr(0, version);
    auto number = text.substr(version + 9, first - version - 9);
    if (number.empty() || number.find_first_not_of("0123456789") != std::string::npos) return std::nullopt;
    list.version = std::strtoull(number.c_str(), nullptr, 10);
    auto numbers = [] (const std::string& part, auto& out) -> bool
    {
        std::stringstream in(part);
        for (std::string item; std::getline(in, item, ',');)
        {
            if (item.empty() || item.find_first_not_of("0123456789") != std::string::npos) return false;
            errno = 0;
            auto value = std::strtoull(item.c_str(), nullptr, 10);
            if (errno) return false;
            out.push_back((typename std::decay_t<decltype(out)>::value_type)value);
        }
        return true;
    };
    if (!numbers(text.substr(first + 5, second - first - 5), list.fds) ||
        !numbers(text.substr(second + 8), list.leases)) return std::nullopt;
    return list;
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
    ~pending_t() { dispose({}); }

    /** Room for everything a list names, before anything is owned: owning can't fail afterwards. */
    bool reserve(size_t fd_count, size_t lease_count) noexcept
    {
        try
        {
            fds.reserve(fds.size() + fd_count);
            leases.reserve(leases.size() + lease_count);
        } catch (...)
        {
            return false;
        }
        return true;
    }

    /** Take ownership of an open pidfd (refused if it isn't one or is already held). */
    bool own_fd(int fd) noexcept
    {
        if (!is_pidfd(fd) || holds_fd(fd)) return false;
        try { fds.push_back(fd); } catch (...) { return false; }
        return true;
    }

    bool own_lease(uint64_t window) noexcept
    {
        if (holds_lease(window)) return false;
        try { leases.push_back(window); } catch (...) { return false; }
        return true;
    }

    bool holds_fd(int fd) const noexcept { return std::find(fds.begin(), fds.end(), fd) != fds.end(); }
    bool holds_lease(uint64_t window) const noexcept
    {
        return std::find(leases.begin(), leases.end(), window) != leases.end();
    }

    /** An entry claims what this owner holds, once: the caller owns it afterwards. */
    int claim_fd(int fd) noexcept
    {
        auto found = std::find(fds.begin(), fds.end(), fd);
        if (found == fds.end()) return -1;
        fds.erase(found);
        return fd;
    }

    bool claim_lease(uint64_t window) noexcept
    {
        auto found = std::find(leases.begin(), leases.end(), window);
        if (found == leases.end()) return false;
        leases.erase(found);
        return true;
    }

    /** Close every held handle and return every held lease with `enable` (once each). */
    void dispose(const std::function<void(uint64_t)>& enable) noexcept
    {
        for (int fd : fds) ::close(fd);
        fds.clear();
        auto returned = std::move(leases);
        leases.clear();
        for (auto window : returned)
        {
            ++returned_leases;
            if (enable)
                try { enable(window); } catch (...) {}
        }
    }

    size_t size() const noexcept { return fds.size() + leases.size(); }
    size_t returned_leases = 0;

  private:
    std::vector<int> fds;
    std::vector<uint64_t> leases;
};
}
