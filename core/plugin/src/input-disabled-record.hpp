#pragma once

#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>

namespace scottland::input
{
inline bool valid_device_name(const std::string& name)
{
    if (name.empty()) return false;
    for (std::size_t i = 0; i < name.size();)
    {
        auto first = static_cast<unsigned char>(name[i]);
        std::uint32_t codepoint = 0;
        std::size_t length = 0;
        if (first < 0x80)
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

        if (i + length > name.size()) return false;
        for (std::size_t j = 1; j < length; ++j)
        {
            auto byte = static_cast<unsigned char>(name[i + j]);
            if ((byte & 0xc0) != 0x80) return false;
            codepoint = (codepoint << 6) | (byte & 0x3f);
        }
        if ((length == 3 && codepoint < 0x800) || (length == 4 && codepoint < 0x10000) ||
            (codepoint >= 0xd800 && codepoint <= 0xdfff) || codepoint > 0x10ffff ||
            codepoint <= 0x1f || (codepoint >= 0x7f && codepoint <= 0x9f))
        {
            return false;
        }
        i += length;
    }
    return true;
}

inline bool device_matches_kind(const std::string& requested, const std::string& actual)
{
    return (requested == "touchpad" && actual == "pointer") ||
        (requested == "touchscreen" && (actual == "touch" || actual == "tablet"));
}

inline bool disabled_record_matches(const std::string& requested,
    const std::optional<std::string>& recorded_name, const std::string& actual_kind,
    const std::string& actual_name)
{
    return recorded_name && valid_device_name(*recorded_name) &&
        valid_device_name(actual_name) && *recorded_name == actual_name &&
        device_matches_kind(requested, actual_kind);
}

template<class Devices, class NameOf, class KindOf, class Disable>
void apply_disabled_record(const std::string& requested,
    const std::optional<std::string>& recorded_name, Devices&& devices, NameOf name_of,
    KindOf kind_of, Disable disable)
{
    if (!recorded_name || !valid_device_name(*recorded_name)) return;
    for (auto&& device : devices)
    {
        auto name = name_of(device);
        if (!name) continue;
        if (disabled_record_matches(requested, recorded_name, kind_of(device), *name))
        {
            disable(device);
        }
    }
}
}
