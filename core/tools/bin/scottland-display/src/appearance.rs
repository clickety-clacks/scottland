use serde_json::{Map, Value, json};
use std::collections::HashSet;
use std::env;
use std::ffi::OsStr;
use std::fs::{self, OpenOptions};
use std::io::{self, Read, Write};
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

const APPEARANCE_SCHEMA: &str = "org.gnome.desktop.interface";
const PORTAL_NAME: &str = "org.freedesktop.portal.Desktop";
const PORTAL_PATH: &str = "/org/freedesktop/portal/desktop";
const PORTAL_INTERFACE: &str = "org.freedesktop.portal.Settings";
const PALETTE_KEYS: [&str; 6] = ["background", "foreground", "muted", "accent", "alert", "attention"];
const MAX_WAYFIRE_REPLY: usize = 4 * 1024 * 1024;
const SIGUSR2: i32 = 12;
static NEXT_TEMP: AtomicU64 = AtomicU64::new(0);

unsafe extern "C" {
    fn kill(pid: i32, signal: i32) -> i32;
}

pub(super) fn apply_current() -> Result<(), String> {
    let scheme = current_scheme();
    set_option("color_scheme", Value::String(scheme.clone()))?;
    let accent = current_accent();
    if let Some(accent) = accent.as_deref() {
        set_option("accent_color", Value::String(format!("{accent}ff")))?;
    }
    set_cursor_size()?;
    write_palette(&scheme, accent.as_deref())
}

pub(super) fn cursor_size() -> Result<(), String> {
    println!("{}", current_cursor_size());
    Ok(())
}

pub(super) fn push_terminal_theme() -> Result<(), String> {
    let location = current_theme_location();
    let display = env::var_os("WAYLAND_DISPLAY");
    let runtime = env::var_os("XDG_RUNTIME_DIR");
    push_terminal_theme_at(
        &location,
        display.as_deref(),
        runtime.as_deref(),
        Path::new("/proc"),
        |pid| {
            if unsafe { kill(pid, SIGUSR2) } == 0 {
                Ok(())
            } else {
                Err(io::Error::last_os_error())
            }
        },
    )
}

fn push_terminal_theme_at(
    location: &Path,
    display: Option<&OsStr>,
    runtime: Option<&OsStr>,
    process_root: &Path,
    mut signal: impl FnMut(i32) -> io::Result<()>,
) -> Result<(), String> {
    if !location.is_dir() || !theme_links_exist(&location) {
        return Ok(());
    }
    let Some(display) = display.filter(|value| !value.is_empty()) else {
        return Ok(());
    };
    let runtime = runtime.filter(|value| !value.is_empty());
    let Ok(processes) = fs::read_dir(process_root) else {
        return Ok(());
    };
    let display_entry = environment_entry("WAYLAND_DISPLAY", &display);
    let runtime_entry = runtime.as_ref().map(|value| environment_entry("XDG_RUNTIME_DIR", value));
    let mut failures = Vec::new();
    for process in processes.flatten() {
        let Some(pid) = process.file_name().to_str().and_then(|value| value.parse::<i32>().ok()) else {
            continue;
        };
        if fs::read_to_string(process_root.join(pid.to_string()).join("comm"))
            .map(|comm| comm.trim() != "ghostty")
            .unwrap_or(true)
        {
            continue;
        }
        let Ok(environment) = fs::read(process_root.join(pid.to_string()).join("environ")) else {
            continue;
        };
        let entries = environment.split(|byte| *byte == 0).collect::<Vec<_>>();
        if !entries.contains(&display_entry.as_slice())
            || runtime_entry
                .as_ref()
                .is_some_and(|wanted| !entries.contains(&wanted.as_slice()))
        {
            continue;
        }
        if let Err(error) = signal(pid) {
            if error.raw_os_error() != Some(3) {
                failures.push(format!("could not reload Ghostty process {pid}: {error}"));
            }
        }
    }
    if failures.is_empty() {
        Ok(())
    } else {
        Err(failures.join("; "))
    }
}

