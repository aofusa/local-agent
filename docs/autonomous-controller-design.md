# チャット制御ループ（自律ツール選択）設計書

状態: 実装済み（v0.7.0、末尾の §16 に実装記録）
対象: `aofusa/local-agent` のチャットグラフ `chat`
目的: チャット内容から、実装済みの検索・執筆・コード・画像引き渡しを自分で選び、結果を見て次の手を決める。Grok / Claude のエージェントループの縮小版。
実装者向け。この文書だけで着手できる粒度にしてある。

## 0. 結論

グラフは増やさない。`langgraph.json` の `chat` のまま、`ingest` / `route` の後に制御ノード `controller` を足す。

既存の検索パイプライン、執筆パイプライン、コードパイプラインは道具として残す。制御側はそれらを選び、結果の要約だけを見て、次を決めるか終了する。新しい検索器や新しい画像生成経路は作らない。

自由な ReAct やネイティブ `tool_calls` にはしない。判断は既存の `ask_json` で JSON を返させる。実行、モデルの unload、`job_lock`、予算はランタイムが持つ。

## 1. 背景と現状

チャットタブは `src/furry_agent/chat_graph.py` の 1 本のグラフである。入口で種別を一度決め、その後は固定パイプラインを走る。

入口の種別（`src/furry_agent/router.py`）:

- `chat`
- `search`
- `write`
- `code`
- `image_tab`（`TO_IMAGE_TAB`）

明示は接頭辞 `/search` `/write` `/code` `/chat` または `configurable.task`。それ以外はキーワード規則、曖昧なら 1.7B ルータ。

既存ノード:

- 共通: `ingest` → `route` → `chat`
- 検索: `plan` → `search` → `filter` → `read` → `judge` → `critique` → `synthesize`
- 執筆: `write_brief` → `write_draft` → `write_revise` → `chapter_confirm`（`write_nodes.add_nodes`）
- コード: `code_plan` → `write_files` → `confirm_run` → `sandbox_exec` → `observe`（`code_nodes.add_nodes`）

条件エッジ: `_after_ingest`、`_after_route`、`_to_search`、`_to_read`、`_after_reading`、`_after_critique`、`_after_synthesize`。

検索は既に「計画 → 検索 → フィルタ → 読む → 批評 → 統合」のマルチステップである。足りないのは、検索の外で「調べてから書く」のように道具を横断して選び直す制御である。`search_first` は執筆の前に検索へ直行する固定結合であり、結果を見て道具を変えることはできない。

## 2. 制約（破らない）

`AGENTS.md` と実装の前提。実装者が緩和してはならない。

1. グラフ ID は増やさない。`chat` の中で分岐する。`langgraph.json` にグラフを足さない。
2. 画像タブのグラフ `agent` / `image`（`src/furry_agent/graph.py`）のノード ID と ComfyUI ワークフロー契約は変更しない。
3. LangGraph から MCP や LM Studio のチャット GUI 経由で ComfyUI を叩かない。画像生成は既存の `agent` グラフだけが行う。
4. 27B と SDXL / Flux チェックポイントは同時常駐しない。検索リーダーを載せる前も 27B を unload する既存順を守る。
5. 画像タブとチャットタブは `job_lock` で直列。制御ループがロックを二重に取らない。既存ノードが取るロックを尊重する。
6. ループバック以外に待受を開かない。検索の通信は Tor SOCKS `127.0.0.1:9050` のまま。
7. コード実行は既存サンドボックス（Docker、`--network none` が基本、2GB / 60 秒 / 非 root）の外へ出さない。制御ノードからシェルを呼ばない。
8. UI の変更は、既存の手順表示・思考表示に制御の `reason` を出すまでに限る。`frontend/` は作らない。
9. ノード名 `observe` はコードパイプラインが使用中。制御側の記録ノードに同じ名前を付けない。

## 3. 真似る範囲

Claude Code と Grok の共通点は、モデルがループを所有しないことである。ハーネスがループを持ち、モデルは「次の道具と引数」か「終了」だけを返す。

