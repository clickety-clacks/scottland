#include "attention-color.hpp"

#include <cassert>
#include <cmath>
#include <iostream>

namespace
{
bool near(float a, float b)
{
    return std::abs(a - b) < 1e-6f;
}

bool same(scottland::attention_color::rgb_t a, scottland::attention_color::rgb_t b)
{
    return near(a.r, b.r) && near(a.g, b.g) && near(a.b, b.b);
}
}

int main()
{
    using namespace scottland::attention_color;
    constexpr rgb_t theme{.23f, .51f, .79f};
    assert(same(select("theme", true, theme), theme));
    assert(same(select("theme", false, theme), theme));
    assert(same(select("warm", true, theme), hex(0xB83F36)));
    assert(same(select("warm", false, theme), hex(0xFF9E57)));
    assert(same(select("cool", true, theme), hex(0x707C28)));
    assert(same(select("cool", false, theme), hex(0xC9DD61)));
    assert(same(select("unknown", true, theme), theme));
    assert(same(select("unknown", false, theme), theme));
    std::cout << "PASS theme passthrough and light/dark warm/cool attention colors\n";
}
