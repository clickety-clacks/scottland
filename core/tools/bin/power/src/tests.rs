use std::collections::BTreeMap;
use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::time::Duration;

use crate::battery::{PowerSnapshot, Supply};
use crate::{
    Backend, Notice, ProfileMode, ProfileState, Urgency, WorkerFailure, execute,
    run_shutdown_worker,
};

#[derive(Default)]
struct FakeBackend {
    events: Vec<String>,
    snapshot: PowerSnapshot,
    profile: Option<ProfileState>,
    profile_state_error: Option<String>,
    set_profile_error: Option<String>,
    active_profile: Option<String>,
    remembered_ac: Option<String>,
    remembered_battery: Option<String>,
    remember_error: Option<String>,
    stay_awake: bool,
    stay_awake_error: Option<String>,
    schedule_error: Option<String>,
    display_error: Option<String>,
    close_error: Option<String>,
    executable: PathBuf,
    worker_error: Option<String>,
    inhibitor_error: Option<String>,
    surface_error: Option<String>,
    power_off_error: Option<WorkerFailure>,
    surface_finished: bool,
    notices: Vec<Notice>,
    hook_failures: Vec<String>,
}

impl Backend for FakeBackend {
    fn power_snapshot(&mut self) -> Result<PowerSnapshot, String> {
        self.events.push("power_snapshot".into());
        Ok(self.snapshot.clone())
    }

    fn profile_state(&mut self) -> Result<Option<ProfileState>, String> {
        self.events.push("profile_state".into());
        if let Some(error) = &self.profile_state_error {
            return Err(error.clone());
        }
        Ok(self.profile.clone())
    }

    fn set_active_profile(&mut self, profile: &str) -> Result<(), String> {
        self.events.push(format!("set_active_profile:{profile}"));
        if let Some(error) = &self.set_profile_error {
            return Err(error.clone());
        }
        if let Some(state) = self.profile.as_mut() {
            state.active = profile.to_string();
        }
        self.active_profile = Some(profile.to_string());
        Ok(())
    }

    fn remembered_profile(&mut self, mode: ProfileMode) -> Result<Option<String>, String> {
        self.events
            .push(format!("read_remembered:{}", mode.label()));
        Ok(match mode {
            ProfileMode::Ac => self.remembered_ac.clone(),
            ProfileMode::Battery => self.remembered_battery.clone(),
        })
    }

    fn remember_profile(&mut self, mode: ProfileMode, profile: &str) -> Result<(), String> {
        self.events
            .push(format!("remember:{}:{profile}", mode.label()));
        if let Some(error) = &self.remember_error {
            return Err(error.clone());
        }
        match mode {
            ProfileMode::Ac => self.remembered_ac = Some(profile.to_string()),
            ProfileMode::Battery => self.remembered_battery = Some(profile.to_string()),
        }
        Ok(())
    }

    fn stay_awake_record_exists(&mut self) -> Result<bool, String> {
        self.events.push("read_stay_awake".into());
        Ok(self.stay_awake)
    }

    fn set_stay_awake_record(&mut self, enabled: bool) -> Result<(), String> {
        self.events.push(format!("write_stay_awake:{enabled}"));
        if let Some(error) = &self.stay_awake_error {
            return Err(error.clone());
        }
        self.stay_awake = enabled;
        Ok(())
    }

    fn notify(&mut self, notice: &Notice) -> Result<(), String> {
        self.events.push("notify".into());
        self.notices.push(notice.clone());
        Ok(())
    }

    fn run_battery_hooks(&mut self, percentage: u8) -> Vec<String> {
        self.events.push(format!("battery_hooks:{percentage}"));
        self.hook_failures.clone()
    }

    fn schedule_logout(&mut self) -> Result<(), String> {
        self.events.push("schedule_logout".into());
        self.schedule_error.clone().map_or(Ok(()), Err)
    }

    fn schedule_reboot(&mut self) -> Result<(), String> {
        self.events.push("schedule_reboot".into());
        self.schedule_error.clone().map_or(Ok(()), Err)
    }

    fn show_osd(&mut self, message: &str, icon: &str, duration_ms: u32) -> Result<(), String> {
        self.events
            .push(format!("show_osd:{message}:{icon}:{duration_ms}"));
        self.display_error.clone().map_or(Ok(()), Err)
    }