pub(super) fn palette(args: &[&str]) -> Result<(), String> {
    if !matches!(args, ["--all"] | [_] | [_, _]) {
        return Err("usage: scottland-display palette --all | KEY [FALLBACK]".to_owned());
    }
    if let ["--all", _] = args {
        return Err("usage: scottland-display palette --all | KEY [FALLBACK]".to_owned());
    }
    if let [_, fallback] = args {
        let path = palette_path();
        match fs::read(&path) {
            Ok(bytes) => return palette_from_bytes(args, &bytes),
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                println!("{fallback}");
                return Ok(());
            }
            Err(error) => {
                return Err(format!("could not read session palette {}: {error}", path.display()));
            }
        }
    }
    let path = palette_path();
    let bytes = match fs::read(&path) {
        Ok(bytes) => bytes,
        Err(error) => return Err(format!("could not read session palette {}: {error}", path.display())),
    };
    palette_from_bytes(args, &bytes)
}

fn palette_from_bytes(args: &[&str], bytes: &[u8]) -> Result<(), String> {
    for line in palette_lines(args, bytes)? {
        println!("{line}");
    }
    Ok(())
}

fn palette_lines(args: &[&str], bytes: &[u8]) -> Result<Vec<String>, String> {
    let palette: Value = serde_json::from_slice(bytes)
        .map_err(|error| format!("session palette is invalid JSON: {error}"))?;
    let values = palette
        .as_object()
        .ok_or_else(|| "session palette is not a JSON object".to_owned())?;
    match args {
        ["--all"] => {
            let mut keys = values.keys().collect::<Vec<_>>();
            keys.sort();
            Ok(keys
                .into_iter()
                .map(|key| format!("{key}\t{}", display_value(&values[key])))
                .collect())
        }
        [key] => {
            let value = values
                .get(*key)
                .ok_or_else(|| format!("session palette has no key {key}"))?;
            Ok(vec![display_value(value)])
        }
        [key, fallback] => {
            if let Some(value) = values.get(*key) {
                Ok(vec![display_value(value)])
            } else if let Some(value) = values.get(*fallback) {
                Ok(vec![display_value(value)])
            } else {
                Ok(vec![(*fallback).to_owned()])
            }
        }
        _ => Err("usage: scottland-display palette --all | KEY [FALLBACK]".to_owned()),
    }
}

fn display_value(value: &Value) -> String {
    value.as_str().map(str::to_owned).unwrap_or_else(|| value.to_string())
}

fn current_scheme() -> String {
    if let Some(value) = portal_setting("color-scheme") {
        return portal_scheme(value).to_owned();
    }
    let output = command_output("gsettings", &["get", APPEARANCE_SCHEMA, "color-scheme"]);
    if output
        .as_ref()
        .is_ok_and(|output| String::from_utf8_lossy(&output.stdout).contains("light"))
    {
        "light".to_owned()
    } else {
        "dark".to_owned()
    }
}

fn portal_scheme(value: u32) -> &'static str {
    if value == 2 { "light" } else { "dark" }
}

fn portal_setting(key: &str) -> Option<u32> {
    let output = command_output(
        "busctl",
        &[
            "--user", "call", PORTAL_NAME, PORTAL_PATH, PORTAL_INTERFACE, "ReadOne", "ss",
            "org.freedesktop.appearance", key,
        ],
    )
    .ok()?;
    if !output.status.success() {
        return None;
    }
    parse_portal_uint(&String::from_utf8_lossy(&output.stdout))
}

fn parse_portal_uint(response: &str) -> Option<u32> {
    let words = response
        .split(|character: char| character.is_whitespace() || matches!(character, '(' | ')'))
        .filter(|word| !word.is_empty())
        .collect::<Vec<_>>();
    words.windows(2).find_map(|pair| {
        (pair[0] == "u").then(|| pair[1].parse::<u32>().ok()).flatten()
    })
}

fn current_accent() -> Option<String> {
    let folder = hooks_dir().join("accent.d");
    let mut providers = fs::read_dir(folder)
        .ok()?
        .flatten()
        .map(|entry| entry.path())
        .collect::<Vec<_>>();
    providers.sort();
    for provider in providers {
        let Ok(metadata) = fs::metadata(&provider) else { continue };
        if !metadata.is_file() || metadata.permissions().mode() & 0o111 == 0 {
            continue;
        }
        if let Ok(output) = command_at(&provider, &[]) {
            let color = String::from_utf8_lossy(&output.stdout).trim().to_owned();
            if output.status.success() && valid_color(&color) {
                return Some(color);
            }
        }
    }
    let output = command_output(
        "busctl",
        &[
            "--user", "call", PORTAL_NAME, PORTAL_PATH, PORTAL_INTERFACE, "ReadOne", "ss",
            "org.freedesktop.appearance", "accent-color",
        ],
    )
    .ok()?;
    output.status.success().then(|| parse_portal_accent(&String::from_utf8_lossy(&output.stdout))).flatten()
}

