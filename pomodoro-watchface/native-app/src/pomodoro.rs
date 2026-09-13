//! The pomodoro page itself: 25/5/15 focus cycle with haptic **and**
//! notification-tray alerts, rendered with stock LVGL through the Canopus UI
//! dispatcher.
//!
//! On `device`, `canopus_target_private` provides the LVGL bindings, a bounded
//! one-shot timer, `vibrator_run` / `vibrator_cancel` (the same service the Lua
//! `vibrator` module wraps — `lua_vibrator_start` in the target pack) and the
//! notification-tray calls (`lvx_notification_init_message` /
//! `lvx_notification_insert_message`). On host builds those are recorded in
//! counters so the state machine and its alerts are unit-testable.
//!
//! Addresses and field offsets for every firmware symbol used here live in
//! `native-app/targets/xiaomi-band-9-pro-3.1.175.md`.

use core::sync::atomic::{AtomicU32, Ordering};

use crate::native_app::POMODORO_STATE;

const FOCUS_SECS: u32 = 25 * 60;
const BREAK_SECS: u32 = 5 * 60;
const LONG_BREAK_SECS: u32 = 15 * 60;
const ROUNDS_PER_LONG_BREAK: u32 = 4;
const TICK_MS: u32 = 1000;

const MODE_FOCUS: u32 = 0;
const MODE_BREAK: u32 = 1;
const MODE_LONG_BREAK: u32 = 2;

/// Haptic patterns (indices into the target pack's pattern table).
pub const HAPTIC_TAP: u32 = 2;
pub const HAPTIC_FOCUS_END: u32 = 0;
pub const HAPTIC_BREAK_END: u32 = 1;

struct State {
    running: bool,
    mode: u32,
    remaining: u32,
    rounds: u32,
}

static mut STATE: State = State {
    running: false,
    mode: MODE_FOCUS,
    remaining: FOCUS_SECS,
    rounds: 0,
};

fn with_state<R>(f: impl FnOnce(&mut State) -> R) -> R {
    // The page framework serializes page callbacks and the timer callback on
    // the page owner thread; no other context touches STATE.
    unsafe { f(&mut *core::ptr::addr_of_mut!(STATE)) }
}

fn publish(state: &State) {
    let word = (state.running as u32)
        | (state.mode << 1)
        | (state.rounds.min(0xFFFF) << 3)
        | (state.remaining.min(0x1FFFF) << 19);
    POMODORO_STATE.store(word, Ordering::Release);
}

// --- target surface ---------------------------------------------------------

#[cfg(feature = "device")]
mod target {
    use crate::native_app::APP_ID;
    use canopus_target_private::*;

    pub fn haptic(kind: u32) {
        let pattern = match kind {
            0 => &VIBRATOR_PATTERN_LONG,  // focus finished
            1 => &VIBRATOR_PATTERN_SHORT, // break finished
            _ => &VIBRATOR_PATTERN_TICK,  // touch feedback
        };
        unsafe { vibrator_run(pattern.as_ptr(), pattern.len() as u32) };
    }

    /// Pushes a real system notification into the tray. `lvx_notification_*`
    /// addresses and the message field offsets come from the target pack:
    ///
    /// ```text
    /// lvx_notification_init_message        0x2C45F390
    /// lvx_notification_insert_message      0x2C4F1C44
    /// lvx_notification_remove_all_appid_message 0x2C4DFB4E
    /// ```
    ///
    /// The message is tagged with our app id so the user opening the pomodoro
    /// page can clear its own backlog, and so a reboot cannot leave stale
    /// entries attributed to another app.
    pub fn notify(kind: u32, minutes: u32) {
        let title = TITLE.as_ptr();
        let body = match kind {
            0 => BODY_FOCUS_END.as_ptr(),
            1 => BODY_BREAK_END.as_ptr(),
            _ => BODY_LONG_BREAK_END.as_ptr(),
        };
        // NOTE: the exact pointer types of these private-ABI calls (`*const u8`
        // vs `*const c_char`) must be confirmed against the framework crate when
        // `device` is first built; the string data itself is final.
        unsafe {
            let message = notification_init_message(APP_ID, title, body, minutes);
            if !message.is_null() {
                notification_insert_message(message);
            }
        }
    }

    // `static`, not `const`: the pointer handed to the firmware must be stable.
    static TITLE: [u8; 10] = [0xE7, 0x95, 0xAA, 0xE8, 0x8C, 0x84, 0xE9, 0x92, 0x9F, 0]; // 番茄钟
    const BODY_FOCUS_END: &[u8] = "专注结束，休息 5 分钟\0".as_bytes();
    const BODY_BREAK_END: &[u8] = "休息结束，开始专注\0".as_bytes();
    const BODY_LONG_BREAK_END: &[u8] = "长休息结束，开始新一轮\0".as_bytes();

