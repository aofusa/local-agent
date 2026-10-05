//! The prompt of every turn (design §7): the loop contract, the project rules, an environment snapshot, the task
//! list and the conversation, fitted into the host's context window. The host keeps nothing between turns.

use std::collections::HashMap;
use std::path::Path;

use crate::host::Msg;
use crate::tools::{self, Todo};

pub const RULE_FILES: &[&str] = &["CIRKA.md", "AGENTS.md", "CLAUDE.md"];
const RULE_FILE_MAX: usize = 16 * 1024;

pub fn system_prompt(locale: &str, plan_mode: bool) -> String {
    let mut s = format!(
        "You are cirka, a coding agent working in the user's directory through tools. The tools run on the user's \
machine; you only see their results.\n\
Rules:\n\
- Reply in the user's language ({locale} unless the user writes otherwise). Be brief.\n\
- Multi-step requests: first call todo_write with 3-7 items (investigate / change / verify), keep exactly one \
in_progress, mark items done as you go. One-step questions: answer without a task list.\n\
- Explore with glob, grep and list_dir; read_file before you edit. edit_file needs an `old` text that is unique \
and copied exactly from the file (without the line-number prefix).\n\
- Verify changes with the project's own commands (tests, build) through bash. Commands need the user's approval.\n\
- If the same failure happens twice, change the approach or ask_user.\n\
- Never read .env files, keys or credentials; ask the user instead.\n\
- Use web_search for facts you do not know (current APIs, error messages); never invent them.\n\
- Call image_generate only when an image is the deliverable.\n\
- End with a short summary of what changed, what ran and what is left, without a tool call."
    );
    if plan_mode {
        s.push_str(
            "\nPLAN MODE: do not call edit_file, write_file or bash. Investigate, then write the patch and the commands \
you would run in your final answer.",
        );
    }
    s
}

/// The same cautious estimate as the host's gate: ~1 token per non-ASCII character, ~1 per 3 ASCII characters.
pub fn tokens(text: &str) -> usize {
    let ascii = text.bytes().filter(|b| *b < 128).count();
    let chars = text.chars().count();
    (chars - ascii.min(chars)) + ascii / 3
}

pub fn msg_tokens(m: &Msg) -> usize {
    tokens(&m.content) + m.tool_calls.iter().map(|c| tokens(&c.name) + tokens(&c.arguments) + 8).sum::<usize>() + 8
}

pub fn total_tokens(msgs: &[Msg]) -> usize {
    msgs.iter().map(msg_tokens).sum()
}

/// Project rules from the workspace root (`CIRKA.md`, `AGENTS.md`, `CLAUDE.md`; parents are not read), each cut at
/// 16 KiB, all of them cut to `max_tokens`.
pub fn project_rules(root: &Path, max_tokens: usize) -> String {
    let mut parts = Vec::new();
    for name in RULE_FILES {
        if let Ok(text) = std::fs::read_to_string(root.join(name)) {
            let text: String = text.chars().take(RULE_FILE_MAX).collect();
            parts.push(format!("## {name}\n{}", text.trim()));
        }
    }
    let joined = parts.join("\n\n");
    cut_to_tokens(&joined, max_tokens)
}

pub fn cut_to_tokens(text: &str, max_tokens: usize) -> String {
    if tokens(text) <= max_tokens {
        return text.to_string();
    }
    let mut out = String::new();
    let mut used = 0usize;
    for ch in text.chars() {
        used += if ch.is_ascii() { 1 } else { 3 };
        if used / 3 >= max_tokens.saturating_sub(12) {
            break;
        }
        out.push(ch);
    }
    out.push_str("\n…（以下省略）");
    out
}

#[derive(Debug, Default, Clone, PartialEq)]
pub struct Fit {
    pub compressed: usize,
    pub dropped: usize,
    pub overflow: bool,
}

/// `[実行済み] grep main src → 12 行（古い結果は省略）`
fn compressed(call_name: &str, call_summary: &str, content: &str) -> String {
    let lines = content.lines().count();
    let head = content.lines().next().unwrap_or("").chars().take(80).collect::<String>();
    format!("[実行済み] {call_name} {call_summary} → {lines} 行: {head}（古い結果は省略）")
}

