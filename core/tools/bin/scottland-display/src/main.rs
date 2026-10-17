use std::process::ExitCode;

fn main() -> ExitCode {
    match scottland_display::run_with_exit_status(&std::env::args_os().skip(1).collect::<Vec<_>>())
    {
        Ok(status) => ExitCode::from(status),
        Err(error) => {
            eprintln!("scottland-display: {error}");
            ExitCode::FAILURE
        }
    }
}
