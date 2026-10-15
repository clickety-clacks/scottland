use serde_json::{Map, Value, json};
use std::io::{self, Read, Write};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::time::Duration;

const MAX_WAYFIRE_FRAME: usize = 16 * 1024 * 1024;
const MAX_OSD_REPLY: usize = 64 * 1024;
const HOST_TIMEOUT: Duration = Duration::from_secs(10);

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct OsdRequest {
    pub message: String,
    pub icon: Option<String>,
    pub progress: Option<u8>,
    pub duration_ms: Option<u32>,
}

impl OsdRequest {
    pub fn parse(args: &[String]) -> Result<Self, String> {
        let mut message = None;
        let mut icon = None;
        let mut progress = None;
        let mut duration_ms = None;
        let mut index = 0;
        while index < args.len() {
            match args[index].as_str() {
                "--message" => {
                    index += 1;
                    let Some(value) = args.get(index).filter(|value| !value.is_empty()) else {
                        return Err("--message requires non-empty text".to_string());
                    };
                    message = Some(value.clone());
                }
                "--icon" => {
                    index += 1;
                    let Some(value) = args.get(index).filter(|value| !value.is_empty()) else {
                        return Err("--icon requires a non-empty name".to_string());
                    };
                    icon = Some(value.clone());
                }
                "--progress" => {
                    index += 1;
                    let Some(value) = args.get(index) else {
                        return Err("--progress requires an integer from 0 to 100".to_string());
                    };
                    let parsed = value
                        .parse::<u8>()
                        .ok()
                        .filter(|value| *value <= 100)
                        .ok_or_else(|| "--progress must be an integer from 0 to 100".to_string())?;
                    progress = Some(parsed);
                }
                "--duration" => {
                    index += 1;
                    let Some(value) = args.get(index) else {
                        return Err(
                            "--duration requires a positive number of milliseconds".to_string()
                        );
                    };
                    let parsed = value
                        .parse::<u32>()
                        .ok()
                        .filter(|value| *value > 0)
                        .ok_or_else(|| {
                            "--duration must be a positive number of milliseconds".to_string()
                        })?;
                    duration_ms = Some(parsed);
                }
                option => return Err(format!("unknown osd option {option}")),
            }
            index += 1;
        }
        let message = message.ok_or_else(|| "osd requires --message TEXT".to_string())?;
        Ok(Self {
            message,
            icon,
            progress,
            duration_ms,
        })
    }

    fn wire_value(&self) -> Value {
        let mut data = Map::new();
        data.insert("message".to_string(), Value::String(self.message.clone()));
        if let Some(icon) = &self.icon {
            data.insert("icon".to_string(), Value::String(icon.clone()));
        }
        if let Some(progress) = self.progress {
            data.insert("progress".to_string(), Value::from(progress));
        }
        if let Some(duration_ms) = self.duration_ms {
            data.insert("duration_ms".to_string(), Value::from(duration_ms));
        }
        json!({"method": "osd.present", "data": data})
    }
}

#[derive(Default)]
pub struct SystemOsdPresenter;

impl SystemOsdPresenter {
    pub fn present(&mut self, request: &OsdRequest) -> Result<(), String> {
        let session = scottland::session::current(None).map_err(|error| {
            format!("could not identify the running Scottland session: {error}")
        })?;
        let socket = osd_socket_path(&session);
        present_at(&socket, request)
    }
}

fn osd_socket_path(session: &scottland::session::Session) -> PathBuf {
    let session_root = session
        .var("SCOTTLAND_SESSION_DIR")
        .map(PathBuf::from)
        .or_else(|| {
            session
                .var("XDG_RUNTIME_DIR")
                .map(PathBuf::from)
                .map(|runtime| runtime.join("scottland"))
        })
        .unwrap_or_else(|| {
            PathBuf::from(format!("/run/user/{}/scottland", scottland::dirs::uid()))
        });
    session_root
        .join("widgets")
        .join(&session.name)
        .join("osd.sock")
}