    fn close_all(&mut self) -> Result<(), String> {
        self.events.push("close_all".into());
        self.close_error.clone().map_or(Ok(()), Err)
    }

    fn sleep(&mut self, duration: Duration) {
        self.events.push(format!("sleep:{}", duration.as_secs()));
    }

    fn current_executable(&mut self) -> Result<PathBuf, String> {
        self.events.push("current_executable".into());
        Ok(self.executable.clone())
    }

    fn start_shutdown_worker(&mut self, executable: &Path) -> Result<(), String> {
        self.events
            .push(format!("start_worker:{}", executable.display()));
        self.worker_error.clone().map_or(Ok(()), Err)
    }

    fn acquire_shutdown_inhibitor(&mut self) -> Result<(), String> {
        self.events.push("acquire_inhibitor".into());
        self.inhibitor_error.clone().map_or(Ok(()), Err)
    }

    fn spawn_shutdown_surface(&mut self) -> Result<(), String> {
        self.events.push("spawn_surface".into());
        self.surface_error.clone().map_or(Ok(()), Err)
    }

    fn power_off(&mut self) -> Result<(), WorkerFailure> {
        if self.surface_finished {
            self.events.push("power_off_after_surface".into());
        } else {
            self.events.push("power_off_surface_pending".into());
        }
        self.power_off_error.clone().map_or(Ok(()), Err)
    }

    fn release_shutdown_inhibitor(&mut self) {
        self.events.push("release_inhibitor".into());
    }
}

fn args(words: &[&str]) -> Vec<OsString> {
    words.iter().map(OsString::from).collect()
}

fn event_names(backend: &FakeBackend) -> Vec<&str> {
    backend.events.iter().map(String::as_str).collect()
}

fn offered_profile() -> ProfileState {
    ProfileState {
        offered: vec!["balanced".into(), "power-saver".into()],
        active: "balanced".into(),
    }
}

fn test_battery() -> Supply {
    Supply {
        name: "BAT0".to_string(),
        kind: "Battery".to_string(),
        present: Some(true),
        scope: Some("System".to_string()),
        online: None,
        attributes: BTreeMap::from([
            ("capacity".to_string(), "51".to_string()),
            ("status".to_string(), "Discharging".to_string()),
        ]),
    }
}

#[test]
fn logout_schedules_once_before_display_and_close_all() {
    let mut backend = FakeBackend {
        close_error: Some("ctl unavailable".into()),
        ..FakeBackend::default()
    };

    let output = execute(&args(&["logout"]), &mut backend);

    assert_eq!(output.status, 0);
    assert!(output.stdout.is_empty());
    assert!(output.stderr.contains("windows could not be closed"));
    assert_eq!(
        event_names(&backend),
        vec![
            "schedule_logout",
            "show_osd:Logging out:system-log-out:5000",
            "close_all",
            "sleep:1"
        ]
    );
}

#[test]
fn refused_logout_schedule_leaves_windows_and_display_untouched() {
    let mut backend = FakeBackend {
        schedule_error: Some("timer refused".into()),
        ..FakeBackend::default()
    };

    let output = execute(&args(&["logout"]), &mut backend);

    assert_eq!(output.status, 1);
    assert!(output.stdout.is_empty());
    assert!(output.stderr.contains("timer refused"));
    assert_eq!(event_names(&backend), vec!["schedule_logout"]);
}

#[test]
fn inhibitor_refusal_never_starts_display_or_closes_windows() {
    let mut backend = FakeBackend {
        inhibitor_error: Some("inhibitor refused".into()),
        ..FakeBackend::default()
    };

    let status = run_shutdown_worker(&mut backend);

    assert_eq!(status, 1);
    assert_eq!(event_names(&backend), vec!["acquire_inhibitor", "notify"]);
    assert_eq!(backend.notices.len(), 1);
    assert_eq!(backend.notices[0].summary, "Shutdown failed");
    assert_eq!(backend.notices[0].urgency, Urgency::Critical);
}

#[test]
fn shutdown_power_off_does_not_wait_for_surface_or_close_all() {
    let mut backend = FakeBackend::default();

    let status = run_shutdown_worker(&mut backend);

    assert_eq!(status, 0);
    assert_eq!(
        event_names(&backend),
        vec![
            "acquire_inhibitor",
            "spawn_surface",
            "sleep:2",
            "power_off_surface_pending",
            "release_inhibitor"
        ]
    );
}

