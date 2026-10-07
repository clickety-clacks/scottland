use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode, Stdio};
use std::thread;
use std::time::{SystemTime, UNIX_EPOCH};

const MAX_INPUT_BYTES: usize = 262_144;
const MAX_RESOLVER_OUTPUT_BYTES: usize = 1_048_576;
const MAX_WINDOWS: usize = 4096;

fn main() -> ExitCode {
    let arguments: Vec<String> = std::env::args().skip(1).collect();
    match arguments.as_slice() {
        [] => {}
        [flag] if flag == "--help" || flag == "-h" => {
            println!("usage: scottland-sender-window < request.json");
            println!(
                "Reads {{\"sender\": <resolver-v1 target>}} and reports matching windows as JSON."
            );
            return ExitCode::SUCCESS;
        }
        [flag] if flag == "--version" || flag == "-V" => {
            println!("scottland-sender-window {}", env!("CARGO_PKG_VERSION"));
            return ExitCode::SUCCESS;
        }
        _ => {
            eprintln!(
                "scottland-sender-window: this command accepts no arguments other than --help or --version"
            );
            return ExitCode::from(2);
        }
    }
    match run() {
        Ok(response) => {
            println!("{response}");
            ExitCode::SUCCESS
        }
        Err(message) => {
            eprintln!("scottland-sender-window: {message}");
            ExitCode::from(2)
        }
    }
}

fn run() -> Result<Value, String> {
    let input = read_input()?;
    let request: Value =
        serde_json::from_slice(&input).map_err(|error| format!("invalid JSON input: {error}"))?;
    let root = request
        .as_object()
        .ok_or_else(|| "input must be a JSON object containing sender".to_string())?;
    for key in root.keys() {
        if key != "sender" {
            return Err(format!(
                "unsupported input field {key:?}; only sender is accepted"
            ));
        }
    }
    let sender = root
        .get("sender")
        .ok_or_else(|| "missing sender resolver target".to_string())?;
    let has_tmux = validate_target(sender)?;

    let (windows, window_facts) = match collect_windows() {
        Ok(snapshot) => snapshot,
        Err(message) => {
            return Ok(unknown(
                "local_collection_incomplete",
                "scottland",
                &message,
            ));
        }
    };
    let local_machine = match local_machine() {
        Ok(machine) => machine,
        Err(message) => {
            return Ok(unknown("local_identity_unavailable", "scottland", &message));
        }
    };
    let resolver_dir = match resolver_dir() {
        Ok(path) => path,
        Err(message) => return Ok(unknown("resolver_unavailable", "resolver", &message)),
    };

    let relations = if has_tmux {
        vec!["visible_exact", "linked_client"]
    } else {
        vec!["visible_exact"]
    };
    let requests: Vec<(String, Value)> = relations
        .iter()
        .map(|relation| {
            (
                (*relation).to_string(),
                resolver_request(sender, &local_machine, &windows, relation),
            )
        })
        .collect();

    let mut handles = Vec::with_capacity(requests.len());
    for (relation, request) in requests {
        let resolver_dir = resolver_dir.clone();
        handles.push((
            relation,
            thread::spawn(move || call_resolver(&resolver_dir, &request)),
        ));
    }

    let mut results = BTreeMap::new();
    let mut failures = Vec::new();
    let mut invalid = None;
    for (relation, handle) in handles {
        let response = match handle.join() {
            Ok(Ok(response)) => response,
            Ok(Err(message)) => {
                failures.push(reason(
                    "resolver_unavailable",
                    "resolver",
                    &format!("{relation} resolution failed: {message}"),
                    true,
                ));
                continue;
            }
            Err(_) => {
                failures.push(reason(
                    "resolver_failure",
                    "resolver",
                    &format!("{relation} resolver process did not complete"),
                    true,
                ));
                continue;
            }
        };
        match parse_relation(&response, &relation, &window_facts) {
            Ok(result) => {
                results.insert(relation, result);
            }
            Err(ResolutionError::Invalid(message)) => invalid = Some(message),
            Err(ResolutionError::Unknown(message)) => failures.push(reason(
                "resolver_protocol_error",
                "resolver",
                &message,
                true,
            )),
        }
    }
    if let Some(message) = invalid {
        return Err(message);
    }
    if !failures.is_empty() {
        return Ok(unknown_from_reasons(failures));
    }

    let shows = results
        .get("visible_exact")
        .ok_or_else(|| "resolver omitted visible_exact result".to_string())?;
    let linked = results.get("linked_client");
    Ok(format_result(shows, linked))
}

