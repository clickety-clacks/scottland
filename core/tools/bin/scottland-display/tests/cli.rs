//! The command's user-visible text-size behavior with an isolated GSettings boundary.

use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::UnixListener;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Output};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::thread;
use std::time::{Duration, Instant};

static NEXT: AtomicU64 = AtomicU64::new(0);

unsafe extern "C" {
    fn kill(pid: i32, signal: i32) -> i32;
}

const BINARY: &str = env!("CARGO_BIN_EXE_scottland-display");
const SCHEMA: &str = "org.gnome.desktop.interface";
const KEY: &str = "text-scaling-factor";

struct Scratch {
    root: PathBuf,
    bin: PathBuf,
    home: PathBuf,
    hooks: PathBuf,
    calls: PathBuf,
    helper_calls: PathBuf,
    monitor_calls: PathBuf,
    state: PathBuf,
}

impl Scratch {
    fn new() -> Self {
        let root = Path::new(env!("CARGO_TARGET_TMPDIR")).join(format!(
            "scottland-display-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&root).expect("create unique scratch directory");

        let bin = root.join("bin");
        let home = root.join("home");
        let hooks = root.join("hooks");
        fs::create_dir(&bin).unwrap();
        fs::create_dir(&home).unwrap();
        fs::create_dir_all(hooks.join("libexec")).unwrap();
        let calls = root.join("calls");
        let helper_calls = root.join("helper-calls");
        let monitor_calls = root.join("monitor-calls");
        let state = root.join("state");
        let fake = bin.join("gsettings");
        fs::write(
            &fake,
            r##"#!/bin/sh
set -eu
printf '%s\n' "$*" >>"$CALLS"
if [ "${FAIL_GSETTINGS:-0}" = 1 ]; then
  echo 'fixture gsettings failure' >&2
  exit 7
fi
if [ "$1" = monitor ]; then
  printf '%s\n' "$$" >>"${MONITOR_PIDS:-/dev/null}"
  echo 'text-scaling-factor changed'
  exec sleep 120
fi
[ "$2" = "org.gnome.desktop.interface" ] || exit 64
[ "$3" = "text-scaling-factor" ] || exit 64
case "$1" in
  get)
    [ "$#" -eq 3 ] || exit 64
    if [ -f "$STATE_FILE" ]; then cat "$STATE_FILE"; else printf '1.0\n'; fi
    ;;
  set)
    [ "$#" -eq 4 ] || exit 64
    printf '%s\n' "$4" >"$STATE_FILE"
    ;;
  reset)
    [ "$#" -eq 3 ] || exit 64
    rm -f "$STATE_FILE"
    ;;
  *) exit 64 ;;
esac
"##,
        )
        .unwrap();
        let mut permissions = fs::metadata(&fake).unwrap().permissions();
        permissions.set_mode(0o755);
        fs::set_permissions(&fake, permissions).unwrap();

        for (name, body) in [
            ("busctl", "#!/bin/sh\nprintf 'u 0\\n'\n"),
            (
                "gdbus",
                "#!/bin/sh\nprintf 'gdbus %s\\n' \"$*\" >>\"$MONITOR_CALLS\"\nprintf '%s\\n' \"$$\" >>\"$MONITOR_PIDS\"\necho 'color-scheme changed'\nexec sleep 120\n",
            ),
            (
                "inotifywait",
                "#!/bin/sh\nprintf 'inotifywait %s\\n' \"$*\" >>\"$MONITOR_CALLS\"\nprintf '%s\\n' \"$$\" >>\"$MONITOR_PIDS\"\nexec sleep 120\n",
            ),
        ] {
            let path = bin.join(name);
            fs::write(&path, body).unwrap();
            let mut permissions = fs::metadata(&path).unwrap().permissions();
            permissions.set_mode(0o755);
            fs::set_permissions(path, permissions).unwrap();
        }

        let fake_helper = hooks.join("libexec/scottland-color-scheme");
        fs::write(
            &fake_helper,
            r##"#!/bin/sh
set -eu
printf '%s\n' "$*" >>"$HELPER_CALLS"
if [ "${FAIL_HELPER:-0}" = 1 ]; then
  echo 'fixture helper failure' >&2
  exit 9
fi
case "$1" in
  cursor-size) printf '24\n' ;;
  once|terminal-theme-push) ;;
  *) echo "unexpected action: $1" >&2; exit 64 ;;
