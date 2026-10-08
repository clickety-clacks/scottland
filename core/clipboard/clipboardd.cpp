#define _GNU_SOURCE

#include <wayland-client.h>
#include <xkbcommon/xkbcommon.h>

#include "wlr-data-control-unstable-v1-client-protocol.h"
#include "virtual-keyboard-unstable-v1-client-protocol.h"

#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <cctype>
#include <csignal>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <fcntl.h>
#include <filesystem>
#include <iostream>
#include <memory>
#include <optional>
#include <poll.h>
#include <stdexcept>
#include <string>
#include <string_view>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>
#include <unordered_map>
#include <utility>
#include <vector>

namespace fs = std::filesystem;
using Bytes = std::vector<uint8_t>;

namespace {
constexpr std::string_view kOwnerMime = "application/x-scottland-clipboard-owner";
constexpr std::string_view kSecretMime = "x-kde-passwordManagerHint";
constexpr uint32_t kMaxFrame = 256 * 1024 * 1024;
constexpr size_t kMaxHistory = 50;
constexpr int kTransferTimeoutMs = 10000;

struct Format {
    std::string mime;
    Bytes data;
};

struct Bundle {
    std::vector<Format> formats;
};

struct Entry {
    uint64_t id;
    std::string mime;
    Bytes data;
};

struct Offer {
    zwlr_data_control_offer_v1 *proxy = nullptr;
    std::vector<std::string> mimes;
};

class Service;

struct Source {
    Service *owner = nullptr;
    Bundle bundle;
};

class Service {
  public:
    Service() { initialize_wayland(); initialize_socket(); }
    ~Service() { shutdown(); }

    void run() {
        const int wayland_fd = wl_display_get_fd(display_);
        while (running_) {
            wl_display_dispatch_pending(display_);
            if (wl_display_flush(display_) < 0 && errno != EAGAIN) {
                throw std::runtime_error("Wayland connection was closed");
            }
            pollfd fds[2]{{wayland_fd, POLLIN, 0}, {server_fd_, POLLIN, 0}};
            int ready;
            do { ready = poll(fds, 2, -1); } while (ready < 0 && errno == EINTR);
            if (ready < 0) throw std::runtime_error("could not wait for clipboard requests");
            if (fds[0].revents & (POLLERR | POLLHUP | POLLNVAL)) {
                throw std::runtime_error("Wayland connection was closed");
            }
            if (fds[0].revents & POLLIN) {
                if (wl_display_dispatch(display_) < 0) throw std::runtime_error("Wayland connection was closed");
            }
            if (fds[1].revents & POLLIN) handle_client();
        }
    }

  private:
    wl_display *display_ = nullptr;
    wl_registry *registry_ = nullptr;
    wl_seat *seat_ = nullptr;
    wl_keyboard *keyboard_ = nullptr;
    zwlr_data_control_manager_v1 *data_manager_ = nullptr;
    zwlr_data_control_device_v1 *data_device_ = nullptr;
    zwp_virtual_keyboard_manager_v1 *keyboard_manager_ = nullptr;
    zwp_virtual_keyboard_v1 *virtual_keyboard_ = nullptr;
    zwlr_data_control_offer_v1 *selection_offer_ = nullptr;
    zwlr_data_control_offer_v1 *primary_offer_ = nullptr;
    std::unordered_map<zwlr_data_control_offer_v1 *, Offer> offers_;
    std::unordered_map<zwlr_data_control_source_v1 *, std::unique_ptr<Source>> sources_;
    Bundle current_clipboard_;
    bool current_clipboard_complete_ = true;
    std::deque<Entry> history_;
    std::unordered_map<uint64_t, Bundle> snapshots_;
    std::vector<fs::path> temp_files_;
    fs::path runtime_dir_;
    fs::path socket_path_;
    fs::path open_dir_;
    int server_fd_ = -1;
    bool running_ = true;
    bool keymap_ready_ = false;
    uint32_t keymap_group_ = 0;
    uint32_t depressed_mods_ = 0;
    uint32_t locked_mods_ = 0;
    uint32_t latched_mods_ = 0;
    uint32_t shift_mask_ = 0;
    uint64_t next_entry_id_ = 1;
    uint64_t next_snapshot_id_ = 1;
    bool transfer_seen_ = false;
    std::string wayland_display_;
    xkb_context *xkb_context_ = nullptr;
    xkb_keymap *xkb_keymap_ = nullptr;

    static void registry_global(void *data, wl_registry *registry, uint32_t name,
                                const char *interface, uint32_t version) {
        auto *self = static_cast<Service *>(data);
        if (!std::strcmp(interface, wl_seat_interface.name)) {
            self->seat_ = static_cast<wl_seat *>(wl_registry_bind(
                registry, name, &wl_seat_interface, std::min(version, 5u)));
        } else if (!std::strcmp(interface, zwlr_data_control_manager_v1_interface.name)) {
            self->data_manager_ = static_cast<zwlr_data_control_manager_v1 *>(wl_registry_bind(
                registry, name, &zwlr_data_control_manager_v1_interface, std::min(version, 2u)));
        } else if (!std::strcmp(interface, zwp_virtual_keyboard_manager_v1_interface.name)) {
            self->keyboard_manager_ = static_cast<zwp_virtual_keyboard_manager_v1 *>(wl_registry_bind(
                registry, name, &zwp_virtual_keyboard_manager_v1_interface, 1));
        }
    }

