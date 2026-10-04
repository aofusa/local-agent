<#
.SYNOPSIS
  Start Tor (SOCKS 127.0.0.1:9050) in the background for the chat tab's web search, and wait for bootstrap.

.DESCRIPTION
  Uses TOR_EXE from .env (scripts\setup-tor.ps1) and tools\tor\torrc. The data directory is tools\tor\data,
  the log is logs\tor.log and the PID is written to tools\tor\tor.pid. Does nothing when 9050 is already
  listening. -Stop stops the Tor process started by this script.
#>
param(
    [int]$TimeoutSec = 120,
    [switch]$Stop
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\common.ps1")
$root = Get-RepoRoot
$pidFile = Join-Path $root "tools\tor\tor.pid"
$log = Join-Path $root "logs\tor.log"

if ($Stop) {
    if (Test-Path $pidFile) {
        $torPid = [int](Get-Content $pidFile)
        Stop-Process -Id $torPid -Force -ErrorAction SilentlyContinue
        Remove-Item $pidFile
        Write-Ok "Tor を停止しました（PID $torPid）"
    } else { Write-Warn2 "このスクリプトで起動した Tor はありません" }
    return
}

Write-Step "Tor"
if (Test-Listening 9050) {
    $addresses = Get-ListenAddresses 9050
    if ($addresses | Where-Object { $_ -notin @("127.0.0.1", "::1") }) { throw "9050 がループバック以外で待ち受けています: $($addresses -join ', ')" }
    Write-Ok "already running (127.0.0.1:9050)"
    return
}
$exe = Get-DotEnvValue "TOR_EXE"
if (-not $exe -or -not (Test-Path $exe)) { throw "tor.exe がありません。先に .\scripts\setup-tor.ps1 を実行してください" }
$data = Join-Path $root "tools\tor\data"
New-Item -ItemType Directory -Force $data, (Split-Path $log) | Out-Null
if (Test-Path $log) { Clear-Content $log }
$arguments = @("-f", "`"$(Join-Path $root 'tools\tor\torrc')`"", "--DataDirectory", "`"$data`"", "--Log", "`"notice file $log`"")
$process = Start-Process -FilePath $exe -ArgumentList $arguments -WindowStyle Hidden -PassThru
Set-Content -Path $pidFile -Value $process.Id
$deadline = (Get-Date).AddSeconds($TimeoutSec)
while ($true) {
    if ($process.HasExited) { throw "Tor が終了しました（$log を確認してください）" }
    $text = if (Test-Path $log) { Get-Content $log -Raw -ErrorAction SilentlyContinue } else { "" }
    if ($text -match "Bootstrapped 100%") { break }
    if ((Get-Date) -gt $deadline) { throw "Tor の bootstrap が ${TimeoutSec} 秒以内に終わりませんでした（$log）" }
    Start-Sleep 2
}
Write-Ok "127.0.0.1:9050 (PID $($process.Id), bootstrapped)"
