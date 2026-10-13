// Turn outputs off and on (DPMS) and report their power state, through the compositor's
// wlr-output-power-management protocol: the same path any power tool (wlopm, swayidle) uses, so
// Wayfire applies it itself and every client sees one state.
//
//   scottland-output-power [--output NAME] status|on|off|toggle
//
// Prints one JSON object per output, as the compositor reports it after the request:
//   {"name":"DP-1","power":"on"}
// Exits 1 if the compositor refused the change (the output's "failed" event), 2 for an unknown
// output or a compositor without the protocol, 64 for a usage error.
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wayland-client.h>
#include "wlr-output-power-management-unstable-v1-client-protocol.h"

enum action { STATUS, ON, OFF, TOGGLE };

struct output
{
    struct wl_output *wl;
    struct zwlr_output_power_v1 *power;
    char *name;
    int mode;  // -1 until the compositor reports it
    bool failed;
    struct output *next;
};

static struct zwlr_output_power_manager_v1 *manager;
static struct output *outputs;

static void output_name(void *data, struct wl_output *wl, const char *name)
{
    (void)wl;
    struct output *output = data;
    free(output->name);
    output->name = strdup(name);
}

static void output_noop() {}

static const struct wl_output_listener output_listener = {
    .geometry = (void*)output_noop,
    .mode = (void*)output_noop,
    .done = (void*)output_noop,
    .scale = (void*)output_noop,
    .name = output_name,
    .description = (void*)output_noop,
};

static void power_mode(void *data, struct zwlr_output_power_v1 *power, uint32_t mode)
{
    (void)power;
    ((struct output*)data)->mode = (int)mode;
}

static void power_failed(void *data, struct zwlr_output_power_v1 *power)
{
    (void)power;
    ((struct output*)data)->failed = true;
}

static const struct zwlr_output_power_v1_listener power_listener = {
    .mode = power_mode,
    .failed = power_failed,
};

static void global(void *data, struct wl_registry *registry, uint32_t id, const char *interface,
    uint32_t version)
{
    (void)data;
    if (strcmp(interface, wl_output_interface.name) == 0 && version >= 4)
    {
        struct output *output = calloc(1, sizeof(*output));
        output->mode = -1;
        output->wl = wl_registry_bind(registry, id, &wl_output_interface, 4);
        wl_output_add_listener(output->wl, &output_listener, output);
        output->next = outputs;
        outputs = output;
    } else if (strcmp(interface, zwlr_output_power_manager_v1_interface.name) == 0)
    {
        manager = wl_registry_bind(registry, id, &zwlr_output_power_manager_v1_interface, 1);
    }
}

static void global_remove(void *data, struct wl_registry *registry, uint32_t id)
{
    (void)data; (void)registry; (void)id;
}

static const struct wl_registry_listener registry_listener = {global, global_remove};

static int usage(void)
{
    fprintf(stderr, "usage: scottland-output-power [--output NAME] status|on|off|toggle\n");
    return 64;
}

int main(int argc, char **argv)
{
    const char *only = NULL;
    enum action action = STATUS;
    for (int i = 1; i < argc; ++i)
    {
        if (strcmp(argv[i], "--output") == 0 && i + 1 < argc) only = argv[++i];
        else if (strcmp(argv[i], "status") == 0) action = STATUS;
        else if (strcmp(argv[i], "on") == 0) action = ON;
        else if (strcmp(argv[i], "off") == 0) action = OFF;
        else if (strcmp(argv[i], "toggle") == 0) action = TOGGLE;
        else return usage();
    }

    struct wl_display *display = wl_display_connect(NULL);
    if (!display)
    {
        fprintf(stderr, "scottland-output-power: no Wayland display\n");
        return 2;
    }

    struct wl_registry *registry = wl_display_get_registry(display);
    wl_registry_add_listener(registry, &registry_listener, NULL);
    wl_display_roundtrip(display);  // globals
    wl_display_roundtrip(display);  // output names
    if (!manager)
    {
        fprintf(stderr, "scottland-output-power: the compositor has no output power management\n");
        return 2;
    }

    bool found = false;
    for (struct output *output = outputs; output; output = output->next)
    {
        if (only && (!output->name || strcmp(output->name, only) != 0)) continue;
        found = true;
        output->power = zwlr_output_power_manager_v1_get_output_power(manager, output->wl);
        zwlr_output_power_v1_add_listener(output->power, &power_listener, output);
    }

    if (!found)
    {
        fprintf(stderr, "scottland-output-power: no output %s\n", only ? only : "");
        return 2;
    }

    wl_display_roundtrip(display);  // current modes
    for (struct output *output = outputs; output; output = output->next)
    {
        if (!output->power || output->failed || action == STATUS) continue;
        uint32_t wanted = action == ON ? ZWLR_OUTPUT_POWER_V1_MODE_ON :
            action == OFF ? ZWLR_OUTPUT_POWER_V1_MODE_OFF :
            (output->mode == ZWLR_OUTPUT_POWER_V1_MODE_ON ? ZWLR_OUTPUT_POWER_V1_MODE_OFF :
                ZWLR_OUTPUT_POWER_V1_MODE_ON);
        zwlr_output_power_v1_set_mode(output->power, wanted);
    }

    // The compositor answers a set_mode with the resulting mode event (or failed).
    wl_display_roundtrip(display);
    wl_display_roundtrip(display);

    int status = 0;
    for (struct output *output = outputs; output; output = output->next)
    {
        if (!output->power) continue;
        if (output->failed || output->mode < 0)
        {
            printf("{\"name\":\"%s\",\"power\":\"unknown\"}\n", output->name ? output->name : "");
            status = 1;
            continue;
        }

        printf("{\"name\":\"%s\",\"power\":\"%s\"}\n", output->name ? output->name : "",
            output->mode == ZWLR_OUTPUT_POWER_V1_MODE_ON ? "on" : "off");
        if ((action == ON && output->mode != ZWLR_OUTPUT_POWER_V1_MODE_ON) ||
            (action == OFF && output->mode != ZWLR_OUTPUT_POWER_V1_MODE_OFF))
        {
            status = 1;
        }
    }

    wl_display_disconnect(display);
    return status;
}
