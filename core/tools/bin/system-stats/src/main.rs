use std::env;
use std::ffi::c_char;
use std::fs::{self, OpenOptions};
use std::io::{self, BufRead, BufReader, Write};
use std::os::raw::{c_int, c_long};
use std::os::unix::fs::OpenOptionsExt;
use std::path::PathBuf;
use std::process::{Command, ExitCode};
use std::sync::mpsc::{self, RecvTimeoutError};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const INTERVAL: Duration = Duration::from_secs(2);
const SERVICE: &str = "org.scottland.Widgets";
const OBJECT: &str = "/org/scottland/Widgets";
const WINDOWS: &str = "org.scottland.Windows";
const WIDGET_DATA: &str = "org.scottland.WidgetData";

#[derive(Clone, Debug)]
struct Reading {
    cpu: Option<u8>,
    memory_percent: Option<u8>,
    memory_used_gb: Option<f64>,
    memory_total_gb: Option<f64>,
    load: Option<f64>,
    updated: Option<String>,
}

#[derive(Clone, Copy, Debug)]
struct CpuCounters {
    total: u64,
    idle: u64,
}

enum WatchInput {
    Line(String),
    Closed,
}

fn main() -> ExitCode {
    let mut args = env::args().skip(1);
    match (args.next().as_deref(), args.next()) {
        (None, None) => launch(),
        (Some("--print"), None) => print_once(),
        (Some("--watch"), None) => watch(),
        (Some("--help" | "-h"), None) => {
            println!("{HELP}");
            ExitCode::SUCCESS
        }
        _ => {
            eprintln!("usage: scottland system-stats [--print | --help]");
            ExitCode::from(2)
        }
    }
}

fn launch() -> ExitCode {
    let qml = match write_qml_file() {
        Ok(path) => path,
        Err(error) => return fail(&format!("cannot prepare the window: {error}")),
    };
    let executable = match env::current_exe() {
        Ok(path) => path,
        Err(error) => {
            let _ = fs::remove_file(&qml);
            return fail(&format!("cannot locate the System Stats command: {error}"));
        }
    };
    let status = Command::new("qs")
        .args(["-n", "-p"])
        .arg(&qml)
        .env("SCOTTLAND_SYSTEM_STATS_BIN", executable)
        .status();
    let _ = fs::remove_file(&qml);
    match status {
        Ok(status) => ExitCode::from(status.code().unwrap_or(1).clamp(0, 255) as u8),
        Err(error) => fail(&format!("cannot start Quickshell: {error}")),
    }
}

fn write_qml_file() -> io::Result<PathBuf> {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let directory = env::temp_dir();
    for attempt in 0..16u8 {
        let path = directory.join(format!(
            "scottland-system-stats-{}-{nonce}-{attempt}.qml",
            std::process::id()
        ));
        match OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&path)
        {
            Ok(mut file) => {
                if let Err(error) =
                    file.write_all(include_str!("../../../../system-stats/shell.qml").as_bytes())
                {
                    let _ = fs::remove_file(&path);
                    return Err(error);
                }
                return Ok(path);
            }
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error),
        }
    }
    Err(io::Error::new(
        io::ErrorKind::AlreadyExists,
        "could not allocate a unique QML file",
    ))
}

fn print_once() -> ExitCode {
    let before = read_cpu_counters();
    thread::sleep(INTERVAL);
    let after = read_cpu_counters();
    let reading = make_reading(before, after);
    let cpu = reading
        .cpu
        .map(|value| format!("{value}%"))
        .unwrap_or_else(|| "unavailable".to_string());
    let used = reading
        .memory_used_gb
        .map(|value| format!("{value:.1}GB"))
        .unwrap_or_else(|| "unavailable".to_string());
    let total = reading
        .memory_total_gb
        .map(|value| format!("{value:.1}GB"))
        .unwrap_or_else(|| "unavailable".to_string());
    println!("cpu {cpu} memory {used} / {total}");
    ExitCode::SUCCESS
}

