# locus — ローカル作業ディレクトリ向け CUI エージェント 設計書

- 状態: 実装済み（v0.7.0。コマンド名は cirka。末尾の §17 に実装記録）
- 日付: 2026-10-05
- 対象: `aofusa/local-agent`（LangGraph `:2024`）と通信する、Rust 製クロスプラットフォーム CUI
- 作業名: `locus`（バイナリ名 `locus`。名前は仮。実装開始時に確定してよい）
- 参照実装の思想: Claude Code のエージェントループとツール境界、Grok の意図理解・タスク分解・検索統合

この文書は実装契約である。UI の見た目より、責任分界・ループ・ツール・ホストとの電文を優先する。

---

## 1. 目的

シェルで作業ディレクトリに入り `locus` を起動する。利用者の日本語（または英語）の指示に対し、アプリは意図を読み、必要なタスク列を自分で作り、そのディレクトリの中で次を行う。

- ファイル検索、読取、要約、説明、調査
- ビルド・テスト・git などのコマンド実行
- ファイル作成、プログラム作成、既存ファイルの部分編集
- 必要なら `local-agent` の検索と画像生成を呼ぶ

完了条件は「モデルがツール呼び出しをやめて最終文を返した」または「利用者の確認が必要で停止した」である。Claude Code と同じく、分類器や固定 DAG ではルーティングしない。モデルが次の一手を決める。

### 1.1 やること

- Windows / macOS / Linux で同じバイナリ種（各 OS 向けリリース）として動く
- 起動 cwd をワークスペースの根とする
- ホストの LLM にツール定義を渡し、返ってきたツール呼び出しをローカルで実行する
- 読取は原則自動、書込・コマンドは許可ゲートを通す
- 途中経過（思考の要点、ツール、差分、タスク一覧）を端末に流す
- セッションを再開できる

### 1.2 やらないこと（v1）

- クラウド LLM API へのフォールバック
- ComfyUI / LM Studio / Tor への直接接続（それらはホストのループバック）
- 認証方式の独自発明（`local-agent` の AGENTS.md と同じく、利用者が決めるまで足さない）
- ベクトル検索・RAG。コード探索は glob / ripgrep
- エージェントループを LangGraph 側へ移すこと（後述）
- GUI

---

## 2. なぜループを CUI 側に置くか

`local-agent` の現状:

| 成分 | 場所 | 外から触れるか |
| --- | --- | --- |
| LangGraph `agent`（画像） | Windows ホスト `:2024` | 触れる |
| LangGraph `chat`（会話・Tor 検索） | 同左 | 触れる |
| LM Studio OpenAI 互換 | `127.0.0.1:1234` | 触らない |
| ComfyUI | `127.0.0.1:8188` | 触らない |
| Tor / 検索用 llama-server | ループバック | 触らない |
| 画像ジョブ | `job_lock` で直列 | クライアントは並列を仮定しない |

ファイルとシェルは「`locus` を起動したディレクトリ」に属す。GPU 機と開発機が分かれていても、編集対象は開発機側である。LM Studio は LAN に出ていない。したがって:

- **手**は CUI（ワークスペースのあるマシン）
- **脳**はホストの LLM。ただし CUI は LM Studio を直打ちせず、LangGraph 越しの新しいゲートを使う
- **検索と画像**は既存グラフに委譲するリモートツール

Claude Code が「`while (tool_call) { execute; send result }` で、プランナー分離も RAG も持たない」のと同じ形にする。Grok 側から採るのは、曖昧な依頼をタスク列に落とし、検索結果を出典付きで統合し、期待とずれたら打ち切って聞き返す挙動である。

```
利用者
  │  日本語の依頼
  ▼
locus（起動 cwd）
  │  エージェントループ
  │  ローカルツール: read / edit / write / glob / grep / bash / todo
  │
  ├── 推論ターン ──▶ ホスト :2024  graph `coder`（新設、ツールは実行しない）
  │                      └── LM Studio（ループバック）
  ├── 検索ツール ──▶ ホスト graph `chat`（既存、Tor 検索）
  └── 画像ツール ──▶ ホスト graph `agent`（既存、ComfyUI）
```

