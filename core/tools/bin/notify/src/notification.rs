use std::collections::HashMap;
use std::process::Command;

use zbus::blocking::{Connection, Proxy};
use zbus::zvariant::{OwnedValue, Str};

const NOTIFICATIONS: &str = "org.freedesktop.Notifications";
const NOTIFICATIONS_PATH: &str = "/org/freedesktop/Notifications";
const NOTIFICATIONS_INTERFACE: &str = "org.freedesktop.Notifications";
const CORE_APP_NAME: &str = "scottland-notify";
const SEND_USAGE: &str = "usage: scottland-notify send [-g GLYPH] [-u low|normal|critical] [--app-name NAME] [-i ICON] [--image PATH] [-t MS] [-r ID] [-p] HEADLINE [DESCRIPTION]";

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Urgency {
    #[default]
    Low,
    Normal,
    Critical,
}

impl Urgency {
    fn byte(self) -> u8 {
        match self {
            Self::Low => 0,
            Self::Normal => 1,
            Self::Critical => 2,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Notification {
    pub app_name: String,
    pub replaces_id: u32,
    pub app_icon: String,
    pub summary: String,
    pub body: String,
    pub urgency: Urgency,
    /// Parsed adapter input; the Core sender refuses it until an approved receiver contract exists.
    pub glyph: Option<String>,
    pub image: Option<String>,
    pub expire_timeout: i32,
}

impl Notification {
    pub fn action(summary: impl Into<String>, body: impl Into<String>) -> Self {
        Self {
            app_name: CORE_APP_NAME.to_string(),
            replaces_id: 0,
            app_icon: String::new(),
            summary: summary.into(),
            body: body.into(),
            urgency: Urgency::Low,
            glyph: None,
            image: None,
            expire_timeout: -1,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SendRequest {
    pub notification: Notification,
    pub print_id: bool,
}

pub trait NotificationSender {
    fn notify(&mut self, notification: &Notification) -> Result<u32, String>;
}

#[derive(Default)]
pub struct DbusNotificationSender {
    connection: Option<Connection>,
}

impl NotificationSender for DbusNotificationSender {
    fn notify(&mut self, notification: &Notification) -> Result<u32, String> {
        ensure_core_sender_supports(notification)?;
        if self.connection.is_none() {
            self.connection = Some(
                Connection::session()
                    .map_err(|error| format!("could not connect to the session bus: {error}"))?,
            );
        }
        let connection = self.connection.as_ref().expect("connection was just set");
        let proxy = Proxy::new(
            connection,
            NOTIFICATIONS,
            NOTIFICATIONS_PATH,
            NOTIFICATIONS_INTERFACE,
        )
        .map_err(|error| format!("could not address the notification server: {error}"))?;

        let mut hints = HashMap::<String, OwnedValue>::new();
        hints.insert(
            "urgency".to_string(),
            OwnedValue::from(notification.urgency.byte()),
        );
        if let Some(image) = &notification.image {
            hints.insert(
                "image-path".to_string(),
                OwnedValue::from(Str::from(image.clone())),
            );
        }

        let actions: Vec<String> = Vec::new();
        let reply = proxy
            .call_method(
                "Notify",
                &(
                    notification.app_name.as_str(),
                    notification.replaces_id,
                    notification.app_icon.as_str(),
                    notification.summary.as_str(),
                    notification.body.as_str(),
                    &actions,
                    &hints,
                    notification.expire_timeout,
                ),
            )
            .map_err(|error| format!("notification server rejected Notify: {error}"))?;
        reply
            .body()
            .deserialize::<u32>()
            .map_err(|error| format!("could not read notification id: {error}"))
    }
}

pub fn parse_send_args(args: &[String]) -> Result<SendRequest, String> {
    let mut request = SendRequest {
        notification: Notification {
            app_name: CORE_APP_NAME.to_string(),
            replaces_id: 0,
            app_icon: String::new(),
            summary: String::new(),
            body: String::new(),
            urgency: Urgency::Low,
            glyph: None,
            image: None,
            expire_timeout: -1,
        },
        print_id: false,
    };

    let mut index = 0;
    while index < args.len() {
        let consumed = parse_option(args, index, &mut request)?;
        if consumed == 0 {
            break;
        }
        index += consumed;
    }
    if args.get(index).is_some_and(|argument| argument == "--exec") {
        return Err(
            "click actions are unavailable until a notification receiver contract is approved"
                .to_string(),
        );
    }
    let Some(headline) = args.get(index) else {
        return Err(SEND_USAGE.to_string());
    };
    request.notification.summary = headline.clone();
    index += 1;

    if let Some(candidate) = args.get(index) {
        if !is_send_option(candidate) && candidate != "--exec" {
            request.notification.body = candidate.clone();
            index += 1;
        }
    }

    while index < args.len() {
        if args[index] == "--exec" {
            return Err(
                "click actions are unavailable until a notification receiver contract is approved"
                    .to_string(),
            );
        }
        let consumed = parse_option(args, index, &mut request)?;
        if consumed == 0 {
            return Err(format!("unknown option: {}\n{SEND_USAGE}", args[index]));
        }
        index += consumed;
    }

    Ok(request)
}

pub(super) fn ensure_core_sender_supports(notification: &Notification) -> Result<(), String> {
    if notification.glyph.is_some() {
        return Err(
            "glyph rendering is deferred until an approved receiver contract exists".into(),
        );
    }
    Ok(())
}

fn parse_option(args: &[String], index: usize, request: &mut SendRequest) -> Result<usize, String> {
    let argument = &args[index];
    if argument == "-p" || argument == "--print-id" {
        request.print_id = true;
        return Ok(1);
    }

    let (option, inline_value) = argument
        .split_once('=')
        .map_or((argument.as_str(), None), |(option, value)| {
            (option, Some(value))
        });
    let wants_value = matches!(
        option,
        "-g" | "--glyph"
            | "-u"
            | "--urgency"
            | "--app-name"
            | "-i"
            | "--icon"
            | "--image"
            | "-r"
            | "--replace-id"
            | "-t"
            | "--expire-time"
    );
    if !wants_value {
        return Ok(0);
    }

    let (value, consumed) = if let Some(value) = inline_value {
        (value, 1)
    } else {
        let value = args
            .get(index + 1)
            .ok_or_else(|| format!("missing value for {option}"))?;
        (value.as_str(), 2)
    };
    match option {
        "-g" | "--glyph" => request.notification.glyph = Some(value.to_string()),
        "-u" | "--urgency" => {
            request.notification.urgency = match value {
                "low" => Urgency::Low,
                "normal" => Urgency::Normal,
                "critical" => Urgency::Critical,
                _ => {
                    return Err(format!(
                        "unknown urgency: {value} (use low, normal, or critical)"
                    ));
                }
            }
        }
        "--app-name" => request.notification.app_name = value.to_string(),
        "-i" | "--icon" => request.notification.app_icon = value.to_string(),
        "--image" => request.notification.image = Some(value.to_string()),
        "-r" | "--replace-id" => {
            request.notification.replaces_id = value
                .parse::<u32>()
                .map_err(|_| format!("invalid {option} value (numeric id expected): {value}"))?;
        }
        "-t" | "--expire-time" => {
            request.notification.expire_timeout = value
                .parse::<i32>()
                .map_err(|_| format!("invalid {option} value (milliseconds expected): {value}"))?;
        }
        _ => unreachable!("only known options reach the parser"),
    }
    Ok(consumed)
}

fn is_send_option(argument: &str) -> bool {
    matches!(
        argument
            .split_once('=')
            .map_or(argument, |(option, _)| option),
        "-g" | "--glyph"
            | "-u"
            | "--urgency"
            | "--app-name"
            | "-i"
            | "--icon"
            | "--image"
            | "-r"
            | "--replace-id"
            | "-t"
            | "--expire-time"
            | "-p"
            | "--print-id"
            | "--exec"
    )
}

pub fn send(
    request: &SendRequest,
    sender: &mut impl NotificationSender,
) -> Result<Option<u32>, String> {
    let id = sender.notify(&request.notification)?;
    Ok(request.print_id.then_some(id))
}

pub fn time_notification(summary: impl Into<String>) -> Notification {
    Notification::action(summary, "")
}

pub fn formatted_time_notice() -> Result<String, String> {
    let output = Command::new("date")
        .arg("+%A %H:%M  ·  %d %B %Y  ·  Week %V")
        .output()
        .map_err(|error| format!("could not run date: {error}"))?;
    if !output.status.success() {
        return Err("date could not format the current local time".to_string());
    }
    String::from_utf8(output.stdout)
        .map(|output| output.trim_end().to_string())
        .map_err(|_| "date returned non-text output".to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[derive(Default)]
    struct FakeSender {
        calls: Vec<Notification>,
    }

    impl NotificationSender for FakeSender {
        fn notify(&mut self, notification: &Notification) -> Result<u32, String> {
            self.calls.push(notification.clone());
            Ok(42)
        }
    }

    #[test]
    fn parses_documented_send_options_before_and_after_the_headline() {
        let request = parse_send_args(&[
            "--app-name".into(),
            "build-status".into(),
            "-g".into(),
            "✓".into(),
            "-u".into(),
            "critical".into(),
            "Finished".into(),
            "-50% remaining".into(),
            "--image=/tmp/icon.png".into(),
            "-i".into(),
            "application-x-executable".into(),
            "-t".into(),
            "0".into(),
            "-r".into(),
            "7".into(),
            "-p".into(),
        ])
        .unwrap();

        assert_eq!(request.notification.app_name, "build-status");
        assert_eq!(request.notification.summary, "Finished");
        assert_eq!(request.notification.body, "-50% remaining");
        assert_eq!(request.notification.urgency, Urgency::Critical);
        assert_eq!(request.notification.glyph.as_deref(), Some("✓"));
        assert_eq!(request.notification.image.as_deref(), Some("/tmp/icon.png"));
        assert_eq!(request.notification.app_icon, "application-x-executable");
        assert_eq!(request.notification.expire_timeout, 0);
        assert_eq!(request.notification.replaces_id, 7);
        assert!(request.print_id);
    }

    #[test]
    fn rejects_unknown_urgency_malformed_options_and_click_actions() {
        assert!(parse_send_args(&["-u".into(), "urgent".into(), "Title".into()]).is_err());
        assert!(parse_send_args(&["-r".into(), "not-an-id".into(), "Title".into()]).is_err());
        assert!(parse_send_args(&["Title".into(), "Body".into(), "--unknown".into()]).is_err());
        assert!(
            parse_send_args(&["Title".into(), "--exec".into(), "run".into()])
                .unwrap_err()
                .contains("receiver contract")
        );
    }

    #[test]
    fn sends_the_parsed_notification_and_prints_only_a_requested_id() {
        let request = parse_send_args(&["-p".into(), "Done".into()]).unwrap();
        let mut sender = FakeSender::default();

        assert_eq!(send(&request, &mut sender).unwrap(), Some(42));
        assert_eq!(sender.calls, [request.notification]);

        let request = parse_send_args(&["Done".into()]).unwrap();
        assert_eq!(send(&request, &mut sender).unwrap(), None);
        assert_eq!(sender.calls.len(), 2);
    }

    #[test]
    fn default_identity_is_core_owned_and_callers_can_override_it() {
        assert_eq!(Notification::action("Done", "").app_name, CORE_APP_NAME);
        assert_eq!(
            parse_send_args(&["Done".into()])
                .unwrap()
                .notification
                .app_name,
            CORE_APP_NAME
        );
        assert_eq!(
            parse_send_args(&["--app-name".into(), "caller-app".into(), "Done".into()])
                .unwrap()
                .notification
                .app_name,
            "caller-app"
        );
    }

    #[test]
    fn core_sender_rejects_deferred_glyph_before_opening_the_bus() {
        let mut notification = Notification::action("Done", "");
        notification.glyph = Some("✓".into());
        let mut sender = DbusNotificationSender::default();

        let error = sender.notify(&notification).unwrap_err();

        assert!(error.contains("receiver contract"));
        assert!(sender.connection.is_none());
    }

    #[test]
    fn time_notice_uses_core_identity_without_deferred_glyph_rendering() {
        let notification = time_notification("Monday 09:30");

        assert_eq!(notification.summary, "Monday 09:30");
        assert_eq!(notification.app_name, CORE_APP_NAME);
        assert_eq!(notification.urgency, Urgency::Low);
        assert!(notification.glyph.is_none());
    }
}
