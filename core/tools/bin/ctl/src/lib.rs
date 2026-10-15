use regex::RegexBuilder;
use scottland::session::Session;
use serde_json::{Map, Value, json};
use std::env;
use std::ffi::OsStr;
use std::fs;
use std::io::{self, BufRead, Read, Write};
use std::mem;
use std::os::fd::{AsRawFd, FromRawFd};
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::UnixStream;
use std::os::unix::process::CommandExt;
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
  sender-window-snapshot                    print an unfiltered sender-window snapshot as JSON
  sender-window-provenance-snapshot         print the desktop model for sender-id lookup
  launch [--name N] [--sender ID] [--launcher L] [--knock K]
         [--writer W --note TEXT]... -- COMMAND...
                                           bind provenance and notes before exec
  focus-app REGEX                           focus the first window matching app id or title
  session-locked                            exit 0 locked, 1 unlocked, 2 unknown
  window-close-all                          close every open window

Session and power commands:
  lid-close                                 request the distro's session locker
  lock                                      request the distro's session locker
  sleep-lock [BUDGET_MS]                    lock and wait until Scottland reports secure
  sleep-monitor [--inhibited]               lock when logind prepares to suspend

Window notes commands:
  notes add --window <id|focused> --writer <name> <text>
  notes update <note-id> <text>
  notes remove <note-id>
  notes list [--window <id|focused>] [--writer <name>] [--json]

For add or update, use - as <text> to read stdin through EOF (one trailing newline is removed).";

