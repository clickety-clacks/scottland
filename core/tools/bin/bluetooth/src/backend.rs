use std::collections::HashMap;
use std::ffi::OsString;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{Duration, Instant};

use zbus::blocking::{Connection, Proxy};
use zbus::zvariant::{ObjectPath, OwnedObjectPath, OwnedValue};

use crate::{Adapter, Backend, Device};

const BLUEZ: &str = "org.bluez";
const OBJECT_MANAGER: &str = "org.freedesktop.DBus.ObjectManager";
const ADAPTER: &str = "org.bluez.Adapter1";
const DEVICE: &str = "org.bluez.Device1";
const RFKILL_TYPE: &str = "bluetooth";

type InterfaceProperties = HashMap<String, OwnedValue>;
type Interfaces = HashMap<String, InterfaceProperties>;
type ManagedObjects = HashMap<OwnedObjectPath, Interfaces>;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Radio {
    pub name: String,
    pub soft_blocked: bool,
    pub hard_blocked: bool,
}

#[derive(Debug)]
pub struct SystemBackend {
    rfkill_root: PathBuf,
    rfkill_binary: OsString,
}

impl Default for SystemBackend {
    fn default() -> Self {
        Self {
            rfkill_root: PathBuf::from("/sys/class/rfkill"),
            rfkill_binary: OsString::from("rfkill"),
        }
    }
}

impl Backend for SystemBackend {
    fn request_radio_block(&mut self, blocked: bool) -> Result<(), String> {
        request_radio_block(&self.rfkill_binary, blocked)
    }

    fn radios(&mut self) -> Result<Vec<Radio>, String> {
        read_radios(&self.rfkill_root)
    }

    fn adapters(&mut self, timeout: Duration) -> Result<Vec<Adapter>, String> {
        let connection = system_connection(timeout)?;
        let objects = managed_objects(&connection)?;
        adapters_from(&objects)
    }

    fn set_default_adapter_power(
        &mut self,
        powered: bool,
        timeout: Duration,
    ) -> Result<(), String> {
        let deadline = Instant::now() + timeout;
        let objects_connection =
            system_connection(remaining(deadline, "controller power request")?)?;
        let objects = managed_objects(&objects_connection)?;
        let mut adapters = adapters_from(&objects)?;
        adapters.sort_by(|left, right| left.path.cmp(&right.path));
        let adapter = adapters
            .first()
            .ok_or_else(|| "no default Bluetooth controller is available".to_string())?;
        let adapter_path = adapter.path.clone();
        drop(objects_connection);

        let call_connection = system_connection(remaining(deadline, "controller power request")?)?;
        let proxy = Proxy::new(&call_connection, BLUEZ, adapter_path.as_str(), ADAPTER)
            .map_err(|error| format!("could not address default Bluetooth controller: {error}"))?;
        proxy
            .set_property("Powered", powered)
            .map_err(|error| format!("could not set controller power: {error}"))
    }

    fn device(&mut self, address: &str, timeout: Duration) -> Result<Option<Device>, String> {
        let connection = system_connection(timeout)?;
        let objects = managed_objects(&connection)?;
        device_from(&objects, address)
    }

    fn pair(&mut self, address: &str, timeout: Duration) -> Result<(), String> {
        let (connection, path) = device_connection(address, timeout, "pair")?;
        let proxy = device_proxy(&connection, &path, address)?;
        proxy
            .call_method("Pair", &())
            .map(|_| ())
            .map_err(|error| error.to_string())
    }

    fn trust(&mut self, address: &str, timeout: Duration) -> Result<(), String> {
        let (connection, path) = device_connection(address, timeout, "trust")?;
        let proxy = device_proxy(&connection, &path, address)?;
        proxy
            .set_property("Trusted", true)
            .map_err(|error| error.to_string())
    }

    fn connect(&mut self, address: &str, timeout: Duration) -> Result<(), String> {
        let (connection, path) = device_connection(address, timeout, "connect")?;
        let proxy = device_proxy(&connection, &path, address)?;
        proxy
            .call_method("Connect", &())
            .map(|_| ())
            .map_err(|error| error.to_string())
    }

    fn disconnect(&mut self, address: &str, timeout: Duration) -> Result<(), String> {
        let (connection, path) = device_connection(address, timeout, "disconnect")?;
        let proxy = device_proxy(&connection, &path, address)?;
        proxy
            .call_method("Disconnect", &())
            .map(|_| ())
            .map_err(|error| error.to_string())
    }

