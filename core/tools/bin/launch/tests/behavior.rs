use serde_json::{Value, json};
use std::fs;
use std::io::{Read, Write};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::UnixListener;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Output, Stdio};
use std::thread;
use std::time::{Duration, Instant};

struct Scratch(PathBuf);

impl Scratch {
    fn new(name: &str) -> Self {
        let path =
            std::env::temp_dir().join(format!("scottland-launch-{}-{name}", std::process::id()));
        let _ = fs::remove_dir_all(&path);
        fs::create_dir_all(&path).unwrap();
        Self(path)
    }

    fn path(&self) -> &Path {
        &self.0
    }

    fn executable(&self, name: &str, contents: &str) -> PathBuf {
        let path = self.0.join(name);
        fs::write(&path, contents).unwrap();
        let mut permissions = fs::metadata(&path).unwrap().permissions();
        permissions.set_mode(0o755);
        fs::set_permissions(&path, permissions).unwrap();
        path
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

struct ChildGuard(Child);

impl Drop for ChildGuard {
    fn drop(&mut self) {
        if self.0.try_wait().ok().flatten().is_none() {
            let _ = self.0.kill();
            let _ = self.0.wait();
        }
    }
}

fn binary() -> &'static str {
    env!("CARGO_BIN_EXE_scottland-launch")
}

fn decode_argv(path: &Path) -> Vec<String> {
    fs::read(path)
        .unwrap()
        .split(|byte| *byte == 0)
        .filter(|part| !part.is_empty())
        .map(|part| String::from_utf8(part.to_vec()).unwrap())
        .collect()
}

fn terminal_probe(scratch: &Scratch) -> std::ffi::OsString {
    scratch.executable(
        "xdg-terminal-exec",
        "#!/bin/sh\nprintf '%s\\0' \"$@\" > \"$LAUNCH_ARGV_CAPTURE\"\n",
    );
    scratch.path().as_os_str().to_owned()
}

fn launch_stack(scratch: &Scratch) -> std::ffi::OsString {
    let path = terminal_probe(scratch);
    scratch.executable(
        "setsid",
        "#!/bin/sh\nprintf '%s\\0' \"$@\" > \"$LAUNCH_ARGV_CAPTURE.setsid\"\n[ \"$1\" = -- ] && shift\nexec \"$@\"\n",
    );
    path
}

fn run(path: &std::ffi::OsStr, capture: &Path, args: &[&str]) -> Output {
    run_with(path, capture, args, Vec::new())
}

fn run_with(
    path: &std::ffi::OsStr,
    capture: &Path,
    args: &[&str],
    extra_env: Vec<(String, String)>,
) -> Output {
    let root = capture.parent().unwrap();
    let home = extra_env
        .iter()
        .find(|(name, _)| name == "HOME")
        .map(|(_, value)| PathBuf::from(value))
        .unwrap_or_else(|| root.join("home"));
    let runtime = root.join("runtime");
    let config = home.join(".config");
    let data = home.join(".local/share");
    let state = home.join(".local/state");
    let cache = home.join(".cache");
    for directory in [&home, &runtime, &config, &data, &state, &cache] {
        fs::create_dir_all(directory).unwrap();
    }

    Command::new(binary())
        .args(args)
        .env_clear()
        .env("PATH", path)
        .env("HOME", &home)
        .env("XDG_CONFIG_HOME", config)
        .env("XDG_DATA_HOME", data)
        .env("XDG_STATE_HOME", state)
        .env("XDG_CACHE_HOME", cache)
        .env("XDG_RUNTIME_DIR", runtime)
        .env("LAUNCH_ARGV_CAPTURE", capture)
        .envs(extra_env)
        .output()
        .unwrap()
}

struct FakeWayfire {
    socket: PathBuf,
    runtime: PathBuf,
    worker: Option<thread::JoinHandle<Vec<Value>>>,
}

impl FakeWayfire {
    fn new(scratch: &Scratch, state: Value, api_requests: usize) -> Self {
        let runtime = scratch.path().join("runtime");
        let scottland_runtime = runtime.join("scottland");
        fs::create_dir_all(&scottland_runtime).unwrap();
        let socket = runtime.join("wayfire.sock");
        let listener = UnixListener::bind(&socket).unwrap();
        listener.set_nonblocking(true).unwrap();
        let worker = thread::spawn(move || {
            let mut requests = Vec::new();
            let deadline = Instant::now() + Duration::from_secs(5);
            for index in 0..=api_requests {
                let (mut stream, _) = loop {
                    match listener.accept() {
                        Ok(connection) => break connection,
                        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                            assert!(
                                Instant::now() < deadline,
                                "timed out waiting for Wayfire request"
                            );
                            thread::yield_now();
                        }
                        Err(error) => panic!("Wayfire fixture accept failed: {error}"),
                    }
                };
                if index == 0 {
                    // Session selection probes the socket without sending an IPC frame.
                    continue;
                }
                stream
                    .set_read_timeout(Some(Duration::from_secs(2)))
                    .unwrap();
                let request = read_frame(&mut stream);
                let reply = match request["method"].as_str().unwrap() {
                    "scottland/desktop-model" => state.clone(),
                    "window-rules/get-focused-view" => state
                        .get("windows")
                        .and_then(Value::as_array)
                        .and_then(|windows| {
                            windows.iter().find(|window| {
                                window.get("focused").and_then(Value::as_bool) == Some(true)
                            })
                        })
                        .and_then(|window| window.get("pid"))
                        .map(|pid| json!({"info": {"pid": pid}}))
                        .unwrap_or_else(|| json!({"info": null})),
                    "scottland/present" => json!({"ok": true}),
                    method => panic!("unexpected Wayfire method {method}"),
                };
                write_frame(&mut stream, &reply);
                requests.push(request);
            }
            requests
        });
        Self {
            socket,
            runtime,
            worker: Some(worker),
        }
    }

