# 作業指示書: 複数参照画像による画風・構図・キャラクター指定

> v0.11.0 から LM Studio は使っていない。本文の「LM Studio」「`LMSTUDIO_*`」は、llama.cpp のルータ（`LLM_*`。[llamacpp-router-design.md](llamacpp-router-design.md)）と読み替える。

- 文書ID: WI-IMG-MULTI-REF-001
- 版: 1.0
- 作成日: 2026-10-04
- 対象: LangGraph + agent-ui + ComfyUI + LM Studio によるローカル画像生成アプリ
- 読者: 実装を行う AI エージェント、およびレビューする開発者
- 状態: 設計確定（実装前）。現状コードの事実は「調査」節で埋めてから実装すること

## 0. この文書の使い方

実装 AI は次の順序を守る。

1. 第2章の調査項目をコードベースに対して実施し、結果を本ファイル末尾の「調査結果」に記入する。
2. 調査結果と第4章の設計が矛盾する場合、実装前に差分を列挙し、設計を壊さず吸収できる案を提示する。独断で別アーキテクチャへ置き換えない。
3. 第8章の作業分解を上から実施する。各タスク完了時に受け入れ条件を自己検証する。
4. ComfyUI のノードグラフを LLM に毎回自由生成させない。登録済みワークフローテンプレートへパラメータを注入する方式を守る。
5. 生成はユーザーのローカルマシン上の ComfyUI と LM Studio のみを経由する。外部画像 API を追加しない。

## 1. 背景と目的

### 1.1 現状機能（依頼時点の申告）

- テキスト入力による画像生成
- 画像1枚とテキスト指示による画像修正

スタックは LangGraph、agent-ui、ローカル ComfyUI、ローカル LM Studio。

### 1.2 追加したいこと

複数画像を同時に入力し、自然言語で次を指示できるようにする。

- 画風の指定（スタイル参照）
- 構図・ポージングの指定（構造参照）
- キャラクターの入れ替え（同一性参照）
- 上記の組み合わせ（例: 「Aの顔を、Bのポーズで、Cの画風にして」）

### 1.3 成功条件

- ユーザーが 2〜4 枚の画像を役割付き、または役割未指定で添付できる。
- エージェントが指示と画像から役割・使用ワークフロー・主要パラメータを決め、実行前に要約を返す。
- ローカル ComfyUI で生成し、結果画像をチャットに返す。
- 既存のテキスト生成と単一画像修正が退行しない。
- 失敗時に、どの段階（役割推定、アップロード、ワークフロー注入、キュー、タイムアウト）で落ちたかが分かる。

### 1.4 非目標（この作業ではやらない）

- 動画生成
- LoRA の学習
- ComfyUI ワークフローのキャンバス上自動構築（Comfy Agent 相当）
- クラウド画像 API へのフォールバック
- 参照画像の永続的な人物データベース

## 2. 実装前調査項目

実装 AI はコードを読み、各項目に「場所 / 事実 / 不明 / 影響」を残す。推測で埋めない。

### 2.1 リポジトリ構成

- フロント、LangGraph サーバ、ComfyUI クライアント、設定のディレクトリ
- 言語と主要依存（Python / TypeScript、LangGraph の版、agent-ui の種別）
- agent-ui が `langchain-ai/agent-chat-ui` か `@assistant-ui/react-langgraph` か、自作かを特定する
- 起動方法、環境変数、ComfyUI URL、LM Studio URL の定義箇所

### 2.2 現行の会話と状態

- LangGraph の state 定義
- メッセージがテキストのみか、画像 content block を持つか
- 画像の受け渡し形式（base64、URL、ローカルパス、ComfyUI 上のファイル名）
- スレッド永続化の有無と、画像を状態に残す場合のサイズ上限
- interrupt / human-in-the-loop の既存利用

### 2.3 現行の画像生成経路

- テキスト生成が呼ぶ関数、ワークフロー JSON の所在、ノード ID のハードコード箇所
- 単一画像修正が img2img か inpaint か、別ワークフローか
- ComfyUI 呼び出し（`/upload/image`、`/prompt`、`/history/{id}`、`/view`）の実装有無
- ポーリング間隔、タイムアウト、同時実行数
- 失敗時のリトライと、ユーザーへのエラー返却形式
- 出力画像を UI に返す方法（artifact、base64、静的ファイル URL）

### 2.4 LM Studio の使い方

- OpenAI 互換エンドポイントのベース URL とモデル名
- ツール呼び出しの有無（native tool calling か、JSON 出力のパースか）
- ビジョン入力に対応しているか。非対応なら役割推定はテキストとユーザー指定役割のみで行う
- コンテキスト長、タイムアウト、温度などの現行値

### 2.5 UI

- 画像添付が 1 枚限定か複数可か
- 添付の MIME 制限とサイズ制限
- 役割選択 UI の有無
- 生成中表示、途中キャンセル、再生成の有無
- 結果画像の表示、ダウンロード、次の入力への再利用

### 2.6 ComfyUI 環境