採用する:

- 道具は許可リストのみ。モデルに任意コードを書かせない。
- 専門処理は既存パイプラインに閉じ、制御側へは要約と引用だけ返す。
- 同一引数の再実行を禁止する。ラウンド、壁時計で打ち切る。
- ユーザー向け本文は最後の `final` だけ。道具の生出力をそのまま返さない。

採用しない:

- ネイティブ function calling への全面移行。abliterated Qwen では形式が崩れやすい。第一段は `search_agent.ask_json`。
- 外側の並列ツール呼び出し。VRAM と `job_lock` の都合で制御ループは直列。並列は検索ファンアウトの内側だけ。
- コンピュータ操作、任意 bash、MCP。
- 長期記憶やベクトルストア。この設計の範囲外。

## 4. 対象外と直行を残すもの

全部を制御ループに入れない。明示の単発は今の経路へ直行させ、遅延と VRAM を増やさない。

直行（`controller` に入らない）:

- 接頭辞 `/chat` `/search` `/write` `/code`、または `configurable.task` が単発を指定している。
- `route.explicit` が真。
- 添付メディア、または描画語による `image_tab`。今と同じく画像タブへ案内して `END`。
- キーワードだけで種別が一つに決まり、接続語がない。接続語の例: 「調べてから」「根拠を確認して書いて」「検索して手順書」。

制御ループに入る:

- 二つ以上の種別が同時に読める。
- 「調べてから書く」「確認してメモ」「動くか試して直して」のように、結果を見て次の道具が変わり得る。
- `configurable.mode` が `think` または `auto` で、ルータが複合と判定した。

`fast` は制御ループに入れない。複合に見えても、今の単発経路（検索なら 1 intent、執筆なら brief なしの草稿）へ落とす。思考トークンと複数往復は `think` だけ。

## 5. 制御ループ

### 5.1 ノード

追加するノードは二つ。

- `controller`: 27B（検索と同じく、リーダーが乗らないときは代理の Ternary-Bonsai-2-27B）に判断させる。
- `controller_record`: 道具から戻った要約を `trace` に積み、予算を確認して `controller` へ戻すか `finish` する。

既存パイプラインはノードを増やさない。戻り先だけを、制御から入ったときだけ変える。

### 5.2 遷移

```
START → ingest
ingest → 既存の直行 | route | END
route  → 既存の直行 | controller | END
controller → plan | write_brief | code_plan | finish | END
synthesize → controller_record   （control.active のときだけ。否则は現状の write_brief | END）
write 系の終端 → controller_record （control.active かつ chapter_confirm を使わないとき）
code 系の終端 → controller_record （control.active のとき。confirm_run の割り込みは残す）
controller_record → controller | finish
finish → END
chat → END （現状どおり）
```

`finish` は新しいノードでよい。制御が既に `answer` を持っていればそれをユーザー向けメッセージにし、ロックを解放して終わる。通常の `chat` ノードは単発会話専用のまま残す。

画像は第 1 段では道具にしない。`controller` が `image` を返したら、今の `image_tab` と同じ案内メッセージを出して `END` する。第 3 段まで `graph.py` を呼ばない。

### 5.3 なぜサブグラフ呼び出しにしないか

執筆の `chapter_confirm` とコードの `confirm_run` は human-in-the-loop の interrupt である。ノード関数から別グラフを `invoke` すると割り込みが親に届かない。

そのため道具の実行は、既存の入口ノードへエッジで渡す。パイプラインの終端が、`control.active` のときだけ `controller_record` に戻る。

割り込みが必要な執筆（`route.long`）とコード実行確認は、戻りの途中で従来どおり止まる。利用者の承認後にパイプラインが終われば `controller_record` に戻る。割り込みのカード仕様は変えない。

## 6. 状態

`ChatState` に `control` を足す。既存フィールドの意味は変えない。

