# 設定（`.env`）

端末ごとの設定は `.env`（`.env.example` から作る。git に含めない）に書きます。全項目と説明は `.env.example` にあります。値を変えたら LangGraph を再起動します。

| キー | 既定 | 説明 |
|---|---|---|
| `COMFYUI_URL` | `http://127.0.0.1:8188` | LangGraph から見た ComfyUI |
| `AGENT_IDLE_TIMEOUT_S` | `1200` | 何も返ってこない時間の上限（秒）。画像タブ・チャットタブ・`/coder/turn` で共通（下の「タイムアウト」） |
| `HOST_MODELS_PATH` / `DEFAULT_INFERENCE_MODEL` / `DEFAULT_IMAGE_MODEL` / `HOST_MODELS_DISABLE` | 下の「モデルの一覧とパラメータ」 | 選べるモデルの一覧と、指定の無い実行のモデル |
| `CHROMA_TEXT_ENCODER` / `CHROMA_VAE` / `CHROMA_WEIGHT_DTYPE` | 空（マップの値） | この端末の Chroma のテキストエンコーダ・VAE・読み込み精度（[usage.md](usage.md) の「画像モデル」） |
| `CHROMA_MAX_PIXELS` / `CHROMA_STEPS` | 空（1048576 / 28） | Chroma の画素数の上限とステップ数（遅い GPU 向け） |
| `LLM_SERVER` / `LLM_PRESET` / `LLM_PORT` | セットアップが設定 / `tools\llm\models.ini` / `8080` | 27B を動かす llama-server とルータのプリセット、待受ポート（`start-llm.ps1` が使う。ループバックのみ） |
| `LLM_URL` / `LLM_MODEL` | `http://127.0.0.1:8080/v1` / `qwen3.8-27b-abliterated` | ルータの OpenAI 互換 URL と、画像のタグ生成（ワークフロー）が使うプリセットの節。推論モデルを選ばない実行のチャットタブと `/coder/turn` もこれを使う |
| `LLM_CONTEXT` | `4096` | `LLM_MODEL` の context。選んだ推論モデルは一覧の `context` を使う |
| `LLM_ENGINE` | `llamacpp` | macOS だけ: `mlx` なら `start-llm.sh` が MLX 優先のルータ（`furry_agent.mlx_router`）を起動する（`setup-llm.sh` が書く） |
| `COMFYUI_MAIN_DIR` ほか `COMFYUI_*` | セットアップが設定 | `start-comfyui.ps1` が使う ComfyUI（`tools\comfyui`）、その Python、モデルの置き場、`extra_model_paths.yaml` |
| `COMFYUI_EXTRA_ARGS` | 空 | ComfyUI の追加引数 |
| `COMFYUI_EXTRA_MODEL_PATHS` | 空 | ComfyUI に別のモデルフォルダを読ませる YAML（手で設定したときだけ使う。v0.11.0 からセットアップは書かず、モデルを `tools\comfyui\models` に取り込む） |
| `HF_HUB_CACHE` / `HF_HOME` / `HF_TOKEN` | 空（Hugging Face の既定） | セットアップが使う Hugging Face のキャッシュの場所と認証。`hf download` と同じ値を読むので、取得したモデルを共有する（[setup.md › モデルの探し方](setup.md#モデルの探し方指定は不要)）。`.env` ではなく環境変数で設定する |

27B の読み込み方（GPU に置く層、量子化、context）を変えるときは、`.\scripts\setup-llm.ps1 -GpuOffload 0.6` のように再実行し、ルータ（`start-llm.ps1`）を再起動します。

チャットタブの設定（画像タブは読みません。全項目と説明は `.env.example`）:

| キー | 既定 | 説明 |
|---|---|---|
| `TOR_SOCKS_URL` / `TOR_EXE` / `TOR_AUTOSTART` | `socks5h://127.0.0.1:9050` / セットアップが設定 / `1` | Tor。`socks5h` 以外は拒否（DNS 漏れ防止） |
| `SEARCH_FANOUT_WIDTH` / `SEARCH_MAX_RESULTS` | `3` / `5` | 検索意図と reader の数の上限（1〜7。この端末のメモリでは 3 まで）、1 検索の結果数（1〜30） |
| `SEARCH_TIMEOUT_S` / `SEARCH_TOTAL_TIMEOUT_S` | `30` / `0` | Tor 経由の HTTP リクエスト 1 回の上限（止まったページは飛ばして続ける）、reader 1 体の時間の予算（0 = なし） |
| `SEARCH_PLANNER` | `llm` | `local` にすると、計画も代理 27B が行う（ルータの 27B を検索で使わない）。v0.10 までの値 `lmstudio` は `llm` と同じ |
| `SEARCH_FILTER` / `SEARCH_CRITIQUE` / `SEARCH_AUTO_ROUTE` | `1` | フィルタ / 思考モードの批評と追加ラウンド / 決まらない文のルータ判定 |
| `SEARCH_MAX_ROUNDS` / `SEARCH_MAX_PAGES` / `SEARCH_WALL_CLOCK_S` | `4` / `12` / `0` | 思考モードの検索のラウンド（1〜50）、読むページ（1〜500）、全体の時間の予算（0 = なし） |
| `SEARCH_HITS_PER_INTENT` | `4` | 1 つの検索意図から reader に渡す結果数 |
| `CHAT_THINK_TOKENS` | `3072` | 思考モードで回答に足す思考トークンの上限（context と速さの範囲内で使う） |
| `SANDBOX_DOCKER` / `SANDBOX_USER` / `SANDBOX_WAIT_S` | `docker` / `10001:10001` / 空 | コード実行の docker コマンド（無ければ実行も Docker Desktop の起動もしない）、コンテナ内の uid:gid（root は不可）、画像タブの生成が終わるのを待つ上限（空 = 生成が進んでいるあいだは待つ） |
| `BONSAI_LLAMA_SERVER` / `BONSAI_MODELS_DIR` | セットアップが設定 | 検索モデルを動かす llama-server.exe（PrismML fork。`LLM_SERVER` と同じ build）と、モデルの置き場 |
| `BONSAI_MODEL` | 空（自動） | reader のモデルを固定する（`ternary-8b` など）。入らなければ断る |
| `BONSAI_RESERVE_MB` | `3072` | モデルを何体載せるか決めるときに残す空きメモリ |
| `JOB_LOCK_TIMEOUT_S` | 空 | もう片方のタブの実行が終わるのを待つ上限（空 = 相手が動いているあいだは待つ） |
| `CLAIM_VERIFY` / `CLAIM_VERIFY_FAIL_OPEN` | `1` / `0` | 主張の突き合わせ（`0` で旧来の統合） / 失敗時に無監査の回答を出すか |
| `CLAIM_MAX` / `CLAIM_QUOTE_CHARS` / `CLAIM_TIMEOUT_S` | `12` / `400` / `0` | 主張の上限（1〜200）、判定に見せる抜粋の長さ（80〜20000）、抽出 + 判定 + 監査の時間の予算（0 = なし） |
| `CONTROLLER_MAX_STEPS` | `3` | 自律モードで道具を使う回数の上限（1〜50） |
| `CONTROLLER_WALL_CLOCK_S` | 空（`SEARCH_WALL_CLOCK_S`） | 自律モード全体の時間の予算（どちらも 0 なら無し）。`SEARCH_WALL_CLOCK_S` を設定したときは、それを超えない |

## モデルの一覧とパラメータ（`config/host_models.json`、v0.12.0）

利用者が選べる推論モデルと画像モデル、その表示名・ファイル・パラメータは `config/host_models.json` にあります（git 管理。パスと秘密は書かない）。Web の画面とcirka はホストの `GET /models`（LangGraph の `:2024`）で一覧を取り、選んだモデルの `id` を送ります。送らなければ `.env` の既定を使います。ファイルが無いモデルや `HOST_MODELS_DISABLE` のモデルは一覧に残り、理由つきで「使えない」と表示されます（選んで送るとエラーになり、別のモデルに自動で替わりません）。

| キー（`.env`） | 既定 | 説明 |
|---|---|---|
| `HOST_MODELS_PATH` | `config/host_models.json` | モデルの一覧 |
| `DEFAULT_INFERENCE_MODEL` | 空 | 推論モデルを指定しない実行のモデル。空ならルータの `LLM_MODEL` と `LLM_CONTEXT`（v0.11 までと同じ） |
| `DEFAULT_IMAGE_MODEL` | 一覧の先頭（`yiffinhell-vantablack`） | 画像モデルを指定しない実行のモデル |
| `HOST_MODELS_DISABLE` | 空 | カンマ区切りの id を使えないものとして出す |

v0.11 までの `CKPT_NAME`、`COMFY_MODEL_FAMILY`、`LORAS`、`CHROMA_LORAS`、`CHROMA_UNET_NAME` は読みません（チェックポイント、系統、LoRA は画像モデルの項目）。`CHROMA_TEXT_ENCODER` / `CHROMA_VAE` / `CHROMA_WEIGHT_DTYPE` / `CHROMA_MAX_PIXELS` / `CHROMA_STEPS` はこの端末の Chroma の部品と速さの調整として残ります。

### 推論モデル

`params` は llama-server のオプション名（`--` なし）で、セットアップ（`setup-llm.ps1` / `setup-llm.sh` が `scripts/host_models.py preset` を呼ぶ）がルータのプリセット `tools/llm/models.ini` の各節に書きます。GGUF が無いモデルの節は書かれず、一覧で使えないと表示されます。変えたら `setup-llm` を再実行してルータを再起動します。

| id | 表示名 | ファイル | context | 思考 | パラメータ | 出典 |
|---|---|---|---|---|---|---|
| `qwen3.8-27b-abliterated` | Qwen 3.8 27B abliterated | `config/llm_model.json`（IQ3_M、mmproj あり）。macOS は MLX 4bit 版（`setup-mlx.sh`）も可 | 4096 | あり | temp 0.4、repeat-penalty 1.1（v0.11 と同じ。タグの JSON を閉じさせるため） | 公式の推奨（思考なし: temp 0.7、top-p 0.8、top-k 20、presence-penalty 1.5）より低い温度は、タグ生成の安定を優先して v0.11 の値を保った |
| `bonsai-2-27b-abliterated` | Bonsai 2 27B abliterated | `config/search_models.json` の同じ id（PTQ1_0、5.9GB。検索の代理リーダーと同じファイル） | 8192 | なし（思考モードでも本文だけ） | temp 0.7、top-p 0.8、top-k 20、min-p 0.05、presence-penalty 1.5、repeat-penalty 1.0、GPU にすべての層 | [Ternary Bonsai 2 27B の推奨](https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf)（思考なし: temperature 0.7、top_p 0.8。min_p 0.05 は llama.cpp の既定）、[Qwen3.8 の思考なしの推奨](https://unsloth.ai/docs/models/qwen3.8)（top_k 20、presence_penalty 1.5） |

画像のタグ生成（ComfyUI のワークフロー）は、選んだ推論モデルによらずルータの `LLM_MODEL` を使います。

### 画像モデル

`params` は `steps`、`cfg`、`sampler_name`、`scheduler`、`width`、`height`、`quality_prefix`、`negative`（`split` の既定の negative）だけで、系統のマップ（`workflows/maps/<family>.json` の `defaults`）の上に重ねます。`loras` は `"<ファイル>:<強度>"` の配列（0 より大きく 2 以下、CLIP も同じ強度）で、`ckpt` の直後に順に挿入します。

| id | 表示名 | 系統 | ファイル | steps / cfg / サンプラー / サイズ | LoRA | 出典 |
|---|---|---|---|---|---|---|
| `yiffinhell-vantablack` | yiffInHell VANTABLACK | sdxl | `yiffInHell_yihVANTABLACK.safetensors` | 28 / 5.5 / euler_ancestral normal / 832×1216 | novabeast xl v1 rank64 pony 1.0 | v0.11 の既定値のまま（ワークフローのテンプレートと同じ） |
| `yiffinhell-metallictetra` | yiffInHell METALLIC TETRA | sdxl | `yiffInHell_yihMETLLICTETR.safetensors` | 24 / 3.5 / euler_ancestral sgm_uniform / 832×1216 | 同上 | [Yiff in Hell の配布ページ](https://civarchive.com/models/1570986)（v4.0: 24 steps、CFG 2〜4、Euler A、Beta / SGM Uniform） |
| `yiffinhell-xxxtended-v2` | yiffInHell XXX-TENDED V2.0 | sdxl | `yiffInHell_yihxxxTENDEDV20.safetensors` | 24 / 3.0 / euler_ancestral sgm_uniform / 832×1216 | 同上 | 同上（XXX-TENDED: 24 steps、CFG 2〜4、Euler A） |
| `rekemono` | Rekemono v1.0 | sdxl | `rekemono_v100.safetensors` | 28 / 4.5 / euler_ancestral normal / 832×1216 | 同上 | 配布ページが見つからないため、同系統の kemono SDXL（[Nova Kemono XL](https://civitai.com/models/1641408)、Mol_Keun Mix など: Euler A、20〜30 steps、CFG 3〜5）の中央値 |
| `indigofurrymix-anima` | Indigo Furry Mix Anima | anima | `indigoFurryMixAnima_v10.safetensors`（拡散モデルのみ）+ `qwen_3_06b_base` + `qwen_image_vae` | 28 / 4.0 / er_sde simple / 832×1216 | なし | [配布ページ](https://civitai.com/models/2787288)（Euler A か ER SDE、30 steps 未満、CFG 3〜6・作者は 4、1024px 前後、`furry` を入れる）、ComfyUI の Anima ブループリント |
| `chroma-hd` | Chroma1-HD | flux | `chroma_v10HD.safetensors` + T5-XXL fp8 + Flux VAE | 28 / 3.5 / euler beta / 1024×1024 | なし | v0.11 の既定値のまま（公式ワークフロー） |
| `wulver` | Wulver (Krea 2) | krea2 | `wulverKrea2_v05_fp8.safetensors`（拡散モデルのみ）+ `qwen3vl_4b_fp8_scaled` + `qwen_image_vae` | 8 / 1.0 / euler simple / 1024×1024 | なし | [配布ページ](https://civitai.com/models/2881657)（Turbo: 8 steps、CFG 1.0・1.0 より上は焼ける・negative は効かない、euler / simple、shift 1.15、1024 ネイティブ、自然文 60〜120 語） |

テキストエンコーダと VAE（`downloads`）は `setup-image-models.ps1` / `.sh` が Hugging Face から取得し、SHA-256 を照合します（Krea 2: [Comfy-Org/Krea-2](https://huggingface.co/Comfy-Org/Krea-2)、Anima: [circlestone-labs/Anima](https://huggingface.co/circlestone-labs/Anima)）。Civitai のチェックポイントは取得しません（利用者が置く）。

モデルを足すときは一覧に 1 件足すだけです（id は英小文字・数字・`.`・`-`）。系統は `sdxl` / `flux` / `krea2` / `anima` から選び、`prompt_style` は系統に合わせます（`sdxl`・`anima` は `danbooru`、`flux`・`krea2` は `prose`）。

## 量と長さの上限（v0.10.0）

以前はコードの定数だった上限も `.env` で変えられます。空なら既定値のままです。値を変えたら LangGraph を再起動します。キーの一覧と既定値は `.env.example` の末尾にあります。

| まとまり | キー |
|---|---|
| 検索 | `SEARCH_MAX_INTENTS`（1 ラウンドの検索意図、既定 3、1〜7）、`SEARCH_INTENT_TIMEOUT_S`（1 つの検索意図、空 = `SEARCH_TIMEOUT_S` の 3 倍）、`SEARCH_MAX_REDIRECTS`（3）、`SEARCH_PAGE_TEXT_CHARS`（取得したページから残す字数、4000）、`TOR_BOOTSTRAP_TIMEOUT_S`（Tor の起動、90 秒） |
| reader | `BONSAI_MAX_TOOL_ROUNDS`（ページを開く回数、2）、`BONSAI_PAGE_TIMEOUT_S`（1 ページの取得、20 秒）、`BONSAI_PAGE_CHARS`（reader に見せる字数、1200）、`BONSAI_PAGE_KEEP_CHARS`（主張の検証用に残す字数、3000）、`BONSAI_LEADER_CTX`（代理 27B の context、8192） |
| トークン | `CHAT_TOKENS` / `CHAT_ANSWER_MIN`（1536 / 512）、`SEARCH_PLAN_TOKENS` / `_THINK`（400 / 700）、`SEARCH_CARD_TOKENS`（700）、`SEARCH_CRITIQUE_TOKENS`（900）、`SEARCH_SYNTH_TOKENS` / `_THINK` / `SEARCH_SYNTH_ANSWER_MIN`（1200 / 1600 / 800）、`CLAIM_EXTRACT_TOKENS` / `CLAIM_VERIFY_TOKENS`（1200 / 2000）、`WRITE_OUTLINE_TOKENS` / `WRITE_DRAFT_TOKENS` / `WRITE_ANSWER_MIN` / `WRITE_REVISE_TOKENS`（1500 / 3000 / 1200 / 1200）、`CODE_TOKENS` / `CODE_ANSWER_MIN`（3500 / 1200）、`CONTROLLER_DECISION_TOKENS`（400）、`CODER_MAX_TOKENS` / `CODER_MIN_ROOM`（2048 / 256） |
| 長さ・件数 | `CLAIM_AUDIT_MAX`（監査する文、24）、`CHAT_HISTORY_CHARS`（4000）、`CHAT_THINK_RESERVE`（256）、`WRITE_MAX_EDITS`（8）、`WRITE_TAIL_CHARS`（1200）、`WRITE_RESEARCH_CHARS`（1500）、`CODE_SHOW_TAIL_CHARS`（4000）、`CODE_FIX_STDOUT_CHARS` / `CODE_FIX_STDERR_CHARS`（1000 / 1500）、`CONTROLLER_SUMMARY_CHARS` / `CONTROLLER_PROMPT_SUMMARY_CHARS`（1000 / 500） |
| コード実行 | `SANDBOX_TAIL_BYTES`（8192）、`SANDBOX_MAX_FILES`（20）、`SANDBOX_MAX_FILE_BYTES`（200000）、`SANDBOX_DESKTOP_START_S`（Docker Desktop の起動待ち、180 秒） |
| その他 | `JOB_LOCK_LEASE_S` / `JOB_LOCK_RENEW_S`（ロックの期限 900 秒 / 延長の間隔 60 秒）、`IMAGE_MAX_MB` / `IMAGE_MAX_SIDE`（添付画像 1 枚 10 MB / 4096 px） |

- トークンの値を上げても、1 回の呼び出しは context（`LLM_CONTEXT`、`BONSAI_CTX`）の範囲に収めます。この端末の context 4096 では、それより大きくしても変わりません。
- コンテナ実行の上限（60 秒、2g、2 CPU、256 pids、ネットワークなし、実行 2 回）は安全のための規則なので、`.env` では変えません。参照画像の 4 枚、生成の既定値（832×1216、steps 28 など）、主張の検証の門（20 字の一致）も同じく固定です。

cirka 向けの `POST /coder/turn` は、`LLM_URL`、`LLM_MODEL`、`LLM_CONTEXT`、`AGENT_IDLE_TIMEOUT_S`、`JOB_LOCK_TIMEOUT_S` を使います。

## タイムアウト

エージェントは「何も返ってこない時間」だけで打ち切ります。何かが返ってきている限り、全体にかかる時間では打ち切りません。この端末の上で 27B や拡散モデルを動かす都合上、時間がかかることはよくあるためです。上限は `.env` の `AGENT_IDLE_TIMEOUT_S`（既定 1200 秒 = 20 分）の 1 か所で変えます（変えたら LangGraph を再起動）。

| 待つもの | 「返ってきている」とみなすもの |
|---|---|
| ルータの 27B、検索用の llama-server の応答 | ストリームで届くトークン・思考トークン（応答はすべてストリームで受け取る） |
| llama-server の起動 | `/health` の応答（モデル読み込み中の 503 も含む） |
| ComfyUI の生成（タグ生成と画像生成） | その prompt の進捗イベント（ノードの実行、サンプラーの 1 ステップごとの進捗など）。ComfyUI の LM Connect ノードがルータを待つ時間（read timeout）も同じ値にする |
| ComfyUI のキューが空くのを待つ / もう片方のタブの処理を待つ | 相手の処理が続いていること（ComfyUI がキューに応答している、相手がロックの期限を延ばしている）。止まった相手のロックは 15 分の期限で外れる |
| cirka の `/coder/turn` | トークンと、待っているあいだ 5 秒ごとに送る状態通知 |

- 次のものは時間の上限を既定で持ちません（設定すれば掛かります）: 思考モードの検索全体（`SEARCH_WALL_CLOCK_S`）、主張の検証（`CLAIM_TIMEOUT_S`）、自律モード（`CONTROLLER_WALL_CLOCK_S`）、reader 1 体（`SEARCH_TOTAL_TIMEOUT_S`）、ほかのタブを待つ時間（`JOB_LOCK_TIMEOUT_S`、`SANDBOX_WAIT_S`）。量はラウンド・ページ・手数・主張の数で決まります。
- 次のものは短い上限を残しています。エージェント全体を止めるものではなく、その 1 件を諦めて先へ進むためのものです: Tor 経由の検索・ページ取得の HTTP リクエスト 1 回（`SEARCH_TIMEOUT_S`、30 秒。止まったページは飛ばします）、Tor の起動（`TOR_BOOTSTRAP_TIMEOUT_S`、90 秒）、reader の 1 ページの取得（`BONSAI_PAGE_TIMEOUT_S`、20 秒）、ComfyUI・ルータ・Docker への状態確認の HTTP リクエスト。
- チャットタブのコンテナ実行は 1 回 60 秒で止めます（モデルが書いたコードの安全のための固定の上限で、このリポジトリの規則で決めています）。
- `COMFYUI_TIMEOUT_S`、`CHAT_TIMEOUT_S`、`BONSAI_WORKER_TIMEOUT_S`、`LMSTUDIO_TOKENS_PER_S` は読まなくなりました（`AGENT_IDLE_TIMEOUT_S` にまとめました）。v0.11.0 で `LMSTUDIO_URL` / `LMSTUDIO_MODEL` / `LMSTUDIO_CONTEXT` は `LLM_URL` / `LLM_MODEL` / `LLM_CONTEXT` になりました（古い名前は読みません。`setup-llm.ps1` が新しい名前を書きます）。
- cirka 側の上限は cirka の設定 `idle_timeout_s`（既定 1200 秒）です（[client/README.md](../client/README.md)）。
