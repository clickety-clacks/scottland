//! Where screenshots and recordings go, and what their files are called (CP5, CP8).

use scottland::dirs;
use std::env;
use std::fs::{self, File, OpenOptions, Permissions};
use std::io;
use std::os::unix::fs::{DirBuilderExt, PermissionsExt};
use std::path::{Path, PathBuf};

/// Where the command keeps its own short-lived files and the running recording's record (CP9):
/// Scottland's folder in `XDG_RUNTIME_DIR`, else a folder only this user can open under the
/// state folder. Never `/tmp`.
pub fn private_parent() -> Result<PathBuf, String> {
    let runtime = env::var_os("XDG_RUNTIME_DIR").is_some_and(|value| !value.is_empty());
    let dir = if runtime {
        dirs::runtime_dir()
    } else {
        dirs::state_dir()
            .map_err(|error| format!("no private folder: {error}"))?
            .join("capture")
    };
    fs::DirBuilder::new()
        .recursive(true)
        .mode(0o700)
        .create(&dir)
        .map_err(|error| format!("cannot create {}: {error}", dir.display()))?;
    if !runtime {
        // The folder may predate this command; make sure it is private either way.
        fs::set_permissions(&dir, Permissions::from_mode(0o700))
            .map_err(|error| format!("cannot protect {}: {error}", dir.display()))?;
    }
    Ok(dir)
}

/// `SCOTTLAND_SCREENSHOT_DIR`, else `Screenshots` in the pictures folder.
pub fn screenshot_dir() -> Result<PathBuf, String> {
    if let Some(dir) = env_path("SCOTTLAND_SCREENSHOT_DIR") {
        return Ok(dir);
    }
    Ok(user_dir("XDG_PICTURES_DIR", "Pictures")?.join("Screenshots"))
}

/// `SCOTTLAND_RECORDING_DIR`, else the videos folder.
pub fn recording_dir() -> Result<PathBuf, String> {
    if let Some(dir) = env_path("SCOTTLAND_RECORDING_DIR") {
        return Ok(dir);
    }
    user_dir("XDG_VIDEOS_DIR", "Videos")
}

/// A file in `dir` named `PREFIX-YYYY-MM-DD_HH-MM-SS.EXT`, or with `-2`, `-3` and so on before
/// the extension when that name is taken; created empty, never replacing a file.
pub fn create_unique(dir: &Path, prefix: &str, extension: &str) -> io::Result<(PathBuf, File)> {
    create_unique_stamped(dir, prefix, extension, &local_timestamp())
}

fn create_unique_stamped(
    dir: &Path,
    prefix: &str,
    extension: &str,
    stamp: &str,
) -> io::Result<(PathBuf, File)> {
    for attempt in 1_u32.. {
        let path = dir.join(file_name(prefix, stamp, attempt, extension));
        match OpenOptions::new().write(true).create_new(true).open(&path) {
            Ok(file) => return Ok((path, file)),
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error),
        }
    }
    unreachable!("the attempt counter never runs out before the folder does")
}

fn file_name(prefix: &str, stamp: &str, attempt: u32, extension: &str) -> String {
    if attempt == 1 {
        format!("{prefix}-{stamp}.{extension}")
    } else {
        format!("{prefix}-{stamp}-{attempt}.{extension}")
    }
}

/// The name a new file in `dir` would get, without creating it. The recorder creates its own
/// file, so the name is chosen just before it starts.
pub fn unused_name(dir: &Path, prefix: &str, extension: &str) -> PathBuf {
    let stamp = local_timestamp();
    (1_u32..)
        .map(|attempt| dir.join(file_name(prefix, &stamp, attempt, extension)))
        .find(|path| fs::symlink_metadata(path).is_err())
        .expect("the attempt counter never runs out before the folder does")
}

fn env_path(name: &str) -> Option<PathBuf> {
    env::var_os(name)
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
}

fn home() -> Result<PathBuf, String> {
    env_path("HOME").ok_or_else(|| "HOME is not set".to_string())
}

