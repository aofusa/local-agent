# トラブルシューティングと制約

## 症状と対処

| 症状 | 対処 |
|---|---|
| `doctor.ps1` で custom nodes が NG | `setup-comfyui.ps1` の後に ComfyUI を再起動したか確認 |
| チャットタブ「Tor が 127.0.0.1:9050 で待ち受けていません」 | `.\scripts\setup-tor.ps1` の後に `.\scripts\start-tor.ps1`。ログは `logs\tor.log` |
| 「PrismML 版 llama.cpp がありません」/「検索用モデルがありません」 | `.\scripts\setup-llamacpp.ps1` / `.\scripts\setup-search-models.ps1` を実行して LangGraph を再起動 |
| 「〜に使えるモデルがありません（メモリ不足…）」 | 他のアプリを閉じる。`BONSAI_RESERVE_MB` を下げる。`SEARCH_FANOUT_WIDTH` を 1〜2 にする |
| 「検索結果がありません。Tor 出口が拒否された…」 | しばらく置いて送り直す（出口が変わる）。`logs\furry_agent.log` の `search provider=` を確認 |
| 「チャットタブ（画像タブ）が実行中です」 | もう片方のタブの処理が終わってから送り直す |
| チャットタブのコードで「docker コマンドがありません」 | Docker（Docker Desktop か Docker Engine）を入れて `.\scripts\setup-sandbox.ps1`（イメージ取得と動作確認）。docker が無いあいだは何も起動しない |
| チャットタブのコードで「Docker のエンジンに接続できません」 | Docker Desktop が入っていれば承認後に自動で起動・停止する。Docker Engine を使っているならそのエンジンを起動する |
| 「コンテナイメージ python:3.12-slim がありません」 | `.\scripts\setup-sandbox.ps1`（Rust は `-Rust`）。実行時はイメージを取得しない（`--pull never`） |
| 思考モードなのに「思考」の折りたたみが出ない | context（4096）に答えの分と思考の余地が残らないと、思考なしで答える（`logs\furry_agent.log` の `thinking=False`）。代理リーダーが統合した検索の回答にも思考は無い |
| 思考モードの文章・コードがとても遅い | この端末の 27B は約 0.9 トークン/秒。長い文章は章立てにするか「速い」で送る |
| 「LLM サーバ（llama.cpp）に接続できないか、時間切れです」 | ルータが起動していない。`.\scripts\start-llm.ps1`（`start-all.ps1` も起動する）。`doctor.ps1` の「LLM ルータ」を確認 |
| 「… model name=qwen3.8-27b-abliterated failed to load」 | 27B を読めなかった（多くはメモリ不足）。ComfyUI を再起動して ROCm の常駐メモリを解放するか、`setup-llm.ps1 -GpuOffload 0.4` で GPU に置く層を減らしてルータを再起動。ルータのウィンドウのログに理由が出る |
| `doctor.ps1` で「no orphan llama-server」が WARN | 検索中でなければ `Stop-Process -Name llama-server` |
| ブラウザに Deployment URL の入力画面が出る / 接続できない | `start-ui.ps1` を再実行（LAN IP が変わると再ビルド）。`open-firewall.ps1` を管理者で実行。ネットワークがプライベートか確認 |
| 「生成できませんでした: ... failed to load」 | メモリ不足。他のアプリを閉じる、`setup-llm.ps1 -GpuOffload 0.4` に下げてルータを再起動、ComfyUI を再起動して常駐メモリを解放 |
| 「… 秒間進捗がありません」「… 秒間応答がありませんでした」 | ComfyUI かルータが止まっている（メモリ不足が多い）。上と同じ対処。本当に長く無音になる処理なら `AGENT_IDLE_TIMEOUT_S` を増やす。画像は完了していれば `再取得 <prompt_id>` で受け取れる |
| 「この環境の ComfyUI に無いノードがあります」 | `setup-comfyui-refs.ps1` を実行して ComfyUI を再起動。`doctor.ps1` の reference nodes を確認 |
| 「この環境の ComfyUI に無いノードがあります: FurryJaDiffusionLoaderAfterEject」 | Chroma 対応後に ComfyUI を再起動していない。`start-comfyui.ps1` で起動し直す |
| 「Chroma1-HD のモデルファイルが ComfyUI に見つかりません」 | `setup-comfyui-chroma.ps1` を実行。拡散モデルは手動で `models\diffusion_models` か `models\checkpoints` に置く |
| モデルの一覧で「使えません: ComfyUI にファイルがありません」 | そのモデルのファイル（`config/host_models.json` の `ckpt`、テキストエンコーダ、VAE、LoRA）を `tools\comfyui\models` に置くか、`setup-image-models.ps1` / `.sh` を実行して ComfyUI を再起動する |
| 「［モデル選択］… はホストのモデル一覧にありません」 | 古い id（v0.11 以前の名前）や表示名を送っている。画面で選び直すか、cirka は `/model` で候補を見て id を指定する |
| 推論モデルが「LLM ルータのプリセットにありません」 | GGUF が無いか、プリセットが古い。`setup-search-models`（Bonsai）や `setup-llm` を再実行してルータを再起動する |
| Chroma / Anima / Krea 2 で「ポーズ ControlNet 未対応」などと返る | これらの系統は元画像 1 枚の img2img だけ対応。ポーズ・画風・キャラクターの参照は SDXL のモデルを選ぶ |
| 「LoRA が ComfyUI に見つかりません」 | 画像モデルの `loras` の名前を `tools\comfyui\models\loras` のファイル名に合わせる（拡張子と大文字小文字は無視する） |
| （macOS）`setup-*.sh` で「hf download に失敗しました」 | `hf` を入れる（`brew install huggingface-cli`）。ゲート付きのモデルは `hf auth login`。空きディスクも確認する（`df -h`） |
| （macOS）他ホストのブラウザから開けない | システム設定 → ネットワーク → ファイアウォールで python（LangGraph）と node（UI）の着信を許可する。`scripts/start-ui.sh --host <この Mac の LAN IP>` |
| `setup-comfyui.ps1` で「… がありません」（チェックポイント・LoRA） | `tools\comfyui\models\<checkpoints / loras>` に置く。以前の ComfyUI のフォルダ（Comfy Desktop、`Documents\ComfyUI\models`）以外にあるなら `-ModelsDir <そのフォルダ>` で取り込む |
| 「hf download で取得できませんでした」 | ネットワークか Hugging Face の認証。ゲート付きのモデルは `hf auth login` か環境変数 `HF_TOKEN`。取得できないときは直接ダウンロードに切り替わる |
| 参照画像を使った 2 回目以降の画像が単色やノイズになる | ComfyUI が `--cache-none` なしで起動している。`doctor.ps1` で確認し、`start-comfyui.ps1` で起動し直す |
| キャラクター参照で色が焼ける・ギラつく | キャラクターの強度を下げる（0.6 前後） |
| 画像の役割が思ったものにならない | 添付時に役割を選ぶ。または `1枚目のキャラ` `2枚目のポーズ` のように序数で書く |
| タグの前に思考文が出る / 遅い | ルータのプリセットが `reasoning = off` か確認（`setup-llm.ps1` を再実行してルータを再起動） |
| 参照画像を送っても説明が空 | プリセットの `mmproj` のファイルがあるか確認（`doctor.ps1` の preset と model の行） |
| ルータのログに `ErrorOutOfDeviceMemory` | プリセットに `load-mode = mmap` があるか確認（`setup-llm.ps1` が書く）。ComfyUI の ROCm が GPU メモリを持ったままなら ComfyUI を再起動 |
| `langgraph dev` が `UnicodeDecodeError: 'cp932'` で落ちる | `start-langgraph.ps1` から起動する（`PYTHONUTF8=1` を設定します） |
| cirka「ホストに届きません」 | `cirka status` で確認。`cirka config set host http://<LAN IP>:2024`（`localhost` は cirka を動かす PC 自身のこと）。ホスト側で `open-firewall.ps1` を実行し、LangGraph が起動しているか確認 |
| cirka「ホストに /coder/turn がありません」 | ホストの local-agent が v0.7.0 より古い。更新して LangGraph を再起動する |
| cirka が「… の処理を待っています」のまま進まない | 画像タブやチャットタブが共有ロックを使っている。終われば自動で進む（最大 600 秒） |
| cirka「文脈が溢れました」 | `/compact` で会話を要約するか、依頼を小さく区切る（ホストの 27B は context 4096） |
| cirka のロゴや枠が崩れる・色が出ない | Windows Terminal など UTF-8 と 24 ビット色の使える端末で開く。色を消すなら `NO_COLOR=1`。端末が狭いと文字だけのロゴになる |
| cirka の auto で確認メニューが出る | git push や再帰的な削除など、取り返しのつかない操作だけは auto でも確認する。毎回聞かれたくなければ「はい、以後このセッションでは確認しない」を選ぶ |

