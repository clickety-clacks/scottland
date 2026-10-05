#include "pairing.hpp"
#include <algorithm>
namespace scottland::windowing
{
pair_layout fit_pair(pair_size left, pair_size right, rectangle area, double gap, double padding)
{
    pair_layout out;
    double total = std::max(0.0, left.width) + std::max(0.0, right.width);
    double room = area.width;
    gap = std::max(0.0, gap); padding = std::max(0.0, padding);
    if (total + gap + 2 * padding <= room) out.gap = gap;
    else if (total + 2 * padding <= room) out.gap = room - 2 * padding - total; // the gap gives way first
    else if (total > room && total > 0) out.scale = std::max(pair_min_scale, room / total);
    // (between those, the padding gives way: the pair keeps 100% with whatever margin is left)
    double width = total * out.scale + out.gap;
    double x = area.x + (room - width) / 2, y = area.y + area.height / 2;
    out.margin = (room - width) / 2;
    out.left = {x + left.width * out.scale / 2, y};
    out.right = {x + left.width * out.scale + out.gap + right.width * out.scale / 2, y};
    return out;
}
}
