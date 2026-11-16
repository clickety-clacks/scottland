//! Session-only display mirroring through Wayfire's existing config reload.

use crate::outputs::{self, OutputInfo};
use serde_json::{Value, json};
use std::env;
use std::fs::{self, OpenOptions};
use std::io::{self, Read, Write};
use std::os::unix::fs::OpenOptionsExt;
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::thread;
use std::time::{Duration, Instant};

const MAX_FRAME_BYTES: usize = 64 * 1024;
const RELOAD_WAIT: Duration = Duration::from_secs(10);
static NEXT_TEMP: AtomicU64 = AtomicU64::new(0);

#[derive(Clone, Debug, Eq, PartialEq)]
struct MirrorState {
    source: String,
    target: String,
    width: u32,
    height: u32,
    refresh_millihz: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct MirrorRequest {
    source: String,
    target: String,
}

pub(super) fn run(args: &[&str]) -> Result<(), String> {
    let session = current_session()?;
    match args {
        ["on"] => turn_on(&session, None),
        ["off"] => turn_off(&session),
        ["toggle"] => toggle(&session),
        ["status"] => status(&session),
        _ => Err("usage: scottland-display mirror <on|off|toggle|status>".to_owned()),
    }
}

fn current_session() -> Result<scottland::session::Session, String> {
    let display = env::var("WAYLAND_DISPLAY").ok();
    scottland::session::current(display.as_deref()).map_err(|error| error.to_string())
}

fn toggle(session: &scottland::session::Session) -> Result<(), String> {
    if read_request(session)?.is_some() {
        turn_off(session)
    } else {
        let selected = select_pair(&outputs::list_outputs_for_session(session)?)?;
        turn_on(session, Some(selected))
    }
}

fn turn_on(
    session: &scottland::session::Session,
    selected: Option<(String, String)>,
) -> Result<(), String> {
    let path = marker_path(session)?;
    let existing = read_request_at(&path)?;
    let before_state = query_state(session)?;

    if let Some(request) = existing.as_ref() {
        if is_mirror(
            &mirrors_from(&before_state)?,
            &request.source,
            &request.target,
        ) {
            report_user_mode_override(session, &request.source, &request.target);
            return Ok(());
        }
    }

    let (source, target) = match selected {
        Some(pair) => pair,
        None => select_pair(&outputs::list_outputs_for_session(session)?)?,
    };
    let reloads = reload_count(&before_state)?;
    let request = MirrorRequest { source, target };
    write_request_at(&path, &request)?;

    if let Err(error) = wait_for_reload(session, reloads) {
        return Err(rollback_marker(session, &path, error));
    }

    let applied = query_state(session).and_then(|state| mirrors_from(&state));
    match applied {
        Ok(mirrors) if is_mirror(&mirrors, &request.source, &request.target) => {
            report_user_mode_override(session, &request.source, &request.target);
            Ok(())
        }
        Ok(_) => Err(rollback_marker(
            session,
            &path,
            format!(
                "Wayfire did not report {} mirroring {}; the config reload/mode assumption failed",
                request.target, request.source
            ),
        )),
        Err(error) => Err(rollback_marker(session, &path, error)),
    }
}

fn turn_off(session: &scottland::session::Session) -> Result<(), String> {
    let path = marker_path(session)?;
    let Some(request) = read_request_at(&path)? else {
        return Ok(());
    };
    let before = query_state(session)?;
    let reloads = reload_count(&before)?;
    let configured_mode = configured_output_mode(session, &request.target)?;
    remove_request_at(&path)?;
    if let Err(error) = wait_for_reload(session, reloads) {
        let restore = write_request_at(&path, &request);
        return Err(match restore {
            Ok(()) => error,
            Err(restore_error) => {
                format!("{error}; could not restore the active mirror request: {restore_error}")
            }
        });
    }

    let state = query_state(session)?;
    let mirrors = mirrors_from(&state)?;
    let output_mode = output_mode_from(&state, &request.target)?;
    if !configured_mode_restored(
        configured_mode.as_deref(),
        &request.target,
        &mirrors,
        &output_mode,
    ) {
        return Err(format!(
            "Wayfire did not restore {} to the mode configured for it after mirror off; the config reload/mode assumption failed",
            request.target
        ));
    }
    Ok(())
}

fn status(session: &scottland::session::Session) -> Result<(), String> {
    let mut mirrors = mirrors_from(&query_state(session)?)?;
    mirrors.retain(is_panel_mirror);
    mirrors.sort_by(|a, b| a.target.cmp(&b.target).then(a.source.cmp(&b.source)));
    if mirrors.is_empty() {
        println!("extended");
        return Ok(());
    }

    println!("mirrored");
    let request = match read_request(session) {
        Ok(request) => request,
        Err(error) => {
            eprintln!("could not determine whether mirroring overrides a user mode: {error}");
            None
        }
    };
    for mirror in mirrors {
        let overrides_user_mode = request.as_ref().is_some_and(|request| {
            request.source == mirror.source
                && request.target == mirror.target
                && match configured_mode_overrides_user_mode(
                    session,
                    &mirror.source,
                    &mirror.target,
                ) {
                    Ok(overrides) => overrides,
                    Err(error) => {
                        eprintln!(
                            "could not determine whether mirroring overrides the user mode for {}: {error}",
                            mirror.target
                        );
                        false
                    }
                }
        });
        let mut detail = format!(
            "{}: {}x{}@{}",
            mirror.target,
            mirror.width,
            mirror.height,
            refresh_text(mirror.refresh_millihz)
        );
        if overrides_user_mode {
            detail.push_str(", overriding the mode set in your config");
        }
        println!("{detail}");
    }
    Ok(())
}

fn report_user_mode_override(session: &scottland::session::Session, source: &str, target: &str) {
    match configured_mode_overrides_user_mode(session, source, target) {
        Ok(true) => eprintln!(
            "mirroring overrides the mode set in your config for {target} until mirror off"
        ),
        Ok(false) => {}
        Err(error) => eprintln!(
            "mirroring is active for {target}; could not determine whether a configured mode is overridden: {error}"
        ),
    }
}

fn configured_mode_overrides_user_mode(
    session: &scottland::session::Session,
    source: &str,
    target: &str,
) -> Result<bool, String> {
    Ok(configured_output_mode(session, target)?
        .is_some_and(|mode| mirror_source_from_mode(&mode) != Some(source)))
}

fn select_pair(outputs: &[OutputInfo]) -> Result<(String, String), String> {
    let mut internal = outputs
        .iter()
        .filter(|output| output.enabled && outputs::is_internal(&output.name))
        .map(|output| output.name.as_str())
        .collect::<Vec<_>>();
    internal.sort_unstable();
    let source = internal
        .first()
        .ok_or_else(|| "cannot mirror: no enabled built-in panel is connected".to_owned())?;

    let mut external = outputs
        .iter()
        .filter(|output| output.enabled && !outputs::is_internal(&output.name))
        .map(|output| output.name.as_str())
        .collect::<Vec<_>>();
    external.sort_unstable();
    let target = external
        .first()
        .ok_or_else(|| "cannot mirror: no enabled external output is connected".to_owned())?;
    Ok(((*source).to_owned(), (*target).to_owned()))
}

fn is_mirror(mirrors: &[MirrorState], source: &str, target: &str) -> bool {
    mirrors
        .iter()
        .any(|mirror| mirror.source == source && mirror.target == target)
}

fn is_panel_mirror(mirror: &MirrorState) -> bool {
    outputs::is_internal(&mirror.source) && !outputs::is_internal(&mirror.target)
}

fn mirrors_from(state: &Value) -> Result<Vec<MirrorState>, String> {
    let values = state
        .get("mirrors")
        .and_then(Value::as_array)
        .ok_or_else(|| {
            "Wayfire session state omitted its compositor-reported mirrors".to_owned()
        })?;
    values
        .iter()
        .map(|value| {
            let target = value
                .get("target")
                .and_then(Value::as_str)
                .filter(|name| outputs::valid_name(name))
                .ok_or_else(|| "Wayfire reported an invalid mirror target".to_owned())?;
            let source = value
                .get("source")
                .and_then(Value::as_str)
                .filter(|name| outputs::valid_name(name))
                .ok_or_else(|| "Wayfire reported an invalid mirror source".to_owned())?;
            let width = value
                .get("width")
                .and_then(Value::as_u64)
                .and_then(|value| u32::try_from(value).ok())
                .filter(|value| *value > 0)
                .ok_or_else(|| format!("Wayfire reported an invalid mode width for {target}"))?;
            let height = value
                .get("height")
                .and_then(Value::as_u64)
                .and_then(|value| u32::try_from(value).ok())
                .filter(|value| *value > 0)
                .ok_or_else(|| format!("Wayfire reported an invalid mode height for {target}"))?;
            let refresh_millihz = value
                .get("refresh-millihz")
                .and_then(Value::as_u64)
                .ok_or_else(|| format!("Wayfire reported an invalid refresh rate for {target}"))?;
            Ok(MirrorState {
                source: source.to_owned(),
                target: target.to_owned(),
                width,
                height,
                refresh_millihz,
            })
        })
        .collect()
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct OutputModeState {
    enabled: bool,
    mirrored: bool,
    width: u32,
    height: u32,
    refresh_millihz: u64,
}

fn output_mode_from(state: &Value, target: &str) -> Result<OutputModeState, String> {
    let values = state
        .get("output-modes")
        .and_then(Value::as_array)
        .ok_or_else(|| {
            "Wayfire session state omitted its compositor-reported output modes".to_owned()
        })?;
    let value = values
        .iter()
        .find(|value| value.get("name").and_then(Value::as_str) == Some(target))
        .ok_or_else(|| format!("Wayfire did not report the mode for {target}"))?;
    let enabled = value
        .get("enabled")
        .and_then(Value::as_bool)
        .ok_or_else(|| format!("Wayfire reported an invalid enabled state for {target}"))?;
    let mirrored = value
        .get("mirrored")
        .and_then(Value::as_bool)
        .ok_or_else(|| format!("Wayfire reported an invalid mirror state for {target}"))?;
    let width = value
        .get("width")
        .and_then(Value::as_u64)
        .and_then(|value| u32::try_from(value).ok())
        .unwrap_or_default();
    let height = value
        .get("height")
        .and_then(Value::as_u64)
        .and_then(|value| u32::try_from(value).ok())
        .unwrap_or_default();
    let refresh_millihz = value
        .get("refresh-millihz")
        .and_then(Value::as_u64)
        .unwrap_or_default();
    Ok(OutputModeState {
        enabled,
        mirrored,
        width,
        height,
        refresh_millihz,
    })
}

fn configured_mode_restored(
    configured_mode: Option<&str>,
    target: &str,
    mirrors: &[MirrorState],
    output: &OutputModeState,
) -> bool {
    match configured_mode {
        Some("off") => !output.enabled && !output.mirrored,
        Some(mode) if mirror_source_from_mode(mode).is_some() => {
            let source = mirror_source_from_mode(mode).unwrap_or_default();
            output.enabled && output.mirrored && is_mirror(mirrors, source, target)
        }
        Some(mode) if resolution_from_mode(mode).is_some() => {
            let (width, height, refresh_millihz) = resolution_from_mode(mode).unwrap_or_default();
            output.enabled
                && !output.mirrored
                && output.width == width
                && output.height == height
                && refresh_millihz.is_none_or(|refresh| output.refresh_millihz == refresh)
        }
        _ => output.enabled && !output.mirrored,
    }
}

fn resolution_from_mode(mode: &str) -> Option<(u32, u32, Option<u64>)> {
    let (size, refresh) = mode.split_once('@').unwrap_or((mode, ""));
    let (width, height) = size.split_once('x')?;
    let width = width.parse::<u32>().ok().filter(|value| *value > 0)?;
    let height = height.parse::<u32>().ok().filter(|value| *value > 0)?;
    let refresh_millihz = if refresh.is_empty() {
        None
    } else {
        let refresh = refresh.parse::<f64>().ok().filter(|value| *value > 0.0)?;
        Some(if refresh < 1000.0 {
            (refresh * 1000.0).round() as u64
        } else {
            refresh.round() as u64
        })
    };
    Some((width, height, refresh_millihz))
}

fn query_state(session: &scottland::session::Session) -> Result<Value, String> {
    let socket = session
        .wayfire_socket()
        .ok_or_else(|| "the selected session has no Wayfire IPC socket".to_owned())?;
    let mut stream = UnixStream::connect(socket)
        .map_err(|error| format!("could not connect to the selected Wayfire session: {error}"))?;
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(2)));
    let body = serde_json::to_vec(&json!({
        "method": "scottland/session-state",
        "data": {}
    }))
    .map_err(|error| format!("could not encode the Wayfire session-state request: {error}"))?;
    if body.len() > MAX_FRAME_BYTES {
        return Err("Wayfire session-state request exceeds its frame limit".to_owned());
    }
    let length = u32::try_from(body.len())
        .map_err(|_| "Wayfire session-state request is too large".to_owned())?;
    stream
        .write_all(&length.to_le_bytes())
        .and_then(|()| stream.write_all(&body))
        .map_err(|error| format!("could not send the Wayfire session-state request: {error}"))?;

