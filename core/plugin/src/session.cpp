#include "session.hpp"
#include <wayfire/core.hpp>
#include <wayfire/output.hpp>
#include <wayfire/output-layout.hpp>
#include <wayfire/scene.hpp>
#include <wayfire/input-device.hpp>
#include <wayfire/signal-definitions.hpp>
#include <wayfire/option-wrapper.hpp>
#include <wayfire/config/compound-option.hpp>
#include <wayfire/plugins/common/shared-core-data.hpp>
#include <wayfire/plugins/ipc/ipc-method-repository.hpp>
#include <wayfire/plugins/ipc/ipc-helpers.hpp>
#include <wayfire/util/log.hpp>
extern "C" {
#include <wlr/backend/headless.h>
#include <wlr/backend/multi.h>
#include <wlr/interfaces/wlr_switch.h>
#include <wlr/types/wlr_input_device.h>
}
#include <cstdlib>
#include <ctime>
#include <map>
#include <string>

namespace scottland
{
bool session_locked()
{
    for (auto output : wf::get_core().output_layout->get_outputs())
    {
        auto lock_layer = output->node_for_layer(wf::scene::layer::LOCK);
        if (output->is_inhibited() && lock_layer && !lock_layer->get_children().empty())
        {
            return true;
        }
    }

    return false;
}

namespace
{
const wlr_switch_impl test_switch_impl = {.name = "scottland-test-switch"};

/** Virtual switches for test sessions, on their own headless backend like stipc's devices. */
struct test_switches_t
{
    wlr_backend *backend = nullptr;
    std::map<std::string, std::unique_ptr<wlr_switch>> devices;

    wlr_switch *get(const std::string& name)
    {
        auto& core = wf::get_core();
        if (!backend)
        {
            backend = wlr_headless_backend_create(core.ev_loop);
            wlr_multi_backend_add(core.backend, backend);
            wlr_backend_start(backend);
        }

        auto& device = devices[name];
        if (!device)
        {
            device = std::make_unique<wlr_switch>();
            wlr_switch_init(device.get(), &test_switch_impl, name.c_str());
            wl_signal_emit_mutable(&backend->events.new_input, &device->base);
        }

        return device.get();
    }

    ~test_switches_t()
    {
        for (auto& [_, device] : devices)
        {
            wlr_switch_finish(device.get());
        }

        devices.clear();
        if (backend)
        {
            wlr_multi_backend_remove(wf::get_core().backend, backend);
            wlr_backend_destroy(backend);
        }
    }
};
}

struct session_t::impl
{
    wf::shared_data::ref_ptr_t<wf::ipc::method_repository_t> ipc;
    wf::option_wrapper_t<wf::config::compound_list_t<std::string, std::string, std::string>>
    switch_bindings{"scottland/switch_bindings"};
    std::unique_ptr<test_switches_t> test_switches;

    wf::ipc::method_callback state = [] (wf::json_t)
    {
        wf::json_t reply = wf::ipc::json_ok();
        reply["locked"] = session_locked();
        return reply;
    };

    wf::signal::connection_t<wf::switch_signal> on_switch = [=] (wf::switch_signal *ev)
    {
        auto handle = ev->device ? ev->device->get_wlr_handle() : nullptr;
        std::string device = (handle && handle->name) ? handle->name : "";
        for (const auto& [_, name, when, command] : switch_bindings.value())
        {
            if ((name != device) || command.empty())
            {
                continue;
            }

            if ((when == "toggle") || (when == (ev->state ? "on" : "off")))
            {
                LOGI("scottland: switch ", device, " ", ev->state ? "on" : "off", ": ", command);
                wf::get_core().run(command);
            }
        }
    };

    wf::ipc::method_callback test_switch = [=] (wf::json_t data)
    {
        if (!data.has_member("device") || !data["device"].is_string() ||
            !data.has_member("state") || !data["state"].is_bool())
        {
            return wf::ipc::json_error("test-switch needs a string \"device\" and a bool \"state\"");
        }

        if (!test_switches)
        {
            test_switches = std::make_unique<test_switches_t>();
        }

        auto device = test_switches->get(data["device"].as_string());
        timespec now;
        clock_gettime(CLOCK_MONOTONIC, &now);
        wlr_switch_toggle_event event = {};
        event.time_msec    = now.tv_sec * 1000 + now.tv_nsec / 1000000;
        event.switch_type  = WLR_SWITCH_TYPE_LID;
        event.switch_state = data["state"].as_bool() ? WLR_SWITCH_STATE_ON : WLR_SWITCH_STATE_OFF;
        wl_signal_emit_mutable(&device->events.toggle, &event);
        return wf::ipc::json_ok();
    };
};

session_t::session_t() = default;
session_t::~session_t()
{
    fini();
}

void session_t::init()
{
    priv = std::make_unique<impl>();
    priv->ipc->register_method("scottland/session-state", priv->state);
    wf::get_core().connect(&priv->on_switch);
    if (getenv("SCOTTLAND_TEST_MODEL"))
    {
        priv->ipc->register_method("scottland/test-switch", priv->test_switch);
    }
}

void session_t::fini()
{
    if (!priv)
    {
        return;
    }

    priv->ipc->unregister_method("scottland/session-state");
    if (getenv("SCOTTLAND_TEST_MODEL"))
    {
        priv->ipc->unregister_method("scottland/test-switch");
    }

    priv->on_switch.disconnect();
    priv.reset();
}
}
