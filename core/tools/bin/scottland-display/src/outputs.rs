use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::sync::atomic::{AtomicU64, Ordering};

use serde_json::Value;

const USAGE: &str =
    "usage: scottland-display output focused|internal|external-active|scale [up|down|SCALE]";
const SCALE_PRESETS: [f64; 6] = [1.0, 1.25, 1.6, 2.0, 3.0, 4.0];
static NEXT_TEMP: AtomicU64 = AtomicU64::new(0);

#[derive(Clone, Debug, PartialEq)]
struct OutputInfo {
    name: String,
    enabled: bool,
    scale: f64,
}

pub(super) fn run(args: &[&str]) -> Result<(), String> {
    match args {
        ["focused"] => println!("{}", focused_output()?),
        ["internal"] => {
            let output = list_outputs()?
                .into_iter()
                .find(|output| is_internal(&output.name))
                .ok_or_else(|| "no internal output found".to_owned())?;
            println!("{}", output.name);
        }
        ["external-active"] => {
            if list_outputs()?
                .iter()
                .any(|output| output.enabled && !is_internal(&output.name))
            {
                return Ok(());
            }
            return Err("no enabled external output found".to_owned());
        }
        ["scale"] => {
            let output = focused_output()?;
            let scale = output_by_name(&output)?.scale;
            println!("{}", scale_text(scale));
        }
        ["scale", action] => set_scale(action)?,
        _ => return Err(USAGE.to_owned()),
    }
    Ok(())
}

pub(super) fn focused_output() -> Result<String, String> {
    let output = Command::new("scottland-widget")
        .arg("__focused-output")
        .output()
        .map_err(|error| format!("could not ask Scottland for the focused output: {error}"))?;
    if !output.status.success() {
        return Err(command_error(
            output,
            "could not determine the focused output",
        ));
    }
    let name = String::from_utf8_lossy(&output.stdout).trim().to_owned();
    if !valid_name(&name) {
        return Err("Scottland did not report a valid focused output".to_owned());
    }
    Ok(name)
}

pub(super) fn power_off() -> Result<(), String> {
    let output = output_power(&[], "off")?;
    if output.status.success() {
        Ok(())
    } else {
        Err(command_error(output, "could not turn off every output"))
    }
}

pub(super) fn power_on() -> Result<(), String> {
    let outputs = list_outputs()?;
    let enabled = outputs
        .iter()
        .filter(|output| output.enabled)
        .map(|output| output.name.as_str())
        .collect::<Vec<_>>();
    if enabled.is_empty() {
        return Ok(());
    }

    let mut states = power_states(&[])?;
    for name in &enabled {
        if states.get(*name).is_some_and(|state| state == "on") {
            continue;
        }
        let output = output_power(&["--output", *name], "on")?;
        if !output.status.success() {
            return Err(command_error(
                output,
                &format!("could not turn on output {name}"),
            ));
        }
        states.extend(parse_power_states(&output.stdout)?);
    }

    for name in enabled {
        if states.get(name).is_none_or(|state| state != "on") {
            return Err(format!("output {name} did not turn on"));
        }
    }
    Ok(())
}

fn output_power(args: &[&str], action: &str) -> Result<Output, String> {
    let mut command = Command::new(scottland_hooks().join("libexec/scottland-output-power"));
    command.args(args).arg(action);
    command
        .output()
        .map_err(|error| format!("could not run scottland-output-power: {error}"))
}

fn power_states(args: &[&str]) -> Result<std::collections::HashMap<String, String>, String> {
    let output = output_power(args, "status")?;
    let states = parse_power_states(&output.stdout)?;
    if !output.status.success() && states.is_empty() {
        return Err(command_error(output, "could not read output power state"));
    }
    Ok(states)
}