    let mut header = [0u8; 4];
    stream
        .read_exact(&mut header)
        .map_err(|error| format!("could not read Wayfire session-state response: {error}"))?;
    let length = u32::from_le_bytes(header) as usize;
    if length > MAX_FRAME_BYTES {
        return Err("Wayfire session-state response exceeds its frame limit".to_owned());
    }
    let mut response = vec![0u8; length];
    stream
        .read_exact(&mut response)
        .map_err(|error| format!("could not read Wayfire session-state response: {error}"))?;
    let value: Value = serde_json::from_slice(&response)
        .map_err(|error| format!("Wayfire returned invalid session-state JSON: {error}"))?;
    let value = value
        .get("info")
        .filter(|value| value.is_object())
        .unwrap_or(&value);
    if let Some(error) = value.get("error").and_then(Value::as_str) {
        return Err(format!("Wayfire session-state failed: {error}"));
    }
    Ok(value.clone())
}

fn reload_count(state: &Value) -> Result<u64, String> {
    state
        .get("config-reloads")
        .and_then(Value::as_u64)
        .ok_or_else(|| "Wayfire session state omitted its config-reload count".to_owned())
}

fn wait_for_reload(session: &scottland::session::Session, before: u64) -> Result<(), String> {
    let deadline = Instant::now() + RELOAD_WAIT;
    loop {
        let state = query_state(session)?;
        let observed = reload_count(&state)?;
        if observed > before {
            return Ok(());
        }
        if Instant::now() >= deadline {
            return Err(format!(
                "the existing config watcher did not report applying the mirror change (config-reloads stayed at {before}; last observed {observed})"
            ));
        }
        thread::sleep(Duration::from_millis(50));
    }
}

