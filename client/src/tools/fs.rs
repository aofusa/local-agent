//! list_dir, glob, read_file, edit_file, write_file.

use std::path::Path;

use globset::GlobBuilder;
use serde_json::Value;

use super::{Planned, ToolCtx, ToolOutput, UndoEntry, arg_str, arg_u64};
use crate::workspace::walker;

const LIST_LIMIT: usize = 200;
const READ_DEFAULT: usize = 400;
const READ_MAX: usize = 2000;

fn is_binary(bytes: &[u8]) -> bool {
    bytes.iter().take(8192).any(|b| *b == 0)
}

pub fn list_dir(ctx: &ToolCtx, args: &Value) -> ToolOutput {
    let raw = arg_str(args, "path").unwrap_or(".");
    let dir = match ctx.ws.resolve(if raw.trim().is_empty() { "." } else { raw }) {
        Ok(p) => p,
        Err(e) => return ToolOutput::err(e.to_string()),
    };
    if !dir.is_dir() {
        return ToolOutput::err(format!("ディレクトリではありません: {raw}"));
    }
    let depth = arg_u64(args, "depth").unwrap_or(1).clamp(1, 2) as usize;
    let mut lines = Vec::new();
    let mut more = 0usize;
    for entry in walker(&dir, Some(depth)).sort_by_file_name(|a, b| a.cmp(b)).build().flatten() {
        if entry.depth() == 0 {
            continue;
        }
        if lines.len() >= LIST_LIMIT {
            more += 1;
            continue;
        }
        let rel = crate::platform::display_rel(&dir, entry.path());
        let is_dir = entry.file_type().is_some_and(|t| t.is_dir());
        lines.push(if is_dir { format!("{rel}/") } else { rel });
    }
    if lines.is_empty() {
        return ToolOutput::ok("（空のディレクトリ）");
    }
    if more > 0 {
        lines.push(format!("…ほか {more} 件"));
    }
    ToolOutput::ok(lines.join("\n"))
}

pub fn glob(ctx: &ToolCtx, args: &Value) -> ToolOutput {
    let Some(pattern) = arg_str(args, "pattern").map(str::trim).filter(|p| !p.is_empty()) else {
        return ToolOutput::err("pattern がありません");
    };
    let pattern = pattern.trim_start_matches("./");
    let matcher = match GlobBuilder::new(pattern).literal_separator(true).build() {
        Ok(g) => g.compile_matcher(),
        Err(e) => return ToolOutput::err(format!("glob を読めません: {e}")),
    };
    let mut found = Vec::new();
    let mut more = 0usize;
    for entry in walker(&ctx.ws.root, None).build().flatten() {
        if !entry.file_type().is_some_and(|t| t.is_file()) {
            continue;
        }
        let rel = ctx.ws.rel(entry.path());
        if matcher.is_match(&rel) || (!pattern.contains('/') && matcher.is_match(entry.file_name())) {
            if found.len() < LIST_LIMIT {
                found.push(rel);
            } else {
                more += 1;
            }
        }
    }
    found.sort();
    if found.is_empty() {
        return ToolOutput::ok("一致するファイルはありません");
    }
    if more > 0 {
        found.push(format!("…ほか {more} 件"));
    }
    ToolOutput::ok(found.join("\n"))
}

pub fn read_file(ctx: &mut ToolCtx, args: &Value) -> ToolOutput {
    let Some(raw) = arg_str(args, "path") else { return ToolOutput::err("path がありません") };
    let path = match ctx.ws.resolve(raw) {
        Ok(p) => p,
        Err(e) => return ToolOutput::err(e.to_string()),
    };
    if path.is_dir() {
        return ToolOutput::err(format!("ディレクトリです（list_dir を使ってください）: {raw}"));
    }
    let bytes = match std::fs::read(&path) {
        Ok(b) => b,
        Err(e) => return ToolOutput::err(format!("読めません: {raw}: {e}")),
    };
    if is_binary(&bytes) {
        let ext = path.extension().map(|e| e.to_string_lossy().to_string()).unwrap_or_default();
        return ToolOutput::ok(format!("バイナリファイルです（{} バイト、拡張子 {ext}）。内容は読みません", bytes.len()));
    }
    ctx.read_files.insert(path.clone());
    let text = String::from_utf8_lossy(&bytes);
    let lines: Vec<&str> = text.lines().collect();
    let offset = arg_u64(args, "offset").unwrap_or(1).max(1) as usize;
    let limit = (arg_u64(args, "limit").unwrap_or(READ_DEFAULT as u64) as usize).clamp(1, READ_MAX);
    if lines.is_empty() {
        return ToolOutput::ok("（空のファイル）");
    }
    if offset > lines.len() {
        return ToolOutput::err(format!("offset {offset} はファイルの行数 {} を超えています", lines.len()));
    }
    let end = (offset - 1 + limit).min(lines.len());
    let mut out: Vec<String> = lines[offset - 1..end]
        .iter()
        .enumerate()
        .map(|(i, l)| format!("{:>5}\t{}", offset + i, l))
        .collect();
    if end < lines.len() {
        out.push(format!("…（全 {} 行。続きは offset={}）", lines.len(), end + 1));
    }
    ToolOutput::ok(out.join("\n"))
}

