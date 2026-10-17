//! The single session watcher’s owner for wlr-gamma-control handles.
//!
//! This module deliberately does not persist policy or choose commands. The
//! Night Light policy in `scottland::night_light` supplies the ordered actions;
//! this adapter reports only protocol facts it observed.

use std::collections::{BTreeMap, HashMap};
use std::ffi::CString;
use std::fs::File;
use std::io::{self, Write};
use std::os::fd::{AsFd, AsRawFd, FromRawFd, OwnedFd};
use std::os::unix::net::UnixStream;
use std::path::Path;
use std::sync::{Arc, Mutex};

use scottland::night_light::{Action, Outcome, OutputInput, OutputOutcome, OutputOwnership};
use scottland::session::Session;
use wayland_client::protocol::{wl_output, wl_registry};
use wayland_client::{Connection, Dispatch, EventQueue, Proxy, QueueHandle};
use wayland_protocols_wlr::gamma_control::v1::client::{
    zwlr_gamma_control_manager_v1, zwlr_gamma_control_v1,
};

/// The only color temperature that the bound Night Light policy may request.
pub const WARM_KELVIN: u32 = 4000;

const MAX_GAMMA_RAMP_SIZE: u32 = 65_536;

#[derive(Clone, Debug, Default)]
struct ControlFacts {
    size: Option<u32>,
    failed: bool,
}

struct OutputRecord {
    global: u32,
    proxy: wl_output::WlOutput,
    name: Arc<Mutex<Option<String>>>,
}

struct ControlRecord {
    global: u32,
    proxy: zwlr_gamma_control_v1::ZwlrGammaControlV1,
    facts: Arc<Mutex<ControlFacts>>,
    warm: bool,
}

#[derive(Default)]
struct State {
    manager_global: Option<u32>,
    manager: Option<zwlr_gamma_control_manager_v1::ZwlrGammaControlManagerV1>,
    outputs: BTreeMap<u32, OutputRecord>,
    controls: HashMap<String, ControlRecord>,
}

impl Dispatch<wl_registry::WlRegistry, ()> for State {
    fn event(
        state: &mut Self,
        registry: &wl_registry::WlRegistry,
        event: wl_registry::Event,
        _: &(),
        _: &Connection,
        qh: &QueueHandle<Self>,
    ) {
        match event {
            wl_registry::Event::Global {
                name,
                interface,
                version,
            } if interface == "wl_output" => {
                let output = registry.bind::<wl_output::WlOutput, _, _>(
                    name,
                    version.clamp(1, 4),
                    qh,
                    (name, Arc::new(Mutex::new(None))),
                );
                let identity = output
                    .data::<(u32, Arc<Mutex<Option<String>>>)>()
                    .expect("wl_output user data is installed")
                    .1
                    .clone();
                state.outputs.insert(
                    name,
                    OutputRecord {
                        global: name,
                        proxy: output,
                        name: identity,
                    },
                );
            }
            wl_registry::Event::Global {
                name,
                interface,
                version: _,
            } if interface == "zwlr_gamma_control_manager_v1" => {
                if state.manager.is_none() {
                    state.manager_global = Some(name);
                    state.manager = Some(
                        registry
                            .bind::<zwlr_gamma_control_manager_v1::ZwlrGammaControlManagerV1, _, _>(
                                name,
                                1,
                                qh,
                                (),
                            ),
                    );
                }
            }
            wl_registry::Event::GlobalRemove { name } => {
                if state.manager_global == Some(name) {
                    state.manager_global = None;
                    if let Some(manager) = state.manager.take() {
                        manager.destroy();
                    }
                    for (_, control) in state.controls.drain() {
                        control.proxy.destroy();
                    }
                }
                if state.outputs.remove(&name).is_some() {
                    let disconnected: Vec<String> = state
                        .controls
                        .iter()
                        .filter(|(_, control)| control.global == name)
                        .map(|(identity, _)| identity.clone())
                        .collect();
                    for identity in disconnected {
                        if let Some(control) = state.controls.remove(&identity) {
                            control.proxy.destroy();
                        }
                    }
                }
            }
            _ => {}
        }
    }
}

