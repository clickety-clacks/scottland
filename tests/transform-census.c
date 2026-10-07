// Test-only LD_PRELOAD for a headless Wayfire: an independent census of wlroots color transforms
// (and of writes of SCOTTLAND_INTERNAL_MODEL_VERSION to the environment, which glibc never frees).
// It wraps their creation and the references render targets take, and free(), so it sees when each
// one is really released, whoever releases it. Each event's call stack attributes it: to the
// Scottland plugin at all, and to the paths named in `tags`. Every 100 ms the counts are written to
// $SCOTTLAND_TEST_STATE/transform-census.json. Only the process named "wayfire" counts anything; it
// takes LD_PRELOAD out of its environment so its clients don't load this.
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <execinfo.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

struct wlr_color_transform;
extern void __libc_free(void *);

static const char *tags[] = {
    "widget_image_t7capture",   // widget morph capture (its snapshot fallback renders a target)
    "13take_snapshot",          // any view snapshot (Wayfire's or Scottland's)
    "live_drag_transform_t",    // live drag
    "frame_render_instance_t",  // the window frame transformer
    "9view_2d_t10instance_t",   // Scottland's view_2d (the hint offset)
    "13shape_cache_t",          // goo shape capture
    "10goo_node_t",             // goo wallpaper capture
    "widget_morph_renderer",    // morph blend freeze
};
#define TAGS (sizeof(tags) / sizeof(tags[0]))
#define SLOTS (1u << 20)

static int active;
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static uintptr_t table[SLOTS];          // live transforms: pointer | 1 if Scottland created it
static unsigned long created, created_scottland, live, live_scottland, freed, overflow;
static unsigned long refs, refs_scottland, ref_tags[TAGS], create_tags[TAGS], version_setenv;
static const char *out_path;
static struct wlr_color_transform *(*real_init)(int);
static struct wlr_color_transform *(*real_ref)(struct wlr_color_transform *);

// Bit 0: a Scottland frame; bit 1 + i: tags[i].
static unsigned attribute(void)
{
    void *frames[64];
    int n = backtrace(frames, 64);
    unsigned bits = 0;
    for (int i = 2; i < n; ++i)
    {
        Dl_info info;
        if (!dladdr(frames[i], &info)) continue;
        if (info.dli_fname && strstr(info.dli_fname, "libscottland")) bits |= 1;
        if (info.dli_sname)
            for (unsigned t = 0; t < TAGS; ++t)
                if (strstr(info.dli_sname, tags[t])) bits |= 2u << t;
    }
    return bits;
}

static size_t slot_of(uintptr_t p) { return (size_t)((p >> 4) * 0x9E3779B97F4A7C15ull >> 44) & (SLOTS - 1); }

struct wlr_color_transform *wlr_color_transform_init_linear_to_inverse_eotf(int tf)
{
    if (!real_init) real_init = dlsym(RTLD_NEXT, "wlr_color_transform_init_linear_to_inverse_eotf");
    struct wlr_color_transform *t = real_init(tf);
    if (!active || !t) return t;
    unsigned bits = attribute();
    pthread_mutex_lock(&lock);
    ++created; ++live;
    if (bits & 1) { ++created_scottland; ++live_scottland; }
    for (unsigned i = 0; i < TAGS; ++i) if (bits & (2u << i)) ++create_tags[i];
    size_t s = slot_of((uintptr_t)t), probes = 0;
    while (table[s] && probes++ < SLOTS) s = (s + 1) & (SLOTS - 1);
    if (probes >= SLOTS) ++overflow; else table[s] = (uintptr_t)t | (bits & 1);
    pthread_mutex_unlock(&lock);
    return t;
}

struct wlr_color_transform *wlr_color_transform_ref(struct wlr_color_transform *t)
{
    if (!real_ref) real_ref = dlsym(RTLD_NEXT, "wlr_color_transform_ref");
    if (active && t)
    {
        unsigned bits = attribute();
        pthread_mutex_lock(&lock);
        ++refs;
        if (bits & 1) ++refs_scottland;
        for (unsigned i = 0; i < TAGS; ++i) if (bits & (2u << i)) ++ref_tags[i];
        pthread_mutex_unlock(&lock);
    }
    return real_ref(t);
}

