//! Resolve a selected Scottland window to its local terminal working directory.
//!
//! The selected session and optional explicit PID are inputs. With no PID, this module asks that
//! session for its focused view. It follows Kitty's focused window when uniquely identified,
//! otherwise a uniquely identified login shell, and falls back to HOME when that relationship is
//! unavailable or ambiguous.

use crate::session::Session;
use serde_json::{Value, json};
use std::collections::HashSet;
use std::ffi::OsStr;
use std::fs;
use std::io::{self, Read, Write};
use std::os::unix::fs::{MetadataExt, PermissionsExt};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::Duration;

const MAX_FRAME: usize = 16 * 1024 * 1024;

/// Resolves a directory for an already-selected Scottland session.
///
/// `requested_pid` is an explicit command-line PID when supplied. Invalid explicit syntax is an
/// error; when omitted, the focused PID is read from the selected session.
pub fn resolve(session: &Session, requested_pid: Option<&OsStr>) -> io::Result<PathBuf> {
    resolve_for_session(Some(session), requested_pid)
}

/// Resolves a directory when a caller may also run without a Scottland session.
///
/// A missing session never triggers a focused-window query. In that case an explicit PID can
/// still identify a same-user local terminal; otherwise the caller's HOME is used.
pub fn resolve_optional(
    session: Option<&Session>,
    requested_pid: Option<&OsStr>,
) -> io::Result<PathBuf> {
    resolve_for_session(session, requested_pid)
}

fn resolve_for_session(
    session: Option<&Session>,
    requested_pid: Option<&OsStr>,
) -> io::Result<PathBuf> {
    let home = session_home(session)
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "no usable HOME is available"))?;
    let pid = match requested_pid {
        Some(pid) => Some(parse_pid(pid)?),
        None => session.and_then(|session| focused_pid(session).ok().flatten()),
    };
    Ok(directory_or_home(
        pid.and_then(|pid| terminal_directory(pid, session)),
        home,
    ))
}

fn directory_or_home(directory: Option<PathBuf>, home: PathBuf) -> PathBuf {
    directory.filter(|path| path.is_dir()).unwrap_or(home)
}

fn parse_pid(value: &OsStr) -> io::Result<u32> {
    value
        .to_str()
        .and_then(|value| value.parse::<u32>().ok())
        .filter(|pid| *pid > 1)
        .ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::InvalidInput,
                "PID must be a process ID greater than 1",
            )
        })
}

fn session_home(session: Option<&Session>) -> Option<PathBuf> {
    session
        .and_then(|session| session.var("HOME"))
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(PathBuf::from))
        .filter(|path| path.is_dir())
}

fn focused_pid(session: &Session) -> io::Result<Option<u32>> {
    let Some(socket_path) = session.wayfire_socket() else {
        return Ok(None);
    };
    let mut stream = UnixStream::connect(socket_path)?;
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(2)));
    let body = serde_json::to_vec(&json!({
        "method": "window-rules/get-focused-view",
        "data": {}
    }))
    .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
    if body.len() > MAX_FRAME {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "Wayfire request exceeds the frame limit",
        ));
    }
    let len = u32::try_from(body.len())
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "Wayfire request is too large"))?;
    stream.write_all(&len.to_le_bytes())?;
    stream.write_all(&body)?;

    let mut header = [0u8; 4];
    stream.read_exact(&mut header)?;
    let len = u32::from_le_bytes(header) as usize;
    if len > MAX_FRAME {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "Wayfire response exceeds the frame limit",
        ));
    }
    let mut response = vec![0u8; len];
    stream.read_exact(&mut response)?;
    let value: Value = serde_json::from_slice(&response)
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
    Ok(value
        .get("info")
        .and_then(|info| info.get("pid"))
        .and_then(Value::as_u64)
        .and_then(|pid| u32::try_from(pid).ok())
        .filter(|pid| *pid > 1))
}

fn terminal_directory(pid: u32, session: Option<&Session>) -> Option<PathBuf> {
    terminal_directory_at(
        Path::new("/proc"),
        Path::new("/proc/net/unix"),
        Path::new("/etc/shells"),
        pid,
        session,
        crate::dirs::uid(),
    )
}

