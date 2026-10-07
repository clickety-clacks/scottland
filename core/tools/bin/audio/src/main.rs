use serde_json::Value;
use std::collections::{BTreeSet, HashMap};
use std::env;
use std::ffi::OsStr;
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::os::fd::AsRawFd;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode, Output};
use std::time::{SystemTime, UNIX_EPOCH};

const USAGE: &str = "\
usage: scottland audio input mute
       scottland audio input set-default <node-id> <source-name>
       scottland audio output set-default <node-id> <sink-name>
       scottland audio output sink [sink-name]
       scottland audio output switch
       scottland audio output volume <raise|lower|mute-toggle|+N|-N>
       scottland audio brightness keyboard mute <on|off>";

fn main() -> ExitCode {
    match dispatch(&env::args().skip(1).collect::<Vec<_>>()) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("scottland-audio: {error}");
            ExitCode::from(1)
        }
    }
}

fn dispatch(args: &[String]) -> Result<(), String> {
    match args {
        [help] if help == "-h" || help == "--help" || help == "help" => {
            println!("{USAGE}");
            Ok(())
        }
        [input, mute] if input == "input" && mute == "mute" => input_mute(),
        [input, set_default, node_id, source_name]
            if input == "input" && set_default == "set-default" =>
        {
            input_set_default(node_id, source_name)
        }
        [output, set_default, node_id, sink_name]
            if output == "output" && set_default == "set-default" =>
        {
            output_set_default(node_id, sink_name)
        }
        [output, sink] if output == "output" && sink == "sink" => {
            let mut graph = AudioGraph::load();
            println!("{}", graph.resolve(&default_sink_name()));
            Ok(())
        }
        [output, sink, sink_name] if output == "output" && sink == "sink" => {
            let mut graph = AudioGraph::load();
            println!("{}", graph.resolve(sink_name));
            Ok(())
        }
        [output, switch] if output == "output" && switch == "switch" => output_switch(),
        [output, volume, action] if output == "output" && volume == "volume" => {
            output_volume(action)
        }
        [brightness, keyboard, mute, action]
            if brightness == "brightness" && keyboard == "keyboard" && mute == "mute" =>
        {
            keyboard_mic_mute(action)
        }
        [] => Err(USAGE.to_string()),
        _ => Err(USAGE.to_string()),
    }
}

fn run(program: &str, args: &[&str]) -> io::Result<Output> {
    Command::new(program).args(args).output()
}

fn stdout(output: io::Result<Output>) -> String {
    output
        .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_string())
        .unwrap_or_default()
}

fn default_sink_name() -> String {
    stdout(run("pactl", &["get-default-sink"]))
}

fn input_mute() -> Result<(), String> {
    let _ = run("wpctl", &["set-mute", "@DEFAULT_AUDIO_SOURCE@", "toggle"]);
    let muted = stdout(run("wpctl", &["get-volume", "@DEFAULT_AUDIO_SOURCE@"])).contains("MUTED");
    let led = if muted { "on" } else { "off" };
    keyboard_mic_mute(led)?;
    let (icon, message) = microphone_osd(muted);
    show_osd(Some(icon), message, None);
    Ok(())
}

fn input_set_default(node_id: &str, source_name: &str) -> Result<(), String> {
    if node_id.is_empty() || source_name.is_empty() {
        return Err("usage: scottland audio input set-default <node-id> <source-name>".into());
    }
    let _ = run("wpctl", &["set-default", node_id]);
    let _ = run("pactl", &["set-default-source", source_name]);
    let active = stdout(run("pactl", &["list", "short", "source-outputs"]));
    for id in active
        .lines()
        .filter_map(|line| line.split_whitespace().next())
    {
        let _ = run("pactl", &["move-source-output", id, source_name]);
    }
    Ok(())
}

fn output_set_default(node_id: &str, sink_name: &str) -> Result<(), String> {
    if node_id.is_empty() || sink_name.is_empty() {
        return Err("usage: scottland audio output set-default <node-id> <sink-name>".into());
    }
    let _ = run("timeout", &["2", "wpctl", "set-default", node_id]);
    let _ = run("timeout", &["2", "pactl", "set-default-sink", sink_name]);

    let active = stdout(run("timeout", &["2", "pactl", "list", "sink-inputs"]));
    for id in application_sink_inputs(&active) {
        let _ = run(
            "timeout",
            &["2", "pactl", "move-sink-input", &id, sink_name],
        );
    }
    Ok(())
}

