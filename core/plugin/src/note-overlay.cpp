#include "note-overlay.hpp"

#include <algorithm>
#include <cmath>
#include <drm_fourcc.h>
#include <iomanip>
#include <sstream>

extern "C" {
#include <wlr/render/wlr_texture.h>
}

namespace scottland::windowing
{
namespace
{
std::string font_family(const hint_palette& palette)
{
    return palette.font_family.empty() ? "sans-serif" : palette.font_family;
}

std::string normalized_text(std::string text)
{
    std::replace(text.begin(), text.end(), '\t', ' ');
    return text;
}

void rounded_rect(cairo_t *cr, double x, double y, double width, double height, double radius)
{
    const double r = std::clamp(radius, 0.0, std::min(width, height) / 2.0);
    cairo_new_sub_path(cr);
    cairo_arc(cr, x + width - r, y + r, r, -M_PI / 2, 0);
    cairo_arc(cr, x + width - r, y + height - r, r, 0, M_PI / 2);
    cairo_arc(cr, x + r, y + height - r, r, M_PI / 2, M_PI);
    cairo_arc(cr, x + r, y + r, r, M_PI, 3 * M_PI / 2);
    cairo_close_path(cr);
}

std::vector<std::string> utf8_chunks(const std::string& text)
{
    std::vector<std::string> chunks;
    for (size_t i = 0; i < text.size();)
    {
        size_t length = 1;
        auto ch = static_cast<unsigned char>(text[i]);
        if ((ch & 0xe0) == 0xc0) length = 2;
        else if ((ch & 0xf0) == 0xe0) length = 3;
        else if ((ch & 0xf8) == 0xf0) length = 4;
        if (i + length > text.size()) length = 1;
        chunks.push_back(text.substr(i, length));
        i += length;
    }

    return chunks;
}
}

note_layout_t::note_layout_t()
{
    surface = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, 1, 1);
    cr = cairo_create(surface);
}

note_layout_t::~note_layout_t()
{
    if (cr) cairo_destroy(cr);
    if (surface) cairo_surface_destroy(surface);
}

double note_layout_t::measure(const std::string& text, const std::string& family, double size,
    cairo_font_weight_t weight)
{
    cairo_select_font_face(cr, family.c_str(), CAIRO_FONT_SLANT_NORMAL, weight);
    cairo_set_font_size(cr, size);
    cairo_text_extents_t ext{};
    cairo_text_extents(cr, text.c_str(), &ext);
    return std::max(0.0, ext.x_advance);
}

std::string note_layout_t::ellipsize(std::string text, const std::string& family, double size, double width)
{
    constexpr const char *ellipsis = "…";
    while (!text.empty() && measure(text + ellipsis, family, size) > width)
    {
        auto chunks = utf8_chunks(text);
        if (chunks.empty()) break;
        text.resize(text.size() - chunks.back().size());
    }

    if (measure(ellipsis, family, size) > width) return {};
    return text + ellipsis;
}

std::vector<std::string> note_layout_t::wrap(const std::string& value, const std::string& family,
    double size, double width)
{
    std::vector<std::string> lines;
    auto text = normalized_text(value);
    size_t paragraph_start = 0;
    while (paragraph_start <= text.size())
    {
        auto paragraph_end = text.find('\n', paragraph_start);
        if (paragraph_end == std::string::npos) paragraph_end = text.size();
        std::string paragraph = text.substr(paragraph_start, paragraph_end - paragraph_start);
        if (paragraph.empty())
        {
            lines.emplace_back();
        } else
        {
            std::istringstream words(paragraph);
            std::string word, line;
            while (words >> word)
            {
                if (measure(word, family, size) > width)
                {
                    if (!line.empty())
                    {
                        lines.push_back(line);
                        line.clear();
                    }

                    std::string part;
                    for (auto& chunk : utf8_chunks(word))
                    {
                        if (!part.empty() && measure(part + chunk, family, size) > width)
                        {
                            lines.push_back(part);
                            part.clear();
                        }

                        part += chunk;
                    }

                    line = part;
                } else if (line.empty())
                {
                    line = word;
                } else if (measure(line + " " + word, family, size) <= width)
                {
                    line += " " + word;
                } else
                {
                    lines.push_back(line);
                    line = word;
                }
            }

            if (!line.empty()) lines.push_back(line);
        }

        if (paragraph_end == text.size()) break;
        paragraph_start = paragraph_end + 1;
    }

    if (lines.empty()) lines.emplace_back();
    return lines;
}