    static void registry_remove(void *, wl_registry *, uint32_t) {}
    static constexpr wl_registry_listener registry_listener_{registry_global, registry_remove};

    static void keyboard_keymap(void *data, wl_keyboard *, uint32_t format, int fd, uint32_t size) {
        auto *self = static_cast<Service *>(data);
        if (format != WL_KEYBOARD_KEYMAP_FORMAT_XKB_V1 || !size || size > 16 * 1024 * 1024) {
            close(fd);
            return;
        }
        std::string text(size, '\0');
        size_t offset = 0;
        while (offset < size) {
            ssize_t count = pread(fd, text.data() + offset, size - offset, static_cast<off_t>(offset));
            if (count < 0 && errno == EINTR) continue;
            if (count <= 0) break;
            offset += static_cast<size_t>(count);
        }
        close(fd);
        if (offset != size) return;
        self->set_virtual_keymap(text);
    }

    static void keyboard_enter(void *, wl_keyboard *, uint32_t, wl_surface *, wl_array *) {}
    static void keyboard_leave(void *, wl_keyboard *, uint32_t, wl_surface *) {}
    static void keyboard_key(void *, wl_keyboard *, uint32_t, uint32_t, uint32_t, uint32_t) {}
    static void keyboard_modifiers(void *data, wl_keyboard *, uint32_t, uint32_t depressed,
                                   uint32_t latched, uint32_t locked, uint32_t group) {
        auto *self = static_cast<Service *>(data);
        self->depressed_mods_ = depressed;
        self->latched_mods_ = latched;
        self->locked_mods_ = locked;
        self->keymap_group_ = group;
    }
    static void keyboard_repeat(void *, wl_keyboard *, int32_t, int32_t) {}
    static constexpr wl_keyboard_listener keyboard_listener_{keyboard_keymap, keyboard_enter,
        keyboard_leave, keyboard_key, keyboard_modifiers, keyboard_repeat};

    static void source_send(void *data, zwlr_data_control_source_v1 *, const char *mime, int fd) {
        auto *source = static_cast<Source *>(data);
        auto found = std::find_if(source->bundle.formats.begin(), source->bundle.formats.end(),
            [mime](const Format &format) { return format.mime == mime; });
        if (found != source->bundle.formats.end() && found->mime != std::string(kOwnerMime)) {
            source->owner->transfer_seen_ = write_all(fd, found->data.data(), found->data.size());
        }
        close(fd);
    }

    static void source_cancelled(void *data, zwlr_data_control_source_v1 *proxy) {
        auto *source = static_cast<Source *>(data);
        source->owner->remove_source(proxy);
    }
    static constexpr zwlr_data_control_source_v1_listener source_listener_{source_send, source_cancelled};

    static void offer_mime(void *data, zwlr_data_control_offer_v1 *, const char *mime) {
        auto *offer = static_cast<Offer *>(data);
        offer->mimes.emplace_back(mime ? mime : "");
    }
    static constexpr zwlr_data_control_offer_v1_listener offer_listener_{offer_mime};

    static void data_offer(void *data, zwlr_data_control_device_v1 *, zwlr_data_control_offer_v1 *proxy) {
        auto *self = static_cast<Service *>(data);
        auto [it, inserted] = self->offers_.try_emplace(proxy);
        it->second.proxy = proxy;
        zwlr_data_control_offer_v1_add_listener(proxy, &offer_listener_, &it->second);
        (void)inserted;
    }
    static void selection(void *data, zwlr_data_control_device_v1 *, zwlr_data_control_offer_v1 *proxy) {
        static_cast<Service *>(data)->new_selection(proxy, false);
    }
    static void finished(void *data, zwlr_data_control_device_v1 *proxy) {
        auto *self = static_cast<Service *>(data);
        if (proxy) zwlr_data_control_device_v1_destroy(proxy);
        self->data_device_ = nullptr;
    }
    static void primary_selection(void *data, zwlr_data_control_device_v1 *,
                                  zwlr_data_control_offer_v1 *proxy) {
        static_cast<Service *>(data)->new_selection(proxy, true);
    }
    static constexpr zwlr_data_control_device_v1_listener data_device_listener_{
        data_offer, selection, finished, primary_selection};

    static bool write_all(int fd, const uint8_t *data, size_t size) {
        size_t offset = 0;
        while (offset < size) {
            ssize_t count = write(fd, data + offset, size - offset);
            if (count < 0 && errno == EINTR) continue;
            if (count <= 0) return false;
            offset += static_cast<size_t>(count);
        }
        return true;
    }

