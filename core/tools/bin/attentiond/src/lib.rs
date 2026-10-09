//! Local, sender-neutral attention transport and store.

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use std::collections::{HashMap, HashSet};
use std::env;
use std::fs::{self, File, OpenOptions};
use std::io::{self, BufRead, BufReader, Write};
use std::os::fd::AsRawFd;
use std::os::unix::fs::{DirBuilderExt, FileTypeExt, MetadataExt, OpenOptionsExt, PermissionsExt};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{SystemTime, UNIX_EPOCH};

const MAX_FRAME_BYTES: usize = 65_536;
const MAX_OPEN_PER_SENDER: usize = 100;
const MAX_OPEN_TOTAL: usize = 1_000;
const MAX_CLOSED_TOTAL: usize = 10_000;
const CLOSED_RETENTION_MS: i64 = 30 * 24 * 60 * 60 * 1_000;

static TEMP_FILE_COUNTER: AtomicU64 = AtomicU64::new(0);

pub fn run() -> io::Result<()> {
    let runtime_home = required_absolute_env("XDG_RUNTIME_DIR")?;
    let state_home = match env::var_os("XDG_STATE_HOME") {
        Some(value) if !value.is_empty() => absolute_path("XDG_STATE_HOME", PathBuf::from(value))?,
        _ => {
            let home = required_absolute_env("HOME")?;
            home.join(".local/state")
        }
    };

    let runtime_dir = runtime_home.join("attention");
    let state_dir = state_home.join("attention");
    ensure_private_directory(&runtime_dir)?;
    ensure_private_directory(&state_dir)?;

    // The lock makes stale-socket replacement safe when two instances start together.
    let _instance_lock = acquire_instance_lock(&runtime_dir)?;
    let socket_path = runtime_dir.join("attention.sock");
    prepare_socket_path(&socket_path)?;
    let listener = UnixListener::bind(&socket_path)?;
    fs::set_permissions(&socket_path, fs::Permissions::from_mode(0o600))?;

    let store_path = state_dir.join("messages.json");
    let mut store = Store::load(&store_path)?;
    if store.normalize(now_ms()) {
        persist_store(&store_path, &store)?;
    }

    let shared = Arc::new(Mutex::new(ServerState::new(store, store_path)));
    for connection in listener.incoming() {
        match connection {
            Ok(stream) => {
                let writer = stream.try_clone()?;
                let client_id = shared
                    .lock()
                    .map_err(|_| io::Error::other("attention state lock poisoned"))?
                    .add_client(writer);
                let state = Arc::clone(&shared);
                if let Err(error) = thread::Builder::new()
                    .name(format!("attention-client-{client_id}"))
                    .spawn(move || serve_connection(stream, client_id, state))
                {
                    shared
                        .lock()
                        .map_err(|_| io::Error::other("attention state lock poisoned"))?
                        .clients
                        .remove(&client_id);
                    eprintln!("attentiond: could not start client thread: {error}");
                }
            }
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) => return Err(error),
        }
    }
    Ok(())
}

fn required_absolute_env(name: &str) -> io::Result<PathBuf> {
    let value = env::var_os(name)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, format!("{name} is not set")))?;
    absolute_path(name, PathBuf::from(value))
}

fn absolute_path(name: &str, path: PathBuf) -> io::Result<PathBuf> {
    if path.is_absolute() {
        Ok(path)
    } else {
        Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("{name} must be an absolute path"),
        ))
    }
}

fn ensure_private_directory(path: &Path) -> io::Result<()> {
    let mut builder = fs::DirBuilder::new();
    builder.recursive(true).mode(0o700);
    builder.create(path)?;

    let metadata = fs::symlink_metadata(path)?;
    if !metadata.file_type().is_dir() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("{} is not a real directory", path.display()),
        ));
    }
    let effective_uid = unsafe { libc::geteuid() };
    if metadata.uid() != effective_uid {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            format!("{} is not owned by this user", path.display()),
        ));
    }
    fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
    let secured = fs::symlink_metadata(path)?;
    if !secured.file_type().is_dir()
        || secured.uid() != effective_uid
        || secured.permissions().mode() & 0o077 != 0
    {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            format!("{} is not private to this user", path.display()),
        ));
    }
    Ok(())
}

fn acquire_instance_lock(runtime_dir: &Path) -> io::Result<File> {
    let path = runtime_dir.join("attention.lock");
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .mode(0o600)
        .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
        .open(&path)?;
    let metadata = file.metadata()?;
    if !metadata.is_file()
        || metadata.uid() != unsafe { libc::geteuid() }
        || metadata.permissions().mode() & 0o077 != 0
    {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            format!("{} is not a private lock file", path.display()),
        ));
    }
    let result = unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) };
    if result == -1 {
        let error = io::Error::last_os_error();
        if error.raw_os_error() == Some(libc::EWOULDBLOCK)
            || error.raw_os_error() == Some(libc::EAGAIN)
        {
            return Err(io::Error::new(
                io::ErrorKind::AddrInUse,
                "another attention daemon holds the instance lock",
            ));
        }
        return Err(error);
    }
    Ok(file)
}

fn prepare_socket_path(path: &Path) -> io::Result<()> {
    let metadata = match fs::symlink_metadata(path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(error),
    };
    if !metadata.file_type().is_socket() {
        return Err(io::Error::new(
            io::ErrorKind::AlreadyExists,
            format!("{} exists and is not a socket", path.display()),
        ));
    }

    match UnixStream::connect(path) {
        Ok(_) => Err(io::Error::new(
            io::ErrorKind::AddrInUse,
            "an attention daemon already accepts connections",
        )),
        Err(error)
            if error.kind() == io::ErrorKind::ConnectionRefused
                || error.kind() == io::ErrorKind::NotFound =>
        {
            // Recheck before unlinking so a socket that became live is never removed.
            match UnixStream::connect(path) {
                Ok(_) => Err(io::Error::new(
                    io::ErrorKind::AddrInUse,
                    "an attention daemon already accepts connections",
                )),
                Err(error)
                    if error.kind() == io::ErrorKind::ConnectionRefused
                        || error.kind() == io::ErrorKind::NotFound =>
                {
                    match fs::symlink_metadata(path) {
                        Ok(current) if current.file_type().is_socket() => fs::remove_file(path),
                        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
                        Ok(_) => Err(io::Error::new(
                            io::ErrorKind::AlreadyExists,
                            format!("{} changed while checking the stale socket", path.display()),
                        )),
                        Err(error) => Err(error),
                    }
                }
                Err(error) => Err(error),
            }
        }
        Err(error) => Err(error),
    }
}