ループをホストに置く案は棄てる。ツール結果（ファイル本文、テストログ）を毎回グラフ状態に積むと、ワークスペースのソースが GPU 機に残り、中断・許可プロンプト・cwd の意味がぼやける。ホストは「ツールを知っているが実行しないモデルゲート」に留める。

---

## 3. 利用者から見たワークフロー

```
$ cd ~/src/some-project
$ locus
```

1. cwd、git 状態、`AGENTS.md` / `LOCUS.md` の有無を読む
2. プロンプトを出す
3. 依頼を受ける。例: 「このクレートのビルドが落ちる原因を調べて直して」
4. モデルがタスク一覧を作り（`todo_write`）、端末に出す
5. 探索（glob / grep / read）→ 仮説 → 編集 → コマンドで検証 → タスクを閉じる
6. 危険なコマンドや想定外の書込は止まって聞く
7. 最終文で、何を変え、何を実行し、何が未了かを述べる

短い質問（「この関数は何をしている」）はタスク一覧を作らず、読んで答えて終わる。ループは依頼の形に合わせる。固定の「必ず計画モード」にはしない。

スラッシュコマンド（v1）:

| コマンド | 動作 |
| --- | --- |
| `/help` | コマンド一覧 |
| `/cd <path>` | ワークスペース根を変更（許可後） |
| `/mode fast\|think\|auto` | ホストの思考予算。既定 `auto` |
| `/plan` | 以降、書込とコマンドを提案だけにする |
| `/accept-edits` | ワークスペース内の編集を自動許可。コマンドは都度 |
| `/undo` | 直近のエージェント編集を git または退避から戻す |
| `/compact` | 会話を要約して窓を空ける |
| `/search <q>` | ホスト検索を明示呼び出し |
| `/image <指示>` | ホスト画像生成を明示呼び出し |
| `/resume` | 直前セッション |
| `/quit` | 終了 |

---

## 4. エージェントループ

外側は会話、内側はツールが尽きるまでのターン。

```
on_user_message(text):
    append user message
    loop until stop:
        req = assemble(system, project_rules, git_snapshot, todos, messages, tool_schemas)
        event_stream = host.coder_turn(req)          # SSE
        msg = render_stream(event_stream)
        if msg.tool_calls is empty:
            return                                   # 最終文。利用者待ち
        results = []
        for call in partition(msg.tool_calls):       # 読取系は並列、書込と bash は直列
            decision = policy.check(call)
            if decision == deny: results.push(denied)
            else if decision == ask:
                if not user_approves(call): results.push(rejected); continue
            results.push(execute(call))
        append assistant msg + tool results
        if turn > max_turns or tokens > budget or user_interrupted:
            stop and summarize
```

終了理由は記録する。`completed` / `max_turns` / `interrupted` / `permission_denied` / `host_error` / `context_overflow`。

### 4.1 自律のルール

モデルにシステムプロンプトで渡す契約:

- 依頼が複数手順なら、先に `todo_write` で 3〜7 項目。1 手順なら作らない
- 各項目は「調べる / 変える / 確認する」のどれかで、検証手段を持つ
- 編集の前に対象を読む。未読ファイルへの `edit_file` は実行側が拒否する
- 検証できる変更はコマンドで確認する（`cargo test`、`pytest` など、リポジトリのやり方に従う）
- 同じ失敗を 2 回繰り返したら方針を変えるか利用者に聞く
- 秘密ファイル（`.env`、鍵、`credentials`）は読まない。必要なら聞く
- 検索が要る事実（ライブラリの現行 API、エラーの既知問題）は `web_search` に出す。記憶で埋めない
- 画像が成果物のときだけ `image_generate`。コード作業の途中で呼ばない

タスクツールの状態: `pending` / `in_progress` / `done` / `blocked`。同時に `in_progress` は 1 つ。

### 4.2 計画モード

`/plan` 中は `write_file` / `edit_file` / `bash` を実行しない。モデルはパッチ案と実行するつもりのコマンドを最終文に書く。利用者が承認したら通常モードで同じスレッドを続ける。Claude Code の plan mode に相当する。大規模変更の既定にはしない。

