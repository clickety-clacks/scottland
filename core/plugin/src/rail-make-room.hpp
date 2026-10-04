#pragma once

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <vector>

namespace scottland::rail
{
// WG26's rail solve is deliberately small and synchronous. The cap is checked before
// copying, sorting, or allocating any per-actor state.
static constexpr size_t MAX_ACTORS = 256;
static constexpr double CONTACT = 1.0;
static constexpr double DIRECTION_MARGIN = 6.0;
static constexpr double EPSILON = 1e-9;

struct interval_t
{
    uint64_t id = 0;
    double lo = 0, hi = 0;
};

enum class status_t { clear, overlap, skipped };

class solver_t
{
  public:
    template<class Range>
    bool begin(const Range& source)
    {
        // Do not even ask for an iterator on the over-cap path. A caller may provide a
        // cheap count-only range when the desktop contains more actors than WG26 considers.
        if (source.size() > MAX_ACTORS)
        {
            ready = false;
            skipped = true;
            result = status_t::skipped;
            return false;
        }

        clear();
        original.reserve(source.size());
        for (const auto& item : source)
        {
            original.push_back(item);
        }

        const size_t count = original.size();
        directions.assign(count, 0);
        previous.assign(count, 0);
        fallback.assign(count, 0);
        offsets.assign(count, 0.0);
        slack.resize(count);
        chain.reserve(count);
        down_order.reserve(count);
        up_order.reserve(count);
        for (size_t i = 0; i < count; ++i)
        {
            down_order.push_back(i);
            up_order.push_back(i);
        }
        std::stable_sort(down_order.begin(), down_order.end(), [&] (size_t a, size_t b)
        {
            return original[a].lo < original[b].lo ||
                (original[a].lo == original[b].lo && original[a].id < original[b].id);
        });
        std::stable_sort(up_order.begin(), up_order.end(), [&] (size_t a, size_t b)
        {
            return original[a].hi > original[b].hi ||
                (original[a].hi == original[b].hi && original[a].id < original[b].id);
        });
        ready = true;
        skipped = false;
        result = status_t::clear;
        return true;
    }

    status_t solve(double drag_lo, double drag_hi, double top, double bottom,
        double direction_margin = DIRECTION_MARGIN)
    {
        if (!ready || skipped)
        {
            result = status_t::skipped;
            return result;
        }

        std::fill(offsets.begin(), offsets.end(), 0.0);
        result = status_t::clear;
        if (!std::isfinite(drag_lo) || !std::isfinite(drag_hi) || !std::isfinite(top) ||
            !std::isfinite(bottom) || drag_hi < drag_lo || bottom < top)
        {
            return invalid_result();
        }

        const double drag_center = (drag_lo + drag_hi) * 0.5;
        previous = directions;
        for (size_t i = 0; i < original.size(); ++i)
        {
            const auto& item = original[i];
            if (!std::isfinite(item.lo) || !std::isfinite(item.hi) || item.hi < item.lo)
            {
                return invalid_result();
            }

            const double center = (item.lo + item.hi) * 0.5;
            const double margin = std::max(direction_margin, 0.10 * (item.hi - item.lo));
            int8_t& side = directions[i];
            if (!side)
            {
                side = center >= drag_center ? 1 : -1;
            } else if (side < 0 && drag_center < center - margin)
            {
                side = 1;
            } else if (side > 0 && drag_center > center + margin)
            {
                side = -1;
            }
        }

        push_both(drag_lo, drag_hi, top, bottom);
        // Falling short only by the contact gap means touching, not overlapping: no card
        // jumps across for that.
        auto overlapping = [] (double shortfall) { return shortfall > CONTACT + EPSILON; };
        if (overlapping(short_down) || overlapping(short_up))
        {
            // A chain pinned against its rail end cannot make room, but the other side may
            // have space: the cards nearest the drag yield that way instead, one at a time,
            // until both sides fit. Order among the neighbors is kept. A card yields only
            // if the drag is on it (its center within the card, so either order matches
            // where the user put it) or it was already on that side (P11: it keeps its way
            // while that works). A card the drag merely clips at its far edge stays: it
            // doesn't fly across the drag for a few pixels. Otherwise overlap is left only
            // when neither way fits: the rail is truly full.
            const int8_t from = short_down > short_up ? 1 : -1;
            fallback = directions;
            bool fitted = false;
            const auto& order = from > 0 ? down_order : up_order;
            for (size_t i : order)
            {
                if (directions[i] != from) continue;
                if (previous[i] != -from &&
                    (drag_center < original[i].lo || drag_center > original[i].hi)) break;
                directions[i] = -from;
                push_both(drag_lo, drag_hi, top, bottom);
                if (!overlapping(short_down) && !overlapping(short_up))
                {
                    fitted = true;
                    break;
                }

                if (overlapping(from > 0 ? short_up : short_down)) break;  // the other side is full too
            }

            if (!fitted)
            {
                directions = fallback;
                push_both(drag_lo, drag_hi, top, bottom);
            }
        }

        if (!validate(top, bottom))
        {
            std::fill(offsets.begin(), offsets.end(), 0.0);
            result = status_t::overlap;
        }
        return result;
    }

