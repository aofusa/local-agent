//! What the agent loop needs from the terminal. The loop and the tools only talk to this trait, so they are
//! tested without a terminal (NullUi, ScriptedUi).

use crate::tools::Todo;

#[derive(Debug, Clone, PartialEq)]
pub enum UiEvent {
    Token(String),
    Thinking(String),
    /// A one-line state that replaces the previous one (host waiting, search progress).
    Status(String),
    ToolStart { name: String, summary: String },
    ToolEnd { name: String, ok: bool, preview: String },
    Todos(Vec<Todo>),
    Info(String),
    Warn(String),
    /// The model turn's text ended (a new line follows).
    TurnEnd,
}

/// The user's answer to a permission prompt.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Answer {
    Yes,
    No,
    /// Yes, and the same kind of call without asking for the rest of the session.
    Always,
    /// No, and end this request.
    Quit,
}

#[derive(Debug, Clone)]
pub struct ApprovalRequest {
    pub tool: String,
    pub title: String,
    /// The full command or the unified diff.
    pub detail: String,
}

pub trait Frontend: Send {
    fn event(&mut self, event: UiEvent);
    fn approve(&mut self, request: &ApprovalRequest) -> Answer;
    fn ask(&mut self, question: &str, options: &[String]) -> String;
}

/// Prints nothing and refuses everything (tests).
#[cfg(test)]
pub struct NullUi;

#[cfg(test)]
impl Frontend for NullUi {
    fn event(&mut self, _event: UiEvent) {}
    fn approve(&mut self, _request: &ApprovalRequest) -> Answer {
        Answer::No
    }
    fn ask(&mut self, _question: &str, _options: &[String]) -> String {
        "（回答なし: 非対話モード）".into()
    }
}

/// Tests: answers from a list and records what was shown.
#[cfg(test)]
#[derive(Default)]
pub struct ScriptedUi {
    pub answers: Vec<Answer>,
    pub replies: Vec<String>,
    pub events: Vec<UiEvent>,
    pub approvals: Vec<ApprovalRequest>,
}

#[cfg(test)]
impl Frontend for ScriptedUi {
    fn event(&mut self, event: UiEvent) {
        self.events.push(event);
    }
    fn approve(&mut self, request: &ApprovalRequest) -> Answer {
        self.approvals.push(request.clone());
        if self.answers.is_empty() { Answer::No } else { self.answers.remove(0) }
    }
    fn ask(&mut self, _question: &str, _options: &[String]) -> String {
        if self.replies.is_empty() { String::new() } else { self.replies.remove(0) }
    }
}
