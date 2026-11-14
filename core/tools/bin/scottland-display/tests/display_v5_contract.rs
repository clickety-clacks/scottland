use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::os::fd::AsRawFd;
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::UnixListener;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::sync::atomic::{AtomicU64, Ordering};
use std::thread;
use std::time::{SystemTime, UNIX_EPOCH};

const BINARY: &str = env!("CARGO_BIN_EXE_scottland-display");
const LOCK_EX: i32 = 2;
const LOCK_NB: i32 = 4;
static NEXT: AtomicU64 = AtomicU64::new(0);

unsafe extern "C" {
    fn flock(fd: i32, operation: i32) -> i32;
}

struct Scratch {
    root: PathBuf,
    bin: PathBuf,
    home: PathBuf,
    config: PathBuf,
    runtime: PathBuf,
    session: PathBuf,
    hooks: PathBuf,
    sysfs: PathBuf,
    ddc_calls: PathBuf,
    osd_calls: PathBuf,
    bus_calls: PathBuf,
    output_calls: PathBuf,
    power_calls: PathBuf,
}

impl Scratch {
    fn new() -> Self {
        let root = Path::new(env!("CARGO_TARGET_TMPDIR")).join(format!(
            "display-v5-contract-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&root).unwrap();
        let bin = root.join("bin");
        let home = root.join("home");
        let config = root.join("config");
        let runtime = root.join("runtime");
        let session = root.join("session");
        let hooks = root.join("hooks");
        let sysfs = root.join("sysfs");
        for path in [&bin, &home, &config, &runtime, &session, &sysfs] {
            fs::create_dir_all(path).unwrap();
        }
        fs::create_dir_all(hooks.join("libexec")).unwrap();

        let ddc_calls = root.join("ddc-calls");
        let osd_calls = root.join("osd-calls");
        let bus_calls = root.join("bus-calls");
        let output_calls = root.join("output-calls");
        let power_calls = root.join("power-calls");

        write_executable(
            &bin.join("scottland-widget"),
            r##"#!/bin/sh
set -eu
case "$1" in
  __focused-output) printf '%s\n' "${FOCUSED_OUTPUT:-DP-1}" ;;
  osd) printf '%s\n' "$*" >>"$OSD_CALLS" ;;
  *) exit 64 ;;
esac
"##,
        );
        write_executable(
            &bin.join("ddcutil"),
            r##"#!/bin/sh
set -eu
printf '%s\n' "$*" >>"$DDC_CALLS"
if [ "${2:-}" = detect ]; then
  [ "${DDC_NO_DEVICE:-0}" != 1 ] || exit 0
  printf 'Display 1\n I2C bus: /dev/i2c-%s\n DRM connector: card0-%s\n' \
    "${DDC_BUS:-7}" "${DDC_CONNECTOR:-DP-1}"
elif [ "${4:-}" = getvcp ]; then
  if [ "${DDC_FAIL_READ:-0}" = 1 ]; then echo 'fixture read failed' >&2; exit 7; fi
  printf 'VCP 10 C %s %s\n' "${DDC_CURRENT:-40}" "${DDC_MAX:-80}"
elif [ "${4:-}" = setvcp ]; then
  :
else
  exit 64
fi
"##,
        );
        write_executable(
            &bin.join("busctl"),
            r##"#!/bin/sh
set -eu
printf '%s\n' "$*" >>"$BUS_CALLS"
if [ "${1:-}" = --system ] && [ "${6:-}" = SetBrightness ]; then
  printf '%s\n' "${10}" >"$SYSFS_ROOT/class/$8/$9/brightness"
fi
"##,
        );
        write_executable(
            &hooks.join("libexec/scottland-output"),
            r##"#!/bin/sh
set -eu
printf '%s\n' "$*" >>"$OUTPUT_CALLS"
case "$1" in
  list)
    printf '[{"name":"DP-1","enabled":true,"scale":%s},{"name":"eDP-1","enabled":true,"scale":%s}]\n' \
      "${OUTPUT_SCALE:-2}" "${INTERNAL_SCALE:-1}"
    ;;
  set)
    applied="${APPLIED_SCALE:-$4}"
    printf '{"name":"%s","enabled":true,"scale":%s}\n' "$2" "$applied"
    if [ "${FAIL_OUTPUT_SET:-0}" = 1 ]; then echo 'fixture output set failed' >&2; exit 1; fi
    if [ "${5:-}" = --session-only ]; then
      mkdir -p "$SCOTTLAND_SESSION_DIR"
      printf '{"%s":%s}\n' "$2" "$applied" >"$SCOTTLAND_SESSION_DIR/session-output-scales.json"
    else
      rm -f "$SCOTTLAND_SESSION_DIR/session-output-scales.json"
    fi
    ;;
  *) exit 64 ;;
