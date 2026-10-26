//! `record` (CP6 to CP9): start a recording of the live screen, or stop the running one and
//! finish its file.

use crate::desktop::{Desktop, session_command};
use crate::folders::{private_parent, recording_dir, unused_name};
use crate::notify::{Notice, show, show_and_wait_for_click};
use crate::overlay::PrivateDir;
use crate::pick::{PickMode, pick};
use crate::run::{last_line, wait_bounded};
use scottland::session::Session;
use std::fs;
use std::io::{self, Write};
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

/// The largest size a whole output is recorded at; bigger outputs are scaled to fit.
const LARGEST: (u64, u64) = (3840, 2160);
/// How long the recorder has to start writing its file.
const START_LIMIT: Duration = Duration::from_secs(10);
/// How long the recorder has to finish its file after it is asked to stop (CP7).
const STOP_LIMIT: Duration = Duration::from_secs(5);
/// How long the "saved" notice waits for a click: a little longer than it shows.
const CLICK_LIMIT: Duration = Duration::from_secs(12);

const FORCED_SUMMARY: &str = "Screen recording error";
const FORCED_BODY: &str = "The recording had to be forced to stop. The video may be damaged.";

#[derive(Debug, Default, PartialEq)]
pub struct Options {
    pub fullscreen: bool,
    pub desktop_audio: bool,
    pub microphone: bool,
    pub resolution: Option<String>,
    pub stop: bool,
}

/// Start a recording, or stop the running one.
pub fn run(session: &Session, options: &Options) -> Result<(), String> {
    match Recording::find()? {
        Some(recording) => stop(session, recording),
        None if options.stop => Err("nothing is recording".to_string()),
        None => start(session, options),
    }
}

/// What CP9's state says about the running recording.
#[derive(Debug, PartialEq)]
struct Recording {
    pid: i32,
    /// The process's start time, so a later process given the same id is not taken for it.
    started: u64,
    file: PathBuf,
}

impl Recording {
    fn state_file() -> Result<PathBuf, String> {
        Ok(private_parent()?.join("capture-recording"))
    }

    /// The running recording; state naming a process that is no longer recording is removed.
    fn find() -> Result<Option<Self>, String> {
        let state = Self::state_file()?;
        let text = match fs::read_to_string(&state) {
            Ok(text) => text,
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(format!("cannot read {}: {error}", state.display())),
        };
        match Self::parse(&text) {
            Some(recording) if recording.is_running() => Ok(Some(recording)),
            _ => {
                let _ = fs::remove_file(&state);
                Ok(None)
            }
        }
    }

    fn parse(text: &str) -> Option<Self> {
        let (process, file) = text.split_once('\n')?;
        let (pid, started) = process.split_once(' ')?;
        let file = file.strip_suffix('\n').unwrap_or(file);
        if file.is_empty() {
            return None;
        }
        Some(Self {
            pid: pid.parse().ok()?,
            started: started.parse().ok()?,
            file: PathBuf::from(file),
        })
    }

    fn save(&self) -> Result<(), String> {
        let state = Self::state_file()?;
        let partial = state.with_extension(format!("partial-{}", std::process::id()));
        let text = format!("{} {}\n{}\n", self.pid, self.started, self.file.display());
        fs::write(&partial, text)
            .and_then(|_| fs::rename(&partial, &state))
            .map_err(|error| {
                let _ = fs::remove_file(&partial);
                format!("cannot write {}: {error}", state.display())
            })
    }

    fn forget() {
        if let Ok(state) = Self::state_file() {
            let _ = fs::remove_file(state);
        }
    }

    fn is_running(&self) -> bool {
        process_start(self.pid) == Some(self.started)
    }
}

/// When process `pid` started, in clock ticks after boot; `None` when there is no such live
/// process (gone, or a zombie that has finished).
fn process_start(pid: i32) -> Option<u64> {
    let stat = fs::read_to_string(format!("/proc/{pid}/stat")).ok()?;
    parse_stat(&stat)
}

