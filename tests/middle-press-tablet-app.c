// Minimal xdg-toplevel client that records the tablet tool events Wayfire delivers.
// The headless stipc virtual tablet drives the same wlroots tablet-v2 path as a device.
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/input-event-codes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#include <wayland-client.h>
#include "tablet-v2-client-protocol.h"
#include "xdg-shell-client-protocol.h"

static struct wl_compositor *compositor;
static struct wl_shm *shm;
static struct wl_seat *seat;
static struct xdg_wm_base *wm_base;
static struct zwp_tablet_manager_v2 *tablet_manager;
static struct zwp_tablet_seat_v2 *tablet_seat;
static struct wl_surface *surface;
static struct xdg_toplevel *toplevel;
static const char *title;
static const char *events_path;
static int width = 360;
static int height = 260;
static int running = 1;

struct recorded_buffer
{
    struct wl_buffer *buffer;
};

static void record_event(const char *event, uint32_t button, uint32_t state)
{
    FILE *file = fopen(events_path, "a");
    if (!file)
    {
        fprintf(stderr, "%s: cannot append %s: %s\n", title, events_path, strerror(errno));
        return;
    }

    fprintf(file, "{\"event\":\"%s\",\"button\":%u,\"state\":%u}\n",
        event, button, state);
    fclose(file);
}

static void buffer_release(void *data, struct wl_buffer *buffer)
{
    struct recorded_buffer *recorded = data;
    wl_buffer_destroy(buffer);
    free(recorded);
}

static const struct wl_buffer_listener buffer_listener = {
    .release = buffer_release,
};

static void attach_buffer(void)
{
    int stride = width * 4;
    int size = stride * height;
    int fd = memfd_create("middle-press-tablet", MFD_CLOEXEC);
    if (fd < 0 || ftruncate(fd, size) < 0)
    {
        fprintf(stderr, "%s: could not create shared buffer: %s\n", title, strerror(errno));
        exit(2);
    }

    uint32_t *pixels = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (pixels == MAP_FAILED)
    {
        fprintf(stderr, "%s: could not map shared buffer: %s\n", title, strerror(errno));
        close(fd);
        exit(2);
    }

    for (int i = 0; i < width * height; ++i)
    {
        pixels[i] = 0xff1fad47u;
    }
    munmap(pixels, size);

    struct wl_shm_pool *pool = wl_shm_create_pool(shm, fd, size);
    struct wl_buffer *buffer = wl_shm_pool_create_buffer(pool, 0, width, height, stride,
        WL_SHM_FORMAT_XRGB8888);
    wl_shm_pool_destroy(pool);
    close(fd);

    struct recorded_buffer *recorded = calloc(1, sizeof(*recorded));
    if (!recorded || !buffer)
    {
        fprintf(stderr, "%s: could not create Wayland buffer\n", title);
        free(recorded);
        exit(2);
    }

    recorded->buffer = buffer;
    wl_buffer_add_listener(buffer, &buffer_listener, recorded);
    wl_surface_attach(surface, buffer, 0, 0);
    wl_surface_damage(surface, 0, 0, width, height);
    wl_surface_commit(surface);
}

static void wm_ping(void *data, struct xdg_wm_base *base, uint32_t serial)
{
    (void)data;
    xdg_wm_base_pong(base, serial);
}

static const struct xdg_wm_base_listener wm_base_listener = {
    .ping = wm_ping,
};

static void xdg_configure(void *data, struct xdg_surface *xdg_surface, uint32_t serial)
{
    (void)data;
    xdg_surface_ack_configure(xdg_surface, serial);
    attach_buffer();
    record_event("ready", 0, 0);
}

static const struct xdg_surface_listener xdg_surface_listener = {
    .configure = xdg_configure,
};

static void toplevel_configure(void *data, struct xdg_toplevel *xdg_toplevel,
    int32_t next_width, int32_t next_height, struct wl_array *states)
{
    (void)data;
    (void)xdg_toplevel;
    (void)states;
    if (next_width > 0 && next_height > 0)
    {
        width = next_width;
        height = next_height;
    }
}

static void toplevel_close(void *data, struct xdg_toplevel *xdg_toplevel)
{
    (void)data;
    (void)xdg_toplevel;
    running = 0;
}

static void toplevel_bounds(void *data, struct xdg_toplevel *xdg_toplevel,
    int32_t next_width, int32_t next_height)
{
    (void)data;
    (void)xdg_toplevel;
    (void)next_width;
    (void)next_height;
}

static void toplevel_capabilities(void *data, struct xdg_toplevel *xdg_toplevel,
    struct wl_array *capabilities)
{
    (void)data;
    (void)xdg_toplevel;
    (void)capabilities;
}

static const struct xdg_toplevel_listener toplevel_listener = {
    .configure = toplevel_configure,
    .close = toplevel_close,
    .configure_bounds = toplevel_bounds,
    .wm_capabilities = toplevel_capabilities,
};

static void tablet_name(void *data, struct zwp_tablet_v2 *tablet, const char *name)
{
    (void)data;
    (void)tablet;
    (void)name;
}

