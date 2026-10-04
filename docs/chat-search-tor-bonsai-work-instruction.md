# チャットタブと Tor 経由検索（Bonsai）実装記録

- 対象設計書: `docs/chat-search-tor-design-bonsai-tabs.md`（0.3-bonsai）
- 版: v0.4.0（2026-10-04）
- 実機: ROG Xbox Ally X（AMD Ryzen AI Z2 Extreme、Radeon 890M、共有メモリ 24GB、Windows 11）
- 本書は設計書 §2 のとおり、実装差分・実測値・Tor と fork の起動確認を記録する。設計書は書き換えない。

## 1. 入力にした追加要件

設計書のあとに利用者から受けた指示。設計書と食い違うところは、こちらを優先した（§3）。

1. モデルは Bonsai 8B、Ternary Bonsai 8B、Bonsai 4B、Bonsai 2 27B、Bonsai 2 27B abliterated、Qwen3.5-4B-heretic Q4_K_M、Qwen3-1.7B-heretic、Qwen3-0.6B-heretic の 8 つ。検証時または実行時に、タスクに応じて自動選択する。
2. 検索時に Qwen3.8 27B abliterated とマルチエージェントのモデルが同時に乗らない場合は、最初の処理だけ 27B を使い、unload する。その後は Override-6/Ternary-Bonsai-2-27B-abliterated-gguf の PTQ1_0 を専用の llama.cpp で起動し、代理にする。
3. 画像タブは既存のワークフローのまま、LM Studio の 27B を使う。
4. Tor の準備・起動・接続と、llama.cpp の構築・起動をアプリに統合する。
5. 役割分担の参考（会話中の追加情報）:
   - 本体（Planner / Critic / Synthesizer）は Ternary Bonsai 2 27B abliterated。
   - ワーカー（ページ要約・証拠抽出）は Ternary Bonsai 8B、代替は Qwen3.5-4B。
   - ルータ（意図分類・クエリ書き換え）は Qwen3-1.7B、高速フィルタは Bonsai 4B。
   - 1-bit Bonsai 8B と Qwen3-0.6B は原則不採用。
   - グラフは route → plan → search → read → critique →（再検索 1 回まで）→ synthesize。
   - JSON は Pydantic で検証し、同じノードを 1 回だけ再試行する。

## 2. 実装した構成

```text
agent-chat-ui（画像 / チャットのタブ）
  ├ 画像タブ   graph agent（= image）  現行のまま
  └ チャットタブ graph chat
        ingest ─┬─ chat（LM Studio 27B）
                ├─ route（Qwen3-1.7B: 規則で決まらない質問だけ）
                └─ plan（LM Studio 27B → 乗らなければ unload）
                     └ Send → search ×意図（Python、Tor、モデルなし）
                          └ filter（Bonsai-4B）
                               └ Send → read ×reader（Ternary-Bonsai-8B、open_page を最大 2 回、事実カード）
                                    └ critique（代理 27B: 不足があれば 1 回だけ search へ）
                                         └ synthesize（代理 27B: カードだけを根拠に [n] 付きで回答）
```

| プロセス | 待受 | 寿命 |
| --- | --- | --- |
| Tor | `127.0.0.1:9050`（SOCKS、socks5h のみ） | `start-all.ps1` が起動。未起動ならチャットタブが自動起動（`TOR_AUTOSTART=1`） |
| PrismML llama-server（reader） | `127.0.0.1:18181〜18183` | reader ごとに起動し、終わったら PID を kill |
| 同（ルータ / フィルタ） | `127.0.0.1:18188〜18189` | 1 回の判定ごとに起動して kill |
| 同（代理リーダー） | `127.0.0.1:18190` | 最初の critique で起動し、synthesize の後に kill |

モデルと役割の対応は `config/search_models.json` の `tasks` が持つ。実機で検証した結果（`tools/bonsai/rank.json`）が、その順位を上書きする。

| タスク | 既定の順 |
| --- | --- |
| route | Qwen3-1.7B-heretic > Qwen3.5-4B-heretic > Qwen3-0.6B-heretic |
| plan（LM Studio を使わないとき） | Bonsai 2 27B abliterated > Bonsai 2 27B > Qwen3.5-4B-heretic > Ternary 8B |
| filter | Bonsai-4B > Qwen3-1.7B-heretic > Qwen3-0.6B-heretic |
| worker（reader） | Ternary 8B > Qwen3.5-4B-heretic > Bonsai 8B > Qwen3-1.7B-heretic > Bonsai 2 27B |
| critique / synthesize | Bonsai 2 27B abliterated > Bonsai 2 27B > Qwen3.5-4B-heretic > Ternary 8B |

実行時の選択は設計書 §5.6 に従う。順位の上から、次の条件で候補を外す。

