//! A whole pick, from the still to the picked rectangle, for every command that picks.

use crate::desktop::{Desktop, window_rects};
use crate::image::Still;
use crate::overlay::{Overlay, PrivateDir, take_stills};
use crate::picker::{Mode, Picker};
use scottland::capture::Rect;
use scottland::session::Session;

/// What the user asked to pick from.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PickMode {
    Smart,
    Region,
    Windows,
    Fullscreen,
}

impl PickMode {
    pub fn parse(word: &str) -> Option<Self> {
        Some(match word {
            "smart" => Self::Smart,
            "region" => Self::Region,
            "windows" => Self::Windows,
            "fullscreen" => Self::Fullscreen,
            _ => return None,
        })
    }
}

/// A finished pick: the rectangle, and the still when one was asked for.
pub struct Picked<'a> {
    pub rect: Rect,
    pub stills: Vec<Still<'a>>,
}

/// Run a pick. `None` when the user cancelled. With `keep_stills` the still comes back with the
/// rectangle, so the result can be taken from it (CP1); without it a still is taken only to show
/// under the pick.
pub fn pick<'a>(
    session: &Session,
    desktop: &'a Desktop,
    mode: PickMode,
    keep_stills: bool,
) -> Result<Option<Picked<'a>>, String> {
    let mode = match mode {
        PickMode::Fullscreen => {
            let stills = if keep_stills {
                take_stills(session, desktop)?
            } else {
                Vec::new()
            };
            return Ok(Some(Picked {
                rect: desktop.focused.rect,
                stills,
            }));
        }
        PickMode::Smart => Mode::Smart,
        PickMode::Region => Mode::Region,
        PickMode::Windows => Mode::Windows,
    };
    // The still comes first, before the window listing or anything else that takes time.
    let stills = take_stills(session, desktop)?;
    let windows = if mode == Mode::Region {
        Ok(Vec::new())
    } else {
        window_rects(session, &desktop.focused.name)
    };
    let mode = match (&windows, mode) {
        (Err(reason), Mode::Windows) => {
            eprintln!(
                "scottland-capture: window picking is unavailable: {reason}; drag a region instead"
            );
            Mode::Smart
        }
        (Err(reason), Mode::Smart) => {
            eprintln!(
                "scottland-capture: window picking is unavailable: {reason}; dragging a region and the whole screen still work"
            );
            Mode::Smart
        }
        (_, mode) => mode,
    };
    let mut picker = Picker::new(mode, desktop.focused.rect, windows);
    let dir = PrivateDir::create()?;
    let mut overlay = Overlay::open(session, desktop, &stills, &dir)?;
    let picked = overlay.pick(&mut picker);
    overlay.close();
    Ok(picked?.map(|rect| Picked {
        rect,
        stills: if keep_stills { stills } else { Vec::new() },
    }))
}
