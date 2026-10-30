use std::collections::BTreeSet;
use std::io::{self, Write};
use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use std::time::Duration;

const WORK_STOP_MARGIN_NS: u64 = 200_000_000;

const ACTIVE: u8 = 0;
const EXPIRED: u8 = 1;
const FINISHED: u8 = 2;
const TERMINATING: u8 = 3;

#[derive(Clone)]
pub struct Invocation {
    inner: Arc<Inner>,
}

struct Inner {
    work_stop_ns: u64,
    service_exit_ns: u64,
    phase: AtomicU8,
    groups: Mutex<Groups>,
    timers: Arc<TimerStop>,
}

#[derive(Default)]
struct Groups {
    process_groups: BTreeSet<u32>,
    expiry_signalled: bool,
    signal_errors: Vec<String>,
    signal_errors_reported: bool,
}

#[derive(Default)]
struct TimerStop {
    stopped: Mutex<bool>,
    changed: Condvar,
}

impl TimerStop {
    fn wait_until(&self, deadline_ns: u64) -> bool {
        let mut stopped = self
            .stopped
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        loop {
            if *stopped {
                return false;
            }
            let now = monotonic_ns().unwrap_or(deadline_ns);
            if now >= deadline_ns {
                return true;
            }
            let timeout = Duration::from_nanos(deadline_ns - now);
            let result = self.changed.wait_timeout(stopped, timeout);
            stopped = match result {
                Ok((guard, _)) => guard,
                Err(poisoned) => poisoned.into_inner().0,
            };
        }
    }

    fn stop(&self) {
        let mut stopped = self
            .stopped
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        *stopped = true;
        self.changed.notify_all();
    }
}

impl Invocation {
    pub fn new(budget_ms: u64, start_ns: u64, entry_ns: u64) -> Result<Self, String> {
        if budget_ms == 0 {
            return Err("--invocation-budget-ms must be positive".to_string());
        }
        if start_ns > entry_ns {
            return Err("invocation start is later than the service monotonic clock".to_string());
        }
        let budget_ns = budget_ms
            .checked_mul(1_000_000)
            .ok_or_else(|| "invocation budget overflows nanoseconds".to_string())?;
        let service_exit_ns = start_ns
            .checked_add(budget_ns)
            .ok_or_else(|| "service exit deadline overflows".to_string())?;
        // A very small positive budget can place Dw before T0. Saturate the
        // absolute monotonic timestamp at zero; the entry-time check below
        // then expires the invocation before it starts naming work.
        let work_stop_ns = service_exit_ns.saturating_sub(WORK_STOP_MARGIN_NS);
        let inner = Arc::new(Inner {
            work_stop_ns,
            service_exit_ns,
            phase: AtomicU8::new(ACTIVE),
            groups: Mutex::new(Groups::default()),
            timers: Arc::new(TimerStop::default()),
        });

        let work_timer = inner.clone();
        let work_thread = thread::Builder::new()
            .name("window-name-work-deadline".to_string())
            .spawn(move || {
                if work_timer.timers.wait_until(work_timer.work_stop_ns) {
                    expire(&work_timer, true);
                }
            })
            .map_err(|error| format!("cannot start work deadline monitor: {error}"))?;
        drop(work_thread);

        let exit_timer = inner.clone();
        if let Err(error) = thread::Builder::new()
            .name("window-name-exit-deadline".to_string())
            .spawn(move || {
                if exit_timer.timers.wait_until(exit_timer.service_exit_ns) {
                    terminate_at_deadline(&exit_timer);
                }
            })
        {
            inner.timers.stop();
            inner.phase.store(FINISHED, Ordering::Release);
            return Err(format!("cannot start service exit monitor: {error}"));
        }

        let invocation = Self { inner };
        if entry_ns >= work_stop_ns {
            invocation.expire();
        }
        Ok(invocation)
    }

    pub fn ensure_work(&self) -> Result<(), String> {
        let now =
            monotonic_ns().map_err(|error| format!("cannot read monotonic clock: {error}"))?;
        if now >= self.inner.work_stop_ns {
            self.expire();
        }
        let phase = self.inner.phase.load(Ordering::Acquire);
        if phase != ACTIVE {
            return Err(self.failure_message());
        }
        let groups = self.lock_groups();
        if !groups.signal_errors.is_empty() {
            return Err(self.failure_message_locked(&groups));
        }
        Ok(())
    }

    pub fn remaining_work(&self) -> Result<Duration, String> {
        self.ensure_work()?;
        let now =
            monotonic_ns().map_err(|error| format!("cannot read monotonic clock: {error}"))?;
        Ok(Duration::from_nanos(
            self.inner.work_stop_ns.saturating_sub(now),
        ))
    }

    pub fn work_stop_ns(&self) -> u64 {
        self.inner.work_stop_ns
    }

    pub fn spawn_group<F>(&self, spawn: F) -> Result<std::process::Child, String>
    where
        F: FnOnce() -> io::Result<std::process::Child>,
    {
        let mut groups = self.lock_groups();
        let now =
            monotonic_ns().map_err(|error| format!("cannot read monotonic clock: {error}"))?;
        if now >= self.inner.work_stop_ns {
            expire_locked(&self.inner, &mut groups, false);
            return Err(self.failure_message_locked(&groups));
        }
        if self.inner.phase.load(Ordering::Acquire) != ACTIVE {
            return Err(self.failure_message_locked(&groups));
        }

        let child = match spawn() {
            Ok(child) => child,
            Err(error) => {
                let now = monotonic_ns()
                    .map_err(|clock_error| format!("cannot read monotonic clock: {clock_error}"))?;
                if now >= self.inner.work_stop_ns {
                    expire_locked(&self.inner, &mut groups, false);
                    return Err(self.failure_message_locked(&groups));
                }
                return Err(format!("cannot start command: {error}"));
            }
        };
        groups.process_groups.insert(child.id());

        match monotonic_ns() {
            Ok(now) if now >= self.inner.work_stop_ns => {
                expire_locked(&self.inner, &mut groups, false);
            }
            Ok(_) => {}
            Err(error) => {
                groups.signal_errors.push(format!(
                    "cannot read monotonic clock after command spawn: {error}"
                ));
                expire_locked(&self.inner, &mut groups, false);
            }
        }
        Ok(child)
    }

