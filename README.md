# local-agent: 日本語プロンプト → furry 静止画・動画（LangGraph × ComfyUI × llama.cpp）

## 概要

日本語の指示から画像生成・動画生成や Web 検索を、手元の PC（Windows / Mac）だけで行うローカルエージェント実行基盤です（動画タブのセットアップは今は Windows だけ）。クラウド API は使わず、外部と通信するのは Web 検索（Tor 経由）とセットアップ時のダウンロードだけです。

- **画像タブ**: 日本語の指示から LLM がタグを作り、ComfyUI の画像モデルで静止画を生成します（参照画像 0〜4 枚）。
- **チャットタブ**: 会話、Tor 経由の Web 検索（出典付き）、文章、コード（承認後に Docker で実行）。
- **動画タブ**: 画像 1 枚と日本語の指示から、ケモノに特化した短い動画を作ります（Wan 2.2 I2V。画像タブの画像から「この画像を動画にする」で渡せます）。
- **cirka**: 別 PC の端末で動く CUI のコーディングエージェント（[client/README.md](client/README.md)）。
- 推論モデル・画像モデル・動画モデルは、送信ボタンの横（cirka は `/model`・`/image-model`）で選べます。

```
他ホストのブラウザ ──> agent-chat-ui   http://<LAN IP>:3000
                         ▼
                   LangGraph       http://<LAN IP>:2024   graph: agent（画像）/ chat（チャット）/ video（動画）、/coder/turn・/models・/media/video
                         │ 画像タブ・動画タブ: ComfyUI HTTP API のみ
                         ▼
                   ComfyUI         http://127.0.0.1:8188  （tools/comfyui、ループバックのみ）
                         │ LM Connect ノード（OpenAI 互換 API）。タグ（動画は動きの説明）を作ったら LLM を unload して生成
                         │ 動画は umT5 → High → Low を 1 つずつ読み込んで外しながら作る
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

画像モデルのチェックポイント（例: `yiffInHell_yihVANTABLACK.safetensors`、Civitai など。一覧は `config/host_models.json`）は利用者が入手して `tools/comfyui/models/checkpoints` に置きます。動画タブのケモノ向け LoRA（Furry Enhancer の Wan 2.2 V3、I2V 用の High / Low）も同じく入手して `tools/comfyui/models/loras` に置きます。この PC に以前の ComfyUI のモデルフォルダがあれば、セットアップが自動で取り込みます。

### 2. Web ブラウザで開く

起動したら、Web ブラウザで次の URL を開きます。

| どこから | URL |
|---|---|
| この PC | http://127.0.0.1:3000 |
| 同じ LAN の別の PC・スマートフォン | `http://<この PC の LAN IP>:3000`（`start-all` が表示する URL） |

既定で、同じ LAN の他のホストからもアクセスできます（UI と LangGraph が `0.0.0.0` で待ち受けます。ComfyUI と LLM はこの PC の中だけ）。

### 3. 送る

画面上部のタブで **画像** / **チャット** / **動画** を選び、日本語で送ります（例: `夕焼けの海辺に立つ、白い毛並みの狼獣人の女性、和服`）。動画タブでは画像を 1 枚添付し、動かし方（例: `しっぽを振りながら、こちらを向いて笑う`）を書きます。

> このアプリに認証はありません。信頼できる LAN の中だけで使い、インターネットへ公開しないでください。
> 生成物と使用するモデルのライセンス・利用規約（成人向け表現を含む）は利用者の責任で確認してください。

## ドキュメント

| ドキュメント | 内容 |
|---|---|
| [docs/setup.md](docs/setup.md) | セットアップの詳細とオプション、macOS、モデルの置き方 |
| [docs/usage.md](docs/usage.md) | 使い方（モデルの選択、画像タブ、チャットタブ、動画タブ、cirka） |
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
| ディスク | 空き 95GB 以上（全モデル。動画タブの既定のモデルで約 23GB）。一部のモデルだけなら 20GB 程度から |
| ツール | Git、[uv](https://docs.astral.sh/uv/)、Node.js 20 以上（Mac は Homebrew と `hf` も）。cirka をビルドするなら Rust 1.85 以上、コード実行を使うなら Docker |

確認済み: ROG Ally X（Radeon 890M、24GB）と Windows 11、Apple M4（24GB）と macOS 15.5。動画タブは ROG Ally X で確認しました（3 秒の動画で 25〜33 分）。

## ライセンス

このリポジトリのコードは MIT または Apache-2.0（[LICENSE-MIT](LICENSE-MIT)、[LICENSE-APACHE](LICENSE-APACHE)）です。
`agent-chat-ui/` は upstream（[langchain-ai/agent-chat-ui](https://github.com/langchain-ai/agent-chat-ui)）の MIT ライセンスです。
セットアップが取得する ComfyUI、llama.cpp、カスタムノード、モデルなどは各配布元のライセンスに従います（[docs/third-party-licenses.md](docs/third-party-licenses.md)）。
