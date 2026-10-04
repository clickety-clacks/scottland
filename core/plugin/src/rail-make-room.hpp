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

// WG26 (Mike, 2026-10-04): making room on a rail is a spread over the whole rail. Any widget
// may move when that's what it takes, widgets keep their order, total movement is the least
// it can be (squared displacement from where each widget is), and widgets overlap only when
// the rail is truly full.
//
// The dragged item splits the widgets into those above it and those below it. For a given
// split each side is independent: its widgets must stay in order, 1 px apart (or no further
// into each other than they already are), between a rail end and the item, and as close to
// home as possible. That is an isotonic regression on gap-adjusted positions with bounds,
// solved exactly by pool-adjacent-violators in O(n). If a side can't fit at all, the item
// itself settles toward the side with room by the minimum amount (counted as movement too).
// Every split is tried and the cheapest wins, so a widget crosses the item only when that
// is the least movement; the whole solve is O(n²) with n capped at 256.
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
        offsets.assign(count, 0.0);
        order.reserve(count);
        for (size_t i = 0; i < count; ++i) order.push_back(i);
        std::stable_sort(order.begin(), order.end(), [&] (size_t a, size_t b)
        {
            return original[a].lo < original[b].lo ||
                (original[a].lo == original[b].lo && original[a].id < original[b].id);
        });

        // Rail order, with each widget's required distance to the next: 1 px of clearance,
        // or the overlap it already has (an earlier full rail), never pulled further apart.
        height.resize(count);
        home.resize(count);
        prefix.resize(count + 1);
        reach_above.resize(count + 1);
        reach_below.resize(count + 1);
        placed.resize(count);
        upper.resize(count);
        blocks.reserve(count);
        valid_input = true;
        for (size_t k = 0; k < count; ++k)
        {
            const auto& item = original[order[k]];
            valid_input &= std::isfinite(item.lo) && std::isfinite(item.hi) && item.hi >= item.lo;
            height[k] = item.hi - item.lo;
            home[k] = item.lo;
        }

        prefix[0] = 0;
        for (size_t k = 0; k + 1 < count; ++k)
        {
            const double gap = home[k + 1] - (home[k] + height[k]);
            prefix[k + 1] = prefix[k] + height[k] + std::min(CONTACT, gap);
        }
        if (count) prefix[count] = prefix[count - 1] + height[count - 1];

        // reach_above[k]: how far below the first widget's top the packed widgets 0..k-1
        // extend; reach_below[k]: the same for widgets k..n-1 measured from widget k's top.
        reach_above[0] = 0;
        double extent = -std::numeric_limits<double>::infinity();
        for (size_t k = 0; k < count; ++k)
        {
            extent = std::max(extent, prefix[k] + height[k]);
            reach_above[k + 1] = extent;
        }
        reach_below[count] = 0;
        extent = -std::numeric_limits<double>::infinity();
        for (size_t k = count; k-- > 0;)
        {
            extent = std::max(extent, prefix[k] + height[k]);
            reach_below[k] = extent - prefix[k];
        }

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
        settle = 0;
        result = status_t::clear;
        if (!valid_input || !std::isfinite(drag_lo) || !std::isfinite(drag_hi) || !std::isfinite(top) ||
            !std::isfinite(bottom) || drag_hi < drag_lo || bottom < top)
        {
            return invalid_result();
        }

        // The item lands on the rail: lay out for it moved onto the rail if it hangs off an
        // end (that's where placement puts it), so every position below moves at most 1 px
        // per pixel of drag (P11). Its settle is reported from where it actually is.
        const double given_lo = drag_lo;
        if (drag_hi - drag_lo >= bottom - top)
        {
            drag_lo = top;
            drag_hi = bottom;
        } else if (drag_lo < top)
        {
            drag_hi += top - drag_lo;
            drag_lo = top;
        } else if (drag_hi > bottom)
        {
            drag_lo -= drag_hi - bottom;
            drag_hi = bottom;
        }

        const size_t count = original.size();
        const double drag_center = (drag_lo + drag_hi) * 0.5;
        size_t natural = 0;  // the split by centers: what the drop says, all else equal
        while (natural < count && home[natural] + height[natural] * 0.5 < drag_center) ++natural;

        size_t best = count + 1;
        double best_cost = 0;
        for (size_t k = 0; k <= count; ++k)
        {
            double cost;
            if (!evaluate(k, drag_lo, drag_hi, top, bottom, 0, cost, false)) continue;
            if (best > count || cost < best_cost - EPSILON || (cost <= best_cost + EPSILON &&
                distance(k, natural) < distance(best, natural)))
            {
                best = k;
                best_cost = cost;
            }
        }

        // The rail is truly full for every split: take the split that needs the least
        // overlap and let the widgets encroach on the item by exactly that, shared evenly at
        // its two edges. The needed overlap grows continuously from zero as the rail fills.
        const bool full = best > count;
        double overlap = 0;
        if (full)
        {
            overlap = std::numeric_limits<double>::infinity();
            for (size_t k = 0; k <= count; ++k)
            {
                const double need = squeeze(k, drag_lo, drag_hi, top, bottom);
                if (need < overlap - EPSILON || (need <= overlap + EPSILON &&
                    distance(k, natural) < distance(best, natural)))
                {
                    overlap = need;
                    best = k;
                }
            }
        }

        // P11 hysteresis: the split (which widgets are above the item) keeps its previous
        // value until the item's center has moved a direction margin from where it last
        // changed, as long as it still fits as well. Small re-pauses can't flip a widget
        // across the item and back.
        if (split <= count && split != best)
        {
            double margin = direction_margin;
            for (size_t k = std::min(split, best); k < std::max(split, best); ++k)
                margin = std::max(margin, 0.10 * height[k]);
            double unused;
            const bool fits = full ? squeeze(split, drag_lo, drag_hi, top, bottom) <= overlap + EPSILON :
                evaluate(split, drag_lo, drag_hi, top, bottom, 0, unused, false);
            if (std::abs(drag_center - anchor) < margin && fits) best = split;
        }

        double cost;
        if (!evaluate(best, drag_lo, drag_hi, top, bottom, overlap, cost, true))
        {
            // Not even the least-overlapping split can be placed: leave every widget where
            // it is.
            return invalid_result();
        }

        settle += drag_lo - given_lo;
        if (best != split)
        {
            split = best;
            anchor = drag_center;
        }

        for (size_t k = 0; k < count; ++k)
        {
            offsets[order[k]] = placed[k] - home[k];
            directions[order[k]] = k < best ? -1 : 1;
        }

        result = full ? status_t::overlap : status_t::clear;
        if (!validate(top, bottom))
        {
            std::fill(offsets.begin(), offsets.end(), 0.0);
            settle = 0;
            result = status_t::overlap;
        }
        return result;
    }

    const std::vector<interval_t>& actors() const { return original; }
    const std::vector<int8_t>& latched_directions() const { return directions; }
    const std::vector<double>& shifts() const { return offsets; }
    // How far the dragged item itself should settle (0 unless a side couldn't fit).
    double item_shift() const { return settle; }
    status_t status() const { return result; }
    bool is_ready() const { return ready; }
    bool is_skipped() const { return skipped; }

  private:
    struct block_t
    {
        double sum = 0, cap = 0, value = 0;
        size_t first = 0, count = 0;
    };

    std::vector<interval_t> original;
    std::vector<size_t> order;
    std::vector<int8_t> directions;
    std::vector<double> offsets, height, home, prefix, reach_above, reach_below;
    std::vector<double> placed, upper;
    std::vector<block_t> blocks;
    size_t split = std::numeric_limits<size_t>::max();
    double anchor = 0, settle = 0;
    bool ready = false, skipped = false, valid_input = true;
    status_t result = status_t::skipped;

    void clear()
    {
        original.clear();
        order.clear();
        directions.clear();
        offsets.clear();
        height.clear();
        home.clear();
        prefix.clear();
        reach_above.clear();
        reach_below.clear();
        placed.clear();
        upper.clear();
        blocks.clear();
        split = std::numeric_limits<size_t>::max();
        anchor = settle = 0;
        ready = false;
        skipped = false;
        result = status_t::skipped;
    }

    static size_t distance(size_t a, size_t b) { return a > b ? a - b : b - a; }

    status_t invalid_result()
    {
        std::fill(offsets.begin(), offsets.end(), 0.0);
        settle = 0;
        result = status_t::overlap;
        return result;
    }

    // What split k asks of the item's settle (negative = up): the cards above need it at
    // least `cards_need` down, the cards below allow at most `cards_room`; it stays on the
    // rail (`rail_need`, `rail_room`).
    void settle_terms(size_t k, double lo, double hi, double top, double bottom, double& cards_need,
        double& cards_room, double& rail_need, double& rail_room) const
    {
        const size_t count = original.size();
        const double inf = std::numeric_limits<double>::infinity();
        cards_need = k ? top + reach_above[k] + CONTACT - lo : -inf;
        cards_room = k < count ? bottom - reach_below[k] - CONTACT - hi : inf;
        rail_need = top - lo;
        rail_room = bottom - hi;
    }

    // The least overlap with the item that lets split k fit (0 when it fits as is).
    double squeeze(size_t k, double lo, double hi, double top, double bottom) const
    {
        double cards_need, cards_room, rail_need, rail_room;
        settle_terms(k, lo, hi, top, bottom, cards_need, cards_room, rail_need, rail_room);
        return std::max({0.0, cards_need - cards_room, 2 * (cards_need - rail_room),
            2 * (rail_need - cards_room)});
    }

    // Places split k with the item at [lo, hi], letting the widgets encroach `overlap` on it
    // (half at each edge) and settling it the least that fits. Returns false if it can't
    // fit; otherwise the total squared movement (widgets and item). `keep` stores the
    // placement and the item's settle.
    bool evaluate(size_t k, double lo, double hi, double top, double bottom, double overlap,
        double& cost, bool keep)
    {
        double cards_need, cards_room, rail_need, rail_room;
        settle_terms(k, lo, hi, top, bottom, cards_need, cards_room, rail_need, rail_room);
        const double need = std::max(cards_need - overlap * 0.5, rail_need);
        const double room = std::min(cards_room + overlap * 0.5, rail_room);
        if (need > room + EPSILON) return false;
        const double shift = std::clamp(0.0, need, std::max(need, room));
        cost = shift * shift;
        cost += place(0, k, top, lo + shift - CONTACT + overlap * 0.5);
        cost += place(k, original.size(), hi + shift + CONTACT - overlap * 0.5, bottom);
        if (keep) settle = shift;
        return true;
    }

    // Widgets a..b-1 in rail order, between `low` (first top) and `high` (every bottom), in
    // order and as close to home as possible: bounded isotonic regression on z = y - s,
    // where s is each widget's packed offset from widget a. Writes `placed`, returns the
    // squared movement.
    double place(size_t a, size_t b, double low, double high)
    {
        if (a >= b) return 0;
        // z is non-decreasing, so every widget's own "bottom ≤ high" bound becomes the
        // tightest bound of it and all widgets after it.
        double cap = std::numeric_limits<double>::infinity();
        for (size_t k = b; k-- > a;)
        {
            cap = std::min(cap, high - height[k] - (prefix[k] - prefix[a]));
            upper[k] = cap;
        }

        blocks.clear();
        for (size_t k = a; k < b; ++k)
        {
            block_t block;
            block.sum = home[k] - (prefix[k] - prefix[a]);
            block.count = 1;
            block.first = k;
            block.cap = upper[k];
            block.value = std::clamp(block.sum, low, std::max(low, block.cap));
            while (!blocks.empty() && blocks.back().value > block.value)
            {
                auto& merged = blocks.back();
                merged.sum += block.sum;
                merged.count += block.count;
                // upper[] is non-decreasing, so the block's tightest bound is its first.
                merged.value = std::clamp(merged.sum / merged.count, low, std::max(low, merged.cap));
                block = merged;
                blocks.pop_back();
            }
            blocks.push_back(block);
        }

        double cost = 0;
        for (const auto& block : blocks)
        {
            for (size_t k = block.first; k < block.first + block.count; ++k)
            {
                placed[k] = block.value + (prefix[k] - prefix[a]);
                const double moved = placed[k] - home[k];
                cost += moved * moved;
            }
        }
        return cost;
    }

    bool validate(double top, double bottom) const
    {
        for (size_t k = 0; k < original.size(); ++k)
        {
            const auto& item = original[order[k]];
            const double moved = offsets[order[k]];
            if (!std::isfinite(moved) || item.lo + moved < top - 1e-6 || item.hi + moved > bottom + 1e-6)
            {
                return false;
            }
            if (k && item.lo + moved < original[order[k - 1]].lo + offsets[order[k - 1]] - 1e-6)
            {
                return false;
            }
        }
        return std::isfinite(settle);
    }
};
}
