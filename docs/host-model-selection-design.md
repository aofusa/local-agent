# ホストカタログからのモデル選択 設計書

- 対象: [aofusa/local-agent](https://github.com/aofusa/local-agent)（クライアント `client/` = cirka、Web = `agent-chat-ui`）
- 日付: 2026-10-05
- 状態: 設計のみ。実装は含まない
- 関連: `AGENTS.md`、`docs/lmstudio-comfyui-workflow-design.md`、`docs/chroma-hd-support-work-instruction.md`、`docs/locus-cui-design.md`、`config/search_models.json`

## 1. 目的

チャット（Web の chat タブ、cirka）と画像生成（Web の画像タブ、cirka の `/runs/stream`）で、利用者がモデルを選べるようにする。

利用者向けの切替は次の2つに限る。

- Web: Claude や Gemini と同じく、入力欄の送信ボタン脇に現在のモデル名を出し、クリックで一覧から選ぶ。会話の途中でも切り替えられ、次の送信から効く。
- cirka: `/model` で推論モデル、`/image-model` で画像モデルを切り替える。引数なしは現在値と候補の表示。

選べるのはホストがあらかじめ許可したモデルだけである。クライアントはモデル名を自由入力せず、ホストが返したカタログの `id` を送る。表示は `label`。送信するのは `id`。

初期の許可対象は次のとおり。チェックポイント名と LoRA はカタログに直書きする。絶対パスは書かない。推論は llama.cpp に統一する作業中なので、カタログに backend は置かない。

| 種別 | id（安定名） | 表示名 | 実体 |
|---|---|---|---|
| 推論 | `qwen3.8-27b-abliterated` | Qwen 3.8 27B abliterated | llama.cpp。GGUF は `search_models.json` の同 id |
| 推論 | `bonsai-2-27b-abliterated` | Bonsai 2 27B abliterated | llama.cpp。GGUF は `search_models.json` の同 id |
| 画像 | `yiffinhell` | yiffInHell | ComfyUI / family `sdxl` |
| 画像 | `indigofurrymixml` | IndigoFurryMix XL | ComfyUI / family `sdxl` |
| 画像 | `chroma-hd` | Chroma1-HD | ComfyUI / family `flux` |
| 画像 | `wulver` | Wulver | ComfyUI / family `sdxl` |

追加モデルはカタログへ 1 件足すだけにする。コードへのモデル名直書きはしない。

## 2. 現状

モデルはプロセス起動時の `.env` で 1 つに固定されている。メッセージ本文や UI からは切り替わらない。

- 推論: `ChatSettings.lmstudio_model` が `LMSTUDIO_MODEL` を読む。`chat_common._lmstudio`、`coder_gate.turn_endpoint`、`/coder/health` がこれを使う。cirka は `POST /coder/turn` に model を送らない（`client/src/host.rs` の turn 本体）。
- 検索の代理リーダー: `chat_models.py` が `Ternary-Bonsai-2-27B abliterated` を llama-server で起動する。チャット本文の作曲者にはなっていない。`SEARCH_PLANNER=lmstudio|local` は計画担当の切替であり、利用者向けのモデル選択ではない。
- 画像: `Settings.from_env()` の `COMFY_MODEL_FAMILY` と `CKPT_NAME`。`families.py` は「family は `.env` だけで決まり、メッセージでは切り替えない」と明記している。`graph.py` の計画は `settings.model_family` を使う。
- 同時常駐禁止: 27B とチェックポイントは同時に載せない。`job_lock` が image / chat / coder を直列化する。Bonsai は使い終わりに PID を kill する。
- cirka は LM Studio・ComfyUI・Tor へ直接つながない。Web も LangGraph（`:2024`）経由。

したがって選択機能は、クライアントが id を送り、ホストがカタログで検証してから llama.cpp（推論）または ComfyUI（画像）を切り替える形にする。LM Studio は選択経路に入れない。

## 3. 方針

1. 単一の真実はホストのカタログ JSON。チェックポイント名・LoRA・推論の GGUF 対応はカタログ（推論のファイル実体は `search_models.json`）に書く。`.env` は既定 id と無効 id だけ。`CKPT_*` は置かない。推論に backend は置かない（llama.cpp のみ）。
2. 未指定のときは従来どおり `.env` の既定。既存の起動スクリプトと単体テストを壊さない。
3. 未知の id、無効な id、ファイルが無い id は拒否する。黙って既定へ落とさない（`client/src/config.rs` の「typo は黙ってフォールバックしない」と同じ）。
4. 選択は実行ごと。スレッド保存は LangGraph 側に増やさない。Web は会話（スレッド）ごとに「次に使うモデル」を覚え、cirka はセッションに覚える。途中変更は次の送信から効き、実行中の生成は切らない。
5. 画像モデルと推論モデルは独立。Web はタブごとにピッカーを出し、cirka は `/model` と `/image-model` を分ける。画像のタグ生成は当面、既存ワークフローの LM Connect（Qwen）のまま。チェックポイントと family だけを選ぶ。タグ生成まで Bonsai に寄せるのは対象外（ワークフロー内 LLM を外に出す変更になる）。
6. 27B は 1 つだけ常駐。切替前に相手を unload / kill する。`job_lock` は維持する。

## 4. カタログ

新規: `config/host_models.json`（git 管理。秘密も絶対パスも置かない）。

```json
{
  "inference": [
    {
      "id": "qwen3.8-27b-abliterated",
      "label": "Qwen 3.8 27B abliterated",
      "catalog_id": "qwen3.8-27b-abliterated",
      "roles": ["chat", "coder", "search_plan", "search_lead"],
      "context_env": "BONSAI_LEADER_CTX",
      "thinking": "qwen3"
    },
    {
      "id": "bonsai-2-27b-abliterated",
      "label": "Bonsai 2 27B abliterated",
      "catalog_id": "bonsai-2-27b-abliterated",
      "roles": ["chat", "coder", "search_plan", "search_lead"],
      "context_env": "BONSAI_LEADER_CTX",
      "thinking": "off"
    }
  ],
  "image": [
    {
      "id": "yiffinhell",
      "label": "yiffInHell",
      "family": "sdxl",
      "ckpt": "yiffInHell_yihVANTABLACK.safetensors",
      "prompt_style": "danbooru",
      "loras": []
    },
    {
      "id": "indigofurrymixml",
      "label": "IndigoFurryMix XL",
      "family": "sdxl",
      "ckpt": "indigoFurryMixXL.safetensors",
      "prompt_style": "danbooru",
      "loras": ["detail_tweaker:0.5"]
    },
    {
      "id": "chroma-hd",
      "label": "Chroma1-HD",
      "family": "flux",
      "ckpt": "chroma_v10HD.safetensors",
      "prompt_style": "prose",
      "loras": []
    },
    {
      "id": "wulver",
      "label": "Wulver",
      "family": "sdxl",
      "ckpt": "wulver.safetensors",
      "prompt_style": "danbooru",
      "loras": ["KemonoStyleAV1.safetensors:0.8"]
    }
  ]
}
```

規則:

- `id` は英小文字・数字・ハイフン。UI の表示は `label`。
- 推論に `backend` は置かない。両方 llama.cpp（PrismML llama-server）。LM Studio 分岐は作らない。GGUF のファイル名・配置は `catalog_id` で `config/search_models.json` を引き、`available_models` で解決する。Qwen のエントリが検索カタログに無ければ、llama.cpp 統一の作業でそこへ足す。二重にパスを書かない。
- 画像の `ckpt` は ComfyUI `models/checkpoints` のファイル名を直書きする。拡張子あり。空、または checkpoints に無いものは `available: false`。`.env` の `CKPT_*` では上書きしない。
- 画像の `loras` は適用順の配列。要素は `"<lora>:<weight>"`。`<lora>` はファイル名（拡張子はあってもなくてもよい）、`<weight>` はモデル強度（0 より大きく 2 以下の数）。空配列は LoRA なし。clip 強度はモデル強度と同じ。`loras_env` は置かない。`.env` の `LORAS` / `CHROMA_LORAS` はこの選択では読まない。
- `enabled` はカタログに置かない。ファイルが解決でき、`HOST_MODELS_DISABLE` に入っていなければ有効。
- 上の Indigo / Wulver のファイル名と LoRA は形の例。導入したファイル名に合わせてカタログを直す。

## 5. `.env` の修正

`.env.example` には既定 id だけ足す。`CKPT_*` は追加しない。既存の `CKPT_NAME` もこの機能では読まず、`.env.example` から削除する。チェックポイントは `host_models.json` の `ckpt` が唯一の指定である。

```bash
# --- 利用者が選ぶモデル（config/host_models.json の id） ---
# 未指定の実行はこれらの既定。クライアントが id を送った実行だけ上書きする。
HOST_MODELS_PATH=config/host_models.json
DEFAULT_INFERENCE_MODEL=qwen3.8-27b-abliterated
DEFAULT_IMAGE_MODEL=yiffinhell
# カンマ区切りの id をカタログから外す（導入していないモデルを選択肢に出さない）
HOST_MODELS_DISABLE=
```

削除するもの:

- `CKPT_NAME` と、設計途上の `CKPT_YIFFINHELL` / `CKPT_INDIGOFURRYMIXML` / `CKPT_CHROMA_HD` / `CKPT_WULVER`。`CKPT_*` は置かない。
- 画像選択用の `COMFY_MODEL_FAMILY`。family はカタログの `family`。未指定時の後方互換にも使わない。

触らないもの:

- `COMFYUI_URL`、`BONSAI_*`、検索用小モデルの割当。`config/search_models.json` は GGUF 実体の SSOT のまま。
- Chroma の text encoder / VAE / ステップ（`CHROMA_UNET_NAME` 以外の `CHROMA_TEXT_ENCODER`、`CHROMA_VAE`、`CHROMA_STEPS`、`CHROMA_MAX_PIXELS`）。これらは family の実行パラメータであり、チェックポイント名ではない。
- `LORAS` / `CHROMA_LORAS` はカタログ選択では読まない。削除してよければ実装時に `.env.example` から外す。残っても選択経路は無視する。

`LMSTUDIO_MODEL` は選択経路では読まない。推論は llama.cpp 統一に合わせ、モデルキーの代わりに `catalog_id` を使う。

実行で推論モデルが明示されたときは、その実行の計画・本文・批判・統合はそのモデルに寄せる（§7）。`SEARCH_PLANNER=lmstudio` は選択経路では使わない。

## 6. ホスト API

LangGraph の `http.app`（`src/furry_agent/coder_app.py`）に足す。認証は増やさない。LAN 内限定は現状どおり。

### `GET /models`

```json
{
  "defaults": {
    "inference": "qwen3.8-27b-abliterated",
    "image": "yiffinhell"
  },
  "inference": [
    {
      "id": "qwen3.8-27b-abliterated",
      "label": "Qwen 3.8 27B abliterated",
      "available": true,
      "reason": "",
      "context": 8192
    },
    {
      "id": "bonsai-2-27b-abliterated",
      "label": "Bonsai 2 27B abliterated",
      "available": false,
      "reason": "GGUF が tools/models に無い",
      "context": 8192
    }
  ],
  "image": [
    {
      "id": "yiffinhell",
      "label": "yiffInHell",
      "family": "sdxl",
      "ckpt_name": "yiffInHell_yihVANTABLACK.safetensors",
      "available": true,
      "reason": ""
    }
  ]
}
```

`available` は選択肢に残す。選べない理由を UI に出す。生成リクエストで `available: false` を指定したら 400。

### `GET /coder/health`

既存フィールドは残す。追加: `inference_default`、`image_default`。`model` は選択中の推論 id（LM Studio キーは返さない）。

### 実行時の指定

| 経路 | 場所 | フィールド |
|---|---|---|
| cirka 推論 | `POST /coder/turn` body | `inference_model`（任意） |
| Web / cirka の chat | `POST /runs/stream` の `config.configurable` | `inference_model` |
| Web / cirka の画像 | 同上、graph `agent` | `image_model` |
| 画像タブでタグ用 LLM を変えない | — | `inference_model` は画像グラフが読まない |

空・欠落は既定。未知 id は `400` / グラフなら利用者向けエラー文（黙って既定にしない）。

## 7. 推論モデルの切替

新規モジュール `src/furry_agent/model_catalog.py`:

- カタログ読込、id 解決、可用性（`search_models.json` の GGUF があるか、ComfyUI checkpoints に `ckpt` があるか）
- `resolve_inference(requested) -> InferenceChoice`
- `resolve_image(requested) -> ImageChoice`

推論は Qwen も Bonsai も同じ経路。`backend` では分岐しない。

- `catalog_id` で `search_models.json` の GGUF を決め、`chat_models._leader` と同じ llama-server 起動（`BONSAI_LLAMA_SERVER`、port offset 9）をする。OpenAI 互換クライアントを chat / coder に渡す。
- 実行開始時、別 id の llama-server が残っていれば stop してからロードする。LM Studio の load / unload は呼ばない。
- 思考はカタログの `thinking`。`qwen3` は既存の思考トークン分離。`off` は think モードでも本文だけ返し、UI に「このモデルは思考非表示」と出す。

coder 側の差分:

- `parse_turn` が任意の `inference_model` を受ける。
- `run_turn` は LM Studio 固定をやめ、解決した llama.cpp クライアントでストリームする。tool calling は OpenAI 互換のまま。tool call を欠く場合は 1 回だけ「ツール呼び出し JSON で返せ」と再試行し、それでも無ければ `error` code `tool_call`（黙って本文に混ぜない）。
- 連続ターンで毎回 kill すると遅いので、coder セッション中は同じ id ならサーバを再利用し、id が変わったときと `job_lock` 解放時に落とす。

chat グラフの差分（`chat_graph.py` / `chat_common.py` / `chat_models.py`）:

- 本文・思考・執筆・コードの作曲者を、選択した推論モデルにする。計画・批判・統合も同じモデル。route / filter / worker の小モデルは現状どおり。
- `SEARCH_PLANNER=lmstudio` は見ない。選択モデルが載らないときはエラーにし、別の 27B へ自動で替えない。

メモリ:

- 切替前に前の llama-server を stop。画像ジョブが lock を持っている間は coder / chat は待つ（既存）。
- 作曲者の 27B と検索 reader を同時に載せるかは現状の `free_memory_mb` 判定のまま。載らなければ既存の次点（小モデル）。27B 同士の自動入替はしない。

## 8. 画像モデルの切替

`graph.py` の計画作成で、`configurable.image_model` があれば `ImageChoice` をジョブに焼く。

```text
job.family      = choice.family          # sdxl | flux
job.ckpt_name   = choice.ckpt            # カタログの ckpt そのまま
job.image_model = choice.id
job.prompt_style = danbooru | prose
job.loras       = choice.loras           # ["<lora>:<weight>", ...]
```

- `Settings.ckpt_for` / `loras_for` は読まない。ckpt と LoRA はジョブの値だけ。`.env` の `CKPT_*` と `LORAS` / `CHROMA_LORAS` は見ない。
- `families.check_roles` は現状どおり family マップで拒否する。Chroma にポーズ ControlNet を黙って通さない。
- プロンプトは family 既存（`prompts/system_furry_tags.txt` / `prompts/system_chroma_prose.txt`）。Indigo / Wulver は yiffInHell と同じ Danbooru 経路。専用プロンプトは作らない。
- ワークフローは `workflows/<family>/` のまま。チェックポイント名はカタログの `ckpt` をテンプレートへ注入する。
- LoRA はカタログの配列をその順で注入する。`"name.safetensors:0.8"` は model 0.8 / clip 0.8。形式が違う要素はカタログ読込時に拒否する（実行時に黙って外さない）。
- 生成前の `_check_models` で ckpt または LoRA ファイルが不在なら、その id を理由付きで返す。別モデルへ自動フォールバックしない。

`families.py` の「メッセージでは切り替えない」は維持する。切り替えるのは明示 id だけ。本文の「Chroma で」「wulver で」は解釈しない。

## 9. cirka の `/model` と `/image-model`

cirka はホストへ id を送るだけ。モデルの実体は知らない。切替の入口はスラッシュコマンドだけにする（対話本文の「Bonsai で」は解釈しない）。

| ファイル | 修正 |
|---|---|
| `client/src/config.rs` | 任意キー `inference_model` / `image_model`。環境変数 `CIRKA_INFERENCE_MODEL` / `CIRKA_IMAGE_MODEL`。空はホスト既定。値は id の形だけ検査し、存在確認はホストに任せる |
| `client/src/host.rs` | `GET /models`。`/coder/turn` body に `inference_model`。`/runs/stream` の configurable に `inference_model` と `image_model`。`cirka status` に選択中の両 id |
| `client/src/tui.rs` / `app.rs` | コマンド解析、ステータス行、候補一覧 |
| `client/src/session.rs` | セッションに最後の両 id を保存し、`--resume` で復元 |
| `client/README.md` | 下記コマンド |

コマンド（確定名）:

```text
/model                                      # 現在の推論モデルと候補
/model qwen3.8-27b-abliterated              # 推論を Qwen に切り替え
/model bonsai-2-27b-abliterated             # 推論を Bonsai に切り替え
/image-model                                # 現在の画像モデルと候補
/image-model yiffinhell
/image-model indigofurrymixml
/image-model chroma-hd
/image-model wulver
/models                                     # GET /models の生一覧（available と reason）
```

挙動:

- 引数なしは切替えない。現在の label、id、ホスト既定かセッション上書きか、候補（使えないものは reason 付き）を出す。
- 引数はカタログの `id`。表示名の部分一致は取らない（`qwen` だけでは拒否）。候補表示のあと利用者が id を打つ。
- 成功時は1行で確認する。例: `推論モデル: Bonsai 2 27B abliterated（bonsai-2-27b-abliterated）。次のターンから。`
- 反映は次のターンから。実行中の `/coder/turn` や画像生成は切らない。切替コマンド自体はホストへ推論を投げない。
- `/model` の id は以降の `POST /coder/turn` body `inference_model` と、チャットグラフへの `config.configurable.inference_model` に載る。
- `/image-model` の id は画像グラフへの `config.configurable.image_model` に載る。推論ターンには付けない。
- 未知 id、またはホストが 400 `unknown_model` / `model_unavailable` を返したときは理由を出し、選択は変えない。
- ステータス行は `model:<label>  image:<label>`。未取得の間は id のまま。
- 永続: セッションファイル。`--save` 付き（`/model <id> --save`、`/image-model <id> --save`）のときだけユーザー設定または `--project` なら `.cirka/config.toml` に書く。既存の `/host --save` と同じ。
- 起動時の優先順: セッション（resume） > 環境変数 > config.toml > ホスト既定（`GET /models` の `defaults`）。空のローカル設定はホスト既定であり、上書きではない。

`/image` は別名にしない。画像モデルは `/image-model` のみ。

## 10. Web UI のモデル切替（Claude / Gemini 型）

在庫の `agent-chat-ui` に最小差分で足す。新しい `frontend/` は作らない。見た目と操作は Claude.ai / Gemini のモデルピッカーに合わせる。

Claude / Gemini から採るもの:

- 送信ボタンのすぐ脇に、今のモデル名が常に見える（Claude の model menu、Gemini のバー上のモデル名）。
- 名前をクリックすると、ホストカタログの一覧が開く。選ぶと閉じ、表示名が変わる。
- 会話の途中でも変えられる。効くのは次の応答から。実行中のストリームは切らない。
- 新しいチャットは既定モデルで始まる。直前のチャットの選択は引き継がない（Claude の「新しいチャットは設定された既定」に合わせる）。同じチャットの中では選択を保持する。
- 使えないモデルは一覧に残し、選べない理由を添えて disabled にする（管理者カタログで落ちている、に相当）。

採らないもの:

- effort / thinking の別メニュー。思考の有無はモデル定義（Qwen は既存の think、Bonsai は off）のまま。
- 本文からのモデル名解釈。ピッカーと、下記の明示操作だけ。
- 有料プランや「More models」の階層。候補は `GET /models` の件数だけ。

配置:

- chat タブ: 送信ボタン左に推論モデル名（例: `Qwen 3.8 27B`）。開く一覧は `inference` のみ。
- 画像タブ: 送信ボタン左に画像モデル名（例: `yiffInHell`）。開く一覧は `image` のみ。family（SDXL / Chroma）を副行に出す。
- 両タブで独立。チャットで Bonsai を選んでも画像タブのチェックポイントは変わらない。

データ:

- 起動時、タブを開いたとき、ピッカーを開いたときに `GET http://<LangGraph host>:2024/models`。UI の `:3000` ではない。失敗時はピッカーを「モデル一覧を取得できません」にし、送信はホスト既定（id を付けない）。
- 選択はスレッド単位でメモリに持つ。リロード用に `sessionStorage` キー `cirka.thread.<threadId>.inference_model` / `image_model`。スレッド id が無い新規画面は `cirka.draft.inference_model`。`localStorage` で全チャット共通にはしない。
- 送信時、chat は `config.configurable.inference_model`、画像は `config.configurable.image_model`。assistant id は現状の `chat` / `agent`。グラフは増やさない。
- 応答メタに使った id と label を足し、吹き出しの補助行に出す（途中で切り替えても、どの応答がどのモデルか残る）。
- ピッカーの各行は `label`、1行説明（推論は context、画像は family と ckpt）、`available == false` なら `reason`。送信値は `id`。backend は出さない。

想定タッチファイル（実在名は実装時に送信箇所で確定）:

- `agent-chat-ui` の入力欄（送信ボタンの隣にピッカー）
- run を組む箇所（configurable へ id を足す）
- メッセージ描画（応答のモデル名）
- モデル API のベース URL は既存の LangGraph URL を再利用。UI 用 `.env` に新しいモデルキーは足さない。

## 11. 修正ファイル一覧

| 区分 | パス | 内容 |
|---|---|---|
| 新規 | `config/host_models.json` | 許可モデル |
| 新規 | `src/furry_agent/model_catalog.py` | 読込・解決・可用性 |
| 新規 | `tests/test_model_catalog.py` | id 拒否、`loras` 形式、欠落 ckpt |
| 設定 | `.env.example` | 既定 id のみ。`CKPT_*` を削除（§5） |
| 設定 | `src/furry_agent/config.py` | 既定 id。`CKPT_NAME` / `COMFY_MODEL_FAMILY` は選択経路から外す |
| 設定 | `src/furry_agent/families.py` | コメント修正のみ（明示 id は可、本文解釈は不可） |
| API | `src/furry_agent/coder_app.py` | `GET /models` |
| API | `src/furry_agent/coder_gate.py` | `inference_model`。llama.cpp のみ |
| chat | `src/furry_agent/chat_common.py` | 作曲者クライアントを選択結果に |
| chat | `src/furry_agent/chat_models.py` | 同じ id はサーバ再利用、切替時 stop |
| chat | `src/furry_agent/chat_graph.py` | configurable の推論 id を読む。計画もその llama.cpp モデル |
| image | `src/furry_agent/graph.py` | `image_model` を job に焼く。ckpt / family / loras |
| image | `src/furry_agent/planner.py` | ジョブの family / ckpt / loras を注入 |
| client | `client/src/config.rs` `host.rs` `tui.rs` `app.rs` `session.rs` | `/model` と `/image-model`（§9） |
| client | `client/README.md` | `/model` `/image-model` `/models` |
| web | `agent-chat-ui/` の入力欄（送信ボタン脇）、run 送信、メッセージ描画 | Claude / Gemini 型ピッカー（§10） |
| 文書 | `AGENTS.md` `CHANGELOG.md` | family / ckpt / LoRA はカタログ。推論は llama.cpp のみ。`CKPT_*` は廃止 |
| 起動 | `scripts/setup-comfyui.ps1` | `CKPT_*` を書かない。自動ダウンロードはしない |

検索カタログ `config/search_models.json` は推論 GGUF の SSOT。`host_models.json` は利用者向け id と、画像の `ckpt` / `loras` を持つ。

## 12. エラー

| 条件 | 結果 |
|---|---|
| 未知 id | 400 `unknown_model`。本文「ホストのモデル一覧にありません」 |
| disable / ファイル無し | 400 `model_unavailable` と reason |
| 切替先の起動失敗 | 既存の idle timeout。次のモデルへ自動で替えない |
| 他タブが lock 中 | 既存の `waiting`。選択自体は受け付ける |
| Chroma に未対応の参照役割 | 既存 `FamilyError`。モデル名を文に含める |
| 推論が tool call を返さない | coder は `tool_call` エラー。1 回再試行 |

## 13. テスト

- カタログ: 既定 id、未知 id、disable、空 `ckpt` は unavailable、`loras` の `"<lora>:<weight>"` 以外は読込拒否。`.env` の `CKPT_*` は無視する。
- coder: `inference_model` 欠落は既定 id。Qwen でも Bonsai でも llama-server factory が呼ばれ、LM Studio は呼ばれない。
- chat: configurable の id が作曲者に渡る。計画も同じモデル。別の 27B へは自動で落ちない。
- image: `image_model=chroma-hd` で family `flux` とカタログの `ckpt` / `loras` が job に入る。未指定は `DEFAULT_IMAGE_MODEL`。本文にモデル名があっても family は変わらない。
- cirka: `/model <id>` が次の turn body の `inference_model` に載る。`/image-model <id>` が画像 run の `image_model` に載る。引数なしは切替えない。部分一致は拒否。未知 id は選択を変えない。`--save` だけ config.toml に書く。
- Web: ピッカーの選択が次の送信の configurable に載る。新規チャットはホスト既定。会話途中の変更は次の応答から。chat タブは inference のみ、画像タブは image のみ。

## 14. 実装順

1. カタログと `GET /models`、`.env.example`。挙動は変えない。
2. 画像 id → family / ckpt。Web 画像タブの送信ボタン脇ピッカー。cirka の `/image-model`。
3. 推論 id → chat と `/coder/turn`。Web chat タブの送信ボタン脇ピッカー。cirka の `/model`。
4. 応答吹き出しへのモデル名、会話途中の切替が次の送信だけに効くことの確認。

各段で `job_lock` と unload を守る。2 と 3 は独立に出せる。

## 15. 対象外

- モデルのダウンロード、量子化、LM Studio への自動ロード設定。
- 利用者による任意 ckpt パスの入力。
- 検索専用小モデル（1.7B / 4B / 8B）の UI 選択。
- 画像タグ生成 LLM の Bonsai 化。
- 動画、LoRA のモデルごと UI、同時に 2 つの 27B を載せること。
- Claude の effort / thinking 切替、Gemini の入れ子メニュー。ピッカーはモデル一覧だけ。
- `/image` を `/image-model` の別名にすること。本文中のモデル名解釈。
- 認証の追加。