esac
"##,
        );
        write_executable(
            &hooks.join("libexec/scottland-output-power"),
            r##"#!/bin/sh
set -eu
printf '%s\n' "$*" >>"$POWER_CALLS"
case "$1" in
  status) printf '{"name":"DP-1","power":"off"}\n{"name":"eDP-1","power":"off"}\n' ;;
  off) : ;;
  --output) printf '{"name":"%s","power":"on"}\n' "$2" ;;
  *) exit 64 ;;
esac
"##,
        );

        Self {
            root,
            bin,
            home,
            config,
            runtime,
            session,
            hooks,
            sysfs,
            ddc_calls,
            osd_calls,
            bus_calls,
            output_calls,
            power_calls,
        }
    }

    fn run(&self, args: &[&str]) -> Output {
        self.run_env(args, &[])
    }

    fn run_env(&self, args: &[&str], extra: &[(&str, &str)]) -> Output {
        let mut paths = vec![self.bin.clone()];
        paths.extend(std::env::split_paths(
            &std::env::var_os("PATH").unwrap_or_default(),
        ));
        let path = std::env::join_paths(paths).unwrap();
        let mut command = Command::new(BINARY);
        command
            .args(args)
            .env("PATH", path)
            .env("HOME", &self.home)
            .env("XDG_CONFIG_HOME", &self.config)
            .env("XDG_RUNTIME_DIR", &self.runtime)
            .env("SCOTTLAND_SESSION_DIR", &self.session)
            .env("SCOTTLAND_HOOKS", &self.hooks)
            .env("SCOTTLAND_SYSFS_ROOT", &self.sysfs)
            .env("WAYLAND_DISPLAY", "wayland-0")
            .env("DDC_CALLS", &self.ddc_calls)
            .env("OSD_CALLS", &self.osd_calls)
            .env("BUS_CALLS", &self.bus_calls)
            .env("OUTPUT_CALLS", &self.output_calls)
            .env("POWER_CALLS", &self.power_calls)
            .env_remove("SCOTTLAND_CONFIG_OUTPUT");
        for (key, value) in extra {
            command.env(key, value);
        }
        command.output().unwrap()
    }

    fn write_backlight(&self, name: &str, current: u32, maximum: u32) {
        self.write_level("backlight", name, current, maximum);
    }

    fn write_keyboard_backlight(&self, name: &str, current: u32, maximum: u32) {
        self.write_level("leds", name, current, maximum);
    }

    fn write_level(&self, class: &str, name: &str, current: u32, maximum: u32) {
        let directory = self.sysfs.join("class").join(class).join(name);
        fs::create_dir_all(&directory).unwrap();
        fs::write(directory.join("brightness"), current.to_string()).unwrap();
        fs::write(directory.join("max_brightness"), maximum.to_string()).unwrap();
    }

    fn calls(path: &Path) -> Vec<String> {
        fs::read_to_string(path)
            .unwrap_or_default()
            .lines()
            .map(str::to_owned)
            .collect()
    }

    fn ddc_cache(&self) -> PathBuf {
        self.session.join("wayland-0.ddc-44502d31")
    }

    fn layout(&self) -> PathBuf {
        self.config.join("scottland/layout.ini")
    }

    fn overrides(&self) -> PathBuf {
        self.config.join("scottland/overrides.ini")
    }

    fn install_real_output_stack(&self) {
        let repository = Path::new(env!("CARGO_MANIFEST_DIR"))
            .ancestors()
            .nth(4)
            .expect("repository root")
            .to_path_buf();
        let libexec = repository.join("core/libexec");
        let config_d = repository.join("core/config.d");

        fs::remove_file(self.hooks.join("libexec/scottland-output")).unwrap();
        symlink(
            &libexec.join("scottland-output"),
            self.hooks.join("libexec/scottland-output"),
        )
        .unwrap();
        symlink(
            repository.join("core/session/scottland-build-config"),
            self.hooks.join("libexec/scottland-build-config"),
        )
        .unwrap();
        symlink(
            libexec.join("scottland_output_scale.py"),
            self.hooks.join("libexec/scottland_output_scale.py"),
        )
        .unwrap();
        write_executable(
            &self.hooks.join("libexec/scottland-output-heads"),
            r##"#!/usr/bin/env python3
import json
import os
import re

path = os.environ["SCOTTLAND_CONFIG_OUTPUT"]
text = open(path, encoding="utf-8").read()
sections = re.findall(r"(?ms)^\[output:DP-1\]\s*\n(.*?)(?=^\[|\Z)", text)
scale = 1.0
for section in sections:
    matches = re.findall(r"(?m)^\s*scale\s*=\s*([0-9.]+)", section)
    if matches:
        scale = float(matches[-1])
print(json.dumps([{"name": "DP-1", "enabled": True, "scale": scale}]))
"##,
        );

        let generators = self.hooks.join("config.d");
        fs::create_dir_all(&generators).unwrap();
        for name in [
            "20-layout-settings",
            "85-session-outputs",
            "90-user-overrides",
            "95-session-output-scales",
        ] {
            symlink(config_d.join(name), generators.join(name)).unwrap();
        }
    }

    fn build_config(&self) -> Output {
        Command::new(self.hooks.join("libexec/scottland-build-config"))
            .env("HOME", &self.home)
            .env("XDG_CONFIG_HOME", &self.config)
            .env("XDG_RUNTIME_DIR", &self.runtime)
            .env("SCOTTLAND_SESSION_DIR", &self.session)
            .env("SCOTTLAND_CONFIG_OUTPUT", self.session.join("wayfire.ini"))
            .env("SCOTTLAND_HOOKS", &self.hooks)
            .output()
            .unwrap()
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

fn write_executable(path: &Path, contents: &str) {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).unwrap();
    }
    fs::write(path, contents).unwrap();
    let mut permissions = fs::metadata(path).unwrap().permissions();
    permissions.set_mode(0o755);
    fs::set_permissions(path, permissions).unwrap();
}

