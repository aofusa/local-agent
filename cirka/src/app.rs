//! The streaming REPL (design §9): tokens as they arrive, tools as short blocks, the task list when it changes,
//! permission prompts that take the input, slash commands.

use std::io::{BufRead, IsTerminal, Write};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};

use crossterm::style::Stylize;
use serde_json::json;

use crate::agent::{Agent, HostBrain, StopReason};
use crate::config::{self, Config, Mode, Permission};
use crate::host::{HostClient, Msg};
use crate::platform;
use crate::session::{self, Session};
use crate::host::ToolCall;
use crate::tools::{self, ToolCtx, fs as tool_fs, todo};
use crate::ui::{Answer, ApprovalRequest, Frontend, UiEvent};
use crate::workspace::Workspace;

pub struct Terminal {
    pub interactive: bool,
    color: bool,
    at_line_start: bool,
    status_shown: bool,
    thinking: bool,
}

impl Terminal {
    pub fn new(interactive: bool) -> Terminal {
        let color = std::io::stdout().is_terminal() && std::env::var_os("NO_COLOR").is_none();
        if color {
            let _ = crossterm::ansi_support::supports_ansi();
        }
        Terminal { interactive, color, at_line_start: true, status_shown: false, thinking: false }
    }

    fn paint(&self, text: &str, style: &str) -> String {
        if !self.color {
            return text.to_string();
        }
        match style {
            "dim" => text.dark_grey().to_string(),
            "tool" => text.cyan().to_string(),
            "ok" => text.green().to_string(),
            "err" => text.red().to_string(),
            "warn" => text.yellow().to_string(),
            "bold" => text.bold().to_string(),
            "add" => text.green().to_string(),
            "del" => text.red().to_string(),
            _ => text.to_string(),
        }
    }

    fn clear_status(&mut self) {
        if self.status_shown {
            print!("\r{}\r", " ".repeat(100));
            self.status_shown = false;
        }
    }

    fn newline_if_needed(&mut self) {
        self.clear_status();
        if !self.at_line_start {
            println!();
            self.at_line_start = true;
        }
    }

    pub fn line(&mut self, text: &str) {
        self.newline_if_needed();
        println!("{text}");
    }

    pub fn styled(&mut self, text: &str, style: &str) {
        let painted = self.paint(text, style);
        self.line(&painted);
    }

    fn read_line(&mut self, prompt: &str) -> Option<String> {
        self.newline_if_needed();
        print!("{prompt}");
        let _ = std::io::stdout().flush();
        let mut line = String::new();
        match std::io::stdin().lock().read_line(&mut line) {
            Ok(0) => None,
            Ok(_) => Some(line.trim_end_matches(['\n', '\r']).to_string()),
            Err(_) => Some(String::new()),
        }
    }

    fn diff(&self, text: &str) -> String {
        text.lines()
            .map(|l| {
                if l.starts_with('+') && !l.starts_with("+++") {
                    self.paint(l, "add")
                } else if l.starts_with('-') && !l.starts_with("---") {
                    self.paint(l, "del")
                } else {
                    l.to_string()
                }
            })
            .collect::<Vec<_>>()
            .join("\n")
    }
}