const CONFIG_NAMES: &[&str] = &[
    "center_width",
    "rail_width",
    "min_scale",
    "max_scale",
    "scale_curve",
    "blend_width",
    "screen_zones",
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
    "agentd_hub_url",
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
const TEXT_NAMES: &[&str] = &[
    "scale_curve",
    "screen_zones",
    "goo_falloff",
    "attention_color_family",
    "agentd_hub_url",
];
const MAX_FRAME: usize = 16 * 1024 * 1024;
const BUDGET_CAP_MS: u64 = 12_000;
const SENDER_WINDOW_SNAPSHOT_SOCKET_TIMEOUT: Duration = Duration::from_secs(2);

type Result<T> = std::result::Result<T, String>;

struct Ctl {
    socket: UnixStream,
    dye_option: Option<&'static str>,
}

impl Ctl {
    fn connect() -> Result<Self> {
        Self::connect_to(&current_session()?)
    }

    fn connect_to(session: &Session) -> Result<Self> {
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
            socket,
            dye_option: None,
        })
    }

    fn call(&mut self, method: &str, data: Value) -> Result<Value> {
        call_socket(&mut self.socket, method, data)
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
                if name == "screen_zones" {
                    let zones = value
                        .as_str()
                        .and_then(|raw| serde_json::from_str::<Value>(raw).ok())
                        .filter(|parsed| parsed.is_array())
                        .unwrap_or_else(|| json!([]));
                    values.insert(name.to_string(), zones);
                } else if BOOL_NAMES.contains(&name) {
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
        let touchpad_speed = self.call(
            "wayfire/get-config-option",
            json!({"option": "input/touchpad_scroll_speed"}),
        )?;
        if let Some(value) = touchpad_speed.get("value") {
            let number = number_value(value).ok_or_else(|| {
                "Wayfire returned a non-numeric touchpad scroll speed".to_string()
            })?;
            values.insert("touchpad_scroll_speed".to_string(), json!(number));
        }
        let layout = self.call("scottland/layout-state", json!({}))?;
        values.insert(
            "outputs".to_string(),
            layout.get("outputs").cloned().unwrap_or_else(|| json!([])),
        );
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
        "notes" => notes_command(args),
        "launch" => launch_command(args),
        "sender-window-snapshot" if args.is_empty() => sender_window_snapshot(),
        "sender-window-provenance-snapshot" if args.is_empty() => {
            sender_window_provenance_snapshot()
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

fn launch_command(args: &[String]) -> Result<u8> {
    let (declaration, staged_notes, command_args) = parse_launch_args(args)?;
    let session = current_session()?;
    let mut ctl = Ctl::connect_to(&session)?;
    let reply = ctl.call("scottland/provenance-declare", declaration)?;
    if let Some(error) = reply.get("error") {
        return Err(error
            .as_str()
            .map(str::to_owned)
            .unwrap_or_else(|| error.to_string())
            .lines()
            .collect::<Vec<_>>()
            .join(" "));
    }
    let token = reply
        .get("token")
        .and_then(Value::as_str)
        .filter(|token| !token.is_empty())
        .ok_or_else(|| "Scottland returned no provenance token".to_string())?;

    for (writer, text) in staged_notes {
        let reply = call_notes(
            &mut ctl,
            json!({"action": "stage", "token": token, "writer": writer, "text": text}),
        )?;
        if reply
            .get("id")
            .and_then(Value::as_i64)
            .filter(|id| *id > 0)
            .is_none()
        {
            return Err("Scottland returned no note id for the staged note".to_string());
        }
    }

    let mut command = session_command(&session, &command_args[0]);
    command.args(&command_args[1..]);
    command.env("XDG_ACTIVATION_TOKEN", token);
    let error = command.exec();
    Err(format!("cannot execute launch command: {error}"))
}

fn parse_launch_args(args: &[String]) -> Result<(Value, Vec<(String, String)>, Vec<String>)> {
    let separator = args
        .iter()
        .position(|arg| arg == "--")
        .ok_or_else(|| "launch requires -- COMMAND...".to_string())?;
    let command_args = args[separator + 1..].to_vec();
    if command_args.is_empty() || command_args[0].is_empty() {
        return Err("launch requires a command after --".to_string());
    }

    let mut declaration = Map::new();
    declaration.insert("pid".to_string(), json!(std::process::id()));
    let mut staged_notes = Vec::new();
    let mut pending_writer: Option<String> = None;
    let options = &args[..separator];
    let mut index = 0;
    while index < options.len() {
        let option = options[index].as_str();
        index += 1;
        match option {
            "--name" | "--sender" | "--launcher" | "--knock" => {
                let value = options
                    .get(index)
                    .filter(|value| !value.is_empty())
                    .ok_or_else(|| format!("launch {option} requires a value"))?;
                let field = &option[2..];
                if declaration.contains_key(field) {
                    return Err(format!("launch accepts {option} once"));
                }
                declaration.insert(field.to_string(), json!(value));
                index += 1;
            }
            "--writer" => {
                if pending_writer.is_some() {
                    return Err("launch requires --note after each --writer".to_string());
                }
                let writer = options
                    .get(index)
                    .filter(|value| !value.is_empty())
                    .ok_or_else(|| "launch --writer requires a value".to_string())?;
                pending_writer = Some(writer.clone());
                index += 1;
            }
            "--note" => {
                let writer = pending_writer
                    .take()
                    .ok_or_else(|| "launch --note requires a preceding --writer".to_string())?;
                let text = options
                    .get(index)
                    .ok_or_else(|| "launch --note requires text".to_string())?;
                staged_notes.push((writer, text.clone()));
                index += 1;
            }
            _ => return Err(format!("unknown launch option {option}")),
        }
    }
    if pending_writer.is_some() {
        return Err("launch --writer requires --note".to_string());
    }

    Ok((Value::Object(declaration), staged_notes, command_args))
}

fn notes_command(args: &[String]) -> Result<u8> {
    let Some((action, args)) = args.split_first() else {
        return Err("expected notes add, update, remove, or list".to_string());
    };
    match action.as_str() {
        "add" => notes_add(args),
        "update" => notes_update(args),
        "remove" => notes_remove(args),
        "list" => notes_list(args),
        _ => Err("expected notes add, update, remove, or list".to_string()),
    }
}

fn notes_add(args: &[String]) -> Result<u8> {
    let mut window = None;
    let mut writer = None;
    let mut positional = Vec::new();
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--window" => {
                if window.is_some() {
                    return Err("notes add accepts --window once".to_string());
                }
                index += 1;
                window = Some(parse_note_window(
                    args.get(index).map(String::as_str).unwrap_or(""),
                )?);
            }
            "--writer" => {
                if writer.is_some() {
                    return Err("notes add accepts --writer once".to_string());
                }
                index += 1;
                let Some(value) = args.get(index) else {
                    return Err("notes add requires --writer <name>".to_string());
                };
                writer = Some(value.clone());
            }
            "--" => {
                positional.extend(args[index + 1..].iter().map(String::as_str));
                break;
            }
            option if option.starts_with("--") => {
                return Err(format!("unknown notes add option {option}"));
            }
            value => positional.push(value),
        }
        index += 1;
    }

    let window = window.ok_or_else(|| "notes add requires --window <id|focused>".to_string())?;
    let writer = writer.ok_or_else(|| "notes add requires --writer <name>".to_string())?;
    if positional.len() != 1 {
        return Err("notes add requires one text argument".to_string());
    }
    let text = read_note_text(positional[0])?;
    let mut ctl = Ctl::connect()?;
    let reply = call_notes(
        &mut ctl,
        json!({"action": "add", "window": window, "writer": writer, "text": text}),
    )?;
    let id = reply
        .get("id")
        .and_then(Value::as_i64)
        .filter(|id| *id > 0)
        .ok_or_else(|| "Wayfire returned no note id".to_string())?;
    println!("{id}");
    Ok(0)
}

fn notes_update(args: &[String]) -> Result<u8> {
    if args.len() != 2 {
        return Err("notes update requires <note-id> and one text argument".to_string());
    }
    let id = parse_positive_id(&args[0], "note-id must be a positive integer")?;
    let text = read_note_text(&args[1])?;
    let mut ctl = Ctl::connect()?;
    call_notes(
        &mut ctl,
        json!({"action": "update", "id": id, "text": text}),
    )?;
    Ok(0)
}