- 重みファイルが無い。
- probe で不合格になった。
- LM Studio の 27B、または ComfyUI がモデルを保持している間の 27B 級。
- 空きメモリから予約分を引くと 1 体も入らない。

入るモデルのうち、最初のものを使う。幅は `min(意図の数, SEARCH_FANOUT_WIDTH, (空き − 予約) / 実測メモリ)` とする。起動に失敗したら次点を 1 回だけ試す。`BONSAI_MODEL` で reader を固定でき、その場合は入らなければ拒否し、別のモデルへ黙って切り替えない。

## 3. 設計書との差分と吸収方法

| 設計書 | 実装 | 理由 |
| --- | --- | --- |
| 候補 4 つ（Bonsai 8B / Ternary 8B / 4B / Bonsai 2 27B） | 8 つ。Override-6 の abliterated 版と Qwen heretic 3 種を追加 | 追加要件 1 |
| 統合はリーダー（LM Studio 27B）が 1 回 | 計画は LM Studio 27B。reader が同居できなければ 27B を unload し、批評と統合は Bonsai 2 27B abliterated（PTQ1_0、fork）が代理で行う（proxy モード）。入る環境では 27B のまま（resident モード） | 追加要件 2。実測で 27B（IQ3_M）ロード中の空きは 0.1〜0.8GB で、reader は 1 体も入らない |
| ワーカーが web_search を 1 回呼ぶ | 検索は Python が意図ごとに並列で行う（モデルなし）。reader（ワーカー）は結果から開くページを `open_page` のツール呼び出しで選ぶ（最大 2 回、同じ URL は再取得しない）。その後、URL ごとの事実カードを返す | 追加要件 5（Grok 型: 計画は検索語だけを書き、ページ読みは問いに関係する事実だけを抜く）。ツール呼び出しの回数上限と同一クエリの破棄は §5.4 のまま |
| ワーカーのツール口は 127.0.0.1 の HTTP | ツールはオーケストレータのプロセス内で実行する。llama-server はツール呼び出しを返すだけで、ネットワークに触れない | SOCKS を開くのがオーケストレータだけ、という §5.10 をより強く満たす |
| リーダーが統合 1 回 | critique を追加。不足する意図を最大 2 本返し、追加の検索を 1 回だけ行う。回答は書き換えない | 追加要件 5。Grok の「穴の判定」 |
| — | route（Qwen3-1.7B）を追加。検索語の無い質問文（「〜はいつ？」など）だけを判定させる。「こんにちは」では起動しない | 追加要件 5。§7 の完了条件（「こんにちは」で llama-server を起動しない）は維持 |
| — | filter（Bonsai-4B）を追加。タイトルと抜粋で関係の無い結果を落とし、意図ごとに最低 2 件は残す | 追加要件 5 |
| JSON 以外は規則で落とす | Pydantic で検証し、`json_schema` の文法制約で出力させる。不正なら同じ呼び出しを 1 回だけ再試行し、その後は規則（抜粋をカードにする / 規則で 1 本の検索）に落とす | 追加要件 5 |
| 引用の確認なし | カードの quote が、実際に取得した本文・抜粋に含まれるかを照合する（`quote_ok`）。統合には「引用確認済み / 未確認」を付けて渡す | Grok の「裏を取る」を、モデル往復なしで行うため |
| ページ本文 4,000 字 | reader へは質問との語の重なりで選んだ行を 1,200 字だけ渡す（`focus_text`）。1 ページの取得は 20 秒まで、ページを開くのは予算の 6 割まで | 実機 e2e で、Tor の遅い 2 ページ目に 150 秒を使い切った。4k コンテキストに 2 ページ全文は入らない |
| `BONSAI_RESERVE_MB` 8192 | 3072 | 実測の空きは、27B を unload した状態で 14.1〜14.4GB。8GB を予約すると代理 27B（6.6〜6.8GB）が入らない |
| `news` / `x` / `browse` のツール | `web`、`news`（DuckDuckGo の期間指定で過去 1 か月）、`browse`（利用者が貼った URL だけ）。`x` は無い | 許可ホストは検索エンジンと結果 URL と利用者の URL だけ（§5.7） |
| thread_id の接頭辞でタブを分ける | タブは `assistantId`（graph id）を切り替える。履歴は graph_id で絞り込まれるので、スレッドは混ざらない | agent-chat-ui の既存の仕組みで足りる。thread_id は UUID で接頭辞を付けられない |
| 変更してよい UI ファイル（現行 5 + 2） | 追加: `mode-tabs.tsx`、`messages/search-trace.tsx`。変更: `ai.tsx`（痕跡の描画）、`thread/index.tsx`（タブの配置、チャットタブで添付ボタンを隠す、プレースホルダ） | タブをシェルに置くには `index.tsx` の変更が避けられない |
| unload は `eject_only.api.json` も可 | LM Studio の REST `/api/v1/models/unload` だけを使う（導入済みの版にある。ComfyUI のノードと同じ API） | §9 の未決 3 を確認した |
| llama.cpp | fork のリリース版（Vulkan、`prism-b10754-2459f68`）を既定にした。`-FromSource` で `prism` ブランチを Vulkan ビルドもできる | §9 の未決 1。ROCm/HIP 版は使わない |
| — | llama-server は起動ごとにランダムな `--api-key` を付ける（`/health` 以外はキーが要る） | ループバックでも CORS が全許可のため、検索中に同じ端末のブラウザのページから叩かれないようにする |
| — | 取得物の SHA-256 を照合する。Tor は配布元の `sha256sums-signed-build.txt`、fork の zip とモデル 8 つは `config/search_models.json` に固定した値（GitHub / Hugging Face の公開値） | 公開リポジトリから第三者のバイナリと重みを取得するため |

