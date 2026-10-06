# AGENTS.md

このファイルは、このリポジトリで作業する AI エージェントへの指示である。実装に入る前に、このファイルと `docs/llm-comfyui-workflow-design.md`（LLM サーバは `docs/llamacpp-router-design.md`）、複数参照画像を触る場合は `docs/multi-image-reference-work-instruction.md` を読む。

## 現状

フェーズ 1（テキスト、参照画像 0〜2 枚）と、役割付き複数参照画像（0〜4 枚。キャラクター / ポーズ / 画風 / 元画像 / マスク）、LoRA、Chroma1-HD 系統（`docs/chroma-hd-support-work-instruction.md`）、画像 / チャットのタブとチャットタブの Tor 経由検索（`docs/chat-search-tor-bonsai-work-instruction.md`）、チャットタブの深い検索・文章・コードと Docker サンドボックス・速い / 思考 / 自動（`docs/chat-deep-search-creative-sandbox.md`、v0.5.0）、検索の回答の主張単位の検証（`docs/claim-verification-design.md`、v0.6.0。同じ版のローカル文書 `/docs` は v0.8.0 で削除した）、チャットタブの自律モード（制御ループ、`docs/autonomous-controller-design.md`）と CUI `cirka` および `POST /coder/turn`（`docs/locus-cui-design.md`、v0.7.0）、cirka の auto モード（既定）・Claude Code に倣った画面・`docs/logo` のロゴ（v0.8.0。ソースはクライアント側 CUI として `client/`）、LM Studio をやめて llama.cpp のルータで 27B を動かすことと、llama.cpp と ComfyUI をセットアップが `tools/` に自前で導入すること（`docs/llamacpp-router-design.md`、v0.11.0）、ホストのモデル一覧（`config/host_models.json`）から Web と cirka が推論モデル・画像モデルを選ぶことと、モデルごとのパラメータ、Krea 2（Wulver）と Anima（Indigo Furry Mix Anima）の画像系統、macOS（Apple silicon、MLX 優先、`scripts/*.sh`）への対応（`docs/host-model-selection-design.md`、v0.12.0）は実装済みで、他ホストのブラウザからの動作も確認済みである。導入と起動の最小手順は `README.md`、詳細は `docs/setup.md` / `docs/usage.md` / `docs/configuration.md` / `docs/architecture.md` / `docs/troubleshooting.md` にある。動画入力（VHS）は未実装で、対象外としている。変更を加えるときも、このファイルと設計書の制約に従う。

## 目的

この端末でセットアップが導入した ComfyUI と llama.cpp（llama-server のルータ）を使い、同じ端末上の LangGraph エージェントへ、他ホストからプロンプトを送れるようにする。利用者は LangGraph 公式 UI の agent-chat-ui に日本語のプロンプトと、任意で画像または動画を入れる。LangGraph が ComfyUI へその入力を渡し、ComfyUI 上のワークフローが llama.cpp のルータの LLM でタグを作り、拡散モデルで静止画を生成する。生成画像はローカルに保存し、同じ画像を agent-chat-ui の応答として表示する。

想定する利用者の操作は次だけである。

1. 他ホストのブラウザで、この端末の agent-chat-ui を開く。
2. 日本語テキストを送る。必要なら画像 0〜4 枚（各画像に役割を選べる）、または動画 1 本を添える。
3. UI 上で進捗のあと、生成された静止画を見る。

## 必読

| 文書 | 役割 |
|---|---|
| このファイル | 入口、UI、他ホストからの到達、画像を UI へ返す責任、作業規則 |
| `docs/llm-comfyui-workflow-design.md` | ComfyUI と LLM サーバの連携。実装指示であり、ノード、順序、モデル、メモリ、禁止事項を固定する |
| `docs/llamacpp-router-design.md` | LLM サーバ（llama.cpp のルータ）、ComfyUI と llama.cpp の自前導入、Docker の起動方針（v0.11.0）。末尾に実装記録 |
| `docs/multi-image-reference-work-instruction.md` | 複数参照画像の要件と設計。末尾の調査結果に、設計書・このファイルとの差分と吸収方法がある |
| `docs/chroma-hd-support-work-instruction.md` | Chroma1-HD 系統の要件と設計。末尾の実装記録に、このファイル・設計書との差分と吸収方法がある |
| `docs/chat-search-tor-design-bonsai-tabs.md` | 画像 / チャットのタブと、Tor 経由検索（Bonsai ワーカー）の設計 |
| `docs/chat-search-tor-bonsai-work-instruction.md` | 上の実装記録。追加要件、設計書との差分、実測、モデルと役割の対応 |
| `docs/chat-deep-search-creative-sandbox.md` | チャットタブの深い検索、文章、コードと Docker サンドボックス、速い / 思考 / 自動。末尾に実装記録 |
| `docs/claim-verification-design.md` | 検索の回答を主張単位で出典と照らす段（抽出・判定・統合・監査・削除）。末尾に実装記録 |
| `docs/autonomous-controller-design.md` | チャットタブの自律モード（思考モードの複合依頼で、27B が検索・文章・コード・画像案内を選び直す制御ループ）。末尾に実装記録 |
| `docs/locus-cui-design.md` | CUI `cirka`（設計書の作業名は locus）と、そのモデルゲート `POST /coder/turn`。末尾に実装記録 |
| `docs/host-model-selection-design.md` | ホストのモデル一覧（`config/host_models.json`、`GET /models`）からの推論モデル・画像モデルの選択、Web のピッカーと cirka の `/model`・`/image-model`、macOS 対応。末尾に実装記録 |
| `docs/setup.md` / `usage.md` / `configuration.md` / `architecture.md` / `troubleshooting.md` / `third-party-licenses.md` | 利用者向けの詳細（v0.11.0 で README から分けた）。セットアップとモデルの探し方、使い方、`.env`、構成と UI の変更点とログ、トラブルと既知の制約、取得物のライセンス。README は概要・動作環境・インストール・実行・ライセンスだけにする |

