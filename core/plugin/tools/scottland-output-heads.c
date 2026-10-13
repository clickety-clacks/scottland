// Print every output the compositor knows, enabled or not, with its real metadata, through the
// wlr-output-management protocol: the same report output tools (wlr-randr, kanshi) read, so it is
// what Wayfire itself says it applied.
//
//   scottland-output-heads
//
// Prints one JSON array; each entry:
//   {"name":"eDP-1","description":"…","make":"…","model":"…","serial":"…",
//    "physical_width":300,"physical_height":190,"enabled":true,
//    "mode":{"width":2560,"height":1600,"refresh":165.0},   (null while disabled)
//    "modes":[{"width":2560,"height":1600,"refresh":165.0,"preferred":true},…],
//    "x":0,"y":0,"scale":1.5,"transform":0,"adaptive_sync":false}
// transform is wl_output's numbering (0 normal, 1 90°, … 4 flipped, …), refresh in Hz.
// Exits 2 when there is no Wayland display or the compositor lacks the protocol.
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wayland-client.h>
#include "wlr-output-management-unstable-v1-client-protocol.h"

struct mode
{
    struct zwlr_output_mode_v1 *wl;
    int width, height, refresh;  // refresh in mHz, 0 if unknown
    bool preferred;
    struct mode *next;
};

struct head
{
    struct zwlr_output_head_v1 *wl;
    char *name, *description, *make, *model, *serial;
    int physical_width, physical_height;
    bool enabled, adaptive_sync;
    struct mode *modes, *current;
    int x, y, transform;
    double scale;
    struct head *next;
};

static struct zwlr_output_manager_v1 *manager;
static struct head *heads;
static bool done;

static void set_string(char **field, const char *value)
{
    free(*field);
    *field = strdup(value ? value : "");
}

static void mode_size(void *data, struct zwlr_output_mode_v1 *wl, int32_t width, int32_t height)
{
    (void)wl;
    struct mode *mode = data;
    mode->width  = width;
    mode->height = height;
}

static void mode_refresh(void *data, struct zwlr_output_mode_v1 *wl, int32_t refresh)
{
    (void)wl;
    ((struct mode*)data)->refresh = refresh;
}

static void mode_preferred(void *data, struct zwlr_output_mode_v1 *wl)
{
    (void)wl;
    ((struct mode*)data)->preferred = true;
}

static void mode_finished(void *data, struct zwlr_output_mode_v1 *wl)
{
    (void)data; (void)wl;
}

static const struct zwlr_output_mode_v1_listener mode_listener = {
    .size = mode_size,
    .refresh = mode_refresh,
    .preferred = mode_preferred,
    .finished = mode_finished,
};

static void head_name(void *data, struct zwlr_output_head_v1 *wl, const char *name)
{
    (void)wl;
    set_string(&((struct head*)data)->name, name);
}

static void head_description(void *data, struct zwlr_output_head_v1 *wl, const char *text)
{
    (void)wl;
    set_string(&((struct head*)data)->description, text);
}

static void head_physical_size(void *data, struct zwlr_output_head_v1 *wl, int32_t width, int32_t height)
{
    (void)wl;
    struct head *head = data;
    head->physical_width  = width;
    head->physical_height = height;
}

static void head_mode(void *data, struct zwlr_output_head_v1 *wl, struct zwlr_output_mode_v1 *wl_mode)
{
    (void)wl;
    struct head *head = data;
    struct mode *mode = calloc(1, sizeof(*mode));
    mode->wl = wl_mode;
    zwlr_output_mode_v1_add_listener(wl_mode, &mode_listener, mode);
    // Appended, so modes print in the compositor's order.
    struct mode **tail = &head->modes;
    while (*tail) tail = &(*tail)->next;
    *tail = mode;
}

static void head_enabled(void *data, struct zwlr_output_head_v1 *wl, int32_t enabled)
{
    (void)wl;
    struct head *head = data;
    head->enabled = enabled;
    if (!enabled) head->current = NULL;
}

static void head_current_mode(void *data, struct zwlr_output_head_v1 *wl, struct zwlr_output_mode_v1 *wl_mode)
{
    (void)wl;
    struct head *head = data;
    for (struct mode *mode = head->modes; mode; mode = mode->next)
    {
        if (mode->wl == wl_mode) head->current = mode;
    }
}

static void head_position(void *data, struct zwlr_output_head_v1 *wl, int32_t x, int32_t y)
{
    (void)wl;
    struct head *head = data;
    head->x = x;
    head->y = y;
}

static void head_transform(void *data, struct zwlr_output_head_v1 *wl, int32_t transform)
{
    (void)wl;
    ((struct head*)data)->transform = transform;
}

static void head_scale(void *data, struct zwlr_output_head_v1 *wl, wl_fixed_t scale)
{
    (void)wl;
    ((struct head*)data)->scale = wl_fixed_to_double(scale);
}

static void head_finished(void *data, struct zwlr_output_head_v1 *wl)
{
    (void)data; (void)wl;
}

static void head_make(void *data, struct zwlr_output_head_v1 *wl, const char *text)
{
    (void)wl;
    set_string(&((struct head*)data)->make, text);
}

static void head_model(void *data, struct zwlr_output_head_v1 *wl, const char *text)
{
    (void)wl;
    set_string(&((struct head*)data)->model, text);
}