esac
"##,
        )
        .unwrap();
        let mut permissions = fs::metadata(&fake_helper).unwrap().permissions();
        permissions.set_mode(0o755);
        fs::set_permissions(&fake_helper, permissions).unwrap();
        std::os::unix::fs::symlink(BINARY, bin.join("scottland-display"))
            .expect("link the command into the session hook PATH");

        Self {
            root,
            bin,
            home,
            hooks,
            calls,
            helper_calls,
            monitor_calls,
            state,
        }
    }

    fn run(&self, args: &[&str]) -> Output {
        self.run_with_failure(args, false)
    }

    fn run_with_failure(&self, args: &[&str], fail: bool) -> Output {
        let mut paths = vec![self.bin.clone()];
        paths.extend(std::env::split_paths(
            &std::env::var_os("PATH").unwrap_or_default(),
        ));
        let path = std::env::join_paths(paths).unwrap();
        Command::new(BINARY)
            .args(args)
            .env("PATH", path)
            .env("HOME", &self.home)
            .env("SCOTTLAND_HOOKS", &self.hooks)
            .env("CALLS", &self.calls)
            .env("HELPER_CALLS", &self.helper_calls)
            .env("STATE_FILE", &self.state)
            .env("FAIL_GSETTINGS", if fail { "1" } else { "0" })
            .output()
            .unwrap()
    }

    fn run_with_helper_failure(&self, args: &[&str], fail: bool) -> Output {
        let mut paths = vec![self.bin.clone()];
        paths.extend(std::env::split_paths(
            &std::env::var_os("PATH").unwrap_or_default(),
        ));
        let path = std::env::join_paths(paths).unwrap();
        Command::new(BINARY)
            .args(args)
            .env("PATH", path)
            .env("HOME", &self.home)
            .env("SCOTTLAND_HOOKS", &self.hooks)
            .env("CALLS", &self.calls)
            .env("HELPER_CALLS", &self.helper_calls)
            .env("STATE_FILE", &self.state)
            .env("FAIL_HELPER", if fail { "1" } else { "0" })
            .output()
            .unwrap()
    }

    fn calls(&self) -> Vec<String> {
        fs::read_to_string(&self.calls)
            .unwrap_or_default()
            .lines()
            .map(str::to_string)
            .collect()
    }

    fn ghostty_config(&self) -> PathBuf {
        self.home.join(".config/ghostty/config")
    }

    fn helper_calls(&self) -> Vec<String> {
        fs::read_to_string(&self.helper_calls)
            .unwrap_or_default()
            .lines()
            .map(str::to_string)
            .collect()
    }

    fn monitor_pids(&self) -> Vec<i32> {
        fs::read_to_string(self.root.join("monitor-pids"))
            .unwrap_or_default()
            .lines()
            .filter_map(|line| line.parse().ok())
            .collect()
    }

    fn monitor_calls(&self) -> Vec<String> {
        fs::read_to_string(&self.monitor_calls)
            .unwrap_or_default()
            .lines()
            .map(str::to_string)
            .collect()
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

fn stdout(output: &Output) -> String {
    String::from_utf8_lossy(&output.stdout).into_owned()
}

fn stderr(output: &Output) -> String {
    String::from_utf8_lossy(&output.stderr).into_owned()
}

struct Watcher(Child);

impl Drop for Watcher {
    fn drop(&mut self) {
        let pid = self.0.id() as i32;
        if matches!(self.0.try_wait(), Ok(None)) {
            unsafe { kill(-pid, 15) };
        }
        let _ = self.0.wait();
    }
}

struct WaylandSocket {
    path: PathBuf,
    listening: Arc<AtomicBool>,
    accept_loop: Option<thread::JoinHandle<()>>,
}

impl WaylandSocket {
    fn new(path: PathBuf) -> Self {
        let listener = UnixListener::bind(&path).unwrap();
        listener.set_nonblocking(true).unwrap();
        let listening = Arc::new(AtomicBool::new(true));
        let accept_loop = {
            let listening = listening.clone();
            thread::spawn(move || {
                while listening.load(Ordering::Acquire) {
                    match listener.accept() {
                        Ok((stream, _)) => drop(stream),
                        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                            thread::sleep(Duration::from_millis(10));
                        }
                        Err(error) => panic!("fixture Wayland socket failed: {error}"),
                    }
                }
            })
        };
        Self {
            path,
            listening,
            accept_loop: Some(accept_loop),
        }
    }
}

