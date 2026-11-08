use std::collections::HashMap;
use std::process::Command;

use zbus::blocking::{Connection, Proxy};
use zbus::zvariant::{OwnedValue, Str};

use crate::hooks::{self, HookAnswer, Hooks, UNSUPPORTED};
use crate::waiter::Waiter;

pub(crate) const NOTIFICATIONS: &str = "org.freedesktop.Notifications";
pub(crate) const NOTIFICATIONS_PATH: &str = "/org/freedesktop/Notifications";
pub(crate) const NOTIFICATIONS_INTERFACE: &str = "org.freedesktop.Notifications";
const CORE_APP_NAME: &str = "scottland-notify";
const SEND_USAGE: &str = "usage: scottland-notify send [-g GLYPH] [-u low|normal|critical] [--app-name NAME] [-i ICON] [--image PATH] [-t MS] [-r ID] [-p] [--transient] HEADLINE [DESCRIPTION] [--exec PROGRAM [ARGS...] | --action LABEL PROGRAM [WORDS...]... [--with ARGS...]]";
const DISMISS_USAGE: &str = "usage: scottland-notify dismiss SUMMARY";
const ACTION_NEEDS_COMMAND: &str =
    "--action needs a label and a command: --action <label> <program> [words...]";

/// The glyph core's own time notice asks for.
pub const TIME_GLYPH: &str = "\u{f43a}";

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
    pub image: Option<String>,
    pub expire_timeout: i32,
    /// The standard `transient` hint.
    pub transient: bool,
    /// String hints a notify hook supplied: the key that carries a glyph, or a receiver route.
    pub hints: Vec<(String, String)>,
    /// Standard action keys and their labels, for the waiter route.
    pub actions: Vec<(String, String)>,
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
            image: None,
            expire_timeout: -1,
            transient: false,
            hints: Vec::new(),
            actions: Vec::new(),
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SendRequest {
    pub notification: Notification,
    pub glyph: Option<String>,
    pub click: Option<ClickRequest>,
    pub print_id: bool,
}

/// What the card runs when clicked, and its buttons. With buttons, the click is button 1's argv.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ClickRequest {
    pub click: Vec<String>,
    pub buttons: Vec<Button>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Button {
    pub label: String,
    pub argv: Vec<String>,
}

impl ClickRequest {
    /// The `action-route` arguments: `click N A1..AN`, then `button LABEL N A1..AN` per button.
    fn route_args(&self) -> Vec<String> {
        let mut args = vec!["click".to_string(), self.click.len().to_string()];
        args.extend(self.click.iter().cloned());
        for button in &self.buttons {
            args.push("button".to_string());
            args.push(button.label.clone());
            args.push(button.argv.len().to_string());
            args.extend(button.argv.iter().cloned());
        }
        args
    }
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
        if self.connection.is_none() {
            self.connection = Some(session_bus()?);
        }
        call_notify(
            self.connection.as_ref().expect("connection was just set"),
            notification,
        )
    }
}

pub(crate) fn session_bus() -> Result<Connection, String> {
    Connection::session().map_err(|error| format!("could not connect to the session bus: {error}"))
}

fn notifications_proxy(connection: &Connection) -> Result<Proxy<'_>, String> {
    Proxy::new(
        connection,
        NOTIFICATIONS,
        NOTIFICATIONS_PATH,
        NOTIFICATIONS_INTERFACE,
    )
    .map_err(|error| format!("could not address the notification server: {error}"))
}

