use regex::RegexBuilder;
use scottland::session::Session;
use serde_json::{Map, Value, json};
use std::env;
use std::ffi::OsStr;
use std::fs;
use std::io::{self, BufRead, Read, Write};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::thread;
use std::time::{Duration, Instant};

const HELP: &str = "\
usage: scottland-ctl [COMMAND] [ARGS...]

Settings and window commands:
  get                                      print live Scottland settings as JSON
  set NAME VALUE                          change a live Scottland setting
  stdin                                    apply setting updates from stdin
  windows                                  list open Scottland windows
  present WINDOW_ID                        bring a window or its widget to the center
  option WAYFIRE/OPTION                    print a live Wayfire option
  terminal-cwd [PID]                       print the focused terminal's working directory
  focus-app REGEX                           focus the first window matching app id or title
  session-locked                            exit 0 locked, 1 unlocked, 2 unknown
  window-close-all                          close every open window

Session and power commands:
  lid-close                                 request the distro's session locker
  lock                                      request the distro's session locker
  sleep-lock [BUDGET_MS]                    lock and wait until Scottland reports secure
  sleep-monitor [--inhibited]               lock when logind prepares to suspend";

const CONFIG_NAMES: &[&str] = &[
    "center_width",
    "rail_width",
    "min_scale",
    "max_scale",
    "scale_curve",
    "blend_width",
    "goo",
    "goo_breath_keys",
    "goo_falloff",
    "goo_thickness",
    "goo_reach",
    "goo_thinning",
    "goo_swell",
    "goo_noise",
    "goo_lump",
    "goo_drift",
    "goo_wave_speed",
    "goo_wave_damp",
    "goo_wave_height",
    "goo_spread",
    "goo_swirl",
    "goo_release",
    "goo_shine",
    "goo_relief",
    "goo_depth",
    "goo_profile",
    "goo_soak",
    "goo_pickup_balance",
    "goo_dye_density",
    "goo_overlap_film",
    "goo_hover_cloudiness",
    "goo_hover_emissivity",
    "goo_hover_distance",
    "attention_color_family",
    "unfocused_edge_tone_light",
    "unfocused_edge_tone_dark",
    "unfocused_edge_strength",
    "cycle_overshoot",
    "key_impulse",
    "key_friction",
    "resize_impulse",
    "resize_friction",
    "key_max_velocity",
    "alt_hold_delay",
    "window_double_tap_delay",
    "window_mode_tint",
    "hint_background_opacity",
    "window_hold_delay",
    "widget_bounce",
    "widget_peek_enter_delay",
    "widget_peek_leave_delay",
    "widget_attention_peek_duration",
    "widget_make_room_dwell",
    "minimize_hold_delay",
    "center_opacity_focused",
    "center_opacity_unfocused",
    "side_opacity_focused",
    "side_opacity_unfocused",
    "widget_opacity_focused",
    "widget_opacity_unfocused",
    "window_mode_opacity_focused",
    "window_mode_opacity_unfocused",
    "window_avoidance_always",
    "solo_audition_hotspot",
];
const BOOL_NAMES: &[&str] = &["goo", "goo_breath_keys", "window_avoidance_always"];
const TEXT_NAMES: &[&str] = &["scale_curve", "goo_falloff", "attention_color_family"];
const MAX_FRAME: usize = 16 * 1024 * 1024;
const BUDGET_CAP_MS: u64 = 12_000;

type Result<T> = std::result::Result<T, String>;

struct Ctl {
    session: Session,
    socket: UnixStream,
    dye_option: Option<&'static str>,
}

impl Ctl {
    fn connect() -> Result<Self> {
        let session = current_session()?;
        let path = session
            .wayfire_socket()
            .ok_or_else(|| "the selected session has no Wayfire socket".to_string())?;
        let socket = UnixStream::connect(path)
            .map_err(|error| format!("cannot connect to the selected session: {error}"))?;
        socket
            .set_read_timeout(Some(Duration::from_secs(5)))
            .map_err(|error| error.to_string())?;
        socket
            .set_write_timeout(Some(Duration::from_secs(5)))
            .map_err(|error| error.to_string())?;
        Ok(Self {
            session,
            socket,
            dye_option: None,
        })
    }

