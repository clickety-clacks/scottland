#[path = "../../scottland-window-name/src/ipc.rs"]
mod ipc;

use scottland::session;
use serde_json::{Value, json};
use std::env;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode, Stdio};

const SCHEMA: &str = "agent-window-resolver.window-session.request.v1";
const RESPONSE_SCHEMA: &str = "agent-window-resolver.window-session.response.v1";
const MAX_WINDOWS: usize = 4096;
const MAX_RESPONSE_BYTES: usize = 1_048_576;

fn main() -> ExitCode {
    match run() {
        Ok(response) => {
            println!("{response}");
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("scottland-terminal-session: {error}");
            ExitCode::FAILURE
        }
    }
}

fn run() -> Result<Value, String> {
    let mut args = env::args().skip(1);
    let id = args
        .next()
        .ok_or_else(|| "usage: scottland-terminal-session WINDOW_ID".to_string())?
        .parse::<u64>()
        .map_err(|_| "WINDOW_ID must be an unsigned decimal number".to_string())?;
    if args.next().is_some() {
        return Err("usage: scottland-terminal-session WINDOW_ID".to_string());
    }
    let selected = session::current(None)
        .map_err(|error| format!("cannot select a running Scottland session: {error}"))?;
    let socket = selected
        .wayfire_socket()
        .ok_or_else(|| "selected session has no Wayfire socket".to_string())?;
    let views = ipc::read_windows(socket)?;
    let machine = fs::read_to_string("/proc/sys/kernel/hostname")
        .map_err(|error| format!("cannot read local host name: {error}"))?;
    let request = build_request(id, &views, &machine, &read_start_ticks)?;
    call_resolver(&resolver_dir()?, &request)
}

fn build_request(
    selected_id: u64,
    views: &[ipc::Window],
    local_machine: &str,
    read_ticks: &impl Fn(u32) -> Result<String, String>,
) -> Result<Value, String> {
    if views.is_empty() || views.len() > MAX_WINDOWS {
        return Err("Scottland window snapshot has an invalid size".to_string());
    }
    let machine = local_machine.trim();
    if machine.is_empty()
        || machine.len() > 255
        || !machine
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '_' | '.' | ':' | '-'))
    {
        return Err("local host name is invalid".to_string());
    }
    let mut windows = Vec::with_capacity(views.len());
    let mut selected = None;
    for view in views {
        let ticks = read_ticks(view.pid)?;
        let identity = json!({
            "stableId": view.id.to_string(),
            "address": format!("0x{:x}", view.id),
            "pid": view.pid,
            "startTimeTicks": ticks,
        });
        if view.id == selected_id {
            if selected.is_some() {
                return Err("Scottland returned duplicate selected window ID".to_string());
            }
            selected = Some(identity.clone());
        }
        windows.push(identity);
    }
    let selected = selected.ok_or_else(|| "selected window is no longer open".to_string())?;
    Ok(json!({
        "schema": SCHEMA,
        "requestId": format!("scottland-terminal-{}-{selected_id}", std::process::id()),
        "operation": "window-session",
        "window": selected,
        "windows": windows,
        "local": {"machine": machine},
        "limits": {"deadlineMs": 5000},
    }))
}

fn read_start_ticks(pid: u32) -> Result<String, String> {
    let stat = fs::read(format!("/proc/{pid}/stat"))
        .map_err(|error| format!("cannot read window process {pid}: {error}"))?;
    parse_start_ticks(pid, &stat)
}

fn parse_start_ticks(pid: u32, stat: &[u8]) -> Result<String, String> {
    let open = stat
        .iter()
        .position(|byte| *byte == b'(')
        .ok_or_else(|| "process stat has no command field".to_string())?;
    let actual_pid = std::str::from_utf8(&stat[..open])
        .ok()
        .and_then(|text| text.trim().parse::<u32>().ok());
    if actual_pid != Some(pid) {
        return Err("process stat PID changed during collection".to_string());
    }
    let close = stat
        .iter()
        .rposition(|byte| *byte == b')')
        .filter(|close| *close > open)
        .ok_or_else(|| "process stat has no closing command delimiter".to_string())?;
    // The suffix starts at stat field 3; starttime is field 22.
    let ticks = stat[close + 1..]
        .split(|byte| byte.is_ascii_whitespace())
        .filter(|field| !field.is_empty())
        .nth(19)
        .and_then(|field| std::str::from_utf8(field).ok())
        .ok_or_else(|| "process stat has no starttime".to_string())?;
    if ticks.is_empty()
        || ticks.len() > 20
        || !ticks.bytes().all(|byte| byte.is_ascii_digit())
        || (ticks.len() > 1 && ticks.starts_with('0'))
    {
        return Err("process stat has invalid starttime".to_string());
    }
    Ok(ticks.to_string())
}

