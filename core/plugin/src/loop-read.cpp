// scottland-loop-read: print a Scottland session's main-loop diagnostic ring (docs/main-loop.md).
//
//   scottland-loop-read [--display wayland-N | --file PATH] [--follow] [--json]
//
// The only external reader of the ring: it loads every word atomically (seq_cst), as the ABI
// requires, and never writes to it, so a slow or stopped reader cannot affect the compositor.
// Scope and note names come from the names file of the instance that wrote a record; records of
// an earlier plugin copy are shown with their numeric ids.
#include "loop-abi.hpp"
#include <algorithm>
#include <cinttypes>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <map>
#include <sstream>
#include <string>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include <vector>

namespace abi = scottland::loop::abi;

namespace
{
struct names_t
{
    uint64_t instance = 0;
    std::map<uint32_t, std::string> scopes;
    std::map<uint32_t, std::pair<std::string, std::string>> notes;
};

uint64_t now_ns()
{
    timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000000000ull + (uint64_t)t.tv_nsec;
}

// Names are used only if the header's instance and build id are the same before and after
// reading the file and equal its first line.
names_t load_names(const void *base, const std::string& path)
{
    names_t names;
    auto before = abi::word(base, abi::w_instance).load();
    auto build = abi::word(base, abi::w_build).load();
    std::ifstream in(path);
    std::string line;
    if (!in || !std::getline(in, line)) return names;
    uint64_t instance = 0, file_build = 0;
    if (sscanf(line.c_str(), "%" SCNu64 " %" SCNx64, &instance, &file_build) != 2) return names;
    names_t loaded;
    loaded.instance = instance;
    while (std::getline(in, line))
    {
        std::istringstream fields(line);
        std::string kind, name;
        uint32_t id;
        if (!(fields >> kind >> id >> name)) continue;
        if (kind == "scope") loaded.scopes[id] = name;
        else if (kind == "note")
        {
            std::string text;
            std::getline(fields, text);
            loaded.notes[id] = {name, text.empty() ? text : text.substr(1)};
        }
    }
    auto after = abi::word(base, abi::w_instance).load();
    if (before != after || before != instance || build != file_build) return names;
    return loaded;
}

uint64_t field(uint64_t value, int lo, int bits)
{
    if (bits >= 64) return value >> lo;
    return (value >> lo) & ((1ull << bits) - 1);
}

std::string format_note(const std::string& text, const uint64_t args[4])
{
    std::string out;
    for (size_t i = 0; i < text.size(); i++)
    {
        if (text[i] != '{')
        {
            out += text[i];
            continue;
        }
        auto end = text.find('}', i);
        if (end == std::string::npos) { out += text.substr(i); break; }
        auto spec = text.substr(i + 1, end - i - 1);
        i = end;
        std::vector<std::string> parts;
        std::stringstream split(spec);
        for (std::string part; std::getline(split, part, ':');) parts.push_back(part);
        int index = parts.empty() ? 0 : atoi(parts[0].c_str());
        if (index < 1 || index > 4) { out += "{" + spec + "}"; continue; }
        auto value = args[index - 1];
        char buffer[64];
        if (parts.size() == 1) snprintf(buffer, sizeof(buffer), "%" PRIu64, value);
        else if (parts[1] == "x") snprintf(buffer, sizeof(buffer), "0x%" PRIx64, value);
        else if (parts[1] == "s") snprintf(buffer, sizeof(buffer), "%" PRId64, (int64_t)value);
        else if (parts[1] == "p") snprintf(buffer, sizeof(buffer), "%d,%d", (int32_t)(uint32_t)value, (int32_t)(uint32_t)(value >> 32));
        else if (parts.size() >= 3)
        {
            auto bits = field(value, atoi(parts[1].c_str()), atoi(parts[2].c_str()));
            if (parts.size() >= 4)
            {
                std::vector<std::string> labels;
                std::stringstream names(parts[3]);
                for (std::string label; std::getline(names, label, '|');) labels.push_back(label);
                snprintf(buffer, sizeof(buffer), "%s", bits < labels.size() ? labels[bits].c_str() : std::to_string(bits).c_str());
            } else snprintf(buffer, sizeof(buffer), "%" PRIu64, bits);
        } else snprintf(buffer, sizeof(buffer), "%" PRIu64, value);
        out += buffer;
    }
    return out;
}

const char *kind_name(uint32_t kind)
{
    switch (kind)
    {
      case abi::k_slow: return "slow";
      case abi::k_stuck: return "stuck";
      case abi::k_unresponsive: return "unresponsive";
      case abi::k_note: return "note";
      case abi::k_loaded: return "loaded";
      case abi::k_unavailable: return "unavailable";
      default: return "unknown";
    }
}

std::string json_string(const std::string& text)
{
    std::string out = "\"";
    for (char c : text)
    {
        if (c == '"' || c == '\\') { out += '\\'; out += c; }
        else if ((unsigned char)c < 0x20) { char b[8]; snprintf(b, sizeof(b), "\\u%04x", c); out += b; }
        else out += c;
    }
    return out + "\"";
}

struct reader_t
{
    const void *base;
    std::string names_path;
    bool json = false;
    names_t names;
    uint64_t next_a = 0, next_b = 0, lost = 0;

