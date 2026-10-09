#![cfg(unix)]

use serde_json::{Value, json};
use std::fs;
use std::io::{BufRead, BufReader, Write};
use std::os::fd::AsRawFd;
use std::os::unix::fs::{DirBuilderExt, PermissionsExt};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::thread;
use std::time::{Duration, Instant};

static TEMP_ID: AtomicU64 = AtomicU64::new(0);

struct TempDir(PathBuf);

impl TempDir {
    fn new() -> Self {
        let id = TEMP_ID.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!("ad-{}-{id}", std::process::id()));
        let mut builder = fs::DirBuilder::new();
        builder.mode(0o700);
        builder.create(&path).expect("create test root");
        Self(path)
    }

    fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

struct RunningDaemon(Child);

impl RunningDaemon {
    fn start(root: &Path) -> Self {
        let runtime = root.join("r");
        let state = root.join("s");
        fs::create_dir_all(&runtime).expect("create runtime home");
        fs::create_dir_all(&state).expect("create state home");
        fs::set_permissions(&runtime, fs::Permissions::from_mode(0o700))
            .expect("make runtime home private");
        fs::set_permissions(&state, fs::Permissions::from_mode(0o700))
            .expect("make state home private");

        let child = Command::new(env!("CARGO_BIN_EXE_attentiond"))
            .env("XDG_RUNTIME_DIR", &runtime)
            .env("XDG_STATE_HOME", &state)
            .env("HOME", root)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::inherit())
            .spawn()
            .expect("start attentiond process");
        let mut daemon = Self(child);
        wait_for_socket(&mut daemon, &runtime.join("attention/attention.sock"));
        daemon
    }

    fn wait_for_exit(&mut self) -> std::process::ExitStatus {
        let deadline = Instant::now() + Duration::from_secs(2);
        loop {
            if let Some(status) = self.0.try_wait().expect("check daemon process") {
                return status;
            }
            assert!(
                Instant::now() < deadline,
                "second daemon did not exit promptly"
            );
            thread::sleep(Duration::from_millis(10));
        }
    }

    fn stop(&mut self) {
        if self.0.try_wait().expect("check daemon process").is_none() {
            self.0.kill().expect("stop daemon");
            self.0.wait().expect("reap daemon");
        }
    }
}

impl Drop for RunningDaemon {
    fn drop(&mut self) {
        self.stop();
    }
}

fn wait_for_socket(daemon: &mut RunningDaemon, path: &Path) {
    let deadline = Instant::now() + Duration::from_secs(3);
    loop {
        if let Ok(stream) = UnixStream::connect(path) {
            drop(stream);
            return;
        }
        if let Some(status) = daemon.0.try_wait().expect("check daemon startup") {
            panic!("attentiond exited before opening its socket: {status}");
        }
        assert!(
            Instant::now() < deadline,
            "attentiond did not open its socket"
        );
        thread::sleep(Duration::from_millis(10));
    }
}

fn connect(path: &Path) -> (UnixStream, BufReader<UnixStream>) {
    let writer = UnixStream::connect(path).expect("connect to attentiond");
    writer
        .set_read_timeout(Some(Duration::from_secs(2)))
        .expect("set stream timeout");
    let reader = BufReader::new(writer.try_clone().expect("clone client stream"));
    (writer, reader)
}

fn send(writer: &mut UnixStream, frame: &Value) {
    serde_json::to_writer(&mut *writer, frame).expect("serialize request");
    writer.write_all(b"\n").expect("terminate request frame");
    writer.flush().expect("flush request");
}

fn receive(reader: &mut BufReader<UnixStream>) -> Value {
    let mut line = String::new();
    let bytes = reader.read_line(&mut line).expect("read response");
    assert!(bytes > 0, "daemon closed a live connection");
    serde_json::from_str(&line).expect("response is JSON")
}

