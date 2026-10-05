# チャットタブ拡張 設計書・作業指示書

> v0.11.0 から LM Studio は使っていない。本文の「LM Studio」「`LMSTUDIO_*`」は、llama.cpp のルータ（`LLM_*`。[llamacpp-router-design.md](llamacpp-router-design.md)）と読み替える。

対象リポジトリ: https://github.com/aofusa/local-image-gen-agent
文書の位置づけ: 実装担当（人または AI）へ渡す設計兼作業指示。本書の方針・制約・分岐・定数は省略せず実装に反映する。
作成日: 2026-10-04
状態: 実装前の設計指示。画像グラフと ComfyUI のノード契約は本改修の対象外。

本書は次の4要求を、既存の LangGraph チャットグラフの上に載せるための指示である。

1. より深い検索を行う。最初のプロンプトだけで検索して終わらせず、検索結果をもとにユーザのクエリや関心事に沿うようにさらに深く思考し、追加検索する。
2. チャットタブで小説や文章の作成など創造的なタスクを行わせる。
3. 創造的なタスクには文章の作成のほか、プログラムの作成と、安全な Docker コンテナ内でのコマンド実行を含める。
4. Grok の思考と速度のように、すばやい回答と思考を切り替えられるようにする。

---

## 0. 実装担当への絶対条件

- 本書に書いた分岐、状態フィールド、停止条件、サンドボックス制約、モード差、触ってはいけない境界は削らない。実装しやすいからといってラウンド上限を1回に戻したり、承認なし実行にしたり、画像グラフへ処理を混ぜたりしない。
- 画像生成グラフ (`src/furry_agent/graph.py`) と ComfyUI ワークフローのノード ID・eject 順・チェックポイント契約は変更しない。
- クラウド API は使わない。既存方針どおりローカルモデル、Tor 経由検索、ローカル Docker のみ。
- 実装前に実ファイルのシンボル名を読む。本書のパスは 2026-10-04 時点の調査に基づく。UI ファイル名がずれていたら、同等の composer と検索トレース表示を探して同じ契約で実装する。
- 各段階の完了条件を満たすまで次の段階に進まない。テストが既存の画像生成を壊していないことを確認する。

---

## 1. 現状の境界

いまのチャットグラフは「計画 → 検索 → フィルタ → 読解 → 批判を最大1回 → 統合」で止まっている。深い調査、創作、コード実行、速い／思考の切り替えは、画像グラフには足さず、チャットグラフをタスク種別で分岐させる。

### 1.1 既にあるもの

`langgraph.json` はすでにグラフを分けている。

- `agent`: `./src/furry_agent/graph.py:graph`
- `image`: `./src/furry_agent/graph.py:graph`（agent と同一）
- `chat`: `./src/furry_agent/chat_graph.py:graph`

画像側 `src/furry_agent/graph.py` は次の固定パイプラインである。

```text
START → ingest → plan → confirm → submit → await_tags → await_image → END
```

条件分岐は次のとおり。

- `ingest` の `_route_ingest`: `next` → `plan`、`refetch` → `await_image`、エラーなら `END`
- `plan` の `_route_plan`: `confirm` → `confirm`、`submit` → `submit`、エラーなら `END`
- `confirm` の `_route`: `next` → `submit`、キャンセルまたはエラーなら `END`
- `submit` → `await_tags` → `await_image` → `END`

`confirm` は `interrupt` で人間の承認を待つ。`approve` / `edit` / `reject` がある。この HITL はコード実行の承認に再利用する。

画像側の状態 (`State(MessagesState)`) には `progress_id`, `job`, `tags`, `error`, `references`, `plan`, `proposal`, `comfy_prompt_id`, `outputs` がある。参照画像バイトは state に置かず、sha256・役割・ファイル名だけを持つ。この契約は維持する。

チャット側 `src/furry_agent/chat_graph.py` の現状は次のとおり。

状態 `ChatState`:

- `progress_id`: 実行識別子
- `error`: 失敗文字列
- `route`: ルーティング結果（`kind`, `text`, `reason` など）
- `lock_token`: 画像タブとの `job_lock`
- `search`: 検索過程（intents, hits, mode, roles など）
- `hits`: 検索結果。並列ノードから追記。`[RESET]` でクリア
- `cards`: ページから抜いたファクトカード。同様に追記
- `logs`: 検索・読解ログ。同様に追記
- `messages`: `MessagesState` の会話履歴

ノード:

- `ingest`: 最後の人間メッセージを見て、ルール (`route_rules`) で chat / search / image タブへ振る。進捗を立て、`[RESET]` で hits / cards / logs を消す
- `route`: 曖昧な質問を Qwen3-1.7B-heretic で再判定し、検索要否とクエリ書き換えを行う。`SEARCH` または `CHAT`
- `chat`: 検索不要なら LM Studio の Qwen3.8 27B で直接回答
- `plan`: Qwen3.8 27B（unload 済みなら Ternary-Bonsai-2-27B）が 1〜3 件の検索意図を作る。意図は `tool` (`web` / `news` / `browse`), `q`, `why`。リーダー常駐と両立しないときは 27B を unload して `resident` から `proxy` へ落とす
- `search`: 意図ごとに `TorSearchClient`（Tor 経由 DuckDuckGo）で検索。モデルは使わない。`title`, `url`, `snippet` を返す
- `filter`: Bonsai-4B が関連ヒットを残す。メモリと `fanout_width` からリーダー枠を決める
- `read`: 枠ごとに Ternary-Bonsai-8B（llama-server）がページを開き、ファクトカードを抜く。引用は取得本文と照合。使い終わったらプロセスを殺す
- `critique`: リーダー（proxy モードの Ternary-Bonsai-2-27B）がカードを見直し、欠けを指摘し、追加意図を最大1ラウンド分足す
- `synthesize`: リーダーがカードから `[n]` 引用付きの最終回答を書く。llama-server と LM Studio を unload し、`job_lock` を放す

エッジ:

```text
START → ingest
ingest → CHAT かつ曖昧でない → chat
ingest → SEARCH → plan
ingest → TO_IMAGE_TAB → END
ingest → それ以外 → route
route → SEARCH → plan
route → それ以外 → chat
chat → END
plan → pending intents あり → Send("search")
plan → なし → synthesize
search → filter
filter → slots あり → Send("read")
filter → なし → critique
read → critique
critique → gaps あり → search
critique → なし → synthesize
synthesize → END
```

深さの現状:

- 追加検索は最大1回。初期ラウンドと critique 駆動の1回で、合計最大2ラウンド
- フィルタ後のヒットは `HITS_PER_INTENT = 4`
- リーダー枠は `fanout_width` と空きメモリで決まる
- 計画が失敗したらルールで意図1件にフォールバック
- 温度は routing 0.0、chat 0.6、cards 0.1、critique 0.2、synthesize 0.4
- 思考モードは無い。LM Studio 側の thinking はオフ

モデル役割:

- Router: Qwen3-1.7B-heretic（`route`）
- Planner: Qwen3.8 27B（LM Studio）または Ternary-Bonsai-2-27B（`plan`）
- Leader: Ternary-Bonsai-2-27B abliterated（`critique`, `synthesize`）。27B unload 後の proxy
- Filter: Bonsai-4B
- Reader: Ternary-Bonsai-8B
- Synthesizer: Leader と同一

道具:

- `TorSearchClient`: `settings.tor_socks_url`（既定 `127.0.0.1:9050`）経由の DuckDuckGo
- `LMStudio`: 計画と直接回答
- `LlamaServer` / `bonsai_worker`: PrismML llama-server。ポートは `BONSAI_BASE_PORT` からの offset（route 7、filter 8、leader 9）
- `ComfyClient`: 画像タブが塞がっていないかの確認にだけ使う
- `job_lock`: チャットと画像の同時実行を禁じる
- `RESET = "__reset__"`: hits / cards / logs のクリア印

UI:

- agent-chat-ui に画像タブとチャットタブがある
- タブは `mode-tabs.tsx` 相当
- 検索トレースは `messages/search-trace.tsx` 相当
- 画像の役割と強度は `ContentBlocksPreview.tsx` / `MultimodalPreview.tsx` 相当
- 実装時にこれらの実パスを確認する。名前が違っても契約（トレース表示、composer、タブ）は同じ場所に足す

ハードウェア制約:

- Windows ホスト。ROG Ally X 級、共有メモリ約 24GB を想定
- LLM（27B）と拡散チェックポイントは同時に載せない
- 検索モデルも同時常駐させず、使い終わったら殺す
- 画像生成はキューで直列
- 動画生成は対象外。クラウドフォールバックは禁止

### 1.2 足りないもの

- 批判ループが1回で打ち切られる。検索結果を読んでから、ユーザの問いに沿って問いを割り、追加検索する仕組みが弱い
- `route` が `CHAT` / `SEARCH` / `TO_IMAGE_TAB` だけなので、小説もコードも雑談ノードに落ちる
- 文章の outline → draft → revise が無い
- プログラム生成と、承認つき Docker サンドボックスが無い
- 速い回答と思考の切り替えが無い。思考トークンも、予算差も無い

---

## 2. 全体の形

UI の composer にモードを足し、LangGraph の `configurable` で渡す。グラフは二つに分けない。トレースとロックを共有するため、1本のチャットグラフの中で予算だけを変える。

渡す設定:

```text
mode: fast | think
task: chat | search | write | code | image
```

`task` はユーザが明示したときだけ固定する。未指定なら `ingest` とルータが推定する。`mode` は毎回 UI から渡す。未指定の既定は `fast`。

`ingest` がこれを `ChatState` に置き、分岐はこうする。

```text
ingest
  ├─ image     → 既存どおり画像タブへ誘導して END
  ├─ search    → plan → search → filter → read → reflect ↻（上限まで）→ synthesize
  ├─ write     → outline → draft → revise ↻ → END
  ├─ code      → spec → write_files → confirm → sandbox → observe ↻ → END
  └─ chat      → 単発回答 → END
```

`fast` は各枝のループを切るだけにする。グラフを `fast_graph` と `think_graph` に分けない。分けるとトレース、ロック、モデル unload が二重になる。

モードの意味は「モデルを替える」ではない。予算と思考トークンを替える。

| | fast | think |
|---|---|---|
| 検索 | 意図1本、批判なし、1ラウンド | 下位問いを埋めるまで最大4ラウンド |
| 文章 | 27B 一発 | outline → draft → revise |
| コード | 生成のみ。実行しない | 生成 → 承認 → コンテナ実行 → 修正 |
| 思考トークン | off | on。回答とは別ブロック |
| 温度 | 計画系は低め、雑談は現状の 0.6 を維持可 | 計画・批判・修正は 0.2 前後に固定 |

進捗は既存の `progress_id` メッセージを流用する。思考モードはモデルの載せ替えが入るので、無言の待ちにしない。各ノードの開始時に「計画中」「第2ラウンドを検索中」「コンテナを起動中」のように短い進捗を足す。

画像グラフと ComfyUI のノード契約はこの改修に含めない。創作や調査の結果を画像に渡したくなったら、チャット側が確定したプロンプトを画像タブへ渡すだけにする。eject 順を壊さない。

---

## 3. 深い検索

ラウンド数を増やすだけでは浅いままである。状態に「ユーザの問い」を残し、毎ラウンドそれを採点させる。

### 3.1 状態

`search` 辞書に次を足す。既存キーは消さない。

- `goal`: ユーザの問いを1文にしたもの。時期、地域、比較対象、スレッド内の制約を含める
- `subquestions`: 答えに必要な下位問いの配列
- `covered`: 答えられた下位問い
- `open`: まだ開いている下位問い
- `contradictions`: カード間の矛盾
- `round`: 完了した検索ラウンド数。初期は 0
- `max_rounds`: `fast` は 1、`think` は 4
- `pages_read`: 読んだページ数
- `max_pages`: 12
- `wall_clock_s`: 経過秒
- `max_wall_clock_s`: 480 から 600（8〜10分）
- `new_cards_last_round`: 前ラウンドで新規に採用したカード数
- `stop_reason`: `sufficient` / `diminishing` / `budget` / `no_hits` / `error`

