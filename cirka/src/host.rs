//! The local-agent host: the model gate (`POST /coder/turn`, SSE) and the existing graphs (`POST /runs/stream`:
//! `chat` for web search through Tor, `agent` for image generation). cirka never talks to LM Studio, ComfyUI or
//! Tor directly; they stay on the host's loopback.

use std::time::Duration;

use futures_util::StreamExt;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use crate::config::Config;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ToolCall {
    pub id: String,
    pub name: String,
    /// The JSON arguments as the model wrote them.
    pub arguments: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Msg {
    pub role: String,
    #[serde(default)]
    pub content: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub tool_calls: Vec<ToolCall>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tool_call_id: Option<String>,
}

impl Msg {
    pub fn system(text: impl Into<String>) -> Msg {
        Msg { role: "system".into(), content: text.into(), tool_calls: vec![], tool_call_id: None }
    }
    pub fn user(text: impl Into<String>) -> Msg {
        Msg { role: "user".into(), content: text.into(), tool_calls: vec![], tool_call_id: None }
    }
    pub fn assistant(text: impl Into<String>, calls: Vec<ToolCall>) -> Msg {
        Msg { role: "assistant".into(), content: text.into(), tool_calls: calls, tool_call_id: None }
    }
    pub fn tool(id: &str, text: impl Into<String>) -> Msg {
        Msg { role: "tool".into(), content: text.into(), tool_calls: vec![], tool_call_id: Some(id.into()) }
    }
}

/// One event of a model turn as the gate streams it.
#[derive(Debug, Clone, PartialEq)]
pub enum TurnEvent {
    Status(String),
    Thinking(String),
    Token(String),
    ToolCall(ToolCall),
    Done { finish_reason: String, input_tokens: u64, output_tokens: u64 },
    Error { message: String, code: String },
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct TurnReply {
    pub text: String,
    pub tool_calls: Vec<ToolCall>,
    pub finish_reason: String,
    pub input_tokens: u64,
    pub output_tokens: u64,
}

#[derive(Debug, Clone, PartialEq)]
pub enum HostError {
    /// The host or the network failed (stream cut, timeout, HTTP error).
    Unreachable(String),
    /// The prompt does not fit the host's context window: compact and try again.
    ContextOverflow(String),
    /// The gate said no (LM Studio down, busy for too long, bad request).
    Refused { message: String, code: String },
    Interrupted,
}

impl std::fmt::Display for HostError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            HostError::Unreachable(m) => write!(f, "ホストに届きません: {m}"),
            HostError::ContextOverflow(m) => write!(f, "文脈が溢れました: {m}"),
            HostError::Refused { message, code } => write!(f, "ホストが拒否しました（{code}）: {message}"),
            HostError::Interrupted => write!(f, "中断しました"),
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct TurnRequest {
    pub mode: String,
    pub messages: Vec<Msg>,
    pub tools: Vec<Value>,
    pub max_tokens: u32,
    pub temperature: f32,
}

#[derive(Debug, Clone, Default)]
pub struct Health {
    pub ok: bool,
    pub gate: bool,
    pub lmstudio: bool,
    pub context: u32,
    pub model: String,
    pub busy: Option<String>,
    pub error: Option<String>,
}

/// SSE framing: `event:` / `data:` lines, blank line ends one event. Feeds partial chunks.
#[derive(Default)]
pub struct SseParser {
    buf: String,
    event: String,
    data: Vec<String>,
}

impl SseParser {
    pub fn feed(&mut self, chunk: &str) -> Vec<(String, String)> {
        self.buf.push_str(chunk);
        let mut out = Vec::new();
        while let Some(pos) = self.buf.find('\n') {
            let line: String = self.buf.drain(..=pos).collect();
            let line = line.trim_end_matches(['\n', '\r']);
            if line.is_empty() {
                if !self.data.is_empty() || !self.event.is_empty() {
                    let event = if self.event.is_empty() { "message".to_string() } else { std::mem::take(&mut self.event) };
                    out.push((event, self.data.join("\n")));
                    self.data.clear();
                }
            } else if let Some(v) = line.strip_prefix("event:") {
                self.event = v.trim().to_string();
            } else if let Some(v) = line.strip_prefix("data:") {
                self.data.push(v.strip_prefix(' ').unwrap_or(v).to_string());
            }
        }
        out
    }
}

pub fn parse_turn_event(event: &str, data: &str) -> Option<TurnEvent> {
    let v: Value = serde_json::from_str(data).unwrap_or(Value::Null);
    let s = |k: &str| v.get(k).and_then(Value::as_str).unwrap_or("").to_string();
    Some(match event {
        "status" => TurnEvent::Status(format!(
            "{} の処理を待っています（{} 秒）",
            v.get("holder").and_then(Value::as_str).unwrap_or("?"),
            v.get("waited_s").and_then(Value::as_u64).unwrap_or(0)
        )),
        "thinking" => TurnEvent::Thinking(s("text")),
        "token" => TurnEvent::Token(s("text")),
        "tool_call" => TurnEvent::ToolCall(ToolCall { id: s("id"), name: s("name"), arguments: s("arguments") }),
        "done" => {
            let usage = v.get("usage").cloned().unwrap_or(Value::Null);
            TurnEvent::Done {
                finish_reason: s("finish_reason"),
                input_tokens: usage.get("input_tokens").and_then(Value::as_u64).unwrap_or(0),
                output_tokens: usage.get("output_tokens").and_then(Value::as_u64).unwrap_or(0),
            }
        }
        "error" => TurnEvent::Error { message: s("message"), code: s("code") },
        _ => return None,
    })
}

/// Fold the events of one turn. Tool calls only count when `done` arrived: a cut stream runs nothing.
pub fn fold_events(events: &[TurnEvent]) -> Result<TurnReply, HostError> {
    let mut reply = TurnReply::default();
    let mut done = false;
    for event in events {
        match event {
            TurnEvent::Token(t) => reply.text.push_str(t),
            TurnEvent::ToolCall(c) => reply.tool_calls.push(c.clone()),
            TurnEvent::Done { finish_reason, input_tokens, output_tokens } => {
                done = true;
                reply.finish_reason = finish_reason.clone();
                reply.input_tokens = *input_tokens;
                reply.output_tokens = *output_tokens;
            }
            TurnEvent::Error { message, code } => {
                return Err(if code == "context_overflow" {
                    HostError::ContextOverflow(message.clone())
                } else {
                    HostError::Refused { message: message.clone(), code: code.clone() }
                });
            }
            TurnEvent::Status(_) | TurnEvent::Thinking(_) => {}
        }
    }
    if !done {
        return Err(HostError::Unreachable("応答の途中で切れました（ツールは実行しません）".into()));
    }
    Ok(reply)
}

/// The final state of a graph run, and the images of its last AI message.
#[derive(Debug, Clone, Default)]
pub struct GraphResult {
    pub text: String,
    pub images: Vec<(String, String)>, // (mime, base64)
    pub interrupted: bool,
    pub error: Option<String>,
}

/// The last AI message of a `values` event: its text and image blocks.
pub fn last_ai(values: &Value) -> GraphResult {
    let mut out = GraphResult::default();
    if values.get("__interrupt__").map(|v| v.as_array().is_some_and(|a| !a.is_empty())).unwrap_or(false) {
        out.interrupted = true;
    }
    let Some(messages) = values.get("messages").and_then(Value::as_array) else { return out };
    let Some(last) = messages.iter().rev().find(|m| m.get("type").and_then(Value::as_str) == Some("ai")) else {
        return out;
    };
    match last.get("content") {
        Some(Value::String(s)) => out.text = s.clone(),
        Some(Value::Array(blocks)) => {
            for b in blocks {
                match b.get("type").and_then(Value::as_str) {
                    Some("text") => {
                        if !out.text.is_empty() {
                            out.text.push('\n');
                        }
                        out.text.push_str(b.get("text").and_then(Value::as_str).unwrap_or(""));
                    }
                    Some("image") => {
                        let mime = b
                            .get("mimeType")
                            .or_else(|| b.get("mime_type"))
                            .and_then(Value::as_str)
                            .unwrap_or("image/png");
                        if let Some(data) = b.get("data").or_else(|| b.get("base64")).and_then(Value::as_str) {
                            out.images.push((mime.to_string(), data.to_string()));
                        }
                    }
                    _ => {}
                }
            }
        }
        _ => {}
    }
    out
}

#[derive(Clone)]
pub struct HostClient {
    pub base: String,
    http: reqwest::Client,
    auth: Option<(String, String)>,
    idle: Duration,
}

impl HostClient {
    pub fn new(config: &Config) -> HostClient {
        let http = reqwest::Client::builder()
            .connect_timeout(Duration::from_secs(10))
            .no_proxy()
            .build()
            .expect("http client");
        HostClient {
            base: config.host.clone(),
            http,
            auth: config.auth_header.clone(),
            idle: Duration::from_secs(config.idle_timeout_s),
        }
    }

    fn req(&self, method: reqwest::Method, path: &str) -> reqwest::RequestBuilder {
        let mut r = self.http.request(method, format!("{}{}", self.base, path));
        if let Some((name, value)) = &self.auth {
            r = r.header(name.as_str(), value.as_str());
        }
        r
    }

    pub async fn health(&self) -> Health {
        let mut h = Health::default();
        match self.req(reqwest::Method::GET, "/ok").timeout(Duration::from_secs(8)).send().await {
            Ok(r) if r.status().is_success() => h.ok = true,
            Ok(r) => {
                h.error = Some(format!("/ok が HTTP {}", r.status()));
                return h;
            }
            Err(e) => {
                h.error = Some(e.to_string());
                return h;
            }
        }
        match self.req(reqwest::Method::GET, "/coder/health").timeout(Duration::from_secs(15)).send().await {
            Ok(r) if r.status().is_success() => {
                if let Ok(v) = r.json::<Value>().await {
                    h.gate = v.get("gate").and_then(Value::as_str) == Some("coder");
                    h.lmstudio = v.get("lmstudio").and_then(Value::as_bool).unwrap_or(false);
                    h.context = v.get("context").and_then(Value::as_u64).unwrap_or(4096) as u32;
                    h.model = v.get("model").and_then(Value::as_str).unwrap_or("").to_string();
                    h.busy = v.get("busy").and_then(Value::as_str).map(String::from);
                }
            }
            Ok(r) => h.error = Some(format!("/coder/health が HTTP {}（ホストの local-agent が古い可能性）", r.status())),
            Err(e) => h.error = Some(e.to_string()),
        }
        h
    }

    /// One model turn. `on_event` sees every event as it arrives (tokens are printed while they stream);
    /// `cancelled` is polled so Ctrl-C ends the turn.
    pub async fn coder_turn(
        &self,
        request: &TurnRequest,
        on_event: &mut (dyn FnMut(&TurnEvent) + Send),
        cancelled: &(dyn Fn() -> bool + Sync),
    ) -> Result<TurnReply, HostError> {
        let response = self
            .req(reqwest::Method::POST, "/coder/turn")
            .header("Accept", "text/event-stream")
            .json(request)
            .send()
            .await
            .map_err(|e| HostError::Unreachable(e.to_string()))?;
        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(HostError::Refused { message: format!("HTTP {status}: {}", body.chars().take(300).collect::<String>()), code: "http".into() });
        }
        let mut stream = response.bytes_stream();
        let mut parser = SseParser::default();
        let mut events = Vec::new();
        let mut last_activity = std::time::Instant::now();
        loop {
            if cancelled() {
                return Err(HostError::Interrupted);
            }
            let next = tokio::time::timeout(Duration::from_millis(250), stream.next()).await;
            let chunk = match next {
                Err(_) => {
                    // No bytes for 250 ms: check the idle limit, then wait again.
                    if last_activity.elapsed() > self.idle {
                        return Err(HostError::Unreachable(format!("{} 秒間応答がありません", self.idle.as_secs())));
                    }
                    continue;
                }
                Ok(None) => break,
                Ok(Some(Err(e))) => return Err(HostError::Unreachable(e.to_string())),
                Ok(Some(Ok(bytes))) => bytes,
            };
            last_activity = std::time::Instant::now();
            for (event, data) in parser.feed(&String::from_utf8_lossy(&chunk)) {
                if let Some(ev) = parse_turn_event(&event, &data) {
                    on_event(&ev);
                    events.push(ev);
                }
            }
        }
        fold_events(&events)
    }

    /// Run a graph to the end (`stream_mode: values`); `on_progress` sees the last AI message text as it changes.
    pub async fn run_graph(
        &self,
        assistant_id: &str,
        content: Value,
        configurable: Value,
        timeout: Duration,
        on_progress: &mut (dyn FnMut(&str) + Send),
        cancelled: &(dyn Fn() -> bool + Sync),
    ) -> Result<GraphResult, HostError> {
        let body = json!({
            "assistant_id": assistant_id,
            "input": {"messages": [{"type": "human", "content": content}]},
            "config": {"configurable": configurable},
            "stream_mode": ["values"],
            "on_disconnect": "cancel",
        });
        let response = self
            .req(reqwest::Method::POST, "/runs/stream")
            .header("Accept", "text/event-stream")
            .json(&body)
            .send()
            .await
            .map_err(|e| HostError::Unreachable(e.to_string()))?;
        if !response.status().is_success() {
            let status = response.status();
            let text = response.text().await.unwrap_or_default();
            return Err(HostError::Refused { message: format!("HTTP {status}: {}", text.chars().take(300).collect::<String>()), code: "http".into() });
        }
        let started = tokio::time::Instant::now();
        let mut stream = response.bytes_stream();
        let mut parser = SseParser::default();
        let mut last = GraphResult::default();
        let mut shown = String::new();
        loop {
            if cancelled() {
                return Err(HostError::Interrupted);
            }
            if started.elapsed() > timeout {
                return Err(HostError::Unreachable(format!("{} 秒で打ち切りました", timeout.as_secs())));
            }
            let chunk = match tokio::time::timeout(Duration::from_millis(250), stream.next()).await {
                Err(_) => continue,
                Ok(None) => break,
                Ok(Some(Err(e))) => return Err(HostError::Unreachable(e.to_string())),
                Ok(Some(Ok(b))) => b,
            };
            for (event, data) in parser.feed(&String::from_utf8_lossy(&chunk)) {
                match event.as_str() {
                    "values" => {
                        if let Ok(v) = serde_json::from_str::<Value>(&data) {
                            let r = last_ai(&v);
                            if r.text != shown {
                                shown = r.text.clone();
                                on_progress(&shown);
                            }
                            last = r;
                        }
                    }
                    "error" => {
                        last.error = Some(data.chars().take(500).collect());
                    }
                    _ => {}
                }
            }
        }
        Ok(last)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sse_parser_handles_split_chunks() {
        let mut p = SseParser::default();
        let mut got = p.feed("event: token\ndata: {\"text\":\"he");
        assert!(got.is_empty());
        got = p.feed("llo\"}\n\nevent: done\r\ndata: {}\r\n\r\n");
        assert_eq!(got, vec![("token".into(), "{\"text\":\"hello\"}".into()), ("done".into(), "{}".into())]);
    }

    #[test]
    fn fold_requires_done_before_tools_count() {
        let call = TurnEvent::ToolCall(ToolCall { id: "1".into(), name: "grep".into(), arguments: "{}".into() });
        let cut = fold_events(&[TurnEvent::Token("a".into()), call.clone()]);
        assert!(matches!(cut, Err(HostError::Unreachable(_))));
        let ok = fold_events(&[
            TurnEvent::Token("a".into()),
            call,
            TurnEvent::Done { finish_reason: "tool_calls".into(), input_tokens: 1, output_tokens: 2 },
        ])
        .unwrap();
        assert_eq!(ok.tool_calls.len(), 1);
        assert_eq!(ok.text, "a");
        let overflow = fold_events(&[TurnEvent::Error { message: "m".into(), code: "context_overflow".into() }]);
        assert!(matches!(overflow, Err(HostError::ContextOverflow(_))));
    }

    #[test]
    fn parse_events() {
        assert_eq!(parse_turn_event("token", r#"{"text":"x"}"#), Some(TurnEvent::Token("x".into())));
        assert!(matches!(parse_turn_event("status", r#"{"holder":"image","waited_s":5}"#), Some(TurnEvent::Status(s)) if s.contains("image")));
        assert_eq!(parse_turn_event("metadata", "{}"), None);
    }

    #[test]
    fn last_ai_reads_text_and_images() {
        let v = json!({"messages": [
            {"type": "human", "content": "x"},
            {"type": "ai", "content": [{"type": "text", "text": "done"},
                                       {"type": "image", "mimeType": "image/png", "data": "QUJD"}]}
        ]});
        let r = last_ai(&v);
        assert_eq!(r.text, "done");
        assert_eq!(r.images, vec![("image/png".to_string(), "QUJD".to_string())]);
        let s = last_ai(&json!({"messages": [{"type": "ai", "content": "answer [1]"}], "__interrupt__": [{"value": 1}]}));
        assert_eq!(s.text, "answer [1]");
        assert!(s.interrupted);
    }

    #[test]
    fn msg_serializes_like_openai() {
        let m = Msg::assistant("", vec![ToolCall { id: "c".into(), name: "grep".into(), arguments: "{}".into() }]);
        let v = serde_json::to_value(&m).unwrap();
        assert_eq!(v["tool_calls"][0]["name"], "grep");
        assert!(v.get("tool_call_id").is_none());
        let t = serde_json::to_value(Msg::tool("c", "out")).unwrap();
        assert_eq!(t["tool_call_id"], "c");
    }
}