- 起動 URL、出力ディレクトリ、カスタムノードのインストール有無
- 利用チェックポイントの系統（SDXL / Illustrious / Pony / Flux / その他）
- インストール済みの IP-Adapter、ControlNet、InstantID、PuLID、DWPose / OpenPose preprocessor
- API フォーマット JSON を書き出せるか
- VRAM の目安と、同時に載せるモデルの制約

### 2.7 セキュリティとローカル制約

- アップロード画像の保存場所と削除タイミング
- パストラバーサル対策（ComfyUI の `subfolder` / `type` をクライアント入力で自由にしていないか）
- ログに base64 画像を出していないか

調査の完了定義: 第11章の表が埋まり、現行のテキスト生成と単一画像修正のシーケンスが各 10 行以内で説明できること。

## 3. 要件

### 3.1 機能要件

| ID | 要件 |
| --- | --- |
| FR-1 | 1 メッセージに画像を 1〜4 枚添付できる。5 枚目は拒否し、理由を返す |
| FR-2 | 各画像に役割を付けられる。値は `auto` `style` `pose` `character` `base` `mask` |
| FR-3 | `auto` の画像は、ユーザー文言から役割を推定する。自信が低い場合は実行前に確認する |
| FR-4 | 画風のみ: スタイル参照を IP-Adapter style（または実装環境で同等のスタイル参照）に渡す |
| FR-5 | 構図・ポーズのみ: 参照から pose または depth を抽出し ControlNet に渡す。テキストで「線画優先」「奥行き優先」と言われたら preprocessor を切り替える |
| FR-6 | キャラクター入れ替え: 同一性参照と、必要ならポーズ参照を分離して渡す。ベース画像がある場合は編集ワークフロー、無い場合は新規生成ワークフロー |
| FR-7 | 組み合わせを許す。同一役割が複数ある場合は先頭を採用し、残りは無視した旨を返す |
| FR-8 | 実行前に「使用ワークフロー、各画像の役割、主要強度、サイズ、seed」を短く提示する |
| FR-9 | 既存のテキストのみ生成、画像1枚+修正指示を維持する |
| FR-10 | 生成結果をチャットに返し、その画像を次ターンの `base` として再利用できる |
| FR-11 | ユーザーが seed を指定しない場合は乱数とし、結果とともに seed を返す |
| FR-12 | キャンセル要求で ComfyUI の該当 prompt を interrupt する。未実装なら調査結果に記録し、UI 上は「中断非対応」と返す |

### 3.2 非機能要件

| ID | 要件 |
| --- | --- |
| NFR-1 | 画像本体は LangGraph のチェックポイントにフル解像度で保存しない。ComfyUI 上のファイル名とハッシュ、ローカル一時パスのみを持つ |
| NFR-2 | ワークフロー選択は決定的。同じ役割セットなら同じテンプレート ID になる |
| NFR-3 | LLM 障害時でも、役割がすべて明示されていればテンプレート選択と生成は継続できる |
| NFR-4 | 秘密情報と画像バイナリをログに出さない |
| NFR-5 | タイムアウトの初期値はキュー投入から 180 秒。環境変数で変更できる |
| NFR-6 | 追加依存は、既存パッケージマネージャで明示インストールする。実行時の暗黙ダウンロードを必須にしない |

### 3.3 代表ユースケース

1. 画風指定  
   入力: スタイル画像1枚 + 「この絵の雰囲気で、夜の神戸港に立つキャラクター」  
   期待: `style_t2i`。スタイル強度は中。ポーズ ControlNet は使わない。

2. ポージング指定  
   入力: ポーズ画像1枚 + 「このポーズで、白衣のケモノキャラ。背景はシンプル」  
   期待: `pose_t2i`。OpenPose を既定。顔の同一性は転送しない。

3. キャラクター入れ替え  
   入力: キャラ画像、ポーズ画像 + 「このキャラをこのポーズにして」  
   期待: `character_pose_t2i`。同一性は character、骨格は pose。

4. ベース編集 + 画風  
   入力: ベース画像、スタイル画像 + 「構図は維持して画風だけ寄せて」  
   期待: `style_i2i`。denoise は低〜中。ポーズ抽出はしない。

5. 三点参照  
   入力: character、pose、style + 「AのキャラをBのポーズ、Cの画風で」  
   期待: `character_pose_style_t2i`。

6. 曖昧  
   入力: 画像2枚 + 「いい感じに合わせて」  
   期待: 生成せず、役割確認の interrupt を返す。

## 4. 設計

### 4.1 方針

役割付き参照画像を、登録済み ComfyUI ワークフローのスロットへ束縛する。

LLM の責務は次に限る。

- ユーザー文言の意図分類
- `auto` 画像の役割推定
- ポジティブ / ネガティブプロンプトの整形
- 強度・denoise・preprocessor の提案
- 不足情報の確認文生成

LLM の責務にしないもの。

- ComfyUI JSON のノード追加・削除
- ノード ID の発明
- ファイルパスの組立
- サンプラー実装の選択を自由文で上書きすること（許容リストからの選択のみ）