## 4. 実測（2026-10-04、この端末）

### 4.1 probe（`scripts\probe-bonsai.ps1`、Vulkan、`-ngl 99`）

| モデル | 起動 | メモリ | 生成速度 | 合格したタスク |
| --- | --- | --- | --- | --- |
| Ternary-Bonsai-8B PQ2_0 | 2.3 s | 2,903 MB | 29.3 tok/s | plan, worker, critique, synthesize |
| Bonsai-8B Q1_0 | 2.9 s | 1,890 MB | 29.4 tok/s | worker |
| Bonsai-4B Q1_0 | 2.3 s | 1,397 MB | 45.9 tok/s | filter |
| Ternary-Bonsai-2-27B PTQ1_0 | 11.1 s | 6,567 MB | 8.0 tok/s | plan, worker, critique, synthesize |
| Ternary-Bonsai-2-27B abliterated PTQ1_0 | 7.0 s | 6,757 MB | 8.9 tok/s | plan, critique, synthesize |
| Qwen3.5-4B-heretic Q4_K_M | 5.0 s | 3,612 MB | 19.8 tok/s | route, plan, worker, critique, synthesize |
| Qwen3-1.7B-heretic Q4_K_M | 1.8 s | 1,909 MB | 59.3 tok/s | route, filter, worker |
| Qwen3-0.6B-heretic Q8_0 | 1.8 s | 1,403 MB | 105.8 tok/s | filter（route は不合格） |

PTQ1_0 / PQ2_0 / Q1_0 は、いずれも fork の Vulkan ビルドで 890M 上で動いた。CPU（`-ngl 0`）へのフォールバックは起きなかった。0.6B が route で不合格になったのは、追加情報の「分類の精度が足りない」と一致する。

### 4.2 メモリ

| 状態 | 空き物理メモリ |
| --- | --- |
| LM Studio のモデルなし、ComfyUI 待機 | 14.1〜14.4 GB |
| LM Studio に Qwen3.8 27B IQ3_M をロード中 | 0.1〜0.8 GB |

この端末では、27B と reader は同時に載らない。このため検索は常に proxy モードで動いた。

### 4.3 検索の通し（Tor、実モデル）

| 実行 | 経路 | 結果 |
| --- | --- | --- |
| プロセス内 1 回目 | 計画 27B → unload → reader ×1 → 代理 27B（通常版。abliterated は未取得）→ 追加検索 1 回 → 統合 | 523 s。reader が 150 秒で打ち切られた（§3 の focus_text の理由） |
| プロセス内 2 回目 | 計画 27B（150 s。うちロード約 115 s）→ reader ×2 並列 → 代理 abliterated → 追加検索 → 統合 | 492 s。出典 8 件。終了後に llama-server の PID 0、ロック解放 |
| UI（Edge、`http://<LAN IP>:3000`） | 同上。reader は 1 本 60 s 前後 | 372 s。痕跡（検索語、ヒット、開いたページ、役割別モデル）と出典付きの回答を表示 |

UI からの 1 回目は、`langgraph dev` の blocking-call 検出（同期の `os.mkdir` / `os.stat`）で filter が失敗した。同期 I/O をスレッドへ移し、blockbuster 付きのテストを足して直した。

### 4.4 画像タブのデグレ確認

| 経路 | 結果 |
| --- | --- |
| SDXL t2i（サーバ API、graph agent） | `t2i_basic`、309 s。eject 検証ログあり。outputs と ComfyUI output の両方に保存 |
| SDXL i2i（元画像 1 枚） | `i2i_basic`、denoise 0.45、380 s |
| Chroma1-HD t2i（`model_family=flux`、512×512、12 steps） | `t2i_basic`（flux）。英語の説明文。保存あり |
| pytest | 既存 284 件を含め、全件合格 |

