//! Product-neutral on-machine source, build and rollback mechanism.

mod error;
mod git;
mod paths;
mod system;
mod user;
mod workspace;

#[cfg(test)]
mod tests;

use crate::error::{Error, Result};
use crate::system::{System, require_root};
use std::env;
use std::ffi::OsString;
use std::process::{Command, ExitCode};

const USAGE: &str = "usage: <product>-source <status|edit|rollback|update|adopt|remove> [ARGS...]";

/// Runs the product-neutral CLI using the product name supplied by the small product binary.
pub fn run(
    product: &str,
    build_commit: &str,
    args: impl IntoIterator<Item = OsString>,
) -> ExitCode {
    let args = args.into_iter().collect::<Vec<_>>();
    let result = run_inner(product, build_commit, &args);
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("{product}-source: {error}");
            ExitCode::FAILURE
        }
    }
}

fn run_inner(product: &str, build_commit: &str, args: &[OsString]) -> Result<()> {
    let Some(command) = args.first().and_then(|arg| arg.to_str()) else {
        print_help(product);
        return Ok(());
    };
    if command == "--version" || command == "-V" {
        println!("{product}-source {build_commit}");
        return Ok(());
    }
    if command == "--help" || command == "-h" {
        print_help(product);
        return Ok(());
    }

    match command {
        "__reconcile" => {
            require_root()?;
            let system = System::for_root(product)?;
            let report = system.reconcile()?;
            print_repairs(&report.repairs);
            Ok(())
        }
        "__install" => root_install(product, &args[1..]),
        "__receive-release" => root_receive_release(product, &args[1..]),
        "adopt" | "remove" => {
            if !is_root() {
                return root_call(product, args);
            }
            let system = System::for_root(product)?;
            if command == "adopt" {
                let before = system.installed()?;
                match system.adopt() {
                    Ok(()) => Ok(()),
                    Err(error) => {
                        let after = system.installed().ok().flatten();
                        let release = system
                            .release()
                            .ok()
                            .flatten()
                            .unwrap_or_else(|| "none".to_owned());
                        let base = system
                            .base()
                            .ok()
                            .flatten()
                            .unwrap_or_else(|| "none".to_owned());
                        if before == after {
                            Err(Error::new(format!(
                                "adopt stopped: {error}; the switch remains on {} and its running binaries were not changed; refs now have release {release} and base {base} (fetched objects may also be present)",
                                before.as_deref().unwrap_or("none"),
                            )))
                        } else {
                            Err(Error::new(format!(
                                "adopt stopped after switch activation; the switch now names {}; refs now have release {release} and base {base}; reconcile before another source operation: {error}",
                                after.as_deref().unwrap_or("none")
                            )))
                        }
                    }
                }
            } else {
                system.remove().map(|_| ())
            }
        }
        "status" | "edit" | "rollback" | "update" => {
            if is_root() {
                let system = System::for_root(product)?;
                let report = system.reconcile()?;
                print_repairs(&report.repairs);
            } else {
                let system = System::for_user(product)?;
                match system.needs_reconcile() {
                    Ok(false) => {}
                    Ok(true) => root_call(product, &[OsString::from("__reconcile")])?,
                    Err(error) if error.to_string().contains("A7 failed") => return Err(error),
                    Err(_) => root_call(product, &[OsString::from("__reconcile")])?,
                }
            }
            let system = System::for_user(product)?;
            user::run(&system, command, &args[1..])
        }
        _ => Err(Error::new(format!("{USAGE}\nunknown command {command:?}"))),
    }
}