fn rollback_marker(session: &scottland::session::Session, path: &Path, reason: String) -> String {
    let cleanup = (|| {
        remove_request_at(path)?;
        let state = query_state(session)?;
        wait_for_reload(session, reload_count(&state)?)
    })();
    match cleanup {
        Ok(()) => reason,
        Err(error) => format!("{reason}; could not restore the configured output mode: {error}"),
    }
}

fn marker_path(session: &scottland::session::Session) -> Result<PathBuf, String> {
    if !outputs::valid_name(&session.name) {
        return Err("selected session has an invalid display name".to_owned());
    }
    Ok(session_directory(session)
        .join("display-mirror")
        .join(&session.name)
        .join("request"))
}

fn session_directory(session: &scottland::session::Session) -> PathBuf {
    if let Some(path) = session
        .var("SCOTTLAND_SESSION_DIR")
        .filter(|value| !value.is_empty())
    {
        return PathBuf::from(path);
    }
    let runtime = session
        .var("XDG_RUNTIME_DIR")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(format!("/run/user/{}", scottland::dirs::uid())));
    runtime.join("scottland")
}

fn read_request(session: &scottland::session::Session) -> Result<Option<MirrorRequest>, String> {
    read_request_at(&marker_path(session)?)
}

fn read_request_at(path: &Path) -> Result<Option<MirrorRequest>, String> {
    let contents = match fs::read_to_string(path) {
        Ok(contents) => contents,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(format!("could not read mirror request: {error}")),
    };
    let mut lines = contents.lines();
    let source = lines.next().unwrap_or_default();
    let target = lines.next().unwrap_or_default();
    if !outputs::valid_name(source) || !outputs::valid_name(target) || lines.next().is_some() {
        return Err("the selected session's mirror request is invalid".to_owned());
    }
    Ok(Some(MirrorRequest {
        source: source.to_owned(),
        target: target.to_owned(),
    }))
}

