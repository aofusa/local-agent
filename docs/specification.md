# 仕様と契約

このプロジェクトの変えてはいけない仕様の詳細です。要点と開発の規則は [AGENTS.md](../AGENTS.md)、各機能の設計と実装記録はこのフォルダの設計書（`*-design.md`、`*-work-instruction.md`）、実装時に決めたことの経緯は [decisions.md](decisions.md) にあります。食い違うときの優先順位は AGENTS.md の「文書」に従います。

## プロセスの分担

三つのプロセスの分担は固定する。

- LangGraph は、チャット入力の受付、参照メディアの受け取り、ComfyUI への投入、完了待ち、画像の回収、ローカル保存の確認、UI へ返るメッセージの組み立てを行う。
- ComfyUI は、設計書のワークフローで参照画像の取り込み、LLM サーバの呼び出し、モデルの eject、タグの分割、チェックポイントによる静止画生成、Save Image を行う。
- LLM サーバ（ルータ）の 27B は、日本語と参照画像から Danbooru / e621 系タグの JSON を返す。画素は作らない。画素を作るのは ComfyUI のチェックポイントと KSampler である。

LangGraph から LLM サーバを直接呼んで、タグ生成や画像生成の経路を置き換えない。例外はチャットタブ（graph `chat`）と cirka のモデルゲート（`POST /coder/turn`）だけである。チャットタブは会話と検索の計画・統合のために LLM サーバ（ルータ）を直接呼び、検索の前後で unload する。モデルゲートは cirka の 1 ターン分の補完（tool calling）だけを LLM サーバに渡し、ツールは実行せず、`job_lock`（tab `coder`）を握るあいだだけ呼ぶ。画像タブ（graph `agent`）の経路は変えない。LLM のロードと unload の順序は、設計書の ComfyUI グラフが決める。同時に複数の生成を走らせない。前の Queue が終わるまで次を投入しない。

## 待受と到達範囲

| プロセス | 待受 | 誰がアクセスするか |
|---|---|---|
| llama.cpp ルータ（llama-server --models-preset。macOS で MLX を使うときは `furry_agent.mlx_router`、同じ API） | `127.0.0.1:8080`（`LLM_PORT`） | この端末の ComfyUI、LangGraph（チャットタブと `/coder/turn`、`/models`）だけ。モデルは要求時に子プロセスで載せ（一度に 1 つ）、unload で止める |
| ComfyUI | `127.0.0.1:8188` | この端末の LangGraph だけ |
| LangGraph | 他ホストから到達できるアドレス。開発時の既定ポートは `2024` | agent-chat-ui、および他ホスト |
| agent-chat-ui | 他ホストから到達できるアドレス。開発時の既定ポートは `3000` | 利用者のブラウザ |
| Tor | `127.0.0.1:9050`（SOCKS） | この端末の LangGraph（チャットタブの検索）だけ |
| PrismML llama-server | `127.0.0.1:18181〜18190` | この端末の LangGraph だけ。検索中だけ起動する |
| Docker サンドボックス | 待受なし（`--network none`、ポートを公開しない） | この端末の LangGraph が承認後に起動する。`docker` がエンジンにつながればそれを使い、つながらないときだけ Docker Desktop を実行のあいだ起動して止める。`docker` コマンドが無ければ何も起動しない |
| cirka（CUI） | 待受なし（クライアント） | 利用者の端末で動き、LangGraph の `/coder/turn`・`/coder/health`・`/runs/stream` へ接続する。接続先は設定（`cirka config set host` など）で決める |

ComfyUI は `--listen 127.0.0.1 --port 8188` のままにする。llama.cpp のルータ、Tor、検索用の llama-server もループバックのままにする。他ホストへ開くのは LangGraph と agent-chat-ui だけである。検索の外向き通信は Tor の出口だけを通る（`socks5h://`）。

