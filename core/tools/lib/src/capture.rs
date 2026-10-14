//! Pure CP1 window-rectangle selection.
//!
//! The caller supplies only eligible window rectangles. Focused-output fallback,
//! hidden-window filtering, compositor access, and picker UI stay outside this module.

/// A rectangle in global logical layout coordinates.
#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub struct Rect {
    pub x: i64,
    pub y: i64,
    pub width: u64,
    pub height: u64,
}

impl Rect {
    /// CP1 geometry text: `x,y widthxheight`.
    fn geometry_text(self) -> String {
        format!("{},{} {}x{}", self.x, self.y, self.width, self.height)
    }

    fn area(self) -> u128 {
        u128::from(self.width) * u128::from(self.height)
    }

    /// CP1 uses half-open bounds: left/top included, right/bottom excluded.
    pub fn contains(self, point: Point) -> bool {
        if self.width == 0 || self.height == 0 {
            return false;
        }

        let x = i128::from(point.x);
        let y = i128::from(point.y);
        let left = i128::from(self.x);
        let top = i128::from(self.y);
        let right = left + i128::from(self.width);
        let bottom = top + i128::from(self.height);

        x >= left && x < right && y >= top && y < bottom
    }
}

/// A click in global logical layout coordinates.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Point {
    pub x: i64,
    pub y: i64,
}

/// Order window rectangles by CP1 geometry-text byte order and collapse exact duplicates.
fn ordered_unique_window_rectangles(rectangles: &[Rect]) -> Vec<Rect> {
    let mut ordered = rectangles.to_vec();
    ordered.sort_by(|a, b| {
        a.geometry_text()
            .as_bytes()
            .cmp(b.geometry_text().as_bytes())
    });
    ordered.dedup();
    ordered
}

/// Pick the smallest window rectangle containing `point`.
///
/// Equal-area candidates retain the first rectangle in CP1 geometry-text byte order.
/// Duplicate rectangles are a single candidate. This function intentionally does not
/// select the focused output or apply window/output eligibility policy.
pub fn select_window_rectangle(point: Point, rectangles: &[Rect]) -> Option<Rect> {
    let mut selected: Option<(Rect, u128)> = None;

    for rectangle in ordered_unique_window_rectangles(rectangles) {
        if !rectangle.contains(point) {
            continue;
        }

        let area = rectangle.area();
        if selected.is_none_or(|(_, selected_area)| area < selected_area) {
            selected = Some((rectangle, area));
        }
    }

    selected.map(|(rectangle, _)| rectangle)
}

#[cfg(test)]
mod tests {
    use super::{Point, Rect, ordered_unique_window_rectangles, select_window_rectangle};

    #[test]
    fn smallest_containing_rectangle_wins() {
        let large = Rect {
            x: 0,
            y: 0,
            width: 1200,
            height: 800,
        };
        let small = Rect {
            x: 200,
            y: 100,
            width: 600,
            height: 400,
        };

        assert_eq!(
            select_window_rectangle(Point { x: 300, y: 200 }, &[large, small]),
            Some(small)
        );
    }

    #[test]
    fn equal_area_tie_uses_geometry_text_byte_order() {
        let left = Rect {
            x: 200,
            y: 0,
            width: 900,
            height: 500,
        };
        let right = Rect {
            x: 1000,
            y: 0,
            width: 900,
            height: 500,
        };

        // The input order is deliberately opposite to CP1's byte order.
        assert!(right.geometry_text().as_bytes() < left.geometry_text().as_bytes());
        assert_eq!(
            select_window_rectangle(Point { x: 1050, y: 100 }, &[left, right]),
            Some(right)
        );
    }

    #[test]
    fn identical_rectangles_are_one_candidate() {
        let rectangle = Rect {
            x: 10,
            y: 20,
            width: 30,
            height: 40,
        };
        let candidates = ordered_unique_window_rectangles(&[rectangle, rectangle]);

        assert_eq!(candidates, vec![rectangle]);
        assert_eq!(
            select_window_rectangle(Point { x: 15, y: 25 }, &[rectangle, rectangle]),
            Some(rectangle)
        );
    }

    #[test]
    fn left_and_top_edges_are_included_but_right_and_bottom_are_excluded() {
        let rectangle = Rect {
            x: 10,
            y: 20,
            width: 30,
            height: 40,
        };

        assert!(rectangle.contains(Point { x: 10, y: 20 }));
        assert!(rectangle.contains(Point { x: 39, y: 59 }));
        assert!(!rectangle.contains(Point { x: 40, y: 35 }));
        assert!(!rectangle.contains(Point { x: 20, y: 60 }));
    }
}
