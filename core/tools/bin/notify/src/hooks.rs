//! The notify hook interface (receiver contract section 5).
//!
//! The freedesktop protocol leaves out dismissing by summary, do-not-disturb, glyphs, a
//! receiver that runs click commands itself, and the distro's reminder panel. An installed
//! integration answers those through executables in `notify.d`, found the way `focus.d` hooks
//! are, so core never names a distro or its shell.

use std::env;
use std::ffi::OsString;
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

/// A hook exits 69 for an operation it cannot perform now: one it does not know, or a receiver
/// that is unreachable or is not the one that owns the notification bus name.
pub const NOT_THIS_HOOK: i32 = 69;
/// A hook exits 65 when the active receiver would not honor this particular request.
pub const UNSUPPORTED: i32 = 65;

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum HookAnswer {
    /// The first hook that did not exit 69.
    Answered {
        hook: String,
        /// `None` when the hook was ended by a signal.
        status: Option<i32>,
        stdout: Vec<u8>,
        stderr: String,
    },
    /// No hooks, or every hook exited 69.
    NotAvailable { last_stderr: Option<String> },
}

impl HookAnswer {
    pub fn status(&self) -> Option<i32> {
        match self {
            Self::Answered { status, .. } => *status,
            Self::NotAvailable { .. } => None,
        }
    }
}

pub trait Hooks {
    /// Ask the hooks for `operation`, each argument one argv element, and return the answer.
    fn ask(&mut self, operation: &str, args: &[String]) -> Result<HookAnswer, String>;
}

/// The `notify.d` executables of the running system.
pub struct SystemHooks {
    paths: Vec<PathBuf>,
}

impl SystemHooks {
    pub fn from_environment() -> Self {
        Self::from_variables(|name| env::var_os(name))
    }

    fn from_variables(variable: impl Fn(&str) -> Option<OsString>) -> Self {
        let var = |name: &str| variable(name).filter(|value| !value.is_empty());
        // SCOTTLAND_NOTIFY_HOOKS names one folder to use instead (a test session's own).
        let directories = if let Some(only) = var("SCOTTLAND_NOTIFY_HOOKS") {
            vec![PathBuf::from(only)]
        } else {
            let installed = var("SCOTTLAND_HOOKS")
                .map_or_else(|| PathBuf::from("/usr/lib/scottland"), PathBuf::from)
                .join("notify.d");
            let config = var("XDG_CONFIG_HOME")
                .map(PathBuf::from)
                .or_else(|| var("HOME").map(|home| PathBuf::from(home).join(".config")));
            let mut directories = vec![installed];
            directories.extend(config.map(|config| config.join("scottland/notify.d")));
            directories
        };
        Self {
            paths: directories
                .iter()
                .flat_map(|directory| executables_in(directory))
                .collect(),
        }
    }
}

fn executables_in(directory: &Path) -> Vec<PathBuf> {
    let Ok(entries) = fs::read_dir(directory) else {
        return Vec::new();
    };
    let mut paths: Vec<PathBuf> = entries
        .filter_map(|entry| entry.ok().map(|entry| entry.path()))
        .filter(|path| {
            fs::metadata(path).is_ok_and(|metadata| {
                metadata.is_file() && metadata.permissions().mode() & 0o111 != 0
            })
        })
        .collect();
    paths.sort();
    paths
}

impl Hooks for SystemHooks {
    fn ask(&mut self, operation: &str, args: &[String]) -> Result<HookAnswer, String> {
        let mut last_stderr = None;
        for path in &self.paths {
            let hook = path.file_name().map_or_else(
                || path.display().to_string(),
                |name| name.to_string_lossy().into_owned(),
            );
            let output = Command::new(path)
                .arg(operation)
                .args(args)
                .stdin(Stdio::null())
                .output()
                .map_err(|error| format!("could not run notify hook {hook}: {error}"))?;
            let stderr = String::from_utf8_lossy(&output.stderr).into_owned();
            if output.status.code() == Some(NOT_THIS_HOOK) {
                last_stderr = last_line(&stderr);
                continue;
            }
            return Ok(HookAnswer::Answered {
                hook,
                status: output.status.code(),
                stdout: output.stdout,
                stderr,
            });
        }
        Ok(HookAnswer::NotAvailable { last_stderr })
    }
}

fn last_line(text: &str) -> Option<String> {
    text.lines()
        .map(str::trim)
        .rfind(|line| !line.is_empty())
        .map(str::to_string)
}

/// The failure for an operation no hook answered.
pub fn not_available(message: &str, answer: &HookAnswer) -> String {
    match answer {
        HookAnswer::NotAvailable {
            last_stderr: Some(line),
        } => format!("{message} ({line})"),
        _ => message.to_string(),
    }
}