impl Frontend for Terminal {
    fn event(&mut self, event: UiEvent) {
        match event {
            UiEvent::Token(t) => {
                self.clear_status();
                if self.thinking {
                    println!();
                    self.thinking = false;
                }
                print!("{t}");
                self.at_line_start = t.ends_with('\n');
                let _ = std::io::stdout().flush();
            }
            UiEvent::Thinking(t) => {
                self.clear_status();
                if !self.thinking {
                    if !self.at_line_start {
                        println!();
                    }
                    print!("{}", self.paint("（思考）", "dim"));
                    self.thinking = true;
                }
                print!("{}", self.paint(&t, "dim"));
                self.at_line_start = false;
                let _ = std::io::stdout().flush();
            }
            UiEvent::Status(s) => {
                if !self.at_line_start {
                    println!();
                    self.at_line_start = true;
                }
                let s: String = s.chars().take(90).collect();
                print!("\r{}{}", self.paint(&format!("⋯ {s}"), "dim"), " ".repeat(8));
                let _ = std::io::stdout().flush();
                self.status_shown = true;
            }
            UiEvent::ToolStart { name, summary } => {
                self.thinking = false;
                let text = self.paint(&format!("● {name}({summary})"), "tool");
                self.line(&text);
            }
            UiEvent::ToolEnd { ok, preview, .. } => {
                let mark = if ok { self.paint("⎿", "ok") } else { self.paint("⎿ ✗", "err") };
                let text = format!("  {mark} {}", self.paint(&preview, "dim"));
                self.line(&text);
            }
            UiEvent::Todos(todos) => {
                let text = self.paint("タスク:", "bold");
                self.line(&text);
                for l in todo::render(&todos).lines() {
                    let style = if l.starts_with("[x]") { "dim" } else if l.starts_with("[>]") { "warn" } else { "" };
                    let text = format!("  {}", self.paint(l, style));
                    self.line(&text);
                }
            }
            UiEvent::Info(i) => self.styled(&i, "dim"),
            UiEvent::Warn(w) => self.styled(&w, "warn"),
            UiEvent::TurnEnd => {
                self.thinking = false;
                self.newline_if_needed();
            }
        }
    }

    fn approve(&mut self, request: &ApprovalRequest) -> Answer {
        let title = format!("許可が要ります: {}", request.title);
        self.styled(&title, "warn");
        let detail = if request.tool == "bash" { request.detail.clone() } else { self.diff(&request.detail) };
        self.line(&detail);
        if !self.interactive {
            self.styled("（非対話モードなので拒否しました。--permission で許可できます）", "dim");
            return Answer::No;
        }
        loop {
            let Some(answer) = self.read_line("[y] 実行 / [n] 拒否 / [a] 以後この種類は許可 / [q] 依頼を止める > ") else {
                return Answer::Quit;
            };
            match answer.trim().to_ascii_lowercase().as_str() {
                "y" | "yes" => return Answer::Yes,
                "n" | "no" => return Answer::No,
                "a" | "always" => return Answer::Always,
                "q" | "quit" => return Answer::Quit,
                _ => {}
            }
        }
    }

    fn ask(&mut self, question: &str, options: &[String]) -> String {
        let q = format!("質問: {question}");
        self.styled(&q, "warn");
        for (n, o) in options.iter().enumerate() {
            self.line(&format!("  {}. {o}", n + 1));
        }
        if !self.interactive {
            return "（回答なし: 非対話モード）".into();
        }
        let answer = self.read_line("> ").unwrap_or_default();
        match answer.trim().parse::<usize>() {
            Ok(n) if n >= 1 && n <= options.len() => options[n - 1].clone(),
            _ => answer,
        }
    }
}

/// OS, shell, cwd, git branch and a short status (design §7.3). Never a full diff.
pub fn env_snapshot(root: &Path, config: &Config) -> String {
    let shell = platform::shell_label(platform::resolve_shell(config.shell));
    let mut out = format!("os: {}, shell: {shell}, cwd: {}", platform::os_label(), root.display());
    let git = |args: &[&str]| {
        std::process::Command::new("git")
            .args(args)
            .current_dir(root)
            .output()
            .ok()
            .filter(|o| o.status.success())
            .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
    };
    if let Some(branch) = git(&["rev-parse", "--abbrev-ref", "HEAD"]) {
        out.push_str(&format!("\ngit branch: {branch}"));
        if let Some(status) = git(&["status", "--porcelain"]) {
            let lines: Vec<&str> = status.lines().collect();
            if lines.is_empty() {
                out.push_str(" (clean)");
            } else {
                out.push_str(&format!("\ngit status ({} files):\n{}", lines.len(), lines.iter().take(12).cloned().collect::<Vec<_>>().join("\n")));
            }
        }
    }
    out
}

