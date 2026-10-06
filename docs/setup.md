# セットアップの詳細

README の「インストール」の補足です。各スクリプトが何をするか、オプション、手動で設定する場合、メモリと量子化の考え方をまとめます。
LLM（llama.cpp）と ComfyUI は、どちらもセットアップがこのリポジトリの `tools\` に入れて使います（LM Studio と既存の ComfyUI は要りません）。設計の経緯は [llamacpp-router-design.md](llamacpp-router-design.md)。

## 動作環境

| 項目 | 要件 |
|---|---|
| OS | Windows 10 / 11（スクリプトは Windows PowerShell 5.1 と PowerShell 7 の両方で動作確認）、macOS 15 以降の Apple silicon（下の「macOS」） |
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

- **LLM**（Huihui Qwen3.8 27B Abliterated）は `setup-llm.ps1` が用意します。指定は要りません（下の「モデルの探し方」）。
  LM Studio のモデルフォルダに IQ3_M（以前のこのプロジェクトが作ったもの）があればそれを使い、無ければ Q4_K_S と mmproj（約 16.5GB）を取得して IQ3_M に再量子化します。
- **チェックポイント** **Yiff in Hell – VANTABLACK**（`yiffInHell_yihVANTABLACK.safetensors`、Illustrious 系 SDXL）は
  [Civitai](https://civitai.com/models/1570986) 等から入手します。次のどれかで ComfyUI に渡します。
  - セットアップ後に `tools\comfyui\models\checkpoints` に置く
  - 以前の ComfyUI（Comfy Desktop、`Documents\ComfyUI\models`）のモデルフォルダにあれば、セットアップが自動で取り込む（ハードリンク）。ほかの場所なら `-ModelsDir <models フォルダ>`
  - `-CheckpointUrl <URL>` でダウンロードする
  ほかの画像モデル（yiffInHell METALLIC TETRA / XXX-TENDED V2.0、Rekemono、Indigo Furry Mix XL、Indigo Furry Mix Anima、Wulver）も同じように置きます。一覧とファイル名は `config/host_models.json`。置いたモデルは画面の一覧で選べるようになり、置いていないモデルは「使えない」と表示されます。Krea 2 / Anima のテキストエンコーダと VAE は `.\scripts\setup-image-models.ps1` が Hugging Face から取得します。
- （任意）**Chroma1-HD** を使う場合は、[lodestones/Chroma1-HD](https://huggingface.co/lodestones/Chroma1-HD) の拡散モデル
  （BF16 約 17.8GB。Civitai 配布名 `chroma_v10HD.safetensors`）を `models\diffusion_models` か `models\checkpoints` に置き、
  セットアップ後に `.\scripts\setup-comfyui-chroma.ps1` を実行する（T5 と VAE を取得し、拡散モデルを fp8 に変換。[usage.md › 画像モデル](usage.md#画像モデル系統ごとの違い)）。

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
| `-ModelsDir <path>[,<path>]` | 自動では探さない場所にあるモデルフォルダ（`checkpoints\`、`loras\` などを含むフォルダ）。使うモデルを `tools\comfyui\models` に取り込む |
| `-CheckpointUrl <URL>` | 既定の画像モデル（`config/host_models.json`）のチェックポイントをダウンロードする |
| `-Torch auto`（既定） / `rocm` / `cuda` / `cpu` | ComfyUI の PyTorch。auto は GPU の名前から選ぶ（Radeon → ROCm、NVIDIA → CUDA、ほか → CPU） |
| `-SourceModel <GGUF>` | 自動では探さない場所にある LLM のファイルを使う（ハードリンク） |
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
| 元のモデル | `config\llm_model.json` の GGUF（Q4_K_S）と mmproj。LM Studio のフォルダ、Hugging Face のキャッシュ、`-SourceModel` にあればハードリンクし、無ければ `hf download` で Hugging Face のキャッシュに取得してハードリンクする。固定した SHA-256 と照合する | `tools\models\llm` |
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
| モデルの置き場 | `tools\comfyui\models` だけ（外部フォルダの参照はしない）。使うモデル（`config/host_models.json` の画像モデルとその LoRA・テキストエンコーダ・VAE、参照画像用、Chroma 用）を以前の ComfyUI のモデルフォルダと `-ModelsDir` から取り込む（下の「モデルの探し方」） |
| `.env` | `COMFYUI_MAIN_DIR`、`COMFYUI_PYTHON`、`COMFYUI_CUSTOM_NODES_DIR`、`COMFYUI_MODELS_DIR`（以前の版が書いた `COMFYUI_EXTRA_MODEL_PATHS` と `extra_model_paths.yaml` は空にして削除する） |
| チェックポイント | 画像モデルのファイルがあるか確認し、無いものを一覧で知らせる（`-CheckpointUrl` なら既定のモデルを取得） |

**参照画像用のノードとモデル**（`scripts\setup-comfyui-refs.ps1`。`setup.ps1 -SkipReferenceModels` で省略可）

テキストだけの生成と、修正する元画像 1 枚の img2img はこれが無くても動きます。画風・ポーズ・キャラクターの参照に使います。
`tools\comfyui\models`、以前の ComfyUI のモデルフォルダ、Hugging Face のキャッシュのどこかに同じファイルがあれば取得しません。

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
| `setup-search-models.ps1` | 検索モデル 8 つ（約 20GB）を Hugging Face のキャッシュ経由で取得し（`hf download` で取得済みなら取得しない）、カタログの SHA-256 と照合する（中断しても再開できる）。`BONSAI_MODELS_DIR` を保存する | `tools\models` |
| `probe-bonsai.ps1` | 各モデルを 1 回ずつ起動し、タスクごとに短いテストで検証する。起動時間・メモリ・生成速度・合否を記録する | `tools\bonsai\rank.json` |

検索モデルは llama.cpp（`setup-llamacpp.ps1` と同じ build）で、検索のあいだだけ起動します。27B のルータには入れません（推論モデルとして選べる Bonsai 2 27B abliterated だけは、同じファイルをルータのプリセットの節にもします）。

## macOS（Apple silicon、v0.12.0）

Windows の `scripts\*.ps1` と同じ手順を `scripts/*.sh`（bash、`scripts/lib/common.sh`）で行います。何度実行しても安全です。確認済み構成: Apple M4・24GB・macOS 15.5。

```bash
brew install git uv node huggingface-cli       # Homebrew（https://brew.sh）。cirka をビルドするなら Rust も
scripts/setup.sh                                # すべて
scripts/setup.sh --image-ids yiffinhell-vantablack \
  --search-models qwen3-1.7b-heretic,bonsai-4b,bonsai-2-27b-abliterated --skip-refs   # ディスクが少ない Mac
```

| スクリプト | 内容 |
|---|---|
| `setup-llamacpp.sh` | PrismML fork の macOS arm64（Metal）版を取得し、GitHub のリリースの SHA-256 と照合する（`--source` で Metal ビルド）。`LLM_SERVER` / `BONSAI_LLAMA_SERVER` |
| `setup-tor.sh` | Homebrew の tor。`TOR_EXE`。設定は Windows と同じ `tools/tor/torrc` |
| `setup-search-models.sh` | 検索モデル（`--models` で絞る）を Hugging Face のキャッシュ経由で取得してリンクし、SHA-256 を照合 |
| `setup-mlx.sh` | `tools/mlx/.venv` に mlx-lm。`--models <id>` で、`config/host_models.json` の `mlx` に書いた MLX 版を取得する（Qwen3.8 27B abliterated の 4bit 版は 14GB） |
| `setup-llm.sh` | ルータのプリセット。MLX 版があるモデルは `engine = mlx`（MLX 優先）、無ければ GGUF を llama-server（GPU にすべての層）。Qwen の GGUF は `--source-model` / LM Studio のフォルダ / `--download`（取得して再量子化）。`.env` の `LLM_*`（`LLM_MODEL` は入っているモデルのうち一覧の先頭。画像のタグ生成のモデル）と `LLM_ENGINE` を書き、必要ならワークフローを再生成する |
| `setup-comfyui.sh` | ComfyUI（Windows と同じコミット）、Python 3.12 の venv、PyPI の PyTorch（MPS）、LM_Connect、`custom_nodes/furry_ja`（シンボリックリンク）。続けて `setup-image-models.sh` |
| `setup-image-models.sh` | 画像モデルのファイル（`--ids` で絞る）。以前の ComfyUI のモデルフォルダ（`~/Documents/ComfyUI/models` など）と `--models-dir` からリンクし、Krea 2 / Anima のテキストエンコーダと VAE を取得する |
| `setup-comfyui-refs.sh` | 参照画像用のノードとモデル（約 5GB） |
| `setup-sandbox.sh` | docker CLI（Docker Desktop、Rancher Desktop の `~/.rd/bin/docker` など）が Linux のエンジンに届けば `python:3.12-slim` を取得 |

置いていないモデル（チェックポイントは Civitai から手で入手）は、画面の一覧で使えないと表示されます。24GB の Mac では Qwen3.8 27B（IQ3_M 12.7GB）と SDXL は同時に載らないので、Windows と同じく順番に載せ替えます（ComfyUI の eject）。空きディスクが 20GB 程度の確認機では、推論とタグ生成に Bonsai 2 27B abliterated（5.9GB）、画像に yiffInHell VANTABLACK だけを入れました。

## モデルの探し方（指定は不要）

セットアップは、使うモデルを次の順で探します。見つかったものはハードリンク（別のドライブならコピー）で `tools\` の下に置くので、元の場所を消しても動き続け、同じドライブならディスクも増えません。どこにも無いものだけをダウンロードします。何度実行しても、取得済みのものは取得しません。

| モデル | 探す場所（この順） | 無いとき |
|---|---|---|
| 27B（IQ3_M と mmproj） | `tools\models\llm` → LM Studio のモデルフォルダ（`%USERPROFILE%\.lmstudio\models`、または LM Studio の設定のダウンロード先）→ `-SourceModel` | 元の Q4_K_S を下の Hugging Face の手順で取得して再量子化 |
| Hugging Face のファイル（27B の元ファイルと mmproj、検索モデル、参照画像用、Chroma の T5 と VAE） | 置き場所 → Hugging Face のキャッシュ（`HF_HUB_CACHE`、`HF_HOME\hub`、`%USERPROFILE%\.cache\huggingface\hub` の順。リポジトリとパスで探し、無ければ同じ SHA-256 の blob を全リポジトリから探す） | `hf download` でキャッシュに取得してからハードリンク（`hf` が無ければ uv で huggingface_hub の `hf` を動かす。`HF_TOKEN` などの設定もそのまま効く）。`hf` を動かせないときだけ直接ダウンロード |
| ComfyUI のモデル（チェックポイント、LoRA、Chroma の拡散モデル、ControlNet など） | `tools\comfyui\models` → 以前の ComfyUI のモデルフォルダ（Comfy Desktop のモデルの置き場、`%LOCALAPPDATA%\Comfy-Desktop\ComfyUI-Shared\models`、`Documents\ComfyUI\models`）→ `-ModelsDir` | Hugging Face にあるものは上の手順で取得。チェックポイントと LoRA は手で置く（`-CheckpointUrl` も可） |

`hf download` で先に取得しておいたモデルも、セットアップが取得したモデルも、同じ Hugging Face のキャッシュに 1 つだけ置かれます。再量子化した IQ3_M は SHA-256 を固定していて（`config\llm_model.json` の `quantized`）、取り込んだときと作ったときに照合します。

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
