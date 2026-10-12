use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::notification::{Notification, NotificationSender, Urgency};

const PREFIX: &str = "scottland-notify-reminder-";
const REMINDER_USAGE: &str =
    "usage: scottland-notify reminder <minutes> [message] | show [--json] | clear";

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ReminderCommand {
    Set {
        minutes: u64,
        message: String,
        custom: bool,
    },
    Show {
        json: bool,
    },
    Clear,
    Fire {
        unit: String,
    },
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Reminder {
    pub unit: String,
    pub minutes: u64,
    pub message: String,
    pub custom_message: bool,
    pub remaining_seconds: u64,
    pub fire_at_epoch: u64,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ReminderOutput {
    pub stdout: Option<String>,
}

pub trait ReminderBackend {
    fn schedule(
        &mut self,
        minutes: u64,
        message: &str,
        custom_message: bool,
    ) -> Result<(String, u64), String>;
    fn active(&mut self) -> Result<Vec<Reminder>, String>;
    fn clear(&mut self) -> Result<usize, String>;
    fn load_message(&mut self, unit: &str) -> Result<String, String>;
    fn clear_message(&mut self, unit: &str) -> Result<(), String>;
}

pub trait LocalTimeFormatter {
    fn format_hhmm(&mut self, epoch_seconds: u64) -> Result<String, String>;
}

#[derive(Default)]
pub struct SystemLocalTimeFormatter;

impl LocalTimeFormatter for SystemLocalTimeFormatter {
    fn format_hhmm(&mut self, epoch_seconds: u64) -> Result<String, String> {
        let output = Command::new("date")
            .arg("-d")
            .arg(format!("@{epoch_seconds}"))
            .arg("+%H:%M")
            .output()
            .map_err(|error| format!("could not run date: {error}"))?;
        if !output.status.success() {
            return Err(format!(
                "date could not format reminder time {epoch_seconds}"
            ));
        }
        String::from_utf8(output.stdout)
            .map(|output| output.trim_end().to_string())
            .map_err(|_| "date returned non-text output".to_string())
    }
}

pub struct SystemReminderBackend {
    executable: PathBuf,
    runtime_dir: PathBuf,
}

impl SystemReminderBackend {
    pub fn new(executable: PathBuf, runtime_dir: PathBuf) -> Self {
        Self {
            executable,
            runtime_dir,
        }
    }

    pub fn from_environment() -> Result<Self, String> {
        let executable = std::env::current_exe()
            .map_err(|error| format!("could not locate scottland-notify: {error}"))?;
        let runtime_dir = std::env::var_os("XDG_RUNTIME_DIR")
            .map(PathBuf::from)
            .ok_or_else(|| "XDG_RUNTIME_DIR is required for reminders".to_string())?;
        Ok(Self::new(
            executable,
            runtime_dir.join("scottland-notify/reminders"),
        ))
    }

    fn message_path(&self, unit: &str) -> PathBuf {
        self.runtime_dir.join(format!("{unit}.message"))
    }

    fn timer_units(&self) -> Result<Vec<String>, String> {
        let output = Command::new("systemctl")
            .args([
                "--user",
                "list-timers",
                "--all",
                "--no-legend",
                "--no-pager",
            ])
            .arg(format!("{PREFIX}*.timer"))
            .output()
            .map_err(|error| format!("could not list reminder timers: {error}"))?;
        if !output.status.success() {
            return Err(format!(
                "systemctl could not list reminder timers (status {})",
                output.status
            ));
        }
        let output = String::from_utf8(output.stdout)
            .map_err(|_| "systemctl returned non-text timer output".to_string())?;
        let mut units = output
            .lines()
            .flat_map(str::split_whitespace)
            .filter(|token| parse_unit(token).is_some())
            .map(str::to_string)
            .collect::<Vec<_>>();
        units.sort();
        units.dedup();
        Ok(units)
    }
}

impl ReminderBackend for SystemReminderBackend {
    fn schedule(
        &mut self,
        minutes: u64,
        message: &str,
        custom_message: bool,
    ) -> Result<(String, u64), String> {
        let set_at = unix_time_now()?;
        let unit = unique_unit(minutes, set_at, &self.runtime_dir);
        let next_at = set_at
            .checked_add(
                minutes
                    .checked_mul(60)
                    .ok_or_else(|| "reminder duration is too large".to_string())?,
            )
            .ok_or_else(|| "reminder time is too large".to_string())?;
        fs::create_dir_all(&self.runtime_dir)
            .map_err(|error| format!("could not create reminder state directory: {error}"))?;
        set_private_directory(&self.runtime_dir)?;

        let path = self.message_path(&unit);
        write_private_record(
            &path,
            set_at,
            minutes,
            custom_message,
            if custom_message { message } else { "" },
        )?;
        let delay = format!("--on-active={minutes}m");
        let unit_arg = format!("--unit={unit}");
        let output = Command::new("systemd-run")
            .args(["--user", "--quiet", "--collect", &delay, &unit_arg, "--"])
            .arg(&self.executable)
            .args(["reminder", "__fire", &unit])
            .output()
            .map_err(|error| format!("could not start reminder timer: {error}"))?;
        if !output.status.success() {
            let _ = fs::remove_file(&path);
            return Err(format!(
                "systemd-run could not start reminder timer (status {}): {}",
                output.status,
                String::from_utf8_lossy(&output.stderr).trim()
            ));
        }
        Ok((unit, next_at))
    }

    fn active(&mut self) -> Result<Vec<Reminder>, String> {
        let mut reminders = Vec::new();
        for unit in self.timer_units()? {
            let Some((minutes, _set_at)) = parse_unit(&unit) else {
                continue;
            };
            let unit_id = unit.trim_end_matches(".timer").to_string();
            let output = Command::new("systemctl")
                .args(["--user", "show", "-P", "NextElapseUSecMonotonic", &unit])
                .output()
                .map_err(|error| format!("could not inspect reminder timer {unit}: {error}"))?;
            if !output.status.success() {
                continue;
            }
            let value = String::from_utf8_lossy(&output.stdout);
            let Some(next_monotonic_usec) = parse_systemd_timespan_microseconds(value.trim())
            else {
                continue;
            };
            let monotonic_now_usec = system_monotonic_microseconds()?;
            let realtime_now = unix_time_now()?;
            let Some((remaining_seconds, fire_at_epoch)) =
                reminder_timing(next_monotonic_usec, monotonic_now_usec, realtime_now)
            else {
                continue;
            };
            let record = read_record(&self.message_path(&unit_id));
            let custom_message = record.as_ref().is_some_and(|record| record.custom_message);
            let message = if custom_message {
                record.map(|record| record.message).unwrap_or_default()
            } else {
                String::new()
            };
            reminders.push(Reminder {
                unit: unit_id,
                minutes,
                message,
                custom_message,
                remaining_seconds,
                fire_at_epoch,
            });
        }
        reminders.sort_by(|left, right| {
            left.remaining_seconds
                .cmp(&right.remaining_seconds)
                .then_with(|| left.unit.cmp(&right.unit))
        });
        Ok(reminders)
    }

    fn clear(&mut self) -> Result<usize, String> {
        let units = self.timer_units()?;
        for timer in &units {
            let service = timer.strip_suffix(".timer").unwrap_or(timer);
            let output = Command::new("systemctl")
                .args(["--user", "stop", timer, service])
                .output()
                .map_err(|error| format!("could not stop reminder timer {timer}: {error}"))?;
            if !output.status.success() {
                return Err(format!(
                    "systemctl could not stop reminder timer {timer} (status {})",
                    output.status
                ));
            }
            let _ = fs::remove_file(self.message_path(timer.trim_end_matches(".timer")));
        }
        Ok(units.len())
    }

    fn load_message(&mut self, unit: &str) -> Result<String, String> {
        if parse_unit(unit).is_none() {
            return Err("invalid reminder unit".to_string());
        }
        let (minutes, _) = parse_unit(unit).ok_or_else(|| "invalid reminder unit".to_string())?;
        let record = read_record(&self.message_path(unit))
            .ok_or_else(|| format!("reminder state for {unit} is missing or invalid"))?;
        if record.custom_message {
            Ok(record.message)
        } else {
            Ok(format!("Your {minutes} minutes are up"))
        }
    }

    fn clear_message(&mut self, unit: &str) -> Result<(), String> {
        if parse_unit(unit).is_none() {
            return Err("invalid reminder unit".to_string());
        }
        fs::remove_file(self.message_path(unit))
            .map_err(|error| format!("could not remove reminder state for {unit}: {error}"))
    }
}

pub fn parse_reminder_args(args: &[String]) -> Result<ReminderCommand, String> {
    match args.first().map(String::as_str) {
        Some("__fire") => match args {
            [_, unit] if parse_unit(unit).is_some() => {
                Ok(ReminderCommand::Fire { unit: unit.clone() })
            }
            _ => Err("internal reminder invocation requires one valid unit".to_string()),
        },
        Some("-i" | "--interactive") => {
            Err("interactive reminders require an approved receiver contract".to_string())
        }
        Some("show" | "list") => match args {
            [_] => Ok(ReminderCommand::Show { json: false }),
            [_, flag] if flag == "--json" || flag == "-j" => {
                Ok(ReminderCommand::Show { json: true })
            }
            _ => Err(REMINDER_USAGE.to_string()),
        },
        Some("clear") if args.len() == 1 => Ok(ReminderCommand::Clear),
        Some("clear") => Err(REMINDER_USAGE.to_string()),
        Some(minutes) => {
            let minutes = minutes
                .parse::<u64>()
                .map_err(|_| REMINDER_USAGE.to_string())?;
            if minutes == 0 {
                return Err(REMINDER_USAGE.to_string());
            }
            let provided_message = args.get(1..).unwrap_or_default().join(" ");
            let custom = !provided_message.is_empty();
            let message = if custom {
                provided_message
            } else {
                format!("Your {minutes} minutes are up")
            };
            Ok(ReminderCommand::Set {
                minutes,
                message,
                custom,
            })
        }
        None => Err(REMINDER_USAGE.to_string()),
    }
}

pub fn execute_reminder(
    command: &ReminderCommand,
    backend: &mut impl ReminderBackend,
    sender: &mut impl NotificationSender,
    formatter: &mut impl LocalTimeFormatter,
) -> Result<ReminderOutput, String> {
    match command {
        ReminderCommand::Set {
            minutes,
            message,
            custom,
        } => {
            let (_, fire_at) = backend.schedule(*minutes, message, *custom)?;
            let when = formatter.format_hhmm(fire_at)?;
            let title = if *custom {
                format!("{message} in {minutes} minutes")
            } else {
                format!("Reminder set for {minutes} minutes")
            };
            let body = format!("You'll be reminded at {when}");
            sender.notify(&reminder_notice(title, body, Urgency::Low))?;
            Ok(ReminderOutput::default())
        }
        ReminderCommand::Show { json } => {
            let reminders = backend.active()?;
            if *json {
                Ok(ReminderOutput {
                    stdout: Some(reminders_json(&reminders, formatter)?),
                })
            } else {
                let body = if reminders.is_empty() {
                    "No outstanding reminders".to_string()
                } else {
                    let mut lines = Vec::with_capacity(reminders.len());
                    for reminder in &reminders {
                        let at = formatter.format_hhmm(reminder.fire_at_epoch)?;
                        lines.push(format!(
                            "{} in {} ({at})",
                            reminder_label(reminder),
                            format_remaining(reminder.remaining_seconds)
                        ));
                    }
                    lines.join("\n")
                };
                sender.notify(&reminder_notice("Upcoming reminders", body, Urgency::Low))?;
                Ok(ReminderOutput::default())
            }
        }
        ReminderCommand::Clear => {
            backend.clear()?;
            sender.notify(&reminder_notice(
                "All reminders have been cleared",
                "",
                Urgency::Low,
            ))?;
            Ok(ReminderOutput::default())
        }
        ReminderCommand::Fire { unit } => {
            let message = backend.load_message(unit)?;
            let send_result = sender.notify(&reminder_notice("Reminder", message, Urgency::Low));
            let clear_result = backend.clear_message(unit);
            send_result?;
            clear_result?;
            Ok(ReminderOutput::default())
        }
    }
}

fn reminder_notice(
    summary: impl Into<String>,
    body: impl Into<String>,
    urgency: Urgency,
) -> Notification {
    let mut notification = Notification::action(summary, body);
    notification.urgency = urgency;
    notification
}

pub fn format_remaining(seconds: u64) -> String {
    let minutes = seconds / 60;
    let remainder = seconds % 60;
    match (minutes, remainder) {
        (0, remainder) => format!("{remainder}s"),
        (minutes, 0) => format!("{minutes}m"),
        (minutes, remainder) => format!("{minutes}m {remainder}s"),
    }
}

fn reminder_label(reminder: &Reminder) -> String {
    if reminder.custom_message {
        reminder.message.clone()
    } else {
        format!("{}-min reminder", reminder.minutes)
    }
}

pub fn parse_unit(unit: &str) -> Option<(u64, u64)> {
    let unit = unit.strip_suffix(".timer").unwrap_or(unit);
    let rest = unit.strip_prefix(PREFIX)?;
    let (minutes, stamp) = rest.split_once("m-")?;
    let minutes = minutes.parse::<u64>().ok()?;
    let stamp = stamp.split('-').next()?;
    let set_at = stamp.parse::<u64>().ok()?;
    if minutes == 0 {
        return None;
    }
    Some((minutes, set_at))
}

fn unique_unit(minutes: u64, set_at: u64, runtime_dir: &Path) -> String {
    let base = format!("{PREFIX}{minutes}m-{set_at}");
    if !runtime_dir.join(format!("{base}.message")).exists() {
        return base;
    }
    let mut suffix = 1u64;
    loop {
        let candidate = format!("{base}-{suffix}");
        if !runtime_dir.join(format!("{candidate}.message")).exists() {
            return candidate;
        }
        suffix = suffix.saturating_add(1);
    }
}

fn unix_time_now() -> Result<u64, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .map_err(|error| format!("system time is before the Unix epoch: {error}"))
}

fn system_monotonic_microseconds() -> Result<u64, String> {
    let mut now = std::mem::MaybeUninit::<libc::timespec>::uninit();
    // SAFETY: clock_gettime writes one timespec to the valid output pointer.
    let status = unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, now.as_mut_ptr()) };
    if status != 0 {
        return Err(format!(
            "could not read the monotonic clock: {}",
            std::io::Error::last_os_error()
        ));
    }
    // SAFETY: the successful clock_gettime call initialized the timespec.
    let now = unsafe { now.assume_init() };
    let seconds = u64::try_from(now.tv_sec)
        .map_err(|_| "monotonic clock returned a negative second value".to_string())?;
    let nanoseconds = u64::try_from(now.tv_nsec)
        .map_err(|_| "monotonic clock returned a negative nanosecond value".to_string())?;
    if nanoseconds >= 1_000_000_000 {
        return Err("monotonic clock returned invalid nanoseconds".to_string());
    }
    seconds
        .checked_mul(1_000_000)
        .and_then(|microseconds| microseconds.checked_add(nanoseconds / 1_000))
        .ok_or_else(|| "monotonic clock value is too large".to_string())
}

