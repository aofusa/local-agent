# AGENTS.md

このファイルは、このリポジトリで作業する AI エージェントへの指示である。実装に入る前に、このファイルと `docs/lmstudio-comfyui-workflow-design.md`、複数参照画像を触る場合は `docs/multi-image-reference-work-instruction.md` を読む。

## 現状

フェーズ 1（テキスト、参照画像 0〜2 枚）と、役割付き複数参照画像（0〜4 枚。キャラクター / ポーズ / 画風 / 元画像 / マスク）、LoRA、Chroma1-HD 系統（`docs/chroma-hd-support-work-instruction.md`）、画像 / チャットのタブとチャットタブの Tor 経由検索（`docs/chat-search-tor-bonsai-work-instruction.md`）は実装済みである。構成、セットアップ、起動、確認の手順は `README.md` にある。動画入力（VHS）は未実装で、対象外としている。変更を加えるときも、このファイルと設計書の制約に従う。

## 目的

この端末で起動済みの ComfyUI と LM Studio を使い、同じ端末上の LangGraph エージェントへ、他ホストからプロンプトを送れるようにする。利用者は LangGraph 公式 UI の agent-chat-ui に日本語のプロンプトと、任意で画像または動画を入れる。LangGraph が ComfyUI へその入力を渡し、ComfyUI 上のワークフローが LM Studio の LLM でタグを作り、拡散モデルで静止画を生成する。生成画像はローカルに保存し、同じ画像を agent-chat-ui の応答として表示する。

想定する利用者の操作は次だけである。

1. 他ホストのブラウザで、この端末の agent-chat-ui を開く。
2. 日本語テキストを送る。必要なら画像 0〜4 枚（各画像に役割を選べる）、または動画 1 本を添える。
3. UI 上で進捗のあと、生成された静止画を見る。

## 必読

| 文書 | 役割 |
|---|---|
| このファイル | 入口、UI、他ホストからの到達、画像を UI へ返す責任、作業規則 |
| `docs/lmstudio-comfyui-workflow-design.md` | ComfyUI と LM Studio の連携。実装指示であり、ノード、順序、モデル、メモリ、禁止事項を固定する |
| `docs/multi-image-reference-work-instruction.md` | 複数参照画像の要件と設計。末尾の調査結果に、設計書・このファイルとの差分と吸収方法がある |
| `docs/chroma-hd-support-work-instruction.md` | Chroma1-HD 系統の要件と設計。末尾の実装記録に、このファイル・設計書との差分と吸収方法がある |
| `docs/chat-search-tor-design-bonsai-tabs.md` | 画像 / チャットのタブと、Tor 経由検索（Bonsai ワーカー）の設計 |
| `docs/chat-search-tor-bonsai-work-instruction.md` | 上の実装記録。追加要件、設計書との差分、実測、モデルと役割の対応 |
| `docs/chat-deep-search-creative-sandbox.md` | チャットタブの深い検索、文章、コードと Docker サンドボックス、速い / 思考 / 自動。末尾に実装記録 |

ComfyUI と LM Studio の呼び出し順、ノード ID、プロンプト契約、メモリ上の制約がこのファイルと設計書で食い違う場合は、設計書を優先する。入口、待受、UI、他ホストから画像が見えることに食い違う場合は、このファイルを優先する。どちらにも書かれていない食い違いを見つけたら、実装を進めず利用者に確認する。曖昧な箇所を埋めるために、別の連携方式へ乗り換えない。

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
ComfyUI  http://127.0.0.1:8188
        │  OpenAI 互換 API。ワークフローが呼ぶ
        ▼
LM Studio Local Server  http://127.0.0.1:1234/v1
        │  英語タグの JSON を返す
        ▼
ComfyUI が LLM を unload してから KSampler
        │
        ▼
