//! Notification helpers that use the freedesktop notification interface.

pub mod notification;
pub mod reminder;

use std::future::Future;
use std::time::{Duration, Instant};

use async_io::Timer;
use futures_lite::future;
use zbus::{Proxy, connection::Builder as ConnectionBuilder};

const NOTIFICATIONS: &str = "org.freedesktop.Notifications";
const NOTIFICATIONS_PATH: &str = "/org/freedesktop/Notifications";
const NOTIFICATIONS_INTERFACE: &str = "org.freedesktop.Notifications";
const POLL_INTERVAL: Duration = Duration::from_millis(100);

/// Parse the optional timeout used by `notification-wait`.
pub fn parse_wait_timeout(args: &[String]) -> Result<u64, String> {
    match args {
        [] => Ok(10),
        [timeout] => timeout
            .parse::<u64>()
            .map_err(|_| "timeout must be a non-negative integer number of seconds".to_string()),
        _ => Err("usage: scottland-notify wait [timeout-seconds]".to_string()),
    }
}

/// Wait for the standard notification server to answer GetServerInformation.
///
/// The probe is attempted ten times per timeout second, matching the upstream
/// helper's 100 ms polling interval. The probe and sleeper are injected so the
/// timing and retry behavior can be tested without a live desktop bus.
pub fn wait_for_server<P, S>(timeout_seconds: u64, probe: &mut P, sleeper: &mut S) -> bool
where
    P: ServerProbe,
    S: Sleeper,
{
    let mut clock = MonotonicWaitClock::default();
    wait_for_server_with_clock(timeout_seconds, probe, sleeper, &mut clock)
}

fn wait_for_server_with_clock<P, S, C>(
    timeout_seconds: u64,
    probe: &mut P,
    sleeper: &mut S,
    clock: &mut C,
) -> bool
where
    P: ServerProbe,
    S: Sleeper,
    C: WaitClock,
{
    let timeout = Duration::from_secs(timeout_seconds);
    let started = clock.elapsed();
    loop {
        let elapsed = clock.elapsed().saturating_sub(started);
        let remaining = timeout.saturating_sub(elapsed);
        if remaining.is_zero() {
            return false;
        }

        let ready = probe.ready(remaining);
        let elapsed = clock.elapsed().saturating_sub(started);
        if ready {
            return elapsed <= timeout;
        }

        let remaining = timeout.saturating_sub(elapsed);
        if remaining.is_zero() {
            return false;
        }
        sleeper.sleep(POLL_INTERVAL.min(remaining));
    }
}

pub trait ServerProbe {
    fn ready(&mut self, timeout: Duration) -> bool;
}

pub trait Sleeper {
    fn sleep(&mut self, duration: Duration);
}

trait WaitClock {
    fn elapsed(&mut self) -> Duration;
}

#[derive(Default)]
struct MonotonicWaitClock {
    started: Option<Instant>,
}

impl WaitClock for MonotonicWaitClock {
    fn elapsed(&mut self) -> Duration {
        let started = *self.started.get_or_insert_with(Instant::now);
        started.elapsed()
    }
}

pub struct SystemServerProbe {
    connection: Option<zbus::Connection>,
}

impl Default for SystemServerProbe {
    fn default() -> Self {
        Self { connection: None }
    }
}

impl ServerProbe for SystemServerProbe {
    fn ready(&mut self, timeout: Duration) -> bool {
        if timeout.is_zero() {
            return false;
        }
        let probe_started = Instant::now();
        let probe_timeout = timeout.min(POLL_INTERVAL);
        if self.connection.is_none() {
            let connection = async {
                Some(
                    ConnectionBuilder::session()
                        .ok()?
                        .method_timeout(probe_timeout)
                        .build()
                        .await
                        .ok()?,
                )
            };
            self.connection = future::block_on(with_timeout(probe_timeout, connection)).flatten();
        }
        let remaining = probe_timeout.saturating_sub(probe_started.elapsed());
        if remaining.is_zero() {
            return false;
        }
        let Some(connection) = self.connection.as_ref() else {
            return false;
        };

        let call = async {
            let proxy = Proxy::new(
                connection,
                NOTIFICATIONS,
                NOTIFICATIONS_PATH,
                NOTIFICATIONS_INTERFACE,
            )
            .await
            .ok()?;
            let reply = proxy.call_method("GetServerInformation", &()).await.ok()?;
            reply
                .body()
                .deserialize::<(String, String, String, String)>()
                .ok()
        };
        future::block_on(with_timeout(remaining, call))
            .flatten()
            .is_some()
    }
}

async fn with_timeout<F>(timeout: Duration, future: F) -> Option<F::Output>
where
    F: Future,
{
    if timeout.is_zero() {
        return None;
    }
    race_with_timeout(future, Timer::after(timeout)).await
}

async fn race_with_timeout<F, T>(future: F, timeout: T) -> Option<F::Output>
where
    F: Future,
    T: Future,
{
    future::race(async { Some(future.await) }, async {
        timeout.await;
        None
    })
    .await
}

#[derive(Default)]
pub struct SystemSleeper;

