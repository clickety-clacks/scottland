#pragma once
#include "goo-renderer.hpp"
#include <functional>
#include <map>
#include <wayfire/output.hpp>

namespace scottland::goo
{
struct screen_t
{
    wf::output_t *output = nullptr;
    std::vector<source_t> sources;
    settings_t settings;
    renderer_t renderer;
    float time = 0, breath = 0;
    bool sleeping = false;
    std::vector<glm::vec4> impulses;
    std::function<void()> wake;
};
extern bool enabled;
extern settings_t current_settings;
extern std::map<wf::output_t *, screen_t *> screens;
} // namespace scottland::goo
