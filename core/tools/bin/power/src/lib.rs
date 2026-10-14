pub mod battery;
mod system;

use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::time::Duration;

use battery::{BatteryReport, PowerSnapshot};
pub use system::SystemBackend;

const USAGE: &str = "usage: scottland-power <present battery|present external|battery [--fields|notify|low PERCENT]|profile list [--active-state]|profile set [autodetect|ac|battery] [PROFILE]|profile init|logout|reboot|shutdown|stay-awake [toggle|on|off|status]>";

const LOGOUT_MESSAGE: &str = "Logging out";
const REBOOT_MESSAGE: &str = "Rebooting";
const SHUTDOWN_FAILED: &str = "Shutdown failed";
const SHUTDOWN_FAILED_BODY: &str = "Could not complete shutdown. Please try again.";

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Output {
    pub status: u8,
    pub stdout: String,
    pub stderr: String,
}

impl Output {
    fn success(stdout: impl Into<String>) -> Self {
        Self {
            status: 0,
            stdout: stdout.into(),
            stderr: String::new(),
        }
    }

    fn failure(message: impl AsRef<str>) -> Self {
        Self {
            status: 1,
            stdout: String::new(),
            stderr: format!("{}\n", message.as_ref()),
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Urgency {
    Low,
    Critical,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Notice {
    pub summary: String,
    pub body: String,
    pub icon: Option<String>,
    pub urgency: Urgency,
    pub expire_ms: i32,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ProfileState {
    pub offered: Vec<String>,
    pub active: String,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ProfileMode {
    Ac,
    Battery,
}

impl ProfileMode {
    fn label(self) -> &'static str {
        match self {
            Self::Ac => "ac",
            Self::Battery => "battery",
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct WorkerFailure {
    pub status: u8,
    pub message: String,
}

/// All operating-system effects are behind this boundary so command outcomes can be tested with
/// controlled power supplies, profile services, windows and service-manager replies.
pub trait Backend {
    fn power_snapshot(&mut self) -> Result<PowerSnapshot, String>;
    fn profile_state(&mut self) -> Result<Option<ProfileState>, String>;
    fn set_active_profile(&mut self, profile: &str) -> Result<(), String>;
    fn remembered_profile(&mut self, mode: ProfileMode) -> Result<Option<String>, String>;
    fn remember_profile(&mut self, mode: ProfileMode, profile: &str) -> Result<(), String>;
    fn stay_awake_record_exists(&mut self) -> Result<bool, String>;
    fn set_stay_awake_record(&mut self, enabled: bool) -> Result<(), String>;
    fn notify(&mut self, notice: &Notice) -> Result<(), String>;
    fn run_battery_hooks(&mut self, percentage: u8) -> Vec<String>;
    fn schedule_logout(&mut self) -> Result<(), String>;
    fn schedule_reboot(&mut self) -> Result<(), String>;
    fn show_osd(&mut self, message: &str, icon: &str, duration_ms: u32) -> Result<(), String>;
    fn close_all(&mut self) -> Result<(), String>;
    fn sleep(&mut self, duration: Duration);
    fn current_executable(&mut self) -> Result<PathBuf, String>;
    fn start_shutdown_worker(&mut self, executable: &Path) -> Result<(), String>;
    fn acquire_shutdown_inhibitor(&mut self) -> Result<(), String>;
    /// Starts the OSD and close-all work in the background and returns without waiting for either.
    fn spawn_shutdown_surface(&mut self) -> Result<(), String>;
    fn power_off(&mut self) -> Result<(), WorkerFailure>;
    fn release_shutdown_inhibitor(&mut self);
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum RequestedMode {
    Autodetect,
    Ac,
    Battery,
}

#[derive(Clone, Debug, PartialEq, Eq)]
enum Action {
    PresentBattery,
    PresentExternal,
    BatteryLine,
    BatteryFields,
    BatteryNotice,
    BatteryLow(u8),
    ProfileList {
        active_state: bool,
    },
    ProfileSet {
        mode: RequestedMode,
        profile: Option<String>,
    },
    ProfileInit,
    Logout,
    Reboot,
    Shutdown,
    StayAwake(StayAwakeAction),
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum StayAwakeAction {
    Toggle,
    On,
    Off,
    Status,
}

fn parse(args: &[OsString]) -> Result<Action, ()> {
    let args = args
        .iter()
        .map(|arg| arg.to_str().ok_or(()))
        .collect::<Result<Vec<_>, _>>()?;
    match args.as_slice() {
        ["present", "battery"] => Ok(Action::PresentBattery),
        ["present", "external"] => Ok(Action::PresentExternal),
        ["battery"] => Ok(Action::BatteryLine),
        ["battery", "--fields"] => Ok(Action::BatteryFields),
        ["battery", "notify"] => Ok(Action::BatteryNotice),
        ["battery", "low", percentage] => percentage
            .parse::<u8>()
            .ok()
            .filter(|value| *value <= 100)
            .map(Action::BatteryLow)
            .ok_or(()),
        ["profile", "list"] => Ok(Action::ProfileList {
            active_state: false,
        }),
        ["profile", "list", "--active-state"] => Ok(Action::ProfileList { active_state: true }),
        ["profile", "set"] => Ok(Action::ProfileSet {
            mode: RequestedMode::Autodetect,
            profile: None,
        }),
        ["profile", "set", mode] => Ok(Action::ProfileSet {
            mode: parse_mode(mode)?,
            profile: None,
        }),
        ["profile", "set", mode, profile] if !profile.is_empty() => Ok(Action::ProfileSet {
            mode: parse_mode(mode)?,
            profile: Some((*profile).to_string()),
        }),
        ["profile", "init"] => Ok(Action::ProfileInit),
        ["logout"] => Ok(Action::Logout),
        ["reboot"] => Ok(Action::Reboot),
        ["shutdown"] => Ok(Action::Shutdown),
        ["stay-awake"] => Ok(Action::StayAwake(StayAwakeAction::Toggle)),
        ["stay-awake", "toggle"] => Ok(Action::StayAwake(StayAwakeAction::Toggle)),
        ["stay-awake", "on"] => Ok(Action::StayAwake(StayAwakeAction::On)),
        ["stay-awake", "off"] => Ok(Action::StayAwake(StayAwakeAction::Off)),
        ["stay-awake", "status"] => Ok(Action::StayAwake(StayAwakeAction::Status)),
        _ => Err(()),
    }
}

fn parse_mode(mode: &str) -> Result<RequestedMode, ()> {
    match mode {
        "autodetect" => Ok(RequestedMode::Autodetect),
        "ac" => Ok(RequestedMode::Ac),
        "battery" => Ok(RequestedMode::Battery),
        _ => Err(()),
    }
}

pub fn execute(args: &[OsString], backend: &mut impl Backend) -> Output {
    let action = match parse(args) {
        Ok(action) => action,
        Err(()) => return Output::failure(USAGE),
    };
    match action {
        Action::PresentBattery => presence(backend, true),
        Action::PresentExternal => presence(backend, false),
        Action::BatteryLine => battery_output(backend, false),
        Action::BatteryFields => battery_output(backend, true),
        Action::BatteryNotice => battery_notice(backend),
        Action::BatteryLow(percentage) => battery_low(backend, percentage),
        Action::ProfileList { active_state } => profile_list(backend, active_state),
        Action::ProfileSet { mode, profile } => profile_set(backend, mode, profile, false),
        Action::ProfileInit => profile_set(backend, RequestedMode::Autodetect, None, true),
        Action::Logout => scheduled_session_action(backend, ActionKind::Logout),
        Action::Reboot => scheduled_session_action(backend, ActionKind::Reboot),
        Action::Shutdown => start_shutdown(backend),
        Action::StayAwake(action) => stay_awake(backend, action),
    }
}

pub fn run_shutdown_worker(backend: &mut impl Backend) -> u8 {
    if let Err(error) = backend.acquire_shutdown_inhibitor() {
        let _ = backend.notify(&shutdown_failure_notice());
        eprintln!("shutdown inhibitor failed: {error}");
        return 1;
    }

    if let Err(error) = backend.spawn_shutdown_surface() {
        eprintln!("display could not be shown: shutdown surface worker could not start: {error}");
        eprintln!("windows could not be closed: shutdown surface worker could not start");
    }
    backend.sleep(Duration::from_secs(2));
    let result = backend.power_off();
    backend.release_shutdown_inhibitor();
    match result {
        Ok(()) => 0,
        Err(failure) => {
            let _ = backend.notify(&shutdown_failure_notice());
            eprintln!("power-off failed: {}", failure.message);
            if failure.status == 0 {
                1
            } else {
                failure.status
            }
        }
    }
}

fn presence(backend: &mut impl Backend, battery: bool) -> Output {
    let snapshot = match backend.power_snapshot() {
        Ok(snapshot) => snapshot,
        Err(error) => return Output::failure(error),
    };
    let present = if battery {
        snapshot.has_battery()
    } else {
        snapshot.has_external_power()
    };
    Output {
        status: u8::from(!present),
        stdout: String::new(),
        stderr: String::new(),
    }
}

fn battery_report(backend: &mut impl Backend) -> Result<(PowerSnapshot, BatteryReport), String> {
    let snapshot = backend.power_snapshot()?;
    let supply = snapshot
        .battery_supply()
        .ok_or_else(|| "No battery".to_string())?;
    let report = BatteryReport::from_supply(supply, snapshot.has_external_power())?;
    Ok((snapshot, report))
}

fn battery_output(backend: &mut impl Backend, fields: bool) -> Output {
    let report = match battery_report(backend) {
        Ok((_, report)) => report,
        Err(error) => return Output::failure(error),
    };
    let stdout = if fields {
        report.fields_text()
    } else {
        format!("{}\n", report.line_text())
    };
    Output::success(stdout)
}

fn battery_notice(backend: &mut impl Backend) -> Output {
    let report = match battery_report(backend) {
        Ok((_, report)) => report,
        Err(error) => return Output::failure(error),
    };
    let notice = Notice {
        summary: report.line_text(),
        body: String::new(),
        icon: None,
        urgency: Urgency::Low,
        expire_ms: -1,
    };
    let _ = backend.notify(&notice);
    Output::success("")
}

fn battery_low(backend: &mut impl Backend, percentage: u8) -> Output {
    let notice = Notice {
        summary: "Time to recharge!".to_string(),
        body: format!("Battery is down to {percentage}%"),
        icon: Some("battery-caution".to_string()),
        urgency: Urgency::Critical,
        expire_ms: 30_000,
    };
    let _ = backend.notify(&notice);
    let failed_hooks = backend.run_battery_hooks(percentage);
    if failed_hooks.is_empty() {
        return Output::success("");
    }
    Output {
        status: 1,
        stdout: String::new(),
        stderr: failed_hooks
            .into_iter()
            .map(|hook| format!("battery-low hook failed: {hook}\n"))
            .collect(),
    }
}

fn profile_list(backend: &mut impl Backend, active_state: bool) -> Output {
    let state = match backend.profile_state() {
        Ok(Some(state)) => state,
        Ok(None) => return Output::failure("Power profiles service is not available"),
        Err(error) => return Output::failure(error),
    };
    let ordered = ordered_profiles(&state.offered);
    let stdout = ordered
        .into_iter()
        .map(|profile| {
            if active_state {
                format!("{profile}\t{}\n", u8::from(profile == state.active))
            } else {
                format!("{profile}\n")
            }
        })
        .collect::<String>();
    Output::success(stdout)
}

fn ordered_profiles(offered: &[String]) -> Vec<String> {
    let preferred = ["power-saver", "balanced", "performance"];
    let mut ordered = preferred
        .iter()
        .filter(|profile| offered.iter().any(|candidate| candidate == **profile))
        .map(|profile| (*profile).to_string())
        .collect::<Vec<_>>();
    ordered.extend(
        offered
            .iter()
            .filter(|profile| !preferred.contains(&profile.as_str()))
            .cloned(),
    );
    ordered
}

fn profile_set(
    backend: &mut impl Backend,
    requested_mode: RequestedMode,
    requested_profile: Option<String>,
    login_init: bool,
) -> Output {
    let state = match backend.profile_state() {
        Ok(Some(state)) => state,
        Ok(None) if login_init => return Output::success(""),
        Ok(None) => return Output::failure("Power profiles service is not available"),
        Err(error) => return Output::failure(error),
    };
    let mode = match requested_mode {
        RequestedMode::Ac => ProfileMode::Ac,
        RequestedMode::Battery => ProfileMode::Battery,
        RequestedMode::Autodetect => {
            let snapshot = match backend.power_snapshot() {
                Ok(snapshot) => snapshot,
                Err(error) => return Output::failure(error),
            };
            if snapshot.on_battery() {
                ProfileMode::Battery
            } else {
                ProfileMode::Ac
            }
        }
    };

    let explicit = requested_profile.is_some();
    let profile = if let Some(profile) = requested_profile {
        if !state.offered.iter().any(|candidate| candidate == &profile) {
            return Output::failure(format!("Power profile is not available: {profile}"));
        }
        profile
    } else {
        let remembered = match backend.remembered_profile(mode) {
            Ok(profile) => profile,
            Err(error) => return Output::failure(error),
        };
        if let Some(remembered) = remembered.filter(|profile| state.offered.contains(profile)) {
            remembered
        } else if mode == ProfileMode::Ac && state.offered.iter().any(|p| p == "performance") {
            "performance".to_string()
        } else {
            "balanced".to_string()
        }
    };

    if let Err(error) = backend.set_active_profile(&profile) {
        return Output::failure(error);
    }
    if explicit {
        if let Err(error) = backend.remember_profile(mode, &profile) {
            return Output::failure(format!(
                "{profile} is active but not remembered for {}: {error}",
                mode.label()
            ));
        }
    }
    Output::success("")
}

fn stay_awake(backend: &mut impl Backend, action: StayAwakeAction) -> Output {
    let exists = match backend.stay_awake_record_exists() {
        Ok(exists) => exists,
        Err(error) => return Output::failure(error),
    };
    if action == StayAwakeAction::Status {
        return Output::success(if exists {
            "{\"enabled\":true,\"class\":\"enabled\",\"tooltip\":\"Allow Idle Lock & Screensaver\"}\n"
        } else {
            "{\"enabled\":false,\"class\":\"disabled\",\"tooltip\":\"Stay Awake\"}\n"
        });
    }
    let should_exist = match action {
        StayAwakeAction::Toggle => !exists,
        StayAwakeAction::On => true,
        StayAwakeAction::Off => false,
        StayAwakeAction::Status => unreachable!(),
    };
    if let Err(error) = backend.set_stay_awake_record(should_exist) {
        return Output::failure(error);
    }
    Output::success(if should_exist {
        "disabled\n"
    } else {
        "enabled\n"
    })
}

#[derive(Clone, Copy)]
enum ActionKind {
    Logout,
    Reboot,
}

fn scheduled_session_action(backend: &mut impl Backend, action: ActionKind) -> Output {
    let scheduled = match action {
        ActionKind::Logout => backend.schedule_logout(),
        ActionKind::Reboot => backend.schedule_reboot(),
    };
    if let Err(error) = scheduled {
        return Output::failure(error);
    }
    let (message, icon) = match action {
        ActionKind::Logout => (LOGOUT_MESSAGE, "system-log-out"),
        ActionKind::Reboot => (REBOOT_MESSAGE, "system-reboot"),
    };
    let mut stderr = String::new();
    if let Err(error) = backend.show_osd(message, icon, 5_000) {
        stderr.push_str(&format!("display could not be shown: {error}\n"));
    }
    if let Err(error) = backend.close_all() {
        stderr.push_str(&format!("windows could not be closed: {error}\n"));
    }
    backend.sleep(Duration::from_secs(1));
    Output {
        status: 0,
        stdout: String::new(),
        stderr,
    }
}

fn start_shutdown(backend: &mut impl Backend) -> Output {
    let executable = match backend.current_executable() {
        Ok(executable) => executable,
        Err(error) => return Output::failure(error),
    };
    match backend.start_shutdown_worker(&executable) {
        Ok(()) => Output::success(""),
        Err(error) => Output::failure(error),
    }
}

fn shutdown_failure_notice() -> Notice {
    Notice {
        summary: SHUTDOWN_FAILED.to_string(),
        body: SHUTDOWN_FAILED_BODY.to_string(),
        icon: None,
        urgency: Urgency::Critical,
        expire_ms: -1,
    }
}

#[cfg(test)]
mod tests;
