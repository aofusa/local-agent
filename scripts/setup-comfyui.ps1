<#
.SYNOPSIS
  Configure an existing ComfyUI installation for local-agent (idempotent).

.DESCRIPTION
  1. Locate ComfyUI (parameter, .env, or Comfy Desktop's installations.json) and save the paths to .env.
  2. Install eedali/LM_Connect (pinned commit) into custom_nodes. Only its light dependencies are installed
     (requests, Pillow, numpy). llama-cpp-python is NOT installed: no GGUF runs inside ComfyUI.
  3. Link custom_nodes\furry_ja to this repository's comfyui_nodes\furry_ja (directory junction).
  4. Optionally (-ConfigureComfyDesktop) set Comfy Desktop's launch args to --listen 127.0.0.1 --port 8188 --cache-none.
  5. Check that the checkpoint exists.
  Restart ComfyUI afterwards so the nodes are loaded.

.EXAMPLE
  .\scripts\setup-comfyui.ps1
  .\scripts\setup-comfyui.ps1 -ComfyUIDir D:\ComfyUI_windows_portable\ComfyUI
  .\scripts\setup-comfyui.ps1 -ConfigureComfyDesktop
#>
param(
    [string]$ComfyUIDir = "",          # folder that contains main.py (or its parent, e.g. a portable root)
    [string]$ComfyPython = "",         # python.exe that runs ComfyUI
    [string]$CkptName = "",            # default: CKPT_NAME in .env
    [string]$LMConnectCommit = "422a08970fee5f3f525589bcb3170099007ae286",
    [switch]$ConfigureComfyDesktop
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\common.ps1")
Initialize-DotEnv | Out-Null

Write-Step "ComfyUI のインストールを特定"
$layout = Get-ComfyLayout -MainDir $ComfyUIDir -Python $ComfyPython
if (-not $layout) {
    throw "ComfyUI が見つかりません。-ComfyUIDir <main.py のあるフォルダ> を指定してください。"
}
if ($ComfyPython) { $layout.Python = $ComfyPython }
if (-not $layout.Python -or -not (Test-Path $layout.Python)) {
    throw "ComfyUI の python.exe が見つかりません。-ComfyPython で指定してください。"
}
Save-ComfyLayout $layout
Write-Ok "source=$($layout.Source)"
Write-Ok "main.py:      $($layout.MainDir)"
Write-Ok "python:       $($layout.Python)"
Write-Ok "custom_nodes: $($layout.CustomNodesDir)"
New-Item -ItemType Directory -Force $layout.CustomNodesDir | Out-Null

Write-Step "LM_Connect (eedali/LM_Connect) を導入"
$lmConnect = Join-Path $layout.CustomNodesDir "LM_Connect"
if (-not (Test-Path $lmConnect)) {
    Invoke-Native git clone --quiet https://github.com/eedali/LM_Connect $lmConnect | Write-Host
    if ($LASTEXITCODE -ne 0) { throw "git clone に失敗しました" }
    Invoke-Native git -C $lmConnect checkout --quiet $LMConnectCommit | Write-Host
    if ($LASTEXITCODE -ne 0) { throw "LM_Connect $LMConnectCommit の checkout に失敗しました" }
    Write-Ok "cloned at $LMConnectCommit"
} else {
    $head = (git -C $lmConnect rev-parse HEAD).Trim()
    if ($head -ne $LMConnectCommit) { Write-Warn2 "既存の LM_Connect は $head（検証済みは $LMConnectCommit）。そのまま使います" }
    else { Write-Ok "already installed ($head)" }
}

Write-Step "LM_Connect の依存（requests / Pillow / numpy のみ。llama-cpp-python は入れない）"
Invoke-Native $layout.Python -m pip --version | Out-Null
if ($LASTEXITCODE -eq 0) {
    Invoke-Native $layout.Python -m pip install --disable-pip-version-check --quiet requests Pillow numpy | Write-Host
} else {
    Invoke-Native uv pip install --python $layout.Python requests Pillow numpy | Write-Host
}
if ($LASTEXITCODE -ne 0) { throw "依存のインストールに失敗しました" }
Write-Ok "installed"

Write-Step "furry_ja ノードをリンク"
$target = Join-Path (Get-RepoRoot) "comfyui_nodes\furry_ja"
$link = Join-Path $layout.CustomNodesDir "furry_ja"
if (Test-Path $link) {
    $item = Get-Item $link -Force
    if ($item.LinkType -eq "Junction" -or $item.LinkType -eq "SymbolicLink") {
        $current = @($item.Target)[0]
        if ($current -and ((Resolve-Path $current).Path -eq (Resolve-Path $target).Path)) {
            Write-Ok "already linked"
        } else {
            $item.Delete()
            New-Item -ItemType Junction -Path $link -Target $target | Out-Null
            Write-Ok "re-linked -> $target"
        }
    } else {
        throw "$link は通常のフォルダです。削除またはリネームしてから再実行してください。"
    }
} else {
    New-Item -ItemType Junction -Path $link -Target $target | Out-Null
    Write-Ok "linked -> $target"
}

if ($ConfigureComfyDesktop) {
    Write-Step "Comfy Desktop の起動引数を --listen 127.0.0.1 --port 8188 に設定"
    if (Get-Process "Comfy Desktop" -ErrorAction SilentlyContinue) {
        Write-Warn2 "Comfy Desktop が起動中のためスキップしました。終了してから再実行してください。"
    } elseif (-not $layout.InstallationId) {
        Write-Warn2 "Comfy Desktop のインストールではないためスキップしました。"
    } else {
        $file = Join-Path $env:APPDATA "Comfy Desktop\installations.json"
        Backup-File $file
        $items = @(Read-JsonArray $file)
        foreach ($inst in $items) {
            if ($inst.id -ne $layout.InstallationId) { continue }
            $current = if ($inst.launchArgs) { $inst.launchArgs } else { "" }
            $rest = ($current -replace '--listen(\s+\S+)?', '' -replace '--port\s+\d+', '' -replace '--cache-none', '').Trim() -replace '\s+', ' '
            # --cache-none: see Get-ComfyServerArgs (IP-Adapter breaks with the node cache on ComfyUI 0.38).
            Set-JsonProperty $inst "launchArgs" ("--listen 127.0.0.1 --port 8188 --cache-none " + $rest).Trim()
            Write-Ok "launchArgs: $($inst.launchArgs)  (backup: installations.json.local-agent.bak)"
        }
        Write-JsonFile $file $items
    }
}

Write-Step "チェックポイントを確認"
if (-not $CkptName) { $CkptName = Get-DotEnvValue "CKPT_NAME" "yiffInHell_yihVANTABLACK.safetensors" }
$dirs = @()
if ($layout.BaseDir) { $dirs += Join-Path $layout.BaseDir "models\checkpoints" }
$dirs += Join-Path $layout.MainDir "models\checkpoints"
if ($layout.ExtraModelPaths -and (Test-Path $layout.ExtraModelPaths)) {
    foreach ($line in Get-Content $layout.ExtraModelPaths) {
        if ($line -match "base_path:\s*'?([^']+)'?\s*$") { $dirs += Join-Path $Matches[1].Trim() "checkpoints" }
    }
}
$found = $dirs | Where-Object { Test-Path (Join-Path $_ $CkptName) } | Select-Object -First 1
if ($found) { Write-Ok "$CkptName ($found)" }
else { Write-Warn2 "$CkptName が見つかりません。ComfyUI の models\checkpoints に置くか、.env の CKPT_NAME を実ファイル名にしてください。探した場所: $($dirs -join '; ')" }

Write-Host ""
Write-Host "ComfyUI を再起動してください（.\scripts\start-comfyui.ps1 または Comfy Desktop）。" -ForegroundColor Cyan
