use regex::RegexBuilder;
use scottland::session::{self, Environment, Session, SessionError};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::env;
use std::ffi::{OsStr, OsString};
use std::fs;
use std::io::{self, Read, Write};
use std::os::unix::ffi::{OsStrExt, OsStringExt};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::UnixStream;
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::Duration;

const USAGE: &str = "\
usage:
  scottland-launch browser [--private] [URL...]
  scottland-launch editor [--inline] [PATH...]
  scottland-launch floating-terminal-with-presentation COMMAND [ARGS...]
  scottland-launch --tui [--app-id=ID] COMMAND [ARGS...]
  scottland-launch --focus PATTERN [LAUNCH-COMMAND]
  scottland-launch --focus --tui [--app-id=ID] COMMAND [ARGS...]
  scottland-launch --cwd files
  scottland-launch --cwd terminal [--pid=PID] [COMMAND [ARGS...]]
  scottland-launch --restart APP [ARGS...]";

#[derive(Debug)]
pub struct LaunchError {
    pub status: u8,
    pub message: String,
}

impl LaunchError {
    fn usage(message: impl Into<String>) -> Self {
        Self {
            status: 2,
            message: format!("{}\n{USAGE}", message.into()),
        }
    }

    fn failed(message: impl Into<String>) -> Self {
        Self {
            status: 1,
            message: message.into(),
        }
    }
}

#[derive(Debug)]
enum Action {
    Help,
    Browser(Vec<OsString>),
    Editor {
        inline: bool,
        args: Vec<OsString>,
    },
    FloatingTerminal(Vec<OsString>),
    Tui {
        app_id: Option<String>,
        command: Vec<OsString>,
    },
    Focus {
        pattern: String,
        command: Option<OsString>,
    },
    FocusTui {
        app_id: Option<String>,
        command: Vec<OsString>,
    },
    CwdFiles,
    CwdTerminal {
        pid: Option<OsString>,
        command: Vec<OsString>,
    },
    Restart {
        app: OsString,
        args: Vec<OsString>,
    },
}

pub fn run(args: Vec<OsString>) -> Result<(), LaunchError> {
    execute(parse(args)?)
}

fn parse(args: Vec<OsString>) -> Result<Action, LaunchError> {
    let Some(first) = args.first() else {
        return Err(LaunchError::usage("a launch action is required"));
    };
    let first = plain(first)?;
    let rest = &args[1..];
    match first {
        "-h" | "--help" | "help" if rest.is_empty() => Ok(Action::Help),
        "browser" => Ok(Action::Browser(rest.to_vec())),
        "editor" => {
            let inline = rest.first().is_some_and(|arg| arg == "--inline");
            Ok(Action::Editor {
                inline,
                args: if inline {
                    rest[1..].to_vec()
                } else {
                    rest.to_vec()
                },
            })
        }
        "floating-terminal-with-presentation" if !rest.is_empty() => {
            Ok(Action::FloatingTerminal(rest.to_vec()))
        }
        "floating-terminal-with-presentation" => Err(LaunchError::usage(
            "floating-terminal-with-presentation requires a command",
        )),
        "--tui" => {
            let (app_id, command) = parse_tui_args(rest)?;
            Ok(Action::Tui { app_id, command })
        }
        "--focus" => {
            if rest.first().is_some_and(|arg| arg == "--tui") {
                let (app_id, command) = parse_tui_args(&rest[1..])?;
                Ok(Action::FocusTui { app_id, command })
            } else {
                let Some(pattern) = rest.first() else {
                    return Err(LaunchError::usage("--focus requires a window pattern"));
                };
                Ok(Action::Focus {
                    pattern: plain(pattern)?.to_owned(),
                    command: rest.get(1).filter(|command| !command.is_empty()).cloned(),
                })
            }
        }
        "--cwd" => match rest.first().and_then(|arg| arg.to_str()) {
            Some("files") if rest.len() == 1 => Ok(Action::CwdFiles),
            Some("terminal") => {
                let mut i = 1;
                let mut pid = None;
                if let Some(value) = rest.get(i).and_then(|arg| arg.to_str())
                    && let Some(value) = value.strip_prefix("--pid=")
                {
                    pid = Some(OsString::from(value));
                    i += 1;
                }
                Ok(Action::CwdTerminal {
                    pid,
                    command: rest[i..].to_vec(),
                })
            }
            _ => Err(LaunchError::usage("--cwd expects 'files' or 'terminal'")),
        },
        "--restart" => {
            let Some(app) = rest.first() else {
                return Err(LaunchError::usage("--restart requires an application name"));
            };
            if app.is_empty() {
                return Err(LaunchError::usage("--restart requires an application name"));
            }
            Ok(Action::Restart {
                app: app.clone(),
                args: rest[1..].to_vec(),
            })
        }
        _ => Err(LaunchError::usage(format!("unknown action {first:?}"))),
    }
}

fn parse_tui_args(args: &[OsString]) -> Result<(Option<String>, Vec<OsString>), LaunchError> {
    let (app_id, command) = if let Some(id) = args
        .first()
        .and_then(|arg| arg.to_str())
        .and_then(|arg| arg.strip_prefix("--app-id="))
    {
        (Some(id.to_owned()), args[1..].to_vec())
    } else {
        (None, args.to_vec())
    };
    if command.is_empty() {
        return Err(LaunchError::usage("--tui requires a command"));
    }
    Ok((app_id, command))
}