fn notes_remove(args: &[String]) -> Result<u8> {
    if args.len() != 1 {
        return Err("notes remove requires one <note-id>".to_string());
    }
    let id = parse_positive_id(&args[0], "note-id must be a positive integer")?;
    let mut ctl = Ctl::connect()?;
    call_notes(&mut ctl, json!({"action": "remove", "id": id}))?;
    Ok(0)
}

fn notes_list(args: &[String]) -> Result<u8> {
    let mut window = None;
    let mut writer = None;
    let mut json_output = false;
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--window" => {
                if window.is_some() {
                    return Err("notes list accepts --window once".to_string());
                }
                index += 1;
                window = Some(parse_note_window(
                    args.get(index).map(String::as_str).unwrap_or(""),
                )?);
            }
            "--writer" => {
                if writer.is_some() {
                    return Err("notes list accepts --writer once".to_string());
                }
                index += 1;
                let Some(value) = args.get(index) else {
                    return Err("notes list requires --writer <name>".to_string());
                };
                writer = Some(value.clone());
            }
            "--json" if !json_output => json_output = true,
            "--json" => return Err("notes list accepts --json once".to_string()),
            option => return Err(format!("unknown notes list option {option}")),
        }
        index += 1;
    }

    let mut request = json!({"action": "list"});
    let fields = request
        .as_object_mut()
        .ok_or_else(|| "could not build notes request".to_string())?;
    if let Some(window) = window {
        fields.insert("window".to_string(), window);
    }
    if let Some(writer) = writer {
        fields.insert("writer".to_string(), json!(writer));
    }

    let mut ctl = Ctl::connect()?;
    let reply = call_notes(&mut ctl, request)?;
    let windows = reply
        .get("windows")
        .filter(|windows| windows.is_array())
        .cloned()
        .ok_or_else(|| "Wayfire returned no notes windows".to_string())?;
    if json_output {
        return print_json(windows);
    }
    print!("{}", format_note_windows(&windows)?);
    Ok(0)
}

fn parse_note_window(value: &str) -> Result<Value> {
    if value == "focused" {
        return Ok(json!("focused"));
    }
    Ok(json!(parse_positive_id(
        value,
        "window must be a positive integer or 'focused'",
    )?))
}

fn parse_positive_id(value: &str, error: &str) -> Result<i64> {
    if value.is_empty() || !value.bytes().all(|byte| byte.is_ascii_digit()) {
        return Err(error.to_string());
    }
    value
        .parse::<i64>()
        .ok()
        .filter(|id| *id > 0)
        .ok_or_else(|| error.to_string())
}

fn read_note_text(value: &str) -> Result<String> {
    if value != "-" {
        return Ok(value.to_string());
    }
    let mut text = String::new();
    io::stdin()
        .read_to_string(&mut text)
        .map_err(|error| format!("cannot read note text from stdin: {error}"))?;
    if text.ends_with('\n') {
        text.pop();
    }
    Ok(text)
}

fn call_notes(ctl: &mut Ctl, request: Value) -> Result<Value> {
    let reply = ctl.call("scottland/notes", request)?;
    if let Some(error) = reply.get("error") {
        let message = error
            .as_str()
            .map(str::to_owned)
            .unwrap_or_else(|| error.to_string());
        return Err(message.lines().collect::<Vec<_>>().join(" "));
    }
    Ok(reply)
}

fn format_note_windows(windows: &Value) -> Result<String> {
    let windows = windows
        .as_array()
        .ok_or_else(|| "Wayfire returned invalid notes windows".to_string())?;
    let mut output = String::new();
    for window in windows {
        let window_id = window
            .get("id")
            .and_then(Value::as_i64)
            .filter(|id| *id > 0)
            .ok_or_else(|| "Wayfire returned an invalid notes window id".to_string())?;
        let notes = window
            .get("notes")
            .and_then(Value::as_array)
            .ok_or_else(|| "Wayfire returned invalid notes".to_string())?;
        for note in notes {
            let note_id = note
                .get("id")
                .and_then(Value::as_i64)
                .filter(|id| *id > 0)
                .ok_or_else(|| "Wayfire returned an invalid note id".to_string())?;
            let writer = note
                .get("writer")
                .and_then(Value::as_str)
                .ok_or_else(|| "Wayfire returned an invalid note writer".to_string())?;
            let text = note
                .get("text")
                .and_then(Value::as_str)
                .ok_or_else(|| "Wayfire returned invalid note text".to_string())?;
            output.push_str(&format!("{window_id} {note_id} {writer}\n"));
            for line in text.split('\n') {
                output.push_str("  ");
                output.push_str(line);
                output.push('\n');
            }
        }
    }
    Ok(output)
}

