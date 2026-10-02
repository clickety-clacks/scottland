#include "cycle-spring.hpp"
#include <cassert>
#include <cmath>
#include <iostream>

int main()
{
    using scottland::windowing::cycle_spring_remaining;
    for (double amount : {0.00001, 0.001, 0.03, 0.1})
    {
        assert(cycle_spring_remaining(0, amount) == 1);
        assert(cycle_spring_remaining(1, amount) == 0);
        assert(cycle_spring_remaining(2, amount) == 0);
        assert(std::abs(cycle_spring_remaining(.6, amount) + amount) < 1e-12);
        double previous = 1;
        bool crossed = false;
        for (int i = 1; i <= 10000; ++i)
        {
            double t = i / 10000.0, r = cycle_spring_remaining(t, amount);
            assert(std::isfinite(r) && r >= -amount && r <= 1);
            assert(t <= .6 ? r <= previous : r >= previous);
            if (crossed) assert(r <= 0);
            crossed |= r < 0;
            previous = r;
        }
        assert(crossed);
        // Both joins have zero velocity, including the exact terminal clamp.
        constexpr double epsilon = 1e-7;
        for (double join : {.6, 1.0})
            assert(std::abs((cycle_spring_remaining(join + epsilon, amount) -
                cycle_spring_remaining(join - epsilon, amount)) / (2 * epsilon)) < 1e-5);
    }
    std::cout << "4 spring amplitudes: exact endpoints, one crossing, bounded peak, monotone settle and C1 joins passed\n";
}