fn keyboard_mic_mute(action: &str) -> Result<(), String> {
    let led = Path::new("/sys/class/leds/platform::micmute/brightness");
    if !led.exists() {
        return Ok(());
    }
    let value = match action {
        "on" => "1",
        "off" => "0",
        _ => return Err("usage: scottland audio brightness keyboard mute <on|off>".into()),
    };
    let _ = run(
        "brightnessctl",
        &["--device=platform::micmute", "set", value],
    );
    Ok(())
}

fn osd_args(icon: Option<&str>, message: &str, progress: Option<u32>) -> Vec<String> {
    let mut args = vec!["osd".into(), "--message".into(), message.into()];
    if let Some(icon) = icon {
        args.extend(["--icon".into(), icon.into()]);
    }
    if let Some(progress) = progress {
        args.extend(["--progress".into(), progress.to_string()]);
    }
    args
}

fn show_osd(icon: Option<&str>, message: &str, progress: Option<u32>) {
    show_osd_with(OsStr::new("scottland-widget"), icon, message, progress);
}

fn show_osd_with(program: &OsStr, icon: Option<&str>, message: &str, progress: Option<u32>) {
    let output = invoke_osd(program, icon, message, progress);
    match output {
        Ok(output) if output.status.success() => {}
        Ok(output) => eprintln!(
            "scottland-audio: display could not be shown (scottland-widget osd exited {})",
            output.status
        ),
        Err(error) => eprintln!("scottland-audio: display could not be shown: {error}"),
    }
}

fn invoke_osd(
    program: &OsStr,
    icon: Option<&str>,
    message: &str,
    progress: Option<u32>,
) -> io::Result<Output> {
    Command::new(program)
        .args(osd_args(icon, message, progress))
        .output()
}

#[derive(Clone, Debug)]
struct Sink {
    index: u32,
    name: String,
    description: String,
    ports: Vec<Port>,
    volume_percent: Option<u32>,
}

#[derive(Clone, Debug)]
struct Port {
    availability: Option<String>,
}

impl Sink {
    fn available(&self) -> bool {
        self.ports.is_empty()
            || self
                .ports
                .iter()
                .any(|port| port.availability.as_deref() != Some("not available"))
    }
}

fn parse_sinks(json: &str) -> Result<Vec<Sink>, String> {
    let value: Value = serde_json::from_str(json).map_err(|error| error.to_string())?;
    let array = value
        .as_array()
        .ok_or_else(|| "pactl sink list was not a JSON array".to_string())?;
    array
        .iter()
        .map(|value| {
            let name = value
                .get("name")
                .and_then(Value::as_str)
                .ok_or_else(|| "pactl sink has no name".to_string())?
                .to_string();
            let index = value
                .get("index")
                .and_then(|index| {
                    index
                        .as_u64()
                        .or_else(|| index.as_str().and_then(|text| text.parse().ok()))
                })
                .and_then(|index| u32::try_from(index).ok())
                .ok_or_else(|| format!("pactl sink {name} has no numeric index"))?;
            let description = value
                .get("description")
                .and_then(Value::as_str)
                .or_else(|| {
                    value
                        .get("properties")
                        .and_then(|properties| properties.get("device.description"))
                        .and_then(Value::as_str)
                })
                .unwrap_or(&name)
                .to_string();
            let ports = value
                .get("ports")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .map(|port| Port {
                    availability: port
                        .get("availability")
                        .and_then(Value::as_str)
                        .map(str::to_string),
                })
                .collect();
            Ok(Sink {
                index,
                name,
                description,
                ports,
                volume_percent: json_volume_percent(value.get("volume")),
            })
        })
        .collect()
}

fn json_volume_percent(value: Option<&Value>) -> Option<u32> {
    let volume = value?.as_object()?.values().next()?;
    percent(volume.get("value_percent")?.as_str()?)
}

fn percent(value: &str) -> Option<u32> {
    value.trim().trim_end_matches('%').parse().ok()
}

#[derive(Clone, Debug, Default)]
struct SinkInput {
    id: Option<String>,
    sink_index: Option<u32>,
    node_name: Option<String>,
    application_name: Option<String>,
}

