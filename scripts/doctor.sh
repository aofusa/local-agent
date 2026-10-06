#!/bin/bash
# Check the macOS setup and what is listening (read-only). The Windows version is scripts/doctor.ps1.
. "$(dirname "$0")/lib/common.sh"
fail=0
check() {
  # check LABEL COMMAND...: prints OK with the command's output, or NG.
  local label="$1"
  shift
  local out
  if out="$("$@" 2>&1)"; then ok "$label ${out:+— $out}"; else printf '    \033[31mNG\033[0m  %s — %s\n' "$label" "$out"; fail=1; fi
}
step "ツール"
check "llama-server (LLM_SERVER)" bash -c '"$0" --version 2>&1 | grep -m1 version' "$(env_get LLM_SERVER)"
check "preset (LLM_PRESET)" bash -c 'grep -c "^\[" "$0" | sed "s/$/ sections/"' "$(env_get LLM_PRESET)"
check "ComfyUI python" test -x "$(env_get COMFYUI_PYTHON)"
check "Tor (TOR_EXE)" test -x "$(env_get TOR_EXE)"
# Optional: only the chat tab's code execution needs it.
if out="$(docker version --format '{{.Server.Os}}' 2>&1)"; then ok "docker — $out"; else warn "docker — コード実行は使えません（$(printf '%s' "$out" | grep -m1 .)）"; fi
step "待受（ループバックのままか）"
check "LLM router 127.0.0.1:$(env_get LLM_PORT 8080)" bash -c 'curl -sf -m 5 "$0/models" | python3 -c "import json,sys; print(\", \".join(m[\"id\"]+\":\"+m.get(\"status\",{}).get(\"value\",\"?\") for m in json.load(sys.stdin)[\"data\"]))"' "$(env_get LLM_URL http://127.0.0.1:8080/v1)"
check "ComfyUI 127.0.0.1:8188" bash -c 'curl -sf -m 5 http://127.0.0.1:8188/system_stats | python3 -c "import json,sys; d=json.load(sys.stdin); print(d[\"system\"][\"comfyui_version\"], d[\"devices\"][0][\"type\"])"'
check "LangGraph :2024 /models" bash -c 'curl -sf -m 30 http://127.0.0.1:2024/models | python3 -c "import json,sys; d=json.load(sys.stdin); print(\" \".join(m[\"id\"]+(\"\" if m[\"available\"] else \"(x)\") for m in d[\"inference\"]+d[\"image\"]))"'
check "agent-chat-ui :3000" bash -c 'curl -sf -m 10 -o /dev/null http://127.0.0.1:3000 && echo up'
for port in 8080 8188 9050; do
  if lsof -nP -iTCP:$port -sTCP:LISTEN 2>/dev/null | awk 'NR>1 {print $9}' | grep -qv '^127\.0\.0\.1:'; then
    printf '    \033[31mNG\033[0m  port %s がループバック以外で待ち受けています\n' "$port"; fail=1
  fi
done
exit $fail