他ホストのブラウザが UI を開くとき、UI が接続する LangGraph の URL は、そのブラウザから到達できるこの端末のアドレスにする。UI をこの端末で動かしていても、他ホスト向けの接続先を `localhost` のままにすると、相手のブラウザは自分自身へ接続しにいく。グラフ id は `agent` とし、agent-chat-ui の既定 `NEXT_PUBLIC_ASSISTANT_ID` と揃える。

開発サーバは、他ホストから届く待受で起動する。例として LangGraph は `langgraph dev --host 0.0.0.0 --port 2024`、UI もホスト `0.0.0.0` で起動する。認証方式は未確定である。利用者が決めるまで認証を独自に足さない。公開範囲と起動コマンドは README（最小限）と `docs/usage.md`（詳細）に書く。

LAN に出すのは開発用の到達であり、LangSmith へのクラウドデプロイやクラウド API へのフォールバックを意味しない。

## ComfyUI と LLM サーバの契約

詳細、システムプロンプトの要件、失敗時の切り分け、フェーズ順は [llm-comfyui-workflow-design.md](llm-comfyui-workflow-design.md) に従います。実装時にずらしやすい点を次に置きます。

- LLM は llama.cpp のルータ（llama-server のルータモード、プリセット `tools/llm/models.ini`）上の Qwen3.8 27B abliterated。画像入力にはテキスト GGUF に加え `mmproj` をロードする。API は `http://127.0.0.1:8080/v1`（`LLM_URL`）、モデル名はプリセットの節 `qwen3.8-27b-abliterated`（`LLM_MODEL`）。thinking は既定で無効（プリセットの `reasoning = off`。チャットタブの思考モードだけ `chat_template_kwargs` で有効にする）。
- 画像生成は ComfyUI 上の furry 系モデル。使えるモデルは `config/host_models.json` の `image` に並べ（ファイル名、系統、LoRA、サンプラーのパラメータ）、実行ごとに `configurable.image_model` の id で選ぶ（無指定は `DEFAULT_IMAGE_MODEL`、既定は `yiffinhell-vantablack` = `yiffInHell_yihVANTABLACK.safetensors`）。`CKPT_NAME`・`COMFY_MODEL_FAMILY`・`LORAS` は読まない。本文のモデル名（「Chroma で」）では切り替えない。
- 24GB 級の共有メモリでは Q4 の 27B がページングで実用にならないため、既定ではセットアップが同じモデルを IQ3_M に再量子化して使う（`scripts/setup-llm.ps1`、`docs/setup.md`「メモリと LLM の量子化」）。元の GGUF と SHA-256 は `config/llm_model.json`。プリセットは `load-mode = mmap`（共有メモリの iGPU では無いと読み込みが `ErrorOutOfDeviceMemory` になる）。
- 27B とチェックポイントは同時常駐しない。順序は、LLM がタグを返す、ルータから unload する（`eject` = `FurryJaEjectLLM`、`POST /models/unload` と `GET /models` での確認）、その後に CheckpointLoader と KSampler、で固定する。eject の前に KSampler へ進めたら失敗である。
- ComfyUI プロセス内に GGUF を載せない。ローカル LLM の実行は llama.cpp の llama-server（別プロセス）だけが行う。
- LLM の出力は次の JSON だけである。説明、Markdown の囲み、思考タグは禁止する。

```json
{"positive":"danbooru tags, short english phrase","negative":"low quality, worst quality, ..."}
```

