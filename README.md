# local-agent: 日本語プロンプト → furry 静止画（LangGraph × ComfyUI × LM Studio）

LAN 内の別の PC やスマートフォンのブラウザから、日本語の指示（任意で参照画像 0〜2 枚）を送ると、
Windows 機の上で次の順に処理して静止画を返すローカルエージェントです。

1. [agent-chat-ui](https://github.com/langchain-ai/agent-chat-ui)（LangGraph 公式 UI）が入力を受け取る
2. LangGraph のグラフが ComfyUI にワークフローを投入する
3. ComfyUI のワークフローが LM Studio の LLM（Huihui Qwen3.8 27B Abliterated）で Danbooru / e621 タグを作る
4. LLM を LM Studio から unload してから、furry 系 SDXL チェックポイント（yiffInHell）で画像を生成する
5. 画像を ComfyUI の output とリポジトリの `outputs/` に保存し、同じ画像をチャットに表示する

クラウド API は使いません。すべてローカルで動きます。

仕様: [AGENTS.md](AGENTS.md)（全体・UI・待受）、[docs/lmstudio-comfyui-workflow-design.md](docs/lmstudio-comfyui-workflow-design.md)（ComfyUI と LM Studio の連携）

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
| ディスク | 空き 40GB 以上（LLM 16.5GB + 再量子化版 13GB + チェックポイント 7GB + 依存） |

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

### 手動で設定する場合

スクリプトを使わない場合は、GUI で次を設定すれば同じ状態になります。

- LM Studio → Developer → Server Settings: ポート 1234、**Serve on Local Network をオフ**、Just-in-Time Model Loading をオン、
  Auto unload unused JIT loaded models をオン（TTL 300 秒）。ランタイム（llama.cpp）を最新に更新。
- LM Studio → My Models → 使うモデルの既定値: Context Length 4096、GPU Offload 約 45%、Flash Attention オン、
  Max Concurrent Predictions 1、Thinking（Reasoning）オフ、Temperature 0.4。
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
| 2 | `.\scripts\start-comfyui.ps1`（または Comfy Desktop でインスタンスを起動） | `127.0.0.1:8188` |
| 3 | `.\scripts\start-langgraph.ps1`（`langgraph dev --host 0.0.0.0 --port 2024`） | `0.0.0.0:2024` |
| 4 | `.\scripts\start-ui.ps1`（`-HostAddress <IP>` で接続先を明示可） | `0.0.0.0:3000` |

- agent-chat-ui は `NEXT_PUBLIC_API_URL=http://<LAN IP>:2024` を **ビルド時に** 埋め込みます。`localhost` にすると他ホストのブラウザは自分自身へ接続してしまうためです。
  LAN IP が変わったら `start-ui.ps1` が自動で再ビルドします。
- Comfy Desktop は再起動するとダッシュボードに戻り、サーバを自動では起動しません。`start-comfyui.ps1` は Desktop と同じインストール・モデル・入出力先でサーバだけを起動します（Desktop は閉じておいてください）。
- モデルの事前ロードは不要です。各生成の最初に LLM が JIT ロードされ、タグ生成後に unload されます。
- 止めるときは各ウィンドウで Ctrl+C を押します。

## 4. 使い方

- テキストだけ: Empty Latent、denoise 1.0、832×1216、steps 28、cfg 5.5、euler_ancestral / normal。
- 参照画像 1〜2 枚（PNG / JPEG / WebP / GIF）: 1 枚目を VAE Encode して img2img（denoise 0.45、構図を保持）。参照画像は Vision でタグ化して指示に連結します。
- 進捗はチャットの 1 つのメッセージが更新されます（受付 → 投入 → タグ生成完了（positive / negative を表示）→ 画像）。
- 1 回の生成は 10 分でタイムアウトします。複数送っても、ComfyUI のキューが空くまで次は投入しません。
- 生成画像は ComfyUI の `output\furry_ja\` と、リポジトリの `outputs\`（同名の `.json` に prompt_id / seed / タグ）に保存されます。

確認済み構成での所要時間の目安:

| 入力 | 所要時間 |
|---|---|
| テキストのみ | 約 3.5〜7 分 |
| 参照画像 1 枚 | 約 7.5 分 |
| 参照画像 2 枚 | 約 8.5 分 |

## 5. 設定（`.env`）

| キー | 既定 | 説明 |
|---|---|---|
| `COMFYUI_URL` | `http://127.0.0.1:8188` | LangGraph から見た ComfyUI |
| `CKPT_NAME` | `yiffInHell_yihVANTABLACK.safetensors` | 使うチェックポイント（実行時にワークフローの値を上書き） |
| `COMFYUI_TIMEOUT_S` | `600` | 1 回の生成の待ち時間上限 |
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
src/furry_agent/                      LangGraph のグラフ、ComfyUI クライアント、入力解析
comfyui_nodes/furry_ja/               ComfyUI カスタムノード（split / ckpt）
workflows/furry_ja_api.json           API 形式。LangGraph が読む（ノード ID は設計書 §4.1）
workflows/furry_ja.json               UI 形式。ComfyUI で開ける（ノードのタイトル = ノード ID）
prompts/system_furry_tags.txt         タグ生成の system prompt
prompts/system_vision_caption.txt     参照画像タグ付けの system prompt
scripts/setup*.ps1                    セットアップ（scripts/lib/common.ps1 が共通処理）
scripts/start-*.ps1, doctor.ps1       起動と確認
scripts/open-firewall.ps1             ファイアウォール（管理者）
scripts/build_workflows.py            workflows/ を prompts/ から生成
agent-chat-ui/                        公式 UI（langchain-ai/agent-chat-ui@cf72cb0、画像表示の最小変更あり）
tests/                                pytest（Python と PowerShell スクリプトの両方）
outputs/  logs/  tools/  artifacts/   実行時に生成（git 管理外）
```

### ワークフローのノード

| ID | クラス | 役割 |
|---|---|---|
| `llm_backend` | LMConnectLMStudioBackend | `http://127.0.0.1:1234/v1`、auto-eject on、thinking off |
| `llm_backend_vision` | LMConnectLMStudioBackend | Vision 用。auto-eject off（直後の `prompt_node` で 27B を再ロードしないため） |
| `user_prompt` | PrimitiveStringMultiline | 日本語指示（LangGraph が書き換え） |
| `ref_image` / `ref_image_2` | LoadImage | 参照画像（参照なしの実行では削除） |
| `vision` | LMConnectVision | 参照画像をタグ化。長辺 768 |
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

### UI の変更点

agent-chat-ui は AI メッセージのテキスト部分しか描画しないため、グラフが返す画像ブロック
`{"type": "image", "mimeType": "image/png", "data": <base64>}` を描画する最小限の変更を
`agent-chat-ui/src/components/thread/messages/ai.tsx` に加えています。

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
| 10 分でタイムアウト | 同上。参照画像を 1 枚にする |
| タグの前に思考文が出る / 遅い | LM Studio のモデル既定値で Thinking がオフか確認（`setup-lmstudio.ps1` を再実行） |
| 参照画像を送っても説明が空 | mmproj がモデルと同じフォルダにあるか確認 |
| `missing tensor 'blk.64...'` でロードできない | LM Studio のランタイムが古い。`lms runtime update --all` |
| `langgraph dev` が `UnicodeDecodeError: 'cp932'` で落ちる | `start-langgraph.ps1` から起動する（`PYTHONUTF8=1` を設定します） |

## 既知の対象外・制約

- **動画入力は対象外**: ComfyUI-VideoHelperSuite（VHS）を前提にした経路は未実装です。動画を送るとチャットにその旨を返します（在庫の agent-chat-ui も動画の添付を受け付けません）。
- IP-Adapter は使いません。
- 認証なし。LAN 内の開発用途のみ。

## ライセンス

このリポジトリのコードは MIT または Apache-2.0（[LICENSE-MIT](LICENSE-MIT)、[LICENSE-APACHE](LICENSE-APACHE)）です。
`agent-chat-ui/` は upstream の MIT ライセンスです。LM_Connect、llama.cpp、各モデルはセットアップ時や事前準備で各配布元から取得するもので、それぞれのライセンス・規約に従います。