fn parse_sink_inputs(text: &str) -> Vec<SinkInput> {
    let mut parsed = Vec::new();
    let mut current: Option<SinkInput> = None;
    for line in text.lines() {
        if let Some(id) = line.trim().strip_prefix("Sink Input #") {
            if let Some(input) = current.take() {
                parsed.push(input);
            }
            current = Some(SinkInput {
                id: Some(id.trim().to_string()),
                ..SinkInput::default()
            });
            continue;
        }
        let Some(input) = current.as_mut() else {
            continue;
        };
        let line = line.trim();
        if let Some(value) = line.strip_prefix("Sink:") {
            input.sink_index = value.trim().parse().ok();
        } else if let Some(value) = quoted_property(line, "node.name") {
            input.node_name = Some(value);
        } else if let Some(value) = quoted_property(line, "application.name") {
            input.application_name = Some(value);
        }
    }
    if let Some(input) = current {
        parsed.push(input);
    }
    parsed
}

fn quoted_property(line: &str, property: &str) -> Option<String> {
    let value = line.strip_prefix(property)?.trim_start();
    let value = value.strip_prefix('=')?.trim_start();
    let value = value.strip_prefix('"')?;
    let end = value.rfind('"')?;
    Some(value[..end].replace("\\\"", "\""))
}

fn application_sink_inputs(text: &str) -> Vec<String> {
    parse_sink_inputs(text)
        .into_iter()
        .filter(|input| {
            input.id.is_some()
                && input.application_name.is_some()
                && input.application_name.as_deref() != Some("EasyEffects")
        })
        .filter_map(|input| input.id)
        .collect()
}

#[derive(Default)]
struct AudioGraph {
    sinks_by_index: HashMap<u32, String>,
    sink_inputs: Vec<SinkInput>,
    pw_links: Option<String>,
}

impl AudioGraph {
    fn load() -> Self {
        let sinks = stdout(run("pactl", &["list", "sinks", "short"]));
        let sinks_by_index = sinks
            .lines()
            .filter_map(|line| {
                let mut columns = line.split_whitespace();
                Some((columns.next()?.parse().ok()?, columns.next()?.to_string()))
            })
            .collect();
        let sink_inputs = parse_sink_inputs(&stdout(run("pactl", &["list", "sink-inputs"])));
        Self {
            sinks_by_index,
            sink_inputs,
            pw_links: None,
        }
    }

    fn resolve(&mut self, sink_name: &str) -> String {
        if sink_name.is_empty() || sink_name.starts_with("alsa_output.") {
            return sink_name.to_string();
        }
        let output_name = sink_name
            .strip_prefix("effect_input.")
            .map(|suffix| format!("effect_output.{suffix}"))
            .unwrap_or_else(|| sink_name.to_string());
        let mut fallback = None;
        for input in &self.sink_inputs {
            let (Some(sink_index), Some(node_name)) =
                (input.sink_index, input.node_name.as_deref())
            else {
                continue;
            };
            let target = self.sinks_by_index.get(&sink_index);
            if node_name == output_name
                || (output_name == sink_name && node_name.starts_with(sink_name))
            {
                if let Some(target) = target {
                    return target.clone();
                }
            }
            if fallback.is_none() && node_name.starts_with(sink_name) {
                fallback = target.cloned();
            }
            if sink_name == "easyeffects_sink"
                && input.application_name.as_deref() == Some("EasyEffects")
            {
                if let Some(target) = target {
                    return target.clone();
                }
            }
        }
        if let Some(fallback) = fallback {
            return fallback;
        }
        if sink_name == "easyeffects_sink" {
            if self.pw_links.is_none() {
                self.pw_links = Some(stdout(run("pw-link", &["-l"])));
            }
            if let Some(linked) = self.pw_links.as_deref().and_then(easyeffects_linked_sink) {
                return linked;
            }
            if let Some(configured) = configured_effects_sink() {
                if self.sinks_by_index.values().any(|name| name == &configured) {
                    return configured;
                }
            }
        }
        sink_name.to_string()
    }
}