fn set_receive_buffer_size(stream: &UnixStream, bytes: libc::c_int) {
    let result = unsafe {
        libc::setsockopt(
            stream.as_raw_fd(),
            libc::SOL_SOCKET,
            libc::SO_RCVBUF,
            (&bytes as *const libc::c_int).cast(),
            std::mem::size_of_val(&bytes) as libc::socklen_t,
        )
    };
    assert_eq!(
        result,
        0,
        "set slow-reader socket buffer: {}",
        std::io::Error::last_os_error()
    );
}

fn read_until_disconnect(reader: &mut BufReader<UnixStream>) {
    loop {
        let mut line = String::new();
        match reader.read_line(&mut line) {
            Ok(0) => return,
            Ok(_) => {} // Drain frames already in the socket after the server evicts the reader.
            Err(error)
                if error.kind() == std::io::ErrorKind::WouldBlock
                    || error.kind() == std::io::ErrorKind::TimedOut =>
            {
                panic!("stalled subscriber was not disconnected")
            }
            Err(error) => panic!("unexpected slow-reader socket error: {error}"),
        }
    }
}

#[test]
fn daemon_owns_one_private_socket_and_restores_idempotent_state_after_restart() {
    let temp = TempDir::new();
    let runtime = temp.path().join("r");
    let state = temp.path().join("s");
    let socket = runtime.join("attention/attention.sock");

    let mut first = RunningDaemon::start(temp.path());
    let metadata = fs::metadata(socket.parent().unwrap()).expect("attention runtime directory");
    assert_eq!(metadata.permissions().mode() & 0o777, 0o700);

    let (mut client, mut client_reader) = connect(&socket);
    send(
        &mut client,
        &json!({"v":1,"type":"subscribe","sender_ids":[]}),
    );
    assert_eq!(receive(&mut client_reader)["type"], "ok");
    assert_eq!(receive(&mut client_reader)["messages"], json!([]));

    let (mut sender, mut sender_reader) = connect(&socket);
    let message = json!({
        "v":1,"type":"message","ref":"before-restart",
        "sender_id":"urn:uuid:00000000-0000-4000-8000-000000000000",
        "message_id":"stable-1","created_at":1000,"title":"Persist me",
        "custom":{"kept":true}
    });
    send(&mut sender, &message);
    assert_eq!(receive(&mut sender_reader)["type"], "ok");
    let delivery = receive(&mut client_reader);
    assert_eq!(delivery["type"], "delivery");
    assert_eq!(delivery["as"], "knock");
    assert_eq!(delivery["message"]["custom"]["kept"], true);

    let second = Command::new(env!("CARGO_BIN_EXE_attentiond"))
        .env("XDG_RUNTIME_DIR", &runtime)
        .env("XDG_STATE_HOME", &state)
        .env("HOME", temp.path())
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::inherit())
        .spawn()
        .expect("start competing daemon");
    let status = RunningDaemon(second).wait_for_exit();
    assert!(
        !status.success(),
        "second daemon must fail while first is active"
    );
    assert!(socket.exists(), "the active socket remains in place");
    assert!(
        UnixStream::connect(&socket).is_ok(),
        "the original daemon still accepts"
    );

    first.stop();
    drop(sender);
    drop(client);
    let mut restarted = RunningDaemon::start(temp.path());

    let (mut reconnect, mut reconnect_reader) = connect(&socket);
    send(
        &mut reconnect,
        &json!({"v":1,"type":"subscribe","sender_ids":[]}),
    );
    assert_eq!(receive(&mut reconnect_reader)["type"], "ok");
    let snapshot = receive(&mut reconnect_reader);
    assert_eq!(snapshot["type"], "snapshot");
    assert_eq!(snapshot["messages"].as_array().unwrap().len(), 1);
    assert_eq!(snapshot["messages"][0]["message"]["custom"]["kept"], true);

    let (mut retry, mut retry_reader) = connect(&socket);
    send(&mut retry, &message);
    let duplicate = receive(&mut retry_reader);
    assert_eq!(duplicate["type"], "ok");
    assert_eq!(duplicate["duplicate"], true);
    reconnect
        .set_read_timeout(Some(Duration::from_millis(80)))
        .expect("set no-redelivery timeout");
    let mut unexpected = String::new();
    match reconnect_reader.read_line(&mut unexpected) {
        Err(error)
            if error.kind() == std::io::ErrorKind::WouldBlock
                || error.kind() == std::io::ErrorKind::TimedOut => {}
        Ok(0) => panic!("reconnected client closed unexpectedly"),
        Ok(_) => panic!("duplicate was delivered again: {unexpected}"),
        Err(error) => panic!("unexpected client read failure: {error}"),
    }
    let store_path = state.join("attention/messages.json");
    assert!(store_path.is_file(), "the store lives under XDG_STATE_HOME");

    restarted.stop();
}