この分離により、LM Studio のツール呼び出し精度が低くても生成経路が壊れない。

### 4.2 論理構成

```text
agent-ui
  画像複数添付、役割、強度の任意指定
        |
        v
LangGraph API
  classify -> bind roles -> select workflow -> confirm
        |                         |
        |                         v
        |                   ComfyUI client
        |                     upload image
        |                     inject workflow
        |                     queue / poll / fetch
        v
LM Studio
  意図、プロンプト、パラメータ提案のみ
```

### 4.3 画像役割

| 役割 | 意味 | ComfyUI 側の主な行き先 | 既定強度 |
| --- | --- | --- | --- |
| `style` | 画風、色、筆致。同一性は持ち込まない | IP-Adapter style / style model。weight 0.6 | 0.55 |
| `pose` | 骨格、カメラ、構図 | DWPose or OpenPose、必要時 depth / canny。ControlNet strength 0.8 | 0.80 |
| `character` | 顔・体の同一性 | InstantID または PuLID。環境に無い場合は IP-Adapter Face に落とす | 0.85 |
| `base` | 修正対象 | img2img の source。denoise は指示で 0.35〜0.75 | denoise 0.55 |
| `mask` | 変更領域 | inpaint mask。白を変更領域とする | - |
| `auto` | 未指定 | 分類ノードが決定 | - |

役割の優先規則:

- ユーザー明示が最優先
- 同一役割の複数指定は先頭のみ採用
- `mask` は `base` が無いと無効
- `character` と `style` を同じ画像に同時付与しない。明示が両方なら確認する
- ポーズ画像から同一性を取らない。逆も同じ

### 4.4 ワークフローカタログ

テンプレートは API フォーマット JSON とし、`workflows/` に置く。プレースホルダは文字列置換ではなく、ノード ID マップ経由で代入する。

| テンプレート ID | 条件 | 必須スロット |
| --- | --- | --- |
| `t2i_basic` | 画像なし | prompt |
| `i2i_basic` | base のみ。現行単一画像修正の後継 | base, prompt |
| `style_t2i` | style のみ | style, prompt |
| `style_i2i` | base + style | base, style, prompt |
| `pose_t2i` | pose のみ | pose, prompt |
| `pose_i2i` | base + pose | base, pose, prompt |
| `character_t2i` | character のみ | character, prompt |
| `character_pose_t2i` | character + pose | character, pose, prompt |
| `character_pose_style_t2i` | character + pose + style | 3 スロット + prompt |
| `inpaint_basic` | base + mask | base, mask, prompt |

選択関数は純粋関数にする。

```text
select_workflow(roles) -> template_id
```

モデル系統差はテンプレートのバリアントで吸収する。

- `workflows/sdxl/*.json`
- `workflows/flux/*.json`

実行時のモデル系統は環境変数 `COMFY_MODEL_FAMILY=sdxl|flux` で決める。不明なら調査結果のチェックポイント名から初期値を提案し、ハードコードしない。

SDXL 系の既定ノード意図:

- style: IP-Adapter style または IP-Adapter plus の style 寄与
- character: InstantID。無ければ PuLID。それも無ければ IP-Adapter Face
- pose: DWPose preprocessor + ControlNet OpenPose
- 構図維持の i2i でポーズ指定が無い場合: denoise を下げ、ControlNet は足さない

Flux 系は環境に入っている参照ノードだけをカタログに登録する。ノードが無いテンプレートは選択不能とし、ユーザーに「この環境では character 参照未対応」と返す。無いノードを実行時にインストールしない。

### 4.5 LangGraph 状態

既存 state に足す。既存の `messages` は維持する。

```python
class ReferenceImage(TypedDict):
    image_id: str
    role: Literal["auto", "style", "pose", "character", "base", "mask"]
    resolved_role: str | None
    filename_on_comfy: str | None
    sha256: str
    width: int
    height: int
    strength: float | None

class GenerationPlan(TypedDict):
    template_id: str
    model_family: str
    positive: str
    negative: str
    width: int
    height: int
    seed: int
    steps: int
    cfg: float
    denoise: float | None
    pose_preprocessor: Literal["openpose", "dwpose", "depth", "canny"] | None
    needs_confirmation: bool
    confirmation_reason: str | None

class AgentState(TypedDict):
    messages: list
    references: list[ReferenceImage]
    plan: GenerationPlan | None
    comfy_prompt_id: str | None
    outputs: list[str]
    error: str | None
```

チェックポイントには `sha256`、役割、ComfyUI ファイル名、プロンプト、seed だけを残す。base64 はノード内の一時変数に限定する。

### 4.6 グラフ

```text
START
  -> ingest_attachments
  -> classify_intent          # LM Studio。失敗時は明示役割のみで続行
  -> resolve_roles
  -> select_workflow
  -> build_plan
  -> confirm_gate             # 曖昧なら interrupt
  -> upload_images
  -> inject_and_queue
  -> poll_result
  -> respond
  -> END
```

各ノードの契約:

