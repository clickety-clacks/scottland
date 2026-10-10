#include "../src/input-disabled-record.hpp"

#include <cstdio>
#include <cstdlib>
#include <optional>
#include <string>
#include <vector>

namespace
{
struct fake_device_t
{
    std::string kind;
    std::string name;
    bool enabled = true;
};

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
        [] (fake_device_t& device) { device.enabled = false; });
}
}

int main()
{
    std::vector<fake_device_t> devices = {
        {"pointer", "Laptop Touchpad"},
        {"pointer", "USB mouse"},
        {"touch", "Laptop Touchpad"},
        {"tablet", "Laptop Touchpad"},
    };
    apply("touchpad", std::string("Laptop Touchpad"), devices);
    require(!devices[0].enabled, "exact-name pointer touchpad was not disabled");
    require(devices[1].enabled, "different-name pointer was disabled");
    require(devices[2].enabled, "same-name touch device was treated as a touchpad");
    require(devices[3].enabled, "same-name tablet was treated as a touchpad");

    std::vector<fake_device_t> touchscreen_devices = {
        {"touch", "Panel touch"},
        {"tablet", "Pen tablet"},
        {"pointer", "Panel touch"},
    };
    apply("touchscreen", std::string("Pen tablet"), touchscreen_devices);
    require(touchscreen_devices[0].enabled, "unrecorded touch device was disabled");
    require(!touchscreen_devices[1].enabled, "exact-name tablet was not disabled as touchscreen");
    require(touchscreen_devices[2].enabled, "same-name pointer was treated as touchscreen");

    std::vector<fake_device_t> current = {{"pointer", "Laptop Touchpad"}};
    std::optional<std::string> current_record = "Laptop Touchpad";
    apply("touchpad", current_record, current);
    require(!current[0].enabled, "current off record was not applied");
    current[0].enabled = true;
    current_record.reset(); // the on action removes the record
    apply("touchpad", current_record, current);
    require(current[0].enabled, "removed on record was reapplied");

    std::vector<fake_device_t> absent = {{"pointer", "Laptop Touchpad"}};
    apply("touchpad", std::nullopt, absent);
    require(absent[0].enabled, "absent record disabled a device");

    std::vector<fake_device_t> malformed = {{"pointer", "Laptop Touchpad"}};
    apply("touchpad", std::string("Laptop Touchpad\nOther device"), malformed);
    require(malformed[0].enabled, "malformed multi-line record disabled a device");
    require(!scottland::input::valid_device_name("Laptop Touchpad\nOther device"),
        "newline-containing device name was accepted");

    return 0;
}
