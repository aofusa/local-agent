<#
.SYNOPSIS
  Install the reference-image nodes and models (IP-Adapter, ControlNet, pose/depth preprocessors). Idempotent.

.DESCRIPTION
  Needed for the multi-image role templates (style / pose / character). Text-only and plain img2img work without it.
  1. Clone cubiq/ComfyUI_IPAdapter_plus and Fannovel16/comfyui_controlnet_aux (pinned commits) into custom_nodes.
  2. Install controlnet_aux's light dependencies into ComfyUI's python. torch / numpy are not changed;
     onnxruntime-gpu (CUDA only) and mediapipe are skipped: DWPose runs as TorchScript on the ComfyUI device.
  3. Download the models once, so no node downloads anything at run time:
       models\controlnet\controlnet-union-sdxl-1.0-promax.safetensors   (xinsir, openpose/depth/canny)
       models\ipadapter\ip-adapter-plus_sdxl_vit-h.safetensors            (h94)
       models\clip_vision\CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors     (h94 image encoder)
       custom_nodes\comfyui_controlnet_aux\ckpts\...                      (DWPose TorchScript pose model, Depth Anything V2 Small)
  Restart ComfyUI afterwards.

.EXAMPLE
  .\scripts\setup-comfyui-refs.ps1
#>
param(
    [string]$IPAdapterCommit = "a0f451a5113cf9becb0847b92884cb10cbdec0ef",
    [string]$ControlNetAuxCommit = "0cd290477128d42cdc3e76a826a402d866e8c684",
    [string]$ModelsDir = ""            # default: <ComfyUI base or main dir>\models
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\common.ps1")
Initialize-DotEnv | Out-Null

$layout = Get-ComfyLayout
if (-not $layout -or -not $layout.Python) { throw "ComfyUI の場所が分かりません。先に .\scripts\setup-comfyui.ps1 を実行してください。" }
if (-not $ModelsDir) {
    $root = if ($layout.BaseDir) { $layout.BaseDir } else { $layout.MainDir }
    $ModelsDir = Join-Path $root "models"
}

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

Write-Step "カスタムノード（IP-Adapter / ControlNet 前処理）"
Install-CustomNode "ComfyUI_IPAdapter_plus" "https://github.com/cubiq/ComfyUI_IPAdapter_plus" $IPAdapterCommit | Out-Null
$aux = Install-CustomNode "comfyui_controlnet_aux" "https://github.com/Fannovel16/comfyui_controlnet_aux" $ControlNetAuxCommit

Write-Step "comfyui_controlnet_aux の依存（torch / numpy は変更しない）"
$deps = @("opencv-python<5", "huggingface_hub", "scipy", "filelock", "einops", "pyyaml", "scikit-image",
          "python-dateutil", "omegaconf", "addict", "yacs", "trimesh", "rtree", "scikit-learn", "matplotlib")
Invoke-Native $layout.Python -m pip install --disable-pip-version-check --quiet @deps | Write-Host
if ($LASTEXITCODE -ne 0) { throw "依存のインストールに失敗しました" }
Write-Ok "installed"

Write-Step "モデル（$ModelsDir）"
$hf = "https://huggingface.co"
Get-Model "$hf/xinsir/controlnet-union-sdxl-1.0/resolve/main/diffusion_pytorch_model_promax.safetensors" `
    (Join-Path $ModelsDir "controlnet\controlnet-union-sdxl-1.0-promax.safetensors")
Get-Model "$hf/h94/IP-Adapter/resolve/main/sdxl_models/ip-adapter-plus_sdxl_vit-h.safetensors" `
    (Join-Path $ModelsDir "ipadapter\ip-adapter-plus_sdxl_vit-h.safetensors")
Get-Model "$hf/h94/IP-Adapter/resolve/main/models/image_encoder/model.safetensors" `
    (Join-Path $ModelsDir "clip_vision\CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors")
$ckpts = Join-Path $aux "ckpts"
Get-Model "$hf/hr16/DWPose-TorchScript-BatchSize5/resolve/main/dw-ll_ucoco_384_bs5.torchscript.pt" `
    (Join-Path $ckpts "hr16\DWPose-TorchScript-BatchSize5\dw-ll_ucoco_384_bs5.torchscript.pt")
Get-Model "$hf/depth-anything/Depth-Anything-V2-Small/resolve/main/depth_anything_v2_vits.pth" `
    (Join-Path $ckpts "depth-anything\Depth-Anything-V2-Small\depth_anything_v2_vits.pth")

Write-Step "完了。ComfyUI を再起動してください（.\scripts\start-comfyui.ps1）"
