use std::env;
use std::ffi::{OsStr, OsString};
use std::fs::{self, OpenOptions};
use std::io::{self, BufRead, BufReader, Write};
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::sync::mpsc::{self, Sender};
use std::thread;
use std::time::{Duration, Instant};

use crate::gamma::GammaController;
use scottland::night_light::Decision;

const HELPER_RELATIVE: &str = "libexec/scottland-color-scheme";
const THEME_LINKS: [&str; 2] = ["light", "dark"];
const SIGNAL_TERM: i32 = 15;
const SIGNAL_KILL: i32 = 9;

unsafe extern "C" {
    fn setpgid(pid: i32, pgid: i32) -> i32;
    fn kill(pid: i32, signal: i32) -> i32;
    fn getuid() -> u32;
    fn poll(fds: *mut PollFd, nfds: usize, timeout: i32) -> i32;
}

#[repr(C)]
struct PollFd {
    fd: i32,
    events: i16,
    revents: i16,
}

const POLLIN: i16 = 0x001;
const POLL_ERROR_EVENTS: i16 = 0x008 | 0x010 | 0x020;
const NIGHT_LIGHT_IDLE_TICK: Duration = Duration::from_millis(100);
const MAX_WATCH_EVENTS_PER_TICK: usize = 32;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum WatchEvent {
    Appearance,
    Accent,
    Theme,
}

pub(super) fn once() -> Result<(), String> {
    invoke_helper("once").map(|_| ())
}

pub(super) fn cursor_size() -> Result<(), String> {
    let output = invoke_helper("cursor-size")?;
    let value = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if value.is_empty() || !value.bytes().all(|byte| byte.is_ascii_digit()) {
        return Err("scottland-color-scheme returned an invalid cursor size".to_string());
    }
    println!("{value}");
    Ok(())
}

pub(super) fn push_terminal_theme() -> Result<(), String> {
    invoke_helper("terminal-theme-push").map(|_| ())
}

fn helper_path(hooks: Option<OsString>) -> PathBuf {
    hooks_path(hooks).join(HELPER_RELATIVE)
}

fn hooks_path(hooks: Option<OsString>) -> PathBuf {
    hooks
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("/usr/lib/scottland"))
}

fn invoke_helper(action: &str) -> Result<Output, String> {
    invoke_helper_at(&helper_path(env::var_os("SCOTTLAND_HOOKS")), action)
}

fn invoke_helper_at(path: &Path, action: &str) -> Result<Output, String> {
    let output = Command::new(path)
        .arg(action)
        .output()
        .map_err(|error| format!("could not run {} {action}: {error}", path.display()))?;
    if output.status.success() {
        let _ = io::stderr().write_all(&output.stderr);
        return Ok(output);
    }

    let detail = String::from_utf8_lossy(&output.stderr).trim().to_string();
    let detail = if detail.is_empty() {
        output.status.code().map_or_else(
            || "terminated by signal".to_string(),
            |code| format!("exit status {code}"),
        )
    } else {
        detail
    };
    Err(format!("scottland-color-scheme {action} failed: {detail}"))
}

pub(super) fn ensure() -> Result<(), String> {
    let path = pid_file_path()?;
    let fingerprint = executable_fingerprint()?;
    if let Some((pid, recorded)) = read_pid_file(&path) {
        if watcher_matches_session(pid) {
            if recorded == fingerprint {
                return invoke_helper("once").map(|_| ());
            }
            terminate_watcher(pid)?;
        }
        remove_pid_file(&path);
    }

    let executable = env::current_exe()
        .map_err(|error| format!("could not locate scottland-display: {error}"))?;
    let mut command = Command::new(executable);
    command
        .args(["color-scheme", "watch"])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .process_group(0);
    command
        .spawn()
        .map_err(|error| format!("could not start the color-scheme watcher: {error}"))?;
    Ok(())
}

pub(super) fn watch() -> Result<(), String> {
    become_process_group_leader()?;
    let path = pid_file_path()?;
    let directory = path
        .parent()
        .ok_or_else(|| "color-scheme watcher record has no parent directory".to_string())?;
    fs::create_dir_all(directory)
        .map_err(|error| format!("could not create color-scheme watcher directory: {error}"))?;
    let fingerprint = executable_fingerprint()?;
    let Some(_pid_guard) = claim_pid_file(&path, &fingerprint)? else {
        return Ok(());
    };

    invoke_helper("once")?;
    let mut night_light = start_night_light_receiver();
    let (sender, receiver) = mpsc::channel();
    spawn_settings_monitor(sender.clone());
    spawn_text_monitor(sender.clone());
    spawn_accent_monitor(sender.clone());
    spawn_theme_monitor(sender);

    loop {
        refresh_night_light_session(&mut night_light);
        if night_light.is_some() {
            match receiver.recv_timeout(NIGHT_LIGHT_IDLE_TICK) {
                Ok(event) => handle_watch_event(event),
                Err(mpsc::RecvTimeoutError::Timeout) => {}
                Err(mpsc::RecvTimeoutError::Disconnected) => break,
            }
        } else {
            match receiver.recv() {
                Ok(event) => handle_watch_event(event),
                Err(_) => break,
            }
        }

        for _ in 1..MAX_WATCH_EVENTS_PER_TICK {
            match receiver.try_recv() {
                Ok(event) => handle_watch_event(event),
                Err(mpsc::TryRecvError::Empty | mpsc::TryRecvError::Disconnected) => break,
            }
        }

        refresh_night_light_session(&mut night_light);
        let Some(runtime) = night_light.as_mut() else {
            continue;
        };
        if let Err(error) = service_night_light(runtime) {
            eprintln!("scottland-display: disabling Night Light receiver: {error}");
            night_light = None;
        }
    }
    Ok(())
}

fn handle_watch_event(event: WatchEvent) {
    match event {
        WatchEvent::Appearance | WatchEvent::Accent => {
            if let Err(error) = invoke_helper("once") {
                eprintln!("scottland-display: {error}");
            }
        }
        WatchEvent::Theme => {
            if let Err(error) = invoke_helper("once") {
                eprintln!("scottland-display: {error}");
            }
            if let Err(error) = push_terminal_theme() {
                eprintln!("scottland-display: {error}");
            }
        }
    }
}

struct NightLightRuntime {
    receiver: NightLightReceiver,
    gamma: GammaController,
}

fn refresh_night_light_session(runtime: &mut Option<NightLightRuntime>) {
    if runtime
        .as_ref()
        .is_some_and(|runtime| !runtime.receiver.session_is_current())
    {
        eprintln!("scottland-display: rebinding Night Light to the current session");
        *runtime = None;
        *runtime = start_night_light_receiver();
    }
}

fn start_night_light_receiver() -> Option<NightLightRuntime> {
    let display = env::var("WAYLAND_DISPLAY").ok();
    let session = match scottland::session::current(display.as_deref()) {
        Ok(session) => session,
        Err(error) => {
            eprintln!("scottland-display: Night Light unavailable: {error}");
            return None;
        }
    };
    let (receiver, retained_request) = match NightLightReceiver::bind(&session) {
        Ok(bound) => bound,
        Err(error) => {
            eprintln!("scottland-display: Night Light unavailable: {error}");
            return None;
        }
    };
    let mut gamma = match GammaController::connect_session(&session) {
        Ok(gamma) => gamma,
        Err(error) => {
            eprintln!("scottland-display: Night Light unavailable: {error}");
            return None;
        }
    };
    if let Some(decision) = match receiver.reapply_after_restart(retained_request, &mut gamma) {
        Ok(decision) => decision,
        Err(error) => {
            eprintln!("scottland-display: could not restore retained Night Light request: {error}");
            None
        }
    } {
        report_night_light_decision(&decision);
    }
    Some(NightLightRuntime { receiver, gamma })
}

fn service_night_light(runtime: &mut NightLightRuntime) -> Result<(), String> {
    let (wayland_ready, receiver_ready) = poll_night_light_fds(&runtime.receiver, &runtime.gamma)?;
    if wayland_ready {
        runtime.gamma.dispatch_blocking()?;
    }
    if receiver_ready {
        match runtime.receiver.try_serve(&mut runtime.gamma) {
            Ok(Some(decision)) => report_night_light_decision(&decision),
            Ok(None) => {}
            Err(error) => eprintln!("scottland-display: rejected Night Light request: {error}"),
        }
    }
    match runtime.receiver.reconcile_persisted_off(&mut runtime.gamma) {
        Ok(Some(decision)) => report_night_light_decision(&decision),
        Ok(None) => {}
        Err(error) => eprintln!("scottland-display: Night Light reconciliation failed: {error}"),
    }
    Ok(())
}

