<#
.SYNOPSIS
  Install ComfyUI for local-agent into tools\comfyui (idempotent).

.DESCRIPTION
  1. Clone Comfy-Org/ComfyUI at a pinned commit into tools\comfyui (git ignored).
  2. Create its own Python 3.12 venv (uv) and install PyTorch for this machine's GPU (-Torch auto): AMD's ROCm
     wheels for Radeon (repo.radeon.com, Windows), CUDA 12.8 for NVIDIA, else CPU. Then ComfyUI's requirements.
  3. Install eedali/LM_Connect (pinned commit) into custom_nodes. It is used only as an OpenAI-compatible client of
     the llama.cpp router; llama-cpp-python is NOT installed: no GGUF runs inside ComfyUI.
  4. Link custom_nodes\furry_ja to this repository's comfyui_nodes\furry_ja (directory junction).
  5. -ModelsDir: folders that already hold ComfyUI models (checkpoints\, loras\, ...) are read in place through
     tools\comfyui\extra_model_paths.yaml. Otherwise models go to tools\comfyui\models.
     -CheckpointUrl downloads the checkpoint (CKPT_NAME) into models\checkpoints.
  6. Save the paths to .env (COMFYUI_*), read by start-comfyui.ps1 and the other setup scripts.
  Start or restart ComfyUI afterwards with .\scripts\start-comfyui.ps1 (127.0.0.1:8188, --cache-none).

.EXAMPLE
  .\scripts\setup-comfyui.ps1
  .\scripts\setup-comfyui.ps1 -ModelsDir D:\ComfyUI\models
  .\scripts\setup-comfyui.ps1 -Torch cpu -CheckpointUrl https://example.invalid/yiffInHell.safetensors
#>
param(
    [string]$Ref = "e9027f2b30f37bb3052714eb08fcf479542f4fc0",   # ComfyUI v0.38.0-32, the version tested here
    [string]$Repo = "https://github.com/Comfy-Org/ComfyUI",
    [ValidateSet("auto", "rocm", "cuda", "cpu")]
    [string]$Torch = "auto",
    [string[]]$ModelsDir = @(),
    [string]$CheckpointUrl = "",
    [string]$CkptName = "",            # default: CKPT_NAME in .env
    [string]$LMConnectCommit = "422a08970fee5f3f525589bcb3170099007ae286",
    [switch]$ReinstallTorch
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\common.ps1")
Initialize-DotEnv | Out-Null

# AMD's PyTorch for Windows (ROCm 7.2), the build ComfyUI was tested with on the Radeon 890M. Order matters: the
# ROCm SDK packages first.
$RocmBase = "https://repo.radeon.com/rocm/windows/rocm-rel-7.2"
$RocmWheels = @(
    "$RocmBase/rocm_sdk_core-7.2.0.dev0-py3-none-win_amd64.whl",
    "$RocmBase/rocm_sdk_libraries_custom-7.2.0.dev0-py3-none-win_amd64.whl",
    "$RocmBase/rocm-7.2.0.dev0.tar.gz",
    "$RocmBase/torch-2.9.1%2Brocmsdk20260116-cp312-cp312-win_amd64.whl",
    "$RocmBase/torchaudio-2.9.1%2Brocmsdk20260116-cp312-cp312-win_amd64.whl",
    "$RocmBase/torchvision-0.24.1%2Brocmsdk20260116-cp312-cp312-win_amd64.whl"
)

Write-Step "前提ツール（git / uv）"
foreach ($tool in "git", "uv") {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "$tool が見つかりません（README の「必要なもの」）" }
}
Write-Ok "ok"

$dir = Get-RepoComfyDir
Write-Step "ComfyUI 本体（$dir）"
if (-not (Test-Path (Join-Path $dir ".git"))) {
    if (Test-Path $dir) { throw "$dir は git の作業ツリーではありません。削除してから再実行してください。" }
    Invoke-Native git clone --quiet $Repo $dir | Write-Host
    if ($LASTEXITCODE -ne 0) { throw "git clone $Repo に失敗しました" }
}
$head = (Invoke-Native git -C $dir rev-parse HEAD | Select-Object -First 1).Trim()
if (-not $head.StartsWith($Ref) -and -not $Ref.StartsWith($head)) {
    Invoke-Native git -C $dir fetch --quiet origin | Out-Null
    Invoke-Native git -C $dir checkout --quiet $Ref | Write-Host
    if ($LASTEXITCODE -ne 0) { throw "ComfyUI $Ref の checkout に失敗しました" }
}
Write-Ok (Invoke-Native git -C $dir log -1 --format="%h %ad %s" --date=short | Select-Object -First 1)

