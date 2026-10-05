<#
.SYNOPSIS
  Download the text encoder and VAE that the Chroma1-HD family (COMFY_MODEL_FAMILY=flux) needs. Idempotent.

.DESCRIPTION
  Chroma1-HD ships as a diffusion model only (no text encoder, no VAE inside the file). The workflow loads:
    diffusion model  CHROMA_UNET_NAME (default chroma_v10HD.safetensors) from models\diffusion_models or models\checkpoints
                     (place it yourself; this script does not download the 17.8 GB model)
    text encoder     models\text_encoders\t5xxl_fp8_e4m3fn.safetensors   (comfyanonymous/flux_text_encoders)
    VAE              models\vae\ae.safetensors                          (lodestones/Chroma1-HD vae, Flux VAE)
  Then, unless -NoConvert, it writes models\diffusion_models\<name>_fp8_e4m3fn.safetensors (~8.9 GB) from the BF16
  file with scripts\convert_chroma_fp8.py, tensor by tensor. The loader node uses it automatically when it exists:
  casting the BF16 file at load time needs the whole 17.8 GB state dict in RAM, which a 24 GB machine cannot spare.
  ComfyUI 0.38 has CLIPLoader type=chroma, T5TokenizerOptions and ModelSamplingAuraFlow; restart ComfyUI once
  so it loads the furry_ja loader node (FurryJaDiffusionLoaderAfterEject).

.EXAMPLE
  .\scripts\setup-comfyui-chroma.ps1
#>
param(
    [string]$ModelsDir = "",           # default: tools\comfyui\models
    [switch]$NoConvert                 # keep only the BF16 file (machines with 32 GB+ RAM)
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\common.ps1")
Initialize-DotEnv | Out-Null

$layout = Get-ComfyLayout
if (-not $layout) { throw "ComfyUI の場所が分かりません。先に .\scripts\setup-comfyui.ps1 を実行してください。" }
if (-not $ModelsDir) { $ModelsDir = $layout.ModelsDir }

function Get-Model([string]$Url, [string]$Folder, [string]$Name) {
    # Any folder ComfyUI reads (its own models, -ModelsDir of setup-comfyui.ps1) may already have it.
    $found = Find-ComfyModel $layout $Folder $Name
    if ($found) { Write-Ok "exists: $found"; return }
    Save-Download $Url (Join-Path $ModelsDir "$Folder\$Name")
}

Write-Step "Chroma1-HD のテキストエンコーダと VAE（$ModelsDir）"
$hf = "https://huggingface.co"
Get-Model "$hf/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp8_e4m3fn.safetensors" "text_encoders" "t5xxl_fp8_e4m3fn.safetensors"
Get-Model "$hf/lodestones/Chroma1-HD/resolve/main/vae/diffusion_pytorch_model.safetensors" "vae" "ae.safetensors"

$unet = Get-DotEnvValue "CHROMA_UNET_NAME" "chroma_v10HD.safetensors"
$found = @("diffusion_models", "unet", "checkpoints") | ForEach-Object { Find-ComfyModel $layout $_ $unet } | Where-Object { $_ }
if ($found) {
    $source = @($found)[0]
    Write-Ok "diffusion model: $source"
    $stem = [IO.Path]::GetFileNameWithoutExtension($unet)
    if (-not $NoConvert -and -not $stem.EndsWith("_fp8_e4m3fn")) {
        $existing = Find-ComfyModel $layout "diffusion_models" "$($stem)_fp8_e4m3fn.safetensors"
        $target = Join-Path $ModelsDir "diffusion_models\$($stem)_fp8_e4m3fn.safetensors"
        if ($existing) { Write-Ok "exists: $existing" }
        else {
            if (-not $layout.Python) { throw "ComfyUI の python が分かりません。先に .\scripts\setup-comfyui.ps1 を実行してください。" }
            Write-Step "fp8 へ変換（数分）: $target"
            Invoke-Native $layout.Python (Join-Path $PSScriptRoot "convert_chroma_fp8.py") $source $target | Write-Host
            if ($LASTEXITCODE -ne 0) { throw "fp8 への変換に失敗しました" }
        }
    }
}
else { Write-Warn2 "$unet が models\diffusion_models / checkpoints にありません。Chroma1-HD を置いてください（https://huggingface.co/lodestones/Chroma1-HD）" }

Write-Step "完了。.env の COMFY_MODEL_FAMILY=flux と CKPT_NAME で Chroma に切り替え、LangGraph を再起動してください"