fn terminal_directory_at(
    proc_root: &Path,
    unix_sockets: &Path,
    shells_file: &Path,
    pid: u32,
    session: Option<&Session>,
    uid: u32,
) -> Option<PathBuf> {
    if process_uid(proc_root, pid) != Some(uid) {
        return None;
    }
    if let Some(socket) = kitty_socket(proc_root, unix_sockets, pid) {
        if let Some(session) = session {
            if let Some(path) = kitty_focused_cwd(&socket, session) {
                return Some(path);
            }
        }
    }
    let pid_exe = fs::canonicalize(proc_root.join(pid.to_string()).join("exe")).ok()?;
    if is_login_shell(&pid_exe, shells_file) {
        return fs::canonicalize(proc_root.join(pid.to_string()).join("cwd")).ok();
    }
    let shell_pid = unique_login_shell_descendant(proc_root, pid, shells_file, uid)?;
    fs::canonicalize(proc_root.join(shell_pid.to_string()).join("cwd")).ok()
}

fn process_uid(proc_root: &Path, pid: u32) -> Option<u32> {
    fs::metadata(proc_root.join(pid.to_string()))
        .ok()
        .map(|metadata| metadata.uid())
}

fn unique_login_shell_descendant(
    proc_root: &Path,
    root_pid: u32,
    shells_file: &Path,
    uid: u32,
) -> Option<u32> {
    let mut pending = vec![root_pid];
    let mut visited = HashSet::from([root_pid]);
    let mut shells = Vec::new();

    while let Some(parent_pid) = pending.pop() {
        let task_dir = proc_root.join(parent_pid.to_string()).join("task");
        for task in fs::read_dir(task_dir).ok()? {
            let task = task.ok()?;
            let children = fs::read_to_string(task.path().join("children")).ok()?;
            for child_pid in children
                .split_ascii_whitespace()
                .filter_map(|pid| pid.parse::<u32>().ok())
            {
                if child_pid <= 1 || !visited.insert(child_pid) {
                    continue;
                }
                if process_uid(proc_root, child_pid) != Some(uid) {
                    continue;
                }
                let executable =
                    fs::canonicalize(proc_root.join(child_pid.to_string()).join("exe")).ok()?;
                if is_login_shell(&executable, shells_file) {
                    shells.push(child_pid);
                    if shells.len() > 1 {
                        return None;
                    }
                }
                pending.push(child_pid);
            }
        }
    }

    (shells.len() == 1).then(|| shells[0])
}

fn is_login_shell(exe: &Path, shells_file: &Path) -> bool {
    fs::read_to_string(shells_file).is_ok_and(|shells| {
        shells
            .lines()
            .map(str::trim)
            .filter(|line| !line.is_empty() && !line.starts_with('#'))
            .any(|shell| fs::canonicalize(shell).is_ok_and(|path| path == exe))
    })
}

fn kitty_socket(proc_root: &Path, unix_sockets: &Path, pid: u32) -> Option<String> {
    let process = proc_root.join(pid.to_string());
    let proc_env = fs::read(process.join("environ")).ok()?;
    for entry in proc_env.split(|byte| *byte == 0) {
        if let Some(value) = entry.strip_prefix(b"KITTY_LISTEN_ON=") {
            let value = String::from_utf8_lossy(value);
            if value.starts_with("unix:") {
                return Some(value.trim_start_matches("unix:").to_string());
            }
            if !value.is_empty() && !value.starts_with("tcp:") {
                return Some(value.to_string());
            }
        }
    }
    // Kitty can omit KITTY_LISTEN_ON from the window process environment. Match its active Unix
    // listener by inode, constrained to file descriptors owned by this same-user process.
    let fds = fs::read_dir(process.join("fd")).ok()?;
    let mut inodes = Vec::new();
    for fd in fds.flatten() {
        if let Ok(link) = fs::read_link(fd.path()) {
            if let Some(inode) = link
                .to_string_lossy()
                .strip_prefix("socket:[")
                .and_then(|value| value.strip_suffix(']'))
            {
                inodes.push(inode.to_string());
            }
        }
    }
    let unix = fs::read_to_string(unix_sockets).ok()?;
    unix.lines().skip(1).find_map(|line| {
        let columns: Vec<_> = line.split_ascii_whitespace().collect();
        if columns.len() < 8
            || columns[4] != "0001"
            || columns[5] != "01"
            || !inodes.iter().any(|inode| inode == columns[6])
        {
            return None;
        }
        let path = columns.get(7)?;
        path.starts_with('/').then(|| path.to_string())
    })
}

