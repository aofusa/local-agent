#!/bin/bash
# Build (when needed) and start agent-chat-ui on 0.0.0.0:3000, in the foreground. NEXT_PUBLIC_API_URL is baked in at
# build time, so it is this Mac's LAN address (what the other host's browser reaches), not localhost.
#   scripts/start-ui.sh [--host 192.168.11.53]
. "$(dirname "$0")/lib/common.sh"
address=""
[ "${1:-}" = "--host" ] && address="$2"
[ -n "$address" ] || address="$(lan_ip)"
[ -n "$address" ] || die "LAN の IPv4 アドレスを特定できません（--host で指定）"
command -v node >/dev/null || die "Node.js 20 以上が必要です（brew install node）"
api="http://$address:2024"
export NEXT_PUBLIC_API_URL="$api" NEXT_PUBLIC_ASSISTANT_ID="agent"
cd "$REPO_ROOT/agent-chat-ui"
echo "agent-chat-ui -> LangGraph $api (assistant: agent)"
[ -d node_modules ] || npx --yes pnpm@10.5.1 install --frozen-lockfile
stamp=".next/local-agent-api-url.txt"
stale=1
if [ -f .next/BUILD_ID ] && [ -f "$stamp" ] && [ "$(cat "$stamp")" = "$api" ]; then
  [ -z "$(find src package.json next.config.mjs -newer .next/BUILD_ID -type f | head -n 1)" ] && stale=0
fi
if [ "$stale" = 1 ]; then
  npx --yes pnpm@10.5.1 build
  printf '%s' "$api" >"$stamp"
fi
echo "Open http://$address:3000 from another host."
exec npx --yes pnpm@10.5.1 start -H 0.0.0.0 -p 3000