fn plain(value: &OsStr) -> Result<&str, LaunchError> {
    value
        .to_str()
        .ok_or_else(|| LaunchError::usage("action and option names must be UTF-8"))
}

fn execute(action: Action) -> Result<(), LaunchError> {
    match action {
        Action::Help => {
            println!("{USAGE}");
            Ok(())
        }
        Action::Browser(args) => browser(args),
        Action::Editor { inline, args } => editor(inline, args),
        Action::FloatingTerminal(args) => floating_terminal(args),
        Action::Tui { app_id, command } => {
            let session = optional_session()?;
            exec_tui(session.as_ref(), app_id.as_deref(), &command)
        }
        Action::Focus { pattern, command } => {
            let session = focus_session()?;
            if focus_pattern(Some(&session), &pattern)? {
                return Ok(());
            }
            if let Some(command) = command {
                exec_shell_command(Some(&session), &command)
            } else {
                exec_with_setsid(Some(&session), OsStr::new(&pattern), &[])
            }
        }
        Action::FocusTui { app_id, command } => {
            let session = focus_session()?;
            let id = app_id.unwrap_or_else(|| default_app_id(&command[0]));
            if focus_pattern(Some(&session), &id_pattern(&id))? {
                return Ok(());
            }
            exec_tui(Some(&session), Some(&id), &command)
        }
        Action::CwdFiles => {
            let session = optional_session()?;
            let cwd = terminal_cwd(session.as_ref(), None)?;
            spawn_detached(
                session.as_ref(),
                OsStr::new("xdg-open"),
                &[cwd.into_os_string()],
            )
        }
        Action::CwdTerminal { pid, command } => {
            let session = optional_session()?;
            let cwd = terminal_cwd(session.as_ref(), pid)?;
            terminal_exec(
                session.as_ref(),
                "org.scottland.terminal",
                "Scottland Terminal",
                Some(&cwd),
                &command,
            )
        }
        Action::Restart { app, args } => restart_app(app, args),
    }
}

#[derive(Debug)]
struct DesktopEntry {
    id: String,
    path: PathBuf,
    name: String,
    icon: Option<String>,
    exec: Option<String>,
    working_directory: Option<PathBuf>,
    terminal: bool,
    dbus_activatable: bool,
}

fn browser(args: Vec<OsString>) -> Result<(), LaunchError> {
    let session = optional_session()?;
    let id = xdg_selection(
        session.as_ref(),
        "xdg-settings",
        &["get", "default-web-browser"],
        "default browser",
    )?;
    let entry = desktop_entry(session.as_ref(), &id)?;
    let private = args.iter().any(|arg| arg == "--private");
    let urls = args
        .into_iter()
        .filter(|arg| arg != "--private")
        .collect::<Vec<_>>();
    if private {
        let private_arg = supported_private_arg(&entry)?;
        let mut argv = desktop_argv(&entry, &urls)?;
        let Some(program) = argv.first().cloned() else {
            return Err(LaunchError::failed("default browser Exec entry is empty"));
        };
        argv.insert(1, private_arg);
        spawn_detached_in(
            session.as_ref(),
            &program,
            &argv[1..],
            entry.working_directory.as_deref(),
        )?;
    } else {
        launch_desktop_entry(session.as_ref(), &entry, &urls, false, None)?;
    }
    if !urls.is_empty()
        && let Some(session) = session.as_ref()
    {
        focus_regex(Some(session), &browser_window_pattern(&entry)?)?;
    }
    Ok(())
}

fn xdg_selection(
    session: Option<&Session>,
    tool: &str,
    args: &[&str],
    description: &str,
) -> Result<String, LaunchError> {
    let args = args.iter().map(OsString::from).collect::<Vec<_>>();
    let output = command_output(session, OsStr::new(tool), &args)
        .map_err(|error| LaunchError::failed(format!("cannot query the {description}: {error}")))?;
    if !output.status.success() {
        return Err(LaunchError::failed(format!(
            "{tool} could not query the {description} (status {})",
            output.status
        )));
    }
    let id = String::from_utf8_lossy(&output.stdout).trim().to_owned();
    if !valid_desktop_id(&id) {
        return Err(LaunchError::failed(format!(
            "{tool} returned an invalid {description} desktop ID"
        )));
    }
    Ok(id)
}

fn valid_desktop_id(id: &str) -> bool {
    !id.is_empty()
        && id.ends_with(".desktop")
        && id.len() > ".desktop".len()
        && id != "."
        && id != ".."
        && !id.contains('/')
        && !id.contains('\\')
        && !id.chars().any(char::is_control)
}