fn reminder_timing(
    next_monotonic_usec: u64,
    monotonic_now_usec: u64,
    realtime_now_epoch: u64,
) -> Option<(u64, u64)> {
    let remaining_microseconds = next_monotonic_usec.checked_sub(monotonic_now_usec)?;
    if remaining_microseconds == 0 {
        return None;
    }
    let remaining_seconds =
        remaining_microseconds / 1_000_000 + u64::from(remaining_microseconds % 1_000_000 != 0);
    Some((
        remaining_seconds,
        realtime_now_epoch.saturating_add(remaining_seconds),
    ))
}

fn parse_systemd_timespan_microseconds(value: &str) -> Option<u64> {
    if let Ok(microseconds) = value.parse::<u64>() {
        return Some(microseconds);
    }
    let bytes = value.as_bytes();
    let mut index = 0usize;
    let mut total = 0u64;
    let mut found = false;
    while index < bytes.len() {
        while index < bytes.len() && !bytes[index].is_ascii_digit() {
            index += 1;
        }
        if index == bytes.len() {
            break;
        }
        let number_start = index;
        while index < bytes.len() && (bytes[index].is_ascii_digit() || bytes[index] == b'.') {
            index += 1;
        }
        let number = value.get(number_start..index)?;
        let unit_start = index;
        while index < bytes.len() && bytes[index].is_ascii_alphabetic() {
            index += 1;
        }
        let unit = value.get(unit_start..index)?;
        let multiplier = match unit {
            "d" => 86_400_000_000,
            "h" => 3_600_000_000,
            "min" => 60_000_000,
            "s" => 1_000_000,
            "ms" => 1_000,
            "us" => 1,
            _ => return None,
        };
        let (whole, fractional) = number.split_once('.').unwrap_or((number, ""));
        let whole_microseconds = whole.parse::<u64>().ok()?.checked_mul(multiplier)?;
        let fractional_microseconds = if fractional.is_empty() {
            0
        } else {
            let scale = 10u64.checked_pow(fractional.len().try_into().ok()?)?;
            fractional.parse::<u64>().ok()?.checked_mul(multiplier)? / scale
        };
        total = total
            .checked_add(whole_microseconds)?
            .checked_add(fractional_microseconds)?;
        found = true;
    }
    found.then_some(total)
}

