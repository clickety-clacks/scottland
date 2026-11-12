//! `screenshot` (CP4, CP5).

use crate::desktop::{Desktop, session_command};
use crate::folders::{create_unique, screenshot_dir};
use crate::image::{cut, png};
use crate::notify::{Notice, show};
use crate::pick::{PickMode, pick};
use scottland::session::Session;
use std::fs;
use std::io::Write;
use std::path::Path;
use std::process::Stdio;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Output {
    Both,
    Copy,
    Save,
}

pub fn run(session: &Session, mode: PickMode, output: Output) -> Result<(), String> {
    let desktop = Desktop::read(session)?;
    let Some(picked) = pick(session, &desktop, mode, true)? else {
        return Err("cancelled".to_string());
    };
    if picked.rect.width == 0 || picked.rect.height == 0 {
        return Err("the picked area is empty".to_string());
    }
    let image = cut(&picked.stills, picked.rect, desktop.scale_for(picked.rect));
    let bytes = png(session, &image)?;

    let saved = if output == Output::Copy {
        None
    } else {
        Some(save(&bytes))
    };
    let copied = if output == Output::Save {
        Ok(false)
    } else {
        copy_png(session, &bytes).map(|_| true)
    };

    let saved_path = match &saved {
        Some(Ok(path)) => Some(path.as_path()),
        _ => None,
    };
    let name = saved_path
        .and_then(Path::file_name)
        .map(|name| name.to_string_lossy().into_owned());
    match (&name, &copied) {
        (Some(name), Ok(true)) => show(
            session,
            &Notice::new("Screenshot saved and copied to clipboard").body(name),
        ),
        (Some(name), _) => show(session, &Notice::new("Screenshot saved").body(name)),
        (None, Ok(true)) => show(session, &Notice::new("Screenshot copied to clipboard")),
        (None, _) => {}
    }
    if let Some(path) = saved_path {
        println!("{}", path.display());
    }
    if let Some(Err(error)) = saved {
        return Err(error);
    }
    copied.map(|_| ())
}

fn save(bytes: &[u8]) -> Result<std::path::PathBuf, String> {
    let dir = screenshot_dir()?;
    fs::create_dir_all(&dir)
        .map_err(|error| format!("cannot create {}: {error}", dir.display()))?;
    let (path, mut file) = create_unique(&dir, "screenshot", "png")
        .map_err(|error| format!("cannot save the screenshot in {}: {error}", dir.display()))?;
    if let Err(error) = file.write_all(bytes).and_then(|_| file.sync_all()) {
        let _ = fs::remove_file(&path);
        return Err(format!("cannot write {}: {error}", path.display()));
    }
    Ok(path)
}

/// Put `bytes` on the clipboard as a PNG image.
fn copy_png(session: &Session, bytes: &[u8]) -> Result<(), String> {
    copy(session, &["--type", "image/png"], bytes)
}

/// Hand `bytes` to `wl-copy`. It serves the clipboard from a background process of its own and
/// returns once that process holds the data; its output is not read, since that process keeps
/// any pipe open for as long as it serves.
pub fn copy(session: &Session, options: &[&str], bytes: &[u8]) -> Result<(), String> {
    let mut child = session_command(session, "wl-copy")
        .args(options)
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|error| format!("cannot run wl-copy to copy to the clipboard: {error}"))?;
    let mut stdin = child.stdin.take().expect("stdin is piped");
    let written = stdin.write_all(bytes);
    drop(stdin);
    let status = child
        .wait()
        .map_err(|error| format!("cannot copy to the clipboard: {error}"))?;
    match (written, status.success()) {
        (Ok(()), true) => Ok(()),
        (Err(error), _) => Err(format!("cannot copy to the clipboard: {error}")),
        (_, false) => Err("cannot copy to the clipboard: wl-copy failed".to_string()),
    }
}
