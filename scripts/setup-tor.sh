#!/bin/bash
# Tor for the chat tab's web search on macOS: Homebrew's tor (signed bottles), configured by tools/tor/torrc
# (SOCKS on 127.0.0.1:9050 only). The orchestrator starts it on demand (TOR_AUTOSTART) or scripts/start-tor.sh.
. "$(dirname "$0")/lib/common.sh"
require_macos
init_env
step "Tor（Homebrew）"
command -v brew >/dev/null || die "Homebrew がありません（https://brew.sh）"
command -v tor >/dev/null || brew install tor
exe="$(command -v tor)"
"$exe" --version | head -n 1 | sed 's/^/    /'
mkdir -p "$REPO_ROOT/tools/tor/data"
chmod 700 "$REPO_ROOT/tools/tor/data"
env_set TOR_EXE "$exe"
ok "TOR_EXE=$exe（.env）。起動は scripts/start-tor.sh"
