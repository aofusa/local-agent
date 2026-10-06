#!/bin/bash
# MLX for the LLM router on Apple silicon (idempotent): mlx-lm in tools/mlx/.venv and, for each inference model of
# config/host_models.json that names an MLX build ("mlx": {"repo": ...}), its files in tools/models/mlx/<repo>
# (linked from the Hugging Face cache). setup-llm.sh then gives those models "engine = mlx" and the router
# (scripts/start-llm.sh) runs them on MLX instead of llama.cpp.
#   scripts/setup-mlx.sh                       mlx-lm only (no model downloaded)
#   scripts/setup-mlx.sh --models qwen3.8-27b-abliterated
#   scripts/setup-mlx.sh --repo mlx-community/Qwen3-0.6B-4bit     a small model for a quick check
. "$(dirname "$0")/lib/common.sh"
require_macos
[ "$(uname -m)" = "arm64" ] || die "MLX は Apple silicon だけで動きます"
models="" repo=""
while [ $# -gt 0 ]; do
  case "$1" in
    --models) models="$2"; shift 2 ;;
    --repo) repo="$2"; shift 2 ;;
    *) die "不明なオプション: $1" ;;
  esac
done

step "mlx-lm（tools/mlx/.venv）"
venv="$REPO_ROOT/tools/mlx/.venv"
[ -x "$venv/bin/python" ] || uv venv --python 3.12 "$venv" >/dev/null
uv pip install --quiet --python "$venv/bin/python" "mlx-lm>=0.28"
"$venv/bin/python" -c "import mlx_lm, mlx.core as mx; print('    mlx-lm', mlx_lm.__version__, 'device', mx.default_device())"

fetch_repo() {
  # fetch_repo REPO: the snapshot in the Hugging Face cache, linked as tools/models/mlx/<org>--<name>.
  local dest="$REPO_ROOT/tools/models/mlx/${1//\//--}"
  if [ -f "$dest/config.json" ]; then ok "exists: $dest"; return 0; fi
  local avail
  avail="$(df -g "$REPO_ROOT" | awk 'NR==2 {print $4}')"
  echo "    hf download $1（空き ${avail} GB）"
  local snap
  snap="$(hf_path download "$1")"
  [ -f "$snap/config.json" ] || die "hf download $1 に失敗しました"
  mkdir -p "$(dirname "$dest")"
  rm -rf "$dest"
  ln -s "$snap" "$dest"
  ok "$dest -> $snap"
}

if [ -n "$repo" ]; then fetch_repo "$repo"; fi
for id in ${models//,/ }; do
  r="$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); m=[x for x in d['inference'] if x['id']==sys.argv[2]]; print((m[0].get('mlx') or {}).get('repo','') if m else '')" "$REPO_ROOT/config/host_models.json" "$id")"
  [ -n "$r" ] || die "$id に MLX 版（config/host_models.json の mlx）がありません"
  fetch_repo "$r"
done
echo
echo "次は scripts/setup-llm.sh でプリセットを書き直し、scripts/start-llm.sh を再起動してください。"