fn parse_portal_accent(response: &str) -> Option<String> {
    let (_, values) = response.rsplit_once("(ddd)")?;
    let values = values
        .split_whitespace()
        .take(3)
        .map(|part| part.trim_matches(['(', ')']).parse::<f64>().ok())
        .collect::<Option<Vec<_>>>()?;
    let [red, green, blue] = values.as_slice() else { return None };
    let channel = |value: f64| (value.clamp(0.0, 1.0) * 255.0).round_ties_even() as u8;
    Some(format!("#{:02x}{:02x}{:02x}", channel(*red), channel(*green), channel(*blue)))
}

fn provider_palette() -> Map<String, Value> {
    let folder = hooks_dir().join("accent.d");
    let Ok(entries) = fs::read_dir(folder) else { return Map::new() };
    let mut providers = entries.flatten().map(|entry| entry.path()).collect::<Vec<_>>();
    providers.sort();
    for provider in providers {
        let Ok(metadata) = fs::metadata(&provider) else { continue };
        if !metadata.is_file() || metadata.permissions().mode() & 0o111 == 0 {
            continue;
        }
        let Ok(output) = command_at(&provider, &["--palette"]) else { continue };
        if !output.status.success() {
            continue;
        }
        let Ok(Value::Object(source)) = serde_json::from_slice::<Value>(&output.stdout) else { continue };
        return source
            .into_iter()
            .filter(|(key, value)| {
                PALETTE_KEYS.contains(&key.as_str()) && value.as_str().is_some_and(valid_color)
            })
            .collect();
    }
    Map::new()
}

fn text_settings() -> (f64, String, bool) {
    let scale = gsetting("text-scaling-factor")
        .and_then(|value| value.parse::<f64>().ok())
        .filter(|value| value.is_finite())
        .unwrap_or(1.0)
        .clamp(0.5, 3.0);
    let font = gsetting("font-name").unwrap_or_default();
    let font = font.trim_matches('\'').trim_matches('"');
    let family = strip_font_size(font).trim().to_owned();
    let family = if family.is_empty() { "sans-serif".to_owned() } else { family };
    let reduced_motion = gsetting("enable-animations").is_some_and(|value| value == "false");
    (scale, family, reduced_motion)
}

fn strip_font_size(font: &str) -> &str {
    let Some(start) = font.rfind(char::is_whitespace) else { return font };
    let suffix = font[start..].trim();
    if !suffix.is_empty()
        && suffix.bytes().all(|byte| byte.is_ascii_digit() || byte == b'.')
        && suffix.bytes().filter(|byte| *byte == b'.').count() <= 1
    {
        &font[..start]
    } else {
        font
    }
}

fn gsetting(key: &str) -> Option<String> {
    let output = command_output("gsettings", &["get", APPEARANCE_SCHEMA, key]).ok()?;
    output.status.success().then(|| String::from_utf8_lossy(&output.stdout).trim().to_owned())
}

fn desktop_cursor_theme() -> String {
    env::var("XCURSOR_THEME")
        .ok()
        .map(|theme| theme.trim().to_owned())
        .filter(|theme| !theme.is_empty())
        .or_else(|| gsetting("cursor-theme").map(|theme| theme.trim_matches(['\'', '"']).to_owned()))
        .filter(|theme| !theme.is_empty())
        .unwrap_or_else(|| "default".to_owned())
}

fn current_cursor_theme() -> String {
    env::var_os("WAYFIRE_CONFIG_FILE")
        .and_then(|path| fs::read_to_string(path).ok())
        .and_then(|contents| cursor_theme_from_config(&contents))
        .unwrap_or_else(desktop_cursor_theme)
}

fn cursor_theme_from_config(contents: &str) -> Option<String> {
    let mut in_input = false;
    for line in contents.lines() {
        let line = line.trim();
        if line.starts_with('[') && line.ends_with(']') {
            in_input = &line[1..line.len() - 1] == "input";
        } else if in_input && !line.starts_with('#') && !line.starts_with(';') {
            if let Some((key, value)) = line.split_once('=') {
                if key.trim() == "cursor_theme" {
                    let value = value.trim().to_owned();
                    if !value.is_empty() {
                        return Some(value);
                    }
                }
            }
        }
    }
    None
}