静止画をローカル保存し、LangGraph が画像の実体を UI へ返す
```

三つのプロセスの分担は固定する。

- LangGraph は、チャット入力の受付、参照メディアの受け取り、ComfyUI への投入、完了待ち、画像の回収、ローカル保存の確認、UI へ返るメッセージの組み立てを行う。
- ComfyUI は、設計書のワークフローで参照画像の取り込み、LM Studio の呼び出し、モデルの eject、タグの分割、チェックポイントによる静止画生成、Save Image を行う。
- LM Studio の LLM は、日本語と参照画像から Danbooru / e621 系タグの JSON を返す。画素は作らない。画素を作るのは ComfyUI のチェックポイントと KSampler である。

LangGraph から LM Studio を直接呼んで、タグ生成や画像生成の経路を置き換えない。例外はチャットタブ（graph `chat`）だけである。チャットタブは会話と検索の計画・統合のために LM Studio を直接呼び、検索の前後で unload する。画像タブ（graph `agent`）の経路は変えない。LLM のロードと unload の順序は、設計書の ComfyUI グラフが決める。同時に複数の生成を走らせない。前の Queue が終わるまで次を投入しない。

## 待受と到達範囲

この端末は Windows である。設計書の対象機は ROG Ally X 級（共有メモリ 24GB 級）で、LM Studio と ComfyUI は実装開始時点でローカル起動済みである前提とする。このリポジトリは、その二つのアプリのインストーラではない。

| プロセス | 待受 | 誰がアクセスするか |
|---|---|---|
| LM Studio | `127.0.0.1:1234` | この端末の ComfyUI だけ |
| ComfyUI | `127.0.0.1:8188` | この端末の LangGraph だけ |
| LangGraph | 他ホストから到達できるアドレス。開発時の既定ポートは `2024` | agent-chat-ui、および他ホスト |
| agent-chat-ui | 他ホストから到達できるアドレス。開発時の既定ポートは `3000` | 利用者のブラウザ |
| Tor | `127.0.0.1:9050`（SOCKS） | この端末の LangGraph（チャットタブの検索）だけ |
| PrismML llama-server | `127.0.0.1:18181〜18190` | この端末の LangGraph だけ。検索中だけ起動する |

ComfyUI は `--listen 127.0.0.1 --port 8188` のままにする。LM Studio、Tor、llama-server もループバックのままにする。他ホストへ開くのは LangGraph と agent-chat-ui だけである。検索の外向き通信は Tor の出口だけを通る（`socks5h://`）。

他ホストのブラウザが UI を開くとき、UI が接続する LangGraph の URL は、そのブラウザから到達できるこの端末のアドレスにする。UI をこの端末で動かしていても、他ホスト向けの接続先を `localhost` のままにすると、相手のブラウザは自分自身へ接続しにいく。グラフ id は `agent` とし、agent-chat-ui の既定 `NEXT_PUBLIC_ASSISTANT_ID` と揃える。

開発サーバは、他ホストから届く待受で起動する。例として LangGraph は `langgraph dev --host 0.0.0.0 --port 2024`、UI もホスト `0.0.0.0` で起動する。認証方式は未確定である。利用者が決めるまで認証を独自に足さない。公開範囲と起動コマンドは、実装時に README へ書く。

LAN に出すのは開発用の到達であり、LangSmith へのクラウドデプロイやクラウド API へのフォールバックを意味しない。

## ComfyUI と LM Studio の契約

詳細、システムプロンプトの要件、失敗時の切り分け、フェーズ順は設計書に従う。実装時にずらしやすい点だけここに置く。

- LLM は LM Studio 上の Qwen3.8 27B abliterated。画像入力にはテキスト GGUF に加え `mmproj` をロードする。API は `http://127.0.0.1:1234/v1`。thinking は無効。
- 画像生成は ComfyUI 上の furry 系チェックポイント。既定ファイル名は `yiffInHell_yihVANTABLACK.safetensors`。環境変数 `CKPT_NAME` で差し替える。
- 24GB 級の共有メモリでは Q4 の 27B がページングで実用にならないため、既定ではセットアップが同じモデルを IQ3_M に再量子化して使う（`scripts/setup-lmstudio.ps1`、README §6）。モデルキーは `.env` の `LMSTUDIO_MODEL`。
- 27B とチェックポイントは同時常駐しない。順序は、LLM がタグを返す、LM Studio から unload する、その後に CheckpointLoader と KSampler、で固定する。eject の前に KSampler へ進めたら失敗である。
- ComfyUI プロセス内に GGUF を載せない。ローカル LLM の実行は LM Studio のサーバだけが行う。
- LLM の出力は次の JSON だけである。説明、Markdown の囲み、思考タグは禁止する。

