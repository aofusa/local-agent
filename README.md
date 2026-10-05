# local-agent: 日本語プロンプト → furry 静止画（LangGraph × ComfyUI × llama.cpp）

LAN 内の別の PC やスマートフォンのブラウザから日本語の指示（任意で参照画像 0〜4 枚）を送ると、Windows 機の上で静止画を作って返すローカルエージェントです。
クラウド API は使わず、すべてこの PC の上で動きます。

- **画像タブ**: LangGraph がワークフローを選んで ComfyUI に投入し、ComfyUI が llama.cpp 上の 27B LLM（Huihui Qwen3.8 27B Abliterated）でタグを作り、LLM を unload してから SDXL（yiffInHell）または Chroma1-HD で画像を生成します。参照画像は役割（キャラクター・ポーズ・画風・元画像・マスク）付きで使えます。
- **チャットタブ**: 27B との会話、Tor 経由の Web 検索（出典付き、主張ごとに出典と照合）、文章、コード（承認後に Docker で実行）、道具を順に使う自律モード。
- **cirka**: 別 PC の端末で動く CUI のコーディングエージェント（Rust、`client/`）。モデルはこの PC の 27B を使い、ファイル操作とコマンドは cirka を動かす PC で実行します。

```
他ホストのブラウザ ──> agent-chat-ui   http://<LAN IP>:3000
                         ▼
                   LangGraph       http://<LAN IP>:2024   graph: agent（画像）/ chat（チャット）、/coder/turn（cirka）
                         │ 画像タブ: ComfyUI HTTP API のみ
                         ▼
                   ComfyUI         http://127.0.0.1:8188  （tools\comfyui、ループバックのみ）
                         │ LM Connect ノード（OpenAI 互換 API）
                         ▼
                   llama.cpp       http://127.0.0.1:8080/v1（llama-server のルータ、ループバックのみ）
                   チャットタブ: LangGraph ──> llama.cpp ルータ / 検索用 llama-server（検索中だけ）/ Tor / Docker
別 PC の端末 ──> cirka ──> LangGraph（POST /coder/turn、/runs/stream）
```

## 動作環境

| 項目 | 要件 |
|---|---|
| OS | Windows 10 / 11（PowerShell 5.1 / 7） |
| メモリ | 24GB 級以上（27B と画像モデルは順番に載せ替えます） |
| GPU | Vulkan が使える GPU。ComfyUI は AMD Radeon（ROCm）/ NVIDIA（CUDA）/ CPU |
| ディスク | 空き 70GB 以上 |
| ツール | Git、[uv](https://docs.astral.sh/uv/)、Node.js 20 以上。cirka を自分でビルドするなら Rust 1.85 以上。コード実行を使うなら Docker（任意） |

確認済み構成: ROG Ally X（Radeon 890M、共有メモリ 24GB）、Windows 11。

## インストール

```powershell
winget install Git.Git; winget install astral-sh.uv; winget install OpenJS.NodeJS.LTS
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

git clone <このリポジトリの URL> local-agent
cd local-agent
.\scripts\setup.ps1 -OpenFirewall
```

`setup.ps1` は次を `tools\`（git 管理外）に用意します。何度実行しても安全です。初回はダウンロードと LLM の再量子化に 1 時間ほどかかります。

- llama.cpp（Vulkan 版）と 27B の LLM（Hugging Face から取得、SHA-256 照合、IQ3_M に再量子化）、ルータの設定
- ComfyUI（専用の Python 環境と GPU に合う PyTorch）、カスタムノード、参照画像用のモデル
- Tor と検索用の小さいモデル（`-SkipSearch` で省略）
- LangGraph の Python 環境と agent-chat-ui の依存、ファイアウォール（`-OpenFirewall`、Private のみ）

チェックポイント（`yiffInHell_yihVANTABLACK.safetensors`、[Civitai](https://civitai.com/models/1570986)）は利用者が入手して `tools\comfyui\models\checkpoints` に置きます。
既存の ComfyUI のモデルフォルダがあれば `-ModelsDir <models フォルダ>` でそのまま使えます。コード実行を使う場合は `.\scripts\setup-sandbox.ps1` も実行します。
オプションと各スクリプトの内容は [docs/setup.md](docs/setup.md) にあります。

## 実行

```powershell
.\scripts\start-all.ps1      # Tor / llama.cpp ルータ / ComfyUI / LangGraph / agent-chat-ui を起動
.\scripts\doctor.ps1         # 設定と待受を確認
```

表示される `http://<LAN IP>:3000` を他ホストのブラウザで開き、画面上部のタブで **画像** / **チャット** を選んで日本語で送ります（例: `夕焼けの海辺に立つ、白い毛並みの狼獣人の女性、和服`）。
止めるときは各ウィンドウで Ctrl+C を押します。

cirka は `client\` で `cargo build --release`（または `.\scripts\build-cirka.ps1`）してから、使う PC で `cirka config set host http://<LAN IP>:2024`、作業ディレクトリで `cirka` を起動します（[client/README.md](client/README.md)）。

> このアプリに認証はありません。信頼できる LAN の中だけで使い、インターネットへ公開しないでください。
> 生成物と使用するモデルのライセンス・利用規約（成人向け表現を含む）は利用者の責任で確認してください。

## ドキュメント

| 文書 | 内容 |
|---|---|
| [docs/setup.md](docs/setup.md) | セットアップの詳細、オプション、手動での設定、メモリと量子化 |
| [docs/usage.md](docs/usage.md) | 起動の詳細、画像タブ（参照画像、LoRA、Chroma1-HD）、チャットタブ（検索、文章、コード、自律モード）、cirka |
| [docs/configuration.md](docs/configuration.md) | `.env` の設定、量と長さの上限、タイムアウト |
| [docs/architecture.md](docs/architecture.md) | ファイル構成、ワークフローのノード、UI の変更点、ログ、開発 |
| [docs/troubleshooting.md](docs/troubleshooting.md) | トラブルシューティング、調整の記録、既知の制約 |
| [docs/third-party-licenses.md](docs/third-party-licenses.md) | セットアップが取得するもののライセンス |
| [AGENTS.md](AGENTS.md) と `docs/*-design.md` などの設計書 | 仕様と設計、実装記録（LLM を llama.cpp に移した経緯は [docs/llamacpp-router-design.md](docs/llamacpp-router-design.md)） |
| [CHANGELOG.md](CHANGELOG.md) | 変更履歴 |

## 開発

```powershell
uv run pytest                     # Python と PowerShell スクリプトのテスト
cd client; cargo test             # cirka のテスト
```

## ライセンス

このリポジトリのコードは MIT または Apache-2.0（[LICENSE-MIT](LICENSE-MIT)、[LICENSE-APACHE](LICENSE-APACHE)）です。
`agent-chat-ui/` は upstream（[langchain-ai/agent-chat-ui](https://github.com/langchain-ai/agent-chat-ui)）の MIT ライセンスです（変更箇所は [docs/architecture.md](docs/architecture.md)）。
セットアップが取得する ComfyUI、llama.cpp、カスタムノード、モデルなどは各配布元のライセンスに従います（[docs/third-party-licenses.md](docs/third-party-licenses.md)）。
