use std::fs;
use std::os::unix::net::UnixListener;
use std::path::{Path, PathBuf};

/// A runtime directory of the test's own, under the build's tmp dir, removed when dropped. Names
/// stay short: a socket path longer than about 100 bytes cannot be bound.
pub(crate) struct Runtime {
    pub(crate) dir: PathBuf,
}

impl Runtime {
    pub(crate) fn new(test: &str) -> Self {
        let dir =
            Path::new(env!("CARGO_TARGET_TMPDIR")).join(format!("s{}-{test}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        Runtime { dir }
    }

    /// Records a session on `display` whose Wayfire socket is at `socket`.
    pub(crate) fn record(&self, display: &str, socket: &Path) {
        let body = format!(
            "WAYLAND_DISPLAY={display}\0WAYFIRE_SOCKET={}\0",
            socket.display()
        );
        fs::write(self.dir.join(format!("{display}.env")), body).unwrap();
    }

    /// A session whose Wayfire is listening; keep the listener alive for the session to be live.
    pub(crate) fn live(&self, display: &str) -> (UnixListener, PathBuf) {
        let socket = self
            .dir
            .join(format!("{}.s", display.trim_start_matches("wayland-")));
        let listener = UnixListener::bind(&socket)
            .unwrap_or_else(|e| panic!("binding {}: {e}", socket.display()));
        self.record(display, &socket);
        (listener, socket)
    }

    /// A session that ended: its env file and socket file remain, nothing listens.
    pub(crate) fn stale(&self, display: &str) {
        let (listener, _) = self.live(display);
        drop(listener);
    }
}

impl Drop for Runtime {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.dir);
    }
}
