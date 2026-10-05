//! todo_write: the task list the model keeps for multi-step requests (one item in progress at a time).

use serde::{Deserialize, Serialize};
use serde_json::Value;

use super::{ToolCtx, ToolOutput};
use crate::ui::{Frontend, UiEvent};

pub const STATUSES: &[&str] = &["pending", "in_progress", "done", "blocked"];

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Todo {
    pub content: String,
    pub status: String,
}

pub fn parse(args: &Value) -> Result<Vec<Todo>, String> {
    let items = args.get("todos").and_then(Value::as_array).ok_or("todos がありません")?;
    if items.len() > 20 {
        return Err("項目は 20 件までです".into());
    }
    let mut todos = Vec::new();
    for item in items {
        let content = item.get("content").and_then(Value::as_str).map(str::trim).unwrap_or("");
        let status = item.get("status").and_then(Value::as_str).map(|s| s.trim().to_ascii_lowercase()).unwrap_or_default();
        let status = if status == "completed" { "done".to_string() } else { status };
        if content.is_empty() {
            return Err("content が空の項目があります".into());
        }
        if !STATUSES.contains(&status.as_str()) {
            return Err(format!("status は {} のどれかです: {status}", STATUSES.join(" / ")));
        }
        todos.push(Todo { content: content.chars().take(200).collect(), status });
    }
    if todos.iter().filter(|t| t.status == "in_progress").count() > 1 {
        return Err("in_progress は同時に 1 件だけです".into());
    }
    Ok(todos)
}

pub fn render(todos: &[Todo]) -> String {
    todos
        .iter()
        .map(|t| {
            let mark = match t.status.as_str() {
                "done" => "[x]",
                "in_progress" => "[>]",
                "blocked" => "[!]",
                _ => "[ ]",
            };
            format!("{mark} {}", t.content)
        })
        .collect::<Vec<_>>()
        .join("\n")
}

pub fn write(ctx: &mut ToolCtx, args: &Value, ui: &mut dyn Frontend) -> ToolOutput {
    match parse(args) {
        Ok(todos) => {
            ctx.todos = todos;
            ui.event(UiEvent::Todos(ctx.todos.clone()));
            let open = ctx.todos.iter().filter(|t| t.status != "done").count();
            ToolOutput::ok(format!("タスク一覧を更新しました（{} 件、未完了 {open} 件）", ctx.todos.len()))
        }
        Err(e) => ToolOutput::err(e),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn one_in_progress_and_known_statuses() {
        assert!(parse(&json!({"todos": [{"content": "a", "status": "in_progress"},
                                         {"content": "b", "status": "in_progress"}]})).is_err());
        assert!(parse(&json!({"todos": [{"content": "a", "status": "doing"}]})).is_err());
        let t = parse(&json!({"todos": [{"content": "a", "status": "completed"}, {"content": "b", "status": "pending"}]})).unwrap();
        assert_eq!(t[0].status, "done");
        assert_eq!(render(&t), "[x] a\n[ ] b");
    }
}