    std::string scope_name(uint64_t instance, uint32_t id)
    {
        if (!id) return "none";
        auto found = names.scopes.find(id);
        if (instance == names.instance && found != names.scopes.end()) return found->second;
        return "#" + std::to_string(id) + (instance == names.instance ? "" : " (previous build)");
    }

    void print(char ring, const abi::record_t& r)
    {
        std::string scope, child, text;
        if (r.kind == abi::k_note)
        {
            uint64_t args[4] = {r.child, r.duration_ns, r.value, r.extra};
            auto found = names.notes.find(r.id);
            if (r.instance == names.instance && found != names.notes.end())
            {
                scope = found->second.first;
                text = format_note(found->second.second, args);
            } else
            {
                scope = "#" + std::to_string(r.id) + " (previous build)";
                text = std::to_string(args[0]) + " " + std::to_string(args[1]) + " " +
                    std::to_string(args[2]) + " " + std::to_string(args[3]);
            }
        } else
        {
            scope = scope_name(r.instance, r.id);
            if (r.kind == abi::k_slow && r.child) child = scope_name(r.instance, (uint32_t)r.child);
        }
        double duration = r.kind == abi::k_stuck || r.kind == abi::k_unresponsive ? r.value / 1e6 :
            r.kind == abi::k_slow ? r.duration_ns / 1e6 : 0;
        if (json)
        {
            printf("{\"ring\":\"%c\",\"n\":%" PRIu64 ",\"instance\":%" PRIu64 ",\"kind\":\"%s\",\"scope\":%s,"
                "\"time_ns\":%" PRIu64 ",\"ms\":%.3f", ring, r.n, r.instance, kind_name(r.kind),
                json_string(scope).c_str(), r.time_ns, duration);
            if (!child.empty()) printf(",\"child\":%s,\"child_ms\":%.3f", json_string(child).c_str(), r.value / 1e6);
            if (!text.empty()) printf(",\"text\":%s", json_string(text).c_str());
            if (r.kind == abi::k_loaded) printf(",\"build\":\"%" PRIx64 "\"", r.value);
            printf("}\n");
        } else
        {
            printf("%12.3f  %-12s %-28s", r.time_ns / 1e9, kind_name(r.kind), scope.c_str());
            if (r.kind == abi::k_slow) printf(" %.1f ms", duration);
            if (!child.empty()) printf(" (slowest inside: %s %.1f ms)", child.c_str(), r.value / 1e6);
            if (r.kind == abi::k_stuck) printf(" running for %.0f ms", duration);
            if (r.kind == abi::k_unresponsive) printf(" no answer for %.0f ms, no Scottland scope active (last: %s)", duration, scope.c_str());
            if (r.kind == abi::k_loaded) printf(" instance %" PRIu64 " build %" PRIx64, r.instance, r.value);
            if (!text.empty()) printf(" %s", text.c_str());
            printf("\n");
        }
    }

    void drain()
    {
        names = load_names(base, names_path);
        std::vector<std::pair<char, abi::record_t>> records;
        for (auto [ring, head_word, next] : {std::tuple{'A', abi::w_head_a, &next_a}, std::tuple{'B', abi::w_head_b, &next_b}})
        {
            auto head = abi::word(base, head_word).load();
            size_t ring_base = ring == 'A' ? abi::ring_a : abi::ring_b;
            if (head > *next + abi::slots)
            {
                lost += head - abi::slots - *next;
                *next = head - abi::slots;
            }
            for (auto n = *next; n < head; n++)
            {
                abi::record_t r;
                if (abi::read_record(base, ring_base, n, r)) records.push_back({ring, r});
                else lost++;
            }
            *next = head;
        }
        std::stable_sort(records.begin(), records.end(),
            [] (auto& a, auto& b) { return a.second.time_ns < b.second.time_ns; });
        for (auto& [ring, r] : records) print(ring, r);
        fflush(stdout);
    }

