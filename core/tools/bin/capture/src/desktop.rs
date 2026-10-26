//! What the selected session shows when the command starts: its outputs, which one has focus,
//! and the window rectangles on that output.

use scottland::capture::Rect;
use scottland::{dirs, session::Session};
use serde_json::{Value, json};
use std::ffi::OsStr;
use std::io::{Read, Write};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::Duration;

const MAX_REPLY_BYTES: usize = 16 * 1024 * 1024;

#[derive(Clone, Debug, PartialEq)]
pub struct Output {
    pub name: String,
    /// Position and size in logical layout pixels.
    pub rect: Rect,
    pub scale: f64,
    /// Size in device pixels, after rotation.
    pub pixels: Option<(u64, u64)>,
}

pub struct Desktop {
    pub outputs: Vec<Output>,
    pub focused: Output,
}

impl Desktop {
    pub fn read(session: &Session) -> Result<Self, String> {
        let socket = session
            .wayfire_socket()
            .ok_or_else(|| "the selected session has no Wayfire IPC socket".to_string())?;
        let listed = ipc_call(socket, "window-rules/list-outputs")?;
        let focused = ipc_call(socket, "window-rules/get-focused-output")?;
        let focused_name = focused
            .pointer("/info/name")
            .and_then(Value::as_str)
            .ok_or_else(|| "Scottland did not report a focused output".to_string())?
            .to_string();
        let heads = read_heads(session);
        let outputs = listed
            .as_array()
            .ok_or_else(|| "Scottland's output list is not an array".to_string())?
            .iter()
            .filter_map(|output| parse_output(output, &heads))
            .collect::<Vec<_>>();
        let focused = outputs
            .iter()
            .find(|output| output.name == focused_name)
            .cloned()
            .ok_or_else(|| {
                format!("the focused output {focused_name} is not in the output list")
            })?;
        Ok(Self { outputs, focused })
    }

    /// The largest scale among the outputs `rect` covers, at least 1.
    pub fn scale_for(&self, rect: Rect) -> f64 {
        self.outputs
            .iter()
            .filter(|output| overlaps(output.rect, rect))
            .map(|output| output.scale)
            .fold(1.0, f64::max)
    }

    /// The output whose logical rectangle is exactly `rect`.
    pub fn whole_output(&self, rect: Rect) -> Option<&Output> {
        self.outputs.iter().find(|output| output.rect == rect)
    }
}

fn overlaps(a: Rect, b: Rect) -> bool {
    let (ax2, ay2) = (a.x + a.width as i64, a.y + a.height as i64);
    let (bx2, by2) = (b.x + b.width as i64, b.y + b.height as i64);
    a.x < bx2 && b.x < ax2 && a.y < by2 && b.y < ay2
}

fn parse_output(output: &Value, heads: &[Value]) -> Option<Output> {
    let name = output.get("name")?.as_str()?.to_string();
    let geometry = output.get("geometry")?;
    let rect = Rect {
        x: geometry.get("x")?.as_i64()?,
        y: geometry.get("y")?.as_i64()?,
        width: geometry.get("width")?.as_u64()?,
        height: geometry.get("height")?.as_u64()?,
    };
    let head = heads
        .iter()
        .find(|head| head.get("name").and_then(Value::as_str) == Some(name.as_str()));
    let scale = head
        .and_then(|head| head.get("scale"))
        .and_then(Value::as_f64)
        .filter(|scale| *scale > 0.0)
        .unwrap_or(1.0);
    let pixels = head.and_then(|head| {
        let mode = head.get("mode")?;
        let (width, height) = (mode.get("width")?.as_u64()?, mode.get("height")?.as_u64()?);
        let rotated = head.get("transform")?.as_u64()? % 2 == 1;
        Some(if rotated {
            (height, width)
        } else {
            (width, height)
        })
    });
    Some(Output {
        name,
        rect,
        scale,
        pixels,
    })
}

