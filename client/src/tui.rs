//! The terminal UI, after Claude Code's: a welcome box with the logo, a bordered input box with the permission
//! mode under it (Shift+Tab cycles it), the conversation as blocks (`⏺` for the assistant and each tool call,
//! `⎿` for results), diffs inline, a spinner while the host thinks or a tool runs, and arrow-key menus for
//! approvals.
//!
//! Raw mode is used only while reading input (the input box, the menus). Everything else is plain line output,
//! so piped output and `cirka -p` stay readable.

use std::io::{IsTerminal, Write};
use std::time::{Duration, Instant};

use crossterm::cursor::{MoveToColumn, MoveUp};
use crossterm::event::{self, Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers};
use crossterm::style::Stylize;
use crossterm::terminal::{self, Clear, ClearType};
use crossterm::queue;
use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};

use crate::art::{self, ColorMode};
use crate::config::{Mode, Permission};
use crate::tools::{Todo, todo};
use crate::ui::{Answer, ApprovalRequest, Frontend, UiEvent};

const SPINNER: &[&str] = &["·", "✢", "✳", "✶", "✻", "✽", "✻", "✶", "✳", "✢"];
const BOX_MAX: usize = 100;
pub const SLASH: &[&str] = &[
    "/help", "/status", "/host", "/mode", "/model", "/image-model", "/models", "/plan", "/auto", "/accept-edits", "/default", "/cd", "/undo", "/compact",
    "/search", "/image", "/todos", "/resume", "/forget", "/logo", "/clear", "/quit",
];

// --- text helpers -----------------------------------------------------------------------------------------------

pub fn strip_ansi(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut chars = s.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '\x1b' {
            if chars.peek() == Some(&'[') {
                chars.next();
                for d in chars.by_ref() {
                    if d.is_ascii_alphabetic() {
                        break;
                    }
                }
            }
            continue;
        }
        out.push(c);
    }
    out
}

/// Display width in terminal cells (CJK counts 2), ignoring colour codes.
pub fn width(s: &str) -> usize {
    UnicodeWidthStr::width(strip_ansi(s).as_str())
}

/// Cut plain text to `max` cells with "…".
pub fn truncate(s: &str, max: usize) -> String {
    if UnicodeWidthStr::width(s) <= max {
        return s.to_string();
    }
    let mut out = String::new();
    let mut w = 0;
    for c in s.chars() {
        let cw = c.width().unwrap_or(0);
        if w + cw + 1 > max {
            break;
        }
        out.push(c);
        w += cw;
    }
    out.push('…');
    out
}

/// Keep the end of a path that is too long: "…/src/agent.rs".
pub fn truncate_left(s: &str, max: usize) -> String {
    if UnicodeWidthStr::width(s) <= max {
        return s.to_string();
    }
    let chars: Vec<char> = s.chars().collect();
    let mut out = Vec::new();
    let mut w = 1;
    for c in chars.iter().rev() {
        let cw = c.width().unwrap_or(0);
        if w + cw > max {
            break;
        }
        out.push(*c);
        w += cw;
    }
    out.reverse();
    format!("…{}", out.into_iter().collect::<String>())
}

/// Pad a styled string with spaces to `w` cells.
pub fn pad(s: &str, w: usize) -> String {
    let n = width(s);
    if n >= w { s.to_string() } else { format!("{s}{}", " ".repeat(w - n)) }
}

/// Wrap plain text to `w` cells (by character; CJK text has no spaces to break on).
pub fn wrap(s: &str, w: usize) -> Vec<String> {
    let w = w.max(4);
    let mut lines = Vec::new();
    for logical in s.split('\n') {
        let mut line = String::new();
        let mut lw = 0;
        for c in logical.chars() {
            let cw = c.width().unwrap_or(0);
            if lw + cw > w {
                lines.push(std::mem::take(&mut line));
                lw = 0;
            }
            line.push(c);
            lw += cw;
        }
        lines.push(line);
    }
    lines
}

#[derive(Debug, Clone, Copy)]
pub struct Theme {
    pub mode: ColorMode,
}

impl Theme {
    fn on(&self) -> bool {
        self.mode != ColorMode::Plain
    }
    pub fn red(&self, s: &str) -> String {
        match self.mode.red() {
            Some(c) => s.with(c).to_string(),
            None => s.to_string(),
        }
    }
    pub fn dim(&self, s: &str) -> String {
        if self.on() { s.dark_grey().to_string() } else { s.to_string() }
    }
    pub fn bold(&self, s: &str) -> String {
        if self.on() { s.bold().to_string() } else { s.to_string() }
    }
    pub fn green(&self, s: &str) -> String {
        if self.on() { s.green().to_string() } else { s.to_string() }
    }
    pub fn err(&self, s: &str) -> String {
        if self.on() { s.red().to_string() } else { s.to_string() }
    }
    pub fn warn(&self, s: &str) -> String {
        if self.on() { s.yellow().to_string() } else { s.to_string() }
    }
    pub fn accent(&self, s: &str) -> String {
        if self.on() { s.cyan().to_string() } else { s.to_string() }
    }
    pub fn strike(&self, s: &str) -> String {
        if self.on() { s.dark_grey().crossed_out().to_string() } else { s.to_string() }
    }
}

// --- welcome box ------------------------------------------------------------------------------------------------

