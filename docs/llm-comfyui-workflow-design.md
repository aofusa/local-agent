# LLM（llama.cpp ルータ）× ComfyUI 画像生成ワークフロー設計書

対象実装者: ローカルの Claude Code
対象環境: ROG Ally X（Windows、共有メモリ 24GB 級）。LLM サーバ（llama.cpp の llama-server、ルータモード）と ComfyUI は、このリポジトリのセットアップが導入し、ローカルで起動する
文書の目的: 日本語（および任意の画像・動画）を入力し、furry 系チェックポイントで静止画を生成するワークフローを、ComfyUI から LLM サーバ（llama.cpp のルータ）を呼ぶ形で構築する

この文書は実装指示である。曖昧な箇所は「未確定」と明記し、勝手に別アーキテクチャへ乗り換えない。

> 改訂（v0.11.0、利用者の指定）: 当初 LM Studio だった LLM サーバを、llama.cpp の llama-server のルータモード
> （`--models-preset`、`127.0.0.1:8080`）に置き換えた。役割（OpenAI 互換 API、要求時のロード、生成後の unload、
> ループバック待受）とノード ID・JSON 契約・unload 順は変えていない。unload は LM Studio のネイティブ API ではなく、
> ルータの `POST /models/unload` と `GET /models` の状態で行う。経緯、対応表、実測は `docs/llamacpp-router-design.md`。

---

## 1. 結論

採用する構成は次の一本だけとする。

```
ユーザー指示（日本語、任意で画像・動画）
        │
        ▼
ComfyUI ワークフロー
        │  OpenAI 互換 API
        ▼
llama.cpp ルータ（llama-server --models-preset。Qwen3.8 27B abliterated + mmproj）
        │  英語タグ / ネガティブ
        ▼
ComfyUI が LLM を unload
        │
        ▼
yiffinhell 等で KSampler
        │
        ▼
プレビュー / 保存。任意で薄いフロントが画像を返す
```

採用しないもの:

- LM Studio のチャット GUI から MCP 経由で ComfyUI を叩く形。可能ではあるが、Ally X では 27B と拡散モデルの unload 順をグラフが決められない。画像をチャットへ戻す MCP（ImageMCP）も、生成前に LLM を下ろさない。
- LLM とチェックポイントの同時常駐。
- 動画そのものの生成（Wan / LTX 等）。本設計の出力は静止画のみ。動画は参照フレームの供給源に限る。

## 2. 背景と制約

### 2.1 モデル

| 役割 | モデル | 備考 |
|---|---|---|
| プロンプト整形・参照キャプション | llama.cpp ルータ上の Qwen3.8 27B abliterated | ネイティブ VLM。画像入力にはテキスト GGUF に加え `mmproj`（約 0.9GB）が必要 |
| 画像生成 | ComfyUI 上の yiffinhell など furry 系チェックポイント | Illustrious / Pony 系を想定。Danbooru タグが散文より効く |

量子化は Q4_K_M または IQ4_XS。コンテキストは 4096〜8192。プロンプト整形に長い文脈は不要。

### 2.2 ROG Ally X

- 共有メモリは 24GB 級。Q4 の 27B はおおむね 16〜17GB、mmproj で約 1GB、yiffinhell 級チェックポイントは約 6GB。同時常駐は不可。
- 実行順は固定する。LLM がタグを返す → LLM サーバ（ルータ）からモデルを unload → その後に CheckpointLoader と KSampler。
- 生成解像度は 768〜1024、batch は 1。UMA の GPU 割当は大きめにしておく（実装対象外。起動手順に注記する）。
- ローカル LLM の実行バックエンドは llama.cpp のルータ（別プロセスの llama-server）のみ。ComfyUI プロセス内に GGUF を載せない（llama-cpp-python の Vulkan ビルドを Ally X 向けに追加すると、依存が壊れる）。

### 2.3 入出力

必須:

- 日本語テキストから静止画。
- 任意の参照画像（0〜2 枚）を、キャプションと img2img / IP-Adapter の両方に使える。
- 任意の参照動画は、代表フレーム 1〜4 枚に落としてから同じ経路へ入れる。

