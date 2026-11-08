//! The waiter route against a stand-in receiver on a private session bus (contract R-S3, R-A6).

#![cfg(unix)]

use std::collections::HashMap;
use std::fs;
use std::io::{BufRead, BufReader};
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Output, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use zbus::blocking::Connection;
use zbus::blocking::connection::Builder;
use zbus::blocking::fdo::DBusProxy;
use zbus::message::Header;
use zbus::names::BusName;
use zbus::zvariant::OwnedValue;

const NAME: &str = "org.freedesktop.Notifications";
const PATH: &str = "/org/freedesktop/Notifications";

#[derive(Default)]
struct State {
    capabilities: Vec<String>,
    refuse: bool,
    calls: Vec<String>,
    actions: Vec<Vec<String>>,
    hints: Vec<HashMap<String, OwnedValue>>,
    callers: Vec<String>,
}

struct Receiver(Arc<Mutex<State>>);

#[zbus::interface(name = "org.freedesktop.Notifications")]
impl Receiver {
    fn get_capabilities(&self, #[zbus(header)] header: Header<'_>) -> Vec<String> {
        let mut state = self.0.lock().unwrap();
        state.calls.push("GetCapabilities".into());
        state.callers.push(header.sender().unwrap().to_string());
        state.capabilities.clone()
    }

    #[allow(clippy::too_many_arguments)]
    fn notify(
        &self,
        #[zbus(header)] header: Header<'_>,
        _app_name: String,
        _replaces_id: u32,
        _app_icon: String,
        _summary: String,
        _body: String,
        actions: Vec<String>,
        hints: HashMap<String, OwnedValue>,
        _expire_timeout: i32,
    ) -> zbus::fdo::Result<u32> {
        let mut state = self.0.lock().unwrap();
        state.calls.push("Notify".into());
        state.callers.push(header.sender().unwrap().to_string());
        if state.refuse {
            return Err(zbus::fdo::Error::Failed("refused by the stand-in".into()));
        }
        state.actions.push(actions);
        state.hints.push(hints);
        Ok(7)
    }
}

struct Bus {
    daemon: Child,
    address: String,
    connection: Connection,
    state: Arc<Mutex<State>>,
    root: PathBuf,
}