---

## 5. ツール

ツール定義は JSON Schema でホストへ渡す。CUI が実体を持つ。名前は安定させる（モデル向け契約）。

### 5.1 ローカル

| ツール | 副作用 | 既定許可 | 契約 |
| --- | --- | --- | --- |
| `list_dir` | 無 | 自動 | `path`、深さ上限 2、件数上限 200。隠し・無視は gitignore 準拠 |
| `glob` | 無 | 自動 | `pattern`。`ignore` クレート。上限 200 |
| `grep` | 無 | 自動 | `pattern`、`glob`、`path`。ripgrep 互換。1 呼び出しあたり出力 100 マッチ / 32 KiB で切る |
| `read_file` | 無 | 自動 | `path`、`offset`、`limit`（既定 400 行、最大 2000）。バイナリは拒否し種類だけ返す |
| `edit_file` | 有 | 聞く（`/accept-edits` で自動） | `path`、`old`、`new`。`old` はファイル内で一意。先に当該パスを `read_file` 済みであること |
| `write_file` | 有 | 聞く | 新規作成、または全置換。既存を置換するなら読了済みであること。差分を表示してから書く |
| `bash` | 有 | 聞く | `command`、`timeout_s`（既定 120、最大 600）、`cwd` はワークスペース内のみ |
| `todo_write` | セッション | 自動 | タスク列の置換または部分更新 |
| `ask_user` | 無 | 自動 | 選択肢つきの確認。曖昧で破壊的な分岐だけ |

`edit_file` は Claude Code の Edit と同じく、行番号パッチではなく一意な原文一致とする。モデルがずれたら「一致せず」を返し、読み直させる。ファジー適用は v1 ではやらない（静かに壊すため）。

### 5.2 リモート（ホスト）

| ツール | 呼び出し | 注意 |
| --- | --- | --- |
| `web_search` | graph `chat`。入力は検索依頼文。CUI は `/search` を前置しない（グラフ側の起動条件に合わせ、必要なら CUI が検索意図の文面にする） | 結果は出典付きテキスト。CUI は本文を要約せず、モデルに渡す。タイムアウトはホストの `SEARCH_WALL_CLOCK_S`（1200s）を上限に、CUI 側は 300s で一度状態表示 |
| `image_generate` | graph `agent`。日本語指示、参照画像 0〜4（役割 `character` / `pose` / `style` / `base` / `mask`、強度） | ホストは `job_lock` で直列。チャット推論と同時に走らない前提を CUI が守る。戻りは base64 PNG。CUI はワークスペースの `locus-outputs/` に保存し、パスだけをモデルに返す。URL や `127.0.0.1:8188` は表示しない |

参照画像はローカルパスで受け、CUI が base64 にして `configurable.references` 相当で送る。ホストの既存契約（役割・枚数・直列）を破らない。

### 5.3 v1 で持たないツール

サブエージェント（`task`）、MCP クライアント、ノートブック編集、worktree 切替、LSP 診断。接続点だけ残す（§10）。

---

## 6. ホストとの通信

### 6.1 新設: graph `coder`（モデルゲート）

`local-agent` にグラフを 1 つ足す。これは LangGraph の「道具を実行するエージェント」ではない。1 ターン分の補完である。

役割:

- 受信した `messages` と `tools` を LM Studio の OpenAI 互換 Chat Completions（tool calling）へ渡す
- ストリームを SSE で返す
- ツールは実行しない。システムプロンプトの上書きもしない（CUI が渡したものを透過）
- 思考予算は `configurable.mode = fast | think | auto`。既存チャットの意味に揃える

推奨エンドポイント（LangGraph のスレッドを使わない、無状態）:

```
POST /coder/turn
Content-Type: application/json
Accept: text/event-stream

{
  "mode": "auto",
  "messages": [ { "role": "system"|"user"|"assistant"|"tool", "content": "...", "tool_calls": [], "tool_call_id": "..." } ],
  "tools": [ { "name": "read_file", "description": "...", "parameters": { } } ],
  "max_tokens": 4096
}
```

SSE:

