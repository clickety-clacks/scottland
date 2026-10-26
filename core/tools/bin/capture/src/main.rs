mod desktop;
mod folders;
mod image;
mod notify;
mod overlay;
mod pick;
mod picker;
mod record;
mod run;
mod scan;
mod screenshot;

use pick::PickMode;
use scottland::session;
use std::env;
use std::ffi::OsString;
use std::process::ExitCode;

const USAGE: &str = "\
usage: scottland-capture [--display DISPLAY] COMMAND

Commands:
  region [smart|region|windows|fullscreen]
      pick a rectangle and print it as X,Y WxH
  screenshot [smart|region|windows|fullscreen] [copy|save]
      pick and save the image as a PNG, put it on the clipboard, or both
  record [--fullscreen] [--desktop-audio] [--microphone] [--resolution WxH]
      pick and start recording the screen, or stop the running recording
  record --stop
      stop the running recording
  qr
      pick a region and copy the QR codes in it
  text
      pick a region and copy the text in it

The mode defaults to smart: drag a region, or click a window or the screen.
During a pick, Tab and the arrow keys move between windows, Return picks the
highlighted one, Ctrl+Return picks the whole screen and Esc cancels.

Options:
  --display DISPLAY  select a recorded Scottland session
  -h, --help         show this help
  -V, --version      show the version";

#[derive(Debug, PartialEq)]
enum Command {
    Help,
    Version,
    Region(PickMode),
    Screenshot(PickMode, screenshot::Output),
    Record(record::Options),
    Qr,
    Text,
}

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("scottland-capture: {error}");
            ExitCode::from(1)
        }
    }
}

fn run() -> Result<(), String> {
    let (display, command) = parse_args(env::args_os().skip(1))?;
    match command {
        Command::Help => {
            println!("{USAGE}");
            return Ok(());
        }
        Command::Version => {
            println!("scottland-capture {}", env!("CARGO_PKG_VERSION"));
            return Ok(());
        }
        _ => {}
    }
    let selected = session::current(display.as_deref())
        .map_err(|error| format!("cannot select a running Scottland session: {error}"))?;
    match command {
        Command::Region(mode) => {
            let desktop = desktop::Desktop::read(&selected)?;
            match pick::pick(&selected, &desktop, mode, false)? {
                Some(picked) => {
                    println!("{}", picker::geometry(picked.rect));
                    Ok(())
                }
                None => Err("cancelled".to_string()),
            }
        }
        Command::Screenshot(mode, output) => screenshot::run(&selected, mode, output),
        Command::Record(options) => record::run(&selected, &options),
        Command::Qr => scan::qr(&selected),
        Command::Text => scan::text(&selected),
        Command::Help | Command::Version => unreachable!("handled above"),
    }
}

fn parse_args(
    args: impl IntoIterator<Item = OsString>,
) -> Result<(Option<String>, Command), String> {
    let mut words = Vec::new();
    for arg in args {
        words.push(
            arg.into_string()
                .map_err(|arg| format!("argument is not UTF-8: {}", arg.to_string_lossy()))?,
        );
    }
    let mut display = None;
    let mut rest = words.as_slice();
    while let Some(first) = rest.first() {
        match first.as_str() {
            "-h" | "--help" => return Ok((display, Command::Help)),
            "-V" | "--version" => return Ok((display, Command::Version)),
            "--display" => {
                let value = rest.get(1).ok_or("--display needs a value")?;
                display = Some(value.clone());
                rest = &rest[2..];
            }
            _ => break,
        }
    }
    let (name, rest) = rest
        .split_first()
        .ok_or_else(|| format!("a command is required\n\n{USAGE}"))?;
    let command = match name.as_str() {
        "region" => {
            let (mode, extra) = mode_from(rest);
            no_more(extra)?;
            Command::Region(mode)
        }
        "screenshot" => {
            let (mode, extra) = mode_from(rest);
            let (output, extra) = match extra.split_first() {
                Some((word, extra)) if word == "copy" => (screenshot::Output::Copy, extra),
                Some((word, extra)) if word == "save" => (screenshot::Output::Save, extra),
                _ => (screenshot::Output::Both, extra),
            };
            no_more(extra)?;
            Command::Screenshot(mode, output)
        }
        "record" => Command::Record(record_options(rest)?),
        "qr" => {
            no_more(rest)?;
            Command::Qr
        }
        "text" => {
            no_more(rest)?;
            Command::Text
        }
        other => return Err(format!("unknown command {other:?}\n\n{USAGE}")),
    };
    Ok((display, command))
}