ComfyUI と LLM サーバの呼び出し順、ノード ID、プロンプト契約、メモリ上の制約がこのファイルと設計書で食い違う場合は、設計書を優先する。入口、待受、UI、他ホストから画像が見えることに食い違う場合は、このファイルを優先する。どちらにも書かれていない食い違いを見つけたら、実装を進めず利用者に確認する。曖昧な箇所を埋めるために、別の連携方式へ乗り換えない。

設計書のフェーズ 2 に書かれた `frontend/`（単一 HTML と小さな Python）は、このプロジェクトの利用者向け UI ではない。利用者向け UI は agent-chat-ui とする。`frontend/` は、利用者に頼まれない限り作らない。

## 構成

```
他ホストのブラウザ
        │  プロンプト、任意の画像・動画
        ▼
agent-chat-ui（この端末。他ホストから到達できる）
        │
        ▼
LangGraph サーバ（この端末。グラフ id は agent）
        │  ComfyUI HTTP API のみ
        ▼
ComfyUI  http://127.0.0.1:8188（tools/comfyui）
        │  OpenAI 互換 API。ワークフローが呼ぶ
        ▼
llama.cpp ルータ（llama-server --models-preset）  http://127.0.0.1:8080/v1
        │  英語タグの JSON を返す
        ▼
ComfyUI が LLM を unload してから KSampler
        │
        ▼
静止画をローカル保存し、LangGraph が画像の実体を UI へ返す
```

CUI の経路（`client/`、コマンド名 `cirka`）は別の入口である。

```
利用者の端末の cirka（作業ディレクトリでツールを実行）
        │  POST /coder/turn（1 ターンの推論）、/runs/stream（検索は graph chat、画像は graph agent）
        ▼
LangGraph サーバ（この端末。/coder/turn はツールを実行しない）
        │  OpenAI 互換 API（tool calling）
        ▼
llama.cpp ルータ  http://127.0.0.1:8080/v1
```

三つのプロセスの分担は固定する。

- LangGraph は、チャット入力の受付、参照メディアの受け取り、ComfyUI への投入、完了待ち、画像の回収、ローカル保存の確認、UI へ返るメッセージの組み立てを行う。
- ComfyUI は、設計書のワークフローで参照画像の取り込み、LLM サーバの呼び出し、モデルの eject、タグの分割、チェックポイントによる静止画生成、Save Image を行う。
- LLM サーバ（ルータ）の 27B は、日本語と参照画像から Danbooru / e621 系タグの JSON を返す。画素は作らない。画素を作るのは ComfyUI のチェックポイントと KSampler である。

LangGraph から LLM サーバを直接呼んで、タグ生成や画像生成の経路を置き換えない。例外はチャットタブ（graph `chat`）と cirka のモデルゲート（`POST /coder/turn`）だけである。チャットタブは会話と検索の計画・統合のために LLM サーバ（ルータ）を直接呼び、検索の前後で unload する。モデルゲートは cirka の 1 ターン分の補完（tool calling）だけを LLM サーバに渡し、ツールは実行せず、`job_lock`（tab `coder`）を握るあいだだけ呼ぶ。画像タブ（graph `agent`）の経路は変えない。LLM のロードと unload の順序は、設計書の ComfyUI グラフが決める。同時に複数の生成を走らせない。前の Queue が終わるまで次を投入しない。

## 待受と到達範囲

この端末は Windows である。設計書の対象機は ROG Ally X 級（共有メモリ 24GB 級）である。v0.12.0 から macOS（Apple silicon、確認は M4・24GB）でも同じ構成で動く: スクリプトは `scripts/*.sh`（`lib/common.sh`）、llama.cpp は PrismML fork の macOS arm64（Metal）版、ComfyUI は PyTorch の MPS。LLM は MLX 版があるモデルを MLX で動かし（`setup-mlx.sh`、ルータは `furry_agent.mlx_router`）、無いモデルは llama-server で動かす。待受と到達範囲は下の表のとおりで OS によらない。v0.11.0 から、llama.cpp（PrismML fork の Vulkan 版）と ComfyUI はセットアップスクリプトが `tools/` に導入し（`setup-llamacpp.ps1`、`setup-llm.ps1`、`setup-comfyui.ps1`）、`start-llm.ps1` / `start-comfyui.ps1`（`start-all.ps1`）が起動する。LM Studio と既存の ComfyUI のインストールは使わない。

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