static void head_serial(void *data, struct zwlr_output_head_v1 *wl, const char *text)
{
    (void)wl;
    set_string(&((struct head*)data)->serial, text);
}

static void head_adaptive_sync(void *data, struct zwlr_output_head_v1 *wl, uint32_t state)
{
    (void)wl;
    ((struct head*)data)->adaptive_sync = state == ZWLR_OUTPUT_HEAD_V1_ADAPTIVE_SYNC_STATE_ENABLED;
}

static const struct zwlr_output_head_v1_listener head_listener = {
    .name = head_name,
    .description = head_description,
    .physical_size = head_physical_size,
    .mode = head_mode,
    .enabled = head_enabled,
    .current_mode = head_current_mode,
    .position = head_position,
    .transform = head_transform,
    .scale = head_scale,
    .finished = head_finished,
    .make = head_make,
    .model = head_model,
    .serial_number = head_serial,
    .adaptive_sync = head_adaptive_sync,
};

static void manager_head(void *data, struct zwlr_output_manager_v1 *wl, struct zwlr_output_head_v1 *wl_head)
{
    (void)data; (void)wl;
    struct head *head = calloc(1, sizeof(*head));
    head->wl    = wl_head;
    head->scale = 1.0;
    zwlr_output_head_v1_add_listener(wl_head, &head_listener, head);
    struct head **tail = &heads;
    while (*tail) tail = &(*tail)->next;
    *tail = head;
}

static void manager_done(void *data, struct zwlr_output_manager_v1 *wl, uint32_t serial)
{
    (void)data; (void)wl; (void)serial;
    done = true;
}

static void manager_finished(void *data, struct zwlr_output_manager_v1 *wl)
{
    (void)data; (void)wl;
}

static const struct zwlr_output_manager_v1_listener manager_listener = {
    .head = manager_head,
    .done = manager_done,
    .finished = manager_finished,
};

static void global(void *data, struct wl_registry *registry, uint32_t id, const char *interface,
    uint32_t version)
{
    (void)data;
    if (strcmp(interface, zwlr_output_manager_v1_interface.name) == 0)
    {
        manager = wl_registry_bind(registry, id, &zwlr_output_manager_v1_interface,
            version < 4 ? version : 4);
        zwlr_output_manager_v1_add_listener(manager, &manager_listener, NULL);
    }
}

static void global_remove(void *data, struct wl_registry *registry, uint32_t id)
{
    (void)data; (void)registry; (void)id;
}

static const struct wl_registry_listener registry_listener = {global, global_remove};

static void json_string(const char *text)
{
    putchar('"');
    for (const unsigned char *c = (const unsigned char*)(text ? text : ""); *c; ++c)
    {
        if (*c == '"' || *c == '\\') printf("\\%c", *c);
        else if (*c < 0x20) printf("\\u%04x", *c);
        else putchar(*c);
    }

    putchar('"');
}

static void json_mode(const struct mode *mode, bool with_preferred)
{
    printf("{\"width\":%d,\"height\":%d,\"refresh\":%.3f", mode->width, mode->height,
        mode->refresh / 1000.0);
    if (with_preferred) printf(",\"preferred\":%s", mode->preferred ? "true" : "false");
    putchar('}');
}

int main(int argc, char **argv)
{
    (void)argv;
    if (argc > 1)
    {
        fprintf(stderr, "usage: scottland-output-heads\n");
        return 64;
    }

    struct wl_display *display = wl_display_connect(NULL);
    if (!display)
    {
        fprintf(stderr, "scottland-output-heads: no Wayland display\n");
        return 2;
    }

    struct wl_registry *registry = wl_display_get_registry(display);
    wl_registry_add_listener(registry, &registry_listener, NULL);
    wl_display_roundtrip(display);
    if (!manager)
    {
        fprintf(stderr, "scottland-output-heads: the compositor has no output management\n");
        return 2;
    }

    // The manager sends every head with its properties, then done.
    while (!done && wl_display_dispatch(display) != -1) {}
    if (!done)
    {
        fprintf(stderr, "scottland-output-heads: the compositor closed the connection\n");
        return 2;
    }

    putchar('[');
    for (struct head *head = heads; head; head = head->next)
    {
        printf("{\"name\":");
        json_string(head->name);
        printf(",\"description\":");
        json_string(head->description);
        printf(",\"make\":");
        json_string(head->make);
        printf(",\"model\":");
        json_string(head->model);
        printf(",\"serial\":");
        json_string(head->serial);
        printf(",\"physical_width\":%d,\"physical_height\":%d,\"enabled\":%s,\"mode\":",
            head->physical_width, head->physical_height, head->enabled ? "true" : "false");
        if (head->enabled && head->current) json_mode(head->current, false);
        else printf("null");
        printf(",\"modes\":[");
        for (struct mode *mode = head->modes; mode; mode = mode->next)
        {
            json_mode(mode, true);
            if (mode->next) putchar(',');
        }

        printf("],\"x\":%d,\"y\":%d,\"scale\":%.6g,\"transform\":%d,\"adaptive_sync\":%s}",
            head->x, head->y, head->scale, head->transform, head->adaptive_sync ? "true" : "false");
        if (head->next) putchar(',');
    }

    printf("]\n");
    wl_display_disconnect(display);
    return 0;
}