fn mode_from(words: &[String]) -> (PickMode, &[String]) {
    match words.split_first() {
        Some((word, rest)) => match PickMode::parse(word) {
            Some(mode) => (mode, rest),
            None => (PickMode::Smart, words),
        },
        None => (PickMode::Smart, words),
    }
}

fn no_more(words: &[String]) -> Result<(), String> {
    match words.first() {
        Some(word) => Err(format!("unexpected argument {word:?}\n\n{USAGE}")),
        None => Ok(()),
    }
}

fn record_options(words: &[String]) -> Result<record::Options, String> {
    let mut options = record::Options::default();
    let mut words = words.iter();
    while let Some(word) = words.next() {
        match word.as_str() {
            "--fullscreen" => options.fullscreen = true,
            "--desktop-audio" => options.desktop_audio = true,
            "--microphone" => options.microphone = true,
            "--stop" => options.stop = true,
            "--resolution" => {
                let size = words.next().ok_or("--resolution needs WxH")?;
                if !is_size(size) {
                    return Err(format!(
                        "--resolution takes WxH, such as 1920x1080, not {size:?}"
                    ));
                }
                options.resolution = Some(size.clone());
            }
            other => return Err(format!("unexpected argument {other:?}\n\n{USAGE}")),
        }
    }
    if options.stop
        && (options.fullscreen
            || options.desktop_audio
            || options.microphone
            || options.resolution.is_some())
    {
        return Err("record --stop takes no other options".to_string());
    }
    Ok(options)
}

fn is_size(text: &str) -> bool {
    let positive = |part: &str| {
        !part.is_empty()
            && part.bytes().all(|byte| byte.is_ascii_digit())
            && part.parse::<u32>().is_ok_and(|n| n > 0)
    };
    text.split_once('x')
        .is_some_and(|(width, height)| positive(width) && positive(height))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(line: &str) -> Result<(Option<String>, Command), String> {
        parse_args(line.split_whitespace().map(OsString::from))
    }

    fn command(line: &str) -> Command {
        parse(line).unwrap().1
    }

    #[test]
    fn modes_default_to_smart() {
        assert_eq!(command("region"), Command::Region(PickMode::Smart));
        assert_eq!(
            command("region windows"),
            Command::Region(PickMode::Windows)
        );
        assert_eq!(
            command("screenshot"),
            Command::Screenshot(PickMode::Smart, screenshot::Output::Both)
        );
        assert_eq!(
            command("screenshot copy"),
            Command::Screenshot(PickMode::Smart, screenshot::Output::Copy)
        );
        assert_eq!(
            command("screenshot fullscreen save"),
            Command::Screenshot(PickMode::Fullscreen, screenshot::Output::Save)
        );
        assert_eq!(command("qr"), Command::Qr);
        assert_eq!(command("text"), Command::Text);
    }

    #[test]
    fn record_options_parse() {
        assert_eq!(
            command("record --fullscreen --desktop-audio --microphone --resolution 1920x1080"),
            Command::Record(record::Options {
                fullscreen: true,
                desktop_audio: true,
                microphone: true,
                resolution: Some("1920x1080".into()),
                stop: false,
            })
        );
        assert_eq!(
            command("record --stop"),
            Command::Record(record::Options {
                stop: true,
                ..Default::default()
            })
        );
        assert!(parse("record --resolution 1920").is_err());
        assert!(parse("record --resolution 0x10").is_err());
        assert!(parse("record --resolution").is_err());
        assert!(parse("record --stop --microphone").is_err());
        assert!(parse("record --webcam").is_err());
    }

    #[test]
    fn display_help_and_mistakes() {
        assert_eq!(
            parse("--display wayland-1 qr").unwrap(),
            (Some("wayland-1".into()), Command::Qr)
        );
        assert_eq!(command("--help"), Command::Help);
        assert_eq!(command("-V"), Command::Version);
        assert!(parse("").is_err());
        assert!(parse("screenshot copy save").is_err());
        assert!(parse("region sideways").is_err());
        assert!(parse("qr region").is_err());
        assert!(parse("--display").is_err());
        assert!(parse("paint").is_err());
    }
}
