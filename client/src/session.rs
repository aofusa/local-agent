//! Sessions as JSON lines under the data directory (`%LOCALAPPDATA%\cirka\sessions`, `~/.local/share/cirka/sessions`).
//! The file is the history: the host keeps nothing. `/resume` loads the newest session of the same directory;
//! `/forget` deletes the current file (tool results are in it).

use std::io::Write;
use std::path::{Path, PathBuf};

use serde_json::{Value, json};

use crate::host::Msg;
use crate::tools::Todo;

pub struct Session {
    #[allow(dead_code)]
    pub id: String,
    pub path: PathBuf,
}

fn now() -> u64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

impl Session {
    pub fn create(dir: &Path, cwd: &Path, host: &str) -> std::io::Result<Session> {
        std::fs::create_dir_all(dir)?;
        let id = format!("{}-{}", now(), &uuid::Uuid::new_v4().simple().to_string()[..8]);
        let path = dir.join(format!("{id}.jsonl"));
        let mut s = Session { id, path };
        s.write(&json!({"type": "meta", "cwd": cwd.to_string_lossy(), "host": host, "started": now(),
                        "version": env!("CARGO_PKG_VERSION")}));
        Ok(s)
    }

    /// Continue an existing file (after /resume).
    pub fn open(path: &Path) -> Session {
        let id = path.file_stem().map(|s| s.to_string_lossy().to_string()).unwrap_or_default();
        Session { id, path: path.to_path_buf() }
    }

    fn write(&mut self, value: &Value) {
        // A failed log write never stops the work.
        if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&self.path) {
            let _ = writeln!(f, "{value}");
        }
    }

    pub fn append_msg(&mut self, msg: &Msg) {
        self.write(&json!({"type": "msg", "msg": msg}));
    }

    pub fn append_todos(&mut self, todos: &[Todo]) {
        self.write(&json!({"type": "todos", "todos": todos}));
    }

    pub fn append_stop(&mut self, reason: &str) {
        self.write(&json!({"type": "stop", "reason": reason, "at": now()}));
    }

    pub fn append_compact(&mut self, summary: &str) {
        self.write(&json!({"type": "compact", "summary": summary}));
    }

    pub fn forget(self) -> std::io::Result<()> {
        std::fs::remove_file(&self.path)
    }
}

#[derive(Debug, Default)]
pub struct Loaded {
    pub messages: Vec<Msg>,
    pub todos: Vec<Todo>,
    pub cwd: String,
}

/// Replay a session file: messages in order, a compaction replaces what came before it.
pub fn load(path: &Path) -> std::io::Result<Loaded> {
    let text = std::fs::read_to_string(path)?;
    let mut loaded = Loaded::default();
    for line in text.lines() {
        let Ok(v) = serde_json::from_str::<Value>(line) else { continue };
        match v.get("type").and_then(Value::as_str) {
            Some("meta") => loaded.cwd = v.get("cwd").and_then(Value::as_str).unwrap_or("").to_string(),
            Some("msg") => {
                if let Some(m) = v.get("msg").and_then(|m| serde_json::from_value::<Msg>(m.clone()).ok()) {
                    loaded.messages.push(m);
                }
            }
            Some("todos") => {
                loaded.todos = v.get("todos").and_then(|t| serde_json::from_value(t.clone()).ok()).unwrap_or_default();
            }
            Some("compact") => {
                let summary = v.get("summary").and_then(Value::as_str).unwrap_or("");
                loaded.messages = vec![Msg::user(format!("（これまでの会話の要約）\n{summary}")),
                                       Msg::assistant("要約を読みました。続けます。", vec![])];
            }
            _ => {}
        }
    }
    // A request cut in the middle (tool calls without results) is closed so the model sees a consistent history.
    if let Some(last) = loaded.messages.last() {
        if last.role == "assistant" && !last.tool_calls.is_empty() {
            let ids: Vec<String> = last.tool_calls.iter().map(|c| c.id.clone()).collect();
            for id in ids {
                loaded.messages.push(Msg::tool(&id, "前回のセッションが途中で終わったため結果はありません"));
            }
        }
    }
    Ok(loaded)
}

/// The newest session file started in `cwd` (other than `except`).
pub fn latest_for(dir: &Path, cwd: &Path, except: Option<&Path>) -> Option<PathBuf> {
    let want = cwd.to_string_lossy().to_string();
    let mut files: Vec<PathBuf> = std::fs::read_dir(dir)
        .ok()?
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.extension().is_some_and(|e| e == "jsonl") && Some(p.as_path()) != except)
        .collect();
    files.sort();
    files.into_iter().rev().find(|p| {
        std::fs::read_to_string(p)
            .ok()
            .and_then(|t| t.lines().next().and_then(|l| serde_json::from_str::<Value>(l).ok()))
            .and_then(|v| v.get("cwd").and_then(Value::as_str).map(|c| c == want))
            .unwrap_or(false)
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::host::ToolCall;

    #[test]
    fn round_trip_and_resume_latest_for_the_directory() {
        let dir = std::env::temp_dir().join(format!("cirka-sess-{}", uuid::Uuid::new_v4()));
        let cwd = Path::new("/work/a");
        let mut s = Session::create(&dir, cwd, "http://h").unwrap();
        s.append_msg(&Msg::user("hi"));
        s.append_msg(&Msg::assistant("", vec![ToolCall { id: "1".into(), name: "grep".into(), arguments: "{}".into() }]));
        s.append_todos(&[Todo { content: "a".into(), status: "pending".into() }]);
        let loaded = load(&s.path).unwrap();
        assert_eq!(loaded.messages.len(), 3); // the open call is closed with a result
        assert_eq!(loaded.messages[2].role, "tool");
        assert_eq!(loaded.todos.len(), 1);
        std::thread::sleep(std::time::Duration::from_millis(1100));
        let other = Session::create(&dir, Path::new("/work/b"), "http://h").unwrap();
        assert_eq!(latest_for(&dir, cwd, None).unwrap(), s.path);
        assert_eq!(latest_for(&dir, cwd, Some(&s.path)), None);
        assert!(latest_for(&dir, Path::new("/work/b"), None).unwrap() == other.path);
        s.forget().unwrap();
        std::fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn compaction_replaces_earlier_messages() {
        let dir = std::env::temp_dir().join(format!("cirka-sess-{}", uuid::Uuid::new_v4()));
        let mut s = Session::create(&dir, Path::new("/w"), "h").unwrap();
        s.append_msg(&Msg::user("old"));
        s.append_compact("summary");
        s.append_msg(&Msg::user("new"));
        let loaded = load(&s.path).unwrap();
        assert_eq!(loaded.messages.len(), 3);
        assert!(loaded.messages[0].content.contains("summary"));
        std::fs::remove_dir_all(dir).ok();
    }
}
