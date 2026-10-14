use std::env;
use std::io::{self, Write};
use std::process::ExitCode;

use scottland_widget::{SystemBackend, run};

fn main() -> ExitCode {
    let output = run(env::args_os().skip(1), &mut SystemBackend::default());
    if let Err(error) = io::stdout().write_all(&output.stdout) {
        eprintln!("scottland-widget: could not write output: {error}");
        return ExitCode::FAILURE;
    }
    if let Err(error) = io::stderr().write_all(&output.stderr) {
        eprintln!("scottland-widget: could not write error: {error}");
        return ExitCode::FAILURE;
    }
    ExitCode::from(output.status)
}