#[test]
fn stalled_subscriber_does_not_stall_healthy_delivery_or_lifecycle() {
    const MESSAGE_COUNT: usize = 48;
    let temp = TempDir::new();
    let socket = temp.path().join("r/attention/attention.sock");
    let mut daemon = RunningDaemon::start(temp.path());

    let (mut slow, mut slow_reader) = connect(&socket);
    slow.set_read_timeout(Some(Duration::from_secs(10)))
        .expect("set slow-reader deadline");
    set_receive_buffer_size(&slow, 4096);
    send(
        &mut slow,
        &json!({"v":1,"type":"subscribe","sender_ids":["agentd://host/slow-reader"]}),
    );
    assert_eq!(receive(&mut slow_reader)["type"], "ok");
    assert_eq!(receive(&mut slow_reader)["type"], "snapshot");

    let (mut healthy, mut healthy_reader) = connect(&socket);
    healthy
        .set_read_timeout(Some(Duration::from_secs(10)))
        .expect("set healthy-reader deadline");
    send(
        &mut healthy,
        &json!({"v":1,"type":"subscribe","sender_ids":["agentd://host/slow-reader"]}),
    );
    assert_eq!(receive(&mut healthy_reader)["type"], "ok");
    assert_eq!(receive(&mut healthy_reader)["type"], "snapshot");

    let expected_ids: Vec<String> = (0..MESSAGE_COUNT)
        .map(|index| format!("backpressure-{index}"))
        .collect();
    let expected_from_reader = expected_ids.clone();
    let healthy_read = thread::spawn(move || {
        let mut received_ids = Vec::with_capacity(MESSAGE_COUNT);
        for _ in 0..MESSAGE_COUNT {
            let frame = receive(&mut healthy_reader);
            assert_eq!(frame["type"], "delivery");
            received_ids.push(
                frame["message"]["message_id"]
                    .as_str()
                    .expect("delivered message id")
                    .to_owned(),
            );
        }
        let lifecycle = receive(&mut healthy_reader);
        (received_ids, lifecycle)
    });

    let (mut sender, mut sender_reader) = connect(&socket);
    sender
        .set_read_timeout(Some(Duration::from_secs(10)))
        .expect("set sender deadline");
    let title = "x".repeat(60_000);
    for (index, message_id) in expected_ids.iter().enumerate() {
        send(
            &mut sender,
            &json!({
                "v":1,"type":"message","sender_id":"agentd://host/slow-reader",
                "message_id":message_id,"created_at":index,"title":title
            }),
        );
        assert_eq!(receive(&mut sender_reader)["type"], "ok");
    }
    send(
        &mut sender,
        &json!({
            "v":1,"type":"lifecycle","sender_id":"agentd://host/slow-reader",
            "message_id":expected_ids[0],"state":"cleared","at":MESSAGE_COUNT
        }),
    );
    assert_eq!(receive(&mut sender_reader)["type"], "ok");

    let (received_ids, lifecycle) = healthy_read.join().expect("healthy reader completes");
    assert_eq!(
        received_ids, expected_from_reader,
        "delivery order is preserved"
    );
    assert_eq!(lifecycle["type"], "lifecycle");
    assert_eq!(lifecycle["message_id"], expected_ids[0]);
    assert_eq!(lifecycle["state"], "cleared");
    read_until_disconnect(&mut slow_reader);
    daemon.stop();
}