static void tablet_id(void *data, struct zwp_tablet_v2 *tablet, uint32_t vendor, uint32_t product)
{
    (void)data;
    (void)tablet;
    (void)vendor;
    (void)product;
}

static void tablet_path(void *data, struct zwp_tablet_v2 *tablet, const char *path)
{
    (void)data;
    (void)tablet;
    (void)path;
}

static void tablet_done(void *data, struct zwp_tablet_v2 *tablet)
{
    (void)data;
    (void)tablet;
}

static void tablet_removed(void *data, struct zwp_tablet_v2 *tablet)
{
    (void)data;
    zwp_tablet_v2_destroy(tablet);
}

static const struct zwp_tablet_v2_listener tablet_listener = {
    .name = tablet_name,
    .id = tablet_id,
    .path = tablet_path,
    .done = tablet_done,
    .removed = tablet_removed,
};

static void tool_type(void *data, struct zwp_tablet_tool_v2 *tool, uint32_t type)
{
    (void)data;
    (void)tool;
    (void)type;
}

static void tool_hardware_serial(void *data, struct zwp_tablet_tool_v2 *tool,
    uint32_t serial_hi, uint32_t serial_lo)
{
    (void)data;
    (void)tool;
    (void)serial_hi;
    (void)serial_lo;
}

static void tool_hardware_id_wacom(void *data, struct zwp_tablet_tool_v2 *tool,
    uint32_t id, uint32_t pid)
{
    (void)data;
    (void)tool;
    (void)id;
    (void)pid;
}

static void tool_capability(void *data, struct zwp_tablet_tool_v2 *tool, uint32_t capability)
{
    (void)data;
    (void)tool;
    (void)capability;
}

static void tool_done(void *data, struct zwp_tablet_tool_v2 *tool)
{
    (void)data;
    (void)tool;
}

static void tool_removed(void *data, struct zwp_tablet_tool_v2 *tool)
{
    (void)data;
    zwp_tablet_tool_v2_destroy(tool);
}

static void tool_proximity_in(void *data, struct zwp_tablet_tool_v2 *tool,
    uint32_t serial, struct zwp_tablet_v2 *tablet, struct wl_surface *focused_surface)
{
    (void)data;
    (void)tool;
    (void)serial;
    (void)tablet;
    (void)focused_surface;
    record_event("proximity_in", 0, 0);
}

static void tool_proximity_out(void *data, struct zwp_tablet_tool_v2 *tool)
{
    (void)data;
    (void)tool;
    record_event("proximity_out", 0, 0);
}

static void tool_down(void *data, struct zwp_tablet_tool_v2 *tool, uint32_t serial)
{
    (void)data;
    (void)tool;
    record_event("tip_down", 0, serial);
}

static void tool_up(void *data, struct zwp_tablet_tool_v2 *tool)
{
    (void)data;
    (void)tool;
    record_event("tip_up", 0, 0);
}

static void tool_motion(void *data, struct zwp_tablet_tool_v2 *tool, wl_fixed_t x, wl_fixed_t y)
{
    (void)data;
    (void)tool;
    (void)x;
    (void)y;
}

static void tool_pressure(void *data, struct zwp_tablet_tool_v2 *tool, uint32_t pressure)
{
    (void)data;
    (void)tool;
    (void)pressure;
}

static void tool_distance(void *data, struct zwp_tablet_tool_v2 *tool, uint32_t distance)
{
    (void)data;
    (void)tool;
    (void)distance;
}

static void tool_tilt(void *data, struct zwp_tablet_tool_v2 *tool, wl_fixed_t x, wl_fixed_t y)
{
    (void)data;
    (void)tool;
    (void)x;
    (void)y;
}

static void tool_rotation(void *data, struct zwp_tablet_tool_v2 *tool, wl_fixed_t degrees)
{
    (void)data;
    (void)tool;
    (void)degrees;
}

static void tool_slider(void *data, struct zwp_tablet_tool_v2 *tool, int32_t position)
{
    (void)data;
    (void)tool;
    (void)position;
}

static void tool_wheel(void *data, struct zwp_tablet_tool_v2 *tool,
    wl_fixed_t degrees, int32_t clicks)
{
    (void)data;
    (void)tool;
    (void)degrees;
    (void)clicks;
}

static void tool_button(void *data, struct zwp_tablet_tool_v2 *tool,
    uint32_t serial, uint32_t button, uint32_t state)
{
    (void)data;
    (void)tool;
    (void)serial;
    record_event("button", button, state);
}

static void tool_frame(void *data, struct zwp_tablet_tool_v2 *tool, uint32_t time)
{
    (void)data;
    (void)tool;
    (void)time;
}

static const struct zwp_tablet_tool_v2_listener tool_listener = {
    .type = tool_type,
    .hardware_serial = tool_hardware_serial,
    .hardware_id_wacom = tool_hardware_id_wacom,
    .capability = tool_capability,
    .done = tool_done,
    .removed = tool_removed,
    .proximity_in = tool_proximity_in,
    .proximity_out = tool_proximity_out,
    .down = tool_down,
    .up = tool_up,
    .motion = tool_motion,
    .pressure = tool_pressure,
    .distance = tool_distance,
    .tilt = tool_tilt,
    .rotation = tool_rotation,
    .slider = tool_slider,
    .wheel = tool_wheel,
    .button = tool_button,
    .frame = tool_frame,
};

