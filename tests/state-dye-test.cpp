#include "state-dye.hpp"

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
    using scottland::state_dye::mix;
    using scottland::state_dye::tone;
    using scottland::state_dye::weight;

    assert(near(mix(0.f, 0.f, false), 0.f));
    assert(near(mix(.5f, 0.f, false), .5f));
    assert(near(mix(0.f, .5f, false), .5f));
    assert(near(mix(.5f, .5f, false), .75f));
    assert(near(mix(0.f, 0.f, true), 1.f));

    // Default GO23 exactly reproduces A16's prior neutral-to-state weight.
    for (float neutral : {0.f, .25f, 1.f})
        for (float state : {0.f, .25f, 1.f})
            assert(near(weight(neutral, state, 1.f), neutral * (1.f - state) + state));
    assert(near(weight(.25f, 0.f, 0.f), .25f)); // neutral A16 strength stays independent
    assert(near(weight(.25f, 1.f, .25f), .25f));
    assert(near(weight(.25f, 1.f, 1.5f), 1.5f)); // shader caps the final blend
    assert(near(weight(1.f, 0.f, 0.f), 1.f));

    const glm::vec3 neutral{.1f, .2f, .3f};
    const glm::vec3 color{.5f, .6f, .7f};
    assert(tone(neutral, color, 1.f) == color); // exact default fallback color
    const auto faint = tone(neutral, color, 0.f);
    assert(faint == neutral);
    const auto strong = tone(neutral, color, 1.5f);
    assert(near(strong.r, .7f) && near(strong.g, .8f) && near(strong.b, .9f));
    const auto clipped = tone(neutral, glm::vec3{.9f, .95f, 1.f}, 1.5f);
    assert(clipped.r <= 1.f && clipped.g <= 1.f && clipped.b == 1.f);
    std::cout << "PASS GO23 state/neutral separation, strength range and fallback tone\n";
}
