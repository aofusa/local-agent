#!/bin/bash
# Install ComfyUI for local-agent into tools/comfyui on macOS (idempotent).
#  1. Comfy-Org/ComfyUI at the commit tested on Windows, its own Python 3.12 venv, PyTorch from PyPI (Metal / MPS on
#     Apple silicon), ComfyUI's requirements.
#  2. eedali/LM_Connect (pinned) as an OpenAI-compatible client of the LLM router; no GGUF runs inside ComfyUI.
#  3. custom_nodes/furry_ja -> comfyui_nodes/furry_ja (symbolic link).
#  4. The image models: scripts/setup-image-models.sh (links what is already on this Mac, fetches encoders).
#   scripts/setup-comfyui.sh [--ids yiffinhell-vantablack]   (--ids is passed to setup-image-models.sh)
. "$(dirname "$0")/lib/common.sh"
require_macos
init_env
ref="e9027f2b30f37bb3052714eb08fcf479542f4fc0"   # ComfyUI v0.38.0-32 (scripts/setup-comfyui.ps1)
lmconnect="422a08970fee5f3f525589bcb3170099007ae286"
dir="$REPO_ROOT/tools/comfyui"

step "ComfyUI 本体（$dir）"
command -v git >/dev/null && command -v uv >/dev/null || die "git と uv が必要です（brew install git uv）"
if [ ! -d "$dir/.git" ]; then
  [ -e "$dir" ] && die "$dir は git の作業ツリーではありません。削除してから再実行してください"
  git clone --quiet https://github.com/Comfy-Org/ComfyUI "$dir"
fi
head="$(git -C "$dir" rev-parse HEAD)"
if [ "$head" != "$ref" ]; then
  git -C "$dir" fetch --quiet origin
  git -C "$dir" checkout --quiet "$ref"
fi
ok "$(git -C "$dir" log -1 --format='%h %ad %s' --date=short)"

step "Python 環境（$dir/.venv、Python 3.12）"
python="$dir/.venv/bin/python"
[ -x "$python" ] || uv venv --seed --python 3.12 "$dir/.venv" >/dev/null
ok "$python"
step "PyTorch（MPS）"
"$python" -c "import torch" 2>/dev/null || uv pip install --quiet --python "$python" torch torchvision torchaudio
"$python" -c "import torch; print('    torch', torch.__version__, 'mps', torch.backends.mps.is_available())"
step "ComfyUI の依存（requirements.txt）"
uv pip install --quiet --python "$python" -r "$dir/requirements.txt"
ok "installed"

step "LM_Connect（eedali/LM_Connect）"
nodes="$dir/custom_nodes"
if [ ! -d "$nodes/LM_Connect" ]; then
  git clone --quiet https://github.com/eedali/LM_Connect "$nodes/LM_Connect"
  git -C "$nodes/LM_Connect" checkout --quiet "$lmconnect"
fi
uv pip install --quiet --python "$python" requests Pillow numpy
ok "$(git -C "$nodes/LM_Connect" rev-parse --short HEAD)"

step "furry_ja ノードをリンク"
link="$nodes/furry_ja"
if [ -L "$link" ] || [ ! -e "$link" ]; then
  rm -f "$link"
  ln -s "$REPO_ROOT/comfyui_nodes/furry_ja" "$link"
  ok "$link -> comfyui_nodes/furry_ja"
else
  die "$link は通常のフォルダです。削除またはリネームしてから再実行してください"
fi

env_set COMFYUI_MAIN_DIR "$dir"
env_set COMFYUI_PYTHON "$python"
env_set COMFYUI_CUSTOM_NODES_DIR "$nodes"
env_set COMFYUI_MODELS_DIR "$dir/models"
ok ".env の COMFYUI_* を更新しました"

"$REPO_ROOT/scripts/setup-image-models.sh" "$@"