```json
{"positive":"danbooru tags, short english phrase","negative":"low quality, worst quality, ..."}
```

- 分割に失敗してもリトライしない。生文字列を positive にし、negative は固定の画質タグにして生成まで進む。
- テキストだけのときは Empty Latent、denoise 1.0。参照画像がある img2img の初期 denoise は 0.45。解像度の初期値は 832×1216。KSampler の初期値は steps 28、cfg 5.5、euler ancestral、normal。batch は 1。
- 参照画像は 0〜4 枚で、役割（`character` / `pose` / `style` / `base` / `mask`）を持つ。役割の組み合わせから `planner.select_workflow` が `workflows/sdxl/` のテンプレート ID を一意に決める。役割推定は LangGraph 内のルール（日本語キーワードと序数）で行い、LM Studio は呼ばない。決められないときは LangGraph の interrupt で利用者に確認する。
- `base` は同一性を残す経路（VAE Encode）。`character` / `style` は IP-Adapter Plus、`pose` は DWPose 等の前処理と ControlNet Union。どの役割も役割専用の Vision プロンプトでタグ化し、LLM の入力へ節として渡す。Vision の長辺は 768。画像が 0 枚の実行では Vision を走らせず、Load Image の欠損で落とさない。
- IP-Adapter、ControlNet、前処理、LoRA のローダーは、すべて `ckpt`（eject の後）に依存させる。依存の無い前処理は `FurryJaImageAfter` で `ckpt` の後に止める。
- 動画は参照フレームの供給源に限る。VHS で 1〜4 フレームを抜き、1 枚目を img2img、残りを Vision へ渡す。動画生成モデルはロードしない。VHS が無い間は動画を対象外にし、その旨を README に書く。
- IP-Adapter / ControlNet は複数参照のテンプレートだけが使う。テキストだけと元画像 1 枚の経路（`t2i_basic` / `i2i_basic`）はフェーズ 1 と同じ投入 JSON のまま保つ。
- ノード ID は設計書 §4.1 のまま固定する。LangGraph が書き換えてよい入力は、設計書の表で「フロントが書き換える入力」とされたもの（日本語指示、参照画像のファイル名、seed、および img2img のとき latent 側）と、`workflows/maps/sdxl.json` のスロット（役割ごとの画像ファイル名、強度、denoise、サイズ、ポーズ前処理の候補）に限る。構造の変更は、マップにある前処理候補の差し替えと、`LORAS` による LoraLoader の挿入（`ckpt` の直後）だけである。`llm_backend`、`user_prompt`、`ref_image`、`vision`、`prompt_node`、`eject`、`split`、`ckpt`、`positive`、`negative`、`latent`、`sampler`、`decode`、`save` を別の ID に変えない。
- API 形式ワークフローの投入手順は設計書 §5 に従う。`POST /upload/image`、API JSON の書き換え、`POST /prompt`、WebSocket `/ws` で完了待ち、`GET /history/{prompt_id}`、`/view` で画像を取る。タイムアウトはタグ生成と画像生成のそれぞれに 10 分。この呼び出しを行うのは LangGraph である。

## チャットタブ（会話と Tor 経由検索）

設計は `docs/chat-search-tor-design-bonsai-tabs.md`、実装記録は `docs/chat-search-tor-bonsai-work-instruction.md` にある。変えてはいけない点だけここに置く。