    fn env_file(
        &self,
        path: &std::ffi::OsStr,
        capture: &Path,
        home: &Path,
        data_home: Option<&Path>,
    ) {
        let mut entries = vec![
            format!("WAYFIRE_SOCKET={}", self.socket.display()),
            format!("XDG_RUNTIME_DIR={}", self.runtime.display()),
            format!("HOME={}", home.display()),
            format!("PATH={}", path.to_string_lossy()),
            format!("LAUNCH_ARGV_CAPTURE={}", capture.display()),
        ];
        if let Some(data_home) = data_home {
            entries.push(format!("XDG_DATA_HOME={}", data_home.display()));
        }
        let mut bytes = Vec::new();
        for entry in entries {
            bytes.extend_from_slice(entry.as_bytes());
            bytes.push(0);
        }
        fs::write(self.runtime.join("scottland/wayland-test.env"), bytes).unwrap();
    }

    fn join(mut self) -> Vec<Value> {
        self.worker.take().unwrap().join().unwrap()
    }
}

fn read_frame(stream: &mut std::os::unix::net::UnixStream) -> Value {
    let mut header = [0; 4];
    stream.read_exact(&mut header).unwrap();
    let mut body = vec![0; u32::from_le_bytes(header) as usize];
    stream.read_exact(&mut body).unwrap();
    serde_json::from_slice(&body).unwrap()
}

fn write_frame(stream: &mut std::os::unix::net::UnixStream, value: &Value) {
    let body = serde_json::to_vec(value).unwrap();
    stream
        .write_all(&(body.len() as u32).to_le_bytes())
        .unwrap();
    stream.write_all(&body).unwrap();
}

fn session_env(runtime: &Path) -> Vec<(String, String)> {
    vec![
        ("XDG_RUNTIME_DIR".into(), runtime.display().to_string()),
        (
            "HOME".into(),
            runtime.parent().unwrap().display().to_string(),
        ),
    ]
}

fn selected_desktop(home: &Path, id: &str, body: &str) -> PathBuf {
    let path = home.join(".local/share/applications").join(id);
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(&path, format!("[Desktop Entry]\nType=Application\n{body}")).unwrap();
    path
}

fn fake_xdg_settings(scratch: &Scratch, id: &str) {
    scratch.executable(
        "xdg-settings",
        &format!("#!/bin/sh\nprintf '%s\\0' \"$@\" > \"$LAUNCH_ARGV_CAPTURE.xdg-settings\"\nprintf '%s\\n' '{id}'\n"),
    );
}

fn fake_xdg_mime(scratch: &Scratch, id: &str) {
    scratch.executable(
        "xdg-mime",
        &format!("#!/bin/sh\nprintf '%s\\0' \"$@\" > \"$LAUNCH_ARGV_CAPTURE.xdg-mime\"\nprintf '%s\\n' '{id}'\n"),
    );
}

fn wait_for(path: &Path) {
    let deadline = Instant::now() + Duration::from_secs(5);
    while !path.exists() {
        assert!(
            Instant::now() < deadline,
            "timed out waiting for {}",
            path.display()
        );
        thread::yield_now();
    }
}

fn recording_program(scratch: &Scratch, name: &str, suffix: &str) -> PathBuf {
    scratch.executable(
        name,
        &format!("#!/bin/sh\ntmp=\"$LAUNCH_ARGV_CAPTURE.{suffix}.tmp.$$\"\nprintf '%s\\0' \"$@\" > \"$tmp\" && /bin/mv -- \"$tmp\" \"$LAUNCH_ARGV_CAPTURE.{suffix}\"\n"),
    )
}

