#include "paste-shortcut.hpp"

#include <array>
#include <cstdint>
#include <iostream>

namespace {
using scottland::clipboard::PasteKeyEvent;
using scottland::clipboard::PasteKeySequence;
using scottland::clipboard::regular_clipboard_paste_sequence;
using scottland::clipboard::shift_insert_paste_sequence;

int check_sequence(const PasteKeySequence &actual, const PasteKeyEvent *expected,
                  std::size_t expected_size, const char *message) {
    if (actual.size != expected_size) {
        std::cerr << "FAIL " << message << ": expected " << expected_size << " events, got "
                  << actual.size << '\n';
        return 1;
    }
    for (std::size_t i = 0; i < expected_size; ++i) {
        const auto &event = actual.events[i];
        if (event.key != expected[i].key || event.pressed != expected[i].pressed ||
            event.modifiers_after != expected[i].modifiers_after) {
            std::cerr << "FAIL " << message << ": event " << i << " differs\n";
            return 1;
        }
    }
    return 0;
}
} // namespace

int main() {
    constexpr uint32_t base_modifiers = 1u << 4;
    constexpr uint32_t control_mask = 1u << 2;
    constexpr uint32_t shift_mask = 1u << 0;

    constexpr std::array<PasteKeyEvent, 6> no_target_modifiers{{
        {KEY_LEFTCTRL, true, base_modifiers | control_mask},
        {KEY_LEFTSHIFT, true, base_modifiers | control_mask | shift_mask},
        {KEY_V, true, base_modifiers | control_mask | shift_mask},
        {KEY_V, false, base_modifiers | control_mask | shift_mask},
        {KEY_LEFTSHIFT, false, base_modifiers | control_mask},
        {KEY_LEFTCTRL, false, base_modifiers},
    }};
    if (check_sequence(regular_clipboard_paste_sequence(base_modifiers, control_mask, shift_mask),
                       no_target_modifiers.data(), no_target_modifiers.size(),
                       "clipboard paste sends Ctrl+Shift+V and restores prior modifiers")) {
        return 1;
    }

    constexpr std::array<PasteKeyEvent, 4> control_already_held{{
        {KEY_LEFTSHIFT, true, base_modifiers | control_mask | shift_mask},
        {KEY_V, true, base_modifiers | control_mask | shift_mask},
        {KEY_V, false, base_modifiers | control_mask | shift_mask},
        {KEY_LEFTSHIFT, false, base_modifiers | control_mask},
    }};
    if (check_sequence(regular_clipboard_paste_sequence(base_modifiers | control_mask,
                                                        control_mask, shift_mask),
                       control_already_held.data(), control_already_held.size(),
                       "clipboard paste preserves an already-held Control modifier")) {
        return 1;
    }

    constexpr std::array<PasteKeyEvent, 4> shift_already_held{{
        {KEY_LEFTCTRL, true, base_modifiers | control_mask | shift_mask},
        {KEY_V, true, base_modifiers | control_mask | shift_mask},
        {KEY_V, false, base_modifiers | control_mask | shift_mask},
        {KEY_LEFTCTRL, false, base_modifiers | shift_mask},
    }};
    if (check_sequence(regular_clipboard_paste_sequence(base_modifiers | shift_mask,
                                                        control_mask, shift_mask),
                       shift_already_held.data(), shift_already_held.size(),
                       "clipboard paste preserves an already-held Shift modifier")) {
        return 1;
    }

    constexpr std::array<PasteKeyEvent, 2> both_already_held{{
        {KEY_V, true, base_modifiers | control_mask | shift_mask},
        {KEY_V, false, base_modifiers | control_mask | shift_mask},
    }};
    if (check_sequence(regular_clipboard_paste_sequence(base_modifiers | control_mask | shift_mask,
                                                        control_mask, shift_mask),
                       both_already_held.data(), both_already_held.size(),
                       "clipboard paste preserves already-held Control and Shift")) {
        return 1;
    }

    constexpr std::array<PasteKeyEvent, 4> shift_insert{ {
        {KEY_LEFTSHIFT, true, base_modifiers | shift_mask},
        {KEY_INSERT, true, base_modifiers | shift_mask},
        {KEY_INSERT, false, base_modifiers | shift_mask},
        {KEY_LEFTSHIFT, false, base_modifiers},
    } };
    if (check_sequence(shift_insert_paste_sequence(base_modifiers, shift_mask),
                       shift_insert.data(), shift_insert.size(),
                       "Shift+Insert adds and restores Shift")) {
        return 1;
    }

    constexpr std::array<PasteKeyEvent, 2> shift_already_held_for_insert{ {
        {KEY_INSERT, true, base_modifiers | shift_mask},
        {KEY_INSERT, false, base_modifiers | shift_mask},
    } };
    if (check_sequence(shift_insert_paste_sequence(base_modifiers | shift_mask, shift_mask),
                       shift_already_held_for_insert.data(), shift_already_held_for_insert.size(),
                       "Shift+Insert preserves an already-held Shift modifier")) {
        return 1;
    }

    std::cout << "PASS clipboard paste key sequences and modifier restoration\n";
    return 0;
}
