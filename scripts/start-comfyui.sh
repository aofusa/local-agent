#!/bin/bash
# Start ComfyUI (tools/comfyui, scripts/setup-comfyui.sh) headless on 127.0.0.1:8188 (loopback only) with
# --cache-none, in the foreground. COMFYUI_EXTRA_ARGS in .env adds options.
. "$(dirname "$0")/lib/common.sh"
port="${1:-8188}"
listening "$port" && { echo "ComfyUI is already listening on $port"; exit 0; }
dir="$(env_get COMFYUI_MAIN_DIR "$REPO_ROOT/tools/comfyui")"
python="$(env_get COMFYUI_PYTHON "$dir/.venv/bin/python")"
[ -x "$python" ] || die "ComfyUI がありません。先に scripts/setup-comfyui.sh を実行してください"
extra="$(env_get COMFYUI_EXTRA_ARGS)"
cd "$dir"
# PyTorch's MPS backend: fall back to the CPU for an operator Metal lacks instead of failing the run.
export PYTORCH_ENABLE_MPS_FALLBACK=1
echo "ComfyUI: $python main.py --listen 127.0.0.1 --port $port --cache-none $extra"
# shellcheck disable=SC2086
exec "$python" -s main.py --listen 127.0.0.1 --port "$port" --cache-none $extra