```python
class Control(TypedDict, total=False):
    active: bool                 # この実行が制御ループか
    steps: int                   # 道具を実行した回数。判断だけは数えない
    max_steps: int               # 既定 3。think でも 4 を超えない
    started: float               # time.monotonic()
    max_wall_clock_s: int        # settings から。検索の壁時計とは別
    decision: dict               # 直近の Decision
    trace: list[dict]            # {tool, args_hash, summary, ok}
    answer: str                  # final の本文
    stop_reason: str             # final | max_steps | wall_clock | repeat | error
```

`trace` の 1 要素は 1KB を目安に切る。検索の `hits` やページ本文は入れない。それらは検索状態の中に残し、制御へは `synthesize` の統合文と引用 URL だけを上げる。

`route` は道具に入る直前に、その道具向けへ書き換えてよい。戻ったあとに元の利用者文は `control` ではなく `messages` から読む。`route.text` を利用者の原文で潰したままにしないこと。道具に渡すテキストは `decision.args.text` を `route.text` にコピーする。

## 7. 判断スキーマ

`search_agent.ask_json` に渡す。未知フィールドは捨てる。

```python
class Decision(BaseModel):
    action: Literal["tool", "final"]
    tool: Literal["search", "write", "code", "image", "none"] = "none"
    text: str = ""          # その道具への依頼文。利用者の原文の複写でよい
    reason: str = ""        # 手順表示用。本文に混ぜない
    answer: str = ""        # action=final のときだけ。利用者向け
```

検証:

- `action=tool` なら `tool` は `none` 以外、`text` は空でない。
- `action=final` なら `answer` が空でない。空なら 1 回だけ再問合せし、まだ空なら `trace` の最後の要約を本文にする。
- `tool=image` は第 1 段では実行せず、画像タブ案内で終了。
- `args` にコマンドやファイルパスを自由記述させない。コードのコマンドは既存の `code_plan` が決める。

再実行禁止:

- `tool + 正規化した text` の sha256 を `trace` と比較する。
- 一致したらその判断を破棄し、`stop_reason=repeat` で `finish`。利用者には最後の要約で答える。

## 8. プロンプト

新規ファイル: `prompts/chat/controller.txt`。

制御プロンプトにタグ生成や検索批評の技能文を混ぜない。技能プロンプトは各パイプラインが今までどおり自分で読む。

プロンプトに書くこと:

- 使える道具は search、write、code、image だけ。image は画像タブへ渡すだけで、ここでは生成しない。
- 知らない事実、最新の情報、引用が要るときは search を先に呼ぶ。
- 道具の結果が出る前に、その結果が必要な文書やコードを作らない。
- 同じ依頼文で同じ道具を二度呼ばない。足りなければ依頼文を変える。
- 材料が揃ったら `final`。道具を呼ぶこと自体を目的にしない。
- `reason` は手順ログ。利用者向けの文は `answer` だけ。
- 出力は JSON のみ。思考トークンは既存の think モードの分離に従い、本文へ混ぜない。

入力として渡すもの:

- 利用者の最新メッセージ。
- `trace` の要約（各 500 字以内）。
- 残りステップ数。

渡さないもの:

- 検索ヒットの生 HTML、カードの全件、コードの標準出力全文、画像バイト。

## 9. 道具の契約

制御が既存入口へ渡すものと、戻ってきたときに `controller_record` が残すもの。

### search

入口: `plan`。`route.kind = search`、`route.text = decision.text`。`search` 状態は既存の `plan` が初期化する。制御から入ったときは `search_first` を立てない（執筆へ自動では進ませない）。

戻り: `synthesize` の統合文、引用 URL、`search.stop_reason`。`cards` 全文は制御へコピーしない。

失敗: 既存の `StageError` を `trace` に `ok=false` で積み、`controller` に戻す。同じ text の再検索は禁止なので、制御は書き換えるか `final` する。

### write

入口: `write_brief`。`route.kind = write`、`route.text = decision.text`。直前に検索していれば `route.search_skipped` を立て、執筆がもう一度検索へ戻らないようにする。検索結果の統合文は `artifact` の材料として既存の brief 生成が読める位置に置く。既存の `writing` が messages から材料を読むなら、統合文を progress ではない通常メッセージとして 1 件足す。

