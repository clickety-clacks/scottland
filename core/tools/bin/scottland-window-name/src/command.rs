use crate::invocation::Invocation;
use scottland::session::Environment;
use serde_json::Value;
use std::collections::BTreeMap;
use std::io::{Read, Write};
use std::os::unix::process::CommandExt;
use std::process::{Command, ExitStatus, Stdio};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

const MAX_OUTPUT_BYTES: usize = 1024 * 1024;

#[derive(Clone, Debug)]
pub struct ResultData {
    pub stdout: Vec<u8>,
}

#[derive(Default)]
pub struct Cache {
    results: BTreeMap<(Vec<String>, Vec<u8>, Environment), Result<ResultData, String>>,
}

impl Cache {
    pub fn run(
        &mut self,
        argv: &[String],
        input: &Value,
        timeout_ms: u64,
        environment: &Environment,
    ) -> Result<ResultData, String> {
        self.run_inner(argv, input, timeout_ms, environment, None)
    }

    pub fn run_with_invocation(
        &mut self,
        argv: &[String],
        input: &Value,
        timeout_ms: u64,
        environment: &Environment,
        invocation: &Invocation,
    ) -> Result<ResultData, String> {
        self.run_inner(argv, input, timeout_ms, environment, Some(invocation))
    }

    fn run_inner(
        &mut self,
        argv: &[String],
        input: &Value,
        timeout_ms: u64,
        environment: &Environment,
        invocation: Option<&Invocation>,
    ) -> Result<ResultData, String> {
        if let Some(invocation) = invocation {
            invocation.ensure_work()?;
        }
        let input_bytes = serde_json::to_vec(input)
            .map_err(|error| format!("cannot encode command input: {error}"))?;
        let key = (argv.to_vec(), input_bytes, environment.clone());
        if let Some(result) = self.results.get(&key) {
            return result.clone();
        }
        let result = match invocation {
            Some(invocation) => {
                run_once_with_invocation(argv, input, timeout_ms, environment, invocation)
            }
            None => run_once(argv, input, timeout_ms, environment),
        };
        self.results.insert(key, result.clone());
        result
    }
}

fn run_once(
    argv: &[String],
    input: &Value,
    timeout_ms: u64,
    environment: &Environment,
) -> Result<ResultData, String> {
    if timeout_ms == 0 {
        return Err("command timed out after 0 ms".to_string());
    }
    let program = argv
        .first()
        .filter(|program| !program.is_empty())
        .ok_or_else(|| "command has no executable".to_string())?;
    let stdin_bytes = serde_json::to_vec(input)
        .map_err(|error| format!("cannot encode command input: {error}"))?;
    let mut command = Command::new(program);
    command
        .env_clear()
        .envs(environment.iter())
        .args(&argv[1..])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .process_group(0);
    let mut child = command
        .spawn()
        .map_err(|error| format!("cannot start command: {error}"))?;
    let child_pid = child.id();
    let started = Instant::now();

    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "command stdout pipe is unavailable".to_string())?;
    let stderr = child
        .stderr
        .take()
        .ok_or_else(|| "command stderr pipe is unavailable".to_string())?;
    let stdout_reader = thread::spawn(move || read_capped(stdout));
    let stderr_reader = thread::spawn(move || read_capped(stderr));
    let stdin = child
        .stdin
        .take()
        .ok_or_else(|| "command stdin pipe is unavailable".to_string())?;
    let stdin_writer = thread::spawn(move || {
        let mut stdin = stdin;
        stdin.write_all(&stdin_bytes)
    });

    let timeout = Duration::from_millis(timeout_ms);
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if started.elapsed() < timeout => {
                thread::sleep(
                    Duration::from_millis(10).min(timeout.saturating_sub(started.elapsed())),
                );
            }
            Ok(None) => {
                kill_group(child_pid);
                let _ = child.kill();
                let _ = child.wait();
                let _ = join_reader(stdout_reader);
                let _ = join_reader(stderr_reader);
                let _ = stdin_writer.join();
                return Err(format!("command timed out after {timeout_ms} ms"));
            }
            Err(error) => {
                kill_group(child_pid);
                let _ = child.kill();
                let _ = child.wait();
                let _ = join_reader(stdout_reader);
                let _ = join_reader(stderr_reader);
                let _ = stdin_writer.join();
                return Err(format!("cannot wait for command: {error}"));
            }
        }
    };

    // A recipe is complete when its direct command exits. Reap any background descendants so
    // they cannot outlive the source invocation or hold the output pipes open.
    kill_group(child_pid);
    let _ = stdin_writer.join();
    let (stdout, stdout_overflow) = join_reader(stdout_reader)?;
    let _ = join_reader(stderr_reader)?;
    if !status.success() {
        return Err(exit_reason(status));
    }
    if stdout_overflow {
        return Err(format!(
            "command output exceeds the {MAX_OUTPUT_BYTES}-byte limit"
        ));
    }
    Ok(ResultData { stdout })
}