    fn call(&mut self, method: &str, data: Value) -> Result<Value> {
        let body = serde_json::to_vec(&json!({"method": method, "data": data}))
            .map_err(|error| error.to_string())?;
        if body.len() > MAX_FRAME {
            return Err("Wayfire request exceeds the frame size limit".to_string());
        }
        self.socket
            .write_all(&(body.len() as u32).to_le_bytes())
            .and_then(|_| self.socket.write_all(&body))
            .map_err(|error| format!("cannot send Wayfire request: {error}"))?;
        let mut header = [0; 4];
        self.socket
            .read_exact(&mut header)
            .map_err(|error| format!("cannot read Wayfire reply header: {error}"))?;
        let size = u32::from_le_bytes(header) as usize;
        if size > MAX_FRAME {
            return Err(format!("Wayfire reply is too large ({size} bytes)"));
        }
        let mut reply = vec![0; size];
        self.socket
            .read_exact(&mut reply)
            .map_err(|error| format!("cannot read Wayfire reply: {error}"))?;
        serde_json::from_slice(&reply)
            .map_err(|error| format!("invalid Wayfire JSON reply: {error}"))
    }

    fn setting_updates(&mut self, name: &str, value: Value) -> Result<Map<String, Value>> {
        let name = canonical_setting(name);
        if !CONFIG_NAMES.contains(&name) && name != "hint_avoidance_always" {
            return Err(format!("unknown setting {name:?}"));
        }
        let mut updates = Map::new();
        let target = if name == "goo_dye_density" {
            if self.dye_option.is_none() {
                let live = self.call(
                    "wayfire/get-config-option",
                    json!({"option": "scottland/goo_dye_density"}),
                )?;
                self.dye_option = Some(if live.get("value").is_some() {
                    "goo_dye_density"
                } else {
                    "goo_dye_strength"
                });
            }
            self.dye_option.unwrap_or("goo_dye_density")
        } else {
            name
        };
        updates.insert(format!("scottland/{target}"), value.clone());
        if name == "window_avoidance_always" {
            updates.insert("scottland/hint_avoidance_always".to_string(), value);
        }
        Ok(updates)
    }

    fn set_one(&mut self, name: &str, value: Value) -> Result<Value> {
        let updates = self.setting_updates(name, value)?;
        self.call("wayfire/set-config-options", Value::Object(updates))
    }

    fn config(&mut self) -> Result<Value> {
        let mut values = Map::new();
        let mut unsupported = Vec::new();
        for &name in CONFIG_NAMES {
            let reply = self.call(
                "wayfire/get-config-option",
                json!({"option": format!("scottland/{name}")}),
            )?;
            if let Some(value) = reply.get("value") {
                if BOOL_NAMES.contains(&name) {
                    values.insert(name.to_string(), Value::Bool(bool_value(value)));
                } else if TEXT_NAMES.contains(&name) {
                    values.insert(name.to_string(), value.clone());
                } else if let Some(number) = number_value(value) {
                    values.insert(name.to_string(), json!(number));
                } else {
                    return Err(format!("Wayfire returned a non-numeric value for {name}"));
                }
                if name == "window_avoidance_always" {
                    let legacy = self.call(
                        "wayfire/get-config-option",
                        json!({"option": "scottland/hint_avoidance_always"}),
                    )?;
                    if let Some(value) = legacy.get("value") {
                        values.insert(
                            name.to_string(),
                            Value::Bool(
                                bool_value(values.get(name).unwrap_or(&Value::Null))
                                    || bool_value(value),
                            ),
                        );
                    }
                }
            } else if name == "window_avoidance_always" {
                let legacy = self.call(
                    "wayfire/get-config-option",
                    json!({"option": "scottland/hint_avoidance_always"}),
                )?;
                if let Some(value) = legacy.get("value") {
                    values.insert(name.to_string(), Value::Bool(bool_value(value)));
                } else {
                    unsupported.push(json!(name));
                }
            } else if name == "goo_dye_density" {
                let legacy = self.call(
                    "wayfire/get-config-option",
                    json!({"option": "scottland/goo_dye_strength"}),
                )?;
                if let Some(value) = legacy.get("value") {
                    let number = number_value(value).ok_or_else(|| {
                        "Wayfire returned a non-numeric legacy dye value".to_string()
                    })?;
                    values.insert(name.to_string(), json!(number));
                } else {
                    unsupported.push(json!(name));
                }
            } else {
                unsupported.push(json!(name));
            }
        }
        values.insert("unsupported".to_string(), Value::Array(unsupported));
        Ok(Value::Object(values))
    }

