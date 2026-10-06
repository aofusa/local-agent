//! The REPL: the welcome box, the input box loop, slash commands, and one agent request per message.
//! Presentation lives in `tui.rs`; this file decides what happens.

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};

use serde_json::json;

use crate::agent::{Agent, HostBrain, StopReason};
use crate::config::{self, Config, Mode, Permission};
use crate::host::{HostClient, Msg, ToolCall};
use crate::models::{self, Kind};
use crate::platform;
use crate::policy;
use crate::session::{self, Session};
use crate::tools::{self, ToolCtx, fs as tool_fs};
use crate::tui::{self, Input, Terminal, WelcomeInfo};
use crate::ui::{Answer, ApprovalRequest, Frontend, UiEvent};
use crate::workspace::Workspace;

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
    model: String,
    problem: Option<String>,
    /// The last `GET /models` answer (labels for the status line; None until fetched).
    host_models: Option<serde_json::Value>,
}

const HELP: &str = "\
/help                 このヘルプ
/status               接続先、モデル、モード、許可、タスク
/host [URL] [--save]  接続先を表示 / 変更（--save でユーザー設定に保存）
/mode fast|think|auto ホストの思考予算
/model [id] [--save]  推論モデルを表示 / 切り替え（ホストの一覧の id。--save で設定に保存）
/image-model [id]     画像モデルを表示 / 切り替え（/image と画像生成ツールが使う。--save も可）
/models               ホストのモデル一覧（使えない理由つき）
/auto                 確認なしで実行（既定。危険な操作だけ確認）
/default              編集とコマンドの前に確認する
/accept-edits         編集は自動、コマンドは確認
/plan                 変更とコマンドは提案だけ
/cd <path>            ワークスペースを変える（確認あり）
/undo                 直前のエージェントの編集を戻す
/compact              会話を要約して文脈を空ける
/search <q>           ホストの Web 検索（Tor 経由）
/image <指示>         ホストの画像生成（結果は cirka-outputs/）
/todos                タスク一覧
/resume               このディレクトリの直前のセッションを再開
/forget               いまのセッションのログを消して新しく始める
/logo                 ロゴを表示
/clear                画面を消してウェルカム画面を出し直す
/quit                 終了
キー: enter で送信、shift+enter・alt+enter・行末の \\ で改行、shift+tab で許可モード切替、
      ↑↓ で履歴、tab でコマンド補完、ctrl+c は実行中なら中断・入力中は 2 回で終了";

impl App {
    pub async fn start(config: Config, root: &Path, interactive: bool, cancel: Arc<AtomicBool>) -> Result<App, String> {
        let ws = Workspace::new(root).map_err(|e| format!("{} を開けません: {e}", root.display()))?;
        let host = HostClient::new(&config);
        let ui = Terminal::new(interactive);
        let health = host.health().await;
        let gate_ok = health.ok && health.gate && health.llm;
        let context = if health.context > 0 { health.context } else { 4096 };
        let problem = if !health.ok {
            Some(format!("ホストに届きません（{}）。cirka config set host <URL>", health.error.unwrap_or_default()))
        } else if !health.gate {
            Some("ホストに /coder/turn がありません（local-agent が古い）。/search と /image だけ使えます".into())
        } else if !health.llm {
            Some("ホストの LLM（llama.cpp）が応答しません。ファイル作業はできません".into())
        } else {
            None
        };
        let tools = ToolCtx::new(ws, config.clone(), Some(host.clone()), cancel.clone());
        let mut agent = Agent::new(HostBrain { host: host.clone() }, tools, config.permission, config.mode, config.max_turns, context);
        agent.env_snapshot = env_snapshot(&agent.tools.ws.root, &config);
        let mut app = App { config, agent, ui, busy: Arc::new(AtomicBool::new(false)), gate_ok, model: health.model, problem,
                            host_models: None };
        if health.ok {
            app.load_models().await;
        }
        app.welcome();
        Ok(app)
    }

    pub fn welcome(&mut self) {
        let (w, h) = crossterm::terminal::size().map(|(w, h)| (w as usize, h as usize)).unwrap_or((100, 40));
        let info = WelcomeInfo {
            version: env!("CARGO_PKG_VERSION").into(),
            cwd: self.agent.tools.ws.root.display().to_string(),
            host: self.config.host.clone(),
            model: self.model.clone(),
            context: self.agent.context_window,
            problem: self.problem.clone(),
        };
        for line in tui::welcome(&info, &self.ui.theme, w, h) {
            self.ui.line(&line);
        }
        let t = self.ui.theme;
        self.ui.line(&t.dim("  信頼できる LAN だけで使ってください（認証なし。読んだファイルの断片とコマンド出力がホストへ送られます）"));
        self.ui.blank();
    }