戻り: `artifact.draft` の先頭 800 字と `artifact.status`。本文そのものは既存ノードが利用者向けメッセージとして出しているので、`finish` で二重に全文を出さない。制御が最後なら「執筆した」旨と成果物の所在だけを足す。

`route.long` の章確認は従来どおり interrupt する。

### code

入口: `code_plan`。`route.kind = code`、`route.text = decision.text`。

戻り: `code.last_ok`、`code.last_step`、`stdout_tail` / `stderr_tail` の末尾 400 字。ファイル本文は戻さない。

`confirm_run` の承認カードは残す。制御が承認を飛ばす経路を作らない。

### image（第 1 段）

実行しない。`TO_IMAGE_TAB` と同じ案内を出して終了する。`graph.py` は呼ばない。

## 10. 予算とモデル

- `max_steps`: think で 3。設定で 4 まで。fast は 0（ループに入らない）。
- 壁時計: 検索の `search_wall_clock_s` を超えない値を制御全体にも掛ける。検索の内部ラウンド予算は現状のまま（think は `settings.search_max_rounds`、fast は 1）。
- 制御の判断は 1 回あたり短くする。`max_tokens` は 400 程度。温度は 0.2。
- 27B を判断に使う前後で、既存の unload を通す。検索リーダーを起こすのは今の `filter` / `read` だけ。
- 制御ループの途中で画像チェックポイントをロードしない。

設定を足すなら `config.py` に次だけ。

- `controller_max_steps: int = 3`
- `controller_wall_clock_s: int`（未設定なら `search_wall_clock_s`）

環境変数名は既存の大文字スタイルに合わせる。

## 11. UI

`logs` または既存の手順メッセージに、道具を選んだ `reason` と道具名を足す。思考本文と利用者向け本文は混ぜない（`AGENTS.md` のモード規約）。

新しいタブや入力欄は作らない。`agent-chat-ui` を触るなら、既存の検索痕跡・手順表示に 1 行足すだけ。在庫 UI で足りるなら UI は変更しない。

## 12. 実装手順

この順で、各段が単体で動いてから次へ進む。

### 段 1: 判断と検索 1 回

1. `prompts/chat/controller.txt` と `Decision` を追加する。スキーマの置き場は `search_agent.py` の既存 JSON スキーマの隣、または `chat_common.py`。循環 import を避ける。
2. `controller` と `finish` を `chat_graph.py` に追加する。
3. `_after_route` を拡張し、複合かつ think のときだけ `controller` へ送る。明示単発は現状のまま。
4. `controller` の条件エッジは `plan` と `finish` だけ。
5. `synthesize` の `_after_synthesize` を拡張し、`control.active` なら `END` ではなく `controller_record`。
6. `controller_record` は要約を積んで `controller` に戻す。2 回目の判断は原則 `final` になることをテストで固定する。
7. 進捗文字列は既存の progress メッセージの形に合わせる。

完了条件: 「調べてから要点を答えて」が検索 1 回のあと `final` で終わる。`/search` 単発は `controller` を通らない。

### 段 2: 検索のあと執筆

1. `controller` のエッジに `write_brief` を足す。
2. 検索から戻った `trace` を執筆が読める形で messages に足す。
3. 執筆終端（`write_draft` / `write_revise` の END、および章確認の reject）が `control.active` のとき `controller_record` に戻る。
4. 戻った制御は文書を再生成せず `final` する。二重投稿をテストで禁止する。

完了条件: 「調べて手順書にして」が search → write → final の順になる。`/write` 単発は検索も制御も通らない。

### 段 3: コードと画像案内

1. `code_plan` へのエッジと、コード終端から `controller_record` への戻りを足す。`confirm_run` は残す。
2. `image` は案内メッセージで終了。`graph.py` は変更しない。
3. ノード名が `code_nodes.observe` と衝突していないことを確認する。

完了条件: コード依頼が承認カードを出したあと、既存サンドボックスの制約のまま終わる。画像依頼が画像タブ案内で終わる。