note_panel_layout_t note_layout_t::panel(const std::string& writer, const std::string& text,
    const hint_palette& palette, double width_limit, double height_limit, bool include_writer)
{
    note_panel_layout_t result;
    result.show_writer = include_writer;
    const double scale = std::clamp(palette.text_scale, 0.5, 3.0);
    result.writer_font = 11 * scale;
    result.text_font = 13 * scale;
    result.writer_line_height = 1.25 * result.writer_font;
    result.text_line_height = 1.28 * result.text_font;
    result.padding = 8 * scale;
    result.border = 1 * scale;
    result.radius = 6 * scale;
    result.writer_gap = include_writer ? 2 * scale : 0;

    const std::string family = font_family(palette);
    const double inset = result.padding + result.border;
    const double content_limit = width_limit - 2 * inset;
    if (content_limit < 1 || height_limit <= 0) return result;

    double desired = 0;
    if (include_writer)
        desired = measure(normalized_text(writer), family, result.writer_font, CAIRO_FONT_WEIGHT_NORMAL);
    auto text_value = normalized_text(text);
    size_t start = 0;
    while (start <= text_value.size())
    {
        auto end = text_value.find('\n', start);
        if (end == std::string::npos) end = text_value.size();
        desired = std::max(desired, measure(text_value.substr(start, end - start), family, result.text_font));
        if (end == text_value.size()) break;
        start = end + 1;
    }
    const double width = std::min(width_limit, std::max(2 * inset + 1, desired + 2 * inset));
    const double text_width = std::max(1.0, width - 2 * inset);
    if (include_writer)
        result.writer_lines = wrap(writer, family, result.writer_font, text_width);
    result.text_lines = wrap(text_value, family, result.text_font, text_width);

    const double writer_height = include_writer ? result.writer_line_height * result.writer_lines.size() : 0;
    const double fixed_height = 2 * inset + writer_height + result.writer_gap;
    const double max_body_height = height_limit - fixed_height;
    const size_t max_lines = max_body_height >= result.text_line_height ?
        size_t(std::floor(max_body_height / result.text_line_height)) : 0;
    if (max_lines == 0) return result;

    const size_t keep = std::min<size_t>({result.text_lines.size(), max_lines, size_t(12)});
    const bool cut = keep < result.text_lines.size();
    result.text_lines.resize(keep);
    if (cut && !result.text_lines.empty())
        result.text_lines.back() = ellipsize(result.text_lines.back(), family, result.text_font, text_width);
    result.width = width;
    result.height = fixed_height + keep * result.text_line_height;
    result.drawable = true;
    return result;
}

double note_layout_t::marker_width(const std::string& label, const hint_palette& palette)
{
    const double scale = std::clamp(palette.text_scale, 0.5, 3.0);
    return std::max(12 * scale, measure(label, font_family(palette), 8 * scale) + 8 * scale);
}

