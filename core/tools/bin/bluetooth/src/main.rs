use std::env;
use std::process::ExitCode;

use scottland_bluetooth::{SystemBackend, SystemWaiter, execute};

fn main() -> ExitCode {
    let args: Vec<_> = env::args_os().skip(1).collect();
    let result = execute(&args, &mut SystemBackend::default(), &mut SystemWaiter);
    print!("{}", result.stdout);
    eprint!("{}", result.stderr);
    ExitCode::from(result.status)
}
