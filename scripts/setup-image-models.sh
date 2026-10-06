#!/bin/bash
# The files of the image models in config/host_models.json, in tools/comfyui/models (macOS; the Windows script is
# setup-image-models.ps1). Files already on this Mac (an earlier ComfyUI's models folder, --models-dir) are linked;
# the Krea 2 / Anima encoders come from Hugging Face (config "downloads", SHA-256 checked). Civitai checkpoints are
# never downloaded: place them yourself. A model whose files are missing is shown as unavailable (GET /models).
#   scripts/setup-image-models.sh
#   scripts/setup-image-models.sh --ids yiffinhell-vantablack     only some models (little disk space)
#   scripts/setup-image-models.sh --models-dir /Volumes/x/ComfyUI/models
. "$(dirname "$0")/lib/common.sh"
init_env
ids="" extra=()
while [ $# -gt 0 ]; do
  case "$1" in
    --ids) ids="$2"; shift 2 ;;
    --models-dir) extra+=("$2"); shift 2 ;;
    *) die "不明なオプション: $1" ;;
  esac
done
models="$(env_get COMFYUI_MODELS_DIR "$REPO_ROOT/tools/comfyui/models")"
mkdir -p "$models"
wanted="$(uv_python scripts/host_models.py wanted --ids "$ids" | tail -n 1)"
missing=0

step "Hugging Face から取得するファイル（テキストエンコーダ / VAE）"
while IFS=$'\t' read -r folder repo file name size sha family; do
  [ -n "$folder" ] || continue
  found="$(import_comfy_model "$models" "$folder" "$name" ${extra[@]+"${extra[@]}"})"
  if [ -z "$found" ]; then hf_file "$repo" "$file" "$models/$folder/$name" "$sha" "$size"; else ok "$name（$family）"; fi
done < <(printf '%s' "$wanted" | python3 -c '
import json,sys
for f in json.load(sys.stdin):
    d = f.get("download")
    if d: print("\t".join([d["folder"], d["repo"], d["file"], d["name"], str(d["size"]), d["sha256"], f["family"]]))')

step "画像モデルのファイル（$models）"
while IFS=$'\t' read -r folders name kind model; do
  [ -n "$name" ] || continue
  if [ "$kind" = "lora" ]; then
    found=""
    for candidate in "$name" "$name.safetensors"; do
      [ -n "$found" ] || found="$(import_comfy_model "$models" loras "$candidate" ${extra[@]+"${extra[@]}"})"
    done
  else
    found="$(import_comfy_model "$models" "$folders" "$name" ${extra[@]+"${extra[@]}"})"
  fi
  if [ -n "$found" ]; then ok "$name（$model）"; else warn "$name がありません（$model）。$models/${folders%% *} に置いてください"; missing=1; fi
done < <(printf '%s' "$wanted" | python3 -c '
import json,sys
for f in json.load(sys.stdin):
    if not f.get("download"): print("\t".join([" ".join(f["folders"]), f["name"], f["kind"], f["model"]]))')

[ "$missing" = 0 ] || echo "置いていないファイルのモデルは、画面のモデル一覧で「使えない」と表示されます。"
echo "ComfyUI を再起動するとモデル一覧に反映されます: scripts/start-comfyui.sh"