pub struct App {
    pub config: Config,
    pub agent: Agent<HostBrain>,
    pub ui: Terminal,
    pub busy: Arc<AtomicBool>,
    gate_ok: bool,
}

const HELP: &str = "\
/help                 このヘルプ
/status               接続先、モデル、モード、許可、タスク
/host [URL] [--save]  接続先を表示 / 変更（--save でユーザー設定に保存）
/mode fast|think|auto ホストの思考予算
/plan                 以降、変更とコマンドは提案だけ（/default で戻す）
/accept-edits         ワークスペース内の編集を自動で許可（コマンドは都度）
/default              許可を既定（編集もコマンドも確認）に戻す
/cd <path>            ワークスペースを変える（確認あり）
/undo                 直前のエージェントの編集を戻す
/compact              会話を要約して文脈を空ける
/search <q>           ホストの Web 検索（Tor 経由）
/image <指示>         ホストの画像生成（結果は cirka-outputs/）
/todos                タスク一覧
/resume               このディレクトリの直前のセッションを再開
/forget               いまのセッションのログを消して新しく始める
/quit                 終了（Ctrl-C は実行中の処理を止める。待機中に 2 回で終了）";

impl App {
    pub async fn start(config: Config, root: &Path, interactive: bool, cancel: Arc<AtomicBool>) -> Result<App, String> {
        let ws = Workspace::new(root).map_err(|e| format!("{} を開けません: {e}", root.display()))?;
        let host = HostClient::new(&config);
        let mut ui = Terminal::new(interactive);
        let health = host.health().await;
        let gate_ok = health.ok && health.gate && health.lmstudio;
        let context = if health.context > 0 { health.context } else { 4096 };
        let tools = ToolCtx::new(ws, config.clone(), Some(host.clone()), cancel.clone());
        let mut agent = Agent::new(HostBrain { host: host.clone() }, tools, config.permission, config.mode, config.max_turns, context);
        agent.env_snapshot = env_snapshot(&agent.tools.ws.root, &config);
        let header = format!("cirka {} — {}", env!("CARGO_PKG_VERSION"), agent.tools.ws.root.display());
        ui.styled(&header, "bold");
        ui.styled(&format!("ホスト {}  モード {}  許可 {}", config.host, config.mode.as_str(), config.permission.as_str()), "dim");
        if !health.ok {
            ui.styled(&format!("ホストに届きません（{}）。cirka config set host <URL> で接続先を設定してください", health.error.unwrap_or_default()), "err");
        } else if !health.gate {
            ui.styled("ホストに /coder/turn がありません（local-agent が古い）。ファイル作業は止め、/search と /image だけ使えます", "warn");
        } else if !health.lmstudio {
            ui.styled("ホストの LM Studio が応答しません。ファイル作業はできません", "warn");
        } else {
            ui.styled(&format!("モデル {}（文脈 {} トークン）", health.model, context), "dim");
        }
        ui.styled("信頼できる LAN だけで使ってください（認証なし。読んだファイルの断片とコマンド出力がホストへ送られます）", "dim");
        Ok(App { config, agent, ui, busy: Arc::new(AtomicBool::new(false)), gate_ok })
    }

    pub fn new_session(&mut self) {
        match Session::create(&platform::sessions_dir(), &self.agent.tools.ws.root, &self.config.host) {
            Ok(s) => self.agent.session = Some(s),
            Err(e) => self.ui.styled(&format!("セッションを記録できません: {e}"), "warn"),
        }
    }

    fn report(&mut self, stop: &StopReason) {
        match stop {
            StopReason::Completed => {}
            StopReason::MaxTurns => self.ui.styled(&format!("最大ターン数（{}）で止めました。続けるなら指示してください", self.agent.max_turns), "warn"),
            StopReason::Interrupted => self.ui.styled("中断しました", "warn"),
            StopReason::PermissionDenied => self.ui.styled("依頼を止めました", "warn"),
            StopReason::HostError(e) => self.ui.styled(&format!("ホストのエラーで止まりました: {e}"), "err"),
            StopReason::ContextOverflow => self.ui.styled("文脈に入りきりません。/compact してから続けてください", "err"),
        }
    }