void note_overlay_node_t::update(const std::vector<note_overlay_item_t>& items,
    const hint_palette& palette, double output_scale)
{
    std::ostringstream key;
    key << std::setprecision(4) << output_scale << ':' << palette.background.r << ',' << palette.background.g << ','
        << palette.background.b << ':' << palette.foreground.r << ',' << palette.foreground.g << ','
        << palette.foreground.b << ':' << palette.accent.r << ',' << palette.accent.g << ','
        << palette.accent.b << ':' << palette.font_family << ':' << palette.text_scale;
    wf::geometry_t bounds{0, 0, 0, 0};
    bool have_bounds = false;
    for (const auto& item : items)
    {
        key << ';' << int(item.kind) << ':' << item.box.x << ',' << item.box.y << ',' << item.box.width << ','
            << item.box.height << ':' << item.label;
        const auto& panel = item.panel;
        key << ':' << panel.drawable << ':' << panel.show_writer << ':' << panel.width << ',' << panel.height
            << ':' << panel.writer_font << ',' << panel.text_font << ':' << panel.padding << ',' << panel.border
            << ',' << panel.radius << ',' << panel.writer_gap;
        for (auto& line : panel.writer_lines) key << "|w" << line;
        for (auto& line : panel.text_lines) key << "|t" << line;
        if (item.box.width <= 0 || item.box.height <= 0) continue;
        if (!have_bounds)
        {
            bounds = item.box;
            have_bounds = true;
        } else
        {
            const double x = std::min(bounds.x, item.box.x), y = std::min(bounds.y, item.box.y);
            const double x2 = std::max(bounds.x + bounds.width, item.box.x + item.box.width);
            const double y2 = std::max(bounds.y + bounds.height, item.box.y + item.box.height);
            bounds = {x, y, x2 - x, y2 - y};
        }
    }

    auto next_signature = key.str();
    if (next_signature == signature) return;
    wf::scene::damage_node(this, box);
    signature = std::move(next_signature);
    box = have_bounds ? bounds : wf::geometry_t{0, 0, 0, 0};
    texture.reset();
    pixels.clear();
    pixel_width = pixel_height = 0;
    if (have_bounds)
    {
        const double scale = std::max(1.0, output_scale);
        pixel_width = std::max(1, int(std::ceil(box.width * scale)));
        pixel_height = std::max(1, int(std::ceil(box.height * scale)));
        pixels.assign(size_t(pixel_width) * pixel_height * 4, 0);
        auto surface = cairo_image_surface_create_for_data(pixels.data(), CAIRO_FORMAT_ARGB32,
            pixel_width, pixel_height, pixel_width * 4);
        auto cr = cairo_create(surface);
        cairo_scale(cr, double(pixel_width) / box.width, double(pixel_height) / box.height);
        const std::string family = font_family(palette);
        for (const auto& item : items)
        {
            if (item.box.width <= 0 || item.box.height <= 0) continue;
            const double x = item.box.x - box.x, y = item.box.y - box.y;
            if (item.kind == note_overlay_item_t::kind_t::dot)
            {
                const double radius = std::min(item.box.width, item.box.height) / 2.0;
                cairo_arc(cr, x + item.box.width / 2, y + item.box.height / 2, radius - .5, 0, 2 * M_PI);
                cairo_set_source_rgb(cr, palette.accent.r, palette.accent.g, palette.accent.b);
                cairo_fill_preserve(cr);
                cairo_set_source_rgb(cr, palette.background.r, palette.background.g, palette.background.b);
                cairo_set_line_width(cr, 1 * std::clamp(palette.text_scale, 0.5, 3.0));
                cairo_stroke(cr);
            } else if (item.kind == note_overlay_item_t::kind_t::marker)
            {
                rounded_rect(cr, x, y, item.box.width, item.box.height, item.box.height / 2.0);
                cairo_set_source_rgb(cr, palette.accent.r, palette.accent.g, palette.accent.b);
                cairo_fill(cr);
                cairo_select_font_face(cr, family.c_str(), CAIRO_FONT_SLANT_NORMAL, CAIRO_FONT_WEIGHT_NORMAL);
                cairo_set_font_size(cr, 8 * std::clamp(palette.text_scale, 0.5, 3.0));
                cairo_text_extents_t ext{};
                cairo_text_extents(cr, item.label.c_str(), &ext);
                cairo_move_to(cr, x + (item.box.width - ext.width) / 2 - ext.x_bearing,
                    y + (item.box.height - ext.height) / 2 - ext.y_bearing);
                cairo_set_source_rgb(cr, palette.background.r, palette.background.g, palette.background.b);
                cairo_show_text(cr, item.label.c_str());
            } else
            {
                const auto& layout = item.panel;
                if (!layout.drawable) continue;
                rounded_rect(cr, x, y, item.box.width, item.box.height, layout.radius);
                cairo_set_source_rgb(cr, palette.background.r, palette.background.g, palette.background.b);
                cairo_fill_preserve(cr);
                cairo_set_source_rgb(cr, palette.accent.r, palette.accent.g, palette.accent.b);
                cairo_set_line_width(cr, layout.border);
                cairo_stroke(cr);

                double baseline = y + layout.border + layout.padding;
                if (layout.show_writer)
                {
                    cairo_select_font_face(cr, family.c_str(), CAIRO_FONT_SLANT_NORMAL, CAIRO_FONT_WEIGHT_NORMAL);
                    cairo_set_font_size(cr, layout.writer_font);
                    cairo_font_extents_t ext{};
                    cairo_font_extents(cr, &ext);
                    for (const auto& line : layout.writer_lines)
                    {
                        baseline += ext.ascent;
                        cairo_move_to(cr, x + layout.border + layout.padding, baseline);
                        cairo_set_source_rgba(cr, palette.foreground.r, palette.foreground.g, palette.foreground.b, .7);
                        cairo_show_text(cr, line.c_str());
                        baseline += layout.writer_line_height - ext.ascent;
                    }

                    baseline += layout.writer_gap;
                }

                cairo_select_font_face(cr, family.c_str(), CAIRO_FONT_SLANT_NORMAL, CAIRO_FONT_WEIGHT_NORMAL);
                cairo_set_font_size(cr, layout.text_font);
                cairo_font_extents_t ext{};
                cairo_font_extents(cr, &ext);
                cairo_set_source_rgb(cr, palette.foreground.r, palette.foreground.g, palette.foreground.b);
                for (const auto& line : layout.text_lines)
                {
                    baseline += ext.ascent;
                    cairo_move_to(cr, x + layout.border + layout.padding, baseline);
                    cairo_show_text(cr, line.c_str());
                    baseline += layout.text_line_height - ext.ascent;
                }
            }
        }

        cairo_destroy(cr);
        cairo_surface_flush(surface);
        cairo_surface_destroy(surface);
    }

    wf::scene::damage_node(this, box);
    wf::scene::update(shared_from_this(), wf::scene::update_flag::GEOMETRY);
}

class note_overlay_render : public wf::scene::simple_render_instance_t<note_overlay_node_t>
{
  public:
    using simple_render_instance_t::simple_render_instance_t;
    void render(const wf::scene::render_instruction_t& data) override
    {
        if (!self->texture && !self->pixels.empty())
        {
            auto texture = wlr_texture_from_pixels(data.pass->get_wlr_renderer(), DRM_FORMAT_ARGB8888,
                self->pixel_width * 4, self->pixel_width, self->pixel_height, self->pixels.data());
            if (texture) self->texture = wf::texture_t::from_texture(texture);
        }

        if (self->texture && self->box.width > 0 && self->box.height > 0)
            data.pass->add_texture(self->texture, data.target, self->box, data.damage, 1);
    }
};

void note_overlay_node_t::gen_render_instances(std::vector<wf::scene::render_instance_uptr>& instances,
    wf::scene::damage_callback damage, wf::output_t *output)
{
    instances.push_back(std::make_unique<note_overlay_render>(this, damage, output));
}
} // namespace scottland::windowing
