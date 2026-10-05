# 構成

ファイルの配置、ワークフローのノード、UI の変更点、ログ、開発の手順です。全体の約束事は [AGENTS.md](../AGENTS.md)、各機能の設計はこのフォルダの設計書にあります。

## ファイル

```
AGENTS.md / docs/                     仕様
langgraph.json                        graphs.agent（= image）-> graph.py:graph、graphs.chat -> chat_graph.py:graph、http.app -> coder_app.py:app（/coder/turn）
src/furry_agent/chat_graph.py         チャットタブのグラフ（ingest → route → chat | plan → search → filter → read → critique → synthesize）
src/furry_agent/chat_models.py        チャットタブの llama-server（reader、代理リーダー）の起動と停止、外部依存の差し替え口
src/furry_agent/claim_verify.py       主張の突き合わせ: EvidenceCard / Claim、門（字面・数値・固有名詞）、文の分割と削除。モデルなし
src/furry_agent/claim_nodes.py        主張の抽出 → 判定 → （統合）→ 監査 → 削除のノード
src/furry_agent/control_nodes.py      自律モード（controller → 道具の流れ → controller_record → finish）。判断の JSON、予算、重複の禁止
src/furry_agent/coder_gate.py         cirka 向けの POST /coder/turn（ルータの 27B の tool calling を SSE で返す。ツールは実行しない）。coder_app.py が langgraph.json の http.app
prompts/chat/controller.txt           自律モードの判断の system prompt
client/                               クライアント側の CUI（Rust。実行ファイル名 cirka）。src/agent.rs（ループ）、tools/（ローカルのツールとホストの検索・画像）、host.rs、policy.rs（許可と auto の確認一覧）、context.rs、session.rs、tui.rs（画面）、art.rs / art_data.rs（ロゴの絵）
docs/logo/                            cirka のロゴ（cirka-icon / cirka-logo / cirka-design の JPG と、icon / logo / image の SVG）
scripts/gen_cirka_art.py              docs/logo の JPG から cirka のロゴの絵（client/src/art_data.rs）を作る
scripts/build-cirka.ps1               cirka のリリースビルドと zip（dist/）
src/furry_agent/search_agent.py       検索のスキーマ（Pydantic）、ページの絞り込み、引用の照合、統合への入力
src/furry_agent/search_client.py      Tor（socks5h）経由の検索と本文取得、URL の許可判定
src/furry_agent/bonsai_select.py      タスクごとのモデル選択（順位、検証結果、空きメモリ）
src/furry_agent/bonsai_worker.py      llama-server の起動と停止（PID）、reader のツール呼び出し
src/furry_agent/bonsai_probe.py       検索モデルの検証（probe-bonsai.ps1）
src/furry_agent/llm_client.py         OpenAI 互換クライアント（チャットタブ専用）、ルータの状態確認と unload（LlamaRouter）
src/furry_agent/router.py, tor_service.py, html_text.py, job_lock.py   検索判定、Tor の自動起動、HTML のテキスト化、タブ共通のロック
config/search_models.json             検索モデル 8 つ（ファイル、メモリの目安、タスクごとの順）
config/llm_model.json                 27B の元ファイル（Hugging Face、SHA-256）、量子化、ルータのモデル名と既定値
src/furry_agent/graph.py              LangGraph のグラフ（ingest → plan → confirm → submit → await_tags → await_image）
src/furry_agent/planner.py            役割推定（ルール）、テンプレート選択、数値のクランプ。純粋関数
src/furry_agent/templates.py          テンプレートの読み込み、ノードマップ経由の注入、LoRA の挿入
src/furry_agent/comfy_client.py       ComfyUI HTTP / WebSocket クライアント
src/furry_agent/media.py              添付画像の取り出しと検証（役割・強度は block の metadata）
comfyui_nodes/furry_ja/               ComfyUI カスタムノード（eject / split / ckpt / Chroma 用 ckpt / image-after / release）。llm_state.py がルータの状態確認
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
scripts/setup*.ps1                    セットアップ（scripts/lib/common.ps1 が共通処理）。setup-llm.ps1 が 27B とルータのプリセット、setup-comfyui.ps1 が tools/comfyui
scripts/gguf_info.py                  GGUF のヘッダから層数を読む（setup-llm.ps1 が GPU に置く層を決める）
scripts/start-*.ps1, doctor.ps1       起動と確認
scripts/open-firewall.ps1             ファイアウォール（管理者）
scripts/build_workflows.py            workflows/（テンプレートとマップを含む）を prompts/ から生成
scripts/convert_chroma_fp8.py         Chroma の BF16 拡散モデルを fp8 に変換（setup-comfyui-chroma.ps1 が呼ぶ）
agent-chat-ui/                        公式 UI（langchain-ai/agent-chat-ui@cf72cb0、画像表示と役割選択の変更あり）
tests/                                pytest（Python と PowerShell スクリプトの両方）
tools/llama-prism/, tools/llm/, tools/models/   llama.cpp、ルータのプリセット、モデル（git 管理外）
tools/comfyui/                        ComfyUI 本体と venv、custom_nodes、models（git 管理外）
outputs/  logs/  artifacts/           実行時に生成（git 管理外）
```