- 分割に失敗してもリトライしない。生文字列を positive にし、negative は固定の画質タグにして生成まで進む。
- テキストだけのときは Empty Latent、denoise 1.0。参照画像がある img2img の初期 denoise は 0.45。解像度の初期値は 832×1216。KSampler の初期値は steps 28、cfg 5.5、euler ancestral、normal。batch は 1。既定の yiffInHell VANTABLACK はこの値のまま（テンプレートの値と同じ）で、ほかのモデルは `config/host_models.json` の `params`（steps、cfg、sampler_name、scheduler、width、height、quality_prefix、negative）を使う。
- 参照画像は 0〜4 枚で、役割（`character` / `pose` / `style` / `base` / `mask`）を持つ。役割の組み合わせから `planner.select_workflow` が `workflows/sdxl/` のテンプレート ID を一意に決める。役割推定は LangGraph 内のルール（日本語キーワードと序数）で行い、LM Studio は呼ばない。決められないときは LangGraph の interrupt で利用者に確認する。
- `base` は同一性を残す経路（VAE Encode）。`character` / `style` は IP-Adapter Plus、`pose` は DWPose 等の前処理と ControlNet Union。どの役割も役割専用の Vision プロンプトでタグ化し、LLM の入力へ節として渡す。Vision の長辺は 768。画像が 0 枚の実行では Vision を走らせず、Load Image の欠損で落とさない。
- IP-Adapter、ControlNet、前処理、LoRA のローダーは、すべて `ckpt`（eject の後）に依存させる。依存の無い前処理は `FurryJaImageAfter` で `ckpt` の後に止める。
- 動画は参照フレームの供給源に限る。VHS で 1〜4 フレームを抜き、1 枚目を img2img、残りを Vision へ渡す。動画生成モデルはロードしない。VHS が無い間は動画を対象外にし、その旨を `docs/troubleshooting.md` に書く。
- IP-Adapter / ControlNet は複数参照のテンプレートだけが使う。テキストだけと元画像 1 枚の経路（`t2i_basic` / `i2i_basic`）はフェーズ 1 と同じ投入 JSON のまま保つ。
- ノード ID は設計書 §4.1 のまま固定する。LangGraph が書き換えてよい入力は、設計書の表で「フロントが書き換える入力」とされたもの（日本語指示、参照画像のファイル名、seed、および img2img のとき latent 側）と、`workflows/maps/<family>.json` のスロット（役割ごとの画像ファイル名、強度、denoise、サイズ、ポーズ前処理の候補、モデルファイル、サンプラーと `split` の quality_prefix / default_negative）に限る。構造の変更は、マップにある前処理候補の差し替えと、画像モデルの `loras` による LoraLoader の挿入（`ckpt` の直後）だけである。`llm_backend`、`user_prompt`、`ref_image`、`vision`、`prompt_node`、`eject`、`split`、`ckpt`、`positive`、`negative`、`latent`、`sampler`、`decode`、`save` を別の ID に変えない。
- API 形式ワークフローの投入手順は設計書 §5 に従う。`POST /upload/image`、API JSON の書き換え、`POST /prompt`、WebSocket `/ws` で完了待ち、`GET /history/{prompt_id}`、`/view` で画像を取る。待ちは「その prompt の進捗イベントが `AGENT_IDLE_TIMEOUT_S`（既定 20 分）届かないとき」だけ打ち切る（利用者の指定で設計書の 10 分の固定値から変えた。[decisions.md](decisions.md) の「タイムアウト」）。この呼び出しを行うのは LangGraph である。

## チャットタブ（会話と Tor 経由検索）

設計は `docs/chat-search-tor-design-bonsai-tabs.md`、実装記録は `docs/chat-search-tor-bonsai-work-instruction.md` にある。変えてはいけない点を次に置く。