```
event: token
data: {"text":"..."}

event: tool_call
data: {"id":"call_1","name":"grep","arguments":"{...}"}

event: done
data: {"finish_reason":"tool_calls"|"stop","usage":{"input_tokens":0,"output_tokens":0}}

event: error
data: {"message":"..."}
```

無状態にする理由: ソース断片をホストのスレッドストアに残さない。再開用の履歴は CUI のセッションファイルが正である。

LM Studio が tool calling を不安定に返す場合の退縮: ゲートが失敗を `error` で明示し、CUI はそのターンを破棄する。XML ツール呼び出しの自前パーサは v1 に入れない（二重実装になる）。必要になったらゲート内の一箇所に閉じる。

このグラフの追加は `local-agent` 側の作業である。CUI 単独では脳に届かない。実装順は §12。

### 6.2 既存グラフ

検索・画像は LangGraph の公開 API を使う。

```
POST /runs/stream
{
  "assistant_id": "chat" | "agent",
  "input": { "messages": [ { "role": "human", "content": "..." } ] },
  "config": { "configurable": { "mode": "think", "references": [] } },
  "stream_mode": "messages-tuple"
}
```

CUI は返却テキストから画像 content block（`type=image`, `mimeType=image/png`, `data`）を抜き、ファイルへ書く。生パスはモデルへ渡さない。

### 6.3 到達性

- ベース URL は設定。既定 `http://127.0.0.1:2024`（同一 Windows 機）。別機なら LAN IP
- ヘルス: `GET /ok` または `/docs`。起動時に graph 一覧を取り、`coder` が無ければ検索・画像だけ使える旨を出してファイル作業は止める
- 認証ヘッダは設定にあれば付ける。方式は未確定のまま、空欄を既定とする
- タイムアウト: 推論の無通信 120s で打ち切り、検索 300s、画像 600s（ホストの `COMFYUI_TIMEOUT_S` に合わせる）

### 6.4 送ってよいもの / 送らないもの

送る: システムプロンプト、利用者が打った文、ツール結果（ファイル断片、コマンド出力、検索結果）。

送らない（既定）: `.env`、`*.pem`、`id_rsa`、`credentials*`、マッチした秘密らしき行。ツール結果に鍵形式が出たら CUI が `[redacted]` にしてから送る。これは完全ではないので、信頼できる LAN だけで使う前提を起動時に 1 行出す。

---

## 7. コンテキストの組み方

毎ターン CUI が組み立てる。ホストは記憶しない。

1. 固定システムプロンプト（ループ契約、ツールの失敗時の動き、言語は利用者に合わせる）
2. プロジェクト規則。探索順: `./LOCUS.md`、`./AGENTS.md`、`./CLAUDE.md`、親は見ない（v1）。各 16 KiB で切る
3. 環境スナップショット。OS、シェル、cwd、git ブランチ、直近でない短い status。毎回フル diff は載せない
4. タスク一覧
5. 会話。ツール結果は古いものから落とす

予算の目安（27B・文脈 4096〜8192 を仮定）:

- システム＋規則で 1500 トークン以内
- ツール結果 1 件 8 KiB 以内（超えたら CUI が頭と末尾を残して切る）
- 残量の 70% を超えたら、自動で古いツール結果を「已実行: grep path pattern → 12 matches」に潰す
- それでも溢れたら `/compact` と同じ要約ターンを挟む。要約もホストの `coder` に書かせ、元ログはセッションに残す

`ignore`: `.git`、`target`、`node_modules`、`dist`、画像出力、バイナリ。追加は `.locusignore`。

---

## 8. 許可と実行境界

モード:

| モード | 読取 | ワークスペース内編集 | ワークスペース外 | bash |
| --- | --- | --- | --- | --- |
| `default` | 自動 | 確認 | 拒否 | 確認 |
| `accept-edits` | 自動 | 自動 | 拒否 | 確認 |
| `plan` | 自動 | 実行しない | 拒否 | 実行しない |
| `bypass` | 自動 | 自動 | 確認 | 自動。明示起動のみ。既定にしない |

確認プロンプトはコマンド全文または unified diff を見せ、`y` / `n` / `a`（以降このセッションで同種を許可）/ `q`。

