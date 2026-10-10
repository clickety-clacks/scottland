#include "../src/input-disabled-record.hpp"

#include <cstdio>
#include <cstdlib>
#include <optional>
#include <string>
#include <vector>

namespace
{
enum class send_events_mode_t
{
    enabled,
    disabled_on_external_mouse,
    disabled,
};

struct fake_device_t
{
    std::string kind;
    std::string name;
    send_events_mode_t send_events_mode = send_events_mode_t::enabled;
    bool unrelated_setting = true;
    bool accepts_mode_change = true;
    bool external_mouse_present = false;

    bool emits_input() const
    {
        return send_events_mode == send_events_mode_t::enabled ||
            (send_events_mode == send_events_mode_t::disabled_on_external_mouse &&
             !external_mouse_present);
    }

    bool wayfire_is_enabled() const
    {
        return send_events_mode == send_events_mode_t::enabled;
    }

    bool wayfire_set_enabled(bool enabled)
    {
        if (enabled == wayfire_is_enabled()) return true;
        if (!accepts_mode_change) return false;
        send_events_mode = enabled ? send_events_mode_t::enabled : send_events_mode_t::disabled;
        return true;
    }
};

bool set_enabled(fake_device_t& device, bool enabled)
{
    const auto requested_mode = enabled ? send_events_mode_t::enabled : send_events_mode_t::disabled;
    return scottland::input::set_send_events_mode(requested_mode,
        [&] (send_events_mode_t mode)
        {
            if (!device.accepts_mode_change) return false;
            device.send_events_mode = mode;
            return true;
        },
        [&] { return device.send_events_mode; });
}

void require(bool condition, const char *message)
{
    if (!condition)
    {
        fprintf(stderr, "input-disabled-record-test: %s\n", message);
        std::exit(1);
    }
}

void apply(const std::string& kind, const std::optional<std::string>& record,
    std::vector<fake_device_t>& devices)
{
    scottland::input::apply_disabled_record(kind, record, devices,
        [] (const fake_device_t& device) -> std::optional<std::string>
        {
            return device.name;
        },
        [] (const fake_device_t& device) { return device.kind; },
        [] (fake_device_t& device) { set_enabled(device, false); });
}
}