fn root_install(product: &str, args: &[OsString]) -> Result<()> {
    require_root()?;
    if !(3..=5).contains(&args.len()) {
        return Err(Error::new("internal install request has invalid arguments"));
    }
    let working_clone = args[0]
        .to_str()
        .map(std::path::PathBuf::from)
        .ok_or_else(|| Error::new("working clone path is invalid"))?;
    let binary_directory = args[1]
        .to_str()
        .map(std::path::PathBuf::from)
        .ok_or_else(|| Error::new("build output path is invalid"))?;
    let commit = args[2]
        .to_str()
        .ok_or_else(|| Error::new("commit id is invalid"))?;
    let mut release = None;
    let mut initialize = false;
    for argument in &args[3..] {
        match argument.to_str() {
            Some("--init") => initialize = true,
            Some(value) if release.is_none() => release = Some(value),
            _ => return Err(Error::new("internal install request has invalid arguments")),
        }
    }
    let system = System::for_root(product)?;
    let report = system.reconcile()?;
    print_repairs(&report.repairs);
    system.require_no_hand_edits()?;
    let before = system.installed()?;
    let installed = system.install_user_build(
        &working_clone,
        &binary_directory,
        commit,
        release,
        initialize,
    );
    let (before, after, changed) = match installed {
        Ok(result) => result,
        Err(error) => {
            let after = system.installed().ok().flatten();
            let release = system
                .release()
                .ok()
                .flatten()
                .unwrap_or_else(|| "none".to_owned());
            let base = system
                .base()
                .ok()
                .flatten()
                .unwrap_or_else(|| "none".to_owned());
            if before == after {
                return Err(Error::new(format!(
                    "install stopped: {error}; the switch remains on {} and its running binaries were not changed; refs now have release {release} and base {base} (unreferenced source objects may also be present)",
                    before.as_deref().unwrap_or("none"),
                )));
            }
            return Err(Error::new(format!(
                "install stopped after switch activation; the switch now names {}; refs now have release {release} and base {base}; reconcile before another source operation: {error}",
                after.as_deref().unwrap_or("none")
            )));
        }
    };
    print_activation(before.as_deref(), &after, &changed, product);
    Ok(())
}

fn root_receive_release(product: &str, args: &[OsString]) -> Result<()> {
    require_root()?;
    if args.len() != 2 {
        return Err(Error::new("internal release request has invalid arguments"));
    }
    let clone = args[0]
        .to_str()
        .map(std::path::PathBuf::from)
        .ok_or_else(|| Error::new("working clone path is invalid"))?;
    let release = args[1]
        .to_str()
        .ok_or_else(|| Error::new("release commit is invalid"))?;
    let system = System::for_root(product)?;
    let report = system.reconcile()?;
    print_repairs(&report.repairs);
    system.require_no_hand_edits()?;
    let before_installed = system.installed()?;
    let before_release = system.release()?;
    match system.receive_release(&clone, release) {
        Ok(()) => Ok(()),
        Err(error) => {
            let after_installed = system.installed().ok().flatten();
            let after_release = system.release().ok().flatten();
            Err(Error::new(format!(
                "release receive stopped; switch remains on {}; release ref is {}; source objects may have been fetched: {error}",
                after_installed
                    .as_deref()
                    .or(before_installed.as_deref())
                    .unwrap_or("none"),
                after_release
                    .as_deref()
                    .or(before_release.as_deref())
                    .unwrap_or("unchanged")
            )))
        }
    }
}

fn print_activation(before: Option<&str>, after: &str, tools: &[String], product: &str) {
    if before == Some(after) {
        println!("installed remains {after}; no binary changed");
        return;
    }
    let changed = if tools.is_empty() {
        "none".to_owned()
    } else {
        tools.join(", ")
    };
    if let Some(before) = before {
        println!(
            "installed {before} → {after}; changed: {changed}; go back: {product} rollback --to {before}"
        );
    } else {
        println!(
            "installed none → {after}; changed: {changed}; no previous installed build exists"
        );
    }
}

fn print_repairs(repairs: &[String]) {
    for repair in repairs {
        println!("reconcile: {repair}");
    }
}

fn print_help(product: &str) {
    println!(
        "usage: {product}-source <command> [ARGS...]\n\
         commands:\n\
           status\n\
           edit <tool> [--path | [--no-editor] --message <why>]\n\
           rollback [<tool>] [--to <commit|base>] [--reason <why>]\n\
           update [--continue | --abort] [--from <repository> --ref <full-commit> [--init]]\n\
           adopt   record and activate a package release (root)\n\
           remove  remove the switch and mechanism links (root)"
    );
}

pub(crate) fn root_call(product: &str, args: &[OsString]) -> Result<()> {
    if is_root() {
        return run_inner(product, env!("PRODUCT_SOURCE_BUILD_COMMIT"), args);
    }
    let executable = env::current_exe()
        .map_err(|error| Error::new(format!("cannot locate the running source command: {error}")))?
        .canonicalize()
        .map_err(|error| {
            Error::new(format!(
                "cannot resolve the running source command: {error}"
            ))
        })?;
    let status = Command::new("sudo")
        .arg("--")
        .arg(&executable)
        .args(args)
        .status()
        .map_err(|error| {
            Error::new(format!(
                "could not start sudo for {}: {error}",
                executable.display()
            ))
        })?;
    if status.success() {
        Ok(())
    } else {
        Err(Error::new(format!(
            "root source operation failed with status {status}"
        )))
    }
}

fn is_root() -> bool {
    require_root().is_ok()
}