fn read_input() -> Result<Vec<u8>, String> {
    let mut bytes = Vec::new();
    io::stdin()
        .take((MAX_INPUT_BYTES + 1) as u64)
        .read_to_end(&mut bytes)
        .map_err(|error| format!("could not read stdin: {error}"))?;
    if bytes.len() > MAX_INPUT_BYTES {
        return Err(format!("input exceeds {MAX_INPUT_BYTES} bytes"));
    }
    Ok(bytes)
}

fn validate_target(target: &Value) -> Result<bool, String> {
    let object = target
        .as_object()
        .ok_or_else(|| "sender must be a resolver-v1 target object".to_string())?;
    reject_unknown_keys(object, &["identity", "tmux", "name"], "sender")?;

    let identity = object
        .get("identity")
        .and_then(Value::as_object)
        .ok_or_else(|| "sender.identity must be an object".to_string())?;
    reject_unknown_keys(
        identity,
        &["machine", "instanceId", "pid", "startTimeTicks"],
        "sender.identity",
    )?;
    validate_text_field(identity, "machine", "sender.identity.machine", 255)?;
    validate_text_field(identity, "instanceId", "sender.identity.instanceId", 512)?;
    let pid = identity
        .get("pid")
        .and_then(Value::as_u64)
        .ok_or_else(|| "sender.identity.pid must be a positive integer".to_string())?;
    if pid == 0 || pid > i32::MAX as u64 {
        return Err("sender.identity.pid is outside the resolver-v1 range".to_string());
    }
    let ticks = identity
        .get("startTimeTicks")
        .and_then(Value::as_str)
        .ok_or_else(|| {
            "sender.identity.startTimeTicks must be a canonical decimal string".to_string()
        })?;
    if !canonical_ticks(ticks) {
        return Err(
            "sender.identity.startTimeTicks must be a canonical unsigned decimal string"
                .to_string(),
        );
    }

    if let Some(name) = object.get("name") {
        validate_text_value(name, "sender.name", 512)?;
    }
    if let Some(tmux) = object.get("tmux") {
        validate_tmux(tmux)?;
        Ok(true)
    } else {
        Ok(false)
    }
}

fn validate_tmux(value: &Value) -> Result<(), String> {
    let object = value
        .as_object()
        .ok_or_else(|| "sender.tmux must be an object".to_string())?;
    reject_unknown_keys(
        object,
        &["session", "windowIndex", "paneId", "socket"],
        "sender.tmux",
    )?;
    validate_text_field(object, "session", "sender.tmux.session", 256)?;
    let index = object
        .get("windowIndex")
        .and_then(Value::as_str)
        .ok_or_else(|| "sender.tmux.windowIndex must be a decimal string".to_string())?;
    if index.is_empty()
        || index.len() > 9
        || !index.bytes().all(|byte| byte.is_ascii_digit())
        || (index.len() > 1 && index.starts_with('0'))
    {
        return Err("sender.tmux.windowIndex is invalid".to_string());
    }
    let pane = object
        .get("paneId")
        .and_then(Value::as_str)
        .ok_or_else(|| "sender.tmux.paneId must be a string".to_string())?;
    let pane_digits = pane.strip_prefix('%').unwrap_or("");
    if pane_digits.is_empty()
        || pane_digits.len() > 12
        || !pane_digits.bytes().all(|byte| byte.is_ascii_digit())
    {
        return Err("sender.tmux.paneId is invalid".to_string());
    }
    if let Some(socket) = object.get("socket") {
        validate_socket(socket)?;
    }
    Ok(())
}

