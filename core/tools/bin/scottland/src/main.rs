//! `scottland`: the front command. It only finds and runs tools, the way `git` runs `git-*`:
//!
//!     scottland                    list the tools installed
//!     scottland TOOL [ARGS...]     run scottland-TOOL with ARGS
//!     scottland help TOOL          run scottland-TOOL --help
//!
//! Tools are executables named `scottland-TOOL` beside this binary or on PATH, in that order; the
//! first found wins. Everything a tool does lives in the tool, so each works the same run directly.

use std::collections::BTreeMap;
use std::env;
use std::ffi::OsString;
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode};

const PREFIX: &str = "scottland-";

const USAGE: &str = "\
usage: scottland TOOL [ARGS...]    run scottland-TOOL
       scottland help TOOL         show a tool's help
       scottland --version";

fn main() -> ExitCode {
    let mut args = env::args_os().skip(1);
    let first = args.next();
    let rest: Vec<OsString> = args.collect();
    let Some(first) = first else {
        return list();
    };
    let Some(first) = first.to_str() else {
        return fail(2, "tool names are plain text");
    };
    match (first, rest.as_slice()) {
        ("-h" | "--help" | "help", []) => list(),
        ("help", [tool]) => match tool.to_str() {
            Some(tool) => run(tool, &[OsString::from("--help")]),
            None => fail(2, "tool names are plain text"),
        },
        ("help", _) => fail(2, USAGE),
        ("-V" | "--version", []) => {
            println!("scottland {}", env!("CARGO_PKG_VERSION"));
            ExitCode::SUCCESS
        }
        (option, _) if option.starts_with('-') => {
            fail(2, &format!("unknown option {option}\n{USAGE}"))
        }
        (tool, args) => run(tool, args),
    }
}

fn list() -> ExitCode {
    println!("{USAGE}\n");
    let tools = installed(&search_path());
    if tools.is_empty() {
        println!("No Scottland tools are installed.");
    } else {
        println!("Tools:");
        for name in tools.keys() {
            println!("  {name}");
        }
    }
    ExitCode::SUCCESS
}

fn run(tool: &str, args: &[OsString]) -> ExitCode {
    if tool.is_empty() || tool.contains('/') || tool.starts_with('-') {
        return fail(2, &format!("{tool:?} is not a tool name"));
    }
    let Some(path) = find(tool, &search_path()) else {
        return fail(
            1,
            &format!("no tool named {tool:?}; run 'scottland' to list them"),
        );
    };
    let error = Command::new(&path)
        .arg0(format!("{PREFIX}{tool}"))
        .args(args)
        .exec();
    fail(126, &format!("cannot run {}: {error}", path.display()))
}

fn fail(code: u8, message: &str) -> ExitCode {
    eprintln!("scottland: {message}");
    ExitCode::from(code)
}

/// Where tools are looked for: this binary's own directory (so an installed set of tools, or a
/// dev snapshot, runs its own siblings), then PATH.
fn search_path() -> Vec<PathBuf> {
    let own = env::current_exe()
        .ok()
        .and_then(|exe| exe.parent().map(Path::to_path_buf));
    let path = env::var_os("PATH").unwrap_or_default();
    let mut dirs: Vec<PathBuf> = Vec::new();
    for dir in own.into_iter().chain(env::split_paths(&path)) {
        if !dir.as_os_str().is_empty() && !dirs.contains(&dir) {
            dirs.push(dir);
        }
    }
    dirs
}

fn is_executable(path: &Path) -> bool {
    fs::metadata(path).is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
}

fn find(tool: &str, dirs: &[PathBuf]) -> Option<PathBuf> {
    dirs.iter()
        .map(|dir| dir.join(format!("{PREFIX}{tool}")))
        .find(|path| is_executable(path))
}

/// Every tool found, by name, with the path that runs it.
fn installed(dirs: &[PathBuf]) -> BTreeMap<String, PathBuf> {
    let mut tools = BTreeMap::new();
    for dir in dirs {
        let Ok(entries) = fs::read_dir(dir) else {
            continue;
        };
        for entry in entries.filter_map(Result::ok) {
            let file = entry.file_name();
            let Some(name) = file.to_str().and_then(|f| f.strip_prefix(PREFIX)) else {
                continue;
            };
            if !name.is_empty() && !tools.contains_key(name) && is_executable(&entry.path()) {
                tools.insert(name.to_string(), entry.path());
            }
        }
    }
    tools
}
