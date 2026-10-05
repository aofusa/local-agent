# Changelog

## v0.6.0 — 主張単位の検証とローカル文書の `/docs`

- 主張の突き合わせ（`docs/claim-verification-design.md`）: 検索の回答を、批評（速いモードは読解）のあと「主張の抽出 → 主張の判定 → 支持された主張だけで統合 → 文ごとの監査 → 支持されない文の削除」の順で作る。抽出・判定・統合・監査は、批評と同じ代理リーダー（Ternary-Bonsai-2-27B abliterated）の 1 プロセスで行う。採否はオーケストレータの門が決める（引用カードの実在、抜粋との 20 字一致または数値・日付・固有名詞、カードに無い数値は不可、引用未確認のカードだけなら「一部」まで）。削除した文は言い換えない。
- JSON が壊れたら同じプロセスに 1 回だけ直させる。2 回目の失敗や時間切れでは、無監査の回答を出さず抜粋だけを返す（`CLAIM_VERIFY_FAIL_OPEN=1` で、印を付けた無監査の統合）。`CLAIM_VERIFY=0` で v0.5.0 の統合に戻る。
- `/docs <パス> [質問]`（`docs/local-doc-mapreduce-design.md`）: `LOCAL_DOC_ROOTS`（空ならオフ）の中のファイルかフォルダを、検索と同じチームで読む。オーケストレータが実パス判定・拒否名（`.env`、鍵、`.git`、`node_modules`、出力画像、モデルなど）・拡張子・見出しでの分割を行い、計画（節が 12 を超えるときだけ LM Studio の 27B、読む前に unload）→ Ternary-Bonsai-8B × 最大 3 の波（4 波・12 節まで）→ 思考モードは代理リーダーのカバー判定 → 主張の突き合わせ → `パス#見出し` の出典付きの回答。モデルはパスを決めず、ツールも持たない。Tor もネットワークも使わない。
- agent-chat-ui: 回答の下に「主張の突き合わせ」（主張ごとの判定、出典番号、削除した文）と「ローカル文書」（対象ファイル、拒否したもの、波ごとに読んだ節）の折りたたみを追加した。
- 内部: llama-server の起動と停止を `chat_models.py` に分け、検索・主張の検証・`/docs` で共有する。
- この端末に合わせた調整: 主張の検証の時間上限を 600 秒（実測 150〜360 秒）、`/docs` の読む時間を 600 秒にした。reader が GPU メモリ不足で起動できないときは、その節を戻して幅を下げる。
- 確認: pytest（525 件）、この端末での実行（検索 + 主張の検証、`/docs` の速い / 思考、SDXL のテキストのみ、SDXL の参照画像 1 枚（ComfyUI の生成と eject の確認まで。試験用クライアントが止められたため、UI への返却はテキストのみの実行で確認）、Chroma1-HD のテキストのみ）、LAN アドレス経由での UI 表示（主張の表とローカル文書の折りたたみ）。画像タブのコード（`graph.py`、`workflows/`、`comfyui_nodes/`）は `main` から差分なし。
- 画像タブ（graph `agent`）、ComfyUI のワークフローとノード ID、eject の順は変えていない。
- 実装記録: 二つの設計書の末尾。

## v0.5.0 — チャットタブの深い検索、文章、コード実行、速い / 思考 / 自動

