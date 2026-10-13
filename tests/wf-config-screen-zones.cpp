#include <iostream>
#include <memory>
#include <string>

#include <wayfire/config/config-manager.hpp>
#include <wayfire/config/file.hpp>
#include <wayfire/config/option.hpp>
#include <wayfire/config/section.hpp>

int main(int argc, char **argv)
{
    if (argc != 2)
    {
        return 2;
    }

    wf::config::config_manager_t manager;
    auto section = std::make_shared<wf::config::section_t>("scottland");
    auto screen_zones = std::make_shared<wf::config::option_t<std::string>>(
        "screen_zones", "[]");
    section->register_new_option(screen_zones);
    manager.merge_section(section);

    if (!wf::config::load_configuration_options_from_file(manager, argv[1]))
    {
        return 3;
    }

    std::cout << "WF_CONFIG_SCREEN_ZONES=" << screen_zones->get_value_str() << '\n';
    return 0;
}