impl Drop for WaylandSocket {
    fn drop(&mut self) {
        self.listening.store(false, Ordering::Release);
        if let Some(accept_loop) = self.accept_loop.take() {
            accept_loop.join().unwrap();
        }
        let _ = fs::remove_file(&self.path);
    }
}

fn launch_watcher(scratch: &Scratch) -> (Watcher, PathBuf, WaylandSocket) {
    let socket = WaylandSocket::new(scratch.root.join("wayland-0"));
    let session_dir = scratch.root.join("session");
    fs::create_dir(&session_dir).unwrap();
    fs::create_dir_all(scratch.home.join(".config/gooarchy")).unwrap();
    let monitor_log = scratch.root.join("monitor-pids");
    let mut paths = vec![scratch.bin.clone()];
    paths.extend(std::env::split_paths(
        &std::env::var_os("PATH").unwrap_or_default(),
    ));
    let path = std::env::join_paths(paths).unwrap();
    let child = Command::new(BINARY)
        .args(["color-scheme", "watch"])
        .env("PATH", path)
        .env("HOME", &scratch.home)
        .env("XDG_CONFIG_HOME", scratch.home.join(".config"))
        .env("XDG_RUNTIME_DIR", &scratch.root)
        .env("WAYLAND_DISPLAY", "wayland-0")
        .env("SCOTTLAND_SESSION_DIR", &session_dir)
        .env("SCOTTLAND_HOOKS", &scratch.hooks)
        .env("CALLS", &scratch.calls)
        .env("HELPER_CALLS", &scratch.helper_calls)
        .env("MONITOR_PIDS", &monitor_log)
        .env("MONITOR_CALLS", &scratch.monitor_calls)
        .spawn()
        .unwrap();
    (
        Watcher(child),
        session_dir.join("wayland-0.color-scheme.pid"),
        socket,
    )
}

fn wait_for_monitors(scratch: &Scratch, watcher: &mut Watcher) -> Vec<i32> {
    let deadline = Instant::now() + Duration::from_secs(8);
    loop {
        let pids = scratch.monitor_pids();
        if pids.len() >= 3 {
            return pids;
        }
        if let Some(status) = watcher.0.try_wait().unwrap() {
            panic!("watcher exited before starting all monitors: {status}");
        }
        assert!(
            Instant::now() < deadline,
            "monitor subprocesses did not start"
        );
        thread::sleep(Duration::from_millis(20));
    }
}

fn wait_for_watcher_cleanup(watcher: &mut Watcher, pid_file: &Path, monitor_pids: &[i32]) {
    let deadline = Instant::now() + Duration::from_secs(8);
    let status = loop {
        if let Some(status) = watcher.0.try_wait().unwrap() {
            break status;
        }
        assert!(Instant::now() < deadline, "watcher did not stop");
        thread::sleep(Duration::from_millis(20));
    };
    assert!(status.success(), "watcher shutdown failed: {status}");
    assert!(!pid_file.exists(), "watcher left a stale pid record");
    for pid in monitor_pids {
        assert!(
            !Path::new(&format!("/proc/{pid}")).exists(),
            "monitor subprocess {pid} survived watcher shutdown"
        );
    }
}

#[test]
fn documented_help_dispatch_succeeds_without_reading_settings() {
    let scratch = Scratch::new();

    for flag in ["--help", "-h", "help"] {
        let output = scratch.run(&[flag]);
        assert!(output.status.success(), "{flag}: {}", stderr(&output));
        assert!(stdout(&output).contains("usage: scottland-display text-size"));
        assert!(stderr(&output).is_empty());
    }
    assert!(scratch.calls().is_empty());
}