    fn windows(&mut self) -> Result<()> {
        let state = self.call("scottland/layout-state", json!({}))?;
        for view in state
            .get("views")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
        {
            let id = view.get("id").and_then(Value::as_i64).unwrap_or_default();
            let app_id = view.get("app_id").and_then(Value::as_str).unwrap_or("");
            let zone = view.get("zone").and_then(Value::as_str).unwrap_or("");
            let scale = view
                .get("applied_scale")
                .and_then(Value::as_f64)
                .unwrap_or(1.0);
            let title = view.get("title").and_then(Value::as_str).unwrap_or("");
            let title: String = title.chars().take(60).collect();
            println!("{id:>5}  {app_id:32} {zone:11} {scale:.2}  {title}");
        }
        Ok(())
    }
}

pub fn run(args: &[String]) -> Result<u8> {
    let (command, args) = args
        .split_first()
        .map_or(("get", &[][..]), |(c, a)| (c.as_str(), a));
    match command {
        "-h" | "--help" | "help" if args.is_empty() => {
            println!("{HELP}");
            Ok(0)
        }
        "get" if args.is_empty() => print_json(Ctl::connect()?.config()?),
        "set" if args.len() == 2 && accepted_name(&args[0]) => {
            print_json(Ctl::connect()?.set_one(&args[0], json!(args[1]))?)
        }
        "option" if args.len() == 1 => {
            let mut ctl = Ctl::connect()?;
            let reply = ctl.call("wayfire/get-config-option", json!({"option": args[0]}))?;
            print_json(reply.get("value").unwrap_or(&reply).clone())
        }
        "present" if args.len() == 1 && args[0].bytes().all(|c| c.is_ascii_digit()) => {
            let id = args[0]
                .parse::<u64>()
                .map_err(|_| "window id is too large".to_string())?;
            let reply = Ctl::connect()?.call("scottland/present", json!({"window": id}))?;
            print_json(reply.clone())?;
            Ok(if reply.get("error").is_some() { 1 } else { 0 })
        }
        "windows" if args.is_empty() => {
            Ctl::connect()?.windows()?;
            Ok(0)
        }
        "stdin" if args.is_empty() => stdin_updates(),
        "terminal-cwd" if args.len() <= 1 => terminal_cwd(args.first().map(String::as_str)),
        "focus-app" if args.len() == 1 => focus_app(&args[0]),
        "session-locked" if args.is_empty() => session_locked(),
        "window-close-all" if args.is_empty() => window_close_all(),
        "lid-close" if args.is_empty() => lock_session(),
        "lock" if args.is_empty() => lock_session(),
        "sleep-lock" if args.len() <= 1 => sleep_lock(args.first().map(String::as_str)),
        "sleep-monitor" if args.is_empty() => sleep_monitor(None),
        "sleep-monitor" if args.len() == 1 && args[0] == "--inhibited" => {
            sleep_monitor(Some("inhibited"))
        }
        _ => Err(format!("invalid command or arguments\n{HELP}")),
    }
}

fn print_json(value: Value) -> Result<u8> {
    println!(
        "{}",
        serde_json::to_string(&value).map_err(|error| error.to_string())?
    );
    Ok(0)
}

fn accepted_name(name: &str) -> bool {
    CONFIG_NAMES.contains(&name) || name == "hint_avoidance_always" || name == "goo_dye_strength"
}