    pub fn new_session(&mut self) {
        match Session::create(&platform::sessions_dir(), &self.agent.tools.ws.root, &self.config.host) {
            Ok(s) => self.agent.session = Some(s),
            Err(e) => self.ui.styled(&format!("  ⚠ セッションを記録できません: {e}"), "warn"),
        }
    }

    fn report(&mut self, stop: &StopReason) {
        match stop {
            StopReason::Completed => {}
            StopReason::MaxTurns => self.ui.styled(&format!("  ⚠ 最大ターン数（{}）で止めました。続けるなら指示してください", self.agent.max_turns), "warn"),
            StopReason::Interrupted => self.ui.styled("  ⎿  中断しました", "warn"),
            StopReason::PermissionDenied => self.ui.styled("  ⎿  依頼を止めました", "warn"),
            StopReason::HostError(e) => self.ui.styled(&format!("  ✗ ホストのエラーで止まりました: {e}"), "err"),
            StopReason::ContextOverflow => self.ui.styled("  ✗ 文脈に入りきりません。/compact してから続けてください", "err"),
        }
    }

    pub async fn request(&mut self, text: &str) -> StopReason {
        if !self.gate_ok {
            self.ui.styled("  ✗ ホストのモデルが使えないため実行できません（/status で確認、/host で接続先を変更）", "err");
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
        self.ui.event(UiEvent::ToolEnd { name: name.into(), ok: out.ok, content: out.content.clone() });
        let t = self.ui.theme;
        for l in out.content.lines() {
            self.ui.line(&format!("     {}", if out.ok { l.to_string() } else { t.err(l) }));
        }
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
                self.gate_ok = health.ok && health.gate && health.llm;
                if health.context > 0 {
                    self.agent.set_context_window(health.context);
                }
                self.model = health.model.clone();
                self.problem = if self.gate_ok { None } else {
                    Some(health.error.clone().unwrap_or_else(|| "ホストの LLM 停止か /coder がない".into()))
                };
                let state = if self.gate_ok { "利用できます".to_string() } else {
                    format!("モデルは使えません（{}）", self.problem.clone().unwrap_or_default())
                };
                self.ui.note(&format!("接続先: {host} — {state}"));
                if save {
                    match platform::user_config_path() {
                        Some(path) => match config::write_key(&path, "host", Some(&host)) {
                            Ok(_) => self.ui.note(&format!("{} に保存しました", path.display())),
                            Err(e) => self.ui.styled(&format!("  ✗ {e}"), "err"),
                        },
                        None => self.ui.styled("  ✗ ユーザー設定の場所が分かりません", "err"),
                    }
                }
            }
            Err(e) => self.ui.styled(&format!("  ✗ {e}"), "err"),
        }
    }

    /// Fetch `GET /models` and fit the context window to the picked inference model.
    async fn load_models(&mut self) {
        match HostClient::new(&self.config).models().await {
            Ok(list) => {
                self.host_models = Some(list);
                self.apply_inference_context();
                if let Some(id) = &self.config.inference_model {
                    self.model = id.clone();
                }
            }
            Err(_) => self.host_models = None, // an older host: ids are sent as they are
        }
    }

    fn apply_inference_context(&mut self) {
        let Some(list) = &self.host_models else { return };
        let id = self.config.inference_model.clone().unwrap_or_else(|| models::default_id(list, Kind::Inference));
        if let Some(e) = models::entries(list, Kind::Inference).into_iter().find(|e| e.id == id) {
            if e.context > 0 {
                self.agent.set_context_window(e.context);
            }
        }
    }

    fn model_label(&self, kind: Kind) -> String {
        let current = match kind {
            Kind::Inference => self.config.inference_model.clone(),
            Kind::Image => self.config.image_model.clone(),
        };
        let id = current.or_else(|| self.host_models.as_ref().map(|m| models::default_id(m, kind))).unwrap_or_default();
        if id.is_empty() { "既定".into() } else { models::label_of(self.host_models.as_ref(), kind, &id) }
    }

    /// The status line under the input box: `model:<label>  image:<label>`.
    pub fn models_line(&self) -> String {
        format!("model:{}  image:{}", self.model_label(Kind::Inference), self.model_label(Kind::Image))
    }

    /// `/model [id] [--save] [--project]` and `/image-model ...`. Without an id: the current model and the
    /// candidates. With one: an exact id the host offers (checked against `GET /models`), from the next turn on.
    async fn model_command(&mut self, kind: Kind, arg: &str) {
        let words: Vec<&str> = arg.split_whitespace().collect();
        let save = words.contains(&"--save");
        let project = words.contains(&"--project");
        let id = words.iter().find(|w| !w.starts_with("--")).copied();
        self.load_models().await;
        let Some(list) = self.host_models.clone() else {
            self.ui.styled("  ✗ ホストのモデル一覧（GET /models）を取得できません。ホストの local-agent を確認してください", "err");
            return;
        };
        let Some(id) = id else {
            let current = match kind {
                Kind::Inference => self.config.inference_model.clone(),
                Kind::Image => self.config.image_model.clone(),
            };
            self.ui.note(&models::describe(&list, kind, current.as_deref()));
            return;
        };
        let entry = match models::pick(&list, kind, id) {
            Ok(e) => e,
            Err(e) => {
                self.ui.styled(&format!("  ✗ {e}（選択は変えていません）"), "err");
                return;
            }
        };
        match kind {
            Kind::Inference => {
                self.config.inference_model = Some(entry.id.clone());
                self.model = entry.id.clone();
                self.apply_inference_context();
            }
            Kind::Image => self.config.image_model = Some(entry.id.clone()),
        }
        self.agent.tools.config = self.config.clone();
        if let Some(s) = self.agent.session.as_mut() {
            s.append_models(self.config.inference_model.as_deref(), self.config.image_model.as_deref());
        }
        self.ui.note(&format!("{}: {}（{}）。次のターンから。", kind.label(), entry.label, entry.id));
        if save {
            let path = if project { Some(config::project_config_path(&self.agent.tools.ws.root)) } else { platform::user_config_path() };
            match path {
                Some(path) => match config::write_key(&path, kind.config_key(), Some(&entry.id)) {
                    Ok(_) => self.ui.note(&format!("{} に保存しました", path.display())),
                    Err(e) => self.ui.styled(&format!("  ✗ {e}"), "err"),
                },
                None => self.ui.styled("  ✗ ユーザー設定の場所が分かりません", "err"),
            }
        }
    }

    async fn models_command(&mut self) {
        match HostClient::new(&self.config).models().await {
            Ok(list) => {
                let text = format!("{}\n\n{}", models::describe(&list, Kind::Inference, self.config.inference_model.as_deref()),
                                   models::describe(&list, Kind::Image, self.config.image_model.as_deref()));
                self.host_models = Some(list);
                self.ui.note(&text);
            }
            Err(e) => self.ui.styled(&format!("  ✗ {e}"), "err"),
        }
    }

    async fn status(&mut self) {
        let host = HostClient::new(&self.config);
        let h = host.health().await;
        let text = [
            format!("ワークスペース: {}", self.agent.tools.ws.root.display()),
            format!("ホスト: {}（到達 {}、ゲート {}、LLM {}、使用中 {}）", self.config.host, h.ok, h.gate, h.llm,
                    h.busy.unwrap_or_else(|| "なし".into())),
            format!("推論モデル: {}（文脈 {} トークン）  画像モデル: {}", self.model_label(Kind::Inference),
                    self.agent.context_window, self.model_label(Kind::Image)),
            format!("思考: {}  許可: {}  最大ターン: {}", self.agent.mode.as_str(), self.agent.policy.mode.as_str(), self.agent.max_turns),
            format!("会話: {} 件  編集の取り消し: {} 件", self.agent.history.len(), self.agent.tools.undo.len()),
            format!("セッション: {}", self.agent.session.as_ref().map(|s| s.path.display().to_string()).unwrap_or_else(|| "なし".into())),
        ]
        .join("\n");
        self.ui.note(&text);
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
                    // The session's models win over the environment and the config files (design §9).
                    if let Some((inference, image)) = loaded.models {
                        self.config.inference_model = inference;
                        self.config.image_model = image;
                        self.agent.tools.config = self.config.clone();
                        self.apply_inference_context();
                    }
                    self.ui.note(&format!("{} を再開しました（{n} 件）。{}", path.display(), self.models_line()));
                }
                Err(e) => self.ui.styled(&format!("  ✗ 読めません: {e}"), "err"),
            },
            None => self.ui.note("このディレクトリの前回のセッションはありません"),
        }
    }

    /// One slash command. Returns false for /quit.
    pub async fn command(&mut self, line: &str) -> bool {
        let line = line.trim();
        let (cmd, arg) = line.split_once(char::is_whitespace).map(|(c, a)| (c, a.trim())).unwrap_or((line, ""));
        match cmd {
            "/quit" | "/exit" => return false,
            "/help" => self.ui.note(HELP),
            "/status" => self.status().await,
            "/host" => {
                if arg.is_empty() {
                    let text = format!("接続先: {}（{}）", self.config.host, self.config.sources.join(" < "));
                    self.ui.note(&text);
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
                    self.ui.note(&format!("思考モード: {}", m.as_str()));
                }
                None => self.ui.note("/mode fast|think|auto"),
            },
            "/model" => self.model_command(Kind::Inference, arg).await,
            "/image-model" => self.model_command(Kind::Image, arg).await,
            "/models" => self.models_command().await,
            "/auto" => self.set_permission(Permission::Auto),
            "/plan" => self.set_permission(Permission::Plan),
            "/accept-edits" => self.set_permission(Permission::AcceptEdits),
            "/default" => self.set_permission(Permission::Default),
            "/undo" => match tool_fs::undo_last(&mut self.agent.tools) {
                Ok(m) => self.ui.note(&m),
                Err(e) => self.ui.note(&e),
            },
            "/compact" => {
                self.busy.store(true, Ordering::SeqCst);
                let r = self.agent.compact(&mut self.ui).await;
                self.busy.store(false, Ordering::SeqCst);
                match r {
                    Ok(s) if s.is_empty() => self.ui.note("要約する会話がありません"),
                    Ok(_) => self.ui.note("会話を要約しました"),
                    Err(e) => self.ui.styled(&format!("  ✗ {e}"), "err"),
                }
            }
            "/search" if !arg.is_empty() => self.direct_tool("web_search", json!({"query": arg}), &format!("/search {arg}")).await,
            "/image" if !arg.is_empty() => self.direct_tool("image_generate", json!({"prompt": arg}), &format!("/image {arg}")).await,
            "/todos" => {
                let todos = self.agent.tools.todos.clone();
                if todos.is_empty() {
                    self.ui.note("タスクはありません");
                } else {
                    self.ui.event(UiEvent::Todos(todos));
                }
            }
            "/resume" => self.resume(),
            "/forget" => {
                if let Some(s) = self.agent.session.take() {
                    let path = s.path.clone();
                    match s.forget() {
                        Ok(()) => self.ui.note(&format!("{} を削除しました", path.display())),
                        Err(e) => self.ui.styled(&format!("  ✗ 削除できません: {e}"), "err"),
                    }
                }
                self.agent.history.clear();
                self.agent.tools.todos.clear();
                self.new_session();
            }
            "/logo" => {
                let theme = self.ui.theme;
                for l in tui::logo(&theme) {
                    self.ui.line(&l);
                }
            }
            "/clear" => {
                let _ = crossterm::execute!(std::io::stdout(), crossterm::terminal::Clear(crossterm::terminal::ClearType::All),
                                            crossterm::cursor::MoveTo(0, 0));
                self.welcome();
            }
            "/cd" if !arg.is_empty() => self.cd(arg),
            _ => self.ui.note("知らないコマンドです（/help）"),
        }
        true
    }

    fn set_permission(&mut self, p: Permission) {
        self.agent.policy.mode = p;
        self.config.permission = p;
        let line = tui::mode_line(&self.ui.theme, p, self.agent.mode, None);
        self.ui.line(&line);
    }

    fn cd(&mut self, arg: &str) {
        let target = {
            let p = PathBuf::from(arg);
            if p.is_absolute() { p } else { self.agent.tools.ws.root.join(p) }
        };
        let Ok(ws) = Workspace::new(&target) else {
            self.ui.styled(&format!("  ✗ ディレクトリを開けません: {}", target.display()), "err");
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
        self.ui.note(&text);
    }

    pub async fn repl(&mut self) {
        loop {
            let cols = crossterm::terminal::size().map(|(w, _)| w as usize).unwrap_or(100);
            let status = tui::status_line(&self.ui.theme, self.agent.policy.mode, self.agent.mode, &self.models_line(), cols);
            match self.ui.read_input(&status, "依頼を書いてください（/help でコマンド一覧）") {
                Input::Eof => break,
                Input::CycleMode => {
                    let next = policy::next_mode(self.agent.policy.mode);
                    self.agent.policy.mode = next;
                    self.config.permission = next;
                }
                Input::Line(text) => {
                    let text = text.trim();
                    if text.starts_with('/') {
                        if !self.command(text).await {
                            break;
                        }
                    } else {
                        self.request(text).await;
                    }
                    self.ui.blank();
                }
            }
        }
    }
}

/// Ctrl-C outside the input box (while a turn or a tool runs): stops it. The input box reads Ctrl-C itself.
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