impl Sleeper for SystemSleeper {
    fn sleep(&mut self, duration: Duration) {
        std::thread::sleep(duration);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[derive(Default)]
    struct FakeProbe {
        answers: Vec<bool>,
        delays: Vec<Duration>,
        budgets: Vec<Duration>,
        attempts: usize,
        elapsed: std::rc::Rc<std::cell::Cell<Duration>>,
    }

    impl ServerProbe for FakeProbe {
        fn ready(&mut self, timeout: Duration) -> bool {
            self.budgets.push(timeout);
            let delay = self.delays.get(self.attempts).copied().unwrap_or_default();
            self.elapsed.set(self.elapsed.get() + delay);
            let answer = self.answers.get(self.attempts).copied().unwrap_or(false);
            self.attempts += 1;
            answer
        }
    }

    #[derive(Default)]
    struct FakeSleeper {
        sleeps: Vec<Duration>,
        elapsed: std::rc::Rc<std::cell::Cell<Duration>>,
    }

    impl Sleeper for FakeSleeper {
        fn sleep(&mut self, duration: Duration) {
            self.sleeps.push(duration);
            self.elapsed.set(self.elapsed.get() + duration);
        }
    }

    struct FakeClock(std::rc::Rc<std::cell::Cell<Duration>>);

    impl WaitClock for FakeClock {
        fn elapsed(&mut self) -> Duration {
            self.0.get()
        }
    }

    fn fake_wait_parts() -> (FakeProbe, FakeSleeper, FakeClock) {
        let elapsed = std::rc::Rc::new(std::cell::Cell::new(Duration::ZERO));
        (
            FakeProbe {
                elapsed: elapsed.clone(),
                ..FakeProbe::default()
            },
            FakeSleeper {
                elapsed: elapsed.clone(),
                ..FakeSleeper::default()
            },
            FakeClock(elapsed),
        )
    }

    #[test]
    fn wait_returns_when_the_standard_server_answers() {
        let (mut probe, mut sleeper, mut clock) = fake_wait_parts();
        probe.answers = vec![true];

        assert!(wait_for_server_with_clock(
            10,
            &mut probe,
            &mut sleeper,
            &mut clock
        ));
        assert_eq!(probe.attempts, 1);
        assert_eq!(probe.budgets, [Duration::from_secs(10)]);
        assert!(sleeper.sleeps.is_empty());
    }

    #[test]
    fn wait_retries_until_the_server_answers() {
        let (mut probe, mut sleeper, mut clock) = fake_wait_parts();
        probe.answers = vec![false, false, true];

        assert!(wait_for_server_with_clock(
            1,
            &mut probe,
            &mut sleeper,
            &mut clock
        ));
        assert_eq!(probe.attempts, 3);
        assert_eq!(sleeper.sleeps, vec![POLL_INTERVAL; 2]);
        assert_eq!(
            probe.budgets,
            [
                Duration::from_secs(1),
                Duration::from_millis(900),
                Duration::from_millis(800)
            ]
        );
    }

    #[test]
    fn wait_times_out_after_ten_probes_per_second() {
        let (mut probe, mut sleeper, mut clock) = fake_wait_parts();

        assert!(!wait_for_server_with_clock(
            1,
            &mut probe,
            &mut sleeper,
            &mut clock
        ));
        assert_eq!(probe.attempts, 10);
        assert_eq!(sleeper.sleeps, vec![POLL_INTERVAL; 10]);
    }

    #[test]
    fn slow_probe_consumes_the_remaining_deadline_and_caps_the_next_sleep() {
        let (mut probe, mut sleeper, mut clock) = fake_wait_parts();
        probe.delays = [Duration::from_millis(250), Duration::from_millis(650)].into();

        assert!(!wait_for_server_with_clock(
            1,
            &mut probe,
            &mut sleeper,
            &mut clock
        ));
        assert_eq!(
            probe.budgets,
            [Duration::from_secs(1), Duration::from_millis(650)]
        );
        assert_eq!(sleeper.sleeps, [POLL_INTERVAL]);
        assert_eq!(clock.elapsed(), Duration::from_secs(1));
    }

    #[test]
    fn a_pending_probe_drop_signal_is_owned_before_polling() {
        struct DropSignal(std::rc::Rc<std::cell::Cell<bool>>);

        impl Drop for DropSignal {
            fn drop(&mut self) {
                self.0.set(true);
            }
        }

        let unpolled_drop = std::rc::Rc::new(std::cell::Cell::new(false));
        let unpolled_signal = DropSignal(unpolled_drop.clone());
        let unpolled = async move {
            let _signal = unpolled_signal;
            std::future::pending::<()>().await;
        };
        drop(unpolled);
        assert!(unpolled_drop.get());

        let dropped = std::rc::Rc::new(std::cell::Cell::new(false));
        let signal = DropSignal(dropped.clone());
        let pending = async move {
            let _signal = signal;
            std::future::pending::<()>().await;
        };
        let result = future::block_on(race_with_timeout(pending, std::future::ready(())));

        assert!(result.is_none());
        assert!(dropped.get());
    }

    #[test]
    fn zero_timeout_does_not_probe_or_sleep() {
        let (mut probe, mut sleeper, mut clock) = fake_wait_parts();

        assert!(!wait_for_server_with_clock(
            0,
            &mut probe,
            &mut sleeper,
            &mut clock
        ));
        assert_eq!(probe.attempts, 0);
        assert!(sleeper.sleeps.is_empty());
    }

    #[test]
    fn wait_defaults_to_ten_seconds_and_rejects_invalid_arguments() {
        assert_eq!(parse_wait_timeout(&[]).unwrap(), 10);
        assert_eq!(parse_wait_timeout(&["3".into()]).unwrap(), 3);
        assert!(parse_wait_timeout(&["-1".into()]).is_err());
        assert!(parse_wait_timeout(&["1".into(), "2".into()]).is_err());
    }
}