pub struct WelcomeInfo {
    pub version: String,
    pub cwd: String,
    pub host: String,
    pub model: String,
    pub context: u32,
    /// None when the host's model is usable; otherwise why not.
    pub problem: Option<String>,
}

fn boxed(theme: &Theme, title: &str, rows: &[String], inner: usize) -> Vec<String> {
    let title = if title.is_empty() { String::new() } else { format!(" {title} ") };
    let top_fill = (inner + 2).saturating_sub(width(&title) + 3);
    let mut out = vec![format!("{}{}{}", theme.red("╭───"), theme.bold(&title), theme.red(&format!("{}╮", "─".repeat(top_fill))))];
    for r in rows {
        out.push(format!("{} {} {}", theme.red("│"), pad(r, inner), theme.red("│")));
    }
    out.push(theme.red(&format!("╰{}╯", "─".repeat(inner + 2))));
    out
}

/// The welcome box: the mark on the left, the wordmark and the session facts on the right.
pub fn welcome(info: &WelcomeInfo, theme: &Theme, term_w: usize, term_h: usize) -> Vec<String> {
    let box_w = term_w.clamp(24, BOX_MAX);
    let inner = box_w - 4;
    let mut facts = vec![
        theme.bold("ローカルの 27B と、このディレクトリで作業します"),
        String::new(),
        theme.dim(&truncate_left(&format!("cwd   {}", info.cwd), inner)),
        theme.dim(&truncate(&format!("host  {}", info.host), inner)),
    ];
    match &info.problem {
        None => facts.push(theme.dim(&truncate(&format!("model {}（文脈 {} トークン）", info.model, info.context), inner))),
        Some(p) => facts.push(theme.warn(&truncate(&format!("⚠ {p}"), inner))),
    }
    facts.push(String::new());
    facts.push(theme.dim("/help でヘルプ · /status で接続の状態 · shift+tab で許可モード"));
    if box_w < 40 {
        let mut rows = vec![format!("{} {}", theme.red("●"), theme.bold("cirka"))];
        rows.extend(facts.into_iter().map(|f| truncate(&strip_ansi(&f), inner)));
        return boxed(theme, &info.version, &rows, inner);
    }
    let small_word = inner < 64;
    let word = art::wordmark(small_word);
    let with_icon = inner >= art::width(art::icon(false)) + 3 + art::width(word);
    let large_icon = with_icon && term_h >= 40 && inner >= art::width(art::icon(true)) + 3 + art::width(word) + 10;
    let mut right = art::render(word, theme.mode);
    right.push(String::new());
    right.extend(facts);
    let mut rows = Vec::new();
    if with_icon {
        let icon = art::render(art::icon(large_icon), theme.mode);
        let iw = art::width(art::icon(large_icon));
        let rw = inner - iw - 3;
        let height = icon.len().max(right.len());
        // The facts sit at the bottom when the icon is taller than the text column.
        let offset = height - right.len();
        for i in 0..height {
            let l = icon.get(i).cloned().unwrap_or_else(|| " ".repeat(iw));
            let r = if i >= offset { right[i - offset].clone() } else { String::new() };
            let r = if width(&r) > rw { truncate(&strip_ansi(&r), rw) } else { r };
            rows.push(format!("{}   {}", pad(&l, iw), r));
        }
    } else {
        rows = right.into_iter().map(|r| if width(&r) > inner { truncate(&strip_ansi(&r), inner) } else { r }).collect();
    }
    boxed(theme, &format!("cirka {}", info.version), &rows, inner)
}

/// `cirka logo`: the large mark and the wordmark side by side.
pub fn logo(theme: &Theme) -> Vec<String> {
    let icon = art::render(art::icon(true), theme.mode);
    let word = art::render(art::wordmark(false), theme.mode);
    let iw = art::width(art::icon(true));
    let offset = (icon.len().saturating_sub(word.len())) / 2;
    (0..icon.len())
        .map(|i| {
            let w = if i >= offset { word.get(i - offset).cloned().unwrap_or_default() } else { String::new() };
            format!("{}   {}", pad(&icon[i], iw), w)
        })
        .collect()
}

// --- permission / mode line ------------------------------------------------------------------------------------

pub fn mode_line(theme: &Theme, permission: Permission, mode: Mode, hint: Option<&str>) -> String {
    let left = match permission {
        Permission::Auto => theme.green("⏵⏵ auto: 確認なしで実行（危険な操作だけ確認）"),
        Permission::Default => theme.accent("◇ 確認: 編集とコマンドの前に聞く"),
        Permission::AcceptEdits => theme.accent("⏵ 編集は自動、コマンドは確認"),
        Permission::Plan => theme.warn("⏸ plan: 変更とコマンドは提案だけ"),
        Permission::Bypass => theme.err("⚠ bypass: すべて確認なし"),
    };
    let right = hint.map(|h| theme.warn(h)).unwrap_or_else(|| {
        theme.dim(&format!("(shift+tab で切替) · 思考 {} · \\ + enter で改行", mode.as_str()))
    });
    format!("  {left}  {right}")
}

// --- spinner ----------------------------------------------------------------------------------------------------

struct Spinner {
    started: Instant,
    label: String,
    frame: usize,
}

// --- the terminal front end -------------------------------------------------------------------------------------