    std::optional<uint64_t> focused_window() {
        const char *path = std::getenv("WAYFIRE_SOCKET");
        if (!path || !*path) throw std::runtime_error("the Scottland compositor session is unavailable");
        int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
        if (fd < 0) throw std::runtime_error("could not connect to the Scottland compositor");
        sockaddr_un address{};
        address.sun_family = AF_UNIX;
        if (std::strlen(path) >= sizeof(address.sun_path)) {
            close(fd);
            throw std::runtime_error("the Scottland compositor socket path is too long");
        }
        std::strcpy(address.sun_path, path);
        if (connect(fd, reinterpret_cast<sockaddr *>(&address), sizeof(address)) < 0) {
            close(fd);
            throw std::runtime_error("could not connect to the Scottland compositor");
        }
        const std::string request = R"({"method":"scottland/desktop-model","data":{"slice":"desktop"}})";
        uint32_t length = static_cast<uint32_t>(request.size());
        uint8_t header[4]{static_cast<uint8_t>(length), static_cast<uint8_t>(length >> 8),
                          static_cast<uint8_t>(length >> 16), static_cast<uint8_t>(length >> 24)};
        if (!write_exact(fd, header, sizeof(header)) ||
            !write_exact(fd, reinterpret_cast<const uint8_t *>(request.data()), request.size())) {
            close(fd);
            throw std::runtime_error("could not query the focused app window");
        }
        uint8_t size_bytes[4];
        if (!read_exact(fd, size_bytes, sizeof(size_bytes))) {
            close(fd);
            throw std::runtime_error("the compositor returned no focused app window");
        }
        uint32_t size = uint32_t(size_bytes[0]) | (uint32_t(size_bytes[1]) << 8) |
                        (uint32_t(size_bytes[2]) << 16) | (uint32_t(size_bytes[3]) << 24);
        if (!size || size > 16 * 1024 * 1024) {
            close(fd);
            throw std::runtime_error("the compositor returned malformed window state");
        }
        std::string response(size, '\0');
        bool read_ok = read_exact(fd, reinterpret_cast<uint8_t *>(response.data()), response.size());
        close(fd);
        if (!read_ok || response.find("\"error\"") != std::string::npos) {
            throw std::runtime_error("could not read the focused app window");
        }
        auto windows = response.find("\"windows\":[");
        if (windows == std::string::npos) return std::nullopt;
        const size_t array_start = windows + std::string_view("\"windows\":[").size();
        std::optional<uint64_t> active;
        size_t object_start = std::string::npos;
        size_t object_depth = 0;
        bool in_string = false;
        bool escaped = false;
        for (size_t cursor = array_start; cursor < response.size(); ++cursor) {
            const char ch = response[cursor];
            if (in_string) {
                if (escaped) escaped = false;
                else if (ch == '\\') escaped = true;
                else if (ch == '"') in_string = false;
                continue;
            }
            if (ch == '"') {
                in_string = true;
            } else if (ch == '{') {
                if (object_depth++ == 0) object_start = cursor;
            } else if (ch == '}') {
                if (!object_depth) return std::nullopt;
                if (--object_depth == 0) {
                    std::string_view object(response.data() + object_start, cursor - object_start + 1);
                    const auto focused = object.find("\"focused\":true");
                    const auto id_key = object.find("\"id\":");
                    if (focused != std::string_view::npos && id_key != std::string_view::npos) {
                        size_t digits = id_key + 5;
                        while (digits < object.size() && object[digits] >= '0' && object[digits] <= '9') ++digits;
                        if (digits == id_key + 5) return std::nullopt;
                        if (active) return std::nullopt;
                        active = std::stoull(std::string(object.substr(id_key + 5, digits - id_key - 5)));
                    }
                }
            } else if (ch == ']' && object_depth == 0) {
                return active;
            }
        }
        return std::nullopt;
    }

    void require_target(uint64_t expected) {
        auto active = focused_window();
        if (!active || *active != expected) {
            throw std::runtime_error("the app focused before the picker is no longer focused");
        }
    }

    void initialize_wayland() {
        const char *display_name = std::getenv("WAYLAND_DISPLAY");
        wayland_display_ = fs::path(display_name && *display_name ? display_name : "wayland-0").filename().string();
        display_ = wl_display_connect(nullptr);
        if (!display_) throw std::runtime_error("could not connect to the Wayland session");
        registry_ = wl_display_get_registry(display_);
        wl_registry_add_listener(registry_, &registry_listener_, this);
        if (wl_display_roundtrip(display_) < 0) throw std::runtime_error("could not read Wayland protocols");
        if (!seat_) throw std::runtime_error("the Wayland session has no keyboard seat");
        if (!data_manager_) throw std::runtime_error("the compositor does not expose clipboard data-control");
        if (!keyboard_manager_) throw std::runtime_error("the compositor does not expose virtual keyboard input");

        data_device_ = zwlr_data_control_manager_v1_get_data_device(data_manager_, seat_);
        zwlr_data_control_device_v1_add_listener(data_device_, &data_device_listener_, this);
        virtual_keyboard_ = zwp_virtual_keyboard_manager_v1_create_virtual_keyboard(keyboard_manager_, seat_);
        keyboard_ = wl_seat_get_keyboard(seat_);
        if (keyboard_) wl_keyboard_add_listener(keyboard_, &keyboard_listener_, this);
        if (wl_display_roundtrip(display_) < 0 || wl_display_get_error(display_)) {
            throw std::runtime_error("the compositor refused clipboard or virtual keyboard access");
        }
        if (!keymap_ready_) throw std::runtime_error("the session keyboard keymap is unavailable");
    }