fn parse_power_states(bytes: &[u8]) -> Result<std::collections::HashMap<String, String>, String> {
    let mut states = std::collections::HashMap::new();
    for line in bytes
        .split(|byte| *byte == b'\n')
        .filter(|line| !line.is_empty())
    {
        let value: Value = serde_json::from_slice(line)
            .map_err(|error| format!("scottland-output-power returned invalid JSON: {error}"))?;
        let name = value
            .get("name")
            .and_then(Value::as_str)
            .filter(|name| valid_name(name))
            .ok_or_else(|| {
                "scottland-output-power returned an output without a valid name".to_owned()
            })?;
        let power = value
            .get("power")
            .and_then(Value::as_str)
            .ok_or_else(|| format!("scottland-output-power returned no power state for {name}"))?;
        states.insert(name.to_owned(), power.to_owned());
    }
    Ok(states)
}

fn set_scale(action: &str) -> Result<(), String> {
    let name = focused_output()?;
    let current = output_by_name(&name)?.scale;
    let target = match action {
        "up" => step_scale(current, true),
        "down" => step_scale(current, false),
        value => parse_scale(value).ok_or_else(|| USAGE.to_owned())?,
    };
    let pin = pinned_scale(&name)?;
    let session_only = pin.is_some();
    let applied = apply_scale(&name, target, session_only)?;

    if let Some(pin) = pin {
        println!("{}", scale_text(applied));
        let reset = match pin.as_str() {
            "auto" => (applied - current).abs() > 1.0 / 120.0,
            value => parse_scale(value).is_none_or(|value| (applied - value).abs() > 1.0 / 120.0),
        };
        if reset {
            eprintln!(
                "scottland-display: overrides.ini sets this output's scale and will put it back on reload"
            );
        }
        return Ok(());
    }

    let layout = scottland::dirs::config_dir()
        .map_err(|error| format!("could not locate the saved layout file: {error}"))?
        .join("layout.ini");
    let value = scale_text(applied);
    if let Err(error) = write_layout_scale(&layout, &name, &value) {
        println!("{}", scale_text(applied));
        return Err(format!(
            "could not save output scale; it lasts only until the session ends: {error}"
        ));
    }
    println!("{}", scale_text(applied));
    Ok(())
}

fn apply_scale(name: &str, target: f64, session_only: bool) -> Result<f64, String> {
    let value = scale_text(target);
    let mut command = Command::new(scottland_hooks().join("libexec/scottland-output"));
    command.args(["set", name, "--scale", &value]);
    if session_only {
        command.arg("--session-only");
    }
    let output = command
        .output()
        .map_err(|error| format!("could not run scottland-output: {error}"))?;
    let reported = output
        .stdout
        .split(|byte| *byte == b'\n')
        .find(|line| !line.is_empty())
        .and_then(|line| serde_json::from_slice::<Value>(line).ok())
        .and_then(|value| value.get("scale").and_then(Value::as_f64));
    if !output.status.success() {
        if let Some(applied) = reported {
            println!("{}", scale_text(applied));
        }
        return Err(command_error(
            output,
            "the compositor did not apply the requested scale",
        ));
    }
    reported.ok_or_else(|| "scottland-output did not report the applied scale".to_owned())
}

fn output_by_name(name: &str) -> Result<OutputInfo, String> {
    list_outputs()?
        .into_iter()
        .find(|output| output.name == name)
        .ok_or_else(|| format!("output {name} is not connected"))
}

fn list_outputs() -> Result<Vec<OutputInfo>, String> {
    let output = Command::new(scottland_hooks().join("libexec/scottland-output"))
        .arg("list")
        .output()
        .map_err(|error| format!("could not run scottland-output list: {error}"))?;
    if !output.status.success() {
        return Err(command_error(output, "could not list outputs"));
    }
    let values: Value = serde_json::from_slice(&output.stdout)
        .map_err(|error| format!("scottland-output list returned invalid JSON: {error}"))?;
    let values = values
        .as_array()
        .ok_or_else(|| "scottland-output list did not return an array".to_owned())?;
    values
        .iter()
        .map(|value| {
            let name = value
                .get("name")
                .and_then(Value::as_str)
                .filter(|name| valid_name(name))
                .ok_or_else(|| {
                    "scottland-output list returned an invalid output name".to_owned()
                })?;
            let enabled = value
                .get("enabled")
                .and_then(Value::as_bool)
                .ok_or_else(|| format!("scottland-output list omitted enabled state for {name}"))?;
            let scale = value
                .get("scale")
                .and_then(Value::as_f64)
                .filter(|scale| scale.is_finite() && *scale > 0.0)
                .ok_or_else(|| {
                    format!("scottland-output list returned an invalid scale for {name}")
                })?;
            Ok(OutputInfo {
                name: name.to_owned(),
                enabled,
                scale,
            })
        })
        .collect()
}

