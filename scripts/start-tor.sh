#!/bin/bash
# Start Tor in the background (the same command line as the chat tab's autostart): tools/tor/torrc, data in
# tools/tor/data, log in logs/tor.log. --stop stops the one this script started.
. "$(dirname "$0")/lib/common.sh"
pidfile="$REPO_ROOT/tools/tor/tor.pid"
if [ "${1:-}" = "--stop" ]; then
  [ -f "$pidfile" ] && kill "$(cat "$pidfile")" 2>/dev/null && rm -f "$pidfile" && ok "Tor を停止しました"
  exit 0
fi
listening 9050 && { ok "Tor は 127.0.0.1:9050 で動いています"; exit 0; }
exe="$(env_get TOR_EXE)"
[ -x "$exe" ] || die "Tor がありません。scripts/setup-tor.sh を実行してください"
mkdir -p "$REPO_ROOT/logs" "$REPO_ROOT/tools/tor/data"
: >"$REPO_ROOT/logs/tor.log"
nohup "$exe" -f "$REPO_ROOT/tools/tor/torrc" --DataDirectory "$REPO_ROOT/tools/tor/data" \
  --Log "notice file $REPO_ROOT/logs/tor.log" >/dev/null 2>&1 &
echo $! >"$pidfile"
for _ in $(seq 1 90); do
  grep -q "Bootstrapped 100%" "$REPO_ROOT/logs/tor.log" 2>/dev/null && { ok "Tor started (pid $(cat "$pidfile"))"; exit 0; }
  sleep 1
done
die "Tor の接続が 90 秒以内に完了しませんでした（logs/tor.log）"