    void set_virtual_keymap(const std::string &text) {
        if (!virtual_keyboard_) return;
        int fd = memfd_create("scottland-keymap", MFD_CLOEXEC);
        if (fd < 0) throw std::runtime_error("could not create the virtual keyboard keymap");
        if (!write_all(fd, reinterpret_cast<const uint8_t *>(text.data()), text.size())) {
            close(fd);
            throw std::runtime_error("could not write the virtual keyboard keymap");
        }
        if (lseek(fd, 0, SEEK_SET) < 0) { close(fd); throw std::runtime_error("could not rewind the keyboard keymap"); }
        zwp_virtual_keyboard_v1_keymap(virtual_keyboard_, WL_KEYBOARD_KEYMAP_FORMAT_XKB_V1,
                                       fd, static_cast<uint32_t>(text.size()));
        close(fd);
        if (xkb_keymap_) xkb_keymap_unref(xkb_keymap_);
        if (xkb_context_) xkb_context_unref(xkb_context_);
        xkb_context_ = xkb_context_new(XKB_CONTEXT_NO_FLAGS);
        xkb_keymap_ = xkb_keymap_new_from_string(xkb_context_, text.c_str(),
                                                 XKB_KEYMAP_FORMAT_TEXT_V1, XKB_KEYMAP_COMPILE_NO_FLAGS);
        if (!xkb_keymap_) throw std::runtime_error("the compositor sent an invalid keyboard keymap");
        auto shift = xkb_keymap_mod_get_index(xkb_keymap_, XKB_MOD_NAME_SHIFT);
        shift_mask_ = shift == XKB_MOD_INVALID || shift >= 32 ? 0 : (1u << shift);
        if (!shift_mask_) throw std::runtime_error("the session keymap has no Shift modifier");
        keymap_ready_ = true;
        zwp_virtual_keyboard_v1_modifiers(virtual_keyboard_, 0, latched_mods_, locked_mods_, keymap_group_);
        wl_display_flush(display_);
    }

    void initialize_socket() {
        const char *runtime = std::getenv("XDG_RUNTIME_DIR");
        if (!runtime || !*runtime) throw std::runtime_error("XDG_RUNTIME_DIR is unavailable");
        runtime_dir_ = fs::path(runtime) / "scottland" / ("clipboard-" + wayland_display_);
        fs::create_directories(runtime_dir_);
        chmod(runtime_dir_.c_str(), 0700);
        socket_path_ = runtime_dir_ / "service.sock";
        open_dir_ = runtime_dir_ / "open";
        fs::create_directories(open_dir_);
        chmod(open_dir_.c_str(), 0700);

        struct stat st{};
        if (lstat(socket_path_.c_str(), &st) == 0) {
            if (!S_ISSOCK(st.st_mode) || st.st_uid != getuid()) {
                throw std::runtime_error("the clipboard service path is owned by another object");
            }
            int probe = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
            sockaddr_un address{};
            address.sun_family = AF_UNIX;
            std::strncpy(address.sun_path, socket_path_.c_str(), sizeof(address.sun_path) - 1);
            if (connect(probe, reinterpret_cast<sockaddr *>(&address), sizeof(address)) == 0) {
                close(probe);
                throw std::runtime_error("the Scottland clipboard service is already running");
            }
            int error = errno;
            close(probe);
            if (error != ECONNREFUSED && error != ENOENT) {
                throw std::runtime_error("could not inspect the clipboard service socket");
            }
            if (unlink(socket_path_.c_str()) < 0) throw std::runtime_error("could not clear a stale clipboard socket");
        }

        server_fd_ = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
        if (server_fd_ < 0) throw std::runtime_error("could not create the clipboard service socket");
        sockaddr_un address{};
        address.sun_family = AF_UNIX;
        auto socket_text = socket_path_.string();
        if (socket_text.size() >= sizeof(address.sun_path)) throw std::runtime_error("clipboard service path is too long");
        std::memcpy(address.sun_path, socket_text.c_str(), socket_text.size() + 1);
        if (bind(server_fd_, reinterpret_cast<sockaddr *>(&address), sizeof(address)) < 0 ||
            chmod(socket_path_.c_str(), 0600) < 0 || listen(server_fd_, 8) < 0) {
            throw std::runtime_error("could not open the clipboard service socket");
        }
    }