pub fn unified_diff(rel: &str, before: &str, after: &str) -> String {
    let diff = similar::TextDiff::from_lines(before, after);
    let text = diff.unified_diff().context_radius(3).header(&format!("a/{rel}"), &format!("b/{rel}")).to_string();
    if text.trim().is_empty() { "（変更なし）".into() } else { text }
}

/// Replace exactly one occurrence of `old`. Keeps the file's CRLF line endings when the model wrote LF.
pub fn replace_unique(text: &str, old: &str, new: &str) -> Result<String, String> {
    if old.is_empty() {
        return Err("old が空です".into());
    }
    let crlf = text.contains("\r\n") && !old.contains('\r');
    let (old, new) = if crlf {
        (old.replace('\n', "\r\n"), new.replace("\r\n", "\n").replace('\n', "\r\n"))
    } else {
        (old.to_string(), new.to_string())
    };
    match text.matches(old.as_str()).count() {
        0 => Err("old がファイル内に見つかりません。read_file で読み直してください".into()),
        1 => Ok(text.replacen(old.as_str(), &new, 1)),
        n => Err(format!("old が {n} か所にあります。前後の行を含めて一意にしてください")),
    }
}

pub fn plan_edit(ctx: &ToolCtx, args: &Value) -> Planned {
    let (Some(raw), Some(old), Some(new)) = (arg_str(args, "path"), arg_str(args, "old"), arg_str(args, "new")) else {
        return Planned::Invalid(ToolOutput::err("path / old / new が要ります"));
    };
    let path = match ctx.ws.resolve(raw) {
        Ok(p) => p,
        Err(e) => return Planned::Invalid(ToolOutput::err(e.to_string())),
    };
    if !ctx.read_files.contains(&path) {
        return Planned::Invalid(ToolOutput::err(format!("先に read_file で {raw} を読んでください（未読のファイルは編集しません）")));
    }
    let before = match std::fs::read(&path) {
        Ok(b) => b,
        Err(e) => return Planned::Invalid(ToolOutput::err(format!("読めません: {raw}: {e}"))),
    };
    let text = String::from_utf8_lossy(&before).into_owned();
    match replace_unique(&text, old, new) {
        Ok(after) => {
            let rel = ctx.ws.rel(&path);
            let diff = unified_diff(&rel, &text, &after);
            Planned::FileChange { path, rel, after: after.into_bytes(), before: Some(before), diff }
        }
        Err(e) => Planned::Invalid(ToolOutput::err(e)),
    }
}

pub fn plan_write(ctx: &ToolCtx, args: &Value) -> Planned {
    let (Some(raw), Some(content)) = (arg_str(args, "path"), arg_str(args, "content")) else {
        return Planned::Invalid(ToolOutput::err("path / content が要ります"));
    };
    let path = match ctx.ws.resolve(raw) {
        Ok(p) => p,
        Err(e) => return Planned::Invalid(ToolOutput::err(e.to_string())),
    };
    if path.is_dir() {
        return Planned::Invalid(ToolOutput::err(format!("ディレクトリです: {raw}")));
    }
    let before = std::fs::read(&path).ok();
    if before.is_some() && !ctx.read_files.contains(&path) {
        return Planned::Invalid(ToolOutput::err(format!("{raw} は既にあります。置き換えるなら先に read_file で読んでください")));
    }
    let old_text = before.as_deref().map(|b| String::from_utf8_lossy(b).into_owned()).unwrap_or_default();
    // An existing CRLF file stays CRLF.
    let content = if old_text.contains("\r\n") && !content.contains('\r') {
        content.replace('\n', "\r\n")
    } else {
        content.to_string()
    };
    let rel = ctx.ws.rel(&path);
    let diff = unified_diff(&rel, &old_text, &content);
    Planned::FileChange { path, rel, after: content.into_bytes(), before, diff }
}

pub fn apply(ctx: &mut ToolCtx, path: &Path, rel: &str, after: Vec<u8>, before: Option<Vec<u8>>) -> ToolOutput {
    if let Some(dir) = path.parent() {
        if let Err(e) = std::fs::create_dir_all(dir) {
            return ToolOutput::err(format!("ディレクトリを作れません: {e}"));
        }
    }
    let created = before.is_none();
    let lines = after.iter().filter(|b| **b == b'\n').count();
    if let Err(e) = std::fs::write(path, &after) {
        return ToolOutput::err(format!("書けません: {rel}: {e}"));
    }
    ctx.undo.push(UndoEntry { path: path.to_path_buf(), before });
    ctx.read_files.insert(path.to_path_buf());
    ToolOutput::ok(if created { format!("{rel} を作りました（{lines} 行）") } else { format!("{rel} を更新しました") })
}