    pub async fn request(&mut self, text: &str) -> StopReason {
        if !self.gate_ok {
            self.ui.styled("ホストのモデルが使えないため実行できません（/status で確認、/host で接続先を変更）", "err");
            return StopReason::HostError("gate unavailable".into());
        }
        self.agent.env_snapshot = env_snapshot(&self.agent.tools.ws.root, &self.config);
        self.busy.store(true, Ordering::SeqCst);
        let stop = self.agent.run(text, &mut self.ui).await;
        self.busy.store(false, Ordering::SeqCst);
        self.report(&stop);
        stop
    }

    async fn direct_tool(&mut self, name: &str, args: serde_json::Value, label: &str) {
        let call = ToolCall { id: format!("direct-{}", uuid::Uuid::new_v4().simple()), name: name.into(), arguments: args.to_string() };
        self.ui.event(UiEvent::ToolStart { name: name.into(), summary: tools::summary(&call) });
        self.busy.store(true, Ordering::SeqCst);
        self.agent.cancel.store(false, Ordering::SeqCst);
        let planned = tools::plan(&self.agent.tools, &call);
        let out = tools::execute(&mut self.agent.tools, planned, &mut self.ui).await;
        self.busy.store(false, Ordering::SeqCst);
        self.ui.line(&out.content);
        // The model sees it on the next request.
        let user = Msg::user(format!("{label}（利用者がホストの道具を直接使いました）"));
        let reply = Msg::assistant(format!("{name} の結果:\n{}", tools::clip(&out.content, 2000)), vec![]);
        if let Some(s) = &mut self.agent.session {
            s.append_msg(&user);
            s.append_msg(&reply);
        }
        self.agent.history.push(user);
        self.agent.history.push(reply);
    }

    async fn set_host(&mut self, url: &str, save: bool) {
        match config::normalize_host(url) {
            Ok(host) => {
                self.config.host = host.clone();
                let client = HostClient::new(&self.config);
                self.agent.brain = HostBrain { host: client.clone() };
                self.agent.tools.host = Some(client.clone());
                let health = client.health().await;
                self.gate_ok = health.ok && health.gate && health.lmstudio;
                if health.context > 0 {
                    self.agent.set_context_window(health.context);
                }
                let state = if self.gate_ok { "利用できます".to_string() } else {
                    format!("モデルは使えません（{}）", health.error.unwrap_or_else(|| "LM Studio 停止か /coder がない".into()))
                };
                self.ui.styled(&format!("接続先: {host} — {state}"), if self.gate_ok { "ok" } else { "warn" });
                if save {
                    match platform::user_config_path() {
                        Some(path) => match config::write_key(&path, "host", Some(&host)) {
                            Ok(_) => self.ui.styled(&format!("{} に保存しました", path.display()), "dim"),
                            Err(e) => self.ui.styled(&e.to_string(), "err"),
                        },
                        None => self.ui.styled("ユーザー設定の場所が分かりません", "err"),
                    }
                }
            }
            Err(e) => self.ui.styled(&e.to_string(), "err"),
        }
    }

    async fn status(&mut self) {
        let host = HostClient::new(&self.config);
        let h = host.health().await;
        let lines = [
            format!("ワークスペース: {}", self.agent.tools.ws.root.display()),
            format!("ホスト: {}（到達 {}、ゲート {}、LM Studio {}、使用中 {}）", self.config.host, h.ok, h.gate, h.lmstudio,
                    h.busy.unwrap_or_else(|| "なし".into())),
            format!("モデル: {}（文脈 {} トークン）", h.model, self.agent.context_window),
            format!("モード: {}  許可: {}  最大ターン: {}", self.agent.mode.as_str(), self.agent.policy.mode.as_str(), self.agent.max_turns),
            format!("会話: {} 件  編集の取り消し: {} 件", self.agent.history.len(), self.agent.tools.undo.len()),
            format!("セッション: {}", self.agent.session.as_ref().map(|s| s.path.display().to_string()).unwrap_or_else(|| "なし".into())),
        ];
        for l in lines {
            self.ui.line(&l);
        }
        if !self.agent.tools.todos.is_empty() {
            self.ui.event(UiEvent::Todos(self.agent.tools.todos.clone()));
        }
    }