fn poll_night_light_fds(
    receiver: &NightLightReceiver,
    gamma: &GammaController,
) -> Result<(bool, bool), String> {
    let mut fds = [
        PollFd {
            fd: gamma.poll_fd(),
            events: POLLIN,
            revents: 0,
        },
        PollFd {
            fd: receiver.poll_fd(),
            events: POLLIN,
            revents: 0,
        },
    ];
    let ready = unsafe { poll(fds.as_mut_ptr(), fds.len(), 0) };
    if ready < 0 {
        let error = io::Error::last_os_error();
        if error.kind() == io::ErrorKind::Interrupted {
            return Ok((false, false));
        }
        return Err(format!("could not poll Night Light endpoints: {error}"));
    }
    for fd in &fds {
        if fd.revents & POLL_ERROR_EVENTS != 0 {
            return Err("Night Light endpoint closed or failed".to_owned());
        }
    }
    Ok((fds[0].revents & POLLIN != 0, fds[1].revents & POLLIN != 0))
}

fn report_night_light_decision(decision: &Decision) {
    if decision.exit_status != 0 {
        if let Some(stderr) = &decision.stderr {
            eprintln!("{stderr}");
        }
    }
}

struct PidGuard {
    path: PathBuf,
    pid: i32,
}

impl Drop for PidGuard {
    fn drop(&mut self) {
        if read_pid_file(&self.path).is_some_and(|(pid, _)| pid == self.pid) {
            remove_pid_file(&self.path);
        }
    }
}

fn claim_pid_file(path: &Path, fingerprint: &str) -> Result<Option<PidGuard>, String> {
    for _ in 0..3 {
        if let Some((pid, recorded)) = read_pid_file(path) {
            if watcher_matches_session(pid) {
                if recorded == fingerprint {
                    return Ok(None);
                }
                terminate_watcher(pid)?;
            }
            remove_pid_file(path);
        }

        let mut options = OpenOptions::new();
        options.write(true).create_new(true).mode(0o600);
        match options.open(path) {
            Ok(mut file) => {
                let pid = std::process::id() as i32;
                writeln!(file, "{pid}\n{fingerprint}")
                    .map_err(|error| format!("could not record color-scheme watcher: {error}"))?;
                file.sync_all().map_err(|error| {
                    format!("could not persist color-scheme watcher record: {error}")
                })?;
                return Ok(Some(PidGuard {
                    path: path.to_path_buf(),
                    pid,
                }));
            }
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(error) => {
                return Err(format!(
                    "could not create color-scheme watcher record: {error}"
                ));
            }
        }
    }
    Err("another color-scheme watcher is starting for this session".to_string())
}

fn read_pid_file(path: &Path) -> Option<(i32, String)> {
    let content = fs::read_to_string(path).ok()?;
    let mut lines = content.lines();
    Some((lines.next()?.parse().ok()?, lines.next()?.to_string()))
}

fn remove_pid_file(path: &Path) {
    let _ = fs::remove_file(path);
}

fn pid_file_path() -> Result<PathBuf, String> {
    let session_dir = if let Some(path) = nonempty_env("SCOTTLAND_SESSION_DIR") {
        PathBuf::from(path)
    } else if let Some(path) = nonempty_env("XDG_RUNTIME_DIR") {
        PathBuf::from(path).join("scottland")
    } else {
        PathBuf::from(format!("/run/user/{}", unsafe { getuid() })).join("scottland")
    };
    let display = nonempty_env("WAYLAND_DISPLAY").unwrap_or_else(|| OsString::from("wayland"));
    Ok(pid_file_path_for(&session_dir, display.as_os_str()))
}

fn pid_file_path_for(session_dir: &Path, display: &OsStr) -> PathBuf {
    let component = display
        .as_bytes()
        .iter()
        .map(|byte| {
            if byte.is_ascii_alphanumeric() || matches!(*byte, b'.' | b'_' | b'-') {
                *byte as char
            } else {
                '_'
            }
        })
        .collect::<String>();
    session_dir.join(format!(
        "{}.color-scheme.pid",
        if component.is_empty() {
            "wayland"
        } else {
            &component
        }
    ))
}

fn nonempty_env(name: &str) -> Option<OsString> {
    env::var_os(name).filter(|value| !value.is_empty())
}

fn executable_fingerprint() -> Result<String, String> {
    let executable = env::current_exe()
        .map_err(|error| format!("could not locate scottland-display: {error}"))?;
    let bytes = fs::read(executable)
        .map_err(|error| format!("could not read scottland-display: {error}"))?;
    let hash = bytes.iter().fold(0xcbf29ce484222325_u64, |hash, byte| {
        (hash ^ u64::from(*byte)).wrapping_mul(0x100000001b3)
    });
    Ok(format!("{hash:016x}"))
}

fn become_process_group_leader() -> Result<(), String> {
    if unsafe { setpgid(0, 0) } == 0 {
        return Ok(());
    }
    let error = io::Error::last_os_error();
    if process_group(std::process::id() as i32) == Some(std::process::id() as i32) {
        Ok(())
    } else {
        Err(format!(
            "could not isolate color-scheme watcher process group: {error}"
        ))
    }
}

fn read_process_args(pid: i32) -> Option<Vec<Vec<u8>>> {
    let bytes = fs::read(format!("/proc/{pid}/cmdline")).ok()?;
    Some(
        bytes
            .split(|byte| *byte == 0)
            .filter(|arg| !arg.is_empty())
            .map(|arg| arg.to_vec())
            .collect(),
    )
}

fn process_matches_watcher(pid: i32) -> bool {
    let Some(args) = read_process_args(pid) else {
        return false;
    };
    let executable = args
        .iter()
        .any(|arg| arg.ends_with(b"scottland-display") || arg.ends_with(b"scottland-color-scheme"));
    executable && args.iter().any(|arg| arg.as_slice() == b"watch")
}

fn process_environment_matches(pid: i32) -> bool {
    let Ok(environment) = fs::read(format!("/proc/{pid}/environ")) else {
        return false;
    };
    let entries = environment.split(|byte| *byte == 0).collect::<Vec<_>>();
    let expected = [
        "WAYLAND_DISPLAY",
        "XDG_RUNTIME_DIR",
        "SCOTTLAND_SESSION_DIR",
    ]
    .into_iter()
    .filter_map(|key| {
        env::var_os(key)
            .filter(|value| !value.is_empty())
            .map(|value| (key, value))
    })
    .collect::<Vec<_>>();
    !expected.is_empty()
        && expected.iter().all(|(key, value)| {
            let mut pair = key.as_bytes().to_vec();
            pair.push(b'=');
            pair.extend_from_slice(value.as_bytes());
            entries.contains(&pair.as_slice())
        })
}

fn watcher_matches_session(pid: i32) -> bool {
    process_matches_watcher(pid) && process_environment_matches(pid)
}

fn process_group(pid: i32) -> Option<i32> {
    let stat = fs::read_to_string(format!("/proc/{pid}/stat")).ok()?;
    let closing = stat.rfind(')')?;
    stat[closing + 1..].split_whitespace().nth(2)?.parse().ok()
}

fn terminate_watcher(pid: i32) -> Result<(), String> {
    if !watcher_matches_session(pid) {
        return Ok(());
    }
    let group = process_group(pid) == Some(pid);
    let target = if group { -pid } else { pid };
    if unsafe { kill(target, SIGNAL_TERM) } != 0 {
        let error = io::Error::last_os_error();
        if error.raw_os_error() != Some(3) {
            return Err(format!(
                "could not stop prior color-scheme watcher: {error}"
            ));
        }
    }

    let deadline = Instant::now() + Duration::from_secs(2);
    while Instant::now() < deadline && watcher_matches_session(pid) {
        thread::sleep(Duration::from_millis(25));
    }
    if watcher_matches_session(pid) {
        if unsafe { kill(target, SIGNAL_KILL) } != 0 {
            let error = io::Error::last_os_error();
            if error.raw_os_error() != Some(3) {
                return Err(format!(
                    "could not finish stopping prior color-scheme watcher: {error}"
                ));
            }
        }
    }
    Ok(())
}

