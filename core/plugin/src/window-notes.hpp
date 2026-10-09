#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace scottland::window_notes
{
struct note_t
{
    int64_t id = 0;
    std::string writer;
    std::string text;
};

inline bool decode_utf8(const std::string& text, std::vector<uint32_t>& scalars)
{
    scalars.clear();
    for (size_t i = 0; i < text.size();)
    {
        const auto first = static_cast<unsigned char>(text[i]);
        uint32_t codepoint = 0;
        size_t length = 0;
        if (first <= 0x7f)
        {
            codepoint = first;
            length = 1;
        } else if (first >= 0xc2 && first <= 0xdf)
        {
            codepoint = first & 0x1f;
            length = 2;
        } else if (first >= 0xe0 && first <= 0xef)
        {
            codepoint = first & 0x0f;
            length = 3;
        } else if (first >= 0xf0 && first <= 0xf4)
        {
            codepoint = first & 0x07;
            length = 4;
        } else
        {
            return false;
        }

        if (i + length > text.size()) return false;
        for (size_t j = 1; j < length; ++j)
        {
            const auto next = static_cast<unsigned char>(text[i + j]);
            if ((next & 0xc0) != 0x80) return false;
            codepoint = (codepoint << 6) | (next & 0x3f);
        }

        if ((length == 3 && ((first == 0xe0 && static_cast<unsigned char>(text[i + 1]) < 0xa0) ||
            (first == 0xed && static_cast<unsigned char>(text[i + 1]) >= 0xa0))) ||
            (length == 4 && ((first == 0xf0 && static_cast<unsigned char>(text[i + 1]) < 0x90) ||
            (first == 0xf4 && static_cast<unsigned char>(text[i + 1]) >= 0x90))) ||
            codepoint > 0x10ffff || (codepoint >= 0xd800 && codepoint <= 0xdfff))
        {
            return false;
        }

        scalars.push_back(codepoint);
        i += length;
    }

    return true;
}

inline bool is_control(uint32_t codepoint)
{
    return codepoint <= 0x1f || (codepoint >= 0x7f && codepoint <= 0x9f);
}

inline bool valid_writer(const std::string& writer)
{
    std::vector<uint32_t> scalars;
    if (!decode_utf8(writer, scalars) || scalars.empty() || scalars.size() > 64) return false;
    for (auto codepoint : scalars)
        if (is_control(codepoint)) return false;
    return true;
}

inline bool valid_text(const std::string& text)
{
    std::vector<uint32_t> scalars;
    if (!decode_utf8(text, scalars) || scalars.empty() || scalars.size() > 1000) return false;
    for (auto codepoint : scalars)
        if (is_control(codepoint) && codepoint != '\n' && codepoint != '\t') return false;
    return true;
}
} // namespace scottland::window_notes
