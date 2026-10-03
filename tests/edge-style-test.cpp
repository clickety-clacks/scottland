#include "edge-style.hpp"

#include <cassert>
#include <cmath>
#include <iostream>

namespace
{
bool near(float a, float b)
{
    return std::abs(a - b) < 1e-6f;
}
}

int main()
{
    using scottland::edge_style::halo_neutral_density;
    using scottland::edge_style::neutral_color;
    using scottland::edge_style::tint_strength;

    const auto light_default = neutral_color(true, .08f);
    assert(light_default.r == .08f && light_default.g == .08f && light_default.b == .1f);
    const auto dark_default = neutral_color(false, .92f);
    assert(dark_default.r == .9f && dark_default.g == .92f && dark_default.b == .95f);
    for (bool light : {false, true})
    {
        const auto black = neutral_color(light, 0.f);
        const auto white = neutral_color(light, 1.f);
        assert(near(black.r, 0.f) && near(black.g, 0.f) && near(black.b, 0.f));
        assert(near(white.r, 1.f) && near(white.g, 1.f) && near(white.b, 1.f));
    }
    const auto gray = neutral_color(true, .37f);
    assert(gray.r == .37f && gray.g == .37f && gray.b == .37f);

    assert(near(tint_strength(0.f, 0.f, 0.f, false), 0.f));
    assert(near(tint_strength(1.f, 0.f, 0.f, false), 1.f));
    assert(near(tint_strength(0.f, 1.f, 0.f, false), 1.f)); // focused accent is unchanged
    assert(near(tint_strength(0.f, 0.f, 1.f, false), 1.f)); // attention is unchanged
    assert(near(tint_strength(0.f, 0.f, 0.f, true), 1.f));  // hint dye is unchanged
    assert(near(tint_strength(.25f, 0.f, 0.f, false), .25f));
    assert(near(tint_strength(.25f, .5f, 0.f, false), .625f));

    assert(near(halo_neutral_density(0.f, 0.f), 0.f));
    assert(near(halo_neutral_density(1.f, 0.f), .16f));
    assert(near(halo_neutral_density(0.f, 1.f), .44f)); // focus retains its old density
    assert(near(halo_neutral_density(1.f, 1.f), .44f)); // unchanged default
    std::cout << "PASS unfocused edge tone, strength, focus, attention and hint rules\n";
}
