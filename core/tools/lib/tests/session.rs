//! Session selection against real recorded files and real sockets: a live session is one whose
//! Wayfire socket accepts a connection, here a listener the test binds.

use scottland::session::{self, SessionError};
use std::ffi::OsStr;
use std::fs;
use std::os::unix::net::UnixListener;
use std::path::{Path, PathBuf};

/// A runtime directory of the test's own, under the build's tmp dir, removed when dropped. Names
/// stay short: a socket path longer than about 100 bytes cannot be bound.
struct Runtime {
    dir: PathBuf,
}

impl Runtime {
    fn new(test: &str) -> Self {
        let dir =
            Path::new(env!("CARGO_TARGET_TMPDIR")).join(format!("s{}-{test}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        Runtime { dir }
    }

    /// Records a session on `display` whose Wayfire socket is at `socket`.
    fn record(&self, display: &str, socket: &Path) {
        let body = format!(
            "WAYLAND_DISPLAY={display}\0WAYFIRE_SOCKET={}\0",
            socket.display()
        );
        fs::write(self.dir.join(format!("{display}.env")), body).unwrap();
    }

    /// A session whose Wayfire is listening; keep the listener alive for the session to be live.
    fn live(&self, display: &str) -> (UnixListener, PathBuf) {
        let socket = self
            .dir
            .join(format!("{}.s", display.trim_start_matches("wayland-")));
        let listener = UnixListener::bind(&socket)
            .unwrap_or_else(|e| panic!("binding {}: {e}", socket.display()));
        self.record(display, &socket);
        (listener, socket)
    }

    /// A session that ended: its env file and socket file remain, nothing listens.
    fn stale(&self, display: &str) {
        let (listener, _) = self.live(display);
        drop(listener);
    }
}

impl Drop for Runtime {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.dir);
    }
}

fn names(sessions: &[session::Session]) -> Vec<&str> {
    sessions.iter().map(|s| s.name.as_str()).collect()
}

#[test]
fn lists_only_live_sessions_in_display_order() {
    let rt = Runtime::new("ls");
    let _two = rt.live("wayland-2");
    let _one = rt.live("wayland-1");
    rt.stale("wayland-3");
    fs::write(rt.dir.join("wayland-4.env.tmp"), "").unwrap();
    assert_eq!(
        names(&session::running(&rt.dir)),
        ["wayland-1", "wayland-2"]
    );
}

#[test]
fn a_missing_runtime_dir_has_no_sessions() {
    let rt = Runtime::new("mi");
    assert!(session::running(&rt.dir.join("absent")).is_empty());
}

#[test]
fn the_only_live_session_is_chosen() {
    let rt = Runtime::new("on");
    let (_l, socket) = rt.live("wayland-1");
    rt.stale("wayland-2");
    let chosen = session::select(&rt.dir, None, None).unwrap();
    assert_eq!(chosen.name, "wayland-1");
    assert_eq!(chosen.wayfire_socket(), Some(socket.as_path()));
}

#[test]
fn the_callers_own_session_wins_among_several() {
    let rt = Runtime::new("ca");
    let _one = rt.live("wayland-1");
    let (_two, socket) = rt.live("wayland-2");
    let chosen = session::select(&rt.dir, None, Some(socket.as_os_str())).unwrap();
    assert_eq!(chosen.name, "wayland-2");
}

#[test]
fn several_sessions_without_a_pick_are_refused() {
    let rt = Runtime::new("sv");
    let _one = rt.live("wayland-1");
    let _two = rt.live("wayland-2");
    let unrelated = OsStr::new("/elsewhere/wayfire.sock");
    assert_eq!(
        session::select(&rt.dir, None, Some(unrelated)),
        Err(SessionError::Several(vec![
            "wayland-1".into(),
            "wayland-2".into()
        ]))
    );
}

#[test]
fn no_live_session_is_refused() {
    let rt = Runtime::new("no");
    rt.stale("wayland-1");
    assert_eq!(
        session::select(&rt.dir, None, None),
        Err(SessionError::NoneRunning)
    );
}

#[test]
fn a_named_display_is_used_even_among_several() {
    let rt = Runtime::new("na");
    let _one = rt.live("wayland-1");
    let _two = rt.live("wayland-2");
    assert_eq!(
        session::select(&rt.dir, Some("wayland-2"), None)
            .unwrap()
            .name,
        "wayland-2"
    );
}

#[test]
fn a_named_display_that_ended_or_never_ran_is_refused() {
    let rt = Runtime::new("ng");
    let _one = rt.live("wayland-1");
    rt.stale("wayland-2");
    for name in ["wayland-2", "wayland-9"] {
        assert_eq!(
            session::select(&rt.dir, Some(name), None),
            Err(SessionError::NotRunning(name.into()))
        );
    }
}

#[test]
fn a_display_name_must_be_a_plain_file_name() {
    let rt = Runtime::new("in");
    for name in ["", "..", "../wayland-1", "a/b"] {
        assert_eq!(
            session::select(&rt.dir, Some(name), None),
            Err(SessionError::InvalidDisplay(name.into()))
        );
    }
}
