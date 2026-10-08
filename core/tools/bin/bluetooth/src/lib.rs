mod backend;

pub use backend::{Radio, SystemBackend};

use std::ffi::OsString;
use std::time::Duration;

const USAGE: &str = "usage: scottland-bluetooth power on|off|toggle|is-on\n\
       scottland-bluetooth device pair|connect|disconnect|forget ADDRESS\n\
       scottland-bluetooth unblock\n";

const RADIO_WAIT: Duration = Duration::from_secs(2);
const CONTROLLER_POWER_WAIT: Duration = Duration::from_secs(5);
const DEVICE_OPERATION_WAIT: Duration = Duration::from_secs(20);
const DISCONNECT_WAIT: Duration = Duration::from_secs(10);
const PROPERTY_TIMEOUT: Duration = Duration::from_secs(2);

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Adapter {
    pub path: String,
    pub powered: bool,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Device {
    pub address: String,
    pub paired: bool,
    pub trusted: bool,
    pub connected: bool,
}

pub trait Backend {
    fn request_radio_block(&mut self, blocked: bool) -> Result<(), String>;
    fn radios(&mut self) -> Result<Vec<Radio>, String>;
    fn adapters(&mut self, timeout: Duration) -> Result<Vec<Adapter>, String>;
    fn set_default_adapter_power(&mut self, powered: bool, timeout: Duration)
    -> Result<(), String>;
    fn device(&mut self, address: &str, timeout: Duration) -> Result<Option<Device>, String>;
    fn pair(&mut self, address: &str, timeout: Duration) -> Result<(), String>;
    fn trust(&mut self, address: &str, timeout: Duration) -> Result<(), String>;
    fn connect(&mut self, address: &str, timeout: Duration) -> Result<(), String>;
    fn disconnect(&mut self, address: &str, timeout: Duration) -> Result<(), String>;
    fn remove(&mut self, address: &str, timeout: Duration) -> Result<(), String>;
}

pub trait Waiter {
    fn wait_for<F>(&mut self, timeout: Duration, condition: F) -> Result<bool, String>
    where
        F: FnMut(Duration) -> Result<bool, String>;
}

#[derive(Default)]
pub struct SystemWaiter;

impl Waiter for SystemWaiter {
    fn wait_for<F>(&mut self, timeout: Duration, mut condition: F) -> Result<bool, String>
    where
        F: FnMut(Duration) -> Result<bool, String>,
    {
        let deadline = std::time::Instant::now() + timeout;
        loop {
            let remaining = deadline.saturating_duration_since(std::time::Instant::now());
            if remaining.is_zero() {
                return Ok(false);
            }
            if condition(remaining)? {
                return Ok(true);
            }

            let remaining = deadline.saturating_duration_since(std::time::Instant::now());
            if remaining.is_zero() {
                return Ok(false);
            }
            std::thread::sleep(remaining.min(Duration::from_millis(100)));
        }
    }
}

#[derive(Debug, PartialEq, Eq)]
pub struct Output {
    pub status: u8,
    pub stdout: String,
    pub stderr: String,
}

impl Output {
    fn success(stdout: String) -> Self {
        Self {
            status: 0,
            stdout,
            stderr: String::new(),
        }
    }

    fn failure(message: String) -> Self {
        Self {
            status: 1,
            stdout: String::new(),
            stderr: format!("{message}\n"),
        }
    }

    fn usage() -> Self {
        Self {
            status: 2,
            stdout: String::new(),
            stderr: USAGE.to_string(),
        }
    }
}

#[derive(Clone)]
enum Command {
    Power(PowerAction),
    Device(DeviceAction, String),
    Unblock,
}

#[derive(Clone, Copy)]
enum PowerAction {
    On,
    Off,
    Toggle,
    IsOn,
}

#[derive(Clone, Copy)]
enum DeviceAction {
    Pair,
    Connect,
    Disconnect,
    Forget,
}

fn parse(args: &[OsString]) -> Option<Command> {
    let args = args
        .iter()
        .map(|argument| argument.to_str())
        .collect::<Option<Vec<_>>>()?;
    match args.as_slice() {
        ["power", "on"] => Some(Command::Power(PowerAction::On)),
        ["power", "off"] => Some(Command::Power(PowerAction::Off)),
        ["power", "toggle"] => Some(Command::Power(PowerAction::Toggle)),
        ["power", "is-on"] => Some(Command::Power(PowerAction::IsOn)),
        ["device", action, address] if valid_address(address) => {
            let action = match *action {
                "pair" => DeviceAction::Pair,
                "connect" => DeviceAction::Connect,
                "disconnect" => DeviceAction::Disconnect,
                "forget" => DeviceAction::Forget,
                _ => return None,
            };
            Some(Command::Device(action, (*address).to_string()))
        }
        ["unblock"] => Some(Command::Unblock),
        _ => None,
    }
}

fn valid_address(address: &str) -> bool {
    let bytes = address.as_bytes();
    bytes.len() == 17
        && bytes.iter().enumerate().all(|(index, byte)| {
            if matches!(index, 2 | 5 | 8 | 11 | 14) {
                *byte == b':'
            } else {
                byte.is_ascii_hexdigit()
            }
        })
}

pub fn execute(args: &[OsString], backend: &mut impl Backend, waiter: &mut impl Waiter) -> Output {
    let Some(command) = parse(args) else {
        return Output::usage();
    };

    match command {
        Command::Power(PowerAction::On) => output_without_stdout(power_on(backend, waiter)),
        Command::Power(PowerAction::Off) => output_without_stdout(power_off(backend)),
        Command::Power(PowerAction::Toggle) => output_without_stdout(toggle(backend, waiter)),
        Command::Power(PowerAction::IsOn) => match is_on(backend, PROPERTY_TIMEOUT) {
            Ok(true) => Output::success(String::new()),
            Ok(false) => Output {
                status: 1,
                stdout: String::new(),
                stderr: String::new(),
            },
            Err(message) => Output::failure(message),
        },
        Command::Device(action, address) => {
            output_without_stdout(device_action(action, &address, backend, waiter))
        }
        Command::Unblock => match unblock(backend) {
            Ok(stdout) => Output::success(stdout),
            Err((stdout, message)) => Output {
                status: 1,
                stdout,
                stderr: format!("{message}\n"),
            },
        },
    }
}

fn output_without_stdout(result: Result<(), String>) -> Output {
    match result {
        Ok(()) => Output::success(String::new()),
        Err(message) => Output::failure(message),
    }
}

fn is_on(backend: &mut impl Backend, timeout: Duration) -> Result<bool, String> {
    backend
        .adapters(timeout)
        .map(|adapters| adapters.iter().any(|adapter| adapter.powered))
        .map_err(|error| format!("could not read Bluetooth adapters: {error}"))
}

fn power_on(backend: &mut impl Backend, waiter: &mut impl Waiter) -> Result<(), String> {
    let lift_error = backend.request_radio_block(false).err();
    let radios = backend.radios().map_err(|error| {
        let mut message = format!("could not read Bluetooth radio state: {error}");
        append_reason(
            &mut message,
            "radio unblock request failed",
            lift_error.as_deref(),
        );
        message
    })?;

    if radios.iter().any(|radio| radio.soft_blocked) {
        let mut message = "Bluetooth radio is still soft blocked".to_string();
        append_reason(
            &mut message,
            "radio unblock request failed",
            lift_error.as_deref(),
        );
        return Err(message);
    }

    if waiter
        .wait_for(RADIO_WAIT, |remaining| is_on(backend, remaining))
        .map_err(|error| format!("could not check Bluetooth power: {error}"))?
    {
        return Ok(());
    }

    let power_error = backend
        .set_default_adapter_power(true, CONTROLLER_POWER_WAIT)
        .err();

    if waiter
        .wait_for(RADIO_WAIT, |remaining| is_on(backend, remaining))
        .map_err(|error| format!("could not check Bluetooth power: {error}"))?
    {
        return Ok(());
    }

    let mut message = "Bluetooth adapter did not come up".to_string();
    append_reason(
        &mut message,
        "controller power request failed",
        power_error.as_deref(),
    );
    Err(message)
}

fn power_off(backend: &mut impl Backend) -> Result<(), String> {
    backend
        .request_radio_block(true)
        .map_err(|error| format!("could not block Bluetooth radios: {error}"))
}

fn toggle(backend: &mut impl Backend, waiter: &mut impl Waiter) -> Result<(), String> {
    if is_on(backend, PROPERTY_TIMEOUT)? {
        power_off(backend)
    } else {
        power_on(backend, waiter)
    }
}

fn unblock(backend: &mut impl Backend) -> Result<String, (String, String)> {
    let request_error = backend.request_radio_block(false).err();
    let radios = backend.radios().map_err(|error| {
        let mut message = format!("could not read Bluetooth radio state: {error}");
        append_reason(
            &mut message,
            "radio unblock request failed",
            request_error.as_deref(),
        );
        (String::new(), message)
    })?;

    let report = radios
        .iter()
        .map(|radio| {
            format!(
                "{}: soft blocked: {}; hard blocked: {}\n",
                radio.name,
                yes_no(radio.soft_blocked),
                yes_no(radio.hard_blocked)
            )
        })
        .collect::<String>();

    if radios.is_empty() {
        let mut message = "no Bluetooth radios were found".to_string();
        append_reason(
            &mut message,
            "radio unblock request failed",
            request_error.as_deref(),
        );
        return Err((String::new(), message));
    }

    if radios
        .iter()
        .any(|radio| radio.soft_blocked || radio.hard_blocked)
    {
        let mut message = "one or more Bluetooth radios remain blocked".to_string();
        append_reason(
            &mut message,
            "radio unblock request failed",
            request_error.as_deref(),
        );
        return Err((report, message));
    }

    Ok(report)
}

fn device_action(
    action: DeviceAction,
    address: &str,
    backend: &mut impl Backend,
    waiter: &mut impl Waiter,
) -> Result<(), String> {
    if matches!(
        action,
        DeviceAction::Pair | DeviceAction::Connect | DeviceAction::Forget
    ) && !is_on(backend, PROPERTY_TIMEOUT)?
    {
        power_on(backend, waiter)?;
    }

    match action {
        DeviceAction::Pair => pair_device(address, backend),
        DeviceAction::Connect => connect_device(address, backend),
        DeviceAction::Disconnect => disconnect_device(address, backend),
        DeviceAction::Forget => forget_device(address, backend),
    }
}

fn pair_device(address: &str, backend: &mut impl Backend) -> Result<(), String> {
    let pair_error = backend.pair(address, DEVICE_OPERATION_WAIT).err();
    let trust_error = backend.trust(address, PROPERTY_TIMEOUT).err();
    let connect_error = backend.connect(address, DEVICE_OPERATION_WAIT).err();
    let device = backend.device(address, PROPERTY_TIMEOUT).map_err(|error| {
        let mut message = format!("could not read back device {address}: {error}");
        append_reason(&mut message, "pair request failed", pair_error.as_deref());
        append_reason(&mut message, "trust request failed", trust_error.as_deref());
        append_reason(
            &mut message,
            "connect request failed",
            connect_error.as_deref(),
        );
        message
    })?;

    let mut unmet = Vec::new();
    let paired = device.as_ref().is_some_and(|device| device.paired);
    let trusted = device.as_ref().is_some_and(|device| device.trusted);
    let connected = device.as_ref().is_some_and(|device| device.connected);
    if !paired {
        unmet.push(state_failure("paired", pair_error.as_deref()));
    }
    if !trusted {
        unmet.push(state_failure("trusted", trust_error.as_deref()));
    }
    if !connected {
        unmet.push(state_failure("connected", connect_error.as_deref()));
    }
    if unmet.is_empty() {
        Ok(())
    } else {
        Err(format!("device {address} is not {}", unmet.join("; not ")))
    }
}

fn connect_device(address: &str, backend: &mut impl Backend) -> Result<(), String> {
    let trust_error = backend.trust(address, PROPERTY_TIMEOUT).err();
    let connect_error = backend.connect(address, DEVICE_OPERATION_WAIT).err();
    let device = backend.device(address, PROPERTY_TIMEOUT).map_err(|error| {
        let mut message = format!("could not read back device {address}: {error}");
        append_reason(&mut message, "trust request failed", trust_error.as_deref());
        append_reason(
            &mut message,
            "connect request failed",
            connect_error.as_deref(),
        );
        message
    })?;

    let trusted = device.as_ref().is_some_and(|device| device.trusted);
    let connected = device.as_ref().is_some_and(|device| device.connected);
    let mut unmet = Vec::new();
    if !trusted {
        unmet.push(state_failure("trusted", trust_error.as_deref()));
    }
    if !connected {
        unmet.push(state_failure("connected", connect_error.as_deref()));
    }
    if unmet.is_empty() {
        Ok(())
    } else {
        Err(format!("device {address} is not {}", unmet.join("; not ")))
    }
}

fn disconnect_device(address: &str, backend: &mut impl Backend) -> Result<(), String> {
    let disconnect_error = backend.disconnect(address, DISCONNECT_WAIT).err();
    let device = backend.device(address, PROPERTY_TIMEOUT).map_err(|error| {
        let mut message = format!("could not read back device {address}: {error}");
        append_reason(
            &mut message,
            "disconnect request failed",
            disconnect_error.as_deref(),
        );
        message
    })?;

    if device.as_ref().is_none_or(|device| !device.connected) {
        Ok(())
    } else {
        let mut message = format!("device {address} remains connected");
        append_reason(
            &mut message,
            "disconnect request failed",
            disconnect_error.as_deref(),
        );
        Err(message)
    }
}

fn forget_device(address: &str, backend: &mut impl Backend) -> Result<(), String> {
    let disconnect_error = backend.disconnect(address, DISCONNECT_WAIT).err();
    let remove_error = backend.remove(address, DISCONNECT_WAIT).err();
    let device = backend.device(address, PROPERTY_TIMEOUT).map_err(|error| {
        let mut message = format!("could not read back device {address}: {error}");
        append_reason(
            &mut message,
            "disconnect request failed",
            disconnect_error.as_deref(),
        );
        append_reason(
            &mut message,
            "remove request failed",
            remove_error.as_deref(),
        );
        message
    })?;

    if device.as_ref().is_none_or(|device| !device.paired) {
        Ok(())
    } else {
        let mut message = format!("device {address} is still known");
        append_reason(
            &mut message,
            "disconnect request failed",
            disconnect_error.as_deref(),
        );
        append_reason(
            &mut message,
            "remove request failed",
            remove_error.as_deref(),
        );
        Err(message)
    }
}

fn state_failure(state: &str, request_error: Option<&str>) -> String {
    match request_error {
        Some(error) => format!("{state} (request failed: {error})"),
        None => state.to_string(),
    }
}

fn append_reason(message: &mut String, label: &str, reason: Option<&str>) {
    if let Some(reason) = reason {
        message.push_str(&format!("; {label}: {reason}"));
    }
}

fn yes_no(value: bool) -> &'static str {
    if value { "yes" } else { "no" }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::VecDeque;

    const ADDRESS: &str = "AA:BB:CC:DD:EE:FF";

    #[derive(Default)]
    struct FakeBackend {
        radios: Vec<Radio>,
        adapters: Vec<Adapter>,
        device: Option<Device>,
        events: Vec<String>,
        errors: VecDeque<(&'static str, String)>,
        adapter_reads: usize,
        radio_reads: usize,
        radio_request_error: Option<String>,
        device_read_error: Option<String>,
    }

    impl FakeBackend {
        fn with_device() -> Self {
            Self {
                radios: vec![Radio {
                    name: "test-radio".to_string(),
                    soft_blocked: false,
                    hard_blocked: false,
                }],
                adapters: vec![Adapter {
                    path: "/org/bluez/hci0".to_string(),
                    powered: true,
                }],
                device: Some(Device {
                    address: ADDRESS.to_string(),
                    paired: true,
                    trusted: true,
                    connected: true,
                }),
                ..Self::default()
            }
        }

        fn take_error(&mut self, operation: &'static str) -> Result<(), String> {
            if self
                .errors
                .front()
                .is_some_and(|(name, _)| *name == operation)
            {
                return Err(self.errors.pop_front().unwrap().1);
            }
            Ok(())
        }
    }

    impl Backend for FakeBackend {
        fn request_radio_block(&mut self, blocked: bool) -> Result<(), String> {
            self.events.push(format!(
                "radio:{}",
                if blocked { "block" } else { "unblock" }
            ));
            if let Some(error) = &self.radio_request_error {
                return Err(error.clone());
            }
            for radio in &mut self.radios {
                radio.soft_blocked = blocked;
            }
            Ok(())
        }

        fn radios(&mut self) -> Result<Vec<Radio>, String> {
            self.radio_reads += 1;
            Ok(self.radios.clone())
        }

        fn adapters(&mut self, _timeout: Duration) -> Result<Vec<Adapter>, String> {
            self.adapter_reads += 1;
            self.events.push("read-adapters".to_string());
            Ok(self.adapters.clone())
        }

        fn set_default_adapter_power(
            &mut self,
            powered: bool,
            timeout: Duration,
        ) -> Result<(), String> {
            self.events.push(format!(
                "power-default:{}:{}",
                if powered { "on" } else { "off" },
                timeout.as_secs()
            ));
            self.take_error("power")?;
            let Some(adapter) = self.adapters.first_mut() else {
                return Err("no default Bluetooth controller".to_string());
            };
            adapter.powered = powered;
            Ok(())
        }

        fn device(&mut self, address: &str, _timeout: Duration) -> Result<Option<Device>, String> {
            if let Some(error) = &self.device_read_error {
                return Err(error.clone());
            }
            Ok(self
                .device
                .as_ref()
                .filter(|device| device.address.eq_ignore_ascii_case(address))
                .cloned())
        }

        fn pair(&mut self, _address: &str, timeout: Duration) -> Result<(), String> {
            self.events.push(format!("pair:{}", timeout.as_secs()));
            self.take_error("pair")?;
            if let Some(device) = &mut self.device {
                device.paired = true;
            }
            Ok(())
        }

        fn trust(&mut self, _address: &str, timeout: Duration) -> Result<(), String> {
            self.events.push(format!("trust:{}", timeout.as_secs()));
            self.take_error("trust")?;
            if let Some(device) = &mut self.device {
                device.trusted = true;
            }
            Ok(())
        }

        fn connect(&mut self, _address: &str, timeout: Duration) -> Result<(), String> {
            self.events.push(format!("connect:{}", timeout.as_secs()));
            self.take_error("connect")?;
            if let Some(device) = &mut self.device {
                device.connected = true;
            }
            Ok(())
        }

        fn disconnect(&mut self, _address: &str, timeout: Duration) -> Result<(), String> {
            self.events
                .push(format!("disconnect:{}", timeout.as_secs()));
            self.take_error("disconnect")?;
            if let Some(device) = &mut self.device {
                device.connected = false;
            }
            Ok(())
        }

        fn remove(&mut self, _address: &str, timeout: Duration) -> Result<(), String> {
            self.events.push(format!("remove:{}", timeout.as_secs()));
            self.take_error("remove")?;
            if let Some(device) = &mut self.device {
                device.paired = false;
                device.trusted = false;
                device.connected = false;
            }
            Ok(())
        }
    }

    #[derive(Default)]
    struct ImmediateWaiter {
        timeouts: Vec<Duration>,
    }

    impl Waiter for ImmediateWaiter {
        fn wait_for<F>(&mut self, timeout: Duration, mut condition: F) -> Result<bool, String>
        where
            F: FnMut(Duration) -> Result<bool, String>,
        {
            self.timeouts.push(timeout);
            condition(timeout)
        }
    }

    fn args(items: &[&str]) -> Vec<OsString> {
        items.iter().map(OsString::from).collect()
    }

    #[test]
    fn invalid_commands_print_usage_without_contacting_the_backend() {
        let mut backend = FakeBackend::default();
        let mut waiter = ImmediateWaiter::default();
        for input in [
            vec![],
            args(&["power"]),
            args(&["power", "unknown"]),
            args(&["device", "pair", "not-an-address"]),
            args(&["device", "scan", ADDRESS]),
            args(&["device", "connect", ADDRESS, "extra"]),
            args(&["unblock", "extra"]),
        ] {
            let output = execute(&input, &mut backend, &mut waiter);
            assert_eq!(output.status, 2);
            assert_eq!(output.stderr, USAGE);
        }
        assert!(backend.events.is_empty());
        assert_eq!(backend.radio_reads, 0);
        assert_eq!(backend.adapter_reads, 0);
        assert!(waiter.timeouts.is_empty());
    }

    #[test]
    fn address_validation_accepts_only_six_hexadecimal_pairs() {
        assert!(valid_address("aA:01:23:45:67:89"));
        assert!(!valid_address("AA:BB:CC:DD:EE:FG"));
        assert!(!valid_address("AA-BB-CC-DD-EE-FF"));
        assert!(!valid_address("A:BB:CC:DD:EE:FF"));
    }

    #[test]
    fn power_off_uses_only_the_radio_block() {
        let mut backend = FakeBackend::with_device();
        let output = execute(
            &args(&["power", "off"]),
            &mut backend,
            &mut ImmediateWaiter::default(),
        );
        assert_eq!(output.status, 0);
        assert_eq!(backend.events, ["radio:block"]);
        assert_eq!(backend.adapters[0].powered, true);
        assert!(backend.radios[0].soft_blocked);
    }

    #[test]
    fn power_off_reports_a_failed_radio_block_without_controller_changes() {
        let mut backend = FakeBackend::with_device();
        backend.radio_request_error = Some("permission denied".to_string());
        let output = execute(
            &args(&["power", "off"]),
            &mut backend,
            &mut ImmediateWaiter::default(),
        );
        assert_eq!(output.status, 1);
        assert!(output.stderr.contains("permission denied"));
        assert_eq!(backend.events, ["radio:block"]);
        assert!(!backend.radios[0].soft_blocked);
        assert!(backend.adapters[0].powered);
    }

    #[test]
    fn power_on_refuses_controller_requests_when_a_radio_stays_blocked() {
        let mut backend = FakeBackend::with_device();
        backend.radios[0].soft_blocked = true;
        backend.adapters[0].powered = false;
        backend.adapters.push(Adapter {
            path: "/org/bluez/hci1".to_string(),
            powered: true,
        });
        backend.radio_request_error = Some("permission denied".to_string());
        let output = execute(
            &args(&["power", "on"]),
            &mut backend,
            &mut ImmediateWaiter::default(),
        );
        assert_eq!(output.status, 1);
        assert!(output.stderr.contains("still soft blocked"));
        assert!(output.stderr.contains("permission denied"));
        assert_eq!(backend.events, ["radio:unblock"]);
        assert_eq!(backend.adapter_reads, 0);
    }

    #[test]
    fn a_failed_unblock_request_continues_when_readback_is_clear() {
        let mut backend = FakeBackend::with_device();
        backend.radio_request_error = Some("rfkill returned an error".to_string());
        let output = execute(
            &args(&["power", "on"]),
            &mut backend,
            &mut ImmediateWaiter::default(),
        );
        assert_eq!(output.status, 0);
        assert_eq!(backend.events, ["radio:unblock", "read-adapters"]);
        assert!(!output.stderr.contains("rfkill returned an error"));
    }

    #[test]
    fn power_on_asks_only_the_default_controller_after_the_first_wait() {
        let mut backend = FakeBackend::with_device();
        backend.adapters[0].powered = false;
        let mut waiter = ImmediateWaiter::default();
        let output = execute(&args(&["power", "on"]), &mut backend, &mut waiter);
        assert_eq!(output.status, 0);
        assert_eq!(
            backend.events,
            [
                "radio:unblock",
                "read-adapters",
                "power-default:on:5",
                "read-adapters"
            ]
        );
        assert_eq!(waiter.timeouts, [RADIO_WAIT, RADIO_WAIT]);
    }

    #[test]
    fn no_adapter_reports_why_after_the_bounded_power_attempt() {
        let mut backend = FakeBackend::default();
        let mut waiter = ImmediateWaiter::default();
        let output = execute(&args(&["power", "on"]), &mut backend, &mut waiter);
        assert_eq!(output.status, 1);
        assert!(output.stderr.contains("adapter did not come up"));
        assert!(output.stderr.contains("no default Bluetooth controller"));
        assert_eq!(waiter.timeouts, [RADIO_WAIT, RADIO_WAIT]);
        assert!(backend.events.contains(&"power-default:on:5".to_string()));
    }

    #[test]
    fn toggle_counts_any_powered_controller_and_turns_off_by_blocking_radios() {
        let mut backend = FakeBackend::with_device();
        backend.adapters.push(Adapter {
            path: "/org/bluez/hci1".to_string(),
            powered: true,
        });
        let output = execute(
            &args(&["power", "toggle"]),
            &mut backend,
            &mut ImmediateWaiter::default(),
        );
        assert_eq!(output.status, 0);
        assert_eq!(backend.events, ["read-adapters", "radio:block"]);
        assert_eq!(backend.adapters[1].powered, true);
    }

    #[test]
    fn toggle_runs_power_on_when_every_controller_is_off() {
        let mut backend = FakeBackend::with_device();
        backend.adapters[0].powered = false;
        let output = execute(
            &args(&["power", "toggle"]),
            &mut backend,
            &mut ImmediateWaiter::default(),
        );
        assert_eq!(output.status, 0);
        assert_eq!(
            backend.events,
            [
                "read-adapters",
                "radio:unblock",
                "read-adapters",
                "power-default:on:5",
                "read-adapters"
            ]
        );
    }

    #[test]
    fn is_on_is_silent_and_uses_all_adapters() {
        let mut backend = FakeBackend::with_device();
        backend.adapters[0].powered = false;
        backend.adapters.push(Adapter {
            path: "/org/bluez/hci1".to_string(),
            powered: true,
        });
        let output = execute(
            &args(&["power", "is-on"]),
            &mut backend,
            &mut ImmediateWaiter::default(),
        );
        assert_eq!(output, Output::success(String::new()));
    }

    #[test]
    fn is_on_when_off_is_nonzero_and_silent() {
        let mut backend = FakeBackend::default();
        let output = execute(
            &args(&["power", "is-on"]),
            &mut backend,
            &mut ImmediateWaiter::default(),
        );
        assert_eq!(output.status, 1);
        assert!(output.stdout.is_empty());
        assert!(output.stderr.is_empty());
        assert_eq!(backend.events, ["read-adapters"]);
    }

    #[test]
    fn device_actions_power_on_first_except_disconnect() {
        let mut backend = FakeBackend::with_device();
        backend.adapters[0].powered = false;
        let output = execute(
            &args(&["device", "connect", ADDRESS]),
            &mut backend,
            &mut ImmediateWaiter::default(),
        );
        assert_eq!(output.status, 0);
        assert_eq!(&backend.events[..2], ["read-adapters", "radio:unblock"]);
        assert!(
            backend
                .events
                .iter()
                .position(|event| event == "pair:20")
                .is_none()
        );
        assert!(backend.events.contains(&"trust:2".to_string()));
        assert!(backend.events.contains(&"connect:20".to_string()));

        let mut backend = FakeBackend::with_device();
        backend.adapters[0].powered = false;
        let output = execute(
            &args(&["device", "disconnect", ADDRESS]),
            &mut backend,
            &mut ImmediateWaiter::default(),
        );
        assert_eq!(output.status, 0);
        assert_eq!(backend.events, ["disconnect:10"]);
    }

    #[test]
    fn pair_runs_all_requests_and_success_comes_from_final_readback() {
        let mut backend = FakeBackend::with_device();
        backend
            .errors
            .push_back(("pair", "already paired".to_string()));
        let output = execute(
            &args(&["device", "pair", ADDRESS]),
            &mut backend,
            &mut ImmediateWaiter::default(),
        );
        assert_eq!(output.status, 0);
        assert_eq!(
            backend.events,
            ["read-adapters", "pair:20", "trust:2", "connect:20"]
        );
    }

    #[test]
    fn pair_reports_each_unmet_state_and_its_failed_request_reason() {
        let mut backend = FakeBackend::with_device();
        backend.device.as_mut().unwrap().paired = false;
        backend.device.as_mut().unwrap().trusted = false;
        backend.device.as_mut().unwrap().connected = false;
        backend.errors.extend([
            ("pair", "pair denied".to_string()),
            ("trust", "trust denied".to_string()),
            ("connect", "connect denied".to_string()),
        ]);
        let output = execute(
            &args(&["device", "pair", ADDRESS]),
            &mut backend,
            &mut ImmediateWaiter::default(),
        );
        assert_eq!(output.status, 1);
        assert!(
            output
                .stderr
                .contains("not paired (request failed: pair denied)")
        );
        assert!(
            output
                .stderr
                .contains("not trusted (request failed: trust denied)")
        );
        assert!(
            output
                .stderr
                .contains("not connected (request failed: connect denied)")
        );
        assert_eq!(
            backend.events,
            ["read-adapters", "pair:20", "trust:2", "connect:20"]
        );
    }

    #[test]
    fn pair_preserves_request_errors_when_readback_fails() {
        let mut backend = FakeBackend::with_device();
        backend.device_read_error = Some("service unavailable".to_string());
        backend
            .errors
            .push_back(("pair", "pair denied".to_string()));
        let output = execute(
            &args(&["device", "pair", ADDRESS]),
            &mut backend,
            &mut ImmediateWaiter::default(),
        );
        assert_eq!(output.status, 1);
        assert!(output.stderr.contains("could not read back device"));
        assert!(output.stderr.contains("pair request failed: pair denied"));
        assert_eq!(
            backend.events,
            ["read-adapters", "pair:20", "trust:2", "connect:20"]
        );
    }

    #[test]
    fn connect_trust_failure_does_not_skip_connection_and_reports_readback() {
        let mut backend = FakeBackend::with_device();
        backend.device.as_mut().unwrap().trusted = false;
        backend
            .errors
            .push_back(("trust", "access denied".to_string()));
        let output = execute(
            &args(&["device", "connect", ADDRESS]),
            &mut backend,
            &mut ImmediateWaiter::default(),
        );
        assert_eq!(output.status, 1);
        assert!(
            output
                .stderr
                .contains("not trusted (request failed: access denied)")
        );
        assert_eq!(
            backend.events,
            ["read-adapters", "trust:2", "connect:20"]
        );
    }

    #[test]
    fn disconnect_uses_final_state_and_forget_removes_after_a_failed_disconnect() {
        let mut backend = FakeBackend::with_device();
        backend
            .errors
            .push_back(("disconnect", "not connected".to_string()));
        backend.device.as_mut().unwrap().connected = false;
        let output = execute(
            &args(&["device", "disconnect", ADDRESS]),
            &mut backend,
            &mut ImmediateWaiter::default(),
        );
        assert_eq!(output.status, 0);

        let mut backend = FakeBackend::with_device();
        backend
            .errors
            .push_back(("disconnect", "transport failed".to_string()));
        let output = execute(
            &args(&["device", "forget", ADDRESS]),
            &mut backend,
            &mut ImmediateWaiter::default(),
        );
        assert_eq!(output.status, 0);
        assert_eq!(
            backend.events,
            ["read-adapters", "disconnect:10", "remove:10"]
        );
        assert!(!backend.device.as_ref().unwrap().paired);
    }

    #[test]
    fn disconnect_reports_a_device_that_remains_connected() {
        let mut backend = FakeBackend::with_device();
        backend
            .errors
            .push_back(("disconnect", "transport failed".to_string()));
        let output = execute(
            &args(&["device", "disconnect", ADDRESS]),
            &mut backend,
            &mut ImmediateWaiter::default(),
        );
        assert_eq!(output.status, 1);
        assert!(output.stderr.contains("remains connected"));
        assert!(output.stderr.contains("transport failed"));
        assert_eq!(backend.events, ["disconnect:10"]);
    }

    #[test]
    fn forget_fails_when_the_pairing_record_remains() {
        let mut backend = FakeBackend::with_device();
        backend
            .errors
            .push_back(("remove", "permission denied".to_string()));
        let output = execute(
            &args(&["device", "forget", ADDRESS]),
            &mut backend,
            &mut ImmediateWaiter::default(),
        );
        assert_eq!(output.status, 1);
        assert!(output.stderr.contains("still known"));
        assert!(output.stderr.contains("permission denied"));
    }

    #[test]
    fn unblock_reports_every_radio_and_requires_a_clear_readback() {
        let mut backend = FakeBackend::with_device();
        backend.radios.push(Radio {
            name: "second-radio".to_string(),
            soft_blocked: false,
            hard_blocked: true,
        });
        let output = execute(
            &args(&["unblock"]),
            &mut backend,
            &mut ImmediateWaiter::default(),
        );
        assert_eq!(output.status, 1);
        assert!(
            output
                .stdout
                .contains("test-radio: soft blocked: no; hard blocked: no")
        );
        assert!(
            output
                .stdout
                .contains("second-radio: soft blocked: no; hard blocked: yes")
        );
        assert!(output.stderr.contains("remain blocked"));
    }

    #[test]
    fn unblock_succeeds_when_failed_request_reads_back_clear() {
        let mut backend = FakeBackend::with_device();
        backend.radio_request_error = Some("rfkill failed after state changed".to_string());
        let output = execute(
            &args(&["unblock"]),
            &mut backend,
            &mut ImmediateWaiter::default(),
        );
        assert_eq!(output.status, 0);
        assert!(!output.stdout.is_empty());
        assert!(output.stderr.is_empty());
    }

    #[test]
    fn unblock_fails_when_no_radios_exist() {
        let mut backend = FakeBackend::default();
        let output = execute(
            &args(&["unblock"]),
            &mut backend,
            &mut ImmediateWaiter::default(),
        );
        assert_eq!(output.status, 1);
        assert!(output.stderr.contains("no Bluetooth radios were found"));
    }
}