impl Dispatch<wl_output::WlOutput, (u32, Arc<Mutex<Option<String>>>)> for State {
    fn event(
        _: &mut Self,
        _: &wl_output::WlOutput,
        event: wl_output::Event,
        data: &(u32, Arc<Mutex<Option<String>>>),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if let wl_output::Event::Name { name } = event
            && let Ok(mut target) = data.1.lock()
        {
            *target = Some(name);
        }
    }
}

impl Dispatch<zwlr_gamma_control_manager_v1::ZwlrGammaControlManagerV1, ()> for State {
    fn event(
        _: &mut Self,
        _: &zwlr_gamma_control_manager_v1::ZwlrGammaControlManagerV1,
        _: zwlr_gamma_control_manager_v1::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
    }
}

impl Dispatch<zwlr_gamma_control_v1::ZwlrGammaControlV1, Arc<Mutex<ControlFacts>>> for State {
    fn event(
        _: &mut Self,
        _: &zwlr_gamma_control_v1::ZwlrGammaControlV1,
        event: zwlr_gamma_control_v1::Event,
        facts: &Arc<Mutex<ControlFacts>>,
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if let Ok(mut facts) = facts.lock() {
            match event {
                zwlr_gamma_control_v1::Event::GammaSize { size } => facts.size = Some(size),
                zwlr_gamma_control_v1::Event::Failed => facts.failed = true,
                _ => {}
            }
        }
    }
}

/// A single Wayland connection and the gamma controls owned by its watcher.
/// Keep this object in the existing per-session color-scheme watcher; CLI
/// processes communicate with that watcher and never create gamma controls.
pub struct GammaController {
    connection: Connection,
    queue: EventQueue<State>,
    state: State,
    _registry: wl_registry::WlRegistry,
}

impl GammaController {
    /// Connect using only the environment captured for the selected session.
    pub fn connect_session(session: &Session) -> Result<Self, String> {
        let display = session
            .var("WAYLAND_DISPLAY")
            .and_then(|value| value.to_str())
            .ok_or_else(|| "selected session has no usable WAYLAND_DISPLAY".to_owned())?;
        let runtime = session
            .var("XDG_RUNTIME_DIR")
            .filter(|value| !value.is_empty())
            .map(Path::new)
            .map(Path::to_path_buf)
            .unwrap_or_else(|| {
                std::path::PathBuf::from(format!("/run/user/{}", scottland::dirs::uid()))
            });
        Self::connect(display, &runtime)
    }

    /// Connect to the Wayland socket recorded for this selected session.
    pub fn connect(display: &str, runtime_dir: &std::path::Path) -> Result<Self, String> {
        if display.is_empty() || display == "." || display == ".." {
            return Err("invalid session Wayland display name".to_owned());
        }
        let display_path = Path::new(display);
        let socket = if display_path.is_absolute() {
            display_path.to_path_buf()
        } else {
            if display.contains('/') {
                return Err("invalid relative Wayland display name".to_owned());
            }
            runtime_dir.join(display_path)
        };
        let stream = UnixStream::connect(&socket)
            .map_err(|error| format!("could not connect to the selected session: {error}"))?;
        let connection = Connection::from_socket(stream).map_err(|error| {
            format!("could not initialize the selected Wayland session: {error}")
        })?;
        let mut queue = connection.new_event_queue();
        let qh = queue.handle();
        let registry = connection.display().get_registry(&qh, ());
        let mut state = State::default();
        queue
            .roundtrip(&mut state)
            .map_err(|error| format!("could not read the Wayland output registry: {error}"))?;
        queue
            .roundtrip(&mut state)
            .map_err(|error| format!("could not read Wayland output identities: {error}"))?;
        Ok(Self {
            connection,
            queue,
            state,
            _registry: registry,
        })
    }

    /// Drain protocol events while the owning watcher is idle.
    pub fn dispatch_pending(&mut self) -> Result<usize, String> {
        self.queue
            .dispatch_pending(&mut self.state)
            .map_err(|error| format!("Wayland gamma event dispatch failed: {error}"))
    }

    /// File descriptor for the watcher event loop to poll with its private
    /// request socket. A readable fd should be followed by `dispatch_blocking`.
    pub fn poll_fd(&self) -> i32 {
        self.connection.as_fd().as_raw_fd()
    }