fn desktop_entry(session: Option<&Session>, id: &str) -> Result<DesktopEntry, LaunchError> {
    if !valid_desktop_id(id) {
        return Err(LaunchError::failed("invalid desktop entry ID"));
    }
    let env = effective_env(session);
    let home = env_value(&env, "HOME")
        .map(PathBuf::from)
        .ok_or_else(|| LaunchError::failed("HOME is not set"))?;
    let data_home = env_value(&env, "XDG_DATA_HOME")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .filter(|path| path.is_absolute())
        .unwrap_or_else(|| home.join(".local/share"));
    let mut data_dirs = env_value(&env, "XDG_DATA_DIRS")
        .filter(|value| !value.is_empty())
        .map(|value| {
            env::split_paths(&value)
                .filter(|path| path.is_absolute())
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    if data_dirs.is_empty() {
        data_dirs = env::split_paths(OsStr::new("/usr/local/share:/usr/share")).collect();
    }
    let mut dirs = vec![data_home];
    dirs.extend(data_dirs);
    let path = dirs
        .iter()
        .map(|dir| dir.join("applications").join(id))
        .find(|path| path.is_file())
        .ok_or_else(|| LaunchError::failed(format!("no desktop entry for {id:?}")))?;
    let contents = fs::read_to_string(&path)
        .map_err(|error| LaunchError::failed(format!("cannot read {}: {error}", path.display())))?;
    let mut values = BTreeMap::new();
    let mut in_entry = false;
    for line in contents.lines() {
        if line.starts_with('[') && line.ends_with(']') {
            in_entry = line == "[Desktop Entry]";
            continue;
        }
        if in_entry && !line.is_empty() && !line.starts_with('#') {
            if let Some((key, value)) = line.split_once('=') {
                let key = key.trim();
                let value = value.trim_start_matches([' ', '\t']);
                values.insert(key.to_owned(), decode_desktop_string(value));
            }
        }
    }
    if values.get("Type").map(String::as_str) != Some("Application") {
        return Err(LaunchError::failed(format!(
            "desktop entry {id:?} is not an application"
        )));
    }
    if desktop_boolean(&values, "Hidden", false)? {
        return Err(LaunchError::failed(format!(
            "desktop entry {id:?} is hidden"
        )));
    }
    let working_directory = values
        .get("Path")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from);
    if working_directory
        .as_ref()
        .is_some_and(|path| !path.is_absolute() || !path.is_dir())
    {
        return Err(LaunchError::failed(format!(
            "desktop entry {id:?} has an invalid working directory"
        )));
    }
    Ok(DesktopEntry {
        id: id.to_owned(),
        path,
        name: values
            .get("Name")
            .cloned()
            .unwrap_or_else(|| id.trim_end_matches(".desktop").to_owned()),
        icon: values
            .get("Icon")
            .filter(|value| !value.is_empty())
            .cloned(),
        exec: values.get("Exec").cloned(),
        working_directory,
        terminal: desktop_boolean(&values, "Terminal", false)?,
        dbus_activatable: desktop_boolean(&values, "DBusActivatable", false)?,
    })
}

fn decode_desktop_string(input: &str) -> String {
    let mut output = String::new();
    let mut chars = input.chars();
    while let Some(ch) = chars.next() {
        if ch != '\\' {
            output.push(ch);
            continue;
        }
        match chars.next() {
            Some('s') => output.push(' '),
            Some('n') => output.push('\n'),
            Some('t') => output.push('\t'),
            Some('r') => output.push('\r'),
            Some('\\') => output.push('\\'),
            Some(other) => {
                output.push('\\');
                output.push(other);
            }
            None => output.push('\\'),
        }
    }
    output
}

fn desktop_boolean(
    values: &BTreeMap<String, String>,
    key: &str,
    default: bool,
) -> Result<bool, LaunchError> {
    match values.get(key).map(String::as_str) {
        None => Ok(default),
        Some("true") => Ok(true),
        Some("false") => Ok(false),
        Some(_) => Err(LaunchError::failed(format!(
            "invalid {key} value in desktop entry"
        ))),
    }
}

fn desktop_argv(entry: &DesktopEntry, inputs: &[OsString]) -> Result<Vec<OsString>, LaunchError> {
    let exec = entry.exec.as_deref().ok_or_else(|| {
        LaunchError::failed(format!("{} has no Exec entry", entry.path.display()))
    })?;
    let words = split_desktop_exec(exec).map_err(|error| {
        LaunchError::failed(format!(
            "invalid Exec entry in {}: {error}",
            entry.path.display()
        ))
    })?;
    if words.is_empty() {
        return Err(LaunchError::failed(format!(
            "{} has an empty Exec entry",
            entry.path.display()
        )));
    }
    let mut output = Vec::new();
    let mut file_code_seen = false;
    for word in words {
        if word == "%i" {
            if let Some(icon) = &entry.icon {
                output.extend([OsString::from("--icon"), OsString::from(icon)]);
            }
            continue;
        }
        if word.contains("%F") || word.contains("%U") {
            if word != "%F" && word != "%U" {
                return Err(LaunchError::failed(
                    "%F and %U must be standalone Exec arguments",
                ));
            }
            if file_code_seen {
                return Err(LaunchError::failed(
                    "Exec contains more than one file or URL field code",
                ));
            }
            file_code_seen = true;
            output.extend(inputs.iter().cloned());
            continue;
        }
        let mut expanded = OsString::new();
        let mut chars = word.chars().peekable();
        let mut remove_argument = false;
        while let Some(ch) = chars.next() {
            if ch != '%' {
                expanded.push(ch.to_string());
                continue;
            }
            let code = chars
                .next()
                .ok_or_else(|| LaunchError::failed("Exec ends with an incomplete field code"))?;
            match code {
                '%' => expanded.push("%"),
                'c' => expanded.push(&entry.name),
                'k' => expanded.push(entry.path.as_os_str()),
                'f' | 'u' => {
                    if file_code_seen {
                        return Err(LaunchError::failed(
                            "Exec contains more than one file or URL field code",
                        ));
                    }
                    file_code_seen = true;
                    if inputs.len() > 1 {
                        return Err(LaunchError::failed(format!(
                            "Exec uses %{code} but received multiple items"
                        )));
                    }
                    if let Some(input) = inputs.first() {
                        expanded.push(input);
                    }
                }
                'd' | 'D' | 'n' | 'N' | 'v' | 'm' => {
                    if word == format!("%{code}") {
                        remove_argument = true;
                    }
                }
                other => {
                    return Err(LaunchError::failed(format!(
                        "Exec contains unsupported field code %{other}"
                    )));
                }
            }
        }
        if !remove_argument && (!expanded.is_empty() || word.is_empty()) {
            output.push(expanded);
        }
    }
    if !inputs.is_empty() && !file_code_seen {
        return Err(LaunchError::failed(
            "desktop entry Exec does not accept the requested files or URLs",
        ));
    }
    Ok(output)
}

fn split_desktop_exec(input: &str) -> Result<Vec<String>, &'static str> {
    let mut words = Vec::new();
    let mut word = String::new();
    let mut quoted = false;
    let mut escaped = false;
    let mut started = false;
    let mut chars = input.chars().peekable();
    while let Some(ch) = chars.next() {
        if escaped {
            if !matches!(ch, '"' | '`' | '$' | '\\') {
                return Err("invalid escape in quoted argument");
            }
            word.push(ch);
            escaped = false;
            started = true;
            continue;
        }
        if quoted && ch == '\\' {
            escaped = true;
            continue;
        }
        if quoted && ch == '%' && chars.peek().is_some_and(|next| next.is_ascii_alphabetic()) {
            return Err("field codes must not be used inside quoted arguments");
        }
        if ch == '"' {
            quoted = !quoted;
            started = true;
            continue;
        }
        if !quoted && ch.is_whitespace() {
            if started {
                words.push(std::mem::take(&mut word));
                started = false;
            }
            continue;
        }
        if !quoted && "'\\<>~|&;$*?#()`".contains(ch) {
            return Err("reserved characters must be quoted");
        }
        word.push(ch);
        started = true;
    }
    if escaped || quoted {
        return Err("unterminated quote or escape");
    }
    if started {
        words.push(word);
    }
    Ok(words)
}