fn validate_socket(value: &Value) -> Result<(), String> {
    let object = value
        .as_object()
        .ok_or_else(|| "sender.tmux.socket must be an object".to_string())?;
    reject_unknown_keys(object, &["kind", "value"], "sender.tmux.socket")?;
    let kind = object
        .get("kind")
        .and_then(Value::as_str)
        .ok_or_else(|| "sender.tmux.socket.kind must be name or path".to_string())?;
    let socket_value = object
        .get("value")
        .and_then(Value::as_str)
        .ok_or_else(|| "sender.tmux.socket.value must be a string".to_string())?;
    match kind {
        "name"
            if !socket_value.is_empty()
                && socket_value.len() <= 128
                && !socket_value.contains('/')
                && safe_text(socket_value) =>
        {
            Ok(())
        }
        "path"
            if socket_value.starts_with('/')
                && socket_value.len() >= 2
                && socket_value.len() <= 4096
                && safe_text(socket_value) =>
        {
            Ok(())
        }
        "name" | "path" => Err("sender.tmux.socket.value is invalid for its kind".to_string()),
        _ => Err("sender.tmux.socket.kind must be name or path".to_string()),
    }
}

fn reject_unknown_keys(
    object: &serde_json::Map<String, Value>,
    allowed: &[&str],
    path: &str,
) -> Result<(), String> {
    if let Some(key) = object.keys().find(|key| !allowed.contains(&key.as_str())) {
        return Err(format!("{path} has unsupported field {key:?}"));
    }
    Ok(())
}

fn validate_text_field(
    object: &serde_json::Map<String, Value>,
    field: &str,
    path: &str,
    maximum: usize,
) -> Result<(), String> {
    let value = object.get(field).ok_or_else(|| format!("missing {path}"))?;
    validate_text_value(value, path, maximum)
}

fn validate_text_value(value: &Value, path: &str, maximum: usize) -> Result<(), String> {
    let text = value
        .as_str()
        .ok_or_else(|| format!("{path} must be a string"))?;
    if text.is_empty() || text.len() > maximum || !safe_text(text) {
        return Err(format!("{path} is invalid"));
    }
    Ok(())
}

fn safe_text(text: &str) -> bool {
    !text.chars().any(char::is_control)
}

fn canonical_ticks(value: &str) -> bool {
    if value.is_empty()
        || value.len() > 20
        || !value.bytes().all(|byte| byte.is_ascii_digit())
        || (value.len() > 1 && value.starts_with('0'))
    {
        return false;
    }
    value.parse::<u64>().is_ok()
}

#[derive(Clone, Debug)]
struct WindowFact {
    id: u64,
    address: String,
    pid: u64,
    ticks: String,
}

