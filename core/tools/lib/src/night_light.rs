//! Pure command and state policy for Scottland's Night Light feature.
//!
//! The future `scottland-display` dispatcher passes the tokens after
//! `night-light` to [`parse_command`], obtains an action plan from [`plan`],
//! performs those actions through the existing display watcher, then supplies
//! the watcher's outcomes to [`finish`]. This module performs no filesystem,
//! process, D-Bus, Wayland, or gamma I/O.

use std::fmt::Write as _;

pub const WARM_KELVIN: u16 = 4000;
pub const USAGE: &str = "usage: scottland-display night-light {on|off|toggle|status}";

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Command {
    On,
    Off,
    Toggle,
    Status,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ParseError;

impl ParseError {
    pub fn stderr_line(self) -> String {
        format!("scottland-display: night-light: invalid command; {USAGE}")
    }

    pub const fn exit_status(self) -> u8 {
        2
    }
}

/// Parse the arguments following the `night-light` subcommand.
pub fn parse_command(args: &[&str]) -> Result<Command, ParseError> {
    match args {
        ["on"] => Ok(Command::On),
        ["off"] => Ok(Command::Off),
        ["toggle"] => Ok(Command::Toggle),
        ["status"] => Ok(Command::Status),
        _ => Err(ParseError),
    }
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub enum RequestState {
    #[default]
    Off,
    On,
}

impl RequestState {
    pub const fn at_session_start() -> Self {
        Self::Off
    }

    const fn as_str(self) -> &'static str {
        match self {
            Self::Off => "off",
            Self::On => "on",
        }
    }
}

/// The watcher-owned state known for one connected output before this command.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum OutputOwnership {
    /// Night Light does not own a gamma handle for this output.
    Unowned,
    /// Night Light owns the output but has not confirmed its 4000K application.
    OwnedUnwarmed,
    /// Night Light owns the output and confirmed its 4000K application.
    WarmOwned,
    /// Available protocol facts do not establish ownership or tint state.
    Unknown,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OutputInput {
    pub identity: String,
    pub ownership: OutputOwnership,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Input {
    pub command: Command,
    pub request: RequestState,
    pub watcher_available: bool,
    pub outputs: Vec<OutputInput>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum Action {
    AcquireAndApply4000K { output: String },
    Apply4000K { output: String },
    RestoreOwned { output: String },
    ReleaseUnwarmed { output: String },
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Outcome {
    Succeeded,
    Failed,
    Unknown,
    NotAttempted,
}

/// Injected per-output results from watcher actions. Unused outcomes are ignored.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OutputOutcome {
    pub identity: String,
    pub acquisition: Outcome,
    pub application: Outcome,
    pub restoration: Outcome,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum OutputStatus {
    Warm,
    NotWarmed,
    Failed,
    Unknown,
}

impl OutputStatus {
    const fn as_str(self) -> &'static str {
        match self {
            Self::Warm => "warm",
            Self::NotWarmed => "not-warmed",
            Self::Failed => "failed",
            Self::Unknown => "unknown",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OutputReport {
    pub identity: String,
    pub status: OutputStatus,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Plan {
    pub request: RequestState,
    pub actions: Vec<Action>,
    command: Command,
    watcher_available: bool,
    outputs: Vec<OutputReport>,
    initial_error: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Decision {
    pub request: RequestState,
    pub actions: Vec<Action>,
    pub outputs: Vec<OutputReport>,
    pub stdout: String,
    pub stderr: Option<String>,
    pub exit_status: u8,
}

/// Plan session state and watcher actions without performing I/O.
pub fn plan(input: Input) -> Plan {
    let mut request = input.request;
    let mut actions = Vec::new();
    let mut outputs = Vec::with_capacity(input.outputs.len());
    let mut initial_error = None;

    if !input.watcher_available {
        match input.command {
            Command::On | Command::Toggle => {
                initial_error = Some(
                    "scottland-display: night-light: display watcher unavailable; request unchanged"
                        .to_owned(),
                );
            }
            Command::Off => {
                // Clearing a request is fail-safe: a later watcher start must not
                // reapply a tint the user has explicitly turned off.
                request = RequestState::Off;
                initial_error = Some(
                    "scottland-display: night-light: display watcher unavailable; request cleared, output state unknown"
                        .to_owned(),
                );
            }
            Command::Status => {}
        }
        outputs.extend(input.outputs.into_iter().map(|output| OutputReport {
            identity: output.identity,
            status: OutputStatus::Unknown,
        }));
    } else {
        let target = match input.command {
            Command::On => Some(RequestState::On),
            Command::Off => Some(RequestState::Off),
            Command::Toggle => Some(match input.request {
                RequestState::On => RequestState::Off,
                RequestState::Off => RequestState::On,
            }),
            Command::Status => None,
        };
        if let Some(target) = target {
            request = target;
        }

        for output in input.outputs {
            let status = match input.command {
                Command::Status => status_from_ownership(output.ownership),
                Command::On => plan_on(&output, &mut actions),
                Command::Off => plan_off(&output, &mut actions),
                Command::Toggle if request == RequestState::On => plan_on(&output, &mut actions),
                Command::Toggle => plan_off(&output, &mut actions),
            };
            outputs.push(OutputReport {
                identity: output.identity,
                status,
            });
        }
    }

    Plan {
        request,
        actions,
        command: input.command,
        watcher_available: input.watcher_available,
        outputs,
        initial_error,
    }
}

/// Apply injected watcher results to a plan and produce truthful CLI output.
pub fn finish(plan: Plan, outcomes: &[OutputOutcome]) -> Decision {
    let mut outputs = plan.outputs;

    for action in &plan.actions {
        let identity = action_output(action);
        let result = outcomes.iter().find(|item| item.identity == identity);
        let report = outputs.iter_mut().find(|item| item.identity == identity);
        let (Some(result), Some(report)) = (result, report) else {
            continue;
        };

        report.status = match action {
            Action::AcquireAndApply4000K { .. } => match result.acquisition {
                Outcome::Succeeded => application_status(result.application),
                Outcome::Failed => OutputStatus::Failed,
                Outcome::Unknown | Outcome::NotAttempted => OutputStatus::Unknown,
            },
            Action::Apply4000K { .. } => application_status(result.application),
            Action::RestoreOwned { .. } => match result.restoration {
                Outcome::Succeeded => OutputStatus::NotWarmed,
                Outcome::Failed => OutputStatus::Failed,
                Outcome::Unknown | Outcome::NotAttempted => OutputStatus::Unknown,
            },
            Action::ReleaseUnwarmed { .. } => OutputStatus::NotWarmed,
        };
    }

    let mut stderr = plan.initial_error;
    if stderr.is_none()
        && plan.command != Command::Status
        && outputs
            .iter()
            .any(|output| matches!(output.status, OutputStatus::Failed | OutputStatus::Unknown))
    {
        stderr = Some(
            "scottland-display: night-light: one or more output results failed or are unknown"
                .to_owned(),
        );
    }

    let stdout = format_status(plan.request, plan.watcher_available, &outputs);
    let exit_status = if stderr.is_some() { 1 } else { 0 };

    Decision {
        request: plan.request,
        actions: plan.actions,
        outputs,
        stdout,
        stderr,
        exit_status,
    }
}

fn plan_on(output: &OutputInput, actions: &mut Vec<Action>) -> OutputStatus {
    match output.ownership {
        OutputOwnership::WarmOwned => OutputStatus::Warm,
        OutputOwnership::Unowned => {
            actions.push(Action::AcquireAndApply4000K {
                output: output.identity.clone(),
            });
            OutputStatus::Unknown
        }
        OutputOwnership::OwnedUnwarmed => {
            actions.push(Action::Apply4000K {
                output: output.identity.clone(),
            });
            OutputStatus::Unknown
        }
        OutputOwnership::Unknown => OutputStatus::Unknown,
    }
}

fn plan_off(output: &OutputInput, actions: &mut Vec<Action>) -> OutputStatus {
    match output.ownership {
        OutputOwnership::WarmOwned => {
            actions.push(Action::RestoreOwned {
                output: output.identity.clone(),
            });
            OutputStatus::Unknown
        }
        OutputOwnership::OwnedUnwarmed => {
            actions.push(Action::ReleaseUnwarmed {
                output: output.identity.clone(),
            });
            OutputStatus::NotWarmed
        }
        OutputOwnership::Unowned => OutputStatus::NotWarmed,
        OutputOwnership::Unknown => OutputStatus::Unknown,
    }
}

fn application_status(outcome: Outcome) -> OutputStatus {
    match outcome {
        Outcome::Succeeded => OutputStatus::Warm,
        Outcome::Failed => OutputStatus::Failed,
        Outcome::Unknown | Outcome::NotAttempted => OutputStatus::Unknown,
    }
}

fn status_from_ownership(ownership: OutputOwnership) -> OutputStatus {
    match ownership {
        OutputOwnership::Unowned | OutputOwnership::OwnedUnwarmed => OutputStatus::NotWarmed,
        OutputOwnership::WarmOwned => OutputStatus::Warm,
        OutputOwnership::Unknown => OutputStatus::Unknown,
    }
}

fn action_output(action: &Action) -> &str {
    match action {
        Action::AcquireAndApply4000K { output }
        | Action::Apply4000K { output }
        | Action::RestoreOwned { output }
        | Action::ReleaseUnwarmed { output } => output,
    }
}

fn format_status(request: RequestState, watcher_available: bool, outputs: &[OutputReport]) -> String {
    let mut rendered = format!(
        "night-light request={} watcher={}\n",
        request.as_str(),
        if watcher_available { "available" } else { "unavailable" }
    );
    for output in outputs {
        let safe_identity = output.identity.replace('\n', "\\n").replace('\r', "\\r");
        let _ = writeln!(
            rendered,
            "night-light output={safe_identity} status={}",
            output.status.as_str()
        );
    }
    rendered
}

#[cfg(test)]
mod tests {
    use super::*;

    fn output(identity: &str, ownership: OutputOwnership) -> OutputInput {
        OutputInput {
            identity: identity.to_owned(),
            ownership,
        }
    }

    fn outcome(
        identity: &str,
        acquisition: Outcome,
        application: Outcome,
        restoration: Outcome,
    ) -> OutputOutcome {
        OutputOutcome {
            identity: identity.to_owned(),
            acquisition,
            application,
            restoration,
        }
    }

    fn input(command: Command, request: RequestState, outputs: Vec<OutputInput>) -> Input {
        Input {
            command,
            request,
            watcher_available: true,
            outputs,
        }
    }

    fn run(input: Input, outcomes: &[OutputOutcome]) -> Decision {
        finish(plan(input), outcomes)
    }

    #[test]
    fn parses_only_the_four_contract_commands() {
        assert_eq!(parse_command(&["on"]), Ok(Command::On));
        assert_eq!(parse_command(&["off"]), Ok(Command::Off));
        assert_eq!(parse_command(&["toggle"]), Ok(Command::Toggle));
        assert_eq!(parse_command(&["status"]), Ok(Command::Status));

        let error = parse_command(&["schedule"]).unwrap_err();
        assert_eq!(error.exit_status(), 2);
        assert!(error.stderr_line().contains(USAGE));
        assert!(!error.stderr_line().contains('\n'));
        assert!(parse_command(&["on", "extra"]).is_err());
    }

    #[test]
    fn a_new_session_starts_with_request_off() {
        assert_eq!(RequestState::at_session_start(), RequestState::Off);
        assert_eq!(RequestState::default(), RequestState::Off);
    }

    #[test]
    fn on_and_toggle_do_not_mutate_request_without_the_watcher() {
        for (command, request) in [
            (Command::On, RequestState::Off),
            (Command::Toggle, RequestState::Off),
            (Command::Toggle, RequestState::On),
        ] {
            let mut test_input = input(
                command,
                request,
                vec![output("DP-1", OutputOwnership::Unowned)],
            );
            test_input.watcher_available = false;
            let result = run(test_input, &[]);

            assert_eq!(result.request, request);
            assert!(result.actions.is_empty());
            assert_eq!(result.outputs[0].status, OutputStatus::Unknown);
            assert_eq!(result.exit_status, 1);
            assert!(result
                .stderr
                .as_deref()
                .unwrap()
                .contains("request unchanged"));
        }
    }

    #[test]
    fn off_clears_the_request_without_claiming_watcher_restoration() {
        let mut test_input = input(
            Command::Off,
            RequestState::On,
            vec![output("DP-1", OutputOwnership::WarmOwned)],
        );
        test_input.watcher_available = false;
        let result = run(test_input, &[]);

        assert_eq!(result.request, RequestState::Off);
        assert!(result.actions.is_empty());
        assert_eq!(result.outputs[0].status, OutputStatus::Unknown);
        assert_eq!(result.exit_status, 1);
        assert!(result.stderr.as_deref().unwrap().contains("request cleared"));
        assert!(result.stdout.contains("request=off watcher=unavailable"));
    }

    #[test]
    fn on_plans_fixed_temperature_and_reports_only_confirmed_warmth() {
        assert_eq!(WARM_KELVIN, 4000);
        let result = run(
            input(
                Command::On,
                RequestState::Off,
                vec![output("DP-1", OutputOwnership::Unowned)],
            ),
            &[outcome(
                "DP-1",
                Outcome::Succeeded,
                Outcome::Succeeded,
                Outcome::NotAttempted,
            )],
        );

        assert_eq!(result.request, RequestState::On);
        assert_eq!(
            result.actions,
            vec![Action::AcquireAndApply4000K {
                output: "DP-1".to_owned()
            }]
        );
        assert_eq!(result.outputs[0].status, OutputStatus::Warm);
        assert_eq!(result.exit_status, 0);
    }

    #[test]
    fn partial_application_failure_keeps_per_output_truthful_status() {
        let result = run(
            input(
                Command::On,
                RequestState::Off,
                vec![
                    output("DP-1", OutputOwnership::Unowned),
                    output("DP-2", OutputOwnership::Unowned),
                ],
            ),
            &[
                outcome(
                    "DP-1",
                    Outcome::Succeeded,
                    Outcome::Succeeded,
                    Outcome::NotAttempted,
                ),
                outcome(
                    "DP-2",
                    Outcome::Succeeded,
                    Outcome::Failed,
                    Outcome::NotAttempted,
                ),
            ],
        );

        assert_eq!(result.request, RequestState::On);
        assert_eq!(result.outputs[0].status, OutputStatus::Warm);
        assert_eq!(result.outputs[1].status, OutputStatus::Failed);
        assert_eq!(result.exit_status, 1);
        assert!(result.stderr.is_some());
    }

    #[test]
    fn all_warming_failures_keep_the_request_and_fail_the_command() {
        let result = run(
            input(
                Command::On,
                RequestState::Off,
                vec![
                    output("DP-1", OutputOwnership::Unowned),
                    output("DP-2", OutputOwnership::Unowned),
                ],
            ),
            &[
                outcome(
                    "DP-1",
                    Outcome::Failed,
                    Outcome::NotAttempted,
                    Outcome::NotAttempted,
                ),
                outcome(
                    "DP-2",
                    Outcome::Succeeded,
                    Outcome::Failed,
                    Outcome::NotAttempted,
                ),
            ],
        );

        assert_eq!(result.request, RequestState::On);
        assert_eq!(result.outputs[0].status, OutputStatus::Failed);
        assert_eq!(result.outputs[1].status, OutputStatus::Failed);
        assert_eq!(result.exit_status, 1);
        assert!(result.stderr.is_some());
    }

    #[test]
    fn off_restores_only_confirmed_warm_outputs() {
        let result = run(
            input(
                Command::Off,
                RequestState::On,
                vec![
                    output("DP-1", OutputOwnership::WarmOwned),
                    output("DP-2", OutputOwnership::Unowned),
                    output("DP-3", OutputOwnership::OwnedUnwarmed),
                ],
            ),
            &[outcome(
                "DP-1",
                Outcome::Succeeded,
                Outcome::Succeeded,
                Outcome::Succeeded,
            )],
        );

        assert_eq!(result.request, RequestState::Off);
        assert_eq!(
            result.actions,
            vec![
                Action::RestoreOwned {
                    output: "DP-1".to_owned()
                },
                Action::ReleaseUnwarmed {
                    output: "DP-3".to_owned()
                }
            ]
        );
        assert_eq!(result.outputs[0].status, OutputStatus::NotWarmed);
        assert_eq!(result.outputs[1].status, OutputStatus::NotWarmed);
        assert_eq!(result.outputs[2].status, OutputStatus::NotWarmed);
        assert_eq!(result.exit_status, 0);
    }

    #[test]
    fn failed_restoration_is_not_reported_as_normal() {
        let result = run(
            input(
                Command::Off,
                RequestState::On,
                vec![output("DP-1", OutputOwnership::WarmOwned)],
            ),
            &[outcome(
                "DP-1",
                Outcome::Succeeded,
                Outcome::Succeeded,
                Outcome::Failed,
            )],
        );

        assert_eq!(result.request, RequestState::Off);
        assert_eq!(result.outputs[0].status, OutputStatus::Failed);
        assert_eq!(result.exit_status, 1);
        assert!(result.stderr.is_some());
    }

    #[test]
    fn unknown_status_never_becomes_warm_and_status_is_read_only() {
        let result = run(
            input(
                Command::Status,
                RequestState::On,
                vec![output("DP-1", OutputOwnership::Unknown)],
            ),
            &[],
        );

        assert_eq!(result.request, RequestState::On);
        assert!(result.actions.is_empty());
        assert_eq!(result.outputs[0].status, OutputStatus::Unknown);
        assert_eq!(result.exit_status, 0);
        assert!(result.stderr.is_none());
        assert!(result.stdout.contains("status=unknown"));
    }

    #[test]
    fn status_without_the_watcher_keeps_the_request_and_reports_outputs_unknown() {
        let mut test_input = input(
            Command::Status,
            RequestState::On,
            vec![output("DP-1", OutputOwnership::WarmOwned)],
        );
        test_input.watcher_available = false;
        let result = run(test_input, &[]);

        assert_eq!(result.request, RequestState::On);
        assert!(result.actions.is_empty());
        assert_eq!(result.outputs[0].status, OutputStatus::Unknown);
        assert_eq!(result.exit_status, 0);
        assert!(result.stderr.is_none());
        assert!(result.stdout.contains("request=on watcher=unavailable"));
        assert!(result.stdout.contains("status=unknown"));
    }

    #[test]
    fn toggle_uses_the_session_request_and_formats_status_deterministically() {
        let result = run(
            input(
                Command::Toggle,
                RequestState::Off,
                vec![output("DP-1", OutputOwnership::Unowned)],
            ),
            &[outcome(
                "DP-1",
                Outcome::Succeeded,
                Outcome::Succeeded,
                Outcome::NotAttempted,
            )],
        );

        assert_eq!(result.request, RequestState::On);
        assert_eq!(result.outputs[0].status, OutputStatus::Warm);
        assert!(result
            .stdout
            .starts_with("night-light request=on watcher=available\n"));
        assert!(result
            .stdout
            .ends_with("night-light output=DP-1 status=warm\n"));
    }

    #[test]
    fn toggle_from_on_restores_only_the_confirmed_warm_output() {
        let result = run(
            input(
                Command::Toggle,
                RequestState::On,
                vec![output("DP-1", OutputOwnership::WarmOwned)],
            ),
            &[outcome(
                "DP-1",
                Outcome::Succeeded,
                Outcome::Succeeded,
                Outcome::Succeeded,
            )],
        );

        assert_eq!(result.request, RequestState::Off);
        assert_eq!(
            result.actions,
            vec![Action::RestoreOwned {
                output: "DP-1".to_owned()
            }]
        );
        assert_eq!(result.outputs[0].status, OutputStatus::NotWarmed);
        assert_eq!(result.exit_status, 0);
    }
}