fn launch_desktop_entry(
    session: Option<&Session>,
    entry: &DesktopEntry,
    inputs: &[OsString],
    inline: bool,
    terminal_context: Option<(&str, &str)>,
) -> Result<(), LaunchError> {
    if entry.terminal && !inline {
        let argv = desktop_argv(entry, inputs)?;
        let Some((program, args)) = argv.split_first() else {
            return Err(LaunchError::failed("desktop entry command is empty"));
        };
        let (app_id, title) =
            terminal_context.unwrap_or(("org.scottland.text_editor", &entry.name));
        let command = std::iter::once(program.clone())
            .chain(args.iter().cloned())
            .collect::<Vec<_>>();
        return terminal_exec(
            session,
            app_id,
            title,
            entry.working_directory.as_deref(),
            &command,
        );
    }
    if entry.dbus_activatable && !inline {
        let mut args = vec![OsString::from("launch"), entry.path.as_os_str().to_owned()];
        args.extend(inputs.iter().map(|input| file_argument_uri(input)));
        return activate_desktop_entry(session, entry, &args);
    }
    let argv = desktop_argv(entry, inputs)?;
    let Some((program, args)) = argv.split_first() else {
        return Err(LaunchError::failed("desktop entry command is empty"));
    };
    spawn_detached_in(session, program, args, entry.working_directory.as_deref())
}

fn file_argument_uri(input: &OsStr) -> OsString {
    let path = Path::new(input);
    if !path.exists() && has_uri_scheme(input.as_bytes()) {
        return input.to_owned();
    }
    let absolute = if path.is_absolute() {
        path.to_path_buf()
    } else {
        env::current_dir()
            .unwrap_or_else(|_| PathBuf::from("/"))
            .join(path)
    };
    let mut uri = String::from("file://");
    for byte in absolute.as_os_str().as_bytes() {
        if byte.is_ascii_alphanumeric() || b"/-._~".contains(byte) {
            uri.push(*byte as char);
        } else {
            uri.push_str(&format!("%{byte:02X}"));
        }
    }
    OsString::from(uri)
}

fn has_uri_scheme(input: &[u8]) -> bool {
    let Some(colon) = input.iter().position(|byte| *byte == b':') else {
        return false;
    };
    colon > 0
        && input[0].is_ascii_alphabetic()
        && input[1..colon]
            .iter()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'+' | b'.' | b'-'))
}