fn serve_connection(stream: UnixStream, client_id: u64, shared: Arc<Mutex<ServerState>>) {
    let mut reader = BufReader::new(stream);
    loop {
        let frame = match read_frame(&mut reader) {
            Ok(ReadFrame::Eof) => break,
            Ok(ReadFrame::TooLong) => {
                if send_error(&shared, client_id, WireError::new("bad_frame"), None).is_err() {
                    break;
                }
                continue;
            }
            Ok(ReadFrame::Incomplete) => {
                let _ = send_error(&shared, client_id, WireError::new("bad_frame"), None);
                break;
            }
            Ok(ReadFrame::Line(bytes)) => match std::str::from_utf8(&bytes) {
                Ok(text) => match serde_json::from_str::<Value>(text) {
                    Ok(value) => value,
                    Err(_) => {
                        if send_error(&shared, client_id, WireError::new("bad_frame"), None)
                            .is_err()
                        {
                            break;
                        }
                        continue;
                    }
                },
                Err(_) => {
                    if send_error(&shared, client_id, WireError::new("bad_frame"), None).is_err() {
                        break;
                    }
                    continue;
                }
            },
            Err(error) => {
                eprintln!("attentiond: client {client_id} read failed: {error}");
                break;
            }
        };

        if let Err(error) = handle_value(&shared, client_id, frame) {
            eprintln!("attentiond: fatal store or client write failure: {error}");
            // A failed durable write must not be acknowledged or followed by more mutations.
            std::process::exit(1);
        }
    }
    if let Ok(mut state) = shared.lock() {
        state.clients.remove(&client_id);
    }
}

enum ReadFrame {
    Line(Vec<u8>),
    TooLong,
    Incomplete,
    Eof,
}

fn read_frame<R: BufRead>(reader: &mut R) -> io::Result<ReadFrame> {
    let mut line = Vec::new();
    let mut too_long = false;
    loop {
        let available = reader.fill_buf()?;
        if available.is_empty() {
            return Ok(if too_long {
                ReadFrame::TooLong
            } else if line.is_empty() {
                ReadFrame::Eof
            } else {
                ReadFrame::Incomplete
            });
        }
        let newline = available.iter().position(|byte| *byte == b'\n');
        let content_len = newline.unwrap_or(available.len());
        let consumed = newline.map_or(available.len(), |index| index + 1);
        if !too_long {
            if line.len().saturating_add(content_len) > MAX_FRAME_BYTES {
                too_long = true;
                line.clear();
            } else {
                line.extend_from_slice(&available[..content_len]);
            }
        }
        reader.consume(consumed);
        if newline.is_some() {
            return Ok(if too_long {
                ReadFrame::TooLong
            } else {
                ReadFrame::Line(line)
            });
        }
    }
}

#[derive(Debug)]
struct WireError {
    reason: &'static str,
    field: Option<String>,
}

impl WireError {
    fn new(reason: &'static str) -> Self {
        Self {
            reason,
            field: None,
        }
    }

    fn field(name: &str) -> Self {
        Self {
            reason: "invalid_field",
            field: Some(name.to_owned()),
        }
    }
}

#[derive(Debug)]
struct Envelope {
    request: Request,
    reference: Option<String>,
}

#[derive(Debug)]
enum Request {
    Message {
        sender_id: String,
        message_id: String,
        payload: Map<String, Value>,
    },
    Lifecycle {
        sender_id: String,
        message_id: String,
        state: String,
        at: Value,
        by: Option<String>,
    },
    Subscribe {
        sender_ids: HashSet<String>,
    },
    Answer {
        sender_id: String,
        message_id: String,
        answer: Value,
    },
}