fn parse_stat(stat: &str) -> Option<u64> {
    // The command name is in parentheses and may hold spaces; the fields after it are plain.
    let fields: Vec<&str> = stat[stat.rfind(')')? + 1..].split_whitespace().collect();
    // fields[0] is the state (field 3 of proc(5)); fields[19] the start time (field 22).
    if matches!(*fields.first()?, "Z" | "X" | "x") {
        return None;
    }
    fields.get(19)?.parse().ok()
}

fn start(session: &Session, options: &Options) -> Result<(), String> {
    let folder = recording_dir()?;
    if !folder.is_dir() {
        let path = folder.display().to_string();
        show(
            session,
            &Notice::new("Screen recording folder does not exist").body(&path),
        );
        return Err(format!("the recordings folder {path} does not exist"));
    }
    let desktop = Desktop::read(session)?;
    let mode = if options.fullscreen {
        PickMode::Fullscreen
    } else {
        PickMode::Smart
    };
    let Some(picked) = pick(session, &desktop, mode, false)? else {
        return Err("cancelled".to_string());
    };
    let rect = picked.rect;
    if rect.width == 0 || rect.height == 0 {
        return Err("the picked area is empty".to_string());
    }

    let mut arguments: Vec<String> = Vec::new();
    match desktop.whole_output(rect) {
        Some(output) => {
            arguments.extend(["-w".into(), output.name.clone()]);
            let pixels = output.pixels.unwrap_or((
                (rect.width as f64 * output.scale).round() as u64,
                (rect.height as f64 * output.scale).round() as u64,
            ));
            let size = options.resolution.clone().or_else(|| {
                (pixels.0 > LARGEST.0 || pixels.1 > LARGEST.1)
                    .then(|| format!("{}x{}", LARGEST.0, LARGEST.1))
            });
            if let Some(size) = size {
                arguments.extend(["-s".into(), size]);
            }
        }
        None => {
            // The recorder takes a region in the same logical layout pixels as the pick.
            arguments.extend([
                "-w".into(),
                format!("{}x{}+{}+{}", rect.width, rect.height, rect.x, rect.y),
            ]);
            if let Some(size) = &options.resolution {
                arguments.extend(["-s".into(), size.clone()]);
            }
        }
    }
    let file = unused_name(&folder, "screenrecording", "mp4");
    arguments.extend(
        [
            "-k",
            "auto",
            "-f",
            "60",
            "-fm",
            "cfr",
            "-fallback-cpu-encoding",
            "yes",
            "-o",
        ]
        .map(String::from),
    );
    arguments.push(file.to_string_lossy().into_owned());
    // Both sources go into one track: most players play only one track of several.
    let audio: Vec<&str> = [
        options.desktop_audio.then_some("default_output"),
        options.microphone.then_some("default_input"),
    ]
    .into_iter()
    .flatten()
    .collect();
    if !audio.is_empty() {
        arguments.extend(["-a".into(), audio.join("|"), "-ac".into(), "aac".into()]);
    }

    // The recorder's messages go to a file in a private folder that is removed when this
    // command exits; the recorder keeps writing to it unseen, and nothing is left behind.
    let dir = PrivateDir::create()?;
    let log = dir.path().join("recorder.log");
    let log_file =
        fs::File::create(&log).map_err(|error| format!("cannot start the recorder: {error}"))?;
    let mut command = session_command(session, "gpu-screen-recorder");
    command
        .args(&arguments)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(log_file);
    // SAFETY: setsid is async-signal-safe and takes no arguments. Its own session keeps the
    // recorder running when the terminal or key binding that started this command goes away.
    unsafe {
        command.pre_exec(|| {
            libc::setsid();
            Ok(())
        });
    }
    let mut child = command
        .spawn()
        .map_err(|error| format!("cannot run gpu-screen-recorder: {error}"))?;
    let pid = child.id() as i32;
    let started = Instant::now();
    let failure = loop {
        if let Ok(Some(_)) = child.try_wait() {
            break format!("the recording did not start: {}", recorder_said(&log));
        }
        if fs::metadata(&file).is_ok_and(|metadata| metadata.len() > 0) {
            let Some(started) = process_start(pid) else {
                break format!("the recording did not start: {}", recorder_said(&log));
            };
            let recording = Recording {
                pid,
                started,
                file: file.clone(),
            };
            if let Err(error) = recording.save() {
                break error;
            }
            return Ok(());
        }
        if started.elapsed() > START_LIMIT {
            break format!(
                "the recording did not start within {} seconds: {}",
                START_LIMIT.as_secs(),
                recorder_said(&log)
            );
        }
        thread::sleep(Duration::from_millis(50));
    };
    let _ = child.kill();
    let _ = child.wait();
    let _ = fs::remove_file(&file);
    Err(failure)
}