fn cursor_search_paths() -> Vec<PathBuf> {
    if let Some(path) = env::var_os("XCURSOR_PATH") {
        return env::split_paths(&path).filter(|path| !path.as_os_str().is_empty()).map(expand_home).collect();
    }
    let home = env::var_os("HOME").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/"));
    let data_home = env::var_os("XDG_DATA_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| home.join(".local/share"));
    let data_dirs = env::var_os("XDG_DATA_DIRS")
        .map(|value| env::split_paths(&value).collect::<Vec<_>>())
        .unwrap_or_else(|| vec![PathBuf::from("/usr/local/share"), PathBuf::from("/usr/share")]);
    let mut paths = vec![data_home.join("icons"), home.join(".icons")];
    paths.extend(data_dirs.into_iter().filter(|path| !path.as_os_str().is_empty()).map(|path| path.join("icons")));
    paths.push(PathBuf::from("/usr/share/pixmaps"));
    paths
}

fn expand_home(path: PathBuf) -> PathBuf {
    let Some(value) = path.to_str() else { return path };
    if value == "~" {
        return env::var_os("HOME").map(PathBuf::from).unwrap_or(path);
    }
    if let Some(suffix) = value.strip_prefix("~/") {
        return env::var_os("HOME").map(|home| PathBuf::from(home).join(suffix)).unwrap_or(path);
    }
    path
}

fn cursor_theme_sizes(theme: &str, paths: &[PathBuf], visited: &mut HashSet<String>) -> HashSet<u32> {
    if theme.is_empty() || !visited.insert(theme.to_owned()) {
        return HashSet::new();
    }
    let Some(directory) = paths.iter().map(|root| root.join(theme)).find(|path| path.is_dir()) else {
        return if theme != "default" { cursor_theme_sizes("default", paths, visited) } else { HashSet::new() };
    };
    let mut sizes = HashSet::new();
    if let Ok(entries) = fs::read_dir(directory.join("cursors")) {
        for entry in entries.flatten() {
            sizes.extend(xcursor_file_sizes(&entry.path()));
        }
    }
    for inherited in inherited_themes(&directory.join("index.theme")) {
        sizes.extend(cursor_theme_sizes(&inherited, paths, visited));
    }
    if theme != "default" {
        sizes.extend(cursor_theme_sizes("default", paths, visited));
    }
    sizes
}

fn inherited_themes(path: &Path) -> Vec<String> {
    let Ok(contents) = fs::read_to_string(path) else { return Vec::new() };
    let mut icon_theme = false;
    for line in contents.lines() {
        let line = line.trim();
        if line.starts_with('[') && line.ends_with(']') {
            icon_theme = line[1..line.len() - 1].trim().eq_ignore_ascii_case("Icon Theme");
        } else if icon_theme && line.to_ascii_lowercase().starts_with("inherits=") {
            return line
                .split_once('=')
                .map(|(_, value)| value.replace(';', ",").split(',').map(str::trim).filter(|value| !value.is_empty()).map(str::to_owned).collect())
                .unwrap_or_default();
        }
    }
    Vec::new()
}

fn xcursor_file_sizes(path: &Path) -> HashSet<u32> {
    let Ok(bytes) = fs::read(path) else { return HashSet::new() };
    if bytes.len() < 16 {
        return HashSet::new();
    }
    let Some(magic) = read_u32(&bytes, 0) else { return HashSet::new() };
    let Some(header_len) = read_u32(&bytes, 4) else { return HashSet::new() };
    let Some(count) = read_u32(&bytes, 12) else { return HashSet::new() };
    if magic != 0x7275_6358 || header_len < 16 || count > 4096 {
        return HashSet::new();
    }
    let toc_end = header_len as usize + count as usize * 12;
    if toc_end > bytes.len() {
        return HashSet::new();
    }
    let mut sizes = HashSet::new();
    for index in 0..count as usize {
        let offset = header_len as usize + index * 12;
        let Some(kind) = read_u32(&bytes, offset) else { continue };
        let Some(size) = read_u32(&bytes, offset + 4) else { continue };
        let Some(position) = read_u32(&bytes, offset + 8) else { continue };
        let start = position as usize;
        if kind != 0xfffd_0002 || size == 0 || start < toc_end || start.saturating_add(36) > bytes.len() {
            continue;
        }
        let (Some(chunk_len), Some(chunk_type), Some(chunk_size)) = (
            read_u32(&bytes, start), read_u32(&bytes, start + 4), read_u32(&bytes, start + 8),
        ) else { continue };
        if chunk_type == kind && chunk_size == size && chunk_len >= 36 && start.saturating_add(chunk_len as usize) <= bytes.len() {
            sizes.insert(size);
        }
    }
    sizes
}

fn read_u32(bytes: &[u8], offset: usize) -> Option<u32> {
    Some(u32::from_le_bytes(bytes.get(offset..offset + 4)?.try_into().ok()?))
}

fn cursor_size_for_scale(scale: f64, sizes: &HashSet<u32>) -> u32 {
    let wanted = (24.0 * scale.clamp(0.5, 3.0)).ceil() as u32;
    let mut sizes = sizes.iter().copied().filter(|size| *size > 0).collect::<Vec<_>>();
    sizes.sort_unstable();
    sizes.iter().copied().find(|size| *size >= wanted).or_else(|| sizes.last().copied()).unwrap_or(wanted)
}

fn current_cursor_size() -> u32 {
    let scale = text_settings().0;
    let paths = cursor_search_paths();
    let sizes = cursor_theme_sizes(&desktop_cursor_theme(), &paths, &mut HashSet::new());
    cursor_size_for_scale(scale, &sizes)
}

fn set_cursor_size() -> Result<(), String> {
    let scale = text_settings().0;
    let paths = cursor_search_paths();
    let compositor_size = cursor_size_for_scale(scale, &cursor_theme_sizes(&current_cursor_theme(), &paths, &mut HashSet::new()));
    let app_size = cursor_size_for_scale(scale, &cursor_theme_sizes(&desktop_cursor_theme(), &paths, &mut HashSet::new()));
    set_wayfire_options(json!({"input/cursor_size": compositor_size}))?;
    let current = gsetting("cursor-size").and_then(|value| value.parse::<u32>().ok());
    if current != Some(app_size) {
        let size = app_size.to_string();
        if let Err(error) = gsettings_command(&["set", APPEARANCE_SCHEMA, "cursor-size", &size]) {
            eprintln!("scottland-display: could not set the app cursor size: {error}");
        }
    }
    update_recorded_environment(app_size);
    update_activation_environment(app_size);
    Ok(())
}

fn update_recorded_environment(size: u32) {
    let Some(display) = env::var_os("WAYLAND_DISPLAY").filter(|value| !value.is_empty()) else { return };
    let display = display.to_string_lossy();
    let path = session_dir().join(format!("{display}.env"));
    for _ in 0..20 {
        match fs::read(&path) {
            Ok(bytes) => {
                let mut entries = bytes
                    .split(|byte| *byte == 0)
                    .filter(|entry| !entry.is_empty() && !entry.starts_with(b"XCURSOR_SIZE="))
                    .map(<[u8]>::to_vec)
                    .collect::<Vec<_>>();
                entries.push(format!("XCURSOR_SIZE={size}").into_bytes());
                let mut data = Vec::new();
                for entry in entries {
                    data.extend_from_slice(&entry);
                    data.push(0);
                }
                if let Err(error) = write_atomic(&path, &data) {
                    eprintln!("scottland-display: could not update {}: {error}", path.display());
                }
                return;
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => std::thread::sleep(Duration::from_millis(50)),
            Err(error) => {
                eprintln!("scottland-display: could not read {}: {error}", path.display());
                return;
            }
        }
    }
}

fn update_activation_environment(size: u32) {
    if !only_graphical_session() {
        return;
    }
    let value = format!("XCURSOR_SIZE={size}");
    match command_output("dbus-update-activation-environment", &["--systemd", &value]) {
        Ok(output) => {
            if let Err(error) = output_result(output, "could not update app launch environment") {
                eprintln!("scottland-display: {error}");
            }
        }
        Err(error) => eprintln!("scottland-display: could not run dbus-update-activation-environment: {error}"),
    }
}

fn only_graphical_session() -> bool {
    let Some(session) = env::var_os("XDG_SESSION_ID") else { return false };
    let Ok(list) = command_output("loginctl", &["list-sessions", "--no-legend"]) else { return false };
    if !list.status.success() {
        return false;
    }
    let Ok(user) = command_output("id", &["-un"]) else { return false };
    if !user.status.success() {
        return false;
    }
    let user = String::from_utf8_lossy(&user.stdout).trim().to_owned();
    let session = session.to_string_lossy();
    for line in String::from_utf8_lossy(&list.stdout).lines() {
        let fields = line.split_whitespace().collect::<Vec<_>>();
        if fields.len() < 3 || fields[2] != user || fields[0] == session {
            continue;
        }
        let Ok(details) = command_output("loginctl", &["show-session", fields[0], "-p", "Type", "-p", "State", "--value"]) else { return false };
        if !details.status.success() {
            return false;
        }
        let details = String::from_utf8_lossy(&details.stdout).lines().map(str::trim).collect::<Vec<_>>();
        if details.len() >= 2 && matches!(details[0], "wayland" | "x11") && details[1] != "closing" {
            return false;
        }
    }
    true
}

fn write_palette(scheme: &str, accent: Option<&str>) -> Result<(), String> {
    let (background, foreground, muted, default_accent, alert, attention) = match scheme {
        "light" => ("#f4f5f7", "#2e3440", "#5e6878", "#3b6ea8", "#c0392b", "#b7791f"),
        _ => ("#2e3440", "#d8dee9", "#97a3ab", "#81a1c1", "#bf616a", "#ebcb8b"),
    };
    let mut palette = Map::from_iter([
        ("scheme".to_owned(), json!(scheme)),
        ("background".to_owned(), json!(background)),
        ("foreground".to_owned(), json!(foreground)),
        ("muted".to_owned(), json!(muted)),
        ("accent".to_owned(), json!(default_accent)),
        ("alert".to_owned(), json!(alert)),
        ("attention".to_owned(), json!(attention)),
    ]);
    for (key, value) in provider_palette() {
        palette.insert(key, value);
    }
    if let Some(accent) = accent {
        palette.insert("accent".to_owned(), json!(accent));
    }
    let (text_scale, font_family, reduced_motion) = text_settings();
    palette.insert("text_scale".to_owned(), json!(text_scale));
    palette.insert("font_family".to_owned(), json!(font_family));
    palette.insert("reduced_motion".to_owned(), json!(reduced_motion));
    let palette = Value::Object(palette);
    let attention = palette
        .get("attention")
        .and_then(Value::as_str)
        .unwrap_or("#ebcb8b");
    set_option("attention_color", Value::String(format!("{attention}ff")))?;
    let data = serde_json::to_vec(&palette).map_err(|error| format!("could not encode session palette: {error}"))?;
    let path = palette_path();
    write_atomic(&path, &data).map_err(|error| format!("could not write {}: {error}", path.display()))
}

fn set_option(name: &str, value: Value) -> Result<(), String> {
    let mut options = Map::new();
    options.insert(format!("scottland/{name}"), value);
    set_wayfire_options(Value::Object(options))
}

fn set_wayfire_options(data: Value) -> Result<(), String> {
    let reply = wayfire_call("wayfire/set-config-options", data)?;
    if let Some(error) = reply.get("error").and_then(Value::as_str) {
        eprintln!("scottland-display: {error}");
    }
    Ok(())
}

fn wayfire_call(method: &str, data: Value) -> Result<Value, String> {
    let socket_path = env::var_os("WAYFIRE_SOCKET").filter(|value| !value.is_empty())
        .ok_or_else(|| "WAYFIRE_SOCKET is missing".to_owned())?;
    let mut socket = UnixStream::connect(&socket_path)
        .map_err(|error| format!("could not connect to Wayfire: {error}"))?;
    let request = serde_json::to_vec(&json!({"method": method, "data": data}))
        .map_err(|error| format!("could not encode Wayfire request: {error}"))?;
    let length = u32::try_from(request.len()).map_err(|_| "Wayfire request is too large".to_owned())?;
    socket.write_all(&length.to_le_bytes()).and_then(|()| socket.write_all(&request))
        .map_err(|error| format!("could not send Wayfire request: {error}"))?;
    let mut header = [0; 4];
    socket.read_exact(&mut header).map_err(|error| format!("could not read Wayfire reply: {error}"))?;
    let length = u32::from_le_bytes(header) as usize;
    if length > MAX_WAYFIRE_REPLY {
        return Err("Wayfire reply is too large".to_owned());
    }
    let mut bytes = vec![0; length];
    socket.read_exact(&mut bytes).map_err(|error| format!("could not read Wayfire reply: {error}"))?;
    serde_json::from_slice(&bytes).map_err(|error| format!("Wayfire returned invalid JSON: {error}"))
}

fn current_theme_location() -> PathBuf {
    let config_home = env::var_os("XDG_CONFIG_HOME")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| env::var_os("HOME").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/" )).join(".config"));
    config_home.join("gooarchy/current-theme")
}

fn theme_links_exist(location: &Path) -> bool {
    ["light", "dark"].iter().any(|name| {
        fs::symlink_metadata(location.join(name))
            .is_ok_and(|metadata| metadata.file_type().is_symlink())
    })
}

fn environment_entry(name: &str, value: &std::ffi::OsStr) -> Vec<u8> {
    let mut entry = name.as_bytes().to_vec();
    entry.push(b'=');
    entry.extend_from_slice(value.as_bytes());
    entry
}

fn hooks_dir() -> PathBuf {
    env::var_os("SCOTTLAND_HOOKS")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("/usr/lib/scottland"))
}

fn session_dir() -> PathBuf {
    env::var_os("SCOTTLAND_SESSION_DIR")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .or_else(|| env::var_os("XDG_RUNTIME_DIR").filter(|value| !value.is_empty()).map(|path| PathBuf::from(path).join("scottland")))
        .unwrap_or_else(|| PathBuf::from(format!("/run/user/{}/scottland", scottland::dirs::uid())))
}

fn palette_path() -> PathBuf {
    let display = env::var_os("WAYLAND_DISPLAY").filter(|value| !value.is_empty()).unwrap_or_else(|| "wayland".into());
    session_dir().join(format!("{}.palette.json", display.to_string_lossy()))
}

fn valid_color(value: &str) -> bool {
    value.len() == 7 && value.starts_with('#') && value[1..].bytes().all(|byte| byte.is_ascii_hexdigit())
}

fn command_output(program: &str, args: &[&str]) -> io::Result<Output> {
    Command::new(program).args(args).output()
}

fn command_at(program: &Path, args: &[&str]) -> io::Result<Output> {
    Command::new(program).args(args).output()
}

fn gsettings_command(args: &[&str]) -> Result<(), String> {
    let output = command_output("gsettings", args).map_err(|error| format!("could not run gsettings: {error}"))?;
    output_result(output, "gsettings failed")
}

fn output_result(output: Output, action: &str) -> Result<(), String> {
    if output.status.success() {
        Ok(())
    } else {
        let detail = String::from_utf8_lossy(&output.stderr).trim().to_owned();
        if detail.is_empty() {
            Err(format!("{action}: exit status {}", output.status.code().unwrap_or(1)))
        } else {
            Err(format!("{action}: {detail}"))
        }
    }
}

fn write_atomic(path: &Path, bytes: &[u8]) -> io::Result<()> {
    let parent = path.parent().ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "file has no parent"))?;
    fs::create_dir_all(parent)?;
    let temporary = path.with_file_name(format!(".{}.{}.{}.tmp", path.file_name().unwrap_or_default().to_string_lossy(), std::process::id(), NEXT_TEMP.fetch_add(1, Ordering::Relaxed)));
    let mut file = OpenOptions::new().write(true).create_new(true).mode(0o600).open(&temporary)?;
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

