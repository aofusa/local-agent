#!/bin/bash
# Set up local-agent on macOS (Apple silicon) in one go (idempotent). The Windows entry point is scripts/setup.ps1.
#   scripts/setup.sh
#   scripts/setup.sh --image-ids yiffinhell-vantablack --search-models qwen3-1.7b-heretic,bonsai-4b,ternary-8b,bonsai-2-27b-abliterated
#   scripts/setup.sh --mlx-models qwen3.8-27b-abliterated     prefer MLX for the 27B (downloads its MLX build)
# Options: --skip-llm --skip-comfyui --skip-refs --skip-search --skip-probe --source-model PATH
. "$(dirname "$0")/lib/common.sh"
require_macos
skip_llm=0 skip_comfy=0 skip_refs=0 skip_search=0 skip_probe=0 image_ids="" search_models="" mlx_models="" source_model=""
while [ $# -gt 0 ]; do
  case "$1" in
    --skip-llm) skip_llm=1; shift ;;
    --skip-comfyui) skip_comfy=1; shift ;;
    --skip-refs) skip_refs=1; shift ;;
    --skip-search) skip_search=1; shift ;;
    --skip-probe) skip_probe=1; shift ;;
    --image-ids) image_ids="$2"; shift 2 ;;
    --search-models) search_models="$2"; shift 2 ;;
    --mlx-models) mlx_models="$2"; shift 2 ;;
    --source-model) source_model="$2"; shift 2 ;;
    *) die "不明なオプション: $1" ;;
  esac
done

step "前提ツール"
missing=""
for tool in git uv node npx hf; do
  if command -v "$tool" >/dev/null; then ok "$tool: $(command -v "$tool")"; else missing="$missing $tool"; fi
done
[ -z "$missing" ] || die "次のツールを入れてから再実行してください:$missing（brew install git uv node huggingface-cli）"
node_major="$(node --version | tr -d v | cut -d. -f1)"
[ "$node_major" -ge 20 ] || die "Node.js 20 以上が必要です（現在 v$node_major）"

step ".env"
init_env
ok "$ENV_FILE"

step "LangGraph の Python 環境（uv sync）"
(cd "$REPO_ROOT" && uv sync --quiet)
ok "done"
step "agent-chat-ui の依存（pnpm install）"
(cd "$REPO_ROOT/agent-chat-ui" && npx --yes pnpm@10.5.1 install --frozen-lockfile >/dev/null)
ok "done"

"$REPO_ROOT/scripts/setup-llamacpp.sh"
if [ "$skip_search" = 0 ]; then
  "$REPO_ROOT/scripts/setup-tor.sh"
  if [ -n "$search_models" ]; then "$REPO_ROOT/scripts/setup-search-models.sh" --models "$search_models"
  else "$REPO_ROOT/scripts/setup-search-models.sh"; fi
fi
if [ "$skip_llm" = 0 ]; then
  if [ -n "$mlx_models" ]; then "$REPO_ROOT/scripts/setup-mlx.sh" --models "$mlx_models"; fi
  if [ -n "$source_model" ]; then "$REPO_ROOT/scripts/setup-llm.sh" --source-model "$source_model"
  else "$REPO_ROOT/scripts/setup-llm.sh"; fi
fi
if [ "$skip_comfy" = 0 ]; then
  if [ -n "$image_ids" ]; then "$REPO_ROOT/scripts/setup-comfyui.sh" --ids "$image_ids"; else "$REPO_ROOT/scripts/setup-comfyui.sh"; fi
  [ "$skip_refs" = 1 ] || "$REPO_ROOT/scripts/setup-comfyui-refs.sh"
fi
"$REPO_ROOT/scripts/setup-sandbox.sh"
if [ "$skip_search" = 0 ] && [ "$skip_probe" = 0 ]; then
  step "検索モデルの検証（tools/bonsai/rank.json）"
  (cd "$REPO_ROOT" && uv run python -m furry_agent.bonsai_probe) || warn "検証に失敗しました（既定の順位で動きます）"
fi

echo
echo "セットアップ完了。次の手順:"
echo "  1. scripts/start-all.sh      # LLM ルータ / Tor / ComfyUI / LangGraph / agent-chat-ui をバックグラウンドで起動"
echo "  2. scripts/doctor.sh         # 設定と待受を確認"
echo "  3. 他ホストのブラウザで http://$(lan_ip):3000 を開く（macOS のファイアウォールが着信を聞いたら許可）"
