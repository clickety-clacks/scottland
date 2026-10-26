//! The still and the pick surface (CP1, CP2).
//!
//! The still is taken before anything of the command's own is on screen. The pick runs a small
//! Quickshell overlay (`picker.qml`) that shows the still on every output and reports pointer and
//! key events over a private socket; [`crate::picker`] decides what each event means.

use crate::desktop::{Desktop, session_command};
use crate::image::{Image, Still};
use crate::picker::{Key, Picker, Reply};
use crate::run::last_line;
use scottland::capture::{Point, Rect};
use scottland::session::Session;
use serde_json::{Map, Value, json};
use std::fs;
use std::io::{BufRead, BufReader, ErrorKind, Write};
use std::os::unix::fs::DirBuilderExt;
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Stdio};
use std::thread;
use std::time::{Duration, Instant};

const PICKER_QML: &str = include_str!("picker.qml");

/// How long the overlay may take to come up and connect.
const CONNECT_LIMIT: Duration = Duration::from_secs(15);

/// A folder only this user can read, removed with everything in it when dropped.
pub struct PrivateDir(PathBuf);

impl PrivateDir {
    pub fn create() -> Result<Self, String> {
        let parent = crate::folders::private_parent()?;
        let path = parent.join(format!("capture-{}", std::process::id()));
        // A folder left by an earlier command with the same process id is stale.
        let _ = fs::remove_dir_all(&path);
        fs::DirBuilder::new()
            .mode(0o700)
            .create(&path)
            .map_err(|error| format!("cannot create {}: {error}", path.display()))?;
        Ok(Self(path))
    }

    pub fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for PrivateDir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

/// Every output's still, taken at once.
pub fn take_stills<'a>(session: &Session, desktop: &'a Desktop) -> Result<Vec<Still<'a>>, String> {
    let captures = desktop
        .outputs
        .iter()
        .map(|output| {
            let child = session_command(session, "grim")
                .args(["-t", "ppm", "-o", &output.name, "-"])
                .stdin(Stdio::null())
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .spawn()
                .map_err(|error| format!("cannot run grim to capture the screen: {error}"))?;
            Ok((output, child))
        })
        .collect::<Result<Vec<_>, String>>()?;
    captures
        .into_iter()
        .map(|(output, child)| {
            let result = child
                .wait_with_output()
                .map_err(|error| format!("cannot capture {}: {error}", output.name))?;
            if !result.status.success() {
                return Err(format!(
                    "cannot capture {}: {}",
                    output.name,
                    last_line(&result.stderr)
                ));
            }
            let image = Image::from_ppm(&result.stdout)
                .map_err(|error| format!("cannot capture {}: {error}", output.name))?;
            Ok(Still { output, image })
        })
        .collect()
}

/// A running pick overlay.
pub struct Overlay {
    child: Child,
    stream: UnixStream,
    lines: BufReader<UnixStream>,
    log: PathBuf,
}

impl Overlay {
    /// Show the still on every output and wait for the overlay to connect.
    pub fn open(
        session: &Session,
        desktop: &Desktop,
        stills: &[Still],
        dir: &PrivateDir,
    ) -> Result<Self, String> {
        let mut paths = Map::new();
        for still in stills {
            let path = dir.path().join(format!("{}.ppm", still.output.name));
            fs::write(&path, still.image.to_ppm())
                .map_err(|error| format!("cannot write the still: {error}"))?;
            paths.insert(
                still.output.name.clone(),
                Value::from(path.to_string_lossy()),
            );
        }
        let shell = dir.path().join("shell.qml");
        fs::write(&shell, PICKER_QML)
            .map_err(|error| format!("cannot write the picker: {error}"))?;
        let socket = dir.path().join("pick.sock");
        let listener = UnixListener::bind(&socket)
            .map_err(|error| format!("cannot open the picker socket: {error}"))?;
        listener
            .set_nonblocking(true)
            .map_err(|error| format!("cannot open the picker socket: {error}"))?;
        let config = json!({
            "socket": socket.to_string_lossy(),
            "focused": desktop.focused.name,
            "stills": paths,
        });
        let log = dir.path().join("picker.log");
        let log_file =
            fs::File::create(&log).map_err(|error| format!("cannot start the picker: {error}"))?;
        let mut child = session_command(session, "qs")
            .arg("-p")
            .arg(&shell)
            .env("SCOTTLAND_CAPTURE_PICK", config.to_string())
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(log_file)
            .spawn()
            .map_err(|error| format!("cannot run qs (Quickshell) for the picker: {error}"))?;

        let started = Instant::now();
        let stream = loop {
            match listener.accept() {
                Ok((stream, _)) => break stream,
                Err(error) if error.kind() == ErrorKind::WouldBlock => {}
                Err(error) => {
                    stop(&mut child);
                    return Err(format!("the picker did not connect: {error}"));
                }
            }
            if let Ok(Some(_)) = child.try_wait() {
                return Err(format!("the picker exited early: {}", log_tail(&log)));
            }
            if started.elapsed() > CONNECT_LIMIT {
                stop(&mut child);
                return Err(format!(
                    "the picker did not come up within {} seconds: {}",
                    CONNECT_LIMIT.as_secs(),
                    log_tail(&log)
                ));
            }
            thread::sleep(Duration::from_millis(20));
        };
        stream
            .set_nonblocking(false)
            .and_then(|_| stream.try_clone())
            .map(|reader| Self {
                child,
                stream,
                lines: BufReader::new(reader),
                log,
            })
            .map_err(|error| format!("cannot talk to the picker: {error}"))
    }

