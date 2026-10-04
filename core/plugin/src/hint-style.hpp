#pragma once
#include <algorithm>
#include <cmath>
#include <string>

namespace scottland::windowing
{
struct hint_rgb
{
    double r, g, b;
};
struct hint_palette
{
    bool light = false;
    hint_rgb background{0.122, 0.137, 0.173};
    hint_rgb foreground{0.847, 0.871, 0.914};
    hint_rgb accent{0.506, 0.631, 0.757};
    double text_scale = 1.0;                 // the desktop's text scaling (WK5: hints follow it)
    std::string font_family = "sans-serif";  // the desktop's interface font
};
constexpr double hint_badge_opacity = 0.21;
// WK14/WK38 default window/card overlay. `scottland/window_mode_tint` replaces it live for
// drawing; hint colors keep this value as their contrast basis so a slider never recolors them.
constexpr double hint_window_opacity = 0.07;
constexpr double hint_border_width = 2.0;
// WK37: a window with less than this share of its on-screen area visible (the rest covered by
// windows and widgets in front, at their displayed avoidance offsets) also gets an opaque
// outline in its hint color in the overlay layer above all windows, this many logical px thick.
constexpr double hint_outline_visible_fraction = 0.5;
constexpr double hint_outline_width = 2.0;
inline double hint_badge_size(double width, double height, double text_scale = 1.0)
{
    // Vimarchy's sizing, scaled with the desktop's text size as everything else that's text.
    double s = std::clamp(text_scale, 0.5, 3.0);
    return std::clamp(std::min(width, height) * 0.34 * s, 72.0 * s, 132.0 * s);
}
inline double widget_hint_overlap(double diameter, double height)
{
    // Keep large text badges out of a short widget's upper/lower corners: the arc
    // entering the card occupies at most its middle 60%. Normal 96px cards use 15%.
    double radius = diameter / 2;
    double half_arc = std::min(radius, height * 0.30);
    return std::min(diameter * 0.15, radius - std::sqrt(radius * radius - half_arc * half_arc));
}
inline hint_rgb hint_mix(hint_rgb a, hint_rgb b, double amount)
{
    return {a.r + (b.r - a.r) * amount, a.g + (b.g - a.g) * amount,
        a.b + (b.b - a.b) * amount};
}
inline double hint_luminance(hint_rgb c)
{
    auto linear = [] (double v) { return v <= 0.04045 ? v / 12.92 : std::pow((v + 0.055) / 1.055, 2.4); };
    return linear(c.r) * 0.2126 + linear(c.g) * 0.7152 + linear(c.b) * 0.0722;
}
inline double hint_contrast(hint_rgb a, hint_rgb b)
{
    double x = hint_luminance(a), y = hint_luminance(b);
    return (std::max(x, y) + 0.05) / (std::min(x, y) + 0.05);
}
inline double hint_hue(hint_rgb c)
{
    double hi = std::max({c.r, c.g, c.b}), lo = std::min({c.r, c.g, c.b});
    if (hi - lo < 1e-6) return 210; // achromatic accent: the neutral palette's blue family
    double h = hi == c.r ? (c.g - c.b) / (hi - lo) :
        hi == c.g ? 2 + (c.b - c.r) / (hi - lo) : 4 + (c.r - c.g) / (hi - lo);
    return std::fmod(h * 60 + 360, 360);
}
inline hint_rgb hint_hsl(double hue, double saturation, double lightness)
{
    double c = (1 - std::abs(2 * lightness - 1)) * saturation;
    double h = std::fmod(hue + 360, 360) / 60;
    double x = c * (1 - std::abs(std::fmod(h, 2) - 1));
    hint_rgb rgb = h < 1 ? hint_rgb{c, x, 0} : h < 2 ? hint_rgb{x, c, 0} :
        h < 3 ? hint_rgb{0, c, x} : h < 4 ? hint_rgb{0, x, c} :
        h < 5 ? hint_rgb{x, 0, c} : hint_rgb{c, 0, x};
    double m = lightness - c / 2;
    return {rgb.r + m, rgb.g + m, rgb.b + m};
}
inline double hint_badge_contrast(hint_rgb color, hint_rgb surface)
{
    return hint_contrast(color, hint_mix(hint_mix(surface, color, hint_window_opacity),
        color, hint_badge_opacity));
}
inline hint_rgb hint_color(unsigned slot, const hint_palette& palette)
{
    // A low-discrepancy sequence fills the opposite 160-degree arc at any population size.
    // Consecutive slots jump ~61 or ~99 degrees. Never divide by the current window count:
    // adding/closing windows must not change the colors of retained letters (tenet 2).
    double fraction = std::fmod(0.5 + slot * 0.6180339887498948482, 1.0);
    double hue = std::fmod(hint_hue(palette.accent) + 180 - 80 + fraction * 160 + 360, 360);
    double saturation = palette.light ? 0.72 : 0.78;
    double start = palette.light ? 0.34 : 0.70;
    auto surface = hint_mix(palette.background, palette.foreground, 0.05);
    auto contrast = [&] (hint_rgb c) { return std::min({hint_contrast(c, palette.background),
        hint_badge_contrast(c, palette.background), hint_badge_contrast(c, surface)}); };
    hint_rgb best = hint_hsl(hue, saturation, start);
    // Keep hue and saturation, adjusting lightness toward the scheme's contrasting pole.
    // Also consider the other pole for unusual/mid-tone theme backgrounds.
    for (int direction : {palette.light ? -1 : 1, palette.light ? 1 : -1})
    {
        for (int step = 0; step <= 200; ++step)
        {
            double l = std::clamp(start + direction * step * 0.005, 0.0, 1.0);
            auto color = hint_hsl(hue, saturation, l);
            if (contrast(color) > contrast(best)) best = color;
            if (contrast(color) >= 3.1) return color;
            if (l == 0 || l == 1) break;
        }
    }
    return best;
}
}
