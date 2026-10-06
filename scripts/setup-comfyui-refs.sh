#!/bin/bash
# Nodes and models for character / style / pose references (SDXL) on macOS: ComfyUI_IPAdapter_plus and
# comfyui_controlnet_aux (the commits pinned in setup-comfyui-refs.ps1), ControlNet Union promax, IP-Adapter Plus
# SDXL, CLIP-ViT-H, DWPose ONNX, Depth Anything V2 Small (~5 GB). Text-only and img2img runs work without them.
. "$(dirname "$0")/lib/common.sh"
python="$(env_get COMFYUI_PYTHON)"
nodes="$(env_get COMFYUI_CUSTOM_NODES_DIR)"
models="$(env_get COMFYUI_MODELS_DIR)"
[ -x "$python" ] || die "先に scripts/setup-comfyui.sh を実行してください"

node() {
  local dir="$nodes/$1"
  if [ ! -d "$dir" ]; then git clone --quiet "$2" "$dir" && git -C "$dir" checkout --quiet "$3"; fi
  ok "$1 $(git -C "$dir" rev-parse --short HEAD)"
}
model() {
  # model REPO FILE DEST: an existing copy (tools/comfyui/models or an earlier ComfyUI) is linked, not fetched.
  local rel="${3#"$models"/}"
  local found
  found="$(import_comfy_model "$models" "$(dirname "$rel")" "$(basename "$rel")")"
  [ -n "$found" ] && { ok "exists: $found"; return; }
  hf_file "$1" "$2" "$3"
}

step "カスタムノード（IP-Adapter / ControlNet 前処理）"
node ComfyUI_IPAdapter_plus https://github.com/cubiq/ComfyUI_IPAdapter_plus a0f451a5113cf9becb0847b92884cb10cbdec0ef
node comfyui_controlnet_aux https://github.com/Fannovel16/comfyui_controlnet_aux 0cd290477128d42cdc3e76a826a402d866e8c684
uv pip install --quiet --python "$python" "opencv-python<5" huggingface_hub scipy filelock einops pyyaml \
  scikit-image python-dateutil omegaconf addict yacs trimesh rtree scikit-learn matplotlib onnxruntime

step "モデル（$models）"
model xinsir/controlnet-union-sdxl-1.0 diffusion_pytorch_model_promax.safetensors \
  "$models/controlnet/controlnet-union-sdxl-1.0-promax.safetensors"
model h94/IP-Adapter sdxl_models/ip-adapter-plus_sdxl_vit-h.safetensors "$models/ipadapter/ip-adapter-plus_sdxl_vit-h.safetensors"
model h94/IP-Adapter models/image_encoder/model.safetensors "$models/clip_vision/CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"
ckpts="$nodes/comfyui_controlnet_aux/ckpts"
hf_file yzd-v/DWPose dw-ll_ucoco_384.onnx "$ckpts/yzd-v/DWPose/dw-ll_ucoco_384.onnx"
hf_file depth-anything/Depth-Anything-V2-Small depth_anything_v2_vits.pth \
  "$ckpts/depth-anything/Depth-Anything-V2-Small/depth_anything_v2_vits.pth"
echo "完了。ComfyUI を再起動してください（scripts/start-comfyui.sh）"