    /// Read and dispatch one ready Wayland event batch.
    pub fn dispatch_blocking(&mut self) -> Result<usize, String> {
        self.queue
            .blocking_dispatch(&mut self.state)
            .map_err(|error| format!("Wayland gamma event dispatch failed: {error}"))
    }

    /// Convert actual current protocol ownership into policy input.
    pub fn outputs(&mut self) -> Vec<OutputInput> {
        let dispatch_error = self
            .queue
            .roundtrip(&mut self.state)
            .err()
            .map(|error| format!("Wayland output snapshot failed: {error}"));
        let failed: Vec<String> = self
            .state
            .controls
            .iter()
            .filter(|(_, control)| control.facts.lock().ok().is_some_and(|facts| facts.failed))
            .map(|(identity, _)| identity.clone())
            .collect();
        for identity in failed {
            self.discard_control(&identity);
        }
        let mut named = self.named_outputs();
        let current_names: HashMap<u32, String> = named
            .iter()
            .map(|(identity, global)| (*global, identity.clone()))
            .collect();
        let renamed: Vec<String> = self
            .state
            .controls
            .iter()
            .filter(|(identity, control)| {
                current_names.get(&control.global).map(String::as_str) != Some(identity.as_str())
            })
            .map(|(identity, _)| identity.clone())
            .collect();
        for identity in renamed {
            self.discard_control(&identity);
        }
        let unnamed_count = self
            .state
            .outputs
            .values()
            .filter(|output| output.name.lock().ok().is_none_or(|name| name.is_none()))
            .count();
        if unnamed_count > 0 {
            named.push(("unnamed output identity".to_owned(), u32::MAX));
        }
        let mut counts = HashMap::<String, usize>::new();
        for (identity, _) in &named {
            *counts.entry(identity.clone()).or_default() += 1;
        }
        named
            .into_iter()
            .map(|(identity, global)| {
                let ownership = match self.state.controls.get(&identity) {
                    Some(control) => {
                        let facts = control.facts.lock().ok().map(|facts| facts.clone());
                        match facts {
                            Some(facts) if facts.failed => (
                                OutputOwnership::Unowned,
                                Some("compositor revoked gamma control".to_owned()),
                            ),
                            Some(facts) if facts.size.is_some_and(|size| size > 0) => (
                                if control.warm {
                                    OutputOwnership::WarmOwned
                                } else {
                                    OutputOwnership::OwnedUnwarmed
                                },
                                None,
                            ),
                            _ => (
                                OutputOwnership::Unknown,
                                Some("compositor has not supplied a gamma ramp size".to_owned()),
                            ),
                        }
                    }
                    None if counts.get(&identity).copied().unwrap_or(0) > 1 => (
                        OutputOwnership::Unknown,
                        Some("output name is not unique in the current session".to_owned()),
                    ),
                    None if self.state.manager.is_none() => (
                        OutputOwnership::Unowned,
                        Some("compositor does not expose wlr-gamma-control".to_owned()),
                    ),
                    None => (OutputOwnership::Unowned, None),
                };
                if global == u32::MAX {
                    return OutputInput {
                        identity,
                        ownership: OutputOwnership::Unknown,
                        reason: Some(format!(
                            "{} connected output(s) have no stable Wayland name",
                            unnamed_count
                        )),
                    };
                }
                let still_connected = self
                    .state
                    .outputs
                    .get(&global)
                    .is_some_and(|output| output.global == global);
                if !still_connected {
                    return OutputInput {
                        identity,
                        ownership: OutputOwnership::Unknown,
                        reason: Some("output was removed during the snapshot".to_owned()),
                    };
                }
                OutputInput {
                    identity,
                    ownership: if dispatch_error.is_some() {
                        OutputOwnership::Unknown
                    } else {
                        ownership.0
                    },
                    reason: dispatch_error.clone().or(ownership.1),
                }
            })
            .collect()
    }

    /// Apply policy actions in order and return only protocol-derived facts.
    pub fn apply(&mut self, actions: &[Action]) -> Vec<OutputOutcome> {
        actions
            .iter()
            .map(|action| self.apply_one(action))
            .collect()
    }

