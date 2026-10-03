# Changelog

## v0.3.0 — Chroma1-HD

- `.env` の `COMFY_MODEL_FAMILY=flux` で、Flux.1 由来の Chroma1-HD（`CKPT_NAME=chroma_v10HD.safetensors`）に切り替えられるようにした。既定（空 / `sdxl`）は従来の yiffInHell とタグ生成のまま。
- Chroma では LLM が Danbooru タグではなく 1〜3 文の英語の説明文を作る（`prompts/system_chroma_prose.txt`）。`split` は重み構文や `masterpiece` などを取り除き、negative を空にしない。LLM の呼び出し → eject → ロードの順序は SDXL と同じ。
- Chroma のワークフロー（`workflows/flux/`、公式ワークフローを `workflows/reference/` に同梱）: 拡散モデル + T5-XXL fp8（CLIPLoader type chroma）+ Flux VAE を eject の後に読む `FurryJaDiffusionLoaderAfterEject`、ModelSamplingAuraFlow shift 1.0、1024×1024、steps 28、cfg 3.5、euler / beta。
- Chroma の参照画像は元画像 1 枚の img2img だけ。ポーズ・画風・キャラクター・マスクの画像は生成せず理由を返す。LoRA は `CHROMA_LORAS`。
- モデルファイル（拡散モデル・テキストエンコーダ・VAE）の不足は、投入前にファイル名を挙げて返す。
- `scripts/setup-comfyui-chroma.ps1`（T5 と VAE の取得、拡散モデルの fp8 変換）と `scripts/convert_chroma_fp8.py` を追加。

## v0.2.0 — 複数参照画像と LoRA

- 1 メッセージに参照画像を最大 4 枚添付し、各画像に役割（キャラクター / ポーズ・構図 / 画風 / 修正する元画像 / マスク）と強度を指定できるようにした。役割は UI で選ぶか、指示の文（`このキャラをこのポーズにして`、`AのキャラをBのポーズ、Cの画風で` など）から決める。
- 役割の組み合わせから 24 本の登録済みテンプレート（`workflows/sdxl/`）を一意に選び、ノードマップ経由で値を注入する。LLM がワークフローを組み立てることはない。
- キャラクターと画風は IP-Adapter Plus、ポーズは DWPose / Depth Anything V2 / Canny と ControlNet Union、マスクは inpaint。どの役割も役割専用の Vision プロンプトでタグ化して LLM に渡す。
- 役割を決められないときは生成前に確認カード（承認 / 編集 / 却下）を出す。
- 生成前と完了時に、テンプレート・役割・強度・サイズ・seed・LoRA の要約を返す。ポーズ参照では抽出した骨格も返す。
- `さっきの画像を…` で直前の生成画像を元画像として再利用できる。
- `.env` の `LORAS` で LoRA を複数指定できる（`名前[:モデル強度[:CLIP 強度]]`）。
- タイムアウトをタグ生成と画像生成のそれぞれに適用。タイムアウト時は prompt_id を返し、`再取得 <prompt_id>` で結果を受け取れる。UI で停止すると ComfyUI の prompt も中断する。
- 失敗時に段階（入力・ワークフロー選択・役割推定・アップロード・注入・キュー投入・生成・タイムアウト）を返す。
- 参照画像用のノードとモデルを入れる `scripts/setup-comfyui-refs.ps1` を追加。ComfyUI は `--cache-none` で起動する。`doctor.ps1` に参照ノード・ControlNet・LORAS・起動引数の確認を追加。
- `start-ui.ps1` は UI のソースが更新されたときも再ビルドする。
- README のライセンス節に、セットアップで取得する外部ノード・モデルとそのライセンスの一覧を追加。

## v0.1.0 — フェーズ 1

- 他ホストの agent-chat-ui から日本語の指示（参照画像 0〜2 枚）を送り、ComfyUI 上で LM Studio の LLM がタグを作り、LLM を unload してから yiffInHell で静止画を生成して UI に返す。
- セットアップ・起動・確認の PowerShell スクリプト（LM Studio の再量子化、ComfyUI の検出、ファイアウォール）。