fn present_at(socket_path: &Path, request: &OsdRequest) -> Result<(), String> {
    let mut stream = UnixStream::connect(socket_path)
        .map_err(|error| format!("could not contact the session OSD host: {error}"))?;
    stream
        .set_read_timeout(Some(HOST_TIMEOUT))
        .and_then(|_| stream.set_write_timeout(Some(HOST_TIMEOUT)))
        .map_err(|error| format!("could not set the session OSD deadline: {error}"))?;
    let mut message = serde_json::to_vec(&request.wire_value())
        .map_err(|error| format!("could not encode the OSD request: {error}"))?;
    message.push(b'\n');
    stream
        .write_all(&message)
        .map_err(|error| format!("could not send the OSD request: {error}"))?;
    let response = read_line_limited(&mut stream, MAX_OSD_REPLY)
        .map_err(|error| format!("could not read the OSD host acknowledgement: {error}"))?;
    let value: Value = serde_json::from_slice(&response)
        .map_err(|error| format!("OSD host returned invalid JSON: {error}"))?;
    accept_ack(value)
}

fn accept_ack(value: Value) -> Result<(), String> {
    if value.get("result").and_then(Value::as_str) == Some("shown") && value.get("error").is_none()
    {
        return Ok(());
    }
    let reason = value
        .get("error")
        .and_then(Value::as_str)
        .unwrap_or("session host did not acknowledge shown");
    Err(reason.to_string())
}

#[cfg(test)]
fn mock_exchange(
    request: &Value,
    exchange: impl FnOnce(&Value) -> Result<Value, String>,
) -> Result<(), String> {
    let response = exchange(request)?;
    accept_ack(response)
}

fn read_line_limited(stream: &mut UnixStream, limit: usize) -> io::Result<Vec<u8>> {
    let mut line = Vec::new();
    while line.len() < limit {
        let mut byte = [0_u8; 1];
        stream.read_exact(&mut byte)?;
        if byte[0] == b'\n' {
            return Ok(line);
        }
        line.push(byte[0]);
    }
    Err(io::Error::new(
        io::ErrorKind::InvalidData,
        "OSD acknowledgement exceeded the protocol limit",
    ))
}

pub fn focused_output_name() -> Result<String, String> {
    let session = scottland::session::current(None)
        .map_err(|error| format!("could not identify the running Scottland session: {error}"))?;
    let focused = wayfire_call(&session, "window-rules/get-focused-output")?;
    let outputs = wayfire_call(&session, "window-rules/list-outputs")?;
    focused_output_from(&focused, &outputs)
}

fn focused_output_from(focused: &Value, outputs: &Value) -> Result<String, String> {
    let focused_id = focused
        .get("info")
        .and_then(|info| info.get("id"))
        .and_then(Value::as_u64)
        .ok_or_else(|| "Wayfire did not report the focused output".to_string())?;
    let outputs = outputs
        .as_array()
        .ok_or_else(|| "Wayfire returned an invalid output list".to_string())?;
    outputs
        .iter()
        .find(|output| output.get("id").and_then(Value::as_u64) == Some(focused_id))
        .and_then(|output| output.get("name"))
        .and_then(Value::as_str)
        .filter(|name| !name.is_empty())
        .map(str::to_string)
        .ok_or_else(|| format!("Wayfire did not name focused output {focused_id}"))
}

