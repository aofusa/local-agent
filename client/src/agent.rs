//! The agent loop (design §4): the model decides the next tool call or answers; cirka checks the permission,
//! runs the tool here and sends the result back, until the model stops calling tools.
//!
//! The loop is UI-free: the model is behind `Brain` (the host's gate, or a script in tests) and the terminal is
//! behind `Frontend`.

#![allow(async_fn_in_trait)]

use std::collections::HashMap;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};

use crate::config::{Mode, Permission};
use crate::context::{self, Fit};
use crate::host::{HostClient, HostError, Msg, ToolCall, TurnEvent, TurnReply, TurnRequest};
use crate::policy::{Policy, Verdict};
use crate::session::Session;
use crate::tools::{self, Planned, ToolCtx, ToolOutput};
use crate::ui::{Answer, ApprovalRequest, Frontend, UiEvent};

/// The model: one completion turn for the given messages and tools.
pub trait Brain {
    async fn turn(&mut self, request: &TurnRequest, ui: &mut dyn Frontend, cancel: &AtomicBool) -> Result<TurnReply, HostError>;
}

/// The host's `/coder/turn` gate. Tokens and thinking are shown while they stream.
pub struct HostBrain {
    pub host: HostClient,
}

impl Brain for HostBrain {
    async fn turn(&mut self, request: &TurnRequest, ui: &mut dyn Frontend, cancel: &AtomicBool) -> Result<TurnReply, HostError> {
        let mut on_event = |event: &TurnEvent| match event {
            TurnEvent::Token(t) => ui.event(UiEvent::Token(t.clone())),
            TurnEvent::Thinking(t) => ui.event(UiEvent::Thinking(t.clone())),
            TurnEvent::Status(s) => ui.event(UiEvent::Status(s.clone())),
            TurnEvent::Tick => ui.event(UiEvent::Tick),
            _ => {}
        };
        let cancelled = || cancel.load(Ordering::SeqCst);
        self.host.coder_turn(request, &mut on_event, &cancelled).await
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum StopReason {
    Completed,
    MaxTurns,
    Interrupted,
    PermissionDenied,
    HostError(String),
    ContextOverflow,
}

impl StopReason {
    pub fn label(&self) -> String {
        match self {
            StopReason::Completed => "completed".into(),
            StopReason::MaxTurns => "max_turns".into(),
            StopReason::Interrupted => "interrupted".into(),
            StopReason::PermissionDenied => "permission_denied".into(),
            StopReason::HostError(e) => format!("host_error: {e}"),
            StopReason::ContextOverflow => "context_overflow".into(),
        }
    }
}

pub struct Agent<B: Brain> {
    pub brain: B,
    pub tools: ToolCtx,
    pub policy: Policy,
    pub history: Vec<Msg>,
    pub mode: Mode,
    pub max_turns: u32,
    /// The host's context window (tokens), from /coder/health.
    pub context_window: u32,
    /// web_search and image_generate are offered.
    pub remote: bool,
    pub env_snapshot: String,
    pub session: Option<Session>,
    pub cancel: Arc<AtomicBool>,
    failures: HashMap<(String, String), u32>,
}

pub const REPLY_TOKENS: u32 = 1024;

impl<B: Brain> Agent<B> {
    pub fn new(brain: B, tools: ToolCtx, permission: Permission, mode: Mode, max_turns: u32, context_window: u32) -> Agent<B> {
        let cancel = tools.cancel.clone();
        let mut agent = Agent {
            brain,
            tools,
            policy: Policy::new(permission),
            history: Vec::new(),
            mode,
            max_turns,
            context_window,
            remote: true,
            env_snapshot: String::new(),
            session: None,
            cancel,
            failures: HashMap::new(),
        };
        agent.set_context_window(context_window);
        agent
    }

    pub fn set_context_window(&mut self, window: u32) {
        self.context_window = window.max(2048);
        // One tool result may use about a third of the window (8 KiB at most, design §7).
        self.tools.result_chars = ((self.context_window as usize).saturating_sub(2600) * 2).clamp(1500, 8 * 1024);
    }

    fn reply_tokens(&self) -> u32 {
        REPLY_TOKENS.min(self.context_window / 4)
    }

    fn push(&mut self, msg: Msg) {
        if let Some(s) = &mut self.session {
            s.append_msg(&msg);
        }
        self.history.push(msg);
    }

    /// The request of the next turn and how the history was fitted.
    pub fn request(&self, shrink: f32) -> (TurnRequest, Fit) {
        let tools = tools::schemas(self.remote);
        let tools_tokens = context::tokens(&serde_json::to_string(&tools).unwrap_or_default());
        let window = self.context_window as usize;
        let rules = context::project_rules(&self.tools.ws.root, (window / 8).min(600));
        let mut system = context::system_prompt(&self.tools.config.locale, self.policy.mode == Permission::Plan);
        if !rules.is_empty() {
            system.push_str("\n\n# Project rules\n");
            system.push_str(&rules);
        }
        if !self.env_snapshot.is_empty() {
            system.push_str("\n\n# Environment\n");
            system.push_str(&self.env_snapshot);
        }
        system.push_str(&context::todos_text(&self.tools.todos));
        let fixed = context::tokens(&system) + tools_tokens + self.reply_tokens() as usize + 96;
        let budget = ((window.saturating_sub(fixed)) as f32 * shrink) as usize;
        let (history, fit) = context::fit_history(&self.history, budget.max(200));
        let mut messages = vec![Msg::system(system)];
        messages.extend(history);
        let request = TurnRequest {
            mode: self.mode.as_str().into(),
            messages,
            tools,
            max_tokens: self.reply_tokens(),
            temperature: 0.2,
            inference_model: self.tools.config.inference_model.clone(),
        };
        (request, fit)
    }

    /// One user request: turns until the model answers without tools, or a stop condition.
    pub async fn run(&mut self, text: &str, ui: &mut dyn Frontend) -> StopReason {
        self.cancel.store(false, Ordering::SeqCst);
        self.push(Msg::user(text));
        let mut turns = 0u32;
        let reason = loop {
            if self.cancel.load(Ordering::SeqCst) {
                break StopReason::Interrupted;
            }
            if turns >= self.max_turns {
                break StopReason::MaxTurns;
            }
            turns += 1;
            let reply = match self.turn_with_retry(ui).await {
                Ok(r) => r,
                Err(stop) => break stop,
            };
            ui.event(UiEvent::TurnEnd);
            let calls = reply.tool_calls.clone();
            self.push(Msg::assistant(reply.text.clone(), calls.clone()));
            if calls.is_empty() {
                break StopReason::Completed;
            }
            if let Some(stop) = self.run_calls(&calls, ui).await {
                break stop;
            }
        };
        if let Some(s) = &mut self.session {
            s.append_stop(&reason.label());
            s.append_todos(&self.tools.todos);
        }
        reason
    }

    async fn turn_with_retry(&mut self, ui: &mut dyn Frontend) -> Result<TurnReply, StopReason> {
        for shrink in [1.0f32, 0.6] {
            let (request, fit) = self.request(shrink);
            if fit.dropped > 0 || fit.compressed > 0 {
                ui.event(UiEvent::Info(format!(
                    "文脈を詰めました（古い結果の要約 {} 件、省いた発言 {} 件）",
                    fit.compressed, fit.dropped
                )));
            }
            ui.event(UiEvent::TurnStart);
            match self.brain.turn(&request, ui, &self.cancel).await {
                Ok(reply) => return Ok(reply),
                Err(HostError::ContextOverflow(m)) => {
                    ui.event(UiEvent::Warn(format!("ホストの文脈に入りません（{m}）。詰めて送り直します")));
                    continue;
                }
                Err(HostError::Interrupted) => return Err(StopReason::Interrupted),
                Err(e) => return Err(StopReason::HostError(e.to_string())),
            }
        }
        Err(StopReason::ContextOverflow)
    }

    /// Run the calls of one assistant message in order. Every call gets a tool result (also the refused and the
    /// skipped ones) so the next turn sees a consistent history.
    async fn run_calls(&mut self, calls: &[ToolCall], ui: &mut dyn Frontend) -> Option<StopReason> {
        let mut stop: Option<StopReason> = None;
        for call in calls {
            if stop.is_some() || self.cancel.load(Ordering::SeqCst) {
                let why = if stop == Some(StopReason::PermissionDenied) { "利用者が依頼を止めたため" } else { "中断のため" };
                self.push(Msg::tool(&call.id, format!("{why}実行していません")));
                stop.get_or_insert(StopReason::Interrupted);
                continue;
            }
            let summary = tools::summary(call);
            ui.event(UiEvent::ToolStart { name: call.name.clone(), summary: summary.clone() });
            let planned = tools::plan(&self.tools, call);
            let change = match &planned {
                Planned::FileChange { rel, diff, .. } => Some((rel.clone(), diff.clone())),
                _ => None,
            };
            let out = match self.permit(call, &planned, ui) {
                Ok(asked) => {
                    let out = tools::execute(&mut self.tools, planned, ui).await;
                    if let (Some((rel, diff)), false, true) = (change, asked, out.ok) {
                        ui.event(UiEvent::Diff { rel, diff });
                    }
                    out
                }
                Err((out, quit)) => {
                    if quit {
                        stop = Some(StopReason::PermissionDenied);
                    }
                    out
                }
            };
            let out = self.note_repeat(call, out);
            ui.event(UiEvent::ToolEnd { name: call.name.clone(), ok: out.ok, content: out.content.clone() });
            self.push(Msg::tool(&call.id, out.content));
        }
        if self.cancel.load(Ordering::SeqCst) && stop.is_none() {
            stop = Some(StopReason::Interrupted);
        }
        stop
    }

    /// The permission gate: Ok(the user was asked) or Err((result for the model, quit the request)).
    fn permit(&mut self, call: &ToolCall, planned: &Planned, ui: &mut dyn Frontend) -> Result<bool, (ToolOutput, bool)> {
        if matches!(planned, Planned::Invalid(_)) {
            return Ok(false); // the tool reports its own error
        }
        let command = match planned {
            Planned::Command { command, .. } => Some(command.as_str()),
            _ => None,
        };
        match self.policy.check(&call.name, command) {
            Verdict::Allow => Ok(false),
            Verdict::Deny(why) => Err((ToolOutput::err(why), false)),
            Verdict::Ask(why) => {
                let (mut title, detail) = match planned {
                    Planned::FileChange { rel, diff, before, .. } => {
                        (format!("{} {rel}", if before.is_some() { "編集" } else { "作成" }), diff.clone())
                    }
                    Planned::Command { command, cwd, timeout_s } => {
                        let dir = self.tools.ws.rel(cwd);
                        (format!("コマンド（{}、出力が {timeout_s} 秒途切れたら停止）", if dir.is_empty() { "." } else { dir.as_str() }), command.clone())
                    }
                    _ => (call.name.clone(), call.arguments.clone()),
                };
                if !why.is_empty() {
                    title = format!("{title} — {why}");
                }
                match ui.approve(&ApprovalRequest { tool: call.name.clone(), title, detail }) {
                    Answer::Yes => Ok(true),
                    Answer::Always => {
                        self.policy.remember(&call.name);
                        Ok(true)
                    }
                    Answer::No => Err((ToolOutput::err("利用者が拒否しました。別の方法を考えるか、理由を聞いてください"), false)),
                    Answer::Quit => Err((ToolOutput::err("利用者が依頼を止めました"), true)),
                }
            }
        }
    }

    /// The same call failing twice is pointed out to the model (design §4.1).
    fn note_repeat(&mut self, call: &ToolCall, mut out: ToolOutput) -> ToolOutput {
        if out.ok {
            return out;
        }
        let key = (call.name.clone(), call.arguments.trim().to_string());
        let n = self.failures.entry(key).or_insert(0);
        *n += 1;
        if *n >= 2 {
            out.content.push_str("\n（同じ呼び出しが 2 回失敗しました。方針を変えるか ask_user で利用者に聞いてください）");
        }
        out
    }

    /// /compact: the host summarises the conversation; the summary replaces it (the session file keeps the rest).
    pub async fn compact(&mut self, ui: &mut dyn Frontend) -> Result<String, HostError> {
        if self.history.is_empty() {
            return Ok(String::new());
        }
        let mut transcript = String::new();
        for m in &self.history {
            let body = if m.role == "tool" { tools::clip(&m.content, 300) } else { m.content.clone() };
            transcript.push_str(&format!("[{}] {}\n", m.role, body));
            for c in &m.tool_calls {
                transcript.push_str(&format!("  -> {} {}\n", c.name, tools::summary(c)));
            }
        }
        let budget = (self.context_window as usize).saturating_sub(self.reply_tokens() as usize + 300);
        let transcript = context::cut_to_tokens(&tail_chars(&transcript, budget * 3), budget);
        let request = TurnRequest {
            mode: Mode::Fast.as_str().into(),
            messages: vec![
                Msg::system(
                    "Summarize this coding session so it can be continued: the user's goals, decisions, files changed, \
commands run and their results, open items. Under 200 words, in the user's language. Plain text.",
                ),
                Msg::user(transcript),
            ],
            tools: vec![],
            max_tokens: self.reply_tokens().min(600),
            temperature: 0.2,
            inference_model: self.tools.config.inference_model.clone(),
        };
        self.cancel.store(false, Ordering::SeqCst);
        let reply = self.brain.turn(&request, ui, &self.cancel).await?;
        ui.event(UiEvent::TurnEnd);
        let summary = reply.text.trim().to_string();
        self.history = vec![Msg::user(format!("（これまでの会話の要約）\n{summary}")), Msg::assistant("要約を読みました。続けます。", vec![])];
        if let Some(s) = &mut self.session {
            s.append_compact(&summary);
        }
        Ok(summary)
    }
}

fn tail_chars(text: &str, n: usize) -> String {
    let count = text.chars().count();
    text.chars().skip(count.saturating_sub(n)).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::Config;
    use crate::ui::ScriptedUi;
    use crate::workspace::Workspace;

    /// A model that answers from a script and records every request.
    struct Script {
        replies: Vec<Result<TurnReply, HostError>>,
        requests: Vec<TurnRequest>,
    }

    impl Brain for Script {
        async fn turn(&mut self, request: &TurnRequest, _ui: &mut dyn Frontend, _c: &AtomicBool) -> Result<TurnReply, HostError> {
            self.requests.push(request.clone());
            if self.replies.is_empty() {
                return Ok(TurnReply { text: "done".into(), finish_reason: "stop".into(), ..Default::default() });
            }
            self.replies.remove(0)
        }
    }

    fn call(id: &str, name: &str, args: &str) -> ToolCall {
        ToolCall { id: id.into(), name: name.into(), arguments: args.into() }
    }

    fn tools_reply(calls: Vec<ToolCall>) -> Result<TurnReply, HostError> {
        Ok(TurnReply { tool_calls: calls, finish_reason: "tool_calls".into(), ..Default::default() })
    }

    fn text_reply(t: &str) -> Result<TurnReply, HostError> {
        Ok(TurnReply { text: t.into(), finish_reason: "stop".into(), ..Default::default() })
    }

    fn agent(replies: Vec<Result<TurnReply, HostError>>, permission: Permission) -> (Agent<Script>, std::path::PathBuf) {
        let dir = std::env::temp_dir().join(format!("cirka-agent-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(dir.join("src")).unwrap();
        std::fs::write(dir.join("src/lib.rs"), "pub fn add(a: i32, b: i32) -> i32 { a - b }\n").unwrap();
        std::fs::write(dir.join(".env"), "TOKEN=x\n").unwrap();
        let ws = Workspace::new(&dir).unwrap();
        let ctx = ToolCtx::new(ws, Config::default(), None, Arc::new(AtomicBool::new(false)));
        let mut a = Agent::new(Script { replies, requests: vec![] }, ctx, permission, Mode::Fast, 10, 4096);
        a.remote = false;
        (a, dir)
    }

    #[tokio::test]
    async fn two_tool_calls_then_a_final_answer() {
        let (mut a, dir) = agent(vec![
            tools_reply(vec![call("1", "grep", r#"{"pattern":"fn add"}"#)]),
            tools_reply(vec![call("2", "read_file", r#"{"path":"src/lib.rs"}"#)]),
            text_reply("add は a - b を返しています"),
        ], Permission::Default);
        let mut ui = ScriptedUi::default();
        assert_eq!(a.run("add を調べて", &mut ui).await, StopReason::Completed);
        assert_eq!(a.brain.requests.len(), 3);
        let roles: Vec<&str> = a.history.iter().map(|m| m.role.as_str()).collect();
        assert_eq!(roles, ["user", "assistant", "tool", "assistant", "tool", "assistant"]);
        assert!(a.history[2].content.contains("src/lib.rs:1:"));
        // The third request carries both results and the tool schemas.
        let last = &a.brain.requests[2];
        assert_eq!(last.messages[0].role, "system");
        assert!(last.tools.iter().any(|t| t["name"] == "edit_file"));
        assert!(!last.tools.iter().any(|t| t["name"] == "web_search"));
        std::fs::remove_dir_all(dir).ok();
    }

    #[tokio::test]
    async fn default_mode_stops_before_bash_and_the_refusal_reaches_the_model() {
        let (mut a, dir) = agent(vec![
            tools_reply(vec![call("1", "bash", r#"{"command":"echo should-not-run > ran.txt"}"#)]),
            text_reply("了解"),
        ], Permission::Default);
        let mut ui = ScriptedUi { answers: vec![Answer::No], ..Default::default() };
        assert_eq!(a.run("実行して", &mut ui).await, StopReason::Completed);
        assert_eq!(ui.approvals.len(), 1);
        assert!(ui.approvals[0].detail.contains("echo should-not-run"));
        assert!(!dir.join("ran.txt").exists());
        assert!(a.history[2].content.contains("拒否"));
        std::fs::remove_dir_all(dir).ok();
    }

    #[tokio::test]
    async fn quit_stops_the_request_and_answers_every_call() {
        let (mut a, dir) = agent(vec![tools_reply(vec![
            call("1", "bash", r#"{"command":"echo a"}"#),
            call("2", "read_file", r#"{"path":"src/lib.rs"}"#),
        ])], Permission::Default);
        let mut ui = ScriptedUi { answers: vec![Answer::Quit], ..Default::default() };
        assert_eq!(a.run("x", &mut ui).await, StopReason::PermissionDenied);
        let tools: Vec<&Msg> = a.history.iter().filter(|m| m.role == "tool").collect();
        assert_eq!(tools.len(), 2);
        assert!(tools[1].content.contains("実行していません"));
        std::fs::remove_dir_all(dir).ok();
    }

    #[tokio::test]
    async fn edit_needs_a_read_and_accept_edits_writes_without_asking() {
        let (mut a, dir) = agent(vec![
            tools_reply(vec![call("1", "edit_file", r#"{"path":"src/lib.rs","old":"a - b","new":"a + b"}"#)]),
            tools_reply(vec![call("2", "read_file", r#"{"path":"src/lib.rs"}"#)]),
            tools_reply(vec![call("3", "edit_file", r#"{"path":"src/lib.rs","old":"a - b","new":"a + b"}"#)]),
            text_reply("直しました"),
        ], Permission::AcceptEdits);
        let mut ui = ScriptedUi::default();
        assert_eq!(a.run("直して", &mut ui).await, StopReason::Completed);
        assert!(a.history[2].content.contains("read_file"));
        assert!(ui.approvals.is_empty());
        assert!(std::fs::read_to_string(dir.join("src/lib.rs")).unwrap().contains("a + b"));
        assert_eq!(a.tools.undo.len(), 1);
        std::fs::remove_dir_all(dir).ok();
    }

    #[tokio::test]
    async fn auto_mode_edits_and_runs_without_asking() {
        let (mut a, dir) = agent(vec![
            tools_reply(vec![call("1", "read_file", r#"{"path":"src/lib.rs"}"#)]),
            tools_reply(vec![call("2", "edit_file", r#"{"path":"src/lib.rs","old":"a - b","new":"a + b"}"#)]),
            tools_reply(vec![call("3", "bash", r#"{"command":"echo checked"}"#)]),
            text_reply("直して確認しました"),
        ], Permission::Auto);
        let mut ui = ScriptedUi::default();
        assert_eq!(a.run("直して確認して", &mut ui).await, StopReason::Completed);
        assert!(ui.approvals.is_empty());
        assert!(std::fs::read_to_string(dir.join("src/lib.rs")).unwrap().contains("a + b"));
        assert!(a.history[6].content.contains("checked"));
        std::fs::remove_dir_all(dir).ok();
    }

    #[tokio::test]
    async fn auto_mode_still_asks_before_a_guarded_command() {
        let (mut a, dir) = agent(vec![
            tools_reply(vec![call("1", "bash", r#"{"command":"git push origin main"}"#)]),
            text_reply("やめました"),
        ], Permission::Auto);
        let mut ui = ScriptedUi { answers: vec![Answer::No], ..Default::default() };
        a.run("push して", &mut ui).await;
        assert_eq!(ui.approvals.len(), 1);
        assert!(ui.approvals[0].title.contains("git push"));
        assert!(a.history[2].content.contains("拒否"));
        std::fs::remove_dir_all(dir).ok();
    }

    #[tokio::test]
    async fn plan_mode_never_writes() {
        let (mut a, dir) = agent(vec![
            tools_reply(vec![call("1", "write_file", r#"{"path":"new.txt","content":"x"}"#)]),
            text_reply("案です"),
        ], Permission::Plan);
        let mut ui = ScriptedUi { answers: vec![Answer::Yes], ..Default::default() };
        a.run("作って", &mut ui).await;
        assert!(!dir.join("new.txt").exists() && ui.approvals.is_empty());
        assert!(a.history[2].content.contains("計画モード"));
        assert!(a.brain.requests[0].messages[0].content.contains("PLAN MODE"));
        std::fs::remove_dir_all(dir).ok();
    }

    #[tokio::test]
    async fn secrets_are_refused_by_the_tool() {
        let (mut a, dir) = agent(vec![tools_reply(vec![call("1", "read_file", r#"{"path":".env"}"#)]), text_reply("ok")],
                                 Permission::Bypass);
        let mut ui = ScriptedUi::default();
        a.run("読んで", &mut ui).await;
        assert!(a.history[2].content.contains("秘密ファイル") && !a.history[2].content.contains("TOKEN=x"));
        std::fs::remove_dir_all(dir).ok();
    }

    #[tokio::test]
    async fn a_cut_stream_runs_no_tool_and_stops_as_host_error() {
        let (mut a, dir) = agent(vec![Err(HostError::Unreachable("切れました".into()))], Permission::Bypass);
        let mut ui = ScriptedUi::default();
        let stop = a.run("x", &mut ui).await;
        assert!(matches!(stop, StopReason::HostError(m) if m.contains("切れました")));
        assert_eq!(a.history.len(), 1);
        std::fs::remove_dir_all(dir).ok();
    }

    #[tokio::test]
    async fn context_overflow_is_retried_smaller_once() {
        let (mut a, dir) = agent(vec![
            Err(HostError::ContextOverflow("big".into())),
            Err(HostError::ContextOverflow("big".into())),
        ], Permission::Default);
        let mut ui = ScriptedUi::default();
        assert_eq!(a.run("x", &mut ui).await, StopReason::ContextOverflow);
        assert_eq!(a.brain.requests.len(), 2);
        std::fs::remove_dir_all(dir).ok();
    }

    #[tokio::test]
    async fn max_turns_ends_a_tool_loop() {
        let replies = (0..20).map(|i| tools_reply(vec![call(&i.to_string(), "list_dir", "{}")])).collect();
        let (mut a, dir) = agent(replies, Permission::Default);
        a.max_turns = 3;
        let mut ui = ScriptedUi::default();
        assert_eq!(a.run("x", &mut ui).await, StopReason::MaxTurns);
        assert_eq!(a.brain.requests.len(), 3);
        std::fs::remove_dir_all(dir).ok();
    }

    #[tokio::test]
    async fn repeated_failure_is_pointed_out() {
        let bad = || tools_reply(vec![call("x", "read_file", r#"{"path":"missing.rs"}"#)]);
        let (mut a, dir) = agent(vec![bad(), bad(), text_reply("諦めます")], Permission::Default);
        let mut ui = ScriptedUi::default();
        a.run("x", &mut ui).await;
        let tools: Vec<&Msg> = a.history.iter().filter(|m| m.role == "tool").collect();
        assert!(!tools[0].content.contains("2 回失敗") && tools[1].content.contains("2 回失敗"));
        std::fs::remove_dir_all(dir).ok();
    }

    #[tokio::test]
    async fn todos_go_into_the_next_prompt() {
        let (mut a, dir) = agent(vec![
            tools_reply(vec![call("1", "todo_write", r#"{"todos":[{"content":"調べる","status":"in_progress"},{"content":"直す","status":"pending"}]}"#)]),
            text_reply("ok"),
        ], Permission::Default);
        let mut ui = ScriptedUi::default();
        a.run("x", &mut ui).await;
        assert!(a.brain.requests[1].messages[0].content.contains("[>] 調べる"));
        assert!(ui.events.iter().any(|e| matches!(e, UiEvent::Todos(t) if t.len() == 2)));
        std::fs::remove_dir_all(dir).ok();
    }

    #[tokio::test]
    async fn compact_replaces_the_history_with_a_summary() {
        let (mut a, dir) = agent(vec![text_reply("ok"), text_reply("要約: add を直した")], Permission::Default);
        let mut ui = ScriptedUi::default();
        a.run("x", &mut ui).await;
        let summary = a.compact(&mut ui).await.unwrap();
        assert_eq!(summary, "要約: add を直した");
        assert_eq!(a.history.len(), 2);
        assert!(a.history[0].content.contains("要約"));
        assert!(a.brain.requests[1].tools.is_empty());
        std::fs::remove_dir_all(dir).ok();
    }
}