#[test]
fn browser_uses_xdg_entry_exec_field_codes_and_presents_in_selected_session() {
    let scratch = Scratch::new("browser-xdg-focus");
    let capture = scratch.path().join("argv");
    let path = launch_stack(&scratch);
    let home = scratch.path().join("home");
    let browser = recording_program(&scratch, "selected-browser", "browser");
    fake_xdg_settings(&scratch, "selected.desktop");
    selected_desktop(
        &home,
        "selected.desktop",
        &format!(
            "Name=Chosen Browser\nIcon=browser-icon\nExec={} --label %c --literal %% %i %U\n",
            browser.display()
        ),
    );
    let state = json!({"windows": [{"id": 42, "pid": 99, "app_id": "selected", "title": "Browser", "focused": false}]});
    let wayfire = FakeWayfire::new(&scratch, state, 2);
    wayfire.env_file(&path, &capture, &home, None);

    let output = run_with(
        &path,
        &capture,
        &["browser", "https://example.test/a?x=y"],
        session_env(&wayfire.runtime),
    );
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    wait_for(&capture.with_extension("browser"));
    assert_eq!(
        decode_argv(&capture.with_extension("browser")),
        [
            "--label",
            "Chosen Browser",
            "--literal",
            "%",
            "--icon",
            "browser-icon",
            "https://example.test/a?x=y"
        ]
    );
    assert_eq!(
        decode_argv(&capture.with_extension("xdg-settings")),
        ["get", "default-web-browser"]
    );
    let requests = wayfire.join();
    assert_eq!(requests[0]["method"], "scottland/desktop-model");
    assert_eq!(requests[1]["method"], "scottland/present");
    assert_eq!(requests[1]["data"]["window"], 42);
}

#[test]
fn browser_fails_when_xdg_default_is_missing() {
    let scratch = Scratch::new("browser-missing-default");
    let capture = scratch.path().join("argv");
    let path = launch_stack(&scratch);
    scratch.executable("xdg-settings", "#!/bin/sh\nexit 0\n");
    recording_program(&scratch, "unselected-browser", "browser");
    let output = run(&path, &capture, &["browser", "https://example.test"]);
    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("invalid default browser desktop ID"));
    assert!(!capture.with_extension("browser").exists());
}

#[test]
fn browser_resolves_the_user_data_directory_before_lower_priority_data_dirs() {
    let scratch = Scratch::new("browser-data-precedence");
    let capture = scratch.path().join("argv");
    let path = launch_stack(&scratch);
    let home = scratch.path().join("home");
    let preferred = scratch.path().join("xdg-data-home");
    let lower = scratch.path().join("xdg-data-dir");
    fake_xdg_settings(&scratch, "choice.desktop");
    let home_browser = recording_program(&scratch, "home-browser", "home-browser");
    let preferred_browser = recording_program(&scratch, "preferred-browser", "preferred-browser");
    let lower_browser = recording_program(&scratch, "lower-browser", "lower-browser");
    selected_desktop(
        &home,
        "choice.desktop",
        &format!("Name=Home\nExec={} %U\n", home_browser.display()),
    );
    for (root, name, executable) in [
        (&preferred, "Preferred", &preferred_browser),
        (&lower, "Lower", &lower_browser),
    ] {
        let apps = root.join("applications");
        fs::create_dir_all(&apps).unwrap();
        fs::write(
            apps.join("choice.desktop"),
            format!(
                "[Desktop Entry]\nType = Application\nName = {name}\nExec = {} %U\n",
                executable.display()
            ),
        )
        .unwrap();
    }
    let output = run_with(
        &path,
        &capture,
        &["browser"],
        vec![
            ("HOME".into(), home.display().to_string()),
            ("XDG_DATA_HOME".into(), preferred.display().to_string()),
            ("XDG_DATA_DIRS".into(), lower.display().to_string()),
        ],
    );
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    wait_for(&capture.with_extension("preferred-browser"));
    assert!(!capture.with_extension("home-browser").exists());
    assert!(!capture.with_extension("lower-browser").exists());
}

#[test]
fn browser_refuses_unknown_private_mode_before_launch() {
    let scratch = Scratch::new("browser-private-unsupported");
    let capture = scratch.path().join("argv");
    let path = launch_stack(&scratch);
    let home = scratch.path().join("home");
    fake_xdg_settings(&scratch, "custom.desktop");
    let browser = recording_program(&scratch, "custom-browser", "browser");
    selected_desktop(
        &home,
        "custom.desktop",
        &format!("Name=Custom\nExec={} %U\n", browser.display()),
    );
    let output = run_with(
        &path,
        &capture,
        &["browser", "--private", "https://example.test"],
        vec![("HOME".into(), home.display().to_string())],
    );
    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("private mode is unsupported"));
    assert!(!capture.with_extension("browser").exists());
}