#[test]
fn color_scheme_defaults_to_once_and_accepts_nested_cursor_size() {
    let once = Scratch::new();
    let output = once.run(&["color-scheme"]);
    assert!(output.status.success(), "{}", stderr(&output));
    assert_eq!(once.helper_calls(), ["once"]);
    assert!(once.calls().is_empty());

    let cursor = Scratch::new();
    let output = cursor.run(&["color-scheme", "cursor-size"]);
    assert!(output.status.success(), "{}", stderr(&output));
    assert_eq!(stdout(&output), "24\n");
    assert_eq!(cursor.helper_calls(), ["cursor-size"]);
    assert!(cursor.calls().is_empty());
}

#[test]
fn show_reads_the_key_and_reports_the_unset_default() {
    let scratch = Scratch::new();
    let output = scratch.run(&["text-size"]);
    assert!(output.status.success(), "{}", stderr(&output));
    assert_eq!(stdout(&output), "1.0\n");
    assert_eq!(scratch.calls(), [format!("get {SCHEMA} {KEY}")]);

    fs::write(&scratch.state, "1.5\n").unwrap();
    let output = scratch.run(&["text-size"]);
    assert!(output.status.success(), "{}", stderr(&output));
    assert_eq!(stdout(&output), "1.5\n");
    assert_eq!(scratch.calls().len(), 2);
}

#[test]
fn set_writes_only_the_gsettings_key_and_preserves_user_config() {
    let scratch = Scratch::new();
    let config = scratch.ghostty_config();
    fs::create_dir_all(config.parent().unwrap()).unwrap();
    fs::write(&config, "font-size = 13\n").unwrap();

    let output = scratch.run(&["text-size", "set", "1.25"]);
    assert!(output.status.success(), "{}", stderr(&output));
    assert_eq!(fs::read_to_string(&scratch.state).unwrap(), "1.25\n");
    assert_eq!(scratch.calls(), [format!("set {SCHEMA} {KEY} 1.25")]);
    assert_eq!(fs::read_to_string(config).unwrap(), "font-size = 13\n");
}

#[test]
fn out_of_range_and_non_numeric_values_are_rejected_without_writing() {
    let scratch = Scratch::new();
    fs::write(&scratch.state, "1.75\n").unwrap();

    for factor in ["4", "0.49", "NaN", "not-a-number"] {
        let output = scratch.run(&["text-size", "set", factor]);
        assert!(!output.status.success(), "accepted {factor}");
        assert!(stderr(&output).contains("0.5"), "{}", stderr(&output));
        assert!(stderr(&output).contains("3.0"), "{}", stderr(&output));
        assert_eq!(fs::read_to_string(&scratch.state).unwrap(), "1.75\n");
    }
    assert!(scratch.calls().is_empty());
}

#[test]
fn reset_unsets_the_key_so_show_returns_the_default() {
    let scratch = Scratch::new();
    fs::write(&scratch.state, "1.75\n").unwrap();

    let output = scratch.run(&["text-size", "reset"]);
    assert!(output.status.success(), "{}", stderr(&output));
    assert!(!scratch.state.exists());
    assert_eq!(scratch.calls(), [format!("reset {SCHEMA} {KEY}")]);

    let output = scratch.run(&["text-size"]);
    assert!(output.status.success(), "{}", stderr(&output));
    assert_eq!(stdout(&output), "1.0\n");
    assert_eq!(scratch.calls().len(), 2);
}

#[test]
fn backend_failure_is_reported_instead_of_claiming_success() {
    let scratch = Scratch::new();
    let output = scratch.run_with_failure(&["text-size", "set", "1.25"], true);
    assert!(!output.status.success());
    assert!(stderr(&output).contains("gsettings set failed"));
    assert!(stderr(&output).contains("fixture gsettings failure"));
    assert_eq!(scratch.calls(), [format!("set {SCHEMA} {KEY} 1.25")]);
    assert!(!scratch.state.exists());
}

#[test]
fn unsupported_arguments_are_rejected_before_calling_gsettings() {
    let scratch = Scratch::new();
    let output = scratch.run(&["text-size", "set", "1.25", "extra"]);
    assert!(!output.status.success());
    assert!(stderr(&output).contains("usage: scottland-display text-size"));
    assert!(scratch.calls().is_empty());
}

