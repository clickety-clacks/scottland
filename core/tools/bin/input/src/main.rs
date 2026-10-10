use std::env;
use std::ffi::OsString;
use std::process::ExitCode;

use scottland::dirs;
use scottland_input::{SystemBackend, SystemDisplay, USAGE, parse_command, run};

fn main() -> ExitCode {
    let args: Vec<OsString> = env::args_os().skip(1).collect();
    let command = match parse_command(&args) {
        Ok(command) => command,
        Err(()) => {
            eprintln!("{USAGE}");
            return ExitCode::from(2);
        }
    };
    let state = match dirs::state_dir() {
        Ok(path) => path,
        Err(error) => {
            eprintln!("scottland-input: could not find the state directory: {error}");
            return ExitCode::FAILURE;
        }
    };
    let result = run(command, &state, &mut SystemBackend, &mut SystemDisplay);
    print!("{}", result.stdout);
    eprint!("{}", result.stderr);
    ExitCode::from(result.status)
}
