#pragma once

#include <string>
#include <string_view>
#include <utility>

namespace scottland::clipboard {

inline bool is_nul_terminated_keymap(std::string_view keymap) {
    return keymap.size() > 1 && keymap.back() == '\0' &&
           keymap.find('\0') == keymap.size() - 1;
}

class KeymapRelay {
  public:
    bool should_send(std::string_view canonical_keymap) const {
        return is_nul_terminated_keymap(canonical_keymap) && canonical_keymap != last_sent_;
    }

    void remember_sent(std::string canonical_keymap) noexcept {
        last_sent_ = std::move(canonical_keymap);
    }

  private:
    std::string last_sent_;
};

} // namespace scottland::clipboard