    fn resume(&mut self) {
        let dir = platform::sessions_dir();
        let current = self.agent.session.as_ref().map(|s| s.path.clone());
        match session::latest_for(&dir, &self.agent.tools.ws.root, current.as_deref()) {
            Some(path) => match session::load(&path) {
                Ok(loaded) => {
                    let n = loaded.messages.len();
                    self.agent.history = loaded.messages;
                    self.agent.tools.todos = loaded.todos;
                    if let Some(old) = self.agent.session.take() {
                        let _ = old.forget(); // the empty session of this start
                    }
                    self.agent.session = Some(Session::open(&path));
                    self.ui.styled(&format!("{} を再開しました（{n} 件）", path.display()), "ok");
                }
                Err(e) => self.ui.styled(&format!("読めません: {e}"), "err"),
            },
            None => self.ui.styled("このディレクトリの前回のセッションはありません", "warn"),
        }
    }

    /// One slash command. Returns false for /quit.
    pub async fn command(&mut self, line: &str) -> bool {
        let line = line.trim();
        let (cmd, arg) = line.split_once(char::is_whitespace).map(|(c, a)| (c, a.trim())).unwrap_or((line, ""));
        match cmd {
            "/quit" | "/exit" => return false,
            "/help" => {
                let help = HELP.to_string();
                self.ui.line(&help);
            }
            "/status" => self.status().await,
            "/host" => {
                if arg.is_empty() {
                    let text = format!("接続先: {}（{}）", self.config.host, self.config.sources.join(" < "));
                    self.ui.line(&text);
                } else {
                    let save = arg.split_whitespace().any(|a| a == "--save");
                    let url = arg.split_whitespace().find(|a| *a != "--save").unwrap_or("");
                    self.set_host(url, save).await;
                }
            }
            "/mode" => match Mode::parse(arg) {
                Some(m) => {
                    self.agent.mode = m;
                    self.config.mode = m;
                    self.ui.styled(&format!("モード: {}", m.as_str()), "ok");
                }
                None => self.ui.styled("/mode fast|think|auto", "warn"),
            },
            "/plan" => self.set_permission(Permission::Plan),
            "/accept-edits" => self.set_permission(Permission::AcceptEdits),
            "/default" => self.set_permission(Permission::Default),
            "/undo" => match tool_fs::undo_last(&mut self.agent.tools) {
                Ok(m) => self.ui.styled(&m, "ok"),
                Err(e) => self.ui.styled(&e, "warn"),
            },
            "/compact" => {
                self.busy.store(true, Ordering::SeqCst);
                let r = self.agent.compact(&mut self.ui).await;
                self.busy.store(false, Ordering::SeqCst);
                match r {
                    Ok(s) if s.is_empty() => self.ui.styled("要約する会話がありません", "dim"),
                    Ok(_) => self.ui.styled("会話を要約しました", "ok"),
                    Err(e) => self.ui.styled(&e.to_string(), "err"),
                }
            }
            "/search" if !arg.is_empty() => self.direct_tool("web_search", json!({"query": arg}), &format!("/search {arg}")).await,
            "/image" if !arg.is_empty() => self.direct_tool("image_generate", json!({"prompt": arg}), &format!("/image {arg}")).await,
            "/todos" => {
                let todos = self.agent.tools.todos.clone();
                if todos.is_empty() {
                    self.ui.styled("タスクはありません", "dim");
                } else {
                    self.ui.event(UiEvent::Todos(todos));
                }
            }
            "/resume" => self.resume(),
            "/forget" => {
                if let Some(s) = self.agent.session.take() {
                    let path = s.path.clone();
                    match s.forget() {
                        Ok(()) => self.ui.styled(&format!("{} を削除しました", path.display()), "ok"),
                        Err(e) => self.ui.styled(&format!("削除できません: {e}"), "err"),
                    }
                }
                self.agent.history.clear();
                self.agent.tools.todos.clear();
                self.new_session();
            }
            "/cd" if !arg.is_empty() => self.cd(arg),
            _ => self.ui.styled("知らないコマンドです（/help）", "warn"),
        }
        true
    }

