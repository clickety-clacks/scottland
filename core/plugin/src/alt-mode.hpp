#pragma once
#include "window-memory.hpp"
#include <array>
#include <functional>
#include <optional>
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
    // Holding a complete hint past hold_delay (WK35/WK36). focused names the app window that has
    // focus now (0: none); it is read before the press acts, since a press selects and focuses.
    std::function<uint64_t()> focused;
    std::function<void(uint64_t held, uint64_t partner)> pair; // WK36: held an unfocused window's hint
    std::function<void(uint64_t)> solo; // WK35: held the focused window's hint
    void begin(std::vector<hint_entry> windows, uint64_t focused);
    void end();
    void refresh(std::vector<hint_entry> windows);
    void letter(char key, uint32_t time_ms);
    void release(char key, uint32_t time_ms);
    unsigned double_tap_delay = 300;
    unsigned hold_delay = 500;
    // A hint press is still held and may yet become a hold.
    bool hold_waiting() const { return bool(hold); }
    uint64_t hold_window() const { return hold ? hold->id : 0; }
    // Milliseconds until a pending hold is due at time_ms (0: due now or none pending).
    uint32_t hold_remaining(uint32_t time_ms) const;
    // Act on a hold whose delay has passed by time_ms; true if it acted.
    bool hold_due(uint32_t time_ms);
    // Another key (Tab, an arrow, F4, Alt release) ends a pending hold: it is not a hold, so a
    // focused window's press that waits for release acts now, before that key (WK35).
    void interrupt();
    // Esc: end a pending hold and drop a focused window's waiting press without acting.
    void cancel_pending() { hold.reset(); waiting_tap.reset(); }
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
    struct pending_hold { uint64_t id; char key; uint32_t pressed; uint64_t partner; };
    std::optional<pending_hold> hold;
    // WK35: a press on the focused window's hint acts on release, so a hold can solo it unmoved.
    struct pending_tap { uint64_t id; char key; };
    std::optional<pending_tap> waiting_tap;
    void act_waiting_tap();
    void activate(uint64_t id, bool double_tap);
};
}
