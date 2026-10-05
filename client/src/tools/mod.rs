//! The tools the model may call. Names and arguments are a contract with the model; cirka runs every tool here,
//! on this machine, inside the workspace. The host only sees their (redacted, truncated) results.

pub mod bash;
pub mod fs;
pub mod grep;
pub mod remote;
pub mod todo;

use std::collections::HashSet;
use std::path::PathBuf;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};

use serde_json::{Value, json};

use crate::config::Config;
use crate::host::{HostClient, ToolCall};
use crate::ui::Frontend;
use crate::workspace::{Workspace, redact};

pub use todo::Todo;

/// What a tool does to the world; the permission policy decides from this.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Effect {
    Read,
    Edit,
    Command,
    Remote,
    Session,
}

pub fn effect(name: &str) -> Effect {
    match name {
        "edit_file" | "write_file" => Effect::Edit,
        "bash" => Effect::Command,
        "web_search" | "image_generate" => Effect::Remote,
        "todo_write" | "ask_user" => Effect::Session,
        _ => Effect::Read,
    }
}

pub const NAMES: &[&str] = &[
    "list_dir",
    "glob",
    "grep",
    "read_file",
    "edit_file",
    "write_file",
    "bash",
    "todo_write",
    "ask_user",
    "web_search",
    "image_generate",
];

/// JSON Schemas sent to the host every turn. Short on purpose: the host's 27B has a 4096-token window.
pub fn schemas(remote: bool) -> Vec<Value> {
    let s = |t: &str| json!({"type": t});
    let mut tools = vec![
        json!({"name": "list_dir", "description": "List a directory (depth 1-2).",
               "parameters": {"type": "object", "properties": {"path": s("string"), "depth": s("integer")}}}),
        json!({"name": "glob", "description": "Find files by glob, e.g. src/**/*.rs.",
               "parameters": {"type": "object", "properties": {"pattern": s("string")}, "required": ["pattern"]}}),
        json!({"name": "grep", "description": "Regex search in files. Returns path:line: text.",
               "parameters": {"type": "object", "properties": {"pattern": s("string"), "path": s("string"), "glob": s("string")},
                              "required": ["pattern"]}}),
        json!({"name": "read_file", "description": "Read a text file with line numbers. offset is the first line (1-based).",
               "parameters": {"type": "object", "properties": {"path": s("string"), "offset": s("integer"), "limit": s("integer")},
                              "required": ["path"]}}),
        json!({"name": "edit_file", "description": "Replace the exact text old (must be unique) with new. Read the file first.",
               "parameters": {"type": "object", "properties": {"path": s("string"), "old": s("string"), "new": s("string")},
                              "required": ["path", "old", "new"]}}),
        json!({"name": "write_file", "description": "Create a file, or replace a file you have read.",
               "parameters": {"type": "object", "properties": {"path": s("string"), "content": s("string")},
                              "required": ["path", "content"]}}),
        json!({"name": "bash", "description": "Run a shell command in the workspace (build, test, git).",
               "parameters": {"type": "object", "properties": {"command": s("string"), "timeout_s": s("integer"), "cwd": s("string")},
                              "required": ["command"]}}),
        json!({"name": "todo_write", "description": "Replace the task list. status: pending|in_progress|done|blocked.",
               "parameters": {"type": "object", "properties": {"todos": {"type": "array", "items": {"type": "object",
                   "properties": {"content": s("string"), "status": s("string")}, "required": ["content", "status"]}}},
                              "required": ["todos"]}}),
        json!({"name": "ask_user", "description": "Ask the user a question (only for ambiguous or destructive choices).",
               "parameters": {"type": "object", "properties": {"question": s("string"), "options": {"type": "array", "items": s("string")}},
                              "required": ["question"]}}),
    ];
    if remote {
        tools.push(json!({"name": "web_search", "description": "Search the web (host, through Tor). Returns an answer with sources.",
                          "parameters": {"type": "object", "properties": {"query": s("string")}, "required": ["query"]}}));
        tools.push(json!({"name": "image_generate", "description": "Generate an image (host). references: [{path, role: character|pose|style|base|mask, strength}].",
                          "parameters": {"type": "object", "properties": {"prompt": s("string"), "references": {"type": "array", "items": {"type": "object",
                              "properties": {"path": s("string"), "role": s("string"), "strength": s("number")}, "required": ["path", "role"]}}},
                                         "required": ["prompt"]}}));
    }
    tools
}

