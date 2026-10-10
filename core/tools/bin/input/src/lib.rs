use std::collections::BTreeMap;
use std::ffi::OsStr;
use std::fs::{self, OpenOptions};
use std::io::{self, Read, Write};
use std::os::unix::fs::OpenOptionsExt;
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

pub const USAGE: &str = "usage: scottland-input device touchpad|touchscreen\n       scottland-input touchpad|touchscreen [on|off|toggle]\n       scottland-input restore";
const INPUT_DEVICES_METHOD: &str = "scottland/input-devices";
const INPUT_DEVICE_METHOD: &str = "scottland/input-device";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    Touchpad,
    Touchscreen,
}

impl Kind {
    fn as_str(self) -> &'static str {
        match self {
            Self::Touchpad => "touchpad",
            Self::Touchscreen => "touchscreen",
        }
    }

    fn icon(self) -> &'static str {
        match self {
            Self::Touchpad => "input-touchpad",
            Self::Touchscreen => "input-tablet",
        }
    }

    fn record(self, state: &Path) -> PathBuf {
        state.join(format!("input-{}.disabled", self.as_str()))
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DeviceClass {
    Pointer,
    Touch,
    Tablet,
}

impl DeviceClass {
    fn parse(value: &str) -> Option<Self> {
        match value {
            "pointer" => Some(Self::Pointer),
            "touch" => Some(Self::Touch),
            "tablet" => Some(Self::Tablet),
            _ => None,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Device {
    pub class: DeviceClass,
    pub name: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Command {
    Device(Kind),
    Set { kind: Kind, action: Action },
    Restore,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Action {
    On,
    Off,
    Toggle,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ResultText {
    pub status: u8,
    pub stdout: String,
    pub stderr: String,
}

impl ResultText {
    fn error(message: impl AsRef<str>) -> Self {
        Self {
            status: 1,
            stdout: String::new(),
            stderr: format!("scottland-input: {}\n", message.as_ref()),
        }
    }
}

pub trait Backend {
    fn devices(&mut self) -> Result<Vec<Device>, String>;

    /// Changes one device by its exact name and kind. `missing_ok` is used only by restore,
    /// where an unplugged recorded device is a successful no-op.
    fn set_enabled(
        &mut self,
        kind: Kind,
        name: &str,
        enabled: bool,
        missing_ok: bool,
    ) -> Result<bool, String>;
}

pub trait Display {
    fn show(&mut self, kind: Kind, enabled: bool) -> Result<(), String>;
}

pub fn parse_command(args: &[std::ffi::OsString]) -> Result<Command, ()> {
    let words: Option<Vec<&str>> = args.iter().map(|arg| arg.to_str()).collect();
    let Some(words) = words else { return Err(()) };
    match words.as_slice() {
        [verb] if *verb == "restore" => Ok(Command::Restore),
        [verb, kind] if *verb == "device" => Ok(Command::Device(parse_kind(kind)?)),
        [kind] => Ok(Command::Set {
            kind: parse_kind(kind)?,
            action: Action::Toggle,
        }),
        [kind, action] => Ok(Command::Set {
            kind: parse_kind(kind)?,
            action: match *action {
                "on" => Action::On,
                "off" => Action::Off,
                "toggle" => Action::Toggle,
                _ => return Err(()),
            },
        }),
        _ => Err(()),
    }
}

fn parse_kind(value: &str) -> Result<Kind, ()> {
    match value {
        "touchpad" => Ok(Kind::Touchpad),
        "touchscreen" => Ok(Kind::Touchscreen),
        _ => Err(()),
    }
}

pub fn choose_device(kind: Kind, devices: &[Device]) -> Option<&Device> {
    match kind {
        Kind::Touchpad => devices.iter().find(|device| {
            device.class == DeviceClass::Pointer
                && ["touchpad", "trackpad"]
                    .iter()
                    .any(|needle| device.name.to_lowercase().contains(needle))
        }),
        Kind::Touchscreen => devices
            .iter()
            .find(|device| device.class == DeviceClass::Touch)
            .or_else(|| {
                devices
                    .iter()
                    .find(|device| device.class == DeviceClass::Tablet)
            }),
    }
}

pub fn valid_name(name: &str) -> bool {
    !name.is_empty() && !name.chars().any(char::is_control)
}

pub fn run(
    command: Command,
    state: &Path,
    backend: &mut dyn Backend,
    display: &mut dyn Display,
) -> ResultText {
    match command {
        Command::Device(kind) => {
            let devices = match backend.devices() {
                Ok(devices) => devices,
                Err(error) => {
                    return ResultText::error(format!("could not list input devices: {error}"));
                }
            };
            let Some(device) = choose_device(kind, &devices) else {
                return ResultText::error(format!("No {} device found", kind.as_str()));
            };
            ResultText {
                status: 0,
                stdout: format!("{}\n", device.name),
                stderr: String::new(),
            }
        }
        Command::Set { kind, action } => {
            let record = kind.record(state);
            let action = match action {
                Action::Toggle => match record_exists(&record) {
                    Ok(true) => Action::On,
                    Ok(false) => Action::Off,
                    Err(error) => {
                        return ResultText::error(format!(
                            "could not read disabled state: {error}"
                        ));
                    }
                },
                action => action,
            };
            set_kind(kind, action, &record, backend, display)
        }
        Command::Restore => restore(state, backend),
    }
}

fn set_kind(
    kind: Kind,
    action: Action,
    record: &Path,
    backend: &mut dyn Backend,
    display: &mut dyn Display,
) -> ResultText {
    let enabled = action == Action::On;
    if enabled {
        if let Err(error) = remove_record(record) {
            return ResultText::error(format!("could not remove disabled state: {error}"));
        }
    }

    let devices = match backend.devices() {
        Ok(devices) => devices,
        Err(error) => return ResultText::error(format!("could not list input devices: {error}")),
    };
    let Some(device) = choose_device(kind, &devices) else {
        return ResultText::error(format!("No {} device found", kind.as_str()));
    };
    if !valid_name(&device.name) {
        return ResultText::error(format!("{} device name is invalid", kind.as_str()));
    }
    if let Err(error) = backend.set_enabled(kind, &device.name, enabled, false) {
        return ResultText::error(format!(
            "could not {} {}: {error}",
            action_word(enabled),
            kind.as_str()
        ));
    }

    if !enabled {
        if let Err(error) = write_record(record, &device.name) {
            return ResultText::error(format!(
                "{} is off only until the next reload or login; the setting was not saved: {error}",
                capitalize(kind.as_str())
            ));
        }
    }

    let mut result = ResultText {
        status: 0,
        stdout: String::new(),
        stderr: String::new(),
    };
    if display.show(kind, enabled).is_err() {
        result.stderr =
            format!("scottland-input: the device changed, but the display could not be shown\n");
    }
    result
}

fn restore(state: &Path, backend: &mut dyn Backend) -> ResultText {
    let mut failures = Vec::new();
    for kind in [Kind::Touchpad, Kind::Touchscreen] {
        let path = kind.record(state);
        match read_record(&path) {
            Ok(None) => {}
            Ok(Some(name)) if valid_name(&name) => {
                if let Err(error) = backend.set_enabled(kind, &name, false, true) {
                    failures.push(format!(
                        "could not disable {} {:?}: {error}",
                        kind.as_str(),
                        name
                    ));
                }
            }
            Ok(Some(_)) => failures.push(format!("{} disabled state is invalid", kind.as_str())),
            Err(error) => failures.push(format!(
                "{} disabled state could not be read: {error}",
                kind.as_str()
            )),
        }
    }
    ResultText {
        status: u8::from(
            failures
                .iter()
                .any(|failure| failure.starts_with("could not disable")),
        ),
        stdout: String::new(),
        stderr: failures
            .into_iter()
            .map(|failure| format!("scottland-input: {failure}\n"))
            .collect(),
    }
}

fn action_word(enabled: bool) -> &'static str {
    if enabled { "enable" } else { "disable" }
}

fn capitalize(value: &str) -> String {
    let mut chars = value.chars();
    match chars.next() {
        Some(first) => first.to_uppercase().chain(chars).collect(),
        None => String::new(),
    }
}

fn record_exists(path: &Path) -> io::Result<bool> {
    match fs::symlink_metadata(path) {
        Ok(_) => Ok(true),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(error),
    }
}

fn remove_record(path: &Path) -> io::Result<()> {
    match fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}

fn read_record(path: &Path) -> io::Result<Option<String>> {
    let bytes = match fs::read(path) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error),
    };
    let bytes = bytes.strip_suffix(b"\n").unwrap_or(&bytes);
    String::from_utf8(bytes.to_vec())
        .map(Some)
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "record is not UTF-8"))
}

fn write_record(path: &Path, name: &str) -> io::Result<()> {
    let parent = path
        .parent()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "state path has no parent"))?;
    fs::create_dir_all(parent)?;
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or(Duration::ZERO)
        .as_nanos();
    let temporary = parent.join(format!(
        ".{}.{}.{}.tmp",
        path.file_name().and_then(OsStr::to_str).unwrap_or("input"),
        std::process::id(),
        nonce
    ));
    let result = (|| {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&temporary)?;
        file.write_all(name.as_bytes())?;
        file.write_all(b"\n")?;
        file.sync_all()?;
        fs::rename(&temporary, path)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

pub struct SystemBackend;

impl Backend for SystemBackend {
    fn devices(&mut self) -> Result<Vec<Device>, String> {
        let response = ipc_call(INPUT_DEVICES_METHOD, "{}")?;
        check_error(&response)?;
        let Some(entries) = response.get("devices").and_then(Json::as_array) else {
            return Err("compositor returned no device list".into());
        };
        let mut devices = Vec::with_capacity(entries.len());
        for entry in entries {
            let Some(name) = entry.get("name").and_then(Json::as_str) else {
                return Err("compositor returned a device without a name".into());
            };
            let Some(class) = entry
                .get("kind")
                .and_then(Json::as_str)
                .and_then(DeviceClass::parse)
            else {
                continue;
            };
            devices.push(Device {
                class,
                name: name.to_string(),
            });
        }
        Ok(devices)
    }

    fn set_enabled(
        &mut self,
        kind: Kind,
        name: &str,
        enabled: bool,
        missing_ok: bool,
    ) -> Result<bool, String> {
        let data = format!(
            "{{\"kind\":{},\"name\":{},\"enabled\":{},\"missing_ok\":{}}}",
            quote(kind.as_str()),
            quote(name),
            enabled,
            missing_ok
        );
        let response = ipc_call(INPUT_DEVICE_METHOD, &data)?;
        check_error(&response)?;
        match response.get("present").and_then(Json::as_bool) {
            Some(true) => Ok(true),
            Some(false) if missing_ok => Ok(false),
            Some(false) => Err("device disappeared before the compositor could change it".into()),
            None => Err("compositor returned no device-change result".into()),
        }
    }
}

pub struct SystemDisplay;

impl Display for SystemDisplay {
    fn show(&mut self, kind: Kind, enabled: bool) -> Result<(), String> {
        let message = format!(
            "{} {}",
            capitalize(kind.as_str()),
            if enabled { "enabled" } else { "disabled" }
        );
        match std::process::Command::new("scottland-widget")
            .arg("osd")
            .arg("--message")
            .arg(message)
            .arg("--icon")
            .arg(kind.icon())
            .output()
        {
            Ok(result) if result.status.success() => Ok(()),
            Ok(_) => Err("OSD command failed".into()),
            Err(error) => Err(error.to_string()),
        }
    }
}

fn ipc_call(method: &str, data: &str) -> Result<Json, String> {
    let socket = std::env::var_os("WAYFIRE_SOCKET")
        .filter(|socket| !socket.is_empty())
        .map(PathBuf::from)
        .or_else(|| {
            scottland::session::current(None)
                .ok()
                .and_then(|session| session.wayfire_socket().map(Path::to_path_buf))
        })
        .ok_or_else(|| "no running Scottland session found".to_string())?;
    let mut stream = UnixStream::connect(socket).map_err(|error| error.to_string())?;
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .map_err(|error| error.to_string())?;
    let body = format!("{{\"method\":{},\"data\":{data}}}", quote(method));
    let size = u32::try_from(body.len()).map_err(|_| "IPC request is too large".to_string())?;
    stream
        .write_all(&size.to_le_bytes())
        .map_err(|error| error.to_string())?;
    stream
        .write_all(body.as_bytes())
        .map_err(|error| error.to_string())?;
    let mut header = [0u8; 4];
    stream
        .read_exact(&mut header)
        .map_err(|error| error.to_string())?;
    let size = u32::from_le_bytes(header) as usize;
    if size > 4 * 1024 * 1024 {
        return Err("IPC response is too large".into());
    }
    let mut response = vec![0; size];
    stream
        .read_exact(&mut response)
        .map_err(|error| error.to_string())?;
    JsonParser::parse(&response).map_err(|error| format!("invalid compositor response: {error}"))
}

fn check_error(response: &Json) -> Result<(), String> {
    match response.get("error").and_then(Json::as_str) {
        Some(error) => Err(error.to_string()),
        None => Ok(()),
    }
}

fn quote(value: &str) -> String {
    let mut output = String::from("\"");
    for ch in value.chars() {
        match ch {
            '"' => output.push_str("\\\""),
            '\\' => output.push_str("\\\\"),
            '\u{0008}' => output.push_str("\\b"),
            '\u{000c}' => output.push_str("\\f"),
            '\n' => output.push_str("\\n"),
            '\r' => output.push_str("\\r"),
            '\t' => output.push_str("\\t"),
            ch if ch <= '\u{001f}' => output.push_str(&format!("\\u{:04x}", ch as u32)),
            ch => output.push(ch),
        }
    }
    output.push('"');
    output
}

#[derive(Clone, Debug, PartialEq, Eq)]
enum Json {
    Null,
    Bool(bool),
    Number,
    String(String),
    Array(Vec<Json>),
    Object(BTreeMap<String, Json>),
}

impl Json {
    fn get(&self, key: &str) -> Option<&Json> {
        match self {
            Self::Object(map) => map.get(key),
            _ => None,
        }
    }
    fn as_array(&self) -> Option<&[Json]> {
        match self {
            Self::Array(items) => Some(items),
            _ => None,
        }
    }
    fn as_str(&self) -> Option<&str> {
        match self {
            Self::String(value) => Some(value),
            _ => None,
        }
    }
    fn as_bool(&self) -> Option<bool> {
        match self {
            Self::Bool(value) => Some(*value),
            _ => None,
        }
    }
}

struct JsonParser<'a> {
    input: &'a [u8],
    at: usize,
}

impl<'a> JsonParser<'a> {
    fn parse(input: &'a [u8]) -> Result<Json, String> {
        let mut parser = Self { input, at: 0 };
        let value = parser.value()?;
        parser.ws();
        if parser.at != input.len() {
            return Err("trailing data".into());
        }
        Ok(value)
    }

    fn ws(&mut self) {
        while self
            .input
            .get(self.at)
            .is_some_and(|byte| byte.is_ascii_whitespace())
        {
            self.at += 1;
        }
    }

    fn value(&mut self) -> Result<Json, String> {
        self.ws();
        match self.input.get(self.at).copied() {
            Some(b'{') => self.object(),
            Some(b'[') => self.array(),
            Some(b'"') => self.string().map(Json::String),
            Some(b't') => {
                self.literal(b"true")?;
                Ok(Json::Bool(true))
            }
            Some(b'f') => {
                self.literal(b"false")?;
                Ok(Json::Bool(false))
            }
            Some(b'n') => {
                self.literal(b"null")?;
                Ok(Json::Null)
            }
            Some(b'-' | b'0'..=b'9') => {
                self.number()?;
                Ok(Json::Number)
            }
            _ => Err("expected a JSON value".into()),
        }
    }

    fn object(&mut self) -> Result<Json, String> {
        self.at += 1;
        let mut values = BTreeMap::new();
        self.ws();
        if self.consume(b'}') {
            return Ok(Json::Object(values));
        }
        loop {
            self.ws();
            if self.input.get(self.at) != Some(&b'"') {
                return Err("expected an object key".into());
            }
            let key = self.string()?;
            self.ws();
            if !self.consume(b':') {
                return Err("expected ':'".into());
            }
            values.insert(key, self.value()?);
            self.ws();
            if self.consume(b'}') {
                break;
            }
            if !self.consume(b',') {
                return Err("expected ',' or '}'".into());
            }
        }
        Ok(Json::Object(values))
    }

    fn array(&mut self) -> Result<Json, String> {
        self.at += 1;
        let mut values = Vec::new();
        self.ws();
        if self.consume(b']') {
            return Ok(Json::Array(values));
        }
        loop {
            values.push(self.value()?);
            self.ws();
            if self.consume(b']') {
                break;
            }
            if !self.consume(b',') {
                return Err("expected ',' or ']'".into());
            }
        }
        Ok(Json::Array(values))
    }

    fn string(&mut self) -> Result<String, String> {
        self.at += 1;
        let mut bytes = Vec::new();
        let mut raw_start = self.at;
        loop {
            match self.input.get(self.at).copied() {
                Some(b'"') => {
                    bytes.extend_from_slice(&self.input[raw_start..self.at]);
                    self.at += 1;
                    return String::from_utf8(bytes).map_err(|_| "string is not UTF-8".into());
                }
                Some(b'\\') => {
                    bytes.extend_from_slice(&self.input[raw_start..self.at]);
                    self.at += 1;
                    match self.input.get(self.at).copied() {
                        Some(b'"') => bytes.push(b'"'),
                        Some(b'\\') => bytes.push(b'\\'),
                        Some(b'/') => bytes.push(b'/'),
                        Some(b'b') => bytes.push(8),
                        Some(b'f') => bytes.push(12),
                        Some(b'n') => bytes.push(b'\n'),
                        Some(b'r') => bytes.push(b'\r'),
                        Some(b't') => bytes.push(b'\t'),
                        Some(b'u') => {
                            self.at += 1;
                            let first = self.hex4()?;
                            let code = if (0xd800..=0xdbff).contains(&first) {
                                if self.input.get(self.at..self.at + 2) != Some(b"\\u") {
                                    return Err("missing low surrogate".into());
                                }
                                self.at += 2;
                                let low = self.hex4()?;
                                if !(0xdc00..=0xdfff).contains(&low) {
                                    return Err("invalid low surrogate".into());
                                }
                                0x10000 + (((first - 0xd800) as u32) << 10) + (low - 0xdc00) as u32
                            } else if (0xdc00..=0xdfff).contains(&first) {
                                return Err("unexpected low surrogate".into());
                            } else {
                                first as u32
                            };
                            let ch = char::from_u32(code)
                                .ok_or_else(|| "invalid Unicode escape".to_string())?;
                            let mut encoded = [0u8; 4];
                            bytes.extend_from_slice(ch.encode_utf8(&mut encoded).as_bytes());
                            raw_start = self.at;
                            continue;
                        }
                        _ => return Err("invalid string escape".into()),
                    }
                    self.at += 1;
                    raw_start = self.at;
                }
                Some(byte) if byte < 0x20 => return Err("unescaped control in string".into()),
                Some(_) => self.at += 1,
                None => return Err("unterminated string".into()),
            }
        }
    }

    fn hex4(&mut self) -> Result<u16, String> {
        let bytes = self
            .input
            .get(self.at..self.at + 4)
            .ok_or_else(|| "short Unicode escape".to_string())?;
        let mut value = 0u16;
        for byte in bytes {
            value = value * 16
                + match byte {
                    b'0'..=b'9' => (byte - b'0') as u16,
                    b'a'..=b'f' => (byte - b'a' + 10) as u16,
                    b'A'..=b'F' => (byte - b'A' + 10) as u16,
                    _ => return Err("invalid Unicode escape".into()),
                };
        }
        self.at += 4;
        Ok(value)
    }

    fn number(&mut self) -> Result<(), String> {
        let start = self.at;
        while self
            .input
            .get(self.at)
            .is_some_and(|byte| matches!(*byte, b'-' | b'+' | b'.' | b'e' | b'E' | b'0'..=b'9'))
        {
            self.at += 1;
        }
        if self.at == start {
            Err("invalid number".into())
        } else {
            Ok(())
        }
    }

    fn literal(&mut self, literal: &[u8]) -> Result<(), String> {
        if self.input.get(self.at..self.at + literal.len()) == Some(literal) {
            self.at += literal.len();
            Ok(())
        } else {
            Err("invalid literal".into())
        }
    }

    fn consume(&mut self, byte: u8) -> bool {
        if self.input.get(self.at) == Some(&byte) {
            self.at += 1;
            true
        } else {
            false
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::OsString;
    use std::os::unix::ffi::OsStringExt;
    use std::sync::atomic::{AtomicU64, Ordering};

    static NEXT: AtomicU64 = AtomicU64::new(0);

    #[derive(Default)]
    struct FakeBackend {
        devices: Vec<Device>,
        changes: Vec<(Kind, String, bool)>,
        fail_change: bool,
    }

    impl Backend for FakeBackend {
        fn devices(&mut self) -> Result<Vec<Device>, String> {
            Ok(self.devices.clone())
        }
        fn set_enabled(
            &mut self,
            kind: Kind,
            name: &str,
            enabled: bool,
            missing_ok: bool,
        ) -> Result<bool, String> {
            self.changes.push((kind, name.to_string(), enabled));
            let present = self.devices.iter().any(|device| {
                device.name == name
                    && match kind {
                        Kind::Touchpad => device.class == DeviceClass::Pointer,
                        Kind::Touchscreen => {
                            matches!(device.class, DeviceClass::Touch | DeviceClass::Tablet)
                        }
                    }
            });
            if self.fail_change && present {
                return Err("refused".into());
            }
            if !present && !missing_ok {
                return Err("missing".into());
            }
            Ok(present)
        }
    }

    #[derive(Default)]
    struct FakeDisplay {
        calls: Vec<(Kind, bool)>,
        fail: bool,
    }
    impl Display for FakeDisplay {
        fn show(&mut self, kind: Kind, enabled: bool) -> Result<(), String> {
            self.calls.push((kind, enabled));
            if self.fail {
                Err("missing".into())
            } else {
                Ok(())
            }
        }
    }

    fn temp_dir() -> PathBuf {
        let path = std::env::temp_dir().join(format!(
            "scottland-input-test-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir_all(&path).unwrap();
        path
    }

    fn device(class: DeviceClass, name: &str) -> Device {
        Device {
            class,
            name: name.into(),
        }
    }

    #[test]
    fn chooses_touchpad_by_case_insensitive_name_and_touch_after_tablet() {
        let devices = [
            device(DeviceClass::Pointer, "USB mouse"),
            device(DeviceClass::Pointer, "USB TRACKPAD"),
            device(DeviceClass::Tablet, "tablet fallback"),
            device(DeviceClass::Touch, "panel touch"),
        ];
        assert_eq!(
            choose_device(Kind::Touchpad, &devices).unwrap().name,
            "USB TRACKPAD"
        );
        assert_eq!(
            choose_device(Kind::Touchscreen, &devices).unwrap().name,
            "panel touch"
        );
    }

    #[test]
    fn touchscreen_falls_back_to_first_tablet_and_empty_list_has_no_device() {
        let devices = [device(DeviceClass::Tablet, "pen tablet")];
        assert_eq!(
            choose_device(Kind::Touchscreen, &devices).unwrap().name,
            "pen tablet"
        );
        assert!(choose_device(Kind::Touchpad, &devices).is_none());
        assert!(choose_device(Kind::Touchscreen, &[]).is_none());
    }

    #[test]
    fn off_stores_the_published_os_execute_name_verbatim_as_data() {
        let state = temp_dir();
        let name = "trackpad\"})os.execute(\"~/calc&\")--";
        let mut backend = FakeBackend {
            devices: vec![device(DeviceClass::Pointer, name)],
            ..Default::default()
        };
        let mut display = FakeDisplay::default();
        let result = run(
            Command::Set {
                kind: Kind::Touchpad,
                action: Action::Off,
            },
            &state,
            &mut backend,
            &mut display,
        );
        assert_eq!(result.status, 0);
        assert_eq!(backend.changes, [(Kind::Touchpad, name.to_string(), false)]);
        assert_eq!(
            fs::read(state.join("input-touchpad.disabled")).unwrap(),
            format!("{name}\n").as_bytes()
        );
        assert_eq!(display.calls, [(Kind::Touchpad, false)]);
        fs::remove_dir_all(state).unwrap();
    }

    #[test]
    fn bare_toggle_turns_off_then_on_from_the_disabled_record() {
        let state = temp_dir();
        let name = "laptop touchpad";
        let mut backend = FakeBackend {
            devices: vec![device(DeviceClass::Pointer, name)],
            ..Default::default()
        };
        let mut display = FakeDisplay::default();
        let command = parse_command(&[OsString::from("touchpad")]).unwrap();
        assert_eq!(
            command,
            Command::Set {
                kind: Kind::Touchpad,
                action: Action::Toggle,
            }
        );

        let first = run(command.clone(), &state, &mut backend, &mut display);
        assert_eq!(first.status, 0);
        assert_eq!(
            fs::read(Kind::Touchpad.record(&state)).unwrap(),
            b"laptop touchpad\n"
        );
        let second = run(command, &state, &mut backend, &mut display);
        assert_eq!(second.status, 0);
        assert!(!Kind::Touchpad.record(&state).exists());
        assert_eq!(
            backend.changes,
            [
                (Kind::Touchpad, name.into(), false),
                (Kind::Touchpad, name.into(), true),
            ]
        );
        assert_eq!(
            display.calls,
            [(Kind::Touchpad, false), (Kind::Touchpad, true)]
        );
        fs::remove_dir_all(state).unwrap();
    }

    #[test]
    fn off_without_a_device_fails_without_record_change_or_display() {
        let state = temp_dir();
        let record = Kind::Touchpad.record(&state);
        let mut backend = FakeBackend::default();
        let mut display = FakeDisplay::default();
        let result = run(
            Command::Set {
                kind: Kind::Touchpad,
                action: Action::Off,
            },
            &state,
            &mut backend,
            &mut display,
        );
        assert_ne!(result.status, 0);
        assert!(result.stderr.contains("No touchpad device found"));
        assert!(!record.exists());
        assert!(backend.changes.is_empty());
        assert!(display.calls.is_empty());
        fs::remove_dir_all(state).unwrap();
    }

    #[test]
    fn off_compositor_refusal_writes_no_record_and_makes_no_display_call() {
        let state = temp_dir();
        let record = Kind::Touchpad.record(&state);
        let mut backend = FakeBackend {
            devices: vec![device(DeviceClass::Pointer, "touchpad")],
            fail_change: true,
            ..Default::default()
        };
        let mut display = FakeDisplay::default();
        let result = run(
            Command::Set {
                kind: Kind::Touchpad,
                action: Action::Off,
            },
            &state,
            &mut backend,
            &mut display,
        );
        assert_ne!(result.status, 0);
        assert_eq!(
            backend.changes,
            [(Kind::Touchpad, "touchpad".into(), false)]
        );
        assert!(!record.exists());
        assert!(display.calls.is_empty());
        fs::remove_dir_all(state).unwrap();
    }

    #[test]
    fn on_removes_record_even_when_the_current_device_name_has_a_newline() {
        let state = temp_dir();
        let record = Kind::Touchpad.record(&state);
        fs::write(&record, "old touchpad\n").unwrap();
        let mut backend = FakeBackend {
            devices: vec![device(DeviceClass::Pointer, "touchpad\nrenamed")],
            ..Default::default()
        };
        let mut display = FakeDisplay::default();
        let result = run(
            Command::Set {
                kind: Kind::Touchpad,
                action: Action::On,
            },
            &state,
            &mut backend,
            &mut display,
        );
        assert_ne!(result.status, 0);
        assert!(result.stderr.contains("device name is invalid"));
        assert!(!record.exists());
        assert!(backend.changes.is_empty());
        assert!(display.calls.is_empty());
        fs::remove_dir_all(state).unwrap();
    }

    #[test]
    fn absent_widget_executable_keeps_the_successful_off_change() {
        const CHILD_MARKER: &str = "SCOTTLAND_INPUT_MISSING_WIDGET_CHILD";
        if std::env::var_os(CHILD_MARKER).is_some() {
            let state = temp_dir();
            let mut backend = FakeBackend {
                devices: vec![device(DeviceClass::Pointer, "touchpad")],
                ..Default::default()
            };
            let mut display = SystemDisplay;
            let result = run(
                Command::Set {
                    kind: Kind::Touchpad,
                    action: Action::Off,
                },
                &state,
                &mut backend,
                &mut display,
            );
            assert_eq!(result.status, 0);
            assert!(result.stderr.contains("display could not be shown"));
            assert_eq!(
                backend.changes,
                [(Kind::Touchpad, "touchpad".into(), false)]
            );
            assert_eq!(
                fs::read(Kind::Touchpad.record(&state)).unwrap(),
                b"touchpad\n"
            );
            fs::remove_dir_all(state).unwrap();
            return;
        }

        let path = temp_dir();
        let output = std::process::Command::new(std::env::current_exe().unwrap())
            .arg("--exact")
            .arg("tests::absent_widget_executable_keeps_the_successful_off_change")
            .arg("--nocapture")
            .env("PATH", &path)
            .env(CHILD_MARKER, "1")
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "isolated test process failed: stdout={} stderr={}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        fs::remove_dir_all(path).unwrap();
    }

    #[test]
    fn a_name_with_controls_is_not_changed_or_recorded() {
        let state = temp_dir();
        let mut backend = FakeBackend {
            devices: vec![device(DeviceClass::Pointer, "touchpad\ninvalid")],
            ..Default::default()
        };
        let mut display = FakeDisplay::default();
        let result = run(
            Command::Set {
                kind: Kind::Touchpad,
                action: Action::Off,
            },
            &state,
            &mut backend,
            &mut display,
        );
        assert_ne!(result.status, 0);
        assert!(backend.changes.is_empty());
        assert!(!state.join("input-touchpad.disabled").exists());
        assert!(display.calls.is_empty());
        fs::remove_dir_all(state).unwrap();
    }

    #[test]
    fn display_failure_keeps_the_device_change_successful() {
        let state = temp_dir();
        let mut backend = FakeBackend {
            devices: vec![device(DeviceClass::Touch, "touch screen")],
            ..Default::default()
        };
        let mut display = FakeDisplay {
            fail: true,
            ..Default::default()
        };
        let result = run(
            Command::Set {
                kind: Kind::Touchscreen,
                action: Action::On,
            },
            &state,
            &mut backend,
            &mut display,
        );
        assert_eq!(result.status, 0);
        assert_eq!(
            backend.changes,
            [(Kind::Touchscreen, "touch screen".into(), true)]
        );
        assert_eq!(display.calls, [(Kind::Touchscreen, true)]);
        assert!(result.stderr.contains("display could not be shown"));
        fs::remove_dir_all(state).unwrap();
    }

    #[test]
    fn on_removes_record_before_a_failed_compositor_change_and_makes_no_osd() {
        let state = temp_dir();
        let record = Kind::Touchpad.record(&state);
        fs::write(&record, "pad\n").unwrap();
        let mut backend = FakeBackend {
            devices: vec![device(DeviceClass::Pointer, "pad touchpad")],
            fail_change: true,
            ..Default::default()
        };
        let mut display = FakeDisplay::default();
        let result = run(
            Command::Set {
                kind: Kind::Touchpad,
                action: Action::On,
            },
            &state,
            &mut backend,
            &mut display,
        );
        assert_ne!(result.status, 0);
        assert!(!record.exists());
        assert!(display.calls.is_empty());
        fs::remove_dir_all(state).unwrap();
    }

    #[test]
    fn off_write_failure_keeps_device_off_but_does_not_show_osd() {
        let parent = temp_dir();
        let state = parent.join("not-a-directory");
        fs::write(&state, "occupied").unwrap();
        let mut backend = FakeBackend {
            devices: vec![device(DeviceClass::Pointer, "touchpad")],
            ..Default::default()
        };
        let mut display = FakeDisplay::default();
        let result = run(
            Command::Set {
                kind: Kind::Touchpad,
                action: Action::Off,
            },
            &state,
            &mut backend,
            &mut display,
        );
        assert_ne!(result.status, 0);
        assert_eq!(
            backend.changes,
            [(Kind::Touchpad, "touchpad".into(), false)]
        );
        assert!(
            result
                .stderr
                .contains("only until the next reload or login")
        );
        assert!(display.calls.is_empty());
        fs::remove_dir_all(parent).unwrap();
    }

    #[test]
    fn an_unremovable_record_prevents_on_from_changing_the_device() {
        let parent = temp_dir();
        let state = parent.join("state");
        fs::create_dir_all(Kind::Touchpad.record(&state)).unwrap();
        let mut backend = FakeBackend {
            devices: vec![device(DeviceClass::Pointer, "touchpad")],
            ..Default::default()
        };
        let mut display = FakeDisplay::default();
        let result = run(
            Command::Set {
                kind: Kind::Touchpad,
                action: Action::On,
            },
            &state,
            &mut backend,
            &mut display,
        );
        assert_ne!(result.status, 0);
        assert!(backend.changes.is_empty());
        assert!(display.calls.is_empty());
        fs::remove_dir_all(parent).unwrap();
    }

    #[test]
    fn restore_keeps_records_ignores_unplugged_device_and_continues_after_refusal() {
        let state = temp_dir();
        fs::write(Kind::Touchpad.record(&state), "missing pad\n").unwrap();
        fs::write(Kind::Touchscreen.record(&state), "panel touch\n").unwrap();
        let mut backend = FakeBackend {
            devices: vec![device(DeviceClass::Touch, "panel touch")],
            fail_change: true,
            ..Default::default()
        };
        let result = restore(&state, &mut backend);
        assert_eq!(result.status, 1);
        assert_eq!(
            backend.changes,
            [
                (Kind::Touchpad, "missing pad".into(), false),
                (Kind::Touchscreen, "panel touch".into(), false),
            ]
        );
        assert!(Kind::Touchpad.record(&state).exists());
        assert!(Kind::Touchscreen.record(&state).exists());
        fs::remove_dir_all(state).unwrap();
    }

    #[test]
    fn parser_rejects_unknown_or_non_text_arguments() {
        assert!(parse_command(&[OsString::from("mouse"), OsString::from("off")]).is_err());
        assert!(parse_command(&[OsString::from("touchpad"), OsString::from("maybe")]).is_err());
        assert!(parse_command(&[OsString::from_vec(vec![0xff])]).is_err());
    }

    #[test]
    fn json_parser_round_trips_escaped_and_hostile_device_names() {
        let encoded = quote("trackpad\"\\\n\u{1f642}");
        let decoded = JsonParser::parse(format!("{{\"name\":{encoded}}}").as_bytes()).unwrap();
        assert_eq!(
            decoded.get("name").and_then(Json::as_str),
            Some("trackpad\"\\\n\u{1f642}")
        );
    }
}