- `ingest_attachments`: content block から画像を取り出し、枚数・MIME・サイズを検証する。既存の単一画像入力もここへ正規化する。
- `classify_intent`: ツール呼び出しまたは JSON スキーマで `intent`、各 `auto` の役割、プロンプト、数値提案を返す。スキーマ外は 1 回だけ再生成し、だめならデフォルト計画に落とす。
- `resolve_roles`: 明示役割を優先して上書きする。衝突を検出する。
- `select_workflow`: 純粋関数。テンプレート欠如は error にして終了する。
- `build_plan`: 数値を許容範囲へクランプする。
- `confirm_gate`: `needs_confirmation` が真なら interrupt。ユーザー回答で役割を更新して `select_workflow` に戻す。
- `upload_images`: ComfyUI `/upload/image`。同一 sha256 は再送しない。
- `inject_and_queue`: ノード ID マップに従い代入し `/prompt`。
- `poll_result`: `/history/{prompt_id}` を 1 秒間隔で最大 180 秒。完了画像を `/view` で取得する。
- `respond`: 使用テンプレート、役割、seed、画像をメッセージと artifact として返す。

確認が必要な条件:

- `auto` が 2 枚以上で、分類の確信度が閾値未満
- character と style が同一画像に要求された
- 必須スロットが欠けている
- 選択テンプレートが現在のモデル系統に存在しない

確信度閾値の初期値は 0.65。LM Studio が確信度を返さない場合は、曖昧語（「いい感じ」「合わせて」「それっぽく」）かつ役割未指定が 2 枚以上なら確認にする。

### 4.7 パラメータクランプ

| 項目 | 許容 | 既定 |
| --- | --- | --- |
| width / height | 512〜1536、64 の倍数 | 1024 |
| steps | 4〜40 | チェックポイント既定。Lightning 系は 8 |
| cfg | 1.0〜8.0 | チェックポイント既定 |
| denoise | 0.15〜0.85 | 0.55。画風のみの i2i は 0.45、ポーズ変更を伴う i2i は 0.65 |
| style strength | 0.2〜1.0 | 0.55 |
| pose strength | 0.3〜1.2 | 0.80 |
| character strength | 0.4〜1.2 | 0.85 |
| 添付 | png / jpeg / webp、1 枚 10 MB、辺 4096 以下 | - |

ユーザー指定が範囲外ならクランプし、応答に「指定値を範囲内へ調整した」と書く。

### 4.8 ComfyUI クライアント

既存クライアントがあれば拡張し、新規並列クライアントを作らない。

必要メソッド:

- `upload_image(bytes, filename) -> comfy_filename`
- `queue_workflow(workflow) -> prompt_id`
- `get_history(prompt_id) -> status, images`
- `fetch_image(filename, subfolder, type) -> bytes`
- `interrupt(prompt_id)`（対応時のみ）

ワークフロー注入は次のマップで行う。

```yaml
template_id: character_pose_style_t2i
model_family: sdxl
nodes:
  positive: "6"
  negative: "7"
  latent: "5"
  seed: "3.inputs.seed"
  character_image: "21"
  pose_image: "22"
  style_image: "23"
  character_weight: "24.inputs.weight"
  pose_strength: "25.inputs.strength"
  style_weight: "26.inputs.weight"
```

ノード ID はテンプレート JSON と一緒にレビューし、コードへ散らさない。

### 4.9 LM Studio 入出力契約

分類ノードの出力は次の JSON のみを採用する。前後の文章は捨てる。

```json
{
  "intent": "character_pose_style",
  "role_guesses": [
    {"image_id": "img_1", "role": "character", "confidence": 0.82}
  ],
  "positive": "...",
  "negative": "...",
  "pose_preprocessor": "dwpose",
  "denoise": 0.6,
  "needs_confirmation": false,
  "confirmation_question": null
}
```

システムプロンプトの要点:

- 役割は定義済み 6 種のみ
- 画風と同一性を混ぜない
- ポーズ参照から顔をコピーしない
- 数値は提案のみで、最終クランプはサーバが行う
- ワークフロー名を発明しない

ビジョン非対応モデルの場合、`role_guesses` は空でよい。明示役割が揃っていれば生成を続ける。揃っていなければ確認する。

### 4.10 UI

agent-ui の添付を複数化する。各添付に次を持たせる。

- サムネイル
- 役割セレクト（既定 `auto`）
- 任意の強度
- 削除

送信ペイロードはテキストと content block に加え、役割メタデータを別チャネルで渡す。画像 block の `metadata.role` を第一候補とする。既存 UI が metadata を落とせない場合は、メッセージ先頭に機械可読な行を付けず、スレッド config の `configurable.references` に渡す。人間可読な印をプロンプト本文へ混ぜない。

生成前確認は LangGraph interrupt とし、UI は選択肢付きメッセージで受ける。

- 提案役割のまま実行
- 役割を修正して実行
- 中止

結果表示にはテンプレート ID、役割、seed、使用強度を折りたたみで付ける。

### 4.11 互換