fn parse_request(value: &Value) -> Result<Envelope, WireError> {
    let object = value
        .as_object()
        .ok_or_else(|| WireError::new("bad_frame"))?;
    let reference = match object.get("ref") {
        None => None,
        Some(Value::String(value)) => Some(value.clone()),
        Some(_) => return Err(WireError::field("ref")),
    };
    let version_one = object.get("v").is_some_and(|value| {
        value.as_i64().is_some_and(|version| version == 1)
            || value.as_u64().is_some_and(|version| version == 1)
    });
    if !version_one {
        return Err(WireError {
            reason: "unsupported_version",
            field: None,
        });
    }
    let kind = object
        .get("type")
        .and_then(Value::as_str)
        .ok_or_else(|| WireError::new("unknown_type"))?;

    let request = match kind {
        "message" => {
            let sender_id = required_string(object, "sender_id")?;
            if !valid_sender_id(&sender_id) {
                return Err(WireError::field("sender_id"));
            }
            let message_id = nonempty_string(object, "message_id")?;
            let _created_at = required_integer(object, "created_at")?;
            let _title = nonempty_string(object, "title")?;
            for name in ["body", "stake"] {
                if object.get(name).is_some_and(|value| !value.is_string()) {
                    return Err(WireError::field(name));
                }
            }
            if object
                .get("by_when")
                .is_some_and(|value| !is_integer(value))
            {
                return Err(WireError::field("by_when"));
            }
            for name in ["answer_ui", "callback"] {
                if object.get(name).is_some_and(|value| !value.is_object()) {
                    return Err(WireError::field(name));
                }
            }
            if let Some(open) = object.get("open") {
                let valid_open = open.as_object().is_some_and(|open| {
                    let argv_valid = open
                        .get("argv")
                        .and_then(Value::as_array)
                        .is_some_and(|argv| !argv.is_empty() && argv.iter().all(Value::is_string));
                    argv_valid && open.get("terminal").is_some_and(Value::is_boolean)
                });
                if !valid_open {
                    return Err(WireError::field("open"));
                }
            }
            let mut payload = object.clone();
            payload.remove("v");
            payload.remove("type");
            payload.remove("ref");
            Request::Message {
                sender_id,
                message_id,
                payload,
            }
        }
        "lifecycle" => {
            let sender_id = required_string(object, "sender_id")?;
            if !valid_sender_id(&sender_id) {
                return Err(WireError::field("sender_id"));
            }
            let message_id = nonempty_string(object, "message_id")?;
            let state = required_string(object, "state")?;
            if !matches!(
                state.as_str(),
                "answered" | "cleared" | "expired" | "superseded"
            ) {
                return Err(WireError::field("state"));
            }
            let at = required_integer_value(object, "at")?;
            let by = match object.get("by") {
                None => None,
                Some(Value::String(value)) => Some(value.clone()),
                Some(_) => return Err(WireError::field("by")),
            };
            Request::Lifecycle {
                sender_id,
                message_id,
                state,
                at,
                by,
            }
        }
        "subscribe" => {
            let values = object
                .get("sender_ids")
                .and_then(Value::as_array)
                .ok_or_else(|| WireError::field("sender_ids"))?;
            let mut sender_ids = HashSet::new();
            for value in values {
                let Some(sender_id) = value.as_str() else {
                    return Err(WireError::field("sender_ids"));
                };
                if !valid_sender_id(sender_id) {
                    return Err(WireError::field("sender_ids"));
                }
                sender_ids.insert(sender_id.to_owned());
            }
            Request::Subscribe { sender_ids }
        }
        "answer" => {
            let sender_id = required_string(object, "sender_id")?;
            if !valid_sender_id(&sender_id) {
                return Err(WireError::field("sender_id"));
            }
            let message_id = nonempty_string(object, "message_id")?;
            let answer = object
                .get("answer")
                .cloned()
                .ok_or_else(|| WireError::field("answer"))?;
            Request::Answer {
                sender_id,
                message_id,
                answer,
            }
        }
        _ => return Err(WireError::new("unknown_type")),
    };
    Ok(Envelope { request, reference })
}

fn required_string(object: &Map<String, Value>, field: &str) -> Result<String, WireError> {
    object
        .get(field)
        .and_then(Value::as_str)
        .map(ToOwned::to_owned)
        .ok_or_else(|| WireError::field(field))
}

fn nonempty_string(object: &Map<String, Value>, field: &str) -> Result<String, WireError> {
    let value = required_string(object, field)?;
    if value.is_empty() {
        Err(WireError::field(field))
    } else {
        Ok(value)
    }
}

fn required_integer(object: &Map<String, Value>, field: &str) -> Result<(), WireError> {
    object
        .get(field)
        .filter(|value| is_integer(value))
        .map(|_| ())
        .ok_or_else(|| WireError::field(field))
}

fn required_integer_value(object: &Map<String, Value>, field: &str) -> Result<Value, WireError> {
    object
        .get(field)
        .filter(|value| is_integer(value))
        .cloned()
        .ok_or_else(|| WireError::field(field))
}

fn is_integer(value: &Value) -> bool {
    value.as_i64().is_some() || value.as_u64().is_some()
}

fn valid_sender_id(value: &str) -> bool {
    let Some((namespace, rest)) = value.split_once(':') else {
        return false;
    };
    let mut chars = namespace.chars();
    let Some(first) = chars.next() else {
        return false;
    };
    first.is_ascii_lowercase()
        && chars.all(|ch| {
            ch.is_ascii_lowercase() || ch.is_ascii_digit() || matches!(ch, '+' | '.' | '-')
        })
        && !rest.is_empty()
        && !rest.chars().any(|ch| ch.is_whitespace() || ch.is_control())
}

fn handle_value(shared: &Arc<Mutex<ServerState>>, client_id: u64, value: Value) -> io::Result<()> {
    let envelope = match parse_request(&value) {
        Ok(envelope) => envelope,
        Err(error) => return send_error(shared, client_id, error, reference_from(&value)),
    };
    let mut state = shared
        .lock()
        .map_err(|_| io::Error::other("attention state lock poisoned"))?;
    if !state.clients.contains_key(&client_id) {
        return Ok(());
    }

    match envelope.request {
        Request::Message {
            sender_id,
            message_id,
            payload,
        } => handle_message(
            &mut state,
            client_id,
            sender_id,
            message_id,
            payload,
            envelope.reference,
        ),
        Request::Lifecycle {
            sender_id,
            message_id,
            state: lifecycle_state,
            at,
            by,
        } => handle_lifecycle(
            &mut state,
            client_id,
            sender_id,
            message_id,
            lifecycle_state,
            at,
            by,
            envelope.reference,
        ),
        Request::Subscribe { sender_ids } => {
            handle_subscribe(&mut state, client_id, sender_ids, envelope.reference)
        }
        Request::Answer {
            sender_id,
            message_id,
            answer,
        } => handle_answer(
            &mut state,
            client_id,
            sender_id,
            message_id,
            answer,
            envelope.reference,
        ),
    }
}

fn reference_from(value: &Value) -> Option<String> {
    value
        .as_object()?
        .get("ref")?
        .as_str()
        .map(ToOwned::to_owned)
}

