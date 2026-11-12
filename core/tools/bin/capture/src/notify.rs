//! Desktop notices (CP12): sent and never waited on for the result. With no notification server,
//! or no `notify-send`, a notice is dropped and the command goes on exactly as before.

use crate::desktop::session_command;
use crate::run::wait_bounded;
use scottland::session::Session;
use std::path::Path;
use std::process::Stdio;
use std::time::Duration;

pub struct Notice<'a> {
    pub summary: &'a str,
    pub body: Option<&'a str>,
    pub critical: bool,
    pub icon: Option<&'a Path>,
}

impl<'a> Notice<'a> {
    pub fn new(summary: &'a str) -> Self {
        Self {
            summary,
            body: None,
            critical: false,
            icon: None,
        }
    }

    pub fn body(self, body: &'a str) -> Self {
        Self {
            body: Some(body),
            ..self
        }
    }

    pub fn critical(self) -> Self {
        Self {
            critical: true,
            ..self
        }
    }
}

/// Show `notice`, waiting at most a few seconds for the server to take it.
pub fn show(session: &Session, notice: &Notice) {
    let Ok(mut child) = command(session, notice, &[])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
    else {
        return;
    };
    wait_bounded(&mut child, Duration::from_secs(3));
}

/// Show `notice` with an "Open" action and wait up to `limit` for the user to click it. True
/// when they did.
pub fn show_and_wait_for_click(session: &Session, notice: &Notice, limit: Duration) -> bool {
    let mut command = command(
        session,
        notice,
        &["--wait", "--action=default=Open", "--expire-time=10000"],
    );
    command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    let Ok(mut child) = command.spawn() else {
        return false;
    };
    let mut stdout = child.stdout.take().expect("stdout is piped");
    let reader = std::thread::spawn(move || {
        let mut text = String::new();
        let _ = std::io::Read::read_to_string(&mut stdout, &mut text);
        text
    });
    let finished = wait_bounded(&mut child, limit).is_some();
    let answer = reader.join().unwrap_or_default();
    finished && answer.trim() == "default"
}

fn command(session: &Session, notice: &Notice, options: &[&str]) -> std::process::Command {
    let mut command = session_command(session, "notify-send");
    command.arg("--app-name=scottland-capture").args(options);
    if notice.critical {
        command.arg("--urgency=critical");
    }
    if let Some(icon) = notice.icon {
        command.arg("--icon").arg(icon);
    }
    command.arg("--").arg(notice.summary);
    if let Some(body) = notice.body {
        command.arg(body);
    }
    command
}