/// Fit the conversation into `budget` tokens: past 70 % of it, old tool results (all but the last two) become
/// one-liners; then the oldest exchanges are dropped whole (an assistant message with its tool results), never
/// the last user message and what follows it.
pub fn fit_history(history: &[Msg], budget: usize) -> (Vec<Msg>, Fit) {
    let mut msgs = history.to_vec();
    let mut fit = Fit::default();
    if total_tokens(&msgs) > budget * 7 / 10 {
        let calls: HashMap<String, (String, String)> = msgs
            .iter()
            .flat_map(|m| m.tool_calls.iter())
            .map(|c| (c.id.clone(), (c.name.clone(), tools::summary(c))))
            .collect();
        let tool_positions: Vec<usize> = msgs.iter().enumerate().filter(|(_, m)| m.role == "tool").map(|(i, _)| i).collect();
        let keep_from = tool_positions.len().saturating_sub(2);
        for &i in &tool_positions[..keep_from] {
            let m = &mut msgs[i];
            if m.content.starts_with("[実行済み]") {
                continue;
            }
            let (name, summary) = m.tool_call_id.as_ref().and_then(|id| calls.get(id)).cloned().unwrap_or_default();
            m.content = compressed(&name, &summary, &m.content);
            fit.compressed += 1;
        }
    }
    let last_user = msgs.iter().rposition(|m| m.role == "user").unwrap_or(0);
    let mut start = 0usize;
    while total_tokens(&msgs[start..]) > budget && start < last_user {
        // Drop one unit: a user message, or an assistant message plus the tool results that answer it.
        let mut end = start + 1;
        if msgs[start].role == "assistant" {
            while end < msgs.len() && msgs[end].role == "tool" {
                end += 1;
            }
        }
        let end = end.min(last_user);
        fit.dropped += end - start;
        start = end.max(start + 1);
    }
    // A tool result whose call was dropped would confuse the model: skip leading tool messages.
    while start < msgs.len() && msgs[start].role == "tool" {
        start += 1;
        fit.dropped += 1;
    }
    let mut out = msgs[start..].to_vec();
    if total_tokens(&out) > budget {
        // Still too long (huge results in this very request): clip every tool result hard.
        for m in out.iter_mut().filter(|m| m.role == "tool") {
            m.content = tools::clip(&m.content, 600);
        }
        fit.overflow = total_tokens(&out) > budget;
    }
    (out, fit)
}

pub fn todos_text(todos: &[Todo]) -> String {
    if todos.is_empty() {
        String::new()
    } else {
        format!("\n\n# Task list\n{}", tools::todo::render(todos))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::host::ToolCall;

    fn exchange(n: usize, size: usize) -> Vec<Msg> {
        let id = format!("c{n}");
        vec![
            Msg::assistant("", vec![ToolCall { id: id.clone(), name: "grep".into(), arguments: format!("{{\"pattern\":\"p{n}\"}}") }]),
            Msg::tool(&id, "x".repeat(size)),
        ]
    }

    #[test]
    fn small_history_is_untouched() {
        let h = vec![Msg::user("hi"), Msg::assistant("hello", vec![])];
        let (out, fit) = fit_history(&h, 1000);
        assert_eq!(out, h);
        assert_eq!(fit, Fit::default());
    }

    #[test]
    fn old_tool_results_are_compressed_first() {
        let mut h = vec![Msg::user("find")];
        for n in 0..4 {
            h.extend(exchange(n, 900));
        }
        let (out, fit) = fit_history(&h, 1200);
        assert_eq!(fit.compressed, 2);
        assert!(out[2].content.starts_with("[実行済み] grep p0"));
        assert_eq!(out.last().unwrap().content.len(), 900); // the last two stay whole
        assert_eq!(fit.dropped, 0);
    }

    #[test]
    fn oldest_exchanges_are_dropped_whole_and_the_request_stays() {
        let mut h = vec![Msg::user("old request")];
        for n in 0..3 {
            h.extend(exchange(n, 300));
        }
        h.push(Msg::user("new request"));
        h.extend(exchange(9, 300));
        let (out, fit) = fit_history(&h, 160);
        assert!(fit.dropped > 0);
        assert_eq!(out[0].content, "new request");
        assert!(out.iter().all(|m| m.role != "tool" || out.iter().any(|a| a.tool_calls.iter().any(|c| Some(&c.id) == m.tool_call_id.as_ref()))));
    }

    #[test]
    fn token_estimate_matches_the_host() {
        assert_eq!(tokens("abcdef"), 2);
        assert_eq!(tokens("日本語"), 3);
    }

    #[test]
    fn rules_are_read_and_cut() {
        let dir = std::env::temp_dir().join(format!("cirka-ctx-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("AGENTS.md"), "use pytest\n".repeat(500)).unwrap();
        let rules = project_rules(&dir, 100);
        assert!(rules.starts_with("## AGENTS.md") && rules.contains("以下省略"));
        assert!(tokens(&rules) <= 110);
        std::fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn plan_mode_prompt() {
        assert!(system_prompt("ja", true).contains("PLAN MODE"));
        assert!(!system_prompt("ja", false).contains("PLAN MODE"));
        assert!(tokens(&system_prompt("ja", false)) < 450);
    }
}
