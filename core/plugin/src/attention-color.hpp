#pragma once

#include <string_view>

namespace scottland::attention_color
{
struct rgb_t
{
    float r, g, b;
};

constexpr rgb_t hex(unsigned int value)
{
    return {
        float((value >> 16) & 0xff) / 255.f,
        float((value >> 8) & 0xff) / 255.f,
        float(value & 0xff) / 255.f,
    };
}

inline constexpr rgb_t warm_light = hex(0xB83F36); // brick red
inline constexpr rgb_t warm_dark  = hex(0xFF9E57); // amber
inline constexpr rgb_t cool_light = hex(0x707C28); // olive green
inline constexpr rgb_t cool_dark  = hex(0xC9DD61); // yellow-green

/** Theme preserves the active palette; unknown values safely behave like Theme. */
constexpr rgb_t select(std::string_view family, bool light, rgb_t theme)
{
    if (family == "warm") return light ? warm_light : warm_dark;
    if (family == "cool") return light ? cool_light : cool_dark;
    return theme;
}
}