fn bool_value(value: &Value) -> bool {
    match value {
        Value::Bool(value) => *value,
        Value::Number(value) => value.as_i64() == Some(1),
        Value::String(value) => matches!(value.to_lowercase().as_str(), "true" | "1"),
        _ => false,
    }
}

fn number_value(value: &Value) -> Option<f64> {
    value
        .as_f64()
        .or_else(|| value.as_str()?.parse::<f64>().ok())
}

fn stdin_updates() -> Result<u8> {
    let stdin = io::stdin();
    let mut ctl = Ctl::connect()?;
    for line in stdin.lock().lines() {
        let line = line.map_err(|error| error.to_string())?;
        if line.trim() == "flush" {
            println!("flushed");
            io::stdout().flush().map_err(|error| error.to_string())?;
            continue;
        }
        if let Some(json) = line.strip_prefix("batch ") {
            let values: Value = serde_json::from_str(json).map_err(|error| error.to_string())?;
            let mut updates = Map::new();
            if let Some(values) = values.as_object() {
                for (name, value) in values {
                    if accepted_name(name) {
                        updates.extend(ctl.setting_updates(name, value.clone())?);
                    }
                }
            }
            let reply = ctl.call("wayfire/set-config-options", Value::Object(updates))?;
            if let Some(error) = reply.get("error").and_then(Value::as_str) {
                eprintln!("{error}");
                io::stderr().flush().map_err(|error| error.to_string())?;
            }
            continue;
        }
        let mut parts = line.trim().splitn(2, char::is_whitespace);
        let Some(name) = parts.next().filter(|part| !part.is_empty()) else {
            continue;
        };
        if accepted_name(name) {
            let value = parts.next().unwrap_or("").trim_start();
            let reply = ctl.set_one(name, json!(value))?;
            if let Some(error) = reply.get("error").and_then(Value::as_str) {
                eprintln!("{name}: {error}");
                io::stderr().flush().map_err(|error| error.to_string())?;
            }
        }
    }
    Ok(0)
}

fn terminal_cwd(pid_arg: Option<&str>) -> Result<u8> {
    let ctl = Ctl::connect()?;
    let cwd = scottland::terminal_cwd::resolve(&ctl.session, pid_arg.map(OsStr::new))
        .map_err(|error| {
            if error.kind() == io::ErrorKind::InvalidInput {
                "PID must be a positive integer".to_string()
            } else {
                error.to_string()
            }
        })?;
    println!("{}", cwd.display());
    Ok(0)
}

