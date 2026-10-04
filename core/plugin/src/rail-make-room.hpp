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
// the rail is truly full. P14 (Mike, 2026-10-04, "the user always wins"): the dropped item
// ends up exactly where the user put it; only the other widgets move around it, and the
// order around it is decided by where it was placed.
//
// The item never moves, so it splits the widgets into those above it and those below it.
// For a given split each side is independent: its widgets must stay in order, 1 px apart (or
// no further into each other than they already are), between a rail end and the item, and as
// close to home as possible. That is an isotonic regression on gap-adjusted positions with
// bounds, solved exactly by pool-adjacent-violators in O(n).
//
// Which split: where the item was placed decides. A widget whose span the item's center is
// past stays on that side (the center below a widget puts it above, and the reverse). A
// widget the item's center is on (the user dropped onto it) may go either way, since both
// orders match the placement; of those splits, the ones that fit compete on total movement.
// If none fits, the widgets overlap the item rather than it moving or the aimed order being
// broken: on a rail that is truly full, or where the item clips a widget pinned at a rail
// end with its center past that widget (at most about half the item). Touching within the
// 1 px contact gap is not overlap. With n capped at 256 a solve is O(n²).
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
        result = status_t::clear;
        if (!valid_input || !std::isfinite(drag_lo) || !std::isfinite(drag_hi) || !std::isfinite(top) ||
            !std::isfinite(bottom) || drag_hi < drag_lo || bottom < top)
        {
            return invalid_result();
        }

        // The item lands on the rail: if it hangs off an end, lay out for where placement puts
        // it, so the gap opens exactly where it will land and every position below moves at
        // most 1 px per pixel of drag (P11).
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
        size_t natural = 0;  // the split by centers: the tie-break, all else equal
        while (natural < count && home[natural] + height[natural] * 0.5 < drag_center) ++natural;

        // The splits the placement allows: every widget the center is past keeps its side.
        // P11: a widget that was on the other side keeps its way until the center is its
        // direction margin past its edge, so a small re-pause at an edge can't flip it back
        // and forth across the item.
        size_t first = 0, last = count;
        for (size_t k = 0; k < count; ++k)
        {
            const double margin = std::max(direction_margin, 0.10 * height[k]);
            const bool was_below = split <= count && k >= split, was_above = split <= count && k < split;
            if (home[k] + height[k] < drag_center - (was_below ? margin : 0)) first = std::max(first, k + 1);
            if (home[k] > drag_center + (was_above ? margin : 0)) last = std::min(last, k);
        }
        if (first > last) first = last = natural;  // nested widgets disagree: by centers

        // Among the allowed splits, the least total movement that fits; if none fits, the
        // least overlap with the item (ties: the split by centers).
        size_t best = count + 1;
        double best_cost = 0, best_overlap = 0;
        for (size_t k = first; k <= last; ++k)
        {
            const double over = overlap(k, drag_lo, drag_hi, top, bottom);
            const bool fits = over <= EPSILON;
            const bool best_fits = best <= count && best_overlap <= EPSILON;
            double cost = 0;
            if (fits) cost = evaluate(k, drag_lo, drag_hi, top, bottom);
            bool better;
            if (best > count) better = true;
            else if (fits != best_fits) better = fits;
            else if (fits) better = cost < best_cost - EPSILON ||
                (cost <= best_cost + EPSILON && distance(k, natural) < distance(best, natural));
            else better = over < best_overlap - CONTACT ||
                (over <= best_overlap + CONTACT && distance(k, natural) < distance(best, natural));
            if (better)
            {
                best = k;
                best_cost = cost;
                best_overlap = over;
            }
        }

        // P11 hysteresis ("a window keeps its current way out of the way until it stops
        // working"): the previous split stands while the placement allows it and it fits as
        // well, unless every widget it would move across the item has had its center passed
        // by its direction margin toward its new side. Near-equal alternatives can't trade
        // places on small re-pauses.
        if (split <= count && split != best && split >= first && split <= last)
        {
            bool passed = true;
            double slack = direction_margin;
            for (size_t k = std::min(split, best); k < std::max(split, best); ++k)
            {
                const double margin = std::max(direction_margin, 0.10 * height[k]);
                const double center = home[k] + height[k] * 0.5;
                passed &= best > split ? drag_center > center + margin : drag_center < center - margin;
                slack = std::max(slack, margin);
            }
            // On a full rail it still works while it overlaps no more than that margin extra.
            // On a nearly full one, where the alternative fits only within that margin, it
            // still works while it overlaps by no more than the margin: a widget doesn't fly
            // across the item and back for a sub-pixel fit (review round 4 fuzz witness).
            const double over = overlap(split, drag_lo, drag_hi, top, bottom);
            const bool fragile = overlap(best, drag_lo - slack, drag_hi + slack, top, bottom) > EPSILON;
            const bool works = best_overlap <= EPSILON ? over <= EPSILON || (fragile && over <= slack) :
                over <= best_overlap + slack;
            // A change by choice must also fit with that margin to spare: a split that just
            // stopped fitting (and forced the change) can't come straight back a pixel later.
            passed = passed && !fragile;
            if (works && !passed) best = split;
        }

        const double over = overlap(best, drag_lo, drag_hi, top, bottom);
        evaluate(best, drag_lo, drag_hi, top, bottom);
        split = best;

        for (size_t k = 0; k < count; ++k)
        {
            offsets[order[k]] = placed[k] - home[k];
            directions[order[k]] = k < best ? -1 : 1;
        }

        result = over > EPSILON ? status_t::overlap : status_t::clear;
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
        ready = false;
        skipped = false;
        result = status_t::skipped;
    }

    static size_t distance(size_t a, size_t b) { return a > b ? a - b : b - a; }

    status_t invalid_result()
    {
        std::fill(offsets.begin(), offsets.end(), 0.0);
        result = status_t::overlap;
        return result;
    }

    // How far split k's widgets reach into the item if packed tight against the rail ends:
    // `above` into its top edge, `below` into its bottom edge, both including the 1 px
    // contact gap (so up to CONTACT on a side is touching, not overlap).
    void reach_into(size_t k, double lo, double hi, double top, double bottom, double& above,
        double& below) const
    {
        above = k ? std::max(0.0, top + reach_above[k] + CONTACT - lo) : 0.0;
        below = k < original.size() ? std::max(0.0, hi + CONTACT + reach_below[k] - bottom) : 0.0;
    }

    // How far split k's widgets must overlap the item (0 when it fits, touching included).
    double overlap(size_t k, double lo, double hi, double top, double bottom) const
    {
        double above, below;
        reach_into(k, lo, hi, top, bottom, above, below);
        return std::max(0.0, above - CONTACT) + std::max(0.0, below - CONTACT);
    }

    // Places split k around the item at [lo, hi], which never moves: each side as close to
    // home as possible, reaching into the item only as far as it must. Leaves the placement
    // in `placed` and returns the total squared movement.
    double evaluate(size_t k, double lo, double hi, double top, double bottom)
    {
        double above, below;
        reach_into(k, lo, hi, top, bottom, above, below);
        return place(0, k, top, lo - CONTACT + above) +
            place(k, original.size(), hi + CONTACT - below, bottom);
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
        return true;
    }
};
}