fn write_request_at(path: &Path, request: &MirrorRequest) -> Result<(), String> {
    let directory = path
        .parent()
        .ok_or_else(|| "mirror request path has no parent directory".to_owned())?;
    fs::create_dir_all(directory)
        .map_err(|error| format!("could not create session mirror marker directory: {error}"))?;
    let bytes = format!("{}\n{}\n", request.source, request.target);
    let mut temporary = None;
    for _ in 0..16 {
        let candidate = directory.join(format!(
            ".{}.{}.{}.tmp",
            path.file_name()
                .and_then(|name| name.to_str())
                .unwrap_or("mirror"),
            std::process::id(),
            NEXT_TEMP.fetch_add(1, Ordering::Relaxed)
        ));
        match OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&candidate)
        {
            Ok(mut file) => {
                if let Err(error) = file
                    .write_all(bytes.as_bytes())
                    .and_then(|()| file.sync_all())
                {
                    let _ = fs::remove_file(&candidate);
                    return Err(format!("could not write session mirror request: {error}"));
                }
                temporary = Some(candidate);
                break;
            }
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(format!("could not create session mirror request: {error}")),
        }
    }
    let temporary = temporary
        .ok_or_else(|| "could not allocate a temporary session mirror request".to_owned())?;
    if let Err(error) = fs::rename(&temporary, path) {
        let _ = fs::remove_file(&temporary);
        return Err(format!("could not activate session mirroring: {error}"));
    }
    Ok(())
}

