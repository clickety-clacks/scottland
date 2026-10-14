//! Thin CLI adapter from the Night Light command policy to Display's watcher.

use crate::color_scheme;
use scottland::night_light;

/// Exact output data for the Display dispatcher to write and return.
///
/// Keeping the policy's status alongside its text lets the outer CLI preserve
/// Night Light's parse and operation statuses instead of flattening every
/// nonzero result into the dispatcher's generic error status.
#[derive(Debug, Eq, PartialEq)]
pub(crate) struct CliOutcome {
    pub(crate) stdout: String,
    pub(crate) stderr: Option<String>,
    pub(crate) exit_status: u8,
}

/// Execute the tokens following `night-light` using the selected session's
/// existing Display watcher. The caller writes `stdout` and `stderr` exactly
/// as returned and uses `exit_status` unchanged.
pub(crate) fn execute(args: &[&str]) -> Result<CliOutcome, String> {
    let command = match night_light::parse_command(args) {
        Ok(command) => command,
        Err(error) => {
            return Ok(CliOutcome {
                stdout: String::new(),
                stderr: Some(error.stderr_line()),
                exit_status: error.exit_status(),
            });
        }
    };

    let display = std::env::var("WAYLAND_DISPLAY").ok();
    let session =
        scottland::session::current(display.as_deref()).map_err(|error| error.to_string())?;
    let decision = color_scheme::request_night_light(&session, command)?;

    Ok(CliOutcome {
        stdout: decision.stdout,
        stderr: decision.stderr,
        exit_status: decision.exit_status,
    })
}