pub enum Input {
    Line(String),
    /// Shift+Tab: the caller changes the permission mode and asks again (the draft is kept).
    CycleMode,
    Eof,
}

pub struct Terminal {
    pub interactive: bool,
    pub theme: Theme,
    tty: bool,
    at_line_start: bool,
    in_text: bool,
    thinking: bool,
    spinner: Option<Spinner>,
    // the input box
    history: Vec<String>,
    draft: Vec<char>,
    cursor: usize,
    drawn_rows: usize,
    cursor_row: usize,
    last_ctrl_c: Option<Instant>,
    /// Events read ahead while checking for a paste (handed out before reading the terminal again).
    pending: std::collections::VecDeque<Event>,
    /// The last file change was already drawn as a diff (its "updated" line is not repeated).
    diff_shown: bool,
}

fn is_press(ev: &Event) -> bool {
    !matches!(ev, Event::Key(KeyEvent { kind: KeyEventKind::Release, .. }))
}

fn term_size() -> (usize, usize) {
    terminal::size().map(|(w, h)| (w as usize, h as usize)).unwrap_or((100, 40))
}

impl Terminal {
    pub fn new(interactive: bool) -> Terminal {
        let tty = std::io::stdout().is_terminal();
        let mode = ColorMode::detect(tty);
        if mode != ColorMode::Plain {
            let _ = crossterm::ansi_support::supports_ansi();
        }
        Terminal {
            interactive: interactive && std::io::stdin().is_terminal() && tty,
            theme: Theme { mode },
            tty,
            at_line_start: true,
            in_text: false,
            thinking: false,
            spinner: None,
            history: Vec::new(),
            draft: Vec::new(),
            cursor: 0,
            drawn_rows: 0,
            cursor_row: 0,
            last_ctrl_c: None,
            pending: std::collections::VecDeque::new(),
            diff_shown: false,
        }
    }

    fn next_event(&mut self) -> std::io::Result<Event> {
        match self.pending.pop_front() {
            Some(ev) => Ok(ev),
            None => event::read(),
        }
    }

    /// Another key press is already waiting (the Enter was part of a paste, not typed). Windows reports key
    /// releases as events too; they do not count.
    fn more_input_waiting(&mut self) -> bool {
        if self.pending.iter().any(is_press) {
            return true;
        }
        while event::poll(Duration::from_millis(0)).unwrap_or(false) {
            match event::read() {
                Ok(ev) if is_press(&ev) => {
                    self.pending.push_back(ev);
                    return true;
                }
                Ok(_) => {}
                Err(_) => return false,
            }
        }
        false
    }

    fn out(&self) -> std::io::Stdout {
        std::io::stdout()
    }

    fn clear_spinner(&mut self) {
        if self.spinner.is_some() && self.tty {
            let mut o = self.out();
            let _ = queue!(o, MoveToColumn(0), Clear(ClearType::CurrentLine));
            let _ = o.flush();
        }
    }

    fn draw_spinner(&mut self) {
        if !self.tty {
            return;
        }
        let theme = self.theme;
        let Some(s) = self.spinner.as_mut() else { return };
        s.frame = (s.frame + 1) % SPINNER.len();
        let secs = s.started.elapsed().as_secs();
        let (w, _) = term_size();
        let text = truncate(&format!("{}…", s.label), w.saturating_sub(30).max(10));
        let line = format!("{} {} {}", theme.red(SPINNER[s.frame]), theme.red(&text),
                           theme.dim(&format!("({secs}s · ctrl+c で中断)")));
        let mut o = self.out();
        let _ = queue!(o, MoveToColumn(0), Clear(ClearType::CurrentLine));
        let _ = write!(o, "{line}");
        let _ = o.flush();
    }

    fn start_spinner(&mut self, label: &str) {
        self.end_text();
        self.spinner = Some(Spinner { started: Instant::now(), label: label.to_string(), frame: 0 });
        self.draw_spinner();
    }

    fn stop_spinner(&mut self) {
        self.clear_spinner();
        self.spinner = None;
    }

    fn end_text(&mut self) {
        if !self.at_line_start {
            println!();
            self.at_line_start = true;
        }
        self.in_text = false;
        self.thinking = false;
    }

    /// One finished line of output (the spinner, if any, is redrawn under it).
    pub fn line(&mut self, text: &str) {
        let spinning = self.spinner.is_some();
        self.clear_spinner();
        self.end_text();
        println!("{text}");
        if spinning {
            self.draw_spinner();
        }
    }

    pub fn styled(&mut self, text: &str, style: &str) {
        let t = self.theme;
        let painted = match style {
            "dim" => t.dim(text),
            "ok" => t.green(text),
            "err" => t.err(text),
            "warn" => t.warn(text),
            "bold" => t.bold(text),
            _ => text.to_string(),
        };
        self.line(&painted);
    }

    /// A reply to a slash command, in the `⎿` style.
    pub fn note(&mut self, text: &str) {
        let t = self.theme;
        let mut first = true;
        for l in text.lines() {
            let prefix = if first { "  ⎿  " } else { "     " };
            first = false;
            self.line(&format!("{}{}", t.dim(prefix), l));
        }
    }

    pub fn blank(&mut self) {
        self.end_text();
        println!();
    }

    // --- the input box --------------------------------------------------------------------------------------

