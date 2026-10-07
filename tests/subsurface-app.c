// A toplevel with a subsurface, so it cannot be captured from one client buffer (a "complex"
// surface): a blue 480x320 window and a 160x100 subsurface at 40,60 that alternates green and
// yellow every 200 ms. Title from argv[1].
#define _GNU_SOURCE
#include <wayland-client.h>
#include "xdg-shell-client.h"
#include <sys/mman.h>
#include <unistd.h>
#include <stdint.h>
#include <poll.h>
#include <time.h>

static struct wl_compositor *compositor;
static struct wl_subcompositor *subcompositor;
static struct wl_shm *shm;
static struct xdg_wm_base *wm;
static struct wl_surface *surface, *child;
static struct wl_buffer *main_buffer, *child_buffers[2];
static int closed, configured_once;
static void ping(void *data, struct xdg_wm_base *base, uint32_t serial)
{ (void)data; xdg_wm_base_pong(base, serial); }
static const struct xdg_wm_base_listener wm_listener = { .ping = ping };
static void global(void *data, struct wl_registry *registry, uint32_t name, const char *interface, uint32_t version)
{
    (void)data; (void)version;
    if (!__builtin_strcmp(interface, "wl_compositor"))
        compositor = wl_registry_bind(registry, name, &wl_compositor_interface, 4);
    else if (!__builtin_strcmp(interface, "wl_subcompositor"))
        subcompositor = wl_registry_bind(registry, name, &wl_subcompositor_interface, 1);
    else if (!__builtin_strcmp(interface, "wl_shm"))
        shm = wl_registry_bind(registry, name, &wl_shm_interface, 1);
    else if (!__builtin_strcmp(interface, "xdg_wm_base"))
    {
        wm = wl_registry_bind(registry, name, &xdg_wm_base_interface, 1);
        xdg_wm_base_add_listener(wm, &wm_listener, NULL);
    }
}
static void removed(void *data, struct wl_registry *registry, uint32_t name)
{ (void)data; (void)registry; (void)name; }
static const struct wl_registry_listener registry_listener = { .global = global, .global_remove = removed };
static void configured(void *data, struct xdg_surface *xdg, uint32_t serial)
{
    (void)data;
    xdg_surface_ack_configure(xdg, serial);
    wl_surface_attach(surface, main_buffer, 0, 0);
    wl_surface_damage(surface, 0, 0, 480, 320);
    wl_surface_commit(surface);
    configured_once = 1;
}
static const struct xdg_surface_listener surface_listener = { .configure = configured };
static void configure(void *data, struct xdg_toplevel *top, int32_t w, int32_t h, struct wl_array *states)
{ (void)data; (void)top; (void)w; (void)h; (void)states; }
static void close_window(void *data, struct xdg_toplevel *top)
{ (void)data; (void)top; closed = 1; }
static const struct xdg_toplevel_listener top_listener = { .configure = configure, .close = close_window };
static struct wl_buffer *solid(int width, int height, uint32_t color)
{
    size_t bytes = (size_t)width * height * 4;
    int fd = memfd_create("scottland-subsurface-test", MFD_CLOEXEC);
    if (fd < 0 || ftruncate(fd, bytes)) return NULL;
    uint32_t *pixels = mmap(NULL, bytes, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (pixels == MAP_FAILED) return NULL;
    for (size_t i = 0; i < bytes / 4; ++i) pixels[i] = color;
    munmap(pixels, bytes);
    struct wl_shm_pool *pool = wl_shm_create_pool(shm, fd, bytes);
    struct wl_buffer *buffer = wl_shm_pool_create_buffer(pool, 0, width, height, width * 4, WL_SHM_FORMAT_XRGB8888);
    wl_shm_pool_destroy(pool);
    close(fd);
    return buffer;
}
static double now(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}
int main(int argc, char **argv)
{
    struct wl_display *display = wl_display_connect(NULL);
    if (!display) return 1;
    struct wl_registry *registry = wl_display_get_registry(display);
    wl_registry_add_listener(registry, &registry_listener, NULL);
    wl_display_roundtrip(display);
    if (!compositor || !subcompositor || !shm || !wm) return 2;
    main_buffer = solid(480, 320, 0x002050c0);
    child_buffers[0] = solid(160, 100, 0x0020c040);
    child_buffers[1] = solid(160, 100, 0x00e0d020);
    if (!main_buffer || !child_buffers[0] || !child_buffers[1]) return 3;
    surface = wl_compositor_create_surface(compositor);
    child = wl_compositor_create_surface(compositor);
    struct wl_subsurface *sub = wl_subcompositor_get_subsurface(subcompositor, child, surface);
    wl_subsurface_set_position(sub, 40, 60);
    wl_subsurface_set_desync(sub);
    struct xdg_surface *xdg = xdg_wm_base_get_xdg_surface(wm, surface);
    xdg_surface_add_listener(xdg, &surface_listener, NULL);
    struct xdg_toplevel *top = xdg_surface_get_toplevel(xdg);
    xdg_toplevel_add_listener(top, &top_listener, NULL);
    xdg_toplevel_set_app_id(top, "scottland-subsurface-fixture");
    xdg_toplevel_set_title(top, argc > 1 ? argv[1] : "subsurface");
    wl_surface_commit(surface);
    struct pollfd peer = { .fd = wl_display_get_fd(display), .events = POLLIN };
    double next = 0;
    int phase = 0;
    while (!closed)
    {
        if (configured_once && now() >= next)
        {
            wl_surface_attach(child, child_buffers[phase ^= 1], 0, 0);
            wl_surface_damage(child, 0, 0, 160, 100);
            wl_surface_commit(child);
            next = now() + .2;
        }
        if (wl_display_dispatch_pending(display) < 0 || wl_display_flush(display) < 0) break;
        if (poll(&peer, 1, 20) > 0 && wl_display_dispatch(display) < 0) break;
    }
    wl_display_disconnect(display);
    return 0;
}