    fn remove(&mut self, address: &str, timeout: Duration) -> Result<(), String> {
        let deadline = Instant::now() + timeout;
        let objects_connection = system_connection(remaining(deadline, "remove pairing record")?)?;
        let objects = managed_objects(&objects_connection)?;
        let Some((adapter_path, device_path)) = device_location(&objects, address)? else {
            // An absent object has no pairing record to remove.
            return Ok(());
        };
        drop(objects_connection);

        let call_connection = system_connection(remaining(deadline, "remove pairing record")?)?;
        let adapter_proxy = Proxy::new(&call_connection, BLUEZ, adapter_path.as_str(), ADAPTER)
            .map_err(|error| format!("could not address Bluetooth controller: {error}"))?;
        let device_path = ObjectPath::try_from(device_path.as_str())
            .map_err(|error| format!("invalid Bluetooth device path: {error}"))?;
        adapter_proxy
            .call_method("RemoveDevice", &device_path)
            .map(|_| ())
            .map_err(|error| error.to_string())
    }
}

fn system_connection(timeout: Duration) -> Result<Connection, String> {
    zbus::blocking::connection::Builder::system()
        .map_err(|error| format!("could not connect to the system Bluetooth bus: {error}"))?
        .method_timeout(timeout)
        .build()
        .map_err(|error| format!("could not connect to the system Bluetooth bus: {error}"))
}

fn managed_objects(connection: &Connection) -> Result<ManagedObjects, String> {
    let proxy = Proxy::new(connection, BLUEZ, "/", OBJECT_MANAGER)
        .map_err(|error| format!("could not address the Bluetooth service: {error}"))?;
    let reply = proxy
        .call_method("GetManagedObjects", &())
        .map_err(|error| format!("could not read the Bluetooth service: {error}"))?;
    reply
        .body()
        .deserialize::<ManagedObjects>()
        .map_err(|error| format!("could not decode Bluetooth service state: {error}"))
}

fn adapters_from(objects: &ManagedObjects) -> Result<Vec<Adapter>, String> {
    let mut adapters = Vec::new();
    for (path, interfaces) in objects {
        let Some(properties) = interfaces.get(ADAPTER) else {
            continue;
        };
        adapters.push(Adapter {
            path: path.to_string(),
            powered: bool_property(properties, "Powered")?,
        });
    }
    adapters.sort_by(|left, right| left.path.cmp(&right.path));
    Ok(adapters)
}

fn device_from(objects: &ManagedObjects, address: &str) -> Result<Option<Device>, String> {
    let mut matches = Vec::new();
    for (path, interfaces) in objects {
        let Some(properties) = interfaces.get(DEVICE) else {
            continue;
        };
        let device_address = string_property(properties, "Address")?;
        if device_address.eq_ignore_ascii_case(address) {
            matches.push((
                path.to_string(),
                Device {
                    address: device_address,
                    paired: bool_property(properties, "Paired")?,
                    trusted: bool_property(properties, "Trusted")?,
                    connected: bool_property(properties, "Connected")?,
                },
            ));
        }
    }
    matches.sort_by(|left, right| left.0.cmp(&right.0));
    Ok(matches.into_iter().next().map(|(_, device)| device))
}

fn device_location(
    objects: &ManagedObjects,
    address: &str,
) -> Result<Option<(String, String)>, String> {
    let mut locations = Vec::new();
    for (path, interfaces) in objects {
        let Some(properties) = interfaces.get(DEVICE) else {
            continue;
        };
        let device_address = string_property(properties, "Address")?;
        if !device_address.eq_ignore_ascii_case(address) {
            continue;
        }
        let device_path = path.to_string();
        let Some((adapter_path, _)) = device_path.rsplit_once('/') else {
            continue;
        };
        if objects
            .iter()
            .any(|(candidate_path, candidate_interfaces)| {
                candidate_path.to_string() == adapter_path
                    && candidate_interfaces.contains_key(ADAPTER)
            })
        {
            locations.push((adapter_path.to_string(), device_path));
        }
    }
    locations.sort();
    Ok(locations.into_iter().next())
}

fn bool_property(properties: &InterfaceProperties, name: &str) -> Result<bool, String> {
    let value = properties
        .get(name)
        .ok_or_else(|| format!("Bluetooth service omitted {name}"))?;
    bool::try_from(value.clone())
        .map_err(|error| format!("invalid Bluetooth property {name}: {error}"))
}

fn string_property(properties: &InterfaceProperties, name: &str) -> Result<String, String> {
    let value = properties
        .get(name)
        .ok_or_else(|| format!("Bluetooth service omitted {name}"))?;
    String::try_from(value.clone())
        .map_err(|error| format!("invalid Bluetooth property {name}: {error}"))
}