    fn set_permission(&mut self, p: Permission) {
        self.agent.policy.mode = p;
        self.config.permission = p;
        self.ui.styled(&format!("許可: {}", p.as_str()), "ok");
    }

    fn cd(&mut self, arg: &str) {
        let target = {
            let p = PathBuf::from(arg);
            if p.is_absolute() { p } else { self.agent.tools.ws.root.join(p) }
        };
        let Ok(ws) = Workspace::new(&target) else {
            self.ui.styled(&format!("ディレクトリを開けません: {}", target.display()), "err");
            return;
        };
        if self.ui.interactive {
            let req = ApprovalRequest { tool: "cd".into(), title: "ワークスペースを変えます".into(), detail: ws.root.display().to_string() };
            if !matches!(self.ui.approve(&req), Answer::Yes | Answer::Always) {
                return;
            }
        }
        self.agent.tools.ws = ws;
        self.agent.tools.read_files.clear();
        self.agent.env_snapshot = env_snapshot(&self.agent.tools.ws.root, &self.config);
        let text = format!("ワークスペース: {}", self.agent.tools.ws.root.display());
        self.ui.styled(&text, "ok");
    }

    pub async fn repl(&mut self) {
        let mut buffer = String::new();
        loop {
            let prompt = match self.agent.policy.mode {
                Permission::Plan => "cirka[plan]> ",
                Permission::AcceptEdits => "cirka[edits]> ",
                Permission::Bypass => "cirka[bypass]> ",
                Permission::Default => "cirka> ",
                Permission::Auto => "cirka[auto]> ",
            };
            let prompt = if buffer.is_empty() { prompt } else { "... " };
            let painted = self.ui.paint(prompt, "bold");
            let Some(line) = self.ui.read_line(&painted) else { break };
            if let Some(stripped) = line.strip_suffix('\\') {
                buffer.push_str(stripped);
                buffer.push('\n');
                continue;
            }
            buffer.push_str(&line);
            let text = std::mem::take(&mut buffer);
            let text = text.trim();
            if text.is_empty() {
                continue;
            }
            if text.starts_with('/') {
                if !self.command(text).await {
                    break;
                }
                continue;
            }
            self.request(text).await;
        }
    }
}

/// Ctrl-C: stops the running tool or turn; at the prompt, twice within 2 seconds ends cirka.
pub fn install_ctrl_c(cancel: Arc<AtomicBool>, busy: Arc<AtomicBool>) {
    let presses = Arc::new(AtomicU32::new(0));
    tokio::spawn(async move {
        let mut last = std::time::Instant::now();
        loop {
            if tokio::signal::ctrl_c().await.is_err() {
                return;
            }
            if busy.load(Ordering::SeqCst) {
                cancel.store(true, Ordering::SeqCst);
                eprintln!("\n（中断します…）");
                continue;
            }
            let n = if last.elapsed() < std::time::Duration::from_secs(2) { presses.fetch_add(1, Ordering::SeqCst) + 1 } else {
                presses.store(1, Ordering::SeqCst);
                1
            };
            last = std::time::Instant::now();
            if n >= 2 {
                eprintln!();
                std::process::exit(130);
            }
            eprintln!("\n（もう一度 Ctrl-C で終了、または /quit）");
        }
    });
}