fn symlink(source: impl AsRef<Path>, destination: impl AsRef<Path>) -> std::io::Result<()> {
    std::os::unix::fs::symlink(source, destination)
}

struct SocketFile(PathBuf);

impl Drop for SocketFile {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0);
    }
}

fn stdout(output: &Output) -> String {
    String::from_utf8_lossy(&output.stdout).into_owned()
}

fn stderr(output: &Output) -> String {
    String::from_utf8_lossy(&output.stderr).into_owned()
}

#[test]
fn ddc_set_uses_percent_math_and_records_the_exact_display_osd_call() {
    let scratch = Scratch::new();
    let output = scratch.run(&["brightness", "--output", "DP-1", "25%"]);

    assert!(output.status.success(), "{}", stderr(&output));
    assert!(stdout(&output).is_empty());
    assert!(stderr(&output).is_empty());
    let ddc = Scratch::calls(&scratch.ddc_calls);
    assert!(ddc.iter().any(|call| call.contains(" detect --brief")));
    assert!(ddc.iter().any(|call| call.ends_with("getvcp 10 --brief")));
    assert!(ddc.iter().any(|call| call.ends_with("setvcp 10 20")));
    assert_eq!(
        Scratch::calls(&scratch.osd_calls),
        ["osd --icon display-brightness --message Brightness --progress 25"]
    );
}

#[test]
fn ddc_read_rounds_forty_of_eighty_to_fifty_without_osd() {
    let scratch = Scratch::new();
    let output = scratch.run(&["brightness", "--output", "DP-1"]);

    assert!(output.status.success(), "{}", stderr(&output));
    assert_eq!(stdout(&output), "50\n");
    assert!(Scratch::calls(&scratch.osd_calls).is_empty());
}