    fn apply_one(&mut self, action: &Action) -> OutputOutcome {
        let identity = match action {
            Action::AcquireAndApply4000K { output }
            | Action::Apply4000K { output }
            | Action::RestoreOwned { output }
            | Action::ReleaseUnwarmed { output } => output.clone(),
        };
        let no_attempt = OutputOutcome {
            identity: identity.clone(),
            acquisition: Outcome::NotAttempted,
            application: Outcome::NotAttempted,
            restoration: Outcome::NotAttempted,
        };
        match action {
            Action::AcquireAndApply4000K { .. } => self.acquire_and_apply(&identity),
            Action::Apply4000K { .. } => self.apply_owned(&identity, no_attempt),
            Action::RestoreOwned { .. } => self.restore_owned(&identity, no_attempt),
            Action::ReleaseUnwarmed { .. } => self.release_unwarmed(&identity, no_attempt),
        }
    }

    fn acquire_and_apply(&mut self, identity: &str) -> OutputOutcome {
        let Some(global) = self
            .named_outputs()
            .into_iter()
            .find_map(|(name, global)| (name == identity).then_some(global))
        else {
            return failed_outcome(identity, "output was removed before gamma acquisition");
        };
        let Some(output) = self.state.outputs.get(&global) else {
            return failed_outcome(identity, "output was removed before gamma acquisition");
        };
        let Some(manager) = self.state.manager.as_ref() else {
            return failed_outcome(identity, "compositor does not expose wlr-gamma-control");
        };
        if self.state.controls.contains_key(identity) {
            return self.apply_owned(identity, empty_outcome(identity));
        }
        let facts = Arc::new(Mutex::new(ControlFacts::default()));
        let control = manager.get_gamma_control(&output.proxy, &self.queue.handle(), facts.clone());
        self.state.controls.insert(
            identity.to_owned(),
            ControlRecord {
                global,
                proxy: control,
                facts: facts.clone(),
                warm: false,
            },
        );
        if let Err(error) = self.queue.roundtrip(&mut self.state) {
            self.discard_control(identity);
            return failed_outcome(identity, &format!("gamma acquisition failed: {error}"));
        }
        let size = facts.lock().ok().and_then(|facts| facts.size);
        let failed = facts.lock().ok().is_some_and(|facts| facts.failed);
        if failed {
            self.discard_control(identity);
            return failed_outcome(identity, "compositor refused exclusive gamma control");
        }
        let Some(size) = size.filter(|size| *size > 0 && *size <= MAX_GAMMA_RAMP_SIZE) else {
            self.discard_control(identity);
            return failed_outcome(identity, "compositor supplied an invalid gamma ramp size");
        };
        let mut result = self.apply_table(identity, size);
        result.acquisition = Outcome::Succeeded;
        result
    }

    fn apply_owned(&mut self, identity: &str, mut result: OutputOutcome) -> OutputOutcome {
        let facts = self
            .state
            .controls
            .get(identity)
            .map(|control| control.facts.clone());
        let Some(facts) = facts else {
            result.application =
                Outcome::Unknown("Night Light no longer owns gamma control".to_owned());
            return result;
        };
        let state = facts.lock().ok().map(|facts| facts.clone());
        let Some(size) = state.and_then(|state| (!state.failed).then_some(state.size).flatten())
        else {
            result.application = Outcome::Unknown("gamma control has no usable ramp".to_owned());
            return result;
        };
        let application = self.apply_table(identity, size);
        result.application = application.application;
        result
    }