## 調整の記録（参照画像）

確認済み構成（yiffInHell VANTABLACK、Radeon 890M / ROCm、ComfyUI 0.38）で、LLM を通さず固定タグで比較した結果です。スクリプトの既定値の根拠です。

| 事象 | 原因と対処 |
|---|---|
| IP-Adapter Plus を weight 0.85 で使うと色が焼け、青く飽和する | Illustrious 系のこのモデルには強すぎる。0.4 で同一性を保ったまま破綻しなくなった → キャラクターの weight = 強度 × 0.5 |
| 同じ ComfyUI プロセスで 2 回目以降の IP-Adapter 生成が単色・ノイズ（12KB 程度の PNG）になる | メモリ量や weight を変えても再現し、`--cache-none` で解消 → 起動引数に追加 |
| DWPose の TorchScript（YOLOX / ポーズ推定）が数回目で `invalid shape dimension` 等で失敗する | ROCm の GPU 上で不安定 → 人物検出なし（画像全体を 1 人とみなす）+ ONNX のポーズ推定を CPU（OpenCV）で実行 |
| SDXL + ControlNet + IP-Adapter + エンコーダが GPU 予算（空き約 6.8GB）を超え、UNet が全てオフロードされる | KSampler 直前でエンコーダを外すノード（`release`）を追加 |
| 画風参照（style transfer）0.55〜0.8 | 主題を写さず配色とトーンが移る。0.8 でも破綻なし |