#[test]
fn absolute_ddc_sets_reuse_fresh_maxima_and_refresh_stale_ones() {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs();

    let fresh = Scratch::new();
    fs::write(fresh.ddc_cache(), format!("ok\t{now}\t7\t80\n")).unwrap();
    let output = fresh.run(&["brightness", "--output", "DP-1", "30%"]);
    assert!(output.status.success(), "{}", stderr(&output));
    let calls = Scratch::calls(&fresh.ddc_calls);
    assert_eq!(calls.len(), 1, "fresh absolute set should not read DDC");
    assert!(calls[0].ends_with("setvcp 10 24"));

    let stale = Scratch::new();
    fs::write(
        stale.ddc_cache(),
        format!("ok\t{}\t7\t80\n", now.saturating_sub(11)),
    )
    .unwrap();
    let output = stale.run(&["brightness", "--output", "DP-1", "30%"]);
    assert!(output.status.success(), "{}", stderr(&output));
    let calls = Scratch::calls(&stale.ddc_calls);
    assert!(calls.iter().any(|call| call.contains(" detect --brief")));
    assert!(calls.iter().any(|call| call.ends_with("getvcp 10 --brief")));
    assert!(calls.iter().any(|call| call.ends_with("setvcp 10 24")));
}

#[test]
fn absent_ddc_is_remembered_for_sixty_seconds_and_failed_reads_are_not_remembered() {
    let absent = Scratch::new();
    let first = absent.run_env(
        &["brightness", "--output", "DP-1"],
        &[("DDC_NO_DEVICE", "1")],
    );
    assert!(!first.status.success());
    assert!(stderr(&first).contains("no DDC device found"));
    let second = absent.run(&["brightness", "--output", "DP-1"]);
    assert!(!second.status.success());
    assert_eq!(
        Scratch::calls(&absent.ddc_calls)
            .iter()
            .filter(|call| call.contains(" detect --brief"))
            .count(),
        1
    );

    let failing = Scratch::new();
    let first = failing.run_env(
        &["brightness", "--output", "DP-1"],
        &[("DDC_FAIL_READ", "1")],
    );
    assert!(!first.status.success());
    assert!(stderr(&first).contains("fixture read failed"));
    let second = failing.run(&["brightness", "--output", "DP-1"]);
    assert!(second.status.success(), "{}", stderr(&second));
    assert_eq!(stdout(&second), "50\n");
    assert_eq!(
        Scratch::calls(&failing.ddc_calls)
            .iter()
            .filter(|call| call.contains(" detect --brief"))
            .count(),
        2,
        "a failed read must not become a cached no-DDC result"
    );
}

#[test]
fn focused_internal_output_uses_backlight_and_only_steps_emit_the_display_osd() {
    let scratch = Scratch::new();
    scratch.write_backlight("intel_backlight", 400, 1000);

    let read = scratch.run_env(&["brightness"], &[("FOCUSED_OUTPUT", "eDP-1")]);
    assert!(read.status.success(), "{}", stderr(&read));
    assert_eq!(stdout(&read), "40\n");
    assert!(Scratch::calls(&scratch.ddc_calls).is_empty());
    assert!(Scratch::calls(&scratch.osd_calls).is_empty());

    let step = scratch.run_env(&["brightness", "+5%"], &[("FOCUSED_OUTPUT", "eDP-1")]);
    assert!(step.status.success(), "{}", stderr(&step));
    assert!(stdout(&step).is_empty());
    assert_eq!(
        fs::read_to_string(
            scratch
                .sysfs
                .join("class/backlight/intel_backlight/brightness")
        )
        .unwrap(),
        "450"
    );
    assert!(
        Scratch::calls(&scratch.bus_calls)
            .iter()
            .any(|call| call.ends_with("SetBrightness ssu backlight intel_backlight 450"))
    );
    assert_eq!(
        Scratch::calls(&scratch.osd_calls),
        ["osd --icon display-brightness --message Brightness --progress 45"]
    );

    let quiet = scratch.run_env(
        &["brightness", "--no-osd", "+5%"],
        &[("FOCUSED_OUTPUT", "eDP-1")],
    );
    assert!(quiet.status.success(), "{}", stderr(&quiet));
    assert_eq!(Scratch::calls(&scratch.osd_calls).len(), 1);
}

