# Start Tor (background), the LLM router, ComfyUI, LangGraph and agent-chat-ui (those not already running), each in
# its own window. The router holds no model until the first request; the search models (llama-server workers)
# are not started here: the chat tab starts them per search.
param(
    [switch]$SkipComfyUI,   # ComfyUI already started some other way
    [switch]$SkipTor        # the chat tab's search needs Tor; the image tab does not
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\common.ps1")
$shell = (Get-Process -Id $PID).Path

function Start-InWindow([string]$Title, [string]$Script) {
    $command = "`$Host.UI.RawUI.WindowTitle = '$Title'; & '$Script'"
    Start-Process $shell -ArgumentList @("-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $command)
    Write-Ok "$Title -> new window"
}

function Wait-Port([int]$Port, [int]$TimeoutSec) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while (-not (Test-Listening $Port)) {
        if ((Get-Date) -gt $deadline) { throw "ポート $Port が ${TimeoutSec} 秒以内に開きませんでした" }
        Start-Sleep 2
    }
}

Write-Step "LLM ルータ（llama.cpp）"
$llmPort = [int](Get-DotEnvValue "LLM_PORT" "8080")
if (Test-Listening $llmPort) { Write-Ok "already running (127.0.0.1:$llmPort)" }
else { Start-InWindow "LLM router" (Join-Path $PSScriptRoot "start-llm.ps1"); Wait-Port $llmPort 60 }

if (-not $SkipTor) {
    if (Get-DotEnvValue "TOR_EXE") {
        try { & (Join-Path $PSScriptRoot "start-tor.ps1") } catch { Write-Warn2 "Tor を起動できません（チャットタブの検索だけが使えません）: $($_.Exception.Message)" }
    } else { Write-Warn2 "Tor 未導入（scripts\setup-tor.ps1）。チャットタブの検索は使えません" }
}

if (-not $SkipComfyUI) {
    Write-Step "ComfyUI"
    if (Test-Listening 8188) { Write-Ok "already running" }
    else { Start-InWindow "ComfyUI" (Join-Path $PSScriptRoot "start-comfyui.ps1"); Wait-Port 8188 300 }
}

Write-Step "LangGraph"
if (Test-Listening 2024) { Write-Ok "already running" }
else { Start-InWindow "LangGraph" (Join-Path $PSScriptRoot "start-langgraph.ps1"); Wait-Port 2024 180 }

Write-Step "agent-chat-ui"
if (Test-Listening 3000) { Write-Ok "already running" }
else { Start-InWindow "agent-chat-ui" (Join-Path $PSScriptRoot "start-ui.ps1"); Wait-Port 3000 600 }

Write-Host ""
Write-Host "起動しました。他ホストのブラウザで http://$(Get-LanIPv4):3000 を開いてください。" -ForegroundColor Cyan