#[test]
fn terminal_theme_push_calls_only_the_private_helper_action_once() {
    let scratch = Scratch::new();
    let output = scratch.run(&["terminal-theme", "push"]);
    assert!(output.status.success(), "{}", stderr(&output));
    assert!(scratch.calls().is_empty());
    assert_eq!(scratch.helper_calls(), ["terminal-theme-push"]);
}

#[test]
fn terminal_theme_push_propagates_helper_failure_and_stderr() {
    let scratch = Scratch::new();
    let output = scratch.run_with_helper_failure(&["terminal-theme", "push"], true);
    assert!(!output.status.success());
    assert!(
        stderr(&output).contains("terminal-theme-push failed"),
        "unexpected error: {}",
        stderr(&output)
    );
    assert!(
        stderr(&output).contains("fixture helper failure"),
        "unexpected error: {}",
        stderr(&output)
    );
    assert_eq!(scratch.helper_calls(), ["terminal-theme-push"]);
    assert!(scratch.calls().is_empty());
}

#[test]
fn terminal_theme_push_rejects_malformed_arguments_before_calling_helper() {
    let scratch = Scratch::new();

    for args in [
        &["terminal-theme"][..],
        &["terminal-theme", "apply"][..],
        &["terminal-theme", "push", "extra"][..],
    ] {
        let output = scratch.run(args);
        assert!(!output.status.success(), "accepted {args:?}");
        assert!(stderr(&output).contains("usage: scottland-display"));
    }

    assert!(scratch.helper_calls().is_empty());
    assert!(scratch.calls().is_empty());
}

#[test]
fn cursor_size_command_returns_the_helper_value_without_settings_side_effects() {
    let scratch = Scratch::new();
    let output = scratch.run(&["cursor-size"]);
    assert!(output.status.success(), "{}", stderr(&output));
    assert_eq!(stdout(&output), "24\n");
    assert_eq!(scratch.helper_calls(), ["cursor-size"]);
    assert!(scratch.calls().is_empty());
}

#[test]
fn sourced_session_environment_exports_the_cursor_size_command_result() {
    let scratch = Scratch::new();
    let script =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../session-env.d/10-cursor-size.sh");
    assert!(script.is_file());
    let mut paths = vec![scratch.bin.clone()];
    paths.extend(std::env::split_paths(
        &std::env::var_os("PATH").unwrap_or_default(),
    ));
    let path = std::env::join_paths(paths).unwrap();

    let output = Command::new("sh")
        .args([
            "-c",
            ". \"$CURSOR_SCRIPT\"; printf '%s\\n' \"${XCURSOR_SIZE-}\"",
        ])
        .env("CURSOR_SCRIPT", &script)
        .env("PATH", path)
        .env("SCOTTLAND_HOOKS", &scratch.hooks)
        .env("HELPER_CALLS", &scratch.helper_calls)
        .output()
        .unwrap();
    assert!(output.status.success(), "{}", stderr(&output));
    assert_eq!(stdout(&output), "24\n");
    assert_eq!(scratch.helper_calls(), ["cursor-size"]);
}

#[test]
fn watcher_stops_and_reaps_monitors_when_its_wayland_session_ends() {
    let scratch = Scratch::new();
    let (mut watcher, pid_file, socket) = launch_watcher(&scratch);
    let monitor_pids = wait_for_monitors(&scratch, &mut watcher);
    assert!(pid_file.is_file());
    assert!(
        scratch
            .monitor_calls()
            .iter()
            .any(|call| call.starts_with("gdbus monitor --session"))
    );
    assert!(
        !scratch
            .calls()
            .iter()
            .any(|call| call == "monitor org.gnome.desktop.interface color-scheme")
    );

    fs::remove_file(&socket.path).unwrap();
    wait_for_watcher_cleanup(&mut watcher, &pid_file, &monitor_pids);
}

#[test]
fn watcher_stops_monitors_on_headless_process_group_termination() {
    let scratch = Scratch::new();
    let (mut watcher, pid_file, _socket) = launch_watcher(&scratch);
    let monitor_pids = wait_for_monitors(&scratch, &mut watcher);
    assert!(pid_file.is_file());
    assert_eq!(unsafe { kill(-(watcher.0.id() as i32), 15) }, 0);
    wait_for_watcher_cleanup(&mut watcher, &pid_file, &monitor_pids);
}