下位問いの要素:

```text
id: str
question: str
status: answered | partial | open
evidence_card_ids: list[str]
note: str
```

カードは現状のファクトカードを維持し、次を足す。

- `subquestion_ids`: このカードが答える下位問い
- `stance`: supports / contradicts / background
- `quote_verified`: bool（現状の引用照合を維持）

### 3.2 計画

`plan` は最初の意図だけでなく、前ラウンドのカードと `open` を受け取って、「まだ開いている下位問い」だけをクエリにする。

初回 (`round == 0`):

- 27B で `goal` と下位問いを作る
- 下位問いは 2〜5 件。`fast` は下位問いを作らず意図1件でもよい
- 意図は 1〜3 件。各意図は `tool`, `q`, `why`, `subquestion_id` を持つ
- 失敗時は現状どおりルールで意図1件にフォールバックする

2回目以降:

- 新しい話題を勝手に広げない
- `open` または `partial` の下位問いだけをクエリにする
- 同じ URL は再読しない
- 矛盾がある下位問いは、反対側の一次に近いページを優先するクエリにする

関心への合わせ方は、検索語の言い換えではなく採点側に置く。`goal` に「何が分かればユーザの判断が変わるか」を入れる。カードがそれに答えていなければ `open` のままにする。プロファイルをプロンプトに焼かない。スレッドに残った制約（時期、地域、比較対象、ユーザがそのターンで言った条件）だけを `goal` に足す。

### 3.3 批判（reflect）

現状の `critique` は「欠けていたら意図を最大1件足す」で終わっている。これを充足判定に変える。ノード名は `critique` のままでも、`reflect` にリネームしてもよい。リネームするならエッジとテストを同時に更新する。

リーダーは次の JSON だけを返す。自由文で終えない。

```text
subquestions: [{id, status: answered|partial|open, evidence_card_ids, note}]
contradictions: [{subquestion_id, card_ids, summary}]
next_intents: [{tool, q, why, subquestion_id}]   # 0〜3 件
stop: bool
stop_reason: sufficient | diminishing | budget | need_more
```

`_after_critique` の条件はこれに変える。現状の「gaps があれば1回だけ戻る」は廃止する。

```text
open または partial が残る
AND round < max_rounds
AND pages_read < max_pages
AND wall_clock_s < max_wall_clock_s
AND 新規採用カードが前ラウンドより増えた（new_cards_last_round > 0）
AND stop が false
  → search
そうでなければ
  → synthesize
```

初回ラウンドの直後は `new_cards_last_round > 0` を満たせば継続してよい。2回目以降、新規カードが 0 なら `diminishing` で止める。ヒットが 0 なら `no_hits` で止める。上限到達は `budget`。

`fast` は critique ノード自体をスキップして `synthesize` へ進む。意図は1本、ラウンドは1。

### 3.4 予算

- `fast`: 1ラウンド、批判なし、意図1本、ヒットは現状の `HITS_PER_INTENT = 4` を維持
- `think`: 最大4ラウンド、ページ上限12、壁時計 8〜10分、1ラウンドの意図は最大3、次ラウンドの意図は最大3
- Ally X 級では幅（同時リーダー数）を増やさない。ラウンドを増やす。27B と Bonsai リーダーは今どおり同時常駐させない。ラウンドの境で unload する
- 既存の `HITS_PER_INTENT = 4` は維持する。増やすなら設定化し、既定は 4 のまま

### 3.5 統合

`synthesize` は現状どおりカードから `[n]` 引用付きで書く。追加で次を回答に含める。

- 答え
- 開いたままの下位問い（推測で埋めない）
- 矛盾が残っていれば両方のカード番号
- `stop_reason`

最終 `AIMessage` の `search_trace` には、現状の intents / hits / cards / timing / roles に加えて、ラウンド、下位問い、採用カード、棄却理由、`stop_reason` を入れる。

### 3.6 UI

`messages/search-trace.tsx`（実パスを確認すること）に次を出す。

- ラウンド番号
- 下位問いと status
- 採用カードと棄却理由
- 停止理由
- 読んだページ数と経過秒

既存トレースの延長として足す。別パネルを新設しない。

---

## 4. チャットタブでの創作

`route_rules` と 1.7B ルータに `WRITE` を足す。小説、設定、文章、推敲、「続きを書いて」はここへ入れる。検索が要る創作（実在の事件や資料を下敷きにする、事実確認が必要、など）だけ、`think` のとき search を先に通してから writer に渡す。`fast` の創作は検索しない。

writer は検索リーダーではなく、LM Studio の 27B に書かせる。検索モデルを文章生成に使わない。

### 4.1 状態

`artifact` を状態に足す。メッセージ本文とは分ける。

```text
artifact:
  kind: write | code | none
  brief: {genre, pov, length, must, must_not, language}
  outline: list[{id, title, beats}]
  draft: str
  revision_notes: list[str]
  chapter_index: int
  status: brief | outline | draft | revised | done
```

本文はメッセージに流しつつ、確定稿は `artifact.draft` に残す。「続き」はこの draft の末尾から続ける。会話履歴だけを頼りにしない。

### 4.2 分岐

```text
write
  fast:  brief を暗黙に取り、27B で一発生成 → END
  think: outline → ユーザへ提示
           → draft
           → revise を1回
           → END
長い依頼（章立て、長編、続き）:
  think のみ、章ごとに interrupt して続ける
  承認は画像側 confirm と同じ interrupt 契約を使う
```

`revise` は別モデルを起こさない。同じ 27B に、draft と brief の差分だけを直させる。全文を毎回捨てて書き直させない。

### 4.3 プロンプト契約

