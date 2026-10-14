use std::collections::HashMap;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::thread;
use std::time::Duration;

use scottland_notify::notification::{DbusNotificationSender, Notification, NotificationSender};
use zbus::blocking::{Connection, Proxy};
use zbus::zvariant::{OwnedFd, OwnedValue};

use crate::battery::{PowerSnapshot, read_snapshot};
use crate::{Backend, Notice, ProfileMode, ProfileState, Urgency, WorkerFailure};

const PROFILES_SERVICE: &str = "net.hadess.PowerProfiles";
const PROFILES_PATH: &str = "/net/hadess/PowerProfiles";
const PROFILES_INTERFACE: &str = "net.hadess.PowerProfiles";
const LOGIND_SERVICE: &str = "org.freedesktop.login1";
const LOGIND_PATH: &str = "/org/freedesktop/login1";
const LOGIND_INTERFACE: &str = "org.freedesktop.login1.Manager";
const POWER_SUPPLY_ROOT: &str = "/sys/class/power_supply";

type ProfileProperties = HashMap<String, OwnedValue>;
type OfferedProfiles = Vec<(String, ProfileProperties)>;

#[derive(Default)]
pub struct SystemBackend {
    inhibitor: Option<OwnedFd>,
}

impl SystemBackend {
    pub fn shutdown_worker(&mut self) -> u8 {
        crate::run_shutdown_worker(self)
    }
}

impl Backend for SystemBackend {
    fn power_snapshot(&mut self) -> Result<PowerSnapshot, String> {
        read_snapshot(Path::new(POWER_SUPPLY_ROOT))
    }

    fn profile_state(&mut self) -> Result<Option<ProfileState>, String> {
        let Ok(connection) = Connection::system() else {
            return Ok(None);
        };
        let Ok(proxy) = Proxy::new(
            &connection,
            PROFILES_SERVICE,
            PROFILES_PATH,
            PROFILES_INTERFACE,
        ) else {
            return Ok(None);
        };
        let Ok(profiles) = proxy.get_property::<OfferedProfiles>("Profiles") else {
            return Ok(None);
        };
        let active = proxy
            .get_property::<String>("ActiveProfile")
            .map_err(|error| format!("could not read active power profile: {error}"))?;
        Ok(Some(ProfileState {
            offered: profiles.into_iter().map(|(name, _)| name).collect(),
            active,
        }))
    }

    fn set_active_profile(&mut self, profile: &str) -> Result<(), String> {
        let connection = Connection::system()
            .map_err(|error| format!("could not connect to the power profiles service: {error}"))?;
        let proxy = Proxy::new(
            &connection,
            PROFILES_SERVICE,
            PROFILES_PATH,
            PROFILES_INTERFACE,
        )
        .map_err(|error| format!("could not address the power profiles service: {error}"))?;
        proxy
            .call_method("SetProfile", &profile)
            .map(|_| ())
            .map_err(|error| format!("could not set power profile {profile}: {error}"))
    }

    fn remembered_profile(&mut self, mode: ProfileMode) -> Result<Option<String>, String> {
        let path = profile_record_path(mode)?;
        match fs::read_to_string(&path) {
            Ok(profile) => Ok(Some(profile.trim().to_string())),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
            Err(error) => Err(format!(
                "could not read remembered {} profile: {error}",
                mode.label()
            )),
        }
    }

    fn remember_profile(&mut self, mode: ProfileMode, profile: &str) -> Result<(), String> {
        let path = profile_record_path(mode)?;
        atomic_write(&path, profile.as_bytes())
            .map_err(|error| format!("could not remember {} profile: {error}", mode.label()))
    }

    fn stay_awake_record_exists(&mut self) -> Result<bool, String> {
        match fs::metadata(stay_awake_path()?) {
            Ok(metadata) if metadata.is_file() => Ok(true),
            Ok(_) => Err("stay-awake record is not a file".to_string()),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
            Err(error) => Err(format!("could not read stay-awake record: {error}")),
        }
    }

