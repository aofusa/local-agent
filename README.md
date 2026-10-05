# local-agent: 日本語プロンプト → furry 静止画（LangGraph × ComfyUI × LM Studio）

LAN 内の別の PC やスマートフォンのブラウザから、日本語の指示（任意で参照画像 0〜4 枚。画風・ポーズ・キャラクターなどの役割付き）を送ると、
Windows 機の上で次の順に処理して静止画を返すローカルエージェントです。

1. [agent-chat-ui](https://github.com/langchain-ai/agent-chat-ui)（LangGraph 公式 UI）が入力を受け取る
2. LangGraph のグラフが画像の役割を決め、登録済みのワークフローテンプレートを選んで ComfyUI に投入する
3. ComfyUI のワークフローが LM Studio の LLM（Huihui Qwen3.8 27B Abliterated）で Danbooru / e621 タグを作る
4. LLM を LM Studio から unload してから、furry 系 SDXL チェックポイント（yiffInHell）で画像を生成する（参照画像は IP-Adapter / ControlNet、任意で LoRA）。
   `.env` の `COMFY_MODEL_FAMILY=flux` にすると、LLM が英語の説明文を作り、Flux 系の Chroma1-HD で生成する（「4. 使い方 › Chroma1-HD」）
5. 画像を ComfyUI の output とリポジトリの `outputs/` に保存し、同じ画像をチャットに表示する

画面上部のタブで **画像** と **チャット** を切り替えます（Grok の画面と同じ分け方）。画像タブは上の画像生成です。
チャットタブでは LM Studio の 27B と会話でき、「/search …」「…を調べて」と送ると Tor 経由で Web を検索して、出典付きで答えます。
検索は Grok のマルチエージェント検索を小さくしたもので、計画 → 並列検索 → reader によるページ読み → 批評 → 統合の順に進みます。
検索のモデル（Bonsai / Qwen heretic）は、PrismML の llama.cpp fork で検索のあいだだけ起動します（「4. 使い方 › チャットタブ」）。
チャットタブでは小説や文章（`/write`）とプログラム（`/code`）も書けます。入力欄の「自動 / 速い / 思考」で、すばやい回答と、深い検索（下位問いを埋めるまで追加検索）・アウトラインと推敲・承認後の Docker 実行・思考トークンを切り替えます。「自動」は内容から自動で選びます。
検索の回答は、主張ごとに出典の抜粋と突き合わせ、支持された主張だけで書き、最後に文ごとに監査して支持されない文を削除します。
「調べてから手順書にして」のように道具を順に使う依頼は、思考モード（または自動）で **自律モード** になり、27B が検索・文章・コード・画像タブ案内を結果を見ながら選び直します（最大 3 手。「4. 使い方 › 自律モード」）。

端末から使う CUI **cirka**（Rust 製、Windows / macOS / Linux）も同梱しています。作業ディレクトリで `cirka` を起動すると、この端末の 27B が次の一手を決め、ファイルの検索・読み取り・編集やビルド・テストのコマンドを、その PC の上で（編集とコマンドは確認のうえで）実行します。接続先のホストは設定で変えられます（「4. 使い方 › CUI（cirka）」）。

クラウド API は使いません。すべてローカルで動きます（検索の通信は Tor の出口だけを通ります）。

変更履歴: [CHANGELOG.md](CHANGELOG.md)

仕様: [AGENTS.md](AGENTS.md)（全体・UI・待受）、[docs/lmstudio-comfyui-workflow-design.md](docs/lmstudio-comfyui-workflow-design.md)（ComfyUI と LM Studio の連携）、[docs/multi-image-reference-work-instruction.md](docs/multi-image-reference-work-instruction.md)（複数参照画像。調査結果と設計との差分を含む）、[docs/chroma-hd-support-work-instruction.md](docs/chroma-hd-support-work-instruction.md)（Chroma1-HD。事前確認の結果と設計との差分を含む）、[docs/chat-search-tor-design-bonsai-tabs.md](docs/chat-search-tor-design-bonsai-tabs.md) と [docs/chat-search-tor-bonsai-work-instruction.md](docs/chat-search-tor-bonsai-work-instruction.md)（タブと Tor 経由検索。実装記録と実測を含む）、[docs/chat-deep-search-creative-sandbox.md](docs/chat-deep-search-creative-sandbox.md)（深い検索、文章、コードと Docker、速い / 思考 / 自動。実装記録と実測を含む）、[docs/claim-verification-design.md](docs/claim-verification-design.md)（主張単位の検証。末尾に実装記録）

```
他ホストのブラウザ ──> agent-chat-ui   http://<LAN IP>:3000
                         │ ブラウザから直接
                         ▼
                   LangGraph       http://<LAN IP>:2024   graph id: agent（画像タブ）/ chat（チャットタブ）
                         │ 画像タブ: ComfyUI HTTP API のみ
                         ▼
                   ComfyUI         http://127.0.0.1:8188  （ループバックのみ）
                         │ LM Connect ノード（OpenAI 互換 API）
                         ▼
                   LM Studio       http://127.0.0.1:1234/v1（ループバックのみ）

                   チャットタブ: LangGraph ──> LM Studio（会話、文章、コード、検索の計画。検索中は unload）
                                          ──> PrismML llama-server 127.0.0.1:18181〜（検索中だけ）
                                          ──> Tor SOCKS 127.0.0.1:9050 ──> 検索エンジンと結果のページ
                                          ──> Docker（思考モードで承認したコードだけ。ネットワークなし、待受なし）

別 PC の端末 ──> cirka（作業ディレクトリでファイル操作とコマンドを実行）
                   │ POST /coder/turn（推論だけ。ツールは実行しない）、/runs/stream（検索・画像）
                   ▼
                 LangGraph http://<LAN IP>:2024 ──> LM Studio（ループバック）
```

## 動作環境

| 項目 | 要件 |
|---|---|
| OS | Windows 10 / 11（スクリプトは Windows PowerShell 5.1 と PowerShell 7 の両方で動作確認） |
| メモリ | 24GB 級以上。27B の LLM と SDXL は同時に載らないため、ワークフローが順番に載せ替えます |
| GPU | ComfyUI と LM Studio が使える GPU（AMD は ROCm 版 ComfyUI と Vulkan 版 LM Studio で確認） |
| ディスク | 空き 46GB 以上（LLM 16.5GB + 再量子化版 13GB + チェックポイント 7GB + 参照画像用モデル約 6GB + 依存） |

確認済み構成: ROG Ally X（Ryzen AI Z2 Extreme / Radeon 890M / 共有メモリ 24GB）、Windows 11、
LM Studio 0.4.25（llama.cpp Vulkan ランタイム 2.51.0）、Comfy Desktop 1.1.6（ComfyUI 0.38、ROCm）。

## 1. 事前準備（人の手で行うこと）

スクリプトでは自動化していない準備です。

### 1-1. ツールをインストールする

PowerShell で実行します（winget の例）。

```powershell
winget install Git.Git
winget install astral-sh.uv
winget install OpenJS.NodeJS.LTS      # Node.js 20 以上
```

インストール後に PowerShell を開き直し、`git --version`、`uv --version`、`node --version` が通ることを確認してください。
Python は uv が自動で用意します。pnpm は `npx` 経由で使うので別途インストール不要です。

スクリプトの実行を許可します（現在のユーザーのみ）。

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### 1-2. LM Studio とモデル

1. [LM Studio](https://lmstudio.ai/) 0.4.25 以降をインストールし、一度起動する（`lms` CLI が `%USERPROFILE%\.lmstudio\bin` に入ります）。
2. LM Studio の検索で **Huihui-Qwen3.8-27B-abliterated** の GGUF（`huihui-ai` 公開）を探し、
   **Q4_K_S（約 15.6GB）と mmproj（約 0.9GB、Vision 用）** をダウンロードする。
   mmproj が無いと参照画像のタグ付け（Vision）が動きません。
3. LM Studio は起動したままにしておく（サーバの設定はセットアップスクリプトが行います）。

### 1-3. ComfyUI とチェックポイント

1. ComfyUI をインストールし、GPU で画像生成できることを確認する。次のどれでも構いません。
   - [Comfy Desktop](https://www.comfy.org/download)（推奨。セットアップが場所を自動で見つけます）
   - portable 版や git clone 版（セットアップ時に `-ComfyUIDir` で `main.py` のあるフォルダを指定します）
2. チェックポイント **Yiff in Hell – VANTABLACK**（`yiffInHell_yihVANTABLACK.safetensors`、Illustrious 系 SDXL）を
   [Civitai](https://civitai.com/models/1570986) 等から入手し、ComfyUI の `models\checkpoints` に置く。
   別のファイル名やモデルを使う場合は、セットアップ後に `.env` の `CKPT_NAME` を変更します。
3. Comfy Desktop を使う場合、セットアップ中は Comfy Desktop を終了しておく（起動引数を書き換えるため）。
4. （任意）Chroma1-HD を使う場合は、[lodestones/Chroma1-HD](https://huggingface.co/lodestones/Chroma1-HD) の拡散モデル
   （BF16 約 17.8GB。Civitai 配布名 `chroma_v10HD.safetensors`）を `models\diffusion_models` か `models\checkpoints` に置き、
   セットアップ後に `.\scripts\setup-comfyui-chroma.ps1` を実行する（T5 と VAE を取得し、拡散モデルを fp8 に変換。「4. 使い方 › Chroma1-HD」）。

### 1-4. ネットワーク

- この PC が接続しているネットワークを **プライベート** にする（設定 → ネットワークとインターネット → プロパティ）。
  ファイアウォールの許可は Private プロファイルにだけ追加します。
- ファイアウォール設定の 1 回だけ、管理者権限（UAC の確認）が必要です。

> **注意**: このアプリに認証はありません。信頼できる LAN の中だけで使い、インターネットへ公開しないでください。
> 生成物と使用するモデルのライセンス・利用規約（成人向け表現を含む）は利用者の責任で確認してください。

## 2. セットアップ

```powershell
git clone <このリポジトリの URL> local-agent
cd local-agent
.\scripts\setup.ps1 -OpenFirewall -ConfigureComfyDesktop
```

初回は LLM の再量子化に 20〜30 分かかります。`setup.ps1` は何度実行しても安全です（済んでいる手順は飛ばします）。
終わったら ComfyUI を再起動してください（カスタムノードを読み込むため）。

主なオプション:

| オプション | 説明 |
|---|---|
| `-OpenFirewall` | TCP 2024 / 3000 の受信を Private プロファイルで許可（UAC が出ます） |
| `-ConfigureComfyDesktop` | Comfy Desktop の起動引数を `--listen 127.0.0.1 --port 8188` にする |
| `-ComfyUIDir <path>` / `-ComfyPython <python.exe>` | Comfy Desktop 以外の ComfyUI を使う場合に指定 |
| `-Quant IQ3_M`（既定） / `none` | LLM を再量子化するか（「6. メモリと LLM の量子化」） |
| `-GpuOffload 0.45`（既定） | LLM の GPU オフロード比率（0〜1） |
| `-SkipLMStudio` / `-SkipComfyUI` | どちらかの設定を飛ばす |
| `-SkipSearch` | チャットタブの検索の準備（Tor、llama.cpp fork、検索モデル約 20GB、probe）を飛ばす |
| `-SkipProbe` | 検索モデルの検証（probe、数分）だけを飛ばす |

個別に実行することもできます: `.\scripts\setup-lmstudio.ps1`、`.\scripts\setup-comfyui.ps1`、`.\scripts\open-firewall.ps1`（管理者）。
チャットタブの検索だけを後から入れる場合:

```powershell
.\scripts\setup-tor.ps1             # Tor Expert Bundle（SHA-256 確認）を tools\tor へ。TOR_EXE を .env へ
.\scripts\setup-llamacpp.ps1        # PrismML llama.cpp fork の Vulkan 版を tools\llama-prism へ（SHA-256 照合。-FromSource でビルド）
.\scripts\setup-search-models.ps1   # 検索モデル 8 つ（約 20GB、再開可、SHA-256 照合）を tools\models へ（-Verify で取得済みも再照合）
.\scripts\probe-bonsai.ps1          # 各モデルを 1 回ずつ起動して検証し、tools\bonsai\rank.json に順位を書く
```

チャットタブのコード実行（思考モード）を使う場合は、Docker Desktop を入れてから次を実行します。
Docker Desktop が止まっていれば起動し、`python:3.12-slim` を取得して、ネットワークなし・読み取り専用・非 root でコンテナが動くことを確かめます。
Docker が無くても、チャットタブはコードを書いてファイルに残し、実行しなかった理由を返します。

```powershell
.\scripts\setup-sandbox.ps1         # -Rust で rust:1.88-slim も取得
```

各スクリプトの詳細は `Get-Help .\scripts\setup-lmstudio.ps1 -Detailed` で表示できます。

### セットアップが行うこと

**開発環境**

- `.env.example` から `.env` を作る（端末固有の値はここにだけ書かれ、git には入りません）
- `uv sync`（LangGraph 用の Python 3.12 環境 `.venv`）
- `agent-chat-ui` で `pnpm install`

**LM Studio**（`scripts\setup-lmstudio.ps1`）

| 設定 | 内容 | 変更されるファイル |
|---|---|---|
| ランタイム | `lms runtime update --all`（Qwen3.8 の MTP 層付き GGUF は新しい llama.cpp が必要） | LM Studio 管理 |
| サーバ | `127.0.0.1:1234` で待受（LAN に出さない）、JIT ロード有効 | `%USERPROFILE%\.lmstudio\.internal\http-server-config.json` |
| JIT TTL | アイドル 300 秒で自動 unload（ワークフローの eject の保険） | `%USERPROFILE%\.lmstudio\settings.json` |
| 再量子化 | `-Quant IQ3_M` のとき、llama.cpp の `llama-quantize`（リポジトリの `tools\` にダウンロード）で `<元フォルダ>-IQ3_M-GGUF` を作成。mmproj はハードリンク。元ファイルは残す | LM Studio のモデルフォルダ |
| JIT 既定値 | context 4096、GPU offload 0.45、flash attention、並列 1、**thinking 無効**、temperature 0.4 | `...\.internal\user-concrete-model-default-config\<モデル>.json` |
| モデルキー | LM Studio が付けたキー（例 `huihui-qwen3.8-27b-abliterated@iq3_m`）を `.env` の `LMSTUDIO_MODEL` に保存し、違えば `workflows\` を再生成 | `.env`、`workflows\*.json` |

既存の設定ファイルは初回に `*.local-agent.bak` として控えます。

**ComfyUI**（`scripts\setup-comfyui.ps1`）

| 設定 | 内容 |
|---|---|
| 場所の特定 | `-ComfyUIDir` → `.env` → Comfy Desktop の `%APPDATA%\Comfy Desktop\installations.json` の順。結果を `.env` の `COMFYUI_*` に保存 |
| LM_Connect | [eedali/LM_Connect](https://github.com/eedali/LM_Connect) を `custom_nodes` に clone（検証済みコミットに固定）。依存は requests / Pillow / numpy のみ。**llama-cpp-python は入れません**（ComfyUI 内で GGUF を動かさない） |
| furry_ja | `custom_nodes\furry_ja` → このリポジトリの `comfyui_nodes\furry_ja` へのジャンクション |
| Comfy Desktop | `-ConfigureComfyDesktop` のとき起動引数を `--listen 127.0.0.1 --port 8188 ...` に（`installations.json` を書き換え、バックアップあり） |
| チェックポイント | `CKPT_NAME` のファイルがあるか確認 |

**参照画像用のノードとモデル**（`scripts\setup-comfyui-refs.ps1`。`setup.ps1 -SkipReferenceModels` で省略可）

テキストだけの生成と、修正する元画像 1 枚の img2img はこれが無くても動きます。画風・ポーズ・キャラクターの参照に使います。

| 対象 | 内容 |
|---|---|
| [cubiq/ComfyUI_IPAdapter_plus](https://github.com/cubiq/ComfyUI_IPAdapter_plus) | キャラクター・画風の参照（検証済みコミットに固定） |
| [Fannovel16/comfyui_controlnet_aux](https://github.com/Fannovel16/comfyui_controlnet_aux) | ポーズ（DWPose）・奥行き（Depth Anything V2）の抽出。依存は torch / numpy を変えない軽いものだけ入れる（onnxruntime-gpu と mediapipe は入れない） |
| `models\controlnet\controlnet-union-sdxl-1.0-promax.safetensors` | xinsir の SDXL ControlNet Union（openpose / depth / canny を 1 本で） |
| `models\ipadapter\ip-adapter-plus_sdxl_vit-h.safetensors`、`models\clip_vision\CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors` | IP-Adapter Plus（SDXL）と画像エンコーダ |
| `custom_nodes\comfyui_controlnet_aux\ckpts\...` | DWPose（ONNX、CPU で実行）と Depth Anything V2 Small |

実行時にモデルを自動ダウンロードするノードはありません（すべてこのスクリプトで事前に取得します）。

**チャットタブの検索**（`setup.ps1 -SkipSearch` で省略可。画像タブはこれが無くても動きます）

| スクリプト | 内容 | 置き場（git 管理外） |
|---|---|---|
| `setup-tor.ps1` | Tor Expert Bundle の最新安定版を取得し、配布元の `sha256sums-signed-build.txt` で SHA-256 を照合する。`TOR_EXE` を `.env` へ保存する。設定は `tools\tor\torrc`（SOCKS は `127.0.0.1:9050` のみ） | `tools\tor\bin`、データは `tools\tor\data` |
| `setup-llamacpp.ps1` | PrismML llama.cpp fork の Windows Vulkan 版（`config\search_models.json` に固定した版）を取得し、SHA-256 を照合する。`--list-devices` で Vulkan デバイスを確認し、`BONSAI_LLAMA_SERVER` を保存する。`-FromSource` なら `prism` ブランチを Vulkan でビルドする（Visual Studio の C++、CMake、Ninja、Vulkan SDK が必要） | `tools\llama-prism` |
| `setup-search-models.ps1` | 検索モデル 8 つ（約 20GB）を Hugging Face から取得し、カタログの SHA-256 と照合する（中断しても再開できる）。`BONSAI_MODELS_DIR` を保存する | `tools\models` |
| `probe-bonsai.ps1` | 各モデルを 1 回ずつ起動し、タスクごとに短いテストで検証する。起動時間・メモリ・生成速度・合否を記録する | `tools\bonsai\rank.json` |

### 手動で設定する場合

スクリプトを使わない場合は、GUI で次を設定すれば同じ状態になります。

- LM Studio → Developer → Server Settings: ポート 1234、**Serve on Local Network をオフ**、Just-in-Time Model Loading をオン、
  Auto unload unused JIT loaded models をオン（TTL 300 秒）。ランタイム（llama.cpp）を最新に更新。
- LM Studio → My Models → 使うモデルの既定値: Context Length 4096、GPU Offload 約 45%、Flash Attention オン、
  Max Concurrent Predictions 1、Thinking（Reasoning）オフ、Temperature 0.4。
- 参照画像を使う場合は、上の表のノードを `custom_nodes` に clone し、モデルを同じパスに置く。
- ComfyUI: `custom_nodes` に LM_Connect を clone し、ComfyUI の Python で `pip install requests Pillow numpy`、
  `comfyui_nodes\furry_ja` を `custom_nodes\furry_ja` にコピーまたはリンク。起動引数は `--listen 127.0.0.1 --port 8188`。
- LM Studio のモデルキーを `$env:LMSTUDIO_MODEL` に入れて `uv run python scripts\build_workflows.py` を実行し、ワークフローを再生成。

## 3. 起動

LM Studio を起動したうえで:

```powershell
.\scripts\start-all.ps1      # Tor（裏で）/ ComfyUI / LangGraph / agent-chat-ui を起動（起動済みのものは飛ばす）
.\scripts\doctor.ps1         # 設定と待受を確認（NG があれば終了コード 1。チャットタブの検索の項目は WARN 扱い）
```

表示される `http://<LAN IP>:3000` を他ホストのブラウザで開きます。
Deployment URL / Assistant ID の入力画面は出ません（ビルド時に設定済み）。
初回の UI 起動時は agent-chat-ui のビルドに数分かかります。

個別に起動する場合（それぞれ別の PowerShell で）:

| 順 | コマンド | 待受 |
|---|---|---|
| 1 | LM Studio を起動 | `127.0.0.1:1234` |
| 2 | `.\scripts\start-comfyui.ps1`（または Comfy Desktop でインスタンスを起動。どちらも `--cache-none` 付き） | `127.0.0.1:8188` |
| 3 | `.\scripts\start-langgraph.ps1`（`langgraph dev --host 0.0.0.0 --port 2024`） | `0.0.0.0:2024` |
| 4 | `.\scripts\start-ui.ps1`（`-HostAddress <IP>` で接続先を明示可） | `0.0.0.0:3000` |
| 5 | `.\scripts\start-tor.ps1`（チャットタブの検索用。止めるときは `-Stop`） | `127.0.0.1:9050` |

- 検索モデルの llama-server は常駐させません。チャットタブが検索のたびに `127.0.0.1` で起動し（起動ごとにランダムな API キー付き）、終わったら止めます。Tor が止まっていれば、チャットタブが自動で起動します（`TOR_AUTOSTART=1`）。

- agent-chat-ui は `NEXT_PUBLIC_API_URL=http://<LAN IP>:2024` を **ビルド時に** 埋め込みます。`localhost` にすると他ホストのブラウザは自分自身へ接続してしまうためです。
  LAN IP が変わったら `start-ui.ps1` が自動で再ビルドします。
- Comfy Desktop は再起動するとダッシュボードに戻り、サーバを自動では起動しません。`start-comfyui.ps1` は Desktop と同じインストール・モデル・入出力先でサーバだけを起動します（Desktop は閉じておいてください）。
- ComfyUI は `--cache-none`（ノード出力をキャッシュしない）で起動します。ComfyUI 0.38 と IP-Adapter の組み合わせでは、
  キャッシュがあると同じプロセスでの 2 回目以降の生成が単色・ノイズの画像になりました（下の「調整の記録」）。
- モデルの事前ロードは不要です。各生成の最初に LLM が JIT ロードされ、タグ生成後に unload されます。
- 止めるときは各ウィンドウで Ctrl+C を押します。

## 4. 使い方

### テキストだけ

日本語で描きたい内容を送ります。Empty Latent、denoise 1.0、832×1216、steps 28、cfg 5.5、euler_ancestral / normal。

### 参照画像（1 メッセージ 4 枚まで）

画像を添付すると、各画像の下に **役割** と任意の **強度** の欄が出ます。役割を選ばない（「自動」）ときは、指示の文から決めます。

| 役割 | 使い方 | ComfyUI での行き先 | 強度（既定 / 範囲） |
|---|---|---|---|
| 自動 | 指示の文から推定 | — | — |
| キャラクター | この子の見た目（種族・毛色・模様・服）を使う | IP-Adapter Plus（weight = 強度 × 0.5）+ 役割専用の Vision タグ | 0.85 / 0.4〜1.2 |
| ポーズ・構図 | この絵のポーズ・カメラを使う。見た目は持ち込まない | DWPose（または深度・線画）→ ControlNet Union + 骨格だけの Vision タグ | 0.80 / 0.3〜1.2 |
| 画風 | この絵の塗り・タッチ・色を使う。主題は持ち込まない | IP-Adapter（style transfer）+ 画風だけの Vision タグ | 0.55 / 0.2〜1.0 |
| 修正する元画像 | この画像を直す（img2img） | VAE Encode + Vision タグ | denoise 0.45（ポーズ・キャラ変更を伴うと 0.65）/ 0.15〜0.85 |
| マスク | 修正する元画像のうち白い部分だけ変える（inpaint） | SetLatentNoiseMask | denoise 0.75 |

役割の組み合わせから、使うワークフローテンプレートが一意に決まります（例: キャラクター + ポーズ → `character_pose_t2i`、
元画像 + 画風 → `style_i2i`、元画像 + マスク → `inpaint_basic`）。テンプレートは `workflows/sdxl/` の 24 本で、LLM がノードを作ることはありません。

指示の例:

| 添付 | 指示 | 結果 |
|---|---|---|
| 1 枚 | `この絵の雰囲気で、夜の神戸港に立つキャラクター` | 画風（`style_t2i`） |
| 1 枚 | `このポーズで、白衣のケモノキャラ。背景はシンプル` | ポーズ（`pose_t2i`） |
| 1 枚 | `この子を和服で` / `背景を夜にして` | 修正する元画像（`i2i_basic`、従来の動作） |
| 2 枚 | `このキャラをこのポーズにして` | 1 枚目キャラクター、2 枚目ポーズ |
| 2 枚 | `構図は維持して画風だけ寄せて` | 1 枚目元画像、2 枚目画風（`style_i2i`） |
| 3 枚 | `AのキャラをBのポーズ、Cの画風で` | 添付順に A/B/C（`1枚目` `画像2` でも可） |
| 2 枚 | `いい感じに合わせて` | 役割を決められないので、生成前に確認カードを出す |

- 役割を決められないとき（曖昧な指示、画像より役割の言葉が少ない、1 枚に画風とキャラクターの両方、元画像の無いマスク）は、
  生成前に確認カードが出ます。**承認**（提案どおり）、**編集**（各画像の値を `character` / `pose` / `style` / `base` / `mask` に書き換え）、**却下**（中止）から選びます。
- 同じ役割が複数あるときは先頭の画像だけを使い、残りは無視したと返します。
- 文中の指定: `seed 1234`、`1024x768` / `横長` / `正方形`、`denoise 0.6`、`線画優先`（ポーズを線画で取る）、`奥行き優先`（深度で取る）、
  `画風を強めに` / `控えめに`。範囲外の値は範囲内へ丸め、その旨を返します。
- 生成前に、テンプレート・各画像の役割と強度・サイズ・seed・LoRA の要約が出ます。完成時にも同じ要約と seed を返すので、同じ条件で再生成できます。
- ポーズ参照を使ったときは、抽出した骨格（または深度・線画）の画像も 2 枚目として返します。抽出が失敗していないか確認できます。
- 続けて直すときは、画像を添付せずに `さっきの画像を夜にして` のように送ると、直前の生成画像を元画像として使います。

### LoRA

`.env` の `LORAS` に書いた LoRA を、すべての生成でチェックポイントの直後に順に適用します。空なら使いません。

```
LORAS=KemonoStyleAV1.safetensors:0.8, CiviFur-30:0.6:0.5
```

`名前[:モデル強度[:CLIP 強度]]` をカンマ区切りで並べます（拡張子と大文字小文字は省略・無視できます。強度の既定は 1.0、範囲 -2〜2）。
ComfyUI の `models\loras` に無い名前があると投入前にエラーを返します。変更後は LangGraph を再起動してください。トリガーワードが必要な LoRA は指示に含めてください。

### Chroma1-HD（モデルの切り替え）

既定は yiffInHell（SDXL、Danbooru タグ）です。Chroma1-HD（Flux.1-schnell 由来、8.9B、Apache-2.0）は `.env` で切り替えます。

```
COMFY_MODEL_FAMILY=flux              # 空または sdxl なら従来の SDXL
CKPT_NAME=chroma_v10HD.safetensors
```

変更後は LangGraph を再起動します（`start-langgraph.ps1`）。チャットの文面でモデルが変わることはありません。元に戻すときは `COMFY_MODEL_FAMILY=sdxl`、`CKPT_NAME=yiffInHell_yihVANTABLACK.safetensors` にします。

Chroma 経路の違い:

| 項目 | SDXL（yiffInHell） | Chroma1-HD |
|---|---|---|
| LLM の出力 | Danbooru / e621 タグ列（`prompts/system_furry_tags.txt`） | 1〜3 文の英語の説明文（`prompts/system_chroma_prose.txt`）。重み構文・`masterpiece` などは `split` が取り除く |
| ローダー | `ckpt` = チェックポイント | `ckpt` = 拡散モデル（fp8 で読み込み）+ T5-XXL fp8（CLIPLoader type chroma）+ Flux VAE `ae.safetensors`。どちらも LLM の eject の後 |
| サンプラー | 832×1216、steps 28、cfg 5.5、euler_ancestral / normal | 1024×1024（`縦長` 832×1216 / `横長` 1216×832、上限約 1MP）、steps 28、cfg 3.5、euler / beta、ModelSamplingAuraFlow shift 1.0 |
| negative | 品質タグ | 短い英語（空にはしない） |
| 参照画像 | 4 種の役割（上記） | **修正する元画像 1 枚の img2img だけ**（denoise 0.45）。ポーズ・画風・キャラクター・マスクの画像は生成せず理由を返す（Flux 用 ControlNet / IP-Adapter は Chroma で未検証のため） |
| LoRA | `LORAS` | `CHROMA_LORAS`（SDXL の LoRA は Chroma に合わないため別） |

モデルファイルの既定値（`workflows/maps/flux.json`）と、`.env` での差し替え:

| 部品 | 既定 | 置き場所 | `.env` |
|---|---|---|---|
| 拡散モデル | `chroma_v10HD.safetensors`（BF16） | `models\diffusion_models` または `models\checkpoints` | `CKPT_NAME` / `CHROMA_UNET_NAME`（`CKPT_NAME` が空のとき） |
| fp8 変換済み | `chroma_v10HD_fp8_e4m3fn.safetensors`（約 8.3GB） | `models\diffusion_models`（`setup-comfyui-chroma.ps1` が作る） | 指定不要。あれば `ckpt` が自動で使う |
| 読み込み精度 | `fp8_e4m3fn` | — | `CHROMA_WEIGHT_DTYPE`（`default` で BF16 のまま。32GB 以上の RAM 向け） |
| テキストエンコーダ | `t5xxl_fp8_e4m3fn.safetensors` | `models\text_encoders` | `CHROMA_TEXT_ENCODER` |
| VAE | `ae.safetensors`（Flux VAE） | `models\vae` | `CHROMA_VAE` |

速度（確認済み構成 Radeon 890M、fp8、拡散モデルの一部をオフロード）: 1024×1024 で 1 ステップ約 64 秒（28 ステップで約 30 分）、
768×768 で約 26 秒、512×512 で約 12 秒。遅い GPU では `.env` で上限を下げます。

```
CHROMA_MAX_PIXELS=589824     # 768x768 相当。縦長・横長も同じ画素数に縮める（既定 1048576 = 1024x1024）
CHROMA_STEPS=28              # 既定 28
COMFYUI_TIMEOUT_S=1200       # 画像生成がタイムアウトより長くなる場合
```

GPU メモリ別の目安（作業指示書 §2.2）: 24GB 以上は BF16（`CHROMA_WEIGHT_DTYPE=default`）、16GB / 12GB / UMA は fp8（既定）か GGUF の Q8_0〜Q5_K_M
（GGUF は ComfyUI-GGUF が必要で、このリポジトリのワークフローは未対応）。8GB 以下は対象外です。

BF16 の 17.8GB を読み込み時に fp8 へ落とすと、変換前の重み全体がいったん RAM に載ります。24GB 機ではこれで ComfyUI が落ちたため、
`setup-comfyui-chroma.ps1` が一度だけ `<名前>_fp8_e4m3fn.safetensors` を作り（`scripts\convert_chroma_fp8.py`、1 テンソルずつ書くので RAM は数百 MB、約 2 分）、
`ckpt` は精度が `fp8_e4m3fn` でその変換済みファイルがあればそれを読みます。`-NoConvert` で変換を省きます。

ファイルが無いときは不足しているファイル名を返し、SDXL へ自動で切り替えません。

### 進捗・中断・保存

- 進捗はチャットの 1 つのメッセージが更新されます（受付 → 実行計画 → 投入 → タグ生成完了（positive / negative を表示）→ 画像）。
- タグ生成と画像生成はそれぞれ 10 分（`COMFYUI_TIMEOUT_S`）で打ち切ります。タイムアウトしたときは prompt_id を返すので、
  完了後に `再取得 <prompt_id>` と送ると結果を受け取れます。
- 生成中に UI の停止ボタンを押すと、ComfyUI の該当 prompt も中断します。
- 複数送っても、ComfyUI のキューが空くまで次は投入しません。
- 失敗したときは、どの段階（入力・ワークフロー選択・役割推定・アップロード・ワークフロー注入・キュー投入・生成・タイムアウト）で止まったかを返します。
- 生成画像は ComfyUI の `output\furry_ja\` と、リポジトリの `outputs\`（同名の `.json` に prompt_id / seed / テンプレート / 役割 / 強度 / LoRA / タグ）に保存されます。
  参照画像は ComfyUI の `input\furry_ja\` に sha256 のファイル名で置き、同じ画像は再送しません。

確認済み構成での所要時間の目安:

| 入力 | 所要時間 |
|---|---|
| テキストのみ | 約 3.5〜7 分 |
| 修正する元画像 1 枚 | 約 6〜7.5 分 |
| 画風 または ポーズ 1 枚 | 約 6.5〜7 分 |
| キャラクター + ポーズ、元画像 + 画風 | 約 8〜9 分 |
| キャラクター + ポーズ + 画風 | 約 12.5 分 |

### チャットタブ（会話、Tor 経由の検索、文章、コード）

画面上部の「チャット」タブに切り替えて送ります。スレッドの履歴はタブごとに分かれます。画像の添付は受け付けず、絵を描く依頼（「〜を描いて」「この場面を画像にして」）は画像タブへ誘導します（書いた文章があれば、最後の場面の描写を貼り付け用に添えます）。

- **会話**: LM Studio の Qwen3.8 27B が答えます（直近 12 往復を文脈にします）。
- **検索**: 行頭の `/search`、または「検索」「調べて」「ググ」「最新」「ニュース」を含む文、URL を含む文（その URL を読みます）で検索します。
- **文章**: 行頭の `/write`、または「小説」「物語」「設定を作って」「推敲」「続きを書いて」「記事の下書き」などを含む文。書くのは LM Studio の 27B です。
- **コード**: 行頭の `/code`、または「コードを書いて」「実装して」「実行して」「テストして」「スクリプトを作って」などを含む文。
- どれとも決まらない文（「〜はいつ？」「〜をまとめて」など）は、Qwen3-1.7B が会話 / 検索 / 文章 / コードのどれかを選びます。失敗したら会話にします（画像タブへは誘導しません）。

#### 応答モード（自動 / 速い / 思考）

入力欄の下の「自動 / 速い / 思考」で切り替えます（既定は「自動」。ブラウザごとに覚えます）。モデルは替えず、予算と思考トークンを替えます。

| | 速い | 思考 |
|---|---|---|
| 検索 | 検索意図 1 本、1 ラウンド、批評なし | 目的と下位問い（2〜5）を立て、未回答の下位問いを埋めるまで最大 4 ラウンド |
| 文章 | 27B が一度で書く | アウトライン → 本文 → 推敲（差分だけ直す）。章立ての依頼は章ごとに続けるか確認 |
| コード | ファイルを書くだけ（実行しない） | 承認後に Docker コンテナで実行し、失敗したら 1 回だけ直して再実行（承認し直し） |
| 思考トークン | なし | あり。回答とは別の折りたたみ（「思考」、既定は閉じる）に出す |

- **自動**は Grok の自動モードと同じく、送った内容から選びます。比較・分析・理由・複数の条件を含む調査、章立てや構成が要る文章、実行やテストを頼んだコード、計算や推論の要る相談は「思考」、単一の事実確認、短い文章、コードの生成だけ、雑談は「速い」です。「じっくり」「詳しく」/「手短に」「ざっくり」と書けばそれに従います。判断に迷う文はルータ（Qwen3-1.7B）の判定も使います。回答の下に「自動 → 思考（『違い』を含む調査）」のように、選んだモードと理由が出ます。
- モードを付けずに API から送った実行は「速い」です。画像タブはモードを使いません。
- 思考トークンは LM Studio の 27B だけが出します（`reasoning_effort`。LM Studio は `chat_template_kwargs` を無視するため）。JSON を返す段（計画・批評・推敲・アウトライン）は思考なしで呼びます。
- タイムアウト: 1 回のモデル呼び出しは 20 分（`CHAT_TIMEOUT_S=1200`）、思考モードの検索全体も 20 分（`SEARCH_WALL_CLOCK_S=1200`）。長い呼び出しのあいだも共有ロックは延長されます。
- この端末での速さ（実測）: LM Studio の 27B（IQ3_M、context 4096）の生成は約 0.9 トークン/秒です（Docker の停止や ComfyUI の `/free` では変わりませんでした）。そのため 1 回の呼び出しで出せるのは 20 分で約 1000 トークンまでで、チャットタブは回答と思考の量を、context（4096）と実測の速さの両方に収まるように決めます（`LMSTUDIO_CONTEXT`、`LMSTUDIO_TOKENS_PER_S`。速さは応答のたびに測り直します）。答えの分が残らないときは思考を使いません。思考が予算を使い切って答えが空なら、思考なしでもう一度だけ答えさせます。
  - 目安: 速いモードの会話は 27B のロード込みで約 3 分、思考モードのコード（短いスクリプト）は生成に約 12 分、文章は 1 回で 1000〜1500 字程度です。長い文章は「章立て」で分けてください。
- Docker Desktop は普段は止めておけます。止まっていると、確認カードに「Docker Desktop: 停止中」と出て、承認後に起動し（30 秒〜数分）、実行が終わったら止めます。Docker の VM は約 1.5GB を使い、この端末では 27B（ロード中の空き 0.4GB）や ComfyUI と取り合うためです。

#### 自律モード（道具を順に使う依頼）

「調べてから要点を教えて」「最新の〇〇を調べて記事にして」「スクリプトを書いて、動くか試して直して」のように、ひとつの道具の結果を見て次の道具が決まる依頼（検索・文章・コードのうち 2 つ以上を含む、または「〜してから」「根拠を確認して」「結果をもとに」などの接続がある文）は、思考モードでは自律モードで処理します。「自動」でもこの形の依頼は思考を選びます。

1. 27B が依頼とこれまでの結果の要約（各 500 字まで）を読み、次の道具（検索 / 文章 / コード / 画像）とその依頼文、または最終回答を JSON で返します。
2. 道具は今までの検索・執筆・コードの流れそのものです（章の確認カードやコンテナ実行の承認カードもそのまま出ます）。終わると要約だけが制御に戻ります（検索のページ本文やカード全件、コードの出力全文は戻しません）。
3. 道具の結果はそれぞれのメッセージとして残り、最後に自律モードの回答が付きます。文章を書いたあとは本文を繰り返さず、書いた旨だけを添えます。
4. 上限: 道具は 3 回まで（`CONTROLLER_MAX_STEPS`、最大 4）、全体の時間は `SEARCH_WALL_CLOCK_S`（20 分）以内（`CONTROLLER_WALL_CLOCK_S`）。同じ道具を同じ依頼文で二度は呼びません。上限や重複で止まったときは、それまでの結果で答え、止まった理由を書きます。この端末では思考モードの検索 1 回で 20 分近くかかるため、検索のあとは時間の上限で終わることが多く、その場合は検索の答え（出典付き）がそのまま結果になります。
5. 画像は生成せず、画像タブへ案内して終わります。
6. 回答の下の「自律の手順」を開くと、選んだ道具、その理由、依頼文、結果の要約が見られます。

行頭の `/search` `/write` `/code` `/chat`、画像の添付、「速い」モードの送信は、今までどおり 1 つの道具で処理します（自律モードに入りません）。27B の判断 1 回は短い JSON（最大 400 トークン）ですが、この端末では 1〜5 分かかります。

#### 深い検索（思考モード）

1. 27B が「目的」（時期・地域・比較対象・その会話で示された条件、何が分かれば判断が変わるか）と下位問い 2〜5 個、最初の検索意図を作ります。
2. 検索 → フィルタ → reader（下と同じ）。前のラウンドで見つけた URL は読み直しません。
3. 批評役（代理リーダー、または 27B が載っていれば 27B）が下位問いごとに「回答済み / 一部 / 未回答」を判定します。回答済みには根拠カードの番号が要ります。カード同士の食い違いも挙げ、未回答と一部の下位問いだけを次の検索にします（新しい話題は広げません。食い違いには一次情報を探すクエリ）。
4. 未回答が残り、前のラウンドで新しいカードが増え、ラウンド（4）・ページ（12）・時間（20 分）が残っていれば次のラウンドへ。そうでなければ統合へ進みます。
5. 回答には [n] 付きの本文に加えて、未解決の下位問い（推測で埋めません）、食い違い（両方のカード番号）、停止理由（十分に答えられた / 新しい根拠が増えなくなった / 予算の上限 / 検索結果なし）が付きます。
6. 「Tor 経由の深い検索」を開くと、ラウンドごとの検索、下位問いと状態、採用したカード（引用確認済み）、使わなかったもの（関連が低い、既に読んだ、引用を本文で確認できない）、読んだページ数と経過秒が見られます。

reader を同時に増やすのではなく、ラウンドを増やします（この端末のメモリでは幅を増やせません）。27B と reader は今までどおり同時に載せません。

#### 文章（`/write`）

- 書いた本文はスレッドの `artifact` に残ります。「続きを書いて」は会話履歴ではなく、この本文の末尾（約 2000 字）から続けます。
- 思考モードでは、まずブリーフ（ジャンル・視点・長さ・入れる / 入れない要素・言語）とアウトラインを作って表示し、本文を書き、推敲します。推敲は「本文中の文字列 → 直した文字列」の置換だけで、全文を書き直しません。回答の下の「執筆の手順」にブリーフ、アウトライン、推敲メモ、事実が分からない点が出ます。
- 「長編」「章立て」「連載」「全 N 章」の依頼は、章ごとに本文を出して「続けるか」を確認します（画像タブの役割確認と同じカード）。承認で次の章、編集（instruction に指示）で指示付きで次の章、却下でそこで止めます。止めても「続きを書いて」で次の章から再開できます。確認待ちのあいだは共有ロックを放すので、画像タブを使えます。
- 実在の事件・史実・資料に基づく創作（「調べてから小説にして」など）は、思考モードなら先に検索し、その結果を参考資料として書きます。速いモードでは検索せずに書き、その旨を添えます。事実が要る箇所は創作で埋めません。
- 文章を書くのは常に LM Studio の 27B です。検索用の小さいモデルや、画像用のプロンプト（タグ生成）は使いません。

#### コード（`/code`）と Docker サンドボックス

- 27B がファイルとコマンドを書き、`artifacts\code\<run_id>\` に保存します（git 管理外）。速いモードはここまでで、ファイルと意図を返します。
- 思考モードでは、実行の前に必ず確認カードを出します（ファイルとバイト数、コマンド、イメージ、ネットワークの有無、時間とメモリの上限）。承認するまで実行しません。編集でコマンド・ファイル・ネットワークを変えると、もう一度カードが出ます。却下するとファイルだけ残します。
- コンテナの制約（変えられません）: `python:3.12-slim`（Debian slim。Rust と書いた依頼だけ `rust:1.88-slim`）、`--network none`、`--read-only`（書けるのは `/work` と 64MB の `/tmp` だけ）、`--memory 2g --cpus 2 --pids-limit 256`、`--cap-drop ALL`、`no-new-privileges`、非 root（`10001:10001`）、マウントはその実行の `artifacts\code\<run_id>` → `/work` だけ、60 秒で `docker kill`、出力は stdout / stderr とも末尾 8KB、`--pull never`。docker.sock、ホームフォルダ、リポジトリ、`.env`、SSH 鍵は渡しません。コマンドは argv の配列だけで、シェル（`sh -c`、`;`、`&&`、`|`、リダイレクト）は使えません。
- 依存（pip / cargo）は、その依頼で「pip install して」「依存を入れて」などと明示したときだけ、承認カードに「network: setup」と出ます。承認すると、依存の取得（`requirements.txt` / `Cargo.toml`）だけをネットワーク付きの別コンテナで行い、プログラム本体はネットワークなしで実行します。
- 失敗したら 27B が直し、承認し直してもう一度だけ実行します（合計 2 回まで）。
- コンテナは共有ロック（画像とチャット）を握りません。別のロックで 1 つずつ実行し、画像タブの生成中は終わるまで待ちます。
- Docker Desktop が無い、または止まっているときは、ファイルを書いたうえで理由を返します。`scripts\setup-sandbox.ps1` が Docker Desktop を起動し、イメージを取得します（`-Rust` で Rust 用も）。

検索の流れ（Grok のマルチエージェント検索の縮小版）:

| 段 | 担当 | 内容 |
|---|---|---|
| 計画 | LM Studio の Qwen3.8 27B | 質問を 1〜3 本の検索意図（web / news / 指定 URL）に分ける。reader と同時に載らなければ、ここで unload する |
| 検索 | Python（モデルなし） | 意図ごとに並列で DuckDuckGo を Tor 経由で検索する |
| フィルタ | Bonsai-4B | タイトルと抜粋から、関係の無い結果を落とす |
| 読む | Ternary-Bonsai-8B × 最大 3 体 | 結果から開くページを選び（ツール呼び出し）、質問に関係する事実・数値・日付・反証だけを URL ごとのカードにする |
| 批評（思考モードのみ） | Ternary-Bonsai-2-27B abliterated（代理） | 下位問いごとに充足を判定し、未回答のものだけを次のラウンドで検索させる（上の「深い検索」） |
| 統合 | 同上 | カードだけを根拠に、[n] 付きの日本語の回答を書く。参照 URL の一覧はシステムが付ける |

- 回答の下の「Tor 経由の検索」を開くと、検索語、ヒットしたページ（● は reader が開いたページ）、役割ごとのモデルが見られます。reader 同士の下書きは返しません。
- モデルは `config/search_models.json` の順と、`probe-bonsai.ps1` の検証結果（`tools/bonsai/rank.json`）、空きメモリから自動で選びます。ファイルが無いモデルや検証に落ちたモデルは使いません。
  この端末（ROG Xbox Ally X）では、27B（IQ3_M）のロード中に空きが 1GB を切るため、計画のあと 27B を unload し、批評と統合は Bonsai 2 27B abliterated が代理で行います。
- 速いモードの検索は意図 1 本・1 ラウンドで数分、思考モードは 3 ラウンドで約 16 分でした（実測。27B のロード約 2 分、reader 1 体 1 分前後、代理 27B の生成 8〜9 tok/s）。`.env` の `SEARCH_PLANNER=local` にすると、計画も代理 27B が行い、LM Studio のロードを省けます。
- 画像タブとチャットタブは同時に動きません。片方の実行中にもう片方へ送ると、実行中のタブ名を示して断ります。検索の後始末（llama-server の停止と LM Studio の unload）が済むまで、画像タブは待ちます。
- 結果が 0 件のとき（Tor の出口が拒否されたときなど）は、統合モデルを起動せずにその旨を返します。
- `doctor.ps1` は、検索について次を確認します。いずれも WARN 扱いで、無くても画像タブは使えます。
  - Tor がループバックだけで待ち受けていて、`socks5h://` を使っていること
  - fork と Vulkan デバイス
  - モデルファイルの数
  - 検証結果の有無
  - 停止し損ねた llama-server が残っていないこと

#### 主張の突き合わせ（検索）

検索の回答は、カードに書いてあることだけで書くように、統合の前後で主張を 1 件ずつ出典と照らします（設計: `docs/claim-verification-design.md`）。

```text
… → 批評（思考モード） → 主張の抽出 → 主張の判定 → 統合 → 監査 → 削除
```

1. **抽出**: 代理リーダー（批評と同じ Ternary-Bonsai-2-27B abliterated のプロセス）が、質問に答えるのに要る主張を最大 12 件、短い日本語の文で出します。
2. **判定**: 同じプロセスが、主張ごとにカードの抜粋だけを根拠に「支持 / 一部 / 矛盾 / 出典なし / 意見」を付けます。
3. **門（オーケストレータ、モデルなし）**: モデルの判定は参考です。「支持」は、引用したカードが実在し、主張とカードの抜粋が 20 字以上連続で一致するか、カードの数値・日付・固有名詞が主張に含まれるときだけ通します。カードに無い数値を含む主張は「出典なし」、固有名詞も数値も無い主張は「一部」まで、引用を原文で確認できなかったカードだけが根拠の主張も「一部」までです。
4. **統合**: 支持と一部の主張だけを渡して書かせます（一部は断定しない）。番号 [n] は振り直しません。
5. **監査と削除**: 書いた回答を句点で文に分け、同じ判定と門をもう一度通します。出典なし・矛盾の文は削除し、言い換えません。削除した文の直後の「したがって」「そのため」で始まる文と、本文が無くなった見出しも消します。
6. 回答の下の「主張の突き合わせ」を開くと、主張ごとの判定と出典番号、監査で削除した文が見られます。矛盾した候補があれば、出典一覧の前に 1 行だけそう書きます（中身は表にだけ出します）。

- JSON が壊れたら同じプロセスに 1 回だけ直させます。2 回目も壊れたとき、または `CLAIM_TIMEOUT_S` を超えたときは、無監査の回答を出さず、抽出に使ったカードの抜粋だけを返します（`CLAIM_VERIFY_FAIL_OPEN=1` にすると、文頭に「突き合わせ失敗」と付けた無監査の統合を返します）。監査だけが時間切れなら、判定済みの主張から書いた統合をそのまま返します。
- 判定のために通信はしません。追加の検索は、思考モードの批評の 1 回だけです。LM Studio の 27B を載せ直すこともしません。
- `CLAIM_VERIFY=0` で、この段を飛ばして v0.5.0 までの統合に戻ります。

### CUI（cirka）

`cirka` は、端末の作業ディレクトリで動くコーディングエージェントです（設計: [docs/locus-cui-design.md](docs/locus-cui-design.md)）。Claude Code と同じく、モデルが「次のツール呼び出し」か「最終回答」を返し、cirka がそのツールを **cirka を起動した PC の上で** 実行して結果を返す、を繰り返します。モデルはこの端末（ホスト）の LM Studio の 27B で、LangGraph の `POST /coder/turn` を通して使います。ホストはツールを実行せず、会話も保存しません（履歴は cirka のセッションファイルが持ちます）。

#### ビルドと配布

Rust 1.85 以上が要ります。Windows / macOS / Linux の各 OS で同じソースからビルドします。

```powershell
.\scripts\build-cirka.ps1            # cirka\target\release\cirka.exe を作り、dist\cirka-<版>-<OS>-<CPU>.zip にまとめる
# または
cd cirka; cargo build --release      # macOS / Linux も同じ
```

できた `cirka`（`cirka.exe`）を PATH の通った場所に置きます。単一の実行ファイルで、ほかに要るものはありません（grep もファイル検索も内蔵）。

#### 接続先の設定

既定の接続先は `http://127.0.0.1:2024`（ホストと同じ PC）です。別の PC から使うときは、ホストの LAN アドレスを設定します。

```powershell
cirka config set host http://192.168.1.20:2024   # ユーザー設定に保存（%APPDATA%\cirka\config.toml、macOS / Linux は ~/.config/cirka/config.toml）
cirka config set host http://gpu-box:2024 --project   # このディレクトリだけ（.\.cirka\config.toml）
cirka config show                                # 実際に使われる値と、読み込んだ場所
cirka status                                     # ホストに届くか、モデルと文脈の大きさ
```

優先順位は「既定 < ユーザー設定 < プロジェクト設定 < 環境変数（`CIRKA_HOST` ほか） < コマンドライン（`--host`）」です。実行中は `/host http://… [--save]` で切り替えられます。そのほかのキー: `mode`（fast / think / auto）、`permission`（default / accept-edits / plan / bypass）、`shell`（auto / pwsh / powershell / cmd / bash / sh）、`max_turns`、`locale`、`auth_header`（`Name: value`。認証方式は未確定なので既定は空。ヘッダを付ける差し込み口だけです）、`idle_timeout_s` / `search_timeout_s` / `image_timeout_s`。

#### 使い方

```
$ cd ~/src/some-project
$ cirka                         # 対話（REPL）
$ cirka -p "テストが落ちる原因を調べて直して" --permission accept-edits   # 1 回だけ実行して終わる
$ cirka --resume                # このディレクトリの直前のセッションを再開
```

起動すると、カレントディレクトリをワークスペースの根にし、`CIRKA.md` / `AGENTS.md` / `CLAUDE.md` があれば規則として読みます。複数の手順が要る依頼では、モデルがまずタスク一覧（`todo_write`）を作り、探索 → 編集 → コマンドでの確認、の順に進めます。短い質問はタスク一覧を作らずに答えます。

| ツール | 内容 | 既定の許可 |
|---|---|---|
| `list_dir` / `glob` / `grep` / `read_file` | 一覧、パターン検索、正規表現検索（.gitignore と `.cirkaignore` に従う）、行番号付きの読み取り | 自動 |
| `edit_file` | 一意に一致する原文を置き換える（先に `read_file` したファイルだけ。CRLF を保つ） | 差分を見せて確認 |
| `write_file` | 新規作成、または読んだファイルの置き換え | 差分を見せて確認 |
| `bash` | シェルのコマンド（Windows は PowerShell、ほかは `sh -lc`）。既定 120 秒・最大 600 秒、出力 64 KiB まで、時間切れや Ctrl-C でプロセスツリーごと止める | 全文を見せて確認 |
| `todo_write` / `ask_user` | タスク一覧、利用者への質問 | 自動 |
| `web_search` | ホストのチャットタブの検索（Tor 経由、出典付き） | 自動 |
| `image_generate` | ホストの画像タブ（参照画像 0〜4 枚と役割）。画像はワークスペースの `cirka-outputs/` に保存し、モデルにはパスだけを返す | 自動 |

確認では `y`（実行）/ `n`（拒否。理由はモデルに伝わる）/ `a`（以後この種類は確認しない）/ `q`（依頼を止める）を選びます。`/accept-edits` で編集は自動、`/plan` で変更とコマンドは提案だけ、`--permission bypass` で確認なし（シェルと同じ権限なので明示したときだけ）。どのモードでも、ワークスペースの外のパス（`..`、外を指すシンボリックリンク）と秘密ファイル（`.env`、`*.pem`、`*.key`、`id_rsa`、`credentials*` など）は扱いません。ツールの結果に鍵らしい文字列があれば `[redacted]` にしてから送ります（完全ではありません）。

スラッシュコマンド: `/help`、`/status`、`/host`、`/mode`、`/plan`、`/accept-edits`、`/default`、`/cd`、`/undo`（直前の編集を戻す）、`/compact`（会話を要約して文脈を空ける）、`/search`、`/image`、`/todos`、`/resume`、`/forget`（いまのセッションのログを消す）、`/quit`。Ctrl-C は実行中のツールやモデルの応答を止め、待機中に 2 回押すと終了します。行末の `\` で複数行を入力できます。

セッションは `%LOCALAPPDATA%\cirka\sessions`（macOS / Linux は `~/.local/share/cirka/sessions`）に JSON Lines で残ります（ツールの結果を含みます）。

#### 注意

- ホストの 27B は context 4096 トークン・約 0.9 トークン/秒です。cirka は毎ターン、規則・環境・タスク一覧・会話をこの窓に収めます（古いツール結果は 1 行の要約に、さらに溢れたら古いやり取りから省き、ホストが「入らない」と返したら詰めて 1 回だけ送り直す）。1 ターンに数分かかるので、依頼は小さく区切ってください。
- ホストの処理は共有ロックで直列です。画像タブやチャットタブが動いているあいだ、cirka は「待っています」と表示して待ちます（エラーにしません）。
- 認証はありません。読んだファイルの断片とコマンドの出力が LAN 上のホストへ送られます。信頼できるネットワークだけで使ってください。

#### 検索で使うモデルと採否

| モデル | 採否 | 役割 | 理由（この端末の実測） |
|---|---|---|---|
| Qwen3.8 27B abliterated（LM Studio、IQ3_M） | 計画だけ採用 | 検索意図に分ける | 指定どおり最初の処理だけ担当。ロード中は空きが 1GB を切り reader が載らないため、計画のあと unload する（ロード約 2 分） |
| Ternary-Bonsai-2-27B abliterated（Override-6、PTQ1_0） | 採用 | 批評・統合（代理リーダー） | 6.8GB で 27B 系の品質。計画・批評・統合の検証に合格（起動 7 s、8.9 tok/s）。拒否が少なく、ソースの批評で止まりにくい |
| Ternary-Bonsai-8B（PQ2_0） | 採用 | reader（最大 3 体） | ツール呼び出しと事実カードの検証に合格。2.9GB で、代理 27B を除いた空きに 3 体入る（29 tok/s） |
| Bonsai-4B（1-bit） | 採用 | フィルタ | 関係あり / なしの判定に合格。1.4GB、46 tok/s と最も軽い。抽出や回答には使わない |
| Qwen3-1.7B-heretic（Q4_K_M） | 採用 | ルータ（検索の要否、検索語の書き換え） | 要る / 要らないの両方を正しく判定。起動 1.8 s、59 tok/s。フィルタと reader の予備も兼ねる |
| Qwen3.5-4B-heretic（Q4_K_M） | 予備 | reader・ルータ・批評・統合の 2〜3 番手 | 全タスクに合格したが、3.6GB と重く 19.8 tok/s と遅い |
| Ternary-Bonsai-2-27B（通常版、PTQ1_0） | 予備 | 批評・統合の 2 番手 | 全タスクに合格（6.6GB、8.0 tok/s）。abliterated 版の方が拒否されにくいため 2 番手。abliterated 版の起動に失敗すると自動でこちらを使う |
| Bonsai-8B（1-bit） | 予備（原則不採用） | reader の 3 番手 | reader の検証に合格し、速度は Ternary 8B と同じで 1.9GB と軽い。「同じ帯域なら Ternary 8B が上」という方針で順位を下げた（品質差はこの端末では比べていない） |
| Qwen3-0.6B-heretic（Q8_0） | 予備（フィルタとルータの最後） | — | 旧形式のルータの判定には不合格（分類の精度が足りない）だった。v0.5.0 のルータ（会話 / 検索 / 文章 / コードを返す形式）の検証には合格したので、ルータの 3 番手。フィルタも合格。Bonsai-4B と 1.7B が使えないときだけ使う |

検証（`scripts\probe-bonsai.ps1`）は、タスクごとに固定の短いテスト 1 本で合否を見ただけです。長い作業での安定性や、回答の質の細かい差は測っていません。
順位は `config\search_models.json` を書き換えるか、probe をやり直すと変わります。reader だけは `.env` の `BONSAI_MODEL` で固定できます。

## 5. 設定（`.env`）

| キー | 既定 | 説明 |
|---|---|---|
| `COMFYUI_URL` | `http://127.0.0.1:8188` | LangGraph から見た ComfyUI |
| `CKPT_NAME` | `yiffInHell_yihVANTABLACK.safetensors` | 使うチェックポイント（実行時にワークフローの値を上書き） |
| `COMFYUI_TIMEOUT_S` | `600` | 待ち時間の上限（タグ生成・画像生成それぞれ） |
| `LORAS` | 空 | 適用する LoRA（「4. 使い方 › LoRA」） |
| `COMFY_MODEL_FAMILY` | `sdxl` | モデル系統（`workflows/<系統>/`）。`sdxl`（yiffInHell、タグ）または `flux`（Chroma1-HD、英語の説明文） |
| `CHROMA_UNET_NAME` / `CHROMA_TEXT_ENCODER` / `CHROMA_VAE` / `CHROMA_WEIGHT_DTYPE` | 空（マップの値） | Chroma のモデルファイルと読み込み精度（「4. 使い方 › Chroma1-HD」） |
| `CHROMA_LORAS` | 空 | Chroma に適用する LoRA（書式は `LORAS` と同じ） |
| `CHROMA_MAX_PIXELS` / `CHROMA_STEPS` | 空（1048576 / 28） | Chroma の画素数の上限とステップ数（遅い GPU 向け） |
| `LMSTUDIO_MODEL` | セットアップが設定 | ワークフローが呼ぶ LM Studio のモデルキー |
| `COMFYUI_MAIN_DIR` ほか `COMFYUI_*` | セットアップが設定 | `start-comfyui.ps1` が使う ComfyUI の場所 |
| `COMFYUI_EXTRA_ARGS` | 空 | ComfyUI の追加引数（例 `--enable-manager`） |

LM Studio 側の値を変えるときは、`.\scripts\setup-lmstudio.ps1 -GpuOffload 0.6` のように再実行します。

チャットタブの設定（画像タブは読みません。全項目と説明は `.env.example`）:

| キー | 既定 | 説明 |
|---|---|---|
| `LMSTUDIO_URL` | `http://127.0.0.1:1234/v1` | チャットタブの会話と検索の計画・統合だけが使う |
| `TOR_SOCKS_URL` / `TOR_EXE` / `TOR_AUTOSTART` | `socks5h://127.0.0.1:9050` / セットアップが設定 / `1` | Tor。`socks5h` 以外は拒否（DNS 漏れ防止） |
| `SEARCH_FANOUT_WIDTH` / `SEARCH_MAX_RESULTS` | `3` / `5` | 検索意図と reader の数の上限（1〜3）、1 検索の結果数 |
| `SEARCH_TIMEOUT_S` / `SEARCH_TOTAL_TIMEOUT_S` | `30` / `150` | 1 リクエストの上限、reader 1 体の上限 |
| `SEARCH_PLANNER` | `lmstudio` | `local` にすると、計画も代理 27B が行う（LM Studio を検索で使わない） |
| `SEARCH_FILTER` / `SEARCH_CRITIQUE` / `SEARCH_AUTO_ROUTE` | `1` | フィルタ / 思考モードの批評と追加ラウンド / 決まらない文のルータ判定 |
| `CHAT_TIMEOUT_S` | `1200` | 会話・文章・コード・統合のモデル呼び出し 1 回の上限（20 分） |
| `SEARCH_MAX_ROUNDS` / `SEARCH_MAX_PAGES` / `SEARCH_WALL_CLOCK_S` | `4` / `12` / `1200` | 思考モードの検索のラウンド、読むページ、全体の時間（20 分）の上限 |
| `SEARCH_HITS_PER_INTENT` | `4` | 1 つの検索意図から reader に渡す結果数 |
| `CHAT_THINK_TOKENS` | `3072` | 思考モードで回答に足す思考トークンの上限（context と速さの範囲内で使う） |
| `LMSTUDIO_CONTEXT` / `LMSTUDIO_TOKENS_PER_S` | `4096` / `1.0` | 27B の context と、測る前の生成速度。1 回の呼び出しの量をこの範囲に収める |
| `SANDBOX_DOCKER` / `SANDBOX_USER` / `SANDBOX_WAIT_S` | `docker` / `10001:10001` / `600` | コード実行の docker、コンテナ内の uid:gid（root は不可）、画像タブの生成が終わるのを待つ上限 |
| `BONSAI_LLAMA_SERVER` / `BONSAI_MODELS_DIR` | セットアップが設定 | PrismML fork の llama-server.exe と、モデルの置き場 |
| `BONSAI_MODEL` | 空（自動） | reader のモデルを固定する（`ternary-8b` など）。入らなければ断る |
| `BONSAI_RESERVE_MB` | `3072` | モデルを何体載せるか決めるときに残す空きメモリ |
| `JOB_LOCK_TIMEOUT_S` | `30` | もう片方のタブの実行が終わるのを待つ上限 |
| `CLAIM_VERIFY` / `CLAIM_VERIFY_FAIL_OPEN` | `1` / `0` | 主張の突き合わせ（`0` で旧来の統合） / 失敗時に無監査の回答を出すか |
| `CLAIM_MAX` / `CLAIM_QUOTE_CHARS` / `CLAIM_TIMEOUT_S` | `12` / `400` / `600` | 主張の上限、判定に見せる抜粋の長さ、抽出 + 判定 + 監査の時間の上限 |
| `CONTROLLER_MAX_STEPS` | `3` | 自律モードで道具を使う回数の上限（1〜4） |
| `CONTROLLER_WALL_CLOCK_S` | 空（`SEARCH_WALL_CLOCK_S`） | 自律モード全体の時間の上限。`SEARCH_WALL_CLOCK_S` を超える値は `SEARCH_WALL_CLOCK_S` になる |

cirka 向けの `POST /coder/turn` は、`LMSTUDIO_URL`、`LMSTUDIO_MODEL`、`LMSTUDIO_CONTEXT`、`LMSTUDIO_TOKENS_PER_S`、`CHAT_TIMEOUT_S` を使います（新しいキーはありません）。

## 6. メモリと LLM の量子化

27B の LLM と SDXL チェックポイントは同時に載りません。ワークフローは
「LLM がタグを返す → LM Studio から unload → チェックポイント読み込み → KSampler」の順に固定し、
LangGraph は投入前と完了後（失敗時も）に ComfyUI の `/free` を呼んで、前回のチェックポイントを解放します。

確認済み構成（共有メモリ 24GB、OS から見える RAM 約 23GB）では、ダウンロードした Q4_K_S（15.6GB + mmproj）は
OS・画面表示・ComfyUI の常駐分と合わせると物理メモリに収まらず、ページングでほぼ停止しました。
そのため既定では、セットアップが同じモデルを **IQ3_M（約 12.2GB）に再量子化** して使います（元ファイルは残します）。

- GPU オフロード 0.45: この iGPU では Vulkan が確保できる量に上限があり、さらに ComfyUI が一度生成すると ROCm ランタイムが約 2GB を保持し続けるためです。0.5 以上では 2 回目以降のロードが `ErrorOutOfDeviceMemory` になりました。
- メモリに余裕がある環境（32GB 以上や dGPU）では `.\scripts\setup.ps1 -Quant none -GpuOffload 1` で Q4_K_S をそのまま使えます。
- UMA の GPU 割当（BIOS / Armoury Crate の VRAM 設定）を増やしても物理 RAM の総量は増えません。生成中はブラウザのタブなど他のアプリを減らしてください。

## 7. 構成

```
AGENTS.md / docs/                     仕様
langgraph.json                        graphs.agent（= image）-> graph.py:graph、graphs.chat -> chat_graph.py:graph
src/furry_agent/chat_graph.py         チャットタブのグラフ（ingest → route → chat | plan → search → filter → read → critique → synthesize）
src/furry_agent/chat_models.py        チャットタブの llama-server（reader、代理リーダー）の起動と停止、外部依存の差し替え口
src/furry_agent/claim_verify.py       主張の突き合わせ: EvidenceCard / Claim、門（字面・数値・固有名詞）、文の分割と削除。モデルなし
src/furry_agent/claim_nodes.py        主張の抽出 → 判定 → （統合）→ 監査 → 削除のノード
src/furry_agent/control_nodes.py      自律モード（controller → 道具の流れ → controller_record → finish）。判断の JSON、予算、重複の禁止
src/furry_agent/coder_gate.py         cirka 向けの POST /coder/turn（LM Studio の tool calling を SSE で返す。ツールは実行しない）。coder_app.py が langgraph.json の http.app
prompts/chat/controller.txt           自律モードの判断の system prompt
cirka/                                CUI（Rust）。src/agent.rs（ループ）、tools/（ローカルのツールとホストの検索・画像）、host.rs、policy.rs、context.rs、session.rs
scripts/build-cirka.ps1               cirka のリリースビルドと zip（dist/）
src/furry_agent/search_agent.py       検索のスキーマ（Pydantic）、ページの絞り込み、引用の照合、統合への入力
src/furry_agent/search_client.py      Tor（socks5h）経由の検索と本文取得、URL の許可判定
src/furry_agent/bonsai_select.py      タスクごとのモデル選択（順位、検証結果、空きメモリ）
src/furry_agent/bonsai_worker.py      llama-server の起動と停止（PID）、reader のツール呼び出し
src/furry_agent/bonsai_probe.py       検索モデルの検証（probe-bonsai.ps1）
src/furry_agent/llm_client.py         OpenAI 互換クライアント（チャットタブ専用）、LM Studio の unload
src/furry_agent/router.py, tor_service.py, html_text.py, job_lock.py   検索判定、Tor の自動起動、HTML のテキスト化、タブ共通のロック
config/search_models.json             検索モデル 8 つ（ファイル、メモリの目安、タスクごとの順）
src/furry_agent/graph.py              LangGraph のグラフ（ingest → plan → confirm → submit → await_tags → await_image）
src/furry_agent/planner.py            役割推定（ルール）、テンプレート選択、数値のクランプ。純粋関数
src/furry_agent/templates.py          テンプレートの読み込み、ノードマップ経由の注入、LoRA の挿入
src/furry_agent/comfy_client.py       ComfyUI HTTP / WebSocket クライアント
src/furry_agent/media.py              添付画像の取り出しと検証（役割・強度は block の metadata）
comfyui_nodes/furry_ja/               ComfyUI カスタムノード（split / ckpt / Chroma 用 ckpt / image-after / release）
src/furry_agent/families.py           モデル系統の名前（COMFY_MODEL_FAMILY）と系統ごとの参照画像の可否
workflows/sdxl/<テンプレートID>.api.json  役割別のテンプレート 24 本。LangGraph が読む
workflows/maps/sdxl.json              テンプレートごとのスロット（node.inputs.field）とポーズ前処理の候補
workflows/flux/*.api.json             Chroma1-HD のテンプレート（t2i_basic / i2i_basic）
workflows/maps/flux.json         Chroma のスロット、モデルファイル、サンプラーの既定値、対応する役割
workflows/reference/                  公式 ComfyUI_Chroma1-HD_T2I-workflow.json（Chroma テンプレートの写し元）
workflows/furry_ja_api.json           フェーズ 1 の API 形式（t2i_basic / i2i_basic の元。ノード ID は設計書 §4.1）
workflows/furry_ja.json               UI 形式。ComfyUI で開ける（ノードのタイトル = ノード ID）
prompts/system_furry_tags.txt         タグ生成の system prompt（テキストのみ / 元画像 1 枚）
prompts/system_furry_tags_roles.txt   役割付き参照のタグ統合用 system prompt
prompts/system_vision_*.txt           参照画像タグ付けの system prompt（caption / style / pose / character）
prompts/system_chroma_prose.txt       Chroma 用。英語の説明文を返させる system prompt
prompts/system_chat.txt, system_search*.txt, system_bonsai_worker.txt   チャットタブの会話と検索の各段
prompts/system_claim_extract.txt, system_claim_verify.txt   主張の抽出と判定（監査も判定と同じ）
tools/tor/torrc                       Tor の設定（SOCKS 127.0.0.1:9050 のみ）
scripts/setup*.ps1                    セットアップ（scripts/lib/common.ps1 が共通処理）
scripts/start-*.ps1, doctor.ps1       起動と確認
scripts/open-firewall.ps1             ファイアウォール（管理者）
scripts/build_workflows.py            workflows/（テンプレートとマップを含む）を prompts/ から生成
scripts/convert_chroma_fp8.py         Chroma の BF16 拡散モデルを fp8 に変換（setup-comfyui-chroma.ps1 が呼ぶ）
agent-chat-ui/                        公式 UI（langchain-ai/agent-chat-ui@cf72cb0、画像表示と役割選択の変更あり）
tests/                                pytest（Python と PowerShell スクリプトの両方）
outputs/  logs/  tools/  artifacts/   実行時に生成（git 管理外）
```

### ワークフローのノード

| ID | クラス | 役割 |
|---|---|---|
| `llm_backend` | LMConnectLMStudioBackend | `http://127.0.0.1:1234/v1`、auto-eject on、thinking off |
| `llm_backend_vision` | LMConnectLMStudioBackend | Vision 用。auto-eject off（直後の `prompt_node` で 27B を再ロードしないため） |
| `user_prompt` | PrimitiveStringMultiline | 日本語指示（LangGraph が書き換え） |
| `ref_image` | LoadImage | 修正する元画像（参照なしの実行では削除） |
| `vision` | LMConnectVision | 元画像をタグ化。長辺 768 |
| `prompt_join` | StringConcatenate | 指示 + `[Reference image tags]` |
| `prompt_node` | LMConnectPromptWithSystem | JSON `{"positive","negative"}` を返させる |
| `eject` | LMConnectEjectLMStudioModel | LM Studio のモデルを unload し、テキストを passthrough |
| `split` | FurryJaSplitTags | 最初の `{` から最後の `}` を `json.loads`（不正なバックスラッシュエスケープは除去）。失敗時はリトライせず生文字列を positive、固定の画質タグを negative。品質タグを先頭に付与。LM Connect がエラー文字列を返した場合は実行を失敗させる |
| `ckpt` | FurryJaCheckpointLoaderAfterEject | CheckpointLoaderSimple に `after`（eject の出力）を足したもの。ロード前に LM Studio のモデルが 0 であることを API で確認し、残っていれば unload、消えなければ失敗 |
| `positive` / `negative` | CLIPTextEncode | |
| `ref_scale` | ImageScaleToTotalPixels | img2img 用に約 1MP（64 の倍数）へ |
| `latent` | EmptyLatentImage / VAEEncode | テキストのみは Empty Latent 832×1216、img2img は VAE Encode |
| `sampler` | KSampler | steps 28、cfg 5.5、euler_ancestral、normal、denoise 1.0 / 0.45 |
| `decode` / `save` | VAEDecode / SaveImage | `output/furry_ja/` に保存 |

`ckpt` を素の CheckpointLoaderSimple にすると、入力が無いため ComfyUI が LLM より先に実行し得ます。`after` 入力で eject の後に固定しています。

### 役割別テンプレートで足すノード

上の表は `t2i_basic` / `i2i_basic` と同じです。役割付きのテンプレートは次を足します。重いモデルを読むノードはすべて `ckpt`（= eject の後）に依存させ、27B と同時に載らないようにしています（テストで全テンプレートを確認）。

| ID | クラス | 役割 |
|---|---|---|
| `character_image` / `pose_image` / `style_image` / `mask_image` | LoadImage | 役割ごとの参照画像（`ref_image` は修正する元画像） |
| `vision_character` / `vision_pose` / `vision_style` | LMConnectVision | 役割専用の system prompt でタグ化（ポーズは骨格とカメラだけ、画風は描き方だけ） |
| `prompt_join_*` | StringConcatenate | 指示 + `[Character reference tags]` などの節 |
| `ipa_loader` | IPAdapterUnifiedLoader | PLUS プリセット。`ckpt` の MODEL を入力にとる |
| `ipa_character` / `ipa_style` | IPAdapterAdvanced | weight = 強度 × 係数（キャラクター 0.5、画風 1.0。マップの `ipadapter_weight_scale`）。画風は `style transfer`（主題を写さない） |
| `pose_gate` | FurryJaImageAfter | ポーズ画像を `ckpt` の後まで止める（前処理が LLM と同時に走らないように） |
| `pose_preprocess` | DWPreprocessor / DepthAnythingV2Preprocessor / Canny | `openpose`（既定）/ `depth` / `canny` をマップの候補から差し替える |
| `pose_cn` / `pose_union` / `pose_apply` | DiffControlNetLoader / SetUnionControlNetType / ControlNetApplyAdvanced | strength = 強度 |
| `pose_preview` | PreviewImage | 抽出結果をチャットに返す |
| `mask_channel` / `latent_mask` | ImageToMask / SetLatentNoiseMask | inpaint |
| `release` | FurryJaReleaseEncoders | すべての条件付け（テキスト・IP-Adapter）ができた後、KSampler の直前でテキストエンコーダと CLIP-Vision を GPU から外す |
| `lora_1`… | LoraLoader | `LORAS` があるときだけ、実行時に `ckpt` の直後へ挿入 |

`t2i_basic` / `i2i_basic` は、フェーズ 1 の `furry_ja_api.json` から作る投入 JSON と完全に同じです（テストで確認）。

### UI の変更点

- 画像表示: agent-chat-ui は AI メッセージのテキスト部分しか描画しないため、グラフが返す画像ブロック
  `{"type": "image", "mimeType": "image/png", "data": <base64>}` を描画する最小限の変更を
  `agent-chat-ui/src/components/thread/messages/ai.tsx` に加えています。
- 役割選択: 添付した画像ごとに役割のセレクトと強度の欄を出し、画像ブロックの `metadata.role` / `metadata.strength` として送ります
  （`ContentBlocksPreview.tsx`、`lib/image-roles.ts`、`hooks/use-file-upload.tsx`）。送信済みのメッセージには役割のラベルを表示します（`MultimodalPreview.tsx`）。
  添付は画像 4 枚までに制限します。metadata を送れないクライアントは、run の `configurable.references = [{"index": 1, "role": "style", "strength": 0.6}]` でも指定できます。
- 確認カード: 役割の確認は agent-chat-ui 在庫の HITL 表示（承認 / 編集 / 却下）をそのまま使います。
- タブ: 画面上部の「画像」「チャット」で、接続するグラフ（`agent` / `chat`）を切り替えます（`components/thread/mode-tabs.tsx`、配置は `thread/index.tsx`）。
  履歴はグラフごとに分かれ、タブごとに最後のスレッドを覚えます。チャットタブでは添付ボタンを隠します。
- 検索痕跡: チャットタブの応答の `additional_kwargs.search_trace` を、折りたたみの一覧として描画します（`messages/search-trace.tsx`、`ai.tsx`）。
  思考モードではラウンド、下位問いと状態、採用 / 不採用のカードと理由、停止理由、読んだページ数と経過秒も出します。
- 応答モード: チャットタブの入力欄に「自動 / 速い / 思考」を置き、送信ごとに `config.configurable.mode`（`auto` / `fast` / `think`）として送ります（`mode-tabs.tsx` の `ChatModeSwitch`、配置は `thread/index.tsx`）。選択はブラウザの localStorage に覚えます。
- 思考と手順: `additional_kwargs.thinking`（思考トークン、既定で閉じた折りたたみ）、`task_trace`（執筆とコードの手順）、`chat_mode`（選ばれたモードと自動の理由）を描画します（`search-trace.tsx`、`ai.tsx`）。思考は回答本文に混ぜません。
- 主張の突き合わせ: `additional_kwargs.claim_trace`（主張ごとの判定、出典番号、監査で削除した文）を折りたたみで描画します（`search-trace.tsx` の `ClaimTraceView`、`ai.tsx`）。
- 自律の手順: 自律モードのメッセージの `task_trace`（`kind: "control"`。選んだ道具、理由、依頼文、結果の要約、終了理由）を、執筆・コードの手順と同じ折りたたみで描画します（`search-trace.tsx` の `TaskTraceView`）。

## 8. ログ

- `logs\furry_agent.log`（LangGraph 側）: チャットタブの経路・モデル・reader の所要時間・結果 URL（検索語の全文は残しません）、画像タブの投入（prompt_id / seed）、タグ、`ckpt gate: LM Studio unloaded=[True]`、
  `KSampler started ... LM Studio unloaded at checkpoint load=[True]`、`eject verified`、保存先。
- ComfyUI のコンソール: `[LM Connect] Eject sonucu`、`[furry_ja] split mode=json|fallback`、
  `[furry_ja] LM Studio verified unloaded before checkpoint/KSampler`。

## 9. 開発

```powershell
uv sync
uv run pytest                                  # Python と PowerShell スクリプトのテスト（Docker 実機のテストは Docker 起動中だけ）
uv run python scripts\build_workflows.py       # prompts\ を変えたら workflows\ を再生成
cd cirka; cargo test                           # cirka（CUI）のテスト。ホストは立てない
```

`.ps1` は UTF-8（BOM 付き）で保存してください。Windows PowerShell 5.1 は BOM の無いファイルを ANSI として読み、日本語を含む行で構文エラーになります（テストで確認しています）。

## 10. トラブルシューティング

| 症状 | 対処 |
|---|---|
| `doctor.ps1` で custom nodes が NG | `setup-comfyui.ps1` の後に ComfyUI を再起動したか確認 |
| チャットタブ「Tor が 127.0.0.1:9050 で待ち受けていません」 | `.\scripts\setup-tor.ps1` の後に `.\scripts\start-tor.ps1`。ログは `logs\tor.log` |
| 「PrismML 版 llama.cpp がありません」/「検索用モデルがありません」 | `.\scripts\setup-llamacpp.ps1` / `.\scripts\setup-search-models.ps1` を実行して LangGraph を再起動 |
| 「〜に使えるモデルがありません（メモリ不足…）」 | 他のアプリを閉じる。`BONSAI_RESERVE_MB` を下げる。`SEARCH_FANOUT_WIDTH` を 1〜2 にする |
| 「検索結果がありません。Tor 出口が拒否された…」 | しばらく置いて送り直す（出口が変わる）。`logs\furry_agent.log` の `search provider=` を確認 |
| 「チャットタブ（画像タブ）が実行中です」 | もう片方のタブの処理が終わってから送り直す |
| チャットタブのコードで「Docker Desktop が起動していません」/「Docker がインストールされていません」 | Docker Desktop を入れて `.\scripts\setup-sandbox.ps1`（イメージ取得と動作確認）。普段は止めたままでよく、承認後に自動で起動・停止する |
| 「コンテナイメージ python:3.12-slim がありません」 | `.\scripts\setup-sandbox.ps1`（Rust は `-Rust`）。実行時はイメージを取得しない（`--pull never`） |
| 思考モードなのに「思考」の折りたたみが出ない | 答えに要る量と 20 分の時間枠（約 1000 トークン）に思考の余地が無いと、思考なしで答える（`logs\furry_agent.log` の `thinking=False`）。代理リーダーが統合した検索の回答にも思考は無い |
| 思考モードの文章・コードがとても遅い | この端末の 27B は約 0.9 トークン/秒。長い文章は章立てにするか「速い」で送る |
| 「LM Studio に接続できないか、時間切れです（HTTP 400: Model is unloaded.）」 | LM Studio の自動 unload と要求が重なった。1 回は自動で送り直すので、続くときは送り直す |
| `doctor.ps1` で「no orphan llama-server」が WARN | 検索中でなければ `Stop-Process -Name llama-server` |
| ブラウザに Deployment URL の入力画面が出る / 接続できない | `start-ui.ps1` を再実行（LAN IP が変わると再ビルド）。`open-firewall.ps1` を管理者で実行。ネットワークがプライベートか確認 |
| 「生成できませんでした: ... Failed to load model」 | メモリ不足。他のアプリを閉じる、`setup-lmstudio.ps1 -GpuOffload 0.4` に下げる、ComfyUI を再起動して常駐メモリを解放 |
| 10 分でタイムアウト | 同上。参照画像の枚数を減らす。完了していれば `再取得 <prompt_id>` で受け取れる |
| 「この環境の ComfyUI に無いノードがあります」 | `setup-comfyui-refs.ps1` を実行して ComfyUI を再起動。`doctor.ps1` の reference nodes を確認 |
| 「この環境の ComfyUI に無いノードがあります: FurryJaDiffusionLoaderAfterEject」 | Chroma 対応後に ComfyUI を再起動していない。`start-comfyui.ps1` で起動し直す |
| 「Chroma1-HD のモデルファイルが ComfyUI に見つかりません」 | `setup-comfyui-chroma.ps1` を実行。拡散モデルは手動で `models\diffusion_models` か `models\checkpoints` に置く |
| 「… は Chroma1-HD のモデルです」 | `CKPT_NAME` だけを Chroma にした。`COMFY_MODEL_FAMILY=flux` も設定して LangGraph を再起動する |
| Chroma で「ポーズ ControlNet 未対応」などと返る | Chroma 経路は元画像 1 枚の img2img だけ対応。ポーズ・画風の参照は `COMFY_MODEL_FAMILY=sdxl` で使う |
| 「LoRA が ComfyUI に見つかりません」 | `.env` の `LORAS` の名前を `models\loras` のファイル名に合わせる |
| 参照画像を使った 2 回目以降の画像が単色やノイズになる | ComfyUI が `--cache-none` なしで起動している。`doctor.ps1` で確認し、`start-comfyui.ps1` で起動し直す（Comfy Desktop は `setup-comfyui.ps1 -ConfigureComfyDesktop`） |
| キャラクター参照で色が焼ける・ギラつく | キャラクターの強度を下げる（0.6 前後） |
| 画像の役割が思ったものにならない | 添付時に役割を選ぶ。または `1枚目のキャラ` `2枚目のポーズ` のように序数で書く |
| タグの前に思考文が出る / 遅い | LM Studio のモデル既定値で Thinking がオフか確認（`setup-lmstudio.ps1` を再実行） |
| 参照画像を送っても説明が空 | mmproj がモデルと同じフォルダにあるか確認 |
| `missing tensor 'blk.64...'` でロードできない | LM Studio のランタイムが古い。`lms runtime update --all` |
| `langgraph dev` が `UnicodeDecodeError: 'cp932'` で落ちる | `start-langgraph.ps1` から起動する（`PYTHONUTF8=1` を設定します） |

## 調整の記録（参照画像）

確認済み構成（yiffInHell VANTABLACK、Radeon 890M / ROCm、ComfyUI 0.38）で、LLM を通さず固定タグで比較した結果です。スクリプトの既定値の根拠です。

| 事象 | 原因と対処 |
|---|---|
| IP-Adapter Plus を weight 0.85 で使うと色が焼け、青く飽和する | Illustrious 系のこのモデルには強すぎる。0.4 で同一性を保ったまま破綻しなくなった → キャラクターの weight = 強度 × 0.5 |
| 同じ ComfyUI プロセスで 2 回目以降の IP-Adapter 生成が単色・ノイズ（12KB 程度の PNG）になる | メモリ量や weight を変えても再現し、`--cache-none` で解消 → 起動引数に追加 |
| DWPose の TorchScript（YOLOX / ポーズ推定）が数回目で `invalid shape dimension` 等で失敗する | ROCm の GPU 上で不安定 → 人物検出なし（画像全体を 1 人とみなす）+ ONNX のポーズ推定を CPU（OpenCV）で実行 |
| SDXL + ControlNet + IP-Adapter + エンコーダが GPU 予算（空き約 6.8GB）を超え、UNet が全てオフロードされる | KSampler 直前でエンコーダを外すノード（`release`）を追加 |
| 画風参照（style transfer）0.55〜0.8 | 主題を写さず配色とトーンが移る。0.8 でも破綻なし |

## 既知の対象外・制約

- **動画入力は対象外**: ComfyUI-VideoHelperSuite（VHS）を前提にした経路は未実装です。動画を送るとチャットにその旨を返します（在庫の agent-chat-ui も動画の添付を受け付けません）。
- InstantID / PuLID（人の顔向けの同一性）は使いません。キャラクター参照は IP-Adapter Plus と Vision タグで行います。
- 登録済みの系統は `sdxl` と `flux`（Chroma1-HD）だけです（Flux Dev 本家、SD3 などは未登録）。
- Chroma1-HD 経路は、ポーズ・画風・キャラクター参照とマスクに未対応です（Flux 用 ControlNet Union Pro / Redux / IP-Adapter の Chroma での動作を確認していないため。作業指示書 §2.4）。GGUF 量子化の読み込みにも未対応です。
- 役割推定は LLM ではなくルール（日本語のキーワードと序数）です。LangGraph から LM Studio を呼ばない（AGENTS.md）ためです。
- 認証なし。LAN 内の開発用途のみ。チャットタブの検索も LAN から誰でも使えます（画像タブと同じリスク）。
- チャットタブの検索は DuckDuckGo（Lite、空なら HTML 版）だけです。Tor の出口によっては空の結果になります。CAPTCHA の突破や指紋偽装、`.onion` の巡回はしません。
- コードの実行は Docker Desktop（Linux エンジン）だけです。Windows コンテナ、WSL 直接、ホストでの実行はしません。コンテナ内からネットワークは使えず（依存の取得だけ例外）、1 回 60 秒・2GB までです。GUI、サーバの常駐、標準入力を使うプログラムは動きません。
- 思考トークンは LM Studio の 27B だけです。代理リーダー（Ternary-Bonsai-2-27B）は `--reasoning off` のまま動かすので、代理で統合した回答には思考の折りたたみが出ません。
- 検索モデルは PrismML の llama.cpp fork の Vulkan 版だけで動かします（Q1_0 / PQ2_0 / PTQ1_0 は素の llama.cpp や LM Studio では動かないため）。ROCm 版は使いません。

## ライセンス

このリポジトリのコードは MIT または Apache-2.0（[LICENSE-MIT](LICENSE-MIT)、[LICENSE-APACHE](LICENSE-APACHE)）です。
`agent-chat-ui/` は upstream（[langchain-ai/agent-chat-ui](https://github.com/langchain-ai/agent-chat-ui)）の MIT ライセンスで、変更箇所は「UI の変更点」に記載しています。

次のものはこのリポジトリに含めていません。セットアップスクリプトや事前準備で各配布元から利用者の環境へ取得し、それぞれのライセンス・規約に従います。
ライセンスは 2026-10-04 時点で各リポジトリ・モデルページの表示を確認したものです。利用前に配布元で最新の表示を確認してください。

| 名称 | 取得するもの | 取得方法 | ライセンス |
|---|---|---|---|
| [eedali/LM_Connect](https://github.com/eedali/LM_Connect) | ComfyUI カスタムノード（LM Studio 連携） | `setup-comfyui.ps1` | リポジトリにライセンスの表示なし |
| [cubiq/ComfyUI_IPAdapter_plus](https://github.com/cubiq/ComfyUI_IPAdapter_plus) | ComfyUI カスタムノード（IP-Adapter） | `setup-comfyui-refs.ps1` | GPL-3.0 |
| [Fannovel16/comfyui_controlnet_aux](https://github.com/Fannovel16/comfyui_controlnet_aux) | ComfyUI カスタムノード（DWPose / 深度などの前処理） | `setup-comfyui-refs.ps1` | Apache-2.0 |
| [xinsir/controlnet-union-sdxl-1.0](https://huggingface.co/xinsir/controlnet-union-sdxl-1.0) | ControlNet Union promax | `setup-comfyui-refs.ps1` | Apache-2.0 |
| [h94/IP-Adapter](https://huggingface.co/h94/IP-Adapter) | IP-Adapter Plus SDXL、画像エンコーダ（OpenCLIP ViT-H/14） | `setup-comfyui-refs.ps1` | Apache-2.0（画像エンコーダの元の [laion/CLIP-ViT-H-14-laion2B-s32B-b79K](https://huggingface.co/laion/CLIP-ViT-H-14-laion2B-s32B-b79K) は MIT） |
| [yzd-v/DWPose](https://huggingface.co/yzd-v/DWPose) | DWPose のポーズ推定モデル（ONNX） | `setup-comfyui-refs.ps1` | Apache-2.0 |
| [depth-anything/Depth-Anything-V2-Small](https://huggingface.co/depth-anything/Depth-Anything-V2-Small) | 深度推定モデル | `setup-comfyui-refs.ps1` | Apache-2.0 |
| [python:3.12-slim](https://hub.docker.com/_/python) / [rust:1.88-slim](https://hub.docker.com/_/rust) | コード実行用のコンテナイメージ（Debian slim） | `setup-sandbox.ps1` | Python は PSF License、Rust は MIT / Apache-2.0、Debian の各パッケージはそれぞれのライセンス |
| [comfyanonymous/flux_text_encoders](https://huggingface.co/comfyanonymous/flux_text_encoders) | T5-XXL fp8（Chroma のテキストエンコーダ） | `setup-comfyui-chroma.ps1` | Apache-2.0（元の google/t5-v1_1-xxl） |
| [lodestones/Chroma1-HD](https://huggingface.co/lodestones/Chroma1-HD) | VAE（`ae.safetensors` として保存） | `setup-comfyui-chroma.ps1` | Apache-2.0 |
| Chroma1-HD 拡散モデル | [lodestones/Chroma1-HD](https://huggingface.co/lodestones/Chroma1-HD) または Civitai | 事前準備（手動） | Apache-2.0 |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) | `llama-quantize`（LLM の再量子化） | `setup-lmstudio.ps1`（`tools\`） | MIT |
| LLM（Huihui Qwen3.8 27B Abliterated と mmproj） | LM Studio でダウンロード | 事前準備（手動） | 配布ページで確認 |
| [Tor Expert Bundle](https://www.torproject.org/download/tor/) | tor.exe（チャットタブの検索） | `setup-tor.ps1`（`tools\tor`） | BSD-3-Clause |
| [PrismML-Eng/llama.cpp](https://github.com/PrismML-Eng/llama.cpp) | llama-server（Vulkan、検索モデルの実行） | `setup-llamacpp.ps1`（`tools\llama-prism`） | MIT |
| [prism-ml/Bonsai-8B-gguf](https://huggingface.co/prism-ml/Bonsai-8B-gguf)、[Ternary-Bonsai-8B-gguf](https://huggingface.co/prism-ml/Ternary-Bonsai-8B-gguf)、[Bonsai-4B-gguf](https://huggingface.co/prism-ml/Bonsai-4B-gguf)、[Ternary-Bonsai-2-27B-gguf](https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf) | 検索モデル | `setup-search-models.ps1`（`tools\models`） | Apache-2.0 |
| [Override-6/Ternary-Bonsai-2-27B-abliterated-gguf](https://huggingface.co/Override-6/Ternary-Bonsai-2-27B-abliterated-gguf) | 検索の代理リーダー（PTQ1_0） | `setup-search-models.ps1` | Apache-2.0 |
| [mradermacher/Qwen3.5-4B-heretic-GGUF](https://huggingface.co/mradermacher/Qwen3.5-4B-heretic-GGUF)、[Qwen3-1.7B-heretic-GGUF](https://huggingface.co/mradermacher/Qwen3-1.7B-heretic-GGUF)、[Qwen3-0.6B-Heretic-GGUF](https://huggingface.co/mradermacher/Qwen3-0.6B-Heretic-GGUF) | 検索モデル | `setup-search-models.ps1` | Apache-2.0 |
| チェックポイント（yiffInHell など）、LoRA | Civitai などから入手 | 事前準備（手動） | 配布ページで確認（生成物や商用利用に条件があることが多い） |

- 例外として、Chroma1-HD の公式ワークフロー `ComfyUI_Chroma1-HD_T2I-workflow.json`（Apache-2.0、[lodestones/Chroma1-HD](https://huggingface.co/lodestones/Chroma1-HD)）は参照用に `workflows/reference/` へ無改変で同梱しています（出典は同フォルダの README）。
- ComfyUI_IPAdapter_plus（GPL-3.0）は ComfyUI のプロセスに読み込まれるノードです。このリポジトリはそのコードを含まず、import もしません（API 形式のワークフロー JSON でノード名を指定するだけです）。
- LM_Connect はリポジトリにライセンスの表示がありません。このリポジトリは再配布せず、利用者の環境へ clone するだけです。
- 生成物の扱いは、使用したチェックポイント・LoRA・LLM の規約と、公開先の規約に従ってください。
