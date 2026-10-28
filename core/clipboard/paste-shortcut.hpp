#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <linux/input-event-codes.h>

namespace scottland::clipboard {

struct PasteKeyEvent {
    uint32_t key;
    bool pressed;
    uint32_t modifiers_after;
};

struct PasteKeySequence {
    std::array<PasteKeyEvent, 6> events{};
    std::size_t size = 0;
};

inline constexpr PasteKeySequence regular_clipboard_paste_sequence(
    uint32_t depressed_modifiers, uint32_t control_mask, uint32_t shift_mask) {
    PasteKeySequence sequence;
    uint32_t modifiers = depressed_modifiers;
    const bool add_control = (modifiers & control_mask) == 0;
    const bool add_shift = (modifiers & shift_mask) == 0;

    if (add_control) {
        modifiers |= control_mask;
        sequence.events[sequence.size++] = {KEY_LEFTCTRL, true, modifiers};
    }
    if (add_shift) {
        modifiers |= shift_mask;
        sequence.events[sequence.size++] = {KEY_LEFTSHIFT, true, modifiers};
    }

    sequence.events[sequence.size++] = {KEY_V, true, modifiers};
    sequence.events[sequence.size++] = {KEY_V, false, modifiers};

    if (add_shift) {
        modifiers &= ~shift_mask;
        sequence.events[sequence.size++] = {KEY_LEFTSHIFT, false, modifiers};
    }
    if (add_control) {
        modifiers &= ~control_mask;
        sequence.events[sequence.size++] = {KEY_LEFTCTRL, false, modifiers};
    }
    return sequence;
}

} // namespace scottland::clipboard
