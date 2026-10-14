//! The command's user-visible text-size behavior with an isolated GSettings boundary.

use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::sync::atomic::{AtomicU64, Ordering};

static NEXT: AtomicU64 = AtomicU64::new(0);

const BINARY: &str = env!("CARGO_BIN_EXE_scottland-display");
const SCHEMA: &str = "org.gnome.desktop.interface";
const KEY: &str = "text-scaling-factor";

struct Scratch {
    root: PathBuf,
    bin: PathBuf,
    home: PathBuf,
    hooks: PathBuf,
    calls: PathBuf,
    helper_calls: PathBuf,
    state: PathBuf,
}

impl Scratch {
    fn new() -> Self {
        let root = Path::new(env!("CARGO_TARGET_TMPDIR")).join(format!(
            "scottland-display-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&root).expect("create unique scratch directory");

        let bin = root.join("bin");
        let home = root.join("home");
        let hooks = root.join("hooks");
        fs::create_dir(&bin).unwrap();
        fs::create_dir(&home).unwrap();
        fs::create_dir_all(hooks.join("libexec")).unwrap();
        let calls = root.join("calls");
        let helper_calls = root.join("helper-calls");
        let state = root.join("state");
        let fake = bin.join("gsettings");
        fs::write(
            &fake,
            r##"#!/bin/sh
set -eu
printf '%s\n' "$*" >>"$CALLS"
if [ "${FAIL_GSETTINGS:-0}" = 1 ]; then
  echo 'fixture gsettings failure' >&2
  exit 7
fi
[ "$2" = "org.gnome.desktop.interface" ] || exit 64
[ "$3" = "text-scaling-factor" ] || exit 64
case "$1" in
  get)
    [ "$#" -eq 3 ] || exit 64
    if [ -f "$STATE_FILE" ]; then cat "$STATE_FILE"; else printf '1.0\n'; fi
    ;;
  set)
    [ "$#" -eq 4 ] || exit 64
    printf '%s\n' "$4" >"$STATE_FILE"
    ;;
  reset)
    [ "$#" -eq 3 ] || exit 64
    rm -f "$STATE_FILE"
    ;;
  *) exit 64 ;;
esac
"##,
        )
        .unwrap();
        let mut permissions = fs::metadata(&fake).unwrap().permissions();
        permissions.set_mode(0o755);
        fs::set_permissions(&fake, permissions).unwrap();

        let fake_helper = hooks.join("libexec/scottland-color-scheme");
        fs::write(
            &fake_helper,
            r##"#!/bin/sh
set -eu
printf '%s\n' "$*" >>"$HELPER_CALLS"
if [ "${FAIL_HELPER:-0}" = 1 ]; then
  echo 'fixture helper failure' >&2
  exit 9
fi
case "$1" in
  cursor-size) printf '24\n' ;;
  once|terminal-theme-push) ;;
  *) echo "unexpected action: $1" >&2; exit 64 ;;