- チャットタブの入力欄に「自動 / 速い / 思考」を追加した（`configurable.mode`）。モデルは替えず、予算と思考トークンを替える。「自動」は Grok の自動モードのように、送った内容（比較・分析を含む調査、章立ての要る文章、実行を頼んだコード、推論の要る相談など）とルータの判定から速い / 思考を選び、選んだ理由を回答の下に出す。モード無指定の API 実行は「速い」。
- 深い検索（思考モード）: 27B が目的と下位問い（2〜5）を立て、批評役がラウンドごとに下位問いを「回答済み / 一部 / 未回答」で採点し、未回答のものだけを次のラウンドで検索する。未回答が残り、新しいカードが増え、予算（4 ラウンド、12 ページ、20 分）が残るあいだ続ける。回答に未解決の下位問い、食い違い（両方の出典番号）、停止理由を付ける。同じ URL は読み直さない。reader の同時数は増やさない。速いモードは検索意図 1 本、1 ラウンド、批評なし。
- 検索痕跡に、ラウンド、下位問いと状態、採用 / 不採用のカードと理由、停止理由、読んだページ数、経過秒を追加した。
- 文章（`/write`、「小説」「推敲」「続きを書いて」など）: 速いは 27B が一度で書く。思考はブリーフとアウトライン → 本文 → 置換指示による推敲（全文を書き直さない）。長編・章立ては章ごとに続けるかを確認する（HITL）。本文はスレッドの artifact に残り、「続き」はその末尾から書く。事実が要る創作は、思考モードなら先に検索する。
- コード（`/code`、「コードを書いて」「実行して」など）: 27B がファイルとコマンドを書き、`artifacts/code/<run_id>/` に置く。速いは実行しない。思考は確認カードで承認したあとにだけ、Docker の `python:3.12-slim`（Rust は `rust:1.88-slim`）で実行する。ネットワークなし（依存の取得だけ明示と承認のうえで別コンテナ）、読み取り専用、`/work` だけマウント、2GB / 2 CPU / 256 プロセス、権限なし、非 root、60 秒、argv のみ。失敗したら 1 回だけ直して承認し直す（合計 2 回）。Docker が無いときはファイルを書いて理由を返す。
- 思考モードでは LM Studio の 27B の思考トークン（`reasoning_effort`）を有効にし、回答とは別の折りたたみ（既定は閉じる）に出す。
- タイムアウト: チャットタブのモデル呼び出し 1 回を 180 秒から 20 分（`CHAT_TIMEOUT_S=1200`）に、思考モードの検索全体を 20 分（`SEARCH_WALL_CLOCK_S=1200`）にした。長い呼び出しのあいだも共有ロックを延長する。
- `scripts/setup-sandbox.ps1`（Docker Desktop の起動とイメージの取得、動作確認）を追加し、`doctor.ps1` に Docker の確認を足した。
- この端末（ROG Ally X）に合わせた調整: LM Studio の 27B は context 4096・約 0.9 トークン/秒のため、1 回の呼び出しの量（回答 + 思考）を context と 20 分で出せる量に収める（`LMSTUDIO_CONTEXT`、`LMSTUDIO_TOKENS_PER_S`、速さは応答ごとに測り直す）。思考が予算を使い切ったら思考なしで答え直す。Docker Desktop は承認した実行のときだけ起動して止める。LM Studio の自動 unload と要求が重なった "Model is unloaded." は 1 回だけ送り直す。
- 検索モデルの probe をルータ（`kind`）と批評（下位問いの採点）の新しい形式に合わせ、やり直した。8 モデルすべて合格し、Qwen3-0.6B-heretic がルータの 3 番手に入った。
- 確認: pytest、この端末での実行（会話・検索・文章・コード、SDXL と Chroma1-HD の画像生成の退行確認）、他ホストのブラウザからの操作。
- 画像タブ（graph `agent`）、ComfyUI のワークフローとノード ID、eject の順は変えていない。
- 実装記録: `docs/chat-deep-search-creative-sandbox.md` の末尾。

## v0.4.0 — 画像 / チャットのタブと Tor 経由検索

- agent-chat-ui の上部に「画像」「チャット」のタブを追加した（Grok の画面と同じ分け方）。画像タブは従来のグラフ（graph `agent`、別名 `image`）で、動作は変えていない。チャットタブは新しいグラフ `chat`。履歴はタブごとに分かれる。
- チャットタブ: LM Studio の Qwen3.8 27B と会話できる。`/search`、「調べて」「最新」「ニュース」、URL を含む文では Tor 経由で Web を検索し、出典付きで答える。検索語の無い質問文は Qwen3-1.7B が検索の要否を判断する。
- 検索は Grok のマルチエージェント検索の縮小版。27B が検索意図を 1〜3 本に分ける → Python が Tor 経由で並列に検索 → Bonsai-4B が関係の無い結果を落とす → Ternary-Bonsai-8B の reader（最大 3 体、並列）がページを選んで開き、事実カードを作る → 批評が不足を見つければ 1 回だけ追加検索 → 統合が [n] 付きで回答する。
- 27B と reader が同時に載らない端末（ROG Ally X 実測: 27B ロード中の空き 1GB 未満）では、計画のあとに 27B を unload する。批評と統合は Override-6 の Ternary-Bonsai-2-27B abliterated（PTQ1_0）が PrismML の llama.cpp fork で代理を務める。
- 検索モデル 8 つ（Bonsai 8B / 4B、Ternary Bonsai 8B、Ternary Bonsai 2 27B と abliterated 版、Qwen3.5-4B / Qwen3-1.7B / Qwen3-0.6B heretic）を、`config/search_models.json` の順と実機の検証結果（`scripts/probe-bonsai.ps1` → `tools/bonsai/rank.json`）、空きメモリからタスクごとに自動で選ぶ。llama-server は検索のあいだだけ起動し、PID を kill して後始末する。
- モデルの JSON 出力は Pydantic のスキーマで文法制約をかけて検証し、不正なら同じ呼び出しを 1 回だけ再試行する。カードの引用は、実際に取得した本文と照合する。
- Tor（socks5h のみ、127.0.0.1:9050）の導入と起動 `scripts/setup-tor.ps1` / `start-tor.ps1`、PrismML fork の導入 `scripts/setup-llamacpp.ps1`（Vulkan リリース、`-FromSource` でビルド）、モデルの取得 `scripts/setup-search-models.ps1` を追加した。`setup.ps1`、`start-all.ps1`、`doctor.ps1` も対応している。
- 画像タブとチャットタブは共有ロックで直列化する。検索の後始末（llama-server の停止と LM Studio の unload）が済むまで、画像タブは生成を始めない。
- llama-server は `127.0.0.1` だけで待ち受け、起動ごとにランダムな API キーを付ける。取得物（Tor、fork の zip、モデル 8 つ）は SHA-256 を照合する。`setup-search-models.ps1 -Verify` で、取得済みのファイルも再照合できる。
- モデルごとの役割と採否の理由を README（「検索で使うモデルと採否」）にまとめた。
- 実装記録と実測: `docs/chat-search-tor-bonsai-work-instruction.md`。

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