- 出力言語はユーザの言語に合わせる。日本語依頼なら日本語
- 事実が要る箇所を創作で埋めない。不足なら `open` として聞くか、search 枝へ戻すかを brief に書く
- システムプロンプトは `prompts/` に置く。画像用の `system_furry_tags.txt` を流用しない
- 提案ファイル: `prompts/system_write_outline.txt`, `prompts/system_write_draft.txt`, `prompts/system_write_revise.txt`

### 4.4 画像への受け渡し

ユーザが「この文章の場面を画像にして」と言った場合だけ、確定した描写を短く切り出して画像タブへ誘導する。チャットグラフから ComfyUI を直接叩かない。

---

## 5. プログラム作成と Docker 実行

コードも同じチャットグラフの枝にする。実行はホストの Docker だけに閉じる。ComfyUI や llama-server のプロセスには載せない。LangGraph の Python プロセス内で `subprocess` による生のシェル実行をしない。必ず `sandbox.py` 経由にする。

### 5.1 分岐

```text
code_plan → write_files → confirm_run → sandbox_exec → observe
                ↑                              |
                └──────── 失敗かつ round < 2 ──┘
```

- `fast`: `code_plan` と `write_files` のみ。実行しない。ファイル一覧と意図を回答する
- `think`: 上のループに入る。ただし `confirm_run` の承認前には絶対に実行しない
- 修正ループは失敗時のみ。成功したら `observe` から回答へ進む。上限は実行2回（初回 + 修正1回）まで。無限ループにしない

### 5.2 状態

```text
code:
  spec: str
  files: list[{path, content, language}]
  command: list[str]          # シェル文字列ではなく argv
  round: int                  # 実行回数。最大 2
  last_exit: int | null
  stdout_tail: str
  stderr_tail: str
  artifact_dir: str           # artifacts/code/<run_id>/
  approved: bool
```

生成ファイルはリポジトリ直下に書き散らさない。`artifacts/code/<run_id>/` に置く。`run_id` は `progress_id` から作る。

### 5.3 サンドボックス制約

この条件は固定する。実装で緩めてはならない。

- イメージは `python:3.12-slim`。Rust が明示された依頼だけ `rust:1.88-slim` を別プロファイルにする。既定は Python
- `--network none` を既定にする。依存取得（pip, cargo）はユーザがそのターンで明示したときだけ、承認画面にネットワーク許可を出してから有効にする
- `--read-only`。書き込みは `/work` のみ。`--tmpfs /tmp:rw,size=64m` は可
- `--memory 2g --cpus 2 --pids-limit 256`
- `--cap-drop ALL`
- 非 root ユーザ（`--user` で uid を指定）
- docker.sock はマウントしない
- ホストのホーム、リポジトリ本体、`.env`、SSH 鍵、ブラウザプロファイルはマウントしない
- マウントしてよいのはその run の `artifacts/code/<run_id>` を `/work` にだけ
- タイムアウト 60 秒。超えたら `docker kill`
- 出力の回収は `docker cp`。コンテナの stdout / stderr はそれぞれ末尾 8KB までに切る
- 環境変数にホストの秘密を渡さない。`.env` を `--env-file` で渡さない

実行例の形（実装はこの argv を組み立てる。シェル経由の文字列連結をしない）:

```text
docker run --rm
  --network none
  --read-only
  --memory 2g --cpus 2 --pids-limit 256
  --cap-drop ALL
  --user <uid>:<gid>
  --mount type=bind,src=<run_dir>,dst=/work
  --workdir /work
  --tmpfs /tmp:rw,size=64m
  python:3.12-slim
  <argv...>
```

### 5.4 承認

`confirm_run` は画像の `confirm` と同じ `interrupt` を使う。承認ペイロードに次を入れる。

- 生成したファイル一覧（パスとバイト数）
- 実行 argv
- イメージ名
- ネットワークの有無
- タイムアウトとメモリ上限

`approve` でのみ `sandbox_exec` へ進む。`reject` / 無応答は実行せず、ファイルだけ残して回答する。`edit` はコマンドまたはファイルの差し替えを受けてから、もう一度承認を出す。

### 5.5 ロック

`job_lock` は VRAM 保護用なので、サンドボックスは別ロックにする。名前は `sandbox_lock`。27B 常駐中でも、コンテナメモリ上限が 2GB なら並行してよい。画像生成中（ComfyUI がキューを持っている間）は従来どおり待たせる。画像側の `job_lock` をサンドボックスが握ったままにしない。

Windows 側は Docker Desktop の Linux エンジン前提とする。Docker が無い、または daemon が止まっているときは実行をスキップし、その理由を回答に書く。ファイル生成までは行う。

### 5.6 新規ファイル

- `src/furry_agent/sandbox.py`: イメージ解決、argv 組み立て、`docker run`、タイムアウト、`docker cp`、後始末。他モジュールから生の `docker` を呼ばない
- テストは Docker が無い環境では skip する。argv 組み立てと「承認前に run を呼ばない」ことは Docker 無しでテストする

---

## 6. 速い／思考の切り替え

Grok の切り替えに相当するのは、モデルを替えることより予算と思考トークンの切り替えである。

### 6.1 UI

composer に segmented control を置く。ラベルは `速い` と `思考`。選択を `config.configurable.mode` に載せる。値は `fast` または `think`。スレッドを跨いでも、その送信の configurable を優先する。未指定は `fast`。

タブ（画像 / チャット）とは独立させる。画像タブではモードを無視してよい。効くのはチャットグラフだけ。

### 6.2 モデル呼び出し

Qwen3 系はリクエストの `chat_template_kwargs.enable_thinking` で切り替える。

- `fast`: `enable_thinking: false`。思考トークンを出させない
- `think`: `enable_thinking: true`

llama-server 側は出力の `<think>` を本文から分離する。分離した思考は回答本文に混ぜない。UI では検索トレースと同じ折りたたみブロックに出す。折りたたみの既定は閉じる。

温度:

- `fast`: routing 0.0 は維持。chat 0.6 は維持してよい。検索計画は作らないか、作るなら 0.2
- `think`: 計画・批判・修正・コード計画は 0.2 前後。本文の draft だけ 0.7 まで上げてよい。revise は 0.2

### 6.3 進捗

`think` では各段の開始メッセージを `progress_id` 付きで流す。最低限次の文言が区別できること。

- 意図を分解している
- 第 N ラウンドを検索している
- ページを読んでいる
- 足りない点を判定している
- アウトラインを作っている
- 本文を書いている
- コンテナの承認待ち
- コンテナを実行している

`fast` ではこの進捗を必須にしない。最終回答だけでよい。

---

## 7. ルーティング

`route_rules` にキーワードを足し、曖昧なら 1.7B に `WRITE` と `CODE` を判定させる。ルータの出力 kind は次の5つのいずれかだけ。

- `CHAT`
- `SEARCH`
- `WRITE`
- `CODE`
- `TO_IMAGE_TAB`

優先順位:

1. 画像生成の明示（描いて、生成して、タグにして、参照画像付きで絵にして）は `TO_IMAGE_TAB`。現状の誘導を壊さない
2. 実行やプログラムの明示（コードを書いて、実装して、実行して、テストして、スクリプト）は `CODE`
3. 文章創作の明示（小説、物語、設定、推敲、続きを書いて、記事の下書き）は `WRITE`
4. 事実・最新・比較・調査の明示は `SEARCH`
5. 残りは `CHAT`

複合（「調べてから小説にして」）は `think` なら search を終えてから write へ渡す。`fast` なら検索せず write のみ、と回答で断る。

ルータが失敗したら `CHAT` に倒す。画像タブへ誤誘導しない。

---

## 8. 触るファイル

実装時に実在を確認してから編集する。

既存:

- `langgraph.json`: グラフ追加はしない。`chat` の参照先は維持
- `src/furry_agent/chat_graph.py`: `mode` と `task`、予算ループ、write 枝、code 枝
- `src/furry_agent/search_agent.py`（import 名 `sa`）: 計画と批判の入出力を下位問い充足判定に変える
- `src/furry_agent/graph.py`: 変更しない
- `agent-chat-ui` の composer: `速い` / `思考`
- `agent-chat-ui` の `messages/search-trace.tsx`: ラウンド、下位問い、停止理由
- `prompts/`: 創作とコード計画のシステムプロンプトを追加。画像用プロンプトは変更しない
- `tests/`: ルーティング、停止条件、承認前非実行、argv 組み立て

新規:

- `src/furry_agent/sandbox.py`
- `prompts/system_write_outline.txt`
- `prompts/system_write_draft.txt`
- `prompts/system_write_revise.txt`
- `prompts/system_code_plan.txt`
- `docs/` に本書を置くならファイル名は `docs/chat-deep-search-creative-sandbox.md`

変更しない:

- `comfyui_nodes/`
- `workflows/`
- `src/furry_agent/templates.py`
- `src/furry_agent/comfy_client.py` の投入契約
- `.env` の `COMFY_MODEL_FAMILY` の意味
- 画像側の eject 順（LLM → unload → ckpt → sampler）

---

## 9. 作業順

段階を飛ばさない。各段階の完了条件を満たしてから次へ進む。

### 段階 1: 検索を予算制にする

対象: `chat_graph.py`, `search_agent` の計画・批判。

作業:

- `ChatState` に `mode` を足す。`configurable.mode` を `ingest` で読む。未指定は `fast`
- `search` に §3.1 のフィールドを足す
- `plan` が初回に `goal` と `subquestions` を作り、2回目以降は `open` だけをクエリにする
- `_after_critique` の「追加1回」を §3.3 の条件に置き換える
- `fast` は critique をスキップする
- 27B とリーダーの同時常駐禁止は維持する

完了条件:

- `think` で、open が残り新規カードが増えるあいだは 2 ラウンド以上回るテストがある
- 新規カード 0 で止まるテストがある
- `max_rounds` で止まるテストがある
- `fast` が 1 ラウンドで critique を呼ばないテストがある
- 既存の画像グラフテストが落ちない

### 段階 2: トレースとモード UI

対象: composer、search-trace。

作業:

- segmented control `速い` / `思考` を composer に足し、`configurable.mode` を送る
- search-trace にラウンド、下位問い、採用と棄却、`stop_reason`、ページ数、経過秒を出す
- `<think>` を本文から分離し、折りたたみに出す。`fast` では思考ブロックを出さない

完了条件:

- モード未指定の送信が `fast` になる
- 思考ブロックが回答本文に混ざらない
- 画像タブの役割選択と画像表示が退行しない

### 段階 3: WRITE 枝

対象: `chat_graph.py`, `prompts/system_write_*.txt`。実行系はまだ入れない。

作業:

- kind `WRITE` をルータと `route_rules` に足す
- `artifact` を状態に足す
- `fast` は一発生成、`think` は outline → draft → revise 1回
- 長い依頼は章ごとに `interrupt`
- 検索が要る創作は `think` のみ search 完了後に write へ渡す

完了条件:

- 「続きを書いて」が `artifact.draft` の末尾を入力にするテストがある
- 画像用タグプロンプトを writer が読まない
- `fast` の write が search ノードを通らない

### 段階 4: CODE 枝とサンドボックス

対象: `sandbox.py`, `chat_graph.py`, `prompts/system_code_plan.txt`。

作業:

- §5 の分岐、状態、制約、承認、別ロックを実装する
- Docker 不在時はファイル生成のみで理由を返す
- 承認前に `docker run` を呼ばないことをテストする
- argv がシェル文字列結合でないことをテストする
- ネットワーク既定 none、docker.sock 非マウント、`.env` 非引き渡しをテストする

完了条件:

- `fast` はファイルを書いて実行しない
- `reject` で実行されない
- 失敗時の再実行が最大1回（合計2回）で止まる
- 画像生成の `job_lock` をサンドボックスが占有しない

