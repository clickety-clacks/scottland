//! Running Scottland sessions and the environments they recorded.
//!
//! Each session writes its own environment at startup (`autostart.d/01-record-environment`) to
//! `RUNTIME/<WAYLAND_DISPLAY>.env`, NUL-separated `KEY=VALUE` entries. A tool that acts on a
//! session uses that environment, never the caller's: an ssh shell or agent belongs to wherever it
//! runs, and its `WAYLAND_DISPLAY` or Hyprland signature would reach the wrong desktop. These are
//! the rules `scottland-exec` follows.

use std::collections::BTreeMap;
use std::ffi::{OsStr, OsString};
use std::fmt;
use std::fs;
use std::io;
use std::os::unix::ffi::{OsStrExt, OsStringExt};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};

/// A session's recorded environment.
pub type Environment = BTreeMap<OsString, OsString>;

/// A running session: its display name (`wayland-1`) and recorded environment.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Session {
    pub name: String,
    pub env: Environment,
}

impl Session {
    pub fn var(&self, key: &str) -> Option<&OsStr> {
        self.env.get(OsStr::new(key)).map(OsString::as_os_str)
    }

    /// The session's Wayfire IPC socket.
    pub fn wayfire_socket(&self) -> Option<&Path> {
        self.var("WAYFIRE_SOCKET").map(Path::new)
    }
}

/// Why no single session could be chosen.
#[derive(Debug, PartialEq, Eq)]
pub enum SessionError {
    /// A display name that is not a plain file name.
    InvalidDisplay(String),
    /// The named display has no running session.
    NotRunning(String),
    /// No session is running.
    NoneRunning,
    /// Several sessions run and nothing picks one; their names.
    Several(Vec<String>),
}

impl fmt::Display for SessionError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidDisplay(name) => write!(f, "invalid display name {name:?}"),
            Self::NotRunning(name) => write!(f, "no running Scottland session on {name}"),
            Self::NoneRunning => write!(f, "no running Scottland session found"),
            Self::Several(names) => {
                write!(
                    f,
                    "several sessions are running ({}); pick one with --display",
                    names.join(", ")
                )
            }
        }
    }
}

impl std::error::Error for SessionError {}

/// Reads a recorded environment file. Entries without `=` are skipped.
pub fn read_env(path: &Path) -> io::Result<Environment> {
    let bytes = fs::read(path)?;
    Ok(parse_env(&bytes))
}

fn parse_env(bytes: &[u8]) -> Environment {
    bytes
        .split(|&b| b == 0)
        .filter_map(|entry| {
            let eq = entry.iter().position(|&b| b == b'=')?;
            Some((
                OsString::from_vec(entry[..eq].to_vec()),
                OsString::from_vec(entry[eq + 1..].to_vec()),
            ))
        })
        .collect()
}

/// True when the environment's Wayfire answers on its IPC socket; a stale socket file left by a
/// session that ended does not count.
pub fn is_alive(env: &Environment) -> bool {
    match env.get(OsStr::new("WAYFIRE_SOCKET")) {
        Some(socket) if !socket.is_empty() => UnixStream::connect(socket).is_ok(),
        _ => false,
    }
}

/// Every running session recorded in `runtime`, by display name.
pub fn running(runtime: &Path) -> Vec<Session> {
    let Ok(entries) = fs::read_dir(runtime) else {
        return Vec::new();
    };
    let mut files: Vec<(String, PathBuf)> = entries
        .filter_map(Result::ok)
        .filter_map(|entry| {
            let file = entry.file_name();
            let name = file.as_bytes().strip_suffix(b".env")?;
            if name.is_empty() || name.starts_with(b".") {
                return None;
            }
            Some((String::from_utf8_lossy(name).into_owned(), entry.path()))
        })
        .collect();
    files.sort();
    files
        .into_iter()
        .filter_map(|(name, path)| {
            let env = read_env(&path).ok()?;
            is_alive(&env).then_some(Session { name, env })
        })
        .collect()
}

/// The session a command should act on: the one named by `display`; else the one whose Wayfire
/// socket the caller already uses (`caller_socket`, the caller's `WAYFIRE_SOCKET`); else the only
/// one running. A named display is looked up alone, so it never probes unrelated sessions (such as
/// another agent's isolated test session).
pub fn select(
    runtime: &Path,
    display: Option<&str>,
    caller_socket: Option<&OsStr>,
) -> Result<Session, SessionError> {
    if let Some(name) = display {
        if name.is_empty() || name.contains('/') || name == "." || name == ".." {
            return Err(SessionError::InvalidDisplay(name.to_string()));
        }
        let env = read_env(&runtime.join(format!("{name}.env"))).unwrap_or_default();
        return if is_alive(&env) {
            Ok(Session {
                name: name.to_string(),
                env,
            })
        } else {
            Err(SessionError::NotRunning(name.to_string()))
        };
    }
    let mut live = running(runtime);
    if let Some(caller) = caller_socket.filter(|socket| !socket.is_empty()) {
        if let Some(index) = live
            .iter()
            .position(|s| s.var("WAYFIRE_SOCKET") == Some(caller))
        {
            return Ok(live.swap_remove(index));
        }
    }
    match live.len() {
        0 => Err(SessionError::NoneRunning),
        1 => Ok(live.remove(0)),
        _ => Err(SessionError::Several(
            live.into_iter().map(|s| s.name).collect(),
        )),
    }
}

/// [`select`] in the standard runtime directory, honouring the caller's `WAYFIRE_SOCKET`.
pub fn current(display: Option<&str>) -> Result<Session, SessionError> {
    let caller = std::env::var_os("WAYFIRE_SOCKET");
    select(&crate::dirs::runtime_dir(), display, caller.as_deref())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_nul_separated_entries() {
        let env = parse_env(b"A=1\0B=x=y\0NOEQUALS\0\0C=\0");
        let pairs: Vec<(&str, &str)> = env
            .iter()
            .map(|(k, v)| (k.to_str().unwrap(), v.to_str().unwrap()))
            .collect();
        assert_eq!(pairs, [("A", "1"), ("B", "x=y"), ("C", "")]);
    }

    #[test]
    fn keeps_non_utf8_values() {
        let env = parse_env(b"P=/tmp/\xff\0");
        assert_eq!(env[OsStr::new("P")].as_bytes(), b"/tmp/\xff");
    }

    #[test]
    fn missing_or_empty_socket_is_not_alive() {
        assert!(!is_alive(&parse_env(b"WAYLAND_DISPLAY=wayland-1\0")));
        assert!(!is_alive(&parse_env(b"WAYFIRE_SOCKET=\0")));
    }
}