fn handle_message(
    state: &mut ServerState,
    client_id: u64,
    sender_id: String,
    message_id: String,
    payload: Map<String, Value>,
    reference: Option<String>,
) -> io::Result<()> {
    if let Some(client) = state.clients.get_mut(&client_id) {
        client.posted_senders.insert(sender_id.clone());
    }
    let now = now_ms();
    let mut candidate = state.store.clone();
    candidate.prune_closed(now);
    if let Some(index) = candidate.find(&sender_id, &message_id) {
        // A duplicate touches retention metadata only. The accepted payload and lifecycle win.
        candidate.records[index].last_touched_ms = now;
        candidate.prune_closed(now);
        commit_store(state, candidate)?;
        return send_reply(state, client_id, ok_frame(reference, true));
    }

    let expired = candidate.make_room_for_new(&sender_id, now);

    let accepted_order = candidate.next_order;
    candidate.next_order = candidate.next_order.saturating_add(1);
    candidate.records.push(Record {
        sender_id: sender_id.clone(),
        message_id,
        payload,
        accepted_order,
        lifecycle: None,
        answer: Value::Null,
        last_touched_ms: now,
    });
    candidate.prune_closed(now);
    commit_store(state, candidate)?;

    send_reply(state, client_id, ok_frame(reference, false))?;
    for frame in expired {
        DELIVERY.lifecycle(state, &frame);
    }
    let record = state
        .store
        .records
        .last()
        .expect("accepted record was appended");
    DELIVERY.message(state, &sender_id, Value::Object(record.payload.clone()));
    Ok(())
}

fn handle_lifecycle(
    state: &mut ServerState,
    client_id: u64,
    sender_id: String,
    message_id: String,
    lifecycle_state: String,
    at: Value,
    by: Option<String>,
    reference: Option<String>,
) -> io::Result<()> {
    let Some(index) = state.store.find(&sender_id, &message_id) else {
        return send_reply(
            state,
            client_id,
            error_frame("unknown_message", None, reference),
        );
    };
    if state.store.records[index].lifecycle.is_some() {
        return send_reply(state, client_id, error_frame("closed", None, reference));
    }
    let now = now_ms();
    let mut candidate = state.store.clone();
    candidate.records[index].lifecycle = Some(LifecycleRecord {
        state: lifecycle_state,
        at,
        by,
    });
    candidate.records[index].last_touched_ms = now;
    let record = candidate.records[index].clone();
    candidate.prune_closed(now);
    commit_store(state, candidate)?;
    send_reply(state, client_id, ok_frame(reference, false))?;
    DELIVERY.lifecycle(state, &lifecycle_frame(&record));
    Ok(())
}

fn handle_subscribe(
    state: &mut ServerState,
    client_id: u64,
    sender_ids: HashSet<String>,
    reference: Option<String>,
) -> io::Result<()> {
    let client = state
        .clients
        .get_mut(&client_id)
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotConnected, "client disconnected"))?;
    client.is_client = true;
    client.subscriptions = sender_ids;
    send_reply(state, client_id, ok_frame(reference, false))?;
    let snapshot = DELIVERY.snapshot(state, client_id);
    send_reply(state, client_id, snapshot)
}

fn handle_answer(
    state: &mut ServerState,
    client_id: u64,
    sender_id: String,
    message_id: String,
    answer: Value,
    reference: Option<String>,
) -> io::Result<()> {
    let Some(index) = state.store.find(&sender_id, &message_id) else {
        return send_reply(
            state,
            client_id,
            error_frame("unknown_message", None, reference),
        );
    };
    if state.store.records[index].lifecycle.is_some() {
        return send_reply(state, client_id, error_frame("closed", None, reference));
    }
    let now = now_ms();
    let mut candidate = state.store.clone();
    candidate.records[index].answer = answer.clone();
    candidate.records[index].lifecycle = Some(LifecycleRecord {
        state: "answered".to_owned(),
        at: json!(now),
        by: Some("answer".to_owned()),
    });
    candidate.records[index].last_touched_ms = now;
    let record = candidate.records[index].clone();
    candidate.prune_closed(now);
    commit_store(state, candidate)?;
    send_reply(state, client_id, ok_frame(reference, false))?;
    DELIVERY.lifecycle(state, &lifecycle_frame(&record));

    let answer_frame = json!({
        "v": 1,
        "type": "answer",
        "sender_id": sender_id,
        "message_id": message_id,
        "answer": answer,
        "at": now,
    });
    let recipients: Vec<u64> = state
        .clients
        .iter()
        .filter_map(|(id, client)| {
            client
                .posted_senders
                .contains(&record.sender_id)
                .then_some(*id)
        })
        .collect();
    DELIVERY.answer(state, &recipients, &answer_frame);
    Ok(())
}

fn ok_frame(reference: Option<String>, duplicate: bool) -> Value {
    let mut frame = json!({"v": 1, "type": "ok"});
    let object = frame.as_object_mut().expect("object frame");
    if duplicate {
        object.insert("duplicate".to_owned(), json!(true));
    }
    if let Some(reference) = reference {
        object.insert("ref".to_owned(), json!(reference));
    }
    frame
}

fn error_frame(reason: &str, field: Option<String>, reference: Option<String>) -> Value {
    let mut frame = json!({"v": 1, "type": "error", "reason": reason});
    let object = frame.as_object_mut().expect("object frame");
    if let Some(field) = field {
        object.insert("field".to_owned(), json!(field));
    }
    if let Some(reference) = reference {
        object.insert("ref".to_owned(), json!(reference));
    }
    frame
}

fn error_value(error: WireError, reference: Option<String>) -> Value {
    error_frame(error.reason, error.field, reference)
}

fn lifecycle_frame(record: &Record) -> Value {
    let lifecycle = record
        .lifecycle
        .as_ref()
        .expect("lifecycle frame for closed record");
    let mut frame = json!({
        "v": 1,
        "type": "lifecycle",
        "sender_id": record.sender_id,
        "message_id": record.message_id,
        "state": lifecycle.state,
        "at": lifecycle.at,
    });
    if let Some(by) = &lifecycle.by {
        frame
            .as_object_mut()
            .expect("object frame")
            .insert("by".to_owned(), json!(by));
    }
    frame
}

fn send_error(
    shared: &Arc<Mutex<ServerState>>,
    client_id: u64,
    error: WireError,
    reference: Option<String>,
) -> io::Result<()> {
    let mut state = shared
        .lock()
        .map_err(|_| io::Error::other("attention state lock poisoned"))?;
    send_reply(&mut state, client_id, error_value(error, reference))
}