## ワークフローのノード

| ID | クラス | 役割 |
|---|---|---|
| `llm_backend` | LMConnectLMStudioBackend | OpenAI 互換クライアントとして使う（名前は LM Connect のもの）。`http://127.0.0.1:8080/v1`、モデル `qwen3.8-27b-abliterated`、auto-eject off（LM Studio の API を叩くため。unload は `eject`）、thinking off |
| `llm_backend_vision` | LMConnectLMStudioBackend | Vision 用。同じルータとモデル（直後の `prompt_node` も同じ 27B を使う） |
| `user_prompt` | PrimitiveStringMultiline | 日本語指示（LangGraph が書き換え） |
| `ref_image` | LoadImage | 修正する元画像（参照なしの実行では削除） |
| `vision` | LMConnectVision | 元画像をタグ化。長辺 768 |
| `prompt_join` | StringConcatenate | 指示 + `[Reference image tags]` |
| `prompt_node` | LMConnectPromptWithSystem | JSON `{"positive","negative"}` を返させる |
| `eject` | FurryJaEjectLLM | ルータのモデルを `POST /models/unload` で外し、`GET /models` で外れたことを確かめてから、テキストを passthrough |
| `split` | FurryJaSplitTags | 最初の `{` から最後の `}` を `json.loads`（不正なバックスラッシュエスケープは除去）。失敗時はリトライせず生文字列を positive、固定の画質タグを negative。品質タグを先頭に付与。LM Connect がエラー文字列を返した場合は実行を失敗させる |
| `ckpt` | FurryJaCheckpointLoaderAfterEject | CheckpointLoaderSimple に `after`（eject の出力）を足したもの。ロード前にルータに常駐するモデルが 0 であることを API で確認し、残っていれば unload、消えなければ失敗 |
| `positive` / `negative` | CLIPTextEncode | |
| `ref_scale` | ImageScaleToTotalPixels | img2img 用に約 1MP（64 の倍数）へ |
| `latent` | EmptyLatentImage / VAEEncode | テキストのみは Empty Latent 832×1216、img2img は VAE Encode |
| `sampler` | KSampler | steps 28、cfg 5.5、euler_ancestral、normal、denoise 1.0 / 0.45 |
| `decode` / `save` | VAEDecode / SaveImage | `output/furry_ja/` に保存 |

`ckpt` を素の CheckpointLoaderSimple にすると、入力が無いため ComfyUI が LLM より先に実行し得ます。`after` 入力で eject の後に固定しています。

## 役割別テンプレートで足すノード

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

## UI の変更点

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

## ログ

- `logs\furry_agent.log`（LangGraph 側）: チャットタブの経路・モデル・reader の所要時間・結果 URL（検索語の全文は残しません）、画像タブの投入（prompt_id / seed）、タグ、`ckpt gate: LLM router unloaded=[True]`、
  `KSampler started ... LLM router unloaded at checkpoint load=[True]`、`eject verified`、保存先。
- ComfyUI のコンソール: `[furry_ja] eject: LLM router unloaded [...]`、`[furry_ja] split mode=json|fallback`、
  `[furry_ja] LLM router verified unloaded before checkpoint/KSampler`。
- `/coder/turn`（cirka）: LangGraph のコンソールに `coder turn mode=… messages=… tools=… calls=… finish=… tokens=… seconds=…` だけを出します（会話やファイルの中身は残しません）。
- cirka: 利用者の PC の `%LOCALAPPDATA%\cirka\sessions`（macOS / Linux は `~/.local/share/cirka/sessions`）にセッションを JSON Lines で残します（ツールの結果を含む。`/forget` で削除）。

## 開発

```powershell
uv sync
uv run pytest                                  # Python と PowerShell スクリプトのテスト（Docker 実機のテストは Docker 起動中だけ）
uv run python scripts\build_workflows.py       # prompts\ を変えたら workflows\ を再生成
cd client; cargo test                          # cirka（CUI）のテスト。ホストは立てない
uv run python scripts\gen_cirka_art.py         # docs\logo を変えたら cirka のロゴの絵を再生成
```

`.ps1` は UTF-8（BOM 付き）で保存してください。Windows PowerShell 5.1 は BOM の無いファイルを ANSI として読み、日本語を含む行で構文エラーになります（テストで確認しています）。
