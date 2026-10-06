//! The workspace boundary: every path a tool touches is inside the root cirka was started in, secrets are never
//! read, and secret-looking text is redacted before it leaves this machine.

use std::path::{Component, Path, PathBuf};
use std::sync::OnceLock;

use regex::Regex;

#[derive(Debug, Clone)]
pub struct Workspace {
    pub root: PathBuf,
    canonical: PathBuf,
}

#[derive(Debug, PartialEq, Eq)]
pub enum PathError {
    Outside(String),
    Secret(String),
    Invalid(String),
}

impl std::fmt::Display for PathError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            PathError::Outside(p) => write!(f, "ワークスペースの外は扱いません: {p}"),
            PathError::Secret(p) => write!(f, "秘密ファイルは読みません（必要なら利用者に聞いてください）: {p}"),
            PathError::Invalid(p) => write!(f, "パスを読めません: {p}"),
        }
    }
}

/// Lexical normalisation: `a/./b/../c` -> `a/c`; None when `..` climbs above the start.
fn normalize(path: &Path) -> Option<PathBuf> {
    let mut out = PathBuf::new();
    for part in path.components() {
        match part {
            Component::CurDir => {}
            Component::ParentDir => {
                if !out.pop() {
                    return None;
                }
            }
            Component::Normal(p) => out.push(p),
            Component::RootDir | Component::Prefix(_) => out.push(part.as_os_str()),
        }
    }
    Some(out)
}

fn strip_verbatim(p: PathBuf) -> PathBuf {
    // Windows canonicalize() returns \\?\C:\...; compare without the prefix.
    let s = p.to_string_lossy();
    if let Some(rest) = s.strip_prefix(r"\\?\") {
        if !rest.starts_with("UNC\\") {
            return PathBuf::from(rest);
        }
    }
    p
}

pub fn canonical(path: &Path) -> std::io::Result<PathBuf> {
    path.canonicalize().map(strip_verbatim)
}

impl Workspace {
    pub fn new(root: &Path) -> std::io::Result<Workspace> {
        let canonical = canonical(root)?;
        Ok(Workspace { root: canonical.clone(), canonical })
    }

    /// The absolute path of `raw` (relative to the root, or absolute inside it). Symlinks whose target is outside
    /// the root are refused, also for paths that do not exist yet (their nearest existing parent is checked).
    pub fn resolve(&self, raw: &str) -> Result<PathBuf, PathError> {
        let raw = raw.trim();
        if raw.is_empty() || raw.contains('\0') {
            return Err(PathError::Invalid(raw.into()));
        }
        let given = Path::new(raw);
        let joined = if given.is_absolute() { given.to_path_buf() } else { self.root.join(given) };
        let lexical = normalize(&joined).ok_or_else(|| PathError::Outside(raw.into()))?;
        // A relative path must stay under the root as written. An absolute one may name the root through a symlink
        // (macOS: /var -> /private/var, /tmp -> /private/tmp): it is judged by its real location below.
        if !given.is_absolute() && !starts_with_ci(&lexical, &self.canonical) {
            return Err(PathError::Outside(raw.into()));
        }
        // Follow symlinks of the deepest existing ancestor.
        let mut probe = lexical.clone();
        let mut tail = Vec::new();
        loop {
            match canonical(&probe) {
                Ok(real) => {
                    if !starts_with_ci(&real, &self.canonical) {
                        return Err(PathError::Outside(raw.into()));
                    }
                    let mut full = real;
                    for part in tail.iter().rev() {
                        full.push(part);
                    }
                    if is_secret(&full) {
                        return Err(PathError::Secret(self.rel(&full)));
                    }
                    return Ok(full);
                }
                Err(_) => {
                    let Some(name) = probe.file_name().map(|n| n.to_os_string()) else {
                        return Err(PathError::Invalid(raw.into()));
                    };
                    tail.push(name);
                    if !probe.pop() {
                        return Err(PathError::Invalid(raw.into()));
                    }
                }
            }
        }
    }

    pub fn rel(&self, path: &Path) -> String {
        crate::platform::display_rel(&self.root, path)
    }
}

fn starts_with_ci(path: &Path, root: &Path) -> bool {
    if cfg!(windows) {
        let p = path.to_string_lossy().to_lowercase();
        let r = root.to_string_lossy().to_lowercase();
        let r = r.trim_end_matches('\\');
        p == r || p.starts_with(&format!("{r}\\"))
    } else {
        path.starts_with(root)
    }
}

