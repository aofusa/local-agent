# 主張単位の検証 設計

> v0.11.0 から LM Studio は使っていない。本文の「LM Studio」「`LMSTUDIO_*`」は、llama.cpp のルータ（`LLM_*`。[llamacpp-router-design.md](llamacpp-router-design.md)）と読み替える。

- 注記（v0.8.0）: 本文の `/docs`（ローカル文書）の経路は削除した。主張の検証は検索の回答だけに掛かる。
- 文書種別: 改修設計（実装前）
- 版: 0.1（2026-10-04）
- 対象: [aofusa/local-agent](https://github.com/aofusa/local-agent)
- 関係: `docs/chat-search-tor-design-bonsai-tabs.md` を置き換えない。チャットタブの検索グラフに、統合の前後へ 1 段足す
- 拘束: `AGENTS.md`。画像グラフ（`graph.py` / `workflows/`）は触らない

## 1. 結論

検索の抜粋カードに対して、回答文の主張を 1 件ずつ支持・部分・矛盾・出典なしに分ける。支持されない主張は最終文から落とす。検証器は通信を開かない。追加検索が要るときだけ、既存の批評の 1 回に乗せる。

裏取りは体同士の議論にしない。`chat-search-tor-design-bonsai-tabs.md` が省いた「体同士の議論」は、この文書でもやらない。同じ Ternary-Bonsai-2-27B を卸さずに 2 回呼び、2 回目はカードと主張リストだけを見る。

ローカル文書の map-reduce（別紙 `local-doc-mapreduce-design.md`）が同じカード形を出すので、検証段は検索と文書で共有する。

## 2. この文書の使い方

実装前の契約である。グラフのノード名、状態キー、JSON 形、やらないこと、ロックと unload の順は本書に合わせる。モデルの実測（JSON が安定するか）は未決 §9 が閉じるまで、検証段はフラグで切れるようにする。

## 3. 現状

チャットタブの検索は次の順である。

1. 計画: LM Studio の Qwen3.8 27B。意図は 1〜3 本。終わったら unload
2. 検索: オーケストレータが Tor 経由で DuckDuckGo。幅は 3 まで
3. フィルタ: Bonsai-4B
4. 読解: Ternary-Bonsai-8B を最大 3 本。URL ごとに事実・数値・日付・反証
5. 批評: Ternary-Bonsai-2-27B abliterated。視点の欠落を見て、追加検索は 1 回だけ
6. 統合: 同じ 27B 級。日本語、`[n]` と URL 一覧

欠落しているのは、統合文の各文がどの抜粋に支えられているかの判定である。批評は「もう一度探すか」であり、文と抜粋の突き合わせではない。

メモリ契約は変えない。LM Studio の 27B と reader、reader と Ternary-Bonsai-2-27B は同時に載らない。検索モデルは PrismML の llama.cpp fork だけ。使い終わったら PID を殺す。画像タブとは `job_lock` で直列。ロックを放すのは llama-server が消え、LM Studio を unload したあと。

## 4. 要件

### 4.1 やる

- 読解カード（および文書カード）を、検証器が参照できる `EvidenceCard` に正規化する
- 統合の前に、質問とカードから主張候補を最大 12 件出す
- 各主張をカードの抜粋だけで `supported` / `partial` / `contradicted` / `unsupported` / `opinion` に分ける
- 統合文は `supported` と `partial` だけを事実として書く。`partial` は断定しない
- 統合のあと、同じ判定器で最終文を 1 回監査する。監査で落ちた文は削除し、言い換えない
- 監査結果をチャットの進捗として短い表で返す。最終回答は従来どおり日本語と `[n]`
- 失敗しても検索結果そのものは捨てない。検証不能なら「主張の突き合わせに失敗した。抜粋は末尾に残す」と明示する
- `CLAIM_VERIFY=0` で段をスキップし、現行の統合だけに戻す

### 4.2 やらない

- 検証器同士の議論、多数決、反証役の常駐
- 検証段からの URL 取得、Tor、追加検索。追加検索は批評段の既存 1 回だけ
- クラウド API、LangSmith、`langgraph-swarm`、CrewAI、AutoGen
- 画像タブへの挿入、LangGraph から LM Studio を呼んでのタグ生成の置換
- 抜粋に無い知識での補完。モデルの記憶で `supported` にしない
- 主張の自動修正（数値の書き換え）。落とすか、ヘッジするかだけ
- 12 件を超える主張の分割再帰
- 利用者に内部プロンプトを返すこと

## 5. 設計

### 5.1 挿入位置

現行:

```text
plan → search → filter → read → critique → synthesize
```

本書:

```text
plan → search → filter → read → critique
  → claim_extract → claim_verify
  → synthesize
  → claim_audit → claim_drop
```

`critique` が追加検索を要求したときは、その 1 回が終わってカードが揃ってから `claim_extract` に入る。検証の途中では検索に戻らない。

文書グラフ（別紙）は `doc_map` のカードを `claim_extract` に渡す。入口だけ違い、`claim_extract` 以降は同じ関数である。

### 5.2 なぜ統合の前と後の両方か

前段は、カードに無いことを書かせないための生成制約である。後段は、統合モデルが制約を破ったときの削除である。同じ重みなので独立の審査ではない。独立にしたい検査は、抜粋との文字列一致と、状態値の列挙でオーケストレータが行う。

### 5.3 モデル

| 段 | モデル | 備考 |
| --- | --- | --- |
| `claim_extract` | 批評と同じ Ternary-Bonsai-2-27B abliterated | 批評のプロセスを殺さず続けて使う。無ければ `bonsai_select` |
| `claim_verify` | 同じプロセスの 2 呼目 | コンテキストは主張とカードだけ。質問の余談は入れない |
| `synthesize` | 同じプロセス | 検証済み主張だけを渡す |
| `claim_audit` | 同じプロセスの最終呼 | 最終文を主張に割り、カードだけで再判定 |
| `claim_drop` | オーケストレータ | LLM を呼ばない。`unsupported` と `contradicted` の文を削除 |

LM Studio の 27B は計画で既に unload 済みである。検証のために載せ直さない。

JSON が壊れたときは 1 回だけ同じプロセスで修復を求める。2 回目も壊れていれば段を失敗にし、`CLAIM_VERIFY_FAIL_OPEN=0`（既定）なら統合を出さず抜粋一覧だけ返す。`1` のときだけ現行の統合にフォールバックし、文頭に「突き合わせ失敗」と付ける。

### 5.4 状態

`MessagesState` に足すキーは次だけである。画像グラフのキーは足さない。

| キー | 型 | 誰が書くか |
| --- | --- | --- |
| `evidence` | `EvidenceCard[]` | read の正規化、または文書 map |
| `claims` | `Claim[]` | `claim_extract` / `claim_verify` |
| `claim_audit` | `Claim[]` | `claim_audit` |
| `verify_error` | 文字列または空 | オーケストレータ |

`evidence` は検索の既存抜粋を捨てずに写す。URL と引用番号 `[n]` の対応はここで固定し、統合は番号を振り直さない。

### 5.5 カード

`EvidenceCard`:

```json
{
  "evidence_id": "e3",
  "n": 3,
  "source_type": "web",
  "locator": "https://example.com/a",
  "title": "ページ題",
  "quote": "抜粋。400字以内",
  "note": "数値・日付・反証があれば 80 字"
}
```

`source_type` は `web` または `file`。`file` の `locator` は別紙の `path#heading`。検証器は `locator` を開かない。

`Claim`:

```json
{
  "claim_id": "c1",
  "text": "1 文。120 字以内",
  "status": "supported",
  "evidence_ids": ["e3"],
  "contradict_ids": [],
  "sentence_index": 0,
  "note": "判定理由。80 字以内"
}
```

`status` の意味:

| status | 最終文 |
| --- | --- |
| `supported` | 書いてよい。`evidence_ids` を `[n]` にする |
| `partial` | 書いてよいが断定しない。「出典では一部だけ」 |
| `contradicted` | 書かない。進捗表にだけ残す |
| `unsupported` | 書かない |
| `opinion` | 事実の欄に書かない。利用者の評価依頼であるときだけ「判断は出典に無い」 |

オーケストレータが拒否する出力:

- `status` が列挙外
- `supported` なのに `evidence_ids` が空
- `evidence_ids` が実在カードに無い
- `quote` の 20 字以上の連続が、どのカードの `quote` にも無い `supported`
- 主張が 13 件以上。13 件目以降は捨て、進捗に「打ち切り」と出す

文字列一致は検証器の自己申告を信用しないための門である。一致しなければその主張は `unsupported` に落とす。翻訳で字面が変わる日本語主張は、カード側 `note` の数値・日付・固有名詞が主張に含まれることを必須にする。固有名詞も数値も無い主張は `supported` にできない（`partial` まで）。

### 5.6 プロンプト契約

`prompts/system_claim_extract.txt`

- 質問に答えるために必要な検証可能な文だけを出す
- 感想、前置き、検索手順は主張にしない
- 最大 12。JSON 配列のみ

`prompts/system_claim_verify.txt`

- 渡されたカード以外を根拠にしない
- カードに無い数値を補わない
- 各主張に `status` と `evidence_ids` を付ける
- ツール呼び出しは禁止。URL を要求しない

`prompts/system_search.txt`（統合、既存）への追加規則:

- 入力の `claims` に無い事実を足さない
- `partial` はヘッジ
- `[n]` は `evidence.n` をそのまま使う

監査プロンプトは verify と同じファイルを使い、入力が「最終文を句点で割った文」である点だけ変える。

### 5.7 幅、時間、メモリ

| キー | 既定 | 意味 |
| --- | --- | --- |
| `CLAIM_VERIFY` | `1` | `0` で段をスキップ |
| `CLAIM_MAX` | `12` | 主張の上限 |
| `CLAIM_QUOTE_CHARS` | `400` | カード抜粋の再掲上限 |
| `CLAIM_TIMEOUT_S` | `120` | extract + verify + audit の合計 |
| `CLAIM_VERIFY_FAIL_OPEN` | `0` | 失敗時に無監査の統合を出すか |

検証は並列にしない。reader を殺したあとの 27B 級 1 本だけを使う。`job_lock` は統合と監査が終わるまで保持する。保持したまま画像タブは走らない。タイムアウトしたら検証を打ち切り、FAIL_OPEN の規則に従う。プロセスは必ず kill してからロックを放す。

### 5.8 利用者に返すもの

進捗（短い表）:

```text
c1 supported  [3]  ……
c2 partial    [1]  ……
c4 unsupported     出典に無いので落とす
```

最終回答の本体は表を繰り返さない。末尾の出典一覧は現行どおり。`contradicted` が 1 件でもあれば、出典の前に 1 行だけ「一部の候補は出典と矛盾したため本文から除いた」と付ける。中身の列挙は進捗側だけにする。

### 5.9 セキュリティ

- 検証段にツールを渡さない。llama-server にプロキシを渡さない
- カードの `locator` は表示用である。検証器の出力に URL があっても取得しない
- 抜粋は既存の取得上限（応答 1.5 MiB、本文 4,000 字）を超えて再取得しない
- プロンプトインジェクション（ページ内の「これまでの規則を無視」）はカードの `quote` として扱い、状態値には採用しない。状態値は JSON スキーマを通ったものだけ

### 5.10 エラー

| 条件 | 利用者向け |
| --- | --- |
| カード 0 件 | 現行どおり「検索結果がありません」。検証は呼ばない |
| JSON が 2 回壊れる | 「主張の突き合わせに失敗した」。FAIL_OPEN でなければ抜粋だけ |
| 制限時間 | 「突き合わせを打ち切った」 |
| ロック取得失敗 | 現行の「画像タブが実行中です」 |
| 重みが無い | 現行の検索失敗。検証独自の重みは増やさない |

## 6. ファイル

追加:

```text
src/furry_agent/claim_verify.py     正規化、スキーマ検査、文削除。LLM は呼ばない
prompts/system_claim_extract.txt
prompts/system_claim_verify.txt
tests/test_claim_verify.py          スキーマ拒否、番号固定、文削除
```

変更:

```text
src/furry_agent/chat_graph.py       ノード追加。画像グラフは import しない
src/furry_agent/search_agent.py     read 結果を EvidenceCard にする
prompts/system_search.txt           検証済み主張以外を書かない
.env.example                        CLAIM_* 
README.md                           検索の流れに 1 行
```

触らない:

```text
src/furry_agent/graph.py
workflows/**
comfyui_nodes/**
画像ブロックの JSON
AGENTS.md の画像経路の禁止
```

`llm_client.py` は検証から呼ばない。検証の推論は `bonsai_worker.py` の寿命規則（fork、ランダム api-key、終了時 PID kill）に乗せる。

## 7. 実装順

1. `EvidenceCard` / `Claim` と、オーケストレータ側の拒否規則。LLM なしでテストを先に書く
2. read 出力の正規化。現行の統合はまだ変えない
3. `CLAIM_VERIFY=0` のままノードを挿入し、状態だけ埋まることを確認する
4. extract と verify。壊れた JSON の 1 回修復
5. 統合プロンプトを検証済み主張に縛る
6. audit と `claim_drop`
7. 進捗表を agent-chat-ui に出す。表が出せない間は最終文の末尾 1 行に件数だけ出す
8. `probe-bonsai.ps1` の結果で、27B 級が JSON 配列を安定して返すかを見る。不安定なら既定を `CLAIM_VERIFY=0` に戻す

## 8. リスク

- 同じモデルの自己監査は甘い。文字列一致の門が本体で、モデル判定は補助である
- 日本語の言い換えで固有名詞が落ちると、正しい主張が `partial` に落ちる。欠落は誤掲載より許容する
- 主張 12 の上限で、長い比較記事は欠ける。上限を上げると 27B 級のコンテキストと時間を超える
- 監査で文を削除すると、接続詞だけが残る。`claim_drop` は空になった文と、根拠を失った「したがって」で始まる文を落とす
- 検証の 3 呼で検索全体が `CLAIM_TIMEOUT_S` を超えやすい。超えたら監査を捨て、前段の結果だけで終える

## 9. 未決

実装前に利用者が閉じる。

1. 進捗表をチャットのツール痕跡として出すか、最終メッセージの折りたたみにするか
2. `CLAIM_VERIFY_FAIL_OPEN` の既定を 0 のままにするか。0 は無監査の断定を出さない
3. `opinion` を初期から使うか。使わないなら抽出段で捨てる
4. 矛盾が複数ソースにまたがるとき、本文に「出典 [n] は逆」と 1 行残すか、進捗だけにするか（本書の既定は進捗だけ）

## 10. 実装記録（v0.6.0、2026-10-05）

### 10.1 未決の閉じ方

利用者から「判断を仰がず進める」と指示があったため、次のとおり決めた。

1. 進捗表: 実行中は進捗メッセージに表（テキスト）を出し、最終回答には `additional_kwargs.claim_trace` を付けて agent-chat-ui の折りたたみ「主張の突き合わせ」（`search-trace.tsx` の `ClaimTraceView`）に出す。最終文の末尾には件数を書かない。
2. `CLAIM_VERIFY_FAIL_OPEN` の既定は `0` のまま。
3. `opinion` は最初から使う。数値を含む `opinion` は事実の主張として `unsupported` に落とす（自己申告で門を逃れさせない）。監査では `opinion` の文は残す。
4. 矛盾は進捗（表）だけに出し、本文には出典一覧の前の 1 行「一部の候補は出典と矛盾したため本文から除いた。」だけを付ける。

### 10.2 本書との差分と吸収

| 本書 | 実装 | 理由 |
|---|---|---|
| 抽出・判定は「JSON 配列のみ」 | `{"claims":[...]}` のオブジェクト | 既存の `json_schema`（llama-server の文法制約）と検証の経路がオブジェクト前提。配列でも同じ情報 |
| `Claim` に `quote` が無いのに、§5.5 は `quote` の 20 字一致を求める | `Claim.quote`（判定器が根拠の語句を写す、任意）を足した。門は「主張文または `quote` が、引用したカードの `quote` + `note` と 20 字連続で一致」または「カードの数値・日付・固有名詞が主張に含まれる」 | §5.5 の 2 つの門をそのまま満たすため |
| `EvidenceCard` に確認済みの印が無い | `verified`（検索は reader の引用が本文で見つかったか、`/docs` は引用が節の本文にあるか）を足し、未確認カードだけが根拠の `supported` は `partial` に落とす | 未確認の引用で「支持」にしないため |
| 修復は「同じプロセスで 1 回」 | 壊れた応答をそのまま見せて「JSON だけを返し直して」と 1 回だけ頼む（`claim_nodes.ask_repair`）。検索の既存の JSON 段（同じ呼び出しの再試行）は変えていない | 本書どおり |
| 監査 | 判定が返らなかった文は、その文の `[n]` を根拠の申告として門に通す。「確認できませんでした」等の留保文は監査しない。監査は最大 24 文 | 判定の取りこぼしで正しい文を消さない／留保文は事実の主張でない |
| 文の分割 | 句点・感嘆符・疑問符で切るが、「」『』（）の中では切らない。見出し・表の行・空行は文にしない | 実機で 「…失敗した。抜粋は…」 が 2 文に割れ、片方だけ消えた |
| 支持・一部の主張が 0 件 | 統合を呼ばず「出典カードで確かめられた主張がありませんでした。」 | 無検証のカードから書かせない |
| 検索の `n` | URL の参照番号（`sa.references`）。同じ URL のカードは同じ `n` | 本書 §5.4「URL と [n] の対応はここで固定」 |
| モード | 速いモードでも検証する（速いモードは批評が無いので、読解のあとすぐ抽出） | 本書はモードを区別していない |
| 常駐モード（27B と reader が同時に載る端末） | 批評と同じく LM Studio の 27B で検証する（`_leader_client`）。この端末では常に代理リーダー | 「批評のプロセスを殺さず続けて使う」を常駐モードにも当てはめた |
| ノードの置き場 | `claim_nodes.py`（抽出・判定・監査・削除）と `chat_models.py`（llama-server の起動と停止を検索と共有） | `chat_graph.py` が 850 行を超えていたため。グラフの組み立ては `chat_graph.py` |
| 状態キー | 本書の 4 つ（`evidence`、`claims`、`claim_audit`、`verify_error`）。時間の集計と統合文の下書きは既存の `search` 辞書に置く | 本書 §5.4 のキーを増やさないため |

### 10.3 既定値と実測（ROG Ally X、代理リーダー Ternary-Bonsai-2-27B abliterated）

| 実行 | 抽出 | 判定 | 統合 | 監査 | 検証の合計 |
|---|---|---|---|---|---|
| 検索（速い、カード 4 枚、主張 4） | 39.5 s | 55.9 s | 34.7 s | 56.4 s | 151.8 s |
| `/docs`（速い、カード 3 枚、主張 9、8 文を監査） | 66.6 s | 98.5 s | — | 120.2 s | 285.3 s |
| `/docs`（思考、カード 6 枚、主張 12、9 文を監査。修正後） | 72.8 s | 160.7 s | 84 s | 126.0 s | 359.5 s |

本書の `CLAIM_TIMEOUT_S=120`（抽出 + 判定 + 監査）では、いずれも監査に届かない。既定を `600` にした（主張 12 件、カード 24 枚でも収まる見込みの値）。超えたときの動き（判定前なら抜粋だけ、監査中なら判定済みの主張から書いた統合を残す）は本書のとおり。

判定の例（上の検索）: 支持 2・一部 2（うち 1 件は「引用を原文で確認できないカード」で門が落とした）。`/docs`（速い）: 支持 5・一部 1・出典なし 3（3 件とも、カードの語句・数値が主張に無いとして門が落とした）。監査で 3 文を削除した。`/docs`（思考、修正後）: 支持 11・出典なし 1、監査 9 文で削除 0。回答は設計書のモデル表とエラー表どおりだった。

途中の 1 回（思考）は 12 件すべてが「判定が返らなかった」になった。判定の応答が `max_tokens=1100` で切れ（`finish_reason=length`）、スキーマの既定値のせいで最初の内側のオブジェクトが空の `Verdicts` として通ったためである。`claims` を必須にし、切れた応答から完全な項目だけを拾い（`claim_verify.salvage`）、`max_tokens` を抽出 1200・判定 2000 にし、判定器に短いメモを求めた（同じ入力で 951 トークンで完了）。

### 10.4 確認

- `tests/test_claim_verify.py`（門、番号固定、文の分割と削除、注入でフェンスが閉じないこと、文法に `maxLength` を出さないこと）、`tests/test_claims_docs_graph.py`（段の順、1 プロセス、修復 1 回、FAIL_OPEN、時間切れ、支持 0 件、`CLAIM_VERIFY=0` で旧来の統合、文章の資料にも監査済みの文が渡ること、ブロッキング呼び出しが無いこと）。
- 実機: 上の 2 件を LangGraph の API 経由で実行し、llama-server がすべて止まり、LM Studio が unload され、ロックが放されたことをログで確認した。
- 確認中に見つけて直したもの: `ChatSettings.from_env` が許可ルートの存在確認でディスクに触れ、langgraph dev が BlockingError で止めた。`maxLength: 2000` の JSON スキーマを llama-server が文法にできず（"failed to parse grammar"）、読解が全部落ちた。