#[test]
fn browser_rejects_invalid_exec_field_codes_without_running_the_entry() {
    let scratch = Scratch::new("browser-invalid-field-code");
    let capture = scratch.path().join("argv");
    let path = launch_stack(&scratch);
    let home = scratch.path().join("home");
    fake_xdg_settings(&scratch, "invalid.desktop");
    let browser = recording_program(&scratch, "invalid-browser", "browser");
    selected_desktop(
        &home,
        "invalid.desktop",
        &format!("Name=Invalid\nExec={} %Q\n", browser.display()),
    );
    let output = run_with(
        &path,
        &capture,
        &["browser", "https://example.test"],
        vec![("HOME".into(), home.display().to_string())],
    );
    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("unsupported field code %Q"));
    assert!(!capture.with_extension("browser").exists());
}

#[test]
fn browser_private_mode_uses_only_the_selected_known_invocation() {
    let scratch = Scratch::new("browser-private-firefox");
    let capture = scratch.path().join("argv");
    let path = launch_stack(&scratch);
    let home = scratch.path().join("home");
    fake_xdg_settings(&scratch, "firefox.desktop");
    let browser = recording_program(&scratch, "firefox", "firefox");
    selected_desktop(
        &home,
        "firefox.desktop",
        &format!("Name=Firefox\nExec={} --new-window %U\n", browser.display()),
    );
    let output = run_with(
        &path,
        &capture,
        &["browser", "--private", "https://example.test"],
        vec![("HOME".into(), home.display().to_string())],
    );
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    wait_for(&capture.with_extension("firefox"));
    assert_eq!(
        decode_argv(&capture.with_extension("firefox")),
        ["--private-window", "--new-window", "https://example.test"]
    );
}

#[test]
fn browser_dbus_entry_uses_neutral_desktop_activation() {
    let scratch = Scratch::new("browser-dbus");
    let capture = scratch.path().join("argv");
    let path = launch_stack(&scratch);
    let home = scratch.path().join("home");
    fake_xdg_settings(&scratch, "browser.desktop");
    selected_desktop(
        &home,
        "browser.desktop",
        "Name=Browser\nDBusActivatable=true\n",
    );
    scratch.executable(
        "gio",
        "#!/bin/sh\ntmp=\"$LAUNCH_ARGV_CAPTURE.gio.tmp.$$\"\nprintf '%s\\0' \"$@\" > \"$tmp\" && /bin/mv -- \"$tmp\" \"$LAUNCH_ARGV_CAPTURE.gio\"\n",
    );
    let output = run_with(
        &path,
        &capture,
        &["browser", "https://example.test/item"],
        vec![("HOME".into(), home.display().to_string())],
    );
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    wait_for(&capture.with_extension("gio"));
    assert_eq!(
        decode_argv(&capture.with_extension("gio")),
        [
            "launch",
            home.join(".local/share/applications/browser.desktop")
                .to_str()
                .unwrap(),
            "https://example.test/item"
        ]
    );
}

#[test]
fn browser_reports_a_missing_selected_desktop_executable() {
    let scratch = Scratch::new("browser-missing-executable");
    let capture = scratch.path().join("argv");
    let path = launch_stack(&scratch);
    let home = scratch.path().join("home");
    fake_xdg_settings(&scratch, "missing.desktop");
    let missing = scratch.path().join("missing-browser");
    selected_desktop(
        &home,
        "missing.desktop",
        &format!("Name=Missing\nExec={} %U\n", missing.display()),
    );

    let output = run_with(
        &path,
        &capture,
        &["browser", "https://example.test"],
        vec![("HOME".into(), home.display().to_string())],
    );

    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("cannot start"), "{stderr}");
    assert!(stderr.contains(missing.to_str().unwrap()), "{stderr}");
}

#[test]
fn browser_reports_an_unexecutable_selected_desktop_target() {
    let scratch = Scratch::new("browser-unexecutable-target");
    let capture = scratch.path().join("argv");
    let path = launch_stack(&scratch);
    let home = scratch.path().join("home");
    fake_xdg_settings(&scratch, "unexecutable.desktop");
    let target = scratch.path().join("unexecutable-browser");
    fs::write(&target, "#!/bin/sh\nexit 0\n").unwrap();
    let mut permissions = fs::metadata(&target).unwrap().permissions();
    permissions.set_mode(0o644);
    fs::set_permissions(&target, permissions).unwrap();
    selected_desktop(
        &home,
        "unexecutable.desktop",
        &format!("Name=Unexecutable\nExec={} %U\n", target.display()),
    );

    let output = run_with(
        &path,
        &capture,
        &["browser", "https://example.test"],
        vec![("HOME".into(), home.display().to_string())],
    );

    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("cannot start"), "{stderr}");
    assert!(stderr.contains(target.to_str().unwrap()), "{stderr}");
}