    fn apply_table(&mut self, identity: &str, size: u32) -> OutputOutcome {
        let fd = match gamma_memfd(size, WARM_KELVIN) {
            Ok(fd) => fd,
            Err(error) => {
                return failed_outcome(identity, &format!("could not create gamma ramp: {error}"));
            }
        };
        let Some(control) = self.state.controls.get(identity) else {
            return OutputOutcome {
                identity: identity.to_owned(),
                acquisition: Outcome::NotAttempted,
                application: Outcome::Unknown(
                    "Night Light no longer owns gamma control".to_owned(),
                ),
                restoration: Outcome::NotAttempted,
            };
        };
        control.proxy.set_gamma(fd.as_fd());
        if let Err(error) = self.queue.roundtrip(&mut self.state) {
            return OutputOutcome {
                identity: identity.to_owned(),
                acquisition: Outcome::NotAttempted,
                application: Outcome::Failed(format!("gamma application failed: {error}")),
                restoration: Outcome::NotAttempted,
            };
        }
        let failed = self
            .state
            .controls
            .get(identity)
            .and_then(|control| control.facts.lock().ok().map(|facts| facts.failed))
            .unwrap_or(true);
        if failed {
            self.discard_control(identity);
            return OutputOutcome {
                identity: identity.to_owned(),
                acquisition: Outcome::NotAttempted,
                application: Outcome::Failed(
                    "compositor rejected the 4000K gamma table".to_owned(),
                ),
                restoration: Outcome::NotAttempted,
            };
        }
        if let Some(control) = self.state.controls.get_mut(identity) {
            control.warm = true;
        }
        OutputOutcome {
            identity: identity.to_owned(),
            acquisition: Outcome::NotAttempted,
            application: Outcome::Succeeded,
            restoration: Outcome::NotAttempted,
        }
    }

    fn restore_owned(&mut self, identity: &str, mut result: OutputOutcome) -> OutputOutcome {
        let Some(control) = self.state.controls.remove(identity) else {
            result.restoration =
                Outcome::Unknown("Night Light no longer owns gamma control".to_owned());
            return result;
        };
        let warm = control.warm;
        control.proxy.destroy();
        if let Err(error) = self.queue.roundtrip(&mut self.state) {
            result.restoration =
                Outcome::Unknown(format!("gamma release was not confirmed: {error}"));
        } else if warm {
            // wlr-gamma-control restores the compositor's original ramp on destroy.
            result.restoration = Outcome::Succeeded;
        } else {
            result.restoration =
                Outcome::Unknown("gamma was not confirmed warm before release".to_owned());
        }
        result
    }

    fn release_unwarmed(&mut self, identity: &str, mut result: OutputOutcome) -> OutputOutcome {
        if let Some(control) = self.state.controls.remove(identity) {
            control.proxy.destroy();
            result.restoration = if self.queue.roundtrip(&mut self.state).is_ok() {
                Outcome::Succeeded
            } else {
                Outcome::Unknown("unwarmed gamma release was not confirmed".to_owned())
            };
        } else {
            result.restoration =
                Outcome::Unknown("unwarmed gamma handle was already absent".to_owned());
        }
        result
    }

    fn discard_control(&mut self, identity: &str) {
        if let Some(control) = self.state.controls.remove(identity) {
            control.proxy.destroy();
            let _ = self.queue.roundtrip(&mut self.state);
        }
    }

    fn named_outputs(&self) -> Vec<(String, u32)> {
        self.state
            .outputs
            .iter()
            .filter_map(|(global, output)| {
                output
                    .name
                    .lock()
                    .ok()
                    .and_then(|name| name.clone())
                    .map(|name| (name, *global))
            })
            .collect()
    }
}

impl Drop for GammaController {
    fn drop(&mut self) {
        for (_, control) in std::mem::take(&mut self.state.controls) {
            control.proxy.destroy();
        }
        if let Some(manager) = self.state.manager.take() {
            manager.destroy();
        }
        let _ = self.queue.roundtrip(&mut self.state);
    }
}

fn empty_outcome(identity: &str) -> OutputOutcome {
    OutputOutcome {
        identity: identity.to_owned(),
        acquisition: Outcome::NotAttempted,
        application: Outcome::NotAttempted,
        restoration: Outcome::NotAttempted,
    }
}

fn failed_outcome(identity: &str, reason: &str) -> OutputOutcome {
    OutputOutcome {
        identity: identity.to_owned(),
        acquisition: Outcome::Failed(reason.to_owned()),
        application: Outcome::NotAttempted,
        restoration: Outcome::NotAttempted,
    }
}