fn sender_window_snapshot() -> Result<u8> {
    let path = if let Some(path) = env::var_os("WAYFIRE_SOCKET").filter(|path| !path.is_empty()) {
        PathBuf::from(path)
    } else if env::var_os("SCOTTLAND_EXEC").is_some_and(|value| !value.is_empty()) {
        return Err("the selected session has no Wayfire socket".to_string());
    } else {
        return Err(Command::new("scottland-exec")
            .args(["--", "scottland-ctl", "sender-window-snapshot"])
            .exec()
            .to_string());
    };
    let mut socket = connect_socket(&path, SENDER_WINDOW_SNAPSHOT_SOCKET_TIMEOUT)
        .map_err(|error| format!("cannot connect to the selected session: {error}"))?;
    let reply = call_socket(&mut socket, "window-rules/list-views", json!({}))?;
    if let Some(error) = reply.get("error") {
        eprintln!(
            "{}",
            error
                .as_str()
                .map(str::to_owned)
                .unwrap_or_else(|| error.to_string())
        );
        return Ok(1);
    }
    print_json(reply)
}

fn sender_window_provenance_snapshot() -> Result<u8> {
    let path = if let Some(path) = env::var_os("WAYFIRE_SOCKET").filter(|path| !path.is_empty()) {
        PathBuf::from(path)
    } else if env::var_os("SCOTTLAND_EXEC").is_some_and(|value| !value.is_empty()) {
        return Err("the selected session has no Wayfire socket".to_string());
    } else {
        return Err(Command::new("scottland-exec")
            .args(["--", "scottland-ctl", "sender-window-provenance-snapshot"])
            .exec()
            .to_string());
    };
    let mut socket = connect_socket(&path, SENDER_WINDOW_SNAPSHOT_SOCKET_TIMEOUT)
        .map_err(|error| format!("cannot connect to the selected session: {error}"))?;
    let model = call_socket(
        &mut socket,
        "scottland/desktop-model",
        json!({"slice": "desktop"}),
    )?;
    if let Some(error) = model.get("error") {
        eprintln!(
            "{}",
            error
                .as_str()
                .map(str::to_owned)
                .unwrap_or_else(|| error.to_string())
        );
        return Ok(1);
    }
    let windows = model
        .get("windows")
        .and_then(Value::as_array)
        .ok_or_else(|| "Scottland desktop model has no windows array".to_string())?;
    let option = call_socket(
        &mut socket,
        "wayfire/get-config-option",
        json!({"option": "scottland/agentd_hub_url"}),
    )?;
    let hub_url = option
        .get("value")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let hub_url_configured = option.get("error").is_none() && option.get("value").is_some();
    print_json(json!({
        "windows": windows,
        "agentd_hub_url": hub_url,
        "agentd_hub_url_configured": hub_url_configured
    }))
}

fn call_socket(socket: &mut UnixStream, method: &str, data: Value) -> Result<Value> {
    let body = serde_json::to_vec(&json!({"method": method, "data": data}))
        .map_err(|error| error.to_string())?;
    if body.len() > MAX_FRAME {
        return Err("Wayfire request exceeds the frame size limit".to_string());
    }
    socket
        .write_all(&(body.len() as u32).to_le_bytes())
        .and_then(|_| socket.write_all(&body))
        .map_err(|error| format!("cannot send Wayfire request: {error}"))?;
    let mut header = [0; 4];
    socket
        .read_exact(&mut header)
        .map_err(|error| format!("cannot read Wayfire reply header: {error}"))?;
    let size = u32::from_le_bytes(header) as usize;
    if size > MAX_FRAME {
        return Err(format!("Wayfire reply is too large ({size} bytes)"));
    }
    let mut reply = vec![0; size];
    socket
        .read_exact(&mut reply)
        .map_err(|error| format!("cannot read Wayfire reply: {error}"))?;
    serde_json::from_slice(&reply).map_err(|error| format!("invalid Wayfire JSON reply: {error}"))
}

fn connect_socket(path: &Path, timeout: Duration) -> io::Result<UnixStream> {
    if timeout.is_zero() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "socket timeout must be positive",
        ));
    }
    let deadline = Instant::now() + timeout;
    let stream = connect_socket_until(path, deadline)?;
    stream.set_nonblocking(false)?;
    stream.set_read_timeout(Some(timeout))?;
    stream.set_write_timeout(Some(timeout))?;
    Ok(stream)
}