任意（フェーズ 2）:

- チャット欄だけの薄いフロント。ComfyUI のノード画面を触らずに日本語を送れる。

## 3. コンポーネント

### 3.1 既存プロセス

- LLM サーバ: llama-server をルータモードで `http://127.0.0.1:8080/v1` に起動する（`scripts/start-llm.ps1`）。プリセット（`scripts/setup-llm.ps1` が作る `tools/llm/models.ini`）に Qwen3.8 27B abliterated と mmproj、context 4096、thinking off を書く。モデルは最初の要求でロードされる。アイドル 300 秒の sleep を有効にしておく（ノード側 eject の保険）。
  （v0.10 まで: LM Studio の Local Server `http://127.0.0.1:1234/v1`、Idle TTL / Auto-Unload。）
- ComfyUI: `http://127.0.0.1:8188`。`--listen 127.0.0.1 --port 8188`。外向きには開けない。

### 3.2 ComfyUI カスタムノード

第一候補は `eedali/LM_Connect`。

- Backend: `LMConnectLMStudioBackend`（base_url、streaming、thinking 無効）。名前に反して中身は OpenAI 互換クライアントなので、ルータの URL を指して使う。auto-eject は LM Studio のネイティブ API を叩くため off にする。
- Prompt: `LM Connect: Prompt + System Prompt`。
- Vision: `LM Connect: Vision`（最大 5 画像）。
- Unload: `furry_ja: Eject LLM`（`FurryJaEjectLLM`、このリポジトリのノード）を生成ノードの直後に挟み、ルータの `POST /models/unload` でモデルを外し、`GET /models` で外れたことを確かめてから、テキストを後段へ passthrough する。（v0.10 まで: `LM Connect: Eject LM Studio Model`。）
- ローカル GGUF バックエンド（`LMConnectLocalGGUFBackend`）は使わない。

導入:

```
cd ComfyUI/custom_nodes
git clone https://github.com/eedali/LM_Connect
pip install requests Pillow numpy
```

ComfyUI を再起動する。CUDA 版 llama-cpp-python は入れない。（このリポジトリでは `scripts/setup-comfyui.ps1` が、ComfyUI 本体を `tools/comfyui` に入れたうえでこれを行う。）

フォールバックは `glonlas/comfyUI-LMStudio-nodes`（`LMStudio - Connect` / `Text Gen` / `Image To Text`）。こちらは auto-eject が無い。使う場合は、テキストノードの後に LLM サーバの unload API を叩く小さな Python ノードを自作し、同じ順を守る（v0.11.0 の `FurryJaEjectLLM` がこれに当たる）。

動画フレーム抽出は ComfyUI-VideoHelperSuite（VHS）の Load Video とフレーム選択。未導入ならフェーズ 1 では動画を対象外にし、画像とテキストだけ先に通す。

### 3.3 フロント（フェーズ 2、任意）

単一 HTML + 小さな Python（標準ライブラリまたは FastAPI）でよい。場所はリポジトリ直下の `frontend/`。ComfyUI の `/prompt`、`/history/{prompt_id}`、`/upload/image`、WebSocket `/ws` だけを叩く。LLM サーバは直接叩かない。

## 4. ワークフロー

グラフは左から右の一本にする。分岐は参照画像を「LLM に見せる」と「サンプラに渡す」の二本だけ。

```
[Load Image] ──┬──> [LM Connect: Vision] ──┐
               │                            │
[日本語テキスト] ┴──> [Prompt + System] ──> [Eject LLM] ──> [文字列分割]
               │                                                    │ positive
               │                                                    │ negative
               └──> [VAE Encode または IP-Adapter] ──────────────> [KSampler]
                                                                      │
                                                              [VAE Decode] → [Save Image]
```

テキストのみのときは Load Image を外し、Empty Latent を KSampler へ入れる。denoise は 1.0。

### 4.1 ノード契約