int setenv(const char *name, const char *value, int overwrite)
{
    static int (*real_setenv)(const char *, const char *, int);
    if (!real_setenv) real_setenv = dlsym(RTLD_NEXT, "setenv");
    if (active && name && !strcmp(name, "SCOTTLAND_INTERNAL_MODEL_VERSION"))
    {
        pthread_mutex_lock(&lock);
        ++version_setenv;
        pthread_mutex_unlock(&lock);
    }
    return real_setenv(name, value, overwrite);
}

void free(void *p)
{
    if (p && active)
    {
        pthread_mutex_lock(&lock);
        size_t s = slot_of((uintptr_t)p), probes = 0;
        while (table[s] && probes++ < SLOTS)
        {
            if ((table[s] & ~(uintptr_t)1) == (uintptr_t)p)
            {
                --live; ++freed;
                if (table[s] & 1) --live_scottland;
                // Backward-shift deletion keeps every remaining probe chain intact.
                size_t hole = s, next = (s + 1) & (SLOTS - 1);
                while (table[next])
                {
                    size_t home = slot_of(table[next] & ~(uintptr_t)1);
                    if (((next - home) & (SLOTS - 1)) >= ((next - hole) & (SLOTS - 1)))
                    {
                        table[hole] = table[next];
                        hole = next;
                    }
                    next = (next + 1) & (SLOTS - 1);
                }
                table[hole] = 0;
                break;
            }
            s = (s + 1) & (SLOTS - 1);
        }
        pthread_mutex_unlock(&lock);
    }
    __libc_free(p);
}

static void write_counts(void)
{
    char tmp[4096], body[4096];
    int len;
    pthread_mutex_lock(&lock);
    len = snprintf(body, sizeof body,
        "{\"created\": %lu, \"created_scottland\": %lu, \"live\": %lu, \"live_scottland\": %lu, "
        "\"freed\": %lu, \"overflow\": %lu, \"refs\": %lu, \"refs_scottland\": %lu, \"version_setenv\": %lu, "
        "\"ref_tags\": {",
        created, created_scottland, live, live_scottland, freed, overflow, refs, refs_scottland, version_setenv);
    for (unsigned i = 0; i < TAGS; ++i)
        len += snprintf(body + len, sizeof body - len, "%s\"%s\": %lu", i ? ", " : "", tags[i], ref_tags[i]);
    len += snprintf(body + len, sizeof body - len, "}, \"create_tags\": {");
    for (unsigned i = 0; i < TAGS; ++i)
        len += snprintf(body + len, sizeof body - len, "%s\"%s\": %lu", i ? ", " : "", tags[i], create_tags[i]);
    len += snprintf(body + len, sizeof body - len, "}}\n");
    pthread_mutex_unlock(&lock);
    snprintf(tmp, sizeof tmp, "%s.tmp", out_path);
    FILE *f = fopen(tmp, "w");
    if (!f) return;
    fwrite(body, 1, len, f);
    fclose(f);
    rename(tmp, out_path);
}

static void *writer(void *unused)
{
    (void)unused;
    for (;;) { write_counts(); usleep(100000); }
    return NULL;
}

static void after_fork_child(void)
{
    active = 0;  // a forked child (before exec) counts nothing and must not wait on the lock
    pthread_mutex_init(&lock, NULL);
}

__attribute__((constructor)) static void start(void)
{
    if (strcmp(program_invocation_short_name, "wayfire")) return;
    unsetenv("LD_PRELOAD");
    const char *state = getenv("SCOTTLAND_TEST_STATE");
    if (!state) return;
    static char path[4096];
    snprintf(path, sizeof path, "%s/transform-census.json", state);
    out_path = path;
    void *warm[4];
    backtrace(warm, 4);  // loads the unwinder now, outside any wrapped call
    pthread_atfork(NULL, NULL, after_fork_child);
    active = 1;
    pthread_t thread;
    pthread_create(&thread, NULL, writer, NULL);
    pthread_detach(thread);
}
