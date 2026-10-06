//! bash: one shell command in the workspace, with an idle timeout (stopped only after `timeout_s` seconds without
//! any output; a build that keeps printing runs as long as it needs), an output cap and a kill of the whole
//! process tree. Whether it may run is the policy's decision, not this file's.

use std::path::Path;
use std::process::Stdio;
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

use serde_json::Value;
use tokio::io::AsyncReadExt;

use super::{Planned, ToolCtx, ToolOutput, arg_str, arg_u64};
use crate::config::ShellKind;
use crate::platform::{command_env, decode_output, resolve_shell, shell_argv};
use crate::ui::{Frontend, UiEvent};

const OUTPUT_CAP: usize = 64 * 1024;

pub fn plan(ctx: &ToolCtx, args: &Value) -> Planned {
    let Some(command) = arg_str(args, "command").map(str::trim).filter(|c| !c.is_empty()) else {
        return Planned::Invalid(ToolOutput::err("command がありません"));
    };
    let cwd = match arg_str(args, "cwd").map(str::trim).filter(|c| !c.is_empty()) {
        Some(raw) => match ctx.ws.resolve(raw) {
            Ok(p) if p.is_dir() => p,
            Ok(_) => return Planned::Invalid(ToolOutput::err(format!("cwd はディレクトリではありません: {raw}"))),
            Err(e) => return Planned::Invalid(ToolOutput::err(e.to_string())),
        },
        None => ctx.ws.root.clone(),
    };
    // The model may ask for a shorter silence limit; the default and the ceiling are idle_timeout_s.
    let idle = ctx.config.idle_timeout_s.max(1);
    let timeout_s = arg_u64(args, "timeout_s").unwrap_or(idle).clamp(1, idle);
    Planned::Command { command: command.to_string(), cwd, timeout_s }
}

/// UTF-8 output from Windows shells (the console code page is often cp932 or cp1252).
fn utf8_command(shell: ShellKind, command: &str) -> String {
    match shell {
        ShellKind::Pwsh | ShellKind::Powershell => {
            format!("[Console]::OutputEncoding = [Text.Encoding]::UTF8; $OutputEncoding = [Text.Encoding]::UTF8; {command}")
        }
        ShellKind::Cmd => format!("chcp 65001 >nul & {command}"),
        _ => command.to_string(),
    }
}

async fn kill_tree(pid: Option<u32>, child: &mut tokio::process::Child) {
    if let Some(pid) = pid {
        #[cfg(windows)]
        {
            let _ = tokio::process::Command::new("taskkill")
                .args(["/T", "/F", "/PID", &pid.to_string()])
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status()
                .await;
        }
        #[cfg(unix)]
        {
            // The child leads its own process group (process_group(0)): kill the group.
            let _ = tokio::process::Command::new("kill")
                .args(["-9", "--", &format!("-{pid}")])
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status()
                .await;
        }
    }
    let _ = child.kill().await;
}

async fn drain<R: tokio::io::AsyncRead + Unpin>(reader: Option<R>, activity: Arc<AtomicU64>, origin: Instant) -> (Vec<u8>, usize) {
    let Some(mut reader) = reader else { return (Vec::new(), 0) };
    let mut kept = Vec::new();
    let mut dropped = 0usize;
    let mut buf = [0u8; 8192];
    loop {
        match reader.read(&mut buf).await {
            Ok(0) | Err(_) => break,
            Ok(n) => {
                activity.store(origin.elapsed().as_millis() as u64, Ordering::SeqCst);
                let room = OUTPUT_CAP.saturating_sub(kept.len());
                kept.extend_from_slice(&buf[..n.min(room)]);
                dropped += n.saturating_sub(room);
            }
        }
    }
    (kept, dropped)
}