#[test]
fn browser_reports_a_failed_neutral_desktop_activation() {
    let scratch = Scratch::new("browser-failed-activation");
    let capture = scratch.path().join("argv");
    let path = launch_stack(&scratch);
    let home = scratch.path().join("home");
    fake_xdg_settings(&scratch, "browser.desktop");
    selected_desktop(
        &home,
        "browser.desktop",
        "Name=Browser\nDBusActivatable=true\n",
    );
    scratch.executable(
        "gio",
        "#!/bin/sh\nprintf '%s\\0' \"$@\" > \"$LAUNCH_ARGV_CAPTURE.gio.tmp.$$\" && /bin/mv -- \"$LAUNCH_ARGV_CAPTURE.gio.tmp.$$\" \"$LAUNCH_ARGV_CAPTURE.gio\"\nexit 9\n",
    );

    let output = run_with(
        &path,
        &capture,
        &["browser", "https://example.test"],
        vec![("HOME".into(), home.display().to_string())],
    );

    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("desktop activation failed for browser.desktop"),
        "{stderr}"
    );
    assert_eq!(
        decode_argv(&capture.with_extension("gio")),
        [
            "launch",
            home.join(".local/share/applications/browser.desktop")
                .to_str()
                .unwrap(),
            "https://example.test"
        ]
    );
}

#[test]
fn editor_prefers_visual_and_keeps_known_tui_in_the_selected_terminal() {
    let scratch = Scratch::new("editor-visual");
    let capture = scratch.path().join("argv");
    let path = launch_stack(&scratch);
    recording_program(&scratch, "nvim", "nvim");
    recording_program(&scratch, "other-editor", "other");
    let output = run_with(
        &path,
        &capture,
        &["editor", "file with spaces.txt"],
        vec![
            ("VISUAL".into(), "nvim --clean".into()),
            ("EDITOR".into(), "other-editor".into()),
        ],
    );
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        decode_argv(&capture),
        [
            "--app-id=org.scottland.nvim",
            "--title=Scottland nvim",
            "-e",
            "nvim",
            "--clean",
            "--",
            "file with spaces.txt"
        ]
    );
}

#[test]
fn invalid_visual_does_not_fall_through_to_editor_or_a_default() {
    let scratch = Scratch::new("editor-invalid-visual");
    let capture = scratch.path().join("argv");
    let path = launch_stack(&scratch);
    recording_program(&scratch, "valid-editor", "valid");
    let output = run_with(
        &path,
        &capture,
        &["editor", "file.txt"],
        vec![
            ("VISUAL".into(), "missing-editor".into()),
            ("EDITOR".into(), "valid-editor".into()),
        ],
    );
    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("configured editor executable"));
    assert!(!capture.with_extension("valid").exists());
}

#[test]
fn text_plain_terminal_editor_uses_xdg_mime_and_preserves_each_path() {
    let scratch = Scratch::new("editor-xdg-terminal");
    let capture = scratch.path().join("argv");
    let path = launch_stack(&scratch);
    let home = scratch.path().join("home");
    let editor = recording_program(&scratch, "cli-editor", "editor");
    fake_xdg_mime(&scratch, "cli-editor.desktop");
    selected_desktop(
        &home,
        "cli-editor.desktop",
        &format!(
            "Name=CLI Editor\nTerminal=true\nExec={} %F\n",
            editor.display()
        ),
    );
    let output = run_with(
        &path,
        &capture,
        &["editor", "one file.txt", "two.txt"],
        vec![("HOME".into(), home.display().to_string())],
    );
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        decode_argv(&capture),
        [
            "--app-id=org.scottland.text_editor",
            "--title=CLI Editor",
            "-e",
            editor.to_str().unwrap(),
            "one file.txt",
            "two.txt"
        ]
    );
    assert_eq!(
        decode_argv(&capture.with_extension("xdg-mime")),
        ["query", "default", "text/plain"]
    );
}