/// Output heads as `scottland-output list` reports them; empty when it cannot.
fn read_heads(session: &Session) -> Vec<Value> {
    let output = session_command(session, hooks_dir(session).join("libexec/scottland-output"))
        .arg("list")
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output();
    output
        .ok()
        .filter(|output| output.status.success())
        .and_then(|output| serde_json::from_slice::<Value>(&output.stdout).ok())
        .and_then(|value| value.as_array().cloned())
        .unwrap_or_default()
}

/// The rectangles of windows drawn on `output`, or why the window listing cannot tell.
pub fn window_rects(session: &Session, output: &str) -> Result<Vec<Rect>, String> {
    let ctl = hooks_dir(session).join("libexec/scottland-ctl");
    let result = session_command(session, &ctl)
        .args(["windows", "--json"])
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
        .map_err(|error| format!("cannot run scottland-ctl windows --json: {error}"))?;
    if !result.status.success() {
        return Err("scottland-ctl windows --json failed".to_string());
    }
    parse_window_rects(&result.stdout, output)
}

fn parse_window_rects(listing: &[u8], output: &str) -> Result<Vec<Rect>, String> {
    const NO_OUTPUT: &str = "the window listing (scottland-ctl windows --json) does not say which output draws each window";
    let windows: Value = serde_json::from_slice(listing).map_err(|_| NO_OUTPUT.to_string())?;
    let windows = windows.as_array().ok_or_else(|| NO_OUTPUT.to_string())?;
    let mut rects = Vec::new();
    for window in windows {
        let Some(drawn_on) = window.get("output") else {
            return Err(NO_OUTPUT.to_string());
        };
        if drawn_on.as_str() != Some(output) || window.get("hidden") == Some(&Value::Bool(true)) {
            continue;
        }
        if let Some(rect) = window.get("rect").and_then(logical_rect) {
            rects.push(rect);
        }
    }
    Ok(rects)
}

/// A listing rectangle rounded to whole logical pixels, edges rounded rather than the size.
fn logical_rect(rect: &Value) -> Option<Rect> {
    let number = |key: &str| rect.get(key).and_then(Value::as_f64);
    let (x, y, width, height) = (
        number("x")?,
        number("y")?,
        number("width")?,
        number("height")?,
    );
    let (left, top) = (x.round() as i64, y.round() as i64);
    let (right, bottom) = ((x + width).round() as i64, (y + height).round() as i64);
    (right > left && bottom > top).then(|| Rect {
        x: left,
        y: top,
        width: (right - left) as u64,
        height: (bottom - top) as u64,
    })
}

/// A command that runs with the session's recorded environment, never the caller's.
pub fn session_command(session: &Session, program: impl AsRef<OsStr>) -> Command {
    let mut command = Command::new(resolve(session, program.as_ref()));
    command.env_clear().envs(&session.env);
    command
}

/// `program` found on the session's `PATH` when it is a bare name.
fn resolve(session: &Session, program: &OsStr) -> PathBuf {
    let path = Path::new(program);
    if path.components().count() != 1 {
        return path.to_path_buf();
    }
    if let Some(search) = session.var("PATH") {
        for dir in std::env::split_paths(search) {
            let candidate = dir.join(path);
            if candidate.is_file() {
                return candidate;
            }
        }
    }
    path.to_path_buf()
}

pub fn hooks_dir(session: &Session) -> PathBuf {
    if let Some(hooks) = session
        .var("SCOTTLAND_HOOKS")
        .filter(|value| !value.is_empty())
    {
        return PathBuf::from(hooks);
    }
    let data_home = session
        .var("XDG_DATA_HOME")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .or_else(|| {
            session
                .var("HOME")
                .filter(|value| !value.is_empty())
                .map(|home| PathBuf::from(home).join(".local/share"))
        });
    if let Some(data_home) = data_home {
        let dev = data_home.join("scottland/dev");
        if dev.join("libexec").is_dir() {
            return dev;
        }
    }
    PathBuf::from(dirs::PACKAGE_HOOKS)
}