fn watch() -> ExitCode {
    let app_pid = unsafe { getppid() }.max(0) as u32;
    let (sender, receiver) = mpsc::channel();
    thread::spawn(move || {
        let input = io::stdin();
        for line in BufReader::new(input.lock()).lines() {
            match line {
                Ok(line) => {
                    if sender.send(WatchInput::Line(line)).is_err() {
                        return;
                    }
                }
                Err(_) => break,
            }
        }
        let _ = sender.send(WatchInput::Closed);
    });

    let mut signal = StateSignalParser::default();
    let started = Instant::now();
    let mut previous = read_cpu_counters();
    let mut widgetized = get_widgetized().unwrap_or(false);
    let mut next_reading = started + INTERVAL;
    let mut latest: Option<Reading> = None;

    loop {
        let timeout = next_reading.saturating_duration_since(Instant::now());
        match receiver.recv_timeout(timeout) {
            Ok(WatchInput::Line(line)) => {
                if let Some((pid, is_widgetized)) = signal.feed(&line) {
                    if pid == app_pid {
                        widgetized = is_widgetized;
                        if widgetized {
                            if let Some(reading) = &latest {
                                publish(reading);
                            }
                        }
                    }
                }
            }
            Ok(WatchInput::Closed) | Err(RecvTimeoutError::Disconnected) => {
                return ExitCode::SUCCESS;
            }
            Err(RecvTimeoutError::Timeout) => {
                let after = read_cpu_counters();
                let reading = make_reading(previous, after);
                previous = after;
                println!("{}", reading.ui_json());
                if let Err(error) = io::stdout().flush() {
                    eprintln!("scottland-system-stats: cannot write reading: {error}");
                    return ExitCode::from(1);
                }
                if widgetized {
                    publish(&reading);
                }
                latest = Some(reading);
                let now = Instant::now();
                next_reading += INTERVAL;
                if next_reading <= now {
                    next_reading = now + INTERVAL;
                }
            }
        }
    }
}

fn read_cpu_counters() -> Option<CpuCounters> {
    let contents = fs::read_to_string("/proc/stat").ok()?;
    let line = contents.lines().find(|line| line.starts_with("cpu "))?;
    let values: Vec<u64> = line
        .split_whitespace()
        .skip(1)
        .map(str::parse)
        .collect::<Result<_, _>>()?;
    if values.len() < 4 {
        return None;
    }
    // Guest time is already included in user/nice. I/O wait belongs to idle below.
    let total = values
        .iter()
        .take(8)
        .try_fold(0u64, |sum, value| sum.checked_add(*value))?;
    let idle = values[3].checked_add(values.get(4).copied().unwrap_or(0))?;
    Some(CpuCounters { total, idle })
}

fn cpu_percent(before: Option<CpuCounters>, after: Option<CpuCounters>) -> Option<u8> {
    let before = before?;
    let after = after?;
    let total = after.total.checked_sub(before.total)?;
    let idle = after.idle.checked_sub(before.idle)?;
    if total == 0 || idle > total {
        return None;
    }
    let busy = total - idle;
    Some(((busy as f64 * 100.0 / total as f64).round() as u8).min(100))
}

fn memory_values() -> (Option<u8>, Option<f64>, Option<f64>) {
    const KIB_PER_GB: f64 = 1024.0 * 1024.0;
    let contents = fs::read_to_string("/proc/meminfo").ok();
    let read_mem_kib = |key: &str| {
        contents.as_deref()?.lines().find_map(|line| {
            let (name, value) = line.split_once(':')?;
            if name != key {
                return None;
            }
            value.split_whitespace().next()?.parse().ok()
        })
    };
    let total_kib = read_mem_kib("MemTotal");
    let available_kib = read_mem_kib("MemAvailable");
    let used_kib = total_kib
        .zip(available_kib)
        .map(|(total, available)| total.saturating_sub(available));
    let total_gb = total_kib.map(|value| value as f64 / KIB_PER_GB);
    let used_gb = used_kib.map(|value| value as f64 / KIB_PER_GB);
    let percent = total_kib
        .zip(used_kib)
        .filter(|(total, _)| *total > 0)
        .map(|(total, used)| ((used as f64 * 100.0 / total as f64).round() as u8).min(100));
    (percent, used_gb, total_gb)
}

fn read_load() -> Option<f64> {
    fs::read_to_string("/proc/loadavg")
        .ok()?
        .split_whitespace()
        .next()?
        .parse::<f64>()
        .ok()
        .filter(|value| value.is_finite())
}

fn read_timestamp() -> Option<String> {
    let mut now: c_long = 0;
    if unsafe { time(&mut now) } == -1 {
        return None;
    }
    let mut local = std::mem::MaybeUninit::<LocalTime>::uninit();
    if unsafe { localtime_r(&now, local.as_mut_ptr()) }.is_null() {
        return None;
    }
    let local = unsafe { local.assume_init() };
    let year = local.year.checked_add(1900)?;
    let month = local.month.checked_add(1)?;
    let offset = local.utc_offset;
    let offset_minutes = offset.checked_abs()?.checked_div(60)?;
    if year < 0
        || year > 9999
        || !(1..=12).contains(&month)
        || offset % 60 != 0
        || offset_minutes > 23 * 60 + 59
    {
        return None;
    }
    let sign = if offset < 0 { '-' } else { '+' };
    Some(format!(
        "{year:04}-{month:02}-{:02}T{:02}:{:02}:{:02}{sign}{:02}:{:02}",
        local.day,
        local.hour,
        local.minute,
        local.second,
        offset_minutes / 60,
        offset_minutes % 60
    ))
}