fn connect_socket_until(path: &Path, deadline: Instant) -> io::Result<UnixStream> {
    remaining_until(deadline)?;
    let path_bytes = path.as_os_str().as_bytes();
    if path_bytes.is_empty() || path_bytes.contains(&0) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "invalid Wayfire socket path",
        ));
    }

    #[cfg(target_os = "linux")]
    let stream = {
        let fd = unsafe {
            libc::socket(
                libc::AF_UNIX,
                libc::SOCK_STREAM | libc::SOCK_CLOEXEC | libc::SOCK_NONBLOCK,
                0,
            )
        };
        if fd < 0 {
            return Err(io::Error::last_os_error());
        }
        let stream = unsafe { UnixStream::from_raw_fd(fd) };
        let mut address: libc::sockaddr_un = unsafe { mem::zeroed() };
        if path_bytes.len() >= address.sun_path.len() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "Wayfire socket path is too long",
            ));
        }
        address.sun_family = libc::AF_UNIX as libc::sa_family_t;
        for (slot, byte) in address.sun_path.iter_mut().zip(path_bytes) {
            *slot = *byte as libc::c_char;
        }
        let address_len = (mem::offset_of!(libc::sockaddr_un, sun_path) + path_bytes.len() + 1)
            as libc::socklen_t;
        let result = unsafe {
            libc::connect(
                fd,
                (&address as *const libc::sockaddr_un).cast::<libc::sockaddr>(),
                address_len,
            )
        };
        if result != 0 {
            let error = io::Error::last_os_error();
            match error.raw_os_error() {
                Some(libc::EINPROGRESS) | Some(libc::EALREADY) | Some(libc::EAGAIN) => {
                    wait_for_socket_connect(&stream, deadline)?;
                }
                Some(libc::EISCONN) => {}
                _ => return Err(error),
            }
        }
        stream
    };

    #[cfg(not(target_os = "linux"))]
    let stream = {
        let stream = UnixStream::connect(path)?;
        stream.set_nonblocking(true)?;
        stream
    };

    remaining_until(deadline)?;
    Ok(stream)
}

#[cfg(target_os = "linux")]
fn wait_for_socket_connect(stream: &UnixStream, deadline: Instant) -> io::Result<()> {
    wait_for_socket_ready(stream, libc::POLLOUT, deadline)?;
    let mut socket_error: libc::c_int = 0;
    let mut len = mem::size_of_val(&socket_error) as libc::socklen_t;
    let result = unsafe {
        libc::getsockopt(
            stream.as_raw_fd(),
            libc::SOL_SOCKET,
            libc::SO_ERROR,
            (&mut socket_error as *mut libc::c_int).cast::<libc::c_void>(),
            &mut len,
        )
    };
    if result < 0 {
        return Err(io::Error::last_os_error());
    }
    if socket_error != 0 {
        return Err(io::Error::from_raw_os_error(socket_error));
    }
    remaining_until(deadline).map(|_| ())
}

fn remaining_until(deadline: Instant) -> io::Result<Duration> {
    let remaining = deadline.saturating_duration_since(Instant::now());
    if remaining.is_zero() {
        Err(io::Error::new(
            io::ErrorKind::TimedOut,
            "Wayfire socket operation exceeded its deadline",
        ))
    } else {
        Ok(remaining)
    }
}

fn wait_for_socket_ready(
    stream: &UnixStream,
    events: libc::c_short,
    deadline: Instant,
) -> io::Result<()> {
    loop {
        let remaining = remaining_until(deadline)?;
        let millis = remaining.as_millis()
            + if remaining.subsec_nanos() % 1_000_000 != 0 {
                1
            } else {
                0
            };
        let timeout_ms = millis.max(1).min(libc::c_int::MAX as u128) as libc::c_int;
        let mut descriptor = libc::pollfd {
            fd: stream.as_raw_fd(),
            events,
            revents: 0,
        };
        let ready = unsafe { libc::poll(&mut descriptor, 1, timeout_ms) };
        if ready < 0 {
            let error = io::Error::last_os_error();
            if error.kind() == io::ErrorKind::Interrupted {
                continue;
            }
            return Err(error);
        }
        if ready == 0 {
            return Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "Wayfire socket operation exceeded its deadline",
            ));
        }
        return Ok(());
    }
}

fn write_all_until(stream: &mut UnixStream, mut bytes: &[u8], deadline: Instant) -> io::Result<()> {
    while !bytes.is_empty() {
        remaining_until(deadline)?;
        match stream.write(bytes) {
            Ok(0) => {
                return Err(io::Error::new(
                    io::ErrorKind::WriteZero,
                    "Wayfire socket stopped accepting the session-state request",
                ));
            }
            Ok(written) => bytes = &bytes[written..],
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                wait_for_socket_ready(stream, libc::POLLOUT, deadline)?;
            }
            Err(error) => return Err(error),
        }
    }
    remaining_until(deadline).map(|_| ())
}

fn read_exact_until(
    stream: &mut UnixStream,
    mut bytes: &mut [u8],
    deadline: Instant,
) -> io::Result<()> {
    while !bytes.is_empty() {
        remaining_until(deadline)?;
        match stream.read(bytes) {
            Ok(0) => {
                return Err(io::Error::new(
                    io::ErrorKind::UnexpectedEof,
                    "Wayfire socket closed before the session-state reply was complete",
                ));
            }
            Ok(read) => bytes = &mut bytes[read..],
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                wait_for_socket_ready(stream, libc::POLLIN, deadline)?;
            }
            Err(error) => return Err(error),
        }
    }
    remaining_until(deadline).map(|_| ())
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
    let session = current_session()?;
    let cwd = terminal_cwd_path(&session, pid_arg.map(OsStr::new)).map_err(|error| {
        if error.kind() == io::ErrorKind::InvalidInput {
            "PID must be a positive integer".to_string()
        } else {
            error.to_string()
        }
    })?;
    println!("{}", cwd.display());
    Ok(0)
}

