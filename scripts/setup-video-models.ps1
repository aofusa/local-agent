<#
.SYNOPSIS
  Prepare the video tab (Wan 2.2 image-to-video): ComfyUI-GGUF and the files of the video models in
  config\host_models.json (idempotent).

.DESCRIPTION
  1. Clones city96/ComfyUI-GGUF (pinned commit) into tools\comfyui\custom_nodes and installs its only dependency
     (gguf) into ComfyUI's venv. It reads the Wan experts' GGUF files in PyTorch; llama-cpp-python is not installed.
  2. Fetches the Hugging Face files of the video model ("downloads" of config\host_models.json: the high- and
     low-noise GGUF experts, umT5-XXL fp8, the Wan 2.1 VAE and the lightx2v 4-step LoRAs) through the Hugging Face
     cache (hf download) and checks their SHA-256. A file already on this machine is hard-linked instead.
  3. Imports the kemono LoRAs (Furry Enhancer Wan 2.2, from Civitai) from the model folders of an earlier ComfyUI on
     this machine (Documents\ComfyUI\models\loras ...) or -ModelsDir. They are never downloaded: place them yourself.
  By default only the default video model (DEFAULT_VIDEO_MODEL, else the first entry): 23 to 28 GB.
  -Ids picks others. Restart ComfyUI afterwards.

.EXAMPLE
  .\scripts\setup-video-models.ps1
  .\scripts\setup-video-models.ps1 -Ids wan22-i2v-a14b-q4,wan22-i2v-a14b-q3
  .\scripts\setup-video-models.ps1 -ModelsDir D:\ComfyUI\models
#>
param(
    [string[]]$Ids = @(),
    [string[]]$ModelsDir = @(),
    [string]$GgufCommit = "6ea2651e7df66d7585f6ffee804b20e92fb38b8a",
    [switch]$SkipHashCheck
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\common.ps1")
Initialize-DotEnv | Out-Null
$root = Get-RepoRoot
$layout = Get-ComfyLayout
if (-not $layout -or -not $layout.Python) { throw "ComfyUI の場所が分かりません。先に .\scripts\setup-comfyui.ps1 を実行してください。" }

function Install-CustomNode([string]$Name, [string]$Url, [string]$Commit) {
    $dir = Join-Path $layout.CustomNodesDir $Name
    if (-not (Test-Path $dir)) {
        Invoke-Native git clone --quiet $Url $dir | Write-Host
        if ($LASTEXITCODE -ne 0) { throw "git clone $Url に失敗しました" }
        Invoke-Native git -C $dir checkout --quiet $Commit | Write-Host
        if ($LASTEXITCODE -ne 0) { throw "$Name $Commit の checkout に失敗しました" }
        Write-Ok "$Name cloned at $Commit"
    } else {
        $head = (git -C $dir rev-parse HEAD).Trim()
        if ($head -ne $Commit) { Write-Warn2 "既存の $Name は $head（検証済みは $Commit）。そのまま使います" }
        else { Write-Ok "$Name already installed" }
    }
    $dir
}

Write-Step "ComfyUI-GGUF（Wan 2.2 の GGUF を PyTorch で読む。llama-cpp-python は入れない）"
Install-CustomNode "ComfyUI-GGUF" "https://github.com/city96/ComfyUI-GGUF" $GgufCommit | Out-Null
Invoke-Native uv pip install --python $layout.Python "gguf>=0.13.0" | Select-Object -Last 2 | Write-Host
if ($LASTEXITCODE -ne 0) { throw "gguf のインストールに失敗しました" }
Write-Ok "gguf"

$sources = @(($ModelsDir | ForEach-Object { $_ -split '[,;]' } | ForEach-Object { $_.Trim() } | Where-Object { $_ }) + (Get-KnownModelDirs)) |
    Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique
$idList = (@($Ids | ForEach-Object { $_ -split '[,;]' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })) -join ","
Push-Location $root
try { $out = Invoke-Native uv run --quiet python scripts\host_models.py video-wanted --ids $idList }
finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { throw "config\host_models.json の動画モデルを読めません: $($out -join ' ')" }
$files = ($out | Select-Object -Last 1) | ConvertFrom-Json

Write-Step "Hugging Face から取得するファイル（Wan 2.2 のモデル、umT5、VAE、lightx2v の LoRA）"
foreach ($item in @($files | Where-Object { $_.download })) {
    $d = $item.download
    $found = Import-ComfyModel $layout @($d.folder) $d.name $sources
    $dest = if ($found) { $found } else { Join-Path $layout.ModelsDir "$($d.folder)\$($d.name)" }
    if (-not $found) { Get-HfFile $d.repo $d.file $dest $d.sha256 ([int64]$d.size) }
    if ((Get-Item $dest).Length -ne [int64]$d.size) { throw "サイズが一致しません: $dest（期待 $($d.size)）" }
    if ($SkipHashCheck) { Write-Ok "$($d.name)（$($item.model)、SHA-256 の照合は省略）"; continue }
    $actual = Get-FileSha256 $dest
    if ($actual -ne $d.sha256) { throw "SHA-256 が一致しません: $dest（期待 $($d.sha256) / 実際 $actual）" }
    Write-Ok "$($d.name)（$($item.model)）"
}

Write-Step "ケモノ向けの LoRA など、利用者が置くファイル（$($layout.ModelsDir)）"
$missing = @()
foreach ($item in @($files | Where-Object { -not $_.download })) {
    if ($item.kind -eq "lora") {
        $name = Resolve-LoraName $item.name (@($layout.ModelsDir) + $sources)
        $path = if ($name) { Import-ComfyModel $layout @("loras") $name $sources } else { $null }
    } else {
        $path = Import-ComfyModel $layout @($item.folders) $item.name $sources
    }
    if ($path) { Write-Ok "$($item.name)（$($item.model)）" }
    else { $missing += $item; Write-Warn2 "$($item.name) がありません（$($item.model)）。Civitai などから入手して $($layout.ModelsDir)\$($item.folders[0]) に置いてください" }
}
Write-Host ""
if ($missing) {
    Write-Host "置いていないファイルの動画モデルは、画面の動画モデルの一覧で「使えない」と表示されます。" -ForegroundColor Yellow
}
Write-Host "ComfyUI を再起動すると反映されます: .\scripts\start-comfyui.ps1" -ForegroundColor Cyan