fn pinned_scale(name: &str) -> Result<Option<String>, String> {
    let path = scottland::dirs::config_dir()
        .map_err(|error| format!("could not locate overrides.ini: {error}"))?
        .join("overrides.ini");
    let text = match fs::read_to_string(&path) {
        Ok(text) => text,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(format!("could not read {}: {error}", path.display())),
    };
    let wanted = format!("[output:{name}]");
    let mut section = false;
    for line in text.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with('[') && trimmed.ends_with(']') {
            section = trimmed == wanted;
        } else if section && !trimmed.starts_with('#') && !trimmed.starts_with(';') {
            if let Some((key, value)) = trimmed.split_once('=') {
                if key.trim() == "scale" {
                    return Ok(Some(
                        value
                            .split(['#', ';'])
                            .next()
                            .unwrap_or("")
                            .trim()
                            .to_owned(),
                    ));
                }
            }
        }
    }
    Ok(None)
}

fn write_layout_scale(path: &Path, output: &str, scale: &str) -> io::Result<()> {
    let source = match fs::read(path) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == io::ErrorKind::NotFound => Vec::new(),
        Err(error) => return Err(error),
    };
    let text = String::from_utf8(source.clone())
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "layout.ini is not UTF-8"))?;
    let section = format!("[output:{output}]");
    let lines = split_lines(&text);
    let mut in_section = false;
    let mut found_section = false;
    let mut wrote_scale = false;
    let mut result = String::with_capacity(text.len() + section.len() + scale.len() + 16);
    for (line, ending) in &lines {
        let trimmed = line.trim();
        if trimmed.starts_with('[') && trimmed.ends_with(']') {
            if in_section && !wrote_scale {
                result.push_str("scale = ");
                result.push_str(scale);
                result.push_str(ending_for(&lines, line, ending));
                wrote_scale = true;
            }
            in_section = trimmed == section;
            found_section |= in_section;
            result.push_str(line);
            result.push_str(ending);
            continue;
        }
        let is_scale = in_section
            && !trimmed.starts_with('#')
            && !trimmed.starts_with(';')
            && trimmed
                .split_once('=')
                .is_some_and(|(key, _)| key.trim() == "scale");
        if is_scale {
            if !wrote_scale {
                result.push_str(&replace_scale_value(line, scale));
                result.push_str(ending);
                wrote_scale = true;
            }
            continue;
        }
        result.push_str(line);
        result.push_str(ending);
    }
    if found_section && !wrote_scale {
        if !result.ends_with('\n') && !result.is_empty() {
            result.push('\n');
        }
        result.push_str("scale = ");
        result.push_str(scale);
        result.push('\n');
    } else if !found_section {
        if !result.is_empty() && !result.ends_with('\n') {
            result.push('\n');
        }
        if !result.is_empty() && !result.ends_with("\n\n") {
            result.push('\n');
        }
        result.push_str(&section);
        result.push('\n');
        result.push_str("scale = ");
        result.push_str(scale);
        result.push('\n');
    }
    let permissions = fs::metadata(path)
        .map(|metadata| metadata.permissions().mode())
        .unwrap_or(0o600);
    write_atomic(path, result.as_bytes(), permissions)
}