## 既知の対象外・制約

- **動画入力は対象外**: ComfyUI-VideoHelperSuite（VHS）を前提にした経路は未実装です。動画を送るとチャットにその旨を返します（在庫の agent-chat-ui も動画の添付を受け付けません）。
- InstantID / PuLID（人の顔向けの同一性）は使いません。キャラクター参照は IP-Adapter Plus と Vision タグで行います。
- 登録済みの系統は `sdxl`、`flux`（Chroma1-HD）、`krea2`（Krea 2）、`anima`（Anima）です（Flux Dev 本家、SD3 などは未登録）。
- 画像のタグ生成の LLM は、選んだ推論モデルではなくルータの `LLM_MODEL` です（ワークフローの `llm_backend`。タグ生成まで選択に合わせるのは対象外）。
- macOS: Krea 2（Wulver）の fp8 の重みは PyTorch の MPS では計算できない型のため、Mac では確認していません（Windows で確認）。MLX はテキスト専用（mlx-lm）なので、MLX で動かす推論モデルは参照画像の Vision（mmproj）に使えません。参照画像を使うときは GGUF + mmproj のモデルをタグ生成に使います。
- Chroma1-HD 経路は、ポーズ・画風・キャラクター参照とマスクに未対応です（Flux 用 ControlNet Union Pro / Redux / IP-Adapter の Chroma での動作を確認していないため。作業指示書 §2.4）。GGUF 量子化の読み込みにも未対応です。
- 役割推定は LLM ではなくルール（日本語のキーワードと序数）です。画像タブの LangGraph は LLM を呼ばない（AGENTS.md）ためです。
- 認証なし。LAN 内の開発用途のみ。チャットタブの検索も LAN から誰でも使えます（画像タブと同じリスク）。
- チャットタブの検索は DuckDuckGo（Lite、空なら HTML 版）だけです。Tor の出口によっては空の結果になります。CAPTCHA の突破や指紋偽装、`.onion` の巡回はしません。
- コードの実行は Docker の Linux エンジン（Docker Desktop か Docker Engine）だけです。`docker` コマンドが無いときは実行も起動もしません。Windows コンテナ、WSL 直接、ホストでの実行はしません。コンテナ内からネットワークは使えず（依存の取得だけ例外）、1 回 60 秒・2GB までです。GUI、サーバの常駐、標準入力を使うプログラムは動きません。
- 思考トークンはルータの 27B だけです。代理リーダー（Ternary-Bonsai-2-27B）は `--reasoning off` のまま動かすので、代理で統合した回答には思考の折りたたみが出ません。
- 検索モデルは PrismML の llama.cpp fork の Vulkan 版だけで動かします（Q1_0 / PQ2_0 / PTQ1_0 は素の llama.cpp では動かないため。27B のルータも同じ build）。ROCm 版は使いません。
- チャットタブの自律モードは時間の上限を既定で持たないため、この端末では検索から執筆まで進む依頼に 1 時間近くかかることがあります。時間で区切りたいときは `CONTROLLER_WALL_CLOCK_S` を設定します。
- cirka: ホストの 27B は context 4096・1 ターン数分です。ツールの結果は窓に合わせて切り詰めます。`bash` は OS のサンドボックスなしで cirka を動かした PC の上で動き、既定の auto では確認なしで実行します（危険な操作の一覧だけ確認）。信頼できないリポジトリでは `/default` か `/plan` で使ってください。実行中の中断は Ctrl-C（Esc ではありません）。ロゴの絵は JPG から作っており、SVG はまだ使っていません。