#[derive(Debug, Clone, PartialEq)]
pub struct ToolOutput {
    pub content: String,
    pub ok: bool,
}

impl ToolOutput {
    pub fn ok(content: impl Into<String>) -> ToolOutput {
        ToolOutput { content: content.into(), ok: true }
    }
    pub fn err(content: impl Into<String>) -> ToolOutput {
        ToolOutput { content: format!("エラー: {}", content.into()), ok: false }
    }
}

/// A file before an agent edit, for /undo.
#[derive(Debug, Clone)]
pub struct UndoEntry {
    pub path: PathBuf,
    pub before: Option<Vec<u8>>,
}

/// A call checked and prepared, waiting for the permission decision. Nothing has changed yet.
#[derive(Debug, Clone)]
pub enum Planned {
    /// Read-only or session tools: run directly.
    Run { name: String, args: Value },
    /// A file change, shown as a diff before it is written.
    FileChange { path: PathBuf, rel: String, after: Vec<u8>, before: Option<Vec<u8>>, diff: String },
    /// A shell command, shown in full before it runs.
    Command { command: String, cwd: PathBuf, timeout_s: u64 },
    /// The call itself is wrong (unknown tool, bad arguments, outside the workspace ...): the model is told why.
    Invalid(ToolOutput),
}

pub struct ToolCtx {
    pub ws: Workspace,
    pub config: Config,
    pub host: Option<HostClient>,
    pub read_files: HashSet<PathBuf>,
    pub todos: Vec<Todo>,
    pub undo: Vec<UndoEntry>,
    /// The most characters of one result sent to the host (head and tail are kept).
    pub result_chars: usize,
    pub cancel: Arc<AtomicBool>,
}

impl ToolCtx {
    pub fn new(ws: Workspace, config: Config, host: Option<HostClient>, cancel: Arc<AtomicBool>) -> ToolCtx {
        ToolCtx {
            ws,
            config,
            host,
            read_files: HashSet::new(),
            todos: Vec::new(),
            undo: Vec::new(),
            result_chars: 8 * 1024,
            cancel,
        }
    }

    pub fn cancelled(&self) -> bool {
        self.cancel.load(Ordering::SeqCst)
    }
}

pub fn parse_args(call: &ToolCall) -> Result<Value, String> {
    let raw = call.arguments.trim();
    if raw.is_empty() {
        return Ok(json!({}));
    }
    match serde_json::from_str::<Value>(raw) {
        Ok(v @ Value::Object(_)) => Ok(v),
        Ok(_) => Err("引数は JSON オブジェクトです".into()),
        Err(e) => Err(format!("引数の JSON を読めません: {e}")),
    }
}

pub fn arg_str<'a>(args: &'a Value, key: &str) -> Option<&'a str> {
    args.get(key).and_then(Value::as_str)
}

pub fn arg_u64(args: &Value, key: &str) -> Option<u64> {
    args.get(key).and_then(|v| v.as_u64().or_else(|| v.as_f64().map(|f| f.max(0.0) as u64)).or_else(|| v.as_str()?.parse().ok()))
}

/// Check a call and prepare its effect (diff or command) without changing anything.
pub fn plan(ctx: &ToolCtx, call: &ToolCall) -> Planned {
    if !NAMES.contains(&call.name.as_str()) {
        return Planned::Invalid(ToolOutput::err(format!("知らないツールです: {}（使えるのは {}）", call.name, NAMES.join(", "))));
    }
    let args = match parse_args(call) {
        Ok(a) => a,
        Err(e) => return Planned::Invalid(ToolOutput::err(e)),
    };
    match call.name.as_str() {
        "edit_file" => fs::plan_edit(ctx, &args),
        "write_file" => fs::plan_write(ctx, &args),
        "bash" => bash::plan(ctx, &args),
        _ => Planned::Run { name: call.name.clone(), args },
    }
}

