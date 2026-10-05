// The diagnostic ring file's layout, shared by the plugin and scottland-loop-read (docs/main-loop.md).
// Standard library only. Every word is an aligned 64-bit little-endian value that each side
// stores and loads as a lock-free std::atomic<uint64_t> with seq_cst, in every process: no plain
// reads, because a compiler may fold the two sequence reads of a record into one.
#pragma once
#include <atomic>
#include <cstddef>
#include <cstdint>

namespace scottland::loop::abi
{
// Supported ABI: x86-64 and aarch64 Linux, where the atomic has the layout of uint64_t. Elsewhere
// the ring and the watchdog are compiled out and only in-memory counters remain.
constexpr bool supported = std::atomic<uint64_t>::is_always_lock_free &&
    sizeof(std::atomic<uint64_t>) == sizeof(uint64_t) && alignof(std::atomic<uint64_t>) == 8;

constexpr uint64_t magic = 0x706f6f6c746f6373ull;  // "scotloop" little-endian
constexpr uint64_t version = 1;

// Header words.
enum header_word : size_t
{
    w_magic, w_abi, w_pid, w_start_time, w_session, w_instance, w_build,
    w_flags,              // bit 0: mlock failed for the current instance
    // Shared sample: the main thread stores, the watchdog loads; sample_seq odd while it changes.
    w_sample_seq, w_current_scope, w_current_start_ns, w_last_scope, w_last_end_ns,
    w_last_work_end_ns, w_hb_acked, w_hb_ack_ns, w_current_work,
    w_hb_requested,       // the watchdog's
    w_head_a, w_head_b,   // records ever committed to ring A (main thread) and ring B (watchdog)
    header_words = 32,
};

constexpr uint64_t flag_mlock_failed = 1;
constexpr size_t slots = 512;
constexpr size_t slot_words = 8;
constexpr size_t ring_a = header_words;
constexpr size_t ring_b = ring_a + slots * slot_words;
constexpr size_t total_words = ring_b + slots * slot_words;
constexpr size_t file_size = total_words * sizeof(uint64_t);

// Slot words: seq, instance, kind<<32 | id, child, time_ns, duration_ns, value, extra.
// A note's four arguments are child, duration, value and extra.
enum slot_word : size_t { s_seq, s_instance, s_kind_id, s_child, s_time, s_duration, s_value, s_reserved };

// Record kinds.
enum kind_t : uint32_t
{
    k_slow = 1,          // an outermost callback ran over 8 ms; id = its scope, child = slowest inner
    k_stuck = 2,         // watchdog: a scope has been running for value ns (id = that scope)
    k_unresponsive = 3,  // watchdog: no answer for value ns and no Scottland scope active; id = last scope
    k_note = 4,          // a diagnostic that once went to the log; id = note, value and child = arguments
    k_loaded = 5,        // a plugin copy mapped the ring; value = its build id
    k_unavailable = 6,   // watchdog: no consistent sample after 3 tries
};

inline std::atomic<uint64_t>& word(void *base, size_t index)
{
    return static_cast<std::atomic<uint64_t>*>(base)[index];
}

inline const std::atomic<uint64_t>& word(const void *base, size_t index)
{
    return static_cast<const std::atomic<uint64_t>*>(base)[index];
}

inline size_t slot_index(size_t ring, uint64_t n) { return ring + (n % slots) * slot_words; }

struct record_t
{
    uint64_t n, instance, time_ns, duration_ns, value;
    uint32_t kind, id;
    uint64_t child, extra;
};

/** Reader rule: one attempt per slot; false if the slot was being written or already reused. */
inline bool read_record(const void *base, size_t ring, uint64_t n, record_t& out)
{
    auto at = slot_index(ring, n);
    auto expected = 2 * n + 2;
    if (word(base, at + s_seq).load() != expected) return false;
    out.n = n;
    out.instance = word(base, at + s_instance).load();
    auto kind_id = word(base, at + s_kind_id).load();
    out.child = word(base, at + s_child).load();
    out.time_ns = word(base, at + s_time).load();
    out.duration_ns = word(base, at + s_duration).load();
    out.value = word(base, at + s_value).load();
    out.extra = word(base, at + s_reserved).load();
    if (word(base, at + s_seq).load() != expected) return false;
    out.kind = (uint32_t)(kind_id >> 32);
    out.id = (uint32_t)kind_id;
    return true;
}

/** Single producer per ring. */
inline void write_record(void *base, size_t ring, size_t head_word, uint64_t instance, uint32_t kind,
    uint32_t id, uint64_t child, uint64_t time_ns, uint64_t duration_ns, uint64_t value, uint64_t extra = 0)
{
    auto n = word(base, head_word).load();
    auto at = slot_index(ring, n);
    word(base, at + s_seq).store(2 * n + 1);
    word(base, at + s_instance).store(instance);
    word(base, at + s_kind_id).store((uint64_t)kind << 32 | id);
    word(base, at + s_child).store(child);
    word(base, at + s_time).store(time_ns);
    word(base, at + s_duration).store(duration_ns);
    word(base, at + s_value).store(value);
    word(base, at + s_reserved).store(extra);
    word(base, at + s_seq).store(2 * n + 2);
    word(base, head_word).store(n + 1);
}
}
