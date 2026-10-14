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

const HELPER_RELATIVE: &str = "libexec/scottland-color-scheme";
const THEME_LINKS: [&str; 2] = ["light", "dark"];
const SIGNAL_TERM: i32 = 15;
const SIGNAL_KILL: i32 = 9;

unsafe extern "C" {
    fn setpgid(pid: i32, pgid: i32) -> i32;
    fn kill(pid: i32, signal: i32) -> i32;
    fn getuid() -> u32;
}

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
    let (sender, receiver) = mpsc::channel();
    spawn_settings_monitor(sender.clone());
    spawn_text_monitor(sender.clone());
    spawn_accent_monitor(sender.clone());
    spawn_theme_monitor(sender);

    while let Ok(event) = receiver.recv() {
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
    Ok(())
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
