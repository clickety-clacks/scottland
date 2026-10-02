#include "goo-model.hpp"
#include <algorithm>
#include <cmath>
#include <sstream>

namespace scottland::goo
{
namespace
{
float fract(float x) { return x - std::floor(x); }
float hash(glm::vec2 p)
{
    p = {fract(p.x * 123.34f), fract(p.y * 456.21f)};
    p += glm::dot(p, p + 45.32f);
    return fract(p.x * p.y);
}
float vnoise(glm::vec2 p)
{
    glm::vec2 i = glm::floor(p), f = glm::fract(p), u = f * f * (3.f - 2.f * f);
    return glm::mix(glm::mix(hash(i), hash(i + glm::vec2{1, 0}), u.x),
                    glm::mix(hash(i + glm::vec2{0, 1}), hash(i + glm::vec2{1, 1}), u.x), u.y);
}
} // namespace
float noise(glm::vec2 p)
{
    float s = 0, a = .5;
    for (int k = 0; k < 3; k++)
    {
        s += a * vnoise(p);
        p = p * 2.03f + 17.1f;
        a *= .5;
    }
    return s / .875;
}
float distance(glm::vec2 p, const source_t &s)
{
    float r = std::min({s.liquid.y, s.rect.z, s.rect.w});
    auto q = glm::abs(p - glm::vec2(s.rect)) - glm::vec2(s.rect.z, s.rect.w) + r;
    return glm::length(glm::max(q, glm::vec2{0})) + std::min(std::max(q.x, q.y), 0.f) - r;
}
settings_t::settings_t() { curve(""); }
bool settings_t::curve(const std::string &text)
{
    std::vector<glm::vec2> points;
    std::istringstream words(text);
    std::string word;
    while (words >> word)
    {
        auto colon = word.find(':');
        if (colon == std::string::npos)
            return false;
        try
        {
            size_t nx, ny;
            float x = std::stof(word.substr(0, colon), &nx), y = std::stof(word.substr(colon + 1), &ny);
            if (nx != colon || ny != word.size() - colon - 1 || !std::isfinite(x) || !std::isfinite(y) ||
                x < 0 || x > 1 || y < 0 || y > 1)
                return false;
            points.push_back({x, y});
        }
        catch (...)
        {
            return false;
        }
    }
    if (points.empty())
    {
        for (size_t i = 0; i < falloff.size(); i++)
            falloff[i] = std::exp(-4.f * i / (falloff.size() - 1));
        return true;
    }
    std::sort(points.begin(), points.end(), [](auto a, auto b) { return a.x < b.x; });
    if (points.size() < 2 || points.front().x != 0 || points.back().x != 1)
        return false;
    std::vector<float> delta(points.size() - 1), slopes(points.size(), 0);
    for (size_t i = 0; i < delta.size(); i++)
    {
        float h = points[i + 1].x - points[i].x;
        if (h < 1e-6 || points[i + 1].y > points[i].y)
            return false;
        delta[i] = (points[i + 1].y - points[i].y) / h;
    }
    slopes.front() = delta.front();
    slopes.back() = delta.back();
    for (size_t i = 1; i + 1 < points.size(); i++)
    {
        if (delta[i - 1] * delta[i] <= 0)
            continue;
        float h0 = points[i].x - points[i - 1].x, h1 = points[i + 1].x - points[i].x;
        float w1 = 2 * h1 + h0, w2 = h1 + 2 * h0;
        slopes[i] = (w1 + w2) / (w1 / delta[i - 1] + w2 / delta[i]);
    }
    for (size_t k = 0; k < falloff.size(); k++)
    {
        float t = float(k) / (falloff.size() - 1);
        size_t i = 0;
        while (i + 2 < points.size() && t > points[i + 1].x)
            i++;
        float h = points[i + 1].x - points[i].x, u = (t - points[i].x) / h, u2 = u * u, u3 = u2 * u;
        falloff[k] = std::clamp((2 * u3 - 3 * u2 + 1) * points[i].y + (u3 - 2 * u2 + u) * h * slopes[i] +
                                    (-2 * u3 + 3 * u2) * points[i + 1].y + (u3 - u2) * h * slopes[i + 1],
                                0.f, 1.f);
    }
    return true;
}
float settings_t::fall(float d) const
{
    if (!std::isfinite(d))
        return 0;
    // Compact support: the liquid tapers to nothing over its last half reach and is exactly zero
    // beyond four reaches, so the goo's extent (and its damage) has a hard analytic bound.
    float t = std::max(d, 0.f) / (4 * reach) * (falloff.size() - 1);
    if (t >= falloff.size() - 1)
        return 0;
    size_t i = size_t(t);
    float u = std::clamp((d / reach - 3.5f) / .5f, 0.f, 1.f);
    return glm::mix(falloff[i], falloff[i + 1], t - i) * (1 - u * u * (3 - 2 * u));
}
float settings_t::threshold() const { return std::max(.0001f, fall(thickness)); }
float density(glm::vec2 p, const std::vector<source_t> &sources, const settings_t &s, float time)
{
    if (!std::isfinite(p.x) || !std::isfinite(p.y))
        return 0;
    float f = 0;
    for (auto &w : sources)
    {
        if (!w.emitter)
            continue;
        float n = noise(p / s.lump + w.liquid.z * glm::vec2{7.13, 3.71} +
                        glm::vec2{time * s.drift, -time * s.drift * .73});
        float scale = std::clamp(w.scale, 0.f, 1.f);
        float a = std::max(w.liquid.x * (1 + s.noise * scale * (n - .5f) * 2),
                           s.threshold()/std::max(s.fall(s.thickness*.1f*scale), .0001f));
        // Smooth state deposits: corners and the dot thicken this same field.
        for (int k = 0; k < 4; k++)
        {
            glm::vec2 c = glm::vec2(w.rect) +
                          glm::vec2{(k & 1 ? 1.f : -1.f) * w.rect.z, (k & 2 ? 1.f : -1.f) * w.rect.w};
            float d = glm::length(p - c) / 30;
            a += w.corners[k] * .22f * std::exp(-d * d);
        }
        float d = glm::length(p - glm::vec2(w.dot)) / 12;
        a += w.dot.z * .45f * std::exp(-d * d);
        f += std::max(a, 0.f) * s.fall(std::max(distance(p, w), 0.f));
    }
    return f;
}
float union_distance(glm::vec2 p, const std::vector<source_t> &sources)
{
    float d = 1e9;
    for (auto &s : sources)
        d = std::min(d, distance(p, s));
    return d;
}
void amounts(std::vector<source_t> &sources, const settings_t &s)
{
    for (auto &a : sources)
    {
        if (!a.emitter)
        {
            a.liquid.x = 0;
            continue;
        }
        float gap = 1e9;
        for (auto &b : sources)
        {
            if (a.id == b.id || !b.emitter)
                continue;
            glm::vec2 d = glm::max(glm::abs(glm::vec2(a.rect) - glm::vec2(b.rect)) -
                                       glm::vec2(a.rect.z + b.rect.z, a.rect.w + b.rect.w),
                                   glm::vec2{0});
            gap = std::min(gap, glm::length(d));
        }
        float bridge = std::max(0.f, 1 - gap / (2.2f * (s.thickness + s.reach * .7f)));
        // Prototype volume draw, plus A7's constant screen-sized proximity swell.
        float thickness = s.thickness * a.scale;
        thickness += (2 * s.thickness - thickness) * a.swell * (s.swell / .7f);
        a.liquid.x = s.threshold()/std::max(s.fall(thickness), .0001f) * (1 - s.thinning * .35f * bridge);
    }
}
} // namespace scottland::goo
