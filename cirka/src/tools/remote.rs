//! Tools that run on the host: web_search (graph `chat`, Tor search) and image_generate (graph `agent`, ComfyUI).
//! The host serialises them with its job lock; cirka never runs two host jobs at once (one tool at a time).

use std::path::{Path, PathBuf};
use std::time::Duration;

use base64::Engine;
use serde_json::{Value, json};

use super::{ToolCtx, ToolOutput, arg_str};
use crate::host::{GraphResult, HostError};
use crate::ui::{Frontend, UiEvent};

pub const ROLES: &[&str] = &["character", "pose", "style", "base", "mask"];
pub const OUTPUT_DIR: &str = "cirka-outputs";
const MAX_REFERENCES: usize = 4;
const MAX_IMAGE_BYTES: u64 = 10 * 1024 * 1024;

fn mime_of(path: &Path) -> Option<&'static str> {
    match path.extension()?.to_string_lossy().to_ascii_lowercase().as_str() {
        "png" => Some("image/png"),
        "jpg" | "jpeg" => Some("image/jpeg"),
        "webp" => Some("image/webp"),
        _ => None,
    }
}

fn progress<'a>(ui: &'a mut dyn Frontend) -> impl FnMut(&str) + Send + 'a {
    move |text: &str| {
        let line = text.lines().find(|l| !l.trim().is_empty()).unwrap_or("").chars().take(100).collect::<String>();
        if line.is_empty() { ui.event(UiEvent::Tick) } else { ui.event(UiEvent::Status(line)) }
    }
}

pub async fn web_search(ctx: &ToolCtx, args: &Value, ui: &mut dyn Frontend) -> ToolOutput {
    let Some(query) = arg_str(args, "query").map(str::trim).filter(|q| !q.is_empty()) else {
        return ToolOutput::err("query がありません");
    };
    let Some(host) = ctx.host.clone() else { return ToolOutput::err("ホストに接続していません") };
    // task=search fixes the chat graph's kind: one explicit search, never its own control loop.
    let configurable = json!({"mode": ctx.config.mode.as_str(), "task": "search"});
    let cancel = ctx.cancel.clone();
    let cancelled = move || cancel.load(std::sync::atomic::Ordering::SeqCst);
    let mut on_progress = progress(ui);
    let result = host
        .run_graph("chat", json!(query), configurable, Duration::from_secs(ctx.config.search_timeout_s), &mut on_progress, &cancelled)
        .await;
    match result {
        Ok(GraphResult { error: Some(e), .. }) => ToolOutput::err(format!("ホストの検索が失敗しました: {e}")),
        Ok(r) if r.text.trim().is_empty() => ToolOutput::err("検索結果が空でした"),
        Ok(r) => ToolOutput { ok: !r.text.starts_with("⚠️"), content: r.text },
        Err(HostError::Interrupted) => ToolOutput::err("中断しました"),
        Err(e) => ToolOutput::err(e.to_string()),
    }
}

/// The references as the image tab's content blocks (role and strength in `metadata`, local-agent AGENTS.md).
pub fn reference_blocks(ctx: &ToolCtx, refs: &[Value]) -> Result<Vec<Value>, String> {
    if refs.len() > MAX_REFERENCES {
        return Err(format!("参照画像は {MAX_REFERENCES} 枚までです"));
    }
    let mut blocks = Vec::new();
    for r in refs {
        let raw = r.get("path").and_then(Value::as_str).ok_or("references の path がありません")?;
        let role = r.get("role").and_then(Value::as_str).unwrap_or("").trim().to_ascii_lowercase();
        if !ROLES.contains(&role.as_str()) {
            return Err(format!("role は {} のどれかです: {role}", ROLES.join(" / ")));
        }
        let path = ctx.ws.resolve(raw).map_err(|e| e.to_string())?;
        let mime = mime_of(&path).ok_or_else(|| format!("png / jpg / webp だけを送れます: {raw}"))?;
        let size = std::fs::metadata(&path).map_err(|e| format!("{raw}: {e}"))?.len();
        if size > MAX_IMAGE_BYTES {
            return Err(format!("{raw} は 10 MB を超えています"));
        }
        let bytes = std::fs::read(&path).map_err(|e| format!("{raw}: {e}"))?;
        let mut metadata = json!({"name": path.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default(),
                                  "role": role});
        if let Some(s) = r.get("strength").and_then(Value::as_f64) {
            metadata["strength"] = json!(s.clamp(0.0, 1.0));
        }
        blocks.push(json!({"type": "image", "mimeType": mime,
                           "data": base64::engine::general_purpose::STANDARD.encode(bytes), "metadata": metadata}));
    }
    Ok(blocks)
}