    void shutdown() {
        if (server_fd_ >= 0) close(server_fd_);
        if (!socket_path_.empty()) unlink(socket_path_.c_str());
        for (const auto &file : temp_files_) unlink(file.c_str());
        if (keyboard_) wl_keyboard_destroy(keyboard_);
        if (virtual_keyboard_) zwp_virtual_keyboard_v1_destroy(virtual_keyboard_);
        if (keyboard_manager_) zwp_virtual_keyboard_manager_v1_destroy(keyboard_manager_);
        if (selection_offer_) zwlr_data_control_offer_v1_destroy(selection_offer_);
        if (primary_offer_ && primary_offer_ != selection_offer_) zwlr_data_control_offer_v1_destroy(primary_offer_);
        for (auto &[proxy, offer] : offers_) {
            if (proxy != selection_offer_ && proxy != primary_offer_) zwlr_data_control_offer_v1_destroy(proxy);
        }
        if (data_device_) zwlr_data_control_device_v1_destroy(data_device_);
        if (data_manager_) zwlr_data_control_manager_v1_destroy(data_manager_);
        if (seat_) wl_seat_destroy(seat_);
        if (registry_) wl_registry_destroy(registry_);
        if (display_) wl_display_disconnect(display_);
        if (xkb_keymap_) xkb_keymap_unref(xkb_keymap_);
        if (xkb_context_) xkb_context_unref(xkb_context_);
    }

    void remove_source(zwlr_data_control_source_v1 *proxy) {
        auto found = sources_.find(proxy);
        if (found == sources_.end()) return;
        zwlr_data_control_source_v1_destroy(proxy);
        sources_.erase(found);
    }

    void new_selection(zwlr_data_control_offer_v1 *proxy, bool primary) {
        auto &previous = primary ? primary_offer_ : selection_offer_;
        if (previous && previous != proxy) {
            zwlr_data_control_offer_v1_destroy(previous);
            offers_.erase(previous);
        }
        previous = proxy;
        if (primary) return; // the contract treats Ctrl+C's regular selection only
        if (!proxy) {
            current_clipboard_.formats.clear();
            current_clipboard_complete_ = true;
            return;
        }
        auto found = offers_.find(proxy);
        if (found == offers_.end()) return;
        auto &mimes = found->second.mimes;
        if (std::find(mimes.begin(), mimes.end(), std::string(kOwnerMime)) != mimes.end()) return;

        auto sensitive = std::find(mimes.begin(), mimes.end(), std::string(kSecretMime));
        std::optional<Bytes> sensitive_value;
        bool is_secret = false;
        bool sensitive_marker_read = true;
        if (sensitive != mimes.end()) {
            sensitive_value = receive(proxy, *sensitive);
            if (sensitive_value) {
                std::string marker(sensitive_value->begin(), sensitive_value->end());
                while (!marker.empty() && std::isspace(static_cast<unsigned char>(marker.back()))) marker.pop_back();
                while (!marker.empty() && std::isspace(static_cast<unsigned char>(marker.front()))) marker.erase(marker.begin());
                is_secret = marker == "secret";
            } else {
                // If a provider advertises the password-manager marker but will not deliver it,
                // prefer losing one history entry to recording a secret copy.
                sensitive_marker_read = false;
            }
        }
        Bundle bundle;
        bool complete = sensitive_marker_read;
        for (const auto &mime : mimes) {
            if (mime == kOwnerMime) continue;
            auto data = mime == kSecretMime && sensitive_value ?
                std::move(sensitive_value) : receive(proxy, mime);
            if (data) bundle.formats.push_back({mime, std::move(*data)});
            else complete = false;
        }
        current_clipboard_ = bundle;
        current_clipboard_complete_ = complete;
        if (!is_secret && sensitive_marker_read) {
            auto entry = primary_entry(bundle);
            if (entry) remember(entry->mime, entry->data);
        }
    }

    static std::optional<Entry> primary_entry(const Bundle &bundle) {
        for (const auto &format : bundle.formats) {
            if (format.mime == "text/plain" || format.mime == "text/plain;charset=utf-8" ||
                format.mime == "text/plain;charset=UTF-8" || format.mime == "UTF8_STRING" ||
                format.mime == "TEXT") {
                return Entry{0, "text/plain;charset=utf-8", format.data};
            }
        }
        for (const auto &format : bundle.formats) {
            if (format.mime.compare(0, 6, "image/") == 0) return Entry{0, format.mime, format.data};
        }
        return std::nullopt;
    }