    /** The running scope and its start, read as one sample (the watchdog's protocol): false if
     *  no coherent sample could be read (the producer is between its two stores, or paused there). */
    bool current_sample(uint64_t& current, uint64_t& start)
    {
        for (int attempt = 0; attempt < 64; attempt++)
        {
            auto seq = abi::word(base, abi::w_sample_seq).load();
            current = abi::word(base, abi::w_current_scope).load();
            start = abi::word(base, abi::w_current_start_ns).load();
            if (!(seq & 1) && abi::word(base, abi::w_sample_seq).load() == seq) return true;
            if (attempt > 8) usleep(100);
        }
        return false;
    }

    void header()
    {
        uint64_t current = 0, start = 0;
        bool coherent = current_sample(current, start);
        auto instance = abi::word(base, abi::w_instance).load();
        names = load_names(base, names_path);
        auto now = now_ns();
        if (json)
        {
            char age[32] = "null";
            if (coherent) snprintf(age, sizeof(age), "%.3f", current ? (now - start) / 1e6 : 0.0);
            printf("{\"header\":{\"pid\":%" PRIu64 ",\"instance\":%" PRIu64 ",\"build\":\"%" PRIx64 "\","
                "\"mlock_failed\":%s,\"current\":%s,\"current_ms\":%s,\"names\":%s}}\n",
                abi::word(base, abi::w_pid).load(), instance, abi::word(base, abi::w_build).load(),
                abi::word(base, abi::w_flags).load() & abi::flag_mlock_failed ? "true" : "false",
                coherent ? json_string(scope_name(instance, (uint32_t)current)).c_str() : "null",
                age, names.instance == instance ? "true" : "false");
        } else
        {
            printf("compositor %" PRIu64 ", plugin instance %" PRIu64 " (build %" PRIx64 ")%s%s\n",
                abi::word(base, abi::w_pid).load(), instance, abi::word(base, abi::w_build).load(),
                abi::word(base, abi::w_flags).load() & abi::flag_mlock_failed ? ", ring not locked in memory" : "",
                names.instance == instance ? "" : ", no names for this instance");
            if (!coherent) printf("now running: unavailable (the compositor was changing it while read)\n");
            else if (current) printf("now running: %s for %.1f ms\n", scope_name(instance, (uint32_t)current).c_str(), (now - start) / 1e6);
        }
    }
};
}

int main(int argc, char **argv)
{
    std::string display = getenv("WAYLAND_DISPLAY") ? getenv("WAYLAND_DISPLAY") : "wayland";
    std::string file;
    bool follow = false, json = false;
    for (int i = 1; i < argc; i++)
    {
        std::string arg = argv[i];
        if (arg == "--display" && i + 1 < argc) display = argv[++i];
        else if (arg == "--file" && i + 1 < argc) file = argv[++i];
        else if (arg == "--follow") follow = true;
        else if (arg == "--json") json = true;
        else
        {
            fprintf(stderr, "usage: scottland-loop-read [--display wayland-N | --file PATH] [--follow] [--json]\n");
            return 2;
        }
    }
    if constexpr (!abi::supported)
    {
        fprintf(stderr, "scottland-loop-read: unsupported platform\n");
        return 1;
    }
    if (file.empty())
    {
        const char *runtime = getenv("XDG_RUNTIME_DIR");
        file = std::string(runtime ? runtime : "/tmp") + "/scottland/" + display + ".loop";
    }
    int fd = open(file.c_str(), O_RDONLY | O_CLOEXEC);
    struct stat info{};
    if (fd < 0 || fstat(fd, &info) != 0 || info.st_size != (off_t)abi::file_size)
    {
        fprintf(stderr, "scottland-loop-read: no diagnostic ring at %s\n", file.c_str());
        return 1;
    }
    void *base = mmap(nullptr, abi::file_size, PROT_READ, MAP_SHARED, fd, 0);
    close(fd);
    if (base == MAP_FAILED || abi::word(base, abi::w_magic).load() != abi::magic ||
        abi::word(base, abi::w_abi).load() != abi::version)
    {
        fprintf(stderr, "scottland-loop-read: %s is not a version %" PRIu64 " ring\n", file.c_str(), abi::version);
        return 1;
    }
    reader_t reader{base, file + ".names", json, {}};
    reader.header();
    reader.drain();
    while (follow)
    {
        usleep(100000);
        // The file is replaced when a new compositor process starts on this display.
        struct stat now{};
        if (stat(file.c_str(), &now) != 0 || now.st_ino != info.st_ino) break;
        reader.drain();
    }
    if (reader.lost && !json) fprintf(stderr, "%" PRIu64 " records overwritten or being written while read\n", reader.lost);
    if (json) printf("{\"lost\":%" PRIu64 "}\n", reader.lost);
    return 0;
}
