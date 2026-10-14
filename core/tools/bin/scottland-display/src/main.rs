use std::process::ExitCode;

fn main() -> ExitCode {
    match scottland_display::run(&std::env::args_os().skip(1).collect::<Vec<_>>()) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("scottland-display: {error}");
            ExitCode::FAILURE
        }
    }
}
