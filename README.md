# local-agent: 日本語プロンプト → furry 静止画（LangGraph × ComfyUI × LM Studio）

LAN 内の別の PC やスマートフォンのブラウザから、日本語の指示（任意で参照画像 0〜4 枚。画風・ポーズ・キャラクターなどの役割付き）を送ると、
Windows 機の上で次の順に処理して静止画を返すローカルエージェントです。

1. [agent-chat-ui](https://github.com/langchain-ai/agent-chat-ui)（LangGraph 公式 UI）が入力を受け取る
2. LangGraph のグラフが画像の役割を決め、登録済みのワークフローテンプレートを選んで ComfyUI に投入する
3. ComfyUI のワークフローが LM Studio の LLM（Huihui Qwen3.8 27B Abliterated）で Danbooru / e621 タグを作る
4. LLM を LM Studio から unload してから、furry 系 SDXL チェックポイント（yiffInHell）で画像を生成する（参照画像は IP-Adapter / ControlNet、任意で LoRA）。
   `.env` の `COMFY_MODEL_FAMILY=flux` にすると、LLM が英語の説明文を作り、Flux 系の Chroma1-HD で生成する（「4. 使い方 › Chroma1-HD」）
5. 画像を ComfyUI の output とリポジトリの `outputs/` に保存し、同じ画像をチャットに表示する

クラウド API は使いません。すべてローカルで動きます。

変更履歴: [CHANGELOG.md](CHANGELOG.md)

仕様: [AGENTS.md](AGENTS.md)（全体・UI・待受）、[docs/lmstudio-comfyui-workflow-design.md](docs/lmstudio-comfyui-workflow-design.md)（ComfyUI と LM Studio の連携）、[docs/multi-image-reference-work-instruction.md](docs/multi-image-reference-work-instruction.md)（複数参照画像。調査結果と設計との差分を含む）、[docs/chroma-hd-support-work-instruction.md](docs/chroma-hd-support-work-instruction.md)（Chroma1-HD。事前確認の結果と設計との差分を含む）

```
他ホストのブラウザ ──> agent-chat-ui   http://<LAN IP>:3000
                         │ ブラウザから直接
                         ▼
                   LangGraph       http://<LAN IP>:2024   graph id: agent
                         │ ComfyUI HTTP API のみ
                         ▼
                   ComfyUI         http://127.0.0.1:8188  （ループバックのみ）
                         │ LM Connect ノード（OpenAI 互換 API）
                         ▼
                   LM Studio       http://127.0.0.1:1234/v1（ループバックのみ）
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

個別に実行することもできます: `.\scripts\setup-lmstudio.ps1`、`.\scripts\setup-comfyui.ps1`、`.\scripts\open-firewall.ps1`（管理者）。
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
.\scripts\start-all.ps1      # ComfyUI / LangGraph / agent-chat-ui を別ウィンドウで起動（起動済みのものは飛ばす）
.\scripts\doctor.ps1         # 設定と待受を確認（NG があれば終了コード 1）
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
langgraph.json                        graphs.agent -> src/furry_agent/graph.py:graph
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

## 8. ログ

- `logs\furry_agent.log`（LangGraph 側）: 投入（prompt_id / seed）、タグ、`ckpt gate: LM Studio unloaded=[True]`、
  `KSampler started ... LM Studio unloaded at checkpoint load=[True]`、`eject verified`、保存先。
- ComfyUI のコンソール: `[LM Connect] Eject sonucu`、`[furry_ja] split mode=json|fallback`、
  `[furry_ja] LM Studio verified unloaded before checkpoint/KSampler`。

## 9. 開発

```powershell
uv sync
uv run pytest                                  # Python と PowerShell スクリプトのテスト
uv run python scripts\build_workflows.py       # prompts\ を変えたら workflows\ を再生成
```

`.ps1` は UTF-8（BOM 付き）で保存してください。Windows PowerShell 5.1 は BOM の無いファイルを ANSI として読み、日本語を含む行で構文エラーになります（テストで確認しています）。

## 10. トラブルシューティング

| 症状 | 対処 |
|---|---|
| `doctor.ps1` で custom nodes が NG | `setup-comfyui.ps1` の後に ComfyUI を再起動したか確認 |
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
- 認証なし。LAN 内の開発用途のみ。

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
| [comfyanonymous/flux_text_encoders](https://huggingface.co/comfyanonymous/flux_text_encoders) | T5-XXL fp8（Chroma のテキストエンコーダ） | `setup-comfyui-chroma.ps1` | Apache-2.0（元の google/t5-v1_1-xxl） |
| [lodestones/Chroma1-HD](https://huggingface.co/lodestones/Chroma1-HD) | VAE（`ae.safetensors` として保存） | `setup-comfyui-chroma.ps1` | Apache-2.0 |
| Chroma1-HD 拡散モデル | [lodestones/Chroma1-HD](https://huggingface.co/lodestones/Chroma1-HD) または Civitai | 事前準備（手動） | Apache-2.0 |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) | `llama-quantize`（LLM の再量子化） | `setup-lmstudio.ps1`（`tools\`） | MIT |
| LLM（Huihui Qwen3.8 27B Abliterated と mmproj） | LM Studio でダウンロード | 事前準備（手動） | 配布ページで確認 |
| チェックポイント（yiffInHell など）、LoRA | Civitai などから入手 | 事前準備（手動） | 配布ページで確認（生成物や商用利用に条件があることが多い） |

- 例外として、Chroma1-HD の公式ワークフロー `ComfyUI_Chroma1-HD_T2I-workflow.json`（Apache-2.0、[lodestones/Chroma1-HD](https://huggingface.co/lodestones/Chroma1-HD)）は参照用に `workflows/reference/` へ無改変で同梱しています（出典は同フォルダの README）。
- ComfyUI_IPAdapter_plus（GPL-3.0）は ComfyUI のプロセスに読み込まれるノードです。このリポジトリはそのコードを含まず、import もしません（API 形式のワークフロー JSON でノード名を指定するだけです）。
- LM_Connect はリポジトリにライセンスの表示がありません。このリポジトリは再配布せず、利用者の環境へ clone するだけです。
- 生成物の扱いは、使用したチェックポイント・LoRA・LLM の規約と、公開先の規約に従ってください。
