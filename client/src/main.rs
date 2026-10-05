//! cirka: a CUI coding agent for the directory it starts in. The model runs on a local-agent host (LangGraph
//! `/coder/turn` in front of LM Studio); the tools run here. docs/locus-cui-design.md is the design.

mod agent;
mod app;
mod art;
mod art_data;
mod config;
mod context;
mod host;
mod platform;
mod policy;
mod session;
mod tools;
mod tui;
mod ui;
mod workspace;

use std::path::PathBuf;
use std::process::ExitCode;
use std::sync::Arc;
use std::sync::atomic::AtomicBool;

use clap::{Parser, Subcommand};

use crate::agent::StopReason;
use crate::config::{Config, FileConfig};

#[derive(Parser)]
#[command(name = "cirka", version, about = "local-agent ホストのモデルで、このディレクトリのファイルとコマンドを扱う CUI エージェント")]
struct Cli {
    /// 接続先（local-agent の LangGraph。例 http://192.168.1.20:2024）。設定ファイルと CIRKA_HOST より優先
    #[arg(long, global = true)]
    host: Option<String>,
    /// fast | think | auto
    #[arg(long)]
    mode: Option<String>,
    /// default | accept-edits | plan | bypass（bypass はシェルと同じ権限です）
    #[arg(long)]
    permission: Option<String>,
    /// 1 回だけ依頼して結果を表示して終わる（確認が要る操作は拒否。--permission で許可）
    #[arg(short = 'p', long = "print")]
    prompt: Option<String>,
    /// このディレクトリの直前のセッションを再開する
    #[arg(long)]
    resume: bool,
    #[arg(long)]
    max_turns: Option<u32>,
    /// ワークスペース（既定は現在のディレクトリ）
    #[arg(short = 'C', long = "dir")]
    dir: Option<PathBuf>,
    #[command(subcommand)]
    command: Option<Command>,
}

#[derive(Subcommand)]
enum Command {
    /// 設定（接続先など）を表示・変更する
    Config {
        #[command(subcommand)]
        action: ConfigAction,
    },
    /// 接続先のホストの状態を表示する
    Status,
    /// ロゴを表示する（docs/logo から作った端末用の絵）
    Logo,
}

#[derive(Subcommand)]
enum ConfigAction {
    /// 実際に使われる値を表示する
    Get { key: String },
    /// 値を保存する（既定はユーザー設定。--project で ./.cirka/config.toml）
    Set {
        key: String,
        value: String,
        #[arg(long)]
        project: bool,
    },
    /// 値を消す
    Unset {
        key: String,
        #[arg(long)]
        project: bool,
    },
    /// 設定ファイルの場所
    Path,
    /// すべての値と読み込んだ場所
    Show,
}

fn workspace_dir(cli: &Cli) -> Result<PathBuf, String> {
    let dir = match &cli.dir {
        Some(d) => d.clone(),
        None => std::env::current_dir().map_err(|e| e.to_string())?,
    };
    if !dir.is_dir() {
        return Err(format!("ディレクトリではありません: {}", dir.display()));
    }
    Ok(dir)
}

fn load_config(cli: &Cli, cwd: &std::path::Path) -> Result<Config, String> {
    let mut config = Config::load(cwd).map_err(|e| e.to_string())?;
    let layer = FileConfig {
        host: cli.host.clone(),
        mode: cli.mode.clone(),
        permission: cli.permission.clone(),
        max_turns: cli.max_turns,
        ..FileConfig::default()
    };
    config.apply(&layer, "command line").map_err(|e| e.to_string())?;
    Ok(config)
}

fn config_command(cli: &Cli, action: &ConfigAction, cwd: &std::path::Path) -> Result<(), String> {
    let user = platform::user_config_path().ok_or("ユーザー設定の場所が分かりません")?;
    let target = |project: bool| if project { config::project_config_path(cwd) } else { user.clone() };
    match action {
        ConfigAction::Path => {
            println!("user:    {}", user.display());
            println!("project: {}", config::project_config_path(cwd).display());
        }
        ConfigAction::Get { key } => {
            let config = load_config(cli, cwd)?;
            println!("{}", config::get_key(&config, key).ok_or_else(|| format!("知らないキーです: {key}"))?);
        }
        ConfigAction::Set { key, value, project } => {
            let path = target(*project);
            config::write_key(&path, key, Some(value)).map_err(|e| e.to_string())?;
            println!("{key} を {} に保存しました", path.display());
        }
        ConfigAction::Unset { key, project } => {
            let path = target(*project);
            config::write_key(&path, key, None).map_err(|e| e.to_string())?;
            println!("{key} を {} から消しました", path.display());
        }
        ConfigAction::Show => {
            let config = load_config(cli, cwd)?;
            for key in config::KEYS {
                println!("{key} = {}", config::get_key(&config, key).unwrap_or_default());
            }
            println!("# sources: {}", config.sources.join(" < "));
        }
    }
    Ok(())
}

async fn status(config: &Config) -> ExitCode {
    let h = host::HostClient::new(config).health().await;
    println!("host:     {}", config.host);
    println!("reachable: {}", h.ok);
    println!("gate:     {}", h.gate);
    println!("lmstudio: {}", h.lmstudio);
    println!("model:    {}", h.model);
    println!("context:  {}", h.context);
    println!("busy:     {}", h.busy.as_deref().unwrap_or("-"));
    if let Some(e) = h.error {
        println!("error:    {e}");
    }
    if h.ok && h.gate && h.lmstudio { ExitCode::SUCCESS } else { ExitCode::from(2) }
}

#[tokio::main]
async fn main() -> ExitCode {
    let cli = Cli::parse();
    let cwd = match workspace_dir(&cli) {
        Ok(d) => d,
        Err(e) => {
            eprintln!("cirka: {e}");
            return ExitCode::from(2);
        }
    };
    if let Some(Command::Config { action }) = &cli.command {
        return match config_command(&cli, action, &cwd) {
            Ok(()) => ExitCode::SUCCESS,
            Err(e) => {
                eprintln!("cirka: {e}");
                ExitCode::from(2)
            }
        };
    }
    let config = match load_config(&cli, &cwd) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("cirka: {e}");
            return ExitCode::from(2);
        }
    };
    if let Some(Command::Status) = &cli.command {
        return status(&config).await;
    }
    if let Some(Command::Logo) = &cli.command {
        let theme = tui::Theme { mode: art::ColorMode::detect(std::io::IsTerminal::is_terminal(&std::io::stdout())) };
        let _ = crossterm::ansi_support::supports_ansi();
        for line in tui::logo(&theme) {
            println!("{line}");
        }
        return ExitCode::SUCCESS;
    }
    let cancel = Arc::new(AtomicBool::new(false));
    let interactive = cli.prompt.is_none();
    let mut app = match app::App::start(config, &cwd, interactive, cancel.clone()).await {
        Ok(a) => a,
        Err(e) => {
            eprintln!("cirka: {e}");
            return ExitCode::from(2);
        }
    };
    app::install_ctrl_c(cancel, app.busy.clone());
    app.new_session();
    if cli.resume {
        app.command("/resume").await;
    }
    if let Some(prompt) = &cli.prompt {
        let stop = app.request(prompt).await;
        return if stop == StopReason::Completed { ExitCode::SUCCESS } else { ExitCode::from(1) };
    }
    app.repl().await;
    ExitCode::SUCCESS
}