    /// Signal the registered command group and remove it from the ledger before reaping its leader.
    pub fn signal_group_before_reap(&self, pid: u32) -> Result<(), String> {
        let mut groups = self.lock_groups();
        let now =
            monotonic_ns().map_err(|error| format!("cannot read monotonic clock: {error}"))?;
        if now >= self.inner.work_stop_ns {
            expire_locked(&self.inner, &mut groups, false);
        }

        if groups.process_groups.contains(&pid)
            && !groups.expiry_signalled
            && self.inner.phase.load(Ordering::Acquire) == ACTIVE
        {
            if let Err(error) = signal_group(pid) {
                groups.signal_errors.push(error.clone());
                groups.process_groups.remove(&pid);
                return Err(error);
            }
        }
        groups.process_groups.remove(&pid);
        Ok(())
    }

    pub fn finish(&self) -> Result<(), String> {
        self.ensure_work()?;
        match self.inner.phase.compare_exchange(
            ACTIVE,
            FINISHED,
            Ordering::AcqRel,
            Ordering::Acquire,
        ) {
            Ok(_) => {
                self.inner.timers.stop();
                Ok(())
            }
            Err(_) => Err(self.failure_message()),
        }
    }

    fn expire(&self) {
        expire(&self.inner, false);
    }

    fn lock_groups(&self) -> std::sync::MutexGuard<'_, Groups> {
        self.inner
            .groups
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    fn failure_message(&self) -> String {
        let groups = self.lock_groups();
        self.failure_message_locked(&groups)
    }

    fn failure_message_locked(&self, groups: &Groups) -> String {
        let mut message = if self.inner.phase.load(Ordering::Acquire) == EXPIRED {
            "invocation budget expired".to_string()
        } else {
            "invocation is no longer active".to_string()
        };
        if !groups.signal_errors.is_empty() && !groups.signal_errors_reported {
            message.push_str("; process-group signal failures: ");
            message.push_str(&groups.signal_errors.join("; "));
        }
        message
    }
}

fn expire(inner: &Inner, report_signal_errors: bool) {
    let mut groups = inner
        .groups
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    expire_locked(inner, &mut groups, report_signal_errors);
}

fn expire_locked(inner: &Inner, groups: &mut Groups, report_signal_errors: bool) {
    let expired_now = inner
        .phase
        .compare_exchange(ACTIVE, EXPIRED, Ordering::AcqRel, Ordering::Acquire)
        .is_ok();
    if !expired_now && inner.phase.load(Ordering::Acquire) != EXPIRED {
        return;
    }

    if expired_now {
        groups.expiry_signalled = true;
        let process_groups: Vec<_> = groups.process_groups.iter().copied().collect();
        for pid in process_groups {
            if let Err(error) = signal_group(pid) {
                groups.signal_errors.push(error);
            }
        }
    }

    if report_signal_errors && !groups.signal_errors.is_empty() && !groups.signal_errors_reported {
        let mut stderr = io::stderr().lock();
        if writeln!(
            stderr,
            "scottland-window-name: process-group cleanup failed: {}",
            groups.signal_errors.join("; ")
        )
        .and_then(|()| stderr.flush())
        .is_ok()
        {
            groups.signal_errors_reported = true;
        }
    }
}

fn terminate_at_deadline(inner: &Inner) {
    loop {
        let phase = inner.phase.load(Ordering::Acquire);
        if phase == FINISHED || phase == TERMINATING {
            return;
        }
        if inner
            .phase
            .compare_exchange(phase, TERMINATING, Ordering::AcqRel, Ordering::Acquire)
            .is_ok()
        {
            unsafe {
                libc::kill(libc::getpid(), libc::SIGKILL);
                libc::_exit(1);
            }
        }
    }
}

fn signal_group(pid: u32) -> Result<(), String> {
    let group = i32::try_from(pid).map_err(|_| format!("invalid process group id {pid}"))?;
    loop {
        let result = unsafe { libc::kill(-group, libc::SIGKILL) };
        if result == 0 {
            return Ok(());
        }
        let error = io::Error::last_os_error();
        if error.raw_os_error() == Some(libc::ESRCH) {
            return Ok(());
        }
        if error.raw_os_error() == Some(libc::EINTR) {
            continue;
        }
        return Err(format!("cannot signal process group {pid}: {error}"));
    }
}

pub fn monotonic_ns() -> io::Result<u64> {
    let mut time = std::mem::MaybeUninit::<libc::timespec>::zeroed();
    let result = unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, time.as_mut_ptr()) };
    if result != 0 {
        return Err(io::Error::last_os_error());
    }
    let time = unsafe { time.assume_init() };
    let seconds = u64::try_from(time.tv_sec)
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "negative monotonic seconds"))?;
    let nanoseconds = u64::try_from(time.tv_nsec).map_err(|_| {
        io::Error::new(io::ErrorKind::InvalidData, "negative monotonic nanoseconds")
    })?;
    seconds
        .checked_mul(1_000_000_000)
        .and_then(|seconds| seconds.checked_add(nanoseconds))
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "monotonic time overflows"))
}