fn kitty_focused_cwd(socket: &str, session: &Session) -> Option<PathBuf> {
    let executable = session_program(session, "kitten")?;
    let output = Command::new(executable)
        .args(["@", "--to"])
        .arg(format!("unix:{socket}"))
        .args(["ls", "--match", "state:focused"])
        .env_clear()
        .envs(session.env.iter())
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let value: Value = serde_json::from_slice(&output.stdout).ok()?;
    let mut directories = value
        .as_array()?
        .iter()
        .flat_map(|os_window| {
            os_window
                .get("tabs")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
        })
        .flat_map(|tab| {
            tab.get("windows")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
        })
        .filter_map(|window| window.get("cwd").and_then(Value::as_str))
        .filter(|path| !path.is_empty());
    let path = PathBuf::from(directories.next()?);
    if directories.next().is_some() {
        return None;
    }
    path.is_dir().then_some(path)
}

fn session_program(session: &Session, program: &str) -> Option<PathBuf> {
    let path = Path::new(program);
    if path.components().count() > 1 {
        return Some(path.to_path_buf());
    }
    let search = session.var("PATH")?;
    std::env::split_paths(search)
        .map(|directory| directory.join(program))
        .find(|candidate| {
            fs::metadata(candidate).is_ok_and(|metadata| {
                metadata.is_file() && metadata.permissions().mode() & 0o111 != 0
            })
        })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::session::Environment;
    use std::os::unix::fs::symlink;
    use std::os::unix::net::UnixListener;
    use std::thread;

    fn session(home: &Path, socket: Option<&Path>, path: Option<&Path>) -> Session {
        let mut env = Environment::new();
        env.insert("HOME".into(), home.as_os_str().to_owned());
        if let Some(socket) = socket {
            env.insert("WAYFIRE_SOCKET".into(), socket.as_os_str().to_owned());
        }
        if let Some(path) = path {
            env.insert("PATH".into(), path.as_os_str().to_owned());
        }
        Session {
            name: "wayland-test".to_owned(),
            env,
        }
    }

    fn proc_fixture(root: &Path, pid: u32, children: &[u32], exe: &Path, cwd: &Path) {
        let process = root.join(pid.to_string());
        let task = process.join("task").join(pid.to_string());
        fs::create_dir_all(&task).unwrap();
        fs::write(
            task.join("children"),
            children
                .iter()
                .map(u32::to_string)
                .collect::<Vec<_>>()
                .join(" "),
        )
        .unwrap();
        symlink(exe, process.join("exe")).unwrap();
        symlink(cwd, process.join("cwd")).unwrap();
    }

    #[test]
    fn selected_session_focus_query_returns_its_focused_pid() {
        let socket = std::env::temp_dir().join(format!("scot-cwd-{}.sock", std::process::id()));
        let _ = fs::remove_file(&socket);
        let listener = UnixListener::bind(&socket).unwrap();
        let worker = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut header = [0u8; 4];
            stream.read_exact(&mut header).unwrap();
            let mut body = vec![0; u32::from_le_bytes(header) as usize];
            stream.read_exact(&mut body).unwrap();
            let request: Value = serde_json::from_slice(&body).unwrap();
            assert_eq!(request["method"], "window-rules/get-focused-view");
            let reply = serde_json::to_vec(&json!({"info": {"pid": 1234}})).unwrap();
            stream
                .write_all(&(reply.len() as u32).to_le_bytes())
                .and_then(|_| stream.write_all(&reply))
                .unwrap();
        });
        let session = session(Path::new("/tmp"), Some(&socket), None);
        assert_eq!(focused_pid(&session).unwrap(), Some(1234));
        worker.join().unwrap();
        fs::remove_file(socket).unwrap();
    }

    #[test]
    fn terminal_directory_uses_same_user_login_shell_and_refuses_other_owners() {
        let root = std::env::temp_dir().join(format!("scot-cwd-proc-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        let terminal = root.join("terminal");
        let shell = root.join("shell");
        fs::create_dir_all(&terminal).unwrap();
        fs::create_dir_all(&shell).unwrap();
        proc_fixture(&root, 101, &[102], Path::new("/bin/sleep"), &terminal);
        proc_fixture(&root, 102, &[], Path::new("/bin/sh"), &shell);
        let shells = root.join("shells");
        fs::write(
            &shells,
            format!("{}\n", fs::canonicalize("/bin/sh").unwrap().display()),
        )
        .unwrap();
        let unix_sockets = root.join("unix");
        fs::write(
            &unix_sockets,
            "Num RefCount Protocol Flags Type St Inode Path\n",
        )
        .unwrap();
        let uid = fs::metadata(&root).unwrap().uid();
        assert_eq!(
            terminal_directory_at(&root, &unix_sockets, &shells, 101, None, uid),
            Some(shell)
        );
        assert_eq!(
            terminal_directory_at(&root, &unix_sockets, &shells, 101, None, uid + 1),
            None
        );
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn terminal_directory_uses_a_unique_shell_across_mixed_depth_branches() {
        let root = std::env::temp_dir().join(format!("scot-cwd-tree-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        let terminal = root.join("terminal");
        let shell_cwd = root.join("shell-cwd");
        fs::create_dir_all(&terminal).unwrap();
        fs::create_dir_all(&shell_cwd).unwrap();
        proc_fixture(&root, 101, &[102, 103], Path::new("/bin/sleep"), &terminal);
        proc_fixture(&root, 102, &[104], Path::new("/bin/sleep"), &terminal);
        proc_fixture(&root, 103, &[], Path::new("/bin/sleep"), &terminal);
        proc_fixture(&root, 104, &[], Path::new("/bin/sh"), &shell_cwd);
        let shells = root.join("shells");
        fs::write(
            &shells,
            format!("{}\n", fs::canonicalize("/bin/sh").unwrap().display()),
        )
        .unwrap();
        let unix_sockets = root.join("unix");
        fs::write(
            &unix_sockets,
            "Num RefCount Protocol Flags Type St Inode Path\n",
        )
        .unwrap();
        let uid = fs::metadata(&root).unwrap().uid();

        assert_eq!(
            terminal_directory_at(&root, &unix_sockets, &shells, 101, None, uid),
            Some(shell_cwd)
        );
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn terminal_directory_rejects_multiple_shell_branches_as_ambiguous() {
        let root = std::env::temp_dir().join(format!("scot-cwd-ambiguous-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        let terminal = root.join("terminal");
        let first_cwd = root.join("first-cwd");
        let second_cwd = root.join("second-cwd");
        for directory in [&terminal, &first_cwd, &second_cwd] {
            fs::create_dir_all(directory).unwrap();
        }
        proc_fixture(&root, 101, &[102, 103], Path::new("/bin/sleep"), &terminal);
        proc_fixture(&root, 102, &[104], Path::new("/bin/sleep"), &terminal);
        proc_fixture(&root, 103, &[], Path::new("/bin/sh"), &first_cwd);
        proc_fixture(&root, 104, &[], Path::new("/bin/sh"), &second_cwd);
        let shells = root.join("shells");
        fs::write(
            &shells,
            format!("{}\n", fs::canonicalize("/bin/sh").unwrap().display()),
        )
        .unwrap();
        let unix_sockets = root.join("unix");
        fs::write(
            &unix_sockets,
            "Num RefCount Protocol Flags Type St Inode Path\n",
        )
        .unwrap();
        let uid = fs::metadata(&root).unwrap().uid();

        let unresolved = terminal_directory_at(&root, &unix_sockets, &shells, 101, None, uid);
        assert_eq!(unresolved, None);
        assert_eq!(directory_or_home(unresolved, root.clone()), root);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn kitty_focused_cwd_reads_os_window_then_tab_then_window() {
        let root = std::env::temp_dir().join(format!("scot-cwd-kitty-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        let bin = root.join("bin");
        let cwd = root.join("kitty-cwd");
        fs::create_dir_all(&bin).unwrap();
        fs::create_dir_all(&cwd).unwrap();
        let script = bin.join("kitten");
        fs::write(
            &script,
            format!(
                "#!/bin/sh\nprintf '%s' '[{{\"id\":7,\"tabs\":[{{\"id\":8,\"windows\":[{{\"cwd\":\"{}\",\"is_focused\":true}}]}}]}}]'\n",
                cwd.display()
            ),
        )
        .unwrap();
        let mut permissions = fs::metadata(&script).unwrap().permissions();
        permissions.set_mode(0o755);
        fs::set_permissions(&script, permissions).unwrap();
        let session = session(&root, None, Some(&bin));
        assert_eq!(
            kitty_focused_cwd("/tmp/kitty-test.sock", &session),
            Some(cwd)
        );
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn explicit_pid_syntax_is_an_error_and_unresolved_pid_uses_home() {
        let root = std::env::temp_dir().join(format!("scot-cwd-home-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        let session = session(&root, None, None);
        assert_eq!(
            resolve(&session, Some(OsStr::new("not-a-pid")))
                .unwrap_err()
                .kind(),
            io::ErrorKind::InvalidInput
        );
        assert_eq!(
            resolve(&session, Some(OsStr::new("4000000000"))).unwrap(),
            root
        );
    }
}
