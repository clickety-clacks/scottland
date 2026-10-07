use std::collections::{BTreeMap, HashMap};
use std::ffi::OsStr;
use std::time::{Duration, Instant};
use zbus::blocking::fdo::DBusProxy;
use zbus::blocking::{Proxy, connection::Builder as ConnectionBuilder};
use zbus::names::BusName;
use zbus::zvariant::OwnedValue;

const MPRIS_PREFIX: &str = "org.mpris.MediaPlayer2.";
const OBJECT_PATH: &str = "/org/mpris/MediaPlayer2";
const ROOT_INTERFACE: &str = "org.mpris.MediaPlayer2";
const PLAYER_INTERFACE: &str = "org.mpris.MediaPlayer2.Player";
const METHOD_TIMEOUT: Duration = Duration::from_millis(150);

#[derive(Clone, Debug, Default)]
pub struct Player {
    pub owner_pid: u32,
    pub desktop_entry: String,
    pub status: Option<String>,
    pub fields: BTreeMap<String, String>,
}

pub fn read(address: Option<&OsStr>, timeout_ms: u64) -> Result<Vec<Player>, String> {
    let address = address
        .and_then(OsStr::to_str)
        .filter(|address| !address.is_empty())
        .ok_or_else(|| "the selected session has no D-Bus address".to_string())?;
    let started = Instant::now();
    let timeout = Duration::from_millis(timeout_ms);
    if timeout.is_zero() {
        return Err("MPRIS query timed out after 0 ms".to_string());
    }
    let connection = ConnectionBuilder::address(address)
        .map_err(|error| format!("cannot create D-Bus connection: {error}"))?
        .method_timeout(METHOD_TIMEOUT.min(timeout))
        .build()
        .map_err(|error| format!("cannot connect to the session bus: {error}"))?;
    check_deadline(started, timeout_ms, timeout)?;
    let bus = DBusProxy::new(&connection)
        .map_err(|error| format!("cannot create D-Bus daemon proxy: {error}"))?;
    check_deadline(started, timeout_ms, timeout)?;
    let names = bus
        .list_names()
        .map_err(|error| format!("cannot list D-Bus names: {error}"))?;
    check_deadline(started, timeout_ms, timeout)?;
    let mut names: Vec<String> = names
        .into_iter()
        .map(|name| name.to_string())
        .filter(|name| name.starts_with(MPRIS_PREFIX))
        .collect();
    names.sort();

    let mut players = Vec::new();
    for name in names {
        check_deadline(started, timeout_ms, timeout)?;
        let bus_name = BusName::try_from(name.as_str())
            .map_err(|error| format!("invalid MPRIS bus name: {error}"))?;
        let owner_pid = match bus.get_connection_unix_process_id(bus_name) {
            Ok(pid) => pid,
            Err(_) => {
                check_deadline(started, timeout_ms, timeout)?;
                continue;
            }
        };
        check_deadline(started, timeout_ms, timeout)?;
        let root = match Proxy::new(&connection, name.as_str(), OBJECT_PATH, ROOT_INTERFACE) {
            Ok(proxy) => proxy,
            Err(_) => continue,
        };
        let desktop_entry = root
            .get_property::<String>("DesktopEntry")
            .unwrap_or_default();
        check_deadline(started, timeout_ms, timeout)?;
        let player = match Proxy::new(&connection, name.as_str(), OBJECT_PATH, PLAYER_INTERFACE) {
            Ok(proxy) => proxy,
            Err(_) => continue,
        };
        let metadata = player
            .get_property::<HashMap<String, OwnedValue>>("Metadata")
            .unwrap_or_default();
        check_deadline(started, timeout_ms, timeout)?;
        let status = player.get_property::<String>("PlaybackStatus").ok();
        check_deadline(started, timeout_ms, timeout)?;
        let mut fields = BTreeMap::new();
        if let Some(track) = metadata.get("xesam:title").and_then(string_value) {
            if !track.is_empty() {
                fields.insert("track".to_string(), track);
            }
        }
        if let Some(artists) = metadata.get("xesam:artist").and_then(string_list_value) {
            let artist = artists.join(", ");
            if !artist.is_empty() {
                fields.insert("artist".to_string(), artist);
            }
        }
        if let Some(album) = metadata.get("xesam:album").and_then(string_value) {
            if !album.is_empty() {
                fields.insert("album".to_string(), album);
            }
        }
        if let Some(status) = status.as_ref().filter(|status| !status.is_empty()) {
            fields.insert("status".to_string(), status.clone());
        }
        players.push(Player {
            owner_pid,
            desktop_entry,
            status,
            fields,
        });
    }
    Ok(players)
}

fn check_deadline(started: Instant, timeout_ms: u64, timeout: Duration) -> Result<(), String> {
    if started.elapsed() >= timeout {
        Err(format!("MPRIS query timed out after {timeout_ms} ms"))
    } else {
        Ok(())
    }
}

fn string_value(value: &OwnedValue) -> Option<String> {
    String::try_from(value.clone()).ok()
}

fn string_list_value(value: &OwnedValue) -> Option<Vec<String>> {
    Vec::<String>::try_from(value.clone()).ok()
}

#[cfg(test)]
mod tests {
    use super::Player;
    use std::collections::BTreeMap;

    #[test]
    fn track_metadata_is_decoded_from_an_owned_dbus_value() {
        let value = zbus::zvariant::OwnedValue::from("Song".to_string());
        assert_eq!(super::string_value(&value).as_deref(), Some("Song"));
    }

    #[test]
    fn an_empty_player_can_still_supply_playback_status() {
        let player = Player {
            owner_pid: 4,
            desktop_entry: String::new(),
            status: Some("Playing".to_string()),
            fields: BTreeMap::from([("status".to_string(), "Playing".to_string())]),
        };
        assert_eq!(
            player.fields.get("status").map(String::as_str),
            Some("Playing")
        );
        assert!(!player.fields.contains_key("track"));
    }
}