#[derive(Debug)]
struct ReminderRecord {
    message: String,
    custom_message: bool,
}

fn write_private_record(
    path: &Path,
    set_at: u64,
    minutes: u64,
    custom_message: bool,
    message: &str,
) -> Result<(), String> {
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options
        .open(path)
        .map_err(|error| format!("could not create reminder state: {error}"))?;
    writeln!(file, "{set_at}\n{minutes}\n{}", u8::from(custom_message))
        .and_then(|()| file.write_all(message.as_bytes()))
        .map_err(|error| format!("could not write reminder state: {error}"))
}

fn read_record(path: &Path) -> Option<ReminderRecord> {
    let content = fs::read_to_string(path).ok()?;
    let mut lines = content.splitn(4, '\n');
    lines.next()?.parse::<u64>().ok()?;
    lines.next()?.parse::<u64>().ok()?;
    let custom_message = match lines.next()? {
        "1" => true,
        "0" => false,
        _ => return None,
    };
    Some(ReminderRecord {
        message: lines.next().unwrap_or_default().to_string(),
        custom_message,
    })
}

fn set_private_directory(path: &Path) -> Result<(), String> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o700))
            .map_err(|error| format!("could not protect reminder state directory: {error}"))?;
    }
    Ok(())
}

fn reminders_json(
    reminders: &[Reminder],
    formatter: &mut impl LocalTimeFormatter,
) -> Result<String, String> {
    let mut items = Vec::with_capacity(reminders.len());
    for reminder in reminders {
        let at_time = formatter.format_hhmm(reminder.fire_at_epoch)?;
        let label = reminder_label(reminder);
        items.push(format!(
            "{{\"unit\":{},\"timer\":{},\"minutes\":{},\"message\":{},\"label\":{},\"remaining\":{},\"remainingSeconds\":{},\"at\":{},\"atTime\":{}}}",
            json_string(&reminder.unit),
            json_string(&format!("{}.timer", reminder.unit)),
            reminder.minutes,
            json_string(&reminder.message),
            json_string(&label),
            json_string(&format_remaining(reminder.remaining_seconds)),
            reminder.remaining_seconds,
            reminder.fire_at_epoch,
            json_string(&at_time),
        ));
    }
    let count = items.len();
    let tooltip = match count {
        0 => "Set Reminder".to_string(),
        1 => "1 reminder".to_string(),
        count => format!("{count} reminders"),
    };
    Ok(format!(
        "{{\"count\":{count},\"active\":{},\"tooltip\":{},\"reminders\":[{}]}}",
        count > 0,
        json_string(&tooltip),
        items.join(",")
    ))
}