/// Build a finite, monotone gamma ramp for the fixed 4000K contract.
fn gamma_memfd(size: u32, kelvin: u32) -> io::Result<OwnedFd> {
    if size == 0 || size > MAX_GAMMA_RAMP_SIZE || kelvin != WARM_KELVIN {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "unsupported gamma ramp request",
        ));
    }
    let name = CString::new("scottland-night-light").expect("static memfd name has no NUL");
    // memfd_create gives the compositor a private seekable file without leaving a
    // temporary gamma table in the user's runtime or state directory.
    let raw = unsafe { memfd_create(name.as_ptr(), 1) };
    if raw < 0 {
        return Err(io::Error::last_os_error());
    }
    let fd = unsafe { OwnedFd::from_raw_fd(raw) };
    let bytes = usize::try_from(size)
        .ok()
        .and_then(|size| size.checked_mul(3 * std::mem::size_of::<u16>()))
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "gamma ramp size overflow"))?;
    let file = File::from(fd);
    file.set_len(bytes as u64)?;
    let gains = gains_for_4000k();
    let mut data = Vec::with_capacity(bytes);
    for channel_gain in gains {
        for index in 0..size {
            let linear = if size == 1 {
                0.0
            } else {
                f64::from(index) / f64::from(size - 1)
            };
            let value = (linear * channel_gain * f64::from(u16::MAX))
                .round()
                .clamp(0.0, f64::from(u16::MAX)) as u16;
            data.extend_from_slice(&value.to_ne_bytes());
        }
    }
    (&file).write_all(&data)?;
    file.sync_all()?;
    Ok(file.into())
}

fn gains_for_4000k() -> [f64; 3] {
    // Tanner Helland's black-body RGB approximation at 4000K, converted from
    // sRGB to linear-light gains. The policy fixes temperature, so this is a
    // stable transform rather than a user-adjustable color-temperature model.
    let temperature = f64::from(WARM_KELVIN) / 100.0;
    let green = (99.470_802_586_1 * temperature.ln() - 161.119_568_166_1).clamp(0.0, 255.0) / 255.0;
    let blue = (138.517_731_223_1 * (temperature - 10.0).ln() - 305.044_792_730_7)
        .clamp(0.0, 255.0)
        / 255.0;
    [1.0, srgb_to_linear(green), srgb_to_linear(blue)]
}

fn srgb_to_linear(value: f64) -> f64 {
    if value <= 0.04045 {
        value / 12.92
    } else {
        ((value + 0.055) / 1.055).powf(2.4)
    }
}

unsafe extern "C" {
    fn memfd_create(name: *const std::ffi::c_char, flags: u32) -> i32;
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Seek, SeekFrom};

    #[test]
    fn fixed_temperature_curve_is_finite_monotone_and_red_biased() {
        let gains = gains_for_4000k();
        assert_eq!(gains[0], 1.0);
        assert!(
            gains
                .iter()
                .all(|gain| gain.is_finite() && (0.0..=1.0).contains(gain))
        );
        assert!(gains[0] > gains[1] && gains[1] > gains[2]);

        let fd = gamma_memfd(5, WARM_KELVIN).unwrap();
        let mut file = File::from(fd);
        let mut bytes = Vec::new();
        file.seek(SeekFrom::Start(0)).unwrap();
        file.read_to_end(&mut bytes).unwrap();
        assert_eq!(bytes.len(), 5 * 3 * 2);
        let channels: Vec<Vec<u16>> = bytes
            .chunks_exact(10)
            .map(|channel| {
                channel
                    .chunks_exact(2)
                    .map(|value| u16::from_ne_bytes([value[0], value[1]]))
                    .collect()
            })
            .collect();
        assert_eq!(channels.len(), 3);
        for channel in &channels {
            assert_eq!(channel.first(), Some(&0));
            assert!(channel.windows(2).all(|pair| pair[0] <= pair[1]));
        }
        assert_eq!(channels[0].last(), Some(&u16::MAX));
        assert!(channels[1].last().unwrap() < channels[0].last().unwrap());
        assert!(channels[2].last().unwrap() < channels[1].last().unwrap());
        assert!(channels[0][3] > channels[1][3] && channels[1][3] > channels[2][3]);
    }

    #[test]
    fn gamma_ramp_rejects_invalid_size_and_other_temperature() {
        assert!(gamma_memfd(0, WARM_KELVIN).is_err());
        assert!(gamma_memfd(MAX_GAMMA_RAMP_SIZE + 1, WARM_KELVIN).is_err());
        assert!(gamma_memfd(16, 4200).is_err());
    }
}
