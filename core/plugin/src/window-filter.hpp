#pragma once

#include <algorithm>
#include <cctype>
#include <string>
#include <string_view>

namespace scottland::windowing
{
// Window mode owns this query only for its lifetime. Editing previews the
// matching set; committing keeps that subset active until the mode exits.
class window_filter
{
  public:
    bool editing = false;
    bool latched = false;
    std::string query;

    void open()
    {
        editing = true;
        latched = true; // the text box remains open after the initiating Alt release
        query.clear();
    }

    void append(std::string_view text) { query.append(text); }

    void backspace()
    {
        if (query.empty()) return;
        size_t start = query.size() - 1;
        while (start && (static_cast<unsigned char>(query[start]) & 0xc0) == 0x80) --start;
        query.erase(start);
    }

    void commit() { editing = false; }

    void cancel()
    {
        editing = false;
        query.clear();
    }

    void exit()
    {
        cancel();
        latched = false;
    }

    bool matches(std::string_view title, std::string_view app_id,
        std::string_view widget_name = {}) const
    {
        return query.empty() || contains(title) || contains(app_id) || contains(widget_name);
    }

  private:
    static unsigned char lower(unsigned char c)
    {
        return static_cast<unsigned char>(std::tolower(c));
    }

    bool contains(std::string_view text) const
    {
        if (query.size() > text.size()) return false;
        return std::search(text.begin(), text.end(), query.begin(), query.end(),
            [] (char a, char b) {
                return lower(static_cast<unsigned char>(a)) == lower(static_cast<unsigned char>(b));
            }) != text.end();
    }
};
}
