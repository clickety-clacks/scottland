//! Cutting a picked rectangle out of the still (CP4): each output's still is in its own device
//! pixels, and the result is at the largest scale of the outputs the rectangle covers.

use crate::desktop::{Output, session_command};
use crate::run::{last_line, with_input};
use scottland::capture::Rect;
use scottland::session::Session;

/// An RGB image, three bytes a pixel, rows top to bottom.
#[derive(Clone, Debug, PartialEq)]
pub struct Image {
    pub width: usize,
    pub height: usize,
    pub rgb: Vec<u8>,
}

/// One output as the still shows it.
pub struct Still<'a> {
    pub output: &'a Output,
    pub image: Image,
}

impl Image {
    /// A binary PPM (`P6`, 8 bits a channel), as `grim -t ppm` writes it.
    pub fn from_ppm(bytes: &[u8]) -> Result<Self, String> {
        let mut rest = bytes;
        let mut fields = Vec::new();
        while fields.len() < 4 {
            rest = skip_space_and_comments(rest);
            let end = rest
                .iter()
                .position(u8::is_ascii_whitespace)
                .ok_or("the still ends inside its header")?;
            fields
                .push(std::str::from_utf8(&rest[..end]).map_err(|_| "the still has a bad header")?);
            rest = &rest[end..];
        }
        if fields[0] != "P6" {
            return Err("the still is not a binary PPM".to_string());
        }
        let number = |text: &str| {
            text.parse::<usize>()
                .map_err(|_| "the still has a bad header")
        };
        let (width, height, max) = (number(fields[1])?, number(fields[2])?, number(fields[3])?);
        if max != 255 {
            return Err("the still is not 8 bits a channel".to_string());
        }
        // Exactly one whitespace byte separates the header from the pixels.
        let pixels = &rest[1..];
        let size = width * height * 3;
        if pixels.len() < size {
            return Err("the still is shorter than its header says".to_string());
        }
        Ok(Self {
            width,
            height,
            rgb: pixels[..size].to_vec(),
        })
    }

    pub fn to_ppm(&self) -> Vec<u8> {
        let mut bytes = format!("P6\n{} {}\n255\n", self.width, self.height).into_bytes();
        bytes.extend_from_slice(&self.rgb);
        bytes
    }
}

fn skip_space_and_comments(mut bytes: &[u8]) -> &[u8] {
    loop {
        match bytes.first() {
            Some(byte) if byte.is_ascii_whitespace() => bytes = &bytes[1..],
            Some(b'#') => {
                let end = bytes
                    .iter()
                    .position(|&b| b == b'\n')
                    .unwrap_or(bytes.len());
                bytes = &bytes[end..];
            }
            _ => return bytes,
        }
    }
}

/// The part of the still under `rect` (logical layout pixels) at `scale` device pixels per
/// logical pixel. Each result pixel takes the still pixel under its center; where `rect` covers
/// no output it is black. At an output's own scale and on whole logical pixels this copies the
/// still's pixels exactly.
pub fn cut(stills: &[Still], rect: Rect, scale: f64) -> Image {
    let width = (rect.width as f64 * scale).round() as usize;
    let height = (rect.height as f64 * scale).round() as usize;
    let mut rgb = vec![0; width * height * 3];
    for still in stills {
        let area = still.output.rect;
        if area.width == 0 || area.height == 0 {
            continue;
        }
        // Device pixels per logical pixel on this output, from the still itself, so rotation and
        // rounding are whatever the output really drew.
        let across = still.image.width as f64 / area.width as f64;
        let down = still.image.height as f64 / area.height as f64;
        for row in 0..height {
            let y = rect.y as f64 + (row as f64 + 0.5) / scale;
            let source_row = ((y - area.y as f64) * down).floor();
            if source_row < 0.0 || source_row >= still.image.height as f64 {
                continue;
            }
            let source_row = source_row as usize;
            for column in 0..width {
                let x = rect.x as f64 + (column as f64 + 0.5) / scale;
                let source_column = ((x - area.x as f64) * across).floor();
                if source_column < 0.0 || source_column >= still.image.width as f64 {
                    continue;
                }
                let from = (source_row * still.image.width + source_column as usize) * 3;
                let to = (row * width + column) * 3;
                rgb[to..to + 3].copy_from_slice(&still.image.rgb[from..from + 3]);
            }
        }
    }
    Image { width, height, rgb }
}

