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

/// Environment for a shell command so that the programs it starts write UTF-8 to the pipe. A variable the user set
/// is left alone. Windows: Python writes a pipe in the ANSI code page (cp932 on Japanese Windows), which came back
/// as mojibake and made it raise on characters outside the code page; in a terminal it writes UTF-8 anyway.
/// Elsewhere: a session without any locale (a bare ssh, a service) gets a UTF-8 character type.
pub fn command_env() -> Vec<(&'static str, &'static str)> {
    let unset = |name: &str| std::env::var_os(name).is_none_or(|v| v.is_empty());
    let mut env = Vec::new();
    if cfg!(windows) {
        if unset("PYTHONIOENCODING") {
            env.push(("PYTHONIOENCODING", "utf-8"));
        }
    } else if unset("LC_ALL") && unset("LC_CTYPE") && unset("LANG") {
        env.push(("LC_CTYPE", if cfg!(target_os = "macos") { "UTF-8" } else { "C.UTF-8" }));
    }
    env
}

/// Command output as text, line by line: UTF-8 where it is valid UTF-8, otherwise (Windows) the ANSI code page
/// that programs use for a pipe, so one tool's cp932 line does not turn the UTF-8 lines around it into mojibake.
pub fn decode_output(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len());
    for line in bytes.split_inclusive(|&b| b == b'\n') {
        match std::str::from_utf8(line) {
            Ok(text) => out.push_str(text),
            Err(_) => out.push_str(&decode_legacy(line)),
        }
    }
    out
}

#[cfg(windows)]
fn decode_legacy(bytes: &[u8]) -> String {
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn GetACP() -> u32;
        fn MultiByteToWideChar(code_page: u32, flags: u32, src: *const u8, src_len: i32, dst: *mut u16, dst_len: i32)
        -> i32;
    }
    let Ok(len) = i32::try_from(bytes.len()) else { return String::from_utf8_lossy(bytes).into_owned() };
    if len == 0 {
        return String::new();
    }
    // SAFETY: the pointers and lengths describe live buffers; the first call only measures the output.
    unsafe {
        let code_page = GetACP();
        let need = MultiByteToWideChar(code_page, 0, bytes.as_ptr(), len, std::ptr::null_mut(), 0);
        if need <= 0 {
            return String::from_utf8_lossy(bytes).into_owned();
        }
        let mut wide = vec![0u16; need as usize];
        let got = MultiByteToWideChar(code_page, 0, bytes.as_ptr(), len, wide.as_mut_ptr(), need);
        if got <= 0 {
            return String::from_utf8_lossy(bytes).into_owned();
        }
        String::from_utf16_lossy(&wide[..got as usize])
    }
}

#[cfg(not(windows))]
fn decode_legacy(bytes: &[u8]) -> String {
    String::from_utf8_lossy(bytes).into_owned()
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
    fn utf8_output_is_kept_as_is() {
        assert_eq!(decode_output("こんにちは\n🐺 ok\r\nlast".as_bytes()), "こんにちは\n🐺 ok\r\nlast");
        assert_eq!(decode_output(b""), "");
    }

    #[test]
    fn a_legacy_line_does_not_spoil_the_utf8_lines() {
        // "あ" in cp932 between two UTF-8 lines (a tool that ignores PYTHONIOENCODING, e.g. a Windows program).
        let mut bytes = "前の行\n".as_bytes().to_vec();
        bytes.extend_from_slice(b"\x82\xa0\n");
        bytes.extend_from_slice("後の行\n".as_bytes());
        let text = decode_output(&bytes);
        assert!(text.starts_with("前の行\n") && text.ends_with("後の行\n"), "{text}");
        #[cfg(windows)]
        {
            #[link(name = "kernel32")]
            unsafe extern "system" {
                fn GetACP() -> u32;
            }
            // SAFETY: no arguments, returns the system's ANSI code page.
            if unsafe { GetACP() } == 932 {
                assert_eq!(text, "前の行\nあ\n後の行\n");
            }
        }
    }

    #[test]
    fn command_env_asks_for_utf8_without_overriding_the_user() {
        let env = command_env();
        if cfg!(windows) {
            if std::env::var_os("PYTHONIOENCODING").is_none() {
                assert!(env.contains(&("PYTHONIOENCODING", "utf-8")));
            }
        } else {
            assert!(env.iter().all(|(k, _)| *k == "LC_CTYPE"));
            if std::env::var_os("LANG").is_some() || std::env::var_os("LC_ALL").is_some() {
                assert!(env.is_empty());
            }
        }
    }

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
