//! grep: a regex over the workspace's text files (gitignore-aware), with output limits.

use globset::GlobBuilder;
use regex::Regex;
use serde_json::Value;

use super::{ToolCtx, ToolOutput, arg_str};
use crate::workspace::{is_secret, walker};

const MAX_MATCHES: usize = 100;
const MAX_BYTES: usize = 32 * 1024;
const MAX_LINE: usize = 300;
const MAX_FILE: u64 = 4 * 1024 * 1024;

pub fn grep(ctx: &ToolCtx, args: &Value) -> ToolOutput {
    let Some(pattern) = arg_str(args, "pattern").filter(|p| !p.is_empty()) else {
        return ToolOutput::err("pattern がありません");
    };
    let re = match Regex::new(pattern) {
        Ok(r) => r,
        Err(e) => return ToolOutput::err(format!("正規表現を読めません: {e}")),
    };
    let start = match arg_str(args, "path").map(str::trim).filter(|p| !p.is_empty()) {
        Some(p) => match ctx.ws.resolve(p) {
            Ok(path) => path,
            Err(e) => return ToolOutput::err(e.to_string()),
        },
        None => ctx.ws.root.clone(),
    };
    let filter = match arg_str(args, "glob").map(str::trim).filter(|g| !g.is_empty()) {
        Some(g) => match GlobBuilder::new(g).build() {
            Ok(m) => Some((m.compile_matcher(), g.contains('/'))),
            Err(e) => return ToolOutput::err(format!("glob を読めません: {e}")),
        },
        None => None,
    };
    let mut out = String::new();
    let mut matches = 0usize;
    let mut truncated = false;
    'files: for entry in walker(&start, None).build().flatten() {
        if !entry.file_type().is_some_and(|t| t.is_file()) || is_secret(entry.path()) {
            continue;
        }
        let rel = ctx.ws.rel(entry.path());
        if let Some((m, full)) = &filter {
            let ok = if *full { m.is_match(&rel) } else { m.is_match(entry.file_name()) };
            if !ok {
                continue;
            }
        }
        if entry.metadata().map(|m| m.len() > MAX_FILE).unwrap_or(true) {
            continue;
        }
        let Ok(bytes) = std::fs::read(entry.path()) else { continue };
        if bytes.iter().take(8192).any(|b| *b == 0) {
            continue;
        }
        let text = String::from_utf8_lossy(&bytes);
        for (n, line) in text.lines().enumerate() {
            if !re.is_match(line) {
                continue;
            }
            let shown: String = line.trim_end().chars().take(MAX_LINE).collect();
            let row = format!("{rel}:{}: {shown}\n", n + 1);
            if matches >= MAX_MATCHES || out.len() + row.len() > MAX_BYTES {
                truncated = true;
                break 'files;
            }
            out.push_str(&row);
            matches += 1;
        }
    }
    if matches == 0 {
        return ToolOutput::ok("一致はありません");
    }
    if truncated {
        out.push_str(&format!("…（{matches} 件で打ち切り。pattern か path を絞ってください）"));
    }
    ToolOutput::ok(out.trim_end().to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::Config;
    use crate::workspace::Workspace;
    use serde_json::json;
    use std::sync::Arc;
    use std::sync::atomic::AtomicBool;

    #[test]
    fn finds_lines_and_skips_secrets_and_binaries() {
        let dir = std::env::temp_dir().join(format!("cirka-grep-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(dir.join("src")).unwrap();
        std::fs::write(dir.join("src/a.rs"), "fn main() {}\nfn helper() {}\n").unwrap();
        std::fs::write(dir.join("src/b.py"), "def main():\n    pass\n").unwrap();
        std::fs::write(dir.join(".env"), "main=secret\n").unwrap();
        std::fs::write(dir.join("blob.bin"), b"main\0\0").unwrap();
        let c = ToolCtx::new(Workspace::new(&dir).unwrap(), Config::default(), None, Arc::new(AtomicBool::new(false)));
        let out = grep(&c, &json!({"pattern": "main"}));
        assert!(out.content.contains("src/a.rs:1: fn main() {}") && out.content.contains("src/b.py:1: def main():"));
        assert!(!out.content.contains(".env") && !out.content.contains("blob.bin"));
        let only_py = grep(&c, &json!({"pattern": "main", "glob": "*.py"}));
        assert!(!only_py.content.contains("a.rs"));
        assert!(!grep(&c, &json!({"pattern": "("})).ok);
        assert_eq!(grep(&c, &json!({"pattern": "zzz"})).content, "一致はありません");
        std::fs::remove_dir_all(dir).ok();
    }
}
