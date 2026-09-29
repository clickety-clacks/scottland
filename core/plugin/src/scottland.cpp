#include <wayfire/plugin.hpp>
#include <wayfire/util/log.hpp>

// Scottland layout plugin. Placeholder: proves the build, packaging and load path
// before the spatial layout (scaling side rails, full-scale center, widget snapping) lands.
class scottland_plugin_t : public wf::plugin_interface_t
{
  public:
    void init() override
    {
        LOGI("scottland: plugin loaded");
    }

    void fini() override
    {
        LOGI("scottland: plugin unloaded");
    }
};

DECLARE_WAYFIRE_PLUGIN(scottland_plugin_t);