#[test]
fn failed_power_off_notifies_and_releases_the_inhibitor_with_failure_status() {
    let mut backend = FakeBackend {
        power_off_error: Some(WorkerFailure {
            status: 5,
            message: "polkit refused".into(),
        }),
        ..FakeBackend::default()
    };

    let status = run_shutdown_worker(&mut backend);

    assert_eq!(status, 5);
    assert_eq!(backend.notices.len(), 1);
    assert!(backend.events.contains(&"release_inhibitor".to_string()));
    assert!(backend.events.contains(&"notify".to_string()));
}

#[test]
fn shutdown_starts_the_worker_with_the_exact_current_executable() {
    let executable = PathBuf::from("/opt/scottland/libexec/scottland-power");
    let mut backend = FakeBackend {
        executable: executable.clone(),
        ..FakeBackend::default()
    };

    let output = execute(&args(&["shutdown"]), &mut backend);

    assert_eq!(output.status, 0);
    assert!(output.stdout.is_empty());
    assert!(output.stderr.is_empty());
    assert_eq!(backend.events.len(), 2);
    assert_eq!(backend.events[0], "current_executable");
    assert_eq!(
        backend.events[1],
        format!("start_worker:{}", executable.display())
    );
}

#[test]
fn profile_remember_failure_reports_active_but_not_remembered() {
    let mut backend = FakeBackend {
        profile: Some(offered_profile()),
        remember_error: Some("state directory is read-only".into()),
        ..FakeBackend::default()
    };

    let output = execute(
        &args(&["profile", "set", "battery", "power-saver"]),
        &mut backend,
    );

    assert_eq!(output.status, 1);
    assert!(output.stdout.is_empty());
    assert!(
        output
            .stderr
            .contains("power-saver is active but not remembered for battery")
    );
    assert_eq!(backend.active_profile.as_deref(), Some("power-saver"));
    assert_eq!(
        event_names(&backend),
        vec![
            "profile_state",
            "set_active_profile:power-saver",
            "remember:battery:power-saver"
        ]
    );
}

#[test]
fn profile_init_quietly_succeeds_only_when_the_service_is_absent() {
    let mut absent = FakeBackend::default();
    let output = execute(&args(&["profile", "init"]), &mut absent);
    assert_eq!(output.status, 0);
    assert!(output.stdout.is_empty());
    assert!(output.stderr.is_empty());
    assert_eq!(event_names(&absent), vec!["profile_state"]);

    let mut failed = FakeBackend {
        profile_state_error: Some("profile property read failed".into()),
        ..FakeBackend::default()
    };
    let output = execute(&args(&["profile", "init"]), &mut failed);
    assert_eq!(output.status, 1);
    assert!(output.stdout.is_empty());
    assert!(output.stderr.contains("profile property read failed"));
    assert_eq!(event_names(&failed), vec!["profile_state"]);
}

#[test]
fn stay_awake_write_failure_has_no_success_output_and_preserves_the_record() {
    let mut backend = FakeBackend {
        stay_awake: false,
        stay_awake_error: Some("state directory is read-only".into()),
        ..FakeBackend::default()
    };

    let output = execute(&args(&["stay-awake", "on"]), &mut backend);

    assert_eq!(output.status, 1);
    assert!(output.stdout.is_empty());
    assert!(output.stderr.contains("state directory is read-only"));
    assert!(!backend.stay_awake);
    assert_eq!(
        event_names(&backend),
        vec!["read_stay_awake", "write_stay_awake:true"]
    );
}

#[test]
fn explicit_profile_set_remembers_only_the_profile_that_became_active() {
    let mut backend = FakeBackend {
        profile: Some(offered_profile()),
        ..FakeBackend::default()
    };

    let output = execute(
        &args(&["profile", "set", "battery", "power-saver"]),
        &mut backend,
    );

    assert_eq!(output.status, 0);
    assert!(output.stdout.is_empty());
    assert!(output.stderr.is_empty());
    assert_eq!(backend.active_profile.as_deref(), Some("power-saver"));
    assert_eq!(backend.remembered_battery.as_deref(), Some("power-saver"));
    assert_eq!(backend.remembered_ac, None);
    assert!(
        backend
            .events
            .iter()
            .any(|event| event == "remember:battery:power-saver")
    );
}