int main()
{
    // Wayfire's bool setter treats DISABLED_ON_EXTERNAL_MOUSE as already disabled and returns
    // early; the exact mode setter must still force the persistent DISABLED state.
    std::vector<fake_device_t> three_modes = {
        {"pointer", "Enabled touchpad", send_events_mode_t::enabled},
        {"pointer", "Conditional touchpad", send_events_mode_t::disabled_on_external_mouse},
        {"pointer", "Disabled touchpad", send_events_mode_t::disabled},
    };
    for (const auto& device : three_modes)
    {
        auto copy = device;
        require(set_enabled(copy, false), "off did not set an exact send-events mode");
        require(copy.send_events_mode == send_events_mode_t::disabled,
            "off did not force unconditional disabled mode from each Wayfire mode");
        require(copy.unrelated_setting, "off changed an unrelated device setting");
    }

    fake_device_t conditional_off = {"pointer", "Laptop Touchpad",
        send_events_mode_t::disabled_on_external_mouse};
    require(conditional_off.emits_input(),
        "conditional mode fixture should allow input without an external mouse");
    require(conditional_off.wayfire_set_enabled(false), "Wayfire bool setter fixture refused disable");
    require(conditional_off.send_events_mode == send_events_mode_t::disabled_on_external_mouse,
        "Wayfire bool setter fixture no longer demonstrates the conditional-mode early return");
    conditional_off.external_mouse_present = true;
    require(!conditional_off.emits_input(), "conditional mode fixture should suppress input with a mouse");
    conditional_off.external_mouse_present = false;
    require(conditional_off.emits_input(), "conditional mode fixture should resume input after mouse removal");
    require(set_enabled(conditional_off, false), "off did not force disabled from conditional mode");
    require(conditional_off.send_events_mode == send_events_mode_t::disabled,
        "off left a conditional send-events mode in place");
    require(!conditional_off.emits_input(), "forced disabled mode still emitted input without a mouse");
    conditional_off.external_mouse_present = true;
    require(!conditional_off.emits_input(), "forced disabled mode emitted input with a mouse");

    std::vector<fake_device_t> devices = {
        {"pointer", "Laptop Touchpad"},
        {"pointer", "USB mouse"},
        {"touch", "Laptop Touchpad"},
        {"tablet", "Laptop Touchpad"},
    };
    apply("touchpad", std::string("Laptop Touchpad"), devices);
    require(devices[0].send_events_mode == send_events_mode_t::disabled,
        "exact-name pointer touchpad was not unconditionally disabled");
    require(devices[1].send_events_mode == send_events_mode_t::enabled,
        "different-name pointer was disabled");
    require(devices[2].send_events_mode == send_events_mode_t::enabled,
        "same-name touch device was treated as a touchpad");
    require(devices[3].send_events_mode == send_events_mode_t::enabled,
        "same-name tablet was treated as a touchpad");

    std::vector<fake_device_t> touchscreen_devices = {
        {"touch", "Panel touch"},
        {"tablet", "Pen tablet"},
        {"pointer", "Panel touch"},
    };
    apply("touchscreen", std::string("Pen tablet"), touchscreen_devices);
    require(touchscreen_devices[0].send_events_mode == send_events_mode_t::enabled,
        "unrecorded touch device was disabled");
    require(touchscreen_devices[1].send_events_mode == send_events_mode_t::disabled,
        "exact-name tablet was not disabled as touchscreen");
    require(touchscreen_devices[2].send_events_mode == send_events_mode_t::enabled,
        "same-name pointer was treated as touchscreen");

    std::vector<fake_device_t> current = {{"pointer", "Laptop Touchpad",
        send_events_mode_t::disabled_on_external_mouse}};
    std::optional<std::string> current_record = "Laptop Touchpad";
    apply("touchpad", current_record, current);
    require(current[0].send_events_mode == send_events_mode_t::disabled,
        "restore did not turn a Wayfire conditional mode into unconditional disabled");
    require(current[0].unrelated_setting, "restore changed an unrelated device setting");
    require(set_enabled(current[0], true), "on did not set the enabled send-events mode");
    current_record.reset(); // the on action removes the record
    apply("touchpad", current_record, current);
    require(current[0].send_events_mode == send_events_mode_t::enabled,
        "removed on record was reapplied");

    std::vector<fake_device_t> added = {{"pointer", "Laptop Touchpad",
        send_events_mode_t::disabled_on_external_mouse}};
    apply("touchpad", std::string("Laptop Touchpad"), added);
    require(added[0].send_events_mode == send_events_mode_t::disabled,
        "device-added path left matching touchpad conditionally enabled");

    std::vector<fake_device_t> reloaded = {{"pointer", "Laptop Touchpad",
        send_events_mode_t::disabled_on_external_mouse}}; // Wayfire reset before reload callback
    apply("touchpad", std::string("Laptop Touchpad"), reloaded);
    require(reloaded[0].send_events_mode == send_events_mode_t::disabled,
        "reload path left matching touchpad conditionally enabled");

    std::vector<fake_device_t> absent = {{"pointer", "Laptop Touchpad"}};
    apply("touchpad", std::nullopt, absent);
    require(absent[0].send_events_mode == send_events_mode_t::enabled,
        "absent record disabled a device");

    std::vector<fake_device_t> malformed = {{"pointer", "Laptop Touchpad",
        send_events_mode_t::enabled}};
    apply("touchpad", std::string("Laptop Touchpad\nOther device"), malformed);
    require(malformed[0].send_events_mode == send_events_mode_t::enabled,
        "malformed multi-line record disabled a device");
    require(!scottland::input::valid_device_name("Laptop Touchpad\nOther device"),
        "newline-containing device name was accepted");

    fake_device_t refused = {"pointer", "Laptop Touchpad",
        send_events_mode_t::disabled_on_external_mouse, true, false};
    require(!set_enabled(refused, false), "compositor refusal was reported as success");
    require(refused.send_events_mode == send_events_mode_t::disabled_on_external_mouse,
        "refused mode change mutated the device state");

    return 0;
}
