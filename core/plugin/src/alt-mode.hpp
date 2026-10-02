#pragma once
#include "window-memory.hpp"
#include <functional>
#include <string>
#include <vector>
namespace scottland::windowing
{
struct hint_entry { uint64_t id; unsigned slot; zone location; bool widget; };
enum class destination { center, periphery, widget };
class alt_mode
{
  public:
    bool active = false;
    uint64_t selected = 0;
    std::vector<hint_entry> entries;
    std::function<void(uint64_t, bool)> select; // bool: restore a widget as a card click
    std::function<void(uint64_t, destination)> move;
    std::function<void(uint64_t)> close;
    void begin(std::vector<hint_entry> windows, uint64_t focused);
    void end();
    void refresh(std::vector<hint_entry> windows);
    void letter(char key);
    void tab(bool backwards);
    void close_selected();
    std::string label(unsigned slot) const;
    unsigned hint_width = 1;
  private:
    std::string prefix;
    uint64_t cycling = 0;
    destination next = destination::periphery, after_center = destination::periphery;
    void activate(uint64_t id);
};
}