第 4 段（画像グラフの道具化）はこの設計の範囲外。やる場合も `graph.py` のノード ID は変えず、`job_lock` と 27B unload の後に既存グラフを 1 回呼ぶ別設計にする。

## 13. テスト

`tests/` の既存スタイル（pytest、外部モデルは偽物）に合わせる。LM Studio も Tor も起動しない。

必須:

- 明示 `/search` は `controller` ノードを通らない。
- 複合文かつ think は `controller` に入り、`Decision.tool=search` で `plan` に進む。
- 同じ text の 2 回目 search は実行されず `stop_reason=repeat`。
- `max_steps` 到達で `final` 以外の道具へ進まない。
- `control.active` のとき `synthesize` の次が `controller_record`。非 active のときは現状の `write_brief` または `END`。
- 執筆の単発が検索へ戻らない（`search_skipped`）。
- `image` 判断が `graph.py` を呼ばない。
- JSON が壊れたとき 1 回再問合せし、失敗なら `chat` 相当の短い失敗メッセージで終わる。利用者にスタックトレースを出さない。

既存の検索・執筆・コードのテストが段 1 以降も通ること。

## 14. 触ってよいファイルと禁止

触ってよい:

- `src/furry_agent/chat_graph.py`
- `src/furry_agent/chat_common.py`（スキーマを置く場合）
- `src/furry_agent/router.py`（複合判定の関数を足す場合だけ。既存の優先順は崩さない）
- `src/furry_agent/config.py`（設定 2 項目まで）
- `src/furry_agent/write_nodes.py` と `code_nodes.py` の終端条件だけ。割り込みの意味は変えない
- `prompts/chat/controller.txt`
- `tests/` の新規テスト
- `CHANGELOG.md` の 1 項目

触らない:

- `src/furry_agent/graph.py`
- `workflows/`
- `comfyui_nodes/`
- `langgraph.json` のグラフ追加
- 検索の Tor クライアント、サンドボックスの権限拡大
- `agent-chat-ui` の大幅改修

## 15. 完了の定義

- think の複合依頼が、許可された道具を 1〜3 回選び、要約を見て文章で終わる。
- 単発の接頭辞と fast の挙動が変わっていない。
- 27B とチェックポイントの同時常駐がない。ロックの取り方が増えていない。
- 失敗時に同じ道具を同じ文で再実行しない。
- 画像タブの経路がバイト単位で変わっていない（`graph.py` の diff が空）。

## 16. 実装記録（v0.7.0）

状態: 段 1〜3 を実装した（第 4 段の画像グラフの道具化は範囲外のまま）。

### 16.1 実装の形

