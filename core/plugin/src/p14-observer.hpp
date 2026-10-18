#pragma once
// Private P14 test observation: actual submitted pixels, never a scene-geometry verdict.
#include <wayfire/util.hpp>
#include <wayfire/plugins/ipc/ipc-method-repository.hpp>
#include <fstream>
#include <vector>
#include <ctime>
#include <drm_fourcc.h>
extern "C" {
#include <wlr/backend/headless.h>
#include <wlr/render/gles2.h>
#include <wlr/render/drm_syncobj.h>
#include <wlr/render/wlr_texture.h>
#include <wlr/types/wlr_output.h>
#include <wlr/types/wlr_buffer.h>
#include <wlr/types/wlr_pointer.h>
#include <wlr/version.h>
}

namespace scottland
{
class p14_observer_t
{
    wlr_output *output = nullptr;
    wf::wl_listener_wrapper precommit, commit, present, destroyed;
    wf::wl_timer<false> deadline;
    std::ofstream log;
    std::vector<uint32_t> pixels;
    std::string directory, nonce, error;
    uint64_t start = 0, events = 0;
    unsigned images = 0;

    static uint64_t now()
    {
        timespec t{};
        clock_gettime(CLOCK_MONOTONIC, &t);
        return uint64_t(t.tv_sec) * 1000000000 + t.tv_nsec;
    }

    void write(wf::json_t row)
    {
        row["event"] = (int64_t)++events;
        row["ns"] = (int64_t)now();
        row["nonce"] = nonce;
        log << row.serialize() << '\n';
        log.flush();
        if (!log) error = "observation log write failed";
    }

    void fail(const std::string& why)
    {
        if (!error.empty()) return;
        error = why;
        wf::json_t row;
        row["type"] = "gap";
        row["reason"] = why;
        write(row);
    }

    bool budget()
    {
        if (now() - start > 8000000000ULL) fail("eight-second observation bound exceeded");
        if (events >= 1024) fail("observation event bound exceeded");
        return error.empty();
    }

    void submitted(wlr_output_event_precommit *ev)
    {
        if (!budget()) return;
        const auto *s = ev->state;
        wf::json_t row;
        row["type"] = "precommit";
        row["seq"] = (int64_t)uint32_t(output->commit_seq + 1);
        row["fields"] = (int64_t)s->committed;
        const auto began = now();
        // Only unchanged state or damage/synchronization bookkeeping may carry an old image.
        const uint32_t allowed = WLR_OUTPUT_STATE_BUFFER | WLR_OUTPUT_STATE_DAMAGE |
            WLR_OUTPUT_STATE_SIGNAL_TIMELINE | WLR_OUTPUT_STATE_WAIT_TIMELINE;
        if ((s->committed & ~allowed) || s->layers_len || s->color_transform ||
            s->image_description || s->tearing_page_flip || output->scale != 1 ||
            output->transform != WL_OUTPUT_TRANSFORM_NORMAL || !output->enabled ||
            output->width != 1280 || output->height != 720 || output->cursor_front_buffer ||
            !wl_list_empty(&output->layers) || output->image_description)
        {
            fail("unaccounted visible state or explicit producer synchronization");
            return;
        }
        if (s->committed & WLR_OUTPUT_STATE_BUFFER)
        {
            if (!s->buffer || s->buffer->width != 1280 || s->buffer->height != 720 ||
                s->buffer_src_box.x || s->buffer_src_box.y || s->buffer_src_box.width ||
                s->buffer_src_box.height || s->buffer_dst_box.x || s->buffer_dst_box.y ||
                s->buffer_dst_box.width || s->buffer_dst_box.height)
            {
                fail("unsupported submitted-buffer size or viewport");
                return;
            }
            if (images == 128) { fail("128-image observation bound exceeded"); return; }
            // Refuse an unsignalled explicit producer before reading; never wait or retry.
            if (s->wait_timeline)
            {
                bool signalled = false;
                if (!wlr_drm_syncobj_timeline_check(s->wait_timeline, s->wait_point, 0, &signalled) ||
                    !signalled) { fail("submitted-buffer producer is not ready"); return; }
            }
            // Readback synchronizes this GLES renderer's submitted image.
            auto texture = wlr_texture_from_buffer(output->renderer, s->buffer);
            if (!texture) { fail("submitted-buffer texture import unavailable"); return; }
            auto format = wlr_texture_preferred_read_format(texture);
            const bool rgb = format == DRM_FORMAT_ARGB8888 || format == DRM_FORMAT_XRGB8888;
            const bool bgr = format == DRM_FORMAT_ABGR8888 || format == DRM_FORMAT_XBGR8888;
            pixels.resize(1280 * 720);
            wlr_texture_read_pixels_options opts{};
            opts.data = pixels.data(); opts.format = format; opts.stride = 1280 * 4;
            bool read = (rgb || bgr) && wlr_texture_read_pixels(texture, &opts);
            wlr_texture_destroy(texture);
            if (!read) { fail("submitted-buffer synchronized RGB readback unavailable"); return; }
            auto bytes = reinterpret_cast<unsigned char*>(pixels.data());
            for (size_t i = 0; i < pixels.size(); ++i)
            {
                auto p = pixels[i]; // compact in place; never overwrite a later source pixel
                bytes[3*i] = (p >> (rgb ? 16 : 0)) & 255;
                bytes[3*i+1] = (p >> 8) & 255;
                bytes[3*i+2] = (p >> (rgb ? 0 : 16)) & 255;
            }
            const auto file = "frame-" + std::to_string(++images) + ".ppm";
            std::ofstream image(directory + "/" + file, std::ios::binary);
            image << "P6\n1280 720\n255\n";
            image.write(reinterpret_cast<char*>(bytes), 1280 * 720 * 3);
            image.close();
            if (!image) { fail("submitted-buffer image write failed"); return; }
            row["file"] = file;
            row["format"] = (int64_t)format;
            row["bytes"] = (int64_t)(1280 * 720 * 3);
        }
        else if (s->wait_timeline)
        {
            fail("producer timeline without an observed buffer"); return;
        }
        row["cost_ns"] = (int64_t)(now() - began);
        write(row);
        budget(); // retain a readback/write that itself crossed the deadline as incomplete
    }