    fn erase_drawn(&mut self, o: &mut std::io::Stdout) {
        if self.drawn_rows > 0 {
            if self.cursor_row > 0 {
                let _ = queue!(o, MoveUp(self.cursor_row as u16));
            }
            let _ = queue!(o, MoveToColumn(0), Clear(ClearType::FromCursorDown));
        }
        self.drawn_rows = 0;
        self.cursor_row = 0;
    }

    /// The visual rows of the draft and where the cursor is (row, column inside the box).
    fn layout(&self, inner: usize) -> (Vec<String>, usize, usize) {
        let text_w = inner.saturating_sub(2).max(4);
        let mut rows: Vec<String> = Vec::new();
        let mut cur = (0usize, 0usize);
        let mut line = String::new();
        let mut lw = 0usize;
        let mut first_logical = true;
        let push_row = |rows: &mut Vec<String>, line: &mut String, first: bool| {
            let prefix = if rows.is_empty() && first { "> " } else { "  " };
            rows.push(format!("{prefix}{line}"));
            line.clear();
        };
        for (i, c) in self.draft.iter().enumerate() {
            if i == self.cursor {
                cur = (rows.len(), 2 + lw);
            }
            if *c == '\n' {
                push_row(&mut rows, &mut line, first_logical);
                first_logical = false;
                lw = 0;
                continue;
            }
            let cw = c.width().unwrap_or(0);
            if lw + cw > text_w {
                push_row(&mut rows, &mut line, first_logical);
                first_logical = false;
                lw = 0;
                if i == self.cursor {
                    cur = (rows.len(), 2);
                }
            }
            line.push(*c);
            lw += cw;
        }
        if self.cursor >= self.draft.len() {
            if lw >= text_w {
                push_row(&mut rows, &mut line, first_logical);
                first_logical = false;
                lw = 0;
            }
            cur = (rows.len(), 2 + lw);
        }
        push_row(&mut rows, &mut line, first_logical);
        (rows, cur.0, cur.1)
    }

    fn draw_input(&mut self, status: &str, placeholder: &str) {
        let mut o = self.out();
        self.erase_drawn(&mut o);
        let (w, _) = term_size();
        let box_w = w.clamp(20, BOX_MAX * 2).saturating_sub(1).max(20);
        let inner = box_w - 4;
        let (rows, crow, ccol) = self.layout(inner);
        let t = self.theme;
        let border = |s: &str| t.dim(s);
        let mut lines = vec![border(&format!("╭{}╮", "─".repeat(inner + 2)))];
        for (i, r) in rows.iter().enumerate() {
            let body = if self.draft.is_empty() && i == 0 {
                format!("> {}", t.dim(placeholder))
            } else {
                r.clone()
            };
            lines.push(format!("{} {} {}", border("│"), pad(&body, inner), border("│")));
        }
        lines.push(border(&format!("╰{}╯", "─".repeat(inner + 2))));
        lines.push(status.to_string());
        for (i, l) in lines.iter().enumerate() {
            let _ = write!(o, "{l}");
            if i + 1 < lines.len() {
                let _ = write!(o, "\r\n");
            }
        }
        // The cursor goes back into the box: from the status line (last row) up to the draft's row.
        let target_row = 1 + crow;
        let up = lines.len() - 1 - target_row;
        if up > 0 {
            let _ = queue!(o, MoveUp(up as u16));
        }
        let _ = queue!(o, MoveToColumn((2 + ccol) as u16));
        let _ = o.flush();
        self.drawn_rows = lines.len();
        self.cursor_row = target_row;
    }

    fn submit_echo(&mut self, text: &str) {
        let mut o = self.out();
        self.erase_drawn(&mut o);
        let t = self.theme;
        let _ = o.flush();
        let (w, _) = term_size();
        for (i, l) in wrap(text, w.saturating_sub(4)).iter().enumerate() {
            let prefix = if i == 0 { "> " } else { "  " };
            let _ = write!(o, "{}{}\r\n", t.dim(prefix), t.dim(l));
        }
        let _ = write!(o, "\r\n");
        let _ = o.flush();
        self.at_line_start = true;
    }

