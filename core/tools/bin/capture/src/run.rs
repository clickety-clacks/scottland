//! Running the helper programs a command uses.

use std::io::{self, Write};
use std::process::{Child, Command, ExitStatus, Output, Stdio};
use std::thread;
use std::time::{Duration, Instant};

/// Run `command` with `input` on standard input and collect what it writes.
pub fn with_input(command: &mut Command, input: &[u8]) -> io::Result<Output> {
    let mut child = command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;
    let mut stdin = child.stdin.take().expect("stdin is piped");
    let input = input.to_vec();
    let writer = thread::spawn(move || {
        // A program that stops reading early (a decoder that found what it wanted) is not an
        // error here; its exit status says how it went.
        let _ = stdin.write_all(&input);
    });
    let output = child.wait_with_output();
    let _ = writer.join();
    output
}

/// Wait up to `limit` for `child` to exit; kill it when it has not.
pub fn wait_bounded(child: &mut Child, limit: Duration) -> Option<ExitStatus> {
    let started = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return Some(status),
            Ok(None) if started.elapsed() < limit => thread::sleep(Duration::from_millis(20)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return None;
            }
        }
    }
}

/// The last line a program wrote to standard error, for a short reason.
pub fn last_line(stderr: &[u8]) -> String {
    String::from_utf8_lossy(stderr)
        .lines()
        .map(str::trim)
        .rfind(|line| !line.is_empty())
        .unwrap_or("no message")
        .to_string()
}
