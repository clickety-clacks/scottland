use super::{Ctl, Result, print_json};
use serde_json::{Value, json};

const USAGE: &str = "usage: scottland-ctl transparency toggle | status [window-id]";
const METHOD: &str = "scottland/transparency";

pub(super) fn run(args: &[String]) -> Result<u8> {
    let reply =
        dispatch(args, |request| Ctl::connect()?.call(METHOD, request)).map_err(one_line_error)?;
    print_json(reply)
}

fn one_line_error(error: String) -> String {
    let message = error
        .lines()
        .next()
        .map(str::trim)
        .filter(|message| !message.is_empty())
        .unwrap_or("transparency request failed");
    message.chars().take(160).collect()
}

fn dispatch(args: &[String], call: impl FnOnce(Value) -> Result<Value>) -> Result<Value> {
    let (request, requested_window) = request(args)?;
    let reply = call(request)?;
    checked_reply(reply, requested_window)
}

fn request(args: &[String]) -> Result<(Value, Option<i64>)> {
    match args {
        [action] if action == "toggle" => Ok((json!({"action": "toggle"}), None)),
        [action] if action == "status" => Ok((json!({"action": "status"}), None)),
        [action, window] if action == "status" => {
            let id = parse_window_id(window)?;
            Ok((json!({"action": "status", "window": id}), Some(id)))
        }
        _ => Err(USAGE.to_string()),
    }
}

fn parse_window_id(value: &str) -> Result<i64> {
    if value.is_empty() || !value.bytes().all(|byte| byte.is_ascii_digit()) {
        return Err("window id must be a positive integer".to_string());
    }
    let id = value
        .parse::<i64>()
        .map_err(|_| "window id is too large".to_string())?;
    if id <= 0 {
        return Err("window id must be a positive integer".to_string());
    }
    Ok(id)
}

fn checked_reply(reply: Value, requested_window: Option<i64>) -> Result<Value> {
    if let Some(error) = reply.get("error") {
        let reason = error
            .as_str()
            .and_then(|message| message.lines().next())
            .map(str::trim)
            .filter(|message| !message.is_empty())
            .unwrap_or("the compositor rejected the request");
        return Err(format!("transparency request failed: {reason}"));
    }
    if reply.get("ok").and_then(Value::as_bool) != Some(true) {
        return Err("Wayfire returned an invalid transparency response".to_string());
    }
    let window = reply
        .get("window")
        .and_then(Value::as_i64)
        .filter(|window| *window > 0)
        .ok_or_else(|| "Wayfire returned an invalid transparency response".to_string())?;
    if requested_window.is_some_and(|requested| requested != window) {
        return Err("Wayfire returned a different transparency window".to_string());
    }
    match reply.get("override").and_then(Value::as_str) {
        Some("none" | "opaque" | "translucent") => Ok(reply),
        _ => Err("Wayfire returned an invalid transparency response".to_string()),
    }
}

#[cfg(test)]
mod tests {
    use super::{dispatch, one_line_error, request};
    use serde_json::{Value, json};
    use std::cell::Cell;

    fn args(values: &[&str]) -> Vec<String> {
        values.iter().map(|value| (*value).to_string()).collect()
    }

    fn success(window: i64, override_state: &str) -> Value {
        json!({
            "ok": true,
            "window": window,
            "override": override_state,
        })
    }

    #[test]
    fn toggle_sends_one_explicit_action_without_a_window() {
        let mut sent = None;
        let reply = dispatch(&args(&["toggle"]), |request| {
            sent = Some(request);
            Ok(success(9, "translucent"))
        })
        .expect("successful toggle response");

        assert_eq!(sent, Some(json!({"action": "toggle"})));
        assert_eq!(reply, success(9, "translucent"));
    }

    #[test]
    fn status_without_window_is_read_only_request() {
        let mut sent = None;
        let reply = dispatch(&args(&["status"]), |request| {
            sent = Some(request);
            Ok(success(9, "opaque"))
        })
        .expect("successful status response");

        assert_eq!(sent, Some(json!({"action": "status"})));
        assert_eq!(reply, success(9, "opaque"));
    }

    #[test]
    fn status_reports_the_requested_window_and_its_override() {
        let mut sent = None;
        let reply = dispatch(&args(&["status", "42"]), |request| {
            sent = Some(request);
            Ok(success(42, "translucent"))
        })
        .expect("successful status response");

        assert_eq!(sent, Some(json!({"action": "status", "window": 42})));
        assert_eq!(reply["window"], 42);
        assert_eq!(reply["override"], "translucent");
    }

    #[test]
    fn invalid_arguments_do_not_connect_or_send_a_request() {
        let called = Cell::new(false);
        for invalid in [
            args(&[]),
            args(&["toggle", "42"]),
            args(&["status", "0"]),
            args(&["status", "-1"]),
            args(&["status", "nope"]),
            args(&["status", "9223372036854775808"]),
            args(&["status", "42", "extra"]),
        ] {
            let error = dispatch(&invalid, |_| {
                called.set(true);
                Ok(success(42, "none"))
            })
            .expect_err("invalid command must fail");
            assert!(!error.is_empty());
        }
        assert!(!called.get());
    }

    #[test]
    fn compositor_errors_are_non_success_results() {
        let error = dispatch(&args(&["toggle"]), |_| {
            Ok(json!({"error": "no focused window"}))
        })
        .expect_err("rejected toggle must fail");
        assert_eq!(error, "transparency request failed: no focused window");
    }

    #[test]
    fn transport_failures_remain_non_success_results() {
        let error = dispatch(&args(&["toggle"]), |_| {
            Err("the selected session has no Wayfire socket".to_string())
        })
        .expect_err("missing session must fail");
        assert_eq!(error, "the selected session has no Wayfire socket");
    }

    #[test]
    fn reported_error_is_one_line_and_bounded() {
        assert_eq!(
            one_line_error("first reason\nsecond detail".to_string()),
            "first reason"
        );
        assert_eq!(
            one_line_error("\n".to_string()),
            "transparency request failed"
        );
        assert_eq!(one_line_error("x".repeat(200)).len(), 160);
    }

    #[test]
    fn malformed_or_misdirected_success_replies_fail_closed() {
        let wrong_window = dispatch(&args(&["status", "42"]), |_| Ok(success(9, "opaque")))
            .expect_err("status must report the requested window");
        assert_eq!(
            wrong_window,
            "Wayfire returned a different transparency window"
        );

        let unknown_state = dispatch(&args(&["status"]), |_| Ok(success(9, "dim")))
            .expect_err("unknown override state must fail");
        assert_eq!(
            unknown_state,
            "Wayfire returned an invalid transparency response"
        );
    }

    #[test]
    fn status_window_uses_the_positive_server_integer_domain() {
        assert_eq!(
            request(&args(&["status", "1"])).unwrap(),
            (json!({"action": "status", "window": 1}), Some(1))
        );
    }
}
