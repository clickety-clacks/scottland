use std::env;
use std::process::ExitCode;

fn main() -> ExitCode {
    let args: Vec<String> = env::args().skip(1).collect();
    match scottland_ctl::run(&args) {
        Ok(code) => ExitCode::from(code),
        Err(error) => {
            eprintln!("scottland-ctl: {error}");
            ExitCode::from(1)
        }
    }
}
