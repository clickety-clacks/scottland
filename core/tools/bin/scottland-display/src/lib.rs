use std::ffi::OsString;
use std::io::{self, Write};
use std::process::{Command, Output};

pub(crate) mod color_scheme;
pub(crate) mod brightness_target;
pub(crate) mod gamma;
pub(crate) mod night_light;

const SCHEMA: &str = "org.gnome.desktop.interface";
const KEY: &str = "text-scaling-factor";
const RANGE_ERROR: &str = "text size must be a number in the range 0.5 to 3.0";
const USAGE: &str = "usage: scottland-display text-size [set <factor>|reset] | color-scheme [once|watch|ensure|cursor-size] | cursor-size | terminal-theme push | night-light <on|off|toggle|status>";

pub fn run(args: &[OsString]) -> Result<(), String> {
    match run_with_exit_status(args)? {
        0 => Ok(()),
        status => Err(format!("command exited with status {status}")),
    }
}

/// Run a Display command while preserving policy-defined process exit statuses.
///
/// `run` remains the status-agnostic library API used by existing callers;
/// the command-line binary uses this function so Night Light can return its
/// documented parse and operation statuses unchanged.
pub fn run_with_exit_status(args: &[OsString]) -> Result<u8, String> {
    let args = args
        .iter()
        .map(|arg| arg.to_str())
        .collect::<Option<Vec<_>>>()
        .ok_or_else(|| USAGE.to_string())?;

    match args.as_slice() {
        ["-h" | "--help" | "help"] => {
            println!("{USAGE}");
            Ok(0)
        }
        ["text-size"] => show().map(|()| 0),
        ["text-size", "set", factor] => set(factor).map(|()| 0),
        ["text-size", "reset"] => reset().map(|()| 0),
        ["color-scheme"] | ["color-scheme", "once"] => color_scheme::once().map(|()| 0),
        ["color-scheme", "watch"] => color_scheme::watch().map(|()| 0),
        ["color-scheme", "ensure"] => color_scheme::ensure().map(|()| 0),
        ["color-scheme", "cursor-size"] => color_scheme::cursor_size().map(|()| 0),
        ["cursor-size"] => color_scheme::cursor_size().map(|()| 0),
        ["terminal-theme", "push"] => color_scheme::push_terminal_theme().map(|()| 0),
        ["night-light", command @ ..] => {
            let outcome = night_light::execute(command)?;
            let stdout = io::stdout();
            let stderr = io::stderr();
            write_night_light_outcome(outcome, &mut stdout.lock(), &mut stderr.lock())
        }
        _ => Err(USAGE.to_string()),
    }
}

fn write_night_light_outcome(
    outcome: night_light::CliOutcome,
    stdout: &mut impl Write,
    stderr: &mut impl Write,
) -> Result<u8, String> {
    stdout
        .write_all(outcome.stdout.as_bytes())
        .map_err(|error| format!("could not write Night Light output: {error}"))?;
    if let Some(message) = outcome.stderr {
        stderr
            .write_all(message.as_bytes())
            .map_err(|error| format!("could not write Night Light error: {error}"))?;
    }
    Ok(outcome.exit_status)
}

#[cfg(test)]
mod tests {
    use super::{night_light::CliOutcome, write_night_light_outcome};

    #[test]
    fn night_light_dispatch_preserves_stdout_stderr_and_each_policy_status() {
        for status in [0, 1, 2] {
            let outcome = CliOutcome {
                stdout: "policy output\n".to_string(),
                stderr: Some("policy detail\n".to_string()),
                exit_status: status,
            };
            let mut stdout = Vec::new();
            let mut stderr = Vec::new();

            assert_eq!(
                write_night_light_outcome(outcome, &mut stdout, &mut stderr).unwrap(),
                status
            );
            assert_eq!(stdout, b"policy output\n");
            assert_eq!(stderr, b"policy detail\n");
        }
    }
}

fn show() -> Result<(), String> {
    let output = gsettings(&["get", SCHEMA, KEY])?;
    let value = std::str::from_utf8(&output.stdout)
        .map_err(|_| "gsettings returned a non-text value".to_string())?
        .trim();
    if value.is_empty() {
        return Err("gsettings returned an empty text size".to_string());
    }
    println!("{value}");
    Ok(())
}

fn set(factor: &str) -> Result<(), String> {
    let factor = factor
        .parse::<f64>()
        .ok()
        .filter(|value| value.is_finite() && (0.5..=3.0).contains(value))
        .ok_or_else(|| RANGE_ERROR.to_string())?;
    let mut value = factor.to_string();
    if !value.contains('.') && !value.contains('e') && !value.contains('E') {
        value.push_str(".0");
    }
    gsettings(&["set", SCHEMA, KEY, &value])?;
    Ok(())
}

fn reset() -> Result<(), String> {
    gsettings(&["reset", SCHEMA, KEY])?;
    Ok(())
}

fn gsettings(args: &[&str]) -> Result<Output, String> {
    let output = Command::new("gsettings")
        .args(args)
        .output()
        .map_err(|error| format!("could not run gsettings: {error}"))?;
    if output.status.success() {
        return Ok(output);
    }

    let detail = String::from_utf8_lossy(&output.stderr).trim().to_string();
    let detail = if detail.is_empty() {
        output.status.code().map_or_else(
            || "terminated by signal".to_string(),
            |code| format!("exit status {code}"),
        )
    } else {
        detail
    };
    Err(format!("gsettings {} failed: {detail}", args[0]))
}