fn split_lines(text: &str) -> Vec<(&str, &str)> {
    let mut lines = Vec::new();
    let mut start = 0;
    for (index, byte) in text.bytes().enumerate() {
        if byte == b'\n' {
            let end = if index > start && text.as_bytes()[index - 1] == b'\r' {
                index - 1
            } else {
                index
            };
            let ending = &text[end..=index];
            lines.push((&text[start..end], ending));
            start = index + 1;
        }
    }
    if start < text.len() {
        lines.push((&text[start..], ""));
    }
    lines
}

fn replace_scale_value(line: &str, scale: &str) -> String {
    let Some(equal) = line.find('=') else {
        return format!("scale = {scale}");
    };
    let value = &line[equal + 1..];
    let comment = value.find(['#', ';']).unwrap_or(value.len());
    let before_comment = &value[..comment];
    let leading = before_comment.len() - before_comment.trim_start_matches([' ', '\t']).len();
    let trailing = before_comment.len() - before_comment.trim_end_matches([' ', '\t']).len();
    let comment_and_suffix = &value[comment..];
    let mut result = String::with_capacity(line.len() + scale.len());
    result.push_str(&line[..=equal]);
    result.push_str(&before_comment[..leading]);
    result.push_str(scale);
    if trailing > 0 {
        result.push_str(&before_comment[before_comment.len() - trailing..]);
    }
    result.push_str(comment_and_suffix);
    result
}

fn ending_for<'a>(lines: &'a [(&'a str, &'a str)], _line: &str, ending: &'a str) -> &'a str {
    if !ending.is_empty() {
        ending
    } else {
        lines
            .iter()
            .find_map(|(_, ending)| (!ending.is_empty()).then_some(*ending))
            .unwrap_or("\n")
    }
}

fn write_atomic(path: &Path, bytes: &[u8], mode: u32) -> io::Result<()> {
    let parent = path
        .parent()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "file has no parent"))?;
    fs::create_dir_all(parent)?;
    let temporary = path.with_file_name(format!(
        ".{}.{}.{}.tmp",
        path.file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("layout"),
        std::process::id(),
        NEXT_TEMP.fetch_add(1, Ordering::Relaxed)
    ));
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(mode & 0o777)
        .open(&temporary)?;
    let result = file.write_all(bytes).and_then(|()| file.sync_all());
    drop(file);
    if let Err(error) = result {
        let _ = fs::remove_file(&temporary);
        return Err(error);
    }
    if let Err(error) = fs::rename(&temporary, path) {
        let _ = fs::remove_file(&temporary);
        return Err(error);
    }
    Ok(())
}

fn parse_scale(value: &str) -> Option<f64> {
    if value.is_empty()
        || value.starts_with('+')
        || value.starts_with('-')
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || byte == b'.')
        || value.matches('.').count() > 1
        || value.starts_with('.')
        || value.ends_with('.')
    {
        return None;
    }
    let scale = value.parse::<f64>().ok()?;
    (scale.is_finite() && (1.0..=4.0).contains(&scale)).then_some(scale)
}

fn step_scale(current: f64, up: bool) -> f64 {
    let mut nearest = 0;
    let mut distance = f64::INFINITY;
    for (index, preset) in SCALE_PRESETS.iter().enumerate() {
        let candidate = (current - preset).abs();
        if candidate < distance {
            nearest = index;
            distance = candidate;
        }
    }
    if up {
        SCALE_PRESETS[(nearest + 1).min(SCALE_PRESETS.len() - 1)]
    } else {
        SCALE_PRESETS[nearest.saturating_sub(1)]
    }
}

fn scale_text(scale: f64) -> String {
    scale.to_string()
}

fn is_internal(name: &str) -> bool {
    ["eDP-", "LVDS-", "DSI-"]
        .iter()
        .any(|prefix| name.starts_with(prefix))
}

fn valid_name(value: &str) -> bool {
    !value.is_empty()
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"._-".contains(&byte))
}

fn scottland_hooks() -> PathBuf {
    scottland::dirs::hooks_dir()
}