fn ipc_call(socket: &Path, method: &str) -> Result<Value, String> {
    let mut stream = UnixStream::connect(socket)
        .map_err(|error| format!("cannot connect to Scottland IPC: {error}"))?;
    let timeout = Some(Duration::from_secs(3));
    stream
        .set_read_timeout(timeout)
        .and_then(|_| stream.set_write_timeout(timeout))
        .map_err(|error| format!("cannot set Scottland IPC timeout: {error}"))?;
    let request = serde_json::to_vec(&json!({ "method": method, "data": {} }))
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
        .map_err(|error| format!("cannot read Scottland IPC reply: {error}"))?;
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
        return Err(format!("Scottland IPC error for {method}: {error}"));
    }
    Ok(reply)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn listing_without_output_names_is_unavailable() {
        let text = b"17 firefox Mozilla Firefox\n";
        assert!(parse_window_rects(text, "DP-1").is_err());
        let old = br#"[{"id":1,"app_id":"a","title":"t","hidden":false,"rect":{"x":0,"y":0,"width":10,"height":10}}]"#;
        assert!(parse_window_rects(old, "DP-1").is_err());
    }

    #[test]
    fn keeps_shown_windows_on_the_output_only() {
        let listing = br#"[
            {"id":1,"hidden":false,"output":"DP-1","rect":{"x":0,"y":0,"width":800,"height":600}},
            {"id":2,"hidden":true,"output":"DP-1","rect":{"x":10,"y":10,"width":300,"height":200}},
            {"id":3,"hidden":false,"output":"HDMI-A-1","rect":{"x":100,"y":100,"width":3000,"height":200}},
            {"id":4,"hidden":false,"output":null,"rect":null},
            {"id":5,"hidden":false,"output":"DP-1","rect":null}
        ]"#;
        assert_eq!(
            parse_window_rects(listing, "DP-1").unwrap(),
            [Rect {
                x: 0,
                y: 0,
                width: 800,
                height: 600
            }]
        );
    }

    #[test]
    fn rounds_edges_not_sizes() {
        let rect = json!({"x": 10.4, "y": 0.5, "width": 100.2, "height": 99.4});
        assert_eq!(
            logical_rect(&rect),
            Some(Rect {
                x: 10,
                y: 1,
                width: 101,
                height: 99
            })
        );
        assert_eq!(
            logical_rect(&json!({"x": 0, "y": 0, "width": 0.2, "height": 5})),
            None
        );
    }

    #[test]
    fn rotated_heads_swap_pixel_size() {
        let output =
            json!({"name": "DSI-1", "geometry": {"x": 0, "y": 0, "width": 1080, "height": 1920}});
        let heads = [json!({"name": "DSI-1", "scale": 1.0, "transform": 1,
                            "mode": {"width": 1920, "height": 1080, "refresh": 60.0}})];
        let parsed = parse_output(&output, &heads).unwrap();
        assert_eq!(parsed.pixels, Some((1080, 1920)));
        assert_eq!(parsed.scale, 1.0);
    }

    #[test]
    fn scale_for_takes_the_largest_covered_scale() {
        let output = |name: &str, x: i64, scale: f64| Output {
            name: name.into(),
            rect: Rect {
                x,
                y: 0,
                width: 1000,
                height: 1000,
            },
            scale,
            pixels: None,
        };
        let desktop = Desktop {
            outputs: vec![output("A", 0, 1.0), output("B", 1000, 2.0)],
            focused: output("A", 0, 1.0),
        };
        let left = Rect {
            x: 10,
            y: 10,
            width: 100,
            height: 100,
        };
        assert_eq!(desktop.scale_for(left), 1.0);
        assert_eq!(
            desktop.scale_for(Rect {
                width: 1500,
                ..left
            }),
            2.0
        );
    }
}
