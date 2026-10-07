use serde_json::{Value, json};
use std::io::{Read, Write};
use std::os::unix::net::UnixStream;
use std::path::Path;
use std::time::Duration;

const MAX_REPLY_BYTES: usize = 16 * 1024 * 1024;

#[derive(Clone, Debug)]
pub struct Window {
    pub id: u64,
    pub pid: u32,
    pub app_id: String,
    pub title: String,
}

pub fn read_windows(socket: &Path) -> Result<Vec<Window>, String> {
    let mut stream = UnixStream::connect(socket)
        .map_err(|error| format!("cannot connect to Scottland IPC: {error}"))?;
    stream
        .set_read_timeout(Some(Duration::from_secs(3)))
        .map_err(|error| format!("cannot set Scottland IPC timeout: {error}"))?;
    stream
        .set_write_timeout(Some(Duration::from_secs(3)))
        .map_err(|error| format!("cannot set Scottland IPC timeout: {error}"))?;

    let request = serde_json::to_vec(&json!({
        "method": "scottland/desktop-model",
        "data": { "slice": "desktop" }
    }))
    .map_err(|error| format!("cannot encode Scottland IPC request: {error}"))?;
    let length = u32::try_from(request.len())
        .map_err(|_| "Scottland IPC request is too large".to_string())?;
    stream
        .write_all(&length.to_le_bytes())
        .and_then(|_| stream.write_all(&request))
        .map_err(|error| format!("cannot send Scottland IPC request: {error}"))?;

    let mut header = [0_u8; 4];
    stream
        .read_exact(&mut header)
        .map_err(|error| format!("cannot read Scottland IPC reply header: {error}"))?;
    let length = u32::from_le_bytes(header) as usize;
    if length > MAX_REPLY_BYTES {
        return Err("Scottland IPC reply is too large".to_string());
    }
    let mut body = vec![0; length];
    stream
        .read_exact(&mut body)
        .map_err(|error| format!("cannot read Scottland IPC reply: {error}"))?;
    let reply: Value = serde_json::from_slice(&body)
        .map_err(|error| format!("Scottland IPC returned invalid JSON: {error}"))?;
    if let Some(error) = reply.get("error").and_then(Value::as_str) {
        return Err(format!("Scottland IPC error: {error}"));
    }
    let views = reply
        .get("windows")
        .and_then(Value::as_array)
        .ok_or_else(|| "Scottland IPC reply has no windows array".to_string())?;

    views
        .iter()
        .map(|view| {
            let id = view
                .get("id")
                .and_then(Value::as_u64)
                .ok_or_else(|| "Scottland IPC window has no unsigned id".to_string())?;
            let pid = view
                .get("pid")
                .and_then(Value::as_u64)
                .and_then(|pid| u32::try_from(pid).ok())
                .ok_or_else(|| format!("Scottland IPC window {id} has no valid pid"))?;
            Ok(Window {
                id,
                pid,
                app_id: string_member(view, "app_id", id)?,
                title: string_member(view, "title", id)?,
            })
        })
        .collect()
}

fn string_member(view: &Value, key: &str, id: u64) -> Result<String, String> {
    view.get(key)
        .and_then(Value::as_str)
        .map(str::to_string)
        .ok_or_else(|| format!("Scottland IPC window {id} has no string {key}"))
}

#[cfg(test)]
mod tests {
    use super::string_member;
    use serde_json::json;

    #[test]
    fn rejects_missing_or_non_string_window_facts() {
        assert!(string_member(&json!({"title": 3}), "title", 17).is_err());
        assert!(string_member(&json!({}), "app_id", 17).is_err());
    }
}
