#include "shrink.hpp"
#include <algorithm>
#include <cmath>
#include <set>

namespace scottland::work
{
shrink_job_t::shrink_job_t(shrink_snapshot_t snapshot, uint64_t cap_units) : s(std::move(snapshot)), cap(cap_units)
{
    out.reserve(s.rects.size());
}

size_t shrink_job_t::snapshot_bytes(const shrink_snapshot_t& snapshot)
{
    size_t bytes = sizeof(snapshot) + snapshot.sources.size() * sizeof(goo::source_t) + snapshot.rects.size() * sizeof(rect_t);
    std::set<const goo::shape_t*> shapes;
    for (auto& source : snapshot.sources)
        if (source.shape && shapes.insert(source.shape.get()).second) bytes += source.shape->pixels.size();
    return bytes;
}

bool shrink_job_t::step(cancel_t& cancel)
{
    const uint64_t terms = std::max<size_t>(1, s.sources.size());
    const float wet = .5f * s.settings.threshold();
    const double padding = 5 + 1. / s.output_scale;
    const double reach = 4 * s.settings.reach + padding;
    auto charge = [&] (uint64_t units)
    {
        if (spent + units > cap)
        {
            capped = true;
            return false;
        }
        if (!cancel.charge(units)) return false;
        spent += units;
        return true;
    };
    while (index < s.rects.size())
    {
        auto& b = s.rects[index];
        double bx2 = b.x + b.width, by2 = b.y + b.height;
        if (!row_started)
        {
            if (!charge(terms)) return capped;  // the mask test, one unit per source
            bool masked = false;
            for (auto& source : s.sources)
            {
                if (!source.shape) continue;
                auto body = source.shape_body.z > 0 && source.shape_body.w > 0 ? source.shape_body : source.rect;
                if (body.x - body.z - reach < bx2 && body.x + body.z + reach > b.x &&
                    body.y - body.w - reach < by2 && body.y + body.w + reach > b.y)
                {
                    masked = true;
                    break;
                }
            }
            const double fine = masked ? 2 : 4, coarse = 4 * fine;
            step_x = b.width > 2 * b.height ? coarse : fine;
            step_y = b.height > 2 * b.width ? coarse : fine;
            y = b.y;
            x = b.x;
            x1 = y1 = 1e9;
            x2 = y2 = -1e9;
            row_started = true;
        }
        for (; y < by2 + step_y; y += step_y, x = b.x)
        {
            for (; x < bx2 + step_x; x += step_x)
            {
                if (!charge(terms)) return capped;  // resumes at this sample
                glm::vec2 point{std::min<double>(x, bx2), std::min<double>(y, by2)};
                if (goo::density(point, s.sources, s.settings, s.time, 1) < wet) continue;
                x1 = std::min<double>(x1, point.x);
                y1 = std::min<double>(y1, point.y);
                x2 = std::max<double>(x2, point.x);
                y2 = std::max<double>(y2, point.y);
            }
        }
        if (x2 >= x1)
        {
            double tx1 = std::max<double>(std::floor(x1 - step_x - padding), b.x);
            double ty1 = std::max<double>(std::floor(y1 - step_y - padding), b.y);
            double tx2 = std::min<double>(std::ceil(x2 + step_x + padding), bx2);
            double ty2 = std::min<double>(std::ceil(y2 + step_y + padding), by2);
            out.push_back({tx1, ty1, tx2 - tx1, ty2 - ty1});
        } else
        {
            out.push_back(b);  // nothing sampled wet: keep the whole band, never drop goo
        }
        refined++;
        index++;
        row_started = false;
    }
    return true;
}

std::unique_ptr<result_t> shrink_job_t::result(outcome_t& outcome)
{
    auto r = std::make_unique<shrink_result_t>();
    r->incarnation = s.incarnation;
    r->refined = refined;
    r->rects = out;
    // Capped: every rectangle not fully refined keeps its loose extent.
    for (size_t i = out.size(); i < s.rects.size(); i++) r->rects.push_back(s.rects[i]);
    outcome = capped ? outcome_t::capped : outcome_t::finished;
    return r;
}
}