- 画像なし: 現行 `t2i_basic` と同一結果になること。プロンプト整形を挟む場合は既存挙動をフラグで維持できること。
- 画像 1 枚かつ役割未指定かつ修正語（「直して」「変えて」「背景を」）: `base` とみなし `i2i_basic`。
- 画像 1 枚かつ「この画風で新規」: `style` とみなし `style_t2i`。

### 4.12 エラー

| 条件 | ユーザー向け文言の方針 |
| --- | --- |
| ComfyUI 接続不可 | ローカル ComfyUI が起動していない旨と URL |
| テンプレート不足 | 不足ノード名と、今回選べた代替があれば代替 |
| アップロード失敗 | 枚数と形式の条件 |
| キュー失敗 | ComfyUI の node_errors を要約。JSON 全文はログのみ |
| タイムアウト | prompt_id を返し、再取得できること |
| LM Studio 失敗 | 明示役割があれば生成継続、無ければ確認 |

## 5. データとファイル配置

既存構成を壊さない範囲で次を追加する。

```text
workflows/
  sdxl/
    t2i_basic.api.json
    i2i_basic.api.json
    style_t2i.api.json
    pose_t2i.api.json
    character_pose_t2i.api.json
    character_pose_style_t2i.api.json
  flux/
    ... 環境にあるもののみ
  maps/
    sdxl.yaml
    flux.yaml
app/
  graph/
    state.py
    nodes/
    workflow_select.py
  comfy/
    client.py
    injector.py
tests/
  test_workflow_select.py
  test_injector.py
  test_role_resolution.py
```

パスは調査結果の実ディレクトリに合わせて読み替える。名前の責務は維持する。

## 6. 実装上の制約

- 新規ウェブフレームワークを導入しない。
- ComfyUI カスタムノードの自動インストールを実装しない。検出とエラーメッセージに留める。
- 生成画像の外部送信を追加しない。
- プロンプトインジェクション対策として、参照画像のファイル名を LLM プロンプトへ連結しない。
- ワークフロー JSON の実行前に、未知のノード type が無いことを検証する。
- seed、強度、役割はレスポンスに残し、再生成が可能であること。

## 7. テスト

### 7.1 単体

- 役割の組み合わせからテンプレート ID が一意に決まる
- 衝突時に確認フラグが立つ
- ノードマップの全キーがテンプレートに存在する
- クランプが上下限と 64 倍数を守る
- 同一 sha256 は再アップロードしない

### 7.2 契約

- ComfyUI をモックし、`/upload/image` と `/prompt` のボディを検証する
- キュー失敗時に画像バイナリがログへ出ない
- LM Studio が壊れた JSON を返しても 1 回リトライし、その後デフォルトへ落ちる

### 7.3 手動受け入れ

調査で把握した実チェックポイントを使い、第3.3章の 6 ケースを実行する。各ケースで次を記録する。

- 選ばれたテンプレート ID
- 各画像の解決後役割
- 生成の成否
- 既存テキスト生成と単一画像修正の成否

手動ケースは ComfyUI または必要ノードが無い環境ではスキップ理由を残し、単体とモック契約は通す。

## 8. 作業分解

実装 AI はこの順で行う。

1. 第2章を実施し、第11章を埋める。現行シーケンスを 2 本書く。
2. 既存生成経路を壊さず、画像入力を `references` へ正規化する。
3. `select_workflow` と役割解決、クランプを純粋関数で実装し、単体テストを先に通す。
4. 現行テキスト生成と単一画像修正をテンプレート化する。出力が現行と同等であることを確認する。
5. ComfyUI の実環境で、style、pose、character の最小 API JSON を 1 本ずつ書き出し、ノードマップを作る。ノードが無いものはカタログに入れない。
6. upload、inject、queue、poll を既存クライアントへ実装する。
7. LangGraph にノードを追加し、曖昧時の interrupt を結線する。
8. LM Studio の分類出力をスキーマ検証する。ビジョン非対応でも明示役割で通るテストを書く。
9. UI を複数添付と役割セレクトに拡張する。metadata が落ちる場合は `configurable.references` を使う。
10. 三点参照テンプレートを追加する。
11. 第7.3章を実施し、結果を調査結果の下へ残す。
12. 既存機能の退行確認後、変更ファイル一覧と既知の未対応を報告する。

完了報告に含めるもの:

- 変更ファイル
- 追加テンプレートと、環境に無く見送ったテンプレート
- テスト結果
- 手動 6 ケースの合否
- 残リスク

## 9. リスクと扱い

| リスク | 扱い |
| --- | --- |
| チェックポイント系統が実装途中で変わる | テンプレートを系統別ディレクトリに分ける |
| InstantID と PuLID のどちらも無い | character を未対応として返し、style と pose は使えるようにする |
| IP-Adapter が顔まで写す | style 用と character 用のノードを分ける。style に FaceID モデルを使わない |
| ポーズ抽出が失敗する | preprocessor のプレビューをレスポンスに含め、失敗時は生成しない |
| VRAM 不足 | 三点参照前に推定使用を出し、失敗時は style を外した再実行を提案する |
| agent-ui が複数画像 metadata を捨てる | configurable チャネルを予備にする。本文への機械行混入は禁止 |
| 現行ノード ID 直書きが残る | テンプレート化の際に呼び出し元から除去する |