/// The failure for a hook answer that is neither success nor the operation's defined result.
pub fn hook_failure(operation: &str, answer: &HookAnswer) -> String {
    let HookAnswer::Answered {
        hook,
        status,
        stderr,
        ..
    } = answer
    else {
        return format!("no notify hook answered {operation}");
    };
    let stderr = stderr.trim();
    if !stderr.is_empty() {
        return stderr.to_string();
    }
    match status {
        Some(0) => format!("notify hook {hook} answered {operation} with output core cannot read"),
        Some(code) => format!("notify hook {hook} failed {operation} with exit status {code}"),
        None => format!("notify hook {hook} was stopped by a signal during {operation}"),
    }
}

/// The reason a hook gave for a 65 answer, for appending to core's own message.
pub fn reason(answer: &HookAnswer) -> String {
    match answer {
        HookAnswer::Answered { stderr, .. } if !stderr.trim().is_empty() => {
            format!(": {}", stderr.trim())
        }
        _ => String::new(),
    }
}

/// The stdout of an exit-0 answer, without its trailing newline.
pub fn stdout_line(answer: &HookAnswer) -> Option<&str> {
    match answer {
        HookAnswer::Answered { stdout, .. } => std::str::from_utf8(stdout)
            .ok()
            .map(|text| text.strip_suffix('\n').unwrap_or(text)),
        HookAnswer::NotAvailable { .. } => None,
    }
}

/// `action-route` stdout: keys and values, every field ended by a NUL byte. `None` when it is
/// empty, has an odd number of fields, an empty key, text that is not UTF-8, or no final NUL.
pub fn parse_string_hints(stdout: &[u8]) -> Option<Vec<(String, String)>> {
    let fields = stdout.strip_suffix(b"\0")?;
    let fields: Vec<&[u8]> = fields.split(|byte| *byte == 0).collect();
    if fields.len() % 2 != 0 {
        return None;
    }
    fields
        .chunks(2)
        .map(|pair| {
            let key = std::str::from_utf8(pair[0]).ok()?;
            let value = std::str::from_utf8(pair[1]).ok()?;
            (!key.is_empty()).then(|| (key.to_string(), value.to_string()))
        })
        .collect()
}

#[cfg(test)]
pub mod testing {
    use super::*;

    /// Scripted hooks: each operation answers with the queued status and stdout; an operation
    /// with nothing queued gets no answer.
    #[derive(Default)]
    pub struct FakeHooks {
        pub calls: Vec<Vec<String>>,
        answers: Vec<(String, HookAnswer)>,
    }

    impl FakeHooks {
        pub fn answer(mut self, operation: &str, status: i32, stdout: &[u8], stderr: &str) -> Self {
            self.answers.push((
                operation.to_string(),
                HookAnswer::Answered {
                    hook: "10-fake".to_string(),
                    status: Some(status),
                    stdout: stdout.to_vec(),
                    stderr: stderr.to_string(),
                },
            ));
            self
        }
    }

