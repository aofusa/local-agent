# 採用モデルと選定の記録

このプロジェクトが使うモデル（LLM、検索用の小さなモデル、画像モデル、参照画像用のモデル）について、誰がなぜ選んだか、どう評価したか、採らなかったものとその理由をまとめます。今の一覧と設定値は `config/host_models.json`（選べるモデル）、`config/llm_model.json`（27B）、`config/search_models.json`（検索モデル）が正で、各値の出典は [configuration.md › モデルの一覧とパラメータ](configuration.md#モデルの一覧とパラメータconfighost_modelsjsonv0120) にあります。

この記録は、過去の README・設計書・実装記録、コミットログ、開発セッションでの依頼と検証結果をもとにしています（末尾の「出典」）。

## 選定の前提

- **すべてこの端末の上で動かす。** クラウド API は使わない（[AGENTS.md](../AGENTS.md)）。
- **想定機は ROG Ally X 級（Radeon 890M、共有メモリ 24GB）。** OS から見える RAM は約 23GB で、GPU も同じメモリを使う。27B の LLM と画像モデルは同時に載らないため、ワークフローは「LLM がタグを返す → unload → 画像モデルを読み込む」の順に固定している。モデルの選び方と量子化は、ほぼこの制約で決まっている。
- **モデルは利用者が指定したものを基本にする。** 利用者は 27B（Huihui Qwen3.8 27B Abliterated）と最初の画像モデル（yiffInHell VANTABLACK）を指定し、検索モデルは候補 8 つを示して「タスクに応じて自動で選ぶ」ことを求めた。v0.12.0 では選べる推論モデル 2 つと画像モデル 8 つを指定した。開発側が決めたのは、量子化、パラメータ、候補の中の役割と順位である。

## 推論の LLM

| モデル | 位置づけ | 決め方と評価 |
|---|---|---|
| Huihui Qwen3.8 27B Abliterated（IQ3_M、mmproj 付き） | 既定の推論モデル。画像のタグ生成、チャットの会話・文章・コード、検索の計画、cirka | 利用者の指定（v0.1.0）。Danbooru タグの JSON を安定して返し、拒否で止まらない abliterated 版を使う |
| Ternary-Bonsai-2-27B abliterated（PTQ1_0、Override-6） | 選べる推論モデル（v0.12.0）。検索の代理リーダーと同じファイル | 利用者の指定。思考を表示しないモデルとして扱う。context 8192 で、Qwen（4096）より長い会話が入る |

### 別ホストの推論モデル（v0.13.0）

`config/host_models.json` の `endpoint` で、ほかの PC の llama.cpp や OpenAI 互換サービスのモデルも推論モデルとして選べる（[remote-llm-design.md](remote-llm-design.md)）。同梱の 2 項目は、確認機（Mac）のルータで動く Ternary-Bonsai-2-27B abliterated（ローカルの項目と同じ GGUF とパラメータ、context 8192、思考なし）を、`llamacpp` と `openai` の 2 つの話し方で呼ぶもので、新しいモデルの採用ではない。評価は接続（一覧、補完、tool calling、検索の計画と統合、主張の検証）だけで、モデルの質はローカルの Bonsai と同じとみなした。

### Qwen3.8 27B の量子化と設定

- **IQ3_M に再量子化する（約 12.7GB）。** 配布されている Q4_K_S（15.6GB + mmproj）は、OS・画面・ComfyUI の常駐分と合わせると物理メモリに収まらず、ページングでほぼ止まった。設計書の想定（Q4_K_M / IQ4_XS）も同じ理由で使えない。セットアップが同じモデルを IQ3_M にして使う（元ファイルは残す）。
- **GPU に置く層は 0.45（29 / 64 層）。** この iGPU では Vulkan が確保できる量に上限があり、ComfyUI が一度生成すると ROCm ランタイムが約 2GB を持ち続けるため。
- **読み込みは mmap。** 既定の読み込みでは CPU 側の重みが pinned メモリに置かれ、共有メモリの iGPU では `ErrorOutOfDeviceMemory` になった。
- **context 4096、思考は既定でオフ、temperature 0.4。** タグの整形に長い文脈は要らず、思考トークンを足すと時間だけかかる。思考はチャットタブの思考モードだけで使う。
- **repeat-penalty 1.1。** LM Studio の既定と同じ値。これが無いと、ルータで動かした最初の SDXL の実行で negative のタグを `max_tokens` まで並べ続け、JSON が閉じずに生文字列へ落ちた。
- **公式の推奨値（思考なし: temperature 0.7、top-p 0.8、presence-penalty 1.5）は採らなかった。** タグの JSON を安定させる上の値を優先した（v0.12.0 で再確認）。
- **生成速度（この端末）:** LM Studio では約 0.9〜1.5 トークン/秒、v0.11.0 の llama.cpp ルータでは約 2.0 トークン/秒。チャットタブの各呼び出しの量は、この速さではなく context で決める。

### LM Studio から llama.cpp へ（v0.11.0）

モデルを動かすサーバは LM Studio から llama.cpp（PrismML fork の llama-server、ルータモード）に替えた。検索モデルと同じ build にまとめ、モデルの load / unload をワークフローとチャットタブから確実に扱うためである。利用者の確認依頼に対して、変更前の LM Studio のファイルと変更後のファイルが同じことを確かめた（12,768,332,704 バイト、SHA-256 `2554601c…3595a4` で一致。mmproj も同じファイル）。モデルも読み込み方の設定も変わっていない。

### Bonsai 2 27B を推論モデルとして選べるようにしたこと（v0.12.0）

- サンプリングは配布元の思考なしの推奨（temperature 0.7、top-p 0.8、top-k 20）に、llama.cpp の既定の min-p 0.05、Qwen 系の思考なしの presence-penalty 1.5 を合わせた。1-bit / ternary の重みで 6.9GB なので、すべての層を GPU に置く。
- ルータのプリセットにも節として入れる（検索の代理リーダーと同じファイルを参照し、二重に置かない）。
- macOS の確認機では空きディスクが少なく Qwen が入らないため、Bonsai が推論・タグ生成の両方を担った。Bonsai はタグの JSON を正しく返し（`split mode=json`）、検索の計画・統合と cirka の作業も完了した。

### macOS と MLX（v0.12.0）

- 利用者の「可能なら MLX を優先」に従い、MLX 版があるモデルは MLX で動かす仕組み（`furry_agent.mlx_router`）を入れた。
- Qwen3.8 27B abliterated の MLX 4bit 版（14.1GB）は一覧に載せたが、確認機の空きディスク（約 20GB）には GGUF 版（12.7GB）とも入らなかった。
- Bonsai 2 27B abliterated の MLX 版は無い。PrismML の MLX 版は abliterated ではなく、独自のランタイムが要る。そのため Mac でも Bonsai は llama.cpp（Metal）で動かした。
- MLX の経路は小さな MLX モデル（Qwen3-0.6B 4bit）で確かめた。

## 検索モデル（チャットタブ）

利用者が示した 8 つの候補を `scripts/probe-bonsai.ps1` でタスクごとに検証し、合否・メモリ・速度から役割と順位を決めた（v0.4.0、v0.5.0 のルータ形式で再検証）。27B と reader は同時に載らないため、計画だけ 27B が行い、そのあと unload して小さなモデルと代理リーダーに渡す（利用者の指定）。

| モデル | 採否 | 役割 | 理由（この端末の実測） |
|---|---|---|---|
| Qwen3.8 27B abliterated | 計画だけ採用 | 検索意図に分ける | 指定どおり最初の処理だけ担当。ロード中は空きメモリが 0.1〜0.8GB まで減り reader が載らないため、計画のあと unload する（ロード約 2 分） |
| Ternary-Bonsai-2-27B abliterated（PTQ1_0） | 採用 | 批評・統合（代理リーダー） | 6.8GB で 27B 系の品質。計画・批評・統合の検証に合格（起動 7 秒、8.9 tok/s）。拒否が少なく、出典の批評で止まりにくい。27B の代理として利用者が指定 |
| Ternary-Bonsai-8B（PQ2_0） | 採用 | reader（最大 3 体） | ツール呼び出しと事実カードの検証に合格。2.9GB で、代理 27B を除いた空きに 3 体入る（29 tok/s） |
| Bonsai-4B（1-bit） | 採用 | フィルタ | 関係あり / なしの判定に合格。1.4GB、46 tok/s と最も軽い。抽出や回答には使わない |
| Qwen3-1.7B-heretic（Q4_K_M） | 採用 | ルータ（検索の要否、検索語の書き換え） | 要る / 要らないの両方を正しく判定。起動 1.8 秒、59 tok/s。フィルタと reader の予備も兼ねる |
| Qwen3.5-4B-heretic（Q4_K_M） | 予備 | reader・ルータ・批評・統合の 2〜3 番手 | 全タスクに合格したが、3.6GB と重く 19.8 tok/s と遅い |
| Ternary-Bonsai-2-27B（通常版） | 予備 | 批評・統合の 2 番手 | 全タスクに合格（6.6GB、8.0 tok/s）。abliterated 版の方が拒否されにくいため 2 番手。abliterated 版の起動に失敗すると自動でこちらを使う |
| Bonsai-8B（1-bit） | 予備（原則不採用） | reader の 3 番手 | reader の検証に合格し、速度は Ternary 8B と同じで 1.9GB と軽い。「同じ帯域なら Ternary 8B が上」という方針で順位を下げた（品質差はこの端末では比べていない） |
| Qwen3-0.6B-heretic（Q8_0） | 予備（最後） | フィルタ・ルータの最後 | 旧形式のルータの判定に不合格（分類の精度が足りない）。v0.5.0 のルータ形式には合格したのでルータの 3 番手。フィルタも合格 |

検証の実測（`scripts\probe-bonsai.ps1`、Vulkan、すべての層を GPU）:

| モデル | 起動 | メモリ | 生成速度 | 合格したタスク |
|---|---|---|---|---|
| Ternary-Bonsai-8B PQ2_0 | 2.3 s | 2,903 MB | 29.3 tok/s | plan, worker, critique, synthesize |
| Bonsai-8B Q1_0 | 2.9 s | 1,890 MB | 29.4 tok/s | worker |
| Bonsai-4B Q1_0 | 2.3 s | 1,397 MB | 45.9 tok/s | filter |
| Ternary-Bonsai-2-27B PTQ1_0 | 11.1 s | 6,567 MB | 8.0 tok/s | plan, worker, critique, synthesize |
| Ternary-Bonsai-2-27B abliterated PTQ1_0 | 7.0 s | 6,757 MB | 8.9 tok/s | plan, critique, synthesize |
| Qwen3.5-4B-heretic Q4_K_M | 5.0 s | 3,612 MB | 19.8 tok/s | route, plan, worker, critique, synthesize |
| Qwen3-1.7B-heretic Q4_K_M | 1.8 s | 1,909 MB | 59.3 tok/s | route, filter, worker |
| Qwen3-0.6B-heretic Q8_0 | 1.8 s | 1,403 MB | 105.8 tok/s | filter（旧形式の route は不合格） |

- Q1_0 / PQ2_0 / PTQ1_0 は素の llama.cpp では動かないため、PrismML の fork（Vulkan 版）を使う。27B のルータも同じ build にまとめた。
- 再検証のとき一度メモリ不足で止まった。原因は Windows ではなく、開発環境の常駐シェルのメモリ監視と、`docker desktop stop` のあとも WSL の VM が約 0.8GB を持っていたことだった。別のシェルで実行すると、7GB の Ternary-Bonsai-2-27B の読み込み中でも空きは最小 4.7GB あった。
- macOS の確認機では、ディスクの都合で Qwen3-1.7B-heretic（ルータ・フィルタ・reader）と Bonsai 2 27B abliterated（計画・統合）だけを入れた。順位の予備が働き、検索から主張の突き合わせまで完了した。

## 画像モデル

| モデル | 系統 | 採用の経緯 | 設定と評価 |
|---|---|---|---|
| yiffInHell VANTABLACK | sdxl | 利用者の指定（v0.1.0）。既定のモデル | 設計書の初期値（steps 28、cfg 5.5、euler ancestral / normal、832×1216、img2img の denoise 0.45）を使い、Illustrious / Pony 系として Danbooru タグを渡す。この値でタグ中心の positive と元画像の構図を残す img2img を確かめた |
| Chroma1-HD | flux | 利用者の指定（v0.3.0）。英語の説明文で描く系統として | 公式の Chroma1-HD ワークフローの値（1024×1024、steps 28、cfg 3.5、euler / beta、T5-XXL fp8、Flux VAE）。BF16（17.8GB）を読み込み時に fp8 へ落とすと 24GB 機では ComfyUI が落ちたため、一度だけ fp8（8.3GB）に変換したファイルを読む。速度は 1024×1024 で 1 ステップ約 64 秒（28 ステップで約 30 分）、768×768 で約 26 秒、512×512 で約 12 秒 |
| yiffInHell METALLIC TETRA | sdxl | 利用者の指定（v0.12.0） | 配布ページの v4.0 の推奨（24 steps、CFG 2〜4、Euler A、Beta / SGM Uniform）から 24 / 3.5 / euler_ancestral / sgm_uniform。UI から選んで生成を確認 |
| yiffInHell XXX-TENDED V2.0 | sdxl | 利用者の指定（v0.12.0） | XXX-TENDED の推奨（24 steps、CFG 2〜4、Euler A）から 24 / 3.0 / euler_ancestral / sgm_uniform |
| Rekemono v1.0 | sdxl | 利用者の指定（v0.12.0） | 配布ページが見つからなかったため、同系統の kemono SDXL（Nova Kemono XL、Mol_Keun Mix など: Euler A、20〜30 steps、CFG 3〜5）の中央値 28 / 4.5 / euler_ancestral / normal。ファイルの中を調べ、ComfyUI でマージした SDXL（eps）であることを確かめた |
| Indigo Furry Mix XL（Noob EPS 11） | sdxl | 利用者の指定（v0.12.0） | NoobAI EPS 1.1 系の SDXL。配布ページの推奨（CFG 3〜7、推奨 5、Euler a）と NoobAI の一般的な推奨（20〜30 steps、832×1216、`masterpiece, best quality, very aesthetic`）から 28 / 5.0 / euler_ancestral。生成を確認（約 5 分） |
| Indigo Furry Mix Anima | anima | 利用者の指定（v0.12.0） | ファイルの中を調べると SDXL ではなく Anima（Cosmos-Predict2 系、テキストエンコーダは Qwen3 0.6B）の拡散モデルだったため、系統 `anima` を足した。配布ページの推奨（Euler a か ER SDE、30 steps 未満、CFG 3〜6・作者は 4、1024px 前後、`furry` を入れる）から 28 / 4.0 / er_sde / simple。生成を確認（約 4 分） |
| Wulver（Krea 2） | krea2 | 利用者の指定（v0.12.0） | ファイルの中を調べると Krea 2（テキストエンコーダは Qwen3-VL-4B）の拡散モデルだったため、系統 `krea2` を足した。配布ページの推奨（Turbo: 8 steps、CFG 1.0、euler / simple、shift 1.15、1024 ネイティブ、自然文 60〜120 語）をそのまま使う。CFG を 1 より上げると画が焼け、negative は効かない。生成を確認（約 12 分） |

- **LoRA:** SDXL の 5 モデルは、利用者が設定していた `novabeast xl v1 rank64 pony.safetensors`（強度 1.0）を使う（v0.12.0 の利用者の指定）。Chroma・Anima・Krea 2 は LoRA なし（SDXL の LoRA は合わない）。
- **テキストエンコーダと VAE:** Krea 2 は Comfy-Org/Krea-2 の Qwen3-VL-4B fp8（5.2GB）と qwen_image_vae、Anima は circlestone-labs/Anima の Qwen3 0.6B と qwen_image_vae。ComfyUI の公式ブループリントと同じ組み合わせで、fp8 を選んだのは 24GB 機で拡散モデルと並べるため。
- **選び方:** どの画像モデルも、モデル名を本文から読み取らない。UI のピッカーか cirka の `/image-model` で選ぶ。

## 参照画像用のモデル（SDXL）

| モデル | 用途 | 選定と調整 |
|---|---|---|
| IP-Adapter Plus SDXL（ViT-H）と CLIP-ViT-H-14 | キャラクター・画風の参照 | weight 0.85 では色が焼けて青く飽和したため、キャラクターの weight は強度 × 0.5（0.4 前後）にした。画風（style transfer）は 0.55〜0.8 で主題を写さず配色とトーンが移った |
| ControlNet Union SDXL promax（xinsir） | ポーズ・奥行き・線画 | 1 本で openpose / depth / canny を扱えるため |
| DWPose（ONNX） | ポーズの抽出 | TorchScript 版は ROCm の GPU で数回目に失敗したため、人物検出なし + ONNX のポーズ推定を CPU で動かす |
| Depth Anything V2 Small | 奥行きの抽出 | 軽い版で足りた |

SDXL + ControlNet + IP-Adapter + エンコーダは GPU の予算（空き約 6.8GB）を超えたため、KSampler の直前でエンコーダを外すノードを足した。同じプロセスでの 2 回目以降の IP-Adapter 生成が壊れた件は、ComfyUI を `--cache-none` で起動して解消した。詳しくは [troubleshooting.md › 調整の記録](troubleshooting.md#調整の記録参照画像)。

## 採らなかったもの

| 候補 | 理由 |
|---|---|
| Qwen3.8 27B の Q4_K_S / Q4_K_M をそのまま使う | 24GB 級の共有メモリではページングで止まる（メモリに余裕がある環境では `-Quant none` で使える） |
| LM Studio（v0.10.0 まで） | モデルは同じまま llama.cpp のルータに替えた（上の「LM Studio から llama.cpp へ」） |
| Chroma の BF16 を読み込み時に fp8 へ落とす / Chroma の GGUF | 前者は 24GB 機で ComfyUI が落ちた。後者は ComfyUI-GGUF が要り、ワークフローが対応していない |
| Flux 用の ControlNet / IP-Adapter（Chroma、Anima、Krea 2 の参照画像） | これらのモデルで動作を確かめていないため、元画像 1 枚の img2img だけに対応する |
| Qwen3-0.6B-heretic を旧形式のルータに使う | 検索の要否の判定に落ちた |
| Bonsai の MLX 版、Qwen の MLX 版（確認機の Mac） | Bonsai に使える MLX の abliterated 版が無い。Qwen の MLX 版はディスクに入らない |
| 動画生成モデル（Wan、LTX など） | 対象外（AGENTS.md の禁止） |

## 評価の限界

- 検索モデルの検証は、タスクごとに固定の短いテスト 1 本で合否を見ただけで、長い作業での安定性や回答の質の細かい差は測っていない。
- 画像モデルのパラメータは配布元の推奨を写したもので、モデル同士の画質を同じ条件で比べてはいない。生成できることと、明らかな破綻が無いことを確かめた。
- 速度とメモリは確認済み構成（ROG Ally X、Apple M4）での実測で、別の端末では変わる。

## 出典

- 依頼と検証の記録: 開発セッションでの利用者の依頼（使うモデルの指定、検索モデルの候補と代理リーダーの指定、v0.12.0 の候補一覧、LM Studio から llama.cpp への切り替え時のモデルの同一性の確認）と、そのときの検証結果。
- 設計書と実装記録: [llm-comfyui-workflow-design.md](llm-comfyui-workflow-design.md)（初期値）、[chroma-hd-support-work-instruction.md](chroma-hd-support-work-instruction.md)、[chat-search-tor-bonsai-work-instruction.md](chat-search-tor-bonsai-work-instruction.md) §4（検索モデルの実測）、[llamacpp-router-design.md](llamacpp-router-design.md)（モデルの同一性）、[host-model-selection-design.md](host-model-selection-design.md) §16（v0.12.0 のモデル）、[decisions.md](decisions.md)、[troubleshooting.md](troubleshooting.md)、[usage.md](usage.md)。
- コミット: `50eac1f`（IQ3_M を使う）、`413ac3d`（Chroma の fp8 変換）、`0729046` / `0ec9383`（検索モデルの採否と再検証）、`8e637a0`（LM Studio での生成速度）、`b56478c`（repeat-penalty 1.1）、`08a1b87`（同じ IQ3_M ファイル）、`07b00b3` / `d6d16f8`（v0.12.0 の画像モデル）。
- 配布ページ: [Wulver](https://civitai.com/models/2881657/wulver-krea-2)、[Indigo Furry Mix Anima](https://civitai.com/models/2787288/indigo-furry-mix-anima)、[Indigo Furry Mix XL](https://civitai.com/models/579632/indigo-furry-mix-xl)、[Yiff in Hell](https://civarchive.com/models/1570986)、[Nova Kemono XL](https://civitai.com/models/1641408/nova-kemono-xl)、[Ternary-Bonsai-2-27B](https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf)、[Qwen3.8（Unsloth）](https://unsloth.ai/docs/models/qwen3.8)、[Chroma1-HD](https://huggingface.co/lodestones/Chroma1-HD)。
