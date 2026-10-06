<#
.SYNOPSIS
  Put the files of the image models in config\host_models.json into tools\comfyui\models (idempotent).

.DESCRIPTION
  Every image entry of config\host_models.json names a checkpoint or diffusion model ("ckpt"), its LoRAs and, for
  Krea 2 / Anima / Chroma, a text encoder and a VAE (workflows\maps\<family>.json "models"). This script:

  1. Hard-links the files that are already on this machine (the model folders of an earlier ComfyUI such as
     Documents\ComfyUI\models, and -ModelsDir) into tools\comfyui\models. Checkpoints from Civitai (yiffInHell,
     Rekemono, Indigo Furry Mix Anima, Wulver ...) are never downloaded: place them yourself.
  2. Fetches the Hugging Face files listed under "downloads" (Krea 2: qwen3vl_4b_fp8_scaled + qwen_image_vae;
     Anima: qwen_3_06b_base + qwen_image_vae) through the Hugging Face cache (hf download) and checks their SHA-256.
  3. Lists what is still missing; GET /models shows those models as unavailable with the same reason.
  -Ids limits it to some models (e.g. -Ids yiffinhell-vantablack on a machine with little disk space).

.EXAMPLE
  .\scripts\setup-image-models.ps1
  .\scripts\setup-image-models.ps1 -Ids wulver,indigofurrymix-anima
  .\scripts\setup-image-models.ps1 -ModelsDir D:\ComfyUI\models
#>
param(
    [string[]]$Ids = @(),
    [string[]]$ModelsDir = @(),
    [switch]$SkipHashCheck
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\common.ps1")
Initialize-DotEnv | Out-Null
$root = Get-RepoRoot
$layout = Get-ComfyLayout
if (-not $layout) { throw "ComfyUI の場所が分かりません。先に .\scripts\setup-comfyui.ps1 を実行してください。" }

$sources = @(($ModelsDir | ForEach-Object { $_ -split '[,;]' } | ForEach-Object { $_.Trim() } | Where-Object { $_ }) + (Get-KnownModelDirs)) |
    Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique
$idList = (@($Ids | ForEach-Object { $_ -split '[,;]' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })) -join ","
Push-Location $root
try { $files = (Invoke-Native uv run --quiet python scripts\host_models.py wanted --ids $idList | Select-Object -Last 1) | ConvertFrom-Json }
finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { throw "config\host_models.json を読めません" }

Write-Step "Hugging Face から取得するファイル（テキストエンコーダ / VAE）"
foreach ($item in @($files | Where-Object { $_.download })) {
    $d = $item.download
    $found = Import-ComfyModel $layout @($d.folder) $d.name $sources
    $dest = if ($found) { $found } else { Join-Path $layout.ModelsDir "$($d.folder)\$($d.name)" }
    if (-not $found) { Get-HfFile $d.repo $d.file $dest $d.sha256 ([int64]$d.size) }
    if ((Get-Item $dest).Length -ne [int64]$d.size) { throw "サイズが一致しません: $dest（期待 $($d.size)）" }
    if ($SkipHashCheck) { Write-Ok "$($d.name)（$($item.family)、SHA-256 の照合は省略）"; continue }
    $actual = Get-FileSha256 $dest
    if ($actual -ne $d.sha256) { throw "SHA-256 が一致しません: $dest（期待 $($d.sha256) / 実際 $actual）" }
    Write-Ok "$($d.name)（$($item.family)）"
}

Write-Step "画像モデルのファイル（$($layout.ModelsDir)）"
$missing = @()
foreach ($item in @($files | Where-Object { -not $_.download })) {
    if ($item.kind -eq "lora") {
        $name = Resolve-LoraName $item.name (@($layout.ModelsDir) + $sources)
        $path = if ($name) { Import-ComfyModel $layout @("loras") $name $sources } else { $null }
    } else {
        $path = Import-ComfyModel $layout @($item.folders) $item.name $sources
    }
    if ($path) { Write-Ok "$($item.name)（$($item.model)）" }
    else { $missing += $item; Write-Warn2 "$($item.name) がありません（$($item.model)）。$($layout.ModelsDir)\$($item.folders[0]) に置いてください" }
}
Write-Host ""
if ($missing) {
    Write-Host "置いていないファイルのモデルは、画面のモデル一覧で「使えない」と表示されます。" -ForegroundColor Yellow
}
Write-Host "ComfyUI を再起動するとモデル一覧に反映されます: .\scripts\start-comfyui.ps1" -ForegroundColor Cyan
