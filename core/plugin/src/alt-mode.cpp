#include "alt-mode.hpp"
#include <algorithm>
namespace scottland::windowing
{
static const std::string keys = "asdfghjklqwertyuiopzxcvbnm";
std::array<destination, 3> cycle_order(destination start)
{
    using D = destination;
    if (start == D::center) return {D::periphery, D::widget, D::center};
    if (start == D::periphery) return {D::center, D::widget, D::periphery};
    return {D::center, D::periphery, D::widget};
}
std::string alt_mode::label(unsigned slot) const
{
    // Prefix-free base-26 labels. Keep width until the desktop empties, so closing a
    // high slot never relabels survivors. Additional width also avoids an artificial window cap.
    std::string result(hint_width, keys.front());
    for (unsigned i = hint_width; i > 0; --i)
    { result[i - 1] = keys[slot % keys.size()]; slot /= keys.size(); }
    return result;
}
void alt_mode::refresh(std::vector<hint_entry> windows)
{
    entries = std::move(windows);
    if (entries.empty()) hint_width = 1;
    std::sort(entries.begin(), entries.end(), [] (auto a, auto b) { return a.slot < b.slot; });
    uint64_t capacity = 1;
    for (unsigned i = 0; i < hint_width; ++i) capacity *= keys.size();
    while (!entries.empty() && entries.back().slot >= capacity)
    { ++hint_width; capacity *= keys.size(); }
    if (std::none_of(entries.begin(), entries.end(), [&] (auto e) { return e.id == cycling; })) cycling = 0;
    if (std::none_of(entries.begin(), entries.end(), [&] (auto e) { return e.id == selected; })) selected = 0;
    if (std::none_of(entries.begin(), entries.end(), [&] (auto e) { return e.id == last_hint; })) last_hint = 0;
    if (hold && std::none_of(entries.begin(), entries.end(), [&] (auto e) { return e.id == hold->id; })) hold.reset();
}
void alt_mode::begin(std::vector<hint_entry> windows, uint64_t focused)
{
    active = true; selected = focused; cycling = 0; last_hint = 0; hold.reset();
    awaiting_release = repeat_candidate = false; prefix.clear(); refresh(std::move(windows));
}
void alt_mode::end() { active = false; cycling = 0; last_hint = 0; awaiting_release = repeat_candidate = false; hold.reset(); prefix.clear(); }
void alt_mode::activate(uint64_t id, bool double_tap)
{
    auto it = std::find_if(entries.begin(), entries.end(), [&] (auto e) { return e.id == id; });
    if (it == entries.end()) return;
    bool already_selected = selected == id;
    bool widget = it->widget;
    selected = id;
    if (cycling != id)
    {
        cycling = id;
        auto start = widget ? destination::widget : it->location == zone::center ?
            destination::center : destination::periphery;
        order = cycle_order(start); step = 0;
        if (!already_selected && !double_tap)
        {
            if (hint_action) hint_action(id);
            select(id, false);
            if (widget && hint_select) hint_select(id);
            return;
        }
    }
    if (double_tap)
    {
        if (widget && hint_peek_active && hint_peek_active(id))
        {
            if (hint_action) hint_action(id);
            auto to = order[step];
            step = (step + 1) % order.size();
            move(id, to);
        } else
        {
            step = (std::find(order.begin(), order.end(), destination::widget) - order.begin() + 1) % order.size();
            if (!widget)
            {
                if (hint_action) hint_action(id);
                move(id, destination::widget);
            }
        }
        return;
    }
    auto to = order[step];
    step = (step + 1) % order.size();
    if (hint_action) hint_action(id);
    move(id, to);
}
void alt_mode::letter(char key, uint32_t time_ms)
{
    if (!active || keys.find(key) == std::string::npos) return;
    hold.reset(); // a further letter press is another key, never part of a hold
    // Dwell on the previous key and typing the rest of a multi-letter repeat
    // do not consume the user's inter-hint gap. Latch at its first physical
    // press, but act only after the same complete hint has been entered.
    if (prefix.empty())
        repeat_candidate = last_hint && !awaiting_release &&
            uint32_t(time_ms - last_release) <= double_tap_delay;
    prefix += key;
    bool partial = false;
    for (auto e : entries)
    {
        auto hint = label(e.slot);
        if (hint == prefix)
        {
            prefix.clear();
            bool double_tap = repeat_candidate && last_hint == e.id;
            last_hint = e.id; last_key = key; awaiting_release = true;
            repeat_candidate = false;
            // The press acts at once (WK6); remember who had focus before it, for a hold.
            uint64_t partner = focused ? focused() : selected;
            activate(e.id, double_tap);
            // A double-tap already did this press's one action; it never becomes a hold.
            if (active && !double_tap) hold = pending_hold{e.id, key, time_ms, partner};
            return;
        }
        partial |= hint.compare(0, prefix.size(), prefix) == 0;
    }
    if (!partial) { prefix.clear(); last_hint = 0; }
}
void alt_mode::release(char key, uint32_t time_ms)
{
    if (hold && hold->key == key) hold.reset(); // a tap, not a hold
    if (active && last_hint && awaiting_release && key == last_key)
    {
        last_release = time_ms;
        awaiting_release = false;
    }
}
bool alt_mode::hold_due(uint32_t time_ms)
{
    if (!active || !hold || uint32_t(time_ms - hold->pressed) < hold_delay) return false;
    auto held = *hold; hold.reset();
    if (held.partner == held.id) { if (solo) solo(held.id); return true; } // WK35 hook
    if (!held.partner) return true; // nothing had focus: nothing to pair with
    // The hold was this press's gesture: the next press of the hint is never its double-tap,
    // and its next cycle starts from wherever the pair leaves the window.
    last_hint = 0; cycling = 0;
    if (hint_action) hint_action(held.id);
    if (pair) pair(held.id, held.partner);
    return true;
}
void alt_mode::tab(bool backwards)
{
    hold.reset();
    if (entries.empty()) return;
    auto it = std::find_if(entries.begin(), entries.end(), [&] (auto e) { return e.id == selected; });
    int at = it == entries.end() ? (backwards ? 0 : -1) : int(it - entries.begin());
    at = (at + (backwards ? -1 : 1) + int(entries.size())) % int(entries.size());
    selected = entries[at].id; cycling = 0; last_hint = 0; prefix.clear(); select(selected, false);
}
void alt_mode::close_selected() { hold.reset(); if (selected) { close(selected); cycling = 0; last_hint = 0; prefix.clear(); } }
}