fn supported_private_arg(entry: &DesktopEntry) -> Result<OsString, LaunchError> {
    let words = entry
        .exec
        .as_deref()
        .ok_or_else(|| {
            LaunchError::failed("private mode is unsupported: browser has no Exec entry")
        })
        .and_then(|exec| {
            split_desktop_exec(exec).map_err(|error| {
                LaunchError::failed(format!("invalid browser Exec entry: {error}"))
            })
        })?;
    let executable = words
        .first()
        .map(|word| {
            Path::new(word)
                .file_name()
                .unwrap_or(OsStr::new(word))
                .to_string_lossy()
                .to_ascii_lowercase()
        })
        .unwrap_or_default();
    let flag = match executable.as_str() {
        "firefox" | "firefox-esr" | "librewolf" => "--private-window",
        "chromium"
        | "chromium-browser"
        | "google-chrome"
        | "google-chrome-stable"
        | "brave"
        | "brave-browser" => "--incognito",
        "microsoft-edge" | "microsoft-edge-stable" => "--inprivate",
        _ => {
            return Err(LaunchError::failed(format!(
                "private mode is unsupported for selected browser {:?}",
                entry.id
            )));
        }
    };
    Ok(OsString::from(flag))
}

fn browser_window_pattern(entry: &DesktopEntry) -> Result<String, LaunchError> {
    let executable = entry
        .exec
        .as_deref()
        .and_then(|exec| split_desktop_exec(exec).ok())
        .and_then(|words| words.first().cloned())
        .map(|word| {
            Path::new(&word)
                .file_name()
                .unwrap_or(OsStr::new(&word))
                .to_string_lossy()
                .into_owned()
        })
        .unwrap_or_default();
    let desktop_stem = entry.id.trim_end_matches(".desktop");
    let alternatives = [desktop_stem.to_owned(), executable]
        .into_iter()
        .filter(|value| !value.is_empty())
        .map(|value| regex::escape(&value))
        .collect::<BTreeSet<_>>();
    Ok(format!(
        r"\b(?:{})\b",
        alternatives.into_iter().collect::<Vec<_>>().join("|")
    ))
}

fn split_words(input: &str) -> Result<Vec<String>, &'static str> {
    #[derive(Clone, Copy, PartialEq, Eq)]
    enum Quote {
        None,
        Single,
        Double,
    }
    let mut words = Vec::new();
    let mut word = String::new();
    let mut quote = Quote::None;
    let mut escaped = false;
    let mut started = false;
    for ch in input.chars() {
        if escaped {
            word.push(ch);
            escaped = false;
            started = true;
            continue;
        }
        match (quote, ch) {
            (Quote::None, '\\') | (Quote::Double, '\\') => escaped = true,
            (Quote::None, '\'') => {
                quote = Quote::Single;
                started = true;
            }
            (Quote::None, '"') => {
                quote = Quote::Double;
                started = true;
            }
            (Quote::Single, '\'') | (Quote::Double, '"') => quote = Quote::None,
            (Quote::None, ch) if ch.is_whitespace() => {
                if started {
                    words.push(std::mem::take(&mut word));
                    started = false;
                }
            }
            (_, ch) => {
                word.push(ch);
                started = true;
            }
        }
    }
    if escaped || quote != Quote::None {
        return Err("unterminated quote or escape");
    }
    if started {
        words.push(word);
    }
    Ok(words)
}

fn editor(inline: bool, args: Vec<OsString>) -> Result<(), LaunchError> {
    let session = optional_session()?;
    let env = effective_env(session.as_ref());
    let configured = ["VISUAL", "EDITOR"]
        .into_iter()
        .find_map(|name| env_value(&env, name).filter(|value| !value.is_empty()));
    if let Some(configured) = configured {
        let configured = configured.to_string_lossy();
        let mut command = split_words(&configured)
            .map_err(|error| LaunchError::failed(format!("invalid editor command: {error}")))?
            .into_iter()
            .map(OsString::from)
            .collect::<Vec<_>>();
        let Some(program) = command.first().cloned() else {
            return Err(LaunchError::failed("configured editor command is empty"));
        };
        if !command_exists(&program, &env) {
            return Err(LaunchError::failed(format!(
                "configured editor executable {program:?} was not found"
            )));
        }
        let basename = Path::new(&program)
            .file_name()
            .unwrap_or(program.as_os_str())
            .to_string_lossy()
            .to_ascii_lowercase();
        let tui_editors = BTreeSet::from(["nvim", "vim", "nano", "micro", "hx", "helix", "fresh"]);
        command.push(OsString::from("--"));
        command.extend(args);
        if tui_editors.contains(basename.as_str()) && !inline {
            terminal_exec(
                session.as_ref(),
                &default_app_id(&program),
                &format!("Scottland {basename}"),
                None,
                &command,
            )
        } else {
            exec_with_setsid(session.as_ref(), &program, &command[1..])
        }
    } else {
        let id = xdg_selection(
            session.as_ref(),
            "xdg-mime",
            &["query", "default", "text/plain"],
            "text editor",
        )?;
        let entry = desktop_entry(session.as_ref(), &id)?;
        launch_desktop_entry(
            session.as_ref(),
            &entry,
            &args,
            inline,
            Some(("org.scottland.text_editor", &entry.name)),
        )
    }
}