#[test]
fn text_plain_nonterminal_editor_launches_its_desktop_entry_directly() {
    let scratch = Scratch::new("editor-xdg-direct");
    let capture = scratch.path().join("argv");
    let path = launch_stack(&scratch);
    let home = scratch.path().join("home");
    let editor = recording_program(&scratch, "desktop-editor", "desktop-editor");
    fake_xdg_mime(&scratch, "desktop-editor.desktop");
    selected_desktop(
        &home,
        "desktop-editor.desktop",
        &format!(
            "Name=Desktop Editor\nTerminal=false\nExec={} %f\n",
            editor.display()
        ),
    );
    let output = run_with(
        &path,
        &capture,
        &["editor", "a path with spaces.txt"],
        vec![("HOME".into(), home.display().to_string())],
    );
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    wait_for(&capture.with_extension("desktop-editor"));
    assert_eq!(
        decode_argv(&capture.with_extension("desktop-editor")),
        ["a path with spaces.txt"]
    );
    assert!(!capture.exists());
}

#[test]
fn tui_keeps_explicit_app_id_and_argument_vector() {
    let scratch = Scratch::new("tui-argv");
    let capture = scratch.path().join("argv");
    let path = launch_stack(&scratch);
    let output = run(
        &path,
        &capture,
        &[
            "--tui",
            "--app-id=org.example.editor",
            "editor-wrapper",
            "file with spaces.txt",
        ],
    );
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        decode_argv(&capture),
        [
            "--app-id=org.example.editor",
            "--title=Scottland editor-wrapper",
            "-e",
            "editor-wrapper",
            "file with spaces.txt"
        ]
    );
}

#[test]
fn floating_presentation_passes_command_and_arguments_without_joining_them() {
    let scratch = Scratch::new("floating-argv");
    let capture = scratch.path().join("argv");
    let path = launch_stack(&scratch);
    let output = run(
        &path,
        &capture,
        &[
            "floating-terminal-with-presentation",
            "task",
            "argument with spaces",
            "$(literal)",
        ],
    );
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let args = decode_argv(&capture);
    assert_eq!(
        &args[..4],
        [
            "--app-id=org.scottland.floating_terminal",
            "--title=Scottland",
            "-e",
            "/bin/sh"
        ]
    );
    assert_eq!(
        &args[4..6],
        [
            "-c",
            "printf 'Scottland\\n'; \"$@\"; status=$?; if [ \"$status\" -ne 130 ]; then printf '\\nCommand exited with status %s. Press Enter to close.' \"$status\"; IFS= read -r _; fi; exit \"$status\""
        ]
    );
    assert_eq!(
        &args[6..9],
        ["scottland-presentation", "task", "argument with spaces"]
    );
    assert_eq!(args[9], "$(literal)");
}

#[test]
fn floating_presentation_shows_status_prompt_except_after_interrupt_status() {
    let scratch = Scratch::new("floating-status");
    let capture = scratch.path().join("argv");
    let attack_path = scratch.path().join("unexpected-shell-evaluation");
    let path = scratch.path().as_os_str().to_owned();
    scratch.executable(
        "xdg-terminal-exec",
        "#!/bin/sh\nwhile [ \"$1\" != -e ]; do shift; done\nshift\nexec \"$@\"\n",
    );
    scratch.executable("interrupt-command", "#!/bin/sh\nexit 130\n");
    let interrupted = run(
        &path,
        &capture,
        &["floating-terminal-with-presentation", "interrupt-command"],
    );
    assert_eq!(interrupted.status.code(), Some(130));
    assert_eq!(String::from_utf8_lossy(&interrupted.stdout), "Scottland\n");

    let record = scratch.executable(
        "record-status",
        "#!/bin/sh\ntmp=\"$LAUNCH_ARGV_CAPTURE.record.tmp.$$\"\nprintf '%s\\0' \"$@\" > \"$tmp\" && /bin/mv -- \"$tmp\" \"$LAUNCH_ARGV_CAPTURE.record\"\nexit 7\n",
    );
    let attack = format!("$(touch {})", attack_path.display());
    let failed = run_with(
        &path,
        &capture,
        &[
            "floating-terminal-with-presentation",
            "record-status",
            &attack,
        ],
        Vec::new(),
    );
    assert_eq!(failed.status.code(), Some(7));
    assert!(
        String::from_utf8_lossy(&failed.stdout)
            .contains("Command exited with status 7. Press Enter to close.")
    );
    assert_eq!(decode_argv(&capture.with_extension("record")), [attack]);
    assert!(!attack_path.exists());
    assert_eq!(record.file_name().unwrap(), "record-status");
}

#[test]
fn focus_without_a_selected_scottland_session_fails_instead_of_claiming_success() {
    let scratch = Scratch::new("focus-no-session");
    let capture = scratch.path().join("argv");
    let path = launch_stack(&scratch);
    recording_program(&scratch, "fallback", "fallback");
    let output = run_with(
        &path,
        &capture,
        &["--focus", "browser", "fallback"],
        Vec::new(),
    );
    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("requires a running Scottland session")
    );
    assert!(!capture.with_extension("fallback").exists());
}

