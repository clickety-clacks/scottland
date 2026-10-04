#pragma once

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <vector>

namespace scottland
{
// Generic audition layer for layouts that suggest moves during a drag. It records real
// origins once, exposes independent visual offsets while the gesture is active, and keeps
// those offsets until the corresponding real geometry has committed. Cancel never mutates
// the origins, so restoring the presentation is exact.
struct drag_actor_position_t
{
    uint64_t id = 0;
    double x = 0, y = 0;
};

class drag_presentation_t
{
  public:
    struct actor_t
    {
        uint64_t id = 0;
        double origin_x = 0, origin_y = 0;
        double dx = 0, dy = 0;
        double scale = 1;  // factor on the actor's own scale (1: translation only)
        double target_x = 0, target_y = 0;
        bool applied = false;
    };

    bool begin(const std::vector<drag_actor_position_t>& origins, size_t cap)
    {
        // The caller checks the layout-specific bound before collecting this vector. Keep
        // this reusable layer defensive too, without allocating or copying on an over-cap call.
        if (origins.size() > cap) return false;
        actors.clear();
        actors.reserve(origins.size());
        for (const auto& origin : origins)
        {
            actors.push_back({origin.id, origin.x, origin.y, 0, 0, 1, origin.x, origin.y, false});
        }
        active = true;
        committing = false;
        return true;
    }

    void set_offset(size_t index, double dx, double dy, double scale = 1)
    {
        if (!active || committing || index >= actors.size()) return;
        actors[index].dx = dx;
        actors[index].dy = dy;
        actors[index].scale = scale;
    }

    void commit()
    {
        if (!active || committing) return;
        committing = true;
        active = false;
        for (auto& actor : actors)
        {
            actor.target_x = actor.origin_x + actor.dx;
            actor.target_y = actor.origin_y + actor.dy;
            actor.applied = std::abs(actor.dx) < 0.0001 && std::abs(actor.dy) < 0.0001;
        }
        discard_if_complete();
    }

    // Geometry backends can quantize the requested position. Match the visual offset to
    // that exact target before asking the backend to move the actor.
    void set_target(size_t index, double x, double y)
    {
        if (!committing || index >= actors.size()) return;
        auto& actor = actors[index];
        actor.target_x = x;
        actor.target_y = y;
        actor.dx = x - actor.origin_x;
        actor.dy = y - actor.origin_y;
        actor.applied = std::abs(actor.dx) < 0.0001 && std::abs(actor.dy) < 0.0001;
    }

    void finalize_targets() { discard_if_complete(); }

    bool acknowledge(uint64_t id, double x, double y)
    {
        if (!committing) return false;
        for (auto& actor : actors)
        {
            if (actor.id != id) continue;
            if (std::abs(x - actor.target_x) > 0.51 || std::abs(y - actor.target_y) > 0.51)
                return false;
            actor.applied = true;
            actor.dx = actor.dy = 0;
            discard_if_complete();
            return true;
        }
        return false;
    }

    void forget(uint64_t id)
    {
        if (!committing) return;
        for (auto& actor : actors)
        {
            if (actor.id == id) actor.applied = true;
        }
    }

    void cancel()
    {
        actors.clear();
        active = false;
        committing = false;
    }

    void clear() { cancel(); }
    bool is_active() const { return active; }
    bool is_committing() const { return committing; }
    bool empty() const { return actors.empty(); }
    size_t size() const { return actors.size(); }
    const actor_t& actor(size_t index) const { return actors[index]; }
    actor_t& actor(size_t index) { return actors[index]; }

    bool contains(uint64_t id) const
    {
        return std::any_of(actors.begin(), actors.end(), [=] (const auto& actor)
        {
            return actor.id == id;
        });
    }

  private:
    std::vector<actor_t> actors;
    bool active = false, committing = false;

    void discard_if_complete()
    {
        if (committing && std::all_of(actors.begin(), actors.end(), [] (const auto& actor)
            { return actor.applied; }))
        {
            actors.clear();
            committing = false;
        }
    }
};
}