fn floating_terminal(args: Vec<OsString>) -> Result<(), LaunchError> {
    if args.is_empty() {
        return Err(LaunchError::usage(
            "floating-terminal-with-presentation requires a command",
        ));
    }
    let session = optional_session()?;
    const PRESENTATION: &str = "printf 'Scottland\\n'; \"$@\"; status=$?; if [ \"$status\" -ne 130 ]; then printf '\\nCommand exited with status %s. Press Enter to close.' \"$status\"; IFS= read -r _; fi; exit \"$status\"";
    let command = std::iter::once(OsString::from("/bin/sh"))
        .chain([
            OsString::from("-c"),
            OsString::from(PRESENTATION),
            OsString::from("scottland-presentation"),
        ])
        .chain(args)
        .collect::<Vec<_>>();
    terminal_exec(
        session.as_ref(),
        "org.scottland.floating_terminal",
        "Scottland",
        None,
        &command,
    )
}

fn exec_tui(
    session: Option<&Session>,
    app_id: Option<&str>,
    command: &[OsString],
) -> Result<(), LaunchError> {
    let Some(program) = command.first() else {
        return Err(LaunchError::usage("--tui requires a command"));
    };
    let id = app_id
        .map(str::to_owned)
        .unwrap_or_else(|| default_app_id(program));
    let title = format!(
        "Scottland {}",
        Path::new(program)
            .file_name()
            .unwrap_or(program)
            .to_string_lossy()
    );
    terminal_exec(session, &id, &title, None, command)
}

fn terminal_exec(
    session: Option<&Session>,
    app_id: &str,
    title: &str,
    directory: Option<&Path>,
    command: &[OsString],
) -> Result<(), LaunchError> {
    let mut args = vec![
        OsString::from(format!("--app-id={app_id}")),
        OsString::from(format!("--title={title}")),
    ];
    if let Some(directory) = directory {
        args.push(OsString::from(format!("--dir={}", directory.display())));
    }
    if !command.is_empty() {
        args.push(OsString::from("-e"));
        args.extend_from_slice(command);
    }
    exec_program(session, OsStr::new("xdg-terminal-exec"), &args)
}

fn default_app_id(command: &OsStr) -> String {
    let basename = Path::new(command)
        .file_name()
        .unwrap_or(command)
        .to_string_lossy()
        .chars()
        .map(|ch| {
            if ch.is_ascii_alphanumeric() || ch == '_' {
                ch
            } else {
                '_'
            }
        })
        .collect::<String>();
    let basename = basename.trim_matches('_');
    let basename = if basename.is_empty() { "app" } else { basename };
    let basename = if basename
        .chars()
        .next()
        .is_some_and(|ch| ch.is_ascii_digit())
    {
        format!("app_{basename}")
    } else {
        basename.to_owned()
    };
    format!("org.scottland.{basename}")
}

fn id_pattern(app_id: &str) -> String {
    format!(r"\b{}\b", regex::escape(app_id))
}

fn focus_pattern(session: Option<&Session>, pattern: &str) -> Result<bool, LaunchError> {
    let bounded_pattern = format!(r"\b{pattern}\b");
    focus_regex(session, &bounded_pattern)
}

fn focus_regex(session: Option<&Session>, pattern: &str) -> Result<bool, LaunchError> {
    let regex = RegexBuilder::new(pattern)
        .case_insensitive(true)
        .build()
        .map_err(|error| LaunchError::usage(format!("invalid window pattern: {error}")))?;
    let Some(session) = session else {
        return Ok(false);
    };
    let state = wayfire_call(session, "scottland/desktop-model", json!({}))?;
    let Some(windows) = state.get("windows").and_then(Value::as_array) else {
        return Ok(false);
    };
    for window in windows {
        let app = window
            .get("app_id")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let title = window
            .get("title")
            .and_then(Value::as_str)
            .unwrap_or_default();
        if regex.is_match(app) || regex.is_match(title) {
            let Some(id) = window.get("id").and_then(Value::as_u64) else {
                continue;
            };
            let reply = wayfire_call(session, "scottland/present", json!({"window": id}))?;
            if reply.get("error").is_some() {
                return Err(LaunchError::failed(format!(
                    "could not focus window {id}: {reply}"
                )));
            }
            return Ok(true);
        }
    }
    Ok(false)
}

fn focus_session() -> Result<Session, LaunchError> {
    match session::current(None) {
        Ok(session) => Ok(session),
        Err(SessionError::NoneRunning) => Err(LaunchError::failed(
            "this action requires a running Scottland session",
        )),
        Err(error) => Err(LaunchError::failed(format!(
            "cannot select Scottland session: {error}"
        ))),
    }
}