fn collect_windows() -> Result<(Vec<Value>, BTreeMap<String, WindowFact>), String> {
    // Select a recorded Scottland environment even when the caller belongs to another Wayfire
    // session. The helper's liveness probe is bounded; the Rust session library is not used here.
    let output = Command::new("scottland-exec")
        .args(["--", "scottland-ctl", "sender-window-snapshot"])
        .output()
        .map_err(|error| format!("could not query the Scottland window list: {error}"))?;
    if !output.status.success() {
        return Err(format!(
            "Scottland window collection failed: {}",
            diagnostic(&output.stderr)
        ));
    }
    let snapshot: Value = serde_json::from_slice(&output.stdout)
        .map_err(|error| format!("Scottland returned invalid window JSON: {error}"))?;
    let rows = snapshot
        .as_array()
        .or_else(|| snapshot.get("views").and_then(Value::as_array))
        .ok_or_else(|| "Scottland window snapshot has no views array".to_string())?;
    if rows.len() > MAX_WINDOWS {
        return Err(format!(
            "Scottland returned more than {MAX_WINDOWS} windows"
        ));
    }

    let mut windows = Vec::with_capacity(rows.len());
    let mut facts = BTreeMap::new();
    let mut ids = BTreeSet::new();
    for row in rows {
        let object = row
            .as_object()
            .ok_or_else(|| "Scottland returned a malformed window row".to_string())?;
        let id = object
            .get("id")
            .and_then(Value::as_u64)
            .filter(|id| *id > 0)
            .ok_or_else(|| "a Scottland window has no valid numeric id".to_string())?;
        if !ids.insert(id) {
            return Err(format!("Scottland returned duplicate window id {id}"));
        }
        let pid = object
            .get("pid")
            .and_then(Value::as_u64)
            .filter(|pid| *pid > 0 && *pid <= i32::MAX as u64)
            .ok_or_else(|| format!("window {id} has no valid process id"))?;
        let ticks = read_start_ticks(pid)
            .map_err(|message| format!("window {id} process identity is incomplete: {message}"))?;
        let class = object
            .get("app-id")
            .or_else(|| object.get("app_id"))
            .and_then(Value::as_str)
            .ok_or_else(|| format!("window {id} has no app-id"))?;
        let title = object
            .get("title")
            .and_then(Value::as_str)
            .ok_or_else(|| format!("window {id} has no title"))?;
        if class.len() > 256 || title.len() > 512 || !safe_text(class) || !safe_text(title) {
            return Err(format!("window {id} has invalid app-id or title text"));
        }

        let stable_id = id.to_string();
        let address = format!("0x{id:x}");
        let fact = WindowFact {
            id,
            address: address.clone(),
            pid,
            ticks: ticks.clone(),
        };
        windows.push(json!({
            "stableId": stable_id,
            "address": address,
            "pid": pid,
            "startTimeTicks": ticks,
            "class": class,
            "title": title,
        }));
        facts.insert(stable_id, fact);
    }
    Ok((windows, facts))
}

fn read_start_ticks(pid: u64) -> Result<String, String> {
    let path = format!("/proc/{pid}/stat");
    let stat = fs::read(path).map_err(|error| error.to_string())?;
    parse_start_ticks(pid, &stat)
}

fn parse_start_ticks(pid: u64, stat: &[u8]) -> Result<String, String> {
    let open = stat
        .iter()
        .position(|byte| *byte == b'(')
        .ok_or_else(|| "process stat has no command field".to_string())?;
    let actual_pid = std::str::from_utf8(&stat[..open])
        .ok()
        .and_then(|value| value.trim().parse::<u64>().ok());
    if actual_pid != Some(pid) {
        return Err("process stat pid changed during collection".to_string());
    }
    let close = stat
        .iter()
        .rposition(|byte| *byte == b')')
        .filter(|close| *close > open)
        .ok_or_else(|| "process stat has no closing command delimiter".to_string())?;
    let fields: Vec<&[u8]> = stat[close + 1..]
        .split(|byte| byte.is_ascii_whitespace())
        .filter(|field| !field.is_empty())
        .collect();
    // The suffix begins at field 3 (state); starttime is field 22.
    let ticks = fields
        .get(19)
        .and_then(|field| std::str::from_utf8(field).ok())
        .ok_or_else(|| "process stat has no starttime field".to_string())?;
    if !canonical_ticks(ticks) {
        return Err("process stat starttime is not canonical unsigned decimal".to_string());
    }
    Ok(ticks.to_string())
}

fn local_machine() -> Result<String, String> {
    let output = Command::new("hostname")
        .output()
        .map_err(|error| format!("could not read the local host name: {error}"))?;
    if !output.status.success() {
        return Err("hostname command failed".to_string());
    }
    let machine = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if machine.is_empty() || machine.len() > 255 || !safe_text(&machine) {
        return Err("hostname returned an invalid local host name".to_string());
    }
    Ok(machine)
}

fn resolver_dir() -> Result<PathBuf, String> {
    let executable = std::env::current_exe()
        .map_err(|error| format!("could not locate this executable: {error}"))?;
    let bin_dir = executable
        .parent()
        .ok_or_else(|| "sender-window executable has no parent directory".to_string())?;
    let candidates = [
        bin_dir.join("../lib/agent_window_resolver"),
        bin_dir.join("agent_window_resolver"),
    ];
    candidates
        .into_iter()
        .map(|path| path.canonicalize().unwrap_or(path))
        .find(|path| path.join("cli.py").is_file())
        .ok_or_else(|| "vendored agent-window-resolver v1 is missing".to_string())
}

