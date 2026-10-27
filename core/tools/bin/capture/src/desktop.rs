//! What the selected session shows when the command starts: its outputs, which one has focus,
//! and the window rectangles on that output.
//!
//! Outputs and focus come from Wayfire's IPC. Without it, the outputs come from the compositor's
//! output report (`scottland-output list`) and the parts that need Wayfire are off: window picking,
//! and with several outputs, knowing which one has focus. A click on a screen still picks it.

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
    /// The output with focus; `None` only without Wayfire's IPC and with several outputs.
    pub focused: Option<Output>,
    /// Why Wayfire's IPC cannot be used, when it cannot.
    pub no_ipc: Option<String>,
}

impl Desktop {
    pub fn read(session: &Session) -> Result<Self, String> {
        let mut stream = match session.wayfire_socket() {
            None => return Self::without_ipc(session, "the session has no WAYFIRE_SOCKET".into()),
            Some(socket) => match UnixStream::connect(socket) {
                Ok(stream) => stream,
                Err(error) => {
                    return Self::without_ipc(
                        session,
                        format!("cannot connect to {}: {error}", socket.display()),
                    );
                }
            },
        };
        let listed = ipc_call(&mut stream, "window-rules/list-outputs")?;
        let focused = ipc_call(&mut stream, "window-rules/get-focused-output")?;
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
        Ok(Self {
            outputs,
            focused: Some(focused),
            no_ipc: None,
        })
    }

    /// The outputs as the compositor reports them, with a warning that says what is off.
    fn without_ipc(session: &Session, why: String) -> Result<Self, String> {
        let why = format!("Scottland's Wayfire IPC is unavailable ({why})");
        let outputs = read_heads(session)
            .iter()
            .filter_map(head_output)
            .collect::<Vec<_>>();
        if outputs.is_empty() {
            return Err(format!(
                "{why} and scottland-output list reported no screens"
            ));
        }
        // With one output it has focus; with several, which one does is Wayfire's to say.
        let focused = match outputs.as_slice() {
            [only] => Some(only.clone()),
            _ => None,
        };
        let off = if focused.is_some() {
            "window picking is off; the whole screen and dragged regions still work"
        } else {
            "window picking and the whole screen without a click are off, since which screen has focus is unknown; clicking a screen and dragged regions still work"
        };
        eprintln!("scottland-capture: {why}: {off}");
        Ok(Self {
            outputs,
            focused,
            no_ipc: Some(why),
        })
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
    // Wayfire reports the geometry as floating-point numbers.
    let rect = logical_rect(output.get("geometry")?)?;
    let head = heads
        .iter()
        .find(|head| head.get("name").and_then(Value::as_str) == Some(name.as_str()));
    Some(Output {
        name,
        rect,
        scale: head.map_or(1.0, head_scale),
        pixels: head.and_then(head_pixels),
    })
}

/// An enabled head from `scottland-output list` as an output. Its logical size is its pixel
/// size over its scale, whole pixels down, as wlroots lays outputs out.
fn head_output(head: &Value) -> Option<Output> {
    if head.get("enabled") != Some(&Value::Bool(true)) {
        return None;
    }
    let name = head.get("name")?.as_str()?.to_string();
    let pixels = head_pixels(head)?;
    let scale = head_scale(head);
    let (width, height) = (
        (pixels.0 as f64 / scale) as u64,
        (pixels.1 as f64 / scale) as u64,
    );
    if width == 0 || height == 0 {
        return None;
    }
    Some(Output {
        name,
        rect: Rect {
            x: head.get("x")?.as_i64()?,
            y: head.get("y")?.as_i64()?,
            width,
            height,
        },
        scale,
        pixels: Some(pixels),
    })
}

fn head_scale(head: &Value) -> f64 {
    head.get("scale")
        .and_then(Value::as_f64)
        .filter(|scale| *scale > 0.0)
        .unwrap_or(1.0)
}

/// The head's current mode in device pixels, after rotation.
fn head_pixels(head: &Value) -> Option<(u64, u64)> {
    let mode = head.get("mode")?;
    let (width, height) = (mode.get("width")?.as_u64()?, mode.get("height")?.as_u64()?);
    let rotated = head.get("transform")?.as_u64()? % 2 == 1;
    Some(if rotated {
        (height, width)
    } else {
        (width, height)
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

fn ipc_call(stream: &mut UnixStream, method: &str) -> Result<Value, String> {
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
        // As Wayfire's window-rules/list-outputs reports it, in floating-point numbers.
        let output = json!({"name": "DSI-1",
                            "geometry": {"x": 0.0, "y": 0.0, "width": 1080.0, "height": 1920.0}});
        let heads = [json!({"name": "DSI-1", "scale": 1.0, "transform": 1,
                            "mode": {"width": 1920, "height": 1080, "refresh": 60.0}})];
        let parsed = parse_output(&output, &heads).unwrap();
        assert_eq!(
            parsed.rect,
            Rect {
                x: 0,
                y: 0,
                width: 1080,
                height: 1920
            }
        );
        assert_eq!(parsed.pixels, Some((1080, 1920)));
        assert_eq!(parsed.scale, 1.0);
    }

    #[test]
    fn heads_give_enabled_outputs_in_logical_pixels() {
        // As scottland-output list reports heads, trimmed to the fields read here.
        let heads = json!([
            {"name": "DP-1", "enabled": true, "x": 0, "y": 0, "scale": 1.5, "transform": 0,
             "mode": {"width": 3840, "height": 2160, "refresh": 60.0}},
            {"name": "DSI-1", "enabled": true, "x": 2560, "y": 0, "scale": 1, "transform": 3,
             "mode": {"width": 1920, "height": 1080, "refresh": 60.0}},
            {"name": "HDMI-A-1", "enabled": false, "x": 0, "y": 0, "scale": 1, "transform": 0,
             "mode": null}
        ]);
        let outputs = heads
            .as_array()
            .unwrap()
            .iter()
            .filter_map(head_output)
            .collect::<Vec<_>>();
        assert_eq!(outputs.len(), 2);
        assert_eq!(
            outputs[0].rect,
            Rect {
                x: 0,
                y: 0,
                width: 2560,
                height: 1440
            }
        );
        assert_eq!(outputs[0].pixels, Some((3840, 2160)));
        assert_eq!(outputs[0].scale, 1.5);
        assert_eq!(
            outputs[1].rect,
            Rect {
                x: 2560,
                y: 0,
                width: 1080,
                height: 1920
            }
        );
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
            focused: Some(output("A", 0, 1.0)),
            no_ipc: None,
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