詳細、システムプロンプトの要件、失敗時の切り分け、フェーズ順は設計書に従う。実装時にずらしやすい点だけここに置く。

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
- API 形式ワークフローの投入手順は設計書 §5 に従う。`POST /upload/image`、API JSON の書き換え、`POST /prompt`、WebSocket `/ws` で完了待ち、`GET /history/{prompt_id}`、`/view` で画像を取る。待ちは「その prompt の進捗イベントが `AGENT_IDLE_TIMEOUT_S`（既定 20 分）届かないとき」だけ打ち切る（利用者の指定で設計書の 10 分の固定値から変えた。下の「タイムアウト」）。この呼び出しを行うのは LangGraph である。

## チャットタブ（会話と Tor 経由検索）

設計は `docs/chat-search-tor-design-bonsai-tabs.md`、実装記録は `docs/chat-search-tor-bonsai-work-instruction.md` にある。変えてはいけない点だけここに置く。

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

設計は `docs/locus-cui-design.md`（作業名 locus。コマンド名は `cirka` に確定）。ソースはクライアント側の CUI として `client/`（Rust、単一バイナリ）に置く。ディレクトリ名は役割で付け、固有名の `cirka` は実行ファイル名、設定とデータの置き場（`cirka` / `.cirka`）、ロゴにだけ使う。変えてはいけない点だけここに置く。

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

## 置く場所

プログラム、ワークフロー、プロンプト、UI は、このリポジトリの中に作る。ComfyUI 本体と llama.cpp は、セットアップが `tools/comfyui`（検証済みコミット、専用の Python 3.12 venv と GPU に合う PyTorch）と `tools/llama-prism` に導入する（git 管理外）。カスタムノードの導入先は `tools/comfyui/custom_nodes/`、モデルは `tools/comfyui/models/` だけである（外部フォルダは参照しない）。セットアップは指定なしで、この端末に既にあるモデル（LM Studio・以前の ComfyUI のモデルフォルダ、`hf download` の Hugging Face キャッシュ）を探して `tools/` へハードリンクし、無いものだけを Hugging Face のキャッシュ経由（`hf download`）で取得する（`docs/setup.md`「モデルの探し方」）。同じモデルを二重に取得しない。パスは `.env`（`COMFYUI_*`、`LLM_*`。git 管理外）に保存する。

実装時の配置は次とする。