fn easyeffects_linked_sink(links: &str) -> Option<String> {
    let mut source_is_effects = false;
    for line in links.lines() {
        if line
            .chars()
            .next()
            .is_some_and(|character| !character.is_whitespace())
        {
            let source = line.split(':').next().unwrap_or_default();
            source_is_effects = source.starts_with("ee_");
            continue;
        }
        let trimmed = line.trim();
        let Some(linked_port) = trimmed.strip_prefix("|->") else {
            continue;
        };
        let linked_port = linked_port.trim();
        if !source_is_effects
            || (!linked_port.starts_with("alsa_output.")
                && !linked_port.starts_with("bluez_output."))
        {
            continue;
        }
        if !linked_port.contains(":playback_") {
            continue;
        }
        return linked_port.split(":playback_").next().map(str::to_string);
    }
    None
}

fn configured_effects_sink() -> Option<String> {
    let path = config_home().join("easyeffects/db/easyeffectsrc");
    let content = fs::read_to_string(path).ok()?;
    content.lines().find_map(|line| {
        line.strip_prefix("outputDevice=")
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_string)
    })
}

fn config_home() -> PathBuf {
    env::var_os("XDG_CONFIG_HOME")
        .filter(|path| !path.is_empty())
        .map(PathBuf::from)
        .or_else(|| {
            env::var_os("HOME")
                .filter(|path| !path.is_empty())
                .map(|home| PathBuf::from(home).join(".config"))
        })
        .unwrap_or_else(|| PathBuf::from(".config"))
}

fn output_switch() -> Result<(), String> {
    let raw = stdout(run(
        "timeout",
        &["2", "pactl", "-f", "json", "list", "sinks"],
    ));
    let sinks = parse_sinks(&raw).unwrap_or_default();
    let mut graph = AudioGraph::load();
    let candidates = switch_candidates(&sinks, &mut graph);
    if candidates.is_empty() {
        show_osd(None, "No audio devices found", None);
        return Err("no audio devices found".into());
    }

    let current = stdout(run("timeout", &["2", "pactl", "get-default-sink"]));
    let next = candidates[next_sink_index(&candidates, &current)];
    let effective_sink = graph.resolve(&next.name);
    let volume = pactl_volume_percent(&effective_sink)
        .or(next.volume_percent)
        .unwrap_or(0);
    let muted = stdout(run(
        "timeout",
        &["2", "pactl", "get-sink-mute", &effective_sink],
    ))
    .contains("yes");
    if next.name != current {
        let _ = output_set_default(&next.index.to_string(), &next.name);
    }
    show_osd(Some(volume_icon(volume, muted)), &next.description, None);
    Ok(())
}

fn switch_candidates<'a>(sinks: &'a [Sink], graph: &mut AudioGraph) -> Vec<&'a Sink> {
    let fronted: BTreeSet<String> = sinks
        .iter()
        .filter(|sink| sink.available() && !sink.name.starts_with("alsa_output."))
        .filter_map(|sink| {
            let resolved = graph.resolve(&sink.name);
            (resolved != sink.name).then_some(resolved)
        })
        .collect();
    sinks
        .iter()
        .filter(|sink| sink.available() && !fronted.contains(&sink.name))
        .collect()
}

fn next_sink_index(candidates: &[&Sink], current: &str) -> usize {
    candidates
        .iter()
        .position(|sink| sink.name == current)
        .map_or(0, |index| (index + 1) % candidates.len())
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum VolumeAction {
    MuteToggle,
    Adjust { raise: bool, amount: u32 },
}

fn parse_volume_action(action: &str) -> Result<VolumeAction, String> {
    match action {
        "raise" => Ok(VolumeAction::Adjust {
            raise: true,
            amount: 5,
        }),
        "lower" => Ok(VolumeAction::Adjust {
            raise: false,
            amount: 5,
        }),
        "mute-toggle" => Ok(VolumeAction::MuteToggle),
        _ => {
            let (raise, amount) = if let Some(amount) = action.strip_prefix('+') {
                (true, amount)
            } else if let Some(amount) = action.strip_prefix('-') {
                (false, amount)
            } else {
                return Err(format!("unknown volume action: {action}"));
            };
            if amount.is_empty() || !amount.bytes().all(|byte| byte.is_ascii_digit()) {
                return Err(format!("unknown volume action: {action}"));
            }
            let amount = amount
                .parse::<u32>()
                .map_err(|_| format!("volume step is too large: {action}"))?;
            Ok(VolumeAction::Adjust { raise, amount })
        }
    }
}

fn adjusted_volume(current: u32, raise: bool, amount: u32) -> u32 {
    if raise {
        current.saturating_add(amount).min(100)
    } else {
        current.saturating_sub(amount)
    }
}

fn volume_icon_state(volume: u32, muted: bool) -> &'static str {
    if muted || volume == 0 {
        "muted"
    } else if volume <= 33 {
        "low"
    } else if volume <= 66 {
        "medium"
    } else {
        "high"
    }
}

