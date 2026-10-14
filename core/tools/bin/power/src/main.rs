use std::env;
use std::process::ExitCode;

use scottland_power::{SystemBackend, execute};

fn main() -> ExitCode {
    let args: Vec<_> = env::args_os().skip(1).collect();
    let mut backend = SystemBackend::default();
    let result = if args.len() == 1 && args[0] == "__shutdown-worker" {
        backend.shutdown_worker()
    } else {
        let output = execute(&args, &mut backend);
        print!("{}", output.stdout);
        eprint!("{}", output.stderr);
        output.status
    };
    ExitCode::from(result)
}