    impl Hooks for FakeHooks {
        fn ask(&mut self, operation: &str, args: &[String]) -> Result<HookAnswer, String> {
            let mut call = vec![operation.to_string()];
            call.extend(args.iter().cloned());
            self.calls.push(call);
            Ok(self
                .answers
                .iter()
                .find(|(queued, _)| queued == operation)
                .map_or(
                    HookAnswer::NotAvailable { last_stderr: None },
                    |(_, answer)| answer.clone(),
                ))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn hook(directory: &Path, name: &str, body: &str, executable: bool) {
        let path = directory.join(name);
        fs::write(&path, format!("#!/bin/sh\n{body}\n")).unwrap();
        let mode = if executable { 0o755 } else { 0o644 };
        fs::set_permissions(&path, fs::Permissions::from_mode(mode)).unwrap();
    }

    fn scratch(name: &str) -> PathBuf {
        let directory = env::temp_dir().join(format!(
            "scottland-notify-hooks-{name}-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&directory);
        fs::create_dir_all(&directory).unwrap();
        directory
    }

    #[test]
    fn runs_only_executable_regular_files_in_name_order_with_one_argv_element_each() {
        let directory = scratch("order");
        let log = directory.join("log");
        hook(
            &directory,
            "20-b",
            &format!("echo b \"$#\" \"$1\" \"$2\" >>'{}'; exit 0", log.display()),
            true,
        );
        hook(
            &directory,
            "10-a",
            &format!("echo a >>'{}'; echo 'a cannot' >&2; exit 69", log.display()),
            true,
        );
        hook(
            &directory,
            "15-x",
            &format!("echo x >>'{}'; exit 0", log.display()),
            false,
        );
        hook(
            &directory,
            "30-c",
            &format!("echo c >>'{}'; exit 0", log.display()),
            true,
        );
        fs::create_dir(directory.join("05-dir")).unwrap();
        let mut hooks = SystemHooks {
            paths: executables_in(&directory),
        };

        let answer = hooks.ask("dismiss", &["a; rm -rf ~".to_string()]).unwrap();

        assert_eq!(answer.status(), Some(0));
        assert_eq!(
            fs::read_to_string(&log).unwrap(),
            "a\nb 2 dismiss a; rm -rf ~\n"
        );
        fs::remove_dir_all(&directory).unwrap();
    }

    #[test]
    fn every_hook_declining_is_not_available_with_the_last_hooks_stderr_line() {
        let directory = scratch("declined");
        hook(&directory, "10-a", "echo first >&2; exit 69", true);
        hook(
            &directory,
            "20-b",
            "echo 'shell is not running' >&2; exit 69",
            true,
        );
        let mut hooks = SystemHooks {
            paths: executables_in(&directory),
        };

        let answer = hooks.ask("dnd-toggle", &[]).unwrap();

        assert_eq!(
            answer,
            HookAnswer::NotAvailable {
                last_stderr: Some("shell is not running".into())
            }
        );
        assert_eq!(
            not_available("notification silencing is not available", &answer),
            "notification silencing is not available (shell is not running)"
        );
        let mut empty = SystemHooks { paths: Vec::new() };
        assert_eq!(
            empty.ask("dismiss", &["X".into()]).unwrap(),
            HookAnswer::NotAvailable { last_stderr: None }
        );
        fs::remove_dir_all(&directory).unwrap();
    }

    #[test]
    fn discovery_uses_only_the_test_folder_when_one_is_named() {
        let directory = scratch("env");
        hook(&directory, "10-a", "exit 0", true);
        hook(&directory, "20-b", "exit 0", false);
        let only = directory.clone();
        let hooks = SystemHooks::from_variables(|name| match name {
            "SCOTTLAND_NOTIFY_HOOKS" => Some(only.clone().into()),
            "SCOTTLAND_HOOKS" => Some("/nonexistent".into()),
            _ => None,
        });
        assert_eq!(hooks.paths, [directory.join("10-a")]);

        let installed = scratch("installed");
        fs::create_dir_all(installed.join("notify.d")).unwrap();
        fs::create_dir_all(installed.join("config/scottland/notify.d")).unwrap();
        hook(&installed.join("notify.d"), "50-system", "exit 0", true);
        hook(
            &installed.join("config/scottland/notify.d"),
            "10-user",
            "exit 0",
            true,
        );
        let root = installed.clone();
        let hooks = SystemHooks::from_variables(|name| match name {
            "SCOTTLAND_NOTIFY_HOOKS" => Some("".into()),
            "SCOTTLAND_HOOKS" => Some(root.clone().into()),
            "XDG_CONFIG_HOME" => Some(root.join("config").into()),
            _ => None,
        });
        assert_eq!(
            hooks.paths,
            [
                installed.join("notify.d/50-system"),
                installed.join("config/scottland/notify.d/10-user")
            ]
        );
        fs::remove_dir_all(&installed).unwrap();
        fs::remove_dir_all(&directory).unwrap();
    }

    #[test]
    fn failures_carry_the_hooks_stderr() {
        let answer = HookAnswer::Answered {
            hook: "10-a".into(),
            status: Some(70),
            stdout: Vec::new(),
            stderr: "omarchy-shell is not responding\n".into(),
        };
        assert_eq!(
            hook_failure("dnd-toggle", &answer),
            "omarchy-shell is not responding"
        );
        let silent = HookAnswer::Answered {
            hook: "10-a".into(),
            status: Some(70),
            stdout: Vec::new(),
            stderr: String::new(),
        };
        assert!(hook_failure("dismiss", &silent).contains("exit status 70"));
    }

    #[test]
    fn string_hints_need_nul_ended_key_value_pairs() {
        assert_eq!(
            parse_string_hints(b"k\0v\0k2\0\0"),
            Some(vec![("k".into(), "v".into()), ("k2".into(), String::new())])
        );
        assert_eq!(parse_string_hints(b""), None);
        assert_eq!(parse_string_hints(b"k"), None);
        assert_eq!(parse_string_hints(b"k\0"), None);
        assert_eq!(parse_string_hints(b"k\0v"), None);
        assert_eq!(parse_string_hints(b"\0v\0"), None);
    }
}