`bash` の追加拒否（モードによらず）:

- `cwd` がワークスペース外
- 改行で繋がれた別コマンドのうち、未表示のものはない（全文を見せる）
- v1 は OS サンドボックスを必須にしない。代わりにタイムアウト、出力上限 64 KiB、プロセスグループごと kill
- 後続で任意: Linux bubblewrap、macOS seatbelt、Windows 制限トークン。インターフェースは `CommandRunner` の裏に隠す

ネットワーク遮断は v1 ではしない。ビルドが依存を取るため。ホストの Docker サンドボックス（`--network none`）とは別物であり、混ぜない。

編集はワークスペース内の相対パスのみ。シンリンクの実体が外なら拒否。

---

## 9. 端末 UX

完全な画面占有 TUI を v1 の必須にしない。Claude Code に近いストリーミング REPL を正とする。

- クロスターミナルは crossterm。色が無ければプレーン
- アシスタント本文はトークン追記
- ツールは折りたたみブロック。名前、引数の要約、終了コード、先頭数行。`Ctrl-O` で展開（v1.1 でもよい）
- タスク一覧は変化のたびに短いリストを描き直す
- 許可待ちは入力を奪う。待ちの間も `Ctrl-C` でそのツールだけ止める。二度目でターンを放棄
- 画像は保存パスを出す。端末画像プロトコル（iTerm / Kitty）は任意
- ログは `~/.local/share/locus/sessions/<id>.jsonl`（Windows は `%LOCALAPPDATA%\locus\sessions`）

設定 `config.toml`:

```toml
host = "http://127.0.0.1:2024"
mode = "auto"            # fast | think | auto
permission = "default"
shell = "auto"           # auto | bash | pwsh | cmd
max_turns = 40
locale = "ja"
```

探索順: `./.locus/config.toml`、`$XDG_CONFIG_HOME/locus/config.toml`、Windows は `%APPDATA%\locus\config.toml`。

---

## 10. Rust 構成

エディション 2024。非同期は tokio。UI スレッドと実行を混ぜない。

```
locus/
  Cargo.toml
  src/
    main.rs              # 引数、起動
    app.rs               # REPL と描画
    loop_.rs             # エージェントループ（純ロジック、UI 非依存）
    context.rs           # プロンプト組立、圧縮
    policy.rs            # 許可
    session.rs           # jsonl
    tools/
      mod.rs             # スキーマとディスパッチ
      fs.rs              # list/glob/read/edit/write
      grep.rs
      bash.rs
      todo.rs
      remote.rs          # web_search, image_generate
    host/
      coder.rs           # /coder/turn SSE
      langgraph.rs       # /runs/stream
    platform/
      paths.rs
      shell.rs           # OS ごとのシェル選択
```

依存の方針:

- HTTP: reqwest（rustls）
- SSE: 自前の小さいパーサか eventsource 系。巨大な LangChain SDK は入れない
- 検索: `ignore` + `globset`。grep は同梱 `rg` より、`grep-regex` / `ignore` で歩く実装を優先（配付を単一バイナリに保つ）。速度が足りなければ `rg` があればそれを使う
- 差分表示: 類似実装で足りる。v1 は行 diff
- TUI フレームワーク（ratatui）は v1 では入れない。REPL が苦しくなったら足す

ライブラリクレートにはしない。バイナリ 1 つ。テストは `loop_` と `policy` とツールを純関数として切る。

エラーは `thiserror`。ツール失敗はパニックにしない。モデルへ「失敗と理由」を返し、ループを続ける。

---

## 11. クロスプラットフォーム

| 関心 | Windows | macOS / Linux |
| --- | --- | --- |
| シェル既定 | PowerShell | `sh -lc` |
| パス | `camino` 経由で UTF-8 に正規化。失敗パスは表示してスキップ | 同左 |
| 設定場所 | `%APPDATA%\locus` | XDG |
| プロセス停止 | Job Object または子の kill | プロセスグループ |
| 改行 | 編集は既存ファイルの改行を保持 | 同左 |

同一ソースから `cargo build --release` で各 OS バイナリを出す。Windows の GPU 機に CUI を置く構成と、Mac から LAN のホストを叩く構成の両方を第 1 級とする。