fn remove_request_at(path: &Path) -> Result<(), String> {
    match fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(format!("could not turn off session mirroring: {error}")),
    }
}

fn configured_output_mode(
    session: &scottland::session::Session,
    target: &str,
) -> Result<Option<String>, String> {
    let config = session
        .var("SCOTTLAND_CONFIG_OUTPUT")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| session_directory(session).join("wayfire.ini"));
    let text = match fs::read_to_string(&config) {
        Ok(text) => text,
        Err(error) => return Err(format!("could not read {}: {error}", config.display())),
    };
    Ok(configured_output_mode_text(&text, target))
}

fn configured_output_mode_text(text: &str, target: &str) -> Option<String> {
    let wanted = format!("[output:{target}]");
    let mut section_matches = false;
    let mut mode = None;
    for line in text.lines() {
        let trimmed = line.trim();
        if trimmed == "# ---- generated by 98-session-output-mirror" {
            break;
        }
        let content = trimmed.split('#').next().unwrap_or("").trim();
        if content.starts_with('[') && content.ends_with(']') {
            section_matches = content == wanted;
        } else if section_matches && !content.is_empty() && !content.starts_with(';') {
            if let Some((key, value)) = content.split_once('=') {
                if key.trim() == "mode" {
                    mode = Some(value.trim().to_owned());
                }
            }
        }
    }
    mode
}

fn mirror_source_from_mode(mode: &str) -> Option<&str> {
    let mut words = mode.split_whitespace();
    (words.next()? == "mirror").then(|| words.next()).flatten()
}

fn refresh_text(millihz: u64) -> String {
    let whole = millihz / 1000;
    let fractional = millihz % 1000;
    if fractional == 0 {
        whole.to_string()
    } else {
        format!("{whole}.{fractional:03}")
            .trim_end_matches('0')
            .to_owned()
    }
}

#[cfg(test)]
mod tests {
    use super::{
        MirrorState, OutputModeState, configured_mode_restored, configured_output_mode_text,
        mirror_source_from_mode, refresh_text, resolution_from_mode, select_pair,
    };
    use crate::outputs::OutputInfo;

    fn output(name: &str, enabled: bool) -> OutputInfo {
        OutputInfo {
            name: name.to_owned(),
            enabled,
            scale: 1.0,
        }
    }

