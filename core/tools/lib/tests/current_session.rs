//! Process environment discovery scenarios. The parent tests open no listener fixtures;
//! only the selected child scenario creates sockets, after it starts.

mod common;

use common::Runtime;
use scottland::session::{self, SessionError};
use std::fs;

// Each scenario uses a subprocess so parallel tests never mutate shared environment variables.
#[test]
fn current_discovers_the_override_directory() {
    current_scenario("override");
}

#[test]
fn current_keeps_default_discovery_when_override_is_unset_or_empty() {
    current_scenario("unset");
    current_scenario("empty");
}

#[test]
fn current_does_not_fall_back_from_an_override() {
    current_scenario("missing");
}

fn current_scenario(scenario: &str) {
    let result = std::process::Command::new(std::env::current_exe().unwrap())
        .args([
            "--exact",
            "current_environment_child",
            "--ignored",
            "--nocapture",
        ])
        .env("SESSION_TEST_SCENARIO", scenario)
        .env_remove("WAYFIRE_SOCKET")
        .output()
        .unwrap();
    assert!(
        result.status.success(),
        "{scenario}: {}{}",
        String::from_utf8_lossy(&result.stdout),
        String::from_utf8_lossy(&result.stderr)
    );
}

#[test]
#[ignore = "invoked by current_scenario in a private process"]
fn current_environment_child() {
    let scenario = std::env::var("SESSION_TEST_SCENARIO").unwrap();
    let default = Runtime::new("default");
    let private = Runtime::new("private");
    // Point the default lookup at a test-owned record directory, never a real session.
    fs::create_dir(default.dir.join("scottland")).unwrap();
    let default_records = Runtime {
        dir: default.dir.join("scottland"),
    };
    let _default_live = default_records.live("wayland-1");
    let _private_one = private.live("wayland-2");
    let (_private_two, caller) = private.live("wayland-3");
    private.stale("wayland-4");
    // This process runs no concurrent tests; environment changes stay in this child.
    unsafe {
        std::env::set_var("XDG_RUNTIME_DIR", &default.dir);
        match scenario.as_str() {
            "unset" => std::env::remove_var("SCOTTLAND_SESSION_DIR"),
            "empty" => std::env::set_var("SCOTTLAND_SESSION_DIR", ""),
            "override" => std::env::set_var("SCOTTLAND_SESSION_DIR", &private.dir),
            "missing" => std::env::set_var("SCOTTLAND_SESSION_DIR", private.dir.join("absent")),
            _ => panic!("unknown scenario"),
        }
    }
    match scenario.as_str() {
        "unset" | "empty" => assert_eq!(session::current(None).unwrap().name, "wayland-1"),
        "missing" => assert_eq!(session::current(None), Err(SessionError::NoneRunning)),
        "override" => {
            assert_eq!(
                session::current(Some("wayland-2")).unwrap().name,
                "wayland-2"
            );
            assert_eq!(
                session::current(Some("wayland-4")),
                Err(SessionError::NotRunning("wayland-4".into()))
            );
            unsafe {
                std::env::set_var("WAYFIRE_SOCKET", caller);
            }
            assert_eq!(session::current(None).unwrap().name, "wayland-3");
            assert_eq!(
                session::current(Some("wayland-2")).unwrap().name,
                "wayland-2"
            );
        }
        _ => unreachable!(),
    }
}