fn recorder_said(log: &Path) -> String {
    format!(
        "gpu-screen-recorder said: {}",
        last_line(&fs::read(log).unwrap_or_default())
    )
}

fn stop(session: &Session, recording: Recording) -> Result<(), String> {
    // SIGINT makes the recorder finish the file properly.
    // SAFETY: kill sends a signal; the id was checked against its start time just now.
    unsafe {
        libc::kill(recording.pid, libc::SIGINT);
    }
    let asked = Instant::now();
    while recording.is_running() && asked.elapsed() < STOP_LIMIT {
        thread::sleep(Duration::from_millis(50));
    }
    if recording.is_running() {
        // SAFETY: as above.
        unsafe {
            libc::kill(recording.pid, libc::SIGKILL);
        }
        Recording::forget();
        show(
            session,
            &Notice::new(FORCED_SUMMARY).body(FORCED_BODY).critical(),
        );
        return Err("the recording had to be forced to stop; the video may be damaged".to_string());
    }
    Recording::forget();
    let file = recording.file;
    if let Err(why) = finish(session, &file) {
        eprintln!(
            "scottland-capture: kept {} unprocessed: {why}",
            file.display()
        );
    }
    println!("{}", file.display());
    let _ = io::stdout().flush();
    // Whoever reads the path has it now; let them stop reading while the notice waits.
    release_stdout();

    let dir = PrivateDir::create()?;
    let preview = dir.path().join("preview.png");
    let made = session_command(session, "ffmpeg")
        .args(["-y", "-loglevel", "quiet", "-i"])
        .arg(&file)
        .args(["-ss", "00:00:00.1", "-vframes", "1", "-q:v", "2"])
        .arg(&preview)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|status| status.success());
    let name = file
        .file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_default();
    let mut notice = Notice::new("Screen recording saved").body(&name);
    if made {
        notice.icon = Some(&preview);
    }
    if show_and_wait_for_click(session, &notice, CLICK_LIMIT) {
        open(session, &file);
    }
    Ok(())
}

