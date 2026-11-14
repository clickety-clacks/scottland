use std::env;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::os::fd::AsRawFd;
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use crate::brightness_target::brightness_target_percent;

const USAGE: &str = "usage: scottland-display brightness [--no-osd] [--output NAME] [STEP|off|on] | brightness keyboard [--no-osd] [up|down|cycle|off|restore]";
const LOCK_EX: i32 = 2;
const LOCK_NB: i32 = 4;
static NEXT_TEMP: AtomicU64 = AtomicU64::new(0);

unsafe extern "C" {
    fn flock(fd: i32, operation: i32) -> i32;
}

pub(super) fn run(args: &[&str]) -> Result<(), String> {
    if args.first() == Some(&"keyboard") {
        run_keyboard(&args[1..])
    } else {
        run_display(args)
    }
}

fn run_display(args: &[&str]) -> Result<(), String> {
    let mut no_osd = false;
    let mut output = None;
    let mut action = None;
    let mut index = 0;
    while index < args.len() {
        match args[index] {
            "--no-osd" if !no_osd => no_osd = true,
            "--output" if output.is_none() && index + 1 < args.len() => {
                index += 1;
                if !valid_name(args[index]) {
                    return Err(USAGE.to_owned());
                }
                output = Some(args[index].to_owned());
            }
            value if !value.starts_with('-') && action.is_none() => action = Some(value),
            _ => return Err(USAGE.to_owned()),
        }
        index += 1;
    }

    match action {
        Some("off") => {
            if output.is_some() {
                return Err(USAGE.to_owned());
            }
            crate::outputs::power_off()
        }
        Some("on") => {
            if output.is_some() {
                return Err(USAGE.to_owned());
            }
            crate::outputs::power_on()
        }
        Some(step) => set_display_brightness(output.as_deref(), step, no_osd),
        None => read_display_brightness(output.as_deref()),
    }
}

fn read_display_brightness(requested_output: Option<&str>) -> Result<(), String> {
    match selected_output(requested_output) {
        Some(name) if !is_internal(&name) => {
            let reading = ddc_read(&name)?;
            println!("{}", percent(reading.current, reading.maximum));
            Ok(())
        }
        _ => {
            let device = select_backlight()?;
            let reading = read_backlight(&device)?;
            println!("{}", percent(reading.current, reading.maximum));
            Ok(())
        }
    }
}

fn set_display_brightness(
    requested_output: Option<&str>,
    step: &str,
    no_osd: bool,
) -> Result<(), String> {
    if brightness_target_percent(50, step).is_none() {
        return Err(USAGE.to_owned());
    }
    let Some(_lock) = try_step_lock()? else {
        return Ok(());
    };
    let resulting = match selected_output(requested_output) {
        Some(name) if !is_internal(&name) => set_ddc_brightness(&name, step)?,
        _ => set_backlight_brightness(step)?,
    };
    if !no_osd {
        show_osd("display-brightness", "Brightness", resulting);
    }
    Ok(())
}

#[derive(Clone, Copy, Debug)]
struct Reading {
    current: u32,
    maximum: u32,
}

impl Reading {
    fn validate(self, what: &str) -> Result<Self, String> {
        if self.maximum == 0 || self.current > self.maximum {
            return Err(format!("{what} returned an invalid brightness range"));
        }
        Ok(self)
    }
}

fn read_backlight(name: &str) -> Result<Reading, String> {
    let root = sysfs_root().join("class/backlight").join(name);
    let current = read_number(&root.join("brightness"), "backlight brightness")?;
    let maximum = read_number(&root.join("max_brightness"), "backlight maximum")?;
    Reading { current, maximum }.validate("backlight")
}

fn set_backlight_brightness(step: &str) -> Result<u32, String> {
    let name = select_backlight()?;
    let reading = read_backlight(&name)?;
    let current_percent = percent(reading.current, reading.maximum);
    let target_percent = brightness_target_percent(current_percent as u8, step)
        .ok_or_else(|| USAGE.to_owned())? as u32;
    let target_raw = raw_level(target_percent, reading.maximum);
    set_logind_brightness("backlight", &name, target_raw)?;
    let actual = read_backlight(&name)?;
    Ok(percent(actual.current, actual.maximum))
}