- タブはグラフの選択である。画像タブは graph `agent`（`image` は別名）、チャットタブは graph `chat`。チャットタブは画像を受け取らず、画像タブへ誘導する。
- 検索の通信を開くのは LangGraph（オーケストレータ）だけである。llama-server にはプロキシを渡さない。モデルが出すツール呼び出しは、オーケストレータが URL の許可判定をしてから実行する。
- 検索用のモデル（Bonsai 系と Qwen heretic 系）は、PrismML の llama.cpp fork（`BONSAI_LLAMA_SERVER`）だけで動かす。LM Studio にも ComfyUI にも入れない。常駐させず、使い終わったら PID を kill する。llama-server には起動ごとにランダムな `--api-key` を付ける。
- 画像タブとチャットタブは `job_lock` で直列化する。チャットタブがロックを放すのは、llama-server がすべて消え、LM Studio を unload した後である。
- LM Studio の 27B と reader が同時に載らないときは、計画のあとに 27B を unload する。批評と統合は Ternary-Bonsai-2-27B abliterated（PTQ1_0）が代理で行う。
- モデルと役割の対応は `config/search_models.json`、実機の検証結果は `tools/bonsai/rank.json`（`scripts/probe-bonsai.ps1`、git 管理外）にある。
- チャットグラフの kind は `CHAT` / `SEARCH` / `WRITE` / `CODE` / `TO_IMAGE_TAB` の 5 つ（設計は `docs/chat-deep-search-creative-sandbox.md`）。1 本の `chat` グラフの中で分岐し、グラフを増やさない。文章とコードを書くのは LM Studio の 27B で、検索モデルと画像用プロンプトは使わない。
- モードは `configurable.mode` の `fast` / `think` / `auto`（UI の「速い / 思考 / 自動」。無指定は `fast`、`auto` はルールとルータの判定で片方を選ぶ）。変わるのは予算と思考トークンだけ: 検索は 1 ラウンド / 下位問いの充足判定で最大 4 ラウンド・12 ページ・20 分、文章は一発 / アウトライン→本文→差分推敲、コードは生成のみ / 承認後に Docker で実行（最大 2 回）。思考トークンは回答本文に混ぜない。
- コードの実行は `src/furry_agent/sandbox.py` だけが行う（`python:3.12-slim`、`--network none`、`--read-only`、`/work` のみマウント、2g / 2 CPU / 256 pids、`--cap-drop ALL`、非 root、60 秒、argv のみ）。承認（HITL）前に実行しない。サンドボックスは `job_lock` を握らない。

既定の役割（採否の理由と実測は README「検索で使うモデルと採否」と実装記録 §4）:

| 役割 | モデル | 予備 |
|---|---|---|
| 計画 | LM Studio の Qwen3.8 27B abliterated（計画のあと unload） | 代理リーダー |
| ルータ | Qwen3-1.7B-heretic | Qwen3.5-4B-heretic |
| フィルタ | Bonsai-4B | Qwen3-1.7B-heretic、Qwen3-0.6B-heretic |
| reader | Ternary-Bonsai-8B（最大 3 体） | Qwen3.5-4B-heretic、Bonsai-8B、Qwen3-1.7B-heretic |
| 批評・統合 | Ternary-Bonsai-2-27B abliterated（PTQ1_0、代理リーダー） | Ternary-Bonsai-2-27B、Qwen3.5-4B-heretic、Ternary-Bonsai-8B |

Qwen3-0.6B-heretic は旧形式のルータの検証に落ちたため、フィルタの最後の予備に使う。v0.5.0 のルータ（`kind` を返す形式）の検証には合格したので、ルータの 3 番手にも入る（`tools/bonsai/rank.json`）。1-bit の Bonsai-8B は reader の予備にとどめる。

## 画像の保存と UI への返却

生成のたびに、次の両方を満たす。

1. ComfyUI の Save Image で、この端末のローカルに静止画が残る。
2. 同じ画像が agent-chat-ui の応答として、他ホストのブラウザに表示される。

ComfyUI の `/view` やこの端末のファイルパスは、他ホストのブラウザから開けない。LangGraph がこの端末上で画像のバイトを取得し、採用した版の agent-chat-ui が描画できるメッセージへ載せる。実装時に、その版が画像として描画する content block を確認してから形式を決める。パス文字列や `127.0.0.1:8188` の URL だけを返して完了にしない。

