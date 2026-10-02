#include "alt-mode.hpp"
#include <algorithm>
namespace scottland::windowing
{
static const std::string keys = "asdfghjklqwertyuiopzxcvbnm";
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
}
void alt_mode::begin(std::vector<hint_entry> windows, uint64_t focused)
{
    active = true; selected = focused; cycling = 0; prefix.clear(); refresh(std::move(windows));
}
void alt_mode::end() { active = false; cycling = 0; prefix.clear(); }
void alt_mode::activate(uint64_t id)
{
    auto it = std::find_if(entries.begin(), entries.end(), [&] (auto e) { return e.id == id; });
    if (it == entries.end()) return;
    selected = id;
    if (cycling != id)
    {
        cycling = id;
        bool periphery = it->location == zone::left_periphery || it->location == zone::right_periphery;
        next = periphery && !it->widget ? destination::center : destination::periphery;
        after_center = periphery && !it->widget ? destination::widget : destination::periphery;
        select(id, it->widget);
        return;
    }
    auto to = next;
    if (to == destination::center) next = after_center;
    else if (to == destination::periphery) next = destination::widget;
    else { next = destination::center; after_center = destination::periphery; }
    move(id, to);
}
void alt_mode::letter(char key)
{
    if (!active || keys.find(key) == std::string::npos) return;
    prefix += key;
    bool partial = false;
    for (auto e : entries)
    {
        auto hint = label(e.slot);
        if (hint == prefix) { prefix.clear(); activate(e.id); return; }
        partial |= hint.compare(0, prefix.size(), prefix) == 0;
    }
    if (!partial) prefix.clear();
}
void alt_mode::tab(bool backwards)
{
    if (entries.empty()) return;
    auto it = std::find_if(entries.begin(), entries.end(), [&] (auto e) { return e.id == selected; });
    int at = it == entries.end() ? (backwards ? 0 : -1) : int(it - entries.begin());
    at = (at + (backwards ? -1 : 1) + int(entries.size())) % int(entries.size());
    selected = entries[at].id; cycling = 0; prefix.clear(); select(selected, false);
}
void alt_mode::close_selected() { if (selected) { close(selected); cycling = 0; prefix.clear(); } }
}