fn set_logind_brightness(subsystem: &str, name: &str, value: u32) -> Result<(), String> {
    let value = value.to_string();
    let output = Command::new("busctl")
        .args([
            "--system",
            "call",
            "org.freedesktop.login1",
            "/org/freedesktop/login1",
            "org.freedesktop.login1.Manager",
            "SetBrightness",
            "ssu",
            subsystem,
            name,
            &value,
        ])
        .output()
        .map_err(|error| format!("could not set {subsystem} brightness: {error}"))?;
    command_result(output, &format!("could not set {subsystem} brightness"))
}

fn select_backlight() -> Result<String, String> {
    let directory = sysfs_root().join("class/backlight");
    let entries = match fs::read_dir(&directory) {
        Ok(entries) => entries,
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            return Err("No backlight device found".to_owned());
        }
        Err(error) => return Err(format!("could not inspect backlight devices: {error}")),
    };
    let mut names = entries
        .filter_map(Result::ok)
        .filter_map(|entry| entry.file_name().into_string().ok())
        .collect::<Vec<_>>();
    names.sort();
    let selected = names
        .iter()
        .find(|name| name.as_str() == "gmux_backlight")
        .or_else(|| names.iter().find(|name| name.starts_with("amdgpu_bl")))
        .or_else(|| names.iter().find(|name| name.as_str() == "intel_backlight"))
        .or_else(|| names.iter().find(|name| name.starts_with("acpi_video")))
        .or_else(|| {
            names
                .iter()
                .find(|name| name.as_str() != "appletb_backlight")
        });
    selected
        .cloned()
        .ok_or_else(|| "No backlight device found".to_owned())
}

fn run_keyboard(args: &[&str]) -> Result<(), String> {
    let mut no_osd = false;
    let mut action = None;
    for arg in args {
        match *arg {
            "--no-osd" if !no_osd => no_osd = true,
            "--no-osd" => return Err(USAGE.to_owned()),
            value if action.is_none() && !value.starts_with('-') => action = Some(value),
            _ => return Err(USAGE.to_owned()),
        }
    }
    match action.unwrap_or("up") {
        "off" => keyboard_off(),
        "restore" => keyboard_restore(false),
        action @ ("up" | "down" | "cycle") => {
            let device = select_keyboard()?;
            let reading = read_keyboard(&device)?;
            if reading.maximum == 0 {
                return Err("keyboard backlight maximum is zero".to_owned());
            }
            let step = (reading.maximum / 10).max(1);
            let next = match action {
                "up" => reading.current.saturating_add(step).min(reading.maximum),
                "down" => reading.current.saturating_sub(step),
                "cycle" => {
                    if reading.current.saturating_add(step) > reading.maximum {
                        0
                    } else {
                        reading.current + step
                    }
                }
                _ => unreachable!(),
            };
            set_logind_brightness("leds", &device, next)?;
            let actual = read_keyboard(&device)?;
            let result_percent = percent_floor(actual.current, actual.maximum);
            if !no_osd {
                show_osd("keyboard-brightness", "Keyboard brightness", result_percent);
            }
            Ok(())
        }
        _ => Err(USAGE.to_owned()),
    }
}

fn keyboard_off() -> Result<(), String> {
    let device = select_keyboard()?;
    let reading = read_keyboard(&device)?;
    let path = keyboard_state_path();
    write_atomic(&path, reading.current.to_string().as_bytes())
        .map_err(|error| format!("could not remember keyboard brightness: {error}"))?;
    set_logind_brightness("leds", &device, 0)
}

