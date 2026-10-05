// A Wayland client that prints the scroll it receives, for scroll-speed tests.
//
//   axis-recorder APP_ID RRGGBB            an xdg toplevel with that app-id
//   axis-recorder --layer NAMESPACE RRGGBB a layer-shell surface (top layer), like a shell panel
//
// Fills itself with the color (400x300) and prints, unbuffered, one line per event:
//   ready | enter | source N | axis AXIS VALUE | stop AXIS | frame
// VALUE is the wl_pointer.axis value exactly as delivered (surface-local units).
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include <wayland-client.h>
#include "xdg-shell-client-protocol.h"
#include "wlr-layer-shell-unstable-v1-client-protocol.h"

static struct wl_compositor *compositor;
static struct wl_shm *shm;
static struct wl_seat *seat;
static struct xdg_wm_base *wm_base;
static struct zwlr_layer_shell_v1 *layer_shell;
static struct wl_surface *surface;
static uint32_t color;
static int width = 400, height = 300;

static void attach_buffer(void)
{
    int stride = width * 4, size = stride * height;
    int fd = memfd_create("axis-recorder", MFD_CLOEXEC);
    if (fd < 0 || ftruncate(fd, size) < 0) exit(1);
    uint32_t *pixels = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    for (int i = 0; i < width * height; ++i) pixels[i] = 0xFF000000u | color;
    munmap(pixels, size);
    struct wl_shm_pool *pool = wl_shm_create_pool(shm, fd, size);
    struct wl_buffer *buffer = wl_shm_pool_create_buffer(pool, 0, width, height, stride,
        WL_SHM_FORMAT_XRGB8888);
    wl_shm_pool_destroy(pool);
    close(fd);
    wl_surface_attach(surface, buffer, 0, 0);
    wl_surface_damage_buffer(surface, 0, 0, width, height);
    wl_surface_commit(surface);
}

static void ping(void *data, struct xdg_wm_base *base, uint32_t serial)
{
    (void)data;
    xdg_wm_base_pong(base, serial);
}
static const struct xdg_wm_base_listener wm_base_listener = {ping};

static int configured;
static void xdg_configure(void *data, struct xdg_surface *xdg, uint32_t serial)
{
    (void)data;
    xdg_surface_ack_configure(xdg, serial);
    attach_buffer();
    if (!configured++) printf("ready\n");
}
static const struct xdg_surface_listener xdg_listener = {xdg_configure};

static void toplevel_configure(void *d, struct xdg_toplevel *t, int32_t w, int32_t h, struct wl_array *s)
{
    (void)d; (void)t; (void)s;
    if (w > 0 && h > 0) { width = w; height = h; }
}
static void toplevel_close(void *d, struct xdg_toplevel *t) { (void)d; (void)t; exit(0); }
static void toplevel_bounds(void *d, struct xdg_toplevel *t, int32_t w, int32_t h) { (void)d; (void)t; (void)w; (void)h; }
static void toplevel_caps(void *d, struct xdg_toplevel *t, struct wl_array *c) { (void)d; (void)t; (void)c; }
static const struct xdg_toplevel_listener toplevel_listener = {
    toplevel_configure, toplevel_close, toplevel_bounds, toplevel_caps};

static void layer_configure(void *data, struct zwlr_layer_surface_v1 *layer, uint32_t serial,
    uint32_t w, uint32_t h)
{
    (void)data;
    if (w > 0 && h > 0) { width = w; height = h; }
    zwlr_layer_surface_v1_ack_configure(layer, serial);
    attach_buffer();
    if (!configured++) printf("ready\n");
}
static void layer_closed(void *d, struct zwlr_layer_surface_v1 *l) { (void)d; (void)l; exit(0); }
static const struct zwlr_layer_surface_v1_listener layer_listener = {layer_configure, layer_closed};

static void p_enter(void *d, struct wl_pointer *p, uint32_t s, struct wl_surface *f, wl_fixed_t x, wl_fixed_t y)
{ (void)d; (void)p; (void)s; (void)f; (void)x; (void)y; printf("enter\n"); }
static void p_leave(void *d, struct wl_pointer *p, uint32_t s, struct wl_surface *f)
{ (void)d; (void)p; (void)s; (void)f; printf("leave\n"); }
static void p_motion(void *d, struct wl_pointer *p, uint32_t t, wl_fixed_t x, wl_fixed_t y)
{ (void)d; (void)p; (void)t; (void)x; (void)y; }
static void p_button(void *d, struct wl_pointer *p, uint32_t s, uint32_t t, uint32_t b, uint32_t st)
{ (void)d; (void)p; (void)s; (void)t; (void)b; (void)st; }
static void p_axis(void *d, struct wl_pointer *p, uint32_t t, uint32_t axis, wl_fixed_t value)
{ (void)d; (void)p; (void)t; printf("axis %u %.4f\n", axis, wl_fixed_to_double(value)); }
static void p_frame(void *d, struct wl_pointer *p) { (void)d; (void)p; printf("frame\n"); }
static void p_source(void *d, struct wl_pointer *p, uint32_t source)
{ (void)d; (void)p; printf("source %u\n", source); }
static void p_stop(void *d, struct wl_pointer *p, uint32_t t, uint32_t axis)
{ (void)d; (void)p; (void)t; printf("stop %u\n", axis); }
static void p_discrete(void *d, struct wl_pointer *p, uint32_t axis, int32_t v)
{ (void)d; (void)p; (void)axis; (void)v; }
static void p_value120(void *d, struct wl_pointer *p, uint32_t axis, int32_t v)
{ (void)d; (void)p; (void)axis; (void)v; }
static void p_direction(void *d, struct wl_pointer *p, uint32_t axis, uint32_t dir)
{ (void)d; (void)p; (void)axis; (void)dir; }
static const struct wl_pointer_listener pointer_listener = {
    p_enter, p_leave, p_motion, p_button, p_axis, p_frame, p_source, p_stop, p_discrete,
    p_value120, p_direction};