- タブはグラフの選択である。画像タブは graph `agent`（`image` は別名）、チャットタブは graph `chat`。チャットタブは画像を受け取らず、画像タブへ誘導する。
- 検索の通信を開くのは LangGraph（オーケストレータ）だけである。llama-server にはプロキシを渡さない。モデルが出すツール呼び出しは、オーケストレータが URL の許可判定をしてから実行する。
- 検索用のモデル（Bonsai 系と Qwen heretic 系）は、PrismML の llama.cpp fork（`BONSAI_LLAMA_SERVER`。27B のルータと同じ build）の個別の llama-server だけで動かす。27B のルータのプリセットにも ComfyUI にも入れない。常駐させず、使い終わったら PID を kill する。llama-server には起動ごとにランダムな `--api-key` を付ける。例外は、利用者が推論モデルとして選べる Ternary-Bonsai-2-27B abliterated（`config/host_models.json` の `bonsai-2-27b-abliterated`）で、これはルータのプリセットの節として載る（同じ GGUF を参照し、二重に置かない）。検索の代理リーダーとしては従来どおり個別の llama-server で動く。
- 画像タブとチャットタブは `job_lock` で直列化する。チャットタブがロックを放すのは、検索用の llama-server がすべて消え、ルータの 27B を unload した後である。
- ルータの 27B と reader が同時に載らないときは、計画のあとに 27B を unload する。批評と統合は Ternary-Bonsai-2-27B abliterated（PTQ1_0）が代理で行う。
- モデルと役割の対応は `config/search_models.json`、実機の検証結果は `tools/bonsai/rank.json`（`scripts/probe-bonsai.ps1`、git 管理外）にある。
- チャットグラフの kind は `CHAT` / `SEARCH` / `WRITE` / `CODE` / `TO_IMAGE_TAB` の 5 つ（設計は `docs/chat-deep-search-creative-sandbox.md`）。1 本の `chat` グラフの中で分岐し、グラフを増やさない。文章とコードを書くのはルータの 27B で、検索モデルと画像用プロンプトは使わない。`/docs`（ローカル文書）は v0.8.0 で削除した。行頭の `/docs` は特別扱いせず、ふつうの文として振り分ける。
- モードは `configurable.mode` の `fast` / `think` / `auto`（UI の「速い / 思考 / 自動」。無指定は `fast`、`auto` はルールとルータの判定で片方を選ぶ）。変わるのは予算と思考トークンだけ: 検索は 1 ラウンド / 下位問いの充足判定で最大 4 ラウンド・12 ページ（時間の予算 `SEARCH_WALL_CLOCK_S` は既定で無し）、文章は一発 / アウトライン→本文→差分推敲、コードは生成のみ / 承認後に Docker で実行（最大 2 回）。思考トークンは回答本文に混ぜない。
- コードの実行は `src/furry_agent/sandbox.py` だけが行う（`python:3.12-slim`、`--network none`、`--read-only`、`/work` のみマウント、2g / 2 CPU / 256 pids、`--cap-drop ALL`、非 root、60 秒、argv のみ）。承認（HITL）前に実行しない。サンドボックスは `job_lock` を握らない。
- 主張の検証（`CLAIM_VERIFY=1`）は、検索で、批評と同じ代理リーダーのプロセスで抽出 → 判定 → 統合 → 監査を行い、`claim_drop` が支持されない文を削除する（言い換えない）。採否を決めるのはオーケストレータの門（`claim_verify.gate`: 実在するカード、20 字の一致または数値・固有名詞、カードに無い数値は不可）で、モデルの判定は参考にとどめる。検証段は通信しない、ルータの 27B を載せ直さない、ツールを渡さない。失敗時の既定（`CLAIM_VERIFY_FAIL_OPEN=0`）は無監査の回答を出さず抜粋だけを返す。`job_lock` は監査が終わり、検索用の llama-server が消え、ルータの 27B を unload するまで放さない。
- 自律モード（`control_nodes.py`）: 思考モード（「自動」で思考になったものを含む）で、検索・文章・コードのうち 2 つ以上、または結果に応じて次が決まる接続（「〜してから」「根拠を確認して」など）を含む依頼だけが入る（`router.is_compound`）。接頭辞・`configurable.task`・添付・続き・速いモードは入らない。27B（ルータに届かなければ代理リーダー）が `ask_json` で 1 手ずつ JSON の Decision を返し、道具は既存の入口ノード（`plan` / `write_brief` / `code_plan`）へエッジで渡す。道具の終端は `controller_record` に戻る。上限は `CONTROLLER_MAX_STEPS`（既定 3。v0.10.0 から `.env` で 50 まで）と、設定したときだけ掛かる壁時計（`CONTROLLER_WALL_CLOCK_S`、`SEARCH_WALL_CLOCK_S` を超えない）、同じ道具と同じ依頼文の再実行は禁止。画像は生成せず画像タブへ案内する（`graph.py` は呼ばない）。章の確認とコンテナ実行の承認は残す。グラフは増やさない。

