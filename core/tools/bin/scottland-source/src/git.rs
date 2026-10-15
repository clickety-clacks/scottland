use crate::error::{Error, Result};
use std::ffi::{OsStr, OsString};
use std::path::Path;
use std::process::{Command, Output};

pub fn git(cwd: &Path, args: &[OsString]) -> Result<Output> {
    let output = Command::new("git")
        .arg("-c")
        .arg(format!("safe.directory={}", cwd.display()))
        .arg("-C")
        .arg(cwd)
        .args(args)
        .output()
        .map_err(|error| Error::new(format!("could not run git: {error}")))?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_owned();
        let command = args
            .iter()
            .map(|arg| arg.to_string_lossy())
            .collect::<Vec<_>>()
            .join(" ");
        return Err(Error::new(format!(
            "git {command} failed{}",
            if stderr.is_empty() {
                String::new()
            } else {
                format!(": {stderr}")
            }
        )));
    }
    Ok(output)
}

pub fn git_at(cwd: &Path, args: &[&str]) -> Result<Output> {
    git(cwd, &args.iter().map(OsString::from).collect::<Vec<_>>())
}

pub fn git_text(cwd: &Path, args: &[&str]) -> Result<String> {
    let output = git_at(cwd, args)?;
    String::from_utf8(output.stdout)
        .map(|text| text.trim().to_owned())
        .map_err(|_| Error::new("git returned non-UTF-8 text where text was required"))
}

pub fn git_status(cwd: &Path, args: &[OsString]) -> Result<Output> {
    Command::new("git")
        .arg("-c")
        .arg(format!("safe.directory={}", cwd.display()))
        .arg("-C")
        .arg(cwd)
        .args(args)
        .output()
        .map_err(|error| Error::new(format!("could not run git: {error}")))
}

pub fn git_command<I, S>(cwd: &Path, args: I) -> Result<Output>
where
    I: IntoIterator<Item = S>,
    S: AsRef<OsStr>,
{
    let args: Vec<OsString> = args
        .into_iter()
        .map(|arg| arg.as_ref().to_os_string())
        .collect();
    git(cwd, &args)
}

pub fn valid_object_id(value: &str) -> bool {
    matches!(value.len(), 40 | 64) && value.bytes().all(|byte| byte.is_ascii_hexdigit())
}