#[test]
fn keyboard_brightness_uses_the_exact_keyboard_osd_arguments() {
    let scratch = Scratch::new();
    scratch.write_keyboard_backlight("platform-kbd_backlight", 50, 100);

    let output = scratch.run(&["brightness", "keyboard", "up"]);
    assert!(output.status.success(), "{}", stderr(&output));
    assert_eq!(
        fs::read_to_string(
            scratch
                .sysfs
                .join("class/leds/platform-kbd_backlight/brightness")
        )
        .unwrap(),
        "60"
    );
    assert!(
        Scratch::calls(&scratch.bus_calls)
            .iter()
            .any(|call| call.ends_with("SetBrightness ssu leds platform-kbd_backlight 60"))
    );
    assert_eq!(
        Scratch::calls(&scratch.osd_calls),
        ["osd --icon keyboard-brightness --message Keyboard brightness --progress 60"]
    );

    let output = scratch.run(&["brightness", "keyboard", "--no-osd", "down"]);
    assert!(output.status.success(), "{}", stderr(&output));
    assert_eq!(Scratch::calls(&scratch.osd_calls).len(), 1);
}

#[test]
fn dropped_brightness_presses_and_power_commands_do_not_show_an_osd() {
    let scratch = Scratch::new();
    fs::create_dir_all(&scratch.session).unwrap();
    let lock_path = scratch.session.join("wayland-0.brightness-step.lock");
    let lock = OpenOptions::new()
        .create(true)
        .read(true)
        .write(true)
        .open(lock_path)
        .unwrap();
    assert_eq!(unsafe { flock(lock.as_raw_fd(), LOCK_EX | LOCK_NB) }, 0);

    let dropped = scratch.run(&["brightness", "--output", "DP-1", "+5%"]);
    assert!(dropped.status.success(), "{}", stderr(&dropped));
    assert!(Scratch::calls(&scratch.ddc_calls).is_empty());
    assert!(Scratch::calls(&scratch.osd_calls).is_empty());
    drop(lock);

    let off = scratch.run(&["brightness", "off"]);
    assert!(off.status.success(), "{}", stderr(&off));
    let on = scratch.run(&["brightness", "on"]);
    assert!(on.status.success(), "{}", stderr(&on));
    let power = Scratch::calls(&scratch.power_calls);
    assert!(power.iter().any(|call| call == "off"));
    assert!(power.iter().any(|call| call == "status"));
    assert!(power.iter().any(|call| call == "--output DP-1 on"));
    assert!(power.iter().any(|call| call == "--output eDP-1 on"));
    assert!(Scratch::calls(&scratch.osd_calls).is_empty());
}

#[test]
fn output_scale_saves_only_the_named_scale_and_reports_compositor_refusal() {
    let scratch = Scratch::new();
    fs::create_dir_all(scratch.layout().parent().unwrap()).unwrap();
    let before = b"[scottland]\ncenter_width = 0.5\n\n[output:DP-1]\nscale = 2\nposition = 0,0\n\n[output:eDP-1]\nscale = 1\n";
    fs::write(scratch.layout(), before).unwrap();

    let changed = scratch.run_env(
        &["output", "scale", "3"],
        &[("OUTPUT_SCALE", "2"), ("APPLIED_SCALE", "3")],
    );
    assert!(changed.status.success(), "{}", stderr(&changed));
    assert_eq!(stdout(&changed), "3\n");
    assert_eq!(
        fs::read(scratch.layout()).unwrap(),
        b"[scottland]\ncenter_width = 0.5\n\n[output:DP-1]\nscale = 3\nposition = 0,0\n\n[output:eDP-1]\nscale = 1\n"
    );

    let replaced = scratch.run_env(
        &["output", "scale", "1.5"],
        &[("OUTPUT_SCALE", "3"), ("APPLIED_SCALE", "1.5")],
    );
    assert!(replaced.status.success(), "{}", stderr(&replaced));
    assert_eq!(stdout(&replaced), "1.5\n");
    let saved = fs::read_to_string(scratch.layout()).unwrap();
    assert!(saved.contains("[output:DP-1]\nscale = 1.5\nposition = 0,0"));
    assert_eq!(saved.matches("scale = 1.5").count(), 1);
    assert!(saved.contains("[output:eDP-1]\nscale = 1\n"));

    let refused = Scratch::new();
    fs::create_dir_all(refused.layout().parent().unwrap()).unwrap();
    let before = b"[output:DP-1]\nscale = 2\n";
    fs::write(refused.layout(), before).unwrap();
    let output = refused.run_env(
        &["output", "scale", "3"],
        &[
            ("OUTPUT_SCALE", "2"),
            ("APPLIED_SCALE", "3"),
            ("FAIL_OUTPUT_SET", "1"),
        ],
    );
    assert!(!output.status.success());
    assert_eq!(stdout(&output), "3\n");
    assert!(stderr(&output).contains("fixture output set failed"));
    assert_eq!(fs::read(refused.layout()).unwrap(), before);
}