fn spawn_settings_monitor(sender: Sender<WatchEvent>) {
    thread::spawn(move || {
        loop {
            let portal = portal_has_scheme();
            let mut command = if portal {
                let mut command = Command::new("gdbus");
                command.args([
                    "monitor",
                    "--session",
                    "--dest",
                    "org.freedesktop.portal.Desktop",
                    "--object-path",
                    "/org/freedesktop/portal/desktop",
                ]);
                command
            } else {
                let mut command = Command::new("gsettings");
                command.args(["monitor", "org.gnome.desktop.interface", "color-scheme"]);
                command
            };
            let _ = monitor_lines(&mut command, |line| {
                if ["color-scheme", "accent-color"]
                    .iter()
                    .any(|key| line.contains(key))
                {
                    let _ = sender.send(WatchEvent::Appearance);
                }
                true
            });
            thread::sleep(Duration::from_secs(2));
        }
    });
}

fn spawn_text_monitor(sender: Sender<WatchEvent>) {
    thread::spawn(move || {
        loop {
            let mut command = Command::new("gsettings");
            command.args(["monitor", "org.gnome.desktop.interface"]);
            let _ = monitor_lines(&mut command, |line| {
                if [
                    "text-scaling-factor",
                    "cursor-theme",
                    "font-name",
                    "enable-animations",
                ]
                .iter()
                .any(|key| line.contains(key))
                {
                    let _ = sender.send(WatchEvent::Appearance);
                }
                true
            });
            thread::sleep(Duration::from_secs(2));
        }
    });
}

fn monitor_lines(command: &mut Command, mut on_line: impl FnMut(&str) -> bool) -> io::Result<()> {
    let mut child = command
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()?;
    if let Some(stdout) = child.stdout.take() {
        for line in BufReader::new(stdout).lines() {
            match line {
                Ok(line) => {
                    if !on_line(&line) {
                        let _ = child.kill();
                        break;
                    }
                }
                Err(error) => {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err(error);
                }
            }
        }
    }
    let _ = child.wait();
    Ok(())
}

fn portal_has_scheme() -> bool {
    let output = Command::new("busctl")
        .args([
            "--user",
            "call",
            "org.freedesktop.portal.Desktop",
            "/org/freedesktop/portal/desktop",
            "org.freedesktop.portal.Settings",
            "ReadOne",
            "ss",
            "org.freedesktop.appearance",
            "color-scheme",
        ])
        .output();
    output.is_ok_and(|output| {
        output.status.success()
            && portal_response_has_scheme(&String::from_utf8_lossy(&output.stdout))
    })
}

fn portal_response_has_scheme(response: &str) -> bool {
    let bytes = response.as_bytes();
    (0..bytes.len()).any(|index| {
        if bytes[index] != b'u'
            || (index > 0 && (bytes[index - 1].is_ascii_alphanumeric() || bytes[index - 1] == b'_'))
            || !bytes.get(index + 1).is_some_and(u8::is_ascii_whitespace)
        {
            return false;
        }
        let Some(mut value) = bytes
            .get(index + 1..)
            .and_then(|tail| tail.iter().position(|byte| !byte.is_ascii_whitespace()))
        else {
            return false;
        };
        value += index + 1;
        matches!(bytes[value], b'1' | b'2')
            && bytes
                .get(value + 1)
                .is_none_or(|byte| !byte.is_ascii_digit())
    })
}

fn spawn_accent_monitor(sender: Sender<WatchEvent>) {
    thread::spawn(move || {
        loop {
            let paths = accent_watch_paths();
            if paths.is_empty() {
                thread::sleep(Duration::from_secs(1));
                continue;
            }
            let mut command = inotify_command(true, &paths);
            let _ = monitor_lines(&mut command, |_| {
                thread::sleep(Duration::from_millis(500));
                let _ = sender.send(WatchEvent::Accent);
                true
            });
            thread::sleep(Duration::from_secs(1));
        }
    });
}

fn accent_watch_paths() -> Vec<PathBuf> {
    let mut providers = Vec::new();
    let directory = hooks_path(env::var_os("SCOTTLAND_HOOKS")).join("accent.d");
    let Ok(entries) = fs::read_dir(directory) else {
        return providers;
    };
    let mut entries = entries
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .collect::<Vec<_>>();
    entries.sort();
    for provider in entries {
        let Ok(metadata) = fs::metadata(&provider) else {
            continue;
        };
        if !metadata.is_file() || metadata.permissions().mode() & 0o111 == 0 {
            continue;
        }
        let Ok(output) = Command::new(&provider).arg("--watch-paths").output() else {
            continue;
        };
        if !output.status.success() {
            continue;
        }
        for line in String::from_utf8_lossy(&output.stdout).lines() {
            let path = PathBuf::from(line);
            if !line.is_empty() && path.exists() {
                providers.push(path);
            }
        }
    }
    providers.sort();
    providers.dedup();
    providers
}

fn spawn_theme_monitor(sender: Sender<WatchEvent>) {
    thread::spawn(move || {
        let location = current_theme_location();
        let mut was_present = location.is_dir();
        loop {
            let now_present = location.is_dir();
            if theme_location_appearance_should_push(&location, was_present, now_present) {
                let _ = sender.send(WatchEvent::Theme);
            }
            was_present = now_present;
            let Some(paths) = theme_watch_paths(&location) else {
                thread::sleep(Duration::from_secs(1));
                continue;
            };
            let mut command = inotify_command(false, &paths);
            let _ = monitor_lines(&mut command, |line| {
                let changed = PathBuf::from(line);
                let now_present = location.is_dir();
                if changed == location {
                    let appeared =
                        theme_location_appearance_should_push(&location, was_present, now_present);
                    was_present = now_present;
                    if appeared {
                        let _ = sender.send(WatchEvent::Theme);
                    }
                    return false;
                }
                if now_present && !was_present {
                    was_present = true;
                    if theme_location_appearance_should_push(&location, false, true) {
                        let _ = sender.send(WatchEvent::Theme);
                    }
                    return false;
                }
                if !now_present {
                    was_present = false;
                    return false;
                }
                if was_present && theme_link_event_should_push(&location, &changed) {
                    let _ = sender.send(WatchEvent::Theme);
                }
                true
            });
            if !location.is_dir() {
                was_present = false;
            }
            thread::sleep(Duration::from_secs(1));
        }
    });
}

fn inotify_command(recursive: bool, paths: &[PathBuf]) -> Command {
    let mut command = Command::new("inotifywait");
    command.args([
        "--monitor",
        "--quiet",
        "--event",
        "close_write",
        "--event",
        "moved_to",
        "--event",
        "create",
        "--event",
        "delete",
        "--event",
        "delete_self",
        "--format",
        "%w%f",
    ]);
    if recursive {
        command.arg("--recursive");
    }
    command.args(paths);
    command
}

fn current_theme_location() -> PathBuf {
    let config_home = nonempty_env("XDG_CONFIG_HOME")
        .map(PathBuf::from)
        .or_else(|| {
            env::var_os("HOME")
                .map(PathBuf::from)
                .map(|home| home.join(".config"))
        })
        .unwrap_or_else(|| PathBuf::from(".config"));
    config_home.join("gooarchy/current-theme")
}

fn theme_watch_paths(location: &Path) -> Option<Vec<PathBuf>> {
    let parent = location.parent()?;
    if !parent.is_dir() {
        return None;
    }
    let mut paths = vec![parent.to_path_buf()];
    if location.is_dir() {
        paths.push(location.to_path_buf());
    }
    Some(paths)
}

fn theme_link_event(location: &Path, changed: &Path) -> bool {
    changed.parent() == Some(location)
        && changed
            .file_name()
            .is_some_and(|name| THEME_LINKS.iter().any(|link| name == OsStr::new(link)))
}

fn theme_link_event_should_push(location: &Path, changed: &Path) -> bool {
    theme_link_event(location, changed) && theme_links_exist(location)
}