### 段階 5: 文書と回帰

作業:

- `AGENTS.md` にチャットグラフの新しい kind とモードだけを短く追記する。画像節は書き換えない
- `CHANGELOG.md` に変更点を追記する
- 既存 pytest を通す。PowerShell スクリプトのテストがリポジトリにあるならそれも通す

---

## 10. テスト項目

最低限次を自動化する。

検索:

- open が残り新規カードが増える → 次ラウンドへ
- 新規カード 0 → synthesize、`stop_reason=diminishing`
- round が max に達する → synthesize、`stop_reason=budget`
- pages_read が 12 に達する → 停止
- wall clock 上限 → 停止
- fast は critique を呼ばない
- 同じ URL を再読しない
- 計画モデル失敗時は意図1件にフォールバックする

創作:

- 小説・続き・推敲が `WRITE` になる
- 描いて、が `TO_IMAGE_TAB` のままである
- think の revise が draft を全捨てしない（差分指示を渡している）
- 章の interrupt で reject したら打ち切る

コード:

- 承認前に sandbox 実行関数が呼ばれない
- argv に `;` や `&&` を含むシェル文字列を渡さない
- 既定ネットワークが none
- マウントが run ディレクトリだけ
- タイムアウトで kill するパスがある（Docker 無しなら関数単位でモック）
- Docker 不在でクラッシュせず理由を返す

モード:

- configurable 無しは fast
- think で `enable_thinking` が true
- fast で false
- 思考本文が回答チャンネルに入らない

回帰:

- 画像グラフの ingest → plan → confirm → submit の遷移テストが残っているならそれを通す
- `job_lock` が画像実行中にチャット検索を直列化する既存挙動を壊さない

---

## 11. 受け入れ条件

次をすべて満たしたら完了とする。

1. チャットタブの検索が、初期クエリの1回で終わらず、結果と下位問いを見て追加検索できる。停止は充足、収穫逓減、予算のいずれかで、理由がトレースに出る。
2. 小説・文章・推敲をチャットタブで行え、続きが artifact の本文を継承する。
3. プログラムを生成でき、思考モードでは承認後にだけ、本書の制約を満たす Docker コンテナで実行できる。速いモードは実行しない。
4. composer の `速い` / `思考` で、検索ラウンド、創作の段数、コード実行の有無、思考トークンが切り替わる。
5. 画像タブの生成、役割確認、eject 順、ComfyUI ノード契約が変わっていない。
6. クラウド API を追加していない。

---

## 12. 実装時にやってはいけないこと

- ラウンド数だけ増やして下位問い採点を省略すること
- ユーザプロファイルや記憶を検索プロンプトに焼き込むこと。そのターンとスレッドの制約だけを `goal` に入れる
- 27B と拡散モデル、または 27B と Bonsai リーダーを同時常駐させること
- 創作やコード実行を `graph.py` の画像パイプラインに挿入すること
- 承認なしで `docker run` すること
- コンテナに docker.sock、ホストホーム、`.env` をマウントまたは渡すこと
- シェル文字列を連結してコンテナに渡すこと
- `fast` で批判ループやコンテナ実行を有効にすること
- 思考トークンをユーザ向け回答本文に混ぜること
- 検索の幅（同時読解数）を Ally X 級のメモリ前提で増やすこと。増やすのはラウンドだけ
- 画像用システムプロンプトを文章生成に流用すること
- 失敗したコード実行を上限なく繰り返すこと

---

## 13. 実装記録（2026-10-04、ブランチ `feature/chat-deep-search-creative-sandbox`）

### 13.1 本書に加えて入力にした要件

利用者から次の追加要件を受けた。本書と食い違う箇所は下の 13.3 で吸収した。

- コンテナは Debian ベースの `python3-slim` を想定。より良いものがあれば替えてよい。→ 本書どおり `python:3.12-slim`（Debian slim）にした。この端末で取得と実行を確認した。Rust の明示時だけ `rust:1.88-slim`。
- 速い / 思考の明示的な切り替えに加えて、Grok のように自動で切り替わる「自動」を用意する。
- 実行環境はこの端末（ROG Xbox Ally X）。この端末で動くことを最優先する。
- 検索とチャットのタイムアウトは 10 分では短いので 20 分にする。
- テストを書き、適度な粒度でコミットする。人間には判断を仰がない。
- 既存の SDXL / Flux（Chroma1-HD）の画像生成が退行していないことを確かめる。

### 13.2 実装した構成

| 本書 | 実装 |
|---|---|
| §2 `mode` / `task` | `configurable.mode`（`fast` / `think` / `auto`）、`configurable.task`（`chat` / `search` / `write` / `code` / `image`）。`/search` `/write` `/code` `/chat` の接頭辞も明示扱い。`src/furry_agent/modes.py`、`router.py` |
| §3 深い検索 | `search_agent.DeepPlan` / `Reflect` / `apply_reflect` / `next_round`。グラフは `plan → search → filter → read → judge → critique ↻ → synthesize`（`judge` は思考モードで「足りない点を判定しています」を先に出すための進捗ノード） |
| §4 文章 | `write_nodes.py`（`write_brief` → `write_draft` → `write_revise` → `chapter_confirm`）、`writing.py`（スキーマ、置換による推敲、続きの入力） |
| §5 コード | `code_nodes.py`（`code_plan` → `write_files` → `confirm_run` → `sandbox_exec` → `observe`）、`coding.py`（応答形式の解析）、`sandbox.py`（Docker） |
| §6 速い / 思考 | `llm_client.chat(thinking=...)`、`ChatReply.reasoning`、UI の `ChatModeSwitch` と `ThinkingView` |
| §8 共有部分 | `chat_common.py`（状態、ロック、進捗、HITL の形） |

画像グラフ（`graph.py`）、`templates.py`、`comfy_client.py`、`workflows/`、`comfyui_nodes/` は変更していない。共有の `job_lock.py` には `holds(token)` を足しただけ。