    std::optional<Bytes> receive(zwlr_data_control_offer_v1 *offer, const std::string &mime) {
        int pipefd[2];
        if (pipe2(pipefd, O_CLOEXEC) < 0) return std::nullopt;
        int flags = fcntl(pipefd[0], F_GETFL);
        if (flags >= 0) fcntl(pipefd[0], F_SETFL, flags | O_NONBLOCK);
        zwlr_data_control_offer_v1_receive(offer, mime.c_str(), pipefd[1]);
        close(pipefd[1]);
        if (wl_display_flush(display_) < 0 && errno != EAGAIN) { close(pipefd[0]); return std::nullopt; }
        Bytes result;
        std::array<uint8_t, 65536> buffer{};
        auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(kTransferTimeoutMs);
        for (;;) {
            auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(deadline - std::chrono::steady_clock::now()).count();
            if (remaining <= 0) { close(pipefd[0]); return std::nullopt; }
            pollfd fd{pipefd[0], POLLIN | POLLHUP, 0};
            int ready;
            do { ready = poll(&fd, 1, static_cast<int>(remaining)); } while (ready < 0 && errno == EINTR);
            if (ready <= 0) { close(pipefd[0]); return std::nullopt; }
            ssize_t count = read(pipefd[0], buffer.data(), buffer.size());
            if (count < 0 && (errno == EINTR || errno == EAGAIN)) continue;
            if (count < 0) { close(pipefd[0]); return std::nullopt; }
            if (count == 0) break;
            result.insert(result.end(), buffer.begin(), buffer.begin() + count);
            if (result.size() > kMaxFrame) { close(pipefd[0]); return std::nullopt; }
        }
        close(pipefd[0]);
        return result;
    }

    void remember(const std::string &mime, const Bytes &data) {
        auto same = std::find_if(history_.begin(), history_.end(), [&](const Entry &entry) {
            return entry.mime == mime && entry.data == data;
        });
        if (same != history_.end()) {
            Entry existing = std::move(*same);
            history_.erase(same);
            history_.push_front(std::move(existing));
        } else {
            history_.push_front(Entry{next_entry_id_++, mime, data});
        }
        while (history_.size() > kMaxHistory) history_.pop_back();
    }

    void set_selection(const Bundle &bundle, bool record_history) {
        if (bundle.formats.empty()) {
            zwlr_data_control_device_v1_set_selection(data_device_, nullptr);
            if (wl_display_flush(display_) < 0 && errno != EAGAIN) throw std::runtime_error("could not clear the clipboard");
            current_clipboard_.formats.clear();
            current_clipboard_complete_ = true;
            return;
        }
        Bundle owned = bundle;
        owned.formats.push_back({std::string(kOwnerMime), {}});
        auto *source = zwlr_data_control_manager_v1_create_data_source(data_manager_);
        if (!source) throw std::runtime_error("could not create a clipboard source");
        auto object = std::make_unique<Source>();
        object->owner = this;
        object->bundle = std::move(owned);
        auto *state = object.get();
        sources_[source] = std::move(object);
        zwlr_data_control_source_v1_add_listener(source, &source_listener_, state);
        for (const auto &format : state->bundle.formats) zwlr_data_control_source_v1_offer(source, format.mime.c_str());
        zwlr_data_control_device_v1_set_selection(data_device_, source);
        if (wl_display_flush(display_) < 0 && errno != EAGAIN) {
            zwlr_data_control_source_v1_destroy(source);
            sources_.erase(source);
            throw std::runtime_error("could not set the clipboard");
        }
        current_clipboard_ = bundle;
        current_clipboard_complete_ = true;
        if (record_history) {
            auto entry = primary_entry(bundle);
            if (entry) remember(entry->mime, entry->data);
        }
    }

    bool paste_and_wait() {
        if (!keymap_ready_ || !virtual_keyboard_) return false;
        transfer_seen_ = false;
        uint32_t now = static_cast<uint32_t>(std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now().time_since_epoch()).count());
        constexpr uint32_t key_leftshift = 42;
        constexpr uint32_t key_insert = 110;
        zwp_virtual_keyboard_v1_key(virtual_keyboard_, now, key_leftshift, WL_KEYBOARD_KEY_STATE_PRESSED);
        zwp_virtual_keyboard_v1_modifiers(virtual_keyboard_, depressed_mods_ | shift_mask_, latched_mods_, locked_mods_, keymap_group_);
        zwp_virtual_keyboard_v1_key(virtual_keyboard_, now + 1, key_insert, WL_KEYBOARD_KEY_STATE_PRESSED);
        zwp_virtual_keyboard_v1_key(virtual_keyboard_, now + 2, key_insert, WL_KEYBOARD_KEY_STATE_RELEASED);
        zwp_virtual_keyboard_v1_key(virtual_keyboard_, now + 3, key_leftshift, WL_KEYBOARD_KEY_STATE_RELEASED);
        zwp_virtual_keyboard_v1_modifiers(virtual_keyboard_, depressed_mods_, latched_mods_, locked_mods_, keymap_group_);
        if (wl_display_flush(display_) < 0 && errno != EAGAIN) return false;
        auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(1200);
        const int wayland_fd = wl_display_get_fd(display_);
        while (std::chrono::steady_clock::now() < deadline) {
            wl_display_dispatch_pending(display_);
            if (transfer_seen_) return true;
            pollfd fd{wayland_fd, POLLIN, 0};
            int ready;
            do { ready = poll(&fd, 1, 25); } while (ready < 0 && errno == EINTR);
            if (ready < 0 || (fd.revents & (POLLERR | POLLHUP | POLLNVAL))) return false;
            if (fd.revents & POLLIN) if (wl_display_dispatch(display_) < 0) return false;
        }
        return false;
    }

