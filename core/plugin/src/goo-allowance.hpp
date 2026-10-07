#pragma once
// The energy-reading collection allowance (main-loop Phase 3): at most `slots` busy slots are
// examined between two consecutive waits of the main loop, across every output's render pass and
// the collection timer.
//
// Why not an idle callback: idle callbacks run inside wl_event_loop_dispatch(), and an idle added
// by another idle runs in the same drain, so an idle refill can be followed by more collection in
// the same dispatch. The two facts this relies on instead, from libwayland's event loop:
//  - each dispatch calls epoll_wait() once, and a descriptor is reported at most once per call,
//    so a descriptor written after that wait is dispatched no earlier than the next dispatch;
//  - an idle added during a dispatch runs before the next epoll_wait() (in that dispatch's
//    trailing drain, or at the latest in the next dispatch's leading drain).
// The first collection after a refill queues an idle that closes the allowance (`left = 0`) and
// writes an eventfd; only that descriptor's callback refills. Every slot of one refill is
// therefore examined between the first of them and the next epoll_wait(), and the following
// refill comes after that wait.
#include "loop.hpp"
#include <cstdint>
#include <sys/eventfd.h>
#include <unistd.h>
#include <wayland-server-core.h>

namespace scottland::goo
{
class collect_allowance_t
{
  public:
    static constexpr int slots = 2;
    int left = slots;  // collectors pass this to renderer_t::collect() and call spent() after

    explicit collect_allowance_t(wl_event_loop *loop) : loop(loop)
    {
        fd = eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
        if (fd >= 0) source = wl_event_loop_add_fd(loop, fd, WL_EVENT_READABLE, &collect_allowance_t::refill, this);
        if (!source) left = 0;
    }
    ~collect_allowance_t()
    {
        if (closing) wl_event_source_remove(closing);
        if (source) wl_event_source_remove(source);
        if (fd >= 0) close(fd);
    }
    collect_allowance_t(const collect_allowance_t &) = delete;
    collect_allowance_t &operator=(const collect_allowance_t &) = delete;

    /** False when the eventfd or its source could not be created: nothing may be collected, and
     *  the renderers use the timed fallback. */
    bool usable() const { return source != nullptr; }
    /** After a collection: if it examined anything, close this allowance before the next wait. */
    void spent()
    {
        if (left >= slots || closing || !open) return;
        open = false;
        closing = wl_event_loop_add_idle(loop, &collect_allowance_t::close_now, this);
        if (!closing) close_now(this);  // no idle: close now, the refill still waits for a wait
    }

  private:
    wl_event_loop *loop;
    int fd = -1;
    wl_event_source *source = nullptr;
    wl_event_source *closing = nullptr;
    bool open = true;  // refilled and not yet closed

    static void close_now(void *data)
    {
        SCOTTLAND_LOOP_SCOPE(goo_collect_close);
        auto *self = static_cast<collect_allowance_t *>(data);
        self->closing = nullptr;  // libwayland removes a dispatched idle source itself
        self->left = 0;
        uint64_t one = 1;
        // Cannot fail short of a closed descriptor: each refill reads the counter back to zero.
        ssize_t written = write(self->fd, &one, sizeof one);
        (void)written;
    }
    static int refill(int fd, uint32_t, void *data)
    {
        SCOTTLAND_LOOP_SCOPE(goo_collect_refill);
        auto *self = static_cast<collect_allowance_t *>(data);
        uint64_t count;
        if (read(fd, &count, sizeof count) != (ssize_t)sizeof count) return 0;  // nothing written
        self->left = slots;
        self->open = true;
        return 0;
    }
};
} // namespace scottland::goo