#[test]
fn failed_scale_save_reports_the_applied_value_and_keeps_the_old_file() {
    let scratch = Scratch::new();
    fs::create_dir_all(scratch.layout().parent().unwrap()).unwrap();
    let before = b"[output:DP-1]\nscale = 2\n\xff";
    fs::write(scratch.layout(), before).unwrap();

    let output = scratch.run_env(
        &["output", "scale", "3"],
        &[("OUTPUT_SCALE", "2"), ("APPLIED_SCALE", "3")],
    );
    assert!(!output.status.success());
    assert_eq!(stdout(&output), "3\n");
    assert!(stderr(&output).contains("lasts only until the session ends"));
    assert_eq!(fs::read(scratch.layout()).unwrap(), before);
}

#[test]
fn pinned_scale_notes_match_the_applied_value_and_do_not_save_a_layout() {
    let scratch = Scratch::new();
    fs::create_dir_all(scratch.overrides().parent().unwrap()).unwrap();
    fs::write(scratch.overrides(), "[output:DP-1]\nscale = 2\n").unwrap();

    let first = scratch.run_env(
        &["output", "scale", "3"],
        &[("OUTPUT_SCALE", "2"), ("APPLIED_SCALE", "3")],
    );
    assert!(first.status.success(), "{}", stderr(&first));
    assert_eq!(stdout(&first), "3\n");
    assert!(stderr(&first).contains("overrides.ini sets this output's scale"));

    let same = scratch.run_env(
        &["output", "scale", "3"],
        &[("OUTPUT_SCALE", "3"), ("APPLIED_SCALE", "3")],
    );
    assert!(same.status.success(), "{}", stderr(&same));
    assert!(stderr(&same).contains("overrides.ini sets this output's scale"));

    let restored = scratch.run_env(
        &["output", "scale", "2"],
        &[("OUTPUT_SCALE", "3"), ("APPLIED_SCALE", "2")],
    );
    assert!(restored.status.success(), "{}", stderr(&restored));
    assert_eq!(stdout(&restored), "2\n");
    assert!(stderr(&restored).is_empty());

    fs::write(scratch.overrides(), "[output:DP-1]\nscale = 4\n").unwrap();
    let upper = scratch.run_env(
        &["output", "scale", "up"],
        &[("OUTPUT_SCALE", "4"), ("APPLIED_SCALE", "4")],
    );
    assert!(upper.status.success(), "{}", stderr(&upper));
    assert_eq!(stdout(&upper), "4\n");
    assert!(stderr(&upper).is_empty());

    fs::write(scratch.overrides(), "[output:DP-1]\nscale = 1\n").unwrap();
    let lower = scratch.run_env(
        &["output", "scale", "down"],
        &[("OUTPUT_SCALE", "1"), ("APPLIED_SCALE", "1")],
    );
    assert!(lower.status.success(), "{}", stderr(&lower));
    assert_eq!(stdout(&lower), "1\n");
    assert!(stderr(&lower).is_empty());
    assert!(!scratch.layout().exists());
    assert_eq!(
        fs::read_to_string(scratch.overrides()).unwrap(),
        "[output:DP-1]\nscale = 1\n"
    );
}

#[test]
fn scale_at_preset_ends_is_saved_when_no_override_is_pinned() {
    let upper = Scratch::new();
    let output = upper.run_env(
        &["output", "scale", "up"],
        &[("OUTPUT_SCALE", "4"), ("APPLIED_SCALE", "4")],
    );
    assert!(output.status.success(), "{}", stderr(&output));
    assert_eq!(stdout(&output), "4\n");
    assert_eq!(
        fs::read_to_string(upper.layout()).unwrap(),
        "[output:DP-1]\nscale = 4\n"
    );

    let lower = Scratch::new();
    let output = lower.run_env(
        &["output", "scale", "down"],
        &[("OUTPUT_SCALE", "1"), ("APPLIED_SCALE", "1")],
    );
    assert!(output.status.success(), "{}", stderr(&output));
    assert_eq!(stdout(&output), "1\n");
    assert_eq!(
        fs::read_to_string(lower.layout()).unwrap(),
        "[output:DP-1]\nscale = 1\n"
    );
}

