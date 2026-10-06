#!/bin/bash
# Start the LLM router on 127.0.0.1 (loopback only) with the preset from scripts/setup-llm.sh, in the foreground.
# LLM_ENGINE=mlx (setup-llm.sh writes it when tools/mlx exists): furry_agent.mlx_router, which runs MLX sections on
# mlx_lm and GGUF sections on llama-server. Else llama-server's own router mode. Either way the router holds no
# model at start; the first request loads one, POST /models/unload or the idle sleep frees it.
. "$(dirname "$0")/lib/common.sh"
port="${1:-$(env_get LLM_PORT 8080)}"
listening "$port" && { echo "LLM router is already listening on $port"; exit 0; }
server="$(env_get LLM_SERVER)"
preset="$(env_get LLM_PRESET "$REPO_ROOT/tools/llm/models.ini")"
[ -x "$server" ] || die "llama-server がありません。先に scripts/setup-llamacpp.sh を実行してください"
[ -f "$preset" ] || die "プリセットがありません（$preset）。先に scripts/setup-llm.sh を実行してください"
mkdir -p "$REPO_ROOT/logs"
if [ "$(env_get LLM_ENGINE llamacpp)" = "mlx" ]; then
  echo "LLM router (MLX first): $preset on 127.0.0.1:$port"
  cd "$REPO_ROOT"
  exec uv run --quiet python -m furry_agent.mlx_router --preset "$preset" --port "$port" --llama-server "$server" \
    --mlx-python "$REPO_ROOT/tools/mlx/.venv/bin/python" --logs "$REPO_ROOT/logs"
fi
echo "LLM router: $server --models-preset $preset --models-max 1 --host 127.0.0.1 --port $port"
exec "$server" --models-preset "$preset" --models-max 1 --host 127.0.0.1 --port "$port"