Write-Step "Python 環境（$dir\.venv、Python 3.12）"
$python = Join-Path $dir ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Invoke-Native uv venv --seed --python 3.12 (Join-Path $dir ".venv") | Select-Object -Last 2 | ForEach-Object { Write-Host "    $_" }
    if ($LASTEXITCODE -ne 0) { throw "uv venv に失敗しました" }
}
Write-Ok $python

function Install-Packages {
    Invoke-Native uv pip install --python $python @args | Select-Object -Last 3 | ForEach-Object { Write-Host "    $_" }
    if ($LASTEXITCODE -ne 0) { throw "uv pip install に失敗しました: $($args -join ' ')" }
}

if ($Torch -eq "auto") {
    $gpus = @(Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue | ForEach-Object { $_.Name })
    $Torch = Get-TorchVariant $gpus
    Write-Ok "GPU: $($gpus -join ' / ') -> $Torch"
}
Write-Step "PyTorch（$Torch）"
$current = Invoke-Native $python -c "import torch; print(torch.__version__)" | Select-Object -Last 1
$have = $LASTEXITCODE -eq 0
$torchOk = $have -and (($Torch -eq "rocm" -and $current -match "rocm") -or ($Torch -eq "cuda" -and $current -match "cu\d") -or
                       ($Torch -eq "cpu" -and $current -notmatch "rocm|cu\d"))
if ($torchOk -and -not $ReinstallTorch) {
    Write-Ok "installed: torch $current"
} else {
    switch ($Torch) {
        "rocm" { Install-Packages @RocmWheels }
        "cuda" { Install-Packages torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128 }
        "cpu" { Install-Packages torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu }
    }
    Write-Ok ("torch " + (Invoke-Native $python -c "import torch; print(torch.__version__)" | Select-Object -Last 1))
}

Write-Step "ComfyUI の依存（requirements.txt。torch はそのまま）"
Install-Packages -r (Join-Path $dir "requirements.txt")
Write-Ok "installed"

$layout = New-ComfyLayout $dir $python "tools"
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
    $lmHead = (git -C $lmConnect rev-parse HEAD).Trim()
    if ($lmHead -ne $LMConnectCommit) { Write-Warn2 "既存の LM_Connect は $lmHead（検証済みは $LMConnectCommit）。そのまま使います" }
    else { Write-Ok "already installed ($lmHead)" }
}
# requests / Pillow / numpy only; llama-cpp-python is never installed (no GGUF inside ComfyUI).
Install-Packages requests Pillow numpy

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

Write-Step "モデルの置き場"
$yaml = Join-Path $dir "extra_model_paths.yaml"
# "a,b" arrives as one string through powershell -File: split on , and ; as well.
$existing = @($ModelsDir | ForEach-Object { $_ -split '[,;]' } | ForEach-Object { $_.Trim() } | Where-Object { $_ } | ForEach-Object {
    if (-not (Test-Path $_)) { throw "-ModelsDir が見つかりません: $_" }
    (Resolve-Path $_).Path
})
if ($existing) {
    Write-TextFile $yaml (ConvertTo-ComfyExtraModelPathsYaml $existing)
    $layout.ExtraModelPaths = $yaml
    Write-Ok "extra_model_paths.yaml: $($existing -join '; ')"
} elseif (Test-Path $yaml) {
    $layout.ExtraModelPaths = $yaml   # kept from an earlier run
    Write-Ok "extra_model_paths.yaml (kept)"
}
Write-Ok "models: $($layout.ModelsDir)"
Save-ComfyLayout $layout
Write-Ok ".env の COMFYUI_* を更新しました"

Write-Step "チェックポイント"
if (-not $CkptName) { $CkptName = Get-DotEnvValue "CKPT_NAME" "yiffInHell_yihVANTABLACK.safetensors" }
$found = Find-ComfyModel $layout "checkpoints" $CkptName
if (-not $found -and $CheckpointUrl) {
    $found = Join-Path $layout.ModelsDir "checkpoints\$CkptName"
    Save-Download $CheckpointUrl $found
}
if ($found) { Write-Ok "$CkptName ($found)" }
else { Write-Warn2 "$CkptName がありません。$($layout.ModelsDir)\checkpoints に置くか、-ModelsDir / -CheckpointUrl を指定してください。" }

Write-Host ""
Write-Host "ComfyUI を起動（再起動）してください: .\scripts\start-comfyui.ps1" -ForegroundColor Cyan