fn run_once_with_invocation(
    argv: &[String],
    input: &Value,
    timeout_ms: u64,
    environment: &Environment,
    invocation: &Invocation,
) -> Result<ResultData, String> {
    if timeout_ms == 0 {
        return Err("command timed out after 0 ms".to_string());
    }
    invocation.ensure_work()?;
    let program = argv
        .first()
        .filter(|program| !program.is_empty())
        .ok_or_else(|| "command has no executable".to_string())?;
    let stdin_bytes = serde_json::to_vec(input)
        .map_err(|error| format!("cannot encode command input: {error}"))?;
    let mut command = Command::new(program);
    command
        .env_clear()
        .envs(environment.iter())
        .args(&argv[1..])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .process_group(0);

    let service_pid = unsafe { libc::getpid() };
    let work_stop_ns = invocation.work_stop_ns();
    let work_stop_seconds = (work_stop_ns / 1_000_000_000) as libc::time_t;
    let work_stop_nanoseconds = (work_stop_ns % 1_000_000_000) as libc::c_long;
    unsafe {
        command.pre_exec(move || {
            if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL, 0, 0, 0) != 0 {
                return Err(std::io::Error::last_os_error());
            }
            if libc::getppid() != service_pid {
                libc::_exit(127);
            }
            let mut now = libc::timespec {
                tv_sec: 0,
                tv_nsec: 0,
            };
            if libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut now) != 0 {
                return Err(std::io::Error::last_os_error());
            }
            if now.tv_sec > work_stop_seconds
                || (now.tv_sec == work_stop_seconds && now.tv_nsec >= work_stop_nanoseconds)
            {
                libc::_exit(125);
            }
            Ok(())
        });
    }

    let mut child = invocation.spawn_group(|| command.spawn())?;
    let started = Instant::now();
    let child_pid = child.id();
    invocation.ensure_work()?;
    let source_timeout = Duration::from_millis(timeout_ms).min(invocation.remaining_work()?);
    if source_timeout.is_zero() {
        return Err("invocation budget expired".to_string());
    }

    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "command stdout pipe is unavailable".to_string())?;
    let stderr = child
        .stderr
        .take()
        .ok_or_else(|| "command stderr pipe is unavailable".to_string())?;
    let stdout_reader = thread::spawn(move || read_capped(stdout));
    let stderr_reader = thread::spawn(move || read_capped(stderr));
    let stdin = child
        .stdin
        .take()
        .ok_or_else(|| "command stdin pipe is unavailable".to_string())?;
    let stdin_writer = thread::spawn(move || {
        let mut stdin = stdin;
        stdin.write_all(&stdin_bytes)
    });

    let status = loop {
        invocation.ensure_work()?;
        match child_exited_without_reaping(child_pid) {
            Ok(true) => {
                if let Err(error) = invocation.signal_group_before_reap(child_pid) {
                    let _ = child.wait();
                    return Err(error);
                }
                break child
                    .wait()
                    .map_err(|error| format!("cannot reap command: {error}"))?;
            }
            Ok(false) => {}
            Err(error) => {
                let signal_error = invocation.signal_group_before_reap(child_pid).err();
                let _ = child.kill();
                if let Some(signal_error) = signal_error {
                    return Err(signal_error);
                }
                return Err(format!("cannot wait for command: {error}"));
            }
        }

        if started.elapsed() >= source_timeout {
            if let Err(error) = invocation.signal_group_before_reap(child_pid) {
                let _ = child.kill();
                return Err(error);
            }
            let _ = child.kill();
            loop {
                invocation.ensure_work()?;
                match child_exited_without_reaping(child_pid) {
                    Ok(true) => {
                        let _ = child.wait();
                        let _ = join_reader(stdout_reader);
                        let _ = join_reader(stderr_reader);
                        let _ = stdin_writer.join();
                        return Err(format!("command timed out after {timeout_ms} ms"));
                    }
                    Ok(false) => thread::sleep(Duration::from_millis(5)),
                    Err(error) => return Err(format!("cannot wait for command: {error}")),
                }
            }
        }
        thread::sleep(
            Duration::from_millis(10).min(source_timeout.saturating_sub(started.elapsed())),
        );
    };

    let _ = stdin_writer.join();
    let (stdout, stdout_overflow) = join_reader(stdout_reader)?;
    let _ = join_reader(stderr_reader)?;
    if !status.success() {
        return Err(exit_reason(status));
    }
    if stdout_overflow {
        return Err(format!(
            "command output exceeds the {MAX_OUTPUT_BYTES}-byte limit"
        ));
    }
    Ok(ResultData { stdout })
}