fn resolver_request(
    target: &Value,
    local_machine: &str,
    windows: &[Value],
    relation: &str,
) -> Value {
    json!({
        "schema": "agent-window-resolver.request.v1",
        "requestId": request_id(relation),
        "operation": "resolve",
        "requestedRelation": relation,
        "target": target,
        "local": { "machine": local_machine },
        "windows": windows,
    })
}

fn request_id(relation: &str) -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or_default();
    format!(
        "scottland-sender-window-{}-{nanos}-{relation}",
        std::process::id()
    )
}

fn call_resolver(resolver_dir: &Path, request: &Value) -> Result<Value, String> {
    let library_dir = resolver_dir
        .parent()
        .ok_or_else(|| "resolver package has no parent directory".to_string())?;
    let mut child = Command::new("python3")
        .args(["-m", "agent_window_resolver.cli"])
        .env("PYTHONPATH", library_dir)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| format!("could not start the vendored resolver: {error}"))?;
    let request_bytes = serde_json::to_vec(request)
        .map_err(|error| format!("could not encode resolver request: {error}"))?;
    let mut stdin = child
        .stdin
        .take()
        .ok_or_else(|| "resolver stdin was not available".to_string())?;
    stdin
        .write_all(&request_bytes)
        .and_then(|_| stdin.write_all(b"\n"))
        .map_err(|error| format!("could not send resolver request: {error}"))?;
    drop(stdin);
    let output = child
        .wait_with_output()
        .map_err(|error| format!("could not read resolver response: {error}"))?;
    if output.stdout.len() > MAX_RESOLVER_OUTPUT_BYTES {
        return Err("resolver response exceeded the v1 output limit".to_string());
    }
    serde_json::from_slice(&output.stdout).map_err(|error| {
        let diagnostic = diagnostic(&output.stderr);
        if diagnostic.is_empty() {
            format!("resolver returned invalid JSON: {error}")
        } else {
            format!("resolver returned invalid JSON: {error}; {diagnostic}")
        }
    })
}

#[derive(Debug)]
enum ResolutionError {
    Invalid(String),
    Unknown(String),
}

#[derive(Debug)]
struct RelationResult {
    complete: bool,
    windows: BTreeSet<u64>,
    reasons: Vec<Value>,
}

