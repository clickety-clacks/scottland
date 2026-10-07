//! The front command run as a user runs it, against tools the tests install in their own dirs.

use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::sync::Mutex;

/// Held while a test writes an executable or starts a process. Tests run in parallel threads, and
/// a process forked while another thread still has a file open for writing would keep that file
/// busy (ETXTBSY) for whoever executes it next.
static FILES: Mutex<()> = Mutex::new(());

fn lock() -> std::sync::MutexGuard<'static, ()> {
    FILES
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

/// A scratch dir of the test's own, under the build's tmp dir, removed when dropped.
struct Scratch {
    dir: PathBuf,
}

impl Scratch {
    fn new(test: &str) -> Self {
        let dir = Path::new(env!("CARGO_TARGET_TMPDIR"))
            .join(format!("front-{}-{test}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        Scratch { dir }
    }

    fn subdir(&self, name: &str) -> PathBuf {
        let dir = self.dir.join(name);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    /// A tool that reports which copy ran and what it was given.
    fn tool(&self, dir: &str, name: &str, mode: u32) -> PathBuf {
        let path = self.subdir(dir).join(format!("scottland-{name}"));
        let _lock = lock();
        fs::write(
            &path,
            format!("#!/bin/sh\necho \"{dir}/{name}:$*\"\nexit 7\n"),
        )
        .unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(mode)).unwrap();
        path
    }

    /// Runs `front` with PATH set to the given subdirs only.
    fn run(&self, front: &Path, path: &[&str], args: &[&str]) -> Output {
        let path = std::env::join_paths(path.iter().map(|d| self.dir.join(d))).unwrap();
        let child = {
            let _lock = lock();
            Command::new(front)
                .args(args)
                .env("PATH", path)
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .spawn()
                .unwrap()
        };
        child.wait_with_output().unwrap()
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.dir);
    }
}

const FRONT: &str = env!("CARGO_BIN_EXE_scottland");

fn stdout(out: &Output) -> String {
    String::from_utf8_lossy(&out.stdout).into_owned()
}

#[test]
fn runs_the_named_tool_with_its_arguments_and_exit_status() {
    let s = Scratch::new("run");
    s.tool("a", "zzfront-hello", 0o755);
    let out = s.run(
        Path::new(FRONT),
        &["a"],
        &["zzfront-hello", "one", "two words"],
    );
    assert_eq!(stdout(&out), "a/zzfront-hello:one two words\n");
    assert_eq!(out.status.code(), Some(7));
}

#[test]
fn help_tool_asks_the_tool_for_its_help() {
    let s = Scratch::new("help");
    s.tool("a", "zzfront-hello", 0o755);
    let out = s.run(Path::new(FRONT), &["a"], &["help", "zzfront-hello"]);
    assert_eq!(stdout(&out), "a/zzfront-hello:--help\n");
}

#[test]
fn the_first_tool_on_path_wins_and_non_executables_are_skipped() {
    let s = Scratch::new("order");
    s.tool("a", "zzfront-plain", 0o644);
    s.tool("b", "zzfront-plain", 0o755);
    s.tool("c", "zzfront-plain", 0o755);
    let out = s.run(Path::new(FRONT), &["a", "b", "c"], &["zzfront-plain"]);
    assert_eq!(stdout(&out), "b/zzfront-plain:\n");
}

#[test]
fn tools_beside_the_front_command_come_before_path() {
    let s = Scratch::new("own");
    let front = s.subdir("own").join("scottland");
    {
        let _lock = lock();
        fs::copy(FRONT, &front).unwrap();
    }
    s.tool("own", "zzfront-dup", 0o755);
    s.tool("a", "zzfront-dup", 0o755);
    let out = s.run(&front, &["a"], &["zzfront-dup"]);
    assert_eq!(stdout(&out), "own/zzfront-dup:\n");
}

#[test]
fn lists_each_tool_once_and_ignores_what_cannot_run() {
    let s = Scratch::new("list");
    s.tool("a", "zzfront-one", 0o755);
    s.tool("b", "zzfront-one", 0o755);
    s.tool("b", "zzfront-two", 0o755);
    s.tool("b", "zzfront-off", 0o644);
    fs::create_dir_all(s.dir.join("b/scottland-zzfront-dir")).unwrap();
    let out = s.run(Path::new(FRONT), &["a", "b"], &[]);
    assert!(out.status.success());
    let listed: Vec<String> = stdout(&out)
        .lines()
        .filter_map(|line| line.trim().strip_prefix("zzfront-").map(str::to_string))
        .collect();
    assert_eq!(listed, ["one", "two"]);
}

#[test]
fn an_unknown_tool_is_an_error() {
    let s = Scratch::new("unknown");
    let out = s.run(Path::new(FRONT), &["a"], &["zzfront-absent"]);
    assert_eq!(out.status.code(), Some(1));
    assert!(String::from_utf8_lossy(&out.stderr).contains("no tool named"));
}

#[test]
fn a_name_that_is_a_path_or_option_is_refused() {
    let s = Scratch::new("names");
    s.tool("a", "zzfront-hello", 0o755);
    for args in [
        &["../a/scottland-zzfront-hello"][..],
        &["--bogus"],
        &["help", "a/b"],
        &["help", "-x"],
    ] {
        let out = s.run(Path::new(FRONT), &["a"], args);
        assert_eq!(out.status.code(), Some(2), "{args:?}");
        assert!(out.stdout.is_empty(), "{args:?}");
    }
}

#[test]
fn prints_its_version() {
    let s = Scratch::new("version");
    let out = s.run(Path::new(FRONT), &[], &["--version"]);
    assert_eq!(
        stdout(&out),
        format!("scottland {}\n", env!("CARGO_PKG_VERSION"))
    );
}