    const std::vector<interval_t>& actors() const { return original; }
    const std::vector<int8_t>& latched_directions() const { return directions; }
    const std::vector<double>& shifts() const { return offsets; }
    status_t status() const { return result; }
    bool is_ready() const { return ready; }
    bool is_skipped() const { return skipped; }

  private:
    std::vector<interval_t> original;
    std::vector<size_t> down_order, up_order, chain;
    std::vector<int8_t> directions, previous, fallback;
    std::vector<double> offsets, slack;
    bool ready = false, skipped = false;
    double short_down = 0, short_up = 0;
    status_t result = status_t::skipped;

    void clear()
    {
        original.clear();
        down_order.clear();
        up_order.clear();
        chain.clear();
        directions.clear();
        previous.clear();
        fallback.clear();
        offsets.clear();
        slack.clear();
        ready = false;
        skipped = false;
        result = status_t::skipped;
    }

    status_t invalid_result()
    {
        std::fill(offsets.begin(), offsets.end(), 0.0);
        result = status_t::overlap;
        return result;
    }

    double mirrored_lo(size_t index, double sign) const
    {
        return sign > 0 ? original[index].lo : -original[index].hi;
    }

    double mirrored_hi(size_t index, double sign) const
    {
        return sign > 0 ? original[index].hi : -original[index].lo;
    }

    void push_both(double drag_lo, double drag_hi, double top, double bottom)
    {
        std::fill(offsets.begin(), offsets.end(), 0.0);
        chain.clear();
        for (size_t i : down_order)
        {
            if (directions[i] > 0) chain.push_back(i);
        }
        short_down = push_chain(drag_hi, bottom, 1.0);

        chain.clear();
        for (size_t i : up_order)
        {
            if (directions[i] < 0) chain.push_back(i);
        }
        short_up = push_chain(-drag_lo, -top, -1.0);
        result = short_down > 0 || short_up > 0 ? status_t::overlap : status_t::clear;
    }

    // Returns how far the chain fell short of clearing the drag (0 when it clears).
    double push_chain(double drag_end, double wall, double sign)
    {
        if (chain.empty()) return 0.0;

        const double first_lo = mirrored_lo(chain.front(), sign);
        const double requested = std::max(0.0, drag_end + CONTACT - first_lo);
        double accumulated_slack = 0.0;
        double capacity = std::numeric_limits<double>::infinity();
        for (size_t k = 0; k < chain.size(); ++k)
        {
            const size_t index = chain[k];
            if (k)
            {
                const double gap = mirrored_lo(index, sign) - mirrored_hi(chain[k - 1], sign);
                accumulated_slack += gap - std::min(CONTACT, gap);
            }
            slack[k] = accumulated_slack;
            capacity = std::min(capacity, wall - mirrored_hi(index, sign) + accumulated_slack);
        }

        const double first = std::min(requested, std::max(0.0, capacity));
        for (size_t k = 0; k < chain.size(); ++k)
        {
            offsets[chain[k]] = sign * std::max(0.0, first - slack[k]);
        }
        return first + EPSILON < requested ? requested - first : 0.0;
    }

    bool validate(double top, double bottom) const
    {
        for (size_t i = 0; i < original.size(); ++i)
        {
            const double lo = original[i].lo + offsets[i];
            const double hi = original[i].hi + offsets[i];
            if (!std::isfinite(offsets[i]) || lo < top - EPSILON || hi > bottom + EPSILON)
            {
                return false;
            }
        }

        for (size_t k = 1; k < down_order.size(); ++k)
        {
            const size_t a = down_order[k - 1], b = down_order[k];
            if (directions[a] > 0 && directions[b] > 0 &&
                original[b].lo + offsets[b] < original[a].lo + offsets[a] - EPSILON)
            {
                return false;
            }
        }
        for (size_t k = 1; k < up_order.size(); ++k)
        {
            const size_t a = up_order[k - 1], b = up_order[k];
            if (directions[a] < 0 && directions[b] < 0 &&
                original[b].hi + offsets[b] > original[a].hi + offsets[a] + EPSILON)
            {
                return false;
            }
        }
        return true;
    }
};
}