#[test]
fn files_and_interactive_terminal_share_home_fallback_without_a_session() {
    let scratch = Scratch::new("cwd-home-fallback");
    let capture = scratch.path().join("argv");
    let path = launch_stack(&scratch);
    scratch.executable(
        "xdg-open",
        "#!/bin/sh\ntmp=\"$LAUNCH_ARGV_CAPTURE.open.tmp.$$\"\nprintf '%s\\0' \"$@\" > \"$tmp\" && /bin/mv -- \"$tmp\" \"$LAUNCH_ARGV_CAPTURE.open\"\n",
    );
    let home = scratch.path().join("home");
    fs::create_dir_all(&home).unwrap();
    let home_text = home.to_str().unwrap();
    let files = run_with(
        &path,
        &capture,
        &["--cwd", "files"],
        vec![("HOME".into(), home_text.into())],
    );
    assert!(
        files.status.success(),
        "{}",
        String::from_utf8_lossy(&files.stderr)
    );
    wait_for(&capture.with_extension("open"));
    assert_eq!(decode_argv(&capture.with_extension("open")), [home_text]);

    let terminal = run_with(
        &path,
        &capture,
        &["--cwd", "terminal"],
        vec![("HOME".into(), home_text.into())],
    );
    assert!(
        terminal.status.success(),
        "{}",
        String::from_utf8_lossy(&terminal.stderr)
    );
    assert_eq!(
        decode_argv(&capture),
        [
            "--app-id=org.scottland.terminal",
            "--title=Scottland Terminal",
            &format!("--dir={home_text}")
        ]
    );
}

#[test]
fn terminal_cwd_rejects_invalid_explicit_pid_syntax() {
    let scratch = Scratch::new("cwd-invalid-pid");
    let capture = scratch.path().join("argv");
    let path = launch_stack(&scratch);
    let output = run(&path, &capture, &["--cwd", "terminal", "--pid=not-a-pid"]);
    assert_eq!(output.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&output.stderr).contains("--pid must be a process ID"));
    assert!(!capture.exists());
}

#[test]
fn terminal_cwd_uses_explicit_same_user_shell_pid() {
    let scratch = Scratch::new("cwd-explicit-shell");
    let capture = scratch.path().join("argv");
    let path = launch_stack(&scratch);
    let shell_cwd = scratch.path().join("explicit shell directory");
    fs::create_dir_all(&shell_cwd).unwrap();
    let mut shell = ChildGuard(
        Command::new("/bin/sh")
            .arg("-c")
            .arg("read -r _")
            .current_dir(&shell_cwd)
            .stdin(Stdio::piped())
            .spawn()
            .unwrap(),
    );
    let pid = shell.0.id().to_string();
    let pid_arg = format!("--pid={pid}");
    let output = run(
        &path,
        &capture,
        &["--cwd", "terminal", &pid_arg, "shell-command", "two words"],
    );
    shell.0.stdin.take().unwrap().write_all(b"exit\n").unwrap();
    shell.0.wait().unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        decode_argv(&capture),
        [
            "--app-id=org.scottland.terminal",
            "--title=Scottland Terminal",
            &format!("--dir={}", shell_cwd.display()),
            "-e",
            "shell-command",
            "two words"
        ]
    );
}

#[test]
fn files_use_the_focused_same_user_shell_directory() {
    let scratch = Scratch::new("cwd-focused-shell");
    let capture = scratch.path().join("argv");
    let path = launch_stack(&scratch);
    scratch.executable(
        "xdg-open",
        "#!/bin/sh\ntmp=\"$LAUNCH_ARGV_CAPTURE.open.tmp.$$\"\nprintf '%s\\0' \"$@\" > \"$tmp\" && /bin/mv -- \"$tmp\" \"$LAUNCH_ARGV_CAPTURE.open\"\n",
    );
    let home = scratch.path().join("home");
    let shell_cwd = scratch.path().join("focused shell directory");
    fs::create_dir_all(&home).unwrap();
    fs::create_dir_all(&shell_cwd).unwrap();
    let mut shell = ChildGuard(
        Command::new("/bin/sh")
            .arg("-c")
            .arg("read -r _")
            .current_dir(&shell_cwd)
            .stdin(Stdio::piped())
            .spawn()
            .unwrap(),
    );
    let state = json!({"windows": [{"id": 10, "pid": shell.0.id(), "app_id": "terminal", "title": "Terminal", "focused": true}]});
    let wayfire = FakeWayfire::new(&scratch, state, 1);
    wayfire.env_file(&path, &capture, &home, None);
    let output = run_with(
        &path,
        &capture,
        &["--cwd", "files"],
        session_env(&wayfire.runtime),
    );
    shell.0.stdin.take().unwrap().write_all(b"exit\n").unwrap();
    shell.0.wait().unwrap();
    let requests = wayfire.join();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    wait_for(&capture.with_extension("open"));
    assert_eq!(
        decode_argv(&capture.with_extension("open")),
        [shell_cwd.to_str().unwrap()]
    );
    assert_eq!(requests[0]["method"], "window-rules/get-focused-view");
}