fn current_session() -> Result<Session> {
    let runtime = env::var_os("SCOTTLAND_SESSION_DIR")
        .filter(|path| !path.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(scottland::dirs::runtime_dir);
    let caller_socket = env::var_os("WAYFIRE_SOCKET");
    scottland::session::select(&runtime, None, caller_socket.as_deref())
        .map_err(|error| error.to_string())
}

fn focus_app(pattern: &str) -> Result<u8> {
    RegexBuilder::new(pattern)
        .case_insensitive(true)
        .build()
        .map_err(|error| format!("invalid app pattern: {error}"))?;
    let mut ctl = Ctl::connect()?;
    let state = ctl.call("scottland/layout-state", json!({}))?;
    let views = state
        .get("views")
        .and_then(Value::as_array)
        .ok_or_else(|| "Scottland returned no window list".to_string())?;
    let view = matching_view(views, pattern);
    let Some(id) = view.and_then(|view| view.get("id")).and_then(Value::as_u64) else {
        return Ok(1);
    };
    let reply = ctl.call("scottland/present", json!({"window": id}))?;
    if let Some(error) = reply.get("error") {
        return Err(format!("cannot focus matching window: {error}"));
    }
    Ok(0)
}

fn session_locked() -> Result<u8> {
    let Ok(mut ctl) = Ctl::connect() else {
        return Ok(2);
    };
    match ctl.call("scottland/session-state", json!({})) {
        Ok(reply) => match reply.get("locked").and_then(Value::as_bool) {
            Some(true) => Ok(0),
            Some(false) => Ok(1),
            None => Ok(2),
        },
        Err(_) => Ok(2),
    }
}

fn window_close_all() -> Result<u8> {
    let mut ctl = Ctl::connect()?;
    let state = ctl.call("scottland/layout-state", json!({}))?;
    let views = state
        .get("views")
        .and_then(Value::as_array)
        .ok_or_else(|| "Scottland returned no window list".to_string())?;
    let mut failures = Vec::new();
    for view in views {
        let Some(id) = view.get("id").and_then(Value::as_i64) else {
            continue;
        };
        match ctl.call("window-rules/close-view", json!({"id": id})) {
            Ok(reply) => {
                if let Some(error) = reply.get("error") {
                    failures.push(format!("window {id}: {error}"));
                }
            }
            Err(error) => failures.push(format!("window {id}: {error}")),
        }
    }
    if failures.is_empty() {
        Ok(0)
    } else {
        Err(failures.join("; "))
    }
}

fn lock_session() -> Result<u8> {
    let session = current_session()?;
    let id = session
        .var("XDG_SESSION_ID")
        .and_then(|value| value.to_str())
        .filter(|value| !value.is_empty())
        .ok_or_else(|| {
            "the selected Scottland session has no XDG_SESSION_ID; refusing to lock another session"
                .to_string()
        })?;
    let status = session_command(&session, "loginctl")
        .args(["lock-session", id])
        .status()
        .map_err(|error| format!("cannot request the session locker: {error}"))?;
    Ok(status_code(status))
}

fn sleep_lock(budget_arg: Option<&str>) -> Result<u8> {
    let session = current_session()?;
    session
        .var("XDG_SESSION_ID")
        .filter(|id| !id.is_empty())
        .ok_or_else(|| "the selected Scottland session has no XDG_SESSION_ID".to_string())?;
    let budget = budget_arg
        .and_then(|value| {
            value
                .parse::<u64>()
                .ok()
                .filter(|ms| (1..=BUDGET_CAP_MS).contains(ms))
        })
        .unwrap_or_else(|| derive_budget_ms(&session));
    let reserve = Instant::now() + Duration::from_millis(budget);
    let mut requested_at = None;
    loop {
        let remaining = reserve.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            eprintln!("scottland-ctl: session did not become secure within {budget}ms");
            return Ok(1);
        }
        if requested_at.is_none_or(|last: Instant| last.elapsed() >= Duration::from_millis(500)) {
            let _ = request_session_lock(&session, remaining.min(Duration::from_secs(1)));
            requested_at = Some(Instant::now());
        }
        match session_state(&session, remaining.min(Duration::from_millis(500))) {
            Ok(true) => return Ok(0),
            Ok(false) | Err(_) => {}
        }
        thread::sleep(remaining.min(Duration::from_millis(100)));
    }
}

fn derive_budget_ms(session: &Session) -> u64 {
    let mut command = session_command(session, "busctl");
    command.args([
        "get-property",
        "org.freedesktop.login1",
        "/org/freedesktop/login1",
        "org.freedesktop.login1.Manager",
        "InhibitDelayMaxUSec",
    ]);
    let output = bounded_command(command, Duration::from_secs(1));
    let delay_us = output
        .ok()
        .filter(|output| output.status.success())
        .and_then(|output| String::from_utf8(output.stdout).ok())
        .and_then(|text| text.split_ascii_whitespace().last()?.parse::<u64>().ok())
        .filter(|value| *value > 0)
        .unwrap_or(5_000_000);
    lock_budget_ms(delay_us)
}

fn request_session_lock(session: &Session, timeout: Duration) -> Result<()> {
    let id = session
        .var("XDG_SESSION_ID")
        .and_then(|value| value.to_str())
        .filter(|value| !value.is_empty())
        .ok_or_else(|| "the selected Scottland session has no XDG_SESSION_ID".to_string())?;
    let mut command = session_command(session, "loginctl");
    command.args(["lock-session", id]);
    let output = bounded_command(command, timeout)
        .map_err(|error| format!("failed to run the session locker: {error}"))?;
    if output.status.success() {
        Ok(())
    } else {
        Err("the session locker refused the lock request".to_string())
    }
}