static void seat_caps(void *d, struct wl_seat *s, uint32_t caps)
{
    (void)d;
    static struct wl_pointer *pointer;
    if ((caps & WL_SEAT_CAPABILITY_POINTER) && !pointer)
    {
        pointer = wl_seat_get_pointer(s);
        wl_pointer_add_listener(pointer, &pointer_listener, NULL);
    }
}
static void seat_name(void *d, struct wl_seat *s, const char *n) { (void)d; (void)s; (void)n; }
static const struct wl_seat_listener seat_listener = {seat_caps, seat_name};

static void global(void *d, struct wl_registry *r, uint32_t id, const char *iface, uint32_t version)
{
    (void)d;
    if (!strcmp(iface, wl_compositor_interface.name))
        compositor = wl_registry_bind(r, id, &wl_compositor_interface, 4);
    else if (!strcmp(iface, wl_shm_interface.name))
        shm = wl_registry_bind(r, id, &wl_shm_interface, 1);
    else if (!strcmp(iface, wl_seat_interface.name))
        seat = wl_registry_bind(r, id, &wl_seat_interface, version < 9 ? version : 9);
    else if (!strcmp(iface, xdg_wm_base_interface.name))
        wm_base = wl_registry_bind(r, id, &xdg_wm_base_interface, 1);
    else if (!strcmp(iface, zwlr_layer_shell_v1_interface.name))
        layer_shell = wl_registry_bind(r, id, &zwlr_layer_shell_v1_interface, 1);
}
static void global_remove(void *d, struct wl_registry *r, uint32_t id) { (void)d; (void)r; (void)id; }
static const struct wl_registry_listener registry_listener = {global, global_remove};

int main(int argc, char **argv)
{
    int layer = argc == 4 && !strcmp(argv[1], "--layer");
    if (argc != 3 && !layer)
    {
        fprintf(stderr, "usage: axis-recorder APP_ID RRGGBB | --layer NAMESPACE RRGGBB\n");
        return 64;
    }

    setvbuf(stdout, NULL, _IOLBF, 0);
    color = strtoul(argv[argc - 1], NULL, 16);
    struct wl_display *display = wl_display_connect(NULL);
    if (!display) return 2;
    struct wl_registry *registry = wl_display_get_registry(display);
    wl_registry_add_listener(registry, &registry_listener, NULL);
    wl_display_roundtrip(display);
    if (!compositor || !shm || !seat || (layer ? !layer_shell : !wm_base)) return 2;
    wl_seat_add_listener(seat, &seat_listener, NULL);
    surface = wl_compositor_create_surface(compositor);

    if (layer)
    {
        struct zwlr_layer_surface_v1 *ls = zwlr_layer_shell_v1_get_layer_surface(layer_shell,
            surface, NULL, ZWLR_LAYER_SHELL_V1_LAYER_TOP, argv[2]);
        zwlr_layer_surface_v1_add_listener(ls, &layer_listener, NULL);
        zwlr_layer_surface_v1_set_size(ls, width, height);
        zwlr_layer_surface_v1_set_anchor(ls, ZWLR_LAYER_SURFACE_V1_ANCHOR_TOP);
        wl_surface_commit(surface);
    } else
    {
        xdg_wm_base_add_listener(wm_base, &wm_base_listener, NULL);
        struct xdg_surface *xdg = xdg_wm_base_get_xdg_surface(wm_base, surface);
        xdg_surface_add_listener(xdg, &xdg_listener, NULL);
        struct xdg_toplevel *toplevel = xdg_surface_get_toplevel(xdg);
        xdg_toplevel_add_listener(toplevel, &toplevel_listener, NULL);
        xdg_toplevel_set_app_id(toplevel, argv[1]);
        xdg_toplevel_set_title(toplevel, argv[1]);
        wl_surface_commit(surface);
    }

    while (wl_display_dispatch(display) != -1) {}
    return 0;
}