#[test]
fn profile_set_uses_a_separate_remembered_profile_for_each_mode() {
    let state = ProfileState {
        offered: vec![
            "balanced".into(),
            "power-saver".into(),
            "performance".into(),
        ],
        active: "balanced".into(),
    };
    let mut ac = FakeBackend {
        profile: Some(state.clone()),
        remembered_ac: Some("performance".into()),
        remembered_battery: Some("power-saver".into()),
        ..FakeBackend::default()
    };
    let mut battery = FakeBackend {
        profile: Some(state),
        remembered_ac: Some("performance".into()),
        remembered_battery: Some("power-saver".into()),
        ..FakeBackend::default()
    };

    assert_eq!(execute(&args(&["profile", "set", "ac"]), &mut ac).status, 0);
    assert_eq!(
        execute(&args(&["profile", "set", "battery"]), &mut battery).status,
        0
    );
    assert_eq!(ac.active_profile.as_deref(), Some("performance"));
    assert_eq!(battery.active_profile.as_deref(), Some("power-saver"));
    assert!(!ac.events.iter().any(|event| event.starts_with("remember:")));
    assert!(
        !battery
            .events
            .iter()
            .any(|event| event.starts_with("remember:"))
    );
}

#[test]
fn profile_autodetect_uses_ac_and_battery_fallbacks() {
    let state = ProfileState {
        offered: vec!["balanced".into(), "performance".into()],
        active: "balanced".into(),
    };
    let mut ac = FakeBackend {
        profile: Some(state.clone()),
        ..FakeBackend::default()
    };
    let mut battery = FakeBackend {
        profile: Some(state),
        snapshot: PowerSnapshot {
            supplies: vec![test_battery()],
        },
        ..FakeBackend::default()
    };

    assert_eq!(execute(&args(&["profile", "set"]), &mut ac).status, 0);
    assert_eq!(execute(&args(&["profile", "set"]), &mut battery).status, 0);
    assert_eq!(ac.active_profile.as_deref(), Some("performance"));
    assert_eq!(battery.active_profile.as_deref(), Some("balanced"));
    assert!(!ac.events.iter().any(|event| event.starts_with("remember:")));
    assert!(
        !battery
            .events
            .iter()
            .any(|event| event.starts_with("remember:"))
    );
}

#[test]
fn unavailable_profile_is_refused_without_changing_or_remembering_state() {
    let mut backend = FakeBackend {
        profile: Some(offered_profile()),
        ..FakeBackend::default()
    };

    let output = execute(&args(&["profile", "set", "ac", "turbo"]), &mut backend);

    assert_eq!(output.status, 1);
    assert!(output.stdout.is_empty());
    assert_eq!(output.stderr, "Power profile is not available: turbo\n");
    assert_eq!(backend.active_profile, None);
    assert_eq!(backend.remembered_ac, None);
    assert!(
        !backend
            .events
            .iter()
            .any(|event| event.starts_with("remember:"))
    );
}

#[test]
fn profile_service_refusal_does_not_remember_the_requested_profile() {
    let mut backend = FakeBackend {
        profile: Some(offered_profile()),
        set_profile_error: Some("service refused profile".into()),
        ..FakeBackend::default()
    };

    let output = execute(
        &args(&["profile", "set", "battery", "power-saver"]),
        &mut backend,
    );

    assert_eq!(output.status, 1);
    assert_eq!(output.stderr, "service refused profile\n");
    assert_eq!(backend.active_profile, None);
    assert_eq!(backend.remembered_battery, None);
    assert!(
        !backend
            .events
            .iter()
            .any(|event| event.starts_with("remember:"))
    );
}

#[test]
fn missing_profile_service_fails_list_and_set_but_profile_init_is_quiet() {
    for command in [vec!["profile", "list"], vec!["profile", "set"]] {
        let mut backend = FakeBackend::default();
        let output = execute(&args(&command), &mut backend);
        assert_eq!(output.status, 1);
        assert!(output.stdout.is_empty());
        assert_eq!(output.stderr, "Power profiles service is not available\n");
    }

    let mut backend = FakeBackend::default();
    let output = execute(&args(&["profile", "init"]), &mut backend);
    assert_eq!(output.status, 0);
    assert!(output.stdout.is_empty());
    assert!(output.stderr.is_empty());
}