### 4.5 モデルの採否

| モデル | 採否 | 役割 | 理由（この端末の実測） |
|---|---|---|---|
| Qwen3.8 27B abliterated（LM Studio、IQ3_M） | 計画だけ採用 | 検索意図に分ける | 指定どおり最初の処理だけ担当。ロード中は空きが 1GB を切り reader が載らないため、計画のあと unload する（ロード約 2 分） |
| Ternary-Bonsai-2-27B abliterated（Override-6、PTQ1_0） | 採用 | 批評・統合（代理リーダー） | 6.8GB で 27B 系の品質。計画・批評・統合の検証に合格（起動 7 s、8.9 tok/s）。拒否が少なく、ソースの批評で止まりにくい |
| Ternary-Bonsai-8B（PQ2_0） | 採用 | reader（最大 3 体） | ツール呼び出しと事実カードの検証に合格。2.9GB で、代理 27B を除いた空きに 3 体入る（29 tok/s） |
| Bonsai-4B（1-bit） | 採用 | フィルタ | 関係あり / なしの判定に合格。1.4GB、46 tok/s と最も軽い。抽出や回答には使わない |
| Qwen3-1.7B-heretic（Q4_K_M） | 採用 | ルータ（検索の要否、検索語の書き換え） | 要る / 要らないの両方を正しく判定。起動 1.8 s、59 tok/s。フィルタと reader の予備も兼ねる |
| Qwen3.5-4B-heretic（Q4_K_M） | 予備 | reader・ルータ・批評・統合の 2〜3 番手 | 全タスクに合格したが、3.6GB と重く 19.8 tok/s と遅い |
| Ternary-Bonsai-2-27B（通常版、PTQ1_0） | 予備 | 批評・統合の 2 番手 | 全タスクに合格（6.6GB、8.0 tok/s）。abliterated 版の方が拒否されにくいため 2 番手。abliterated 版の起動に失敗すると自動でこちらを使う |
| Bonsai-8B（1-bit） | 予備（原則不採用） | reader の 3 番手 | reader の検証に合格し、速度は Ternary 8B と同じで 1.9GB と軽い。「同じ帯域なら Ternary 8B が上」という方針で順位を下げた（品質差はこの端末では比べていない） |
| Qwen3-0.6B-heretic（Q8_0） | 不採用（フィルタの最後の予備だけ） | — | ルータの判定に不合格（分類の精度が足りない）。フィルタは合格したので、Bonsai-4B と 1.7B が使えないときだけ使う |

検証（`scripts\probe-bonsai.ps1`）は、タスクごとに固定の短いテスト 1 本で合否を見ただけです。長い作業での安定性や、回答の質の細かい差は測っていません。
順位は `config\search_models.json` を書き換えるか、probe をやり直すと変わります。reader だけは `.env` の `BONSAI_MODEL` で固定できます。

## 5. 完了条件（設計書 §7）

| 条件 | 確認 |
| --- | --- |
| 画像タブの添付あり生成が現行と同じテンプレート選択 | 既存テスト（`test_graph.py`、`test_planner.py`）と、実機の i2i |
| チャットタブの「こんにちは」が ComfyUI と llama-server を起動しない | `test_hello_does_not_start_llama_server_or_tor`。UI でも確認 |
| SOCKS を開くのがオーケストレータだけ | llama-server にプロキシを渡さない。ツールはプロセス内で実行する |
| 検索終了後に llama-server の PID が残らない | `test_search_proxy_mode_unloads_27b_before_readers_and_cleans_up`。実機 e2e でも `live pids: []`、doctor の孤児チェック |
| 27B が載っているとき Bonsai 2 27B にならない | `test_large_model_never_chosen_while_leader_resident`、resident モードのテスト |
| 検索直後の画像タブは、unload とワーカー消滅のあと | 共有 `job_lock`。synthesize の後始末（kill → unload → 解放）。`test_chat_tab_holding_the_job_lock_refuses_the_image_tab` |
| pytest が Tor・LM Studio・fork 無しで通る | 偽の LM Studio / llama-server / 検索で通る（fork の寿命テストは Python の偽サーバ） |

## 6. 残る制約

- 検索 1 回に 6〜9 分かかる。内訳の大きいものは、LM Studio の 27B のロード（約 2 分）、reader（1 本 1 分前後）、代理 27B の生成（8〜9 tok/s）。`SEARCH_PLANNER=local` にすると、計画も代理 27B が行い、LM Studio のロードを省ける。
- DuckDuckGo は Tor の出口によって空の結果を返すことがある。Lite が空なら HTML 版を試すだけで、指紋偽装などの回避はしない。
- 他ホストの実機ブラウザからの確認はしていない。この端末から LAN アドレス（`http://<LAN IP>:3000`）で、headless の Edge を使って確認した。
