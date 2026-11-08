//! The waiter route (receiver contract R-A6): with no receiver route, core sends standard actions
//! and leaves a process that runs the chosen command when the receiver reports it.

use std::fs::File;
use std::io::{Read, Write};
use std::os::fd::{FromRawFd, OwnedFd};
use std::os::unix::process::CommandExt;
use std::process::{Command, Stdio};

use zbus::MatchRule;
use zbus::blocking::{Connection, MessageIterator};

use crate::notification::{
    NOTIFICATIONS, NOTIFICATIONS_INTERFACE, NOTIFICATIONS_PATH, Notification, call_notify,
    offers_actions, session_bus,
};

pub trait Waiter {
    /// Send `notification` and, once the receiver accepts it, run the argv of the first action
    /// key it reports for that notification. Returns the notification id as soon as Notify does.
    fn deliver(
        &mut self,
        notification: &Notification,
        commands: &[(String, Vec<String>)],
    ) -> Result<u32, String>;
}

/// A forked process that sends Notify and listens on that same connection, so it cannot miss an
/// action the receiver reports for the id it was given.
pub struct SystemWaiter;

impl Waiter for SystemWaiter {
    fn deliver(
        &mut self,
        notification: &Notification,
        commands: &[(String, Vec<String>)],
    ) -> Result<u32, String> {
        let mut fds = [0; 2];
        // SAFETY: fds has room for the two descriptors pipe2 writes.
        if unsafe { libc::pipe2(fds.as_mut_ptr(), libc::O_CLOEXEC) } != 0 {
            return Err(format!(
                "could not start the notification waiter: {}",
                std::io::Error::last_os_error()
            ));
        }
        // SAFETY: pipe2 just returned these descriptors and nothing else owns them.
        let (read_end, write_end) =
            unsafe { (OwnedFd::from_raw_fd(fds[0]), OwnedFd::from_raw_fd(fds[1])) };

        // The fork happens before this process opens any bus connection, while it has only its
        // main thread, so the child starts from a consistent state.
        // SAFETY: see above; the child leaves only through _exit.
        match unsafe { libc::fork() } {
            -1 => Err(format!(
                "could not start the notification waiter: {}",
                std::io::Error::last_os_error()
            )),
            0 => {
                drop(read_end);
                let code = run_waiter(File::from(write_end), notification, commands);
                // SAFETY: ends the child without running the parent's exit handlers.
                unsafe { libc::_exit(code) }
            }
            child => {
                drop(write_end);
                let mut report = String::new();
                let _ = File::from(read_end).read_to_string(&mut report);
                if let Some(id) = report.strip_prefix("id ") {
                    return id
                        .trim()
                        .parse()
                        .map_err(|_| format!("notification waiter reported a bad id: {id}"));
                }
                let mut status = 0;
                // SAFETY: child is this process's own child, not yet reaped.
                unsafe { libc::waitpid(child, &mut status, 0) };
                Err(report.strip_prefix("error ").map_or_else(
                    || "notification waiter ended before Notify returned".to_string(),
                    str::to_string,
                ))
            }
        }
    }
}

fn run_waiter(
    mut report: File,
    notification: &Notification,
    commands: &[(String, Vec<String>)],
) -> i32 {
    detach();
    let (connection, mut signals, id) = match send(notification) {
        Ok(sent) => sent,
        Err(error) => {
            let _ = write!(report, "error {error}");
            return 1;
        }
    };
    let _ = write!(report, "id {id}");
    drop(report);

    for message in &mut signals {
        let Ok(message) = message else {
            return 0;
        };
        let header = message.header();
        match header.member().map(|member| member.as_str()) {
            Some("ActionInvoked") => {
                let Ok((invoked, key)) = message.body().deserialize::<(u32, String)>() else {
                    continue;
                };
                if invoked != id {
                    continue;
                }
                if let Some((_, argv)) = commands.iter().find(|(known, _)| *known == key) {
                    run_detached(argv);
                    return 0;
                }
            }
            Some("NotificationClosed") => {
                if matches!(message.body().deserialize::<(u32, u32)>(), Ok((closed, _)) if closed == id)
                {
                    return 0;
                }
            }
            _ => {}
        }
    }
    drop(connection);
    0
}

/// The waiter outlives the command that started it: it leaves that command's session and holds
/// none of its terminal or pipes open.
fn detach() {
    // SAFETY: plain system calls on this process's own session and descriptors.
    unsafe {
        libc::setsid();
        let null = libc::open(c"/dev/null".as_ptr(), libc::O_RDWR);
        if null >= 0 {
            for fd in 0..=2 {
                libc::dup2(null, fd);
            }
            if null > 2 {
                libc::close(null);
            }
        }
    }
}

fn send(notification: &Notification) -> Result<(Connection, MessageIterator, u32), String> {
    let connection = session_bus()?;
    // Subscribe before Notify, so an action reported right after it is not missed.
    let rule = MatchRule::builder()
        .msg_type(zbus::message::Type::Signal)
        .sender(NOTIFICATIONS)
        .and_then(|rule| rule.interface(NOTIFICATIONS_INTERFACE))
        .and_then(|rule| rule.path(NOTIFICATIONS_PATH))
        .map(|rule| rule.build())
        .map_err(|error| format!("could not describe notification signals: {error}"))?;
    let signals = MessageIterator::for_match_rule(rule, &connection, None)
        .map_err(|error| format!("could not listen for notification actions: {error}"))?;
    if !offers_actions(&connection)? {
        return Err("click commands and buttons are not available: the notification server does not offer actions".to_string());
    }
    let id = call_notify(&connection, notification)?;
    Ok((connection, signals, id))
}

/// Run argv once, as given (never through a shell), in its own session with no inherited stdio.
fn run_detached(argv: &[String]) {
    let Some((program, args)) = argv.split_first() else {
        return;
    };
    let mut command = Command::new(program);
    command
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    // SAFETY: setsid is async-signal-safe.
    unsafe {
        command.pre_exec(|| {
            libc::setsid();
            Ok(())
        });
    }
    let _ = command.spawn();
}