fn wayfire_call(session: &Session, method: &str, data: Value) -> Result<Value, LaunchError> {
    let Some(socket) = session.wayfire_socket() else {
        return Err(LaunchError::failed(
            "the selected session has no Wayfire socket",
        ));
    };
    let mut stream = UnixStream::connect(socket)
        .map_err(|error| LaunchError::failed(format!("cannot connect to Wayfire: {error}")))?;
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(2)));
    let body = serde_json::to_vec(&json!({"method": method, "data": data}))
        .map_err(|error| LaunchError::failed(format!("cannot encode Wayfire request: {error}")))?;
    let len = u32::try_from(body.len())
        .map_err(|_| LaunchError::failed("Wayfire request is too large"))?;
    stream
        .write_all(&len.to_le_bytes())
        .and_then(|_| stream.write_all(&body))
        .map_err(|error| LaunchError::failed(format!("cannot send Wayfire request: {error}")))?;
    let mut header = [0u8; 4];
    stream
        .read_exact(&mut header)
        .map_err(|error| LaunchError::failed(format!("cannot read Wayfire response: {error}")))?;
    let len = u32::from_le_bytes(header) as usize;
    let mut response = vec![0u8; len];
    stream
        .read_exact(&mut response)
        .map_err(|error| LaunchError::failed(format!("cannot read Wayfire response: {error}")))?;
    let value: Value = serde_json::from_slice(&response)
        .map_err(|error| LaunchError::failed(format!("invalid Wayfire response: {error}")))?;
    if value.get("error").is_some() {
        return Err(LaunchError::failed(format!(
            "Wayfire rejected {method}: {value}"
        )));
    }
    Ok(value)
}

fn terminal_cwd(
    session: Option<&Session>,
    requested_pid: Option<OsString>,
) -> Result<PathBuf, LaunchError> {
    scottland::terminal_cwd::resolve_optional(session, requested_pid.as_deref()).map_err(|error| {
        if error.kind() == io::ErrorKind::InvalidInput {
            LaunchError::usage("--pid must be a process ID greater than 1")
        } else {
            LaunchError::failed(format!("cannot resolve terminal directory: {error}"))
        }
    })
}

fn proc_uid(status: &Path) -> Option<u32> {
    let text = fs::read_to_string(status).ok()?;
    text.lines()
        .find_map(|line| {
            line.strip_prefix("Uid:")
                .and_then(|values| values.split_whitespace().next())
        })?
        .parse()
        .ok()
}

fn restart_app(app: OsString, args: Vec<OsString>) -> Result<(), LaunchError> {
    let session = optional_session()?;
    let uid = proc_uid(Path::new("/proc/self/status"))
        .ok_or_else(|| LaunchError::failed("cannot determine the current user ID for restart"))?;
    let process_name = Path::new(&app)
        .file_name()
        .unwrap_or(app.as_os_str())
        .to_owned();
    let mut kill = Command::new("pkill");
    kill.args([
        OsString::from("-u"),
        OsString::from(uid.to_string()),
        OsString::from("-x"),
        OsString::from("--"),
    ]);
    kill.arg(escape_posix_ere(&process_name));
    apply_session_env(&mut kill, session.as_ref());
    let status = kill.status().map_err(|error| {
        LaunchError::failed(format!("cannot stop {}: {error}", app.to_string_lossy()))
    })?;
    if status.code().is_some_and(|code| code > 1) || status.code().is_none() {
        return Err(LaunchError::failed(format!(
            "cannot stop {} (status {status})",
            app.to_string_lossy()
        )));
    }
    let mut relaunch = Command::new(&app);
    relaunch.args(&args);
    relaunch
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    apply_session_env(&mut relaunch, session.as_ref());
    unsafe {
        relaunch.pre_exec(|| {
            if libc::setsid() == -1 {
                return Err(io::Error::last_os_error());
            }
            Ok(())
        });
    }
    relaunch.spawn().map(|_| ()).map_err(|error| {
        LaunchError::failed(format!(
            "cannot relaunch {}: {error}",
            app.to_string_lossy()
        ))
    })
}

fn escape_posix_ere(value: &OsStr) -> OsString {
    let mut escaped = Vec::with_capacity(value.as_bytes().len());
    for byte in value.as_bytes() {
        if b".^$*+?()|[]{}\\".contains(byte) {
            escaped.push(b'\\');
        }
        escaped.push(*byte);
    }
    OsString::from_vec(escaped)
}

fn exec_with_setsid(
    session: Option<&Session>,
    program: &OsStr,
    args: &[OsString],
) -> Result<(), LaunchError> {
    let mut command = Command::new("setsid");
    command.arg("--").arg(program).args(args);
    apply_session_env(&mut command, session);
    let error = command.exec();
    Err(LaunchError {
        status: 126,
        message: format!("cannot run {}: {error}", program.to_string_lossy()),
    })
}

fn spawn_detached(
    session: Option<&Session>,
    program: &OsStr,
    args: &[OsString],
) -> Result<(), LaunchError> {
    spawn_detached_in(session, program, args, None)
}

fn spawn_detached_in(
    session: Option<&Session>,
    program: &OsStr,
    args: &[OsString],
    working_directory: Option<&Path>,
) -> Result<(), LaunchError> {
    let mut command = Command::new(program);
    command.args(args);
    if let Some(directory) = working_directory {
        command.current_dir(directory);
    }
    command
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    apply_session_env(&mut command, session);
    unsafe {
        command.pre_exec(|| {
            if libc::setsid() == -1 {
                return Err(io::Error::last_os_error());
            }
            Ok(())
        });
    }
    command.spawn().map(|_| ()).map_err(|error| {
        LaunchError::failed(format!(
            "cannot start {}: {error}",
            program.to_string_lossy()
        ))
    })
}

