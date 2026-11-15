#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <linux/input-event-codes.h>
#include <string_view>

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

inline constexpr PasteKeySequence control_v_paste_sequence(
    uint32_t depressed_modifiers, uint32_t control_mask) {
    PasteKeySequence sequence;
    uint32_t modifiers = depressed_modifiers;
    const bool add_control = (modifiers & control_mask) == 0;

    if (add_control) {
        modifiers |= control_mask;
        sequence.events[sequence.size++] = {KEY_LEFTCTRL, true, modifiers};
    }

    sequence.events[sequence.size++] = {KEY_V, true, modifiers};
    sequence.events[sequence.size++] = {KEY_V, false, modifiers};

    if (add_control) {
        modifiers &= ~control_mask;
        sequence.events[sequence.size++] = {KEY_LEFTCTRL, false, modifiers};
    }
    return sequence;
}

inline constexpr PasteKeySequence shift_insert_paste_sequence(
    uint32_t depressed_modifiers, uint32_t shift_mask) {
    PasteKeySequence sequence;
    uint32_t modifiers = depressed_modifiers;
    const bool add_shift = (modifiers & shift_mask) == 0;

    if (add_shift) {
        modifiers |= shift_mask;
        sequence.events[sequence.size++] = {KEY_LEFTSHIFT, true, modifiers};
    }

    sequence.events[sequence.size++] = {KEY_INSERT, true, modifiers};
    sequence.events[sequence.size++] = {KEY_INSERT, false, modifiers};

    if (add_shift) {
        modifiers &= ~shift_mask;
        sequence.events[sequence.size++] = {KEY_LEFTSHIFT, false, modifiers};
    }
    return sequence;
}

enum class PasteBinding {
    control_v,
    control_shift_v,
    shift_insert,
};

inline constexpr bool ascii_equal_case_insensitive(std::string_view left,
                                                    std::string_view right) {
    if (left.size() != right.size()) return false;
    for (std::size_t i = 0; i < left.size(); ++i) {
        char a = left[i];
        char b = right[i];
        if (a >= 'A' && a <= 'Z') a = static_cast<char>(a - 'A' + 'a');
        if (b >= 'A' && b <= 'Z') b = static_cast<char>(b - 'A' + 'a');
        if (a != b) return false;
    }
    return true;
}

inline constexpr bool app_id_is(std::string_view app_id, std::string_view candidate) {
    if (ascii_equal_case_insensitive(app_id, candidate)) return true;
    const auto separator = app_id.find_last_of('.');
    return separator != std::string_view::npos &&
        ascii_equal_case_insensitive(app_id.substr(separator + 1), candidate);
}

inline constexpr PasteBinding paste_binding_for_app(std::string_view app_id) {
    constexpr std::array<std::string_view, 17> control_shift_v_terminals{{
        "foot", "footclient", "kitty", "alacritty", "wezterm", "wezterm-gui",
        "ghostty", "org.wezfurlong.wezterm", "com.mitchellh.ghostty",
        "org.gnome.terminal", "org.gnome.console", "gnome-terminal", "kgx",
        "konsole", "org.kde.konsole", "tilix", "com.gexperts.tilix",
    }};
    constexpr std::array<std::string_view, 7> shift_insert_terminals{{
        "xterm", "rxvt", "rxvt-unicode", "urxvt", "xfce4-terminal", "mate-terminal",
        "lxterminal",
    }};

    for (const auto terminal : control_shift_v_terminals) {
        if (app_id_is(app_id, terminal)) return PasteBinding::control_shift_v;
    }
    for (const auto terminal : shift_insert_terminals) {
        if (app_id_is(app_id, terminal)) return PasteBinding::shift_insert;
    }
    return PasteBinding::control_v;
}

inline constexpr PasteKeySequence fallback_paste_sequence(
    std::string_view app_id, uint32_t depressed_modifiers,
    uint32_t control_mask, uint32_t shift_mask) {
    switch (paste_binding_for_app(app_id)) {
        case PasteBinding::control_shift_v:
            return regular_clipboard_paste_sequence(
                depressed_modifiers, control_mask, shift_mask);
        case PasteBinding::shift_insert:
            return shift_insert_paste_sequence(depressed_modifiers, shift_mask);
        case PasteBinding::control_v:
            return control_v_paste_sequence(depressed_modifiers, control_mask);
    }
    return control_v_paste_sequence(depressed_modifiers, control_mask);
}

} // namespace scottland::clipboard
