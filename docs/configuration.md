# 設定（`.env`）

端末ごとの設定は `.env`（`.env.example` から作る。git に含めない）に書きます。全項目と説明は `.env.example` にあります。値を変えたら LangGraph を再起動します。

| キー | 既定 | 説明 |
|---|---|---|
| `COMFYUI_URL` | `http://127.0.0.1:8188` | LangGraph から見た ComfyUI |
| `CKPT_NAME` | `yiffInHell_yihVANTABLACK.safetensors` | 使うチェックポイント（実行時にワークフローの値を上書き） |
| `AGENT_IDLE_TIMEOUT_S` | `1200` | 何も返ってこない時間の上限（秒）。画像タブ・チャットタブ・`/coder/turn` で共通（下の「タイムアウト」） |
| `LORAS` | 空 | 適用する LoRA（[usage.md](usage.md) の「LoRA」） |
| `COMFY_MODEL_FAMILY` | `sdxl` | モデル系統（`workflows/<系統>/`）。`sdxl`（yiffInHell、タグ）または `flux`（Chroma1-HD、英語の説明文） |
| `CHROMA_UNET_NAME` / `CHROMA_TEXT_ENCODER` / `CHROMA_VAE` / `CHROMA_WEIGHT_DTYPE` | 空（マップの値） | Chroma のモデルファイルと読み込み精度（[usage.md](usage.md) の「Chroma1-HD」） |
| `CHROMA_LORAS` | 空 | Chroma に適用する LoRA（書式は `LORAS` と同じ） |
| `CHROMA_MAX_PIXELS` / `CHROMA_STEPS` | 空（1048576 / 28） | Chroma の画素数の上限とステップ数（遅い GPU 向け） |
| `LLM_SERVER` / `LLM_PRESET` / `LLM_PORT` | セットアップが設定 / `tools\llm\models.ini` / `8080` | 27B を動かす llama-server とルータのプリセット、待受ポート（`start-llm.ps1` が使う。ループバックのみ） |
| `LLM_URL` / `LLM_MODEL` | `http://127.0.0.1:8080/v1` / `qwen3.8-27b-abliterated` | ルータの OpenAI 互換 URL と、プリセットのモデル名。ワークフロー、チャットタブ、`/coder/turn` が使う |
| `LLM_CONTEXT` | `4096` | 27B の context（プリセットの `ctx-size`）。1 回の呼び出しの量（回答 + 思考）をこの範囲に収める |
| `COMFYUI_MAIN_DIR` ほか `COMFYUI_*` | セットアップが設定 | `start-comfyui.ps1` が使う ComfyUI（`tools\comfyui`）、その Python、モデルの置き場、`extra_model_paths.yaml` |
| `COMFYUI_EXTRA_ARGS` | 空 | ComfyUI の追加引数 |

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