/// Run a planned call (after the permission check passed).
pub async fn execute(ctx: &mut ToolCtx, planned: Planned, ui: &mut dyn Frontend) -> ToolOutput {
    let out = match planned {
        Planned::Invalid(out) => out,
        Planned::FileChange { path, rel, after, before, .. } => fs::apply(ctx, &path, &rel, after, before),
        Planned::Command { command, cwd, timeout_s } => bash::run(ctx, &command, &cwd, timeout_s, ui).await,
        Planned::Run { name, args } => match name.as_str() {
            "list_dir" => fs::list_dir(ctx, &args),
            "glob" => fs::glob(ctx, &args),
            "grep" => grep::grep(ctx, &args),
            "read_file" => fs::read_file(ctx, &args),
            "todo_write" => todo::write(ctx, &args, ui),
            "ask_user" => ask_user(&args, ui),
            "web_search" => remote::web_search(ctx, &args, ui).await,
            "image_generate" => remote::image_generate(ctx, &args, ui).await,
            other => ToolOutput::err(format!("知らないツールです: {other}")),
        },
    };
    ToolOutput { content: clip(&redact(&out.content), ctx.result_chars), ok: out.ok }
}

fn ask_user(args: &Value, ui: &mut dyn Frontend) -> ToolOutput {
    let Some(question) = arg_str(args, "question").filter(|q| !q.trim().is_empty()) else {
        return ToolOutput::err("question がありません");
    };
    let options: Vec<String> = args
        .get("options")
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(|v| v.as_str().map(String::from)).collect())
        .unwrap_or_default();
    let answer = ui.ask(question, &options);
    ToolOutput::ok(format!("利用者の回答: {answer}"))
}

/// Keep the head and the tail of a long result (the middle is usually the least useful part of a log).
pub fn clip(text: &str, max_chars: usize) -> String {
    let count = text.chars().count();
    if count <= max_chars {
        return text.to_string();
    }
    let head_n = max_chars * 2 / 3;
    let tail_n = max_chars.saturating_sub(head_n).saturating_sub(40);
    let head: String = text.chars().take(head_n).collect();
    let tail: String = text.chars().skip(count - tail_n).collect();
    format!("{head}\n…（{} 文字省略）…\n{tail}", count - head_n - tail_n)
}

/// A one-line summary of a call for the terminal and for compressed history.
pub fn summary(call: &ToolCall) -> String {
    let args = parse_args(call).unwrap_or(Value::Null);
    let pick = |k: &str| arg_str(&args, k).map(|s| s.replace('\n', " ")).unwrap_or_default();
    let short = |s: String| if s.chars().count() > 80 { format!("{}…", s.chars().take(80).collect::<String>()) } else { s };
    match call.name.as_str() {
        "list_dir" => short(if pick("path").is_empty() { ".".into() } else { pick("path") }),
        "glob" => short(pick("pattern")),
        "grep" => short(format!("{} {}", pick("pattern"), pick("path")).trim().to_string()),
        "read_file" | "edit_file" | "write_file" => short(pick("path")),
        "bash" => short(pick("command")),
        "web_search" => short(pick("query")),
        "image_generate" => short(pick("prompt")),
        "ask_user" => short(pick("question")),
        "todo_write" => format!("{} 件", args.get("todos").and_then(Value::as_array).map(|a| a.len()).unwrap_or(0)),
        _ => String::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn clip_keeps_head_and_tail() {
        let text: String = (0..1000).map(|i| format!("{i}\n")).collect();
        let out = clip(&text, 200);
        assert!(out.starts_with("0\n1\n") && out.ends_with("998\n999\n") && out.contains("省略"));
        assert!(out.chars().count() < 260);
        assert_eq!(clip("short", 200), "short");
    }

    #[test]
    fn schemas_are_named_and_remote_optional() {
        let local = schemas(false);
        assert_eq!(local.len(), 9);
        let all = schemas(true);
        let names: Vec<&str> = all.iter().map(|t| t["name"].as_str().unwrap()).collect();
        assert_eq!(names, NAMES);
        assert!(serde_json::to_string(&all).unwrap().len() < 4000);
    }

    #[test]
    fn effects() {
        assert_eq!(effect("read_file"), Effect::Read);
        assert_eq!(effect("edit_file"), Effect::Edit);
        assert_eq!(effect("bash"), Effect::Command);
        assert_eq!(effect("web_search"), Effect::Remote);
    }

    #[test]
    fn args_parse() {
        let c = |a: &str| ToolCall { id: "1".into(), name: "grep".into(), arguments: a.into() };
        assert!(parse_args(&c("{\"pattern\":\"x\"}")).is_ok());
        assert!(parse_args(&c("[1]")).is_err());
        assert!(parse_args(&c("{broken")).is_err());
        assert_eq!(parse_args(&c("")).unwrap(), json!({}));
        assert_eq!(arg_u64(&json!({"n": "12"}), "n"), Some(12));
    }
}