このリポジトリ側の複製は `outputs/` に置く。実行のたびに増える生成画像であり、ソースではない。`outputs/` は git に含めない。エージェントの作業メモを `outputs/` に置かない。

## 置く場所

プログラム、ワークフロー、プロンプト、UI は、このリポジトリの中に作る。ComfyUI 本体と LM Studio 本体は、それぞれの既存インストールのまま使う。カスタムノードの導入先は、ComfyUI インストール配下の `custom_nodes/` である。そのパスは端末ごとに違うので、リポジトリに固定値を書かない。`scripts/setup-comfyui.ps1` が検出して `.env`（git 管理外）に保存する。検出できないときは利用者に確認する。

実装時の配置は次とする。

```
AGENTS.md
docs/lmstudio-comfyui-workflow-design.md
docs/multi-image-reference-work-instruction.md   複数参照画像の要件、設計、調査結果、受け入れ結果
docs/chroma-hd-support-work-instruction.md       Chroma1-HD 系統の要件、設計、実装記録
README.md                         事前準備、セットアップ、起動順、待受、UMA の注記、既知の対象外
CHANGELOG.md                      版ごとの変更
langgraph.json                    graphs.agent がグラフを指す
src/                              LangGraph のグラフ、役割推定（planner）、テンプレート注入、ComfyUI クライアント
comfyui_nodes/furry_ja/           ComfyUI カスタムノード（split / ckpt / image-after / release）。custom_nodes へリンクする
workflows/furry_ja.json           UI 形式
workflows/furry_ja_api.json       フェーズ 1 の API 形式。t2i_basic / i2i_basic の元
workflows/sdxl/*.api.json         役割別テンプレート 24 本。LangGraph が読む（scripts/build_workflows.py が生成）
workflows/maps/sdxl.json          テンプレートのスロット、ポーズ前処理の候補、IP-Adapter の weight 係数
workflows/flux/*.api.json         Chroma1-HD のテンプレート（t2i_basic / i2i_basic）
workflows/maps/flux.json          Chroma のスロット、モデルファイル、サンプラー既定値、対応する役割
workflows/reference/              公式 Chroma1-HD ワークフロー（写し元）
prompts/system_furry_tags.txt
prompts/system_furry_tags_roles.txt
prompts/system_vision_*.txt       caption / style / pose / character
prompts/system_chroma_prose.txt   Chroma 用（英語の説明文）
prompts/system_chat.txt           チャットタブの会話
prompts/system_search*.txt        検索の計画 / ルータ / フィルタ / 批評 / 統合
prompts/system_bonsai_worker.txt  検索の reader（open_page と事実カード）
prompts/system_write_*.txt        チャットタブの文章（アウトライン / 本文 / 推敲）
prompts/system_code_plan.txt      チャットタブのコード生成
config/search_models.json         検索用モデル 8 つのファイル・メモリの目安・タスクごとの順位
tools/tor/torrc                   Tor の設定（tools/ の中で git 管理するのはこれと tools/bonsai/.gitkeep だけ）
scripts/                          セットアップ、起動、確認（PowerShell、UTF-8 BOM 付き）。参照画像用は setup-comfyui-refs.ps1、検索用は setup-tor / start-tor / setup-llamacpp / setup-search-models / probe-bonsai
tests/                            pytest
agent-chat-ui/                    公式 UI。設定で接続する
.env                              端末固有の設定。.env.example から作る。git に含めない
outputs/                          生成画像の複製。git に含めない
logs/ tools/                      実行ログ、ダウンロードしたツール。git に含めない
artifacts/                        下記。git に含めない
```

`agent-chat-ui/` は公式アプリをこのリポジトリへ置き、環境変数でこの端末の LangGraph へ接続する。在庫の UI に加えた変更は、返却画像と検索痕跡の表示（`ai.tsx`、`messages/search-trace.tsx`）、添付画像ごとの役割・強度の指定（`ContentBlocksPreview.tsx`、`MultimodalPreview.tsx`、`use-file-upload.tsx`、`lib/image-roles.ts`）、画像 / チャットのタブとチャットタブの応答モード「自動 / 速い / 思考」（`mode-tabs.tsx`、`thread/index.tsx` での配置）、思考・執筆・コードの手順の表示（`search-trace.tsx`、`ai.tsx`）だけである。これ以上の変更は、在庫の UI では要件を満たせないと確認できたときに限る。グラフ id、待受、公式の導入手順が版で変わった場合は、実装時点の公式クイックスタートに合わせ、結果を README に残す。

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

