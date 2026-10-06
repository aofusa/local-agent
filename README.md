# local-agent: 日本語プロンプト → furry 静止画（LangGraph × ComfyUI × llama.cpp）

## 概要

日本語の指示から画像生成や Web 検索を、手元の PC（Windows / Mac）だけで行うローカルエージェント実行基盤です。クラウド API は使わず、外部と通信するのは Web 検索（Tor 経由）とセットアップ時のダウンロードだけです。

- **画像タブ**: 日本語の指示から LLM がタグを作り、ComfyUI の画像モデルで静止画を生成します（参照画像 0〜4 枚）。
- **チャットタブ**: 会話、Tor 経由の Web 検索（出典付き）、文章、コード（承認後に Docker で実行）。
- **cirka**: 別 PC の端末で動く CUI のコーディングエージェント（[client/README.md](client/README.md)）。
- 推論モデルと画像モデルは、送信ボタンの横（cirka は `/model`・`/image-model`）で選べます。

```
他ホストのブラウザ ──> agent-chat-ui   http://<LAN IP>:3000
                         ▼
                   LangGraph       http://<LAN IP>:2024   graph: agent（画像）/ chat（チャット）、/coder/turn・/models
                         │ 画像タブ: ComfyUI HTTP API のみ
                         ▼
                   ComfyUI         http://127.0.0.1:8188  （tools/comfyui、ループバックのみ）
                         │ LM Connect ノード（OpenAI 互換 API）。タグを作ったら LLM を unload して画像を生成
                         ▼
                   llama.cpp       http://127.0.0.1:8080/v1（ルータ。macOS は MLX 優先。ループバックのみ）
                   チャットタブ: LangGraph ──> llama.cpp ルータ / 検索用 llama-server（検索中だけ）/ Tor / Docker
別 PC の端末 ──> cirka ──> LangGraph（POST /coder/turn、/runs/stream）
```

## QuickStart

### 1. セットアップと起動

Windows（PowerShell）:

```powershell
winget install Git.Git; winget install astral-sh.uv; winget install OpenJS.NodeJS.LTS
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
git clone https://github.com/aofusa/local-agent.git local-agent; cd local-agent
.\scripts\setup.ps1 -OpenFirewall     # 初回は 1 時間ほど（何度実行しても安全）
.\scripts\start-all.ps1               # 起動
.\scripts\doctor.ps1                  # 確認
```

macOS（ターミナル）:

```bash
brew install git uv node huggingface-cli
git clone https://github.com/aofusa/local-agent.git local-agent && cd local-agent
scripts/setup.sh                      # 入れるモデルを絞るオプションは docs/setup.md
scripts/start-all.sh                  # 起動（止めるときは scripts/stop-all.sh）
scripts/doctor.sh                     # 確認
```

画像モデルのチェックポイント（例: `yiffInHell_yihVANTABLACK.safetensors`、Civitai など。一覧は `config/host_models.json`）は利用者が入手して `tools/comfyui/models/checkpoints` に置きます。この PC に以前の ComfyUI のモデルフォルダがあれば、セットアップが自動で取り込みます。

### 2. Web ブラウザで開く

起動したら、Web ブラウザで次の URL を開きます。

| どこから | URL |
|---|---|
| この PC | http://127.0.0.1:3000 |
| 同じ LAN の別の PC・スマートフォン | `http://<この PC の LAN IP>:3000`（`start-all` が表示する URL） |

既定で、同じ LAN の他のホストからもアクセスできます（UI と LangGraph が `0.0.0.0` で待ち受けます。ComfyUI と LLM はこの PC の中だけ）。

### 3. 送る

画面上部のタブで **画像** / **チャット** を選び、日本語で送ります（例: `夕焼けの海辺に立つ、白い毛並みの狼獣人の女性、和服`）。

> このアプリに認証はありません。信頼できる LAN の中だけで使い、インターネットへ公開しないでください。
> 生成物と使用するモデルのライセンス・利用規約（成人向け表現を含む）は利用者の責任で確認してください。

## ドキュメント

| ドキュメント | 内容 |
|---|---|
| [docs/setup.md](docs/setup.md) | セットアップの詳細とオプション、macOS、モデルの置き方 |
| [docs/usage.md](docs/usage.md) | 使い方（モデルの選択、画像タブ、チャットタブ、cirka） |
| [docs/configuration.md](docs/configuration.md) | `.env` の設定、モデルの一覧とパラメータ |
| [docs/models.md](docs/models.md) | 採用モデルと選定の理由・評価、採らなかったもの |
| [docs/architecture.md](docs/architecture.md) | 構成とファイルの配置 |
| [docs/troubleshooting.md](docs/troubleshooting.md) | トラブルシューティングと既知の制約 |
| [AGENTS.md](AGENTS.md) | 開発者向けの規則と構成（仕様は [docs/specification.md](docs/specification.md)） |
| [CHANGELOG.md](CHANGELOG.md) | 変更履歴 |

## 動作環境

| 項目 | 要件 |
|---|---|
| OS | Windows 10 / 11、または macOS 15 以降（Apple silicon） |
| メモリ | 24GB 級以上 |
| GPU | Windows: Vulkan が使える GPU（ComfyUI は AMD Radeon / NVIDIA / CPU）。Mac: Apple silicon の GPU（Metal） |
| ディスク | 空き 70GB 以上（全モデル）。一部のモデルだけなら 20GB 程度から |
| ツール | Git、[uv](https://docs.astral.sh/uv/)、Node.js 20 以上（Mac は Homebrew と `hf` も）。cirka をビルドするなら Rust 1.85 以上、コード実行を使うなら Docker |

確認済み: ROG Ally X（Radeon 890M、24GB）と Windows 11、Apple M4（24GB）と macOS 15.5。

## ライセンス

このリポジトリのコードは MIT または Apache-2.0（[LICENSE-MIT](LICENSE-MIT)、[LICENSE-APACHE](LICENSE-APACHE)）です。
`agent-chat-ui/` は upstream（[langchain-ai/agent-chat-ui](https://github.com/langchain-ai/agent-chat-ui)）の MIT ライセンスです。
セットアップが取得する ComfyUI、llama.cpp、カスタムノード、モデルなどは各配布元のライセンスに従います（[docs/third-party-licenses.md](docs/third-party-licenses.md)）。