pub async fn run(ctx: &ToolCtx, command: &str, cwd: &Path, timeout_s: u64, ui: &mut dyn Frontend) -> ToolOutput {
    let shell = resolve_shell(ctx.config.shell);
    let (program, argv) = shell_argv(shell, &utf8_command(shell, command));
    let mut cmd = tokio::process::Command::new(&program);
    cmd.args(&argv)
        .current_dir(cwd)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .envs(command_env())
        .kill_on_drop(true);
    #[cfg(unix)]
    cmd.process_group(0);
    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => return ToolOutput::err(format!("{program} を起動できません: {e}")),
    };
    let pid = child.id();
    let started = Instant::now();
    let activity = Arc::new(AtomicU64::new(0)); // ms since start of the last output
    let out_task = tokio::spawn(drain(child.stdout.take(), activity.clone(), started));
    let err_task = tokio::spawn(drain(child.stderr.take(), activity.clone(), started));
    let limit = Duration::from_secs(timeout_s);
    let mut stopped: Option<&str> = None;
    let mut last_tick = Instant::now();
    let status = loop {
        if last_tick.elapsed() >= Duration::from_millis(250) {
            ui.event(UiEvent::Tick);
            last_tick = Instant::now();
        }
        tokio::select! {
            status = child.wait() => break status.ok(),
            _ = tokio::time::sleep(Duration::from_millis(100)) => {
                if ctx.cancelled() {
                    stopped = Some("中断（Ctrl-C）");
                } else if started.elapsed().saturating_sub(Duration::from_millis(activity.load(Ordering::SeqCst))) > limit {
                    stopped = Some("時間切れ");
                }
                if stopped.is_some() {
                    kill_tree(pid, &mut child).await;
                    break None;
                }
            }
        }
    };
    let (stdout, out_dropped) = out_task.await.unwrap_or_default();
    let (stderr, err_dropped) = err_task.await.unwrap_or_default();
    let seconds = started.elapsed().as_secs_f32();
    let code = status.and_then(|s| s.code());
    let mut text = match (stopped, code) {
        (Some("時間切れ"), _) => format!("時間切れ: {timeout_s} 秒間出力がなかったため、{seconds:.1} 秒で停止しました（プロセスツリーごと終了）\n"),
        (Some(why), _) => format!("{why}: {seconds:.1} 秒で停止しました（プロセスツリーごと終了）\n"),
        (None, Some(c)) => format!("終了コード {c}（{seconds:.1} 秒）\n"),
        (None, None) => format!("シグナルで終了しました（{seconds:.1} 秒）\n"),
    };
    let stdout = decode_output(&stdout);
    let stderr = decode_output(&stderr);
    if !stdout.trim().is_empty() {
        text.push_str(&format!("--- stdout ---\n{}\n", stdout.trim_end()));
    }
    if !stderr.trim().is_empty() {
        text.push_str(&format!("--- stderr ---\n{}\n", stderr.trim_end()));
    }
    if out_dropped + err_dropped > 0 {
        text.push_str(&format!("（出力の上限 64 KiB を超えた {} バイトを捨てました）\n", out_dropped + err_dropped));
    }
    ToolOutput { content: text.trim_end().to_string(), ok: stopped.is_none() && code == Some(0) }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::Config;
    use crate::ui::NullUi;
    use crate::workspace::Workspace;
    use serde_json::json;
    use std::sync::Arc;
    use std::sync::atomic::AtomicBool;

    fn ctx() -> (ToolCtx, std::path::PathBuf) {
        let dir = std::env::temp_dir().join(format!("cirka-bash-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(dir.join("sub")).unwrap();
        let c = ToolCtx::new(Workspace::new(&dir).unwrap(), Config::default(), None, Arc::new(AtomicBool::new(false)));
        (c, dir)
    }

    #[test]
    fn plan_checks_cwd_and_timeout() {
        let (c, dir) = ctx();
        // The default and the ceiling are the configured idle timeout (1200 s).
        assert!(matches!(plan(&c, &json!({"command": "echo hi", "timeout_s": 9999})),
                         Planned::Command { timeout_s: 1200, .. }));
        assert!(matches!(plan(&c, &json!({"command": "echo hi"})), Planned::Command { timeout_s: 1200, .. }));
        assert!(matches!(plan(&c, &json!({"command": "echo hi", "timeout_s": 5})), Planned::Command { timeout_s: 5, .. }));
        assert!(matches!(plan(&c, &json!({"command": "echo hi", "cwd": ".."})), Planned::Invalid(_)));
        assert!(matches!(plan(&c, &json!({"command": "  "})), Planned::Invalid(_)));
        assert!(matches!(plan(&c, &json!({"command": "x", "cwd": "sub"})), Planned::Command { .. }));
        std::fs::remove_dir_all(dir).ok();
    }

    #[tokio::test]
    async fn runs_and_reports_the_exit_code() {
        let (c, dir) = ctx();
        let out = run(&c, "echo hello", &dir, 60, &mut NullUi).await;
        assert!(out.ok, "{}", out.content);
        assert!(out.content.contains("終了コード 0") && out.content.contains("hello"));
        let fail = run(&c, "exit 3", &dir, 60, &mut NullUi).await;
        assert!(!fail.ok && fail.content.contains("終了コード 3"));
        std::fs::remove_dir_all(dir).ok();
    }

    #[tokio::test]
    async fn japanese_output_of_a_child_program_is_not_garbled() {
        // A native program (Python here) writes to the pipe itself; on Windows it used to come back in the ANSI
        // code page (cp932) and showed as mojibake. Emoji are outside cp932 and made Python raise.
        let python = ["python", "python3"].into_iter().find(|p| crate::platform::which(p).is_some());
        let Some(python) = python else { return };
        let (c, dir) = ctx();
        std::fs::write(dir.join("hello.py"), "print('こんにちは、世界')\nprint('絵文字 🐺')\n").unwrap();
        let out = run(&c, &format!("{python} hello.py"), &dir, 60, &mut NullUi).await;
        assert!(out.ok, "{}", out.content);
        assert!(out.content.contains("こんにちは、世界"), "{}", out.content);
        assert!(out.content.contains("絵文字 🐺"), "{}", out.content);
        std::fs::remove_dir_all(dir).ok();
    }

    #[tokio::test]
    async fn japanese_output_of_the_shell_itself_is_not_garbled() {
        let (c, dir) = ctx();
        let cmd = if cfg!(windows) { "Write-Output 'シェルの出力'" } else { "echo シェルの出力" };
        let out = run(&c, cmd, &dir, 60, &mut NullUi).await;
        assert!(out.ok && out.content.contains("シェルの出力"), "{}", out.content);
        std::fs::remove_dir_all(dir).ok();
    }

    #[tokio::test]
    async fn a_command_that_keeps_printing_is_not_stopped() {
        let (c, dir) = ctx();
        // Prints every 0.4 s for about 2.4 s; the silence limit is 1 s.
        let cmd = if cfg!(windows) {
            "1..6 | ForEach-Object { [Console]::Out.WriteLine($_); [Console]::Out.Flush(); Start-Sleep -Milliseconds 400 }"
        } else {
            "for i in 1 2 3 4 5 6; do echo $i; sleep 0.4; done"
        };
        let started = Instant::now();
        let out = run(&c, cmd, &dir, 1, &mut NullUi).await;
        assert!(out.ok, "{}", out.content);
        assert!(started.elapsed() > Duration::from_secs(2));
        std::fs::remove_dir_all(dir).ok();
    }

    #[tokio::test]
    async fn timeout_kills_the_command() {
        let (c, dir) = ctx();
        let sleep = if cfg!(windows) { "Start-Sleep -Seconds 30" } else { "sleep 30" };
        let started = Instant::now();
        let out = run(&c, sleep, &dir, 1, &mut NullUi).await;
        assert!(!out.ok && out.content.contains("時間切れ") && out.content.contains("出力がなかった"));
        assert!(started.elapsed() < Duration::from_secs(15));
        std::fs::remove_dir_all(dir).ok();
    }
}
