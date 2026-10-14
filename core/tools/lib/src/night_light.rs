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
    /// A watcher-known reason an output is not warm, or why its state is unknown.
    /// An unowned output without a reason is normal from Night Light's perspective.
    pub reason: Option<String>,
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

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum Outcome {
    Succeeded,
    Failed(String),
    Unknown(String),
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

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum OutputStatus {
    Warm,
    Normal,
    NotWarm { reason: String },
    Unknown { reason: String },
}

impl OutputStatus {
    fn render(&self) -> String {
        match self {
            Self::Warm => "warm".to_owned(),
            Self::Normal => "normal".to_owned(),
            Self::NotWarm { reason } => format!("not warm: {}", single_line(reason)),
            Self::Unknown { reason } => format!("unknown: {}", single_line(reason)),
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
            status: unknown("display watcher unavailable"),
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
                Command::Status => status_from_ownership(&output),
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
    let turning_on = plan.watcher_available
        && (plan.command == Command::On
            || (plan.command == Command::Toggle && plan.request == RequestState::On));

    for action in &plan.actions {
        let identity = action_output(action);
        let result = outcomes.iter().find(|item| item.identity == identity);
        let report = outputs.iter_mut().find(|item| item.identity == identity);
        let (Some(result), Some(report)) = (result, report) else {
            continue;
        };

        report.status = match action {
            Action::AcquireAndApply4000K { .. } => match &result.acquisition {
                Outcome::Succeeded => application_status(&result.application),
                Outcome::Failed(reason) => not_warm(reason),
                Outcome::Unknown(reason) => unknown(reason),
                Outcome::NotAttempted => unknown("watcher did not attempt acquisition"),
            },
            Action::Apply4000K { .. } => application_status(&result.application),
            Action::RestoreOwned { .. } => match &result.restoration {
                Outcome::Succeeded => OutputStatus::Normal,
                Outcome::Failed(reason) => unknown(&format!("restoration failed: {reason}")),
                Outcome::Unknown(reason) => unknown(reason),
                Outcome::NotAttempted => unknown("watcher did not attempt restoration"),
            },
            // Releasing an unwarmed handle does not change the output's color.
            Action::ReleaseUnwarmed { .. } => report.status.clone(),
        };
    }

    // NL7 defines the state as on only when at least one connected output is
    // confirmed warm; an empty set or unconfirmed results cannot claim on.
    let request = if turning_on
        && !outputs
            .iter()
            .any(|output| output.status == OutputStatus::Warm)
    {
        RequestState::Off
    } else {
        plan.request
    };

    let mut stderr = plan.initial_error;
    if stderr.is_none() && plan.command != Command::Status {
        let failures: Vec<String> = outputs
            .iter()
            .filter_map(|output| match &output.status {
                OutputStatus::NotWarm { reason } if turning_on => Some(format!(
                    "scottland-display: night-light: {}: not warm: {}",
                    single_line(&output.identity),
                    single_line(reason)
                )),
                OutputStatus::Unknown { reason } => Some(format!(
                    "scottland-display: night-light: {}: unknown: {}",
                    single_line(&output.identity),
                    single_line(reason)
                )),
                _ => None,
            })
            .collect();
        if !failures.is_empty() {
            stderr = Some(failures.join("\n"));
        }
    }

    let stdout = format_status(request, &outputs);
    let exit_status = if stderr.is_some() { 1 } else { 0 };

    Decision {
        request,
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
            unknown("watcher has not completed warming this output")
        }
        OutputOwnership::OwnedUnwarmed => {
            actions.push(Action::Apply4000K {
                output: output.identity.clone(),
            });
            unknown("watcher has not completed warming this output")
        }
        OutputOwnership::Unknown => status_from_ownership(output),
    }
}

fn plan_off(output: &OutputInput, actions: &mut Vec<Action>) -> OutputStatus {
    match output.ownership {
        OutputOwnership::WarmOwned => {
            actions.push(Action::RestoreOwned {
                output: output.identity.clone(),
            });
            unknown("watcher has not completed restoration of this output")
        }
        OutputOwnership::OwnedUnwarmed => {
            actions.push(Action::ReleaseUnwarmed {
                output: output.identity.clone(),
            });
            not_warm(output.reason.as_deref().unwrap_or("not warmed by Night Light"))
        }
        OutputOwnership::Unowned => status_from_ownership(output),
        OutputOwnership::Unknown => status_from_ownership(output),
    }
}

fn application_status(outcome: &Outcome) -> OutputStatus {
    match outcome {
        Outcome::Succeeded => OutputStatus::Warm,
        Outcome::Failed(reason) => not_warm(reason),
        Outcome::Unknown(reason) => unknown(reason),
        Outcome::NotAttempted => unknown("watcher did not attempt application"),
    }
}

fn status_from_ownership(output: &OutputInput) -> OutputStatus {
    match output.ownership {
        OutputOwnership::Unowned => output.reason.as_deref().map_or(
            OutputStatus::Normal,
            not_warm,
        ),
        OutputOwnership::OwnedUnwarmed => not_warm(
            output
                .reason
                .as_deref()
                .unwrap_or("not warmed by Night Light"),
        ),
        OutputOwnership::WarmOwned => OutputStatus::Warm,
        OutputOwnership::Unknown => unknown(
            output
                .reason
                .as_deref()
                .unwrap_or("watcher has not confirmed the output state"),
        ),
    }
}

fn not_warm(reason: &str) -> OutputStatus {
    OutputStatus::NotWarm {
        reason: nonempty_reason(reason, "output could not be warmed"),
    }
}

fn unknown(reason: &str) -> OutputStatus {
    OutputStatus::Unknown {
        reason: nonempty_reason(reason, "watcher did not confirm the output state"),
    }
}

fn nonempty_reason(reason: &str, fallback: &str) -> String {
    let reason = reason.trim();
    if reason.is_empty() {
        fallback.to_owned()
    } else {
        reason.to_owned()
    }
}

fn single_line(value: &str) -> String {
    value.replace('\n', "\\n").replace('\r', "\\r")
}

fn action_output(action: &Action) -> &str {
    match action {
        Action::AcquireAndApply4000K { output }
        | Action::Apply4000K { output }
        | Action::RestoreOwned { output }
        | Action::ReleaseUnwarmed { output } => output,
    }
}

fn format_status(request: RequestState, outputs: &[OutputReport]) -> String {
    let mut rendered = format!("{}\n", request.as_str());
    for output in outputs {
        let safe_identity = single_line(&output.identity);
        let _ = writeln!(
            rendered,
            "{safe_identity}: {}",
            output.status.render()
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
            reason: None,
        }
    }

    fn failed(reason: &str) -> Outcome {
        Outcome::Failed(reason.to_owned())
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
            assert_eq!(
                result.outputs[0].status,
                unknown("display watcher unavailable")
            );
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
        assert_eq!(
            result.outputs[0].status,
            unknown("display watcher unavailable")
        );
        assert_eq!(result.exit_status, 1);
        assert!(result.stderr.as_deref().unwrap().contains("request cleared"));
        assert!(result.stdout.starts_with("off\n"));
        assert!(result.stdout.ends_with("DP-1: unknown: display watcher unavailable\n"));
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
    fn partial_application_failure_keeps_the_request_and_names_the_failed_output_reason() {
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
                    failed("controlled by another program"),
                    Outcome::NotAttempted,
                ),
            ],
        );

        assert_eq!(result.request, RequestState::On);
        assert_eq!(result.outputs[0].status, OutputStatus::Warm);
        assert_eq!(
            result.outputs[1].status,
            OutputStatus::NotWarm {
                reason: "controlled by another program".to_owned()
            }
        );
        assert_eq!(result.exit_status, 1);
        assert_eq!(
            result.stderr.as_deref(),
            Some("scottland-display: night-light: DP-2: not warm: controlled by another program")
        );
        assert!(result.stdout.starts_with("on\n"));
        assert!(result.stdout.contains("DP-1: warm\n"));
        assert!(result
            .stdout
            .contains("DP-2: not warm: controlled by another program\n"));
    }

    #[test]
    fn all_warming_failures_clear_the_request_and_report_each_output_reason() {
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
                    failed("gamma is owned elsewhere"),
                    Outcome::NotAttempted,
                    Outcome::NotAttempted,
                ),
                outcome(
                    "DP-2",
                    Outcome::Succeeded,
                    failed("gamma application refused"),
                    Outcome::NotAttempted,
                ),
            ],
        );

        assert_eq!(result.request, RequestState::Off);
        assert_eq!(
            result.outputs[0].status,
            OutputStatus::NotWarm {
                reason: "gamma is owned elsewhere".to_owned()
            }
        );
        assert_eq!(
            result.outputs[1].status,
            OutputStatus::NotWarm {
                reason: "gamma application refused".to_owned()
            }
        );
        assert_eq!(result.exit_status, 1);
        assert_eq!(
            result.stderr.as_deref(),
            Some("scottland-display: night-light: DP-1: not warm: gamma is owned elsewhere\nscottland-display: night-light: DP-2: not warm: gamma application refused")
        );
        assert!(result.stdout.starts_with("off\n"));
    }

    #[test]
    fn an_on_request_with_no_confirmed_warm_output_reports_off() {
        let result = run(input(Command::On, RequestState::Off, Vec::new()), &[]);

        assert_eq!(result.request, RequestState::Off);
        assert_eq!(result.stdout, "off\n");
        assert_eq!(result.exit_status, 0);
        assert!(result.stderr.is_none());
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
        assert_eq!(result.outputs[0].status, OutputStatus::Normal);
        assert_eq!(result.outputs[1].status, OutputStatus::Normal);
        assert_eq!(
            result.outputs[2].status,
            OutputStatus::NotWarm {
                reason: "not warmed by Night Light".to_owned()
            }
        );
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
                failed("gamma restoration refused"),
            )],
        );

        assert_eq!(result.request, RequestState::Off);
        assert_eq!(
            result.outputs[0].status,
            OutputStatus::Unknown {
                reason: "restoration failed: gamma restoration refused".to_owned()
            }
        );
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
        assert_eq!(
            result.outputs[0].status,
            unknown("watcher has not confirmed the output state")
        );
        assert_eq!(result.exit_status, 0);
        assert!(result.stderr.is_none());
        assert_eq!(
            result.stdout,
            "on\nDP-1: unknown: watcher has not confirmed the output state\n"
        );
    }

    #[test]
    fn status_lists_real_output_states_after_the_request_line() {
        let mut held_elsewhere = output("DP-2", OutputOwnership::Unowned);
        held_elsewhere.reason = Some("controlled by another program".to_owned());
        let result = run(
            input(
                Command::Status,
                RequestState::On,
                vec![
                    output("eDP-1", OutputOwnership::WarmOwned),
                    held_elsewhere,
                    output("HDMI-1", OutputOwnership::Unowned),
                ],
            ),
            &[],
        );

        assert_eq!(result.request, RequestState::On);
        assert!(result.actions.is_empty());
        assert_eq!(result.exit_status, 0);
        assert!(result.stderr.is_none());
        assert_eq!(
            result.stdout,
            "on\neDP-1: warm\nDP-2: not warm: controlled by another program\nHDMI-1: normal\n"
        );
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
        assert_eq!(
            result.outputs[0].status,
            unknown("display watcher unavailable")
        );
        assert_eq!(result.exit_status, 0);
        assert!(result.stderr.is_none());
        assert_eq!(
            result.stdout,
            "on\nDP-1: unknown: display watcher unavailable\n"
        );
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
        assert_eq!(result.stdout, "on\nDP-1: warm\n");
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
        assert_eq!(result.outputs[0].status, OutputStatus::Normal);
        assert_eq!(result.exit_status, 0);
    }
}
