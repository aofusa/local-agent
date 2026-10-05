//! Settings: defaults < user config < project `.cirka/config.toml` < environment < command line.
//!
//! The host (the LangGraph server of local-agent) is the one setting most people change: `cirka config set
//! host http://192.168.1.20:2024`, `CIRKA_HOST=...`, `--host ...` or `/host ...` inside a session.

use std::fmt;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::platform;

pub const DEFAULT_HOST: &str = "http://127.0.0.1:2024";
pub const KEYS: &[&str] = &[
    "host",
    "mode",
    "permission",
    "shell",
    "max_turns",
    "locale",
    "auth_header",
    "idle_timeout_s",
    "search_timeout_s",
    "image_timeout_s",
];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    Fast,
    Think,
    Auto,
}

impl Mode {
    pub fn parse(s: &str) -> Option<Mode> {
        match s.trim().to_ascii_lowercase().as_str() {
            "fast" => Some(Mode::Fast),
            "think" => Some(Mode::Think),
            "auto" => Some(Mode::Auto),
            _ => None,
        }
    }
    pub fn as_str(self) -> &'static str {
        match self {
            Mode::Fast => "fast",
            Mode::Think => "think",
            Mode::Auto => "auto",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Permission {
    Default,
    AcceptEdits,
    Plan,
    Bypass,
}

impl Permission {
    pub fn parse(s: &str) -> Option<Permission> {
        match s.trim().to_ascii_lowercase().replace('_', "-").as_str() {
            "default" => Some(Permission::Default),
            "accept-edits" | "acceptedits" => Some(Permission::AcceptEdits),
            "plan" => Some(Permission::Plan),
            "bypass" => Some(Permission::Bypass),
            _ => None,
        }
    }
    pub fn as_str(self) -> &'static str {
        match self {
            Permission::Default => "default",
            Permission::AcceptEdits => "accept-edits",
            Permission::Plan => "plan",
            Permission::Bypass => "bypass",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ShellKind {
    Auto,
    Bash,
    Sh,
    Pwsh,
    Powershell,
    Cmd,
}

impl ShellKind {
    pub fn parse(s: &str) -> Option<ShellKind> {
        match s.trim().to_ascii_lowercase().as_str() {
            "auto" => Some(ShellKind::Auto),
            "bash" => Some(ShellKind::Bash),
            "sh" => Some(ShellKind::Sh),
            "pwsh" => Some(ShellKind::Pwsh),
            "powershell" => Some(ShellKind::Powershell),
            "cmd" => Some(ShellKind::Cmd),
            _ => None,
        }
    }
}

/// One config file as written on disk (every key optional).
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
#[serde(default)]
pub struct FileConfig {
    pub host: Option<String>,
    pub mode: Option<String>,
    pub permission: Option<String>,
    pub shell: Option<String>,
    pub max_turns: Option<u32>,
    pub locale: Option<String>,
    pub auth_header: Option<String>,
    pub idle_timeout_s: Option<u64>,
    pub search_timeout_s: Option<u64>,
    pub image_timeout_s: Option<u64>,
}

#[derive(Debug, Clone)]
pub struct Config {
    pub host: String,
    pub mode: Mode,
    pub permission: Permission,
    pub shell: ShellKind,
    pub max_turns: u32,
    pub locale: String,
    /// `Name: value`, sent with every request when set. No auth scheme is decided yet (local-agent AGENTS.md).
    pub auth_header: Option<(String, String)>,
    /// A model turn with no event for this long is abandoned (the 27B can take minutes to load and prefill).
    pub idle_timeout_s: u64,
    pub search_timeout_s: u64,
    pub image_timeout_s: u64,
    /// Where each value came from, for `cirka config show`.
    pub sources: Vec<String>,
}

impl Default for Config {
    fn default() -> Self {
        Config {
            host: DEFAULT_HOST.into(),
            mode: Mode::Auto,
            permission: Permission::Default,
            shell: ShellKind::Auto,
            max_turns: 40,
            locale: "ja".into(),
            auth_header: None,
            idle_timeout_s: 900,
            search_timeout_s: 1500,
            image_timeout_s: 1500,
            sources: vec!["defaults".into()],
        }
    }
}

#[derive(Debug)]
pub struct ConfigError(pub String);

impl fmt::Display for ConfigError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for ConfigError {}

/// `http://host:port` without a trailing slash; anything else is refused.
pub fn normalize_host(raw: &str) -> Result<String, ConfigError> {
    let raw_trimmed = raw.trim();
    if raw_trimmed.is_empty() {
        return Err(ConfigError("接続先が空です".into()));
    }
    let with_scheme = if raw_trimmed.contains("://") { raw_trimmed.to_string() } else { format!("http://{raw_trimmed}") };
    let with_scheme = with_scheme.trim_end_matches('/').to_string();
    if !(with_scheme.starts_with("http://") || with_scheme.starts_with("https://")) {
        return Err(ConfigError(format!("接続先は http:// か https:// で始めてください: {raw}")));
    }
    let rest = with_scheme.split_once("://").map(|(_, r)| r).unwrap_or("");
    if rest.is_empty() || rest.contains(char::is_whitespace) {
        return Err(ConfigError(format!("接続先を読めません: {raw}")));
    }
    Ok(with_scheme)
}

pub fn parse_auth_header(raw: &str) -> Result<Option<(String, String)>, ConfigError> {
    let raw = raw.trim();
    if raw.is_empty() {
        return Ok(None);
    }
    let (name, value) = raw
        .split_once(':')
        .ok_or_else(|| ConfigError("auth_header は `Name: value` の形です".into()))?;
    let name = name.trim();
    if name.is_empty() || !name.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_') {
        return Err(ConfigError(format!("ヘッダ名を読めません: {name}")));
    }
    Ok(Some((name.to_string(), value.trim().to_string())))
}

impl Config {
    /// Apply one layer. Invalid values are errors (a typo in the host must not silently fall back).
    pub fn apply(&mut self, layer: &FileConfig, source: &str) -> Result<(), ConfigError> {
        let mut used = false;
        if let Some(h) = &layer.host {
            self.host = normalize_host(h)?;
            used = true;
        }
        if let Some(m) = &layer.mode {
            self.mode = Mode::parse(m).ok_or_else(|| ConfigError(format!("mode は fast / think / auto です: {m}")))?;
            used = true;
        }
        if let Some(p) = &layer.permission {
            self.permission = Permission::parse(p)
                .ok_or_else(|| ConfigError(format!("permission は default / accept-edits / plan / bypass です: {p}")))?;
            used = true;
        }
        if let Some(s) = &layer.shell {
            self.shell = ShellKind::parse(s)
                .ok_or_else(|| ConfigError(format!("shell は auto / bash / sh / pwsh / powershell / cmd です: {s}")))?;
            used = true;
        }
        if let Some(n) = layer.max_turns {
            self.max_turns = n.clamp(1, 200);
            used = true;
        }
        if let Some(l) = &layer.locale {
            self.locale = l.trim().to_string();
            used = true;
        }
        if let Some(a) = &layer.auth_header {
            self.auth_header = parse_auth_header(a)?;
            used = true;
        }
        if let Some(n) = layer.idle_timeout_s {
            self.idle_timeout_s = n.max(10);
            used = true;
        }
        if let Some(n) = layer.search_timeout_s {
            self.search_timeout_s = n.max(10);
            used = true;
        }
        if let Some(n) = layer.image_timeout_s {
            self.image_timeout_s = n.max(10);
            used = true;
        }
        if used {
            self.sources.push(source.to_string());
        }
        Ok(())
    }

    /// Defaults, the user file, the project file (`<cwd>/.cirka/config.toml`) and the environment.
    pub fn load(cwd: &Path) -> Result<Config, ConfigError> {
        let mut config = Config::default();
        if let Some(path) = platform::user_config_path() {
            if let Some(layer) = read_file(&path)? {
                config.apply(&layer, &path.display().to_string())?;
            }
        }
        let project = project_config_path(cwd);
        if let Some(layer) = read_file(&project)? {
            config.apply(&layer, &project.display().to_string())?;
        }
        config.apply(&env_layer(|k| std::env::var(k).ok()), "environment")?;
        Ok(config)
    }
}

pub fn project_config_path(cwd: &Path) -> PathBuf {
    cwd.join(".cirka").join("config.toml")
}

/// CIRKA_HOST, CIRKA_MODE, CIRKA_PERMISSION, CIRKA_AUTH_HEADER.
pub fn env_layer(get: impl Fn(&str) -> Option<String>) -> FileConfig {
    let pick = |k: &str| get(k).filter(|v| !v.trim().is_empty());
    FileConfig {
        host: pick("CIRKA_HOST"),
        mode: pick("CIRKA_MODE"),
        permission: pick("CIRKA_PERMISSION"),
        auth_header: pick("CIRKA_AUTH_HEADER"),
        ..FileConfig::default()
    }
}

pub fn read_file(path: &Path) -> Result<Option<FileConfig>, ConfigError> {
    match std::fs::read_to_string(path) {
        Ok(text) => toml::from_str(&text)
            .map(Some)
            .map_err(|e| ConfigError(format!("{} を読めません: {e}", path.display()))),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(ConfigError(format!("{} を読めません: {e}", path.display()))),
    }
}

/// Set (or with `value = None`, remove) one key in a config file; the value is validated first.
pub fn write_key(path: &Path, key: &str, value: Option<&str>) -> Result<FileConfig, ConfigError> {
    if !KEYS.contains(&key) {
        return Err(ConfigError(format!("知らないキーです: {key}（{}）", KEYS.join(", "))));
    }
    let mut file = read_file(path)?.unwrap_or_default();
    let v = value.map(|s| s.trim().to_string());
    let num = |s: &Option<String>| -> Result<Option<u64>, ConfigError> {
        s.as_ref()
            .map(|x| x.parse::<u64>().map_err(|_| ConfigError(format!("{key} は数値です: {x}"))))
            .transpose()
    };
    match key {
        "host" => file.host = v.as_deref().map(normalize_host).transpose()?,
        "mode" => file.mode = v,
        "permission" => file.permission = v,
        "shell" => file.shell = v,
        "max_turns" => file.max_turns = num(&v)?.map(|n| n as u32),
        "locale" => file.locale = v,
        "auth_header" => file.auth_header = v,
        "idle_timeout_s" => file.idle_timeout_s = num(&v)?,
        "search_timeout_s" => file.search_timeout_s = num(&v)?,
        "image_timeout_s" => file.image_timeout_s = num(&v)?,
        _ => unreachable!(),
    }
    // Validate the whole file as it will be read back.
    Config::default().apply(&file, "check")?;
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).map_err(|e| ConfigError(format!("{} を作れません: {e}", dir.display())))?;
    }
    let text = toml::to_string(&file).map_err(|e| ConfigError(e.to_string()))?;
    std::fs::write(path, text).map_err(|e| ConfigError(format!("{} に書けません: {e}", path.display())))?;
    Ok(file)
}

pub fn get_key(config: &Config, key: &str) -> Option<String> {
    Some(match key {
        "host" => config.host.clone(),
        "mode" => config.mode.as_str().into(),
        "permission" => config.permission.as_str().into(),
        "shell" => format!("{:?}", config.shell).to_ascii_lowercase(),
        "max_turns" => config.max_turns.to_string(),
        "locale" => config.locale.clone(),
        "auth_header" => config.auth_header.as_ref().map(|(n, _)| format!("{n}: ***")).unwrap_or_default(),
        "idle_timeout_s" => config.idle_timeout_s.to_string(),
        "search_timeout_s" => config.search_timeout_s.to_string(),
        "image_timeout_s" => config.image_timeout_s.to_string(),
        _ => return None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn host_is_normalized_and_checked() {
        assert_eq!(normalize_host("http://192.168.1.5:2024/").unwrap(), "http://192.168.1.5:2024");
        assert_eq!(normalize_host("192.168.1.5:2024").unwrap(), "http://192.168.1.5:2024");
        assert_eq!(normalize_host("https://gpu.lan").unwrap(), "https://gpu.lan");
        assert!(normalize_host("ftp://x").is_err());
        assert!(normalize_host("").is_err());
        assert!(normalize_host("http://").is_err());
    }

    #[test]
    fn layers_override_in_order() {
        let mut c = Config::default();
        c.apply(&FileConfig { host: Some("http://a:1".into()), mode: Some("think".into()), ..Default::default() }, "user")
            .unwrap();
        c.apply(&FileConfig { host: Some("http://b:2".into()), ..Default::default() }, "project").unwrap();
        let env = env_layer(|k| (k == "CIRKA_HOST").then(|| "http://c:3".to_string()));
        c.apply(&env, "environment").unwrap();
        assert_eq!(c.host, "http://c:3");
        assert_eq!(c.mode, Mode::Think);
        assert_eq!(c.sources, vec!["defaults", "user", "project", "environment"]);
    }

    #[test]
    fn bad_values_are_errors() {
        let mut c = Config::default();
        assert!(c.apply(&FileConfig { mode: Some("turbo".into()), ..Default::default() }, "x").is_err());
        assert!(c.apply(&FileConfig { permission: Some("root".into()), ..Default::default() }, "x").is_err());
        assert!(parse_auth_header("no colon").is_err());
        assert_eq!(
            parse_auth_header("Authorization: Bearer x").unwrap(),
            Some(("Authorization".into(), "Bearer x".into()))
        );
    }

    #[test]
    fn write_key_round_trips() {
        let dir = std::env::temp_dir().join(format!("cirka-cfg-{}", uuid::Uuid::new_v4()));
        let path = dir.join("config.toml");
        write_key(&path, "host", Some("10.0.0.2:2024")).unwrap();
        write_key(&path, "mode", Some("think")).unwrap();
        let file = read_file(&path).unwrap().unwrap();
        assert_eq!(file.host.as_deref(), Some("http://10.0.0.2:2024"));
        assert_eq!(file.mode.as_deref(), Some("think"));
        assert!(write_key(&path, "mode", Some("turbo")).is_err());
        assert!(write_key(&path, "colour", Some("x")).is_err());
        write_key(&path, "host", None).unwrap();
        assert_eq!(read_file(&path).unwrap().unwrap().host, None);
        std::fs::remove_dir_all(dir).ok();
    }
}