    /// Read one request with the input box (raw mode). `status` is the line under the box.
    pub fn read_input(&mut self, status: &str, placeholder: &str) -> Input {
        if !self.interactive {
            return self.read_plain();
        }
        self.end_text();
        let _ = terminal::enable_raw_mode();
        let mut history_pos = self.history.len();
        let mut hint: Option<String> = None;
        let result = loop {
            let line = match &hint {
                Some(h) => format!("  {}", self.theme.warn(h)),
                None => status.to_string(),
            };
            self.draw_input(&line, placeholder);
            let ev = match self.next_event() {
                Ok(ev) => ev,
                Err(_) => break Input::Eof,
            };
            hint = None;
            match ev {
                Event::Paste(s) => {
                    for c in s.replace("\r\n", "\n").replace('\r', "\n").chars() {
                        self.insert(c);
                    }
                }
                Event::Resize(_, _) => {}
                Event::Key(KeyEvent { code, modifiers, kind, .. }) => {
                    if kind == KeyEventKind::Release {
                        continue;
                    }
                    let ctrl = modifiers.contains(KeyModifiers::CONTROL);
                    match code {
                        KeyCode::Char('c') if ctrl => {
                            if !self.draft.is_empty() {
                                self.draft.clear();
                                self.cursor = 0;
                            } else if self.last_ctrl_c.is_some_and(|t| t.elapsed() < Duration::from_secs(2)) {
                                break Input::Eof;
                            } else {
                                self.last_ctrl_c = Some(Instant::now());
                                hint = Some("もう一度 ctrl+c で終了".into());
                            }
                        }
                        KeyCode::Char('d') if ctrl && self.draft.is_empty() => break Input::Eof,
                        KeyCode::Char('a') if ctrl => self.cursor = self.line_start(),
                        KeyCode::Char('e') if ctrl => self.cursor = self.line_end(),
                        KeyCode::Char('u') if ctrl => {
                            self.draft.clear();
                            self.cursor = 0;
                        }
                        KeyCode::Char('j') if ctrl => self.insert('\n'),
                        KeyCode::Char(c) if !ctrl => self.insert(c),
                        KeyCode::BackTab => break Input::CycleMode,
                        KeyCode::Tab => self.complete(),
                        KeyCode::Enter => {
                            let pasting = self.more_input_waiting();
                            if modifiers.intersects(KeyModifiers::SHIFT | KeyModifiers::ALT) || pasting {
                                self.insert('\n');
                            } else if self.cursor > 0 && self.draft[self.cursor - 1] == '\\' {
                                self.draft[self.cursor - 1] = '\n';
                            } else {
                                let text: String = self.draft.iter().collect();
                                if text.trim().is_empty() {
                                    continue;
                                }
                                self.draft.clear();
                                self.cursor = 0;
                                break Input::Line(text);
                            }
                        }
                        KeyCode::Backspace
                            if self.cursor > 0 => {
                                self.cursor -= 1;
                                self.draft.remove(self.cursor);
                            }
                        KeyCode::Delete
                            if self.cursor < self.draft.len() => {
                                self.draft.remove(self.cursor);
                            }
                        KeyCode::Left => self.cursor = self.cursor.saturating_sub(1),
                        KeyCode::Right => self.cursor = (self.cursor + 1).min(self.draft.len()),
                        KeyCode::Home => self.cursor = self.line_start(),
                        KeyCode::End => self.cursor = self.line_end(),
                        KeyCode::Up if !self.draft.contains(&'\n')
                            && history_pos > 0 => {
                                history_pos -= 1;
                                self.draft = self.history[history_pos].chars().collect();
                                self.cursor = self.draft.len();
                            }
                        KeyCode::Down if !self.draft.contains(&'\n')
                            && history_pos < self.history.len() => {
                                history_pos += 1;
                                self.draft = self.history.get(history_pos).map(|h| h.chars().collect()).unwrap_or_default();
                                self.cursor = self.draft.len();
                            }
                        KeyCode::Esc => {
                            self.draft.clear();
                            self.cursor = 0;
                        }
                        _ => {}
                    }
                }
                _ => {}
            }
        };
        let _ = terminal::disable_raw_mode();
        match &result {
            Input::Line(text) => {
                self.submit_echo(text);
                if self.history.last() != Some(text) {
                    self.history.push(text.clone());
                }
            }
            Input::CycleMode => {}
            Input::Eof => {
                let mut o = self.out();
                self.erase_drawn(&mut o);
                let _ = o.flush();
            }
        }
        result
    }

    fn read_plain(&mut self) -> Input {
        use std::io::BufRead;
        print!("> ");
        let _ = std::io::stdout().flush();
        let mut line = String::new();
        match std::io::stdin().lock().read_line(&mut line) {
            Ok(0) | Err(_) => Input::Eof,
            Ok(_) => Input::Line(line.trim_end_matches(['\n', '\r']).to_string()),
        }
    }

    fn insert(&mut self, c: char) {
        self.draft.insert(self.cursor, c);
        self.cursor += 1;
    }

    fn line_start(&self) -> usize {
        self.draft[..self.cursor].iter().rposition(|c| *c == '\n').map(|i| i + 1).unwrap_or(0)
    }

    fn line_end(&self) -> usize {
        self.draft[self.cursor..].iter().position(|c| *c == '\n').map(|i| self.cursor + i).unwrap_or(self.draft.len())
    }

    fn complete(&mut self) {
        let text: String = self.draft.iter().collect();
        if !text.starts_with('/') || text.contains(char::is_whitespace) {
            return;
        }
        let matches: Vec<&&str> = SLASH.iter().filter(|c| c.starts_with(text.as_str())).collect();
        if matches.len() == 1 {
            self.draft = format!("{} ", matches[0]).chars().collect();
            self.cursor = self.draft.len();
        }
    }