```
AGENTS.md
docs/llm-comfyui-workflow-design.md
docs/llamacpp-router-design.md                  LLM サーバ（llama.cpp のルータ）と ComfyUI / llama.cpp の自前導入、Docker の起動方針
docs/multi-image-reference-work-instruction.md   複数参照画像の要件、設計、調査結果、受け入れ結果
docs/chroma-hd-support-work-instruction.md       Chroma1-HD 系統の要件、設計、実装記録
docs/host-model-selection-design.md             モデルの選択（ホストの一覧、Web のピッカー、cirka）と macOS 対応、実装記録
config/host_models.json           利用者が選べる推論モデル・画像モデル（id、表示名、ファイル、系統、LoRA、モデルごとのパラメータ）
README.md                         概要、動作環境、インストール、実行、ライセンス（最小限。詳細は docs/）
docs/setup.md / usage.md / configuration.md / architecture.md / troubleshooting.md / third-party-licenses.md
                                  セットアップの詳細、使い方、.env、構成、トラブルと制約、取得物のライセンス
CHANGELOG.md                      版ごとの変更
langgraph.json                    graphs.agent がグラフを指す。http.app が /coder/turn（coder_app.py）
src/                              LangGraph のグラフ、役割推定（planner）、テンプレート注入、ComfyUI クライアント。
                                  チャットタブは chat_graph（ルーティングと検索）、write_nodes / code_nodes、chat_common、
                                  modes（速い / 思考 / 自動）、sandbox（Docker）、chat_models（llama-server の起動と停止）、
                                  claim_verify / claim_nodes（主張の検証）、
                                  control_nodes（自律モード）、coder_gate / coder_app（cirka 向けの /coder/turn）、
                                  model_catalog（host_models.json の読み込みと検証）、models_api（GET /models）、
                                  mlx_router（macOS の MLX 優先のルータ。llama.cpp ルータと同じ API）
docs/autonomous-controller-design.md            チャットタブの自律モード（制御ループ）の設計と実装記録
docs/locus-cui-design.md                        CUI cirka とモデルゲートの設計と実装記録
client/                           クライアント側の CUI（Rust、単一バイナリ、実行ファイル名 cirka）。client/target/ は git に
                                  含めない。agent.rs がループ、tools/ がツール、policy.rs が許可、tui.rs が Claude Code に倣った
                                  画面、art.rs と art_data.rs（生成物）がロゴの端末用の絵。テストは cargo test。
                                  client/README.md が cirka の利用者向けの説明
docs/logo/                        cirka のロゴ（cirka-icon / cirka-logo / cirka-design の JPG、icon / logo / image の SVG）
scripts/gen_cirka_art.py          docs/logo の JPG から client/src/art_data.rs（半角ブロック用のビットマップ）を作る
scripts/build-cirka.ps1           cirka のリリースビルドと配布用の zip（dist/、git に含めない）
docs/chat-deep-search-creative-sandbox.md       チャットタブの深い検索、文章、コード、モードの設計と実装記録
docs/claim-verification-design.md               主張単位の検証の設計と実装記録
comfyui_nodes/furry_ja/           ComfyUI カスタムノード（split / ckpt / image-after / release）。custom_nodes へリンクする
workflows/furry_ja.json           UI 形式
workflows/furry_ja_api.json       フェーズ 1 の API 形式。t2i_basic / i2i_basic の元
workflows/sdxl/*.api.json         役割別テンプレート 24 本。LangGraph が読む（scripts/build_workflows.py が生成）
workflows/maps/sdxl.json          テンプレートのスロット、ポーズ前処理の候補、IP-Adapter の weight 係数
workflows/flux/*.api.json         Chroma1-HD のテンプレート（t2i_basic / i2i_basic）
workflows/maps/flux.json          Chroma のスロット、モデルファイル、サンプラー既定値、対応する役割
workflows/krea2/ / anima/         Krea 2（Wulver）と Anima（Indigo Furry Mix Anima）のテンプレート（t2i_basic / i2i_basic）
workflows/maps/krea2.json / anima.json   それぞれのスロット、テキストエンコーダと VAE、サンプラー既定値
workflows/reference/              公式 Chroma1-HD ワークフロー（写し元）
prompts/system_furry_tags.txt
prompts/system_furry_tags_roles.txt
prompts/system_vision_*.txt       caption / style / pose / character
prompts/system_chroma_prose.txt   Chroma 用（英語の説明文）
prompts/system_krea2_prose.txt    Krea 2 用（英語の説明文）。Anima は system_furry_tags.txt
prompts/system_chat.txt           チャットタブの会話
prompts/system_search*.txt        検索の計画 / ルータ / フィルタ / 批評 / 統合
prompts/system_bonsai_worker.txt  検索の reader（open_page と事実カード）
prompts/system_write_*.txt        チャットタブの文章（アウトライン / 本文 / 推敲）
prompts/system_code_plan.txt      チャットタブのコード生成
prompts/system_claim_*.txt        主張の抽出 / 判定（監査も判定と同じ）
prompts/chat/controller.txt       自律モードの判断（道具の選択と最終回答）
config/search_models.json         検索用モデル 8 つのファイル・メモリの目安・タスクごとの順位
config/llm_model.json             27B の元ファイル（Hugging Face、SHA-256）、量子化、ルータのモデル名と既定値
tools/tor/torrc                   Tor の設定（tools/ の中で git 管理するのはこれと tools/bonsai/.gitkeep だけ）
scripts/*.sh, scripts/lib/common.sh   macOS のセットアップ・起動・確認（bash）。setup.sh が順に呼ぶ。setup-mlx.sh が MLX、
                                  setup-image-models.sh / .ps1 が画像モデルのファイル、host_models.py がその一覧とルータのプリセット
scripts/                          セットアップ、起動、確認（PowerShell、UTF-8 BOM 付き）。LLM は setup-llm / start-llm（gguf_info.py が層数を読む）、ComfyUI は setup-comfyui / start-comfyui。参照画像用は setup-comfyui-refs.ps1、検索用は setup-tor / start-tor / setup-llamacpp / setup-search-models / probe-bonsai、コード実行用は setup-sandbox
tests/                            pytest（cirka のテストは client/ の cargo test）
agent-chat-ui/                    公式 UI。設定で接続する
.env                              端末固有の設定。.env.example から作る。git に含めない
outputs/                          生成画像の複製。git に含めない
logs/ tools/                      実行ログ、ダウンロードしたツール。git に含めない
artifacts/                        下記。git に含めない
```

`agent-chat-ui/` は公式アプリをこのリポジトリへ置き、環境変数でこの端末の LangGraph へ接続する。在庫の UI に加えた変更は、返却画像と検索痕跡の表示（`ai.tsx`、`messages/search-trace.tsx`）、添付画像ごとの役割・強度の指定（`ContentBlocksPreview.tsx`、`MultimodalPreview.tsx`、`use-file-upload.tsx`、`lib/image-roles.ts`）、画像 / チャットのタブとチャットタブの応答モード「自動 / 速い / 思考」（`mode-tabs.tsx`、`thread/index.tsx` での配置）、思考・執筆・コードの手順の表示（`search-trace.tsx`、`ai.tsx`）、主張の突き合わせの表の表示（`search-trace.tsx` の `ClaimTraceView`、`ai.tsx`）、自律モードの手順の表示（`search-trace.tsx` の `TaskTraceView` に `kind: "control"` を足しただけ）、送信ボタン横のモデルのピッカーと応答のモデル名（`model-picker.tsx`、`thread/index.tsx` の送信設定、`ai.tsx`）だけである。これ以上の変更は、在庫の UI では要件を満たせないと確認できたときに限る。グラフ id、待受、公式の導入手順が版で変わった場合は、実装時点の公式クイックスタートに合わせ、結果を `docs/architecture.md`（UI の変更点）と README に残す。

設計書 §9 の `frontend/` は作らない。

## artifacts と git

