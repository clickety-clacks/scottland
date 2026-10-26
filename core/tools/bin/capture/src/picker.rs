//! What a pick does with each pointer and key event (CP1, CP2), apart from any drawing.
//!
//! The overlay reports events in global logical coordinates and draws what this module answers:
//! a highlight, a notice, or the end of the pick. Window rectangles come only from a window
//! listing that says which output draws each window; without one, the paths that need them say
//! so and every other path still works.

use scottland::capture::{Point, Rect, select_window_rectangle};

/// A drag covering less than this many square logical pixels counts as a click in `smart`.
const SMART_CLICK_AREA: u128 = 20;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Mode {
    Smart,
    Region,
    Windows,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Key {
    Tab,
    CtrlTab,
    Left,
    Right,
    Up,
    Down,
    Return,
    CtrlReturn,
    Escape,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum Reply {
    /// Highlight this rectangle, or nothing.
    Highlight(Option<Rect>),
    /// Tell the user something and keep picking.
    Notice(String),
    /// The pick ended with this rectangle.
    Picked(Rect),
    /// The user cancelled.
    Cancelled,
    /// Nothing changes.
    Nothing,
}

pub struct Picker {
    mode: Mode,
    focused: Rect,
    /// Window rectangles on the focused output, unique, in CP1 order; `None` when the window
    /// listing cannot say which output draws each window.
    windows: Option<Vec<Rect>>,
    /// Why `windows` is `None`.
    unavailable: String,
    pointer: Option<Point>,
    /// The window rectangle keys move from.
    current: Option<Rect>,
    highlight: Option<Rect>,
}

impl Picker {
    pub fn new(mode: Mode, focused: Rect, windows: Result<Vec<Rect>, String>) -> Self {
        let (windows, unavailable) = match windows {
            Ok(windows) => (Some(cp1_order(windows)), String::new()),
            Err(reason) => (None, reason),
        };
        Self {
            mode,
            focused,
            windows,
            unavailable,
            pointer: None,
            current: None,
            highlight: None,
        }
    }

    pub fn hover(&mut self, point: Point) -> Reply {
        self.pointer = Some(point);
        if self.mode == Mode::Region {
            return Reply::Nothing;
        }
        let Some(windows) = &self.windows else {
            return Reply::Nothing;
        };
        self.current = select_window_rectangle(point, windows);
        let highlight = self.windows_pick(point);
        self.set_highlight(highlight)
    }

    /// The pointer went down at `from` and came up at `to`.
    pub fn release(&mut self, from: Point, to: Point) -> Reply {
        let dragged = drag_rect(from, to);
        match self.mode {
            Mode::Region if dragged.width == 0 || dragged.height == 0 => Reply::Nothing,
            Mode::Region => Reply::Picked(dragged),
            Mode::Windows => self.click(to),
            Mode::Smart if area(dragged) >= SMART_CLICK_AREA => Reply::Picked(dragged),
            Mode::Smart => {
                let point = Point {
                    x: dragged.x,
                    y: dragged.y,
                };
                if !self.focused.contains(point) {
                    // A click outside the focused output picks the dragged rectangle itself.
                    Reply::Picked(dragged)
                } else {
                    self.click(point)
                }
            }
        }
    }

    pub fn key(&mut self, key: Key) -> Reply {
        if key == Key::Escape {
            return Reply::Cancelled;
        }
        if self.mode == Mode::Region {
            return Reply::Nothing;
        }
        if key == Key::CtrlReturn {
            return Reply::Picked(self.focused);
        }
        if key == Key::Return {
            return match self.highlight {
                Some(rect) => Reply::Picked(rect),
                None => Reply::Nothing,
            };
        }
        let Some(windows) = &self.windows else {
            return Reply::Notice(self.unavailable_notice());
        };
        if windows.is_empty() {
            return Reply::Nothing;
        }
        let next = match key {
            Key::Tab | Key::CtrlTab => step(windows, self.current, key == Key::Tab),
            _ => {
                let from = match (self.current, self.pointer) {
                    (Some(rect), _) => center(rect),
                    (None, Some(point)) => point,
                    (None, None) => center(self.focused),
                };
                nearest(windows, self.current, from, key)
            }
        };
        match next {
            Some(rect) => {
                self.current = Some(rect);
                self.set_highlight(Some(rect))
            }
            None => Reply::Nothing,
        }
    }

    /// A click at `point` in `windows` mode, or a small drag in `smart`.
    fn click(&mut self, point: Point) -> Reply {
        if !self.focused.contains(point) {
            return Reply::Nothing;
        }
        if self.windows.is_none() {
            return Reply::Notice(self.unavailable_notice());
        }
        match self.windows_pick(point) {
            Some(rect) => Reply::Picked(rect),
            None => Reply::Nothing,
        }
    }

    /// What a `windows` click at `point` picks: among the focused output and the window
    /// rectangles containing it, the smallest, the focused output first on equal areas.
    fn windows_pick(&self, point: Point) -> Option<Rect> {
        if !self.focused.contains(point) {
            return None;
        }
        let window = self
            .windows
            .as_deref()
            .and_then(|windows| select_window_rectangle(point, windows));
        match window {
            Some(window) if area(window) < area(self.focused) => Some(window),
            _ => Some(self.focused),
        }
    }

    fn set_highlight(&mut self, highlight: Option<Rect>) -> Reply {
        if self.highlight == highlight {
            return Reply::Nothing;
        }
        self.highlight = highlight;
        Reply::Highlight(highlight)
    }

    fn unavailable_notice(&self) -> String {
        format!(
            "Window picking is unavailable: {}. Drag a region, or press Ctrl+Return for the whole screen.",
            self.unavailable
        )
    }
}

pub fn geometry(rect: Rect) -> String {
    format!("{},{} {}x{}", rect.x, rect.y, rect.width, rect.height)
}

fn area(rect: Rect) -> u128 {
    u128::from(rect.width) * u128::from(rect.height)
}

fn drag_rect(from: Point, to: Point) -> Rect {
    Rect {
        x: from.x.min(to.x),
        y: from.y.min(to.y),
        width: from.x.abs_diff(to.x),
        height: from.y.abs_diff(to.y),
    }
}

fn center(rect: Rect) -> Point {
    Point {
        x: rect.x + (rect.width / 2) as i64,
        y: rect.y + (rect.height / 2) as i64,
    }
}

/// Unique window rectangles in CP1 order: by geometry text, byte by byte.
fn cp1_order(mut windows: Vec<Rect>) -> Vec<Rect> {
    windows.sort_by_key(|rect| geometry(*rect).into_bytes());
    windows.dedup();
    windows
}

/// The next (or previous) window rectangle in reading order: top edge, then left edge.
fn step(windows: &[Rect], current: Option<Rect>, forward: bool) -> Option<Rect> {
    let mut ordered = windows.to_vec();
    ordered.sort_by(|a, b| {
        (a.y, a.x)
            .cmp(&(b.y, b.x))
            .then_with(|| geometry(*a).cmp(&geometry(*b)))
    });
    let at = current.and_then(|rect| ordered.iter().position(|other| *other == rect));
    let index = match (at, forward) {
        (None, true) => 0,
        (None, false) => ordered.len() - 1,
        (Some(index), true) => (index + 1) % ordered.len(),
        (Some(index), false) => (index + ordered.len() - 1) % ordered.len(),
    };
    ordered.get(index).copied()
}

/// The window rectangle nearest `from` in the arrow's direction, by the distance along the arrow
/// plus twice the distance across it; the current one is never its own neighbour.
fn nearest(windows: &[Rect], current: Option<Rect>, from: Point, key: Key) -> Option<Rect> {
    let mut best: Option<(Rect, i128)> = None;
    for rect in windows {
        if Some(*rect) == current {
            continue;
        }
        let to = center(*rect);
        let dx = i128::from(to.x) - i128::from(from.x);
        let dy = i128::from(to.y) - i128::from(from.y);
        let (along, across) = match key {
            Key::Left => (-dx, dy),
            Key::Right => (dx, dy),
            Key::Up => (-dy, dx),
            Key::Down => (dy, dx),
            _ => return None,
        };
        if along <= 0 {
            continue;
        }
        let score = along + 2 * across.abs();
        if best.is_none_or(|(_, best_score)| score < best_score) {
            best = Some((*rect, score));
        }
    }
    best.map(|(rect, _)| rect)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rect(x: i64, y: i64, width: u64, height: u64) -> Rect {
        Rect {
            x,
            y,
            width,
            height,
        }
    }

    fn point(x: i64, y: i64) -> Point {
        Point { x, y }
    }

    const OUTPUT: Rect = Rect {
        x: 0,
        y: 0,
        width: 1920,
        height: 1080,
    };

    fn picker(mode: Mode, windows: Vec<Rect>) -> Picker {
        Picker::new(mode, OUTPUT, Ok(windows))
    }

    fn click(picker: &mut Picker, x: i64, y: i64) -> Reply {
        picker.release(point(x, y), point(x, y))
    }

    fn side_by_side() -> Vec<Rect> {
        vec![rect(0, 0, 960, 1080), rect(960, 0, 960, 1080)]
    }

    #[test]
    fn smart_click_picks_the_window_under_it() {
        let mut picker = picker(Mode::Smart, side_by_side());
        assert_eq!(
            click(&mut picker, 100, 100),
            Reply::Picked(rect(0, 0, 960, 1080))
        );
    }

    #[test]
    fn smart_drag_picks_the_dragged_rectangle() {
        let mut picker = picker(Mode::Smart, side_by_side());
        assert_eq!(
            picker.release(point(100, 100), point(400, 300)),
            Reply::Picked(rect(100, 100, 300, 200))
        );
        let mut picker = self::picker(Mode::Smart, side_by_side());
        assert_eq!(
            picker.release(point(400, 300), point(100, 100)),
            Reply::Picked(rect(100, 100, 300, 200))
        );
    }

    #[test]
    fn small_smart_drag_is_a_click_at_its_top_left() {
        let mut picker = picker(Mode::Smart, side_by_side());
        assert_eq!(
            picker.release(point(1003, 503), point(1000, 500)),
            Reply::Picked(rect(960, 0, 960, 1080))
        );
    }

    #[test]
    fn twenty_square_pixels_is_a_drag() {
        let mut picker = picker(Mode::Smart, side_by_side());
        assert_eq!(
            picker.release(point(1000, 500), point(1005, 504)),
            Reply::Picked(rect(1000, 500, 5, 4))
        );
        let mut picker = self::picker(Mode::Smart, side_by_side());
        assert_eq!(
            picker.release(point(1000, 500), point(1019, 501)),
            Reply::Picked(rect(960, 0, 960, 1080))
        );
    }

    #[test]
    fn overlap_picks_the_smaller_window_even_behind_the_larger() {
        let windows = vec![rect(100, 100, 1200, 800), rect(200, 200, 600, 400)];
        let mut picker = picker(Mode::Windows, windows);
        assert_eq!(
            click(&mut picker, 300, 300),
            Reply::Picked(rect(200, 200, 600, 400))
        );
    }

    #[test]
    fn equal_areas_take_the_first_geometry_text() {
        let windows = vec![rect(200, 0, 900, 500), rect(1000, 0, 900, 500)];
        let mut picker = picker(Mode::Windows, windows);
        assert_eq!(
            click(&mut picker, 1050, 100),
            Reply::Picked(rect(1000, 0, 900, 500))
        );
    }

    #[test]
    fn no_window_there_picks_the_focused_output() {
        let windows = vec![rect(0, 0, 100, 100)];
        let mut picker = picker(Mode::Windows, windows);
        assert_eq!(click(&mut picker, 500, 500), Reply::Picked(OUTPUT));
    }

    #[test]
    fn a_window_as_large_as_the_output_loses_to_the_output() {
        let mut picker = picker(Mode::Windows, vec![OUTPUT]);
        assert_eq!(click(&mut picker, 5, 5), Reply::Picked(OUTPUT));
    }

    #[test]
    fn edges_are_half_open() {
        let windows = vec![rect(100, 100, 200, 200)];
        let mut picker = picker(Mode::Windows, windows.clone());
        assert_eq!(
            click(&mut picker, 100, 100),
            Reply::Picked(rect(100, 100, 200, 200))
        );
        let mut picker = self::picker(Mode::Windows, windows);
        assert_eq!(click(&mut picker, 300, 300), Reply::Picked(OUTPUT));
    }

    #[test]
    fn windows_click_outside_the_focused_output_goes_on() {
        let mut picker = picker(Mode::Windows, side_by_side());
        assert_eq!(click(&mut picker, 2000, 100), Reply::Nothing);
    }

    #[test]
    fn smart_click_outside_the_focused_output_picks_the_drag() {
        let mut picker = picker(Mode::Smart, side_by_side());
        assert_eq!(
            picker.release(point(2000, 100), point(2003, 102)),
            Reply::Picked(rect(2000, 100, 3, 2))
        );
        let mut picker = self::picker(Mode::Smart, side_by_side());
        assert_eq!(
            click(&mut picker, 2000, 100),
            Reply::Picked(rect(2000, 100, 0, 0))
        );
    }

    #[test]
    fn region_mode_ignores_empty_drags_and_window_keys() {
        let mut picker = picker(Mode::Region, side_by_side());
        assert_eq!(click(&mut picker, 10, 10), Reply::Nothing);
        assert_eq!(picker.release(point(10, 10), point(10, 50)), Reply::Nothing);
        assert_eq!(picker.key(Key::Tab), Reply::Nothing);
        assert_eq!(picker.key(Key::CtrlReturn), Reply::Nothing);
        assert_eq!(picker.hover(point(10, 10)), Reply::Nothing);
        assert_eq!(picker.key(Key::Escape), Reply::Cancelled);
    }

    #[test]
    fn hover_highlights_what_a_click_would_pick() {
        let windows = vec![rect(100, 100, 1200, 800), rect(200, 200, 600, 400)];
        let mut picker = picker(Mode::Smart, windows);
        assert_eq!(
            picker.hover(point(300, 300)),
            Reply::Highlight(Some(rect(200, 200, 600, 400)))
        );
        assert_eq!(picker.hover(point(301, 300)), Reply::Nothing);
        assert_eq!(
            picker.hover(point(1500, 1000)),
            Reply::Highlight(Some(OUTPUT))
        );
        assert_eq!(picker.hover(point(2500, 10)), Reply::Highlight(None));
    }

    #[test]
    fn tab_visits_each_rectangle_once_in_reading_order() {
        let windows = vec![
            rect(500, 0, 100, 100),
            rect(0, 500, 100, 100),
            rect(0, 0, 100, 100),
            rect(0, 0, 100, 100),
        ];
        let mut picker = picker(Mode::Windows, windows);
        let mut seen = Vec::new();
        for _ in 0..4 {
            match picker.key(Key::Tab) {
                Reply::Highlight(Some(rect)) => seen.push(rect),
                other => panic!("unexpected {other:?}"),
            }
        }
        assert_eq!(
            seen,
            [
                rect(0, 0, 100, 100),
                rect(500, 0, 100, 100),
                rect(0, 500, 100, 100),
                rect(0, 0, 100, 100),
            ]
        );
        assert_eq!(
            picker.key(Key::CtrlTab),
            Reply::Highlight(Some(rect(0, 500, 100, 100)))
        );
    }

    #[test]
    fn ctrl_tab_from_nothing_takes_the_last() {
        let mut picker = picker(Mode::Smart, side_by_side());
        assert_eq!(
            picker.key(Key::CtrlTab),
            Reply::Highlight(Some(rect(960, 0, 960, 1080)))
        );
    }

    fn three_in_a_row() -> Vec<Rect> {
        vec![
            rect(0, 300, 400, 400),
            rect(700, 300, 400, 400),
            rect(1400, 300, 400, 400),
        ]
    }

    #[test]
    fn arrows_move_to_the_nearest_rectangle_that_way() {
        let mut picker = picker(Mode::Smart, three_in_a_row());
        picker.hover(point(900, 500));
        assert_eq!(
            picker.key(Key::Right),
            Reply::Highlight(Some(rect(1400, 300, 400, 400)))
        );
        assert_eq!(
            picker.key(Key::Return),
            Reply::Picked(rect(1400, 300, 400, 400))
        );

        let mut picker = self::picker(Mode::Smart, three_in_a_row());
        picker.hover(point(900, 500));
        assert_eq!(
            picker.key(Key::Left),
            Reply::Highlight(Some(rect(0, 300, 400, 400)))
        );
        assert_eq!(
            picker.key(Key::Left),
            Reply::Nothing,
            "nothing further left"
        );
        assert_eq!(picker.key(Key::Up), Reply::Nothing);
        assert_eq!(picker.key(Key::Down), Reply::Nothing);
    }

    #[test]
    fn arrows_weigh_distance_across_twice() {
        let windows = vec![
            rect(0, 0, 100, 100),
            // 300 along, 0 across: 300.
            rect(300, 0, 100, 100),
            // 200 along, 60 across: 320.
            rect(200, 60, 100, 100),
        ];
        let mut picker = picker(Mode::Windows, windows);
        picker.key(Key::Tab);
        assert_eq!(
            picker.key(Key::Right),
            Reply::Highlight(Some(rect(300, 0, 100, 100)))
        );
    }

    #[test]
    fn arrows_from_nothing_measure_from_the_pointer() {
        let mut picker = picker(Mode::Smart, three_in_a_row());
        picker.hover(point(1000, 100));
        assert_eq!(
            picker.key(Key::Down),
            Reply::Highlight(Some(rect(700, 300, 400, 400)))
        );
    }

    #[test]
    fn return_and_ctrl_return() {
        let mut picker = picker(Mode::Windows, three_in_a_row());
        assert_eq!(picker.key(Key::Return), Reply::Nothing);
        assert_eq!(picker.key(Key::CtrlReturn), Reply::Picked(OUTPUT));
    }

    #[test]
    fn without_window_identity_window_paths_say_so_and_the_rest_works() {
        let mut picker = Picker::new(Mode::Smart, OUTPUT, Err("no output named".into()));
        assert_eq!(picker.hover(point(10, 10)), Reply::Nothing);
        let Reply::Notice(text) = click(&mut picker, 10, 10) else {
            panic!("expected a notice");
        };
        assert!(text.contains("no output named"));
        assert!(matches!(picker.key(Key::Tab), Reply::Notice(_)));
        assert!(matches!(picker.key(Key::Right), Reply::Notice(_)));
        assert_eq!(
            picker.release(point(10, 10), point(110, 60)),
            Reply::Picked(rect(10, 10, 100, 50))
        );
        assert_eq!(
            click(&mut picker, 2000, 100),
            Reply::Picked(rect(2000, 100, 0, 0))
        );
        assert_eq!(picker.key(Key::CtrlReturn), Reply::Picked(OUTPUT));
        assert_eq!(picker.key(Key::Escape), Reply::Cancelled);
    }

    #[test]
    fn geometry_text() {
        assert_eq!(geometry(rect(-1440, 0, 1440, 900)), "-1440,0 1440x900");
    }
}