    /// An arrow-key menu in a box. Returns the chosen index, or None for Esc.
    pub fn select(&mut self, title: &str, body: &[String], options: &[String]) -> Option<usize> {
        if !self.interactive {
            return None;
        }
        self.stop_spinner();
        self.end_text();
        let _ = terminal::enable_raw_mode();
        let mut chosen = 0usize;
        let (w, _) = term_size();
        let inner = w.clamp(30, BOX_MAX).saturating_sub(5);
        let result = loop {
            let t = self.theme;
            let mut rows = vec![t.bold(&truncate(title, inner))];
            for b in body {
                for l in wrap(&strip_ansi(b), inner) {
                    rows.push(l);
                }
            }
            rows.push(String::new());
            for (i, o) in options.iter().enumerate() {
                let label = truncate(&format!("{}. {o}", i + 1), inner - 2);
                rows.push(if i == chosen { format!("{} {}", t.accent("❯"), t.accent(&label)) } else { format!("  {label}") });
            }
            let lines = boxed(&t, "", &rows, inner);
            let mut o = self.out();
            self.erase_drawn(&mut o);
            for (i, l) in lines.iter().enumerate() {
                let _ = write!(o, "{l}");
                if i + 1 < lines.len() {
                    let _ = write!(o, "\r\n");
                }
            }
            let _ = o.flush();
            self.drawn_rows = lines.len();
            self.cursor_row = lines.len() - 1;
            match self.next_event() {
                Ok(Event::Key(KeyEvent { code, kind, modifiers, .. })) if kind != KeyEventKind::Release => match code {
                    KeyCode::Up | KeyCode::Char('k') => chosen = chosen.saturating_sub(1),
                    KeyCode::Down | KeyCode::Char('j') | KeyCode::Tab => chosen = (chosen + 1).min(options.len() - 1),
                    KeyCode::Enter => break Some(chosen),
                    KeyCode::Esc => break None,
                    KeyCode::Char('c') if modifiers.contains(KeyModifiers::CONTROL) => break None,
                    KeyCode::Char(d) if d.is_ascii_digit() => {
                        let n = d.to_digit(10).unwrap_or(0) as usize;
                        if n >= 1 && n <= options.len() {
                            break Some(n - 1);
                        }
                    }
                    _ => {}
                },
                Ok(_) => {}
                Err(_) => break None,
            }
        };
        let _ = terminal::disable_raw_mode();
        let mut o = self.out();
        self.erase_drawn(&mut o);
        let _ = o.flush();
        result
    }

    // --- blocks ------------------------------------------------------------------------------------------------

    fn tool_label(name: &str) -> &'static str {
        match name {
            "list_dir" => "List",
            "glob" => "Glob",
            "grep" => "Search",
            "read_file" => "Read",
            "edit_file" => "Update",
            "write_file" => "Write",
            "bash" => "Bash",
            "todo_write" => "Update Todos",
            "ask_user" => "Ask",
            "web_search" => "Web Search",
            "image_generate" => "Image",
            _ => "Tool",
        }
    }

    fn result_summary(name: &str, ok: bool, content: &str) -> Vec<String> {
        let first = content.lines().next().unwrap_or("").to_string();
        if !ok {
            return content.lines().take(4).map(String::from).collect();
        }
        match name {
            "read_file" => {
                let n = content.lines().filter(|l| l.contains('\t')).count();
                vec![if n > 0 { format!("{n} 行を読みました") } else { first }]
            }
            "grep" | "glob" | "list_dir" => {
                let n = content.lines().filter(|l| !l.starts_with('…')).count();
                if content.starts_with("一致") || content.starts_with("（") { vec![first] } else { vec![format!("{n} 件")] }
            }
            "bash" => content.lines().filter(|l| !l.starts_with("---")).take(5).map(String::from).collect(),
            "web_search" => vec![format!("{} 字の回答（出典付き）", content.chars().count())],
            "todo_write" | "edit_file" | "write_file" => vec![first],
            _ => content.lines().take(3).map(String::from).collect(),
        }
    }

    fn render_diff(&mut self, diff: &str) {
        self.render_diff_titled(diff, "");
    }

    fn render_diff_titled(&mut self, diff: &str, rel: &str) {
        let t = self.theme;
        let mut shown = 0;
        let (mut old_no, mut new_no) = (0usize, 0usize);
        let (mut adds, mut dels) = (0, 0);
        let mut out = Vec::new();
        for l in diff.lines() {
            if l.starts_with("---") || l.starts_with("+++") {
                continue;
            }
            if let Some(h) = l.strip_prefix("@@") {
                // @@ -a,b +c,d @@
                let nums: Vec<usize> = h
                    .split(|c: char| !c.is_ascii_digit())
                    .filter(|s| !s.is_empty())
                    .filter_map(|s| s.parse().ok())
                    .collect();
                old_no = *nums.first().unwrap_or(&1);
                new_no = *nums.get(2).unwrap_or(&1);
                if shown > 0 {
                    out.push(t.dim("       ⋮"));
                }
                continue;
            }
            let (mark, text) = l.split_at(l.chars().next().map(|c| c.len_utf8()).unwrap_or(0));
            let line = match mark {
                "+" => {
                    adds += 1;
                    new_no += 1;
                    t.green(&format!("{:>6} + {text}", new_no - 1))
                }
                "-" => {
                    dels += 1;
                    old_no += 1;
                    t.err(&format!("{:>6} - {text}", old_no - 1))
                }
                _ => {
                    old_no += 1;
                    new_no += 1;
                    t.dim(&format!("{:>6}   {text}", new_no - 1))
                }
            };
            if shown < 18 {
                out.push(line);
            }
            shown += 1;
        }
        let head = if rel.is_empty() { format!("+{adds} -{dels}") } else { format!("{rel} を更新しました（+{adds} -{dels}）") };
        self.line(&format!("{}{}", t.dim("  ⎿  "), t.dim(&head)));
        for l in out {
            self.line(&format!("     {l}"));
        }
        if shown > 18 {
            self.line(&t.dim(&format!("       …ほか {} 行", shown - 18)));
        }
    }

    fn render_todos(&mut self, todos: &[Todo]) {
        let t = self.theme;
        self.line(&format!("{} {}", t.green("⏺"), t.bold("Update Todos")));
        for (i, item) in todos.iter().enumerate() {
            let prefix = if i == 0 { "  ⎿  " } else { "     " };
            let text = match item.status.as_str() {
                "done" => format!("☒ {}", t.strike(&item.content)),
                "in_progress" => t.bold(&format!("◼ {}", item.content)),
                "blocked" => t.warn(&format!("⚠ {}", item.content)),
                _ => format!("☐ {}", item.content),
            };
            self.line(&format!("{}{}", t.dim(prefix), text));
        }
        let _ = todo::STATUSES;
    }
}