fn wayfire_call(session: &scottland::session::Session, method: &str) -> Result<Value, String> {
    let socket = session
        .wayfire_socket()
        .ok_or_else(|| "the selected session has no Wayfire IPC socket".to_string())?;
    let mut stream = UnixStream::connect(socket).map_err(|error| {
        format!("could not connect to the selected session compositor: {error}")
    })?;
    stream
        .set_read_timeout(Some(Duration::from_secs(2)))
        .and_then(|_| stream.set_write_timeout(Some(Duration::from_secs(2))))
        .map_err(|error| format!("could not set the compositor IPC deadline: {error}"))?;
    let body = serde_json::to_vec(&json!({"method": method, "data": {}}))
        .map_err(|error| format!("could not encode the compositor query: {error}"))?;
    let size = u32::try_from(body.len())
        .map_err(|_| "compositor query exceeds the protocol limit".to_string())?;
    if body.len() > MAX_WAYFIRE_FRAME {
        return Err("compositor query exceeds the protocol limit".to_string());
    }
    stream
        .write_all(&size.to_le_bytes())
        .and_then(|_| stream.write_all(&body))
        .map_err(|error| format!("could not send the compositor query: {error}"))?;
    let mut header = [0_u8; 4];
    stream
        .read_exact(&mut header)
        .map_err(|error| format!("could not read the compositor response: {error}"))?;
    let response_size = u32::from_le_bytes(header) as usize;
    if response_size > MAX_WAYFIRE_FRAME {
        return Err("compositor response exceeds the protocol limit".to_string());
    }
    let mut bytes = vec![0_u8; response_size];
    stream
        .read_exact(&mut bytes)
        .map_err(|error| format!("could not read the compositor response: {error}"))?;
    let response: Value = serde_json::from_slice(&bytes)
        .map_err(|error| format!("compositor returned invalid JSON: {error}"))?;
    if let Some(error) = response.get("error") {
        return Err(format!("compositor rejected {method}: {error}"));
    }
    Ok(response)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::OsStr;

    #[test]
    fn osd_wire_request_contains_only_selected_fields() {
        let request = OsdRequest::parse(&[
            "--message".into(),
            "Saving".into(),
            "--progress".into(),
            "100".into(),
        ])
        .unwrap();
        assert_eq!(
            request.wire_value(),
            json!({
                "method": "osd.present",
                "data": {"message": "Saving", "progress": 100}
            })
        );
    }

    #[test]
    fn osd_request_is_sent_once_and_only_shown_ack_succeeds() {
        let request = OsdRequest::parse(&["--message".into(), "Saved".into()]).unwrap();
        let wire = request.wire_value();
        let mut calls = 0;
        assert!(
            mock_exchange(&wire, |received| {
                calls += 1;
                assert_eq!(received["method"], "osd.present");
                assert_eq!(received["data"]["message"], "Saved");
                Ok(json!({"result": "shown"}))
            })
            .is_ok()
        );
        assert_eq!(calls, 1);
    }

    #[test]
    fn osd_host_error_is_returned_without_success() {
        let request = OsdRequest::parse(&["--message".into(), "Saved".into()]).unwrap();
        assert_eq!(
            mock_exchange(&request.wire_value(), |_| {
                Ok(json!({"error": "panel unavailable"}))
            })
            .unwrap_err(),
            "panel unavailable"
        );
        assert!(accept_ack(json!({"result": "shown", "error": "not shown"})).is_err());
    }

    #[test]
    fn session_socket_is_scoped_to_recorded_session_runtime_and_display() {
        let session = scottland::session::Session {
            name: "wayland-7".into(),
            env: [(
                OsStr::new("XDG_RUNTIME_DIR").to_os_string(),
                "/run/user/1000".into(),
            )]
            .into_iter()
            .collect(),
        };
        assert_eq!(
            osd_socket_path(&session),
            PathBuf::from("/run/user/1000/scottland/widgets/wayland-7/osd.sock")
        );
    }

    #[test]
    fn focused_output_uses_the_id_reported_by_the_session() {
        assert_eq!(
            focused_output_from(
                &json!({"info": {"id": 12}}),
                &json!([
                    {"id": 3, "name": "first"},
                    {"id": 12, "name": "second"}
                ])
            )
            .unwrap(),
            "second"
        );
    }

    #[test]
    fn focused_output_fails_closed_when_the_selected_output_is_missing() {
        assert!(
            focused_output_from(
                &json!({"info": {"id": 12}}),
                &json!([{ "id": 3, "name": "first" }])
            )
            .is_err()
        );
    }
}
