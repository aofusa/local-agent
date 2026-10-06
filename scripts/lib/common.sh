# Shared helpers of the macOS scripts (scripts/*.sh). The Windows scripts are scripts/*.ps1 with lib/common.ps1.
# Source it:  . "$(dirname "$0")/lib/common.sh"

set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.cargo/bin:$HOME/.rd/bin:$PATH"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"

step() { printf '\n\033[36m==> %s\033[0m\n' "$*"; }
ok() { printf '    \033[32mOK\033[0m  %s\n' "$*"; }
warn() { printf '    \033[33m!!\033[0m  %s\n' "$*"; }
die() { printf '\033[31mエラー: %s\033[0m\n' "$*" >&2; exit 1; }

require_macos() {
  [ "$(uname -s)" = "Darwin" ] || die "このスクリプトは macOS 用です（Windows は scripts/*.ps1）"
}

init_env() {
  # .env from .env.example on the first run (machine-specific, never committed).
  if [ ! -f "$ENV_FILE" ]; then
    cp "$REPO_ROOT/.env.example" "$ENV_FILE"
    echo "$ENV_FILE を作成しました"
  fi
}

env_get() {
  # env_get KEY [default]: the value in .env (the last line wins), else the default.
  local value=""
  if [ -f "$ENV_FILE" ]; then
    value="$(grep -E "^$1=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- || true)"
  fi
  if [ -n "$value" ]; then printf '%s' "$value"; else printf '%s' "${2:-}"; fi
}

env_set() {
  # env_set KEY VALUE: replace the KEY= line of .env (or append it).
  init_env
  local tmp
  tmp="$(mktemp)"
  if grep -qE "^$1=" "$ENV_FILE"; then
    awk -v k="$1" -v v="$2" 'BEGIN{FS=OFS="="} $1==k {print k "=" v; next} {print}' "$ENV_FILE" >"$tmp"
  else
    cat "$ENV_FILE" >"$tmp"
    printf '%s=%s\n' "$1" "$2" >>"$tmp"
  fi
  mv "$tmp" "$ENV_FILE"
}

listening() {
  # listening PORT: something accepts connections on 127.0.0.1:PORT.
  nc -z -G 1 127.0.0.1 "$1" >/dev/null 2>&1
}

wait_port() {
  # wait_port PORT SECONDS
  local deadline=$((SECONDS + $2))
  until listening "$1"; do
    [ $SECONDS -lt $deadline ] || die "ポート $1 が $2 秒以内に開きませんでした"
    sleep 2
  done
}

lan_ip() {
  # The IPv4 address of the default-route interface (what another host on the LAN reaches).
  local iface
  iface="$(route -n get default 2>/dev/null | awk '/interface:/ {print $2}')"
  ipconfig getifaddr "${iface:-en0}" 2>/dev/null || ipconfig getifaddr en0 2>/dev/null || true
}

sha256_of() { shasum -a 256 "$1" | awk '{print $1}'; }

check_sha() {
  # check_sha FILE SHA256 (empty sha: skipped)
  [ -z "${2:-}" ] && return 0
  local actual
  actual="$(sha256_of "$1")"
  [ "$actual" = "$2" ] || die "SHA-256 が一致しません: $1（期待 $2 / 実際 $actual）"
}

file_size() { stat -f %z "$1"; }

link_file() {
  # link_file DEST SOURCE: a hard link (same volume), else a symbolic link. Never a second copy of a model.
  mkdir -p "$(dirname "$1")"
  rm -f "$1"
  ln "$2" "$1" 2>/dev/null || ln -s "$2" "$1"
}

hf_file() {
  # hf_file REPO FILE DEST [SHA256] [SIZE]: DEST from the Hugging Face cache (hf download), linked, not copied.
  local repo="$1" file="$2" dest="$3" sha="${4:-}" size="${5:-}"
  if [ -s "$dest" ] && { [ -z "$size" ] || [ "$(file_size "$dest")" = "$size" ]; }; then
    ok "exists: $dest"
    return 0
  fi
  command -v hf >/dev/null || die "hf（huggingface_hub の CLI）がありません: brew install huggingface-cli か uv tool install huggingface_hub"
  echo "    hf download $repo $file"
  local cached
  cached="$(hf download "$repo" "$file" 2>/dev/null | tail -n 1)"
  [ -s "$cached" ] || die "hf download に失敗しました（$repo $file）"
  # The cache keeps a symbolic link to the blob: link the blob itself.
  cached="$(python3 -c 'import os,sys;print(os.path.realpath(sys.argv[1]))' "$cached")"
  link_file "$dest" "$cached"
  if [ -n "$size" ] && [ "$(file_size "$dest")" != "$size" ]; then die "サイズが一致しません: $dest（期待 $size）"; fi
  check_sha "$dest" "$sha"
  ok "$dest（Hugging Face のキャッシュ）"
}

known_model_dirs() {
  # Model folders of an earlier ComfyUI on this Mac (imported by hard link; ComfyUI reads tools/comfyui/models only).
  local d
  for d in "$HOME/Documents/ComfyUI/models" "$HOME/ComfyUI/models" "$HOME/Library/Application Support/ComfyUI/models"; do
    [ -d "$d" ] && printf '%s\n' "$d"
  done
  return 0
}

import_comfy_model() {
  # import_comfy_model MODELS_DIR "folder1 folder2" NAME [extra source dirs...]: echo the path, or nothing.
  local models="$1" folders="$2" name="$3"
  shift 3
  local folder dir
  for folder in $folders; do
    [ -s "$models/$folder/$name" ] && { printf '%s\n' "$models/$folder/$name"; return 0; }
  done
  while IFS= read -r dir; do
    for folder in $folders; do
      if [ -s "$dir/$folder/$name" ]; then
        link_file "$models/$folder/$name" "$dir/$folder/$name"
        printf '%s\n' "$models/$folder/$name"
        return 0
      fi
    done
  done < <({ for d in "$@"; do printf '%s\n' "$d"; done; known_model_dirs; })
  return 0
}

uv_python() { (cd "$REPO_ROOT" && uv run --quiet python "$@"); }

json_get() {
  # json_get FILE PYTHON_EXPR (the JSON is `d`): print the expression's value.
  python3 -c "import json,sys; d=json.load(open(sys.argv[1], encoding='utf-8')); print($2)" "$1"
}