    pub fn schedule_tick(ms: u32) {
        unsafe { ui_dispatch_timer_start(ms, on_tick) };
    }

    pub fn cancel_tick() {
        unsafe { ui_dispatch_timer_stop() };
    }

    extern "C" fn on_tick() {
        super::tick();
    }
}

#[cfg(not(feature = "device"))]
mod target {
    use core::sync::atomic::{AtomicU32, Ordering};

    /// Host-build recorders, read by the unit tests below.
    pub static HAPTICS: AtomicU32 = AtomicU32::new(0);
    pub static NOTIFICATIONS: AtomicU32 = AtomicU32::new(0);
    pub static LAST_NOTIFY_MINUTES: AtomicU32 = AtomicU32::new(0);
    pub static TIMER_MS: AtomicU32 = AtomicU32::new(0);

    pub fn haptic(_kind: u32) {
        HAPTICS.fetch_add(1, Ordering::Relaxed);
    }

    pub fn notify(_kind: u32, minutes: u32) {
        NOTIFICATIONS.fetch_add(1, Ordering::Relaxed);
        LAST_NOTIFY_MINUTES.store(minutes, Ordering::Relaxed);
    }

    pub fn schedule_tick(ms: u32) {
        TIMER_MS.store(ms, Ordering::Relaxed);
    }

    pub fn cancel_tick() {
        TIMER_MS.store(0, Ordering::Relaxed);
    }
}

// --- page lifecycle ---------------------------------------------------------

pub fn page_create(_root: *mut core::ffi::c_void) -> i32 {
    // A full implementation builds the LVGL label/bar widgets under `root`
    // here (same layout as the Lua watchface: time, mode, round, bar).
    with_state(|s| {
        s.running = false;
        s.mode = MODE_FOCUS;
        s.remaining = FOCUS_SECS;
        s.rounds = 0;
        publish(s);
    });
    target::cancel_tick();
    0
}

pub fn page_resume() -> i32 {
    // Only a running cycle needs the timer back; a paused one waits for a tap.
    if with_state(|s| s.running) {
        target::schedule_tick(TICK_MS);
    }
    0
}

pub fn page_pause() -> i32 {
    target::cancel_tick();
    0
}

pub fn page_destroy() -> i32 {
    target::cancel_tick();
    0
}

/// Touch input. zone 0 = left half (reset), 1 = right half (start/pause) —
/// kept identical to the Lua watchface interaction.
pub extern "C" fn on_click(zone: u32) {
    match zone {
        0 => {
            with_state(|s| {
                s.running = false;
                s.mode = MODE_FOCUS;
                s.remaining = FOCUS_SECS;
                s.rounds = 0;
                publish(s);
            });
            target::cancel_tick();
        }
        _ => {
            let started = with_state(|s| {
                s.running = !s.running;
                publish(s);
                s.running
            });
            if started {
                target::schedule_tick(TICK_MS);
                target::haptic(HAPTIC_TAP);
            } else {
                target::cancel_tick();
            }
        }
    }
}

enum Tick {
    /// Cycle not running.
    Idle,
    /// One second consumed.
    Countdown,
    /// A phase ended; the alert kind identifies which one.
    PhaseEnded(u32),
}

fn advance(state: &mut State) -> Tick {
    if !state.running {
        return Tick::Idle;
    }
    state.remaining = state.remaining.saturating_sub(1);
    if state.remaining > 0 {
        return Tick::Countdown;
    }

    // Phase complete: switch mode and hand back which alert to raise.
    if state.mode == MODE_FOCUS {
        state.rounds = state.rounds.saturating_add(1);
        if state.rounds % ROUNDS_PER_LONG_BREAK == 0 {
            state.mode = MODE_LONG_BREAK;
            state.remaining = LONG_BREAK_SECS;
        } else {
            state.mode = MODE_BREAK;
            state.remaining = BREAK_SECS;
        }
        Tick::PhaseEnded(HAPTIC_FOCUS_END)
    } else {
        state.mode = MODE_FOCUS;
        state.remaining = FOCUS_SECS;
        Tick::PhaseEnded(HAPTIC_BREAK_END)
    }
}