fn keyboard_restore(ignore_failure: bool) -> Result<(), String> {
    let path = keyboard_state_path();
    let saved = match fs::read_to_string(&path) {
        Ok(value) => value,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(()),
        Err(error) => {
            let message = format!("could not read saved keyboard brightness: {error}");
            if ignore_failure {
                eprintln!("scottland-display: {message}");
                return Ok(());
            }
            return Err(message);
        }
    };
    let level = match saved.trim().parse::<u32>() {
        Ok(value) => value,
        Err(_) => {
            let message = "saved keyboard brightness is invalid".to_owned();
            if ignore_failure {
                eprintln!("scottland-display: {message}");
                return Ok(());
            }
            return Err(message);
        }
    };
    let result = select_keyboard().and_then(|device| {
        let reading = read_keyboard(&device)?;
        set_logind_brightness("leds", &device, level.min(reading.maximum))
    });
    if let Err(error) = result {
        if ignore_failure {
            eprintln!("scottland-display: could not restore keyboard brightness: {error}");
            return Ok(());
        }
        return Err(error);
    }
    match fs::remove_file(&path) {
        Ok(()) => Ok(()),
        Err(error) if ignore_failure => {
            eprintln!("scottland-display: could not clear saved keyboard brightness: {error}");
            Ok(())
        }
        Err(error) => Err(format!(
            "could not clear saved keyboard brightness: {error}"
        )),
    }
}

pub(super) fn wake() -> Result<(), String> {
    let outputs = crate::outputs::power_on();
    keyboard_restore(true)?;
    outputs
}

fn select_keyboard() -> Result<String, String> {
    select_keyboard_under(&sysfs_root())
}

fn select_keyboard_under(root: &Path) -> Result<String, String> {
    let directory = root.join("class/leds");
    let entries = match fs::read_dir(&directory) {
        Ok(entries) => entries,
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            return Err("No keyboard backlight device found".to_owned());
        }
        Err(error) => return Err(format!("could not inspect keyboard backlights: {error}")),
    };
    let mut names = entries
        .filter_map(Result::ok)
        .filter_map(|entry| entry.file_name().into_string().ok())
        .filter(|name| name.contains("kbd_backlight"))
        .collect::<Vec<_>>();
    names.sort();
    names
        .into_iter()
        .next()
        .ok_or_else(|| "No keyboard backlight device found".to_owned())
}

fn read_keyboard(name: &str) -> Result<Reading, String> {
    let root = sysfs_root().join("class/leds").join(name);
    let current = read_number(&root.join("brightness"), "keyboard brightness")?;
    let maximum = read_number(&root.join("max_brightness"), "keyboard maximum")?;
    Reading { current, maximum }.validate("keyboard backlight")
}

fn show_osd(icon: &str, message: &str, progress: u32) {
    let progress = progress.to_string();
    let result = Command::new("scottland-widget")
        .args([
            "osd",
            "--icon",
            icon,
            "--message",
            message,
            "--progress",
            &progress,
        ])
        .output();
    match result {
        Ok(output) if output.status.success() => {}
        Ok(output) => {
            let detail = String::from_utf8_lossy(&output.stderr).trim().to_owned();
            eprintln!(
                "scottland-display: could not show brightness display{}",
                if detail.is_empty() {
                    output.status.code().map_or_else(
                        || " (terminated by signal)".to_owned(),
                        |code| format!(" (exit status {code})"),
                    )
                } else {
                    format!(": {detail}")
                }
            );
        }
        Err(error) => eprintln!("scottland-display: could not show brightness display: {error}"),
    }
}

fn selected_output(requested: Option<&str>) -> Option<String> {
    requested
        .map(str::to_owned)
        .or_else(|| crate::outputs::focused_output().ok())
}

fn is_internal(name: &str) -> bool {
    ["eDP-", "LVDS-", "DSI-"]
        .iter()
        .any(|prefix| name.starts_with(prefix))
}

fn percent(current: u32, maximum: u32) -> u32 {
    if maximum == 0 {
        0
    } else {
        ((u64::from(current) * 100 + u64::from(maximum) / 2) / u64::from(maximum)) as u32
    }
}

fn percent_floor(current: u32, maximum: u32) -> u32 {
    if maximum == 0 {
        0
    } else {
        ((u64::from(current) * 100) / u64::from(maximum)) as u32
    }
}

