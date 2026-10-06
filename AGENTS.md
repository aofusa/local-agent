# AGENTS.md

このリポジトリで作業する AI エージェントと開発者への指示です。作業の前にこのファイルと [docs/specification.md](docs/specification.md)（仕様と契約）を読み、触る機能の設計書も読みます。

## 目的

LAN 内の別ホストのブラウザ（agent-chat-ui）や CUI（cirka）から日本語の指示を送り、この端末（Windows、または Apple silicon の macOS）の上だけで、静止画の生成・会話・Tor 経由の検索・文章・コード・コーディングエージェントを行う。クラウド API は使わない。

- **画像タブ**: LangGraph が ComfyUI へ投入し、ComfyUI のワークフローが llama.cpp のルータの LLM でタグ（または英語の説明文）を作り、LLM を unload してから拡散モデルで静止画を作る。画像はこの端末に保存し、同じ画像を UI に返す。
- **チャットタブ**: 会話、Tor 経由の検索（出典と主張の突き合わせ付き）、文章、コード（承認後に Docker で実行）、自律モード。
- **cirka**: 利用者の端末で動く CUI。モデルはこの端末の LLM を `POST /coder/turn` で使い、ツールは cirka の端末で実行する。
- 推論モデルと画像モデルは `config/host_models.json` の一覧から、Web は送信ボタン横、cirka は `/model`・`/image-model` で選ぶ。

## 文書

| 文書 | 内容 |
|---|---|
| [docs/specification.md](docs/specification.md) | 変えてはいけない仕様の詳細（プロセスの分担、待受、ComfyUI と LLM の契約、チャットタブ、cirka、画像の返却、UI の変更範囲、受け入れ条件） |
| [docs/decisions.md](docs/decisions.md) | 実装時に決めたことと理由、版ごとの経緯 |
| [docs/models.md](docs/models.md) | 採用モデルの選定理由と評価、採らなかったもの。モデルを足す・設定値を変えるときに先に読み、結果をここに追記する |
| 設計書（`docs/*-design.md`、`docs/*-work-instruction.md`） | 機能ごとの設計と実装記録。ComfyUI と LLM の連携は [llm-comfyui-workflow-design.md](docs/llm-comfyui-workflow-design.md)、LLM サーバは [llamacpp-router-design.md](docs/llamacpp-router-design.md)、モデルの選択と macOS は [host-model-selection-design.md](docs/host-model-selection-design.md) |
| [docs/architecture.md](docs/architecture.md) | ファイルの配置、ワークフローのノード、UI の変更点、ログ |
| [docs/setup.md](docs/setup.md) / [usage.md](docs/usage.md) / [configuration.md](docs/configuration.md) / [troubleshooting.md](docs/troubleshooting.md) / [third-party-licenses.md](docs/third-party-licenses.md) | 利用者向けの詳細 |
| [CHANGELOG.md](CHANGELOG.md) | 版ごとの変更 |

ComfyUI と LLM サーバの呼び出し順、ノード ID、プロンプト契約、メモリの制約が食い違うときは設計書を優先する。入口、待受、UI、他ホストから画像が見えることが食い違うときは、このファイルと specification.md を優先する。どれにも書かれていない食い違いは、実装を進めず利用者に確認する。曖昧な箇所を埋めるために別の連携方式へ乗り換えない。

## 構成

```
他ホストのブラウザ ─> agent-chat-ui（:3000）─> LangGraph（:2024。graph agent = 画像、chat = チャット、
                                                 http.app = /coder/turn・/coder/health・/models）
別ホストの cirka ──────────────────────────────> LangGraph
LangGraph ─> ComfyUI（127.0.0.1:8188）─> llama.cpp ルータ（127.0.0.1:8080）  画像: LLM → eject → 拡散モデル
LangGraph ─> llama.cpp ルータ / 検索用 llama-server（127.0.0.1:18181〜）/ Tor（127.0.0.1:9050）/ Docker
```

- 他ホストへ開くのは LangGraph と agent-chat-ui だけ。ComfyUI、llama.cpp のルータ、検索用 llama-server、Tor はループバックのまま。
- UI が接続する LangGraph の URL は、他ホストのブラウザから届くこの端末のアドレスにする（`localhost` にしない）。
- 認証は未確定。利用者が決めるまで足さない。LAN に出すのは開発用の到達で、クラウドへのデプロイやフォールバックではない。

## 固定の規則

- LangGraph は画像タブの LLM 呼び出しを置き換えない。タグ生成、eject、モデルの読み込みは ComfyUI のワークフロー内で行う。LangGraph が LLM を直接呼ぶのは、チャットタブと `/coder/turn` だけ。
- LLM と拡散モデルは同時に常駐させない。順序は「LLM がタグを返す → `eject` が unload を確認 → `ckpt` が読み込み → KSampler」で固定。ComfyUI の中で GGUF を動かさない。
- ノード ID、LLM の出力の JSON 契約（`{"positive","negative"}`）、unload の順は変えない。LangGraph が書き換えてよいのはマップのスロットと LoRA の挿入だけ。
- モデル、LoRA、モデルごとのパラメータは `config/host_models.json` に書く（`.env` やコードに直書きしない）。クライアントは id を送り、ホストは未知・使えない id を拒否して、別のモデルへ自動で替えない。
- 画像タブ・チャットタブ・`/coder/turn` は `job_lock` で直列化する。同時に複数の生成を走らせない。
- 検索の通信は LangGraph だけが Tor（`socks5h://`）経由で開く。コードの実行は承認後に `sandbox.py` の Docker だけで行う。
- cirka のツールは cirka の端末のワークスペースの中だけで実行する。ホストはツールを実行せず、会話やファイルの中身を残さない。