static void tablet_seat_tablet_added(void *data, struct zwp_tablet_seat_v2 *tablet_seat,
    struct zwp_tablet_v2 *tablet)
{
    (void)data;
    (void)tablet_seat;
    zwp_tablet_v2_add_listener(tablet, &tablet_listener, NULL);
}

static void tablet_seat_tool_added(void *data, struct zwp_tablet_seat_v2 *tablet_seat,
    struct zwp_tablet_tool_v2 *tool)
{
    (void)data;
    (void)tablet_seat;
    zwp_tablet_tool_v2_add_listener(tool, &tool_listener, NULL);
}

static void tablet_seat_pad_added(void *data, struct zwp_tablet_seat_v2 *tablet_seat,
    struct zwp_tablet_pad_v2 *pad)
{
    (void)data;
    (void)tablet_seat;
    zwp_tablet_pad_v2_destroy(pad);
}

static const struct zwp_tablet_seat_v2_listener tablet_seat_listener = {
    .tablet_added = tablet_seat_tablet_added,
    .tool_added = tablet_seat_tool_added,
    .pad_added = tablet_seat_pad_added,
};

static void registry_global(void *data, struct wl_registry *registry,
    uint32_t name, const char *interface, uint32_t version)
{
    (void)data;
    if (strcmp(interface, wl_compositor_interface.name) == 0)
    {
        compositor = wl_registry_bind(registry, name, &wl_compositor_interface,
            version < 4 ? version : 4);
    } else if (strcmp(interface, wl_shm_interface.name) == 0)
    {
        shm = wl_registry_bind(registry, name, &wl_shm_interface, 1);
    } else if (strcmp(interface, wl_seat_interface.name) == 0)
    {
        seat = wl_registry_bind(registry, name, &wl_seat_interface, 1);
    } else if (strcmp(interface, xdg_wm_base_interface.name) == 0)
    {
        wm_base = wl_registry_bind(registry, name, &xdg_wm_base_interface, 1);
    } else if (strcmp(interface, zwp_tablet_manager_v2_interface.name) == 0)
    {
        tablet_manager = wl_registry_bind(registry, name, &zwp_tablet_manager_v2_interface,
            version < 1 ? version : 1);
    }
}

static void registry_global_remove(void *data, struct wl_registry *registry, uint32_t name)
{
    (void)data;
    (void)registry;
    (void)name;
}

static const struct wl_registry_listener registry_listener = {
    .global = registry_global,
    .global_remove = registry_global_remove,
};

int main(int argc, char **argv)
{
    if (argc != 3)
    {
        fprintf(stderr, "usage: middle-press-tablet-app TITLE EVENTS.jsonl\n");
        return 64;
    }

    title = argv[1];
    events_path = argv[2];
    struct wl_display *display = wl_display_connect(NULL);
    if (!display)
    {
        fprintf(stderr, "%s: could not connect to Wayland\n", title);
        return 2;
    }

    struct wl_registry *registry = wl_display_get_registry(display);
    wl_registry_add_listener(registry, &registry_listener, NULL);
    if (wl_display_roundtrip(display) < 0 || !compositor || !shm || !seat || !wm_base || !tablet_manager)
    {
        fprintf(stderr, "%s: required Wayland globals are unavailable\n", title);
        wl_display_disconnect(display);
        return 2;
    }

    tablet_seat = zwp_tablet_manager_v2_get_tablet_seat(tablet_manager, seat);
    zwp_tablet_seat_v2_add_listener(tablet_seat, &tablet_seat_listener, NULL);
    xdg_wm_base_add_listener(wm_base, &wm_base_listener, NULL);
    surface = wl_compositor_create_surface(compositor);
    struct xdg_surface *xdg_surface = xdg_wm_base_get_xdg_surface(wm_base, surface);
    xdg_surface_add_listener(xdg_surface, &xdg_surface_listener, NULL);
    toplevel = xdg_surface_get_toplevel(xdg_surface);
    xdg_toplevel_add_listener(toplevel, &toplevel_listener, NULL);
    xdg_toplevel_set_app_id(toplevel, "org.scottland.MiddlePressTablet");
    xdg_toplevel_set_title(toplevel, title);
    wl_surface_commit(surface);

    if (wl_display_roundtrip(display) < 0)
    {
        wl_display_disconnect(display);
        return 2;
    }

    while (running && wl_display_dispatch(display) != -1)
    {}

    zwp_tablet_seat_v2_destroy(tablet_seat);
    zwp_tablet_manager_v2_destroy(tablet_manager);
    xdg_toplevel_destroy(toplevel);
    xdg_surface_destroy(xdg_surface);
    wl_surface_destroy(surface);
    xdg_wm_base_destroy(wm_base);
    wl_seat_destroy(seat);
    wl_shm_destroy(shm);
    wl_compositor_destroy(compositor);
    wl_registry_destroy(registry);
    wl_display_disconnect(display);
    return 0;
}