    static uint32_t read_u32(const Bytes &data, size_t &offset) {
        if (offset + 4 > data.size()) throw std::runtime_error("malformed clipboard request");
        uint32_t value = (uint32_t(data[offset]) << 24) | (uint32_t(data[offset + 1]) << 16) |
                         (uint32_t(data[offset + 2]) << 8) | uint32_t(data[offset + 3]);
        offset += 4;
        return value;
    }
    static uint64_t read_u64(const Bytes &data, size_t &offset) {
        if (offset + 8 > data.size()) throw std::runtime_error("malformed clipboard request");
        uint64_t value = 0;
        for (int i = 0; i < 8; ++i) value = (value << 8) | data[offset++];
        return value;
    }
    static Bytes read_bytes(const Bytes &data, size_t &offset) {
        uint32_t size = read_u32(data, offset);
        if (size > kMaxFrame || offset + size > data.size()) throw std::runtime_error("malformed clipboard request");
        Bytes value(data.begin() + offset, data.begin() + offset + size);
        offset += size;
        return value;
    }
    static std::string read_string(const Bytes &data, size_t &offset) {
        auto value = read_bytes(data, offset);
        return std::string(value.begin(), value.end());
    }
    static void append_u32(Bytes &out, uint32_t value) {
        out.push_back((value >> 24) & 255); out.push_back((value >> 16) & 255);
        out.push_back((value >> 8) & 255); out.push_back(value & 255);
    }
    static void append_u64(Bytes &out, uint64_t value) {
        for (int i = 7; i >= 0; --i) out.push_back((value >> (i * 8)) & 255);
    }
    static void append_bytes(Bytes &out, const Bytes &value) {
        append_u32(out, static_cast<uint32_t>(value.size())); out.insert(out.end(), value.begin(), value.end());
    }
    static void append_string(Bytes &out, const std::string &value) {
        append_bytes(out, Bytes(value.begin(), value.end()));
    }
    static bool read_exact(int fd, uint8_t *data, size_t size) {
        size_t offset = 0;
        while (offset < size) {
            ssize_t count = read(fd, data + offset, size - offset);
            if (count < 0 && errno == EINTR) continue;
            if (count <= 0) return false;
            offset += static_cast<size_t>(count);
        }
        return true;
    }
    static bool write_exact(int fd, const uint8_t *data, size_t size) {
        size_t offset = 0;
        while (offset < size) {
            ssize_t count = write(fd, data + offset, size - offset);
            if (count < 0 && errno == EINTR) continue;
            if (count <= 0) return false;
            offset += static_cast<size_t>(count);
        }
        return true;
    }
    static Bytes read_frame(int fd) {
        uint8_t header[4];
        if (!read_exact(fd, header, sizeof(header))) return {};
        uint32_t size = (uint32_t(header[0]) << 24) | (uint32_t(header[1]) << 16) |
                        (uint32_t(header[2]) << 8) | header[3];
        if (size == 0 || size > kMaxFrame) throw std::runtime_error("clipboard request is too large");
        Bytes data(size);
        if (!read_exact(fd, data.data(), data.size())) throw std::runtime_error("incomplete clipboard request");
        return data;
    }
    static void write_frame(int fd, uint8_t status, const Bytes &body) {
        Bytes frame{status}; frame.insert(frame.end(), body.begin(), body.end());
        Bytes header; append_u32(header, static_cast<uint32_t>(frame.size()));
        write_exact(fd, header.data(), header.size());
        write_exact(fd, frame.data(), frame.size());
    }

    void handle_client() {
        int fd = accept4(server_fd_, nullptr, nullptr, SOCK_CLOEXEC);
        if (fd < 0) return;
        try {
            auto request = read_frame(fd);
            if (request.empty()) { close(fd); return; }
            Bytes reply = dispatch_request(request);
            write_frame(fd, 0, reply);
        } catch (const std::exception &error) {
            auto message = std::string(error.what());
            if (message.find('\n') != std::string::npos) message = message.substr(0, message.find('\n'));
            write_frame(fd, 1, Bytes(message.begin(), message.end()));
        }
        close(fd);
    }

