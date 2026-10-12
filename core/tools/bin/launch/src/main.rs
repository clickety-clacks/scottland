use std::process::ExitCode;

fn main() -> ExitCode {
    match scottland_launch::run(std::env::args_os().skip(1).collect()) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("scottland-launch: {}", error.message);
            ExitCode::from(error.status)
        }
    }
}