fn send_reply(state: &mut ServerState, client_id: u64, frame: Value) -> io::Result<()> {
    if let Some(client) = state.clients.get_mut(&client_id) {
        if write_frame(&mut client.writer, &frame).is_err() {
            state.clients.remove(&client_id);
        }
    }
    Ok(())
}

fn broadcast_to_ids(state: &mut ServerState, recipients: &[u64], frame: &Value) {
    for id in recipients {
        let _ = send_reply(state, *id, frame.clone());
    }
}

trait DeliverySeam {
    fn message(&self, state: &mut ServerState, sender_id: &str, message: Value);
    fn lifecycle(&self, state: &mut ServerState, frame: &Value);
    fn answer(&self, state: &mut ServerState, recipients: &[u64], frame: &Value);
    fn snapshot(&self, state: &ServerState, client_id: u64) -> Value;
}

struct LocalFirehose;

static DELIVERY: LocalFirehose = LocalFirehose;

impl DeliverySeam for LocalFirehose {
    fn message(&self, state: &mut ServerState, sender_id: &str, message: Value) {
        let recipients: Vec<u64> = state
            .clients
            .iter()
            .filter_map(|(id, client)| client.is_client.then_some(*id))
            .collect();
        for id in recipients {
            let Some(client) = state.clients.get(&id) else {
                continue;
            };
            let kind = if client.subscriptions.contains(sender_id) {
                "message"
            } else {
                "knock"
            };
            let frame = json!({
                "v": 1,
                "type": "delivery",
                "as": kind,
                "message": message,
            });
            let _ = send_reply(state, id, frame);
        }
    }

    fn lifecycle(&self, state: &mut ServerState, frame: &Value) {
        let recipients: Vec<u64> = state
            .clients
            .iter()
            .filter_map(|(id, client)| client.is_client.then_some(*id))
            .collect();
        broadcast_to_ids(state, &recipients, frame);
    }

    fn answer(&self, state: &mut ServerState, recipients: &[u64], frame: &Value) {
        broadcast_to_ids(state, recipients, frame);
    }

    fn snapshot(&self, state: &ServerState, client_id: u64) -> Value {
        let mut records: Vec<_> = state
            .store
            .records
            .iter()
            .filter(|record| record.lifecycle.is_none())
            .collect();
        records.sort_by_key(|record| record.accepted_order);
        let messages = records
            .into_iter()
            .map(|record| {
                let kind = state
                    .clients
                    .get(&client_id)
                    .filter(|client| client.subscriptions.contains(&record.sender_id))
                    .map_or("knock", |_| "message");
                json!({"as": kind, "message": Value::Object(record.payload.clone())})
            })
            .collect::<Vec<_>>();
        json!({"v": 1, "type": "snapshot", "messages": messages})
    }
}