Python と Node の依存ディレクトリ、キャッシュ、チェックポイント、`mmproj`、その他のモデルウェイトもコミットしない。モデルは LM Studio と ComfyUI の既存の置き場にあるものを使う。

## 実装を指示されたときの順序

設計書 §6 の完了条件を、その順で満たす。呼び出し元は設計書に書かれた薄いフロントではなく LangGraph である。各段が終わるまで次へ進まない。

1. `http://127.0.0.1:1234/v1/models` と `http://127.0.0.1:8188/system_stats` を確認する。応答が無いときは起動手順を README に書き、起動できないことだけを理由にリポジトリ内の実装を放棄しない。起動そのものをエージェントが勝手に広範囲へ変更しない。
2. ComfyUI の既存インストールへ `eedali/LM_Connect` を導入し、再起動後にノード一覧へ出ることを確認する。ローカル GGUF バックエンドは使わない。CUDA 版 llama-cpp-python は入れない。フォールバック条件は設計書 §3.2 に従う。
3. `workflows/furry_ja_api.json` と `workflows/furry_ja.json`、`prompts/system_furry_tags.txt` を作る。
4. LangGraph のグラフを作り、テキストだけで 1 枚生成できることを確認する。ログに、eject が成功したことと、KSampler 開始時に LM Studio のモデルが unloaded であることを残す。
5. 生成画像が `outputs/` と ComfyUI の Save Image の両方に残り、グラフの応答として UI に出ることを確認する。
6. agent-chat-ui をこのリポジトリで起動し、他ホストから日本語 1 文を送って UI 上に静止画が返ることを確認する。
7. 参照画像 1 枚の img2img を確認する。
8. VHS があるときだけ動画フレーム経路を足す。無いときは README に「未導入のため対象外」と書く。

## 受け入れ条件

- 他ホストの agent-chat-ui から日本語 1 文を送り、yiffinhell の静止画が 1 枚、UI に表示される。
- 同じ画像がこの端末のローカルに保存されている。
- 生成された positive がタグ中心で、日本語の主題が落ちていない。
- KSampler の前に LM Studio の 27B が unload されている。同時常駐で OOM になったら失敗。
- 参照画像を渡した実行で、denoise 0.45 のとき元画像の構図が残る。
- 参照が無い実行は Empty Latent を使い、Load Image の欠損で落ちない。
- LLM が JSON を壊しても、生文字列で生成まで進む。
- ComfyUI と LM Studio は `127.0.0.1` のままである。

## 禁止

設計書が採用しないもの、およびこのファイルで入口を固定したことに反するものは作らない。

- LM Studio のチャット GUI や MCP、ImageMCP から ComfyUI を叩く経路。
- LangGraph が LM Studio を直接叩いて、ComfyUI グラフの LLM 呼び出しと eject を置き換えること。
- LLM とチェックポイントの同時常駐。ComfyUI 内での GGUF 常駐。
- クラウド API へのフォールバック。LangSmith クラウドへのデプロイを、このローカル連携の代替にすること。
- 動画生成ワークフロー。Wan、LTX などを含む。
- チャットタブの検索で、クラウド検索 API、CAPTCHA の突破、指紋偽装、`.onion` の巡回を行うこと。Tor を通らない検索の通信。
- 設計書 §5 の薄い `frontend/`。
- ComfyUI または LM Studio をループバック以外へ開くこと。
- ノード ID、JSON 契約、unload 順の変更。

## 未確定

次は決めていない。実装中に都合のよい値へ確定しない。必要になったら利用者に確認する。

- 他ホスト向け待受の認証方式。
- 共有メモリの UMA 割当。実装対象外であり、README に注記するだけにする。

実装時に決めたもの:

- ComfyUI と LM Studio のインストールディレクトリは端末ごとに違う。セットアップスクリプトが検出し、`.env` に保存する。
- チェックポイントの既定は `yiffInHell_yihVANTABLACK.safetensors`（`CKPT_NAME`）。
- agent-chat-ui に返す画像は `{"type": "image", "mimeType": "image/png", "data": <base64>}`。在庫の UI は AI メッセージの画像を描画しないため、`ai.tsx` に最小限の変更を加えた。
- 参照画像の役割と強度は、画像ブロックの `metadata.role` / `metadata.strength` で送る。UI には添付ごとの役割セレクトと強度欄を加えた。役割の確認は在庫の HITL 表示（承認 / 編集 / 却下）を使う。
- LoRA は `.env` の `LORAS`（`名前[:モデル強度[:CLIP 強度]]` のカンマ区切り）。空なら使わない。
- 参照画像用のノードとモデルは `scripts/setup-comfyui-refs.ps1` が入れる（ComfyUI_IPAdapter_plus、comfyui_controlnet_aux、ControlNet Union promax、IP-Adapter Plus SDXL、CLIP-ViT-H、DWPose ONNX、Depth Anything V2 Small）。実行時の自動ダウンロードはしない。
- タイムアウトはタグ生成と画像生成のそれぞれに `COMFYUI_TIMEOUT_S`（600 秒）。
- ComfyUI は `--cache-none` で起動する（`start-comfyui.ps1` と Comfy Desktop の起動引数）。ComfyUI 0.38 では IP-Adapter のキャッシュ済み出力が 2 回目以降の生成を壊した。
- モデル系統は `.env` の `COMFY_MODEL_FAMILY` だけで決める（空 / `sdxl` は yiffInHell とタグ、`flux` は Chroma1-HD と英語の説明文）。チャットの文面では切り替えない。Chroma でも LLM の呼び出しと eject は ComfyUI グラフ内で行い、`ckpt`（`FurryJaDiffusionLoaderAfterEject`）が eject の後に拡散モデル・T5・VAE を読む。ノード ID は SDXL と同じ。Chroma の参照画像は `base` だけで、他の役割は生成せず理由を返す。
- 検索: Tor は Tor Expert Bundle（`scripts/setup-tor.ps1`、`tools/tor`）。llama.cpp は PrismML fork の Vulkan リリース（`scripts/setup-llamacpp.ps1`、`-FromSource` でビルドも可）。モデルは `scripts/setup-search-models.ps1` が `tools/models` に取得する。取得物（Tor、fork の zip、モデル）は SHA-256 を照合する（値は `config/search_models.json` と Tor の配布元）。`BONSAI_RESERVE_MB` の既定は 3072（実測の空き 14GB で代理 27B が入る値）。
- チャットタブのタイムアウトは、モデル呼び出し 1 回が `CHAT_TIMEOUT_S`（1200 秒）、思考モードの検索全体が `SEARCH_WALL_CLOCK_S`（1200 秒）。コード実行の Docker イメージは `scripts/setup-sandbox.ps1` が取得し、実行時は `--pull never`。生成したコードは `artifacts/code/<run_id>/`。
- IP-Adapter のキャラクター weight は強度 × 0.5（`workflows/maps/sdxl.json` の `ipadapter_weight_scale`）。DWPose は人物検出なし + ONNX の CPU 実行。根拠は README の「調整の記録」。

## 作業規則

- 利用者の指示の範囲を超えて、周辺の機能や別アーキテクチャを足さない。
- 設計書に無い連携方式を、調査メモの結論として採用しない。
- 一時ファイル、試行スクリプト、ログは `artifacts/` に置き、成果物のディレクトリへ混ぜない。
- コマンドは PowerShell で動く形にする。
- モデルウェイト、API キー、`.env` の秘密をコミットしない。
- 起動していない外部プロセスを、ドキュメントに書いたコマンドの範囲を超えて修復しない。
- UI を変更した場合は、この端末で表示と送信を確認し、可能なら他ホストから同じ操作を確認する。他ホストから確認できなかった場合は、その旨を結果に書く。