impl Frontend for Terminal {
    fn event(&mut self, event: UiEvent) {
        let t = self.theme;
        match event {
            UiEvent::TurnStart => {
                self.start_spinner("考えています");
            }
            UiEvent::Tick => {
                if self.spinner.is_some() {
                    self.draw_spinner();
                }
            }
            UiEvent::Token(text) => {
                self.stop_spinner();
                if !self.in_text || self.thinking {
                    if !self.at_line_start {
                        println!();
                    }
                    print!("{} ", t.bold("⏺"));
                    self.in_text = true;
                    self.thinking = false;
                }
                print!("{}", text.replace('\n', "\n  "));
                self.at_line_start = false;
                let _ = std::io::stdout().flush();
            }
            UiEvent::Thinking(text) => {
                self.stop_spinner();
                if !self.thinking {
                    self.end_text();
                    print!("{} ", t.dim("✻ 思考:"));
                    self.thinking = true;
                    self.in_text = true;
                }
                print!("{}", t.dim(&text.replace('\n', "\n  ")));
                self.at_line_start = false;
                let _ = std::io::stdout().flush();
            }
            UiEvent::Status(s) => {
                let first = s.lines().find(|l| !l.trim().is_empty()).unwrap_or("").to_string();
                if self.tty {
                    match self.spinner.as_mut() {
                        Some(sp) => sp.label = first,
                        None => {
                            self.start_spinner(&first);
                            return;
                        }
                    }
                    self.draw_spinner();
                } else if !first.is_empty() {
                    self.line(&t.dim(&format!("  … {first}")));
                }
            }
            UiEvent::ToolStart { name, summary } => {
                self.stop_spinner();
                let label = Self::tool_label(&name);
                let line = if summary.is_empty() || name == "todo_write" {
                    format!("{} {}", t.green("⏺"), t.bold(label))
                } else {
                    format!("{} {}({})", t.green("⏺"), t.bold(label), summary)
                };
                if name != "todo_write" {
                    self.line(&line);
                    self.start_spinner(if name == "web_search" { "ホストで検索しています" } else if name == "image_generate" {
                        "ホストで画像を生成しています"
                    } else {
                        "実行しています"
                    });
                }
            }
            UiEvent::ToolEnd { name, ok, content } => {
                self.stop_spinner();
                if name == "todo_write" && ok {
                    return; // the list itself was shown (Todos)
                }
                if matches!(name.as_str(), "edit_file" | "write_file") && ok && std::mem::take(&mut self.diff_shown) {
                    return; // the diff header said it
                }
                let lines = Self::result_summary(&name, ok, &content);
                for (i, l) in lines.iter().enumerate() {
                    let prefix = if i == 0 { "  ⎿  " } else { "     " };
                    let l = truncate(l, term_size().0.saturating_sub(8));
                    let body = if ok { t.dim(&l) } else { t.err(&l) };
                    self.line(&format!("{}{}", t.dim(prefix), body));
                }
            }
            UiEvent::Diff { rel, diff } => {
                self.stop_spinner();
                self.render_diff_titled(&diff, &rel);
                self.diff_shown = true;
            }
            UiEvent::Todos(todos) => {
                self.stop_spinner();
                self.render_todos(&todos);
            }
            UiEvent::Info(i) => {
                let line = format!("{}{}", t.dim("  ⎿  "), t.dim(&i));
                self.line(&line);
            }
            UiEvent::Warn(w) => {
                let line = t.warn(&format!("  ⚠ {w}"));
                self.line(&line);
            }
            UiEvent::TurnEnd => {
                self.stop_spinner();
                self.end_text();
            }
        }
    }

