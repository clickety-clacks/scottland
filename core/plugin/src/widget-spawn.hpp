// Included inside scottland_plugin_t. Conversion launches never fork the compositor.
// One disowned broker per plugin copy inherits the same session context as core.run().
int widget_spawn_fd = -1;
wl_event_source *widget_spawn_watch = nullptr;
std::map<std::string, widget_process> widget_spawn_pending;

bool send_widget_spawn(const wf::json_t& request)
{
    auto body = request.serialize();
    return widget_spawn_fd >= 0 && body.size() < 65536 &&
        send(widget_spawn_fd, body.data(), body.size(), MSG_DONTWAIT | MSG_NOSIGNAL) == (ssize_t)body.size();
}

void receive_widget_spawn()
{
    char body[65536];
    alignas(cmsghdr) char control[CMSG_SPACE(sizeof(int))];
    iovec io{body, sizeof(body)};
    msghdr message{};
    message.msg_iov = &io; message.msg_iovlen = 1;
    message.msg_control = control; message.msg_controllen = sizeof(control);
    auto size = recvmsg(widget_spawn_fd, &message, MSG_DONTWAIT | MSG_CMSG_CLOEXEC);
    if (size <= 0) return;
    int pidfd = -1;
    for (auto *c = CMSG_FIRSTHDR(&message); c; c = CMSG_NXTHDR(&message, c))
        if (c->cmsg_level == SOL_SOCKET && c->cmsg_type == SCM_RIGHTS && c->cmsg_len == CMSG_LEN(sizeof(int)))
            memcpy(&pidfd, CMSG_DATA(c), sizeof(pidfd));
    wf::json_t reply;
    if ((message.msg_flags & (MSG_TRUNC | MSG_CTRUNC)) ||
        wf::json_t::parse_string(std::string_view(body, size), reply) ||
        !reply.has_member("unit") || !reply.has_member("pid"))
    {
        if (pidfd >= 0) close(pidfd);
        return;
    }
    auto found = widget_spawn_pending.find(reply["unit"].as_string());
    if (found == widget_spawn_pending.end())
    {
        if (pidfd >= 0) close(pidfd);
        return;
    }
    auto process = found->second;
    widget_spawn_pending.erase(found);
    process->pid = reply["pid"].as_int();
    process->pidfd = pidfd;
    if (process->pid <= 0 || pidfd < 0)
    {
        scottland::loop::note(scottland::loop::note_id::broker_launch_failed);
        // The normal launch watchdog restores the still-visible app.
        return;
    }
    watch_process(process);
    if (process->cancelled) end_now(process);
    publish_model();
}

void init_widget_spawn()
{
    int endpoints[2];
    if (socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, endpoints) < 0) return;
    // Only the broker endpoint survives core.run's exec. The broker makes it
    // close-on-exec again before launching any widget, so children cannot hold it open.
    fcntl(endpoints[1], F_SETFD, 0);
    auto launcher = widget_launcher();
    auto helper = launcher.substr(0, launcher.rfind('/') + 1) + "scottland-widget-spawn";
    auto pid = run_command(shell_quote(helper) + " " + std::to_string(endpoints[1]));
    close(endpoints[1]);
    if (pid <= 0) { close(endpoints[0]); return; }
    widget_spawn_fd = endpoints[0];
    widget_spawn_watch = wl_event_loop_add_fd(wf::get_core().ev_loop, widget_spawn_fd,
        WL_EVENT_READABLE, [] (int, uint32_t mask, void *data)
        {
            SCOTTLAND_LOOP_SCOPE(widget_spawn_reply);
            auto self = static_cast<scottland_plugin_t*>(data);
            if (mask & (WL_EVENT_HANGUP | WL_EVENT_ERROR))
            {
                self->fini_widget_spawn();
                return 0;
            }
            self->receive_widget_spawn();
            return 0;
        }, this);
}

void fini_widget_spawn()
{
    if (widget_spawn_watch) wl_event_source_remove(widget_spawn_watch);
    widget_spawn_watch = nullptr;
    if (widget_spawn_fd >= 0) close(widget_spawn_fd);
    widget_spawn_fd = -1;
    widget_spawn_pending.clear();
}