fn raw_level(target_percent: u32, maximum: u32) -> u32 {
    ((u64::from(target_percent) * u64::from(maximum) + 50) / 100) as u32
}

fn read_number(path: &Path, label: &str) -> Result<u32, String> {
    let value =
        fs::read_to_string(path).map_err(|error| format!("could not read {label}: {error}"))?;
    value
        .trim()
        .parse()
        .map_err(|_| format!("{label} is not an integer"))
}

fn run_ddc(arguments: &[&str], what: &str) -> Result<Output, String> {
    let output = Command::new("ddcutil")
        .args(arguments)
        .output()
        .map_err(|error| format!("could not run ddcutil for {what}: {error}"))?;
    if output.status.success() {
        Ok(output)
    } else {
        Err(command_error(output, &format!("ddcutil {what} failed")))
    }
}

#[derive(Clone, Debug)]
struct DdcInfo {
    bus: u32,
    maximum: u32,
    current: Option<u32>,
}

fn set_ddc_brightness(output: &str, step: &str) -> Result<u32, String> {
    let absolute = !step.starts_with('+') && !step.ends_with("%-");
    let mut info = cached_ddc(output)?;
    if !absolute {
        info = match refresh_ddc_reading(output, info.bus) {
            Ok(info) => info,
            Err(error) => {
                clear_ddc_cache(output);
                return Err(error);
            }
        };
    }
    let current_percent = if absolute {
        0
    } else {
        percent(info.current.unwrap_or(0), info.maximum)
    };
    let target_percent = brightness_target_percent(current_percent as u8, step)
        .ok_or_else(|| USAGE.to_owned())? as u32;
    let raw = raw_level(target_percent, info.maximum);
    let bus = info.bus.to_string();
    let raw_text = raw.to_string();
    if let Err(error) = run_ddc(
        &[
            "--bus",
            &bus,
            "--skip-ddc-checks",
            "--noverify",
            "setvcp",
            "10",
            &raw_text,
        ],
        "brightness write",
    ) {
        clear_ddc_cache(output);
        return Err(format!("DDC brightness write failed for {output}: {error}"));
    }
    cache_ddc(output, &info, cache_now());
    Ok(target_percent)
}

fn ddc_read(output: &str) -> Result<Reading, String> {
    let info = cached_ddc(output)?;
    let info = match refresh_ddc_reading(output, info.bus) {
        Ok(info) => info,
        Err(error) => {
            clear_ddc_cache(output);
            return Err(error);
        }
    };
    let current = info.current.unwrap_or(0);
    Reading {
        current,
        maximum: info.maximum,
    }
    .validate("DDC")
}

fn cached_ddc(output: &str) -> Result<DdcInfo, String> {
    let path = ddc_cache_path(output);
    if let Ok(value) = fs::read_to_string(&path) {
        let fields = value.trim().split('\t').collect::<Vec<_>>();
        if fields.len() >= 2
            && let Ok(timestamp) = fields[1].parse::<u64>()
        {
            let age = cache_now().saturating_sub(timestamp);
            if fields[0] == "none" && age <= 60 {
                return Err(format!("no DDC device found for {output}"));
            }
            if fields[0] == "ok" && age <= 10 && fields.len() == 4 {
                let parsed = fields[2]
                    .parse::<u32>()
                    .ok()
                    .zip(fields[3].parse::<u32>().ok());
                if let Some((bus, maximum)) = parsed.filter(|(_, maximum)| *maximum > 0) {
                    return Ok(DdcInfo {
                        bus,
                        maximum,
                        current: None,
                    });
                }
            }
        }
    }
    match detect_ddc(output) {
        Err(error) if !error.starts_with("no DDC device found for ") => {
            clear_ddc_cache(output);
            Err(error)
        }
        result => result,
    }
}