既定の役割（採否の理由と実測は `docs/usage.md`「検索で使うモデルと採否」と実装記録 §4）:

| 役割 | モデル | 予備 |
|---|---|---|
| 計画 | ルータの Qwen3.8 27B abliterated（計画のあと unload） | 代理リーダー |
| ルータ | Qwen3-1.7B-heretic | Qwen3.5-4B-heretic |
| フィルタ | Bonsai-4B | Qwen3-1.7B-heretic、Qwen3-0.6B-heretic |
| reader | Ternary-Bonsai-8B（最大 3 体） | Qwen3.5-4B-heretic、Bonsai-8B、Qwen3-1.7B-heretic |
| 批評・統合 | Ternary-Bonsai-2-27B abliterated（PTQ1_0、代理リーダー） | Ternary-Bonsai-2-27B、Qwen3.5-4B-heretic、Ternary-Bonsai-8B |

Qwen3-0.6B-heretic は旧形式のルータの検証に落ちたため、フィルタの最後の予備に使う。v0.5.0 のルータ（`kind` を返す形式）の検証には合格したので、ルータの 3 番手にも入る（`tools/bonsai/rank.json`）。1-bit の Bonsai-8B は reader の予備にとどめる。

## CUI（cirka）とモデルゲート

設計は `docs/locus-cui-design.md`（作業名 locus。コマンド名は `cirka` に確定）。ソースはクライアント側の CUI として `client/`（Rust、単一バイナリ）に置く。ディレクトリ名は役割で付け、固有名の `cirka` は実行ファイル名、設定とデータの置き場（`cirka` / `.cirka`）、ロゴにだけ使う。変えてはいけない点を次に置く。

- エージェントループは cirka 側に置く。ツール（ファイルの一覧・検索・読み取り・編集・作成、シェル、タスク一覧、質問）は cirka を起動した端末のワークスペースの中だけで実行する。ホストはツールを実行しない。
- ホストの `POST /coder/turn` は無状態の 1 ターン（SSE: status / thinking / token / tool_call / done / error）。会話やファイルの断片をスレッドやログに残さない（ログは件数と秒数だけ）。システムプロンプトを書き換えない。`job_lock` を握り、画像タブ・チャットタブと同時にルータの 27B を使わない。LangGraph の `langgraph.json` の `http.app`（`src/furry_agent/coder_app.py`）で載せ、グラフは増やさない。
- 検索と画像は既存のグラフ（`chat` の `configurable.task=search`、`agent`）を `/runs/stream` で呼ぶ。cirka は LLM サーバ・ComfyUI・Tor へ直接つながない。返った画像はワークスペースの `cirka-outputs/` に保存し、モデルにはパスだけを渡す。
- 許可モードの既定は `auto`（利用者の指定、v0.8.0）: 編集とコマンドを確認なしで実行し、`policy::guarded` の一覧（push、reset --hard、再帰的な削除、ダウンロードの直接実行、sudo、公開、ディスク・電源・レジストリ）に当たるコマンドだけ確認する。`default` は編集とコマンドの前に確認、`accept-edits` は編集だけ自動、`plan` は実行しない、`bypass` はすべて確認なしで明示したときだけ。Shift+Tab は auto / default / accept-edits / plan を巡回し、bypass には入らない。ワークスペースの外と秘密ファイル（`.env`、鍵、`credentials*` など）はどのモードでも扱わない。
- 認証は足さない（ヘッダの差し込み口 `auth_header` だけ）。
- モデルの選択（v0.12.0）: `/model <id>` は `POST /coder/turn` の `inference_model` と検索の `configurable.inference_model` に、`/image-model <id>` は画像の `configurable.image_model` に載る。id はホストの `GET /models` の一覧と完全一致（部分一致は取らない）、使えないものは理由を出して選択を変えない。保存は `--save`（ユーザー設定、`--project` で `.cirka/config.toml`）とセッション（`/resume` で戻る）。