fn terminal_cwd_path(session: &Session, pid_arg: Option<&OsStr>) -> io::Result<PathBuf> {
    scottland::terminal_cwd::resolve(session, pid_arg)
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
    let session = current_session_for_sleep_lock()?;
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
    wait_for_session_secure(
        &session,
        Duration::from_millis(budget),
        Instant::now,
        request_session_lock,
        session_state,
    )
}

/// Selects the session from its recorded environment without probing Wayfire's IPC socket.
/// `sleep-lock` must request the login session lock before touching that socket: a full Wayfire
/// listen backlog can make a blocking `connect` wait until logind's delay-inhibitor window ends.
fn current_session_for_sleep_lock() -> Result<Session> {
    let runtime = env::var_os("SCOTTLAND_SESSION_DIR")
        .filter(|path| !path.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(scottland::dirs::runtime_dir);
    let caller_socket = env::var_os("WAYFIRE_SOCKET");
    let caller_id = env::var_os("XDG_SESSION_ID");
    select_recorded_sleep_session(&runtime, caller_socket.as_deref(), caller_id.as_deref())
}

fn select_recorded_sleep_session(
    runtime: &Path,
    caller_socket: Option<&OsStr>,
    caller_id: Option<&OsStr>,
) -> Result<Session> {
    let entries = fs::read_dir(runtime)
        .map_err(|error| format!("cannot read recorded Scottland sessions: {error}"))?;
    let mut sessions = Vec::new();
    for entry in entries {
        let entry =
            entry.map_err(|error| format!("cannot read recorded Scottland session: {error}"))?;
        let file_name = entry.file_name();
        let bytes = file_name.as_bytes();
        let Some(name) = bytes.strip_suffix(b".env") else {
            continue;
        };
        if name.is_empty() || name.starts_with(b".") {
            continue;
        }
        let Ok(session_env) = scottland::session::read_env(&entry.path()) else {
            continue;
        };
        if session_env
            .get(OsStr::new("WAYFIRE_SOCKET"))
            .is_none_or(|socket| socket.is_empty())
            || session_env
                .get(OsStr::new("XDG_SESSION_ID"))
                .is_none_or(|id| id.is_empty())
        {
            continue;
        }
        sessions.push(Session {
            name: String::from_utf8_lossy(name).into_owned(),
            env: session_env,
        });
    }
    sessions.sort_by(|left, right| left.name.cmp(&right.name));

    if let Some(socket) = caller_socket.filter(|socket| !socket.is_empty()) {
        return sessions
            .into_iter()
            .find(|session| session.var("WAYFIRE_SOCKET") == Some(socket))
            .ok_or_else(|| {
                "the caller's Wayfire socket is not a recorded Scottland session".to_string()
            });
    }
    if let Some(id) = caller_id.filter(|id| !id.is_empty()) {
        if let Some(session) = sessions
            .iter()
            .find(|session| session.var("XDG_SESSION_ID") == Some(id))
        {
            return Ok(session.clone());
        }
    }
    match sessions.len() {
        0 => Err("no recorded Scottland session can be selected for sleep-lock".to_string()),
        1 => Ok(sessions.remove(0)),
        _ => Err(format!(
            "several Scottland sessions are recorded ({}); run sleep-lock inside a selected session",
            sessions
                .into_iter()
                .map(|session| session.name)
                .collect::<Vec<_>>()
                .join(", ")
        )),
    }
}

fn wait_for_session_secure<Now, RequestLock, SessionState>(
    session: &Session,
    budget: Duration,
    mut now: Now,
    mut request_lock: RequestLock,
    mut session_state: SessionState,
) -> Result<u8>
where
    Now: FnMut() -> Instant,
    RequestLock: FnMut(&Session, Duration) -> Result<()>,
    SessionState: FnMut(&Session, Instant) -> Result<bool>,
{
    let deadline = now() + budget;
    let mut requested_at = None;
    loop {
        let remaining = deadline.saturating_duration_since(now());
        if remaining.is_zero() {
            return session_lock_timeout(budget);
        }
        if requested_at.is_none_or(|last: Instant| {
            now().saturating_duration_since(last) >= Duration::from_millis(500)
        }) {
            let _ = request_lock(session, remaining.min(Duration::from_secs(1)));
            requested_at = Some(now());
        }
        if deadline.saturating_duration_since(now()).is_zero() {
            return session_lock_timeout(budget);
        }
        match session_state(session, deadline) {
            Ok(true) if now() < deadline => return Ok(0),
            Ok(true) => return session_lock_timeout(budget),
            Ok(false) | Err(_) => {}
        }
        let remaining = deadline.saturating_duration_since(now());
        if remaining.is_zero() {
            return session_lock_timeout(budget);
        }
        thread::sleep(remaining.min(Duration::from_millis(100)));
    }
}

fn session_lock_timeout(budget: Duration) -> Result<u8> {
    eprintln!(
        "scottland-ctl: session did not become secure within {}ms",
        budget.as_millis()
    );
    Ok(1)
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

fn session_state(session: &Session, deadline: Instant) -> Result<bool> {
    let path = session
        .wayfire_socket()
        .ok_or_else(|| "the selected session has no Wayfire socket".to_string())?;
    let mut socket = connect_socket_until(path, deadline).map_err(|error| error.to_string())?;
    let body = serde_json::to_vec(&json!({"method":"scottland/session-state", "data":{}}))
        .map_err(|error| error.to_string())?;
    write_all_until(&mut socket, &(body.len() as u32).to_le_bytes(), deadline)
        .and_then(|_| write_all_until(&mut socket, &body, deadline))
        .map_err(|error| error.to_string())?;
    let mut header = [0; 4];
    read_exact_until(&mut socket, &mut header, deadline).map_err(|error| error.to_string())?;
    let size = u32::from_le_bytes(header) as usize;
    if size > MAX_FRAME {
        return Err("Wayfire reply is too large".to_string());
    }
    let mut bytes = vec![0; size];
    read_exact_until(&mut socket, &mut bytes, deadline).map_err(|error| error.to_string())?;
    remaining_until(deadline).map_err(|error| error.to_string())?;
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
    use std::cell::Cell;
    use std::os::unix::net::UnixListener;
    use std::sync::atomic::{AtomicU64, Ordering};

    static NEXT_SOCKET: AtomicU64 = AtomicU64::new(0);

    struct TestDir(PathBuf);

    impl TestDir {
        fn new(path: PathBuf) -> Self {
            let _ = fs::remove_dir_all(&path);
            fs::create_dir_all(&path).expect("create private test directory");
            fs::set_permissions(&path, fs::Permissions::from_mode(0o700))
                .expect("restrict private test directory");
            Self(path)
        }
    }

    impl Drop for TestDir {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

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
    fn sleep_lock_selects_a_recorded_session_without_connecting_to_wayfire() {
        let id = NEXT_SOCKET.fetch_add(1, Ordering::Relaxed);
        let root = TestDir::new(
            Path::new("/tmp").join(format!("sct-lock-select-{}-{id}", std::process::id())),
        );
        let runtime = root.0.join("runtime");
        fs::create_dir_all(&runtime).expect("create private runtime directory");
        let socket_path = root.0.join("wayfire.sock");
        let listener = UnixListener::bind(&socket_path).expect("bind unaccepting Wayfire socket");
        listener
            .set_nonblocking(true)
            .expect("make listener observation nonblocking");
        fs::write(
            runtime.join("wayland-1.env"),
            format!(
                "XDG_SESSION_ID=test-session\0WAYFIRE_SOCKET={}\0",
                socket_path.display()
            ),
        )
        .expect("record selected session environment");

        let socket_arg = OsStr::new(socket_path.as_os_str());
        let session = select_recorded_sleep_session(&runtime, Some(socket_arg), None)
            .expect("select the caller's recorded session without probing Wayfire");
        assert_eq!(session.name, "wayland-1");
        assert_eq!(
            session.var("XDG_SESSION_ID"),
            Some(OsStr::new("test-session"))
        );
        assert!(matches!(
            listener.accept(),
            Err(error) if error.kind() == io::ErrorKind::WouldBlock
        ));
    }

    #[test]
    fn sleep_lock_does_not_reuse_budget_consumed_by_the_locker_request() {
        let start = Instant::now();
        let now = Cell::new(start);
        let state_calls = Cell::new(0);
        let session = Session {
            name: "test-session".to_string(),
            env: scottland::session::Environment::new(),
        };

        let result = wait_for_session_secure(
            &session,
            Duration::from_millis(10),
            || now.get(),
            |_, timeout| {
                now.set(start + timeout + Duration::from_nanos(1));
                Ok(())
            },
            |_, _| {
                state_calls.set(state_calls.get() + 1);
                Ok(true)
            },
        );

        assert_eq!(result, Ok(1));
        assert_eq!(
            state_calls.get(),
            0,
            "expired budget must not accept a late lock state"
        );
    }

    #[test]
    fn session_state_applies_one_deadline_to_the_entire_framed_reply() {
        let id = NEXT_SOCKET.fetch_add(1, Ordering::Relaxed);
        let root =
            TestDir::new(Path::new("/tmp").join(format!("sct-lock-{}-{id}", std::process::id())));
        let socket_path = root.0.join("wayfire.sock");
        let listener = UnixListener::bind(&socket_path).expect("bind fake Wayfire socket");
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("accept session-state query");
            let mut request_header = [0; 4];
            stream
                .read_exact(&mut request_header)
                .expect("read session-state request header");
            let request_size = u32::from_le_bytes(request_header) as usize;
            let mut request = vec![0; request_size];
            stream
                .read_exact(&mut request)
                .expect("read session-state request");

            let reply = br#"{"locked":true}"#;
            stream
                .write_all(&(reply.len() as u32).to_le_bytes())
                .expect("write session-state reply header");
            for byte in reply {
                if stream.write_all(&[*byte]).is_err() {
                    break;
                }
                thread::sleep(Duration::from_millis(10));
            }
        });
        let mut env = scottland::session::Environment::new();
        env.insert("WAYFIRE_SOCKET".into(), socket_path.as_os_str().to_owned());
        let session = Session {
            name: "test-session".to_string(),
            env,
        };

        let result = session_state(&session, Instant::now() + Duration::from_millis(75));

        assert!(
            result
                .as_ref()
                .is_err_and(|error| error.contains("deadline")),
            "a fragmented reply completed after the total deadline: {result:?}"
        );
        server.join().expect("fake Wayfire server panicked");
    }

    #[test]
    fn terminal_cwd_uses_the_selected_home_without_a_second_wayfire_connection() {
        let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .ancestors()
            .nth(4)
            .expect("ctl crate lives below the repository root")
            .to_path_buf();
        let home = repo
            .join("build")
            .join(format!("ctl-terminal-cwd-home-{}", std::process::id()));
        let home = TestDir::new(home);
        let mut env = scottland::session::Environment::new();
        env.insert("HOME".into(), home.0.as_os_str().to_owned());
        let session = Session {
            name: "test-session".to_string(),
            env,
        };

        assert_eq!(
            terminal_cwd_path(&session, Some(OsStr::new("4000000000"))).unwrap(),
            home.0
        );
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

#[cfg(all(test, target_os = "linux"))]
mod sender_window_snapshot_timeout_tests {
    use super::*;
    use std::os::unix::net::UnixListener;
    use std::sync::atomic::{AtomicU64, Ordering};

    static NEXT_DIR: AtomicU64 = AtomicU64::new(0);

    struct TimeoutFixture {
        root: PathBuf,
        socket_root: PathBuf,
    }

    impl TimeoutFixture {
        fn new() -> Self {
            let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .ancestors()
                .nth(4)
                .expect("ctl crate lives below repository root")
                .to_path_buf();
            let id = NEXT_DIR.fetch_add(1, Ordering::Relaxed);
            let root = repo
                .join("build")
                .join(format!("ctl-snapshot-timeout-{}-{id}", std::process::id()));
            fs::create_dir_all(&root).expect("create timeout fixture directory");
            fs::set_permissions(&root, fs::Permissions::from_mode(0o700))
                .expect("restrict timeout fixture permissions");
            let socket_root =
                Path::new("/tmp").join(format!("sct-timeout-{}-{id}", std::process::id()));
            fs::create_dir(&socket_root).expect("create short-lived timeout socket directory");
            fs::set_permissions(&socket_root, fs::Permissions::from_mode(0o700))
                .expect("restrict timeout socket directory permissions");
            Self { root, socket_root }
        }
    }

    impl Drop for TimeoutFixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.socket_root);
            let _ = fs::remove_dir_all(&self.root);
        }
    }

    #[test]
    fn snapshot_connection_sets_two_second_read_and_write_timeouts() {
        let fixture = TimeoutFixture::new();
        let socket_path = fixture.socket_root.join("wayfire.sock");
        let listener = UnixListener::bind(&socket_path).expect("bind timeout fixture socket");
        let acceptor = thread::spawn(move || listener.accept().expect("accept timeout fixture"));
        let stream = connect_socket(&socket_path, SENDER_WINDOW_SNAPSHOT_SOCKET_TIMEOUT)
            .expect("connect with snapshot timeout");
        let (server, _) = acceptor.join().expect("timeout fixture acceptor panicked");
        assert_eq!(
            SENDER_WINDOW_SNAPSHOT_SOCKET_TIMEOUT,
            Duration::from_secs(2)
        );

        for option in [libc::SO_RCVTIMEO, libc::SO_SNDTIMEO] {
            let mut value: libc::timeval = unsafe { mem::zeroed() };
            let mut len = mem::size_of_val(&value) as libc::socklen_t;
            let result = unsafe {
                libc::getsockopt(
                    stream.as_raw_fd(),
                    libc::SOL_SOCKET,
                    option,
                    (&mut value as *mut libc::timeval).cast::<libc::c_void>(),
                    &mut len,
                )
            };
            assert_eq!(result, 0, "read back socket timeout option {option}");
            assert_eq!(value.tv_sec, 2, "socket timeout option {option}");
            assert_eq!(value.tv_usec, 0, "socket timeout option {option}");
        }

        drop(stream);
        drop(server);
    }
}