---

## 12. 実装段階

各段階は単独で使えること。

1. **ホストゲート**（`local-agent`）: `/coder/turn` が LM Studio へツール定義を通し、SSE でトークンと tool_call を返す。ツールは実行しない。テストは固定 JSON の往復
2. **CUI 骨格**: 設定、REPL、ゲート接続、ツールなし会話
3. **読取**: `list_dir` / `glob` / `grep` / `read_file`。要約と調査が終わる
4. **書込**: `edit_file` / `write_file`、diff 確認、`accept-edits`
5. **コマンドとタスク**: `bash`、`todo_write`、中断、最大ターン。ここが「ビルドして直す」の完了地点
6. **リモート**: `web_search`、`image_generate`、ホストビジー表示
7. **持続**: セッション再開、圧縮、`.locusignore`、`LOCUS.md`

v2 候補: MCP クライアント、サブエージェント、plan の永続化、worktree、OS サンドボックス、ratatui。

---

## 13. テスト

- ループ: ツール呼び出し 2 回のあと最終文、で停止する（ホストはモック）
- 許可: default で bash が実行前に止まる。拒否結果がモデルに届く
- `edit_file`: 一意でない `old` は書かない。未読パスは書かない
- パス: `../` とワークスペース外シンリンクを拒否
- 秘密: `.env` を読もうとしたらツールが拒否する
- SSE: 途中切断でターンが `host_error` になり、半分のツール実行をしない（ツールは done 後）
- 画像: モックの content block をファイルへ書き、モデルへはパスだけ
- OS: パス正規化とシェル引数のテーブルテスト。実機は Windows と Linux を CI、macOS は手元

ホスト側のゲートテストは `local-agent` の pytest に置く。CUI の CI はホストを立てない。

---

## 14. セキュリティと運用上の前提

- 認証未実装の LAN に、ワークスペースの断片が飛ぶ。信頼できないネットワークでは使わない
- `bypass` はシェル等価である。案内にそう書く
- ホストの `job_lock` により、画像生成中は推論ゲートも待ち得る。CUI は待ちをエラーにしない
- 生成画像のライセンスとモデルの利用条件はホスト側のまま。CUI は再配布しない
- ログにツール結果が残る。セッション削除コマンドを v1 に入れる

---

## 15. 未決事項

実装前に利用者が決めるもの。決めない間は括弧内で進める。

1. バイナリ名（`locus`）
2. 認証を足すか（足さない。ヘッダの差込口だけ）
3. `coder` を LangGraph グラフとして登録するか、素の HTTP にするか（素の `POST /coder/turn`。スレッドにソースを残さない）
4. 27B の tool calling が実用に足りるか。足りなければゲート内で一度だけリトライし、それでもダメなら利用者に表示して止める
5. v1 のシェルを常に確認にするか、ワークスペース内の `cargo` / `pytest` / `git status` だけ自動にするか（常に確認）

---

## 16. 参照

- `aofusa/local-agent` README / AGENTS.md。グラフ `agent` と `chat`、ポート 2024 / 3000、ループバックの ComfyUI・LM Studio、認証未確定、画像は base64、ジョブ直列
- Claude Code のエージェントループ: gather / act / verify が混ざる。ツールが主体。ファイル・検索・実行・Web。Edit は原文一致。計画モードと権限モードを分ける
- Grok 系の会話エージェント: 依頼の意図からタスクを切り、検索を出典付きで統合し、足りない事実を埋めない

---

## 17. 実装記録（v0.7.0）

状態: 実装済み。段階 1〜7（§12）をまとめて実装した。

### 17.1 決めたこと（§15 の未決事項）