  public:
    int64_t cover = 0;
    bool active() const { return output != nullptr; }

    bool arm(wlr_output *o, int64_t id, const std::string& path, const std::string& run)
    {
        if (active() || !o || !o->renderer || !wlr_output_is_headless(o) || !wlr_renderer_is_gles2(o->renderer) ||
            o->width != 1280 || o->height != 720 || o->scale != 1 ||
            o->transform != WL_OUTPUT_TRANSFORM_NORMAL || !o->enabled ||
            std::string(WLR_VERSION_STR) != "0.20.2") return false;
        directory = path; nonce = run; cover = id;
        error.clear(); events = images = 0; start = now();
        log.open(path + "/events.jsonl", std::ios::out | std::ios::trunc);
        if (!log) return false;
        output = o;
        wf::json_t row;
        row["type"] = "arm"; row["cover"] = cover; row["output"] = o->name;
        row["seq"] = (int64_t)o->commit_seq; row["backend"] = "headless-synthetic";
        row["wlroots_headers"] = WLR_VERSION_STR;
        row["width"] = o->width; row["height"] = o->height;
        write(row);
        precommit.set_callback([this] (void *data)
        {
            try { submitted(static_cast<wlr_output_event_precommit*>(data)); }
            catch (...) { fail("submitted-buffer observer exception"); }
        });
        commit.set_callback([this] (void *data)
        {
            if (!budget()) return;
            auto ev = static_cast<wlr_output_event_commit*>(data);
            wf::json_t row;
            row["type"] = "commit"; row["seq"] = (int64_t)output->commit_seq;
            row["fields"] = (int64_t)ev->state->committed;
            write(row);
        });
        present.set_callback([this] (void *data)
        {
            if (!budget()) return;
            auto ev = static_cast<wlr_output_event_present*>(data);
            wf::json_t row;
            row["type"] = "present"; row["seq"] = (int64_t)ev->commit_seq;
            row["presented"] = ev->presented;
            row["when_ns"] = (int64_t)(uint64_t(ev->when.tv_sec)*1000000000 + ev->when.tv_nsec);
            row["refresh"] = ev->refresh; row["refresh_seq"] = (int64_t)ev->seq;
            row["flags"] = (int64_t)ev->flags;
            write(row);
        });
        destroyed.set_callback([this] (void*) { fail("observed output destroyed"); stop(); });
        precommit.connect(&o->events.precommit); commit.connect(&o->events.commit);
        present.connect(&o->events.present); destroyed.connect(&o->events.destroy);
        deadline.set_timeout(8000, [this] { fail("eight-second observation bound exceeded"); stop(); });
        return error.empty();
    }

    void marker(const char *kind, int64_t id, uint32_t input_time = 0, uint32_t modifiers = 0,
        bool cancelled = false, const wlr_pointer_button_event *button = nullptr)
    {
        if (!active() || !budget()) return;
        wf::json_t row;
        row["type"] = kind; row["cover"] = id; row["input_time_ms"] = (int64_t)input_time;
        row["modifiers"] = (int64_t)modifiers; row["cancelled"] = cancelled;
        row["seq"] = (int64_t)output->commit_seq;
        if (button)
        {
            row["pointer"] = (int64_t)reinterpret_cast<uintptr_t>(button->pointer);
            row["button"] = (int64_t)button->button; row["state"] = (int64_t)button->state;
        }
        write(row);
    }

    wf::json_t stop()
    {
        wf::json_t row;
        if (active())
        {
            budget(); row["type"] = "stop"; row["seq"] = (int64_t)output->commit_seq;
            row["error"] = error; row["images"] = (int64_t)images;
            write(row);
        }
        deadline.disconnect();
        precommit.disconnect(); commit.disconnect(); present.disconnect(); destroyed.disconnect();
        output = nullptr; log.close(); std::vector<uint32_t>().swap(pixels);
        row["error"] = error;
        return row;
    }

    ~p14_observer_t() { stop(); }
};
}