pub(crate) fn call_notify(
    connection: &Connection,
    notification: &Notification,
) -> Result<u32, String> {
    let proxy = notifications_proxy(connection)?;

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
    if notification.transient {
        hints.insert("transient".to_string(), OwnedValue::from(true));
    }
    for (key, value) in &notification.hints {
        hints.insert(key.clone(), OwnedValue::from(Str::from(value.clone())));
    }

    let actions: Vec<&str> = notification
        .actions
        .iter()
        .flat_map(|(key, label)| [key.as_str(), label.as_str()])
        .collect();
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

/// Whether the notification server advertises standard `actions`.
pub(crate) fn offers_actions(connection: &Connection) -> Result<bool, String> {
    let reply = notifications_proxy(connection)?
        .call_method("GetCapabilities", &())
        .map_err(|error| format!("notification server did not answer GetCapabilities: {error}"))?;
    let capabilities = reply
        .body()
        .deserialize::<Vec<String>>()
        .map_err(|error| format!("could not read notification server capabilities: {error}"))?;
    Ok(capabilities
        .iter()
        .any(|capability| capability == "actions"))
}

pub fn parse_send_args(args: &[String]) -> Result<SendRequest, String> {
    let mut request = SendRequest {
        notification: Notification::action("", ""),
        glyph: None,
        click: None,
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
    let Some(headline) = args.get(index) else {
        return Err(SEND_USAGE.to_string());
    };
    request.notification.summary = headline.clone();
    index += 1;

    if let Some(candidate) = args.get(index) {
        if !is_send_option(candidate) {
            request.notification.body = candidate.clone();
            index += 1;
        }
    }

    // --exec and --with take the rest of the line, and are recognized only here, after the
    // headline and description, so a headline that is literally "--exec" stays text.
    let mut exec = None;
    let mut buttons = Vec::new();
    let mut with = None;
    while index < args.len() {
        match args[index].as_str() {
            "--exec" => {
                exec = Some(args[index + 1..].to_vec());
                break;
            }
            "--with" => {
                with = Some(args[index + 1..].to_vec());
                break;
            }
            "--action" => {
                index += 1;
                let (Some(label), Some(program)) = (args.get(index), args.get(index + 1)) else {
                    return Err(ACTION_NEEDS_COMMAND.to_string());
                };
                if label.is_empty() || program.is_empty() {
                    return Err(ACTION_NEEDS_COMMAND.to_string());
                }
                index += 1;
                let mut words = Vec::new();
                while let Some(word) = args
                    .get(index)
                    .filter(|word| !matches!(word.as_str(), "--action" | "--with" | "--exec"))
                {
                    words.push(word.clone());
                    index += 1;
                }
                if words.is_empty() {
                    return Err(ACTION_NEEDS_COMMAND.to_string());
                }
                if let [word] = words.as_slice() {
                    if has_whitespace(word) {
                        return Err(format!(
                            "--action takes the command as separate words, not one quoted string.\nWrite:  --action \"{label}\" {word}"
                        ));
                    }
                }
                buttons.push(Button {
                    label: label.clone(),
                    argv: words,
                });
            }
            _ => {
                let consumed = parse_option(args, index, &mut request)?;
                if consumed == 0 {
                    return Err(format!("unknown option: {}\n{SEND_USAGE}", args[index]));
                }
                index += consumed;
            }
        }
    }

    if let Some(exec) = &exec {
        if exec.first().is_none_or(String::is_empty) {
            return Err("--exec needs a command: --exec <program> [args...]".to_string());
        }
        if let [word] = exec.as_slice() {
            if has_whitespace(word) {
                return Err(format!(
                    "--exec takes the command as separate words, not one quoted string.\nWrite:  --exec {word}"
                ));
            }
        }
    }
    if !buttons.is_empty() {
        if exec.is_some() {
            return Err(
                "--exec and --action cannot be combined: the card click runs the first action."
                    .to_string(),
            );
        }
        if buttons.len() > 3 {
            return Err("A notification takes at most 3 actions.".to_string());
        }
        let with = with.unwrap_or_default();
        for button in &mut buttons {
            button.argv.extend(with.iter().cloned());
        }
        request.click = Some(ClickRequest {
            click: buttons[0].argv.clone(),
            buttons,
        });
    } else if with.is_some() {
        return Err(
            "--with gives arguments to --action; it needs at least one --action before it."
                .to_string(),
        );
    } else if let Some(exec) = exec {
        request.click = Some(ClickRequest {
            click: exec,
            buttons: Vec::new(),
        });
    }

    Ok(request)
}

/// The script's `[[:space:]]`.
fn has_whitespace(word: &str) -> bool {
    word.contains([' ', '\t', '\n', '\x0b', '\x0c', '\r'])
}

fn parse_option(args: &[String], index: usize, request: &mut SendRequest) -> Result<usize, String> {
    let argument = &args[index];
    if argument == "-p" || argument == "--print-id" {
        request.print_id = true;
        return Ok(1);
    }
    // A flag takes no value, so only the bare word is one: --transient=false does not set it.
    if argument == "--transient" {
        request.notification.transient = true;
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
        "-g" | "--glyph" => request.glyph = Some(value.to_string()),
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
    argument == "--transient"
        || argument == "--action"
        || matches!(
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
    hooks: &mut impl Hooks,
    sender: &mut impl NotificationSender,
    waiter: &mut impl Waiter,
) -> Result<Option<u32>, String> {
    let mut notification = request.notification.clone();
    if let Some(glyph) = request.glyph.as_deref().filter(|glyph| !glyph.is_empty()) {
        match ask_glyph(hooks, glyph)? {
            GlyphAnswer::Key(key) => notification.hints.push((key, glyph.to_string())),
            GlyphAnswer::NotAvailable(answer) => {
                return Err(hooks::not_available("glyph is not available", &answer));
            }
            GlyphAnswer::Unsupported(answer) => {
                return Err(format!(
                    "glyph {glyph} is not supported by the notification receiver{}",
                    hooks::reason(&answer)
                ));
            }
        }
    }

    let Some(click) = &request.click else {
        let id = sender.notify(&notification)?;
        return Ok(request.print_id.then_some(id));
    };
    let answer = hooks.ask("action-route", &click.route_args())?;
    let id = match answer {
        HookAnswer::NotAvailable { .. } => deliver_on_waiter_route(notification, click, waiter)?,
        HookAnswer::Answered {
            status: Some(0),
            ref stdout,
            ..
        } => {
            let route = hooks::parse_string_hints(stdout)
                .ok_or_else(|| hooks::hook_failure("action-route", &answer))?;
            notification.hints.extend(route);
            sender.notify(&notification)?
        }
        HookAnswer::Answered {
            status: Some(UNSUPPORTED),
            ..
        } => {
            return Err(format!(
                "click command or buttons are not supported by the notification receiver{}",
                hooks::reason(&answer)
            ));
        }
        HookAnswer::Answered { .. } => return Err(hooks::hook_failure("action-route", &answer)),
    };
    Ok(request.print_id.then_some(id))
}

fn deliver_on_waiter_route(
    mut notification: Notification,
    click: &ClickRequest,
    waiter: &mut impl Waiter,
) -> Result<u32, String> {
    if notification.replaces_id != 0 {
        return Err(
            "replacing a notification with a click command or buttons is not available on this route"
                .to_string(),
        );
    }
    let mut commands = vec![("default".to_string(), click.click.clone())];
    notification
        .actions
        .push(("default".to_string(), String::new()));
    for (index, button) in click.buttons.iter().enumerate() {
        let key = (index + 1).to_string();
        notification
            .actions
            .push((key.clone(), button.label.clone()));
        commands.push((key, button.argv.clone()));
    }
    waiter.deliver(&notification, &commands)
}

enum GlyphAnswer {
    Key(String),
    NotAvailable(HookAnswer),
    Unsupported(HookAnswer),
}

/// Ask `glyph-hint G`. A hook failure, or an exit 0 that does not print one key, is an error.
fn ask_glyph(hooks: &mut impl Hooks, glyph: &str) -> Result<GlyphAnswer, String> {
    let answer = hooks.ask("glyph-hint", &[glyph.to_string()])?;
    match answer.status() {
        _ if matches!(answer, HookAnswer::NotAvailable { .. }) => {
            Ok(GlyphAnswer::NotAvailable(answer))
        }
        Some(0) => match hooks::stdout_line(&answer) {
            Some(key) if !key.is_empty() && !key.contains(['\n', '\0']) => {
                Ok(GlyphAnswer::Key(key.to_string()))
            }
            _ => Err(hooks::hook_failure("glyph-hint", &answer)),
        },
        Some(UNSUPPORTED) => Ok(GlyphAnswer::Unsupported(answer)),
        _ => Err(hooks::hook_failure("glyph-hint", &answer)),
    }
}

/// Send one of core's own notices (the time, reminders) with its glyph. Without glyph support, or
/// for a glyph the receiver would not draw, the notice goes out without one; a hook failure stays
/// a failure.
pub fn send_own_notice(
    mut notification: Notification,
    glyph: &str,
    hooks: &mut impl Hooks,
    sender: &mut impl NotificationSender,
) -> Result<u32, String> {
    if let GlyphAnswer::Key(key) = ask_glyph(hooks, glyph)? {
        notification.hints.push((key, glyph.to_string()));
    }
    sender.notify(&notification)
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

/// `notification-dismiss SUMMARY`: the receiver dismisses every shown notification whose summary
/// contains SUMMARY.
pub fn dismiss(args: &[String], hooks: &mut impl Hooks) -> Result<(), String> {
    let Some(summary) = args.first() else {
        return Err(DISMISS_USAGE.to_string());
    };
    let answer = hooks.ask("dismiss", std::slice::from_ref(summary))?;
    match answer {
        HookAnswer::NotAvailable { .. } => Err(hooks::not_available(
            "notification dismissal is not available",
            &answer,
        )),
        HookAnswer::Answered {
            status: Some(0), ..
        } => Ok(()),
        HookAnswer::Answered {
            status: Some(1), ..
        } => Err("no shown notification matched".to_string()),
        HookAnswer::Answered { .. } => Err(hooks::hook_failure("dismiss", &answer)),
    }
}

/// `toggle-notification-silencing`: flip the receiver's do-not-disturb, whoever set it.
pub fn toggle_silencing(hooks: &mut impl Hooks) -> Result<(), String> {
    let answer = hooks.ask("dnd-toggle", &[])?;
    match answer {
        HookAnswer::NotAvailable { .. } => Err(hooks::not_available(
            "notification silencing is not available",
            &answer,
        )),
        HookAnswer::Answered {
            status: Some(0), ..
        } if matches!(hooks::stdout_line(&answer), Some("on" | "off")) => Ok(()),
        HookAnswer::Answered { .. } => Err(hooks::hook_failure("dnd-toggle", &answer)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hooks::testing::FakeHooks;

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

    /// One delivery: the notification and its action keys with their argv.
    type Delivery = (Notification, Vec<(String, Vec<String>)>);

    #[derive(Default)]
    struct FakeWaiter {
        calls: Vec<Delivery>,
    }

    impl Waiter for FakeWaiter {
        fn deliver(
            &mut self,
            notification: &Notification,
            commands: &[(String, Vec<String>)],
        ) -> Result<u32, String> {
            self.calls.push((notification.clone(), commands.to_vec()));
            Ok(43)
        }
    }

    fn strings(words: &[&str]) -> Vec<String> {
        words.iter().map(|word| word.to_string()).collect()
    }

    fn parse(words: &[&str]) -> Result<SendRequest, String> {
        parse_send_args(&strings(words))
    }

    fn run_send(
        words: &[&str],
        hooks: &mut FakeHooks,
    ) -> (Result<Option<u32>, String>, FakeSender, FakeWaiter) {
        let mut sender = FakeSender::default();
        let mut waiter = FakeWaiter::default();
        let result =
            parse(words).and_then(|request| send(&request, hooks, &mut sender, &mut waiter));
        (result, sender, waiter)
    }

    #[test]
    fn parses_documented_send_options_before_and_after_the_headline() {
        let request = parse(&[
            "--app-name",
            "build-status",
            "-g",
            "✓",
            "-u",
            "critical",
            "Finished",
            "-50% remaining",
            "--image=/tmp/icon.png",
            "-i",
            "application-x-executable",
            "-t",
            "0",
            "-r",
            "7",
            "-p",
        ])
        .unwrap();

        assert_eq!(request.notification.app_name, "build-status");
        assert_eq!(request.notification.summary, "Finished");
        assert_eq!(request.notification.body, "-50% remaining");
        assert_eq!(request.notification.urgency, Urgency::Critical);
        assert_eq!(request.glyph.as_deref(), Some("✓"));
        assert_eq!(request.notification.image.as_deref(), Some("/tmp/icon.png"));
        assert_eq!(request.notification.app_icon, "application-x-executable");
        assert_eq!(request.notification.expire_timeout, 0);
        assert_eq!(request.notification.replaces_id, 7);
        assert!(request.print_id);
        assert!(request.click.is_none());
    }

    #[test]
    fn rejects_unknown_urgency_and_malformed_options() {
        assert!(parse(&["-u", "urgent", "Title"]).is_err());
        assert!(parse(&["-r", "not-an-id", "Title"]).is_err());
        assert!(parse(&["Title", "Body", "--unknown"]).is_err());
    }

    #[test]
    fn only_a_bare_transient_flag_sets_the_transient_hint() {
        assert!(parse(&["--transient", "A"]).unwrap().notification.transient);
        assert!(
            parse(&["A", "B", "--transient"])
                .unwrap()
                .notification
                .transient
        );
        let request = parse(&["--transient=false", "A"]).unwrap();
        assert!(!request.notification.transient);
        assert_eq!(request.notification.summary, "--transient=false");
        assert!(parse(&["A", "B", "--transient=false"]).is_err());
    }

    #[test]
    fn exec_takes_the_rest_of_the_line_after_the_headline_and_description() {
        let request = parse(&["A", "B", "--exec", "notify", "-u", "--exec"]).unwrap();
        assert_eq!(request.notification.body, "B");
        assert_eq!(
            request.click,
            Some(ClickRequest {
                click: strings(&["notify", "-u", "--exec"]),
                buttons: Vec::new(),
            })
        );
        let request = parse(&["A", "--exec", "true"]).unwrap();
        assert_eq!(request.notification.body, "");
        assert_eq!(request.click.unwrap().click, ["true"]);
        let request = parse(&["--exec", "true"]).unwrap();
        assert_eq!(request.notification.summary, "--exec");
        assert_eq!(request.notification.body, "true");
        assert!(request.click.is_none());
    }

    #[test]
    fn buttons_get_their_words_and_every_with_argument_and_the_first_is_the_click() {
        let request = parse(&[
            "A",
            "--action",
            "Open",
            "xdg-open",
            "--action",
            "Copy",
            "wl-copy",
            "-n",
            "--with",
            "/tmp/a b.txt",
            "--action",
        ])
        .unwrap();
        assert_eq!(
            request.click,
            Some(ClickRequest {
                click: strings(&["xdg-open", "/tmp/a b.txt", "--action"]),
                buttons: vec![
                    Button {
                        label: "Open".into(),
                        argv: strings(&["xdg-open", "/tmp/a b.txt", "--action"]),
                    },
                    Button {
                        label: "Copy".into(),
                        argv: strings(&["wl-copy", "-n", "/tmp/a b.txt", "--action"]),
                    },
                ],
            })
        );
    }

    #[test]
    fn rejects_click_requests_with_the_scripts_messages() {
        let error = |words: &[&str]| parse(words).unwrap_err();
        assert_eq!(
            error(&["A", "--exec"]),
            "--exec needs a command: --exec <program> [args...]"
        );
        assert_eq!(
            error(&["A", "--exec", ""]),
            "--exec needs a command: --exec <program> [args...]"
        );
        assert_eq!(
            error(&["A", "--exec", "ls -l"]),
            "--exec takes the command as separate words, not one quoted string.\nWrite:  --exec ls -l"
        );
        assert!(parse(&["A", "--exec", "ls -l", "x"]).is_ok());
        assert_eq!(error(&["A", "--action", "Open"]), ACTION_NEEDS_COMMAND);
        assert_eq!(error(&["A", "--action", "", "true"]), ACTION_NEEDS_COMMAND);
        assert_eq!(
            error(&["A", "--action", "Open", "--with", "x"]),
            ACTION_NEEDS_COMMAND
        );
        assert_eq!(
            error(&["A", "--action", "Open", "xdg-open\tx"]),
            "--action takes the command as separate words, not one quoted string.\nWrite:  --action \"Open\" xdg-open\tx"
        );
        assert_eq!(
            error(&["A", "--action", "x", "true", "--exec", "true"]),
            "--exec and --action cannot be combined: the card click runs the first action."
        );
        assert_eq!(
            error(&[
                "A", "--action", "1", "true", "--action", "2", "true", "--action", "3", "true",
                "--action", "4", "true",
            ]),
            "A notification takes at most 3 actions."
        );
        let with = "--with gives arguments to --action; it needs at least one --action before it.";
        assert_eq!(error(&["A", "B", "--with", "x"]), with);
        assert_eq!(error(&["A", "B", "--with"]), with);
    }

    #[test]
    fn sends_the_parsed_notification_and_prints_only_a_requested_id() {
        let mut hooks = FakeHooks::default();
        let (result, sender, _) = run_send(&["-p", "Done"], &mut hooks);
        assert_eq!(result.unwrap(), Some(42));
        assert_eq!(sender.calls, [parse(&["Done"]).unwrap().notification]);

        let (result, sender, _) = run_send(&["Done"], &mut hooks);
        assert_eq!(result.unwrap(), None);
        assert_eq!(sender.calls.len(), 1);
        assert!(hooks.calls.is_empty());
    }

    #[test]
    fn default_identity_is_core_owned_and_callers_can_override_it() {
        assert_eq!(Notification::action("Done", "").app_name, CORE_APP_NAME);
        assert_eq!(
            parse(&["Done"]).unwrap().notification.app_name,
            CORE_APP_NAME
        );
        assert_eq!(
            parse(&["--app-name", "caller-app", "Done"])
                .unwrap()
                .notification
                .app_name,
            "caller-app"
        );
    }

    #[test]
    fn a_glyph_rides_the_key_the_hook_names() {
        let mut hooks = FakeHooks::default().answer("glyph-hint", 0, b"omarchy-glyph\n", "");
        let (result, sender, _) = run_send(&["-g", "X", "Hi"], &mut hooks);
        result.unwrap();
        assert_eq!(hooks.calls, [strings(&["glyph-hint", "X"])]);
        assert_eq!(
            sender.calls[0].hints,
            [("omarchy-glyph".into(), "X".into())]
        );
    }

    #[test]
    fn an_empty_glyph_asks_no_hook_and_adds_no_hint() {
        let mut hooks = FakeHooks::default().answer("glyph-hint", 0, b"omarchy-glyph\n", "");
        let (result, sender, _) = run_send(&["-g", "", "Hi"], &mut hooks);
        result.unwrap();
        assert!(hooks.calls.is_empty());
        assert!(sender.calls[0].hints.is_empty());
    }

    #[test]
    fn an_explicit_glyph_fails_before_notify_when_the_receiver_cannot_draw_it() {
        let (result, sender, _) = run_send(&["-g", "X", "Hi"], &mut FakeHooks::default());
        assert!(result.unwrap_err().contains("glyph is not available"));
        assert!(sender.calls.is_empty());

        let mut hooks = FakeHooks::default().answer("glyph-hint", 65, b"", "longer than 16\n");
        let (result, sender, _) = run_send(&["-g", "X", "Hi"], &mut hooks);
        assert_eq!(
            result.unwrap_err(),
            "glyph X is not supported by the notification receiver: longer than 16"
        );
        assert!(sender.calls.is_empty());

        let mut hooks =
            FakeHooks::default().answer("glyph-hint", 70, b"", "shell is not responding");
        let (result, sender, _) = run_send(&["-g", "X", "Hi"], &mut hooks);
        assert_eq!(result.unwrap_err(), "shell is not responding");
        assert!(sender.calls.is_empty());

        let mut hooks = FakeHooks::default().answer("glyph-hint", 0, b"", "");
        let (result, sender, _) = run_send(&["-g", "X", "Hi"], &mut hooks);
        assert!(result.is_err());
        assert!(sender.calls.is_empty());
    }

    #[test]
    fn transient_and_hook_hints_reach_notify_as_given() {
        let mut hooks =
            FakeHooks::default().answer("action-route", 0, b"omarchy-exec-argv\0[\"true\"]\0", "");
        let (result, sender, waiter) = run_send(
            &["--transient", "-r", "9", "A", "--exec", "true"],
            &mut hooks,
        );
        result.unwrap();
        assert_eq!(
            hooks.calls,
            [strings(&["action-route", "click", "1", "true"])]
        );
        assert!(waiter.calls.is_empty());
        let sent = &sender.calls[0];
        assert!(sent.transient);
        assert_eq!(sent.replaces_id, 9);
        assert!(sent.actions.is_empty());
        assert_eq!(
            sent.hints,
            [("omarchy-exec-argv".into(), "[\"true\"]".into())]
        );
    }

    #[test]
    fn route_arguments_carry_each_argv_with_its_length() {
        let mut hooks = FakeHooks::default();
        let _ = run_send(
            &[
                "A",
                "--action",
                "Open",
                "xdg-open",
                "--with",
                "/tmp/a b.txt",
            ],
            &mut hooks,
        );
        assert_eq!(
            hooks.calls,
            [strings(&[
                "action-route",
                "click",
                "2",
                "xdg-open",
                "/tmp/a b.txt",
                "button",
                "Open",
                "2",
                "xdg-open",
                "/tmp/a b.txt",
            ])]
        );
    }

    #[test]
    fn without_a_receiver_route_the_waiter_sends_standard_actions() {
        let mut hooks = FakeHooks::default();
        let (result, sender, waiter) = run_send(
            &[
                "-p", "A", "--action", "Open", "xdg-open", "--action", "Copy", "wl-copy", "--with",
                "x",
            ],
            &mut hooks,
        );
        assert_eq!(result.unwrap(), Some(43));
        assert!(sender.calls.is_empty());
        let (notification, commands) = &waiter.calls[0];
        assert_eq!(
            notification.actions,
            [
                ("default".into(), String::new()),
                ("1".into(), "Open".into()),
                ("2".into(), "Copy".into()),
            ]
        );
        assert_eq!(
            commands,
            &[
                ("default".to_string(), strings(&["xdg-open", "x"])),
                ("1".to_string(), strings(&["xdg-open", "x"])),
                ("2".to_string(), strings(&["wl-copy", "x"])),
            ]
        );

        let (result, _, waiter) = run_send(&["A", "--exec", "true"], &mut hooks);
        assert_eq!(result.unwrap(), None);
        assert_eq!(
            waiter.calls[0].0.actions,
            [("default".into(), String::new())]
        );
    }

    #[test]
    fn the_waiter_route_refuses_replacement_before_reaching_the_bus() {
        let (result, sender, waiter) = run_send(
            &["-r", "7", "B", "--exec", "true"],
            &mut FakeHooks::default(),
        );
        assert_eq!(
            result.unwrap_err(),
            "replacing a notification with a click command or buttons is not available on this route"
        );
        assert!(sender.calls.is_empty());
        assert!(waiter.calls.is_empty());
    }

    #[test]
    fn a_receiver_that_refuses_the_route_or_answers_badly_gets_no_notify_and_no_waiter() {
        for (status, stdout, stderr, expected) in [
            (
                65,
                &b""[..],
                "argv element starts with -",
                "click command or buttons are not supported by the notification receiver: argv element starts with -",
            ),
            (
                70,
                b"",
                "shell is not responding",
                "shell is not responding",
            ),
            (0, b"k", "", "output core cannot read"),
            (0, b"", "", "output core cannot read"),
            (0, b"k\0", "", "output core cannot read"),
        ] {
            let mut hooks = FakeHooks::default().answer("action-route", status, stdout, stderr);
            let (result, sender, waiter) = run_send(&["A", "--exec", "true"], &mut hooks);
            assert!(
                result.unwrap_err().contains(expected),
                "{status} {stdout:?}"
            );
            assert!(sender.calls.is_empty());
            assert!(waiter.calls.is_empty());
        }
    }

    #[test]
    fn the_glyph_is_settled_before_the_route_and_both_hints_are_sent() {
        let mut hooks = FakeHooks::default()
            .answer("glyph-hint", 0, b"omarchy-glyph\n", "")
            .answer("action-route", 0, b"omarchy-exec-argv\0[\"true\"]\0", "");
        let (result, sender, _) = run_send(&["-g", "X", "A", "--exec", "true"], &mut hooks);
        result.unwrap();
        assert_eq!(hooks.calls[0][0], "glyph-hint");
        assert_eq!(hooks.calls[1][0], "action-route");
        assert_eq!(sender.calls[0].hints.len(), 2);
    }

    #[test]
    fn own_notices_go_out_without_a_glyph_only_when_glyphs_are_unavailable_or_refused() {
        let notice = || time_notification("Monday 09:30");

        let mut hooks = FakeHooks::default().answer("glyph-hint", 0, b"omarchy-glyph\n", "");
        let mut sender = FakeSender::default();
        send_own_notice(notice(), TIME_GLYPH, &mut hooks, &mut sender).unwrap();
        assert_eq!(
            sender.calls[0].hints,
            [("omarchy-glyph".into(), "\u{f43a}".into())]
        );
        assert_eq!(sender.calls[0].app_name, CORE_APP_NAME);
        assert_eq!(sender.calls[0].urgency, Urgency::Low);

        for mut hooks in [
            FakeHooks::default(),
            FakeHooks::default().answer("glyph-hint", 65, b"", "no"),
        ] {
            let mut sender = FakeSender::default();
            send_own_notice(notice(), TIME_GLYPH, &mut hooks, &mut sender).unwrap();
            assert_eq!(sender.calls.len(), 1);
            assert!(sender.calls[0].hints.is_empty());
        }

        let mut hooks = FakeHooks::default().answer("glyph-hint", 70, b"", "broken");
        let mut sender = FakeSender::default();
        assert_eq!(
            send_own_notice(notice(), TIME_GLYPH, &mut hooks, &mut sender).unwrap_err(),
            "broken"
        );
        assert!(sender.calls.is_empty());
    }

    #[test]
    fn dismiss_passes_the_summary_as_one_argument_and_reports_the_receivers_answer() {
        let mut hooks = FakeHooks::default();
        assert_eq!(dismiss(&[], &mut hooks).unwrap_err(), DISMISS_USAGE);
        assert!(hooks.calls.is_empty());

        assert!(
            dismiss(&strings(&["X"]), &mut hooks)
                .unwrap_err()
                .contains("notification dismissal is not available")
        );

        let mut hooks = FakeHooks::default().answer("dismiss", 0, b"", "");
        dismiss(&strings(&["a; rm -rf ~"]), &mut hooks).unwrap();
        assert_eq!(hooks.calls, [strings(&["dismiss", "a; rm -rf ~"])]);

        let mut hooks = FakeHooks::default().answer("dismiss", 1, b"", "");
        assert_eq!(
            dismiss(&strings(&["X"]), &mut hooks).unwrap_err(),
            "no shown notification matched"
        );

        let mut hooks = FakeHooks::default().answer("dismiss", 70, b"", "is not responding");
        assert_eq!(
            dismiss(&strings(&["X"]), &mut hooks).unwrap_err(),
            "is not responding"
        );
    }

    #[test]
    fn silencing_needs_the_new_state_from_the_receiver() {
        for stdout in [&b"on"[..], b"off\n"] {
            let mut hooks = FakeHooks::default().answer("dnd-toggle", 0, stdout, "");
            toggle_silencing(&mut hooks).unwrap();
            assert_eq!(hooks.calls, [strings(&["dnd-toggle"])]);
        }
        for stdout in [&b""[..], b"maybe", b"on\n\n"] {
            let mut hooks = FakeHooks::default().answer("dnd-toggle", 0, stdout, "");
            assert!(toggle_silencing(&mut hooks).is_err(), "{stdout:?}");
        }
        assert!(
            toggle_silencing(&mut FakeHooks::default())
                .unwrap_err()
                .contains("notification silencing is not available")
        );
        let mut hooks =
            FakeHooks::default().answer("dnd-toggle", 70, b"", "omarchy-shell is not responding");
        assert_eq!(
            toggle_silencing(&mut hooks).unwrap_err(),
            "omarchy-shell is not responding"
        );
    }
}