fn json_string(value: &str) -> String {
    let mut output = String::with_capacity(value.len() + 2);
    output.push('"');
    for character in value.chars() {
        match character {
            '"' => output.push_str("\\\""),
            '\\' => output.push_str("\\\\"),
            '\n' => output.push_str("\\n"),
            '\r' => output.push_str("\\r"),
            '\t' => output.push_str("\\t"),
            character if character < ' ' => {
                use std::fmt::Write as _;
                let _ = write!(output, "\\u{:04x}", character as u32);
            }
            character => output.push(character),
        }
    }
    output.push('"');
    output
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::notification::{NotificationSender, ensure_core_sender_supports};

    #[derive(Default)]
    struct FakeBackend {
        scheduled: Vec<(u64, String, bool)>,
        reminders: Vec<Reminder>,
        cleared: usize,
        fired: Vec<String>,
    }

    impl ReminderBackend for FakeBackend {
        fn schedule(
            &mut self,
            minutes: u64,
            message: &str,
            custom_message: bool,
        ) -> Result<(String, u64), String> {
            self.scheduled
                .push((minutes, message.to_string(), custom_message));
            Ok(("scottland-notify-reminder-5m-100".to_string(), 400))
        }

        fn active(&mut self) -> Result<Vec<Reminder>, String> {
            Ok(self.reminders.clone())
        }

        fn clear(&mut self) -> Result<usize, String> {
            self.cleared += 1;
            Ok(self.reminders.len())
        }

        fn load_message(&mut self, unit: &str) -> Result<String, String> {
            self.fired.push(unit.to_string());
            Ok("check the oven".to_string())
        }

        fn clear_message(&mut self, unit: &str) -> Result<(), String> {
            self.fired.push(format!("clear:{unit}"));
            Ok(())
        }
    }

    #[derive(Default)]
    struct FakeSender {
        calls: Vec<Notification>,
    }

    impl NotificationSender for FakeSender {
        fn notify(&mut self, notification: &Notification) -> Result<u32, String> {
            ensure_core_sender_supports(notification)?;
            self.calls.push(notification.clone());
            Ok(1)
        }
    }

    struct FakeFormatter;

    impl LocalTimeFormatter for FakeFormatter {
        fn format_hhmm(&mut self, epoch_seconds: u64) -> Result<String, String> {
            Ok(format!("at-{epoch_seconds}"))
        }
    }

    #[test]
    fn reminder_parser_accepts_set_show_clear_and_explicitly_holds_receiver_flow() {
        assert_eq!(
            parse_reminder_args(&["5".into()]).unwrap(),
            ReminderCommand::Set {
                minutes: 5,
                message: "Your 5 minutes are up".into(),
                custom: false,
            }
        );
        assert_eq!(
            parse_reminder_args(&["5".into(), "".into()]).unwrap(),
            ReminderCommand::Set {
                minutes: 5,
                message: "Your 5 minutes are up".into(),
                custom: false,
            }
        );
        assert_eq!(
            parse_reminder_args(&["30".into(), "Check".into(), "oven".into()]).unwrap(),
            ReminderCommand::Set {
                minutes: 30,
                message: "Check oven".into(),
                custom: true,
            }
        );
        assert_eq!(
            parse_reminder_args(&["show".into(), "--json".into()]).unwrap(),
            ReminderCommand::Show { json: true }
        );
        assert_eq!(
            parse_reminder_args(&["clear".into()]).unwrap(),
            ReminderCommand::Clear
        );
        assert!(parse_reminder_args(&["0".into()]).is_err());
        assert!(
            parse_reminder_args(&["-i".into()])
                .unwrap_err()
                .contains("receiver contract")
        );
    }

    #[test]
    fn setting_a_reminder_schedules_and_sends_a_confirmation() {
        let mut backend = FakeBackend::default();
        let mut sender = FakeSender::default();
        let mut formatter = FakeFormatter;
        execute_reminder(
            &ReminderCommand::Set {
                minutes: 5,
                message: "Check oven".into(),
                custom: true,
            },
            &mut backend,
            &mut sender,
            &mut formatter,
        )
        .unwrap();

        assert_eq!(backend.scheduled, [(5, "Check oven".into(), true)]);
        assert_eq!(sender.calls.len(), 1);
        assert_eq!(sender.calls[0].summary, "Check oven in 5 minutes");
        assert_eq!(sender.calls[0].body, "You'll be reminded at at-400");
        assert_eq!(sender.calls[0].urgency, Urgency::Low);
    }

    #[test]
    fn reminder_show_formats_active_items_and_json_state_without_a_receiver() {
        let mut backend = FakeBackend {
            reminders: vec![Reminder {
                unit: "scottland-notify-reminder-5m-100".into(),
                minutes: 5,
                message: "Check oven".into(),
                custom_message: true,
                remaining_seconds: 75,
                fire_at_epoch: 400,
            }],
            ..FakeBackend::default()
        };
        let mut sender = FakeSender::default();
        let mut formatter = FakeFormatter;

        execute_reminder(
            &ReminderCommand::Show { json: false },
            &mut backend,
            &mut sender,
            &mut formatter,
        )
        .unwrap();
        assert_eq!(sender.calls[0].summary, "Upcoming reminders");
        assert_eq!(sender.calls[0].body, "Check oven in 1m 15s (at-400)");

        let output = execute_reminder(
            &ReminderCommand::Show { json: true },
            &mut backend,
            &mut sender,
            &mut formatter,
        )
        .unwrap();
        assert_eq!(sender.calls.len(), 1);
        let json = output.stdout.unwrap();
        assert!(json.contains("\"unit\":\"scottland-notify-reminder-5m-100\""));
        assert!(json.contains("\"timer\":\"scottland-notify-reminder-5m-100.timer\""));
        assert!(json.contains("\"remainingSeconds\":75"));
    }

    #[test]
    fn clear_and_fire_have_real_backend_effects_and_send_their_notices() {
        let mut backend = FakeBackend {
            reminders: vec![Reminder {
                unit: "scottland-notify-reminder-5m-100".into(),
                minutes: 5,
                message: "Check oven".into(),
                custom_message: true,
                remaining_seconds: 75,
                fire_at_epoch: 400,
            }],
            ..FakeBackend::default()
        };
        let mut sender = FakeSender::default();
        let mut formatter = FakeFormatter;
        execute_reminder(
            &ReminderCommand::Clear,
            &mut backend,
            &mut sender,
            &mut formatter,
        )
        .unwrap();
        execute_reminder(
            &ReminderCommand::Fire {
                unit: "scottland-notify-reminder-5m-100".into(),
            },
            &mut backend,
            &mut sender,
            &mut formatter,
        )
        .unwrap();

        assert_eq!(backend.cleared, 1);
        assert_eq!(
            backend.fired,
            [
                "scottland-notify-reminder-5m-100",
                "clear:scottland-notify-reminder-5m-100"
            ]
        );
        assert_eq!(sender.calls[0].summary, "All reminders have been cleared");
        assert_eq!(sender.calls[1].summary, "Reminder");
        assert_eq!(sender.calls[1].body, "check the oven");
        assert_eq!(sender.calls[1].urgency, Urgency::Low);
    }

    #[test]
    fn formats_remaining_times_and_parses_timer_names_and_systemd_spans() {
        assert_eq!(format_remaining(0), "0s");
        assert_eq!(format_remaining(60), "1m");
        assert_eq!(format_remaining(75), "1m 15s");
        assert_eq!(
            parse_unit("scottland-notify-reminder-30m-1791000000-1.timer"),
            Some((30, 1_791_000_000))
        );
        assert_eq!(
            parse_systemd_timespan_microseconds("1h 2min 3.9s"),
            Some(3_723_900_000)
        );
        assert_eq!(
            parse_systemd_timespan_microseconds("60000000"),
            Some(60_000_000)
        );
        assert_eq!(
            parse_systemd_timespan_microseconds("1.25s 500ms"),
            Some(1_750_000)
        );
        assert_eq!(parse_systemd_timespan_microseconds("n/a"), None);
    }

    #[test]
    fn reminder_listing_uses_matching_monotonic_and_realtime_clocks_after_suspend() {
        // The systemd deadline remains at 3,900 seconds on CLOCK_MONOTONIC;
        // CLOCK_BOOTTIME may be 7,200 seconds after suspend, but is not used.
        let (remaining, fire_at) =
            reminder_timing(3_900_000_000, 3_600_000_000, 1_700_000_000).unwrap();

        assert_eq!(remaining, 300);
        assert_eq!(fire_at, 1_700_000_300);
        assert_eq!(
            reminder_timing(3_600_000_000, 3_600_000_000, 1_700_000_000),
            None
        );
        assert_eq!(
            reminder_timing(1_500_000, 1_000_000, 1_700_000_000),
            Some((1, 1_700_000_001))
        );
    }

    #[test]
    fn json_string_escapes_control_and_quote_characters() {
        assert_eq!(json_string("oven \"timer\"\n"), "\"oven \\\"timer\\\"\\n\"");
    }
}