    /// Run the pick to its end: the picked rectangle, or `None` on cancel.
    pub fn pick(&mut self, picker: &mut Picker) -> Result<Option<Rect>, String> {
        let mut line = String::new();
        loop {
            line.clear();
            let read = self
                .lines
                .read_line(&mut line)
                .map_err(|error| format!("lost the picker: {error}"))?;
            if read == 0 {
                let _ = self.child.wait();
                return Err(format!("the picker closed: {}", log_tail(&self.log)));
            }
            let reply = match parse_event(line.trim_end()) {
                Some(Event::Hover(point)) => picker.hover(point),
                Some(Event::Release(from, to)) => picker.release(from, to),
                Some(Event::Key(key)) => picker.key(key),
                None => Reply::Nothing,
            };
            let message = match reply {
                Reply::Picked(rect) => return Ok(Some(rect)),
                Reply::Cancelled => return Ok(None),
                Reply::Nothing => continue,
                Reply::Highlight(Some(rect)) => format!(
                    "highlight {} {} {} {}",
                    rect.x, rect.y, rect.width, rect.height
                ),
                Reply::Highlight(None) => "highlight none".to_string(),
                Reply::Notice(text) => format!("notice {}", text.replace('\n', " ")),
            };
            self.send(&message)?;
        }
    }

    fn send(&mut self, message: &str) -> Result<(), String> {
        writeln!(self.stream, "{message}")
            .and_then(|_| self.stream.flush())
            .map_err(|error| format!("lost the picker: {error}"))
    }

    /// Take the overlay off the screen and wait until it is gone.
    pub fn close(mut self) {
        let _ = self.send("quit");
        stop(&mut self.child);
    }
}

impl Drop for Overlay {
    fn drop(&mut self) {
        stop(&mut self.child);
    }
}

/// Ask the overlay to quit, and make sure it has.
fn stop(child: &mut Child) {
    if let Ok(Some(_)) = child.try_wait() {
        return;
    }
    // SAFETY: kill only sends a signal to the child this command started and has not reaped.
    unsafe {
        libc::kill(child.id() as libc::pid_t, libc::SIGTERM);
    }
    if crate::run::wait_bounded(child, Duration::from_secs(2)).is_none() {
        let _ = child.kill();
        let _ = child.wait();
    }
}

fn log_tail(log: &Path) -> String {
    let text = fs::read(log).unwrap_or_default();
    if text.iter().all(u8::is_ascii_whitespace) {
        "no message from qs".to_string()
    } else {
        format!("qs said: {}", last_line(&text))
    }
}

#[derive(Debug, PartialEq)]
enum Event {
    Hover(Point),
    Release(Point, Point),
    Key(Key),
}

fn parse_event(line: &str) -> Option<Event> {
    let words: Vec<&str> = line.split(' ').collect();
    let number = |index: usize| words.get(index)?.parse::<i64>().ok();
    let point = |index: usize| {
        Some(Point {
            x: number(index)?,
            y: number(index + 1)?,
        })
    };
    match *words.first()? {
        "hover" => Some(Event::Hover(point(1)?)),
        "release" => Some(Event::Release(point(1)?, point(3)?)),
        "key" => Some(Event::Key(match *words.get(1)? {
            "tab" => Key::Tab,
            "ctrl-tab" => Key::CtrlTab,
            "left" => Key::Left,
            "right" => Key::Right,
            "up" => Key::Up,
            "down" => Key::Down,
            "return" => Key::Return,
            "ctrl-return" => Key::CtrlReturn,
            "escape" => Key::Escape,
            _ => return None,
        })),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_overlay_events() {
        assert_eq!(
            parse_event("hover -5 12"),
            Some(Event::Hover(Point { x: -5, y: 12 }))
        );
        assert_eq!(
            parse_event("release 1 2 3 4"),
            Some(Event::Release(Point { x: 1, y: 2 }, Point { x: 3, y: 4 }))
        );
        assert_eq!(
            parse_event("key ctrl-return"),
            Some(Event::Key(Key::CtrlReturn))
        );
        assert_eq!(parse_event("key space"), None);
        assert_eq!(parse_event("hover 1"), None);
        assert_eq!(parse_event("ready"), None);
    }
}