fn resolver_dir() -> Result<PathBuf, String> {
    let binary = env::current_exe().map_err(|error| format!("cannot locate tool: {error}"))?;
    let bin_dir = binary
        .parent()
        .ok_or_else(|| "tool has no parent directory".to_string())?;
    [
        bin_dir.join("../lib/agent_window_resolver"),
        bin_dir.join("agent_window_resolver"),
    ]
    .into_iter()
    .find(|path| path.join("cli.py").is_file())
    .ok_or_else(|| "vendored agent-window-resolver is unavailable".to_string())
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
        .map_err(|error| format!("cannot start resolver: {error}"))?;
    let bytes = serde_json::to_vec(request)
        .map_err(|error| format!("cannot encode resolver request: {error}"))?;
    child
        .stdin
        .take()
        .ok_or_else(|| "resolver stdin is unavailable".to_string())?
        .write_all(&bytes)
        .map_err(|error| format!("cannot send resolver request: {error}"))?;
    let output = child
        .wait_with_output()
        .map_err(|error| format!("cannot read resolver response: {error}"))?;
    if output.stdout.len() > MAX_RESPONSE_BYTES {
        return Err("resolver response is too large".to_string());
    }
    let response: Value = serde_json::from_slice(&output.stdout)
        .map_err(|error| format!("resolver returned invalid JSON: {error}"))?;
    if response.get("schema").and_then(Value::as_str) != Some(RESPONSE_SCHEMA)
        || response.get("operation").and_then(Value::as_str) != Some("window-session")
        || response.get("requestId") != request.get("requestId")
        || !matches!(
            response.get("status").and_then(Value::as_str),
            Some("found" | "none" | "unknown" | "invalid")
        )
    {
        return Err("resolver returned a mismatched window-session response".to_string());
    }
    if !output.status.success() && response.get("status").and_then(Value::as_str) != Some("invalid")
    {
        return Err("resolver failed before completing the window-session read".to_string());
    }
    Ok(response)
}

#[cfg(test)]
mod tests {
    use super::{RESPONSE_SCHEMA, build_request, call_resolver, parse_start_ticks};
    use crate::ipc::Window;
    use std::path::PathBuf;

    #[test]
    fn request_contains_every_window_and_exact_process_identity() {
        let views = [
            Window {
                id: 17,
                pid: 41,
                app_id: String::new(),
                title: String::new(),
            },
            Window {
                id: 18,
                pid: 41,
                app_id: String::new(),
                title: String::new(),
            },
        ];
        let request = build_request(17, &views, "example-host\n", &|_| Ok("123".into())).unwrap();
        assert_eq!(request["window"]["startTimeTicks"], "123");
        assert_eq!(request["windows"].as_array().unwrap().len(), 2);
        assert_eq!(request["windows"][1]["pid"], 41);
        assert_eq!(request["local"]["machine"], "example-host");
    }

    #[test]
    fn parses_start_ticks_after_parentheses_in_process_name() {
        let mut stat = b"41 (a ) name) S 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 123".to_vec();
        stat.extend_from_slice(b" 1 1\n");
        assert_eq!(parse_start_ticks(41, &stat).unwrap(), "123");
    }

    #[test]
    fn vendored_cli_accepts_the_scottland_window_session_request() {
        let view = Window {
            id: 17,
            pid: 4_194_304,
            app_id: String::new(),
            title: String::new(),
        };
        let request = build_request(17, &[view], "example-host", &|_| Ok("1".into())).unwrap();
        let resolver =
            PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../vendor/agent_window_resolver");
        let response = call_resolver(&resolver, &request).unwrap();
        assert_eq!(response["schema"], RESPONSE_SCHEMA);
        assert_eq!(response["requestId"], request["requestId"]);
        assert_eq!(response["status"], "unknown");
    }
}
