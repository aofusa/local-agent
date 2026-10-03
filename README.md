# local-agent: 日本語プロンプト → furry 静止画（LangGraph × ComfyUI × LM Studio）

他ホストのブラウザから agent-chat-ui に日本語（任意で参照画像 0〜2 枚）を送ると、この端末の LangGraph が ComfyUI のワークフローを投入し、
ComfyUI が LM Studio の Qwen3.8 27B でタグを作り、LLM を unload してから yiffInHell で静止画を生成します。
画像は ComfyUI の output とリポジトリの `outputs/` に保存され、同じ画像がチャットの応答として表示されます。

仕様は [AGENTS.md](AGENTS.md) と [docs/lmstudio-comfyui-workflow-design.md](docs/lmstudio-comfyui-workflow-design.md) を参照してください。

```
他ホストのブラウザ ──> agent-chat-ui  http://<この端末のLAN IP>:3000
                         │ (ブラウザから直接)
                         ▼
                   LangGraph  http://<この端末のLAN IP>:2024   graph id: agent
                         │ ComfyUI HTTP API のみ
                         ▼
                   ComfyUI    http://127.0.0.1:8188  (ループバックのみ)
                         │ LM Connect ノード (OpenAI 互換 API)
                         ▼
                   LM Studio  http://127.0.0.1:1234/v1  (ループバックのみ)
```

## 待受と公開範囲

| プロセス | 待受 | 公開範囲 |
|---|---|---|
| LM Studio | `127.0.0.1:1234` | この端末の ComfyUI だけ |
| ComfyUI | `127.0.0.1:8188` | この端末の LangGraph だけ |
| LangGraph (`langgraph dev`) | `0.0.0.0:2024` | LAN（Private プロファイル） |
| agent-chat-ui (`next start`) | `0.0.0.0:3000` | LAN（Private プロファイル） |

- 認証はありません（方式は未確定）。信頼できる LAN の中だけで使ってください。インターネットへ公開しないでください。
- クラウド API や LangSmith へのデプロイは使いません。

## 起動順

PowerShell で、リポジトリ直下から実行します。

1. **LM Studio** を起動する。Developer → Local Server が `127.0.0.1:1234` で動いていること（「Serve on Local Network」はオフ）。
   モデルは事前ロード不要です。ワークフローの最初の LLM 呼び出しで JIT ロードされ、タグ生成後に ComfyUI のノードが unload します。
2. **ComfyUI** を起動する。どちらか一方。

   ```powershell
   .\scripts\start-comfyui.ps1   # Comfy Desktop と同じインストール・venv・モデル・入出力先でサーバだけを起動
   ```

   または Comfy Desktop を開き、ダッシュボードで「ComfyUI」インスタンスを起動する（起動引数は `--listen 127.0.0.1 --port 8188 --enable-manager` に設定済み）。
   Comfy Desktop は再起動するとダッシュボードに戻り、サーバを自動では起動しません。
3. **LangGraph** を起動する。

   ```powershell
   .\scripts\start-langgraph.ps1
   ```

4. **agent-chat-ui** を起動する（初回と LAN IP が変わったときはビルドが走ります）。

   ```powershell
   .\scripts\start-ui.ps1                 # LAN IP を自動検出
   .\scripts\start-ui.ps1 -HostAddress <LAN IP>   # 明示する場合
   ```

5. 初回だけ、管理者 PowerShell でファイアウォールを開ける（TCP 2024 / 3000、Private のみ）。

   ```powershell
   .\scripts\open-firewall.ps1
   ```

6. 他ホストのブラウザで `http://<この端末のLAN IP>:3000` を開き、日本語を送る。
   Deployment URL / Assistant ID の入力画面は出ません（ビルド時に `NEXT_PUBLIC_API_URL=http://<LAN IP>:2024`、`NEXT_PUBLIC_ASSISTANT_ID=agent` を設定済み）。

