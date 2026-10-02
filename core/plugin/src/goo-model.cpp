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
    float t = std::max(d, 0.f) / (4 * reach) * (falloff.size() - 1);
    if (t >= falloff.size() - 1)
        return falloff.back() * std::exp(-(d / reach - 4));
    size_t i = size_t(t);
    return glm::mix(falloff[i], falloff[i + 1], t - i);
}
float settings_t::threshold() const { return std::max(.0001f, fall(thickness)); }
bool overlaps(const std::vector<source_t> &sources)
{
    for (size_t i = 0; i < sources.size(); i++)
        for (size_t j = i + 1; j < sources.size(); j++)
        {
            auto a = sources[i].rect, b = sources[j].rect;
            if (std::abs(a.x - b.x) < a.z + b.z && std::abs(a.y - b.y) < a.w + b.w)
                return true;
        }
    return false;
}
size_t content_index(glm::vec2 p, const std::vector<source_t> &sources)
{
    for (size_t i = 0; i < sources.size(); i++)
        if (distance(p, sources[i]) <= 0) return i;
    return sources.size();
}
float control_cloud(glm::vec2 p, const source_t &w)
{
    auto q = p - glm::vec2(w.rect);
    auto corner = glm::smoothstep(glm::vec2(w.rect.z, w.rect.w) - w.control_extent,
        glm::vec2(w.rect.z, w.rect.w) - w.control_extent + 6.f, glm::abs(q));
    float c = w.corners[(q.y < 0 ? 0 : 2) + (q.x < 0 ? 0 : 1)];
    float side = std::abs(q.x) - w.rect.z > std::abs(q.y) - w.rect.w ?
        w.sides[q.x > 0 ? 1 : 3] : w.sides[q.y > 0 ? 2 : 0];
    return glm::mix(side, c, corner.x * corner.y);
}
float overlap_film_width(const source_t &w, const settings_t &s)
{
    // The inner film follows the same outer swell, relative to this window's
    // resting thickness. Its unswollen width is always the user's film setting.
    float rest = std::max(s.thickness * w.scale, .01f);
    float swollen = std::max(rest * .7f,
        rest + (2 * s.thickness - rest) * w.swell * (s.swell / .7f));
    return s.overlap_film * swollen / rest;
}
float density(glm::vec2 p, const std::vector<source_t> &sources, const settings_t &s, float time)
{
    if (!std::isfinite(p.x) || !std::isfinite(p.y))
        return 0;
    float f = 0;
    size_t back = content_index(p, sources);
    if (back < sources.size() && s.overlap_film <= 0) return 0;
    for (size_t i = 0; i < back; i++)
    {
        auto &w = sources[i];
        if (!w.emitter)
            continue;
        float n = noise(p / s.lump + w.liquid.z * glm::vec2{7.13, 3.71} +
                        glm::vec2{time * s.drift, -time * s.drift * .73});
        float scale = std::clamp(w.scale, 0.f, 1.f);
        float a = std::max(w.liquid.x * (1 + s.noise * scale * (n - .5f) * 2),
                           s.threshold()/std::max(s.fall(s.thickness*.1f*scale), .0001f));
        a += .22f * s.hover_cloudiness * control_cloud(p, w);
        float d = glm::length(p - glm::vec2(w.dot)) / 12;
        a += w.dot.z * .45f * std::exp(-d * d);
        float e = std::max(distance(p, w), 0.f);
        if (back < sources.size())
        {
            float width = glm::mix(overlap_film_width(w, s), s.thickness,
                1.f - glm::smoothstep(0.f, s.reach, -distance(p, sources[back])));
            e *= s.thickness / std::max(width, .01f);
            a /= std::max(w.liquid.x, .0001f);
        }
        f += std::max(a, 0.f) * s.fall(e);
    }
    return f;
}
std::vector<float> support_radii(std::vector<source_t> sources, const settings_t &s, bool film)
{
    amounts(sources, s);
    std::vector<float> peaks;
    float total = 0;
    for (auto &w : sources)
    {
        float reserve = s.threshold() / std::max(s.fall(std::max(s.thickness * .1f *
            std::clamp(w.scale, 0.f, 1.f), w.hinted ? 1.f : 0.f)), .0001f);
        float peak = w.emitter ? std::max(w.liquid.x * (1 + s.noise), reserve) +
            .22f * s.hover_cloudiness * std::max({w.corners.x, w.corners.y, w.corners.z, w.corners.w,
                w.sides.x, w.sides.y, w.sides.z, w.sides.w}) + .45f * w.dot.z : 0;
        if (film && w.emitter) peak /= std::max(w.liquid.x, .0001f);
        peaks.push_back(peak);
        total += peak;
    }
    // Bound the threshold contour with the simulation mask's existing 3% margin.
    // Draw reconstruction and device-pixel AA get spatial padding in compute_bands.
    // Allow a full packed-field quantum (also bounds half-float rounding).
    float edge = s.threshold() * .97f / (1 + 3.9f * std::max(0.f, s.wave_height));
    edge = std::expm1(std::max(0.f, std::log1p(edge) - 2.83321334f / 255));
    // Extremely low custom thresholds cannot be bounded after packed quantization.
    if (edge <= 0)
        return std::vector<float>(sources.size(), 1e6f);
    auto root = [&](auto field, float hi)
    {
        float lo = 0;
        for (int k = 0; k < 32; k++)
        {
            float mid = (lo + hi) / 2;
            if (field(mid) >= edge) lo = mid; else hi = mid;
        }
        return hi;
    };
    float hi = 4 * s.reach;
    while (total * s.fall(hi) >= edge) hi *= 2;
    float global = root([&](float d) { return total * s.fall(d); }, hi);
    std::vector<float> radii;
    for (auto &a : sources)
    {
        // Any visible point is within global of some island. If a is its nearest
        // rectangle, each other source is at least d away and gap(a,b)-global away.
        // Far sources retain their exponential tails; only nearby rectangles bridge.
        std::vector<float> gaps;
        for (auto &b : sources)
        {
            auto gap = glm::max(glm::abs(glm::vec2(a.rect) - glm::vec2(b.rect)) -
                glm::vec2(a.rect.z + b.rect.z, a.rect.w + b.rect.w), glm::vec2{0});
            gaps.push_back(glm::length(gap) - global);
        }
        auto bound = [&](float d)
        {
            float sum = 0;
            for (size_t j = 0; j < sources.size(); j++)
                sum += peaks[j] * s.fall(std::max(d, gaps[j]));
            return sum;
        };
        radii.push_back(root(bound, global));
    }
    return radii;
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
        float rest = s.thickness * a.scale;
        float thickness = std::max(rest * .7f,
            rest + (2 * s.thickness - rest) * a.swell * (s.swell / .7f));
        a.liquid.x = s.threshold()/std::max(s.fall(thickness), .0001f) * (1 - s.thinning * .35f * bridge);
    }
}
} // namespace scottland::goo
