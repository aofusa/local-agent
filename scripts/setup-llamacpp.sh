#!/bin/bash
# Install the PrismML llama.cpp fork for macOS (Apple silicon, Metal) into tools/llama-prism (idempotent).
# The same build serves the LLM router (scripts/start-llm.sh) and the chat tab's search workers; Bonsai's
# Q1_0 / PTQ1_0 / PQ2_0 kernels exist only in this fork. The release and its SHA-256 are pinned in
# config/search_models.json (llama_release); the tarball's digest comes from the GitHub release.
#   scripts/setup-llamacpp.sh            the pinned release
#   scripts/setup-llamacpp.sh --source   build from the fork's "prism" branch with Metal (needs Xcode CLT, cmake)
. "$(dirname "$0")/lib/common.sh"
require_macos
init_env

catalog="$REPO_ROOT/config/search_models.json"
tag="$(json_get "$catalog" "d['llama_release']")"
repo="$(json_get "$catalog" "d['llama_repo']")"
base="$REPO_ROOT/tools/llama-prism"
mkdir -p "$base"

if [ "${1:-}" = "--source" ]; then
  step "PrismML fork をビルド（Metal）"
  branch="$(json_get "$catalog" "d['llama_branch']")"
  [ -d "$base/src/.git" ] || git clone --depth 1 --branch "$branch" "https://github.com/$repo.git" "$base/src"
  cmake -S "$base/src" -B "$base/src/build" -DGGML_METAL=ON -DLLAMA_CURL=OFF -DCMAKE_BUILD_TYPE=Release >/dev/null
  cmake --build "$base/src/build" -j --target llama-server llama-quantize >/dev/null
  exe="$base/src/build/bin/llama-server"
else
  arch="$(uname -m)"
  [ "$arch" = "arm64" ] || die "Apple silicon（arm64）だけを対象にしています（現在 $arch）"
  asset="llama-$tag-bin-macos-arm64.tar.gz"
  dir="$base/$tag-macos"
  exe="$(find "$dir" -name llama-server -type f 2>/dev/null | head -n 1 || true)"
  if [ -z "$exe" ]; then
    step "PrismML fork $tag（$asset）"
    digest="$(curl -fsSL "https://api.github.com/repos/$repo/releases/tags/$tag" |
      python3 -c "import json,sys; a=[x for x in json.load(sys.stdin)['assets'] if x['name']==sys.argv[1]]; print(a[0].get('digest','').split(':')[-1] if a else '')" "$asset")"
    [ -n "$digest" ] || die "リリース $tag に $asset がありません"
    tmp="$base/$asset.part"
    curl -fL --progress-bar -o "$tmp" "https://github.com/$repo/releases/download/$tag/$asset"
    check_sha "$tmp" "$digest"
    mkdir -p "$dir"
    tar -xzf "$tmp" -C "$dir"
    rm -f "$tmp"
    xattr -dr com.apple.quarantine "$dir" 2>/dev/null || true
    exe="$(find "$dir" -name llama-server -type f | head -n 1)"
  fi
fi
[ -x "$exe" ] || die "llama-server が見つかりません"
step "llama-server の確認"
"$exe" --version 2>&1 | grep -E "version|built" | sed 's/^/    /' || true
"$exe" --list-devices 2>&1 | grep -iE "metal|MTL" | sed 's/^/    /' || warn "Metal デバイスが見つかりません（CPU で動きます）"
env_set LLM_SERVER "$exe"
env_set BONSAI_LLAMA_SERVER "$exe"
ok "LLM_SERVER=$exe（.env）"