fn theme_location_appearance_should_push(
    location: &Path,
    was_present: bool,
    is_present: bool,
) -> bool {
    !was_present && is_present && theme_links_exist(location)
}

fn theme_links_exist(location: &Path) -> bool {
    THEME_LINKS
        .iter()
        .any(|name| fs::symlink_metadata(location.join(name)).is_ok())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::symlink;
    use std::sync::atomic::{AtomicU64, Ordering};

    static NEXT: AtomicU64 = AtomicU64::new(0);

    struct Scratch(PathBuf);

    impl Scratch {
        fn new() -> Self {
            let path = env::temp_dir().join(format!(
                "scottland-display-color-scheme-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&path).unwrap();
            Self(path)
        }

        fn path(&self, name: &str) -> PathBuf {
            self.0.join(name)
        }
    }

    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn helper_path_uses_hooks_fallback_and_exact_relative_binary() {
        assert_eq!(
            helper_path(Some(OsString::from("/private/hooks"))),
            PathBuf::from("/private/hooks/libexec/scottland-color-scheme")
        );
        assert_eq!(
            helper_path(Some(OsString::new())),
            PathBuf::from("/usr/lib/scottland/libexec/scottland-color-scheme")
        );
    }

    #[test]
    fn portal_monitor_is_selected_only_for_a_supported_appearance_value() {
        assert!(portal_response_has_scheme("u 1"));
        assert!(portal_response_has_scheme("(u 2)"));
        assert!(!portal_response_has_scheme("u 0"));
        assert!(!portal_response_has_scheme("u 10"));
        assert!(!portal_response_has_scheme("error: no portal"));
    }

    #[test]
    fn only_current_theme_light_or_dark_link_events_push() {
        let scratch = Scratch::new();
        let location = scratch.path("current-theme");
        fs::create_dir(&location).unwrap();
        symlink("../theme", location.join("light")).unwrap();
        assert!(theme_link_event_should_push(
            &location,
            &location.join("light")
        ));
        assert!(theme_link_event_should_push(
            &location,
            &location.join("dark")
        ));
        assert!(!theme_link_event_should_push(
            &location,
            &location.join("other")
        ));
        assert!(!theme_link_event_should_push(
            &location,
            &scratch.path("light")
        ));
        assert!(theme_links_exist(&location));

        fs::remove_file(location.join("light")).unwrap();
        assert!(!theme_links_exist(&location));
        assert!(!theme_link_event_should_push(
            &location,
            &location.join("light")
        ));
    }

    #[test]
    fn current_theme_appearance_only_has_links_when_a_link_is_present() {
        let scratch = Scratch::new();
        let location = scratch.path("current-theme");
        fs::create_dir(&location).unwrap();
        assert!(!theme_links_exist(&location));
        assert!(!theme_location_appearance_should_push(
            &location, false, true
        ));
        symlink("../dark-theme", location.join("dark")).unwrap();
        assert!(theme_links_exist(&location));
        assert!(theme_location_appearance_should_push(
            &location, false, true
        ));
        assert!(!theme_location_appearance_should_push(
            &location, true, true
        ));
    }
}

mod night_light_receiver {
    //! Private Night Light command receiver for the existing per-session display watcher.
    //!
    //! `NightLightReceiver` is an endpoint object, not a daemon. The existing watcher
    //! owns it, polls its listener beside its Wayland connection, and calls
    //! `try_serve` on readiness. This keeps gamma handles in that watcher and
    //! serializes requests with output events.

    use std::fs::{self, File, OpenOptions};
    use std::io::{self, Read, Write};
    use std::os::fd::AsRawFd;
    use std::os::unix::fs::{FileTypeExt, MetadataExt, OpenOptionsExt, PermissionsExt};
    use std::os::unix::net::{UnixListener, UnixStream};
    use std::path::{Path, PathBuf};
    use std::time::Duration;

    use scottland::dirs;
    use scottland::night_light::{
        self, Action, Command, Decision, Input, OutputInput, OutputOutcome, OutputStatus,
        RequestState,
    };
    use scottland::session::Session;

    const REQUEST_MAGIC: &[u8; 4] = b"SNL1";
    const RESPONSE_MAGIC: &[u8; 4] = b"SNR1";
    const VERSION: u8 = 1;
    const MAX_FRAME_BYTES: usize = 64 * 1024;
    const REQUEST_WAIT: Duration = Duration::from_secs(5);

    const REQUEST_ON: u8 = 1;
    const REQUEST_OFF: u8 = 2;
    const REQUEST_TOGGLE: u8 = 3;
    const REQUEST_STATUS: u8 = 4;

    #[derive(Clone, Copy, Debug, Eq, PartialEq)]
    struct SessionToken {
        device: u64,
        inode: u64,
        modified_seconds: i64,
        modified_nanoseconds: i64,
    }

    impl SessionToken {
        fn read(env_path: &Path) -> io::Result<Self> {
            let metadata = fs::metadata(env_path)?;
            Ok(Self {
                device: metadata.dev(),
                inode: metadata.ino(),
                modified_seconds: metadata.mtime(),
                modified_nanoseconds: metadata.mtime_nsec(),
            })
        }

        fn encode(self, target: &mut Vec<u8>) {
            target.extend_from_slice(&self.device.to_le_bytes());
            target.extend_from_slice(&self.inode.to_le_bytes());
            target.extend_from_slice(&self.modified_seconds.to_le_bytes());
            target.extend_from_slice(&self.modified_nanoseconds.to_le_bytes());
        }

        fn decode(bytes: &[u8]) -> Option<Self> {
            if bytes.len() < 32 {
                return None;
            }
            Some(Self {
                device: u64::from_le_bytes(bytes[0..8].try_into().ok()?),
                inode: u64::from_le_bytes(bytes[8..16].try_into().ok()?),
                modified_seconds: i64::from_le_bytes(bytes[16..24].try_into().ok()?),
                modified_nanoseconds: i64::from_le_bytes(bytes[24..32].try_into().ok()?),
            })
        }
    }

    #[derive(Clone, Copy, Debug, Eq, PartialEq)]
    struct RequestRecord {
        token: SessionToken,
        revision: u64,
        request: RequestState,
    }

    #[derive(Clone, Debug)]
    struct SessionFiles {
        directory: PathBuf,
        env: PathBuf,
        socket: PathBuf,
        state: PathBuf,
        lock: PathBuf,
    }

    impl SessionFiles {
        fn for_session(session: &Session) -> Result<Self, String> {
            validate_display_name(&session.name)?;
            let directory = selected_runtime_dir(session);
            Ok(Self {
                env: directory.join(format!("{}.env", session.name)),
                socket: directory.join(format!("{}.night-light.sock", session.name)),
                state: directory.join(format!("{}.night-light.state", session.name)),
                lock: directory.join(format!(".{}.night-light.lock", session.name)),
                directory,
            })
        }

        fn token(&self) -> Result<SessionToken, String> {
            SessionToken::read(&self.env)
                .map_err(|error| format!("could not identify the selected session: {error}"))
        }
    }

    fn selected_runtime_dir(session: &Session) -> PathBuf {
        if let Some(path) = session
            .var("SCOTTLAND_SESSION_DIR")
            .filter(|value| !value.is_empty())
        {
            return PathBuf::from(path);
        }
        let base = session
            .var("XDG_RUNTIME_DIR")
            .filter(|value| !value.is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from(format!("/run/user/{}", dirs::uid())));
        base.join("scottland")
    }

    fn validate_display_name(display: &str) -> Result<(), String> {
        if display.is_empty() || display == "." || display == ".." || display.contains('/') {
            Err("invalid session display name".to_owned())
        } else {
            Ok(())
        }
    }

    /// Operations supplied by the one Display watcher. Implementations must execute
    /// actions synchronously on its event-loop thread, in order, and return actual
    /// per-output protocol outcomes.
    pub trait GammaBackend {
        fn outputs(&mut self) -> Vec<OutputInput>;
        fn apply(&mut self, actions: &[Action]) -> Vec<OutputOutcome>;
    }

    /// Run one command through the landed pure policy and the watcher-owned gamma
    /// adapter. This is the receiver seam used by the existing color-scheme event
    /// loop and by its focused behavior tests.
    pub fn execute_night_light<B: GammaBackend>(
        command: Command,
        request: RequestState,
        backend: &mut B,
    ) -> Decision {
        let outputs = backend.outputs();
        let plan = night_light::plan(Input {
            command,
            request,
            watcher_available: true,
            outputs,
        });
        let outcomes = backend.apply(&plan.actions);
        night_light::finish(plan, &outcomes)
    }

    impl GammaBackend for crate::gamma::GammaController {
        fn outputs(&mut self) -> Vec<OutputInput> {
            crate::gamma::GammaController::outputs(self)
        }

        fn apply(&mut self, actions: &[Action]) -> Vec<OutputOutcome> {
            crate::gamma::GammaController::apply(self, actions)
        }
    }

    /// A request/reply listener owned by the current color-scheme watcher process.
    pub struct NightLightReceiver {
        files: SessionFiles,
        token: SessionToken,
        listener: UnixListener,
        socket_identity: (u64, u64),
    }

    impl NightLightReceiver {
        /// Bind the private endpoint and load only this recorded session's retained
        /// request. A replacement watcher therefore retains On, while a newly
        /// recorded session starts Off.
        pub fn bind(session: &Session) -> Result<(Self, RequestState), String> {
            let files = SessionFiles::for_session(session)?;
            ensure_private_directory(&files.directory)?;
            let token = files.token()?;
            let request = state_store(&files).initialize(token)?;
            remove_stale_socket(&files.socket)?;
            let listener = UnixListener::bind(&files.socket)
                .map_err(|error| format!("could not bind Night Light receiver: {error}"))?;
            if let Err(error) =
                fs::set_permissions(&files.socket, fs::Permissions::from_mode(0o600))
            {
                drop(listener);
                let _ = fs::remove_file(&files.socket);
                return Err(format!("could not secure Night Light receiver: {error}"));
            }
            listener
                .set_nonblocking(true)
                .map_err(|error| format!("could not configure Night Light receiver: {error}"))?;
            let metadata = fs::symlink_metadata(&files.socket)
                .map_err(|error| format!("could not inspect Night Light receiver: {error}"))?;
            let socket_identity = (metadata.dev(), metadata.ino());
            Ok((
                Self {
                    files,
                    token,
                    listener,
                    socket_identity,
                },
                request,
            ))
        }

        /// Descriptor for the existing watcher event loop to poll alongside Wayland.
        pub fn poll_fd(&self) -> i32 {
            self.listener.as_raw_fd()
        }

        /// Reject a stale watcher if the session record was replaced underneath it.
        pub fn session_is_current(&self) -> bool {
            self.files.token().ok() == Some(self.token)
        }

        /// Accept and execute at most one ready request. `Ok(None)` means the socket
        /// had no client, allowing the same event loop to continue Wayland work.
        pub fn try_serve<B: GammaBackend>(
            &mut self,
            backend: &mut B,
        ) -> Result<Option<Decision>, String> {
            let (mut stream, _) = match self.listener.accept() {
                Ok(connection) => connection,
                Err(error) if error.kind() == io::ErrorKind::WouldBlock => return Ok(None),
                Err(error) => return Err(format!("Night Light receiver accept failed: {error}")),
            };
            stream
                .set_read_timeout(Some(REQUEST_WAIT))
                .map_err(|error| format!("could not bound receiver read: {error}"))?;
            stream
                .set_write_timeout(Some(REQUEST_WAIT))
                .map_err(|error| format!("could not bound receiver reply: {error}"))?;
            if peer_uid(&stream)? != dirs::uid() {
                return Err("Night Light receiver rejected a different user".to_owned());
            }
            let frame = read_request(&mut stream)?;
            let Some((token, command)) = decode_request(&frame) else {
                return Err("Night Light receiver rejected a malformed request".to_owned());
            };
            if token != self.token || !self.session_is_current() {
                return Err("Night Light receiver belongs to a stale session".to_owned());
            }
            let store = state_store(&self.files);
            let record = store.read(self.token)?;
            let decision = execute_night_light(command, record.request, backend);
            if command != Command::Status {
                store.write_if_revision(record, decision.request)?;
            }
            write_response(&mut stream, self.token, &decision)?;
            Ok(Some(decision))
        }

        /// Reapply a retained request immediately after this replacement watcher
        /// acquires its protocol connection. The caller must do this before polling
        /// for ordinary input so NL9's two-second bound is met.
        pub fn reapply_after_restart<B: GammaBackend>(
            &self,
            request: RequestState,
            backend: &mut B,
        ) -> Result<Option<Decision>, String> {
            if request != RequestState::On {
                return Ok(None);
            }
            if !self.session_is_current() {
                return Err("Night Light receiver belongs to a stale session".to_owned());
            }
            let decision = execute_night_light(Command::On, request, backend);
            let store = state_store(&self.files);
            let record = store.read(self.token)?;
            store.write_if_revision(record, decision.request)?;
            Ok(Some(decision))
        }

        /// Reconcile a clear-off written by a client whose request could not reach
        /// this listener. The existing watcher should call this after each event
        /// batch and on its bounded idle tick; it restores only handles the protocol
        /// adapter still reports as Night Light-owned.
        pub fn reconcile_persisted_off<B: GammaBackend>(
            &self,
            backend: &mut B,
        ) -> Result<Option<Decision>, String> {
            if !self.session_is_current() {
                return Err("Night Light receiver belongs to a stale session".to_owned());
            }
            let store = state_store(&self.files);
            let record = store.read(self.token)?;
            if record.request != RequestState::Off {
                return Ok(None);
            }
            let outputs = backend.outputs();
            let has_owned_gamma = outputs.iter().any(|output| {
                matches!(
                    output.ownership,
                    scottland::night_light::OutputOwnership::WarmOwned
                        | scottland::night_light::OutputOwnership::OwnedUnwarmed
                )
            });
            if !has_owned_gamma {
                return Ok(None);
            }
            let plan = night_light::plan(Input {
                command: Command::Off,
                request: RequestState::Off,
                watcher_available: true,
                outputs,
            });
            let outcomes = backend.apply(&plan.actions);
            let decision = night_light::finish(plan, &outcomes);
            store.write_if_revision(record, decision.request)?;
            Ok(Some(decision))
        }
    }

    impl Drop for NightLightReceiver {
        fn drop(&mut self) {
            if let Ok(metadata) = fs::symlink_metadata(&self.files.socket)
                && metadata.file_type().is_socket()
                && (metadata.dev(), metadata.ino()) == self.socket_identity
            {
                let _ = fs::remove_file(&self.files.socket);
            }
        }
    }

    /// Send one typed command to the selected session's existing watcher.
    ///
    /// If the watcher is unavailable, `on`/`toggle` leave the retained request
    /// unchanged; `off` writes Off under the same state lock before attempting IPC.
    /// `status` returns an unknown-watcher decision from the landed policy.
    pub fn request_night_light(session: &Session, command: Command) -> Result<Decision, String> {
        let files = SessionFiles::for_session(session)?;
        let token = files.token()?;
        let store = state_store(&files);
        if command == Command::Off {
            store.force_off(token)?;
        }
        let mut stream = match UnixStream::connect(&files.socket) {
            Ok(stream) => stream,
            Err(_error) => {
                let record = store.read(token)?;
                return Ok(unavailable_decision(command, record.request));
            }
        };
        stream
            .set_read_timeout(Some(REQUEST_WAIT))
            .map_err(|error| format!("Night Light watcher did not reply in time: {error}"))?;
        stream
            .set_write_timeout(Some(REQUEST_WAIT))
            .map_err(|error| {
                format!("Night Light watcher did not accept the request in time: {error}")
            })?;
        let request = encode_request(token, command);
        stream
            .write_all(&request)
            .map_err(|error| format!("could not send Night Light command: {error}"))?;
        let frame = read_response(&mut stream)?;
        decode_response(&frame, token)
            .ok_or_else(|| "Night Light watcher returned a stale or malformed response".to_owned())
    }

    fn unavailable_decision(command: Command, request: RequestState) -> Decision {
        let plan = night_light::plan(Input {
            command,
            request,
            watcher_available: false,
            outputs: Vec::new(),
        });
        night_light::finish(plan, &[])
    }

    #[derive(Clone, Copy)]
    struct StateStore<'a> {
        files: &'a SessionFiles,
    }

    fn state_store(files: &SessionFiles) -> StateStore<'_> {
        StateStore { files }
    }

    impl StateStore<'_> {
        fn initialize(&self, token: SessionToken) -> Result<RequestState, String> {
            let _lock = self.lock()?;
            let existing = self.read_unlocked()?;
            if let Some(record) = existing.filter(|record| record.token == token) {
                return Ok(record.request);
            }
            let revision = existing.map_or(0, |record| record.revision.saturating_add(1));
            self.write_unlocked(RequestRecord {
                token,
                revision,
                request: RequestState::Off,
            })?;
            Ok(RequestState::Off)
        }

        fn read(&self, token: SessionToken) -> Result<RequestRecord, String> {
            let _lock = self.lock()?;
            let record = self.read_unlocked()?.filter(|record| record.token == token);
            Ok(record.unwrap_or(RequestRecord {
                token,
                revision: 0,
                request: RequestState::Off,
            }))
        }

        fn force_off(&self, token: SessionToken) -> Result<(), String> {
            let _lock = self.lock()?;
            let previous = self.read_unlocked()?;
            let revision = previous
                .filter(|record| record.token == token)
                .map_or(0, |record| record.revision.saturating_add(1));
            self.write_unlocked(RequestRecord {
                token,
                revision,
                request: RequestState::Off,
            })
        }

        fn write_if_revision(
            &self,
            previous: RequestRecord,
            request: RequestState,
        ) -> Result<(), String> {
            let _lock = self.lock()?;
            let current = self.read_unlocked()?;
            if current != Some(previous) {
                return Ok(());
            }
            self.write_unlocked(RequestRecord {
                revision: previous.revision.saturating_add(1),
                request,
                ..previous
            })
        }

        fn lock(&self) -> Result<StateLock, String> {
            ensure_private_directory(&self.files.directory)?;
            if fs::symlink_metadata(&self.files.lock)
                .is_ok_and(|metadata| metadata.file_type().is_symlink())
            {
                return Err("Night Light state lock path is a symlink".to_owned());
            }
            let file = OpenOptions::new()
                .read(true)
                .write(true)
                .create(true)
                .truncate(false)
                .mode(0o600)
                .open(&self.files.lock)
                .map_err(|error| format!("could not open Night Light state lock: {error}"))?;
            validate_private_file(&file, "Night Light state lock")?;
            // flock serializes clear-off with watcher replacement/startup and with
            // each server-side request commit.
            let result = unsafe { flock(file.as_raw_fd(), LOCK_EX) };
            if result != 0 {
                return Err(format!(
                    "could not lock Night Light state: {}",
                    io::Error::last_os_error()
                ));
            }
            Ok(StateLock(file))
        }

        fn read_unlocked(&self) -> Result<Option<RequestRecord>, String> {
            if fs::symlink_metadata(&self.files.state)
                .is_ok_and(|metadata| metadata.file_type().is_symlink())
            {
                return Err("Night Light state path is a symlink".to_owned());
            }
            let file = match OpenOptions::new().read(true).open(&self.files.state) {
                Ok(file) => file,
                Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
                Err(error) => return Err(format!("could not read Night Light state: {error}")),
            };
            validate_private_file(&file, "Night Light state")?;
            let mut bytes = Vec::new();
            file.take(256)
                .read_to_end(&mut bytes)
                .map_err(|error| format!("could not read Night Light state: {error}"))?;
            Ok(decode_state(&bytes))
        }

        fn write_unlocked(&self, record: RequestRecord) -> Result<(), String> {
            let mut bytes = Vec::with_capacity(96);
            bytes.extend_from_slice(b"SSTATE1 ");
            record.token.encode(&mut bytes);
            bytes.extend_from_slice(&record.revision.to_le_bytes());
            bytes.push(match record.request {
                RequestState::Off => 0,
                RequestState::On => 1,
            });
            let temporary = unique_temp_path(&self.files.state);
            let mut file = OpenOptions::new()
                .write(true)
                .create_new(true)
                .mode(0o600)
                .open(&temporary)
                .map_err(|error| format!("could not create Night Light state: {error}"))?;
            if let Err(error) = file.write_all(&bytes).and_then(|_| file.sync_all()) {
                drop(file);
                let _ = fs::remove_file(&temporary);
                return Err(format!("could not persist Night Light state: {error}"));
            }
            drop(file);
            if let Err(error) = fs::rename(&temporary, &self.files.state) {
                let _ = fs::remove_file(&temporary);
                return Err(format!("could not replace Night Light state: {error}"));
            }
            Ok(())
        }
    }

    struct StateLock(File);

    impl Drop for StateLock {
        fn drop(&mut self) {
            let _ = unsafe { flock(self.0.as_raw_fd(), LOCK_UN) };
        }
    }

    fn decode_state(bytes: &[u8]) -> Option<RequestRecord> {
        if !bytes.starts_with(b"SSTATE1 ") || bytes.len() != 8 + 32 + 8 + 1 {
            return None;
        }
        let token = SessionToken::decode(&bytes[8..40])?;
        let revision = u64::from_le_bytes(bytes[40..48].try_into().ok()?);
        let request = match bytes[48] {
            0 => RequestState::Off,
            1 => RequestState::On,
            _ => return None,
        };
        Some(RequestRecord {
            token,
            revision,
            request,
        })
    }

    fn unique_temp_path(path: &Path) -> PathBuf {
        static NEXT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
        let suffix = NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        let file = path.file_name().unwrap_or_default().to_string_lossy();
        path.with_file_name(format!(".{file}.{}.{}.tmp", std::process::id(), suffix))
    }

    fn ensure_private_directory(path: &Path) -> Result<(), String> {
        let metadata = fs::symlink_metadata(path).map_err(|error| {
            format!("selected session runtime directory is unavailable: {error}")
        })?;
        if !metadata.is_dir() || metadata.file_type().is_symlink() || metadata.uid() != dirs::uid()
        {
            return Err("selected session runtime directory is not privately owned".to_owned());
        }
        Ok(())
    }

    fn validate_private_file(file: &File, label: &str) -> Result<(), String> {
        let metadata = file
            .metadata()
            .map_err(|error| format!("could not inspect {label}: {error}"))?;
        if !metadata.is_file()
            || metadata.uid() != dirs::uid()
            || metadata.permissions().mode() & 0o077 != 0
        {
            return Err(format!("{label} is not a private file owned by this user"));
        }
        Ok(())
    }

    fn remove_stale_socket(path: &Path) -> Result<(), String> {
        let metadata = match fs::symlink_metadata(path) {
            Ok(metadata) => metadata,
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(()),
            Err(error) => return Err(format!("could not inspect the receiver endpoint: {error}")),
        };
        if !metadata.file_type().is_socket() || metadata.uid() != dirs::uid() {
            return Err("receiver endpoint path is occupied by a non-owned object".to_owned());
        }
        match UnixStream::connect(path) {
            Ok(_) => Err("Night Light receiver is already running for this session".to_owned()),
            Err(error) if error.kind() == io::ErrorKind::ConnectionRefused => fs::remove_file(path)
                .map_err(|error| format!("could not remove a stale receiver endpoint: {error}")),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
            Err(error) => Err(format!(
                "could not verify existing receiver endpoint: {error}"
            )),
        }
    }

    fn encode_request(token: SessionToken, command: Command) -> Vec<u8> {
        let mut bytes = Vec::with_capacity(38);
        bytes.extend_from_slice(REQUEST_MAGIC);
        bytes.push(VERSION);
        token.encode(&mut bytes);
        bytes.push(command_code(command));
        bytes
    }

    fn decode_request(bytes: &[u8]) -> Option<(SessionToken, Command)> {
        if bytes.len() != 38 || &bytes[..4] != REQUEST_MAGIC || bytes[4] != VERSION {
            return None;
        }
        let token = SessionToken::decode(&bytes[5..37])?;
        let command = match bytes[37] {
            REQUEST_ON => Command::On,
            REQUEST_OFF => Command::Off,
            REQUEST_TOGGLE => Command::Toggle,
            REQUEST_STATUS => Command::Status,
            _ => return None,
        };
        Some((token, command))
    }

    fn command_code(command: Command) -> u8 {
        match command {
            Command::On => REQUEST_ON,
            Command::Off => REQUEST_OFF,
            Command::Toggle => REQUEST_TOGGLE,
            Command::Status => REQUEST_STATUS,
        }
    }

    fn read_request(stream: &mut UnixStream) -> Result<Vec<u8>, String> {
        let mut frame = vec![0; 38];
        stream
            .read_exact(&mut frame)
            .map_err(|error| format!("could not read Night Light command: {error}"))?;
        Ok(frame)
    }

    fn write_response(
        stream: &mut UnixStream,
        token: SessionToken,
        decision: &Decision,
    ) -> Result<(), String> {
        let frame = encode_response(token, decision)?;
        stream
            .write_all(&frame)
            .map_err(|error| format!("could not return Night Light decision: {error}"))
    }

    fn encode_response(token: SessionToken, decision: &Decision) -> Result<Vec<u8>, String> {
        let payload = encode_decision(decision)?;
        if payload.len() > MAX_FRAME_BYTES {
            return Err("Night Light decision exceeded the private response limit".to_owned());
        }
        let mut frame = Vec::with_capacity(4 + 1 + 32 + 4 + payload.len());
        frame.extend_from_slice(RESPONSE_MAGIC);
        frame.push(VERSION);
        token.encode(&mut frame);
        frame.extend_from_slice(&(payload.len() as u32).to_le_bytes());
        frame.extend_from_slice(&payload);
        Ok(frame)
    }

    fn read_response(stream: &mut UnixStream) -> Result<Vec<u8>, String> {
        let mut header = [0u8; 41];
        stream
            .read_exact(&mut header)
            .map_err(|error| format!("Night Light watcher did not return a decision: {error}"))?;
        if &header[..4] != RESPONSE_MAGIC || header[4] != VERSION {
            return Err("Night Light watcher returned an invalid response header".to_owned());
        }
        let total =
            u32::from_le_bytes(header[37..41].try_into().expect("fixed response header")) as usize;
        if total > MAX_FRAME_BYTES {
            return Err("Night Light watcher response exceeded the size limit".to_owned());
        }
        let mut frame = header.to_vec();
        frame.resize(41 + total, 0);
        stream
            .read_exact(&mut frame[41..])
            .map_err(|error| format!("could not read Night Light decision: {error}"))?;
        Ok(frame)
    }

    fn decode_response(bytes: &[u8], expected: SessionToken) -> Option<Decision> {
        if bytes.len() < 41 || &bytes[..4] != RESPONSE_MAGIC || bytes[4] != VERSION {
            return None;
        }
        let token = SessionToken::decode(&bytes[5..37])?;
        if token != expected {
            return None;
        }
        let payload_len = u32::from_le_bytes(bytes[37..41].try_into().ok()?) as usize;
        if payload_len > MAX_FRAME_BYTES || bytes.len() != 41 + payload_len {
            return None;
        }
        decode_decision(&bytes[41..])
    }

    fn encode_decision(decision: &Decision) -> Result<Vec<u8>, String> {
        let mut bytes = Vec::new();
        bytes.push(request_code(decision.request));
        bytes.push(decision.exit_status);
        push_count(&mut bytes, decision.actions.len())?;
        for action in &decision.actions {
            match action {
                Action::AcquireAndApply4000K { output } => {
                    bytes.push(1);
                    push_string(&mut bytes, output)?;
                }
                Action::Apply4000K { output } => {
                    bytes.push(2);
                    push_string(&mut bytes, output)?;
                }
                Action::RestoreOwned { output } => {
                    bytes.push(3);
                    push_string(&mut bytes, output)?;
                }
                Action::ReleaseUnwarmed { output } => {
                    bytes.push(4);
                    push_string(&mut bytes, output)?;
                }
            }
        }
        push_count(&mut bytes, decision.outputs.len())?;
        for output in &decision.outputs {
            push_string(&mut bytes, &output.identity)?;
            match &output.status {
                OutputStatus::Warm => bytes.push(1),
                OutputStatus::Normal => bytes.push(2),
                OutputStatus::NotWarm { reason } => {
                    bytes.push(3);
                    push_string(&mut bytes, reason)?;
                }
                OutputStatus::Unknown { reason } => {
                    bytes.push(4);
                    push_string(&mut bytes, reason)?;
                }
            }
        }
        push_string(&mut bytes, &decision.stdout)?;
        match &decision.stderr {
            Some(stderr) => {
                bytes.push(1);
                push_string(&mut bytes, stderr)?;
            }
            None => bytes.push(0),
        }
        if bytes.len() > MAX_FRAME_BYTES {
            return Err("Night Light decision exceeded the private response limit".to_owned());
        }
        Ok(bytes)
    }

    fn decode_decision(bytes: &[u8]) -> Option<Decision> {
        let mut cursor = FrameCursor { bytes, offset: 0 };
        let request = decode_request_code(cursor.byte()?)?;
        let exit_status = cursor.byte()?;
        let action_count = cursor.count()?;
        let mut actions = Vec::with_capacity(action_count);
        for _ in 0..action_count {
            let tag = cursor.byte()?;
            let output = cursor.string()?;
            actions.push(match tag {
                1 => Action::AcquireAndApply4000K { output },
                2 => Action::Apply4000K { output },
                3 => Action::RestoreOwned { output },
                4 => Action::ReleaseUnwarmed { output },
                _ => return None,
            });
        }
        let output_count = cursor.count()?;
        let mut outputs = Vec::with_capacity(output_count);
        for _ in 0..output_count {
            let identity = cursor.string()?;
            let status = match cursor.byte()? {
                1 => OutputStatus::Warm,
                2 => OutputStatus::Normal,
                3 => OutputStatus::NotWarm {
                    reason: cursor.string()?,
                },
                4 => OutputStatus::Unknown {
                    reason: cursor.string()?,
                },
                _ => return None,
            };
            outputs.push(night_light::OutputReport { identity, status });
        }
        let stdout = cursor.string()?;
        let stderr = match cursor.byte()? {
            0 => None,
            1 => Some(cursor.string()?),
            _ => return None,
        };
        if cursor.offset != bytes.len() {
            return None;
        }
        Some(Decision {
            request,
            actions,
            outputs,
            stdout,
            stderr,
            exit_status,
        })
    }

    fn request_code(request: RequestState) -> u8 {
        match request {
            RequestState::Off => 0,
            RequestState::On => 1,
        }
    }

    fn decode_request_code(value: u8) -> Option<RequestState> {
        match value {
            0 => Some(RequestState::Off),
            1 => Some(RequestState::On),
            _ => None,
        }
    }

    fn push_count(bytes: &mut Vec<u8>, count: usize) -> Result<(), String> {
        let count =
            u16::try_from(count).map_err(|_| "too many Night Light response entries".to_owned())?;
        bytes.extend_from_slice(&count.to_le_bytes());
        Ok(())
    }

    fn push_string(bytes: &mut Vec<u8>, value: &str) -> Result<(), String> {
        let value = value.as_bytes();
        let length = u32::try_from(value.len())
            .map_err(|_| "Night Light response field is too large".to_owned())?;
        bytes.extend_from_slice(&length.to_le_bytes());
        bytes.extend_from_slice(value);
        Ok(())
    }

    struct FrameCursor<'a> {
        bytes: &'a [u8],
        offset: usize,
    }

    impl FrameCursor<'_> {
        fn byte(&mut self) -> Option<u8> {
            let value = *self.bytes.get(self.offset)?;
            self.offset += 1;
            Some(value)
        }

        fn count(&mut self) -> Option<usize> {
            let end = self.offset.checked_add(2)?;
            let value = u16::from_le_bytes(self.bytes.get(self.offset..end)?.try_into().ok()?);
            self.offset = end;
            Some(usize::from(value))
        }

        fn string(&mut self) -> Option<String> {
            let end = self.offset.checked_add(4)?;
            let length =
                u32::from_le_bytes(self.bytes.get(self.offset..end)?.try_into().ok()?) as usize;
            self.offset = end;
            let end = self.offset.checked_add(length)?;
            let value = String::from_utf8(self.bytes.get(self.offset..end)?.to_vec()).ok()?;
            self.offset = end;
            Some(value)
        }
    }

    fn peer_uid(stream: &UnixStream) -> Result<u32, String> {
        let mut credentials = UCred {
            pid: 0,
            uid: 0,
            gid: 0,
        };
        let mut length = std::mem::size_of::<UCred>() as u32;
        let result = unsafe {
            getsockopt(
                stream.as_raw_fd(),
                SOL_SOCKET,
                SO_PEERCRED,
                (&mut credentials as *mut UCred).cast(),
                &mut length,
            )
        };
        if result != 0 || length as usize != std::mem::size_of::<UCred>() {
            return Err(format!(
                "could not verify Night Light request owner: {}",
                io::Error::last_os_error()
            ));
        }
        Ok(credentials.uid)
    }

    #[repr(C)]
    struct UCred {
        pid: i32,
        uid: u32,
        gid: u32,
    }

    const LOCK_EX: i32 = 2;
    const LOCK_UN: i32 = 8;
    const SOL_SOCKET: i32 = 1;
    const SO_PEERCRED: i32 = 17;

    unsafe extern "C" {
        fn flock(fd: i32, operation: i32) -> i32;
        fn getsockopt(
            fd: i32,
            level: i32,
            option: i32,
            value: *mut std::ffi::c_void,
            length: *mut u32,
        ) -> i32;
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        use scottland::night_light::Outcome;
        use std::os::unix::fs::PermissionsExt;
        use std::sync::atomic::{AtomicU64, Ordering};

        static NEXT: AtomicU64 = AtomicU64::new(0);

        fn token() -> SessionToken {
            SessionToken {
                device: 2,
                inode: 9,
                modified_seconds: 10,
                modified_nanoseconds: 11,
            }
        }

        struct FakeGamma {
            outputs: Vec<OutputInput>,
            outcomes: Vec<OutputOutcome>,
            applied: Vec<Action>,
        }

        impl GammaBackend for FakeGamma {
            fn outputs(&mut self) -> Vec<OutputInput> {
                self.outputs.clone()
            }

            fn apply(&mut self, actions: &[Action]) -> Vec<OutputOutcome> {
                self.applied.extend_from_slice(actions);
                self.outcomes.clone()
            }
        }

        fn runtime_files() -> (PathBuf, SessionFiles) {
            let root = std::env::temp_dir().join(format!(
                "scottland-night-light-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&root).unwrap();
            fs::set_permissions(&root, fs::Permissions::from_mode(0o700)).unwrap();
            let files = SessionFiles {
                directory: root.clone(),
                env: root.join("wayland-1.env"),
                socket: root.join("wayland-1.night-light.sock"),
                state: root.join("wayland-1.night-light.state"),
                lock: root.join(".wayland-1.night-light.lock"),
            };
            fs::write(&files.env, b"WAYLAND_DISPLAY=wayland-1\0").unwrap();
            (root, files)
        }

        #[test]
        fn request_frame_round_trips_only_the_four_night_light_commands() {
            for command in [Command::On, Command::Off, Command::Toggle, Command::Status] {
                let encoded = encode_request(token(), command);
                assert_eq!(decode_request(&encoded), Some((token(), command)));
            }
            let mut malformed = encode_request(token(), Command::On);
            malformed[37] = 0xff;
            assert_eq!(decode_request(&malformed), None);
        }

        #[test]
        fn response_preserves_policy_exit_and_diagnostic_bytes() {
            let decision = Decision {
                request: RequestState::Off,
                actions: vec![Action::RestoreOwned {
                    output: "DP-1".to_owned(),
                }],
                outputs: vec![night_light::OutputReport {
                    identity: "DP-1".to_owned(),
                    status: OutputStatus::Unknown {
                        reason: "no ramp".to_owned(),
                    },
                }],
                stdout: "off\nDP-1: unknown: no ramp\n".to_owned(),
                stderr: Some("unavailable\noutput unknown".to_owned()),
                exit_status: 1,
            };
            let frame = encode_response(token(), &decision).unwrap();
            let result = decode_response(&frame, token()).unwrap();
            assert_eq!(result.stdout, decision.stdout);
            assert_eq!(result.stderr, decision.stderr);
            assert_eq!(result.exit_status, 1);
            assert_eq!(result.request, RequestState::Off);
            assert_eq!(result.actions, decision.actions);
            assert_eq!(result.outputs, decision.outputs);
        }

        #[test]
        fn response_from_another_session_is_rejected() {
            let decision = unavailable_decision(Command::Status, RequestState::Off);
            let frame = encode_response(token(), &decision).unwrap();
            let mut other = token();
            other.inode += 1;
            assert_eq!(decode_response(&frame, other), None);
        }

        #[test]
        fn unavailable_watcher_keeps_on_unchanged_but_clears_off() {
            let on = unavailable_decision(Command::On, RequestState::Off);
            assert_eq!(on.request, RequestState::Off);
            assert_eq!(on.exit_status, 1);
            assert!(on.stderr.unwrap().contains("request unchanged"));

            let off = unavailable_decision(Command::Off, RequestState::On);
            assert_eq!(off.request, RequestState::Off);
            assert_eq!(off.exit_status, 1);
            assert!(off.stderr.unwrap().contains("request cleared"));
        }

        #[test]
        fn state_record_is_session_scoped_and_revisioned() {
            let record = RequestRecord {
                token: token(),
                revision: 4,
                request: RequestState::On,
            };
            let mut bytes = Vec::from(&b"SSTATE1 "[..]);
            record.token.encode(&mut bytes);
            bytes.extend_from_slice(&record.revision.to_le_bytes());
            bytes.push(1);
            assert_eq!(decode_state(&bytes), Some(record));
            assert_eq!(decode_state(&bytes[..bytes.len() - 1]), None);
        }

        #[test]
        fn watcher_replacement_keeps_on_but_a_new_session_starts_off_and_off_wins_races() {
            let (root, files) = runtime_files();
            let store = state_store(&files);
            let first_token = files.token().unwrap();
            assert_eq!(store.initialize(first_token).unwrap(), RequestState::Off);
            let initial = store.read(first_token).unwrap();
            store.write_if_revision(initial, RequestState::On).unwrap();
            assert_eq!(store.initialize(first_token).unwrap(), RequestState::On);

            let stale_on = store.read(first_token).unwrap();
            store.force_off(first_token).unwrap();
            store.write_if_revision(stale_on, RequestState::On).unwrap();
            assert_eq!(store.read(first_token).unwrap().request, RequestState::Off);

            let before = store.read(first_token).unwrap();
            store.write_if_revision(before, RequestState::On).unwrap();
            let replacement = root.join("next.env");
            fs::write(&replacement, b"WAYLAND_DISPLAY=wayland-1\0").unwrap();
            fs::rename(&replacement, &files.env).unwrap();
            let next_token = files.token().unwrap();
            assert_ne!(first_token, next_token);
            assert_eq!(store.initialize(next_token).unwrap(), RequestState::Off);
            assert_eq!(store.read(next_token).unwrap().request, RequestState::Off);
            fs::remove_dir_all(root).unwrap();
        }

        #[test]
        fn receiver_applies_the_policy_plan_and_returns_the_actual_backend_outcome() {
            let mut backend = FakeGamma {
                outputs: vec![OutputInput {
                    identity: "DP-1".to_owned(),
                    ownership: night_light::OutputOwnership::Unowned,
                    reason: None,
                }],
                outcomes: vec![OutputOutcome {
                    identity: "DP-1".to_owned(),
                    acquisition: Outcome::Failed("gamma is owned elsewhere".to_owned()),
                    application: Outcome::NotAttempted,
                    restoration: Outcome::NotAttempted,
                }],
                applied: Vec::new(),
            };
            let decision = execute_night_light(Command::On, RequestState::Off, &mut backend);
            assert_eq!(
                backend.applied,
                [Action::AcquireAndApply4000K {
                    output: "DP-1".to_owned()
                }]
            );
            assert_eq!(decision.request, RequestState::Off);
            assert_eq!(decision.exit_status, 1);
            assert!(
                decision
                    .stderr
                    .unwrap()
                    .contains("DP-1: not warm: gamma is owned elsewhere")
            );
        }
    }
}
pub(crate) use self::night_light_receiver::{
    GammaBackend, NightLightReceiver, execute_night_light, request_night_light,
};