`NEXT_PUBLIC_API_URL` は **他ホストのブラウザから届くアドレス** でなければなりません。`localhost` にすると、相手のブラウザは自分自身へ接続します。

## 使い方

- テキストだけ: Empty Latent、denoise 1.0、832×1216、steps 28、cfg 5.5、euler_ancestral / normal。
- 参照画像 1〜2 枚（PNG / JPEG / WebP / GIF）: 1 枚目を VAE Encode して img2img（denoise 0.45）。全参照画像を LM Studio の Vision でタグ化し、指示に連結します。
- 進捗はチャットの 1 つのメッセージが更新されていきます（受付 → 投入 → タグ生成完了（positive/negative を表示）→ 画像）。
- 1 回の生成は 10 分でタイムアウトします。同時に複数を送っても、ComfyUI のキューが空くまで次は投入しません。

## この端末での設定（実施済み）

| 対象 | 設定 |
|---|---|
| ComfyUI カスタムノード | `<ComfyUI のベースフォルダ>\custom_nodes\LM_Connect`（eedali/LM_Connect）。依存は requests / Pillow / numpy のみ。llama-cpp-python は入れていません |
| ComfyUI カスタムノード | `custom_nodes\furry_ja` → このリポジトリの `comfyui_nodes\furry_ja` へのジャンクション |
| Comfy Desktop | `%APPDATA%\Comfy Desktop\installations.json` の launchArgs を `--listen 127.0.0.1 --port 8188 --enable-manager` に変更（元は `--port 8000`）。変更前のファイルは `artifacts/installations.json.orig`（git 管理外）に控えてあります |
| LM Studio サーバ | `127.0.0.1` で待受（元は `0.0.0.0`）、JIT ロード有効、JIT モデル TTL 300 秒（eject の保険） |
| LM Studio ランタイム | llama.cpp Vulkan / CPU を 2.51.0 に更新（2.13.0 は Qwen3.8 の MTP 層付き GGUF を読めない） |
| LLM | `huihui-qwen3.8-27b-abliterated@iq3_m`（下記）。既定設定: context 4096、GPU offload 0.45、flash attention、並列 1、thinking 無効、temperature 0.4 |
| チェックポイント | `yiffInHell_yihVANTABLACK.safetensors`（`.env` の `CKPT_NAME`） |

### LLM を IQ3_M に再量子化した理由

ダウンロード済みの Huihui Qwen3.8 27B Abliterated（Unsloth UD-DW Q4_K_S、15.6GB + mmproj 0.9GB）は、この端末では動作が実用になりませんでした。

- OS から見える RAM は 23.1GB。何もロードしていない状態で利用可能なのは約 15GB でした（dwm が共有 GPU メモリを約 4.5GB 使用）。
- Q4_K_S はロードできても空きが無くなり、ページングでプロンプト処理が 5 tok/s 程度、15 分待っても応答が終わりませんでした。
- Vulkan 側は約 7〜9GB 以上の割り当てで `ErrorOutOfDeviceMemory` になります（専用 VRAM は 512MB）。

