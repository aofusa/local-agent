#!/bin/bash
# Stop what scripts/start-all.sh started (and the router's model child, the search workers, Tor).
. "$(dirname "$0")/lib/common.sh"
for name in ui langgraph comfyui llm; do
  pidfile="$REPO_ROOT/tools/run/$name.pid"
  [ -f "$pidfile" ] || continue
  pid="$(cat "$pidfile")"
  # The script's children (node, uvicorn, python, llama-server) first, then the script itself.
  pkill -TERM -P "$pid" 2>/dev/null || true
  kill "$pid" 2>/dev/null || true
  rm -f "$pidfile"
  ok "$name stopped"
done
pkill -f "$REPO_ROOT/tools/llama-prism" 2>/dev/null || true
"$REPO_ROOT/scripts/start-tor.sh" --stop || true
