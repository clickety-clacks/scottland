// A single wl_shm XRGB surface whose unused alpha bits are zero. Real input drives it.
#define _GNU_SOURCE
#include <wayland-client.h>
#include "xdg-shell-client.h"
#include <sys/mman.h>
#include <unistd.h>
#include <stdint.h>
#include <stdlib.h>
#include <poll.h>
#include <signal.h>

static struct wl_compositor *compositor;
static struct wl_shm *shm;
static struct xdg_wm_base *wm;
static struct wl_surface *surface;
static struct wl_buffer *buffer, *larger;
static volatile sig_atomic_t resize_requested;
static void request_resize(int signal_number)
{ (void)signal_number; resize_requested = 1; }
static int closed;
static void ping(void *data, struct xdg_wm_base *base, uint32_t serial)
{ (void)data; xdg_wm_base_pong(base, serial); }
static const struct xdg_wm_base_listener wm_listener = { .ping = ping };
static void global(void *data, struct wl_registry *registry, uint32_t name, const char *interface, uint32_t version)
{
    (void)data; (void)version;
    if (!__builtin_strcmp(interface, "wl_compositor"))
        compositor = wl_registry_bind(registry, name, &wl_compositor_interface, 4);
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
    wl_surface_attach(surface, buffer, 0, 0);
    wl_surface_damage(surface, 0, 0, 400, 200);
    wl_surface_commit(surface);
}
static const struct xdg_surface_listener surface_listener = { .configure = configured };
static void configure(void *data, struct xdg_toplevel *top, int32_t w, int32_t h, struct wl_array *states)
{ (void)data; (void)top; (void)w; (void)h; (void)states; }
static void close_window(void *data, struct xdg_toplevel *top)
{ (void)data; (void)top; closed = 1; }
static const struct xdg_toplevel_listener top_listener = { .configure = configure, .close = close_window };
int main(void)
{
    struct wl_display *display = wl_display_connect(NULL);
    if (!display) return 1;
    struct wl_registry *registry = wl_display_get_registry(display);
    wl_registry_add_listener(registry, &registry_listener, NULL);
    wl_display_roundtrip(display);
    if (!compositor || !shm || !wm) return 2;
    const size_t bytes = 600 * 300 * 4;
    int fd = memfd_create("scottland-rgbx-test", MFD_CLOEXEC);
    if (fd < 0 || ftruncate(fd, bytes)) return 3;
    uint32_t *pixels = mmap(NULL, bytes, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (pixels == MAP_FAILED) return 4;
    for (int y = 0; y < 300; ++y)
        for (int x = 0; x < 600; ++x) pixels[y * 600 + x] = y < 20 ? 0x00ffff00 : 0x00ff0000;
    struct wl_shm_pool *pool = wl_shm_create_pool(shm, fd, bytes);
    buffer = wl_shm_pool_create_buffer(pool, 0, 400, 200, 1600, WL_SHM_FORMAT_XRGB8888);
    larger = wl_shm_pool_create_buffer(pool, 0, 600, 300, 2400, WL_SHM_FORMAT_XRGB8888);
    wl_shm_pool_destroy(pool); close(fd);
    surface = wl_compositor_create_surface(compositor);
    struct xdg_surface *xdg = xdg_wm_base_get_xdg_surface(wm, surface);
    xdg_surface_add_listener(xdg, &surface_listener, NULL);
    struct xdg_toplevel *top = xdg_surface_get_toplevel(xdg);
    xdg_toplevel_add_listener(top, &top_listener, NULL);
    xdg_toplevel_set_app_id(top, "scottland-morph-fixture");
    xdg_toplevel_set_title(top, "startup-delayed-rgbx");
    wl_surface_commit(surface);
    signal(SIGUSR1, request_resize);
    struct pollfd peer = { .fd = wl_display_get_fd(display), .events = POLLIN };
    while (!closed)
    {
        if (wl_display_dispatch_pending(display) < 0 || wl_display_flush(display) < 0) break;
        if (resize_requested)
        {
            resize_requested = 0;
            buffer = larger;
            wl_surface_attach(surface, buffer, 0, 0);
            wl_surface_damage(surface, 0, 0, 600, 300);
            wl_surface_commit(surface);
        }
        if (poll(&peer, 1, 10) > 0 && wl_display_dispatch(display) < 0) break;
    }
    wl_display_disconnect(display); munmap(pixels, bytes);
    return 0;
}