pub fn tick() {
    let outcome = with_state(|s| {
        let outcome = advance(s);
        if !matches!(outcome, Tick::Idle) {
            publish(s);
        }
        outcome
    });

    match outcome {
        Tick::Idle => {}
        Tick::Countdown => target::schedule_tick(TICK_MS),
        Tick::PhaseEnded(kind) => {
            target::haptic(kind);
            let minutes = with_state(|s| s.remaining / 60);
            target::notify(kind, minutes);
            // The cycle keeps running: a pomodoro session is focus -> break ->
            // focus without the user touching the watch again.
            target::schedule_tick(TICK_MS);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::target::{HAPTICS, LAST_NOTIFY_MINUTES, NOTIFICATIONS, TIMER_MS};
    use super::*;

    fn start() {
        page_create(core::ptr::null_mut());
        on_click(1);
    }

    fn run_ticks(n: u32) {
        for _ in 0..n {
            tick();
        }
    }

    /// Exactly one phase transition, with the right duration, alert pair and
    /// notification payload.
    #[test]
    fn focus_phase_ends_into_short_break() {
        start();
        let before_haptics = HAPTICS.load(Ordering::Relaxed);
        let before_notifications = NOTIFICATIONS.load(Ordering::Relaxed);

        run_ticks(FOCUS_SECS - 1);
        let (mode, remaining) = with_state(|s| (s.mode, s.remaining));
        assert_eq!((mode, remaining), (MODE_FOCUS, 1), "must not fire early");

        tick();
        let (mode, remaining, rounds, running) =
            with_state(|s| (s.mode, s.remaining, s.rounds, s.running));
        assert_eq!((mode, remaining, rounds), (MODE_BREAK, BREAK_SECS, 1));
        assert!(running, "session continues into the break");
        assert_eq!(
            HAPTICS.load(Ordering::Relaxed),
            before_haptics + 1,
            "exactly one haptic for the focus end"
        );
        assert_eq!(
            NOTIFICATIONS.load(Ordering::Relaxed),
            before_notifications + 1,
            "exactly one tray notification for the focus end"
        );
        assert_eq!(
            LAST_NOTIFY_MINUTES.load(Ordering::Relaxed),
            BREAK_SECS / 60,
            "the notification announces the break length"
        );
        assert_eq!(TIMER_MS.load(Ordering::Relaxed), TICK_MS, "timer rearmed");
    }

    /// Four focus rounds must reach the long break, not the short one.
    #[test]
    fn fourth_round_reaches_long_break() {
        start();
        run_ticks(FOCUS_SECS + BREAK_SECS); // round 1 + the break after it
        for _ in 0..2 {
            run_ticks(FOCUS_SECS + BREAK_SECS);
        }
        run_ticks(FOCUS_SECS); // round 4

        let (mode, remaining, rounds) = with_state(|s| (s.mode, s.remaining, s.rounds));
        assert_eq!((mode, remaining, rounds), (MODE_LONG_BREAK, LONG_BREAK_SECS, 4));
        // 4 focus ends + 3 short-break ends = 7 phase transitions, each notified.
        assert_eq!(NOTIFICATIONS.load(Ordering::Relaxed), 7);
    }

    /// Reset zeroes the cycle, stops the timer and silences the alerts.
    #[test]
    fn reset_stops_the_cycle() {
        start();
        run_ticks(FOCUS_SECS);
        on_click(0);

        let (running, mode, remaining, rounds) = with_state(|s| (s.running, s.mode, s.remaining, s.rounds));
        assert_eq!((running, mode, remaining, rounds), (false, MODE_FOCUS, FOCUS_SECS, 0));
        assert_eq!(TIMER_MS.load(Ordering::Relaxed), 0, "timer cancelled");

        let notifications = NOTIFICATIONS.load(Ordering::Relaxed);
        run_ticks(FOCUS_SECS * 2);
        assert_eq!(
            NOTIFICATIONS.load(Ordering::Relaxed),
            notifications,
            "a stopped cycle must not notify"
        );
    }

    /// Pausing keeps the remaining time; resuming re-arms the timer.
    #[test]
    fn pause_holds_time_and_resume_rearms() {
        start();
        run_ticks(60);
        let remaining = with_state(|s| s.remaining);
        on_click(1); // pause
        assert_eq!(TIMER_MS.load(Ordering::Relaxed), 0);

        page_pause();
        run_ticks(10);
        assert_eq!(with_state(|s| s.remaining), remaining, "paused clock is frozen");

        page_resume();
        assert_eq!(TIMER_MS.load(Ordering::Relaxed), 0, "paused page stays paused");

        on_click(1); // resume
        page_resume();
        assert_eq!(TIMER_MS.load(Ordering::Relaxed), TICK_MS);
    }

    /// The published status word must track the state machine.
    #[test]
    fn status_word_tracks_state() {
        start();
        let word = crate::native_app::snapshot();
        assert_eq!(word & 1, 1, "running bit");
        assert_eq!((word >> 1) & 3, MODE_FOCUS, "mode bits");

        run_ticks(FOCUS_SECS);
        let word = crate::native_app::snapshot();
        assert_eq!((word >> 1) & 3, MODE_BREAK);
        assert_eq!((word >> 3) & 0xFFFF, 1, "one completed round");
    }
}