fn session_state(session: &Session, timeout: Duration) -> Result<bool> {
    let path = session
        .wayfire_socket()
        .ok_or_else(|| "the selected session has no Wayfire socket".to_string())?;
    let mut socket = UnixStream::connect(path).map_err(|error| error.to_string())?;
    socket
        .set_read_timeout(Some(timeout))
        .map_err(|error| error.to_string())?;
    socket
        .set_write_timeout(Some(timeout))
        .map_err(|error| error.to_string())?;
    let body = serde_json::to_vec(&json!({"method":"scottland/session-state", "data":{}}))
        .map_err(|error| error.to_string())?;
    socket
        .write_all(&(body.len() as u32).to_le_bytes())
        .and_then(|_| socket.write_all(&body))
        .map_err(|error| error.to_string())?;
    let mut header = [0; 4];
    socket
        .read_exact(&mut header)
        .map_err(|error| error.to_string())?;
    let size = u32::from_le_bytes(header) as usize;
    if size > MAX_FRAME {
        return Err("Wayfire reply is too large".to_string());
    }
    let mut bytes = vec![0; size];
    socket
        .read_exact(&mut bytes)
        .map_err(|error| error.to_string())?;
    let reply: Value = serde_json::from_slice(&bytes).map_err(|error| error.to_string())?;
    reply
        .get("locked")
        .and_then(Value::as_bool)
        .ok_or_else(|| "session lock state is unknown".to_string())
}

fn sleep_monitor(mode: Option<&str>) -> Result<u8> {
    match mode {
        Some("inhibited") => {
            let mut child = session_command(
                &current_session()?,
                "dbus-monitor",
            )
            .args([
                "--system",
                "type='signal',sender='org.freedesktop.login1',interface='org.freedesktop.login1.Manager',member='PrepareForSleep'",
            ])
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|error| format!("cannot monitor logind sleep events: {error}"))?;
            let stdout = child
                .stdout
                .take()
                .ok_or_else(|| "cannot read logind events".to_string())?;
            let mut reader = io::BufReader::new(stdout);
            let mut line = String::new();
            loop {
                line.clear();
                let count = reader
                    .read_line(&mut line)
                    .map_err(|error| error.to_string())?;
                if count == 0 {
                    break;
                }
                if is_prepare_for_sleep(&line) {
                    let result = sleep_lock(None);
                    let _ = child.kill();
                    let _ = child.wait();
                    return result;
                }
            }
            let status = child.wait().map_err(|error| error.to_string())?;
            if status.success() {
                Ok(0)
            } else {
                Err("dbus-monitor exited before logind's sleep event".to_string())
            }
        }
        None => {
            let session = current_session()?;
            let exe = env::current_exe().map_err(|error| error.to_string())?;
            let mut command = session_command(&session, "systemd-inhibit");
            command
                .args([
                    "--what=sleep",
                    "--mode=delay",
                    "--who=Scottland",
                    "--why=Lock screen before suspend",
                ])
                .arg(exe)
                .args(["sleep-monitor", "--inhibited"]);
            let error = std::os::unix::process::CommandExt::exec(&mut command);
            Err(format!("cannot start the logind delay inhibitor: {error}"))
        }
        _ => Err("invalid sleep-monitor mode".to_string()),
    }
}

fn session_command(session: &Session, program: &str) -> Command {
    let executable = session_program(session, program).unwrap_or_else(|| PathBuf::from(program));
    let mut command = Command::new(executable);
    command.env_clear().envs(session.env.iter());
    command.stdin(Stdio::null()).stderr(Stdio::null());
    command
}

fn session_program(session: &Session, program: &str) -> Option<PathBuf> {
    let path = Path::new(program);
    if path.components().count() > 1 {
        return Some(path.to_path_buf());
    }
    let search = session.var("PATH")?;
    env::split_paths(search)
        .map(|directory| directory.join(program))
        .find(|candidate| {
            fs::metadata(candidate).is_ok_and(|metadata| {
                metadata.is_file() && metadata.permissions().mode() & 0o111 != 0
            })
        })
}

