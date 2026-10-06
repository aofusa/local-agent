//! The models the host lets cirka pick (`GET /models`, local-agent docs/host-model-selection-design.md §9).
//!
//! cirka only sends a catalog id: `/model <id>` puts it in `POST /coder/turn` (`inference_model`) and in the chat
//! graph's `configurable.inference_model` (web_search); `/image-model <id>` goes to the image graph's
//! `configurable.image_model`. The host checks the id again; nothing here knows where a model lives.

use serde_json::Value;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Inference,
    Image,
}

impl Kind {
    pub fn key(self) -> &'static str {
        match self {
            Kind::Inference => "inference",
            Kind::Image => "image",
        }
    }
    pub fn config_key(self) -> &'static str {
        match self {
            Kind::Inference => "inference_model",
            Kind::Image => "image_model",
        }
    }
    pub fn label(self) -> &'static str {
        match self {
            Kind::Inference => "推論モデル",
            Kind::Image => "画像モデル",
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Entry {
    pub id: String,
    pub label: String,
    pub available: bool,
    pub reason: String,
    /// Inference: the context window. Image: 0.
    pub context: u32,
    /// Inference: "context 8192 · 思考なし". Image: "SDXL · yiffInHell_yihVANTABLACK.safetensors".
    pub detail: String,
}

/// The ids cirka accepts before asking the host: lower-case letters, digits, '.', '-' (the host's catalog rule).
pub fn valid_id(id: &str) -> bool {
    let mut chars = id.chars();
    matches!(chars.next(), Some(c) if c.is_ascii_lowercase() || c.is_ascii_digit())
        && id.len() <= 64
        && chars.all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '.' || c == '-')
}

pub fn entries(models: &Value, kind: Kind) -> Vec<Entry> {
    models
        .get(kind.key())
        .and_then(Value::as_array)
        .map(|list| {
            list.iter()
                .map(|m| {
                    let s = |k: &str| m.get(k).and_then(Value::as_str).unwrap_or("").to_string();
                    let context = m.get("context").and_then(Value::as_u64).unwrap_or(0) as u32;
                    let detail = match kind {
                        Kind::Inference => {
                            let mut parts = vec![];
                            if context > 0 {
                                parts.push(format!("context {context}"));
                            }
                            if m.get("thinking").and_then(Value::as_bool) == Some(false) {
                                parts.push("思考なし".into());
                            }
                            parts.join(" · ")
                        }
                        Kind::Image => {
                            let family = m.get("family_label").and_then(Value::as_str).unwrap_or_else(|| {
                                m.get("family").and_then(Value::as_str).unwrap_or("")
                            });
                            [family.to_string(), s("ckpt_name")].iter().filter(|x| !x.is_empty()).cloned()
                                .collect::<Vec<_>>().join(" · ")
                        }
                    };
                    Entry {
                        id: s("id"),
                        label: s("label"),
                        available: m.get("available").and_then(Value::as_bool).unwrap_or(false),
                        reason: s("reason"),
                        context,
                        detail,
                    }
                })
                .collect()
        })
        .unwrap_or_default()
}

pub fn default_id(models: &Value, kind: Kind) -> String {
    models.get("defaults").and_then(|d| d.get(kind.key())).and_then(Value::as_str).unwrap_or("").to_string()
}

/// `/model <id>`: the entry for an exact id. A label, a part of an id or an unusable model is refused with the
/// reason; the caller keeps the current choice then.
pub fn pick(models: &Value, kind: Kind, id: &str) -> Result<Entry, String> {
    let id = id.trim();
    if !valid_id(id) {
        return Err(format!("{} の id は英小文字・数字・.・- です（{id}）。/{} で候補を出せます", kind.label(),
                           if kind == Kind::Inference { "model" } else { "image-model" }));
    }
    let list = entries(models, kind);
    match list.iter().find(|e| e.id == id) {
        Some(e) if e.available => Ok(e.clone()),
        Some(e) => Err(format!("{}（{}）は使えません: {}", e.label, e.id, e.reason)),
        None => {
            let close: Vec<&str> = list.iter().filter(|e| e.id.contains(id)).map(|e| e.id.as_str()).collect();
            let hint = if close.is_empty() { String::new() } else { format!("。id は正確に: {}", close.join(", ")) };
            Err(format!("{id} はホストのモデル一覧にありません{hint}"))
        }
    }
}