fn activate_desktop_entry(
    session: Option<&Session>,
    entry: &DesktopEntry,
    args: &[OsString],
) -> Result<(), LaunchError> {
    let mut command = Command::new("gio");
    command
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    if let Some(directory) = entry.working_directory.as_deref() {
        command.current_dir(directory);
    }
    apply_session_env(&mut command, session);
    unsafe {
        command.pre_exec(|| {
            if libc::setsid() == -1 {
                return Err(io::Error::last_os_error());
            }
            Ok(())
        });
    }
    let status = command
        .status()
        .map_err(|error| LaunchError::failed(format!("cannot activate {}: {error}", entry.id)))?;
    if !status.success() {
        return Err(LaunchError::failed(format!(
            "desktop activation failed for {} (status {status})",
            entry.id
        )));
    }
    Ok(())
}

fn exec_program(
    session: Option<&Session>,
    program: &OsStr,
    args: &[OsString],
) -> Result<(), LaunchError> {
    let mut command = Command::new(program);
    command.args(args);
    apply_session_env(&mut command, session);
    let error = command.exec();
    Err(LaunchError {
        status: 126,
        message: format!("cannot run {}: {error}", program.to_string_lossy()),
    })
}

fn exec_shell_command(session: Option<&Session>, command: &OsStr) -> Result<(), LaunchError> {
    let mut shell = OsString::from("exec setsid ");
    shell.push(command);
    exec_shell(session, &shell, &[])
}

fn exec_shell(
    session: Option<&Session>,
    script: &OsStr,
    args: &[OsString],
) -> Result<(), LaunchError> {
    let mut command = Command::new("bash");
    command.arg("-c").arg(script).arg("scottland-launch");
    command.args(args);
    apply_session_env(&mut command, session);
    let error = command.exec();
    Err(LaunchError {
        status: 126,
        message: format!("cannot start shell command: {error}"),
    })
}

fn optional_session() -> Result<Option<Session>, LaunchError> {
    match session::current(None) {
        Ok(session) => Ok(Some(session)),
        Err(SessionError::NoneRunning) => Ok(None),
        Err(error) => Err(LaunchError::failed(format!(
            "cannot select Scottland session: {error}"
        ))),
    }
}

fn apply_session_env(command: &mut Command, session: Option<&Session>) {
    if let Some(session) = session {
        command.env_clear();
        command.envs(session.env.iter());
        command.env("SCOTTLAND_EXEC", "1");
    }
}

fn effective_env(session: Option<&Session>) -> Environment {
    session
        .map(|session| session.env.clone())
        .unwrap_or_else(|| env::vars_os().collect())
}

fn env_value(env: &Environment, key: &str) -> Option<OsString> {
    env.get(OsStr::new(key)).cloned()
}

fn command_output(
    session: Option<&Session>,
    program: &OsStr,
    args: &[OsString],
) -> io::Result<std::process::Output> {
    let mut command = Command::new(program);
    command.args(args);
    apply_session_env(&mut command, session);
    command.output()
}

fn command_exists(program: &OsStr, env: &Environment) -> bool {
    let path = Path::new(program);
    if path.components().count() > 1 {
        return executable_file(path);
    }
    let Some(search) = env_value(env, "PATH") else {
        return false;
    };
    env::split_paths(&search).any(|dir| executable_file(&dir.join(program)))
}

fn executable_file(path: &Path) -> bool {
    fs::metadata(path).is_ok_and(|meta| meta.is_file() && meta.permissions().mode() & 0o111 != 0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn desktop_exec_rejects_unknown_and_quoted_field_codes() {
        let entry = DesktopEntry {
            id: "browser.desktop".to_owned(),
            path: PathBuf::from("/tmp/browser.desktop"),
            name: "Browser".to_owned(),
            icon: None,
            exec: Some("browser \"%u\"".to_owned()),
            working_directory: None,
            terminal: false,
            dbus_activatable: false,
        };
        assert!(desktop_argv(&entry, &[OsString::from("https://example.test")]).is_err());
        let entry = DesktopEntry {
            exec: Some("browser %Q".to_owned()),
            ..entry
        };
        assert!(desktop_argv(&entry, &[]).is_err());
    }

    #[test]
    fn desktop_file_uri_conversion_preserves_non_file_schemes() {
        assert_eq!(
            file_argument_uri(OsStr::new("mailto:user@example.test")),
            "mailto:user@example.test"
        );
        assert_eq!(
            file_argument_uri(OsStr::new("https://example.test/path")),
            "https://example.test/path"
        );
        assert!(has_uri_scheme(b"file:///tmp/a%20b"));
        assert!(!has_uri_scheme(b"relative/path"));
    }

    #[test]
    fn generated_app_ids_use_valid_components() {
        assert_eq!(
            default_app_id(OsStr::new("some-command")),
            "org.scottland.some_command"
        );
        assert_eq!(
            default_app_id(OsStr::new("/path/123 editor")),
            "org.scottland.app_123_editor"
        );
        assert_eq!(default_app_id(OsStr::new("/path/---")), "org.scottland.app");
    }
}