### 13.3 本書との差分と吸収方法

- **自動モード（追加要件）**: 本書は `fast` / `think` の 2 値。`auto` を足し、`ingest` がルールで片方に解決して `state.mode` には常に `fast` / `think` を置く（以降のノードは本書どおり 2 値だけを見る）。ルール: 利用者の指定語（「じっくり」「手短に」）→ タスクごとの語（検索なら比較・違い・理由・分析など、文章なら章立て・構成、コードなら実行・テスト、会話なら推論・計算）→ 長さ。決まらない文でルータ（Qwen3-1.7B）を呼んだときは、その `deep` 判定を使う。モデルを 1 つ余計に載せることはしない。無指定は本書どおり `fast`、UI の既定は `auto`。
- **タイムアウト 20 分（追加要件）**: 本書 §3.1 の `max_wall_clock_s` 480〜600 を 1200 にした（`SEARCH_WALL_CLOCK_S`）。モデル呼び出し 1 回の上限 `CHAT_TIMEOUT_S` も 180 → 1200。共有ロックのリース（15 分）より長い呼び出しになるため、呼び出し中は 60 秒ごとにリースを延長する（`_held`）。
- **思考トークンの切り替え**: 本書は `chat_template_kwargs.enable_thinking`。この端末の LM Studio で実測すると、LM Studio はこのキーを無視し、`reasoning_effort` を付けたときだけ Qwen3.8 27B が思考し、思考は `message.reasoning` に分かれて返った。両方を送る（llama-server は前者、LM Studio は後者を読む）。代理リーダーの llama-server は `--reasoning off` で起動しているため、思考モードでも代理の出力に思考は無い。JSON を返す段（計画・批評・推敲・アウトライン）は文法制約と両立させるため思考なしで呼ぶ。
- **`docker cp` による回収（§5.3）**: 本書の argv 例は `--rm` 付きで、`/work` はその実行の `artifacts/code/<run_id>` を bind mount する。このため終了時には出力がすでにホスト側にあり、`--rm` 後のコンテナからは `docker cp` できない。argv 例を優先し、`docker cp` は使わず run ディレクトリから回収する（新しく作られたファイルを一覧にする）。stdout / stderr は `docker run` のパイプから末尾 8KB。
- **依存取得のネットワーク（§5.3）**: 本書は「明示と承認があればネットワークを有効にする」。プログラム本体にネットワークを渡さず、依存の取得（`pip install --target .deps -r requirements.txt` / `cargo fetch`）だけを別のコンテナでネットワーク付きで動かし、本体は常に `--network none` にした（より狭い）。承認カードでは `network: setup` と表示する。
- **追加の制約**: `--security-opt no-new-privileges`、`--memory-swap 2g`、`--pull never`（実行中に取得しない。`setup-sandbox.ps1` が取得）、root の uid を拒否、`python -c` とシェル（`sh -c` など）を拒否。いずれも本書の制約を狭める方向。
- **進捗（§6.3）**: ノードの進捗はノード終了時に UI へ届くため、「次の段の開始」を前のノードの終了時に出す（例: `plan` の終了で「第 1 ラウンドを検索しています」）。批評の前だけは前段が並列の reader なので、進捗だけのノード `judge` を足した。
- **fast の停止理由**: 本書の `stop_reason` の値に「速いモード」は無い。速いモードは `max_rounds=1` を使い切ったものとして `budget`（結果 0 件なら `no_hits`）を記録し、回答本文には付けない（トレースには出す）。
- **ルータの `TO_IMAGE_TAB`**: §7 は「ルータの kind は 5 つのいずれか」かつ「画像タブへ誤誘導しない」。ルータが `TO_IMAGE_TAB` を返しても会話として扱う。画像タブへの誘導はキーワード規則（描いて、画像にして など）だけが行う。
- **章の確認とロック**: interrupt で利用者を待つあいだ共有ロックを持ち続けると画像タブが止まるため、`chapter_confirm` / `confirm_run` の前にロックを放し、再開後のモデル呼び出しで取り直す。
- **会話履歴**: 実行中の進捗メッセージが履歴の最後の assistant ターンとしてモデルに渡っていた（既存の挙動）。その実行の進捗メッセージは履歴から外した。
- **27B の context と速さ（この端末の実測）**: LM Studio は 27B を context 4096 で読み込む（`setup-lmstudio.ps1`。これ以上はメモリに載らない）。生成は約 0.9 トークン/秒で、Docker Desktop を止めても、ComfyUI に `/free` を送っても変わらなかった。本書の「思考は回答と別ブロック」「draft は 0.7」はそのままに、1 回の呼び出しの `max_tokens`（回答 + 思考）を context と「20 分で出せる量」（速さは応答ごとに測る）の小さい方に収め、答えの分が残らないときは思考を使わない。思考が予算を使い切って本文が空なら、思考なしで 1 回だけ答え直す。修正前は、思考モードのコード生成が 4096 を超える `max_tokens` を要求して 15 分以上止まらなかった。
- **Docker Desktop の起動（この端末の実測）**: Docker Desktop の VM は約 1.5GB を使い、27B のロード中の空き（約 0.4GB）を食う。止まっているときは確認カードにその旨を出し、承認後に `docker desktop start` で起動して、実行が終わったら止める（承認前には起動しない）。`setup-sandbox.ps1` も、自分で起動したときは最後に止める。
- **検索モデルの probe**: ルータの出力（`kind`）と批評の出力（`Reflect`）が変わったため、`bonsai_probe.py` の判定を新しい契約に合わせ、`probe-bonsai.ps1` をやり直した。

### 13.4 実測と確認（2026-10-05、この端末）

