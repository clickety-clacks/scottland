mod command;
mod desktop;
mod ipc;
mod mpris;
mod process;
mod recipe;
mod service;

use scottland::{dirs, session};
use serde_json::to_writer;
use std::env;
use std::ffi::{OsStr, OsString};
use std::io::{self, Write};
use std::path::PathBuf;
use std::process::ExitCode;

const USAGE: &str = "\
usage: scottland-window-name [--display DISPLAY] [--explain] [WINDOW_ID ...]

Name the selected session's windows from configured recipes, MPRIS metadata, or
the window title/app name floor. Prints one JSON object per requested window;
with no WINDOW_ID arguments it prints every open window.

Options:
  --display DISPLAY  select a recorded Scottland session
  --explain          include each recipe's match/failure reason
  -h, --help         show this help
  -V, --version      show the version";

struct Options {
    display: Option<String>,
    explain: bool,
    ids: Vec<u64>,
    help: bool,
    version: bool,
}

fn main() -> ExitCode {
    match run() {
        Ok(has_missing_window) if has_missing_window => ExitCode::FAILURE,
        Ok(_) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("scottland-window-name: {error}");
            ExitCode::from(1)
        }
    }
}

fn run() -> Result<bool, String> {
    let options = parse_args(env::args_os().skip(1))?;
    if options.help {
        println!("{USAGE}");
        return Ok(false);
    }
    if options.version {
        println!("scottland-window-name {}", env!("CARGO_PKG_VERSION"));
        return Ok(false);
    }

    let selected = session::current(options.display.as_deref())
        .map_err(|error| format!("cannot select a running Scottland session: {error}"))?;
    let socket = selected
        .wayfire_socket()
        .ok_or_else(|| "the selected session has no Wayfire IPC socket".to_string())?;
    let windows = ipc::read_windows(socket)?;
    let ids = if options.ids.is_empty() {
        windows.iter().map(|window| window.id).collect::<Vec<_>>()
    } else {
        options.ids
    };

    let desktop_index =
        desktop::Index::read_from(|name| selected.var(name).map(OsStr::to_os_string));
    let process_snapshot = process::Snapshot::read();
    let recipes = recipe::load(&recipe_directories(&selected));
    let outputs = service::name(
        &ids,
        &windows,
        &desktop_index,
        &process_snapshot,
        &recipes,
        selected.var("DBUS_SESSION_BUS_ADDRESS"),
        &selected.env,
        options.explain,
    );

    let stdout = io::stdout();
    let mut output = stdout.lock();
    for result in &outputs {
        to_writer(&mut output, &service::json_value(result, options.explain))
            .map_err(|error| format!("cannot encode window result: {error}"))?;
        output
            .write_all(b"\n")
            .map_err(|error| format!("cannot write window result: {error}"))?;
    }
    Ok(outputs.iter().any(|result| result.missing))
}

fn parse_args(args: impl IntoIterator<Item = OsString>) -> Result<Options, String> {
    let mut options = Options {
        display: None,
        explain: false,
        ids: Vec::new(),
        help: false,
        version: false,
    };
    let mut args = args.into_iter();
    let mut positional_only = false;
    while let Some(argument) = args.next() {
        if positional_only {
            options.ids.push(parse_id(&argument)?);
            continue;
        }
        let Some(argument_text) = argument.to_str() else {
            return Err("options and window IDs must be plain text".to_string());
        };
        match argument_text {
            "--" => positional_only = true,
            "-h" | "--help" => options.help = true,
            "-V" | "--version" => options.version = true,
            "--explain" => options.explain = true,
            "--display" => {
                let display = args
                    .next()
                    .ok_or_else(|| "--display requires a value".to_string())?;
                let display = display
                    .into_string()
                    .map_err(|_| "--display must be plain text".to_string())?;
                if display.is_empty() {
                    return Err("--display must not be empty".to_string());
                }
                options.display = Some(display);
            }
            option if option.starts_with('-') => {
                return Err(format!("unknown option {option:?}\n{USAGE}"));
            }
            _ => options.ids.push(parse_id(&argument)?),
        }
    }
    if options.help && options.version {
        return Err("--help and --version cannot be used together".to_string());
    }
    Ok(options)
}

fn parse_id(argument: &OsStr) -> Result<u64, String> {
    argument
        .to_str()
        .ok_or_else(|| "window IDs must be plain decimal numbers".to_string())?
        .parse::<u64>()
        .map_err(|_| format!("invalid window ID {argument:?}; expected an unsigned decimal number"))
}

fn recipe_directories(selected: &session::Session) -> Vec<PathBuf> {
    let mut directories = Vec::new();
    if let Some(config_home) = selected
        .var("XDG_CONFIG_HOME")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .or_else(|| {
            selected
                .var("HOME")
                .filter(|value| !value.is_empty())
                .map(|home| PathBuf::from(home).join(".config"))
        })
    {
        directories.push(config_home.join("scottland/window-names.d"));
    }

    directories.push(PathBuf::from("/usr/share/scottland/window-names.d"));
    directories.push(hooks_dir(selected).join("window-names.d"));
    directories
}

fn hooks_dir(selected: &session::Session) -> PathBuf {
    if let Some(hooks) = selected
        .var("SCOTTLAND_HOOKS")
        .filter(|value| !value.is_empty())
    {
        return PathBuf::from(hooks);
    }
    let data_home = selected
        .var("XDG_DATA_HOME")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .or_else(|| {
            selected
                .var("HOME")
                .filter(|value| !value.is_empty())
                .map(|home| PathBuf::from(home).join(".local/share"))
        });
    if let Some(data_home) = data_home {
        let dev = data_home.join("scottland/dev");
        if dev.join("libexec").is_dir() {
            return dev;
        }
    }
    PathBuf::from(dirs::PACKAGE_HOOKS)
}

#[cfg(test)]
mod tests {
    use super::{parse_args, recipe_directories};
    use scottland::session::{Environment, Session};
    use std::ffi::OsString;
    use std::path::PathBuf;

    #[test]
    fn parses_display_explain_and_multiple_window_ids() {
        let options = parse_args([
            OsString::from("--display"),
            OsString::from("wayland-2"),
            OsString::from("--explain"),
            OsString::from("17"),
            OsString::from("23"),
        ])
        .unwrap();
        assert_eq!(options.display.as_deref(), Some("wayland-2"));
        assert!(options.explain);
        assert_eq!(options.ids, [17, 23]);
    }

    #[test]
    fn recipe_directory_precedence_uses_the_selected_session_environment() {
        let environment = Environment::from([
            (OsString::from("HOME"), OsString::from("/home/test")),
            (OsString::from("XDG_CONFIG_HOME"), OsString::from("/cfg")),
            (OsString::from("XDG_DATA_HOME"), OsString::from("/data")),
            (
                OsString::from("XDG_DATA_DIRS"),
                OsString::from("/share:/usr/share"),
            ),
            (OsString::from("SCOTTLAND_HOOKS"), OsString::from("/hooks")),
        ]);
        let selected = Session {
            name: "wayland-1".to_string(),
            env: environment,
        };
        assert_eq!(
            recipe_directories(&selected),
            [
                PathBuf::from("/cfg/scottland/window-names.d"),
                PathBuf::from("/usr/share/scottland/window-names.d"),
                PathBuf::from("/hooks/window-names.d"),
            ]
        );
    }
}