fn volume_icon(volume: u32, muted: bool) -> &'static str {
    match volume_icon_state(volume, muted) {
        "muted" => "audio-volume-muted",
        "low" => "audio-volume-low",
        "medium" => "audio-volume-medium",
        _ => "audio-volume-high",
    }
}

fn volume_osd(volume: u32, muted: bool) -> (&'static str, &'static str, u32) {
    (
        if muted || volume == 0 {
            "audio-volume-muted"
        } else {
            "audio-volume-high"
        },
        if muted { "Muted" } else { "Volume" },
        volume.min(100),
    )
}

fn microphone_osd(muted: bool) -> (&'static str, &'static str) {
    if muted {
        ("microphone-sensitivity-muted", "Microphone muted")
    } else {
        ("audio-input-microphone", "Microphone on")
    }
}

fn pactl_volume_percent(sink: &str) -> Option<u32> {
    let text = stdout(run("timeout", &["2", "pactl", "get-sink-volume", sink]));
    text.split_whitespace()
        .find_map(|value| value.strip_suffix('%').and_then(|value| value.parse().ok()))
}

fn output_volume(action: &str) -> Result<(), String> {
    let action = parse_volume_action(action)?;
    let mut graph = AudioGraph::load();
    let sink = graph.resolve(&default_sink_name());
    if sink.is_empty() {
        return Err("could not resolve an audio sink to control".into());
    }
    if action == VolumeAction::MuteToggle {
        if !allow_mute_toggle()? {
            return Ok(());
        }
        let _ = run("pactl", &["set-sink-mute", &sink, "toggle"]);
    } else if let VolumeAction::Adjust { raise, amount } = action {
        let current = pactl_volume_percent(&sink)
            .ok_or_else(|| format!("could not read volume for {sink}"))?;
        let next = adjusted_volume(current, raise, amount);
        let _ = run("pactl", &["set-sink-mute", &sink, "0"]);
        let _ = run("pactl", &["set-sink-volume", &sink, &format!("{next}%")]);
    }
    let volume = pactl_volume_percent(&sink).unwrap_or(0);
    let muted = stdout(run("pactl", &["get-sink-mute", &sink])).contains("yes");
    let (icon, message, progress) = volume_osd(volume, muted);
    show_osd(Some(icon), message, Some(progress));
    Ok(())
}

unsafe extern "C" {
    fn flock(fd: i32, operation: i32) -> i32;
}

const LOCK_EX: i32 = 2;
const LOCK_NB: i32 = 4;

fn allow_mute_toggle() -> Result<bool, String> {
    let runtime = env::var_os("XDG_RUNTIME_DIR")
        .filter(|path| !path.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("/tmp"));
    allow_mute_toggle_in(&runtime).map_err(|error| format!("mute-toggle debounce: {error}"))
}