    fn set_stay_awake_record(&mut self, enabled: bool) -> Result<(), String> {
        let path = stay_awake_path()?;
        let parent = path
            .parent()
            .ok_or_else(|| "stay-awake path has no parent directory".to_string())?;
        if enabled {
            fs::create_dir_all(parent)
                .map_err(|error| format!("could not create power state directory: {error}"))?;
            match OpenOptions::new().write(true).create_new(true).open(&path) {
                Ok(_) => Ok(()),
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                    match fs::metadata(&path) {
                        Ok(metadata) if metadata.is_file() => Ok(()),
                        Ok(_) => Err("stay-awake record is not a file".to_string()),
                        Err(error) => Err(format!("could not read stay-awake record: {error}")),
                    }
                }
                Err(error) => Err(format!("could not create stay-awake record: {error}")),
            }
        } else {
            match fs::remove_file(&path) {
                Ok(()) => Ok(()),
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
                Err(error) => Err(format!("could not remove stay-awake record: {error}")),
            }
        }
    }

    fn notify(&mut self, notice: &Notice) -> Result<(), String> {
        let mut notification = Notification::action(&notice.summary, &notice.body);
        notification.app_icon = notice.icon.clone().unwrap_or_default();
        notification.urgency = match notice.urgency {
            Urgency::Low => scottland_notify::notification::Urgency::Low,
            Urgency::Critical => scottland_notify::notification::Urgency::Critical,
        };
        notification.expire_timeout = notice.expire_ms;
        DbusNotificationSender::default()
            .notify(&notification)
            .map(|_| ())
    }

    fn run_battery_hooks(&mut self, percentage: u8) -> Vec<String> {
        let mut roots = vec![scottland::dirs::hooks_dir().join("battery-low.d")];
        if let Ok(config) = scottland::dirs::config_dir() {
            roots.push(config.join("battery-low.d"));
        }
        let mut hooks = Vec::new();
        for root in roots {
            let Ok(entries) = fs::read_dir(&root) else {
                continue;
            };
            let mut paths = entries
                .filter_map(Result::ok)
                .map(|entry| entry.path())
                .collect::<Vec<_>>();
            paths.sort();
            for path in paths {
                let Ok(metadata) = fs::metadata(&path) else {
                    continue;
                };
                if !metadata.is_file() || metadata.permissions().mode() & 0o111 == 0 {
                    continue;
                }
                hooks.push(path);
            }
        }

        let mut failures = Vec::new();
        for hook in hooks {
            let result = Command::new(&hook).arg(percentage.to_string()).status();
            if !matches!(result, Ok(status) if status.success()) {
                failures.push(hook.display().to_string());
            }
        }
        failures
    }

    fn schedule_logout(&mut self) -> Result<(), String> {
        let session = scottland::session::current(None).map_err(|error| {
            format!("could not identify the running Scottland session: {error}")
        })?;
        let helper = scottland::dirs::hooks_dir().join("libexec/scottland-exec");
        let unit = format!("scottland-power-logout-{}", std::process::id());
        let status = Command::new("systemd-run")
            .args([
                "--user",
                "--collect",
                "--quiet",
                "--on-active=2s",
                "--unit",
                &unit,
                "--",
            ])
            .arg(helper)
            .args([
                "--display",
                &session.name,
                "--",
                "/usr/bin/pkill",
                "-x",
                "wayfire",
            ])
            .status()
            .map_err(|error| format!("could not schedule session logout: {error}"))?;
        command_success(status, "could not schedule session logout")
    }

    fn schedule_reboot(&mut self) -> Result<(), String> {
        let status = Command::new("systemctl")
            .args(["--no-wall", "--when=+2s", "reboot"])
            .status()
            .map_err(|error| format!("could not schedule reboot: {error}"))?;
        command_success(status, "could not schedule reboot")
    }

    fn show_osd(&mut self, message: &str, icon: &str, duration_ms: u32) -> Result<(), String> {
        let widget = scottland::dirs::hooks_dir().join("libexec/scottland-widget");
        show_osd_at(&widget, message, icon, duration_ms)
    }

    fn close_all(&mut self) -> Result<(), String> {
        let ctl = scottland::dirs::hooks_dir().join("libexec/scottland-ctl");
        close_all_at(&ctl)
    }

    fn sleep(&mut self, duration: Duration) {
        thread::sleep(duration);
    }

    fn current_executable(&mut self) -> Result<PathBuf, String> {
        std::env::current_exe()
            .map_err(|error| format!("could not locate this executable: {error}"))
    }

    fn start_shutdown_worker(&mut self, executable: &Path) -> Result<(), String> {
        let unit = format!("scottland-power-shutdown-{}", std::process::id());
        let status = shutdown_worker_command(executable, &unit)
            .status()
            .map_err(|error| format!("could not start shutdown worker: {error}"))?;
        command_success(status, "could not start shutdown worker")
    }

    fn acquire_shutdown_inhibitor(&mut self) -> Result<(), String> {
        let connection = Connection::system()
            .map_err(|error| format!("could not connect to the login manager: {error}"))?;
        let proxy = Proxy::new(&connection, LOGIND_SERVICE, LOGIND_PATH, LOGIND_INTERFACE)
            .map_err(|error| format!("could not address the login manager: {error}"))?;
        let inhibitor = proxy
            .call::<_, _, OwnedFd>(
                "Inhibit",
                &(
                    "sleep:idle:handle-lid-switch",
                    "Scottland Power",
                    "Shutting down",
                    "block",
                ),
            )
            .map_err(|error| format!("could not take shutdown inhibitor: {error}"))?;
        self.inhibitor = Some(inhibitor);
        Ok(())
    }

    fn spawn_shutdown_surface(&mut self) -> Result<(), String> {
        let widget = scottland::dirs::hooks_dir().join("libexec/scottland-widget");
        let ctl = scottland::dirs::hooks_dir().join("libexec/scottland-ctl");
        thread::Builder::new()
            .name("scottland-power-shutdown-display".to_string())
            .spawn(move || {
                if let Err(error) = show_osd_at(&widget, "Shutting down", "system-shutdown", 5_000)
                {
                    eprintln!("display could not be shown: {error}");
                }
                if let Err(error) = close_all_at(&ctl) {
                    eprintln!("windows could not be closed: {error}");
                }
            })
            .map(|_| ())
            .map_err(|error| error.to_string())
    }

    fn power_off(&mut self) -> Result<(), WorkerFailure> {
        let status = Command::new("systemctl")
            .args(["--no-wall", "poweroff"])
            .status()
            .map_err(|error| WorkerFailure {
                status: 1,
                message: format!("could not request power-off: {error}"),
            })?;
        if status.success() {
            Ok(())
        } else {
            Err(WorkerFailure {
                status: status
                    .code()
                    .filter(|code| (1..=255).contains(code))
                    .unwrap_or(1) as u8,
                message: format!("systemctl poweroff exited with {status}"),
            })
        }
    }

    fn release_shutdown_inhibitor(&mut self) {
        self.inhibitor = None;
    }
}

