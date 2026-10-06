#!/bin/bash
# Start Tor, the LLM router, ComfyUI, LangGraph and agent-chat-ui on macOS (those not already running), each in the
# background with its log in logs/<name>.out; scripts/stop-all.sh stops them. The router holds no model until the
# first request; the search workers start per search.
#   scripts/start-all.sh [--skip-tor] [--skip-comfyui]
. "$(dirname "$0")/lib/common.sh"
skip_tor=0 skip_comfy=0
for a in "$@"; do
  case "$a" in --skip-tor) skip_tor=1 ;; --skip-comfyui) skip_comfy=1 ;; *) die "不明なオプション: $a" ;; esac
done
mkdir -p "$REPO_ROOT/logs" "$REPO_ROOT/tools/run"

launch() {
  # launch NAME PORT SECONDS SCRIPT: nohup in the background, its pid in tools/run/NAME.pid.
  if listening "$2"; then ok "$1 already running (127.0.0.1:$2)"; return; fi
  nohup "$REPO_ROOT/scripts/$4" >"$REPO_ROOT/logs/$1.out" 2>&1 &
  echo $! >"$REPO_ROOT/tools/run/$1.pid"
  wait_port "$2" "$3"
  ok "$1 started (log logs/$1.out)"
}

step "LLM ルータ"
launch llm "$(env_get LLM_PORT 8080)" 120 start-llm.sh
if [ "$skip_tor" = 0 ]; then
  step "Tor"
  if [ -n "$(env_get TOR_EXE)" ]; then "$REPO_ROOT/scripts/start-tor.sh" || warn "Tor を起動できません（検索だけが使えません）"
  else warn "Tor 未導入（scripts/setup-tor.sh）。チャットタブの検索は使えません"; fi
fi
if [ "$skip_comfy" = 0 ]; then step "ComfyUI"; launch comfyui 8188 300 start-comfyui.sh; fi
step "LangGraph"
launch langgraph 2024 180 start-langgraph.sh
step "agent-chat-ui"
launch ui 3000 900 start-ui.sh
echo
echo "起動しました。他ホストのブラウザで http://$(lan_ip):3000 を開いてください。"