fn child_exited_without_reaping(pid: u32) -> std::io::Result<bool> {
    let mut information = std::mem::MaybeUninit::<libc::siginfo_t>::zeroed();
    let result = unsafe {
        libc::waitid(
            libc::P_PID,
            pid,
            information.as_mut_ptr(),
            libc::WEXITED | libc::WNOHANG | libc::WNOWAIT,
        )
    };
    if result != 0 {
        return Err(std::io::Error::last_os_error());
    }
    let information = unsafe { information.assume_init() };
    Ok(unsafe { information.si_pid() } != 0)
}

fn read_capped(mut reader: impl Read) -> std::io::Result<(Vec<u8>, bool)> {
    let mut output = Vec::new();
    let mut overflow = false;
    let mut buffer = [0_u8; 8192];
    loop {
        let read = reader.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        let room = MAX_OUTPUT_BYTES.saturating_sub(output.len());
        let keep = room.min(read);
        output.extend_from_slice(&buffer[..keep]);
        overflow |= keep < read;
    }
    Ok((output, overflow))
}

fn join_reader(
    reader: JoinHandle<std::io::Result<(Vec<u8>, bool)>>,
) -> Result<(Vec<u8>, bool), String> {
    reader
        .join()
        .map_err(|_| "command output reader stopped unexpectedly".to_string())?
        .map_err(|error| format!("cannot read command output: {error}"))
}

fn exit_reason(status: ExitStatus) -> String {
    match status.code() {
        Some(code) => format!("command exited with status {code}"),
        None => "command ended without a normal exit status".to_string(),
    }
}

fn kill_group(pid: u32) {
    if let Ok(pid) = i32::try_from(pid) {
        // The process_group call makes the child pid its own process-group id.
        unsafe {
            libc::kill(-pid, libc::SIGKILL);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::Cache;
    use scottland::session::Environment;
    use serde_json::json;
    use std::ffi::OsString;

    fn test_environment() -> Environment {
        let mut environment = Environment::new();
        if let Some(path) = std::env::var_os("PATH") {
            environment.insert(OsString::from("PATH"), path);
        }
        environment
    }

    #[test]
    fn caches_identical_argv_only_for_the_same_window_input() {
        let mut cache = Cache::default();
        let argv = vec!["cat".to_string()];
        let environment = test_environment();
        let first = cache
            .run(&argv, &json!({"window": 1}), 1000, &environment)
            .unwrap();
        let second = cache
            .run(&argv, &json!({"window": 2}), 1000, &environment)
            .unwrap();
        let repeated = cache
            .run(&argv, &json!({"window": 1}), 1000, &environment)
            .unwrap();
        assert_eq!(first.stdout, b"{\"window\":1}");
        assert_eq!(second.stdout, b"{\"window\":2}");
        assert_eq!(repeated.stdout, first.stdout);
    }

    #[test]
    fn does_not_repeat_an_identical_invocation_when_the_timeout_differs() {
        let mut cache = Cache::default();
        let argv = vec![
            "/bin/sh".to_string(),
            "-c".to_string(),
            "printf x".to_string(),
        ];
        let environment = test_environment();
        let first = cache
            .run(&argv, &json!({"window": 1}), 1000, &environment)
            .unwrap();
        let repeated = cache
            .run(&argv, &json!({"window": 1}), 0, &environment)
            .unwrap();
        assert_eq!(first.stdout, repeated.stdout);
    }

    #[test]
    fn stops_a_source_at_its_timeout() {
        let mut cache = Cache::default();
        let argv = vec!["sh".to_string(), "-c".to_string(), "sleep 10".to_string()];
        let error = cache
            .run(&argv, &json!({}), 40, &test_environment())
            .unwrap_err();
        assert!(error.contains("timed out"));
    }

    #[test]
    fn command_uses_the_selected_session_environment() {
        let mut cache = Cache::default();
        let argv = vec![
            "/bin/sh".to_string(),
            "-c".to_string(),
            "printf %s \"$SCOTTLAND_WINDOW_NAME_TEST\"".to_string(),
        ];
        let environment = Environment::from([(
            OsString::from("SCOTTLAND_WINDOW_NAME_TEST"),
            OsString::from("recorded-session"),
        )]);
        let result = cache.run(&argv, &json!({}), 1000, &environment).unwrap();
        assert_eq!(result.stdout, b"recorded-session");
    }
}
