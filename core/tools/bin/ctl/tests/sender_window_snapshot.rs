use serde_json::{Value, json};
use std::fs;
use std::io::{Read, Write};
use std::os::unix::fs::{PermissionsExt, symlink};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Output, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

static NEXT_FIXTURE: AtomicU64 = AtomicU64::new(0);

#[derive(Clone)]
enum Reply {
    Json(Value),
    IpcError,
    Eof,
    TruncatedFrame,
    MalformedJson,
    Timeout,
    RejectWrongSession,
}

struct FakeWayfire {
    stop: Arc<AtomicBool>,
    requests: Arc<Mutex<Vec<Value>>>,
    errors: Arc<Mutex<Vec<String>>>,
    worker: Option<JoinHandle<()>>,
}

impl FakeWayfire {
    fn start(path: &Path, reply: Reply) -> Self {
        let listener = UnixListener::bind(path).expect("bind fake Wayfire socket");
        listener
            .set_nonblocking(true)
            .expect("set fake Wayfire listener nonblocking");
        let stop = Arc::new(AtomicBool::new(false));
        let requests = Arc::new(Mutex::new(Vec::new()));
        let errors = Arc::new(Mutex::new(Vec::new()));
        let thread_stop = Arc::clone(&stop);
        let thread_requests = Arc::clone(&requests);
        let thread_errors = Arc::clone(&errors);
        let worker = thread::spawn(move || {
            loop {
                match listener.accept() {
                    Ok((mut stream, _)) => {
                        if let Err(error) = handle_connection(&mut stream, &reply, &thread_requests)
                        {
                            thread_errors
                                .lock()
                                .expect("fake Wayfire error lock")
                                .push(error);
                        }
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                        if thread_stop.load(Ordering::Acquire) {
                            break;
                        }
                        thread::sleep(Duration::from_millis(5));
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
                    Err(error) => {
                        thread_errors
                            .lock()
                            .expect("fake Wayfire error lock")
                            .push(error.to_string());
                        break;
                    }
                }
            }
        });
        Self {
            stop,
            requests,
            errors,
            worker: Some(worker),
        }
    }

    fn finish(&mut self) -> Vec<Value> {
        self.stop.store(true, Ordering::Release);
        self.worker
            .take()
            .expect("fake Wayfire worker")
            .join()
            .expect("fake Wayfire worker panicked");
        let errors = self.errors.lock().expect("fake Wayfire error lock");
        assert!(errors.is_empty(), "fake Wayfire errors: {errors:?}");
        self.requests
            .lock()
            .expect("fake Wayfire request lock")
            .clone()
    }
}

impl Drop for FakeWayfire {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}

fn handle_connection(
    stream: &mut UnixStream,
    reply: &Reply,
    requests: &Mutex<Vec<Value>>,
) -> Result<(), String> {
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .map_err(|error| error.to_string())?;
    stream
        .set_write_timeout(Some(Duration::from_secs(5)))
        .map_err(|error| error.to_string())?;
    let Some(request) = read_frame(stream)? else {
        return Ok(()); // Session liveness probe; it sends no IPC request.
    };
    requests
        .lock()
        .map_err(|_| "fake Wayfire request lock poisoned".to_string())?
        .push(request);
    match reply {
        Reply::Json(value) => write_frame(stream, &serde_json::to_vec(value).unwrap())?,
        Reply::IpcError => write_frame(stream, br#"{"error":"snapshot unavailable"}"#)?,
        Reply::Eof => {}
        Reply::TruncatedFrame => {
            stream
                .write_all(&20_u32.to_le_bytes())
                .and_then(|_| stream.write_all(b"{\"views\":"))
                .map_err(|error| error.to_string())?;
        }
        Reply::MalformedJson => write_frame(stream, b"not-json")?,
        Reply::Timeout => {
            let mut bytes = [0; 1];
            loop {
                match stream.read(&mut bytes) {
                    Ok(0) => break,
                    Ok(_) => {}
                    Err(error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
                    Err(error) if error.kind() == std::io::ErrorKind::TimedOut => break,
                    Err(error) => return Err(error.to_string()),
                }
            }
        }
        Reply::RejectWrongSession => {
            write_frame(stream, br#"{"error":"request reached the wrong session"}"#)?;
        }
    }
    Ok(())
}

fn read_frame(stream: &mut UnixStream) -> Result<Option<Value>, String> {
    let mut header = [0; 4];
    match stream.read_exact(&mut header) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::UnexpectedEof => return Ok(None),
        Err(error) => return Err(error.to_string()),
    }
    let size = u32::from_le_bytes(header) as usize;
    if size > 16 * 1024 * 1024 {
        return Err(format!("request frame too large: {size}"));
    }
    let mut body = vec![0; size];
    stream
        .read_exact(&mut body)
        .map_err(|error| error.to_string())?;
    serde_json::from_slice(&body)
        .map(Some)
        .map_err(|error| error.to_string())
}

fn write_frame(stream: &mut UnixStream, body: &[u8]) -> Result<(), String> {
    let len = u32::try_from(body.len()).map_err(|_| "reply frame too large".to_string())?;
    stream
        .write_all(&len.to_le_bytes())
        .and_then(|_| stream.write_all(body))
        .map_err(|error| error.to_string())
}

struct Fixture {
    root: PathBuf,
    socket_root: PathBuf,
    runtime: PathBuf,
    bin: PathBuf,
    selected_socket: PathBuf,
    original_path: String,
}

impl Fixture {
    fn new() -> Self {
        let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .ancestors()
            .nth(4)
            .expect("ctl crate lives below the repository root")
            .to_path_buf();
        let id = NEXT_FIXTURE.fetch_add(1, Ordering::Relaxed);
        let root = repo
            .join("build")
            .join(format!("ctl-sender-window-{}-{id}", std::process::id()));
        fs::create_dir_all(&root).expect("create private fixture root");
        fs::set_permissions(&root, fs::Permissions::from_mode(0o700))
            .expect("restrict fixture root permissions");
        let socket_root = Path::new("/tmp").join(format!("sctl-{}-{id}", std::process::id()));
        fs::create_dir(&socket_root).expect("create short-lived socket directory");
        fs::set_permissions(&socket_root, fs::Permissions::from_mode(0o700))
            .expect("restrict socket directory permissions");
        let runtime = root.join("session");
        let bin = root.join("bin");
        fs::create_dir(&runtime).expect("create fixture session directory");
        fs::create_dir(&bin).expect("create fixture PATH directory");
        fs::set_permissions(&runtime, fs::Permissions::from_mode(0o700))
            .expect("restrict fixture session permissions");
        fs::set_permissions(&bin, fs::Permissions::from_mode(0o700))
            .expect("restrict fixture PATH permissions");
        let ctl = bin.join("scottland-ctl");
        symlink(env!("CARGO_BIN_EXE_scottland-ctl"), ctl)
            .expect("add installed-command name to fixture PATH");
        Self {
            root,
            socket_root,
            runtime,
            bin,
            selected_socket: PathBuf::new(),
            original_path: std::env::var("PATH").unwrap_or_default(),
        }
    }

    fn add_session(&mut self, display: &str, socket: &Path) {
        let path = self.runtime.join(format!("{display}.env"));
        let search_path = format!("{}:{}", self.bin.display(), self.original_path);
        let body = format!(
            "WAYLAND_DISPLAY={display}\0WAYFIRE_SOCKET={}\0PATH={}\0",
            socket.display(),
            search_path,
        );
        fs::write(path, body.as_bytes()).expect("write fixture session environment");
    }

    fn invoke(&self) -> Output {
        let script = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .ancestors()
            .nth(4)
            .expect("ctl crate lives below the repository root")
            .join("core/libexec/scottland-exec");
        let child = Command::new("python3")
            .arg(script)
            .arg("--")
            .arg("scottland-ctl")
            .arg("sender-window-snapshot")
            .env("SCOTTLAND_SESSION_DIR", &self.runtime)
            .env("WAYFIRE_SOCKET", &self.selected_socket)
            .env_remove("SCOTTLAND_EXEC")
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .expect("start scottland-exec consumer path");
        wait_with_deadline(child, Duration::from_secs(8))
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.socket_root);
        let _ = fs::remove_dir_all(&self.root);
    }
}

fn wait_with_deadline(mut child: Child, deadline: Duration) -> Output {
    let expires = Instant::now() + deadline;
    loop {
        if child.try_wait().expect("poll ctl process").is_some() {
            return child
                .wait_with_output()
                .expect("collect ctl process output");
        }
        if Instant::now() >= expires {
            let _ = child.kill();
            let output = child
                .wait_with_output()
                .expect("reap timed out ctl process");
            panic!(
                "scottland-ctl exceeded the generous fixture hang deadline: {} {}",
                String::from_utf8_lossy(&output.stdout),
                String::from_utf8_lossy(&output.stderr)
            );
        }
        thread::sleep(Duration::from_millis(10));
    }
}

fn two_session_fixture(reply: Reply) -> (Fixture, FakeWayfire, FakeWayfire) {
    let mut fixture = Fixture::new();
    let first_socket = fixture.socket_root.join("wayfire-first.sock");
    let selected_socket = fixture.socket_root.join("wayfire-selected.sock");
    let first = FakeWayfire::start(&first_socket, Reply::RejectWrongSession);
    let selected = FakeWayfire::start(&selected_socket, reply);
    fixture.add_session("wayland-1", &first_socket);
    fixture.add_session("wayland-2", &selected_socket);
    fixture.selected_socket = selected_socket;
    (fixture, first, selected)
}

fn assert_one_selected_request(first: &mut FakeWayfire, selected: &mut FakeWayfire) {
    assert!(first.finish().is_empty(), "unselected session received IPC");
    assert_eq!(
        selected.finish(),
        [json!({"method":"window-rules/list-views","data":{}})],
        "selected session must receive exactly one snapshot request"
    );
}

fn assert_failure_without_snapshot(output: Output) {
    assert!(!output.status.success(), "failure returned success");
    assert!(
        output.stdout.is_empty(),
        "failure emitted a success payload"
    );
    assert!(!output.stderr.is_empty(), "failure did not report an error");
}

#[test]
fn command_rejects_arguments_outside_the_snapshot_contract() {
    let output = Command::new(env!("CARGO_BIN_EXE_scottland-ctl"))
        .args(["sender-window-snapshot", "ignored"])
        .env_remove("WAYFIRE_SOCKET")
        .env_remove("SCOTTLAND_EXEC")
        .output()
        .expect("run ctl with an unsupported snapshot argument");
    assert!(!output.status.success());
    assert!(output.stdout.is_empty());
    assert!(String::from_utf8_lossy(&output.stderr).contains("invalid command or arguments"));
}

#[test]
fn emits_the_selected_sessions_unfiltered_snapshot_once() {
    let reply = json!([
        {"id": 19, "kind": "widget", "widget": {"name": "clock"}, "title": null},
        {"id": 3, "kind": "panel", "app_id": null},
        {"id": 7, "app_id": "foot"}
    ]);
    let (fixture, mut first, mut selected) = two_session_fixture(Reply::Json(reply.clone()));
    let output = fixture.invoke();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        serde_json::from_slice::<Value>(&output.stdout).unwrap(),
        reply
    );
    assert!(output.stdout.ends_with(b"\n"));
    assert_one_selected_request(&mut first, &mut selected);
}

#[test]
fn ipc_error_is_nonzero_stderr_without_a_snapshot_payload() {
    let (fixture, mut first, mut selected) = two_session_fixture(Reply::IpcError);
    let output = fixture.invoke();
    assert_eq!(output.status.code(), Some(1));
    assert!(output.stdout.is_empty());
    assert!(String::from_utf8_lossy(&output.stderr).contains("snapshot unavailable"));
    assert_one_selected_request(&mut first, &mut selected);
}

#[test]
fn unavailable_session_fails_without_a_snapshot_payload() {
    let mut fixture = Fixture::new();
    let missing = fixture.socket_root.join("missing.sock");
    fixture.add_session("wayland-2", &missing);
    fixture.selected_socket = missing;
    let output = fixture.invoke();
    assert_failure_without_snapshot(output);
}

#[test]
fn premature_eof_fails_without_a_snapshot_payload() {
    let (fixture, mut first, mut selected) = two_session_fixture(Reply::Eof);
    let output = fixture.invoke();
    assert_failure_without_snapshot(output);
    assert_one_selected_request(&mut first, &mut selected);
}

#[test]
fn truncated_frame_fails_without_a_snapshot_payload() {
    let (fixture, mut first, mut selected) = two_session_fixture(Reply::TruncatedFrame);
    let output = fixture.invoke();
    assert_failure_without_snapshot(output);
    assert_one_selected_request(&mut first, &mut selected);
}

#[test]
fn malformed_json_fails_without_a_snapshot_payload() {
    let (fixture, mut first, mut selected) = two_session_fixture(Reply::MalformedJson);
    let output = fixture.invoke();
    assert_failure_without_snapshot(output);
    assert_one_selected_request(&mut first, &mut selected);
}

#[test]
fn stalled_reply_ends_at_the_socket_operation_timeout() {
    let (fixture, mut first, mut selected) = two_session_fixture(Reply::Timeout);
    let output = fixture.invoke();
    assert_failure_without_snapshot(output);
    assert_one_selected_request(&mut first, &mut selected);
}