/// Undo the last agent edit: restore the old bytes, or delete a file the agent created.
pub fn undo_last(ctx: &mut ToolCtx) -> Result<String, String> {
    let entry = ctx.undo.pop().ok_or("戻せる編集がありません")?;
    let rel = ctx.ws.rel(&entry.path);
    match entry.before {
        Some(bytes) => std::fs::write(&entry.path, bytes).map(|_| format!("{rel} を編集前に戻しました")),
        None => std::fs::remove_file(&entry.path).map(|_| format!("{rel}（新規作成）を削除しました")),
    }
    .map_err(|e| format!("{rel} を戻せません: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::Config;
    use crate::workspace::Workspace;
    use serde_json::json;
    use std::sync::Arc;
    use std::sync::atomic::AtomicBool;

    fn ctx() -> (ToolCtx, std::path::PathBuf) {
        let dir = std::env::temp_dir().join(format!("cirka-fs-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(dir.join("src")).unwrap();
        std::fs::write(dir.join("src/main.rs"), "fn main() {\n    println!(\"hi\");\n}\n").unwrap();
        std::fs::write(dir.join("dos.txt"), "a\r\nb\r\nc\r\n").unwrap();
        std::fs::write(dir.join("twice.txt"), "x\nx\n").unwrap();
        std::fs::write(dir.join("bin.dat"), [0u8, 1, 2, 3]).unwrap();
        std::fs::write(dir.join(".gitignore"), "ignored/\n").unwrap();
        std::fs::create_dir_all(dir.join("ignored")).unwrap();
        std::fs::write(dir.join("ignored/x.rs"), "fn x() {}").unwrap();
        let ws = Workspace::new(&dir).unwrap();
        (ToolCtx::new(ws, Config::default(), None, Arc::new(AtomicBool::new(false))), dir)
    }

    #[test]
    fn edit_requires_a_read_and_a_unique_match() {
        let (mut c, dir) = ctx();
        let args = json!({"path": "src/main.rs", "old": "hi", "new": "hello"});
        assert!(matches!(plan_edit(&c, &args), Planned::Invalid(o) if o.content.contains("read_file")));
        read_file(&mut c, &json!({"path": "src/main.rs"}));
        let Planned::FileChange { path, rel, after, before, diff } = plan_edit(&c, &args) else { panic!() };
        assert!(diff.contains("-    println!(\"hi\");") && diff.contains("+    println!(\"hello\");"));
        // Nothing is written before apply.
        assert!(std::fs::read_to_string(&path).unwrap().contains("\"hi\""));
        apply(&mut c, &path, &rel, after, before);
        assert!(std::fs::read_to_string(&path).unwrap().contains("hello"));
        read_file(&mut c, &json!({"path": "twice.txt"}));
        assert!(matches!(plan_edit(&c, &json!({"path": "twice.txt", "old": "x", "new": "y"})),
                         Planned::Invalid(o) if o.content.contains("2 か所")));
        assert!(matches!(plan_edit(&c, &json!({"path": "twice.txt", "old": "zzz", "new": "y"})),
                         Planned::Invalid(o) if o.content.contains("見つかりません")));
        std::fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn crlf_is_kept() {
        assert_eq!(replace_unique("a\r\nb\r\nc\r\n", "a\nb", "a\nB").unwrap(), "a\r\nB\r\nc\r\n");
        assert_eq!(replace_unique("a\nb\n", "a\nb", "x").unwrap(), "x\n");
    }

    #[test]
    fn write_new_file_and_undo() {
        let (mut c, dir) = ctx();
        let Planned::FileChange { path, rel, after, before, .. } =
            plan_write(&c, &json!({"path": "docs/new.md", "content": "# t\n"})) else { panic!() };
        apply(&mut c, &path, &rel, after, before);
        assert!(dir.join("docs/new.md").exists());
        assert!(undo_last(&mut c).unwrap().contains("削除"));
        assert!(!dir.join("docs/new.md").exists());
        // Replacing an existing file needs a read first.
        assert!(matches!(plan_write(&c, &json!({"path": "src/main.rs", "content": "x"})), Planned::Invalid(_)));
        std::fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn read_binary_and_ranges() {
        let (mut c, dir) = ctx();
        assert!(read_file(&mut c, &json!({"path": "bin.dat"})).content.contains("バイナリ"));
        let out = read_file(&mut c, &json!({"path": "src/main.rs", "offset": 2, "limit": 1}));
        assert_eq!(out.content.lines().next().unwrap(), "    2\t    println!(\"hi\");");
        assert!(out.content.contains("offset=3"));
        assert!(!read_file(&mut c, &json!({"path": "../x"})).ok);
        std::fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn glob_and_list_honour_gitignore() {
        let (c, dir) = ctx();
        let out = glob(&c, &json!({"pattern": "**/*.rs"}));
        assert_eq!(out.content, "src/main.rs");
        let list = list_dir(&c, &json!({"path": "."}));
        assert!(list.content.contains("src/") && !list.content.contains("ignored"));
        std::fs::remove_dir_all(dir).ok();
    }
}
