//! `qr` and `text` (CP10, CP11): read the picked area of the still.

use crate::desktop::{Desktop, session_command};
use crate::image::{cut, png};
use crate::notify::{Notice, show};
use crate::pick::{PickMode, pick};
use crate::run::{last_line, with_input};
use crate::screenshot::copy;
use scottland::session::Session;

/// zbarimg's exit status when it found no code.
const ZBAR_NOTHING_FOUND: i32 = 4;

pub fn qr(session: &Session) -> Result<(), String> {
    let image = picked_png(session)?;
    // QR codes only: with every symbology on, dense screen content reads as a product barcode.
    let output = with_input(
        session_command(session, "zbarimg").args([
            "-q",
            "--raw",
            "-Sdisable",
            "-Sqrcode.enable",
            "-",
        ]),
        &image,
    )
    .map_err(|error| format!("cannot run zbarimg to read QR codes: {error}"))?;
    // The decoded text often holds a secret, such as a 2FA setup link, so it goes to the
    // clipboard and nowhere else: not to standard output, an error message or a notice.
    let text = String::from_utf8_lossy(&output.stdout);
    let codes = text.trim_end_matches('\n');
    if output.status.code() == Some(ZBAR_NOTHING_FOUND)
        || (output.status.success() && codes.is_empty())
    {
        show(
            session,
            &Notice::new("No QR code found")
                .body("Select a region containing a QR code")
                .critical(),
        );
        return Err("no QR code found".to_string());
    }
    if !output.status.success() {
        return Err(format!(
            "cannot read QR codes: {}",
            last_line(&output.stderr)
        ));
    }
    copy(session, &["--sensitive"], codes.as_bytes())?;
    show(session, &Notice::new("QR code copied to clipboard"));
    Ok(())
}

pub fn text(session: &Session) -> Result<(), String> {
    let image = picked_png(session)?;
    let languages = std::env::var("SCOTTLAND_OCR_LANGS")
        .ok()
        .filter(|languages| !languages.is_empty())
        .unwrap_or_else(|| "eng".to_string());
    let output = with_input(
        session_command(session, "tesseract").args([
            "stdin",
            "stdout",
            "--oem",
            "1",
            "--psm",
            "6",
            "-l",
            &languages,
            "--dpi",
            "300",
            "-c",
            "preserve_interword_spaces=1",
        ]),
        &image,
    )
    .map_err(|error| format!("cannot run tesseract to read text: {error}"))?;
    if !output.status.success() {
        return Err(format!("cannot read text: {}", last_line(&output.stderr)));
    }
    let text = String::from_utf8_lossy(&output.stdout);
    let text = text.trim();
    if text.is_empty() {
        return Err("no text found in the picked area".to_string());
    }
    copy(session, &[], text.as_bytes())?;
    show(
        session,
        &Notice::new("Copied text from selection to clipboard"),
    );
    Ok(())
}

/// A `region` pick of the still, as PNG.
fn picked_png(session: &Session) -> Result<Vec<u8>, String> {
    let desktop = Desktop::read(session)?;
    let Some(picked) = pick(session, &desktop, PickMode::Region, true)? else {
        return Err("cancelled".to_string());
    };
    let image = cut(&picked.stills, picked.rect, desktop.scale_for(picked.rect));
    png(session, &image)
}
