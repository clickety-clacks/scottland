use std::env;
use std::ffi::OsString;
use std::process::ExitCode;

use scottland_notify::hooks::SystemHooks;
use scottland_notify::notification::{
    DbusNotificationSender, TIME_GLYPH, dismiss, formatted_time_notice, parse_send_args, send,
    send_own_notice, time_notification, toggle_silencing,
};
use scottland_notify::reminder::{
    SystemLocalTimeFormatter, SystemReminderBackend, execute_reminder, parse_reminder_args,
};
use scottland_notify::waiter::SystemWaiter;
use scottland_notify::{SystemServerProbe, SystemSleeper, parse_wait_timeout, wait_for_server};

const USAGE: &str = "usage: scottland-notify <send|wait|time|reminder|notification-dismiss|toggle-notification-silencing> [arguments]\n\nCommands:\n  send / notification-send       Send a freedesktop notification\n  wait / notification-wait       Wait for the notification server to answer\n  time / notification-time       Send the current local date and time\n  reminder                       Set, show, or clear timed reminders\n  notification-dismiss           Dismiss shown notifications whose summary contains the text\n  toggle-notification-silencing  Turn notification do-not-disturb on or off";

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
            let mut hooks = SystemHooks::from_environment();
            let mut sender = DbusNotificationSender::default();
            if let Some(id) = send(&request, &mut hooks, &mut sender, &mut SystemWaiter)? {
                println!("{id}");
            }
            Ok(())
        }
        "wait" | "notification-wait" => {
            let timeout = parse_wait_timeout(command_args)?;
            if wait_for_server(
                timeout,
                &mut SystemServerProbe::default(),
                &mut SystemSleeper,
            ) {
                Ok(())
            } else {
                Err(format!(
                    "notification server did not answer within {timeout} seconds"
                ))
            }
        }
        "time" | "notification-time" if command_args.is_empty() => {
            let notice = time_notification(formatted_time_notice()?);
            let mut hooks = SystemHooks::from_environment();
            let mut sender = DbusNotificationSender::default();
            send_own_notice(notice, TIME_GLYPH, &mut hooks, &mut sender)?;
            Ok(())
        }
        "reminder" => {
            let command = parse_reminder_args(command_args)?;
            let mut backend = SystemReminderBackend::from_environment()?;
            let mut hooks = SystemHooks::from_environment();
            let mut sender = DbusNotificationSender::default();
            let mut formatter = SystemLocalTimeFormatter;
            let output = execute_reminder(
                &command,
                &mut backend,
                &mut hooks,
                &mut sender,
                &mut formatter,
            )?;
            if let Some(stdout) = output.stdout {
                println!("{stdout}");
            }
            Ok(())
        }
        "dismiss" | "notification-dismiss" => {
            dismiss(command_args, &mut SystemHooks::from_environment())
        }
        "silencing" | "toggle-notification-silencing" => {
            toggle_silencing(&mut SystemHooks::from_environment())
        }
        _ => Err(USAGE.to_string()),
    }
}

#[cfg(test)]
mod tests {
    use super::{USAGE, run};
    use std::ffi::OsString;

    #[test]
    fn dismiss_without_a_summary_is_a_usage_error() {
        for command in ["dismiss", "notification-dismiss"] {
            let error = run([OsString::from(command)]).unwrap_err();
            assert!(
                error.starts_with("usage: scottland-notify dismiss"),
                "{command}: {error}"
            );
        }
    }

    #[test]
    fn help_offers_no_close_command() {
        assert!(!USAGE.to_lowercase().contains("close"));
        assert!(!USAGE.contains("Deferred"));
    }
}