fn allow_mute_toggle_in(runtime: &Path) -> io::Result<bool> {
    let lock_path = runtime.join("scottland-audio-output-volume-mute-toggle.lock");
    let state_path = runtime.join("scottland-audio-output-volume-mute-toggle.last");
    let lock = OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .open(lock_path)?;
    // The timestamp check and update guard a toggle, where a concurrent key event could
    // otherwise read the same old value and toggle back immediately.
    let locked = unsafe { flock(lock.as_raw_fd(), LOCK_EX | LOCK_NB) };
    if locked != 0 {
        let error = io::Error::last_os_error();
        if error.kind() == io::ErrorKind::WouldBlock {
            return Ok(false);
        }
        return Err(error);
    }
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64;
    let last = fs::read_to_string(&state_path)
        .ok()
        .and_then(|value| value.trim().parse::<u64>().ok())
        .unwrap_or(0);
    if now.saturating_sub(last) < 250 {
        return Ok(false);
    }
    let mut state = OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .open(state_path)?;
    writeln!(state, "{now}")?;
    state.sync_data()?;
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sink_ports_match_pactl_availability_rules() {
        let sinks = parse_sinks(
            r#"[
              {"index":1,"name":"alsa_output.a","description":"Speaker","ports":[],"volume":{"front-left":{"value_percent":"72%"}}},
              {"index":2,"name":"alsa_output.b","ports":[{"availability":"not available"}]},
              {"index":3,"name":"alsa_output.c","properties":{"device.description":"Dock"},"ports":[{"availability":"unknown"}]}
            ]"#,
        )
        .unwrap();
        assert_eq!(sinks[0].description, "Speaker");
        assert_eq!(sinks[0].volume_percent, Some(72));
        assert!(sinks[0].available());
        assert!(!sinks[1].available());
        assert_eq!(sinks[2].description, "Dock");
        assert!(sinks[2].available());
    }

    #[test]
    fn active_output_move_excludes_internal_and_missing_application_streams() {
        let inputs = "Sink Input #11\n    Sink: 2\n    Properties:\n        application.name = \"Firefox\"\nSink Input #12\n    Sink: 2\n    Properties:\n        application.name = \"EasyEffects\"\nSink Input #13\n    Sink: 2\n    Properties:\n        node.name = \"filter-chain\"\n";
        assert_eq!(application_sink_inputs(inputs), ["11"]);
    }

    #[test]
    fn resolver_follows_the_sink_input_downstream_of_a_dsp_sink() {
        let mut graph = AudioGraph {
            sinks_by_index: HashMap::from([(7, "alsa_output.speakers".into())]),
            sink_inputs: parse_sink_inputs(
                "Sink Input #44\n    Sink: 7\n    Properties:\n        node.name = \"effect_output.room\"\n",
            ),
            pw_links: None,
        };
        assert_eq!(graph.resolve("effect_input.room"), "alsa_output.speakers");
        assert_eq!(
            graph.resolve("alsa_output.speakers"),
            "alsa_output.speakers"
        );
    }

    fn test_sink(index: u32, name: &str) -> Sink {
        Sink {
            index,
            name: name.into(),
            description: name.into(),
            ports: Vec::new(),
            volume_percent: Some(50),
        }
    }

    #[test]
    fn output_switch_replaces_a_fronted_physical_speaker_with_its_dsp_sink() {
        let sinks = [
            test_sink(1, "alsa_output.speakers"),
            test_sink(2, "effect_input.room"),
            test_sink(3, "alsa_output.headphones"),
        ];
        let mut fronted_graph = AudioGraph {
            sinks_by_index: HashMap::from([(7, "alsa_output.speakers".into())]),
            sink_inputs: parse_sink_inputs(
                "Sink Input #44\n    Sink: 7\n    Properties:\n        node.name = \"effect_output.room\"\n",
            ),
            pw_links: None,
        };
        let fronted = switch_candidates(&sinks, &mut fronted_graph);
        assert_eq!(
            fronted
                .iter()
                .map(|sink| sink.name.as_str())
                .collect::<Vec<_>>(),
            ["effect_input.room", "alsa_output.headphones"]
        );
        assert_eq!(
            fronted[next_sink_index(&fronted, "effect_input.room")].name,
            "alsa_output.headphones"
        );

        let mut unfronted_graph = AudioGraph::default();
        let ordinary = switch_candidates(&sinks, &mut unfronted_graph);
        assert_eq!(ordinary.len(), 3);
        assert_eq!(
            ordinary[next_sink_index(&ordinary, "alsa_output.speakers")].name,
            "effect_input.room"
        );
    }

    #[test]
    fn effects_links_resolve_a_physical_alsa_or_bluetooth_sink() {
        assert_eq!(
            easyeffects_linked_sink(
                "ee_out:output_FL\n  |-> alsa_output.pci:playback_FL\n  |-> alsa_output.pci:playback_FR\n"
            ),
            Some("alsa_output.pci".into())
        );
        assert_eq!(
            easyeffects_linked_sink("ee_out:output_FL\n  |-> bluez_output.headset:playback_FL\n"),
            Some("bluez_output.headset".into())
        );
        assert_eq!(
            easyeffects_linked_sink("other:out\n  |-> alsa_output.pci:playback_FL\n"),
            None
        );
    }

    #[test]
    fn volume_actions_preserve_the_script_steps_and_clamp_to_zero_through_one_hundred() {
        assert_eq!(
            parse_volume_action("raise").unwrap(),
            VolumeAction::Adjust {
                raise: true,
                amount: 5
            }
        );
        assert_eq!(
            parse_volume_action("-17").unwrap(),
            VolumeAction::Adjust {
                raise: false,
                amount: 17
            }
        );
        assert_eq!(
            parse_volume_action("mute-toggle").unwrap(),
            VolumeAction::MuteToggle
        );
        assert_eq!(adjusted_volume(98, true, 5), 100);
        assert_eq!(adjusted_volume(3, false, 5), 0);
        assert_eq!(
            parse_volume_action("+"),
            Err("unknown volume action: +".into())
        );
    }

    #[test]
    fn volume_icons_follow_mute_and_threshold_rules() {
        assert_eq!(volume_icon(80, true), "audio-volume-muted");
        assert_eq!(volume_icon(0, false), "audio-volume-muted");
        assert_eq!(volume_icon(33, false), "audio-volume-low");
        assert_eq!(volume_icon(34, false), "audio-volume-medium");
        assert_eq!(volume_icon(66, false), "audio-volume-medium");
        assert_eq!(volume_icon(67, false), "audio-volume-high");
    }

    #[test]
    fn volume_osd_reports_mute_state_and_caps_progress() {
        assert_eq!(volume_osd(73, true), ("audio-volume-muted", "Muted", 73));
        assert_eq!(volume_osd(0, false), ("audio-volume-muted", "Volume", 0));
        assert_eq!(volume_osd(115, false), ("audio-volume-high", "Volume", 100));
    }

    #[test]
    fn microphone_osd_uses_the_widget_icon_names() {
        assert_eq!(
            microphone_osd(true),
            ("microphone-sensitivity-muted", "Microphone muted")
        );
        assert_eq!(
            microphone_osd(false),
            ("audio-input-microphone", "Microphone on")
        );
    }

    #[test]
    fn osd_args_call_the_widget_osd_subcommand_directly() {
        assert_eq!(
            osd_args(Some("audio-volume-high"), "Volume", Some(45)),
            [
                "osd",
                "--message",
                "Volume",
                "--icon",
                "audio-volume-high",
                "--progress",
                "45"
            ]
        );
        assert_eq!(
            osd_args(None, "No audio devices found", None),
            ["osd", "--message", "No audio devices found"]
        );
    }

    #[cfg(unix)]
    #[test]
    fn widget_osd_receives_exact_audio_call_arguments() {
        use std::os::unix::fs::PermissionsExt;

        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos();
        let directory = env::temp_dir().join(format!(
            "scottland-audio-osd-{}-{nonce}",
            std::process::id()
        ));
        fs::create_dir_all(&directory).unwrap();
        let program = directory.join("scottland-widget");
        fs::write(&program, "#!/bin/sh\nprintf '%s\\n' \"$@\"\n").unwrap();
        fs::set_permissions(&program, fs::Permissions::from_mode(0o755)).unwrap();

        let cases = [
            (
                Some("audio-volume-high"),
                "Volume",
                Some(45),
                vec![
                    "osd",
                    "--message",
                    "Volume",
                    "--icon",
                    "audio-volume-high",
                    "--progress",
                    "45",
                ],
            ),
            (
                Some("microphone-sensitivity-muted"),
                "Microphone muted",
                None,
                vec![
                    "osd",
                    "--message",
                    "Microphone muted",
                    "--icon",
                    "microphone-sensitivity-muted",
                ],
            ),
            (
                Some("audio-volume-low"),
                "Headphones",
                None,
                vec![
                    "osd",
                    "--message",
                    "Headphones",
                    "--icon",
                    "audio-volume-low",
                ],
            ),
            (
                None,
                "No audio devices found",
                None,
                vec!["osd", "--message", "No audio devices found"],
            ),
        ];

        for (icon, message, progress, expected) in cases {
            let output = invoke_osd(program.as_os_str(), icon, message, progress).unwrap();
            assert!(output.status.success());
            let received = String::from_utf8(output.stdout).unwrap();
            assert_eq!(received.lines().collect::<Vec<_>>(), expected);
        }

        fs::remove_dir_all(directory).unwrap();
    }
}