fn detect_ddc(output: &str) -> Result<DdcInfo, String> {
    let detected = run_ddc(
        &["--skip-ddc-checks", "detect", "--brief"],
        "display detection",
    )?;
    let text = String::from_utf8_lossy(&detected.stdout);
    let mut display = false;
    let mut bus = None;
    for line in text.lines() {
        let line = line.trim();
        if line.starts_with("Display ") {
            if display && bus.is_some() {
                return refresh_ddc_reading(output, bus.unwrap());
            }
            display = false;
            bus = None;
        }
        if let Some(value) = line.strip_prefix("I2C bus:") {
            let value = value
                .trim()
                .strip_prefix("/dev/i2c-")
                .unwrap_or(value.trim());
            bus = value.parse::<u32>().ok();
        }
        if let Some(value) = line.strip_prefix("DRM connector:") {
            display = drm_output_name(value.trim()) == output;
        }
    }
    if display && let Some(bus) = bus {
        return refresh_ddc_reading(output, bus);
    }
    cache_no_ddc(output);
    Err(format!("no DDC device found for {output}"))
}

fn drm_output_name(connector: &str) -> &str {
    let Some(card) = connector.strip_prefix("card") else {
        return connector;
    };
    let Some((index, output)) = card.split_once('-') else {
        return connector;
    };
    if !index.is_empty() && index.bytes().all(|byte| byte.is_ascii_digit()) {
        output
    } else {
        connector
    }
}

fn refresh_ddc_reading(output: &str, bus: u32) -> Result<DdcInfo, String> {
    let bus_text = bus.to_string();
    let result = run_ddc(
        &[
            "--bus",
            &bus_text,
            "--skip-ddc-checks",
            "getvcp",
            "10",
            "--brief",
        ],
        "brightness read",
    )?;
    let text = String::from_utf8_lossy(&result.stdout);
    let values = text.split_whitespace().collect::<Vec<_>>();
    let current_max = values
        .iter()
        .position(|value| *value == "C")
        .and_then(|index| {
            Some((
                values.get(index + 1)?.parse::<u32>().ok()?,
                values.get(index + 2)?.parse::<u32>().ok()?,
            ))
        })
        .filter(|(_, maximum)| *maximum > 0)
        .ok_or_else(|| format!("ddcutil returned an invalid brightness reading for {output}"))?;
    let info = DdcInfo {
        bus,
        maximum: current_max.1,
        current: Some(current_max.0),
    };
    cache_ddc(output, &info, cache_now());
    Ok(info)
}

fn ddc_cache_path(output: &str) -> PathBuf {
    let key = output
        .as_bytes()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    session_dir().join(format!("{}.ddc-{key}", display_name()))
}

fn cache_ddc(output: &str, info: &DdcInfo, timestamp: u64) {
    let data = format!("ok\t{timestamp}\t{}\t{}\n", info.bus, info.maximum);
    let _ = write_atomic(&ddc_cache_path(output), data.as_bytes());
}

fn cache_no_ddc(output: &str) {
    let data = format!("none\t{}\n", cache_now());
    let _ = write_atomic(&ddc_cache_path(output), data.as_bytes());
}

fn clear_ddc_cache(output: &str) {
    let _ = fs::remove_file(ddc_cache_path(output));
}

fn cache_now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or(0)
}

fn try_step_lock() -> Result<Option<File>, String> {
    let path = session_dir().join(format!("{}.brightness-step.lock", display_name()));
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("could not create brightness runtime directory: {error}"))?;
    }
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .mode(0o600)
        .open(&path)
        .map_err(|error| format!("could not open brightness step lock: {error}"))?;
    if unsafe { flock(file.as_raw_fd(), LOCK_EX | LOCK_NB) } == 0 {
        Ok(Some(file))
    } else {
        let error = io::Error::last_os_error();
        if error.kind() == io::ErrorKind::WouldBlock || error.raw_os_error() == Some(11) {
            Ok(None)
        } else {
            Err(format!("could not lock brightness step: {error}"))
        }
    }
}

fn keyboard_state_path() -> PathBuf {
    session_dir().join(format!("{}.keyboard-brightness", display_name()))
}