    #[test]
    fn picks_the_first_enabled_panel_and_external_by_connector_name() {
        let values = [
            output("HDMI-A-1", true),
            output("eDP-2", true),
            output("DP-1", true),
            output("eDP-1", true),
            output("DP-2", false),
        ];
        assert_eq!(
            select_pair(&values).unwrap(),
            ("eDP-1".into(), "DP-1".into())
        );
    }

    #[test]
    fn refuses_without_changing_when_no_enabled_panel_or_external_exists() {
        assert!(
            select_pair(&[output("DP-1", true)])
                .unwrap_err()
                .contains("built-in panel")
        );
        assert!(
            select_pair(&[output("eDP-1", true)])
                .unwrap_err()
                .contains("external output")
        );
    }

    #[test]
    fn status_recognizes_only_external_outputs_mirroring_a_builtin_panel() {
        let panel_to_external = super::MirrorState {
            source: "eDP-1".into(),
            target: "DP-1".into(),
            width: 1920,
            height: 1080,
            refresh_millihz: 60000,
        };
        let external_to_external = super::MirrorState {
            source: "DP-1".into(),
            target: "HDMI-A-1".into(),
            width: 1920,
            height: 1080,
            refresh_millihz: 60000,
        };
        assert!(super::is_panel_mirror(&panel_to_external));
        assert!(!super::is_panel_mirror(&external_to_external));
    }

    #[test]
    fn reads_the_effective_pre_mirror_mode_from_the_assembled_session_config() {
        let config = "[output:DP-1]\nmode = 1920x1080@60\n\n[output:eDP-1]\nmode = auto\n\n# ---- generated by 98-session-output-mirror\n[output:DP-1]\nmode = mirror eDP-1\n";
        assert_eq!(
            configured_output_mode_text(config, "DP-1").as_deref(),
            Some("1920x1080@60")
        );
        assert_eq!(
            configured_output_mode_text(config, "eDP-1").as_deref(),
            Some("auto")
        );
        assert_eq!(mirror_source_from_mode("mirror eDP-1"), Some("eDP-1"));
        assert_eq!(mirror_source_from_mode("1920x1080@60"), None);
    }

    #[test]
    fn formats_the_compositor_refresh_in_hertz() {
        assert_eq!(refresh_text(60000), "60");
        assert_eq!(refresh_text(59940), "59.94");
        assert_eq!(refresh_text(60001), "60.001");
    }

    #[test]
    fn parses_configured_refresh_rates_in_hertz_or_millihertz() {
        assert_eq!(
            resolution_from_mode("1280x720@60"),
            Some((1280, 720, Some(60000)))
        );
        assert_eq!(
            resolution_from_mode("1920x1080@59940"),
            Some((1920, 1080, Some(59940)))
        );
        assert_eq!(resolution_from_mode("1280x720"), Some((1280, 720, None)));
        assert_eq!(resolution_from_mode("auto"), None);
    }

    #[test]
    fn off_requires_the_compositor_to_restore_the_configured_output_mode() {
        let output = OutputModeState {
            enabled: true,
            mirrored: false,
            width: 1280,
            height: 720,
            refresh_millihz: 60000,
        };
        assert!(configured_mode_restored(
            Some("1280x720@60"),
            "DP-1",
            &[],
            &output
        ));
        assert!(!configured_mode_restored(
            Some("1920x1080@60"),
            "DP-1",
            &[],
            &output
        ));
        assert!(configured_mode_restored(
            Some("1280x720"),
            "DP-1",
            &[],
            &OutputModeState {
                refresh_millihz: 59940,
                ..output.clone()
            }
        ));
        assert!(configured_mode_restored(Some("auto"), "DP-1", &[], &output));

        let mirror = MirrorState {
            source: "HDMI-A-1".into(),
            target: "DP-1".into(),
            width: 1280,
            height: 720,
            refresh_millihz: 60000,
        };
        let mirrored_output = OutputModeState {
            mirrored: true,
            ..output
        };
        assert!(configured_mode_restored(
            Some("mirror HDMI-A-1"),
            "DP-1",
            &[mirror.clone()],
            &mirrored_output
        ));
        assert!(!configured_mode_restored(
            Some("mirror eDP-1"),
            "DP-1",
            &[mirror],
            &mirrored_output
        ));
    }
}
