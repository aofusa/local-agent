#!/bin/bash
# Build cirka (the CUI client) in release mode and pack dist/cirka-<version>-macos-<arch>.tar.gz (macOS / Linux).
. "$(dirname "$0")/lib/common.sh"
command -v cargo >/dev/null || die "cargo が見つかりません。Rust（https://rustup.rs）を入れてください"
cd "$REPO_ROOT/client"
cargo build --release
version="$(grep -m1 '^version' Cargo.toml | cut -d'"' -f2)"
os="$(uname -s | tr '[:upper:]' '[:lower:]' | sed 's/darwin/macos/')"
mkdir -p "$REPO_ROOT/dist"
out="$REPO_ROOT/dist/cirka-$version-$os-$(uname -m).tar.gz"
tar -czf "$out" -C target/release cirka -C "$REPO_ROOT" LICENSE-MIT LICENSE-APACHE
ok "packed $out"