    fn approve(&mut self, request: &ApprovalRequest) -> Answer {
        self.stop_spinner();
        let t = self.theme;
        if request.tool != "bash" && request.tool != "cd" {
            self.line(&format!("{} {}", t.warn("⏺"), t.bold(&request.title)));
            self.render_diff(&request.detail);
        }
        if !self.interactive {
            if request.tool == "bash" {
                self.line(&format!("{} {}", t.warn("⏺"), t.bold(&request.title)));
                self.line(&format!("     {}", request.detail));
            }
            self.styled("  ⎿  非対話モードなので実行しませんでした（--permission で許可できます）", "dim");
            return Answer::No;
        }
        let (head, why) = match request.title.split_once(" — ") {
            Some((h, w)) => (h.to_string(), Some(w.to_string())),
            None => (request.title.clone(), None),
        };
        let (title, body) = if request.tool == "bash" {
            let mut body: Vec<String> = request.detail.lines().map(|l| format!("  {l}")).collect();
            if let Some(w) = &why {
                body.push(String::new());
                body.push(format!("⚠ {w}"));
            }
            (format!("Bash {head}"), body)
        } else if request.tool == "cd" {
            (request.title.clone(), vec![format!("  {}", request.detail)])
        } else {
            (request.title.clone(), vec![])
        };
        let options = vec![
            "はい".to_string(),
            format!("はい、このセッションでは以後 {} を確認しない", request.tool),
            "いいえ（理由をモデルに伝えて別の方法を考えさせる）".to_string(),
            "依頼を止める (esc)".to_string(),
        ];
        let mut body = body;
        body.push(String::new());
        body.push("実行しますか？".into());
        match self.select(&title, &body, &options) {
            Some(0) => Answer::Yes,
            Some(1) => Answer::Always,
            Some(2) => Answer::No,
            _ => Answer::Quit,
        }
    }

    fn ask(&mut self, question: &str, options: &[String]) -> String {
        self.stop_spinner();
        if !self.interactive {
            self.line(&format!("  ? {question}"));
            return "（回答なし: 非対話モード）".into();
        }
        if !options.is_empty() {
            let mut opts = options.to_vec();
            opts.push("自分で書く".into());
            match self.select(question, &[], &opts) {
                Some(i) if i < options.len() => return options[i].clone(),
                None => return "（回答なし）".into(),
                _ => {}
            }
        }
        self.line(&format!("{} {}", self.theme.warn("?"), question));
        loop {
            match self.read_input("  質問への回答（enter で送る）", "回答を書いてください") {
                Input::Line(l) => return l,
                Input::CycleMode => continue,
                Input::Eof => return "（回答なし）".into(),
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn widths_and_wrapping() {
        assert_eq!(width("日本語abc"), 9);
        assert_eq!(width("\x1b[31mred\x1b[0m"), 3);
        assert_eq!(truncate("日本語のテキスト", 7), "日本語…");
        assert_eq!(truncate_left("/very/long/path/agent.rs", 10), "…/agent.rs");
        assert_eq!(wrap("あいうえお", 4), vec!["あい", "うえ", "お"]);
        assert_eq!(pad("ab", 4), "ab  ");
    }

    fn info(problem: Option<&str>) -> WelcomeInfo {
        WelcomeInfo { version: "0.2.0".into(), cwd: "C:/work/project".into(), host: "http://192.168.1.2:2024".into(),
                      model: "qwen".into(), context: 4096, problem: problem.map(String::from) }
    }

    #[test]
    fn welcome_box_fits_the_terminal_and_shows_the_logo() {
        let theme = Theme { mode: ColorMode::TrueColor };
        for w in [30usize, 50, 72, 80, 120, 200] {
            let lines = welcome(&info(None), &theme, w, 40);
            let box_w = w.clamp(24, BOX_MAX);
            for l in &lines {
                assert_eq!(width(l), box_w, "w={w}: {}", strip_ansi(l));
            }
            let text: String = lines.iter().map(|l| strip_ansi(l)).collect::<Vec<_>>().join("\n");
            assert!(text.contains("cirka") && text.contains("/help"));
            if w >= 72 {
                assert!(text.contains('▀') || text.contains('▄') || text.contains('█'), "w={w}");
            }
        }
        let warn = welcome(&info(Some("ホストに届きません")), &theme, 100, 40);
        assert!(warn.iter().any(|l| strip_ansi(l).contains("ホストに届きません")));
    }

    #[test]
    fn mode_line_names_the_permission() {
        let theme = Theme { mode: ColorMode::Plain };
        assert!(mode_line(&theme, Permission::Auto, Mode::Auto, None).contains("auto"));
        assert!(mode_line(&theme, Permission::Plan, Mode::Fast, None).contains("plan"));
        assert!(mode_line(&theme, Permission::Default, Mode::Fast, Some("もう一度")).contains("もう一度"));
    }

    #[test]
    fn input_layout_places_the_cursor() {
        let mut t = Terminal::new(false);
        t.draft = "abc\nde".chars().collect();
        t.cursor = 5; // after "d"
        let (rows, r, c) = t.layout(20);
        assert_eq!(rows, vec!["> abc", "  de"]);
        assert_eq!((r, c), (1, 3));
        t.draft = "あいうえおかきくけこ".chars().collect();
        t.cursor = t.draft.len();
        let (rows, r, c) = t.layout(12); // 10 cells (5 CJK characters) per row
        assert_eq!(rows.len(), 3); // two full rows; the cursor starts the third
        assert_eq!((r, c), (2, 2));
    }

    #[test]
    fn summaries() {
        assert_eq!(Terminal::result_summary("read_file", true, "    1\ta\n    2\tb"), vec!["2 行を読みました"]);
        assert_eq!(Terminal::result_summary("grep", true, "a.rs:1: x\nb.rs:2: y"), vec!["2 件"]);
        assert_eq!(Terminal::result_summary("bash", false, "エラー: x"), vec!["エラー: x"]);
    }

    #[test]
    fn logo_is_drawn() {
        let lines = logo(&Theme { mode: ColorMode::Plain });
        assert!(lines.len() >= art::height(art::icon(true)));
    }
}