fn parse_relation(
    response: &Value,
    relation: &str,
    window_facts: &BTreeMap<String, WindowFact>,
) -> Result<RelationResult, ResolutionError> {
    if response.get("status").and_then(Value::as_str) == Some("invalid") {
        return Err(ResolutionError::Invalid(format!(
            "resolver rejected sender target: {}",
            reason_text(response)
        )));
    }
    if response.get("schema").and_then(Value::as_str) != Some("agent-window-resolver.response.v1")
        || response.get("operation").and_then(Value::as_str) != Some("resolve")
        || response.get("requestedRelation").and_then(Value::as_str) != Some(relation)
    {
        return Err(ResolutionError::Unknown(
            "resolver response did not match the requested v1 relation".to_string(),
        ));
    }
    let status = response
        .get("status")
        .and_then(Value::as_str)
        .ok_or_else(|| ResolutionError::Unknown("resolver response has no status".to_string()))?;
    let reasons = response
        .get("reasons")
        .and_then(Value::as_array)
        .cloned()
        .ok_or_else(|| {
            ResolutionError::Unknown("resolver response has no reasons array".to_string())
        })?;
    let candidates = response
        .get("candidates")
        .and_then(Value::as_array)
        .ok_or_else(|| {
            ResolutionError::Unknown("resolver response has no candidates array".to_string())
        })?;
    let mut windows = BTreeSet::new();
    for candidate in candidates {
        let window = candidate.get("window").ok_or_else(|| {
            ResolutionError::Unknown("resolver candidate has no window identity".to_string())
        })?;
        let stable_id = window
            .get("stableId")
            .and_then(Value::as_str)
            .ok_or_else(|| {
                ResolutionError::Unknown("resolver candidate has no stable window id".to_string())
            })?;
        let fact = window_facts.get(stable_id).ok_or_else(|| {
            ResolutionError::Unknown(
                "resolver returned a window outside its input snapshot".to_string(),
            )
        })?;
        if window.get("address").and_then(Value::as_str) != Some(fact.address.as_str())
            || window.get("pid").and_then(Value::as_u64) != Some(fact.pid)
            || window.get("startTimeTicks").and_then(Value::as_str) != Some(fact.ticks.as_str())
        {
            return Err(ResolutionError::Unknown(
                "resolver returned a changed window identity".to_string(),
            ));
        }
        let proof = candidate.get("proof").ok_or_else(|| {
            ResolutionError::Unknown("resolver candidate has no proof".to_string())
        })?;
        if proof.get("state").and_then(Value::as_str) != Some("complete")
            || proof.get("relation").and_then(Value::as_str) != Some(relation)
        {
            return Err(ResolutionError::Unknown(
                "resolver returned a candidate without the requested complete proof".to_string(),
            ));
        }
        windows.insert(fact.id);
    }

    match status {
        "matched" if candidates.len() == 1 => Ok(RelationResult {
            complete: true,
            windows,
            reasons,
        }),
        "ambiguous" if candidates.len() > 1 => Ok(RelationResult {
            complete: true,
            windows,
            reasons,
        }),
        "unresolved" if candidates.is_empty() => {
            let complete = !reasons.is_empty()
                && reasons.iter().all(|reason| {
                    reason.get("code").and_then(Value::as_str) == Some("candidate_count")
                });
            Ok(RelationResult {
                complete,
                windows,
                reasons,
            })
        }
        "unreachable" if candidates.is_empty() => Ok(RelationResult {
            complete: false,
            windows,
            reasons,
        }),
        "matched" | "ambiguous" | "unresolved" | "unreachable" => Err(ResolutionError::Unknown(
            format!("resolver returned inconsistent {status} cardinality"),
        )),
        _ => Err(ResolutionError::Unknown(format!(
            "resolver returned unsupported status {status:?}"
        ))),
    }
}

fn format_result(shows: &RelationResult, linked: Option<&RelationResult>) -> Value {
    let mut results = vec![shows];
    if let Some(linked) = linked {
        results.push(linked);
    }
    let incomplete: Vec<&RelationResult> = results
        .iter()
        .copied()
        .filter(|result| !result.complete)
        .collect();
    if !incomplete.is_empty() {
        let mut reasons = Vec::new();
        for result in incomplete {
            extend_unique(&mut reasons, &result.reasons);
        }
        if reasons.is_empty() {
            reasons.push(reason(
                "local_collection_incomplete",
                "resolver",
                "the resolver could not complete every requested relation",
                true,
            ));
        }
        return unknown_from_reasons(reasons);
    }

    let show_ids = shows.windows.clone();
    let mut linked_ids = linked
        .map(|result| result.windows.clone())
        .unwrap_or_default();
    linked_ids.retain(|id| !show_ids.contains(id));
    let windows: Vec<Value> = show_ids
        .iter()
        .map(|id| json!({ "window": id, "relation": "shows" }))
        .chain(
            linked_ids
                .iter()
                .map(|id| json!({ "window": id, "relation": "linked" })),
        )
        .collect();
    if !windows.is_empty() {
        return json!({ "status": "found", "windows": windows, "reasons": [] });
    }

    let mut reasons = Vec::new();
    for result in results {
        extend_unique(&mut reasons, &result.reasons);
    }
    json!({ "status": "none", "windows": [], "reasons": reasons })
}

fn extend_unique(destination: &mut Vec<Value>, values: &[Value]) {
    for value in values {
        if !destination.contains(value) {
            destination.push(value.clone());
        }
    }
}

fn unknown(code: &str, source: &str, message: &str) -> Value {
    unknown_from_reasons(vec![reason(code, source, message, true)])
}

fn unknown_from_reasons(reasons: Vec<Value>) -> Value {
    json!({ "status": "unknown", "windows": [], "reasons": reasons })
}