/// `image` as PNG bytes, encoded by ffmpeg (losslessly).
pub fn png(session: &Session, image: &Image) -> Result<Vec<u8>, String> {
    let output = with_input(
        session_command(session, "ffmpeg").args([
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "ppm_pipe",
            "-i",
            "-",
            "-frames:v",
            "1",
            "-c:v",
            "png",
            "-f",
            "image2pipe",
            "-",
        ]),
        &image.to_ppm(),
    )
    .map_err(|error| format!("cannot run ffmpeg to encode the PNG: {error}"))?;
    if !output.status.success() || output.stdout.is_empty() {
        return Err(format!(
            "cannot encode the PNG: {}",
            last_line(&output.stderr)
        ));
    }
    Ok(output.stdout)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn output(name: &str, x: i64, width: u64, height: u64) -> Output {
        Output {
            name: name.into(),
            rect: Rect {
                x,
                y: 0,
                width,
                height,
            },
            scale: 1.0,
            pixels: None,
        }
    }

    /// A still whose every pixel encodes its own position and a tag.
    fn numbered(width: usize, height: usize, tag: u8) -> Image {
        let mut rgb = Vec::new();
        for y in 0..height {
            for x in 0..width {
                rgb.extend_from_slice(&[x as u8, y as u8, tag]);
            }
        }
        Image { width, height, rgb }
    }

    fn pixel(image: &Image, x: usize, y: usize) -> [u8; 3] {
        let at = (y * image.width + x) * 3;
        image.rgb[at..at + 3].try_into().unwrap()
    }

    #[test]
    fn ppm_round_trips_and_skips_comments() {
        let image = numbered(3, 2, 7);
        assert_eq!(Image::from_ppm(&image.to_ppm()).unwrap(), image);
        let mut commented = b"P6\n# grim\n3 2\n255\n".to_vec();
        commented.extend_from_slice(&image.rgb);
        assert_eq!(Image::from_ppm(&commented).unwrap(), image);
        assert!(Image::from_ppm(b"P3\n1 1\n255\n0 0 0\n").is_err());
        assert!(Image::from_ppm(b"P6\n2 2\n255\n\0\0\0").is_err());
    }

    #[test]
    fn same_scale_cut_copies_pixels_exactly() {
        // A scale-2 output: 50 x 40 logical, 100 x 80 device pixels.
        let out = output("A", 0, 50, 40);
        let still = numbered(100, 80, 1);
        let stills = [Still {
            output: &out,
            image: still.clone(),
        }];
        let rect = Rect {
            x: 10,
            y: 5,
            width: 20,
            height: 10,
        };
        let cut = cut(&stills, rect, 2.0);
        assert_eq!((cut.width, cut.height), (40, 20));
        for y in 0..20 {
            for x in 0..40 {
                assert_eq!(pixel(&cut, x, y), pixel(&still, 20 + x, 10 + y));
            }
        }
    }

    #[test]
    fn a_cut_across_outputs_takes_the_largest_scale_and_fills_gaps_black() {
        // A at scale 1 (0..50), B at scale 2 (50..100, 30 high so the bottom of B's column is
        // outside every output).
        let a = output("A", 0, 50, 40);
        let b = output("B", 50, 50, 30);
        let stills = [
            Still {
                output: &a,
                image: numbered(50, 40, 1),
            },
            Still {
                output: &b,
                image: numbered(100, 60, 2),
            },
        ];
        let rect = Rect {
            x: 40,
            y: 0,
            width: 20,
            height: 40,
        };
        let cut = cut(&stills, rect, 2.0);
        assert_eq!((cut.width, cut.height), (40, 80));
        // A's pixels are doubled: device columns 0 and 1 both come from A's column 40.
        assert_eq!(pixel(&cut, 0, 0), [40, 0, 1]);
        assert_eq!(pixel(&cut, 1, 1), [40, 0, 1]);
        // B's begin at logical 50, device column 20 of the cut, B's own column 0.
        assert_eq!(pixel(&cut, 20, 0), [0, 0, 2]);
        assert_eq!(pixel(&cut, 39, 59), [19, 59, 2]);
        // Below B and right of A nothing is drawn.
        assert_eq!(pixel(&cut, 30, 70), [0, 0, 0]);
    }
}
