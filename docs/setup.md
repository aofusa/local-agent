# セットアップの詳細

README の「インストール」の補足です。各スクリプトが何をするか、オプション、手動で設定する場合、メモリと量子化の考え方をまとめます。
LLM（llama.cpp）と ComfyUI は、どちらもセットアップがこのリポジトリの `tools\` に入れて使います（LM Studio と既存の ComfyUI は要りません）。設計の経緯は [llamacpp-router-design.md](llamacpp-router-design.md)。

## 動作環境

| 項目 | 要件 |
|---|---|
| OS | Windows 10 / 11（スクリプトは Windows PowerShell 5.1 と PowerShell 7 の両方で動作確認） |
| メモリ | 24GB 級以上。27B の LLM と SDXL は同時に載らないため、ワークフローが順番に載せ替えます |
| GPU | Vulkan が使える GPU（llama.cpp）と、PyTorch が使える GPU（ComfyUI。AMD Radeon は ROCm、NVIDIA は CUDA。無ければ CPU） |
| ディスク | 空き 70GB 以上（LLM 16.5GB + 再量子化版 13GB + ComfyUI と PyTorch 約 10GB + チェックポイント 7GB + 参照画像用モデル約 6GB + 検索モデル約 20GB） |

確認済み構成: ROG Ally X（Ryzen AI Z2 Extreme / Radeon 890M / 共有メモリ 24GB）、Windows 11、
llama.cpp（PrismML fork `prism-b10754`、Vulkan）、ComfyUI 0.38（`tools\comfyui`、PyTorch 2.9.1 ROCm 7.2）。

## 事前準備（人の手で行うこと）

### ツール

PowerShell で実行します（winget の例）。

```powershell
winget install Git.Git
winget install astral-sh.uv
winget install OpenJS.NodeJS.LTS      # Node.js 20 以上
```

インストール後に PowerShell を開き直し、`git --version`、`uv --version`、`node --version` が通ることを確認してください。
CUI の cirka を自分でビルドする場合だけ、Rust も入れます（`winget install Rustlang.Rustup`。ビルド済みの zip を使うなら不要）。
Python（LangGraph 用と ComfyUI 用）は uv が自動で用意します。pnpm は `npx` 経由で使うので別途インストール不要です。
`curl.exe` は Windows 10 以降に同梱です。

スクリプトの実行を許可します（現在のユーザーのみ）。

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### モデル

- **LLM**（Huihui Qwen3.8 27B Abliterated の Q4_K_S と mmproj、約 16.5GB）は `setup-llm.ps1` が Hugging Face から取得します。
  すでに手元にある場合は `-SourceModel <GGUF のパス>` で指定すると、ダウンロードせずにハードリンクします（同じフォルダの `mmproj-model-bf16.gguf` も使います）。
- **チェックポイント** **Yiff in Hell – VANTABLACK**（`yiffInHell_yihVANTABLACK.safetensors`、Illustrious 系 SDXL）は
  [Civitai](https://civitai.com/models/1570986) 等から入手します。次のどれかで ComfyUI に渡します。
  - セットアップ後に `tools\comfyui\models\checkpoints` に置く
  - 既存の ComfyUI のモデルフォルダがあれば、`setup.ps1 -ModelsDir <models フォルダ>` でそのまま読ませる（コピーしません）
  - `-CheckpointUrl <URL>` でダウンロードする
  別のファイル名やモデルを使う場合は、`.env` の `CKPT_NAME` を変更します。
- （任意）**Chroma1-HD** を使う場合は、[lodestones/Chroma1-HD](https://huggingface.co/lodestones/Chroma1-HD) の拡散モデル
  （BF16 約 17.8GB。Civitai 配布名 `chroma_v10HD.safetensors`）を `models\diffusion_models` か `models\checkpoints` に置き、
  セットアップ後に `.\scripts\setup-comfyui-chroma.ps1` を実行する（T5 と VAE を取得し、拡散モデルを fp8 に変換。[usage.md › Chroma1-HD](usage.md#chroma1-hdモデルの切り替え)）。

### ネットワーク

- この PC が接続しているネットワークを **プライベート** にする（設定 → ネットワークとインターネット → プロパティ）。
  ファイアウォールの許可は Private プロファイルにだけ追加します。
- ファイアウォール設定の 1 回だけ、管理者権限（UAC の確認）が必要です。

> **注意**: このアプリに認証はありません。信頼できる LAN の中だけで使い、インターネットへ公開しないでください。
> 生成物と使用するモデルのライセンス・利用規約（成人向け表現を含む）は利用者の責任で確認してください。

## setup.ps1 のオプション

```powershell
.\scripts\setup.ps1 -OpenFirewall
```

初回は LLM のダウンロードと再量子化（20〜30 分）、PyTorch と ComfyUI の依存の取得に時間がかかります。`setup.ps1` は何度実行しても安全です（済んでいる手順は飛ばします）。

| オプション | 説明 |
|---|---|
| `-OpenFirewall` | TCP 2024 / 3000 の受信を Private プロファイルで許可（UAC が出ます） |
| `-ModelsDir <path>[,<path>]` | 既存のモデルフォルダ（`checkpoints\`、`loras\` などを含むフォルダ）を ComfyUI にそのまま読ませる |
| `-CheckpointUrl <URL>` | チェックポイント（`CKPT_NAME`）をダウンロードする |
| `-Torch auto`（既定） / `rocm` / `cuda` / `cpu` | ComfyUI の PyTorch。auto は GPU の名前から選ぶ（Radeon → ROCm、NVIDIA → CUDA、ほか → CPU） |
| `-SourceModel <GGUF>` | LLM の元ファイルを手元のファイルから使う（ハードリンク） |
| `-Quant IQ3_M`（既定） / `none` | LLM を再量子化するか（下の「メモリと LLM の量子化」） |
| `-GpuOffload 0.45`（既定） | LLM の層のうち GPU に置く割合（0〜1） |
| `-SkipLLM` / `-SkipComfyUI` | どちらかの設定を飛ばす |
| `-SkipReferenceModels` | 参照画像用のノードとモデル（約 6GB）を飛ばす |
| `-SkipSearch` | チャットタブの検索の準備（Tor、検索モデル約 20GB、probe）を飛ばす |
| `-SkipProbe` | 検索モデルの検証（probe、数分）だけを飛ばす |

個別に実行することもできます。各スクリプトの詳細は `Get-Help .\scripts\setup-llm.ps1 -Detailed` のように表示できます。

```powershell
.\scripts\setup-llamacpp.ps1        # llama.cpp（PrismML fork の Vulkan 版）を tools\llama-prism へ（SHA-256 照合。-FromSource でビルド）
.\scripts\setup-llm.ps1             # 27B を tools\models\llm へ取得・再量子化し、ルータのプリセットを書く
.\scripts\setup-comfyui.ps1         # ComfyUI を tools\comfyui へ（専用の venv と PyTorch）
.\scripts\setup-comfyui-refs.ps1    # 参照画像用のノードとモデル
.\scripts\setup-tor.ps1             # Tor Expert Bundle（SHA-256 確認）を tools\tor へ。TOR_EXE を .env へ
.\scripts\setup-search-models.ps1   # 検索モデル 8 つ（約 20GB、再開可、SHA-256 照合）を tools\models へ（-Verify で取得済みも再照合）
.\scripts\probe-bonsai.ps1          # 各モデルを 1 回ずつ起動して検証し、tools\bonsai\rank.json に順位を書く
.\scripts\open-firewall.ps1         # ファイアウォール（管理者）
```

チャットタブのコード実行（思考モード）を使う場合は、Docker（Docker Desktop または Docker Engine）を入れてから次を実行します。

```powershell
.\scripts\setup-sandbox.ps1         # -Rust で rust:1.88-slim も取得
```

`docker` コマンドがエンジンにつながればそのまま使います。つながらなければ Docker Desktop を起動し（終わったら止めます）、`python:3.12-slim` を取得して、ネットワークなし・読み取り専用・非 root でコンテナが動くことを確かめます。
`docker` コマンドが無いときは何も起動せずに終わります。そのときもチャットタブはコードを書いてファイルに残し、実行しなかった理由を返します。

## セットアップが行うこと

**開発環境**

- `.env.example` から `.env` を作る（端末固有の値はここにだけ書かれ、git には入りません）
- `uv sync`（LangGraph 用の Python 3.12 環境 `.venv`）
- `agent-chat-ui` で `pnpm install`

**llama.cpp と LLM**（`scripts\setup-llamacpp.ps1`、`scripts\setup-llm.ps1`）

| 設定 | 内容 | 置き場（git 管理外） |
|---|---|---|
| llama.cpp | PrismML fork の Windows Vulkan 版（`config\search_models.json` に固定した版）を取得し、SHA-256 を照合する。`--list-devices` で Vulkan デバイスを確認し、`LLM_SERVER` と `BONSAI_LLAMA_SERVER` を保存する。`-FromSource` なら `prism` ブランチを Vulkan でビルドする（Visual Studio の C++、CMake、Ninja、Vulkan SDK が必要） | `tools\llama-prism` |
| 元のモデル | `config\llm_model.json` の GGUF（Q4_K_S）と mmproj を Hugging Face から取得し、固定した SHA-256 と照合する（`-SourceModel` なら手元のファイルをハードリンク） | `tools\models\llm` |
| 再量子化 | `-Quant IQ3_M` のとき、同じ build の `llama-quantize` で `…-IQ3_M.gguf` を作る（20〜30 分）。元ファイルは残す | `tools\models\llm` |
| プリセット | ルータの設定。context 4096、GPU に置く層（GGUF の層数 × `-GpuOffload`）、flash attention、並列 1、mmap で読み込み、**thinking 無効**、temperature 0.4、repeat penalty 1.1（LM Studio の既定と同じ）、アイドル 300 秒で sleep（メモリを返す。eject の保険） | `tools\llm\models.ini` |
| `.env` | `LLM_SERVER`、`LLM_PRESET`、`LLM_PORT`、`LLM_URL`、`LLM_MODEL`（プリセットのモデル名 `qwen3.8-27b-abliterated`）、`LLM_CONTEXT`。ワークフローの接続先と違えば `workflows\` を再生成する | `.env`、`workflows\*.json` |

**ComfyUI**（`scripts\setup-comfyui.ps1`）

| 設定 | 内容 |
|---|---|
| 本体 | [Comfy-Org/ComfyUI](https://github.com/Comfy-Org/ComfyUI) を検証済みのコミット（v0.38.0-32）で `tools\comfyui` に clone する |
| Python | `tools\comfyui\.venv`（uv で Python 3.12）。PyTorch は `-Torch` に従う（Radeon は AMD の ROCm 7.2 Windows 版 `repo.radeon.com`、NVIDIA は CUDA 12.8、ほかは CPU）。続けて ComfyUI の `requirements.txt` |
| LM_Connect | [eedali/LM_Connect](https://github.com/eedali/LM_Connect) を `custom_nodes` に clone（検証済みコミットに固定）。依存は requests / Pillow / numpy のみ。**llama-cpp-python は入れません**（ComfyUI 内で GGUF を動かさない）。プロンプトと Vision のノードを、ルータの OpenAI 互換クライアントとして使う |
| furry_ja | `custom_nodes\furry_ja` → このリポジトリの `comfyui_nodes\furry_ja` へのジャンクション（eject、ckpt、split など） |
| モデルの置き場 | `tools\comfyui\models`。`-ModelsDir` を付けると、そのフォルダを `tools\comfyui\extra_model_paths.yaml` に書いて、そのまま読む |
| `.env` | `COMFYUI_MAIN_DIR`、`COMFYUI_PYTHON`、`COMFYUI_CUSTOM_NODES_DIR`、`COMFYUI_MODELS_DIR`、`COMFYUI_EXTRA_MODEL_PATHS` |
| チェックポイント | `CKPT_NAME` のファイルがあるか確認（`-CheckpointUrl` なら取得） |

**参照画像用のノードとモデル**（`scripts\setup-comfyui-refs.ps1`。`setup.ps1 -SkipReferenceModels` で省略可）

テキストだけの生成と、修正する元画像 1 枚の img2img はこれが無くても動きます。画風・ポーズ・キャラクターの参照に使います。
ComfyUI が読むフォルダ（`-ModelsDir` を含む）に同じファイルがあれば取得しません。

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
| `setup-search-models.ps1` | 検索モデル 8 つ（約 20GB）を Hugging Face から取得し、カタログの SHA-256 と照合する（中断しても再開できる）。`BONSAI_MODELS_DIR` を保存する | `tools\models` |
| `probe-bonsai.ps1` | 各モデルを 1 回ずつ起動し、タスクごとに短いテストで検証する。起動時間・メモリ・生成速度・合否を記録する | `tools\bonsai\rank.json` |

検索モデルは llama.cpp（`setup-llamacpp.ps1` と同じ build）で、検索のあいだだけ起動します。27B のルータには入れません。

## 手動で設定する場合

スクリプトを使わない場合は、次を行えば同じ状態になります。

- llama.cpp の llama-server（Vulkan 版）を用意し、次のようなプリセット（INI）を書いて、`llama-server --models-preset <ini> --models-max 1 --host 127.0.0.1 --port 8080` で起動する。

  ```ini
  version = 1

  [qwen3.8-27b-abliterated]
  model = C:/path/Huihui-Qwen3.8-27B-abliterated-IQ3_M.gguf
  mmproj = C:/path/mmproj-model-bf16.gguf
  ctx-size = 4096
  n-gpu-layers = 29
  flash-attn = on
  parallel = 1
  load-mode = mmap
  jinja = true
  reasoning-format = deepseek
  reasoning = off
  temp = 0.4
  repeat-penalty = 1.1
  sleep-idle-seconds = 300
  ```

- ComfyUI: `custom_nodes` に LM_Connect を clone し、ComfyUI の Python で `pip install requests Pillow numpy`、
  `comfyui_nodes\furry_ja` を `custom_nodes\furry_ja` にコピーまたはリンク。起動引数は `--listen 127.0.0.1 --port 8188 --cache-none`。
  `.env` の `COMFYUI_MAIN_DIR` と `COMFYUI_PYTHON` にその場所を書けば `start-comfyui.ps1` で起動できます。
- 参照画像を使う場合は、上の表のノードを `custom_nodes` に clone し、モデルを同じパスに置く。
- `.env` の `LLM_URL` と `LLM_MODEL`（プリセットの節の名前）を `$env:` にも入れて `uv run python scripts\build_workflows.py` を実行し、ワークフローを再生成。

## メモリと LLM の量子化

27B の LLM と SDXL チェックポイントは同時に載りません。ワークフローは
「LLM がタグを返す → ルータから unload → チェックポイント読み込み → KSampler」の順に固定し、
LangGraph は投入前と完了後（失敗時も）に ComfyUI の `/free` を呼んで、前回のチェックポイントを解放します。

確認済み構成（共有メモリ 24GB、OS から見える RAM 約 23GB）では、ダウンロードした Q4_K_S（15.6GB + mmproj）は
OS・画面表示・ComfyUI の常駐分と合わせると物理メモリに収まらず、ページングでほぼ停止しました。
そのため既定では、セットアップが同じモデルを **IQ3_M（約 12.7GB）に再量子化** して使います（元ファイルは残します）。

- GPU に置く層 0.45（29 / 64 層）: この iGPU では Vulkan が確保できる量に上限があり、さらに ComfyUI が一度生成すると ROCm ランタイムが約 2GB を保持し続けるためです。
- 読み込みは mmap です（プリセットの `load-mode = mmap`）。既定の読み込みでは CPU 側の重みが Vulkan の pinned メモリに置かれ、共有メモリの iGPU では `ErrorOutOfDeviceMemory` になりました。
- メモリに余裕がある環境（32GB 以上や dGPU）では `.\scripts\setup.ps1 -Quant none -GpuOffload 1` で Q4_K_S をそのまま使えます。
- UMA の GPU 割当（BIOS / Armoury Crate の VRAM 設定）を増やしても物理 RAM の総量は増えません。生成中はブラウザのタブなど他のアプリを減らしてください。
