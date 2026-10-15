#include "../core/plugin/src/window-filter.hpp"
#include <cassert>

int main()
{
    scottland::windowing::window_filter filter;
    assert(filter.matches("Terminal", "foot"));
    filter.open();
    filter.append("fox");
    assert(filter.editing && filter.latched);
    assert(filter.matches("Firefox — docs", "org.mozilla.firefox"));
    assert(!filter.matches("Terminal", "foot"));
    assert(filter.matches("", "", "Fox weather")); // widgets take the same query
    filter.commit();
    assert(!filter.editing && filter.matches("Firefox", "firefox"));
    assert(!filter.matches("Terminal", "foot"));
    filter.open();
    filter.append("weather");
    filter.cancel();
    assert(!filter.editing && filter.query.empty());
    filter.open();
    filter.append("\xc3\xa9");
    filter.backspace(); // remove a UTF-8 codepoint, not one byte
    assert(filter.query.empty());
    filter.append("keep");
    filter.exit();
    assert(!filter.latched && !filter.editing && filter.query.empty());
}