/// CP7's processing pass: drop the first 0.1 seconds and, with audio, mute the capture pop, fade
/// in and normalize loudness. The file is replaced only when the pass succeeded.
fn finish(session: &Session, file: &Path) -> Result<(), String> {
    let has_audio = probe(
        session,
        file,
        &["-select_streams", "a", "-show_entries", "stream=codec_type"],
    )?
    .contains("audio");
    // Copying the video is lossless, but a first group of pictures holding discardable frames
    // copies as a frozen start; those files are encoded again.
    let discardable = probe(
        session,
        file,
        &[
            "-select_streams",
            "v:0",
            "-read_intervals",
            "%+0.2",
            "-show_entries",
            "packet=flags",
        ],
    )?
    .contains('D');
    let processed = file.with_file_name(format!(
        "{}-processed.mp4",
        file.file_stem().unwrap_or_default().to_string_lossy()
    ));
    let mut command = session_command(session, "ffmpeg");
    command
        .args([
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            "0.1",
            "-i",
        ])
        .arg(file);
    if discardable {
        command.args(["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]);
    } else {
        command.args(["-c:v", "copy"]);
    }
    if has_audio {
        // Mute the first 0.4 seconds, where the audio capture opens with a near-clipping pop,
        // fade in over 0.05 seconds so the edge does not click, then normalize.
        command.args([
            "-af",
            "volume=enable='lt(t,0.4)':volume=0,afade=t=in:st=0.4:d=0.05,loudnorm=I=-14:TP=-1.5:LRA=11",
        ]);
    }
    let result = command
        .arg(&processed)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .output()
        .map_err(|error| format!("cannot run ffmpeg: {error}"))?;
    if !result.status.success() {
        let _ = fs::remove_file(&processed);
        return Err(format!("ffmpeg said: {}", last_line(&result.stderr)));
    }
    fs::rename(&processed, file).map_err(|error| {
        let _ = fs::remove_file(&processed);
        format!("cannot replace the file: {error}")
    })
}

fn probe(session: &Session, file: &Path, options: &[&str]) -> Result<String, String> {
    let result = session_command(session, "ffprobe")
        .args(["-v", "error"])
        .args(options)
        .args(["-of", "csv=p=0"])
        .arg(file)
        .stdin(Stdio::null())
        .stderr(Stdio::piped())
        .output()
        .map_err(|error| format!("cannot run ffprobe: {error}"))?;
    if !result.status.success() {
        return Err(format!("ffprobe said: {}", last_line(&result.stderr)));
    }
    Ok(String::from_utf8_lossy(&result.stdout).into_owned())
}

/// Point standard output at /dev/null.
fn release_stdout() {
    let Ok(null) = fs::OpenOptions::new().write(true).open("/dev/null") else {
        return;
    };
    // SAFETY: dup2 duplicates a descriptor this function owns onto standard output, which is
    // already flushed; the original descriptor is closed when `null` drops.
    unsafe {
        libc::dup2(std::os::fd::AsRawFd::as_raw_fd(&null), libc::STDOUT_FILENO);
    }
}

/// Open `file` in the default video player, which the user asked for by clicking.
fn open(session: &Session, file: &Path) {
    let mut command: Command = session_command(session, "xdg-open");
    command
        .arg(file)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    if let Ok(mut child) = command.spawn() {
        // xdg-open hands the file to the player and returns.
        wait_bounded(&mut child, Duration::from_secs(10));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn state_round_trips() {
        let recording = Recording {
            pid: 4242,
            started: 987654,
            file: PathBuf::from("/home/u/Videos/screenrecording-2026-10-07_14-22-05.mp4"),
        };
        let text = format!(
            "{} {}\n{}\n",
            recording.pid,
            recording.started,
            recording.file.display()
        );
        assert_eq!(Recording::parse(&text), Some(recording));
        assert_eq!(Recording::parse("4242\n/x.mp4\n"), None);
        assert_eq!(Recording::parse("4242 1\n"), None);
    }

    #[test]
    fn reads_the_start_time_and_skips_finished_processes() {
        let fields = "S 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 777 20";
        assert_eq!(
            parse_stat(&format!("12 (gpu-screen-reco) {fields}")),
            Some(777)
        );
        // A name holding spaces and a parenthesis does not shift the fields.
        assert_eq!(parse_stat(&format!("12 (a) b) {fields}")), Some(777));
        let zombie = fields.replacen('S', "Z", 1);
        assert_eq!(parse_stat(&format!("12 (gpu-screen-reco) {zombie}")), None);
    }

    #[test]
    fn this_process_counts_as_running_and_a_changed_start_time_does_not() {
        let pid = std::process::id() as i32;
        let started = process_start(pid).expect("this process has a start time");
        let mut recording = Recording {
            pid,
            started,
            file: PathBuf::from("/x.mp4"),
        };
        assert!(recording.is_running());
        recording.started += 1;
        assert!(!recording.is_running());
    }
}