プロジェクトの成果物に直接かかわらない、AI が作業中に作る一時生成物は `artifacts/` に置く。該当するものは、調査メモ、ログ、HTTP のダンプ、試行用スクリプト、不採用の下書き、中間出力である。

成果物は `artifacts/` に置かない。成果物は、このファイル、設計書、README、グラフのソース、`langgraph.json`、ワークフロー JSON、システムプロンプト、UI とその接続設定である。

`artifacts/` は git の管理に含めない。リポジトリの ignore がまだ無い、または `artifacts/` を含んでいない場合は、最初のコミットより前に `.gitignore` へ次を入れる。

```
artifacts/
outputs/
.env
.env.*
!.env.example
```

Python と Node の依存ディレクトリ、キャッシュ、チェックポイント、`mmproj`、その他のモデルウェイトもコミットしない。モデルは `tools/` の下（セットアップが取得）か、`-ModelsDir` で指定した既存の置き場にあるものを使う。

## 実装を指示されたときの順序

設計書 §6 の完了条件を、その順で満たす。呼び出し元は設計書に書かれた薄いフロントではなく LangGraph である。各段が終わるまで次へ進まない。

1. `http://127.0.0.1:8080/v1/models`（llama.cpp のルータ）と `http://127.0.0.1:8188/system_stats` を確認する。応答が無いときは起動手順を README と `docs/usage.md` に書き、起動できないことだけを理由にリポジトリ内の実装を放棄しない。起動そのものをエージェントが勝手に広範囲へ変更しない。
2. セットアップ（`scripts/setup-comfyui.ps1`）が `tools/comfyui` に入れた ComfyUI へ `eedali/LM_Connect` を導入し、再起動後にノード一覧へ出ることを確認する。ローカル GGUF バックエンドは使わない。CUDA 版 llama-cpp-python は入れない。フォールバック条件は設計書 §3.2 に従う。
3. `workflows/furry_ja_api.json` と `workflows/furry_ja.json`、`prompts/system_furry_tags.txt` を作る。
4. LangGraph のグラフを作り、テキストだけで 1 枚生成できることを確認する。ログに、eject が成功したことと、KSampler 開始時にルータのモデルが unloaded であることを残す。
5. 生成画像が `outputs/` と ComfyUI の Save Image の両方に残り、グラフの応答として UI に出ることを確認する。
6. agent-chat-ui をこのリポジトリで起動し、他ホストから日本語 1 文を送って UI 上に静止画が返ることを確認する。
7. 参照画像 1 枚の img2img を確認する。
8. VHS があるときだけ動画フレーム経路を足す。無いときは `docs/troubleshooting.md`（既知の対象外）に「未導入のため対象外」と書く。

## 受け入れ条件

- 他ホストの agent-chat-ui から日本語 1 文を送り、yiffinhell の静止画が 1 枚、UI に表示される。
- 同じ画像がこの端末のローカルに保存されている。
- 生成された positive がタグ中心で、日本語の主題が落ちていない。
- KSampler の前にルータの 27B が unload されている。同時常駐で OOM になったら失敗。
- 参照画像を渡した実行で、denoise 0.45 のとき元画像の構図が残る。
- 参照が無い実行は Empty Latent を使い、Load Image の欠損で落ちない。
- LLM が JSON を壊しても、生文字列で生成まで進む。
- ComfyUI と llama.cpp のルータは `127.0.0.1` のままである。

## 禁止

設計書が採用しないもの、およびこのファイルで入口を固定したことに反するものは作らない。

- LLM のチャット GUI や MCP、ImageMCP から ComfyUI を叩く経路。
- LangGraph が LLM サーバを直接叩いて、ComfyUI グラフの LLM 呼び出しと eject を置き換えること。
- LLM とチェックポイントの同時常駐。ComfyUI 内での GGUF 常駐。
- クラウド API へのフォールバック。LangSmith クラウドへのデプロイを、このローカル連携の代替にすること。
- 動画生成ワークフロー。Wan、LTX などを含む。
- チャットタブの検索で、クラウド検索 API、CAPTCHA の突破、指紋偽装、`.onion` の巡回を行うこと。Tor を通らない検索の通信。
- チャットタブのコードを、承認（HITL）なしで、または `sandbox.py` 以外（ホストのシェル、LangGraph プロセス内の subprocess）で実行すること。コンテナに docker.sock、ホームフォルダ、リポジトリ、`.env` を渡すこと。速いモードで実行すること。
- 思考トークンを回答本文に混ぜること。文章生成に検索モデルや画像用プロンプトを使うこと。
- 主張の検証で、検証器同士の議論・多数決、検証段からの取得や追加検索、モデルの記憶での補完、数値の自動書き換え、支持されない文の言い換え。
- 設計書 §5 の薄い `frontend/`。
- ComfyUI または llama.cpp のルータ・llama-server をループバック以外へ開くこと。
- ノード ID、JSON 契約、unload 順の変更。
- cirka のツールをホスト（LangGraph）で実行すること。cirka から LLM サーバ・ComfyUI・Tor へ直接つなぐこと。`/coder/turn` で会話やファイルの中身をスレッド・ストア・ログに残すこと。
- cirka で、ワークスペースの外や秘密ファイル（`.env`、鍵、`credentials*` など）を扱うこと。auto モードの確認の一覧（`policy::guarded`）を利用者の指示なしに外すこと。bypass を既定にすること。