| 確認 | 結果 |
|---|---|
| pytest | 457 passed、2 skipped（Docker 実機テストは Docker 起動中に単独で実行して合格: uid 10001、読み取り専用ルート、ネットワークなし、`/work` への出力） |
| 速いモードの会話 | 約 165 秒（27B のロード込み） |
| 思考モードの会話 | 思考 157 トークン + 回答、約 196 秒。思考は回答と別の折りたたみに出た |
| 思考モードのコード | 生成 → 確認カード → 承認 → `python:3.12-slim` で実行（終了コード 0、1.7 秒）。生成は 619 トークン / 709 秒 |
| 思考モードの文章 | アウトライン → 本文 → 推敲（置換 0 件）、約 6 分 |
| 自動モードの検索 | 「ROG Xbox Ally X と Steam Deck OLED の違いは？」→ ルータ（Qwen3-1.7B、新しい `kind`）が SEARCH、自動が「違い」で思考を選択。3 ラウンド、12 ページで `budget` 停止、食い違い（両方の出典番号）と未解決の下位問いを回答に付けた。約 16 分 |
| 画像タブ SDXL（退行確認） | `t2i_basic`、タグ生成 → LM Studio の unload を確認 → KSampler、`outputs/` と ComfyUI の両方に保存、約 4.3 分 |
| 画像タブ Chroma1-HD（退行確認） | `COMFY_MODEL_FAMILY=flux`、768×768（`CHROMA_MAX_PIXELS=589824`）、英語の説明文 → 生成・保存、約 15 分 |
| UI（この端末から LAN アドレス `http://<LAN IP>:3000` へ headless Edge、のちに利用者が他ホストの実機ブラウザで確認） | チャットタブに「自動 / 速い / 思考」、送信の `config.configurable.mode` が選択どおり、回答下のモード表示、思考の折りたたみ。画像タブはモード切替なし・添付ありで変化なし |
| 27B の生成速度 | 約 0.9 トークン/秒（Docker 停止、ComfyUI `/free` 後も同じ） |
| メモリ | 27B ロード中の空き約 0.4GB、Docker Desktop の VM 約 1.5GB |

確認中に見つけて直したもの: 承認後の実行が langgraph dev の BlockingError で止まる（`build_argv` の `resolve()`）、4096 を超える `max_tokens`、思考の予備枠が大きすぎて思考モードで思考しない、LM Studio の TTL と要求が重なったときの "Model is unloaded."、critic の「一部」が未回答に落ちる。

### 13.5 残る制約

- 他ホストの実機ブラウザからの動作は、利用者が確認した（問題なし）。
- `probe-bonsai.ps1` のやり直し（13.6）で、8 モデルすべてが担当タスクに合格した。

### 13.6 検索モデルの再検証と、1 回目が中断した理由（2026-10-05）

2 回目の `probe-bonsai.ps1`（03:27〜03:34）は完走し、`tools/bonsai/rank.json` を書き直した。全モデルが担当タスクに合格し、順位は前回と同じ。違いは Qwen3-0.6B-heretic で、旧形式のルータには落ちていたが、新しい形式（`kind`）のルータ検証には合格したため、ルータの 3 番手（1.7B、3.5-4B の次）に入った。

| モデル | メモリ | 速度 | 合格 |
|---|---|---|---|
| Ternary-Bonsai-8B | 2915 MB | 31.0 tok/s | plan / worker / critique / synthesize |
| Bonsai-4B | 1359 MB | 46.5 tok/s | filter |
| Bonsai-8B | 1730 MB | 25.7 tok/s | worker |
| Ternary-Bonsai-2-27B | 6446 MB | 8.7 tok/s | plan / worker / critique / synthesize |
| Ternary-Bonsai-2-27B abliterated | 7033 MB | 9.0 tok/s | plan / critique / synthesize |
| Qwen3.5-4B-heretic | 3824 MB | 20.0 tok/s | route / plan / worker / critique / synthesize |
| Qwen3-1.7B-heretic | 1894 MB | 57.0 tok/s | route / filter / worker |
| Qwen3-0.6B-heretic | 1349 MB | 104.7 tok/s | route / filter |

1 回目（10-04 23:57 ごろ）が中断した理由の調査:

- 中断したのは probe そのものではなく、Claude Code のバックグラウンドシェルだった。Claude Code は、セッションが待機中に空き物理メモリが少なくなると自分のバックグラウンドシェルを止める。Windows 側にはメモリ枯渇のイベント（Resource-Exhaustion-Detector）は記録されておらず、コミットも上限（55 GB）に遠かった。llama-server の取り残しも無かった。
- 2 回目は Claude Code のシェルの外（独立したプロセス）で動かし、5 秒ごとに空きメモリを記録した。Ternary-Bonsai-2-27B（約 7 GB）を読み込んだときの空き物理メモリの最小は 4.7 GB で、問題なく動いた。
- 10-04 18:24 の probe（成功）と 1 回目（中断）の環境の違いは、この作業で Docker Desktop を起動したこと。`docker desktop stop` の後も WSL の VM（vmmemWSL）が約 0.8 GB（停止前は約 1.5 GB）を持ち続けていた（23:50 の測定）。この作業を始めた時点（18:24 より後）でも Docker Desktop は起動しておらず（`docker version` が接続できなかった）、18:24 の probe も Docker なしで動いたと考えられる。2 回目の時点では vmmemWSL は消えていた。
- ComfyUI（約 6.3 GB のコミット、ほぼページアウト）、LM Studio の 27B（probe 前に unload）、LangGraph は 1 回目と 2 回目で同じ条件だった。
- 結論: 1 回目は、Docker の VM が残っていた分（約 0.8 GB）だけ空きが少なく（推定の最小は約 3.5〜4 GB）、Claude Code の待機中のメモリ監視がそのしきい値を下回ったと判断してシェルを止めた。probe とこのリポジトリのコードがメモリを余計に使うようになったわけではない（各モデルのメモリは前回とほぼ同じ: 6567 → 6446 MB、6757 → 7033 MB）。Docker Desktop を実行のときだけ起動して止める変更（13.3）は、この取り合いを避けるためでもある。
- 27B が遅いため、思考モードのコードと長い文章は 1 回 10 分以上かかる。1 回の呼び出しで出せるのは約 1000 トークン。
