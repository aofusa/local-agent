#!/bin/bash
# The chat tab's search models (config/search_models.json) for macOS: GGUFs in tools/models (BONSAI_MODELS_DIR),
# linked from the Hugging Face cache and checked against their SHA-256. They run on the PrismML llama-server only.
#   scripts/setup-search-models.sh                                   all models
#   scripts/setup-search-models.sh --models qwen3-1.7b-heretic,bonsai-2-27b-abliterated   a small Mac
. "$(dirname "$0")/lib/common.sh"
init_env
models=""
[ "${1:-}" = "--models" ] && models="$2"
dir="$(env_get BONSAI_MODELS_DIR "$REPO_ROOT/tools/models")"
mkdir -p "$dir"
list="$(python3 - "$REPO_ROOT/config/search_models.json" "$models" <<'PY'
import json, sys
catalog = json.load(open(sys.argv[1], encoding="utf-8"))
wanted = [x for x in sys.argv[2].split(",") if x]
ids = {m["id"] for m in catalog["models"]}
unknown = [x for x in wanted if x not in ids]
if unknown:
    sys.exit("不明なモデル id: " + ", ".join(unknown))
for m in catalog["models"]:
    if not wanted or m["id"] in wanted:
        print(m["id"], m["repo"], m["file"], m["size"], m["sha256"])
PY
)"
while read -r id repo file size sha; do
  [ -n "$id" ] || continue
  step "$id（$(python3 -c "print(round($size/2**30,2))") GB）"
  hf_file "$repo" "$file" "$dir/$file" "$sha" "$size"
done <<<"$list"
env_set BONSAI_MODELS_DIR "$dir"
ok "BONSAI_MODELS_DIR=$dir（.env）"