fn write_frame(writer: &mut UnixStream, frame: &Value) -> io::Result<()> {
    serde_json::to_writer(&mut *writer, frame).map_err(io::Error::other)?;
    writer.write_all(b"\n")?;
    writer.flush()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct Store {
    version: u32,
    next_order: u64,
    records: Vec<Record>,
}

impl Default for Store {
    fn default() -> Self {
        Self {
            version: 1,
            next_order: 1,
            records: Vec::new(),
        }
    }
}

impl Store {
    fn load(path: &Path) -> io::Result<Self> {
        match fs::read(path) {
            Ok(bytes) => {
                let store: Self = serde_json::from_slice(&bytes).map_err(io::Error::other)?;
                if store.version != 1 {
                    return Err(io::Error::new(
                        io::ErrorKind::InvalidData,
                        format!("unsupported attention store version {}", store.version),
                    ));
                }
                Ok(store)
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(Self::default()),
            Err(error) => Err(error),
        }
    }

    fn find(&self, sender_id: &str, message_id: &str) -> Option<usize> {
        self.records
            .iter()
            .position(|record| record.sender_id == sender_id && record.message_id == message_id)
    }

    fn open_count(&self) -> usize {
        self.records
            .iter()
            .filter(|record| record.lifecycle.is_none())
            .count()
    }

    fn open_count_for_sender(&self, sender_id: &str) -> usize {
        self.records
            .iter()
            .filter(|record| record.sender_id == sender_id && record.lifecycle.is_none())
            .count()
    }

    fn prune_closed(&mut self, now: i64) -> bool {
        let old_len = self.records.len();
        self.records.retain(|record| {
            record.lifecycle.is_none()
                || now.saturating_sub(record.last_touched_ms) <= CLOSED_RETENTION_MS
        });
        let mut changed = self.records.len() != old_len;
        while self
            .records
            .iter()
            .filter(|record| record.lifecycle.is_some())
            .count()
            > MAX_CLOSED_TOTAL
        {
            let oldest = self
                .records
                .iter()
                .enumerate()
                .filter(|(_, record)| record.lifecycle.is_some())
                .min_by_key(|(_, record)| record.last_touched_ms)
                .map(|(index, _)| index)
                .expect("closed count is nonzero");
            self.records.remove(oldest);
            changed = true;
        }
        changed
    }

    fn expire_oldest_for_sender(&mut self, sender_id: &str, now: i64) -> Option<Value> {
        self.expire_oldest_matching(now, |record| {
            record.sender_id == sender_id && record.lifecycle.is_none()
        })
    }

    fn expire_oldest_any(&mut self, now: i64) -> Option<Value> {
        self.expire_oldest_matching(now, |record| record.lifecycle.is_none())
    }

    fn make_room_for_new(&mut self, sender_id: &str, now: i64) -> Vec<Value> {
        let mut expired = Vec::new();
        while self.open_count_for_sender(sender_id) >= MAX_OPEN_PER_SENDER {
            let Some(frame) = self.expire_oldest_for_sender(sender_id, now) else {
                break;
            };
            expired.push(frame);
        }
        while self.open_count() >= MAX_OPEN_TOTAL {
            let Some(frame) = self.expire_oldest_any(now) else {
                break;
            };
            expired.push(frame);
        }
        self.prune_closed(now);
        expired
    }

    fn expire_oldest_matching(
        &mut self,
        now: i64,
        predicate: impl Fn(&Record) -> bool,
    ) -> Option<Value> {
        let index = self
            .records
            .iter()
            .enumerate()
            .filter(|(_, record)| predicate(record))
            .min_by_key(|(_, record)| record.accepted_order)
            .map(|(index, _)| index)?;
        let record = &mut self.records[index];
        record.lifecycle = Some(LifecycleRecord {
            state: "expired".to_owned(),
            at: json!(now),
            by: Some("retention".to_owned()),
        });
        record.last_touched_ms = now;
        Some(lifecycle_frame(record))
    }

    fn normalize(&mut self, now: i64) -> bool {
        let mut changed = self.prune_closed(now);
        let mut senders = HashSet::new();
        for record in &self.records {
            if record.lifecycle.is_none() {
                senders.insert(record.sender_id.clone());
            }
        }
        let mut expired = false;
        for sender_id in senders {
            while self.open_count_for_sender(&sender_id) > MAX_OPEN_PER_SENDER {
                if self.expire_oldest_for_sender(&sender_id, now).is_none() {
                    break;
                }
                expired = true;
            }
        }
        while self.open_count() > MAX_OPEN_TOTAL {
            if self.expire_oldest_any(now).is_none() {
                break;
            }
            expired = true;
        }
        changed |= expired;
        changed |= self.prune_closed(now);
        if let Some(max_order) = self
            .records
            .iter()
            .map(|record| record.accepted_order)
            .max()
        {
            if self.next_order <= max_order {
                self.next_order = max_order.saturating_add(1);
                changed = true;
            }
        }
        changed
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct Record {
    sender_id: String,
    message_id: String,
    payload: Map<String, Value>,
    accepted_order: u64,
    #[serde(default)]
    lifecycle: Option<LifecycleRecord>,
    #[serde(default)]
    // `null` is a valid answer. Keep it as a Value so persistence distinguishes it
    // from an unanswered record through the lifecycle without losing the payload.
    answer: Value,
    last_touched_ms: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct LifecycleRecord {
    state: String,
    at: Value,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    by: Option<String>,
}

struct Client {
    writer: UnixStream,
    is_client: bool,
    subscriptions: HashSet<String>,
    posted_senders: HashSet<String>,
}

struct ServerState {
    store: Store,
    store_path: PathBuf,
    clients: HashMap<u64, Client>,
    next_client_id: u64,
}

impl ServerState {
    fn new(store: Store, store_path: PathBuf) -> Self {
        Self {
            store,
            store_path,
            clients: HashMap::new(),
            next_client_id: 1,
        }
    }

    fn add_client(&mut self, writer: UnixStream) -> u64 {
        let id = self.next_client_id;
        self.next_client_id = self.next_client_id.wrapping_add(1).max(1);
        self.clients.insert(
            id,
            Client {
                writer,
                is_client: false,
                subscriptions: HashSet::new(),
                posted_senders: HashSet::new(),
            },
        );
        id
    }
}

fn commit_store(state: &mut ServerState, candidate: Store) -> io::Result<()> {
    persist_store(&state.store_path, &candidate)?;
    state.store = candidate;
    Ok(())
}

fn persist_store(path: &Path, store: &Store) -> io::Result<()> {
    let bytes = serde_json::to_vec(store).map_err(io::Error::other)?;
    let parent = path
        .parent()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "store path has no parent"))?;
    let temporary = loop {
        let counter = TEMP_FILE_COUNTER.fetch_add(1, Ordering::Relaxed);
        let candidate = parent.join(format!(
            ".messages.json.tmp.{}.{}",
            std::process::id(),
            counter
        ));
        match OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&candidate)
        {
            Ok(mut file) => {
                file.write_all(&bytes)?;
                file.sync_all()?;
                break candidate;
            }
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error),
        }
    };
    fs::rename(&temporary, path)?;
    File::open(parent)?.sync_all()
}

fn now_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .min(i64::MAX as u128) as i64
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{BufRead, Cursor};
    use std::time::Duration;

    struct TempDir(PathBuf);

    impl TempDir {
        fn new() -> Self {
            let id = TEMP_FILE_COUNTER.fetch_add(1, Ordering::Relaxed);
            let path = env::temp_dir().join(format!("attentiond-test-{}-{id}", std::process::id()));
            let mut builder = fs::DirBuilder::new();
            builder.mode(0o700);
            builder.create(&path).expect("create test directory");
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

    fn add_connection(shared: &Arc<Mutex<ServerState>>) -> (u64, BufReader<UnixStream>) {
        let (server, client) = UnixStream::pair().expect("local socket pair");
        client
            .set_read_timeout(Some(Duration::from_millis(40)))
            .expect("set reader timeout");
        let id = shared.lock().expect("state lock").add_client(server);
        (id, BufReader::new(client))
    }

    fn read_json(reader: &mut BufReader<UnixStream>) -> Value {
        let mut line = String::new();
        let bytes = reader.read_line(&mut line).expect("read output frame");
        assert!(bytes > 0, "connection closed before a frame");
        serde_json::from_str(&line).expect("valid output JSON")
    }

    fn expect_no_frame(reader: &mut BufReader<UnixStream>) {
        let mut line = String::new();
        match reader.read_line(&mut line) {
            Err(error)
                if error.kind() == io::ErrorKind::WouldBlock
                    || error.kind() == io::ErrorKind::TimedOut => {}
            Ok(0) => panic!("connection closed instead of remaining idle"),
            Ok(_) => panic!("unexpected pushed frame: {line}"),
            Err(error) => panic!("unexpected read error: {error}"),
        }
    }

    fn open_record(sender_id: &str, message_id: &str, order: u64, touched: i64) -> Record {
        let payload = json!({
            "sender_id": sender_id,
            "message_id": message_id,
            "created_at": order,
            "title": message_id,
        });
        Record {
            sender_id: sender_id.to_owned(),
            message_id: message_id.to_owned(),
            payload: payload.as_object().expect("message object").clone(),
            accepted_order: order,
            lifecycle: None,
            answer: Value::Null,
            last_touched_ms: touched,
        }
    }

    #[test]
    fn protocol_validation_keeps_message_extensions_and_rejects_bad_versions() {
        let valid = json!({
            "v": 1,
            "type": "message",
            "ref": "first",
            "sender_id": "agentd://host/instance/17:900",
            "message_id": "need@1000",
            "created_at": 1000,
            "title": "Review",
            "color": {"tone": "red"},
        });
        let Envelope {
            request: Request::Message { payload, .. },
            reference,
        } = parse_request(&valid).expect("valid message")
        else {
            panic!("expected message request");
        };
        assert_eq!(reference.as_deref(), Some("first"));
        assert_eq!(payload.get("color"), Some(&json!({"tone":"red"})));
        assert!(!payload.contains_key("v"));
        assert!(!payload.contains_key("type"));
        assert!(!payload.contains_key("ref"));

        let error = parse_request(&json!({"v": 2, "type": "message", "ref": "v2"}))
            .expect_err("unsupported version");
        assert_eq!(error.reason, "unsupported_version");
        assert_eq!(reference_from(&json!({"ref":"v2"})).as_deref(), Some("v2"));

        let error = parse_request(&json!({"v": 1, "type": "hello"})).expect_err("unknown type");
        assert_eq!(error.reason, "unknown_type");
        assert_eq!(error.field, None);
        assert!(valid_sender_id(
            "urn:uuid:00000000-0000-4000-8000-000000000000"
        ));
        assert!(!valid_sender_id("Agentd://host/id"));
        assert!(!valid_sender_id("agentd://host/with space"));
    }

    #[test]
    fn oversized_line_is_drained_without_closing_the_connection() {
        let mut bytes = vec![b'x'; MAX_FRAME_BYTES + 1];
        bytes.extend_from_slice(b"\n{\"v\":1}\n");
        let mut reader = Cursor::new(bytes);
        assert!(matches!(
            read_frame(&mut reader).unwrap(),
            ReadFrame::TooLong
        ));
        assert!(
            matches!(read_frame(&mut reader).unwrap(), ReadFrame::Line(line) if line == b"{\"v\":1}")
        );
    }

    #[test]
    fn local_delivery_snapshot_dedup_lifecycle_answer_and_persistence() {
        let temp = TempDir::new();
        let store_path = temp.path().join("messages.json");
        let shared = Arc::new(Mutex::new(ServerState::new(
            Store::default(),
            store_path.clone(),
        )));
        let sender_id = "agentd://host/instance/17:900";
        let (client_a, mut reader_a) = add_connection(&shared);
        let (client_b, mut reader_b) = add_connection(&shared);
        let (sender, mut sender_reader) = add_connection(&shared);
        let (_unrelated, mut unrelated_reader) = add_connection(&shared);

        handle_value(
            &shared,
            client_a,
            json!({"v":1,"type":"subscribe","sender_ids":[sender_id]}),
        )
        .unwrap();
        assert_eq!(read_json(&mut reader_a)["type"], "ok");
        assert_eq!(read_json(&mut reader_a)["messages"], json!([]));
        handle_value(
            &shared,
            client_b,
            json!({"v":1,"type":"subscribe","sender_ids":[]}),
        )
        .unwrap();
        assert_eq!(read_json(&mut reader_b)["type"], "ok");
        assert_eq!(read_json(&mut reader_b)["messages"], json!([]));

        let first = json!({
            "v":1,"type":"message","ref":"m1",
            "sender_id":sender_id,"message_id":"need@1","created_at":1,
            "title":"First","by_when":0,"color":"red"
        });
        handle_value(&shared, sender, first.clone()).unwrap();
        assert_eq!(
            read_json(&mut sender_reader),
            json!({"v":1,"type":"ok","ref":"m1"})
        );
        let a_delivery = read_json(&mut reader_a);
        let b_delivery = read_json(&mut reader_b);
        assert_eq!(a_delivery["type"], "delivery");
        assert_eq!(a_delivery["as"], "message");
        assert_eq!(a_delivery["message"]["color"], "red");
        assert_eq!(b_delivery["as"], "knock");

        let mut changed_duplicate = first.clone();
        changed_duplicate["title"] = json!("Changed copy");
        handle_value(&shared, sender, changed_duplicate).unwrap();
        assert_eq!(
            read_json(&mut sender_reader),
            json!({"v":1,"type":"ok","duplicate":true,"ref":"m1"})
        );
        expect_no_frame(&mut reader_a);
        expect_no_frame(&mut reader_b);

        handle_value(
            &shared,
            sender,
            json!({
                "v":1,"type":"message","sender_id":sender_id,
                "message_id":"need@2","created_at":2,"title":"Second"
            }),
        )
        .unwrap();
        assert_eq!(read_json(&mut sender_reader)["type"], "ok");
        assert_eq!(read_json(&mut reader_a)["message"]["message_id"], "need@2");
        assert_eq!(read_json(&mut reader_b)["message"]["message_id"], "need@2");

        let (late_client, mut late_reader) = add_connection(&shared);
        handle_value(
            &shared,
            late_client,
            json!({"v":1,"type":"subscribe","sender_ids":[]}),
        )
        .unwrap();
        assert_eq!(read_json(&mut late_reader)["type"], "ok");
        let snapshot = read_json(&mut late_reader);
        assert_eq!(snapshot["type"], "snapshot");
        assert_eq!(snapshot["messages"].as_array().unwrap().len(), 2);
        assert_eq!(snapshot["messages"][0]["as"], "knock");

        handle_value(
            &shared,
            client_a,
            json!({"v":1,"type":"answer","sender_id":sender_id,"message_id":"need@1","answer":null}),
        )
        .unwrap();
        assert_eq!(read_json(&mut reader_a)["type"], "ok");
        assert_eq!(read_json(&mut reader_a)["state"], "answered");
        assert_eq!(read_json(&mut reader_b)["state"], "answered");
        assert_eq!(read_json(&mut late_reader)["state"], "answered");
        let answer = read_json(&mut sender_reader);
        assert_eq!(answer["type"], "answer");
        assert!(answer["answer"].is_null());
        expect_no_frame(&mut unrelated_reader);

        handle_value(
            &shared,
            sender,
            json!({"v":1,"type":"lifecycle","sender_id":sender_id,"message_id":"need@2","state":"cleared","at":3,"by":"bridge"}),
        )
        .unwrap();
        assert_eq!(read_json(&mut sender_reader)["type"], "ok");
        assert_eq!(read_json(&mut reader_a)["state"], "cleared");
        assert_eq!(read_json(&mut reader_b)["state"], "cleared");
        assert_eq!(read_json(&mut late_reader)["state"], "cleared");

        handle_value(
            &shared,
            client_a,
            json!({"v":1,"type":"answer","sender_id":sender_id,"message_id":"need@1","answer":true}),
        )
        .unwrap();
        assert_eq!(read_json(&mut reader_a)["reason"], "closed");
        handle_value(
            &shared,
            client_a,
            json!({"v":1,"type":"lifecycle","sender_id":sender_id,"message_id":"missing","state":"cleared","at":4}),
        )
        .unwrap();
        assert_eq!(read_json(&mut reader_a)["reason"], "unknown_message");

        handle_value(&shared, sender, first).unwrap();
        assert_eq!(
            read_json(&mut sender_reader),
            json!({"v":1,"type":"ok","duplicate":true,"ref":"m1"})
        );
        expect_no_frame(&mut reader_a);
        expect_no_frame(&mut reader_b);
        expect_no_frame(&mut late_reader);

        let loaded = Store::load(&store_path).expect("persistent store reload");
        let first = &loaded.records[loaded.find(sender_id, "need@1").unwrap()];
        assert_eq!(first.payload.get("title"), Some(&json!("First")));
        assert_eq!(first.answer, Value::Null);
        assert_eq!(first.lifecycle.as_ref().unwrap().state, "answered");
        let second = &loaded.records[loaded.find(sender_id, "need@2").unwrap()];
        assert_eq!(
            second.lifecycle.as_ref().unwrap().by.as_deref(),
            Some("bridge")
        );
    }

    #[test]
    fn capacity_expires_oldest_open_message_and_closed_retention_is_bounded() {
        let mut per_sender = Store::default();
        for order in 1..=MAX_OPEN_PER_SENDER as u64 {
            per_sender.records.push(open_record(
                "app://host/editor",
                &format!("m{order}"),
                order,
                0,
            ));
        }
        per_sender.next_order = MAX_OPEN_PER_SENDER as u64 + 1;
        let evicted = per_sender.make_room_for_new("app://host/editor", 10);
        assert_eq!(evicted.len(), 1);
        assert_eq!(evicted[0]["message_id"], "m1");
        assert_eq!(evicted[0]["state"], "expired");
        assert_eq!(evicted[0]["by"], "retention");
        assert_eq!(per_sender.open_count_for_sender("app://host/editor"), 99);

        let mut total = Store::default();
        for order in 0..MAX_OPEN_TOTAL as u64 {
            let sender = format!("app://host/{order}");
            total.records.push(open_record(&sender, "m", order + 1, 0));
        }
        let evicted = total.make_room_for_new("app://host/new", 10);
        assert_eq!(evicted.len(), 1);
        assert_eq!(evicted[0]["sender_id"], "app://host/0");
        assert_eq!(total.open_count(), MAX_OPEN_TOTAL - 1);

        let now = 10_000;
        let mut closed = Store::default();
        for order in 0..=MAX_CLOSED_TOTAL {
            let mut record = open_record(
                "app://host/editor",
                &format!("closed-{order}"),
                order as u64,
                order as i64,
            );
            record.lifecycle = Some(LifecycleRecord {
                state: "cleared".to_owned(),
                at: json!(order),
                by: None,
            });
            closed.records.push(record);
        }
        assert!(closed.prune_closed(now));
        assert_eq!(closed.records.len(), MAX_CLOSED_TOTAL);
        assert!(closed.find("app://host/editor", "closed-0").is_none());
        assert!(closed.find("app://host/editor", "closed-10000").is_some());

        let mut aged = Store::default();
        let mut old = open_record("app://host/editor", "old", 1, 0);
        old.lifecycle = Some(LifecycleRecord {
            state: "cleared".to_owned(),
            at: json!(0),
            by: None,
        });
        aged.records.push(old);
        assert!(aged.prune_closed(CLOSED_RETENTION_MS + 1));
        assert!(aged.records.is_empty());
    }

    #[test]
    fn stale_socket_is_replaced_but_accepting_socket_is_untouched() {
        let temp = TempDir::new();
        let _lock = acquire_instance_lock(temp.path()).expect("first instance lock");
        let lock_error = acquire_instance_lock(temp.path()).expect_err("second instance must fail");
        assert_eq!(lock_error.kind(), io::ErrorKind::AddrInUse);
        let stale = temp.path().join("stale.sock");
        drop(UnixListener::bind(&stale).expect("bind stale socket"));
        assert!(fs::symlink_metadata(&stale).is_ok());
        prepare_socket_path(&stale).expect("replace stale socket");
        assert!(fs::symlink_metadata(&stale).is_err());

        let active = temp.path().join("active.sock");
        let _listener = UnixListener::bind(&active).expect("bind active socket");
        let error = prepare_socket_path(&active).expect_err("active socket must refuse");
        assert_eq!(error.kind(), io::ErrorKind::AddrInUse);
        assert!(
            fs::symlink_metadata(&active)
                .expect("active socket remains")
                .file_type()
                .is_socket()
        );
    }
}
