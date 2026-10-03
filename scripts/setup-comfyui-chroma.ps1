<#
.SYNOPSIS
  Download the text encoder and VAE that the Chroma1-HD family (COMFY_MODEL_FAMILY=chroma_hd) needs. Idempotent.

.DESCRIPTION
  Chroma1-HD ships as a diffusion model only (no text encoder, no VAE inside the file). The workflow loads:
    diffusion model  CHROMA_UNET_NAME (default chroma_v10HD.safetensors) from models\diffusion_models or models\checkpoints
                     (place it yourself; this script does not download the 17.8 GB model)
    text encoder     models\text_encoders\t5xxl_fp8_e4m3fn.safetensors   (comfyanonymous/flux_text_encoders)
    VAE              models\vae\ae.safetensors                          (lodestones/Chroma1-HD vae, Flux VAE)
  No custom node is needed: ComfyUI 0.38 has CLIPLoader type=chroma, T5TokenizerOptions and ModelSamplingAuraFlow.
  Restart is not needed for new model files.

.EXAMPLE
  .\scripts\setup-comfyui-chroma.ps1
#>
param(
    [string]$ModelsDir = ""            # default: <ComfyUI base or main dir>\models
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\common.ps1")
Initialize-DotEnv | Out-Null

$layout = Get-ComfyLayout
if (-not $ModelsDir) {
    if (-not $layout) { throw "ComfyUI の場所が分かりません。先に .\scripts\setup-comfyui.ps1 を実行してください。" }
    $root = if ($layout.BaseDir) { $layout.BaseDir } else { $layout.MainDir }
    $ModelsDir = Join-Path $root "models"
}

function Get-Model([string]$Url, [string]$Path) {
    if ((Test-Path $Path) -and (Get-Item $Path).Length -gt 0) { Write-Ok "exists: $Path"; return }
    New-Item -ItemType Directory -Force (Split-Path $Path) | Out-Null
    $partial = "$Path.partial"
    Write-Host "    downloading $Url"
    Invoke-Native curl.exe -L --fail --retry 3 -C - -o $partial $Url | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "ダウンロードに失敗しました: $Url" }
    Move-Item -Force $partial $Path
    Write-Ok "saved: $Path"
}

Write-Step "Chroma1-HD のテキストエンコーダと VAE（$ModelsDir）"
$hf = "https://huggingface.co"
Get-Model "$hf/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp8_e4m3fn.safetensors" `
    (Join-Path $ModelsDir "text_encoders\t5xxl_fp8_e4m3fn.safetensors")
Get-Model "$hf/lodestones/Chroma1-HD/resolve/main/vae/diffusion_pytorch_model.safetensors" `
    (Join-Path $ModelsDir "vae\ae.safetensors")

$unet = Get-DotEnvValue "CHROMA_UNET_NAME" "chroma_v10HD.safetensors"
$found = @("diffusion_models", "unet", "checkpoints") | ForEach-Object { Join-Path $ModelsDir "$_\$unet" } | Where-Object { Test-Path $_ }
if ($found) { Write-Ok "diffusion model: $(@($found)[0])" }
else { Write-Warn2 "$unet が models\diffusion_models / checkpoints にありません。Chroma1-HD を置いてください（https://huggingface.co/lodestones/Chroma1-HD）" }

Write-Step "完了。.env の COMFY_MODEL_FAMILY=chroma と CKPT_NAME で Chroma に切り替え、LangGraph を再起動してください"
