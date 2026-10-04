#pragma once
#include "window-memory.hpp"
#include <array>
#include <functional>
#include <string>
#include <vector>
namespace scottland::windowing
{
struct hint_entry { uint64_t id; unsigned slot; zone location; bool widget; };
enum class destination { center, periphery, widget };
// The other two zones, toward the center first, then the original zone.
std::array<destination, 3> cycle_order(destination start);
class alt_mode
{
  public:
    bool active = false;
    uint64_t selected = 0;
    std::vector<hint_entry> entries;
    std::function<void(uint64_t, bool)> select; // bool: restore a widget as a card click
    std::function<void(uint64_t, destination)> move;
    std::function<void(uint64_t)> close;
    std::function<void(uint64_t)> hint_select; // an unselected widget was selected by its hint
    std::function<bool(uint64_t)> hint_peek_active;
    std::function<void(uint64_t)> hint_action; // a complete hint acted, never Tab or a prefix
    void begin(std::vector<hint_entry> windows, uint64_t focused);
    void end();
    void refresh(std::vector<hint_entry> windows);
    void letter(char key, uint32_t time_ms);
    void release(char key, uint32_t time_ms);
    unsigned double_tap_delay = 300;
    void tab(bool backwards);
    void close_selected();
    std::string label(unsigned slot) const;
    unsigned hint_width = 1;
  private:
    std::string prefix;
    uint64_t cycling = 0;
    std::array<destination, 3> order;
    unsigned step = 0;
    uint64_t last_hint = 0;
    uint32_t last_release = 0;
    char last_key = 0;
    bool awaiting_release = false;
    bool repeat_candidate = false;
    void activate(uint64_t id, bool double_tap);
};
}
