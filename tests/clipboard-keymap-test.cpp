#include "keymap-relay.hpp"

#include <iostream>
#include <string>
#include <string_view>

namespace {
int fail(const char *message) {
    std::cerr << "FAIL " << message << '\n';
    return 1;
}
} // namespace

int main() {
    using scottland::clipboard::KeymapRelay;
    using scottland::clipboard::is_nul_terminated_keymap;

    std::string first_map = "serialized XKB map A";
    first_map.push_back('\0');
    if (!is_nul_terminated_keymap(first_map)) return fail("accept one trailing NUL");

    KeymapRelay relay;
    if (!relay.should_send(first_map)) return fail("send the initial keymap");
    relay.remember_sent(first_map);
    if (relay.should_send(first_map)) return fail("suppress an echoed keymap");

    std::string changed_map = "serialized XKB map B";
    changed_map.push_back('\0');
    if (!relay.should_send(changed_map)) return fail("send a changed keymap");
    relay.remember_sent(changed_map);
    if (relay.should_send(changed_map)) return fail("suppress the changed map echo");
    if (!relay.should_send(first_map)) return fail("send a previously used layout after switching back");

    if (relay.should_send("serialized XKB map C")) return fail("reject a missing terminator");

    constexpr char embedded_nul_bytes[] = {'m', 'a', 'p', '\0', 't', 'a', 'i', 'l', '\0'};
    if (relay.should_send(std::string_view(embedded_nul_bytes, sizeof(embedded_nul_bytes)))) {
        return fail("reject embedded NUL bytes");
    }

    std::string double_nul = "serialized XKB map D";
    double_nul.append(2, '\0');
    if (relay.should_send(double_nul)) return fail("reject multiple trailing NUL bytes");

    std::cout << "PASS keymap payload and echo filtering\n";
    return 0;
}