fn make_reading(before: Option<CpuCounters>, after: Option<CpuCounters>) -> Reading {
    let (memory_percent, memory_used_gb, memory_total_gb) = memory_values();
    Reading {
        cpu: cpu_percent(before, after),
        memory_percent,
        memory_used_gb,
        memory_total_gb,
        load: read_load().map(|value| (value * 100.0).round() / 100.0),
        updated: read_timestamp(),
    }
}

impl Reading {
    fn ui_json(&self) -> String {
        format!(
            "{{\"cpu\":{},\"memory\":{},\"memory_used_gb\":{},\"memory_total_gb\":{},\"load\":{},\"updated\":{}}}",
            optional_display(self.cpu),
            optional_display(self.memory_percent),
            optional_fixed(self.memory_used_gb, 1),
            optional_fixed(self.memory_total_gb, 1),
            optional_fixed(self.load, 2),
            optional_timestamp(self.updated.as_deref()),
        )
    }

    fn widget_json(&self) -> String {
        format!(
            "{{\"cpu\":{},\"memory\":{},\"load\":{},\"updated\":{}}}",
            optional_display(self.cpu),
            optional_display(self.memory_percent),
            optional_fixed(self.load, 2),
            optional_timestamp(self.updated.as_deref()),
        )
    }
}

fn optional_display(value: Option<u8>) -> String {
    value.map_or_else(|| "null".to_string(), |value| value.to_string())
}

fn optional_fixed(value: Option<f64>, digits: usize) -> String {
    value.map_or_else(|| "null".to_string(), |value| format!("{value:.digits$}"))
}

fn optional_timestamp(value: Option<&str>) -> String {
    value.map_or_else(|| "null".to_string(), |value| format!("\"{value}\""))
}

fn publish(reading: &Reading) {
    let output = Command::new("busctl")
        .args([
            "--user",
            "call",
            SERVICE,
            OBJECT,
            WIDGET_DATA,
            "Publish",
            "s",
        ])
        .arg(reading.widget_json())
        .output();
    match output {
        Ok(output) if output.status.success() => {}
        Ok(output) => eprintln!(
            "scottland-system-stats: WG11 publish failed: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        ),
        Err(error) => eprintln!("scottland-system-stats: cannot publish WG11 reading: {error}"),
    }
}

fn get_widgetized() -> Option<bool> {
    let output = Command::new("busctl")
        .args(["--user", "call", SERVICE, OBJECT, WINDOWS, "GetState"])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let text = String::from_utf8(output.stdout).ok()?;
    let mut fields = text.split_whitespace();
    if fields.next()? != "bd" {
        return None;
    }
    match fields.next()? {
        "true" | "1" => Some(true),
        "false" | "0" => Some(false),
        _ => None,
    }
}

#[derive(Default)]
struct StateSignalParser {
    in_signal: bool,
    pid: Option<u32>,
}

impl StateSignalParser {
    fn feed(&mut self, line: &str) -> Option<(u32, bool)> {
        if line.contains("Member=StateChanged") || line.contains("member=StateChanged") {
            self.in_signal = true;
            self.pid = None;
            return None;
        }
        if !self.in_signal {
            return None;
        }
        let value = line.trim();
        if let Some(pid) = value
            .strip_prefix("uint32 ")
            .and_then(|number| number.parse().ok())
        {
            self.pid = Some(pid);
            return None;
        }
        if let Some(widgetized) = value.strip_prefix("boolean ") {
            let result = self.pid.and_then(|pid| match widgetized {
                "true" | "1" => Some((pid, true)),
                "false" | "0" => Some((pid, false)),
                _ => None,
            });
            self.in_signal = false;
            self.pid = None;
            return result;
        }
        None
    }
}

fn fail(message: &str) -> ExitCode {
    eprintln!("scottland-system-stats: {message}");
    ExitCode::from(1)
}

const HELP: &str = "System Stats for Scottland

Usage:
  scottland system-stats          Open the System Stats window
  scottland system-stats --print  Print CPU and memory, then exit

The window reads Linux kernel counters every two seconds. Print mode takes two live CPU
counter readings two seconds apart.";

// Linux libc's tm extension supplies the active local UTC offset without spawning a formatter.
#[repr(C)]
struct LocalTime {
    second: c_int,
    minute: c_int,
    hour: c_int,
    day: c_int,
    month: c_int,
    year: c_int,
    _weekday: c_int,
    _yearday: c_int,
    _is_dst: c_int,
    utc_offset: c_long,
    _timezone: *const c_char,
}

unsafe extern "C" {
    fn getppid() -> c_int;
    fn time(timer: *mut c_long) -> c_long;
    fn localtime_r(timer: *const c_long, result: *mut LocalTime) -> *mut LocalTime;
}
