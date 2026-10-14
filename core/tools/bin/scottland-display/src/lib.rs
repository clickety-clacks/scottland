use std::ffi::OsString;
use std::process::{Command, Output};

const SCHEMA: &str = "org.gnome.desktop.interface";
const KEY: &str = "text-scaling-factor";
const RANGE_ERROR: &str = "text size must be a number in the range 0.5 to 3.0";
const USAGE: &str = "usage: scottland-display text-size | scottland-display text-size set <factor> | scottland-display text-size reset";

pub fn run(args: &[OsString]) -> Result<(), String> {
    let args = args
        .iter()
        .map(|arg| arg.to_str())
        .collect::<Option<Vec<_>>>()
        .ok_or_else(|| USAGE.to_string())?;

    match args.as_slice() {
        ["-h" | "--help" | "help"] => {
            println!("{USAGE}");
            Ok(())
        }
        ["text-size"] => show(),
        ["text-size", "set", factor] => set(factor),
        ["text-size", "reset"] => reset(),
        _ => Err(USAGE.to_string()),
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
