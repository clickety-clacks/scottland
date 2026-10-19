use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode, Stdio};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const MAX_INPUT_BYTES: usize = 262_144;
const MAX_RESOLVER_OUTPUT_BYTES: usize = 1_048_576;
const MAX_WINDOWS: usize = 4096;
const MAX_PID: u64 = 4_194_304;

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
        .ok_or_else(|| "missing sender".to_string())?;
    match sender {
        Value::String(sender_id) => run_sender_id(sender_id),
        Value::Object(_) => run_location(sender),
        _ => Err("sender must be a sender id string or resolver-v1 target object".to_string()),
    }
}

fn run_location(sender: &Value) -> Result<Value, String> {
    let (has_tmux, collection) = validate_target_then_collect(sender, collect_windows)?;
    let (windows, window_facts) = match collection {
        Ok(snapshot) => snapshot,
        Err(message) => {
            return Ok(unknown(
                "local_collection_incomplete",
                "scottland",
                &message,
            ));
        }
    };
    resolve_location(sender, has_tmux, &windows, &window_facts)
}

fn resolve_location(
    sender: &Value,
    has_tmux: bool,
    windows: &[Value],
    window_facts: &BTreeMap<String, WindowFact>,
) -> Result<Value, String> {
    let local_machine = match local_machine() {
        Ok(machine) => machine,
        Err(message) => return Ok(unknown("local_identity_unavailable", "scottland", &message)),
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
                resolver_request(sender, &local_machine, windows, relation),
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
        match parse_relation(&response, &relation, window_facts) {
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

enum RegistryLookup {
    Target(Value),
    Answer(Value),
}

fn run_sender_id(sender_id: &str) -> Result<Value, String> {
    if !valid_sender_id(sender_id) {
        return Err("sender must be a valid sender id".to_string());
    }
    let snapshot = match collect_provenance_windows() {
        Ok(snapshot) => snapshot,
        Err(message) => {
            return Ok(unknown(
                "local_collection_incomplete",
                "scottland",
                &message,
            ));
        }
    };
    let declared: BTreeSet<u64> = snapshot
        .declared_windows
        .iter()
        .filter(|(_, declared_sender)| declared_sender.as_str() == sender_id)
        .map(|(window, _)| *window)
        .collect();

    let lookup = match parse_agentd_sender_id(sender_id) {
        None => RegistryLookup::Answer(json!({"status": "none", "windows": [], "reasons": []})),
        Some(identity) => {
            let local_machine = match local_machine() {
                Ok(machine) => machine,
                Err(_) => {
                    return Ok(merge_declared_windows(
                        &declared,
                        unknown_registry(
                            "registry_unreachable",
                            "local machine identity is unavailable",
                        ),
                    ));
                }
            };
            if agentd_identity_matches_local_machine(&identity, &local_machine) {
                lookup_local_agentd(&identity)
            } else if !snapshot.hub_url_configured {
                RegistryLookup::Answer(unknown_registry(
                    "registry_unreachable",
                    "the Scottland Hub setting could not be read",
                ))
            } else if snapshot.hub_url.trim().is_empty() {
                RegistryLookup::Answer(unknown_registry(
                    "registry_not_configured",
                    "the Agentd Hub URL is not configured",
                ))
            } else {
                match fetch_hub_snapshot(&snapshot.hub_url) {
                    Ok(hub) => lookup_hub_agentd(&identity, &hub),
                    Err(_) => RegistryLookup::Answer(unknown_registry(
                        "registry_unreachable",
                        "the Agentd Hub snapshot could not be read",
                    )),
                }
            }
        }
    };

    let location = match lookup {
        RegistryLookup::Answer(answer) => answer,
        RegistryLookup::Target(target) => {
            let has_tmux = match validate_target(&target) {
                Ok(has_tmux) => has_tmux,
                Err(_) => {
                    return Ok(merge_declared_windows(
                        &declared,
                        unknown_registry(
                            "registry_incomplete",
                            "the Agentd entry cannot form a resolver target",
                        ),
                    ));
                }
            };
            if let Some(message) = snapshot.location_error.as_deref() {
                unknown("local_collection_incomplete", "scottland", message)
            } else {
                match resolve_location(&target, has_tmux, &snapshot.windows, &snapshot.window_facts)
                {
                    Ok(result) => result,
                    Err(_) => unknown_registry(
                        "registry_incomplete",
                        "the Agentd entry cannot form a resolver target",
                    ),
                }
            }
        }
    };
    Ok(merge_declared_windows(&declared, location))
}

fn unknown_registry(code: &str, message: &str) -> Value {
    unknown_from_reasons(vec![reason(code, "registry", message, true)])
}

fn none_agentd_not_listed() -> Value {
    json!({
        "status": "none",
        "windows": [],
        "reasons": [reason("agent_not_listed", "registry", "Agentd did not list this sender", false)]
    })
}

fn merge_declared_windows(declared: &BTreeSet<u64>, location: Value) -> Value {
    if declared.is_empty() {
        return location;
    }
    let mut by_window = BTreeMap::<u64, String>::new();
    for window in declared {
        by_window.insert(*window, "declared".to_string());
    }
    if let Some(rows) = location.get("windows").and_then(Value::as_array) {
        for row in rows {
            let Some(window) = row.get("window").and_then(Value::as_u64) else {
                continue;
            };
            let Some(relation) = row.get("relation").and_then(Value::as_str) else {
                continue;
            };
            by_window
                .entry(window)
                .or_insert_with(|| relation.to_string());
        }
    }
    let mut rows: Vec<(u8, u64, String)> = by_window
        .into_iter()
        .map(|(window, relation)| (relation_order(&relation), window, relation))
        .collect();
    rows.sort_by(|a, b| (a.0, a.1).cmp(&(b.0, b.1)));
    let windows: Vec<Value> = rows
        .into_iter()
        .map(|(_, window, relation)| json!({"window": window, "relation": relation}))
        .collect();
    let reasons = location
        .get("reasons")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    json!({"status": "found", "windows": windows, "reasons": reasons})
}

fn relation_order(relation: &str) -> u8 {
    match relation {
        "declared" => 0,
        "shows" => 1,
        "linked" => 2,
        "attributed" => 3,
        _ => 4,
    }
}

fn lookup_local_agentd(identity: &AgentdIdentity) -> RegistryLookup {
    let output = match Command::new("agentd").args(["list", "--json"]).output() {
        Ok(output) if output.status.success() => output,
        _ => {
            return RegistryLookup::Answer(unknown_registry(
                "registry_unreachable",
                "the local Agentd snapshot could not be read",
            ));
        }
    };
    let snapshot: Value = match serde_json::from_slice(&output.stdout) {
        Ok(snapshot) => snapshot,
        Err(_) => {
            return RegistryLookup::Answer(unknown_registry(
                "registry_unreachable",
                "the local Agentd snapshot is invalid",
            ));
        }
    };
    if snapshot.get("type").and_then(Value::as_str) != Some("snapshot")
        || snapshot.get("schema").and_then(Value::as_str) != Some("agentd.snapshot.v1")
    {
        return RegistryLookup::Answer(unknown_registry(
            "registry_unreachable",
            "the local Agentd snapshot schema is unavailable",
        ));
    }
    let Some(instance_id) = snapshot.get("instanceId").and_then(Value::as_str) else {
        return RegistryLookup::Answer(unknown_registry(
            "registry_incomplete",
            "the local Agentd snapshot has no instance id",
        ));
    };
    if snapshot.pointer("/scan/state").and_then(Value::as_str) != Some("complete") {
        return RegistryLookup::Answer(unknown_registry(
            "registry_incomplete",
            "the local Agentd scan is incomplete",
        ));
    }
    if instance_id != identity.instance_id {
        return RegistryLookup::Answer(none_agentd_not_listed());
    }
    let Some(agents) = snapshot.get("agents").and_then(Value::as_array) else {
        return RegistryLookup::Answer(unknown_registry(
            "registry_incomplete",
            "the local Agentd snapshot has no agent list",
        ));
    };
    let mut matched = None;
    for agent in agents {
        let Some(id) = agent.get("id").and_then(Value::as_object) else {
            return RegistryLookup::Answer(unknown_registry(
                "registry_incomplete",
                "a local Agentd entry has no process identity",
            ));
        };
        let (Some(pid), Some(ticks)) = (
            id.get("pid").and_then(Value::as_u64),
            id.get("startTimeTicks").and_then(Value::as_u64),
        ) else {
            return RegistryLookup::Answer(unknown_registry(
                "registry_incomplete",
                "a local Agentd entry lacks its process identity",
            ));
        };
        if pid == identity.pid as u64 && ticks == identity.start_time_ticks {
            if matched.is_some() {
                return RegistryLookup::Answer(unknown_registry(
                    "registry_incomplete",
                    "the local Agentd snapshot contains duplicate process identities",
                ));
            }
            matched = Some(agent);
        }
    }
    match matched {
        Some(agent) => match agent_to_resolver_target(agent, &identity.machine, instance_id) {
            Ok(target) => RegistryLookup::Target(target),
            Err(_) => RegistryLookup::Answer(unknown_registry(
                "registry_incomplete",
                "a local Agentd entry lacks a field required for window lookup",
            )),
        },
        None => RegistryLookup::Answer(none_agentd_not_listed()),
    }
}

fn lookup_hub_agentd(identity: &AgentdIdentity, hub: &Value) -> RegistryLookup {
    if hub.get("type").and_then(Value::as_str) != Some("snapshot")
        || hub.get("schema").and_then(Value::as_str) != Some("agentd-hub.snapshot.v1")
    {
        return RegistryLookup::Answer(unknown_registry(
            "registry_unreachable",
            "the Agentd Hub snapshot schema is unavailable",
        ));
    }
    let Some(sources) = hub.get("sources").and_then(Value::as_array) else {
        return RegistryLookup::Answer(unknown_registry(
            "registry_incomplete",
            "the Agentd Hub snapshot has no source list",
        ));
    };
    let matching_sources: Vec<&Value> = sources
        .iter()
        .filter(|source| {
            source
                .get("machine")
                .and_then(Value::as_str)
                .map_or(false, |machine| {
                    let machine = canonical_machine(machine);
                    machine_matches_canonical(&machine, &identity.machine)
                })
        })
        .collect();
    if matching_sources.len() != 1 {
        return RegistryLookup::Answer(unknown_registry(
            "registry_incomplete",
            "the Agentd Hub has no unique source for this machine",
        ));
    }
    let source = matching_sources[0];
    if source.pointer("/health/state").and_then(Value::as_str) != Some("reporting") {
        return RegistryLookup::Answer(unknown_registry(
            "registry_unreachable",
            "the Agentd Hub source is not reporting",
        ));
    }
    if source.pointer("/scan/state").and_then(Value::as_str) != Some("complete") {
        return RegistryLookup::Answer(unknown_registry(
            "registry_incomplete",
            "the Agentd Hub source scan is incomplete",
        ));
    }
    let Some(source_instance_id) = source.get("instanceId").and_then(Value::as_str) else {
        return RegistryLookup::Answer(unknown_registry(
            "registry_incomplete",
            "the Agentd Hub source has no instance id",
        ));
    };
    if source_instance_id != identity.instance_id {
        return RegistryLookup::Answer(none_agentd_not_listed());
    }
    let Some(agents) = hub.get("agents").and_then(Value::as_array) else {
        return RegistryLookup::Answer(unknown_registry(
            "registry_incomplete",
            "the Agentd Hub snapshot has no agent list",
        ));
    };
    let mut matched = None;
    for agent in agents {
        let Some(machine) = agent.get("machine").and_then(Value::as_str) else {
            return RegistryLookup::Answer(unknown_registry(
                "registry_incomplete",
                "an Agentd Hub entry has no machine",
            ));
        };
        let machine = canonical_machine(machine);
        if !machine_matches_canonical(&machine, &identity.machine) {
            continue;
        }
        let Some(instance_id) = agent.get("instanceId").and_then(Value::as_str) else {
            return RegistryLookup::Answer(unknown_registry(
                "registry_incomplete",
                "an Agentd Hub entry has no instance id",
            ));
        };
        if instance_id != identity.instance_id {
            continue;
        }
        let Some(id) = agent.get("id").and_then(Value::as_object) else {
            return RegistryLookup::Answer(unknown_registry(
                "registry_incomplete",
                "an Agentd Hub entry has no process identity",
            ));
        };
        let (Some(pid), Some(ticks)) = (
            id.get("pid").and_then(Value::as_u64),
            id.get("startTimeTicks").and_then(Value::as_u64),
        ) else {
            return RegistryLookup::Answer(unknown_registry(
                "registry_incomplete",
                "an Agentd Hub entry lacks its process identity",
            ));
        };
        if pid == identity.pid as u64 && ticks == identity.start_time_ticks {
            if matched.is_some() {
                return RegistryLookup::Answer(unknown_registry(
                    "registry_incomplete",
                    "the Agentd Hub contains duplicate process identities",
                ));
            }
            matched = Some(agent);
        }
    }
    match matched {
        Some(agent) => {
            match agent_to_resolver_target(agent, &identity.machine, &identity.instance_id) {
                Ok(target) => RegistryLookup::Target(target),
                Err(_) => RegistryLookup::Answer(unknown_registry(
                    "registry_incomplete",
                    "an Agentd Hub entry lacks a field required for window lookup",
                )),
            }
        }
        None => RegistryLookup::Answer(none_agentd_not_listed()),
    }
}

fn agent_to_resolver_target(
    agent: &Value,
    machine: &str,
    instance_id: &str,
) -> Result<Value, String> {
    let id = agent
        .get("id")
        .and_then(Value::as_object)
        .ok_or_else(|| "missing Agentd process identity".to_string())?;
    let pid = id
        .get("pid")
        .and_then(Value::as_u64)
        .filter(|pid| *pid > 0)
        .ok_or_else(|| "invalid Agentd process id".to_string())?;
    let ticks = id
        .get("startTimeTicks")
        .and_then(Value::as_u64)
        .filter(|ticks| *ticks > 0)
        .ok_or_else(|| "invalid Agentd start ticks".to_string())?;
    let mut target = json!({
        "identity": {
            "machine": machine,
            "instanceId": instance_id,
            "pid": pid,
            "startTimeTicks": ticks.to_string()
        }
    });
    if let Some(name) = agent.get("name") {
        if !name.is_null() {
            target["name"] = name.clone();
        }
    }
    if let Some(tmux) = agent.get("tmux") {
        if !tmux.is_null() {
            let object = tmux
                .as_object()
                .ok_or_else(|| "invalid Agentd tmux location".to_string())?;
            let session = object
                .get("session")
                .and_then(Value::as_str)
                .ok_or_else(|| "Agentd tmux location has no session".to_string())?;
            let window_index = object
                .get("windowIndex")
                .and_then(Value::as_u64)
                .ok_or_else(|| "Agentd tmux location has no window index".to_string())?;
            let pane_id = object
                .get("paneId")
                .and_then(Value::as_str)
                .ok_or_else(|| "Agentd tmux location has no pane id".to_string())?;
            target["tmux"] = json!({
                "session": session,
                "windowIndex": window_index.to_string(),
                "paneId": pane_id
            });
        }
    }
    Ok(target)
}

fn wait_for_child_stdout_until(
    child: &mut std::process::Child,
    deadline: Instant,
) -> Result<(std::process::ExitStatus, Vec<u8>), String> {
    let mut stdout = child
        .stdout
        .take()
        .ok_or_else(|| "the Agentd Hub response stream is unavailable".to_string())?;
    let reader = thread::spawn(move || {
        let mut bytes = Vec::new();
        stdout.read_to_end(&mut bytes).map(|_| bytes)
    });

    loop {
        let now = Instant::now();
        if now >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            let _ = reader.join();
            return Err("the Agentd Hub request exceeded its deadline".to_string());
        }

        match child.try_wait() {
            Ok(Some(status)) => {
                let bytes = reader
                    .join()
                    .map_err(|_| "the Agentd Hub response reader failed".to_string())?
                    .map_err(|_| "the Agentd Hub response could not be read".to_string())?;
                return Ok((status, bytes));
            }
            Ok(None) => {}
            Err(_) => {
                let _ = child.kill();
                let _ = child.wait();
                let _ = reader.join();
                return Err("the Agentd Hub request could not be monitored".to_string());
            }
        }

        thread::sleep((deadline - now).min(Duration::from_millis(10)));
    }
}

fn fetch_hub_snapshot(base_url: &str) -> Result<Value, String> {
    let deadline = Instant::now() + Duration::from_secs(3);
    const SCRIPT: &str = r#"
import json, sys, urllib.request, urllib.parse
try:
    base = json.load(sys.stdin)
    if not isinstance(base, str) or not base.strip():
        raise ValueError()
    endpoint = base.rstrip('/') + '/snapshot'
    if urllib.parse.urlsplit(endpoint).scheme != 'https':
        raise ValueError()
    request = urllib.request.Request(endpoint, headers={'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=3) as response:
        body = response.read()
    sys.stdout.buffer.write(body)
except Exception:
    raise SystemExit(2)
"#;
    let mut child = Command::new("python3")
        .args(["-c", SCRIPT])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|_| "python could not read the Agentd Hub".to_string())?;
    if let Some(mut stdin) = child.stdin.take() {
        if serde_json::to_writer(&mut stdin, base_url).is_err() {
            let _ = child.kill();
            let _ = child.wait();
            return Err("could not send the Agentd Hub URL to python".to_string());
        }
    }
    let (status, stdout) = wait_for_child_stdout_until(&mut child, deadline)?;
    if !status.success() {
        return Err("the Agentd Hub request failed".to_string());
    }
    serde_json::from_slice(&stdout).map_err(|_| "the Agentd Hub returned invalid JSON".to_string())
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

fn validate_target_then_collect<T>(
    target: &Value,
    collect: impl FnOnce() -> Result<T, String>,
) -> Result<(bool, Result<T, String>), String> {
    let has_tmux = validate_target(target)?;
    Ok((has_tmux, collect()))
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
    validate_machine_field(identity, "machine", "sender.identity.machine", 255)?;
    validate_text_field(identity, "instanceId", "sender.identity.instanceId", 512)?;
    let pid = identity
        .get("pid")
        .and_then(Value::as_u64)
        .ok_or_else(|| "sender.identity.pid must be a positive integer".to_string())?;
    if pid == 0 || pid > MAX_PID {
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
                && socket_value.chars().count() <= 128
                && !socket_value.contains('/')
                && safe_text(socket_value) =>
        {
            Ok(())
        }
        "path"
            if socket_value.starts_with('/')
                && socket_value.len() >= 2
                && socket_value.chars().count() <= 4096
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

fn validate_machine_field(
    object: &serde_json::Map<String, Value>,
    field: &str,
    path: &str,
    maximum: usize,
) -> Result<(), String> {
    let value = object.get(field).ok_or_else(|| format!("missing {path}"))?;
    let text = value
        .as_str()
        .ok_or_else(|| format!("{path} must be a string"))?;
    validate_text_value(value, path, maximum)?;
    if !valid_machine(text) {
        return Err(format!("{path} is invalid"));
    }
    Ok(())
}

fn validate_text_value(value: &Value, path: &str, maximum: usize) -> Result<(), String> {
    let text = value
        .as_str()
        .ok_or_else(|| format!("{path} must be a string"))?;
    if text.is_empty() || text.chars().count() > maximum || !safe_text(text) {
        return Err(format!("{path} is invalid"));
    }
    Ok(())
}

fn safe_text(text: &str) -> bool {
    !text.chars().any(|character| character.is_ascii_control())
}

fn valid_machine(machine: &str) -> bool {
    let mut bytes = machine.bytes();
    matches!(bytes.next(), Some(byte) if byte.is_ascii_alphanumeric())
        && bytes
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'.' | b':' | b'-'))
}

fn valid_sender_id(value: &str) -> bool {
    let Some((namespace, rest)) = value.split_once(':') else {
        return false;
    };
    let mut bytes = namespace.bytes();
    matches!(bytes.next(), Some(byte) if byte.is_ascii_lowercase())
        && bytes.all(|byte| {
            byte.is_ascii_lowercase() || byte.is_ascii_digit() || matches!(byte, b'+' | b'.' | b'-')
        })
        && !rest.is_empty()
        && !value
            .chars()
            .any(|character| character.is_whitespace() || character.is_control())
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct AgentdIdentity {
    machine: String,
    instance_id: String,
    pid: u32,
    start_time_ticks: u64,
}

fn parse_agentd_sender_id(value: &str) -> Option<AgentdIdentity> {
    let rest = value.strip_prefix("agentd://")?;
    let parts: Vec<&str> = rest.split('/').collect();
    if parts.len() != 3 || !valid_machine(parts[0]) || parts[1].is_empty() {
        return None;
    }
    if parts[1]
        .chars()
        .any(|character| character.is_whitespace() || character.is_control())
    {
        return None;
    }
    let (pid, ticks) = parts[2].split_once(':')?;
    if pid.is_empty()
        || !pid.bytes().all(|byte| byte.is_ascii_digit())
        || (pid.len() > 1 && pid.starts_with('0'))
        || !canonical_ticks(ticks)
    {
        return None;
    }
    let pid = pid.parse::<u32>().ok().filter(|pid| *pid > 0)?;
    let start_time_ticks = ticks.parse::<u64>().ok().filter(|ticks| *ticks > 0)?;
    Some(AgentdIdentity {
        machine: canonical_machine(parts[0]),
        instance_id: parts[1].to_string(),
        pid,
        start_time_ticks,
    })
}

fn canonical_machine(machine: &str) -> String {
    machine
        .strip_suffix('.')
        .unwrap_or(machine)
        .to_ascii_lowercase()
}

fn agentd_identity_matches_local_machine(identity: &AgentdIdentity, local_machine: &str) -> bool {
    let local_machine = canonical_machine(local_machine);
    machine_matches_canonical(&identity.machine, &local_machine)
}

fn machine_matches_canonical(left: &str, right: &str) -> bool {
    if left.is_empty() || right.is_empty() {
        return false;
    }
    if left == right {
        return true;
    }

    fn short_matches_full(short: &str, full: &str) -> bool {
        if short.contains('.') {
            return false;
        }
        let Some((first, suffix)) = full.split_once('.') else {
            return false;
        };
        first == short && !suffix.is_empty() && suffix.split('.').all(|label| !label.is_empty())
    }

    short_matches_full(&left, &right) || short_matches_full(&right, &left)
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

struct ProvenanceSnapshot {
    windows: Vec<Value>,
    window_facts: BTreeMap<String, WindowFact>,
    declared_windows: BTreeMap<u64, String>,
    location_error: Option<String>,
    hub_url: String,
    hub_url_configured: bool,
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
    collect_window_rows(rows)
}

fn collect_provenance_windows() -> Result<ProvenanceSnapshot, String> {
    let output = Command::new("scottland-exec")
        .args(["--", "scottland-ctl", "sender-window-provenance-snapshot"])
        .output()
        .map_err(|error| format!("could not query the Scottland desktop model: {error}"))?;
    if !output.status.success() {
        return Err(format!(
            "Scottland desktop-model collection failed: {}",
            diagnostic(&output.stderr)
        ));
    }
    let snapshot: Value = serde_json::from_slice(&output.stdout)
        .map_err(|error| format!("Scottland returned invalid desktop-model JSON: {error}"))?;
    let object = snapshot
        .as_object()
        .ok_or_else(|| "Scottland returned a malformed desktop model".to_string())?;
    let rows = object
        .get("windows")
        .and_then(Value::as_array)
        .ok_or_else(|| "Scottland desktop model has no windows array".to_string())?;
    let declared_windows = collect_declared_windows(rows)?;
    let (windows, window_facts, location_error) = match collect_window_rows(rows) {
        Ok((windows, facts)) => (windows, facts, None),
        Err(message) => (Vec::new(), BTreeMap::new(), Some(message)),
    };
    let hub_url = object
        .get("agentd_hub_url")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let hub_url_configured = object
        .get("agentd_hub_url_configured")
        .and_then(Value::as_bool)
        == Some(true);
    Ok(ProvenanceSnapshot {
        windows,
        window_facts,
        declared_windows,
        location_error,
        hub_url,
        hub_url_configured,
    })
}

fn collect_window_rows(
    rows: &[Value],
) -> Result<(Vec<Value>, BTreeMap<String, WindowFact>), String> {
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
        if class.chars().count() > 256
            || title.chars().count() > 512
            || !safe_text(class)
            || !safe_text(title)
        {
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

fn collect_declared_windows(rows: &[Value]) -> Result<BTreeMap<u64, String>, String> {
    if rows.len() > MAX_WINDOWS {
        return Err(format!(
            "Scottland returned more than {MAX_WINDOWS} windows"
        ));
    }
    let mut declared = BTreeMap::new();
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
        let Some(provenance) = object.get("provenance") else {
            continue;
        };
        let provenance = provenance
            .as_object()
            .ok_or_else(|| format!("window {id} has a malformed provenance record"))?;
        if provenance
            .get("launched_at")
            .and_then(Value::as_i64)
            .is_none()
        {
            return Err(format!("window {id} has an invalid provenance timestamp"));
        }
        let Some(sender) = provenance.get("sender") else {
            continue;
        };
        let sender = sender
            .as_str()
            .filter(|sender| valid_sender_id(sender))
            .ok_or_else(|| format!("window {id} has an invalid provenance sender"))?;
        declared.insert(id, sender.to_string());
    }
    Ok(declared)
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
    if machine.chars().count() > 255 || !safe_text(&machine) || !valid_machine(&machine) {
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
        .args(["-m", "agent_window_resolver"])
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
    use std::cell::Cell;

    fn resolver_v1_accepts_target(target: &Value) -> bool {
        let script = r#"
import json, sys
from agent_window_resolver.model import RequestError, parse_target
try:
    parse_target(json.load(sys.stdin))
except RequestError as error:
    print(error.code)
    raise SystemExit(3)
print("valid")
"#;
        let vendor = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../vendor");
        let mut child = Command::new("python3")
            .args(["-c", script])
            .env("PYTHONPATH", vendor)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .expect("python3 should be available for resolver-v1 parity tests");
        let mut stdin = child.stdin.take().expect("parity stdin is piped");
        serde_json::to_writer(&mut stdin, target).expect("target JSON should serialize");
        drop(stdin);
        let output = child
            .wait_with_output()
            .expect("parity process should exit");
        match output.status.code() {
            Some(0) => true,
            Some(3) => false,
            code => panic!(
                "resolver-v1 parity helper exited unexpectedly ({code:?}): {}",
                String::from_utf8_lossy(&output.stderr)
            ),
        }
    }

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
    fn target_validation_matches_resolver_v1_unicode_and_boundary_rules() {
        let sender = json!({
            "identity": {
                "machine": "example-host",
                "instanceId": format!("{}\u{0085}", "é".repeat(299)),
                "pid": MAX_PID,
                "startTimeTicks": "1"
            },
            "name": "🚀".repeat(512),
            "tmux": {
                "session": "界".repeat(200),
                "windowIndex": "0",
                "paneId": "%1",
                "socket": {"kind": "name", "value": "é".repeat(128)}
            }
        });

        assert_eq!(validate_target(&sender), Ok(true));
        assert!(resolver_v1_accepts_target(&sender));
    }

    #[test]
    fn malformed_targets_match_resolver_v1_and_precede_unavailable_collection() {
        let invalid_targets = [
            json!({
                "identity": {
                    "machine": "bad host",
                    "instanceId": "sender",
                    "pid": 1,
                    "startTimeTicks": "1"
                }
            }),
            json!({
                "identity": {
                    "machine": "example-host",
                    "instanceId": "sender",
                    "pid": MAX_PID + 1,
                    "startTimeTicks": "1"
                }
            }),
        ];

        for sender in invalid_targets {
            assert!(validate_target(&sender).is_err());
            assert!(!resolver_v1_accepts_target(&sender));

            let collection_called = Cell::new(false);
            let result = validate_target_then_collect(&sender, || {
                collection_called.set(true);
                Err::<(), _>("window collection is unavailable".to_string())
            });
            assert!(result.is_err());
            assert!(!collection_called.get());
        }
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

    #[test]
    fn sender_ids_reject_missing_namespaces_and_unicode_whitespace() {
        assert!(valid_sender_id("agentd://example-host/inst-7/48211:9127734"));
        assert!(valid_sender_id(
            "urn:uuid:00000000-0000-4000-8000-000000000000"
        ));
        assert!(!valid_sender_id("deploy"));
        assert!(!valid_sender_id("sender:with\u{00a0}space"));
        assert!(!valid_sender_id("sender:with\u{0085}control"));
    }

    #[test]
    fn agentd_sender_id_requires_four_canonical_identity_parts() {
        assert_eq!(
            parse_agentd_sender_id("agentd://example-host/inst-7/48211:9127734"),
            Some(AgentdIdentity {
                machine: "example-host".to_string(),
                instance_id: "inst-7".to_string(),
                pid: 48211,
                start_time_ticks: 9127734,
            })
        );
        assert!(parse_agentd_sender_id("agentd://example-host/inst-7/048211:9127734").is_none());
        assert!(parse_agentd_sender_id("agentd://example-host/inst-7/48211:09127734").is_none());
        assert!(parse_agentd_sender_id("agentd://example-host/inst-7/48211").is_none());
    }

    #[test]
    fn agentd_machine_names_are_normalized_for_location_matching() {
        let identity =
            parse_agentd_sender_id("agentd://Example-Host./inst-7/48211:9127734").unwrap();
        assert_eq!(identity.machine, "example-host");
        assert_eq!(canonical_machine("EXAMPLE-HOST."), "example-host");
        assert_eq!(canonical_machine("EXAMPLE-HOST.."), "example-host.");
        assert!(machine_matches_canonical(
            &canonical_machine("example-host"),
            &canonical_machine("EXAMPLE-HOST.lan.")
        ));
        assert!(machine_matches_canonical(
            &canonical_machine("EXAMPLE-HOST.lan."),
            &canonical_machine("example-host")
        ));
        assert!(!machine_matches_canonical(
            &canonical_machine("example-host"),
            &canonical_machine("example-host..")
        ));
        assert!(!machine_matches_canonical(
            &canonical_machine("example-host.lan"),
            &canonical_machine("example-host.other")
        ));
    }

    #[test]
    fn parsed_repeated_terminal_dot_does_not_select_local_machine() {
        let identity =
            parse_agentd_sender_id("agentd://example-host../inst-7/48211:9127734").unwrap();
        assert_eq!(identity.machine, "example-host.");
        assert!(!agentd_identity_matches_local_machine(
            &identity,
            "example-host"
        ));
    }

    fn hub_snapshot(health: &str, scan: &str) -> Value {
        json!({
            "type": "snapshot",
            "schema": "agentd-hub.snapshot.v1",
            "sources": [{
                "machine": "example-host",
                "health": {"state": health},
                "instanceId": "inst-7",
                "scan": {"state": scan}
            }],
            "agents": [{
                "machine": "example-host",
                "instanceId": "inst-7",
                "id": {"pid": 48211, "startTimeTicks": 9127734},
                "name": "Agentd sender",
                "tmux": {"session": "api", "windowIndex": 2, "paneId": "%14"}
            }]
        })
    }

    #[test]
    fn hub_sender_lookup_builds_the_exact_resolver_target_from_agentd_identity() {
        let identity =
            parse_agentd_sender_id("agentd://example-host/inst-7/48211:9127734").unwrap();
        let target = match lookup_hub_agentd(&identity, &hub_snapshot("reporting", "complete")) {
            RegistryLookup::Target(target) => target,
            RegistryLookup::Answer(answer) => panic!("expected a target, got {answer}"),
        };
        assert_eq!(target["identity"]["machine"], "example-host");
        assert_eq!(target["identity"]["instanceId"], "inst-7");
        assert_eq!(target["identity"]["pid"], 48211);
        assert_eq!(target["identity"]["startTimeTicks"], "9127734");
        assert_eq!(target["tmux"]["windowIndex"], "2");
        assert_eq!(validate_target(&target), Ok(true));
    }

    #[test]
    fn hub_sender_lookup_normalizes_source_and_agent_machine_names() {
        let identity =
            parse_agentd_sender_id("agentd://example-host/inst-7/48211:9127734").unwrap();
        let mut hub = hub_snapshot("reporting", "complete");
        hub["sources"][0]["machine"] = json!("Example-Host.lan.");
        hub["agents"][0]["machine"] = json!("EXAMPLE-HOST.lan.");
        assert!(matches!(
            lookup_hub_agentd(&identity, &hub),
            RegistryLookup::Target(_)
        ));
    }

    #[test]
    fn hub_sender_lookup_does_not_double_normalize_parsed_machine() {
        let identity =
            parse_agentd_sender_id("agentd://example-host../inst-7/48211:9127734").unwrap();
        assert_eq!(identity.machine, "example-host.");
        assert!(matches!(
            lookup_hub_agentd(&identity, &hub_snapshot("reporting", "complete")),
            RegistryLookup::Answer(_)
        ));
    }

    #[test]
    fn hub_sender_lookup_distinguishes_unreachable_from_incomplete() {
        let identity =
            parse_agentd_sender_id("agentd://example-host/inst-7/48211:9127734").unwrap();
        let answer_code = |lookup| match lookup {
            RegistryLookup::Answer(answer) => {
                answer["reasons"][0]["code"].as_str().unwrap().to_string()
            }
            RegistryLookup::Target(_) => panic!("expected an unavailable registry result"),
        };
        assert_eq!(
            answer_code(lookup_hub_agentd(
                &identity,
                &hub_snapshot("not_reached", "complete")
            )),
            "registry_unreachable"
        );
        assert_eq!(
            answer_code(lookup_hub_agentd(
                &identity,
                &hub_snapshot("reporting", "degraded")
            )),
            "registry_incomplete"
        );
    }

    #[test]
    fn declared_sender_windows_precede_location_results_and_keep_registry_reasons() {
        let declared = BTreeSet::from([41, 3]);
        let location = json!({
            "status": "unknown",
            "windows": [
                {"window": 41, "relation": "shows"},
                {"window": 30, "relation": "linked"},
                {"window": 20, "relation": "shows"}
            ],
            "reasons": [reason("registry_unreachable", "registry", "offline", true)]
        });
        let result = merge_declared_windows(&declared, location);
        assert_eq!(result["status"], "found");
        assert_eq!(
            result["windows"],
            json!([
                {"window": 3, "relation": "declared"},
                {"window": 41, "relation": "declared"},
                {"window": 20, "relation": "shows"},
                {"window": 30, "relation": "linked"}
            ])
        );
        assert_eq!(result["reasons"][0]["code"], "registry_unreachable");
    }
}