/// The label for an id, or the id itself while the list is unknown.
pub fn label_of(models: Option<&Value>, kind: Kind, id: &str) -> String {
    models
        .and_then(|m| entries(m, kind).into_iter().find(|e| e.id == id))
        .map(|e| e.label)
        .unwrap_or_else(|| id.to_string())
}

/// `/model` without an argument: the current model and every candidate (unusable ones with their reason).
pub fn describe(models: &Value, kind: Kind, current: Option<&str>) -> String {
    let default = default_id(models, kind);
    let active = current.filter(|c| !c.is_empty()).unwrap_or(&default).to_string();
    let source = if current.is_some_and(|c| !c.is_empty()) { "このセッションで指定" } else { "ホストの既定" };
    let mut out = vec![format!("{}: {}（{active}、{source}）", kind.label(), label_of(Some(models), kind, &active))];
    for e in entries(models, kind) {
        let mark = if e.id == active { "●" } else { "○" };
        let default_note = if e.id == default { "（既定）" } else { "" };
        let tail = if e.available { e.detail.clone() } else { format!("使えません: {}", e.reason) };
        out.push(format!("  {mark} {}{default_note}  {}  {tail}", e.id, e.label));
    }
    out.join("\n")
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn models() -> Value {
        json!({
            "defaults": {"inference": "qwen3.8-27b-abliterated", "image": "yiffinhell-vantablack"},
            "inference": [
                {"id": "qwen3.8-27b-abliterated", "label": "Qwen 3.8 27B abliterated", "available": true, "reason": "",
                 "context": 4096, "thinking": true},
                {"id": "bonsai-2-27b-abliterated", "label": "Bonsai 2 27B abliterated", "available": true, "reason": "",
                 "context": 8192, "thinking": false}
            ],
            "image": [
                {"id": "yiffinhell-vantablack", "label": "yiffInHell VANTABLACK", "family": "sdxl", "family_label": "SDXL",
                 "ckpt_name": "yiffInHell_yihVANTABLACK.safetensors", "available": true, "reason": ""},
                {"id": "wulver", "label": "Wulver (Krea 2)", "family": "krea2", "available": false,
                 "reason": "ComfyUI にファイルがありません: wulverKrea2_v05_fp8.safetensors"}
            ]
        })
    }

    #[test]
    fn exact_ids_only() {
        let m = models();
        assert_eq!(pick(&m, Kind::Inference, "bonsai-2-27b-abliterated").unwrap().context, 8192);
        let err = pick(&m, Kind::Inference, "bonsai").unwrap_err();
        assert!(err.contains("一覧にありません") && err.contains("bonsai-2-27b-abliterated"), "{err}");
        assert!(pick(&m, Kind::Inference, "Qwen 3.8 27B abliterated").is_err());
        assert!(pick(&m, Kind::Inference, "QWEN3.8-27B-ABLITERATED").is_err());
        assert!(pick(&m, Kind::Image, "qwen3.8-27b-abliterated").is_err()); // not an image model
    }

    #[test]
    fn unavailable_models_are_refused_with_the_reason() {
        let err = pick(&models(), Kind::Image, "wulver").unwrap_err();
        assert!(err.contains("使えません") && err.contains("wulverKrea2_v05_fp8"), "{err}");
    }

    #[test]
    fn describe_marks_the_current_and_the_default() {
        let m = models();
        let text = describe(&m, Kind::Inference, None);
        assert!(text.starts_with("推論モデル: Qwen 3.8 27B abliterated（qwen3.8-27b-abliterated、ホストの既定）"), "{text}");
        assert!(text.contains("● qwen3.8-27b-abliterated（既定）") && text.contains("思考なし"));
        let text = describe(&m, Kind::Image, Some("yiffinhell-vantablack"));
        assert!(text.contains("このセッションで指定") && text.contains("使えません: ComfyUI"));
    }

    #[test]
    fn ids_are_checked_locally() {
        assert!(valid_id("qwen3.8-27b-abliterated") && valid_id("wulver"));
        assert!(!valid_id("") && !valid_id("Wulver") && !valid_id("a b") && !valid_id("-x") && !valid_id("a/b"));
        assert_eq!(label_of(None, Kind::Image, "wulver"), "wulver");
        assert_eq!(label_of(Some(&models()), Kind::Image, "wulver"), "Wulver (Krea 2)");
    }
}