/// Write the returned images under `cirka-outputs/`; the model only gets the paths.
pub fn save_images(root: &Path, images: &[(String, String)], stamp: u64) -> Result<Vec<PathBuf>, String> {
    let dir = root.join(OUTPUT_DIR);
    std::fs::create_dir_all(&dir).map_err(|e| format!("{OUTPUT_DIR} を作れません: {e}"))?;
    let mut paths = Vec::new();
    for (n, (mime, data)) in images.iter().enumerate() {
        let ext = match mime.as_str() {
            "image/jpeg" => "jpg",
            "image/webp" => "webp",
            _ => "png",
        };
        let bytes = base64::engine::general_purpose::STANDARD
            .decode(data.trim())
            .map_err(|e| format!("画像を読めません: {e}"))?;
        let path = dir.join(format!("image-{stamp}-{}.{ext}", n + 1));
        std::fs::write(&path, bytes).map_err(|e| format!("{} に書けません: {e}", path.display()))?;
        paths.push(path);
    }
    Ok(paths)
}

pub async fn image_generate(ctx: &ToolCtx, args: &Value, ui: &mut dyn Frontend) -> ToolOutput {
    let Some(prompt) = arg_str(args, "prompt").map(str::trim).filter(|p| !p.is_empty()) else {
        return ToolOutput::err("prompt がありません");
    };
    let Some(host) = ctx.host.clone() else { return ToolOutput::err("ホストに接続していません") };
    let refs = args.get("references").and_then(Value::as_array).cloned().unwrap_or_default();
    let mut content = vec![json!({"type": "text", "text": prompt})];
    match reference_blocks(ctx, &refs) {
        Ok(blocks) => content.extend(blocks),
        Err(e) => return ToolOutput::err(e),
    }
    let cancel = ctx.cancel.clone();
    let cancelled = move || cancel.load(std::sync::atomic::Ordering::SeqCst);
    let result = {
        let mut on_progress = progress(ui);
        host.run_graph("agent", Value::Array(content), json!({}), Duration::from_secs(ctx.config.image_timeout_s),
                       &mut on_progress, &cancelled)
            .await
    };
    let result = match result {
        Ok(r) => r,
        Err(HostError::Interrupted) => return ToolOutput::err("中断しました"),
        Err(e) => return ToolOutput::err(e.to_string()),
    };
    if let Some(e) = result.error {
        return ToolOutput::err(format!("ホストの画像生成が失敗しました: {e}"));
    }
    if result.images.is_empty() {
        let why = if result.interrupted { "ホストが確認を求めて止まりました（役割をはっきり指定してください）" } else { "画像が返りませんでした" };
        return ToolOutput::err(format!("{why}: {}", result.text.chars().take(400).collect::<String>()));
    }
    let stamp = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
    match save_images(&ctx.ws.root, &result.images, stamp) {
        Ok(paths) => {
            let rels: Vec<String> = paths.iter().map(|p| ctx.ws.rel(p)).collect();
            ui.event(UiEvent::Info(format!("画像を保存しました: {}", rels.join(", "))));
            ToolOutput::ok(format!("画像を保存しました: {}", rels.join(", ")))
        }
        Err(e) => ToolOutput::err(e),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::Config;
    use crate::workspace::Workspace;
    use std::sync::Arc;
    use std::sync::atomic::AtomicBool;

    #[test]
    fn references_become_role_blocks() {
        let dir = std::env::temp_dir().join(format!("cirka-img-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("ref.png"), b"\x89PNG....").unwrap();
        std::fs::write(dir.join("notes.txt"), b"x").unwrap();
        let c = ToolCtx::new(Workspace::new(&dir).unwrap(), Config::default(), None, Arc::new(AtomicBool::new(false)));
        let blocks = reference_blocks(&c, &[json!({"path": "ref.png", "role": "Character", "strength": 1.7})]).unwrap();
        assert_eq!(blocks[0]["mimeType"], "image/png");
        assert_eq!(blocks[0]["metadata"]["role"], "character");
        assert_eq!(blocks[0]["metadata"]["strength"], 1.0);
        assert!(reference_blocks(&c, &[json!({"path": "ref.png", "role": "hero"})]).is_err());
        assert!(reference_blocks(&c, &[json!({"path": "notes.txt", "role": "base"})]).is_err());
        assert!(reference_blocks(&c, &[json!({"path": "../x.png", "role": "base"})]).is_err());
        let five: Vec<Value> = (0..5).map(|_| json!({"path": "ref.png", "role": "base"})).collect();
        assert!(reference_blocks(&c, &five).is_err());
        std::fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn images_are_saved_and_only_paths_return() {
        let dir = std::env::temp_dir().join(format!("cirka-save-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&dir).unwrap();
        let paths = save_images(&dir, &[("image/png".into(), "QUJD".into())], 42).unwrap();
        assert!(paths[0].ends_with("cirka-outputs/image-42-1.png") || paths[0].ends_with("cirka-outputs\\image-42-1.png"));
        assert_eq!(std::fs::read(&paths[0]).unwrap(), b"ABC");
        std::fs::remove_dir_all(dir).ok();
    }
}