fn command_error(output: Output, action: &str) -> String {
    let detail = String::from_utf8_lossy(&output.stderr).trim().to_owned();
    if detail.is_empty() {
        output.status.code().map_or_else(
            || format!("{action}: terminated by signal"),
            |code| format!("{action}: exit status {code}"),
        )
    } else {
        format!("{action}: {detail}")
    }
}

#[cfg(test)]
mod tests {
    use super::{
        parse_power_states, parse_scale, scale_text, split_lines, step_scale, write_layout_scale,
    };
    use std::fs;
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicU64, Ordering};

    static NEXT: AtomicU64 = AtomicU64::new(0);

    struct Scratch(PathBuf);

    impl Scratch {
        fn new() -> Self {
            let root = std::env::temp_dir().join(format!(
                "scottland-display-outputs-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&root).unwrap();
            Self(root)
        }

        fn path(&self, name: &str) -> PathBuf {
            self.0.join(name)
        }
    }

    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn scale_inputs_and_preset_steps_follow_the_cli_contract() {
        assert_eq!(parse_scale("1"), Some(1.0));
        assert_eq!(parse_scale("1.25"), Some(1.25));
        for invalid in ["", ".5", "1.", "+1", "-1", "1e2", "0.9", "4.1", "1,5"] {
            assert_eq!(parse_scale(invalid), None, "accepted {invalid:?}");
        }
        assert_eq!(step_scale(1.125, true), 1.25);
        assert_eq!(step_scale(1.125, false), 1.0);
        assert_eq!(step_scale(4.0, true), 4.0);
        assert_eq!(scale_text(1.6), "1.6");
    }

    #[test]
    fn output_power_status_parses_one_json_record_per_line() {
        let states = parse_power_states(
            b"{\"name\":\"DP-1\",\"power\":\"on\"}\n{\"name\":\"eDP-1\",\"power\":\"off\"}\n",
        )
        .unwrap();
        assert_eq!(states.get("DP-1").map(String::as_str), Some("on"));
        assert_eq!(states.get("eDP-1").map(String::as_str), Some("off"));
        assert!(parse_power_states(b"{\"name\":\"bad/name\",\"power\":\"on\"}").is_err());
    }

    #[test]
    fn layout_scale_update_changes_only_the_named_output_and_keeps_comments() {
        let scratch = Scratch::new();
        let path = scratch.path("layout.ini");
        let original = "[scottland]\ncenter_width = 0.5\n\n[output:DP-1]\nscale = 2 ; user note\nposition = 0,0\n\n[output:eDP-1]\nscale = 1\n";
        fs::write(&path, original).unwrap();

        write_layout_scale(&path, "DP-1", "1.5").unwrap();

        let updated = fs::read_to_string(path).unwrap();
        assert_eq!(
            updated,
            "[scottland]\ncenter_width = 0.5\n\n[output:DP-1]\nscale = 1.5 ; user note\nposition = 0,0\n\n[output:eDP-1]\nscale = 1\n"
        );
        assert_eq!(split_lines(&updated).len(), split_lines(original).len());
    }

    #[test]
    fn layout_scale_update_creates_missing_file_and_keeps_one_target_scale_line() {
        let scratch = Scratch::new();
        let new_path = scratch.path("new-layout.ini");
        write_layout_scale(&new_path, "DP-1", "1.25").unwrap();
        assert_eq!(
            fs::read_to_string(&new_path).unwrap(),
            "[output:DP-1]\nscale = 1.25\n"
        );

        let path = scratch.path("layout.ini");
        fs::write(
            &path,
            "[output:DP-1]\nscale = 1\nposition = 0,0\nscale = 2\n\n[output:eDP-1]\nscale = 1\n",
        )
        .unwrap();
        write_layout_scale(&path, "DP-1", "1.6").unwrap();
        assert_eq!(
            fs::read_to_string(path).unwrap(),
            "[output:DP-1]\nscale = 1.6\nposition = 0,0\n\n[output:eDP-1]\nscale = 1\n"
        );
    }
}