#[test]
fn saved_layout_scale_is_used_by_the_config_builder() {
    let scratch = Scratch::new();
    scratch.install_real_output_stack();
    fs::create_dir_all(scratch.config.join("scottland")).unwrap();
    fs::write(scratch.config.join("scottland/scottland.ini"), "[core]\n").unwrap();
    fs::write(scratch.layout(), "[output:DP-1]\nscale = 3\n").unwrap();

    let built = scratch.build_config();
    assert!(built.status.success(), "{}", stderr(&built));
    assert!(
        fs::read_to_string(scratch.session.join("wayfire.ini"))
            .unwrap()
            .contains("[output:DP-1]\nscale = 3\n")
    );
}

#[test]
fn pinned_scale_is_temporary_until_the_next_config_reload() {
    let scratch = Scratch::new();
    scratch.install_real_output_stack();
    fs::create_dir_all(scratch.config.join("scottland")).unwrap();
    fs::write(scratch.config.join("scottland/scottland.ini"), "[core]\n").unwrap();
    fs::write(scratch.overrides(), "[output:DP-1]\nscale = 2\n").unwrap();
    let initial = scratch.build_config();
    assert!(initial.status.success(), "{}", stderr(&initial));
    let config_path = scratch.session.join("wayfire.ini");
    assert!(
        fs::read_to_string(&config_path)
            .unwrap()
            .contains("[output:DP-1]\nscale = 2\n")
    );

    // Unix sockets cap path length; the test's cargo-managed scratch can be deeply nested.
    let socket_file = SocketFile(std::env::temp_dir().join(format!(
        "sd-{}-{}.sock",
        std::process::id(),
        NEXT.fetch_add(1, Ordering::Relaxed)
    )));
    let socket_path = &socket_file.0;
    let listener = UnixListener::bind(&socket_path).unwrap();
    let server = thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut reloads = 0_u64;
        loop {
            let mut header = [0_u8; 4];
            if stream.read_exact(&mut header).is_err() {
                break;
            }
            let length = u32::from_le_bytes(header) as usize;
            let mut request = vec![0; length];
            stream.read_exact(&mut request).unwrap();
            let reply = format!("{{\"config-reloads\":{reloads}}}");
            reloads += 1;
            let reply = reply.as_bytes();
            stream
                .write_all(&(reply.len() as u32).to_le_bytes())
                .unwrap();
            stream.write_all(reply).unwrap();
        }
    });

    let output = Command::new(scratch.hooks.join("libexec/scottland-output"))
        .args(["set", "DP-1", "--scale", "3", "--session-only"])
        .env("HOME", &scratch.home)
        .env("XDG_CONFIG_HOME", &scratch.config)
        .env("XDG_RUNTIME_DIR", &scratch.runtime)
        .env("SCOTTLAND_SESSION_DIR", &scratch.session)
        .env("SCOTTLAND_CONFIG_OUTPUT", &config_path)
        .env("SCOTTLAND_HOOKS", &scratch.hooks)
        .env("WAYFIRE_SOCKET", socket_path)
        .output()
        .unwrap();
    assert!(output.status.success(), "{}", stderr(&output));
    assert!(stdout(&output).contains("\"scale\": 3"));
    let temporary = fs::read_to_string(&config_path).unwrap();
    assert!(temporary.contains("[output:DP-1]\nscale = 2\n"));
    assert!(temporary.contains("# set for this session over overrides.ini\nscale = 3\n"));
    assert!(scratch.session.join("session-output-scales.json").exists());
    server.join().unwrap();

    let reloaded = scratch.build_config();
    assert!(reloaded.status.success(), "{}", stderr(&reloaded));
    assert!(!scratch.session.join("session-output-scales.json").exists());
    let config = fs::read_to_string(config_path).unwrap();
    assert!(config.contains("[output:DP-1]\nscale = 2\n"));
    assert!(!config.contains("# set for this session over overrides.ini"));
}