| 項目 | 決定 |
| --- | --- |
| バイナリ名 | `cirka`（利用者の指定）。ディレクトリは `cirka/`、設定とデータの置き場も `cirka`（`.cirka/config.toml`、`.cirkaignore`、`CIRKA.md`、`cirka-outputs/`、`CIRKA_HOST`） |
| 認証 | 足さない。`auth_header`（`Name: value`）を設定すれば全リクエストに付けるだけ |
| `coder` の形 | 素の `POST /coder/turn`（LangGraph のスレッドを使わない）。`langgraph.json` の `http.app` で LangGraph サーバに載せる。グラフ一覧には出ないので、到達確認は `GET /coder/health`（`gate: "coder"`、LM Studio の到達、モデル、`context`、使用中のタブ）で行う |
| 27B の tool calling | LM Studio 0.4 のネイティブ解析で足りた（下の実測）。XML の自前パーサは入れていない。LM Studio が開始時に HTTP エラーを返したときだけ、ゲート内で 1 回送り直す |
| シェルの確認 | 常に確認（`accept-edits` でもコマンドは確認。`bypass` だけ確認なし） |

### 17.2 設計との差分と吸収

- **接続先の設定**（利用者の要件）: §9 の `config.toml` に加え、`cirka config get|set|unset|path|show`（`--project` でディレクトリごと）、環境変数 `CIRKA_HOST` / `CIRKA_MODE` / `CIRKA_PERMISSION` / `CIRKA_AUTH_HEADER`、`--host`、実行中の `/host <URL> [--save]` を足した。優先は「既定 < ユーザー < プロジェクト < 環境変数 < コマンドライン」。値は書く前に検証する（不正な host やモードで黙って既定に戻らない）。
- **ゲートの位置**: `coder_gate.py` を LangGraph がファイルパスで読むと、モジュールが `sys.modules` に登録されず `@dataclass` が失敗した。薄い入口 `coder_app.py`（パッケージから `app` を import するだけ）を `http.app` に指定した。
- **ジョブロック**: ゲートは tab `coder` で `job_lock` を握る。ほかのタブが握っているあいだは `event: status`（待っているタブと秒数）を 5 秒ごとに送り、最大 600 秒待つ（§14「待ちをエラーにしない」）。ComfyUI のキューが動いているあいだも待つ。
- **SSE の追加イベント**: `status`（上記）と `thinking`（思考モードの思考トークン。回答とは別に表示）を足した。`tool_call` はストリームの終わり（`done` の直前）にまとめて送り、cirka は `done` を受けるまでツールを実行しない（§13「途中切断で半分のツールを実行しない」）。
- **context 4096**: §7 の予算は 4096〜8192 の想定だったが、この端末は 4096 で固定。ツールの説明を 1 行に縮め（11 ツールで JSON 約 2.9 KB）、システムプロンプトを約 300 トークンにした。履歴は 70 % を超えたら直近 2 件以外のツール結果を 1 行に潰し、さらに溢れたら古いやり取りを（assistant とその tool 結果を組で）落とす。1 件のツール結果の上限は窓から決める（4096 なら約 3 KB、最大 8 KiB）。ゲートは推定トークンが窓に入らなければ `error: context_overflow` を返し、cirka は 6 割に詰めて 1 回だけ送り直す。
- **同じ失敗の繰り返し**（§4.1）: 同じツール・同じ引数が 2 回失敗したら、2 回目の結果に「方針を変えるか ask_user で聞いてください」を足す。
- **非対話モード**: `cirka -p "<依頼>"` を足した（1 回の依頼で終わる。確認が要る操作は拒否し、`--permission` で許可）。CI や試験に使う。
- **TUI と構成**: §10 の `loop_.rs` は `agent.rs`、`platform/` は `platform.rs` 1 ファイルにした。境界（Brain / Frontend のトレイトで UI とホストを外す）は設計どおり。Windows のシェルは PowerShell 7 があれば `pwsh`、無ければ Windows PowerShell で、出力を UTF-8 にする前置きを付ける。
- **`/forget`**（§14 のセッション削除）と `/todos`、`/status`、`/default` を足した。`/undo` は直前のエージェントの編集を 1 件ずつ戻す（新規作成は削除）。

### 17.3 確認