- ノード: `controller` / `controller_record` / `finish` を `src/furry_agent/control_nodes.py` に置き、`write_nodes` / `code_nodes` と同じ `add_nodes(builder)` で `chat` グラフに足した（§14 の「触ってよい」に新しいモジュールを 1 つ足した。`chat_graph.py` の肥大を避けるため）。`Decision` のスキーマも同じモジュールに置き、循環 import を避けた。
- 入口: `router.is_compound`（`compound_kinds` が 2 種以上、または 1 種 + 接続語）と、思考モードであること。`ingest` が `control` を初期化し、`_after_ingest` が `controller` へ送る。ルータ（1.7B）を通る曖昧な文は、規則で道具の種類が 1 つも読めないので複合にならない（`_after_route` は変えていない）。
- 「自動」: `modes.auto_mode` に `compound` を足し、複合なら思考を選ぶ（「手短に」の指定が先に効く）。
- 道具の終端: 共通の `end_or_record(state)`（`chat_common.py`）が、`control.active` なら `controller_record`、そうでなければ `END` を返す。検索（`_to_search` / `_to_read` / `_after_reading` / `_after_critique` / `_after_synthesize` / `_after_drop`）、執筆（4 つの終端）、コード（5 つの終端）の `END` をこれに置き換えた。制御から入った検索は `search_first` を立てず、`_after_synthesize` も執筆へ直行しない。
- 失敗: 道具の `_fail`（`StageError` を含む）は `error` を立てたまま `controller_record` に戻り、`ok=false` で `trace` に積んでから `error` を消して `controller` に戻す。章の確認やコンテナ実行の却下（`error` が `stopped` / `rejected`）は、その時点で制御も終える（`stop_reason=rejected`）。
- メッセージ: 道具が出したメッセージは道具の `progress_id` のまま残す。`controller_record` が新しい `progress_id` を振り、以後の制御の進捗と最終回答は別のメッセージになる（同じ id だと道具の出力を上書きしてしまう）。
- 道具への入力: `route` を道具向けに書き換える（`decision.text`）。利用者の原文は `messages` の最後の human から読む。コードの `code_plan` は会話履歴を入力にするため、制御から入ったときだけ最後に `decision.text` を user として足した（`code_nodes._generate`）。執筆は、直前の検索の答え（出典付き、1500 字まで）を `artifact.research` に置き、`search_first` を立てて `write_brief` に読ませる（`search_skipped` も立てる）。
- 判断のモデル: LM Studio に届けば 27B、届かなければ代理リーダー（`_leader`）で、判断のあとすぐ止める。`ask_json`（JSON スキーマ付き、温度 0.2、最大 400 トークン）。`ask_json` 自体が 1 回だけ再問合せする。`final` の `answer` が空ならもう 1 回聞き、それでも空なら最後の要約で答える。
- 最終回答: 文章が最後なら本文を繰り返さず、書いた旨だけ（モデルの答えが 300 字以内ならそれも）。検索や コードが最後で、モデルの最終回答が無いまま止まったときは「上のメッセージのとおり」とだけ書き、止まった理由を添える（検索の答えを二重に出さない）。失敗で止まったときだけ最後の要約を出す。
- UI: `task_trace` に `kind: "control"`（手順ごとの道具・成否・理由・依頼文・要約、終了理由）を載せ、`TaskTraceView` を `control` に対応させた（1 箇所）。

### 16.2 実測と設計との差分

- この端末（27B IQ3_M、0.9〜1.5 トークン/秒）で「Rust の最新の安定版を調べてから、その要点を3行で教えて」（思考モード）: 判断 1 回目 111 秒で `search`（依頼文は英語の検索語に書き換えられた）→ 思考モードの検索（計画 7.6 分、2 ラウンド 6 ページ、主張の検証 4 + 一部 5）→ 検索の答えの時点で 20 分を超えたため `wall_clock` で終了（合計 1337 秒）。道具の答えは出典付きのメッセージとして残り、自律の手順に理由と要約が出た。
- §10 の壁時計（`SEARCH_WALL_CLOCK_S` を超えない）は守った。この端末では思考モードの検索 1 回が 20 分近くかかるため、検索のあとに 2 回目の判断が回らないことが多い。上限を緩めるのは設計の制約に反するので変えず、README に書いた。2 回目以降の判断（検索 → 執筆 → final、重複の禁止、最大手数）はフェイクのモデルでのテストで確認した。
- 判断は JSON だけを返す短い呼び出しだが、27B のロード込みで 1〜2 分かかる。

### 16.3 テスト

`tests/test_controller.py`（37 件）: 複合判定の表、接頭辞・添付・`/docs`・`task` は入らない、速いは入らない、自動は思考になる、`/search` は制御を通らない、検索 → final、要約にページ本文が入らない、同じ依頼文は `repeat`、最大手数、壁時計、`_after_synthesize` の分岐、JSON が壊れたら 1 回の再問合せで短い失敗文、空の final、検索の失敗が `ok=false` で残る、LM Studio が無いと代理で判断して止める、検索 → 執筆 → final（本文を二重に出さない、再検索しない）、章の却下で終了、コードの承認カードと戻り、実行の却下で終了、画像は `graph.py` を呼ばない、ノード名が `observe` と衝突しない、純関数。既存の検索・執筆・コードのテストは、フェイクの LLM に制御のプロンプトの応答を足しただけで、すべて通る。
