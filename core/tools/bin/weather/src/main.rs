use scottland::dirs;
use std::env;
use std::os::unix::process::CommandExt;
use std::path::PathBuf;
use std::process::{Command, ExitCode};

const USAGE: &str = "Usage: scottland-weather [--help]\n\nOpen Scottland Weather for the current Scottland location.";

fn main() -> ExitCode {
    let mut args = env::args_os().skip(1);
    match args.next().as_deref().and_then(|arg| arg.to_str()) {
        Some("--help" | "-h") if args.next().is_none() => {
            println!("{USAGE}");
            ExitCode::SUCCESS
        }
        None => launch(),
        _ => {
            eprintln!("{USAGE}");
            ExitCode::from(2)
        }
    }
}

fn launch() -> ExitCode {
    let Some(app) = app_directory() else {
        eprintln!("scottland-weather: its application files are not installed");
        return ExitCode::from(1);
    };

    let error = Command::new("qs").arg("-n").arg("-p").arg(app).exec();
    eprintln!("scottland-weather: cannot start Quickshell: {error}");
    ExitCode::from(127)
}

fn app_directory() -> Option<PathBuf> {
    let session = dirs::hooks_dir().join("apps/weather");
    if session.is_dir() {
        return Some(session);
    }

    let packaged = PathBuf::from("/usr/share/scottland/apps/weather");
    packaged.is_dir().then_some(packaged)
}
