//! Where files live on each OS, and which shell runs `bash` tool commands.

use std::path::{Path, PathBuf};

use crate::config::ShellKind;

/// `%APPDATA%\cirka\config.toml` on Windows, `$XDG_CONFIG_HOME/cirka/config.toml` (or `~/.config/...`) elsewhere.
pub fn user_config_path() -> Option<PathBuf> {
    config_dir().map(|d| d.join("cirka").join("config.toml"))
}

fn config_dir() -> Option<PathBuf> {
    if cfg!(windows) {
        return dirs::config_dir();
    }
    std::env::var_os("XDG_CONFIG_HOME")
        .map(PathBuf::from)
        .filter(|p| p.is_absolute())
        .or_else(|| dirs::home_dir().map(|h| h.join(".config")))
}

/// `%LOCALAPPDATA%\cirka` on Windows, `$XDG_DATA_HOME/cirka` (or `~/.local/share/cirka`) elsewhere.
pub fn data_dir() -> PathBuf {
    let base = if cfg!(windows) {
        dirs::data_local_dir()
    } else {
        std::env::var_os("XDG_DATA_HOME")
            .map(PathBuf::from)
            .filter(|p| p.is_absolute())
            .or_else(|| dirs::home_dir().map(|h| h.join(".local").join("share")))
    };
    base.unwrap_or_else(std::env::temp_dir).join("cirka")
}

pub fn sessions_dir() -> PathBuf {
    data_dir().join("sessions")
}

/// The first executable called `name` on PATH (PATHEXT is tried on Windows).
pub fn which(name: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    let exts: Vec<String> = if cfg!(windows) {
        std::env::var("PATHEXT")
            .unwrap_or_else(|_| ".EXE;.CMD;.BAT".into())
            .split(';')
            .map(|s| s.to_ascii_lowercase())
            .collect()
    } else {
        vec![String::new()]
    };
    for dir in std::env::split_paths(&path) {
        for ext in &exts {
            let candidate = dir.join(format!("{name}{ext}"));
            if candidate.is_file() {
                return Some(candidate);
            }
        }
    }
    None
}

/// The resolved shell: Windows `auto` is PowerShell 7 when installed, else Windows PowerShell; elsewhere `sh -lc`.
pub fn resolve_shell(kind: ShellKind) -> ShellKind {
    match kind {
        ShellKind::Auto if cfg!(windows) => {
            if which("pwsh").is_some() {
                ShellKind::Pwsh
            } else {
                ShellKind::Powershell
            }
        }
        ShellKind::Auto => ShellKind::Sh,
        other => other,
    }
}

/// Program and arguments that run `command` with the shell. The command is one argument, never split here.
pub fn shell_argv(kind: ShellKind, command: &str) -> (String, Vec<String>) {
    let c = command.to_string();
    match kind {
        ShellKind::Pwsh => ("pwsh".into(), vec!["-NoProfile".into(), "-NonInteractive".into(), "-Command".into(), c]),
        ShellKind::Powershell | ShellKind::Auto => (
            "powershell".into(),
            vec!["-NoProfile".into(), "-NonInteractive".into(), "-Command".into(), c],
        ),
        ShellKind::Cmd => ("cmd".into(), vec!["/D".into(), "/S".into(), "/C".into(), c]),
        ShellKind::Bash => ("bash".into(), vec!["-lc".into(), c]),
        ShellKind::Sh => ("sh".into(), vec!["-lc".into(), c]),
    }
}

pub fn shell_label(kind: ShellKind) -> &'static str {
    match kind {
        ShellKind::Pwsh => "pwsh",
        ShellKind::Powershell | ShellKind::Auto => "powershell",
        ShellKind::Cmd => "cmd",
        ShellKind::Bash => "bash",
        ShellKind::Sh => "sh",
    }
}

pub fn os_label() -> &'static str {
    std::env::consts::OS
}

/// A path for display: forward slashes, relative to `root` when inside it.
pub fn display_rel(root: &Path, path: &Path) -> String {
    path.strip_prefix(root).unwrap_or(path).to_string_lossy().replace('\\', "/")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shell_argv_table() {
        assert_eq!(shell_argv(ShellKind::Sh, "ls -la").1, vec!["-lc", "ls -la"]);
        assert_eq!(shell_argv(ShellKind::Bash, "a && b").0, "bash");
        let (p, a) = shell_argv(ShellKind::Pwsh, "Get-ChildItem");
        assert_eq!(p, "pwsh");
        assert_eq!(a.last().unwrap(), "Get-ChildItem");
        assert_eq!(shell_argv(ShellKind::Cmd, "dir").1, vec!["/D", "/S", "/C", "dir"]);
    }

    #[test]
    fn auto_resolves_per_os() {
        let s = resolve_shell(ShellKind::Auto);
        if cfg!(windows) {
            assert!(matches!(s, ShellKind::Pwsh | ShellKind::Powershell));
        } else {
            assert_eq!(s, ShellKind::Sh);
        }
        assert_eq!(resolve_shell(ShellKind::Cmd), ShellKind::Cmd);
    }

    #[test]
    fn display_rel_uses_forward_slashes() {
        let root = Path::new("/w");
        assert_eq!(display_rel(root, &root.join("a").join("b.txt")), "a/b.txt");
    }
}
