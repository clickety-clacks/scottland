//! Scottland's directories, resolved the way the session scripts resolve them.
//!
//! The scripts use `${XDG_...:-default}`: a set, non-empty variable wins, otherwise the default
//! under `$HOME`. These functions follow the same rule so that a tool finds exactly the files the
//! scripts wrote.

use std::ffi::OsString;
use std::io;
use std::path::{Path, PathBuf};

unsafe extern "C" {
    safe fn getuid() -> u32;
}

/// Where packages install Scottland's hooks and helpers.
pub const PACKAGE_HOOKS: &str = "/usr/lib/scottland";

/// The calling user's id.
pub fn uid() -> u32 {
    getuid()
}

/// `$XDG_RUNTIME_DIR/scottland` (default `/run/user/UID/scottland`): per-boot files such as the
/// environments sessions record at startup.
pub fn runtime_dir() -> PathBuf {
    runtime_dir_from(&env)
}

/// `$XDG_CONFIG_HOME/scottland` (default `~/.config/scottland`): the user's configuration.
pub fn config_dir() -> io::Result<PathBuf> {
    xdg_dir(&env, "XDG_CONFIG_HOME", ".config")
}

/// `$XDG_STATE_HOME/scottland` (default `~/.local/state/scottland`): logs and remembered state.
pub fn state_dir() -> io::Result<PathBuf> {
    xdg_dir(&env, "XDG_STATE_HOME", ".local/state")
}

/// `$XDG_DATA_HOME/scottland` (default `~/.local/share/scottland`).
pub fn data_dir() -> io::Result<PathBuf> {
    xdg_dir(&env, "XDG_DATA_HOME", ".local/share")
}

/// The dev-mode directory `make dev-install` points the user's session at.
pub fn dev_dir() -> io::Result<PathBuf> {
    Ok(data_dir()?.join("dev"))
}

/// The hooks directory a session started now would use (`start-scottland`): `$SCOTTLAND_HOOKS`
/// inside a session; otherwise the dev-mode directory when one is installed, else the package's.
pub fn hooks_dir() -> PathBuf {
    hooks_dir_from(&env, &|path| path.is_dir())
}

fn env(key: &str) -> Option<OsString> {
    std::env::var_os(key)
}

fn non_empty(value: Option<OsString>) -> Option<OsString> {
    value.filter(|value| !value.is_empty())
}

fn runtime_dir_from(env: &dyn Fn(&str) -> Option<OsString>) -> PathBuf {
    let base = non_empty(env("XDG_RUNTIME_DIR"))
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(format!("/run/user/{}", uid())));
    base.join("scottland")
}

fn xdg_dir(
    env: &dyn Fn(&str) -> Option<OsString>,
    var: &str,
    under_home: &str,
) -> io::Result<PathBuf> {
    let base = match non_empty(env(var)) {
        Some(dir) => PathBuf::from(dir),
        None => home(env)?.join(under_home),
    };
    Ok(base.join("scottland"))
}

fn home(env: &dyn Fn(&str) -> Option<OsString>) -> io::Result<PathBuf> {
    non_empty(env("HOME"))
        .map(PathBuf::from)
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "HOME is not set"))
}

fn hooks_dir_from(
    env: &dyn Fn(&str) -> Option<OsString>,
    is_dir: &dyn Fn(&Path) -> bool,
) -> PathBuf {
    if let Some(hooks) = non_empty(env("SCOTTLAND_HOOKS")) {
        return PathBuf::from(hooks);
    }
    match xdg_dir(env, "XDG_DATA_HOME", ".local/share") {
        Ok(data) if is_dir(&data.join("dev/libexec")) => data.join("dev"),
        _ => PathBuf::from(PACKAGE_HOOKS),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn vars(pairs: &[(&str, &str)]) -> impl Fn(&str) -> Option<OsString> {
        let map: HashMap<String, OsString> = pairs
            .iter()
            .map(|(k, v)| (k.to_string(), OsString::from(v)))
            .collect();
        move |key| map.get(key).cloned()
    }

    #[test]
    fn runtime_dir_uses_xdg_runtime_dir() {
        let env = vars(&[("XDG_RUNTIME_DIR", "/run/user/1234")]);
        assert_eq!(
            runtime_dir_from(&env),
            PathBuf::from("/run/user/1234/scottland")
        );
    }

    #[test]
    fn runtime_dir_defaults_to_run_user_uid() {
        for env in [vars(&[]), vars(&[("XDG_RUNTIME_DIR", "")])] {
            assert_eq!(
                runtime_dir_from(&env),
                PathBuf::from(format!("/run/user/{}/scottland", uid()))
            );
        }
    }

    #[test]
    fn xdg_dirs_prefer_the_variable_then_home() {
        let set = vars(&[("HOME", "/h"), ("XDG_STATE_HOME", "/s")]);
        assert_eq!(
            xdg_dir(&set, "XDG_STATE_HOME", ".local/state").unwrap(),
            PathBuf::from("/s/scottland")
        );
        let empty = vars(&[("HOME", "/h"), ("XDG_STATE_HOME", "")]);
        assert_eq!(
            xdg_dir(&empty, "XDG_STATE_HOME", ".local/state").unwrap(),
            PathBuf::from("/h/.local/state/scottland")
        );
    }

    #[test]
    fn xdg_dirs_without_home_are_an_error() {
        let err = xdg_dir(&vars(&[]), "XDG_CONFIG_HOME", ".config").unwrap_err();
        assert_eq!(err.kind(), io::ErrorKind::NotFound);
    }

    #[test]
    fn hooks_dir_prefers_the_sessions_own() {
        let env = vars(&[("SCOTTLAND_HOOKS", "/somewhere"), ("HOME", "/h")]);
        assert_eq!(hooks_dir_from(&env, &|_| true), PathBuf::from("/somewhere"));
    }

    #[test]
    fn hooks_dir_uses_dev_mode_only_when_its_libexec_exists() {
        let env = vars(&[("HOME", "/h")]);
        let dev_libexec = PathBuf::from("/h/.local/share/scottland/dev/libexec");
        assert_eq!(
            hooks_dir_from(&env, &|path| path == dev_libexec),
            PathBuf::from("/h/.local/share/scottland/dev")
        );
        assert_eq!(
            hooks_dir_from(&env, &|_| false),
            PathBuf::from(PACKAGE_HOOKS)
        );
    }
}
