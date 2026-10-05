// Writes every goo program variant, in both GLSL dialects, exactly as the renderer assembles it
// (goo-shaders.hpp: program_variants, vertex_source, fragment_source), for
// tests/goo-shader-variants-test.sh to compile with glslangValidator.
#include "goo-shaders.hpp"
#include <fstream>
#include <iostream>

int main(int argc, char **argv)
{
    if (argc != 2)
    {
        std::cerr << "usage: goo-shader-variants-test OUTDIR\n";
        return 2;
    }
    std::string out = argv[1];
    for (bool es3 : {false, true})
    {
        std::string dialect = es3 ? "es300" : "es100";
        std::ofstream(out + "/vertex." + dialect + ".vert") << scottland::goo::vertex_source(es3);
        for (auto &variant : scottland::goo::program_variants())
        {
            if (variant.es3_only && !es3)
                continue;
            std::ofstream(out + "/" + variant.name + "." + dialect + ".frag") <<
                scottland::goo::fragment_source(variant, es3);
            std::cout << variant.name << "." << dialect << "\n";
        }
    }
    return 0;
}