## 未確定

次は決めていない。実装中に都合のよい値へ確定しない。必要になったら利用者に確認する。

- 他ホスト向け待受の認証方式。
- 共有メモリの UMA 割当。実装対象外であり、`docs/setup.md` に注記するだけにする。

実装時に決めたもの:

- ComfyUI と llama.cpp は `tools/` に自前で導入する（v0.11.0、利用者の指定）。モデルの置き場は指定なしで決まる: 既にある場所（LM Studio・以前の ComfyUI のモデルフォルダ、Hugging Face のキャッシュ）から `tools/` へハードリンクし、Hugging Face のファイルは `hf download`（無ければ uv 経由の huggingface_hub）でキャッシュに取得してからリンクする（同じモデルを二重に取得しない。`scripts/lib/common.ps1` の `Import-ComfyModel` / `Get-HfFile`）。再量子化した IQ3_M の SHA-256 は `config/llm_model.json` に固定し、この端末では変更前の LM Studio のファイルと一致した。ComfyUI は Comfy-Org/ComfyUI の検証済みコミット（v0.38.0-32、`e9027f2b`）、PyTorch は Radeon なら AMD の ROCm 7.2 Windows 版（`repo.radeon.com`）、NVIDIA なら CUDA 12.8、ほかは CPU。パスは `.env` に保存する。
- チェックポイントの既定は `yiffInHell_yihVANTABLACK.safetensors`（v0.12.0 から `config/host_models.json` の `yiffinhell-vantablack`、`DEFAULT_IMAGE_MODEL`）。
- モデルの選択（v0.12.0、利用者の指定。`docs/host-model-selection-design.md` 末尾の実装記録）: `config/host_models.json` に推論 2 つ（Qwen3.8 27B abliterated、Bonsai 2 27B abliterated）と画像 8 つ（yiffInHell VANTABLACK / METALLIC TETRA / XXX-TENDED V2.0、Rekemono、Indigo Furry Mix XL（Noob EPS 11）、Indigo Furry Mix Anima、Chroma1-HD、Wulver）を置く。推論モデルはルータのプリセットの節（`scripts/host_models.py preset` が導入済みの GGUF ごとに書く）で、選ぶとルータへ送る `model` と文脈・思考の有無が変わる。Bonsai は `thinking: off`。SDXL のモデル（5 つ）はすべて以前の `LORAS` の LoRA（novabeast xl v1 rank64 pony、強度 1.0）を使う。パラメータの出典は `docs/configuration.md`「モデルの一覧とパラメータ」。画像のタグ生成の LLM は選択によらずルータの `LLM_MODEL`（ワークフローの `llm_backend`）のまま。`GET /models` は使えないモデルも理由つきで返し、Web はピッカーで無効にし、cirka は拒否する。
- `.env` の整理（v0.12.0、利用者の指定）: 役割が `config/host_models.json` に移った設定と使わなくなった設定（`CKPT_NAME`、`COMFY_MODEL_FAMILY`、`LORAS`、`CHROMA_LORAS`、`CHROMA_UNET_NAME`、`LMSTUDIO_*`、`LOCAL_DOC_ROOTS`、`DOC_EXTENSIONS`）は、`.env.example` からもコードの読み取りからも消した。残っていても読まない。
- 画像系統の追加（v0.12.0）: `krea2`（Wulver、Krea 2 の turbo 系。Qwen3-VL-4B fp8、qwen_image_vae、8 steps、cfg 1、euler / simple、英語の説明文）と `anima`（Indigo Furry Mix Anima。Qwen3 0.6B、qwen_image_vae、er_sde / simple、cfg 4、タグ + score タグ）。どちらも Chroma と同じく `ckpt` が `FurryJaDiffusionLoaderAfterEject`（eject の後に拡散モデル・テキストエンコーダ・VAE を読む）で、ノード ID は SDXL と同じ。参照画像は `base` だけ。テキストエンコーダと VAE は `setup-image-models`（`config/host_models.json` の `downloads`、SHA-256 照合）が入れる。
- macOS（v0.12.0）: LLM は MLX を優先する（`setup-mlx.sh` が MLX 版を入れたモデルは `engine = mlx` の節になり、`furry_agent.mlx_router` が `mlx_lm.server` で動かす）。MLX 版が無いモデル（Bonsai の 1-bit / ternary、量子化済みの GGUF だけのもの）と検索用のモデルは PrismML fork の llama-server（Metal）で動かす。確認機（空き 20GB）では Qwen3.8 27B（GGUF 12.7GB / MLX 4bit 14GB）が入らないため、推論とタグ生成は Bonsai 2 27B abliterated（`LLM_MODEL` もこれ）、画像は yiffInHell VANTABLACK だけを入れた。入れていないモデルは一覧で使えないと表示される。
- agent-chat-ui に返す画像は `{"type": "image", "mimeType": "image/png", "data": <base64>}`。在庫の UI は AI メッセージの画像を描画しないため、`ai.tsx` に最小限の変更を加えた。
- 参照画像の役割と強度は、画像ブロックの `metadata.role` / `metadata.strength` で送る。UI には添付ごとの役割セレクトと強度欄を加えた。役割の確認は在庫の HITL 表示（承認 / 編集 / 却下）を使う。
- LoRA は画像モデルごとに `config/host_models.json` の `loras`（`"<ファイル>:<強度>"` の配列、CLIP 強度は同じ値、0 より大きく 2 以下）。v0.11 までの `.env` の `LORAS` / `CHROMA_LORAS` は読まない。
- 参照画像用のノードとモデルは `scripts/setup-comfyui-refs.ps1` が入れる（ComfyUI_IPAdapter_plus、comfyui_controlnet_aux、ControlNet Union promax、IP-Adapter Plus SDXL、CLIP-ViT-H、DWPose ONNX、Depth Anything V2 Small）。実行時の自動ダウンロードはしない。
- タイムアウト（v0.9.0、利用者の指定）: 何も返ってこない時間が `AGENT_IDLE_TIMEOUT_S`（`.env`、既定 1200 秒、最小 30 秒）続いたときだけ打ち切り、何かが返ってきている限り全体の時間では打ち切らない。モデル呼び出し（ルータの 27B、検索用の llama-server）はすべてストリームで受け取り、トークンと思考トークンで計る（`llm_client.idle_timeout`）。ComfyUI の待ちはその prompt の進捗イベントで計り、LM Connect ノードの `read_timeout_seconds` にも同じ値を入れる（`templates.build_run_prompt`。入力値の差し替えだけで、ノード ID と構造は変えない）。llama-server の起動は `/health` の応答（読み込み中の 503 を含む）で計る。ほかのタブや ComfyUI のキューは、相手が動いているあいだ待つ（ロックの期限 15 分が止まった相手を外す）。全体の予算（`SEARCH_WALL_CLOCK_S`、`CLAIM_TIMEOUT_S`、`CONTROLLER_WALL_CLOCK_S`、`SEARCH_TOTAL_TIMEOUT_S`、`JOB_LOCK_TIMEOUT_S`、`SANDBOX_WAIT_S`）は既定で無しで、設定したときだけ掛かる。1 件を諦めて先へ進むための短い上限（Tor 経由の HTTP リクエスト 30 秒、Tor の起動 90 秒、状態確認の HTTP）とコンテナ実行の 60 秒は残す。時間で `max_tokens` を削らない（context だけで決める）。`COMFYUI_TIMEOUT_S`、`CHAT_TIMEOUT_S`、`BONSAI_WORKER_TIMEOUT_S`、`LMSTUDIO_TOKENS_PER_S` は廃止。cirka は設定 `idle_timeout_s`（既定 1200 秒）で同じ考え方（`bash` は出力が途切れた時間で止める）。
- ComfyUI は `--cache-none` で起動する（`start-comfyui.ps1`）。ComfyUI 0.38 では IP-Adapter のキャッシュ済み出力が 2 回目以降の生成を壊した。
- モデル系統は選んだ画像モデルの `family` で決まる（v0.11 までは `.env` の `COMFY_MODEL_FAMILY`。`sdxl` はタグ、`flux` は Chroma1-HD と英語の説明文、`krea2` / `anima` は上記）。チャットの文面では切り替えない。Chroma でも LLM の呼び出しと eject は ComfyUI グラフ内で行い、`ckpt`（`FurryJaDiffusionLoaderAfterEject`）が eject の後に拡散モデル・T5・VAE を読む。ノード ID は SDXL と同じ。Chroma の参照画像は `base` だけで、他の役割は生成せず理由を返す。
- 検索: Tor は Tor Expert Bundle（`scripts/setup-tor.ps1`、`tools/tor`）。llama.cpp は PrismML fork の Vulkan リリース（`scripts/setup-llamacpp.ps1`、`-FromSource` でビルドも可。27B のルータと `llama-quantize` も同じ build）。モデルは `scripts/setup-search-models.ps1` が `tools/models` に取得する。取得物（Tor、fork の zip、モデル）は SHA-256 を照合する（値は `config/search_models.json` と Tor の配布元）。`BONSAI_RESERVE_MB` の既定は 3072（実測の空き 14GB で代理 27B が入る値）。
- 量と長さの上限（v0.10.0、利用者の指定）: 以前はコードの定数だった上限（検索意図の数、ページの字数、各段の `max_tokens`、監査する文の数、履歴の字数、推敲の件数、出力の末尾、ロックの期限、添付画像の大きさなど）は `.env` で変えられる（`config.env_int` / `env_float`、モジュールの読み込み時に読む。一覧は `docs/configuration.md`「量と長さの上限」と `.env.example` の末尾）。既定値は変えていない。`SEARCH_MAX_ROUNDS`・`SEARCH_MAX_PAGES`・`CLAIM_MAX`・`CLAIM_QUOTE_CHARS`・`CONTROLLER_MAX_STEPS` などの範囲の上限も広げた（既定は設計書の値のまま）。reader の数と検索意図は 7 まで（ポート `BONSAI_BASE_PORT + 0..6`）。固定のまま残すもの: コンテナ実行の上限（60 秒、2g、2 CPU、256 pids、`--network none`、実行 2 回）、主張の検証の門（20 字の一致）、参照画像 4 枚、ノード ID と生成の既定値。
- コード実行の Docker イメージは `scripts/setup-sandbox.ps1` が取得し、実行時は `--pull never`。生成したコードは `artifacts/code/<run_id>/`。
- この端末のルータの 27B は context 4096（`LLM_CONTEXT`）で約 2 トークン/秒（v0.10 までの LM Studio では 0.9〜1.5）。チャットタブは 1 回の呼び出しの `max_tokens`（回答 + 思考）を context に収める（v0.9.0 から時間では削らない）。思考の余地（256 トークン）が無いときは思考を使わず、思考が予算を使い切ったら思考なしで 1 回答え直す。
- Docker（v0.11.0、利用者の指定）: `docker` コマンドがエンジンにつながればそれを使う（Docker Desktop でも Docker Engine でもよく、Desktop には触れない）。つながらないときだけ、承認したコードの実行のあいだ Docker Desktop を起動し（`docker desktop start`、無ければ `Docker Desktop.exe`）、CLI プラグインで起動したときは終わったら止める（VM が約 1.5GB を使い、27B や ComfyUI と取り合うため）。`docker` コマンドが無ければ何も起動しない（`sandbox.engine_state`）。
- 主張の検証（`docs/claim-verification-design.md` §10）: 既定で有効（`CLAIM_VERIFY=1`）、速いモードでも行う。時間の予算 `CLAIM_TIMEOUT_S` は既定で無し（v0.9.0。以前は 600 秒。設計の 120 秒では実測 150〜360 秒の検証が監査に届かなかった）。失敗時は抜粋だけを返す（`CLAIM_VERIFY_FAIL_OPEN=0`）。進捗表は `claim_trace` として UI の折りたたみに出す。`opinion` は使い、数値を含むものは事実の主張として扱う。
- IP-Adapter のキャラクター weight は強度 × 0.5（`workflows/maps/sdxl.json` の `ipadapter_weight_scale`）。DWPose は人物検出なし + ONNX の CPU 実行。根拠は `docs/troubleshooting.md` の「調整の記録」。
- 自律モード（`docs/autonomous-controller-design.md` 末尾の実装記録）: 制御のノードは `control_nodes.py` に置き（write_nodes / code_nodes と同じ形）、Decision のスキーマもそこに置く。道具のメッセージはその id のまま残し、制御のメッセージには新しい id を振る（道具の出力を上書きしない）。文章のあとの最終回答は本文を繰り返さない。利用者が章の確認やコンテナ実行を却下したら、制御もそこで終える。
- CUI の画面（v0.8.0）: Claude Code に倣い、ロゴ入りの枠、枠付きの入力欄と許可モードの行、`⏺` / `⎿` のブロック、差分、スピナー、矢印キーのメニュー。生のキー入力（raw mode）は入力欄とメニューのあいだだけ使い、出力は通常の行のまま（パイプや `-p` でも読める）。ロゴは画像のまま出せないので、`scripts/gen_cirka_art.py` が `docs/logo/cirka-icon.jpg` と `cirka-logo.jpg` を小さなビットマップにし、▀ ▄ █ で描く（24 ビット色の端末ではロゴの赤 #D63A2F）。
- CUI のディレクトリ名（v0.8.1、利用者の指定）: 固有名詞ではなく役割で `client/` とした。コマンド名・設定・データの置き場・スクリプト名（`build-cirka.ps1`、`gen_cirka_art.py`）は `cirka` のまま。
- CUI（`docs/locus-cui-design.md` 末尾の実装記録）: コマンド名は `cirka`。設定は `%APPDATA%\cirka\config.toml`（XDG）< `./.cirka/config.toml` < `CIRKA_HOST` など < `--host`。モデルゲートは素の `POST /coder/turn`（LangGraph のスレッドを使わない）で、`GET /coder/health` が文脈の大きさを返す。27B の tool calling はサーバのネイティブの解析で足りた（v0.10 までは LM Studio、v0.11.0 から llama-server の `--jinja`。XML の自前解析は入れていない）。cirka は context 4096 に合わせ、ツールの説明を短くし、古い結果を 1 行に潰して収める。

## 作業規則

- 利用者の指示の範囲を超えて、周辺の機能や別アーキテクチャを足さない。
- 設計書に無い連携方式を、調査メモの結論として採用しない。
- 一時ファイル、試行スクリプト、ログは `artifacts/` に置き、成果物のディレクトリへ混ぜない。
- コマンドは PowerShell で動く形にする（macOS 向けは `scripts/*.sh` の bash。macOS 付属の bash 3.2 で動く書き方にする）。
- モデルウェイト、API キー、`.env` の秘密をコミットしない。
- 起動していない外部プロセスを、ドキュメントに書いたコマンドの範囲を超えて修復しない。
- UI を変更した場合は、この端末で表示と送信を確認し、可能なら他ホストから同じ操作を確認する。他ホストから確認できなかった場合は、その旨を結果に書く。
- cirka を変更した場合は `client/` で `cargo test` と `cargo clippy` を通し、画面や入力に触れたときは実際の端末（Windows では ConPTY でもよい）で表示と入力を確認する。`docs/logo` を変えたら `scripts/gen_cirka_art.py` で絵を作り直す。
