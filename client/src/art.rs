//! The logo in the terminal: the bitmaps of `art_data.rs` (made from docs/logo by scripts/gen_cirka_art.py)
//! drawn with half blocks, two pixel rows per character row (▀ top, ▄ bottom, █ both).

use crossterm::style::{Color, Stylize};

use crate::art_data;

/// What the terminal can show.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ColorMode {
    /// 24-bit colour: the logo red as it is (#D63A2F).
    TrueColor,
    /// The 16 ANSI colours: red is the terminal's red.
    Ansi,
    /// No colour (NO_COLOR, not a terminal): the shapes only.
    Plain,
}

pub const LOGO_RED: (u8, u8, u8) = (214, 58, 47);

impl ColorMode {
    pub fn detect(is_tty: bool) -> ColorMode {
        if !is_tty || std::env::var_os("NO_COLOR").is_some() {
            return ColorMode::Plain;
        }
        let truecolor = std::env::var("COLORTERM").map(|v| v.contains("truecolor") || v.contains("24bit")).unwrap_or(false)
            || std::env::var_os("WT_SESSION").is_some() // Windows Terminal
            || std::env::var("TERM_PROGRAM").map(|v| matches!(v.as_str(), "vscode" | "iTerm.app" | "WezTerm")).unwrap_or(false);
        if truecolor { ColorMode::TrueColor } else { ColorMode::Ansi }
    }

    pub fn red(self) -> Option<Color> {
        match self {
            ColorMode::TrueColor => Some(Color::Rgb { r: LOGO_RED.0, g: LOGO_RED.1, b: LOGO_RED.2 }),
            ColorMode::Ansi => Some(Color::Red),
            ColorMode::Plain => None,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Px {
    Empty,
    Red,
    Text,
}

fn px(c: u8) -> Px {
    match c {
        b'r' => Px::Red,
        b'k' => Px::Text,
        _ => Px::Empty,
    }
}

fn paint(ch: char, px: Px, mode: ColorMode) -> String {
    match (px, mode.red()) {
        (Px::Red, Some(red)) => ch.to_string().with(red).to_string(),
        _ => ch.to_string(),
    }
}

/// Character rows of a bitmap. Every row is exactly as wide as the bitmap (in terminal cells).
pub fn render(bitmap: &[&str], mode: ColorMode) -> Vec<String> {
    let mut lines = Vec::new();
    for pair in bitmap.chunks(2) {
        let top = pair[0].as_bytes();
        let bottom = pair.get(1).map(|r| r.as_bytes()).unwrap_or(&[]);
        let mut line = String::new();
        for (x, &top_px) in top.iter().enumerate() {
            let (t, b) = (px(top_px), px(*bottom.get(x).unwrap_or(&b'.')));
            line.push_str(&match (t, b) {
                (Px::Empty, Px::Empty) => " ".to_string(),
                (t, b) if t == b => paint('█', t, mode),
                (t, Px::Empty) => paint('▀', t, mode),
                (Px::Empty, b) => paint('▄', b, mode),
                // Red over text (or the reverse): the upper half wins, the lower half is coloured as background.
                (t, _) => match (mode.red(), t) {
                    (Some(red), Px::Red) => "▀".with(red).to_string(),
                    (Some(red), _) => "▄".with(red).to_string(),
                    (None, _) => "█".to_string(),
                },
            });
        }
        lines.push(line);
    }
    lines
}

pub fn width(bitmap: &[&str]) -> usize {
    bitmap.first().map(|r| r.len()).unwrap_or(0)
}

#[cfg(test)]
pub fn height(bitmap: &[&str]) -> usize {
    bitmap.len().div_ceil(2)
}

pub fn icon(large: bool) -> &'static [&'static str] {
    if large { art_data::ICON } else { art_data::ICON_SMALL }
}

pub fn wordmark(small: bool) -> &'static [&'static str] {
    if small { art_data::WORDMARK_SMALL } else { art_data::WORDMARK }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn strip(s: &str) -> String {
        let re = regex::Regex::new(r"\x1b\[[0-9;]*m").unwrap();
        re.replace_all(s, "").into_owned()
    }

    #[test]
    fn half_blocks() {
        let lines = render(&["r.r.", "..rr"], ColorMode::Plain);
        assert_eq!(lines, vec!["▀ █▄"]);
        let coloured = render(&["r."], ColorMode::TrueColor);
        assert!(coloured[0].contains("\x1b[") && strip(&coloured[0]) == "▀ ");
    }

    #[test]
    fn generated_art_is_rectangular_and_uses_known_pixels() {
        for bm in [art_data::ICON, art_data::ICON_SMALL, art_data::WORDMARK, art_data::WORDMARK_SMALL] {
            let w = width(bm);
            assert!(w > 0 && bm.iter().all(|r| r.len() == w && r.bytes().all(|c| matches!(c, b'.' | b'r' | b'k'))));
            assert_eq!(bm.len() % 2, 0);
            for line in render(bm, ColorMode::TrueColor) {
                assert_eq!(unicode_width::UnicodeWidthStr::width(strip(&line).as_str()), w);
            }
        }
        // The icon is the red mark; the wordmark has text pixels and the red dot.
        assert!(art_data::ICON.iter().any(|r| r.contains('r')) && !art_data::ICON.iter().any(|r| r.contains('k')));
        assert!(art_data::WORDMARK.iter().any(|r| r.contains('k')) && art_data::WORDMARK.iter().any(|r| r.contains('r')));
    }
}