## 画像の保存と UI への返却

生成のたびに、次の両方を満たす。

1. ComfyUI の Save Image で、この端末のローカルに静止画が残る。
2. 同じ画像が agent-chat-ui の応答として、他ホストのブラウザに表示される。

ComfyUI の `/view` やこの端末のファイルパスは、他ホストのブラウザから開けない。LangGraph がこの端末上で画像のバイトを取得し、採用した版の agent-chat-ui が描画できるメッセージへ載せる。実装時に、その版が画像として描画する content block を確認してから形式を決める。パス文字列や `127.0.0.1:8188` の URL だけを返して完了にしない。

このリポジトリ側の複製は `outputs/` に置く。実行のたびに増える生成画像であり、ソースではない。`outputs/` は git に含めない。エージェントの作業メモを `outputs/` に置かない。

## 置き場所と UI の変更範囲

プログラム、ワークフロー、プロンプト、UI は、このリポジトリの中に作る。ComfyUI 本体と llama.cpp は、セットアップが `tools/comfyui`（検証済みコミット、専用の Python 3.12 venv と GPU に合う PyTorch）と `tools/llama-prism` に導入する（git 管理外）。カスタムノードの導入先は `tools/comfyui/custom_nodes/`、モデルは `tools/comfyui/models/` だけである（外部フォルダは参照しない）。セットアップは指定なしで、この端末に既にあるモデル（LM Studio・以前の ComfyUI のモデルフォルダ、`hf download` の Hugging Face キャッシュ）を探して `tools/` へハードリンクし、無いものだけを Hugging Face のキャッシュ経由（`hf download`）で取得する（`docs/setup.md`「モデルの探し方」）。同じモデルを二重に取得しない。パスは `.env`（`COMFYUI_*`、`LLM_*`。git 管理外）に保存する。

`agent-chat-ui/` は公式アプリをこのリポジトリへ置き、環境変数でこの端末の LangGraph へ接続する。在庫の UI に加えた変更は、返却画像と検索痕跡の表示（`ai.tsx`、`messages/search-trace.tsx`）、添付画像ごとの役割・強度の指定（`ContentBlocksPreview.tsx`、`MultimodalPreview.tsx`、`use-file-upload.tsx`、`lib/image-roles.ts`）、画像 / チャットのタブとチャットタブの応答モード「自動 / 速い / 思考」（`mode-tabs.tsx`、`thread/index.tsx` での配置）、思考・執筆・コードの手順の表示（`search-trace.tsx`、`ai.tsx`）、主張の突き合わせの表の表示（`search-trace.tsx` の `ClaimTraceView`、`ai.tsx`）、自律モードの手順の表示（`search-trace.tsx` の `TaskTraceView` に `kind: "control"` を足しただけ）、送信ボタン横のモデルのピッカーと応答のモデル名（`model-picker.tsx`、`thread/index.tsx` の送信設定、`ai.tsx`）だけである。これ以上の変更は、在庫の UI では要件を満たせないと確認できたときに限る。グラフ id、待受、公式の導入手順が版で変わった場合は、実装時点の公式クイックスタートに合わせ、結果を `docs/architecture.md`（UI の変更点）と README に残す。

設計書 §9 の `frontend/` は作らない。

## 受け入れ条件

- 他ホストの agent-chat-ui から日本語 1 文を送り、yiffinhell の静止画が 1 枚、UI に表示される。
- 同じ画像がこの端末のローカルに保存されている。
- 生成された positive がタグ中心で、日本語の主題が落ちていない。
- KSampler の前にルータの 27B が unload されている。同時常駐で OOM になったら失敗。
- 参照画像を渡した実行で、denoise 0.45 のとき元画像の構図が残る。
- 参照が無い実行は Empty Latent を使い、Load Image の欠損で落ちない。
- LLM が JSON を壊しても、生文字列で生成まで進む。
- ComfyUI と llama.cpp のルータは `127.0.0.1` のままである。
