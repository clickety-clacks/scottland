mod command;
mod desktop;
mod invocation;
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
  --invocation-budget-ms MS  opt into a total naming budget
  --invocation-start-monotonic-ns NS  use the caller's CLOCK_MONOTONIC origin
  -h, --help         show this help
  -V, --version      show the version";

struct Options {
    display: Option<String>,
    explain: bool,
    ids: Vec<u64>,
    invocation_budget_ms: Option<u64>,
    invocation_start_ns: Option<u64>,
    help: bool,
    version: bool,
}

fn main() -> ExitCode {
    run()
}

fn run() -> ExitCode {
    let entry_ns = invocation::monotonic_ns().ok();
    let options = match parse_args(env::args_os().skip(1)) {
        Ok(options) => options,
        Err(error) => {
            eprintln!("scottland-window-name: {error}");
            return ExitCode::from(1);
        }
    };
    if options.help {
        println!("{USAGE}");
        return ExitCode::SUCCESS;
    }
    if options.version {
        println!("scottland-window-name {}", env!("CARGO_PKG_VERSION"));
        return ExitCode::SUCCESS;
    }

    let invocation = match options.invocation_budget_ms {
        Some(budget_ms) => {
            let entry_ns = match entry_ns {
                Some(entry_ns) => entry_ns,
                None => {
                    eprintln!("scottland-window-name: cannot read monotonic clock at entry");
                    return ExitCode::from(1);
                }
            };
            let start_ns = options.invocation_start_ns.unwrap_or(entry_ns);
            match invocation::Invocation::new(budget_ms, start_ns, entry_ns) {
                Ok(invocation) => Some(invocation),
                Err(error) => {
                    eprintln!("scottland-window-name: {error}");
                    return ExitCode::from(1);
                }
            }
        }
        None => None,
    };

    let result = execute(&options, invocation.as_ref());
    match result {
        Ok(has_missing_window) => {
            if let Some(invocation) = invocation.as_ref() {
                if let Err(error) = invocation.finish() {
                    eprintln!("scottland-window-name: {error}");
                    return ExitCode::from(1);
                }
            }
            if has_missing_window {
                ExitCode::FAILURE
            } else {
                ExitCode::SUCCESS
            }
        }
        Err(error) => {
            eprintln!("scottland-window-name: {error}");
            ExitCode::from(1)
        }
    }
}

fn execute(options: &Options, invocation: Option<&invocation::Invocation>) -> Result<bool, String> {
    if let Some(invocation) = invocation {
        invocation.ensure_work()?;
    }
    let selected = session::current(options.display.as_deref())
        .map_err(|error| format!("cannot select a running Scottland session: {error}"))?;
    if let Some(invocation) = invocation {
        invocation.ensure_work()?;
    }
    let socket = selected
        .wayfire_socket()
        .ok_or_else(|| "the selected session has no Wayfire IPC socket".to_string())?;
    let windows = ipc::read_windows(socket)?;
    if let Some(invocation) = invocation {
        invocation.ensure_work()?;
    }
    let ids = if options.ids.is_empty() {
        windows.iter().map(|window| window.id).collect::<Vec<_>>()
    } else {
        options.ids.clone()
    };

    let desktop_index =
        desktop::Index::read_from(&|name| selected.var(name).map(OsStr::to_os_string));
    if let Some(invocation) = invocation {
        invocation.ensure_work()?;
    }
    let process_snapshot = process::Snapshot::read();
    if let Some(invocation) = invocation {
        invocation.ensure_work()?;
    }
    let recipes = recipe::load(&recipe_directories(&selected));
    if let Some(invocation) = invocation {
        invocation.ensure_work()?;
        let stdout = io::stdout();
        let mut output = stdout.lock();
        service::name_with_invocation(
            &ids,
            &windows,
            &desktop_index,
            &process_snapshot,
            &recipes,
            selected.var("DBUS_SESSION_BUS_ADDRESS"),
            &selected.env,
            options.explain,
            invocation,
            |result| {
                to_writer(&mut output, &service::json_value(result, options.explain))
                    .map_err(|error| format!("cannot encode window result: {error}"))?;
                output
                    .write_all(b"\n")
                    .map_err(|error| format!("cannot write window result: {error}"))?;
                output
                    .flush()
                    .map_err(|error| format!("cannot flush window result: {error}"))
            },
        )
    } else {
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
}

fn parse_args(args: impl IntoIterator<Item = OsString>) -> Result<Options, String> {
    let mut options = Options {
        display: None,
        explain: false,
        ids: Vec::new(),
        invocation_budget_ms: None,
        invocation_start_ns: None,
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
            "--invocation-budget-ms" => {
                if options.invocation_budget_ms.is_some() {
                    return Err("--invocation-budget-ms may be supplied only once".to_string());
                }
                let value = args
                    .next()
                    .ok_or_else(|| "--invocation-budget-ms requires a value".to_string())?;
                let value = parse_decimal_option(&value, "--invocation-budget-ms")?;
                if value == 0 {
                    return Err("--invocation-budget-ms must be positive".to_string());
                }
                options.invocation_budget_ms = Some(value);
            }
            "--invocation-start-monotonic-ns" => {
                if options.invocation_start_ns.is_some() {
                    return Err(
                        "--invocation-start-monotonic-ns may be supplied only once".to_string()
                    );
                }
                let value = args.next().ok_or_else(|| {
                    "--invocation-start-monotonic-ns requires a value".to_string()
                })?;
                options.invocation_start_ns = Some(parse_decimal_option(
                    &value,
                    "--invocation-start-monotonic-ns",
                )?);
            }
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
    if options.invocation_start_ns.is_some() && options.invocation_budget_ms.is_none() {
        return Err("--invocation-start-monotonic-ns requires --invocation-budget-ms".to_string());
    }
    Ok(options)
}

fn parse_decimal_option(argument: &OsStr, option: &str) -> Result<u64, String> {
    let text = argument
        .to_str()
        .ok_or_else(|| format!("{option} must be a decimal integer"))?;
    if text.is_empty() || !text.bytes().all(|byte| byte.is_ascii_digit()) {
        return Err(format!("{option} must be a decimal integer"));
    }
    text.parse::<u64>()
        .map_err(|_| format!("{option} is out of range"))
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
