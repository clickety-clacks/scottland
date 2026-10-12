use std::env;
use std::ffi::OsString;
use std::process::ExitCode;

use scottland_notify::notification::{
    DbusNotificationSender, NotificationSender, formatted_time_notice, parse_send_args, send,
    time_notification,
};
use scottland_notify::reminder::{
    SystemLocalTimeFormatter, SystemReminderBackend, execute_reminder, parse_reminder_args,
};
use scottland_notify::{SystemServerProbe, SystemSleeper, parse_wait_timeout, wait_for_server};

const USAGE: &str = "usage: scottland-notify <send|wait|time|reminder|notification-dismiss|toggle-notification-silencing> [arguments]\n\nCommands:\n  send / notification-send       Send a freedesktop notification\n  wait / notification-wait       Wait for the notification server to answer\n  time / notification-time       Send the current local date and time\n  reminder                       Set, show, or clear timed reminders\n  notification-dismiss           Deferred pending an approved receiver contract\n  toggle-notification-silencing  Deferred pending an approved receiver contract";

fn main() -> ExitCode {
    match run(env::args_os().skip(1)) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("scottland-notify: {error}");
            ExitCode::FAILURE
        }
    }
}

fn run(args: impl IntoIterator<Item = OsString>) -> Result<(), String> {
    let args = args
        .into_iter()
        .map(|argument| {
            argument
                .into_string()
                .map_err(|_| "arguments must be valid UTF-8".to_string())
        })
        .collect::<Result<Vec<_>, _>>()?;
    let Some(command) = args.first().map(String::as_str) else {
        return Err(USAGE.to_string());
    };
    let command_args = &args[1..];

    match command {
        "-h" | "--help" | "help" if command_args.is_empty() => {
            println!("{USAGE}");
            Ok(())
        }
        "-V" | "--version" if command_args.is_empty() => {
            println!("scottland-notify {}", env!("CARGO_PKG_VERSION"));
            Ok(())
        }
        "send" | "notification-send" => {
            let request = parse_send_args(command_args)?;
            let mut sender = DbusNotificationSender::default();
            if let Some(id) = send(&request, &mut sender)? {
                println!("{id}");
            }
            Ok(())
        }
        "wait" | "notification-wait" => {
            let timeout = parse_wait_timeout(command_args)?;
            if wait_for_server(timeout, &mut SystemServerProbe::default(), &mut SystemSleeper) {
                Ok(())
            } else {
                Err(format!("notification server did not answer within {timeout} seconds"))
            }
        }
        "time" | "notification-time" if command_args.is_empty() => {
            let request = time_notification(formatted_time_notice()?);
            let mut sender = DbusNotificationSender::default();
            sender.notify(&request)?;
            Ok(())
        }
        "reminder" => {
            let command = parse_reminder_args(command_args)?;
            let mut backend = SystemReminderBackend::from_environment()?;
            let mut sender = DbusNotificationSender::default();
            let mut formatter = SystemLocalTimeFormatter;
            let output = execute_reminder(&command, &mut backend, &mut sender, &mut formatter)?;
            if let Some(stdout) = output.stdout {
                println!("{stdout}");
            }
            Ok(())
        }
        "dismiss" | "notification-dismiss" => Err(
            "summary dismissal is deferred until an approved notification receiver contract exists"
                .to_string(),
        ),
        "silencing" | "toggle-notification-silencing" => Err(
            "notification silencing is deferred until an approved notification receiver contract exists"
                .to_string(),
        ),
        _ => Err(USAGE.to_string()),
    }
}

#[cfg(test)]
mod tests {
    use super::run;
    use std::ffi::OsString;

    #[test]
    fn receiver_dependent_commands_fail_instead_of_succeeding_as_noops() {
        for command in [
            "dismiss",
            "notification-dismiss",
            "silencing",
            "toggle-notification-silencing",
        ] {
            let error = run([OsString::from(command)]).unwrap_err();
            assert!(error.contains("deferred"), "{command}: {error}");
        }
    }
}