そこで同じモデルを `llama-quantize --allow-requantize ... IQ3_M` で 12.2GB に再量子化し、
`%USERPROFILE%\.lmstudio\models\huihui-ai\Huihui-Qwen3.8-27B-abliterated-IQ3_M-GGUF\` に置きました（mmproj は元ファイルへのハードリンク、MTP 層は保持）。
元の Q4_K_S は削除していません（`@q4_k_s` として LM Studio に残っています）。実測は初回ロード込みで約 45 秒、生成約 1.6 tok/s（MTP 投機デコード有効）。

UMA の GPU 割当（Armoury Crate の VRAM 設定）を増やしても物理 RAM の総量は増えないため、この問題の解決にはなりません。
RAM に余裕がある環境なら、ワークフローのモデル名を `huihui-qwen3.8-27b-abliterated@q4_k_s` に戻せます（`scripts/build_workflows.py` の `LMSTUDIO_MODEL`）。

### UMA の注記

ROG Ally X は CPU と GPU がメモリを共有します。27B とチェックポイントは同時に載りません。
そのためワークフローは「LLM がタグを返す → LM Studio から unload → チェックポイント読み込み → KSampler」の順に固定し、
LangGraph は投入前と完了後に ComfyUI の `/free` を呼んで、前回のチェックポイントを解放してから次の LLM ロードに進みます。
UMA の GPU 割当自体は実装対象外です。生成中は他の重いアプリ（ブラウザのタブ、ゲーム等）を閉じてください。

## 構成

```
AGENTS.md / docs/                     仕様
langgraph.json                        graphs.agent -> src/furry_agent/graph.py:graph
src/furry_agent/                      LangGraph のグラフ、ComfyUI クライアント、入力解析
comfyui_nodes/furry_ja/               ComfyUI カスタムノード（split / ckpt）
workflows/furry_ja_api.json           API 形式。LangGraph が読む（ノード ID は設計書 §4.1）
workflows/furry_ja.json               UI 形式。ComfyUI で開ける（ノードのタイトル = ノード ID）
prompts/system_furry_tags.txt         タグ生成の system prompt
prompts/system_vision_caption.txt     参照画像タグ付けの system prompt
scripts/build_workflows.py            上 2 つの JSON を prompts/ から生成
scripts/start-comfyui.ps1, start-langgraph.ps1, start-ui.ps1, open-firewall.ps1
agent-chat-ui/                        公式 UI（langchain-ai/agent-chat-ui@cf72cb0）
outputs/                              生成画像の複製と JSON メタデータ（git 管理外）
logs/furry_agent.log                  LangGraph 側の実行ログ（git 管理外）
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
| `split` | FurryJaSplitTags | 最初の `{` から最後の `}` を `json.loads`（不正なバックスラッシュエスケープは除去）。失敗時はリトライせず生文字列を positive、固定の画質タグを negative。Illustrious 向けの品質タグを先頭に付与。LM Connect がエラー文字列を返した場合は実行を失敗させる |
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

## ログで確認できること

- `logs/furry_agent.log`: 投入（prompt_id / seed）、タグ、`ckpt gate: LM Studio unloaded=[True]`、
  `KSampler started ... LM Studio unloaded at checkpoint load=[True]`、`eject verified`、保存先。
- ComfyUI のログ（`<ComfyUI のベースフォルダ>\user\comfyui_8188.log`）: `[LM Connect] Eject sonucu`、
  `[furry_ja] split mode=json|fallback`、`[furry_ja] LM Studio verified unloaded before checkpoint/KSampler`。

## 開発

```powershell
uv sync
uv run pytest
uv run python scripts/build_workflows.py   # prompts/ を変えたら再生成
```

## 既知の対象外・制約

- **動画入力は対象外**: ComfyUI-VideoHelperSuite（VHS）が未導入のため。動画を送るとチャットにその旨を返します。
  （agent-chat-ui の在庫版も動画ファイルの添付を受け付けません。）
- IP-Adapter は使いません（フェーズ 1 の必須ではないため）。
- 認証なし。LAN 内の開発用途のみ。
- LLM はプロンプト処理 5〜10 tok/s、生成 1.2〜1.6 tok/s です。実測の所要時間（ComfyUI の 10 分タイムアウト内）:

  | 入力 | 所要時間 |
  |---|---|
  | テキストのみ | 約 3.5〜7 分 |
  | 参照画像 1 枚（img2img） | 約 7.5 分 |
  | 参照画像 2 枚 | 約 8.5 分 |

  メモリに余裕が無いほど遅くなります。生成中はブラウザのタブなど他のアプリを減らしてください。
- ComfyUI は一度生成すると、`/free` 後も ROCm ランタイムが共有 GPU メモリを約 2GB 保持します。そのため LLM の GPU offload は 0.45 にしています（0.5 以上は 2 回目以降のロードで `ErrorOutOfDeviceMemory`）。