## 10. 受け入れ条件

- 第3.3章のケース 1〜5 が、対応ノードのある環境で実行前要約と画像を返す。
- ケース 6 は生成せず確認を返す。
- 画像なし生成と単一画像修正が維持される。
- 役割セットに対するテンプレート選択がテストで固定されている。
- ワークフロー JSON を LLM が生成している経路が無い。
- 外部画像 API の呼び出しが追加されていない。

## 11. 調査結果（実装 AI が記入）

調査日: 2026-10-04。ComfyUI 0.38.0 を `scripts/start-comfyui.ps1` で起動し、`/object_info` を取得して確認した。

| 項目 | 結果 | 根拠ファイル |
| --- | --- | --- |
| フロントの実体 | `langchain-ai/agent-chat-ui`（upstream cf72cb0 を vendoring、Next.js、`@langchain/langgraph-sdk` 1.11）。AI 画像描画のため `ai.tsx` のみ改変済み | `agent-chat-ui/`, `agent-chat-ui/package.json` |
| LangGraph のエントリ | `langgraph.json` の `graphs.agent` → `src/furry_agent/graph.py:graph`。Python 3.12、langgraph>=1.0、`langgraph dev --host 0.0.0.0 --port 2024` | `langgraph.json`, `pyproject.toml`, `scripts/start-langgraph.ps1` |
| state 定義 | `State(MessagesState)` に `progress_id` `job` `tags` `error`。参照画像の状態は持たない | `src/furry_agent/graph.py` |
| 現行 t2i 経路 | `ingest → submit → await_tags → await_image`。`workflows/furry_ja_api.json` を `workflow.build_prompt` が参照ノード除去して投入 | `graph.py`, `workflow.py` |
| 現行 i2i 経路 | 同じグラフ。画像 1〜2 枚で 1 枚目を `ref_scale → VAEEncode`（denoise 0.45）、全画像を `vision` でタグ化して `prompt_join` で連結。inpaint 無し | `workflow.py` |
| ComfyUI URL とクライアント | `COMFYUI_URL`（既定 `http://127.0.0.1:8188`）。`ComfyClient` が `/upload/image` `/prompt` `/ws` `/history` `/view` `/queue` `/free` を実装。完了待ちは WebSocket + history フォールバック、タイムアウト `COMFYUI_TIMEOUT_S`=600。同時実行は `asyncio.Lock` とキュー空き待ちで 1。リトライ無し。interrupt 未実装 | `comfy_client.py`, `config.py` |
| LM Studio URL とビジョン可否 | `http://127.0.0.1:1234/v1`、`LMSTUDIO_MODEL`=`huihui-qwen3.8-27b-abliterated@iq3_m`（mmproj 付き VLM）。**LangGraph からは呼ばない**（AGENTS.md の禁止事項）。呼ぶのは ComfyUI の LM_Connect ノード（Vision は最大 5 画像、prompt は JSON パース） | `AGENTS.md`, `workflows/furry_ja_api.json` |
| 添付画像の現行形式 | agent-chat-ui の `{"type":"image","mimeType","data":base64,"metadata":{"name"}}`。`metadata` は送信時に保持される。上限 2 枚、PNG/JPEG/WebP/GIF、サイズ上限無し | `media.py`, `agent-chat-ui/src/lib/multimodal-utils.ts` |
| 使用チェックポイント系統 | SDXL 系（Illustrious / Pony 系 furry）。既定 `yiffInHell_yihVANTABLACK.safetensors`。他に SDXL 系多数と `chroma_v10HD`（Flux 系、未設定） | ComfyUI `models/checkpoints` |
| 利用可能カスタムノード | 調査時点は `LM_Connect`、`furry_ja` のみ。IP-Adapter / InstantID / PuLID / ControlNet preprocessor 無し、controlnet・clip_vision・ipadapter のモデル 0 件。LoRA は 11 本（`LoraLoader` 組込み）。利用者の許可を得て `scripts/setup-comfyui-refs.ps1` で ComfyUI_IPAdapter_plus、comfyui_controlnet_aux、ControlNet Union promax、IP-Adapter Plus SDXL、CLIP-ViT-H、DWPose、Depth Anything V2 Small を追加した | `/object_info`、`models/` |
| interrupt の既存利用 | 無し。agent-chat-ui は HITL 形式（`action_requests` / `review_configs`、resume は `{decisions:[...]}`）を描画できる | `agent-chat-ui/src/lib/agent-inbox-interrupt.ts` |
| 出力画像の返却方法 | `/view` で取得したバイトを base64 の image block として AI メッセージに載せる。`outputs/` に複製と JSON メタ | `graph.py` |

### 現行シーケンス（記入欄）

テキスト生成:

1. agent-chat-ui が human メッセージ（text block）を送る。
2. `ingest` が本文を取り出し、進捗メッセージを返す。
3. `submit` がキュー空きを待ち、`/free` で ComfyUI のモデルを解放する。
4. チェックポイント存在を `/object_info` で確認し、`furry_ja_api.json` から参照ノードを除いた API JSON を作る。
5. `user_prompt` と seed を書き換え、`/prompt` に投入する。
6. `await_tags` が `/ws` で `split` の実行を待ち、positive/negative を進捗に出す。
7. ComfyUI 内で LLM → eject → ckpt（LM Studio unload を検証）→ KSampler。
8. `await_image` が完了を待ち、unload 検証済みを確認、`/view` で画像取得、`outputs/` に保存し、image block で返す。最後に `/free`。

単一画像修正:

1. human メッセージに image block が 1 枚付く。
2. `submit` が `/upload/image`（subfolder `furry_ja`）で上げる。
3. `ref_image` にファイル名、`vision` が画像をタグ化、`prompt_join` で日本語指示と連結。
4. `latent` を `VAEEncode(ref_scale)` に差し替え、denoise 0.45。
5. 以降はテキスト生成の 5〜8 と同じ。

### 設計との差分（記入欄）

- 差分 1: 本書 §4.2/§4.6 の `classify_intent` は LangGraph から LM Studio を呼ぶ前提だが、AGENTS.md は LangGraph → LM Studio の直接呼び出しと、ComfyUI グラフ外での LLM ロード（27B とチェックポイントの同時常駐の危険）を禁じている。
- 吸収案 1: `classify_intent` は LangGraph 内の決定的なルールベース分類（日本語キーワード、序数「1枚目」「A/B/C」、曖昧語）にする。確信度はルールから算出し、閾値 0.65 で確認 interrupt。LLM の責務（プロンプト整形、各画像の役割別タグ化）は ComfyUI 内の LM_Connect が行い、unload 順は従来どおりグラフが決める。NFR-3 の「LLM 障害時も明示役割で継続」は常に満たす。
- 差分 2: 調査時点では IP-Adapter / ControlNet（ノード・モデル）が環境に無かった。利用者の指示（「ControlNet などが必要であれば追加してよい」）により `scripts/setup-comfyui-refs.ps1` で導入した。InstantID / PuLID は人の顔向けで furry キャラクターに合わないため入れていない。
- 吸収案 2: character は IP-Adapter Plus（weight = 強度 × 0.5）、style は IP-Adapter の `style transfer`、pose は DWPose（人物検出なし、ONNX を CPU で実行）/ Depth Anything V2 / Canny → ControlNet Union promax。どの役割も役割専用の Vision システムプロンプトでタグ化し、LLM の統合入力へ節として渡す。mask は `ImageToMask` + `SetLatentNoiseMask`。IP-Adapter・ControlNet・前処理のローダーはすべて `ckpt`（eject 後）に依存させ、27B と同時に載らないようにした。
- 差分 2b: 実機（Radeon 890M / ROCm、ComfyUI 0.38）で次を確認し対処した。IP-Adapter weight 0.85 で色焼け → キャラクターは強度 × 0.5。同一プロセス 2 回目以降の IP-Adapter 生成が破損 → ComfyUI を `--cache-none` で起動。DWPose の TorchScript が GPU で不安定 → ONNX を CPU。GPU 予算超過で UNet が全オフロード → KSampler 直前でエンコーダを外す `FurryJaReleaseEncoders`。
- 差分 3: 本書 NFR-5 のタイムアウト初期値 180 秒、§4.7 の既定解像度 1024 と i2i 既定 denoise 0.55 は、AGENTS.md / 設計書（10 分、832×1216、img2img 0.45）と食い違う。ComfyUI 連携は設計書優先のため、既定値は設計書側を維持し、`COMFYUI_TIMEOUT_S` で変更可能とする。WI の denoise 規則は役割付きテンプレート（pose を伴う i2i 0.65 相当、inpaint 0.75）に適用する。
- 差分 4: 参照画像の上限が AGENTS.md では 2 枚。利用者の今回の依頼（複数画像）と本書 FR-1 に従い 4 枚に拡張し、AGENTS.md と README を更新する。
- 差分 5: ノードマップは YAML ではなく JSON（`workflows/maps/sdxl.json`）。PyYAML を新たな実行時依存にしないため。
- 差分 6: GIF 添付は現行で受け付けているため維持する（本書は PNG/JPEG/WebP）。
- 差分 7: LoRA（本書の非目標は「LoRA の学習」のみ）。利用者の依頼により、`.env` の `LORAS` で指定した LoRA を全テンプレートの `ckpt` 直後に挿入する。
- 差分 8: タイムアウトは投入から一括ではなく、タグ生成と画像生成のそれぞれに 600 秒。キャラクター + ポーズで LLM 段だけで 7 分超かかったため。
- 実装しないこと: InstantID / PuLID テンプレート、Flux 系テンプレート（`COMFY_MODEL_FAMILY=flux` は未登録エラー）、実行時のカスタムノードやモデルの自動導入（セットアップスクリプトで事前に導入する）、LangGraph からの LM Studio 呼び出し。