fn profile_record_path(mode: ProfileMode) -> Result<PathBuf, String> {
    Ok(scottland::dirs::state_dir()
        .map_err(|error| format!("could not locate Scottland state directory: {error}"))?
        .join("power")
        .join(match mode {
            ProfileMode::Ac => "profile-ac",
            ProfileMode::Battery => "profile-battery",
        }))
}

fn stay_awake_path() -> Result<PathBuf, String> {
    Ok(scottland::dirs::state_dir()
        .map_err(|error| format!("could not locate Scottland state directory: {error}"))?
        .join("power/stay-awake"))
}

fn atomic_write(path: &Path, contents: &[u8]) -> std::io::Result<()> {
    let parent = path
        .parent()
        .ok_or_else(|| std::io::Error::other("record path has no parent directory"))?;
    fs::create_dir_all(parent)?;
    let file_name = path
        .file_name()
        .ok_or_else(|| std::io::Error::other("record path has no file name"))?;
    let temporary = parent.join(format!(
        ".{}.{}.tmp",
        file_name.to_string_lossy(),
        std::process::id()
    ));
    let result = (|| {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)?;
        file.write_all(contents)?;
        file.sync_all()?;
        fs::rename(&temporary, path)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

fn command_success(status: std::process::ExitStatus, message: &str) -> Result<(), String> {
    if status.success() {
        Ok(())
    } else {
        Err(format!("{message}: {status}"))
    }
}

fn show_osd_at(widget: &Path, message: &str, icon: &str, duration_ms: u32) -> Result<(), String> {
    let status = Command::new(widget)
        .args(["osd", "--message", message, "--icon", icon, "--duration"])
        .arg(duration_ms.to_string())
        .status()
        .map_err(|error| format!("could not run scottland-widget osd: {error}"))?;
    command_success(status, "scottland-widget osd failed")
}

fn close_all_at(ctl: &Path) -> Result<(), String> {
    let status = Command::new(ctl)
        .arg("close-all")
        .status()
        .map_err(|error| format!("could not run scottland-ctl close-all: {error}"))?;
    command_success(status, "scottland-ctl close-all failed")
}

fn shutdown_worker_command(executable: &Path, unit: &str) -> Command {
    let mut command = Command::new("systemd-run");
    command.args([
        "--user",
        "--collect",
        "--quiet",
        "--service-type=exec",
        "--property=RuntimeMaxSec=30s",
        "--unit",
        unit,
        "--",
    ]);
    command.arg(executable).arg("__shutdown-worker");
    command
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shutdown_worker_spec_uses_the_current_executable_path() {
        let executable = Path::new("/opt/scottland/libexec/scottland-power");
        let command = shutdown_worker_command(executable, "scottland-power-shutdown-42");
        let args = command.get_args().collect::<Vec<_>>();
        let separator = args.iter().position(|arg| *arg == "--").unwrap();
        assert_eq!(args.get(separator + 1), Some(&executable.as_os_str()));
        assert_eq!(
            args.get(separator + 2).copied(),
            Some(std::ffi::OsStr::new("__shutdown-worker"))
        );
        assert!(
            args.iter()
                .any(|arg| *arg == "--property=RuntimeMaxSec=30s")
        );
    }
}
