# Start ComfyUI (if not already running), LangGraph and agent-chat-ui, each in its own window.
# LM Studio must already be running (its server starts on launch; see README).
param(
    [switch]$SkipComfyUI    # use when ComfyUI is started from Comfy Desktop instead
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

Write-Step "LM Studio"
if (Test-Listening 1234) { Write-Ok "127.0.0.1:1234" } else { Write-Warn2 "LM Studio のサーバが起動していません（LM Studio を起動してください）" }

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