#[test]
fn no_battery_commands_report_no_battery_without_sending_a_notice() {
    for command in [
        vec!["battery"],
        vec!["battery", "--fields"],
        vec!["battery", "notify"],
    ] {
        let mut backend = FakeBackend::default();
        let output = execute(&args(&command), &mut backend);
        assert_eq!(output.status, 1);
        assert!(output.stdout.is_empty());
        assert_eq!(output.stderr, "No battery\n");
        assert!(backend.notices.is_empty());
        assert_eq!(event_names(&backend), vec!["power_snapshot"]);
    }
}

#[test]
fn present_external_accepts_online_mains_and_usb_only() {
    for (kind, online, expected) in [
        ("Mains", Some(true), 0),
        ("USB", Some(true), 0),
        ("Mains", Some(false), 1),
        ("Battery", Some(true), 1),
    ] {
        let supply = Supply {
            name: "SUPPLY".into(),
            kind: kind.into(),
            present: None,
            scope: None,
            online,
            attributes: BTreeMap::new(),
        };
        let mut backend = FakeBackend {
            snapshot: PowerSnapshot {
                supplies: vec![supply],
            },
            ..FakeBackend::default()
        };

        let output = execute(&args(&["present", "external"]), &mut backend);
        assert_eq!(output.status, expected, "{kind} online={online:?}");
        assert!(output.stdout.is_empty());
        assert!(output.stderr.is_empty());
    }
}

#[test]
fn battery_low_reports_failed_hooks_after_sending_the_notice() {
    let mut backend = FakeBackend {
        hook_failures: vec!["10-first-hook".into(), "20-second-hook".into()],
        ..FakeBackend::default()
    };

    let output = execute(&args(&["battery", "low", "9"]), &mut backend);

    assert_eq!(output.status, 1);
    assert!(output.stdout.is_empty());
    assert_eq!(
        output.stderr,
        "battery-low hook failed: 10-first-hook\nbattery-low hook failed: 20-second-hook\n"
    );
    assert_eq!(event_names(&backend), vec!["notify", "battery_hooks:9"]);
    assert_eq!(backend.notices.len(), 1);
    assert_eq!(backend.notices[0].summary, "Time to recharge!");
    assert_eq!(backend.notices[0].body, "Battery is down to 9%");
    assert_eq!(backend.notices[0].icon.as_deref(), Some("battery-caution"));
    assert_eq!(backend.notices[0].urgency, Urgency::Critical);
    assert_eq!(backend.notices[0].expire_ms, 30_000);
}

#[test]
fn stay_awake_on_and_off_change_the_record_and_report_its_new_state() {
    let mut off = FakeBackend::default();
    let on_output = execute(&args(&["stay-awake", "on"]), &mut off);
    assert_eq!(on_output.status, 0);
    assert_eq!(on_output.stdout, "disabled\n");
    assert!(off.stay_awake);

    let mut on = FakeBackend {
        stay_awake: true,
        ..FakeBackend::default()
    };
    let off_output = execute(&args(&["stay-awake", "off"]), &mut on);
    assert_eq!(off_output.status, 0);
    assert_eq!(off_output.stdout, "enabled\n");
    assert!(!on.stay_awake);
}

#[test]
fn stay_awake_status_reports_presence_without_writing_the_record() {
    let mut on = FakeBackend {
        stay_awake: true,
        ..FakeBackend::default()
    };
    let on_output = execute(&args(&["stay-awake", "status"]), &mut on);
    assert_eq!(
        on_output.stdout,
        "{\"enabled\":true,\"class\":\"enabled\",\"tooltip\":\"Allow Idle Lock & Screensaver\"}\n"
    );
    assert_eq!(event_names(&on), vec!["read_stay_awake"]);

    let mut off = FakeBackend::default();
    let off_output = execute(&args(&["stay-awake", "status"]), &mut off);
    assert_eq!(
        off_output.stdout,
        "{\"enabled\":false,\"class\":\"disabled\",\"tooltip\":\"Stay Awake\"}\n"
    );
    assert_eq!(event_names(&off), vec!["read_stay_awake"]);
}