#[cfg(test)]
mod tests {
    use super::{
        cursor_size_for_scale, cursor_theme_from_config, palette_lines, parse_portal_accent,
        parse_portal_uint, portal_scheme, push_terminal_theme_at, strip_font_size, valid_color,
    };
    use std::collections::HashSet;
    use std::fs;
    use std::os::unix::fs::symlink;
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicU64, Ordering};

    static NEXT: AtomicU64 = AtomicU64::new(0);

    struct Scratch(PathBuf);

    impl Scratch {
        fn new() -> Self {
            let root = std::env::temp_dir().join(format!(
                "scottland-display-appearance-{}-{}",
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
    fn parses_portal_values_and_accent() {
        assert_eq!(parse_portal_uint("u 1"), Some(1));
        assert_eq!(parse_portal_uint("(u 2)"), Some(2));
        assert_eq!(parse_portal_uint("error"), None);
        assert_eq!(parse_portal_accent("(ddd) 1.0 0.5 0"), Some("#ff8000".to_owned()));
        assert_eq!(portal_scheme(2), "light");
        assert_eq!(portal_scheme(1), "dark");
        assert_eq!(portal_scheme(0), "dark");
        assert_eq!(portal_scheme(99), "dark");
        assert!(valid_color("#Aa00ff"));
        assert!(!valid_color("red"));
    }

    #[test]
    fn palette_output_is_sorted_and_fallback_uses_a_key_before_literal_text() {
        let bytes = br##"{"scheme":"dark","accent":"#123456","alert":"#ff0000"}"##;
        assert_eq!(
            palette_lines(&["--all"], bytes).unwrap(),
            vec![
                "accent\t#123456".to_owned(),
                "alert\t#ff0000".to_owned(),
                "scheme\tdark".to_owned(),
            ]
        );
        assert_eq!(
            palette_lines(&["missing", "alert"], bytes).unwrap(),
            vec!["#ff0000".to_owned()]
        );
        assert_eq!(
            palette_lines(&["missing", "#fff"], bytes).unwrap(),
            vec!["#fff".to_owned()]
        );
        assert!(palette_lines(&["missing"], bytes).is_err());
    }

    #[test]
    fn computes_cursor_theme_and_font_preferences() {
        assert_eq!(cursor_theme_from_config("[input]\ncursor_theme = Adwaita\n"), Some("Adwaita".to_owned()));
        assert_eq!(cursor_theme_from_config("[other]\ncursor_theme=wrong\n"), None);
        assert_eq!(strip_font_size("Cantarell 11"), "Cantarell");
        assert_eq!(strip_font_size("sans-serif"), "sans-serif");
        let sizes = HashSet::from([24, 32, 48, 64]);
        assert_eq!(cursor_size_for_scale(1.0, &sizes), 24);
        assert_eq!(cursor_size_for_scale(1.1, &sizes), 32);
        assert_eq!(cursor_size_for_scale(3.0, &sizes), 64);
    }

    #[test]
    fn terminal_theme_push_is_a_noop_without_a_location_link_or_display() {
        let scratch = Scratch::new();
        let process_root = scratch.path("proc");
        fs::create_dir(&process_root).unwrap();
        let location = scratch.path("config/gooarchy/current-theme");
        let mut signals = Vec::new();

        assert!(push_terminal_theme_at(
            &location,
            Some(std::ffi::OsStr::new("wayland-0")),
            None,
            &process_root,
            |pid| { signals.push(pid); Ok(()) },
        )
        .is_ok());
        fs::create_dir_all(&location).unwrap();
        assert!(push_terminal_theme_at(
            &location,
            Some(std::ffi::OsStr::new("wayland-0")),
            None,
            &process_root,
            |pid| { signals.push(pid); Ok(()) },
        )
        .is_ok());
        symlink("light-theme", location.join("light")).unwrap();
        assert!(push_terminal_theme_at(
            &location,
            None,
            None,
            &process_root,
            |pid| { signals.push(pid); Ok(()) },
        )
        .is_ok());
        assert!(signals.is_empty());
    }

    #[test]
    fn terminal_theme_push_signals_only_matching_ghostty_sessions_once() {
        let scratch = Scratch::new();
        let process_root = scratch.path("proc");
        let location = scratch.path("config/gooarchy/current-theme");
        fs::create_dir_all(&process_root).unwrap();
        fs::create_dir_all(&location).unwrap();
        symlink("light-theme", location.join("light")).unwrap();

        for (pid, display, runtime) in [
            (101, "wayland-0", "/run/user/1000"),
            (102, "wayland-1", "/run/user/1000"),
            (103, "wayland-0", "/run/user/1001"),
        ] {
            let process = process_root.join(pid.to_string());
            fs::create_dir(&process).unwrap();
            fs::write(process.join("comm"), "ghostty\n").unwrap();
            fs::write(
                process.join("environ"),
                format!("WAYLAND_DISPLAY={display}\0XDG_RUNTIME_DIR={runtime}\0"),
            )
            .unwrap();
        }
        let mut signals = Vec::new();
        push_terminal_theme_at(
            &location,
            Some(std::ffi::OsStr::new("wayland-0")),
            Some(std::ffi::OsStr::new("/run/user/1000")),
            &process_root,
            |pid| {
                signals.push(pid);
                Ok(())
            },
        )
        .unwrap();
        assert_eq!(signals, [101]);
    }

    #[test]
    fn terminal_theme_push_reports_attempted_signal_errors() {
        let scratch = Scratch::new();
        let process_root = scratch.path("proc");
        let location = scratch.path("config/gooarchy/current-theme");
        fs::create_dir_all(&process_root).unwrap();
        fs::create_dir_all(&location).unwrap();
        symlink("dark-theme", location.join("dark")).unwrap();
        let process = process_root.join("104");
        fs::create_dir(&process).unwrap();
        fs::write(process.join("comm"), "ghostty\n").unwrap();
        fs::write(process.join("environ"), b"WAYLAND_DISPLAY=wayland-0\0").unwrap();

        let result = push_terminal_theme_at(
            &location,
            Some(std::ffi::OsStr::new("wayland-0")),
            None,
            &process_root,
            |_| Err(std::io::Error::from_raw_os_error(1)),
        );
        assert!(result.unwrap_err().contains("Ghostty process 104"));
    }
}