fn device_connection(
    address: &str,
    timeout: Duration,
    action: &str,
) -> Result<(Connection, String), String> {
    let deadline = Instant::now() + timeout;
    let objects_connection = system_connection(remaining(deadline, action)?)?;
    let objects = managed_objects(&objects_connection)?;
    let Some((_, device_path)) = device_location(&objects, address)? else {
        return Err(format!("no Bluetooth device was found at {address}"));
    };
    drop(objects_connection);

    let call_connection = system_connection(remaining(deadline, action)?)?;
    Ok((call_connection, device_path))
}

fn device_proxy<'a>(
    connection: &'a Connection,
    device_path: &'a str,
    address: &str,
) -> Result<Proxy<'a>, String> {
    Proxy::new(connection, BLUEZ, device_path, DEVICE)
        .map_err(|error| format!("could not address Bluetooth device {address}: {error}"))
}

fn remaining(deadline: Instant, action: &str) -> Result<Duration, String> {
    let remaining = deadline.saturating_duration_since(Instant::now());
    if remaining.is_zero() {
        Err(format!("{action} timed out"))
    } else {
        Ok(remaining)
    }
}

fn read_radios(root: &Path) -> Result<Vec<Radio>, String> {
    let entries = match fs::read_dir(root) {
        Ok(entries) => entries,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(error) => return Err(format!("could not list {}: {error}", root.display())),
    };

    let mut radios = Vec::new();
    for entry in entries {
        let entry = entry.map_err(|error| format!("could not read rfkill entry: {error}"))?;
        let path = entry.path();
        let kind = match read_text(&path.join("type")) {
            Ok(kind) => kind,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
            Err(error) => {
                return Err(format!(
                    "could not read {}: {error}",
                    path.join("type").display()
                ));
            }
        };
        if !kind.trim().eq_ignore_ascii_case(RFKILL_TYPE) {
            continue;
        }

        let name = read_text(&path.join("name"))
            .map_err(|error| format!("could not read {}: {error}", path.join("name").display()))?
            .trim()
            .to_string();
        let soft_blocked = read_rfkill_bool(&path.join("soft"))?;
        let hard_blocked = read_rfkill_bool(&path.join("hard"))?;
        radios.push(Radio {
            name,
            soft_blocked,
            hard_blocked,
        });
    }
    radios.sort_by(|left, right| left.name.cmp(&right.name));
    Ok(radios)
}

fn read_text(path: &Path) -> Result<String, std::io::Error> {
    fs::read_to_string(path)
}

fn read_rfkill_bool(path: &Path) -> Result<bool, String> {
    let value =
        read_text(path).map_err(|error| format!("could not read {}: {error}", path.display()))?;
    match value.trim() {
        "0" => Ok(false),
        "1" => Ok(true),
        other => Err(format!(
            "invalid rfkill state in {}: {other}",
            path.display()
        )),
    }
}

fn request_radio_block(binary: &OsString, blocked: bool) -> Result<(), String> {
    let action = if blocked { "block" } else { "unblock" };
    let output = Command::new(binary)
        .args([action, RFKILL_TYPE])
        .output()
        .map_err(|error| format!("could not run rfkill {action}: {error}"))?;
    if output.status.success() {
        return Ok(());
    }

    let reason = String::from_utf8_lossy(&output.stderr).trim().to_string();
    let reason = if reason.is_empty() {
        String::from_utf8_lossy(&output.stdout).trim().to_string()
    } else {
        reason
    };
    if reason.is_empty() {
        Err(format!("rfkill {action} exited with {}", output.status))
    } else {
        Err(reason)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn scratch() -> PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let path = std::env::temp_dir().join(format!("scottland-bluetooth-{nonce}"));
        fs::create_dir_all(&path).unwrap();
        path
    }

    #[test]
    fn reads_each_bluetooth_radio_and_ignores_other_types() {
        let root = scratch();
        let bt = root.join("rfkill0");
        let wlan = root.join("rfkill1");
        fs::create_dir_all(&bt).unwrap();
        fs::create_dir_all(&wlan).unwrap();
        fs::write(bt.join("type"), "bluetooth\n").unwrap();
        fs::write(bt.join("name"), "hci0\n").unwrap();
        fs::write(bt.join("soft"), "0\n").unwrap();
        fs::write(bt.join("hard"), "1\n").unwrap();
        fs::write(wlan.join("type"), "wlan\n").unwrap();
        fs::write(wlan.join("name"), "phy0\n").unwrap();
        fs::write(wlan.join("soft"), "0\n").unwrap();
        fs::write(wlan.join("hard"), "0\n").unwrap();

        assert_eq!(
            read_radios(&root).unwrap(),
            [Radio {
                name: "hci0".to_string(),
                soft_blocked: false,
                hard_blocked: true,
            }]
        );
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn missing_rfkill_sysfs_is_an_empty_radio_inventory() {
        let root = scratch().join("missing");
        assert!(read_radios(&root).unwrap().is_empty());
    }
}