impl Bus {
    fn start(name: &str, capabilities: &[&str], refuse: bool) -> Self {
        let root = std::env::temp_dir().join(format!(
            "scottland-notify-waiter-{name}-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(root.join("hooks")).unwrap();
        let mut daemon = Command::new("dbus-daemon")
            .args(["--session", "--nofork", "--print-address"])
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .spawn()
            .expect("start a private dbus-daemon");
        let mut address = String::new();
        BufReader::new(daemon.stdout.take().unwrap())
            .read_line(&mut address)
            .unwrap();
        let address = address.trim().to_string();
        let state = Arc::new(Mutex::new(State {
            capabilities: capabilities.iter().map(|c| c.to_string()).collect(),
            refuse,
            ..State::default()
        }));
        let connection = Builder::address(address.as_str())
            .unwrap()
            .name(NAME)
            .unwrap()
            .serve_at(PATH, Receiver(state.clone()))
            .unwrap()
            .build()
            .unwrap();
        Self {
            daemon,
            address,
            connection,
            state,
            root,
        }
    }

    fn send(&self, args: &[&str]) -> Output {
        Command::new(env!("CARGO_BIN_EXE_scottland-notify"))
            .arg("send")
            .args(args)
            .env("DBUS_SESSION_BUS_ADDRESS", &self.address)
            .env("SCOTTLAND_NOTIFY_HOOKS", self.root.join("hooks"))
            .stdin(Stdio::null())
            .output()
            .unwrap()
    }

    fn file(&self, name: &str) -> String {
        self.root.join(name).display().to_string()
    }

    fn invoke(&self, id: u32, key: &str) {
        self.connection
            .emit_signal(None::<BusName<'_>>, PATH, NAME, "ActionInvoked", &(id, key))
            .unwrap();
    }

    fn close(&self, id: u32) {
        self.connection
            .emit_signal(
                None::<BusName<'_>>,
                PATH,
                NAME,
                "NotificationClosed",
                &(id, 2u32),
            )
            .unwrap();
    }

    fn calls(&self) -> Vec<String> {
        self.state.lock().unwrap().calls.clone()
    }

    fn waiter(&self) -> String {
        self.state.lock().unwrap().callers[0].clone()
    }

    fn has_owner(&self, name: &str) -> bool {
        DBusProxy::new(&self.connection)
            .unwrap()
            .name_has_owner(BusName::try_from(name).unwrap())
            .unwrap()
    }

    fn wait_until_gone(&self, name: &str) {
        let deadline = Instant::now() + Duration::from_secs(5);
        while self.has_owner(name) {
            assert!(Instant::now() < deadline, "the waiter did not end");
            thread::sleep(Duration::from_millis(20));
        }
    }
}

impl Drop for Bus {
    fn drop(&mut self) {
        let _ = self.daemon.kill();
        let _ = self.daemon.wait();
        let _ = fs::remove_dir_all(&self.root);
    }
}

fn wait_for(path: &Path) {
    let deadline = Instant::now() + Duration::from_secs(5);
    while !path.exists() {
        assert!(
            Instant::now() < deadline,
            "{} never appeared",
            path.display()
        );
        thread::sleep(Duration::from_millis(20));
    }
}

fn stdout(output: &Output) -> String {
    String::from_utf8_lossy(&output.stdout).trim().to_string()
}

fn stderr(output: &Output) -> String {
    String::from_utf8_lossy(&output.stderr).into_owned()
}

#[test]
fn a_click_runs_its_argv_once_without_a_shell_and_the_waiter_ends() {
    let bus = Bus::start("click", &["actions", "body"], false);
    let target = bus.file("a b.txt");
    let script = "printf x >> \"$1\"";
    let output = bus.send(&["-p", "Hi", "--exec", "sh", "-c", script, "sh", &target]);
    assert!(output.status.success(), "{}", stderr(&output));
    assert_eq!(stdout(&output), "7");
    assert_eq!(bus.calls(), ["GetCapabilities", "Notify"]);
    assert_eq!(bus.state.lock().unwrap().actions[0], ["default", ""]);
    let waiter = bus.waiter();
    assert!(bus.has_owner(&waiter));

    // Another notification's action, and a key this one does not have, are not this waiter's.
    bus.invoke(8, "default");
    bus.invoke(7, "2");
    thread::sleep(Duration::from_millis(200));
    assert!(!Path::new(&target).exists());
    assert!(bus.has_owner(&waiter));

    bus.invoke(7, "default");
    wait_for(Path::new(&target));
    bus.wait_until_gone(&waiter);
    bus.invoke(7, "default");
    thread::sleep(Duration::from_millis(200));
    assert_eq!(fs::read_to_string(&target).unwrap(), "x");
}

#[test]
fn a_button_runs_its_own_words_followed_by_the_with_arguments() {
    let bus = Bus::start("buttons", &["actions"], false);
    let one = bus.file("one");
    let two = bus.file("two");
    let record = "printf '%s|' \"$@\" > \"$1\"";
    let output = bus.send(&[
        "Hi", "--action", "One", "sh", "-c", record, "sh", &one, "--action", "Two", "sh", "-c",
        record, "sh", &two, "--with", "a b", "-c",
    ]);
    assert!(output.status.success(), "{}", stderr(&output));
    assert_eq!(stdout(&output), "");
    assert_eq!(
        bus.state.lock().unwrap().actions[0],
        ["default", "", "1", "One", "2", "Two"]
    );
    let waiter = bus.waiter();
    bus.invoke(7, "2");
    wait_for(Path::new(&two));
    bus.wait_until_gone(&waiter);
    assert_eq!(fs::read_to_string(&two).unwrap(), format!("{two}|a b|-c|"));
    assert!(!Path::new(&one).exists());
}

#[test]
fn closing_the_notification_ends_the_waiter_without_running_anything() {
    let bus = Bus::start("closed", &["actions"], false);
    let target = bus.file("never");
    let output = bus.send(&["Hi", "--exec", "touch", &target]);
    assert!(output.status.success(), "{}", stderr(&output));
    let waiter = bus.waiter();
    bus.close(8);
    thread::sleep(Duration::from_millis(200));
    assert!(bus.has_owner(&waiter));
    bus.close(7);
    bus.wait_until_gone(&waiter);
    bus.invoke(7, "default");
    thread::sleep(Duration::from_millis(200));
    assert!(!Path::new(&target).exists());
}

#[test]
fn replacement_is_refused_before_the_bus_is_used() {
    let bus = Bus::start("replace", &["actions"], false);
    let output = bus.send(&["-r", "7", "B", "--exec", "true"]);
    assert!(!output.status.success());
    assert!(stderr(&output).contains("replacing a notification with a click command"));
    assert!(bus.calls().is_empty());
}

#[test]
fn a_receiver_without_actions_gets_no_notify_and_no_waiter_remains() {
    let bus = Bus::start("noactions", &["body"], false);
    let output = bus.send(&["Hi", "--exec", "true"]);
    assert!(!output.status.success());
    let error = stderr(&output);
    assert!(
        error.contains("actions") && error.contains("not available"),
        "{error}"
    );
    assert_eq!(bus.calls(), ["GetCapabilities"]);
    bus.wait_until_gone(&bus.waiter());
}

#[test]
fn a_refused_notify_fails_and_leaves_no_waiter() {
    let bus = Bus::start("refused", &["actions"], true);
    let output = bus.send(&["Hi", "--exec", "true"]);
    assert!(!output.status.success());
    assert!(stderr(&output).contains("refused by the stand-in"));
    assert_eq!(bus.calls(), ["GetCapabilities", "Notify"]);
    bus.wait_until_gone(&bus.waiter());
}

#[test]
fn a_receiver_route_sends_its_hints_and_no_actions_without_a_waiter() {
    let bus = Bus::start("route", &["actions"], false);
    let hook = bus.root.join("hooks/10-route");
    fs::write(
        &hook,
        "#!/bin/sh\n[ \"$1\" = action-route ] || exit 69\nprintf 'route-argv\\0%s\\0' \"$4\"\n",
    )
    .unwrap();
    fs::set_permissions(&hook, fs::Permissions::from_mode(0o755)).unwrap();
    let output = bus.send(&["--transient", "-r", "3", "-p", "Hi", "--exec", "true"]);
    assert!(output.status.success(), "{}", stderr(&output));
    assert_eq!(stdout(&output), "7");
    assert_eq!(bus.calls(), ["Notify"]);
    let state = bus.state.lock().unwrap();
    assert!(state.actions[0].is_empty());
    let hints = &state.hints[0];
    assert_eq!(
        String::try_from(hints["route-argv"].clone()).unwrap(),
        "true"
    );
    assert!(bool::try_from(hints["transient"].clone()).unwrap());
}
