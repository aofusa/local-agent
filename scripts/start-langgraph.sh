#!/bin/bash
# Start the LangGraph dev server (graphs agent / chat, /coder/turn, /models) reachable from other hosts, in the
# foreground. ComfyUI (127.0.0.1:8188) and the LLM router (127.0.0.1:8080) must already be running.
. "$(dirname "$0")/lib/common.sh"
port="${1:-2024}"
init_env
cd "$REPO_ROOT"
exec uv run langgraph dev --host 0.0.0.0 --port "$port" --no-browser --no-reload
