#include "inertia.hpp"
#include <cmath>
#include <cstdlib>
#include <iostream>
using scottland::windowing::inertial_axis;
int passed = 0;
void check(bool ok, const char *name)
{
    if (!ok) { std::cerr << "FAIL " << name << '\n'; std::exit(1); }
    ++passed;
}
bool near(double a, double b) { return std::abs(a-b) < 1e-8; }
int main()
{
    for (double dt : {0.001, 1.0/60, 0.04, 1.0})
    {
        for (double sign : {-1.0, 1.0})
        {
            inertial_axis axis; axis.impulse(sign * 335, 6000);
            double distance = 0;
            for (int n = 0; n < 1000 && axis.velocity != 0; ++n) distance += axis.step(dt, 608);
            check(near(distance, sign*335*335/(2.0*608)), "single impulse analytic distance at different frame rates");
            check(axis.velocity == 0, "stops without sign reversal");
        }
    }
    scottland::windowing::release_velocity release;
    for (uint32_t t : {0, 13, 31, 55, 80}) release.add(t, t * .4, -double(t) * .2);
    auto vfit = release.estimate(80);
    check(near(vfit.first, 400) && near(vfit.second, -200), "irregular input times retain physical release velocity");
    check(release.estimate(130) == std::pair<double,double>{}, "paused release has no momentum");
    release.clear();
    for (uint32_t t : {0, 25, 50, 75, 100}) release.add(t, t * .02, 0);
    check(release.estimate(100) == std::pair<double,double>{}, "slow precise drop has no momentum");
    release.clear();
    for (uint32_t t : {0, 25, 50, 75}) release.add(UINT32_MAX - 50 + t, t * .4, 0);
    check(near(release.estimate(24).first, 400), "input timestamp wrap preserves velocity");
    inertial_axis axis;
    axis.impulse(335, 6000); double first = axis.step(.1, 608);
    axis.impulse(335, 6000);
    check(near(axis.velocity, 670-60.8), "presses accumulate surviving velocity");
    double total = first + axis.step(2, 608);
    check(total > 2*335*335/(2.0*608), "repeated impulses travel farther than separate impulses");
    axis.impulse(10000, 6000); check(axis.velocity == 6000, "positive speed clamp");
    axis.impulse(-20000, 6000); check(axis.velocity == -6000, "negative speed clamp");
    check(axis.constrain(-1, 0, 100) == 0 && axis.velocity == 0, "lower boundary stops outward velocity");
    axis.impulse(335,6000); check(axis.constrain(101,0,100)==100 && axis.velocity==0, "upper boundary stops outward velocity");
    axis.impulse(335,6000); check(axis.constrain(-1,0,100)==0 && axis.velocity==335, "inward velocity survives at boundary");
    double v = axis.velocity; check(axis.step(0,608)==0 && axis.velocity==v, "zero duration preserves velocity");
    check(axis.step(-1,608)==0 && axis.velocity==v, "negative duration preserves velocity");
    axis.impulse(-335,6000); check(axis.velocity==0, "opposite impulse cancels velocity");
    inertial_axis x,y; x.impulse(335,6000); y.impulse(-335,6000);
    check(near(x.step(.1,608), -y.step(.1,608)), "diagonal axes independent");
    x.constrain(200,0,100); check(x.velocity==0 && y.velocity<0, "boundary stops only its axis");
    for (double restitution : {0.0, 0.5, 1.0})
    {
        x.velocity = 100; y.velocity = -80;
        check(x.bounce(101,0,100,restitution)==100 && near(x.velocity,-100*restitution), "upper bounce reverses with configured restitution");
        check(y.bounce(-1,0,100,restitution)==0 && near(y.velocity,80*restitution), "lower bounce reverses with configured restitution");
    }
    x.velocity=100; y.velocity=80;
    x.bounce(101,0,100,.5);
    check(x.velocity==-50 && y.velocity==80,"bounce leaves the orthogonal axis alone");
    check(near(x.step(.02,608),-.8784) && near(x.velocity,-37.84),"friction continues after restitution");
    x.velocity=100; check(x.bounce(-1,0,100,.5)==0 && x.velocity==100,"inward velocity is not reflected twice");
    x.velocity=100; check(x.bounce(101,0,100,2)==100 && x.velocity==-100,"restitution is capped at one");
    x.velocity=100; check(x.bounce(101,0,100,-1)==100 && x.velocity==0,"negative restitution becomes a stop");
    scottland::windowing::friction_curve law;
    for (const auto& text : {"", "0:1 1:1", "nonsense", "0:nan 1:1", "0:2 0:3 1:1"})
    {
        law.parse(text); x.velocity=335;
        check(near(x.step(2,608,law,6000),335*335/(2.0*608)), "empty, flat and invalid laws preserve default analytic motion");
    }
    law.parse("0:2 1:2");x.velocity=335;
    check(near(x.step(2,608,law,6000),335*335/(4.0*608)), "double curve friction halves stopping distance");
    law.parse("0:0.2 0.4:3 1:1");
    double reference=0;
    for (double dt : {1.0/240, 1.0/60, 1.0/30})
    {
        x.velocity=1800;double d=0;
        while(x.velocity!=0)d+=x.step(dt,608,law,6000);
        if(!reference)reference=d;
        check(std::abs(d-reference)<0.02,"nonlinear law stable across frame rates");
    }
    std::cout << passed << " inertia unit checks passed\n";
}