各規則の詳細は [docs/specification.md](docs/specification.md) にあります。

## 禁止

- LLM のチャット GUI、MCP、ImageMCP から ComfyUI を叩く経路。LangGraph が ComfyUI の LLM 呼び出しと eject を置き換えること。
- LLM とチェックポイントの同時常駐、ComfyUI 内での GGUF 常駐。
- クラウド API へのフォールバック、LangSmith クラウドへのデプロイを代わりにすること。
- 動画生成ワークフロー（Wan、LTX など）。設計書 §5 の薄い `frontend/`。
- 検索でのクラウド検索 API、CAPTCHA の突破、指紋偽装、`.onion` の巡回、Tor を通らない通信。
- コードを承認なしで、または `sandbox.py` 以外で実行すること。コンテナに docker.sock、ホーム、リポジトリ、`.env` を渡すこと。速いモードで実行すること。
- 思考トークンを回答本文に混ぜること。文章生成に検索モデルや画像用プロンプトを使うこと。
- 主張の検証での議論・多数決、検証段からの取得、記憶での補完、数値の書き換え、支持されない文の言い換え。
- ComfyUI、llama.cpp のルータ、llama-server をループバック以外へ開くこと。
- cirka のツールをホストで実行すること。cirka から LLM サーバ・ComfyUI・Tor へ直接つなぐこと。`/coder/turn` で会話やファイルの中身を残すこと。ワークスペースの外や秘密ファイル（`.env`、鍵、`credentials*`）を扱うこと。auto モードの確認の一覧（`policy::guarded`）を指示なしに外すこと。bypass を既定にすること。

## 未確定

実装中に都合のよい値へ確定せず、必要になったら利用者に確認する。

- 他ホスト向けの待受の認証方式。
- 共有メモリの UMA 割当（実装対象外。`docs/setup.md` に注記するだけ）。

## リポジトリ

| 場所 | 内容 |
|---|---|
| `src/furry_agent/` | LangGraph のグラフ（`graph.py` 画像、`chat_graph.py` チャット）、`coder_gate.py`（`/coder/turn`）、`models_api.py`（`/models`）、`model_catalog.py`、`mlx_router.py`（macOS） |
| `comfyui_nodes/furry_ja/` | ComfyUI のカスタムノード（eject、ckpt、split など） |
| `workflows/` | ワークフローのテンプレートとマップ（`scripts/build_workflows.py` が `prompts/` から生成。手で直さない） |
| `prompts/` | システムプロンプト |
| `config/` | `host_models.json`（選べるモデル）、`llm_model.json`（27B）、`search_models.json`（検索モデル） |
| `client/` | cirka（Rust） |
| `agent-chat-ui/` | 公式 UI（変更は specification.md の範囲だけ） |
| `scripts/` | セットアップ・起動・確認。Windows は `*.ps1`（UTF-8 BOM 付き）、macOS は `*.sh`（bash 3.2 で動く書き方） |
| `tests/` | pytest（cirka は `client/` の `cargo test`） |
| `tools/`、`outputs/`、`logs/`、`artifacts/`、`.env` | git 管理外（取得物、生成画像、ログ、作業中の一時物、端末の設定） |

配置の詳細は [docs/architecture.md](docs/architecture.md)。

## 開発の流れ

1. `main` から作業ブランチを切る。利用者の指示の範囲を超えて機能や別アーキテクチャを足さない。設計書に無い連携方式を調査の結論として採用しない。
2. 変更に合わせてテストを足し、適切な粒度でコミットする（コミットメッセージは英語、本文に理由）。
   - `uv run pytest`（Python とスクリプト）
   - `client/` で `cargo test` と `cargo clippy`（cirka を変えたとき）
   - `prompts/` を変えたら `uv run python scripts/build_workflows.py`、`docs/logo` を変えたら `scripts/gen_cirka_art.py`
3. 実機で確かめる。画像生成（SDXL、Chroma など）、Tor 検索、cirka のエージェントのデグレを確認する。UI を変えたらこの端末と他ホストのブラウザで表示と送信を、cirka の画面や入力を変えたら実際の端末（Windows では ConPTY でもよい）で確かめる。確かめられなかったことは結果に書く。
4. ドキュメントを直す。README.md は概要・QuickStart・動作環境・ライセンスだけにし、詳細は `docs/` へ書く。このファイルは規則と構成だけにし、決めたことや経緯は `docs/decisions.md`、版ごとの変更は `CHANGELOG.md` に書く。
5. 利用者の指示があればマージ・push・タグ付けをする。

作業上の注意:

- 一時ファイル、試行スクリプト、ログ、調査メモは `artifacts/` に置き、成果物のディレクトリへ混ぜない（成果物は文書、ソース、`langgraph.json`、ワークフロー、プロンプト、UI とその設定）。
- モデルウェイト、API キー、`.env` の秘密、`tools/`・`outputs/` の中身をコミットしない。モデルはセットアップが `tools/` に取り込む（同じモデルを二重に取得しない）。
- 起動していない外部プロセスを、ドキュメントに書いたコマンドの範囲を超えて修復しない。
- コマンドは PowerShell で動く形にする（macOS 向けは `scripts/*.sh`）。