/// Files that are never read or sent to the host: env files, keys, credentials.
pub fn is_secret(path: &Path) -> bool {
    let name = path.file_name().map(|n| n.to_string_lossy().to_lowercase()).unwrap_or_default();
    if name == ".env" || (name.starts_with(".env.") && name != ".env.example" && name != ".env.sample") {
        return true;
    }
    if name.starts_with("id_rsa") || name.starts_with("id_ed25519") || name.starts_with("id_ecdsa") || name.starts_with("id_dsa") {
        return true;
    }
    if name.starts_with("credentials") || name == ".netrc" || name == ".pgpass" || name == ".git-credentials" {
        return true;
    }
    let ext = path.extension().map(|e| e.to_string_lossy().to_lowercase()).unwrap_or_default();
    matches!(ext.as_str(), "pem" | "key" | "p12" | "pfx" | "keystore" | "jks")
}

fn secret_patterns() -> &'static [Regex] {
    static PATTERNS: OnceLock<Vec<Regex>> = OnceLock::new();
    PATTERNS.get_or_init(|| {
        [
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(-----END [A-Z ]*PRIVATE KEY-----|$)",
            r"\bAKIA[0-9A-Z]{16}\b",
            r"\bgh[pousr]_[A-Za-z0-9]{30,}\b",
            r"\bsk-[A-Za-z0-9_-]{20,}\b",
            r"\bxox[abpr]-[A-Za-z0-9-]{10,}\b",
            r#"(?i)\b(api[_-]?key|secret|token|passw(or)?d|client[_-]?secret)\b(\s*[:=]\s*)["']?[^\s"']{6,}"#,
        ]
        .iter()
        .map(|p| Regex::new(p).expect("secret pattern"))
        .collect()
    })
}

/// Replace key-shaped text with `[redacted]` (not complete: cirka is for trusted networks only).
pub fn redact(text: &str) -> String {
    let mut out = text.to_string();
    for (n, re) in secret_patterns().iter().enumerate() {
        out = if n == 5 {
            re.replace_all(&out, "$1$3[redacted]").into_owned()
        } else {
            re.replace_all(&out, "[redacted]").into_owned()
        };
    }
    out
}

/// A directory walker that honours .gitignore, .ignore and .cirkaignore and skips the usual build output.
pub fn walker(root: &Path, max_depth: Option<usize>) -> ignore::WalkBuilder {
    let mut builder = ignore::WalkBuilder::new(root);
    builder
        .hidden(false)
        .git_ignore(true)
        .git_global(false)
        .git_exclude(true)
        .require_git(false)
        .add_custom_ignore_filename(".cirkaignore")
        .max_depth(max_depth)
        .filter_entry(|entry| {
            let name = entry.file_name().to_string_lossy();
            !matches!(
                name.as_ref(),
                ".git" | "target" | "node_modules" | "dist" | ".venv" | "__pycache__" | ".next" | "cirka-outputs"
            )
        });
    builder
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ws() -> (Workspace, PathBuf) {
        let dir = std::env::temp_dir().join(format!("cirka-ws-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(dir.join("src")).unwrap();
        std::fs::write(dir.join("src").join("main.rs"), "fn main() {}").unwrap();
        (Workspace::new(&dir).unwrap(), dir)
    }

    #[test]
    fn resolves_inside_and_refuses_outside() {
        let (w, dir) = ws();
        assert!(w.resolve("src/main.rs").unwrap().ends_with("main.rs"));
        assert!(w.resolve("./src/../src/main.rs").is_ok());
        assert!(w.resolve("src/new/file.txt").is_ok()); // not created yet
        assert!(matches!(w.resolve("../x"), Err(PathError::Outside(_))));
        assert!(matches!(w.resolve("src/../../x"), Err(PathError::Outside(_))));
        let outside = std::env::temp_dir().join("elsewhere.txt");
        assert!(matches!(w.resolve(&outside.to_string_lossy()), Err(PathError::Outside(_))));
        let inside_abs = dir.join("src").join("main.rs");
        assert!(w.resolve(&inside_abs.to_string_lossy()).is_ok());
        std::fs::remove_dir_all(dir).ok();
    }

    #[cfg(unix)]
    #[test]
    fn symlink_out_of_the_workspace_is_refused() {
        let (w, dir) = ws();
        std::os::unix::fs::symlink(std::env::temp_dir(), dir.join("link")).unwrap();
        assert!(matches!(w.resolve("link/x"), Err(PathError::Outside(_))));
        std::fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn secrets_are_refused() {
        let (w, dir) = ws();
        for name in [".env", ".env.local", "id_rsa", "server.pem", "credentials.json", "deploy.key"] {
            assert!(matches!(w.resolve(name), Err(PathError::Secret(_))), "{name}");
        }
        assert!(w.resolve(".env.example").is_ok());
        std::fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn redaction() {
        let text = "token = abcdef123456\nkey AKIAABCDEFGHIJKLMNOP\nplain line\npassword: \"hunter22\"";
        let out = redact(text);
        assert!(!out.contains("abcdef123456") && !out.contains("AKIAABCDEFGHIJKLMNOP") && !out.contains("hunter22"));
        assert!(out.contains("plain line") && out.contains("token = [redacted]"));
        let pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----";
        assert_eq!(redact(pem), "[redacted]");
    }
}