#[test]
fn restart_targets_exact_current_user_name_and_relaunches_arguments() {
    let scratch = Scratch::new("restart-app");
    let capture = scratch.path().join("argv");
    let path = launch_stack(&scratch);
    let pkill_capture = scratch.path().join("pkill-argv");
    let app = scratch.executable(
        "sample-app",
        "#!/bin/sh\ntmp=\"$LAUNCH_ARGV_CAPTURE.app.tmp.$$\"\nprintf '%s\\0' \"$@\" > \"$tmp\" && /bin/mv -- \"$tmp\" \"$LAUNCH_ARGV_CAPTURE.app\"\n",
    );
    scratch.executable(
        "pkill",
        "#!/bin/sh\nprintf '%s\\0' \"$@\" > \"$PKILL_ARGV_CAPTURE\"\nexit 1\n",
    );
    let output = run_with(
        &path,
        &capture,
        &["--restart", "sample-app", "--safe", "two words"],
        vec![(
            "PKILL_ARGV_CAPTURE".into(),
            pkill_capture.display().to_string(),
        )],
    );
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    wait_for(&capture.with_extension("app"));
    let process_uid: u32 = fs::read_to_string("/proc/self/status")
        .unwrap()
        .lines()
        .find_map(|line| {
            line.strip_prefix("Uid:")
                .and_then(|value| value.split_whitespace().next())
        })
        .unwrap()
        .parse()
        .unwrap();
    assert_eq!(
        decode_argv(&pkill_capture),
        ["-u", &process_uid.to_string(), "-x", "--", "sample-app"]
    );
    assert_eq!(
        decode_argv(&capture.with_extension("app")),
        ["--safe", "two words"]
    );
    assert_eq!(app.file_name().unwrap(), "sample-app");
}

#[test]
fn restart_escapes_process_name_regex_metacharacters() {
    let scratch = Scratch::new("restart-literal-name");
    let capture = scratch.path().join("argv");
    let path = launch_stack(&scratch);
    let pkill_capture = scratch.path().join("pkill-argv");
    recording_program(&scratch, "sample.app*", "app");
    scratch.executable(
        "pkill",
        "#!/bin/sh\nprintf '%s\\0' \"$@\" > \"$PKILL_ARGV_CAPTURE\"\nexit 1\n",
    );
    let output = run_with(
        &path,
        &capture,
        &["--restart", "sample.app*", "two words"],
        vec![(
            "PKILL_ARGV_CAPTURE".into(),
            pkill_capture.display().to_string(),
        )],
    );
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    wait_for(&capture.with_extension("app"));
    let process_uid: u32 = fs::read_to_string("/proc/self/status")
        .unwrap()
        .lines()
        .find_map(|line| {
            line.strip_prefix("Uid:")
                .and_then(|value| value.split_whitespace().next())
        })
        .unwrap()
        .parse()
        .unwrap();
    assert_eq!(
        decode_argv(&pkill_capture),
        ["-u", &process_uid.to_string(), "-x", "--", r"sample\.app\*"]
    );
    assert_eq!(decode_argv(&capture.with_extension("app")), ["two words"]);
}

#[test]
fn restart_reports_target_exec_errors_after_absent_old_process() {
    let scratch = Scratch::new("restart-target-exec-error");
    let capture = scratch.path().join("argv");
    let path = launch_stack(&scratch);
    scratch.executable("pkill", "#!/bin/sh\nexit 1\n");

    let output = run(&path, &capture, &["--restart", "missing-launch-target"]);

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("cannot relaunch missing-launch-target")
    );
}

#[test]
fn restart_reports_stop_errors_and_does_not_launch_after_a_failed_stop() {
    let scratch = Scratch::new("restart-stop-error");
    let capture = scratch.path().join("argv");
    let path = launch_stack(&scratch);
    recording_program(&scratch, "sample-app", "app");
    scratch.executable("pkill", "#!/bin/sh\nexit 2\n");
    let output = run(&path, &capture, &["--restart", "sample-app"]);
    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("cannot stop sample-app"));
    assert!(!capture.with_extension("app").exists());
}