### 手動受け入れ結果（§7.3、2026-10-04）

実機: ROG Ally X（Radeon 890M / ROCm）、ComfyUI 0.38（`--cache-none`）、LM Studio Qwen3.8 27B IQ3_M、yiffInHell VANTABLACK。
参照画像はこのリポジトリの過去の生成画像（キャラクター: 赤狐の冒険者、ポーズ: 椅子で読書する狼）と、OilDaftV1 LoRA で生成した油彩風景（画風）。
各ケースで `ckpt gate: LM Studio unloaded=[True]` と `eject verified` をログで確認した。

| ケース | 入力 | テンプレート | 解決後の役割 | 結果 | 所要 |
|---|---|---|---|---|---|
| 1 画風 | 油彩風景 + 「この絵の雰囲気で、夜の神戸港に立つキャラクター」 | `style_t2i` | style | 成功。配色と筆致が移り、山は写らない。夕焼けの色に寄り「夜」が弱い → 画風タグから時間帯・照明を除くようプロンプトを修正 | 6.5 分 |
| 2 ポーズ | 読書する狼 + 「このポーズで、白衣のケモノキャラ。背景はシンプル」 | `pose_t2i` | pose | 成功。座り・体の向き・本の位置が骨格どおり、白衣、無地背景 | 6.9 分 |
| 3 キャラ入れ替え | 赤狐 + 読書する狼 + 「このキャラをこのポーズにして」（UI で役割を選択、他ホスト相当の LAN アドレス経由） | `character_pose_t2i` | character, pose | 成功。赤狐の毛色・フード・ベルトで座りポーズ。持ち物は本ではなくカメラになった | 8.2 分 |
| 4 ベース + 画風 | 赤狐（焚き火）+ 油彩風景 + 「構図は維持して画風だけ寄せて」 | `style_i2i` | base, style | 成功。構図・月・焚き火を保ったまま暖色の平塗り調へ | 8.9 分 |
| 5 三点参照 | 赤狐 + 読書する狼 + 油彩風景 + 「AのキャラをBのポーズ、Cの画風で」 | `character_pose_style_t2i` | character, pose, style | 成功。ポーズと画風は反映。画風の配色に引かれ毛色が淡くなる | 12.6 分 |
| 6 曖昧 | 2 枚 + 「いい感じに合わせて」 | （確認） | 提案 character, pose | 生成せず確認カード。UI で承認すると `character_pose_t2i` で続行 | 即時 |
| 既存 t2i | 「夕焼けの海辺に立つ、白い毛並みの狼獣人の女性、和服」 | `t2i_basic` | - | 成功 | 3.7 分 |
| 既存 i2i | 赤狐 + 「この子を和服で」 | `i2i_basic` | base | 成功（denoise 0.45、構図維持） | 5.7 分 |
| LoRA | `LORAS=OilDaftV1:0.8` + t2i | `t2i_basic` + `lora_1` | - | 成功。同じ指示で LoRA 無しより塗りが平坦・絵画的 | 4.8 分 |

残リスク:

- キャラクター参照は IP-Adapter Plus（非 FaceID）と Vision タグによる近似で、細部の同一性（模様の位置など）は保証しない。強い画風参照と併用すると配色が画風側へ寄る。
- 三点参照は GPU 予算（空き約 6.8GB）の上限近くで動いており、所要は 12 分台。
- ComfyUI_IPAdapter_plus は 2025-04-14 のコミットを最後に更新が止まっている（固定したのもこのコミット）。ComfyUI 0.38 では `--cache-none` が必須だった。ComfyUI 更新時は再確認が要る。
- 役割推定はルールベースのため、想定外の言い回しでは確認カードに落ちる（誤って生成するより確認を優先する設計）。
- ブラウザを閉じると LangGraph の run がキャンセルされる。待ち（await_tags / await_image）中なら ComfyUI の prompt も中断する。投入直前にキャンセルされた場合は ComfyUI 側で 1 件走り切ることがある。

## 12. 参照した外部事実

設計判断の根拠としてのみ使う。実装の必須依存ではない。

- agent-chat-ui は LangGraph サーバの `messages` とチャットし、画像と PDF の添付を content block として送れる。
- `@assistant-ui/react-langgraph` は LangGraph の stream、thread、interrupt を UI runtime に接続する。
- ComfyUI はワークフロー JSON とローカル API を持つ。複数条件は IP-Adapter 系（スタイル / 同一性）と ControlNet 系（ポーズ / 構造）を分けるのが現行の安定手法。
- InstantID / PuLID は同一性、ControlNet OpenPose は骨格、IP-Adapter style は画風、という分離がキャラクター入れ替えの基本形。
- 2026-10 時点の Comfy Agent はクラウド側のワークフロー自動構築であり、ローカル LLM 対応はロードマップ。本作業のローカル実行要件とは分け、採用しない。