fn session_dir() -> PathBuf {
    env::var_os("SCOTTLAND_SESSION_DIR")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .or_else(|| {
            env::var_os("XDG_RUNTIME_DIR")
                .filter(|value| !value.is_empty())
                .map(|runtime| PathBuf::from(runtime).join("scottland"))
        })
        .unwrap_or_else(|| PathBuf::from(format!("/run/user/{}/scottland", scottland::dirs::uid())))
}

fn display_name() -> String {
    env::var("WAYLAND_DISPLAY")
        .ok()
        .filter(|value| valid_name(value))
        .unwrap_or_else(|| "wayland".to_owned())
}

fn sysfs_root() -> PathBuf {
    env::var_os("SCOTTLAND_SYSFS_ROOT")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("/sys"))
}

fn write_atomic(path: &Path, data: &[u8]) -> io::Result<()> {
    let parent = path.parent().ok_or_else(|| {
        io::Error::new(io::ErrorKind::InvalidInput, "path has no parent directory")
    })?;
    fs::create_dir_all(parent)?;
    let temporary = path.with_file_name(format!(
        ".{}.{}.{}.{}.tmp",
        path.file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("state"),
        std::process::id(),
        NEXT_TEMP.fetch_add(1, Ordering::Relaxed),
        cache_now()
    ));
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&temporary)?;
    let result = file.write_all(data).and_then(|()| file.sync_all());
    drop(file);
    if let Err(error) = result {
        let _ = fs::remove_file(&temporary);
        return Err(error);
    }
    if let Err(error) = fs::rename(&temporary, path) {
        let _ = fs::remove_file(&temporary);
        return Err(error);
    }
    Ok(())
}

fn command_result(output: Output, action: &str) -> Result<(), String> {
    if output.status.success() {
        Ok(())
    } else {
        Err(command_error(output, action))
    }
}

fn command_error(output: Output, action: &str) -> String {
    let detail = String::from_utf8_lossy(&output.stderr).trim().to_owned();
    if detail.is_empty() {
        output.status.code().map_or_else(
            || format!("{action}: terminated by signal"),
            |code| format!("{action}: exit status {code}"),
        )
    } else {
        format!("{action}: {detail}")
    }
}

fn valid_name(value: &str) -> bool {
    !value.is_empty()
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"._-".contains(&byte))
}

#[cfg(test)]
mod tests {
    use super::{drm_output_name, percent, percent_floor, raw_level, select_keyboard_under};
    use std::fs;
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicU64, Ordering};

    static NEXT: AtomicU64 = AtomicU64::new(0);

    struct Scratch(PathBuf);

    impl Scratch {
        fn new() -> Self {
            let root = std::env::temp_dir().join(format!(
                "scottland-display-brightness-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&root).unwrap();
            Self(root)
        }
    }

    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn brightness_levels_round_and_floor_as_contract_requires() {
        assert_eq!(percent(1, 8), 13);
        assert_eq!(percent_floor(1, 8), 12);
        assert_eq!(raw_level(25, 7), 2);
        assert_eq!(percent(0, 0), 0);
    }

    #[test]
    fn ddc_connector_names_preserve_output_suffixes() {
        assert_eq!(drm_output_name("card0-DP-1"), "DP-1");
        assert_eq!(drm_output_name("card12-HDMI-A-2"), "HDMI-A-2");
        assert_eq!(drm_output_name("DP-1"), "DP-1");
        assert_eq!(drm_output_name("cardX-DP-1"), "cardX-DP-1");
    }

    #[test]
    fn keyboard_backlight_selection_has_the_contract_missing_device_error() {
        let scratch = Scratch::new();
        assert_eq!(
            select_keyboard_under(&scratch.0).unwrap_err(),
            "No keyboard backlight device found"
        );

        let leds = scratch.0.join("class/leds");
        fs::create_dir_all(leds.join("zz-kbd_backlight")).unwrap();
        fs::create_dir_all(leds.join("aa-kbd_backlight")).unwrap();
        fs::create_dir_all(leds.join("status:blue")).unwrap();
        assert_eq!(
            select_keyboard_under(&scratch.0).unwrap(),
            "aa-kbd_backlight"
        );
    }
}