fn bounded_command(mut command: Command, timeout: Duration) -> io::Result<std::process::Output> {
    command.stdout(Stdio::piped());
    let mut child = command.spawn()?;
    let start = Instant::now();
    loop {
        if let Some(status) = child.try_wait()? {
            return collect_output(child, status);
        }
        if start.elapsed() >= timeout {
            let _ = child.kill();
            let _ = child.wait()?;
            return Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "command exceeded its time limit",
            ));
        }
        thread::sleep(Duration::from_millis(10).min(timeout.saturating_sub(start.elapsed())));
    }
}

fn collect_output(mut child: Child, status: ExitStatus) -> io::Result<std::process::Output> {
    let mut stdout = Vec::new();
    if let Some(mut pipe) = child.stdout.take() {
        pipe.read_to_end(&mut stdout)?;
    }
    Ok(std::process::Output {
        status,
        stdout,
        stderr: Vec::new(),
    })
}

fn status_code(status: ExitStatus) -> u8 {
    status
        .code()
        .and_then(|code| u8::try_from(code).ok())
        .unwrap_or(1)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn setting_aliases_are_accepted_and_canonicalized() {
        assert!(accepted_name("goo_dye_strength"));
        assert!(accepted_name("hint_avoidance_always"));
        assert!(!accepted_name("transparency"));
        assert_eq!(canonical_setting("goo_dye_strength"), "goo_dye_density");
        assert_eq!(
            canonical_setting("hint_avoidance_always"),
            "window_avoidance_always"
        );
    }

    #[test]
    fn boolean_and_numeric_config_values_match_the_python_ctl() {
        assert!(bool_value(&json!("TRUE")));
        assert!(bool_value(&json!(1)));
        assert!(!bool_value(&json!("yes")));
        assert_eq!(number_value(&json!("0.75")), Some(0.75));
        assert_eq!(number_value(&json!(false)), None);
    }

    #[test]
    fn matching_prefers_app_ids_then_titles_and_ignores_case() {
        let views = vec![
            json!({"id": 1, "app_id": "other", "title": "Slack"}),
            json!({"id": 2, "app_id": "org.SLACK.App", "title": "unrelated"}),
        ];
        assert_eq!(matching_view(&views, "slack").unwrap()["id"], 2);
        assert_eq!(matching_view(&views[..1], "slack").unwrap()["id"], 1);
    }

    #[test]
    fn lock_budget_reserves_at_least_one_second_and_caps_at_twelve() {
        assert_eq!(lock_budget_ms(5_000_000), 4_000);
        assert_eq!(lock_budget_ms(20_000_000), 12_000);
        assert_eq!(lock_budget_ms(500_000), 1);
    }

    #[test]
    fn sleep_event_matches_only_prepare_true_lines() {
        assert!(is_prepare_for_sleep("      boolean true"));
        assert!(!is_prepare_for_sleep("      boolean false"));
        assert!(!is_prepare_for_sleep("member=PrepareForSleep"));
    }
}

fn canonical_setting(name: &str) -> &str {
    match name {
        "hint_avoidance_always" => "window_avoidance_always",
        "goo_dye_strength" => "goo_dye_density",
        other => other,
    }
}

fn matching_view<'a>(views: &'a [Value], pattern: &str) -> Option<&'a Value> {
    let regex = RegexBuilder::new(pattern)
        .case_insensitive(true)
        .build()
        .ok()?;
    views
        .iter()
        .find(|view| {
            view.get("app_id")
                .and_then(Value::as_str)
                .is_some_and(|value| regex.is_match(value))
        })
        .or_else(|| {
            views.iter().find(|view| {
                view.get("title")
                    .and_then(Value::as_str)
                    .is_some_and(|value| regex.is_match(value))
            })
        })
}

fn lock_budget_ms(delay_us: u64) -> u64 {
    let window_ms = delay_us / 1_000;
    window_ms
        .saturating_sub((window_ms / 5).max(1_000))
        .min(BUDGET_CAP_MS)
        .max(1)
}

fn is_prepare_for_sleep(line: &str) -> bool {
    line.contains("boolean true")
}