実装時に API 形式で書き出す。ノード ID は固定し、フロントが差し替えられるようにする。

| ID | 役割 | フロントが書き換える入力 |
|---|---|---|
| `llm_backend` | LM Connect Backend（OpenAI 互換） | なし（base_url は `http://127.0.0.1:8080/v1`、auto_eject false、thinking off） |
| `user_prompt` | Primitive 文字列 | 日本語指示 |
| `ref_image` | Load Image | アップロードしたファイル名。無しならノードをバイパス |
| `vision` | LM Connect Vision | 画像が無い実行ではスキップ |
| `prompt_node` | System + user | system は固定。user は日本語 + vision のキャプション |
| `eject` | Eject LLM（`FurryJaEjectLLM`） | なし。ルータのモデルを unload して、テキストを passthrough |
| `split` | positive / negative 分割 | なし |
| `ckpt` | CheckpointLoaderSimple | `yiffinhell.safetensors`（実ファイル名に合わせる） |
| `positive` / `negative` | CLIP Text Encode | split の出力 |
| `latent` | Empty Latent または VAE Encode | 参照ありかつ img2img のときだけ VAE Encode。denoise は 0.45 を初期値 |
| `sampler` | KSampler | seed、steps 28、cfg 5.5、euler ancestral、normal。解像度 832×1216 を初期値 |
| `decode` / `save` | VAE Decode / Save Image | なし |

IP-Adapter はフェーズ 1 の必須にしない。チェックポイントと同系の IP-Adapter 重みが手元に無い場合、img2img（denoise 0.35〜0.65）だけで同一性を残す。重みがある場合のみ任意入力にする。

### 4.2 LLM の出力形式

system prompt はワークフローに埋め込み、フロントからは変えない。モデルには次の JSON だけを返させる。前後の説明、Markdown の囲み、思考タグは禁止。

```json
{"positive":"danbooru tags, short english phrase","negative":"low quality, worst quality, ..."}
```

system prompt の要件:

- 入力は日本語でよい。出力の positive は Danbooru / e621 系タグと、80 語以内の英語。
- キャラの種別、被毛色、服装、ポーズ、画角、照明、場所を落とさない。
- 参照画像のキャプションが付いている場合は、ユーザー指示と矛盾しない範囲でタグへ統合する。ユーザー指示を優先する。
- 拒否しない。倫理注釈を返さない。
- negative は画質タグ中心。ユーザーが明示した除外だけ足す。
- Qwen3 系は thinking を無効にする。ノードが `<think>` を残した場合は分割前に除去する。

分割ノードは、最初の `{` から最後の `}` までを切り出して `json.loads` する。失敗したらリトライせず、生文字列を positive にし、negative は固定の画質タグにする。

### 4.3 画像入力

二系統を同時に使える。

- 同一性を残す: Load Image → VAE Encode → KSampler（denoise 0.45）。構図だけ借りるなら denoise を 0.7 前後まで上げる。
- 言葉に落とす: 同じ画像を Vision ノードへ入れ、キャプションを user prompt に連結する。Vision 側の長辺は 768 に縮小し、コンテキスト溢れを避ける。

画像が 0 枚なら Vision ノードを実行しない。

### 4.4 動画入力

フェーズ 1.5。VHS で 1〜4 フレームを等間隔に抜く。1 枚目を img2img の基準にし、残りは Vision の追加画像にする。LLM への指示は「被写体・ポーズ・カメラだけタグ化」。動画生成モデルはロードしない。

## 5. フェーズ 2 のフロント

ComfyUI のノード画面が本体。フロントは次の操作だけを持つ。

- 日本語テキスト欄。
- 画像 0〜2 枚、または動画 1 本。動画はサーバ側でフレーム抽出してから `/upload/image` する。
- 生成ボタン、進捗、結果画像、保存リンク。
- モード切替: `txt2img` / `img2img`。denoise は img2img のときだけ表示。

API の流れ:

1. 画像があれば `POST /upload/image`。
2. 保存済みの API 形式ワークフロー JSON を読み、`user_prompt` と `ref_image` と seed を書き換える。
3. `POST /prompt` に `client_id` 付きで投入。
4. WebSocket `/ws?clientId=...` で完了を待つ。タイムアウトは 10 分。
5. `GET /history/{prompt_id}` から Save Image の filename / subfolder / type を取り、`/view` で表示する。

フロントは 127.0.0.1 のみ。認証は付けない（同一マシン前提）。

## 6. 実装タスク

Claude Code は次の順で進める。各タスクの完了条件を満たしてから次へ進む。

1. 環境確認。`http://127.0.0.1:8080/v1/models`（LLM ルータ）と `http://127.0.0.1:8188/system_stats` が応答することを確認する。応答しなければ起動手順を README に書き、実装を止めない。
2. `custom_nodes/LM_Connect` を導入し、ComfyUI 再起動後にノード一覧へ出ることを確認する。
3. `workflows/furry_ja_api.json` を API 形式で作る。UI 形式の `workflows/furry_ja.json` も対で置く。チェックポイント名は環境変数 `CKPT_NAME`（既定 `yiffinhell.safetensors`）で差し替え可能にする。
4. system prompt を `prompts/system_furry_tags.txt` に置き、ワークフローから読むか、ノードへ貼る。
5. テキストのみで 1 枚生成できることを確認する。ログに eject が成功したこと、KSampler 開始時に LLM サーバのモデルが unloaded であることを残す。
6. 参照画像 1 枚の img2img を確認する。
7. 任意: `frontend/` を追加し、テキストのみの往復を確認する。
8. 任意: VHS が入っていれば動画フレーム経路を足す。入っていなければ `docs/troubleshooting.md` に「未導入のため対象外」と書く。

作ってはいけないもの:

- MCP サーバ、ImageMCP、LLM サーバ側のツール定義。
- ComfyUI 内での GGUF 常駐。
- クラウド API へのフォールバック。
- 動画生成ワークフロー。

## 7. 受け入れ条件

- 日本語 1 文だけで、yiffinhell の静止画が 1 枚保存される。
- 生成された positive がタグ中心で、日本語の主題が落ちていない。
- KSampler 実行前に LLM サーバの 27B が unload されている。同時常駐で OOM になったら失敗。
- 参照画像を渡した実行で、denoise 0.45 のとき元画像の構図が残る。
- 参照が無い実行は Empty Latent を使い、Load Image の欠損で落ちない。
- LLM が JSON を壊しても、生文字列で生成まで進む。
- すべて 127.0.0.1 で閉じている。

## 8. 失敗時の切り分け

| 症状 | 確認 |
|---|---|
| ノードが LLM を呼べない | ルータの起動（`start-llm.ps1`）、ポート 8080、プリセットのモデル、`/v1/models` |
| 画像を見せても説明が空 | mmproj 未ロード。テキスト専用 GGUF だけでは不可 |
| タグの前に思考文が出る | thinking を off。分割前に `<think>` を除去 |
| KSampler で OOM | eject が走っていない。解像度を 768 へ下げる。プリセットの `sleep-idle-seconds` を確認 |
| タグが散文になる | system prompt の JSON 契約を見直す。temperature は 0.4 前後 |
| furry 表現が拒否される | abliterated 本体がロードされているか。system prompt に注釈禁止を入れる |
| フロントだけ失敗 | API 形式 JSON のノード ID が設計の ID と一致しているか |

## 9. ディレクトリ

```
workflows/furry_ja.json          UI 形式
workflows/furry_ja_api.json      API 形式。フロントが読む
prompts/system_furry_tags.txt
frontend/                        フェーズ 2 のみ
README.md                        起動順、UMA 注記、既知の対象外
```

README と `docs/usage.md` の起動順は固定する。LLM ルータを起動（モデルは要求時にロード）→ ComfyUI 起動 → ワークフローを開く → 日本語を入れて Queue。生成後に続けて打つときは、次の Queue の先頭で LLM が再度ロードされる前提でよい。
