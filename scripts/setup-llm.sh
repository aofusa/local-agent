#!/bin/bash
# Prepare the LLM router on macOS (idempotent): model files, the router preset tools/llm/models.ini, .env.
#
# One preset section per inference model of config/host_models.json whose files are on this Mac:
#   - an MLX build (config "mlx", downloaded by scripts/setup-mlx.sh into tools/models/mlx) runs on MLX — preferred
#     on Apple silicon;
#   - else its GGUF runs on llama-server (PrismML fork, Metal): the Qwen3.8 27B from tools/models/llm (or
#     --source-model, or LM Studio's folder), the Bonsai 2 27B abliterated from scripts/setup-search-models.sh.
# A model with no file is left out and shown as unavailable (GET /models). The router's tag model (LLM_MODEL, the
# one ComfyUI's workflow calls) is the first model that is there.
#   scripts/setup-llm.sh
#   scripts/setup-llm.sh --source-model ~/models/Huihui-Qwen3.8-27B-abliterated-IQ3_M.gguf
#   scripts/setup-llm.sh --download        fetch the 27B's GGUF (+ mmproj) and requantize it (~30 GB of disk)
. "$(dirname "$0")/lib/common.sh"
require_macos
init_env

source_model="" download=0 port=""
while [ $# -gt 0 ]; do
  case "$1" in
    --source-model) source_model="$2"; shift 2 ;;
    --download) download=1; shift ;;
    --port) port="$2"; shift 2 ;;
    *) die "不明なオプション: $1" ;;
  esac
done
spec="$REPO_ROOT/config/llm_model.json"
[ -n "$port" ] || port="$(json_get "$spec" "d['port']")"

step "llama.cpp"
server="$(env_get LLM_SERVER)"
[ -x "$server" ] || { "$REPO_ROOT/scripts/setup-llamacpp.sh"; server="$(env_get LLM_SERVER)"; }
ok "$server"

step "Qwen3.8 27B abliterated（GGUF）"
model_dir="$REPO_ROOT/tools/models/llm"
mkdir -p "$model_dir"
quant="$(json_get "$spec" "d['quant']")"
qfile="$(json_get "$spec" "d['quantized'][d['quant']]['file']")"
qsize="$(json_get "$spec" "d['quantized'][d['quant']]['size']")"
target="$model_dir/$qfile"
mmproj="$model_dir/$(json_get "$spec" "d['mmproj']['file']")"
if [ -n "$source_model" ]; then
  [ -f "$source_model" ] || die "--source-model が見つかりません: $source_model"
  target="$model_dir/$(basename "$source_model")"
  [ -e "$target" ] || link_file "$target" "$source_model"
  sibling="$(dirname "$source_model")/$(basename "$mmproj")"
  [ -f "$sibling" ] && [ ! -e "$mmproj" ] && link_file "$mmproj" "$sibling"
fi
if [ ! -s "$target" ] && [ -d "$HOME/.lmstudio/models" ]; then
  found="$(find "$HOME/.lmstudio/models" -name "$qfile" -size +1G 2>/dev/null | head -n 1 || true)"
  [ -n "$found" ] && link_file "$target" "$found" && ok "linked $target（$found）"
fi
if [ ! -s "$target" ] && [ "$download" = 1 ]; then
  repo="$(json_get "$spec" "d['repo']")"
  src="$model_dir/$(json_get "$spec" "d['file']")"
  hf_file "$repo" "$(basename "$src")" "$src" "$(json_get "$spec" "d['sha256']")" "$(json_get "$spec" "d['size']")"
  hf_file "$repo" "$(basename "$mmproj")" "$mmproj" "$(json_get "$spec" "d['mmproj']['sha256']")" \
    "$(json_get "$spec" "d['mmproj']['size']")"
  step "$quant に再量子化（20〜30 分）"
  "$(dirname "$server")/llama-quantize" --allow-requantize "$src" "$target.partial" "$quant" "$(sysctl -n hw.perflevel0.physicalcpu 2>/dev/null || echo 4)" >/dev/null
  mv "$target.partial" "$target"
fi
if [ -s "$target" ]; then
  [ "$(basename "$target")" != "$qfile" ] || [ "$(file_size "$target")" = "$qsize" ] || warn "サイズが固定値と違います: $target"
  ok "$target"
else
  warn "Qwen3.8 27B の GGUF がありません（--source-model / --download）。モデル一覧では使えないと表示します"
fi

step "ルータのプリセット（config/host_models.json の推論モデルごと）"
engine="llamacpp"
if [ -x "$REPO_ROOT/tools/mlx/.venv/bin/python" ]; then engine="mlx"; fi
preset="$REPO_ROOT/tools/llm/models.ini"
models_dir="$(env_get BONSAI_MODELS_DIR "$REPO_ROOT/tools/models")"
qwen_args=()
[ -s "$target" ] && qwen_args=(--qwen-model "$target" --qwen-mmproj "$mmproj")
out="$(uv_python scripts/host_models.py preset --out "$preset" --offload 1.0 --engine "$engine" \
  --models-dir "$models_dir" --sleep-idle "$(json_get "$spec" "d['sleep_idle_s']")" ${qwen_args[@]+"${qwen_args[@]}"} | tail -n 1)"
first="$(printf '%s' "$out" | python3 -c '
import json,sys
sections=json.load(sys.stdin)
for s in sections:
    if "skipped" in s: print("skip", s["skipped"]["id"], s["skipped"]["reason"], file=sys.stderr)
    else: print("ok", s["id"], s["settings"].get("engine", "llamacpp"), "ctx", s["settings"].get("ctx-size"), file=sys.stderr)
ids=[s["id"] for s in sections if "id" in s]
print(ids[0] if ids else "")')"
[ -n "$first" ] || die "使える推論モデルがありません（setup-search-models.sh で Bonsai を、--source-model で Qwen を入れてください）"
ok "$preset（engine: $engine）"

step ".env"
url="http://127.0.0.1:$port/v1"
ctx="$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(next(m['context'] for m in d['inference'] if m['id']==sys.argv[2]))" "$REPO_ROOT/config/host_models.json" "$first")"
env_set LLM_PRESET "$preset"
env_set LLM_PORT "$port"
env_set LLM_URL "$url"
env_set LLM_MODEL "$first"
env_set LLM_CONTEXT "$ctx"
env_set LLM_ENGINE "$engine"
ok "LLM_URL=$url LLM_MODEL=$first（画像のタグ生成のモデル）"

step "ワークフローの接続先"
current="$(json_get "$REPO_ROOT/workflows/furry_ja_api.json" "d['llm_backend']['inputs']['model'] + ' ' + d['llm_backend']['inputs']['base_url']")"
if [ "$current" = "$first $url" ]; then
  ok "workflows already use $first at $url"
else
  (cd "$REPO_ROOT" && LLM_MODEL="$first" LLM_URL="$url" uv run --quiet python scripts/build_workflows.py)
  ok "regenerated workflows（$current -> $first $url）"
fi
echo
echo "完了。scripts/start-llm.sh でルータを起動します（モデルは最初の要求で読み込み、使い終わると unload します）。"