fn reason(code: &str, source: &str, message: &str, retryable: bool) -> Value {
    json!({
        "code": code,
        "source": source,
        "message": message.chars().take(512).collect::<String>(),
        "retryable": retryable,
    })
}

fn reason_text(response: &Value) -> String {
    response
        .get("reasons")
        .and_then(Value::as_array)
        .map(|reasons| {
            reasons
                .iter()
                .map(|reason| {
                    reason
                        .get("message")
                        .and_then(Value::as_str)
                        .or_else(|| reason.get("code").and_then(Value::as_str))
                        .unwrap_or("invalid sender target")
                })
                .collect::<Vec<_>>()
                .join("; ")
        })
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "invalid sender target".to_string())
}

fn diagnostic(bytes: &[u8]) -> String {
    String::from_utf8_lossy(bytes)
        .trim()
        .chars()
        .take(512)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn complete(windows: &[u64], reason_code: &str) -> RelationResult {
        RelationResult {
            complete: true,
            windows: windows.iter().copied().collect(),
            reasons: vec![reason(reason_code, "caller", "test reason", false)],
        }
    }

    #[test]
    fn proc_stat_starttime_uses_field_22_after_the_last_parenthesis() {
        let mut fields = vec!["1".to_string(); 20];
        fields[0] = "S".to_string();
        fields[19] = "9127734".to_string();
        let stat = format!("48211 (agent ) with (parens)) {}", fields.join(" "));
        assert_eq!(
            parse_start_ticks(48211, stat.as_bytes()).unwrap(),
            "9127734"
        );
    }

    #[test]
    fn complete_results_return_every_show_then_link_in_id_order() {
        let shows = complete(&[10, 2], "candidate_count");
        let linked = complete(&[9, 2, 1], "candidate_count");
        let result = format_result(&shows, Some(&linked));
        assert_eq!(result["status"], "found");
        assert_eq!(
            result["windows"],
            json!([
                {"window": 2, "relation": "shows"},
                {"window": 10, "relation": "shows"},
                {"window": 1, "relation": "linked"},
                {"window": 9, "relation": "linked"}
            ])
        );
        assert_eq!(result["reasons"], json!([]));
    }

    #[test]
    fn incomplete_relation_suppresses_even_a_proven_partial_list() {
        let shows = complete(&[10], "candidate_count");
        let linked = RelationResult {
            complete: false,
            windows: BTreeSet::new(),
            reasons: vec![reason(
                "remote_unreachable",
                "transport",
                "host is unreachable",
                true,
            )],
        };
        let result = format_result(&shows, Some(&linked));
        assert_eq!(result["status"], "unknown");
        assert_eq!(result["windows"], json!([]));
        assert_eq!(result["reasons"][0]["code"], "remote_unreachable");
    }

    #[test]
    fn only_complete_no_match_results_return_none() {
        let shows = complete(&[], "candidate_count");
        let linked = complete(&[], "candidate_count");
        let result = format_result(&shows, Some(&linked));
        assert_eq!(result["status"], "none");
        assert_eq!(result["windows"], json!([]));
        assert_eq!(result["reasons"].as_array().unwrap().len(), 1);
    }

    #[test]
    fn sender_ticks_must_be_a_decimal_string() {
        let sender = json!({
            "identity": {
                "machine": "host",
                "instanceId": "agent-1",
                "pid": 10,
                "startTimeTicks": 123
            }
        });
        assert!(
            validate_target(&sender)
                .unwrap_err()
                .contains("sender.identity.startTimeTicks")
        );
    }

    #[test]
    fn accepts_the_resolver_v1_tmux_target_shape() {
        let sender = json!({
            "identity": {
                "machine": "example-host",
                "instanceId": "agent-7",
                "pid": 48211,
                "startTimeTicks": "9127734"
            },
            "tmux": {
                "session": "api",
                "windowIndex": "2",
                "paneId": "%14",
                "socket": {"kind": "name", "value": "agent"}
            }
        });
        assert_eq!(validate_target(&sender), Ok(true));
    }
}