    Bytes dispatch_request(const Bytes &request) {
        size_t offset = 1;
        const uint8_t op = request[0];
        Bytes reply;
        if (op == 1) return reply; // ping
        if (op == 2) { // list
            append_u32(reply, static_cast<uint32_t>(history_.size()));
            for (const auto &entry : history_) {
                append_u64(reply, entry.id); append_string(reply, entry.mime); append_bytes(reply, entry.data);
            }
            return reply;
        }
        if (op == 3) { history_.clear(); return reply; }
        if (op == 4 || op == 5) { // copy text or file
            if (offset >= request.size()) throw std::runtime_error("malformed clipboard request");
            const bool copy_only = request[offset++] != 0;
            const uint64_t target = read_u64(request, offset);
            const std::string mime = op == 4 ? "text/plain;charset=utf-8" : read_string(request, offset);
            Bytes data = read_bytes(request, offset);
            if (offset != request.size()) throw std::runtime_error("malformed clipboard request");
            if (!copy_only) require_target(target);
            Bundle bundle{{Format{mime, std::move(data)}}};
            set_selection(bundle, true);
            if (!copy_only) {
                try { require_target(target); }
                catch (...) { throw std::runtime_error("paste failed: the target window closed; the entry is on the clipboard"); }
            }
            if (!copy_only && !paste_and_wait()) {
                throw std::runtime_error("paste failed: the clipboard is set, but the focused window did not accept it");
            }
            return reply;
        }
        if (op == 6) { // select history entry
            const uint64_t id = read_u64(request, offset);
            if (offset >= request.size()) throw std::runtime_error("malformed clipboard request");
            const uint8_t mode = request[offset++];
            const uint64_t target = read_u64(request, offset);
            auto found = std::find_if(history_.begin(), history_.end(), [id](const Entry &entry) { return entry.id == id; });
            if (found == history_.end()) throw std::runtime_error("the selected clipboard entry is no longer available");
            if (mode == 2) {
                append_string(reply, found->mime); append_bytes(reply, found->data);
                return reply;
            }
            if (mode == 1) require_target(target);
            Bundle bundle{{Format{found->mime, found->data}}};
            set_selection(bundle, true);
            if (mode == 1) {
                try { require_target(target); }
                catch (...) { throw std::runtime_error("paste failed: the target window closed; the entry is on the clipboard"); }
            }
            if (mode == 1 && !paste_and_wait()) {
                throw std::runtime_error("paste failed: the selected entry is on the clipboard, but the focused window did not accept it");
            }
            return reply;
        }
        if (op == 7) {
            if (!current_clipboard_complete_)
                throw std::runtime_error("could not preserve every format on the current clipboard");
            uint64_t id = next_snapshot_id_++;
            snapshots_[id] = current_clipboard_;
            append_u64(reply, id);
            return reply;
        }
        if (op == 8) { snapshots_.erase(read_u64(request, offset)); return reply; }
        if (op == 9) { // temporarily paste text and restore the pre-picker clipboard
            const uint64_t target = read_u64(request, offset);
            const uint64_t snapshot_id = read_u64(request, offset);
            Bytes data = read_bytes(request, offset);
            auto snapshot = snapshots_.find(snapshot_id);
            if (snapshot == snapshots_.end()) throw std::runtime_error("the saved clipboard is no longer available");
            require_target(target);
            Bundle previous = snapshot->second;
            Bundle temporary{{Format{"text/plain;charset=utf-8", std::move(data)}}};
            set_selection(temporary, false);
            try { require_target(target); }
            catch (...) {
                set_selection(previous, false);
                snapshots_.erase(snapshot_id);
                throw std::runtime_error("emoji insertion failed; the original clipboard was restored");
            }
            bool pasted = paste_and_wait();
            set_selection(previous, false);
            snapshots_.erase(snapshot_id);
            if (!pasted) throw std::runtime_error("emoji insertion failed; the original clipboard was restored");
            return reply;
        }
        if (op == 10) { // create a private session-only file for xdg-open
            std::string mime = read_string(request, offset);
            Bytes data = read_bytes(request, offset);
            std::string suffix = mime == "image/png" ? ".png" : mime == "image/jpeg" ? ".jpg" :
                mime == "image/webp" ? ".webp" : mime == "image/gif" ? ".gif" :
                mime == "image/bmp" ? ".bmp" : mime == "image/tiff" ? ".tif" :
                mime == "image/svg+xml" ? ".svg" : mime == "image/avif" ? ".avif" :
                mime == "image/x-icon" ? ".ico" : ".img";
            if (mime.compare(0, 5, "text/") == 0) suffix = ".txt";
            auto path = open_dir_ / ("entry-" + std::to_string(next_snapshot_id_++) + suffix);
            int file = ::open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
            if (file < 0) throw std::runtime_error("could not create a temporary clipboard file");
            bool ok = write_exact(file, data.data(), data.size());
            close(file);
            if (!ok) { unlink(path.c_str()); throw std::runtime_error("could not write the temporary clipboard file"); }
            temp_files_.push_back(path);
            append_string(reply, path.string());
            return reply;
        }
        throw std::runtime_error("unknown clipboard service request");
    }
};

constexpr wl_registry_listener Service::registry_listener_;
constexpr wl_keyboard_listener Service::keyboard_listener_;
constexpr zwlr_data_control_source_v1_listener Service::source_listener_;
constexpr zwlr_data_control_offer_v1_listener Service::offer_listener_;
constexpr zwlr_data_control_device_v1_listener Service::data_device_listener_;
} // namespace

int main() {
    std::signal(SIGPIPE, SIG_IGN);
    try {
        Service service;
        service.run();
        return 0;
    } catch (const std::exception &error) {
        std::cerr << "scottland-clipboardd: " << error.what() << '\n';
        return 1;
    }
}