esac
"##,
        )
        .unwrap();
        let mut permissions = fs::metadata(&fake_helper).unwrap().permissions();
        permissions.set_mode(0o755);
        fs::set_permissions(&fake_helper, permissions).unwrap();
        std::os::unix::fs::symlink(BINARY, bin.join("scottland-display"))
            .expect("link the command into the session hook PATH");

        Self {
            root,
            bin,
            home,
            hooks,
            calls,
            helper_calls,
            state,
        }
    }

    fn run(&self, args: &[&str]) -> Output {
        self.run_with_failure(args, false)
    }

    fn run_with_failure(&self, args: &[&str], fail: bool) -> Output {
        let mut paths = vec![self.bin.clone()];
        paths.extend(std::env::split_paths(
            &std::env::var_os("PATH").unwrap_or_default(),
        ));
        let path = std::env::join_paths(paths).unwrap();
        Command::new(BINARY)
            .args(args)
            .env("PATH", path)
            .env("HOME", &self.home)
            .env("SCOTTLAND_HOOKS", &self.hooks)
            .env("CALLS", &self.calls)
            .env("HELPER_CALLS", &self.helper_calls)
            .env("STATE_FILE", &self.state)
            .env("FAIL_GSETTINGS", if fail { "1" } else { "0" })
            .output()
            .unwrap()
    }

    fn run_with_helper_failure(&self, args: &[&str], fail: bool) -> Output {
        let mut paths = vec![self.bin.clone()];
        paths.extend(std::env::split_paths(
            &std::env::var_os("PATH").unwrap_or_default(),
        ));
        let path = std::env::join_paths(paths).unwrap();
        Command::new(BINARY)
            .args(args)
            .env("PATH", path)
            .env("HOME", &self.home)
            .env("SCOTTLAND_HOOKS", &self.hooks)
            .env("CALLS", &self.calls)
            .env("HELPER_CALLS", &self.helper_calls)
            .env("STATE_FILE", &self.state)
            .env("FAIL_HELPER", if fail { "1" } else { "0" })
            .output()
            .unwrap()
    }

    fn calls(&self) -> Vec<String> {
        fs::read_to_string(&self.calls)
            .unwrap_or_default()
            .lines()
            .map(str::to_string)
            .collect()
    }

    fn ghostty_config(&self) -> PathBuf {
        self.home.join(".config/ghostty/config")
    }

    fn helper_calls(&self) -> Vec<String> {
        fs::read_to_string(&self.helper_calls)
            .unwrap_or_default()
            .lines()
            .map(str::to_string)
            .collect()
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

fn stdout(output: &Output) -> String {
    String::from_utf8_lossy(&output.stdout).into_owned()
}

fn stderr(output: &Output) -> String {
    String::from_utf8_lossy(&output.stderr).into_owned()
}

#[test]
fn documented_help_dispatch_succeeds_without_reading_settings() {
    let scratch = Scratch::new();

    for flag in ["--help", "-h", "help"] {
        let output = scratch.run(&[flag]);
        assert!(output.status.success(), "{flag}: {}", stderr(&output));
        assert!(stdout(&output).contains("usage: scottland-display text-size"));
        assert!(stderr(&output).is_empty());
    }
    assert!(scratch.calls().is_empty());
}

#[test]
fn show_reads_the_key_and_reports_the_unset_default() {
    let scratch = Scratch::new();
    let output = scratch.run(&["text-size"]);
    assert!(output.status.success(), "{}", stderr(&output));
    assert_eq!(stdout(&output), "1.0\n");
    assert_eq!(scratch.calls(), [format!("get {SCHEMA} {KEY}")]);

    fs::write(&scratch.state, "1.5\n").unwrap();
    let output = scratch.run(&["text-size"]);
    assert!(output.status.success(), "{}", stderr(&output));
    assert_eq!(stdout(&output), "1.5\n");
    assert_eq!(scratch.calls().len(), 2);
}

#[test]
fn set_writes_only_the_gsettings_key_and_preserves_user_config() {
    let scratch = Scratch::new();
    let config = scratch.ghostty_config();
    fs::create_dir_all(config.parent().unwrap()).unwrap();
    fs::write(&config, "font-size = 13\n").unwrap();

    let output = scratch.run(&["text-size", "set", "1.25"]);
    assert!(output.status.success(), "{}", stderr(&output));
    assert_eq!(fs::read_to_string(&scratch.state).unwrap(), "1.25\n");
    assert_eq!(scratch.calls(), [format!("set {SCHEMA} {KEY} 1.25")]);
    assert_eq!(fs::read_to_string(config).unwrap(), "font-size = 13\n");
}

#[test]
fn out_of_range_and_non_numeric_values_are_rejected_without_writing() {
    let scratch = Scratch::new();
    fs::write(&scratch.state, "1.75\n").unwrap();

    for factor in ["4", "0.49", "NaN", "not-a-number"] {
        let output = scratch.run(&["text-size", "set", factor]);
        assert!(!output.status.success(), "accepted {factor}");
        assert!(stderr(&output).contains("0.5"), "{}", stderr(&output));
        assert!(stderr(&output).contains("3.0"), "{}", stderr(&output));
        assert_eq!(fs::read_to_string(&scratch.state).unwrap(), "1.75\n");
    }
    assert!(scratch.calls().is_empty());
}

#[test]
fn reset_unsets_the_key_so_show_returns_the_default() {
    let scratch = Scratch::new();
    fs::write(&scratch.state, "1.75\n").unwrap();

    let output = scratch.run(&["text-size", "reset"]);
    assert!(output.status.success(), "{}", stderr(&output));
    assert!(!scratch.state.exists());
    assert_eq!(scratch.calls(), [format!("reset {SCHEMA} {KEY}")]);

    let output = scratch.run(&["text-size"]);
    assert!(output.status.success(), "{}", stderr(&output));
    assert_eq!(stdout(&output), "1.0\n");
    assert_eq!(scratch.calls().len(), 2);
}

#[test]
fn backend_failure_is_reported_instead_of_claiming_success() {
    let scratch = Scratch::new();
    let output = scratch.run_with_failure(&["text-size", "set", "1.25"], true);
    assert!(!output.status.success());
    assert!(stderr(&output).contains("gsettings set failed"));
    assert!(stderr(&output).contains("fixture gsettings failure"));
    assert_eq!(scratch.calls(), [format!("set {SCHEMA} {KEY} 1.25")]);
    assert!(!scratch.state.exists());
}

#[test]
fn unsupported_arguments_are_rejected_before_calling_gsettings() {
    let scratch = Scratch::new();
    let output = scratch.run(&["text-size", "set", "1.25", "extra"]);
    assert!(!output.status.success());
    assert!(stderr(&output).contains("usage: scottland-display text-size"));
    assert!(scratch.calls().is_empty());
}

#[test]
fn terminal_theme_push_calls_only_the_private_helper_action_once() {
    let scratch = Scratch::new();
    let output = scratch.run(&["terminal-theme", "push"]);
    assert!(output.status.success(), "{}", stderr(&output));
    assert!(scratch.calls().is_empty());
    assert_eq!(scratch.helper_calls(), ["terminal-theme-push"]);
}

#[test]
fn terminal_theme_push_propagates_helper_failure_and_stderr() {
    let scratch = Scratch::new();
    let output = scratch.run_with_helper_failure(&["terminal-theme", "push"], true);
    assert!(!output.status.success());
    assert!(stderr(&output).contains("terminal-theme-push failed"));
    assert!(stderr(&output).contains("fixture helper failure"));
    assert_eq!(scratch.helper_calls(), ["terminal-theme-push"]);
    assert!(scratch.calls().is_empty());
}

#[test]
fn terminal_theme_push_rejects_malformed_arguments_before_calling_helper() {
    let scratch = Scratch::new();

    for args in [
        &["terminal-theme"][..],
        &["terminal-theme", "apply"][..],
        &["terminal-theme", "push", "extra"][..],
    ] {
        let output = scratch.run(args);
        assert!(!output.status.success(), "accepted {args:?}");
        assert!(stderr(&output).contains("usage: scottland-display"));
    }

    assert!(scratch.helper_calls().is_empty());
    assert!(scratch.calls().is_empty());
}

#[test]
fn night_light_parse_errors_preserve_the_policy_status_without_session_io() {
    let scratch = Scratch::new();
    let output = scratch.run(&["night-light", "unsupported"]);

    assert_eq!(output.status.code(), Some(2));
    assert!(stdout(&output).is_empty());
    assert!(stderr(&output).contains("usage: scottland-display night-light"));
    assert!(scratch.helper_calls().is_empty());
    assert!(scratch.calls().is_empty());
}

#[test]
fn cursor_size_command_returns_the_helper_value_without_settings_side_effects() {
    let scratch = Scratch::new();
    let output = scratch.run(&["cursor-size"]);
    assert!(output.status.success(), "{}", stderr(&output));
    assert_eq!(stdout(&output), "24\n");
    assert_eq!(scratch.helper_calls(), ["cursor-size"]);
    assert!(scratch.calls().is_empty());
}

#[test]
fn color_scheme_default_and_nested_cursor_size_forms_match_the_contract() {
    let scratch = Scratch::new();
    let output = scratch.run(&["color-scheme"]);
    assert!(output.status.success(), "{}", stderr(&output));
    assert_eq!(scratch.helper_calls(), ["once"]);

    let scratch = Scratch::new();
    let output = scratch.run(&["color-scheme", "cursor-size"]);
    assert!(output.status.success(), "{}", stderr(&output));
    assert_eq!(stdout(&output), "24\n");
    assert_eq!(scratch.helper_calls(), ["cursor-size"]);
}

#[test]
fn sourced_session_environment_exports_the_cursor_size_command_result() {
    let scratch = Scratch::new();
    let script =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../session-env.d/10-cursor-size.sh");
    assert!(script.is_file());
    let mut paths = vec![scratch.bin.clone()];
    paths.extend(std::env::split_paths(
        &std::env::var_os("PATH").unwrap_or_default(),
    ));
    let path = std::env::join_paths(paths).unwrap();

    let output = Command::new("sh")
        .args([
            "-c",
            ". \"$CURSOR_SCRIPT\"; printf '%s\\n' \"${XCURSOR_SIZE-}\"",
        ])
        .env("CURSOR_SCRIPT", &script)
        .env("PATH", path)
        .env("SCOTTLAND_HOOKS", &scratch.hooks)
        .env("HELPER_CALLS", &scratch.helper_calls)
        .output()
        .unwrap();
    assert!(output.status.success(), "{}", stderr(&output));
    assert_eq!(stdout(&output), "24\n");
    assert_eq!(scratch.helper_calls(), ["cursor-size"]);
}