/// An XDG user directory from `user-dirs.dirs`, else `~/FALLBACK`.
fn user_dir(key: &str, fallback: &str) -> Result<PathBuf, String> {
    let home = home()?;
    let config = env_path("XDG_CONFIG_HOME").unwrap_or_else(|| home.join(".config"));
    let configured = fs::read_to_string(config.join("user-dirs.dirs"))
        .ok()
        .and_then(|text| parse_user_dir(&text, key, &home));
    Ok(configured.unwrap_or_else(|| home.join(fallback)))
}

/// One `KEY="$HOME/Dir"` or `KEY="/abs/dir"` line of `user-dirs.dirs`.
fn parse_user_dir(text: &str, key: &str, home: &Path) -> Option<PathBuf> {
    text.lines().rev().find_map(|line| {
        let value = line.trim().strip_prefix(key)?.strip_prefix('=')?;
        let value = value.trim().strip_prefix('"')?.strip_suffix('"')?;
        let path = if let Some(rest) = value.strip_prefix("$HOME") {
            home.join(rest.trim_start_matches('/'))
        } else if value.starts_with('/') {
            PathBuf::from(value)
        } else {
            return None;
        };
        // `$HOME/` alone means the folder is disabled; the default applies.
        (path != home).then_some(path)
    })
}

/// The current local time as `YYYY-MM-DD_HH-MM-SS`.
fn local_timestamp() -> String {
    // SAFETY: time and localtime_r are called with valid pointers to owned values; glibc's
    // localtime_r reads the time zone itself.
    unsafe {
        let now = libc::time(std::ptr::null_mut());
        let mut tm: libc::tm = std::mem::zeroed();
        if libc::localtime_r(&now, &mut tm).is_null() {
            return now.to_string();
        }
        format!(
            "{:04}-{:02}-{:02}_{:02}-{:02}-{:02}",
            tm.tm_year + 1900,
            tm.tm_mon + 1,
            tm.tm_mday,
            tm.tm_hour,
            tm.tm_min,
            tm.tm_sec
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_user_dirs_lines() {
        let text = "# comment\nXDG_PICTURES_DIR=\"$HOME/Bilder\"\nXDG_VIDEOS_DIR=\"/media/videos\"\nXDG_MUSIC_DIR=\"$HOME/\"\n";
        let home = Path::new("/home/u");
        assert_eq!(
            parse_user_dir(text, "XDG_PICTURES_DIR", home),
            Some(PathBuf::from("/home/u/Bilder"))
        );
        assert_eq!(
            parse_user_dir(text, "XDG_VIDEOS_DIR", home),
            Some(PathBuf::from("/media/videos"))
        );
        assert_eq!(parse_user_dir(text, "XDG_MUSIC_DIR", home), None);
        assert_eq!(parse_user_dir(text, "XDG_DESKTOP_DIR", home), None);
    }

    #[test]
    fn timestamp_has_the_cp5_shape() {
        let stamp = local_timestamp();
        assert_eq!(stamp.len(), 19, "{stamp}");
        assert_eq!(&stamp[4..5], "-");
        assert_eq!(&stamp[10..11], "_");
        assert_eq!(&stamp[13..14], "-");
    }

    #[test]
    fn same_second_names_get_suffixes() {
        let dir =
            std::env::temp_dir().join(format!("scottland-capture-test-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        let names: Vec<String> = (0..3)
            .map(|_| {
                let (path, _) =
                    create_unique_stamped(&dir, "screenshot", "png", "2026-10-07_14-22-05")
                        .unwrap();
                path.file_name().unwrap().to_string_lossy().into_owned()
            })
            .collect();
        fs::remove_dir_all(&dir).unwrap();
        assert_eq!(
            names,
            [
                "screenshot-2026-10-07_14-22-05.png",
                "screenshot-2026-10-07_14-22-05-2.png",
                "screenshot-2026-10-07_14-22-05-3.png",
            ]
        );
    }
}