- `cargo test`: 53 件（設定、パスの境界と秘密ファイル、伏せ字、SSE の分割と切断、ループ（ツール 2 回のあと最終文、default で bash が止まり拒否がモデルに届く、q で依頼が止まり全呼び出しに結果、未読の edit 拒否、plan で書かない、溢れたら詰めて 1 回だけ再送、最大ターン、同じ失敗の指摘、タスク一覧がプロンプトに入る、要約）、ツール（grep、glob と .gitignore、CRLF の保持、undo、bash の終了コードと時間切れでの停止）、画像の保存とパスだけを返すこと、セッションの再開）。
- pytest `tests/test_coder_gate.py`: 12 件（トークン、断片の tool_call の組み立て、思考の分離、tool 結果の往復、context_overflow、max_tokens の上限、不正な要求の 400、LM Studio の拒否の再送、画像タブの待ちと status、ComfyUI のキューが動いているあいだの待ちと打ち切り、auto の判定、health）。
- この端末（ROG Ally X、27B IQ3_M、context 4096）での実行:
  - ゲート単体: grep のツール定義を渡した 1 ターンで、27B が `grep` の tool_call を正しい JSON 引数で返した（134 秒、モデルのロード込み）。
  - cirka: テストが落ちる小さな Python プロジェクトで `cirka --mode fast --permission accept-edits -p "test_calc.py のテストが落ちる原因を調べて src/calc.py を直してください。"`。todo_write（3 項目）→ read_file × 2 → edit_file（`a - b` → `a + b`、確認なしで書き込み）→ bash（pytest。非対話なので拒否）→ 別の引数で bash（拒否）→ todo_write（確認の項目を blocked）→ 最終文（直したこと、手で確認するコマンド）。7 ターン、約 10 分 30 秒（1 ターン 23〜205 秒）。直したあとのテストは 2 件とも合格。

### 17.4 v0.8.0 の変更

- **許可モード `auto` を既定にした**（利用者の指定）。§8 の表に `auto` を足す: 読取・編集・コマンドを確認なしで実行する。ただし `policy::guarded` の一覧に当たるコマンド（git push / reset --hard / clean -f / 変更の一括破棄 / ブランチの強制削除、再帰的な削除、ディスクの初期化や dd、電源、sudo・runas、レジストリと実行ポリシー、ダウンロードの直接実行、npm・cargo の publish、Docker の prune、権限の一括変更）は確認する。§15 の 5（シェルは常に確認）は、この指定で置き換えた。`default` / `accept-edits` / `plan` / `bypass` は残し、Shift+Tab で auto → default → accept-edits → plan を巡回する（bypass は明示したときだけ）。
- **画面を Claude Code に倣って作り直した**（§9。v1 の「ストリーミング REPL」の範囲で、TUI フレームワークは入れていない）: ロゴ入りのウェルカム枠、枠付きの入力欄（raw mode。複数行、履歴、Tab 補完、貼り付けの判定）と許可モードの行、`⏺` / `⎿` のブロック、行番号付きの差分、タスク一覧、スピナー、矢印キーの確認メニュー。raw mode は入力欄とメニューのあいだだけで、出力は通常の行なので `-p` やパイプでも読める。Windows はキーを離したイベントも届くので、貼り付けの判定（Enter の直後に次のキーが待っている）では離したイベントを数えない。
- **ロゴ**: `docs/logo/cirka-icon.jpg` と `cirka-logo.jpg` を `scripts/gen_cirka_art.py` が小さなビットマップ（`cirka/src/art_data.rs`、生成物をコミット）にし、半角ブロック（▀ ▄ █）で 1 文字 = 縦 2 画素として描く。アイコンは 22 / 14 画素幅、文字は 44 / 30 画素幅。端末の幅と高さで選ぶ。
- 確認: `cargo test`（65 件）、ConPTY（pywinpty + pyte）で実際の cirka を動かした画面の確認（ウェルカム枠、Shift+Tab、/help、auto での読み取り → 編集（差分表示）→ コマンド → 最終回答、auto でも `rm -rf` は確認メニューになり「いいえ」がモデルに伝わる）。

### 17.5 v0.8.1 の変更

- ソースのディレクトリを `cirka/` から `client/` に変えた（利用者の指定。固有名詞ではなく、クライアント側のアプリという役割で汎用的に名付ける）。§10 の構成図の `locus/` は `client/` に読み替える。実行ファイル名・設定とデータの置き場・スクリプト名は `cirka` のまま。
