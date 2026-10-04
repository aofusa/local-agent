<#
.SYNOPSIS
  Prepare the Docker sandbox of the chat tab's code branch (idempotent).

.DESCRIPTION
  The chat tab runs generated programs only in think mode, only after the user approves them, and only in a
  Docker Desktop Linux container (src\furry_agent\sandbox.py): python:3.12-slim (Debian slim), --network none,
  read-only root, artifacts\code\<run_id> as /work, 2 GB, 2 CPUs, 60 s. Containers are started with
  --pull never, so the images are fetched here, not during a chat run.

  This script checks that Docker Desktop is installed and its Linux engine is running (it starts Docker Desktop
  when it is installed but stopped), then pulls python:3.12-slim and, with -Rust, rust:1.88-slim. Docker Desktop
  started here is stopped again at the end (-KeepRunning keeps it): its VM takes about 1.5 GB, and the chat tab
  starts it by itself for an approved run and stops it afterwards.
  Without Docker the chat tab still writes the files and says why it did not run them.

.EXAMPLE
  .\scripts\setup-sandbox.ps1
  .\scripts\setup-sandbox.ps1 -Rust
#>
param(
    [switch]$Rust,
    [switch]$KeepRunning,
    [int]$WaitSeconds = 240
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\common.ps1")

Write-Step "Docker"
$docker = (Get-Command docker -ErrorAction SilentlyContinue).Source
if (-not $docker) {
    throw "docker が見つかりません。Docker Desktop を入れてください（https://www.docker.com/products/docker-desktop/）"
}
Write-Ok $docker

function Test-DockerEngine {
    $os = & docker version --format "{{.Server.Os}}" 2>$null
    if ($LASTEXITCODE -ne 0) { return $null }
    return "$os".Trim()
}

$os = Test-DockerEngine
$startedHere = $false
if (-not $os) {
    $startedHere = $true
    $desktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (-not (Test-Path $desktop)) { throw "Docker のエンジンが動いていません。Docker Desktop を起動してください" }
    Write-Warn2 "Docker Desktop を起動します（最大 $WaitSeconds 秒待ちます）"
    Start-Process -FilePath $desktop | Out-Null
    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    while (-not ($os = Test-DockerEngine)) {
        if ((Get-Date) -gt $deadline) { throw "Docker のエンジンが $WaitSeconds 秒以内に起動しませんでした" }
        Start-Sleep -Seconds 3
    }
}
if ($os -ne "linux") { throw "Docker のエンジンが $os です。Docker Desktop で Linux コンテナに切り替えてください" }
Write-Ok "engine: $os"

$images = @("python:3.12-slim")
if ($Rust) { $images += "rust:1.88-slim" }
foreach ($image in $images) {
    Write-Step "イメージ $image"
    & docker image inspect $image *> $null
    if ($LASTEXITCODE -eq 0) { Write-Ok "取得済み"; continue }
    & docker pull $image
    if ($LASTEXITCODE -ne 0) { throw "$image を取得できませんでした" }
    Write-Ok "取得しました"
}

Write-Step "動作確認（ネットワークなし、読み取り専用、非 root）"
$probe = & docker run --rm --pull never --network none --read-only --memory 256m --cap-drop ALL `
    --security-opt no-new-privileges --user 10001:10001 python:3.12-slim `
    python -c "import os; print('uid', os.getuid())" 2>&1
if ($LASTEXITCODE -ne 0) { throw "コンテナを起動できません: $probe" }
Write-Ok "$probe"

# Docker Desktop's VM holds about 1.5 GB that the 27B and ComfyUI need on this machine. The chat tab starts it
# for an approved run and stops it afterwards, so it is stopped again here unless it was already running.
if ($startedHere -and -not $KeepRunning) {
    Write-Step "Docker Desktop を止めます（チャットタブが実行のときだけ起動します。-KeepRunning で起動したまま）"
    & docker desktop stop --timeout 120 | Out-Null
    Write-Ok "stopped"
}
