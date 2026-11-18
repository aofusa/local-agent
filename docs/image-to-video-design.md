# 画像から動画（Wan 2.2 I2V）設計書（v0.14.0）

対象: 新しい動画タブ（graph `video`）、ComfyUI の動画ワークフロー、`config/host_models.json` の `video`、agent-chat-ui の動画タブ。
この文書は要件、設計、決めたこと、実装記録をまとめる。作業の手順と受け入れ基準は [image-to-video-work-instruction.md](image-to-video-work-instruction.md) にある。LLM と ComfyUI の契約は [llm-comfyui-workflow-design.md](llm-comfyui-workflow-design.md)、モデルの一覧は [host-model-selection-design.md](host-model-selection-design.md) に従う。

作成日: 2026-11-18（同日、§10 の D1〜D8 を推奨どおりに決め、ケモノ向け LoRA の要件を足した。D9、D10 も同日に決まった）
ステータス: 実装済み（`feat/image-to-video`）。フェーズ 0 の計測と決めた既定値、確認した動作、設計からの変更は §11。

## 1. 要件（利用者の指定、2026-11-18）

1. 画像を入力して動画にする（image-to-video）。
2. その機能を Web 画面（agent-chat-ui）から使える。
3. この端末での実行を念頭に最適化する。環境ごとの微調整は `.env` などの設定ファイルでできるようにする。
4. 同時に 1 モデルしか実行できないので、画像生成と同じく、動画の生成中は動画のモデルだけがメモリに載るようにする。
5. 利用者の参考案: ComfyUI の Wan 2.2 I2V A14B（GGUF の High Noise / Low Noise の 2 段）、lightx2v の 4 ステップ LoRA、追加のスタイル LoRA（H-LoRA Anime、Furry Enhancer、Anime Style など）、CFG 1.0、euler / simple、shift 5、81 フレーム・16fps、VideoHelperSuite で MP4（RTX 3080 10GB 向けの構成）。よりよい案があれば採る。
6. このプロジェクトの画像生成はケモノに特化しているので、動画生成もケモノに特化させる。参考案のスタイル LoRA も使い、LoRA を使うかどうかは画像の LoRA と同じく設定で決める（同日の追加の指定）。

変えないもの: 画像タブ（graph `agent`）、チャットタブ（graph `chat`）、cirka と `/coder/turn` の経路と契約。ComfyUI・llama.cpp のルータ・検索用 llama-server・Tor のループバック待受。`job_lock` での直列化。クラウド API を使わないこと。モデルの情報を `config/host_models.json` に書くこと。未知・使えない id を拒否して、別のモデルへ替えないこと。

AGENTS.md と specification.md は、動画生成ワークフローと ComfyUI の中の GGUF を禁止している。この機能の実装の前に仕様を改定する（§10 の D1、D2。決定済み）。

## 2. この端末の条件（2026-11-18 に確認）

| 項目 | 値 |
|---|---|
| 機種 | ROG Xbox Ally X（RC73XA）、AMD Ryzen AI Z2 Extreme |
| GPU | Radeon 890M（gfx1150、RDNA 3.5 の統合 GPU）。専用 VRAM は 0.5GB で、残りは共有メモリ |
| RAM | 24GB（OS から見えるのは 23.1GB）。確認時は他のアプリが動いていて、空きは 2〜4GB だった |
| ComfyUI から見える GPU メモリ | **9.17GB**（ComfyUI の venv の PyTorch 2.9.1 + ROCm 7.2 で `torch.cuda.mem_get_info()`）。Windows では、ComfyUI がそこからさらに 0.6GB を予約する |
| attention | PyTorch の省メモリ SDPA と flash SDPA が有効（AOTriton。ComfyUI は gfx1150 を対応アーキテクチャとして扱う） |
| ComfyUI | v0.38.0-32（`e9027f2b`）。動画に要る標準ノードはそろっている: `WanImageToVideo`、`KSamplerAdvanced`、`ModelSamplingSD3`、`LoraLoaderModelOnly`、`VAEDecodeTiled`（時間方向のタイルあり）、`CreateVideo`、`SaveVideo`（PyAV 19。mp4 / h264、CRF 指定）、`FrameInterpolate`（RIFE / FILM）。GGUF は標準では読めない |
| 入っていないもの | ComfyUI-GGUF、VideoHelperSuite（VHS） |
| ケモノ向けの LoRA | 画像モデル用（Flux・Chroma 用の Furry Enhancer、Anima 用、SDXL 系など）が `Documents\ComfyUI\models\loras` にある。Wan 用は無い（画像モデル用の LoRA は Wan には使えない） |
| ディスク | C: の空きは 92GB |

既存の記録: ComfyUI は一度生成すると、ROCm ランタイムが約 2GB を持ち続ける（`docs/setup.md`）。UMA の予算の中で UNet が全部オフロードされたとき、ROCm で画像が壊れた（`FurryJaReleaseEncoders` の注記）。

## 3. モデルの選定

### 3.1 動画モデルの候補（2026-11 時点）

| モデル | 大きさ | この端末で | 判断 |
|---|---|---|---|
| Wan 2.2 I2V A14B（14B の High / Low の 2 エキスパート）の GGUF と lightx2v の 4 ステップ LoRA | 1 エキスパートあたり Q4_K_M 9.65GB、Q4_K_S 8.75GB、Q3_K_M 7.18GB、Q3_K_S 6.52GB。umT5-XXL fp8 6.74GB、VAE 0.25GB、lightx2v の LoRA 0.64GB / 0.74GB | 1 エキスパートずつなら載る（Q3_K_M は丸ごと、Q4 は一部をオフロード） | **採る（既定）**。ローカルの I2V で画質と ComfyUI での実績が最も厚く、ケモノ向けの LoRA もある。Apache-2.0 |
| lightx2v の蒸留済みモデル（2026-04、I2V A14B 4 ステップ 720p） | BF16 28.6GB、FP8 / INT8 約 15GB（1 エキスパート） | 公式の形では載らない（GGUF はコミュニティの変換だけ） | 採らない。同じ蒸留を LoRA で当てる |
| Wan 2.2 TI2V-5B | GGUF Q8_0 で約 5GB、VAE 1.4GB | 載る。ただし 720p 専用（1280×704）で、公式の 4 ステップ蒸留が無い（コミュニティの Turbo はある） | 予備。A14B が実用にならないときに比べる |
| LTX 2.3（22B） | fp8 で 23GB、GGUF の Q4 でも 12GB を超える | 載らない | 採らない（独自ライセンス） |
| MiniMax H3 | int8 で 19.5GB、テキストエンコーダは Qwen3-VL-32B | 載らない | 採らない（商用は別ライセンス） |
| Wan 2.2 の統合モデル（Rapid AIO などコミュニティのマージ） | 14B が 1 本 | 載る | 採らない（混ぜた LoRA と出所を確かめられない） |

### 3.2 参考案から変えるところ

| 参考案 | この設計 | 理由 |
|---|---|---|
| VideoHelperSuite の Video Combine で MP4 | ComfyUI 標準の `CreateVideo` + `SaveVideo`（mp4 / h264、CRF） | VHS は入っておらず、標準ノード（PyAV は導入済み）で足りる。カスタムノードを増やさない |
| High と Low を両方読み込んでおく | 段ごとに読み、使い終わったら unload する（umT5 → High → Low の順に 1 つずつ）。読み込みの順は `after` の入力で固定する | GPU 9.17GB・RAM 23GB では、両方（Q4_K_M で 19.3GB）を持つとページングになる。要件 4 |
| umT5 を CPU に置く | 既定は GPU（LLM を unload した後なので空いている）。`.env` で CPU にも切り替えられる | 共有メモリでは、CPU に置いても物理メモリは減らない。GPU のほうが速い |
| 832×480・81 フレームの固定 | 入力画像の縦横比を保ち、画素数の上限（`.env`）に収める。長さも `.env` で決める | 縦長の画像（画像タブの 832×1216 など）を横長に切り抜かない。この端末では小さめ・短めを既定にする |
| lightx2v の LoRA（版の指定なし） | lightx2v の I2V 用 `…_lightx2v_4step_1022`（High 635MB、Low 739MB）を、カタログの `distill_loras` に書く | lightx2v が公開している I2V 用 LoRA の最新版（2026-11 時点。T2V 用の `1217` とは別物）。サンプリングの値と組なので、スタイルの LoRA と分ける（§6.3） |
| 追加のスタイル LoRA（H-LoRA Anime、Furry Enhancer、Anime Style など） | ケモノ向けの LoRA を、カタログの `loras` に High / Low 別に書く（書き方は画像の `loras` と同じ）。既定は Furry Enhancer の Wan 2.2 V3（I2V 用の High / Low）。どちらのエキスパートに当てるかと強度はフェーズ 0 で決める（§3.3） | 要件 6。Wan 2.2 の LoRA は High と Low で別のファイルのことが多く、片方にしか効かない（または壊れる）ものもある |
| 英語のプロンプトを手で書く | 画像タブと同じく、ComfyUI の中で 27B が開始画像を見て英語の動きの説明（JSON）を書き、eject してから動画モデルを読む。説明はケモノに特化する（§5.5） | Wan は英語と中国語で学習している。画像タブと同じ契約（LLM → eject → 拡散モデル）にそろえる |
| GGUF は Q4_K_M | 既定の量子化は、フェーズ 0 で Q4_K_M と Q3_K_M を実測して決める。カタログには両方を並べ、`.env` の `DEFAULT_VIDEO_MODEL` で選ぶ | 9.17GB に Q4_K_M（9.65GB）は丸ごと載らない。部分オフロードが ROCm で壊れないか、速さと画質の差を確かめる |

### 3.3 ケモノ向けの LoRA

| LoRA | 配布 | 判断 |
|---|---|---|
| Furry Enhancer（Wan 2.2 V3、I2V 用の High / Low） | Civitai | **採る（既定）**。この端末で画像用（Flux 版・Chroma 版）を使っている系列。古い版には「High に当てると真っ黒な動画になる」という作者の注記があり、V3 の High はフェーズ 0 で確かめる。強度は 1.0 を起点にフェーズ 0 で決める |
| H-LoRA Anime、Anime Style（参考案の名前） | 不明 | 名前から配布元を特定できなかった（D10）。Wan 2.2 用のアニメ調 LoRA には、動きが大きく減ると作者が書いているもの（Retro 90s anime）や、T2V 用で絵柄が安定しないという報告があるもの（Simple pure-color anime）がある。利用者が使いたいものを決めてから、フェーズ 0 で比べる |
| Anthro Furry transformation（FurryTF） | Civitai | 採らない。人をケモノに変える変身の LoRA で、ケモノの画像を動かす用途と合わない |

- 入手: Civitai の LoRA は、チェックポイントや画像の LoRA と同じく利用者が入手して置く（セットアップは取得しない）。`Documents\ComfyUI\models\loras` など、セットアップが探すフォルダに置けば `tools/comfyui/models/loras` にリンクされる。ライセンスと利用規約は利用者が確かめる（README と同じ）。
- トリガー語の要る LoRA は、カタログの `params.prompt_prefix`（画像の `quality_prefix` と同じ仕組み）で positive の先頭に足す。
- CFG 1（蒸留済み）では negative が効かない。ケモノらしさは、LoRA と positive の書き方（§5.5）で保つ。

## 4. 全体の流れ

```
他ホストのブラウザ（動画タブ: ケモノの画像 1 枚 + 日本語の指示）
   │
   ▼
LangGraph（graph video、:2024）  job_lock("video") を握る。ComfyUI の /free を呼んでから投入する
   │  ComfyUI の HTTP API だけ
   ▼
ComfyUI（127.0.0.1:8188）の動画ワークフロー（1 本）
   1. ルータの 27B が開始画像を見て、ケモノの動きの説明を英語の JSON で返す
   2. eject: ルータの 27B を unload し、外れたことを確かめる
   3. umT5 で positive / negative を作り、unload する
   4. VAE で開始画像を latent に入れる（WanImageToVideo）
   5. High（GGUF + lightx2v + ケモノ向け LoRA）で 0〜2 ステップ。終わったら unload する
   6. Low（GGUF + lightx2v + ケモノ向け LoRA）で 2〜4 ステップ。終わったら unload する
   7. VAE のタイルデコード → CreateVideo（16fps）→ SaveVideo（mp4 / h264）
   ▼
LangGraph が mp4 を outputs/video/ に保存し、/media/video/<uuid>.mp4 の URL を UI に返す。最後に /free
```

大きなモデルが載るのは、どの時点でも 1 つだけ（27B → umT5 → High → Low）。LoRA はそれぞれのエキスパートと一緒に載り、一緒に外れる。VAE（0.25GB）は動画モデルの一部として残ってよい。

## 5. ComfyUI ワークフロー

### 5.1 ノード ID（固定）

テンプレートは `scripts/build_workflows.py` が生成する（`workflows/wan22/i2v.api.json` と `workflows/maps/wan22.json`。手で直さない）。LLM の部分は、画像のテンプレートと同じ ID・同じノードを使う。

| ID | クラス | 役割 | LangGraph が書き換える入力 |
|---|---|---|---|
| `llm_backend` / `llm_backend_vision` | LMConnectLMStudioBackend | ルータ（`LLM_URL`、`LLM_MODEL`） | なし（read timeout は `AGENT_IDLE_TIMEOUT_S`） |
| `user_prompt` | PrimitiveStringMultiline | 日本語の指示 | 指示（空なら既定の文、§5.5） |
| `ref_image` | LoadImage | 開始画像 | ファイル名 |
| `vision` | LMConnectVision | 開始画像のケモノの英語の説明（長辺 768） | なし |
| `prompt_join` | StringConcatenate | 指示 + `[Start image]` の節 | なし |
| `prompt_node` | LMConnectPromptWithSystem | `{"positive","negative"}` の JSON | なし |
| `eject` | FurryJaEjectLLM | ルータの unload と確認 | なし |
| `split` | FurryJaSplitTags（prose） | JSON の分割。壊れていたらリトライせず、生文字列を positive に | 既定の negative、`prompt_prefix`（quality_prefix のスロット） |
| `te` | FurryJaTextEncoderAfter（新） | `eject` の後に umT5 を読む（CLIPLoader の type `wan`）。読む前にルータが空か確かめる | ファイル名、device |
| `positive` / `negative` | CLIPTextEncode | | なし |
| `release_te` | FurryJaReleaseAfter（新） | 両方の条件付けができたら、モデルをすべて unload | なし |
| `vae` | FurryJaVAELoaderAfter（新） | `release_te` の後に VAE を読む | ファイル名 |
| `i2v` | WanImageToVideo | 開始画像を latent に入れる（縦横は中央で合わせる） | width、height、length |
| `unet_high` | FurryJaVideoModelAfter（新） | `i2v` の後に High を読む | ファイル名 |
| `lora_high_1`… | LoraLoaderModelOnly | カタログの `distill_loras.high`（lightx2v）、続けて `loras.high`（ケモノ向け）を、書いた順に `unet_high` の直後へ | 実行時に挿入 |
| `shift_high` | ModelSamplingSD3 | | shift |
| `sampler_high` | KSamplerAdvanced | ノイズあり、0 から split まで、残りのノイズを返す | seed、steps、cfg、sampler、scheduler、split |
| `release_high` | FurryJaReleaseAfter | High（と、その LoRA）を unload（latent を素通し） | なし |
| `unet_low` | FurryJaVideoModelAfter | `release_high` の後に Low を読む | ファイル名 |
| `lora_low_1`… | LoraLoaderModelOnly | 同じく `distill_loras.low`、続けて `loras.low` を `unet_low` の直後へ | 実行時に挿入 |
| `shift_low` | ModelSamplingSD3 | | shift |
| `sampler_low` | KSamplerAdvanced | ノイズなし、split から最後まで | seed、steps、cfg、sampler、scheduler、split |
| `release_low` | FurryJaReleaseAfter | Low を unload | なし |
| `decode` | VAEDecodeTiled | 空間と時間方向のタイル | tile_size、overlap、temporal_size、temporal_overlap |
| `video` | CreateVideo | フレームから動画 | fps |
| `save` | SaveVideo | `output/furry_ja/video/` に mp4 | crf |

フレーム補間（§7 の `VIDEO_INTERPOLATE`）を有効にしたときだけ、`decode` と `video` のあいだに `interp_model`（FrameInterpolationModelLoader）と `interp`（FrameInterpolate）を挟み、fps を倍にする案だった（見送り。§11）。

### 5.2 順序の保証

ComfyUI は、入力の無いローダを先に実行することがある。画像の `ckpt` と同じく、重いモデルを読むノードにはすべて `after` の入力を付けて、前の段に依存させる。

- `te` ← `eject`（LLM の unload の後）
- `vae` ← `release_te`
- `unet_high` ← `i2v`（条件付けと latent ができた後）
- `unet_low` ← `release_high`（High の段が終わって unload した後）
- `decode` ← `release_low`

テストで、どのローダも依存をたどると `eject` に着くこと、`unet_low` の祖先に `release_high` があることを確かめる（画像の「重いノードはすべて `ckpt` に依存」のテストと同じ形）。LoRA のノードはそれぞれのエキスパートのローダの後ろにつながるので、同じ順序に従う。

### 5.3 新しい ComfyUI ノードと LoRA の挿入

`comfyui_nodes/furry_ja` に足すノード:

- `FurryJaTextEncoderAfter`: CLIPLoader（type `wan`、device `default` / `cpu`）に `after` を足したもの。読む前に `ensure_unloaded` でルータが空か確かめる。
- `FurryJaVAELoaderAfter`: VAELoader に `after` を足したもの。
- `FurryJaVideoModelAfter`: 拡散モデルのローダに `after` とルータの確認を足したもの。`.gguf` は ComfyUI-GGUF の `UnetLoaderGGUF`、`.safetensors` は標準の UNETLoader で読む。読み込んだ大きさと、GPU に全部載ったか（部分読み込みか）をログに出す。
- `FurryJaReleaseAfter`: 受け取った値を返す前に `unload_all_models()`、`cleanup_models_gc()`、`gc.collect()`、`soft_empty_cache()` を行い、残っているモデルの数と空きメモリをログと UI の出力に残す。

LoRA の挿入は、画像の `templates.insert_loras`（LoraLoader を `ckpt` の直後に挿入）を変えずに、モデルだけに当てる版を足す（LoraLoaderModelOnly を、指定したノードの直後に挿入する）。High と Low で別々に呼ぶ。既存の `FurryJaReleaseEncoders`、`ckpt` 系、`eject`、`split` は変えない。

### 5.4 サンプリングの既定値（カタログの `params`）

| 値 | 既定 | 出典 |
|---|---|---|
| steps / split | 4 / 2（High が 0〜2、Low が 2〜4） | lightx2v の 4 ステップ LoRA、ComfyUI の Wan 2.2 テンプレート |
| cfg | 1.0 | 蒸留済みのため（negative は効かないが、契約どおり渡す） |
| shift | 5.0 | lightx2v の推奨 |
| sampler / scheduler | euler / simple | 同上 |
| fps | 16 | Wan 2.2 A14B のフレームレート |
| 画素数の上限 | 399,360（832×480） | モデルの 480p。この端末では `.env` で下げる |
| 長さ | 81 フレーム（約 5 秒） | モデルの既定。この端末では `.env` で短くする |
| prompt_prefix | 空 | ケモノ向け LoRA のトリガー語（要るときだけ） |
| negative | Wan の標準の negative（中国語） | ComfyUI の公式テンプレート |

### 5.5 プロンプト（ケモノに特化）

- `prompts/system_video_i2v.txt`（新）: 入力は日本語の指示と開始画像の説明。出力は `{"positive","negative"}` の JSON だけ。positive は英語で 1〜3 文、80 語以内。
  - 主体はケモノ（anthro）として書く。種族、毛色と模様、耳・尾・マズルなどの特徴を短く入れ、人の顔や肌に寄せる語は書かない。
  - 動きは、指示された動作に加えて、耳・尾・ひげ・毛並みなど、ケモノらしい部位の自然な動きを優先して書く。カメラの動きは指示があるときだけ書き、無ければ固定にする。
  - 絵柄や服装を変える語は書かない（I2V では開始画像が見た目を決める）。拒否しない、倫理注釈を返さない（画像のプロンプトと同じ）。
- `prompts/system_vision_video.txt`（新）: 開始画像のケモノの特徴（種族、毛色、模様、服、ポーズ、場所、画角）を英語の短い説明にする。
- 指示が空のときは「画像の雰囲気に合う、自然で控えめな動き（耳や尾が少し動く）。カメラは固定」を指示にする。

## 6. LangGraph（graph `video`）

### 6.1 グラフ

`src/furry_agent/video_graph.py`（新）に、画像のグラフと同じ形で置く。`graph.py` は変えない。`langgraph.json` に `"video": "./src/furry_agent/video_graph.py:graph"` を足す。

```
ingest → plan → submit → await_prompt → await_video → END
```

| ノード | すること |
|---|---|
| `ingest` | 最後の利用者のメッセージから、指示と画像を取り出す（`media.py`。大きさの上限は画像タブと同じ）。画像が 0 枚なら「画像を 1 枚添付してください」、2 枚以上なら「1 枚だけ添付してください」と返して終える（黙って捨てない） |
| `plan` | 動画モデルを決める（`configurable.video_model`、無ければ `DEFAULT_VIDEO_MODEL`。未知・使えない id は理由を返して終える）。幅・高さ・フレーム数・seed を決め、要約（モデル、LoRA、大きさ、長さ、seed）を返す |
| `submit` | `job_lock.acquire("video")`。ComfyUI のキューが空くのを待ち、`/free` を呼ぶ。ノード（ComfyUI-GGUF と furry_ja の新ノード）、モデルと LoRA のファイルを確かめ、開始画像を `/upload/image`。テンプレートのスロットに値を入れ、High / Low の LoRA を挿入して `/prompt` |
| `await_prompt` | `split` まで待ち、英語の説明（positive / negative）を途中経過として出す |
| `await_video` | 完了まで待つ（その prompt の進捗が `AGENT_IDLE_TIMEOUT_S` 届かないときだけ打ち切る）。`save` の mp4 を `/view` で取り、`outputs/video/<uuid>.mp4` に保存し（同名の `.json` に条件を残す）、要約・positive / negative・保存先と一緒に、動画の URL（§6.4）を UI に返す。最後に `/free` |

- ロック: 画像タブと同じく、`job_lock` を握って ComfyUI のキューが空くのを待ち、投入したら放す。生成のあいだはチャットタブと `/coder/turn` が ComfyUI のキューが空くのを待ち（`chat_common._image_tab_busy`）、画像タブは投入の前にキューが空くのを待つ。`job_lock.TAB_LABELS` に `video`（動画タブ）を足す。
- 中断: UI で止めたら ComfyUI の prompt を interrupt し、`/free` を呼ぶ（画像タブと同じ）。
- 失敗の段: 入力、モデル、ノード、アップロード、キュー投入、プロンプト生成、生成、タイムアウト、保存。
- ログ: `logs/furry_agent.log` に prompt_id、モデル、LoRA、幅×高さ×フレーム数、seed、各段の unload の確認、保存先を残す。指示の全文は残さない（字数だけ）。

### 6.2 大きさと長さ（`video_plan.py`、純粋関数）

- 幅・高さ: 開始画像の縦横比を保ち、`min(モデルの上限, VIDEO_MAX_PIXELS)` 以下で 16 の倍数に切り下げる（例: 832×1216 の画像、上限 307,200 画素 → 448×656）。
- フレーム数: 秒数 × fps を 4k+1 に丸める（3 秒 × 16fps → 49、5 秒 → 81）。秒数は `configurable.video_seconds`（UI の長さの選択）→ `VIDEO_SECONDS` → モデルの既定の順に決め、`VIDEO_MAX_SECONDS` を上限にする。
- seed: 実行ごとに乱数で決め、要約に出す。

### 6.3 カタログ（`config/host_models.json` の `video`）

```json
"video": [
  {
    "id": "wan22-i2v-a14b-q4",
    "label": "Wan 2.2 I2V A14B（Q4_K_M・画質優先）",
    "family": "wan22",
    "experts": {"high": "Wan2.2-I2V-A14B-HighNoise-Q4_K_M.gguf", "low": "Wan2.2-I2V-A14B-LowNoise-Q4_K_M.gguf"},
    "models": {"clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "vae_name": "wan_2.1_vae.safetensors"},
    "distill_loras": {
      "high": ["wan2.2_i2v_A14b_high_noise_lora_rank64_lightx2v_4step_1022.safetensors:1.0"],
      "low": ["wan2.2_i2v_A14b_low_noise_lora_rank64_lightx2v_4step_1022.safetensors:1.0"]
    },
    "loras": {
      "high": ["Furry Enhancer Wan2.2 V3 High Noise I2V.safetensors:1.0"],
      "low": ["Furry Enhancer Wan2.2 V3 Low Noise I2V.safetensors:1.0"]
    },
    "params": {"steps": 4, "split_step": 2, "cfg": 1.0, "shift": 5.0, "sampler_name": "euler",
               "scheduler": "simple", "fps": 16, "max_pixels": 399360, "frames": 81, "prompt_prefix": ""}
  },
  {"id": "wan22-i2v-a14b-q3", "label": "Wan 2.2 I2V A14B（Q3_K_M・省メモリ）", "...": "Q3_K_M の GGUF。ほかは同じ"}
]
```

LoRA の書き方（画像の `loras` と同じ）:

- `"<ファイル>:<強度>"` の配列で、書いた順に当てる。強度は 0 より大きく 2 以下。ファイル名は、大文字と小文字、拡張子の有無を区別せずに ComfyUI の LoRA の一覧と照らす（画像と同じ `parse_catalog_loras` と `resolve_lora_names` を使う）。
- Wan 2.2 A14B は High と Low の 2 つのモデルなので、`high` と `low` に分けて書く。同じファイルを両方に当てるときは両方に書く。LoRA を使わないときは空の配列にする（キーごと省いてもよい）。
- `distill_loras` は、サンプリングの値（steps 4、cfg 1）と組になる蒸留の LoRA（lightx2v）。`loras` はケモノ向けのスタイルの LoRA。分けるのは、`loras` を空にしても 4 ステップの設定が崩れないようにするため。蒸留を使わないモデルを足すときは、`distill_loras` を書かず、`params` を 20 ステップ・cfg 3.5 などにする。
- 書いた LoRA が ComfyUI に無いモデルは、画像と同じく、一覧で理由つきで使えないと表示する（黙って外さない）。
- 同梱のエントリは、どちらの量子化もケモノ向けの LoRA を入れた構成にする（SDXL の画像モデルがすべて novabeast を使うのと同じ）。LoRA の有無と強度はカタログだけで決め、`.env` では変えない（画像と同じ）。ファイル名は、利用者が入手したものに合わせる。

ほかの検証と一覧:

- 読み込み時に、未知のキー、`params` の範囲外、`experts` の欠け、LoRA の書式の誤り、未登録の family を拒否する（画像と同じく、黙って外さない）。
- `downloads` は、family（共通の umT5・VAE・lightx2v の LoRA）と id（エキスパートの GGUF）の両方をキーにできるようにし、`scripts/host_models.py wanted --ids` が両方を集める。項目は Hugging Face の repo、ファイル、大きさ、SHA-256。ケモノ向けの LoRA は `downloads` に入れない（利用者が置く）。
- `GET /models` に `video`（id、label、family、params、loras、available、reason）と `defaults.video` を足す。使えるかどうかは、ComfyUI のノード（`UnetLoaderGGUF` と furry_ja の新ノード）とファイル（GGUF、umT5、VAE、LoRA）で決める。`HOST_MODELS_DISABLE` も効く。

取得元（実装時にファイルの場所を確かめ、SHA-256 を固定する）:

| ファイル | 取得元 |
|---|---|
| High / Low の GGUF | `QuantStack/Wan2.2-I2V-A14B-GGUF`（`HighNoise/`、`LowNoise/`） |
| umT5-XXL fp8 | `Comfy-Org/Wan_2.1_ComfyUI_repackaged`（`split_files/text_encoders/`） |
| Wan 2.1 VAE | `Comfy-Org/Wan_2.2_ComfyUI_Repackaged`（`split_files/vae/`） |
| lightx2v の LoRA | `lightx2v/Wan2.2-Distill-Loras` |
| ケモノ向けの LoRA（Furry Enhancer など） | Civitai（利用者が入手して置く。セットアップは取得しない） |
| ComfyUI-GGUF | `city96/ComfyUI-GGUF`（コミットを固定。依存は `gguf` パッケージ） |

### 6.4 UI への返し方（D3 で決定: LangGraph が配信する）

- http.app（`/coder/turn` と同じ app）に `GET /media/video/{name}` を足し、`outputs/video/` の mp4 を返す。受け付ける名前は実行ごとの UUID の形（`<uuid>.mp4`）だけで、パスは受け取らない。Range 要求に答えるので、シークできる。
- メッセージには `{"type":"video","mimeType":"video/mp4","url":"/media/video/<uuid>.mp4","metadata":{…}}` を載せ、UI は LangGraph の URL と結合して再生する。他ホストのブラウザからも見え、スレッドは軽いまま。
- `outputs/` を消すと、古いメッセージの動画は見えなくなる（UI は「動画が見つかりません」と出す）。
- 採らなかった案: 画像と同じく base64 でメッセージに載せる。agent-chat-ui はスレッドの値を丸ごと送り直す（`streamMode: values`、送信のたびの履歴の取得、サイドバーは最大 100 スレッドの値を取る）ため、数 MB の動画がスレッドの数だけ毎回届く。
- 開始画像（入力）は、利用者のメッセージに base64 で残る（画像タブと同じ）。

## 7. 環境ごとの調整（`.env`）

モデルの情報（ファイル、LoRA、モデル固有のサンプリング値）は `config/host_models.json` に、この端末の性能に合わせる値は `.env` に置く（既存の `CHROMA_MAX_PIXELS` と同じ扱い。D4 で決定）。空ならコードの既定を使う。既定はこの端末に合わせた値で、フェーズ 0 の計測で決める。LangGraph を再起動すると反映される。

| キー | 既定（案） | 意味 |
|---|---|---|
| `DEFAULT_VIDEO_MODEL` | フェーズ 0 で決める（`wan22-i2v-a14b-q3` か `-q4`） | 要求に id が無いときの動画モデル |
| `VIDEO_MAX_PIXELS` | 307200（約 480×640） | 1 フレームの画素数の上限 |
| `VIDEO_SECONDS` | 3 | 既定の長さ（秒） |
| `VIDEO_MAX_SECONDS` | 5 | 要求できる長さの上限（UI の長さの選択の上限にもなる） |
| `VIDEO_VAE_TILE` | 256 | VAE のタイルの一辺（0 でタイルなし）。重なりは 1/4 |
| `VIDEO_VAE_TEMPORAL` | 16 | VAE が一度にデコードするフレーム数（重なりは 4） |
| `VIDEO_TEXT_ENCODER_DEVICE` | `default` | `cpu` にすると umT5 を CPU で動かす |
| `VIDEO_CRF` | 20 | h264 の画質（小さいほど高画質で、ファイルが大きい） |
| `VIDEO_INTERPOLATE` | — | 見送った（§11）。2 にすると RIFE でフレームを補間し、fps を 2 倍にする案だった（補間モデルの取得が要る。任意の機能） |

既存のキーで効くもの: `COMFYUI_EXTRA_ARGS`（ComfyUI の起動引数。例: `--reserve-vram 0.5`）、`AGENT_IDLE_TIMEOUT_S`、`JOB_LOCK_*`、`HOST_MODELS_DISABLE`。LoRA の有無と強度は `.env` では変えない（§6.3）。

`docs/configuration.md` には、ほかの環境の目安も表で書く（例: 10GB 級の dGPU は Q4_K_M・832×480・5 秒、16GB 以上は Q5_K_M 以上）。

## 8. UI（agent-chat-ui）

| 変更 | ファイル |
|---|---|
| 「動画」タブ（graph `video`）。`isChatTab` の二択を三択にする | `components/thread/mode-tabs.tsx`、`components/thread/index.tsx` |
| 動画タブの添付は画像 1 枚だけ（PDF は受け付けない）。入力欄の案内文 | `hooks/use-file-upload.tsx`、`components/thread/index.tsx` |
| 応答の動画を `<video controls loop playsinline>` で再生し（`/media/video` の URL を LangGraph の URL と結合）、保存のリンクを付ける | `components/thread/messages/ai.tsx`、`lib/multimodal-utils.ts` |
| モデルのピッカーに `video`（`configurable.video_model`、sessionStorage は `video_model`）。副行に LoRA も出す | `components/thread/model-picker.tsx`、`components/thread/index.tsx` |
| 長さの選択（D5 で決定）: 動画タブの入力欄に「2 秒 / 3 秒 / 5 秒」など（上限は `VIDEO_MAX_SECONDS`）。`configurable.video_seconds` で送る | `components/thread/mode-tabs.tsx`、`components/thread/index.tsx` |
| 「この画像を動画にする」ボタン（D5 で決定）: 画像タブの生成画像から動画タブへ切り替え、その画像を添付する | `components/thread/messages/ai.tsx`、`components/thread/index.tsx` |

在庫の UI に足すのはこの範囲だけで、specification.md の「UI の変更範囲」にも書き足す。

調査で見つけた既存の挙動（動画タブを足すときに確かめて直す）:

- チャットタブで添付を隠すのは見た目だけで、別のタブで付けた添付が送信に乗る。動画タブでは添付の数を制限するので、タブを替えたときの添付の扱いを決める。
- `ModeTabs` は開始画面と会話画面の 2 か所にあり、タブの切り替えで両者をまたぐと、履歴の読み直しが飛ぶかもしれない（コードを読んだだけで、未確認）。

## 9. 制約と危険

| 項目 | 内容 | 対策 |
|---|---|---|
| 速さ | 未計測。iGPU のため、1 本に 10〜30 分以上かかる見込み | フェーズ 0 で実測し、既定の大きさと長さを決める。進捗がある限り打ち切らない（`AGENT_IDLE_TIMEOUT_S`） |
| 部分オフロードで出力が壊れる | 以前、UNet の全オフロードで ROCm の画像が壊れた | フェーズ 0 で Q4_K_M（部分読み込み）の出力を確かめる。壊れたら Q3_K_M を既定にし、Q4 は使えないと記録する |
| LoRA のメモリ | LoRA の重みも GPU の予算（9.17GB）を使う。Q3_K_M でも部分読み込みになるかもしれない | フェーズ 0 で LoRA を当てた状態で測る。収まらなければ Q3_K_S（6.52GB）も試す |
| LoRA の副作用 | 古い Furry Enhancer は High に当てると真っ黒な動画になる。アニメ調の LoRA には、動きを大きく減らすものがある | フェーズ 0 で、Low だけ・High と Low の両方を同じ seed で比べ、効く側にだけ当てる |
| スローモーション | lightx2v の LoRA は、I2V の動きが遅くなりがちと報告されている | カタログで LoRA と強度を差し替えられる。補正の LoRA は今回は入れない |
| メモリ不足 | ほかのアプリ（ブラウザ、VRChat など）が RAM を使っていると、ページングで止まる | 利用の手引きに「生成中は他のアプリを減らす」と書く（画像と同じ）。release ノードのログに空きを残す |
| ComfyUI-GGUF の互換 | ComfyUI v0.38 との組み合わせは未確認 | コミットを固定し、フェーズ 0 で確かめる |
| ライセンス | Wan 2.2 と umT5 は Apache-2.0。GGUF の変換物、lightx2v の LoRA、ComfyUI-GGUF は実装時に確かめる。Civitai の LoRA は作者ごとに条件が違う | `docs/third-party-licenses.md` に書く。Civitai の LoRA は利用者が確かめる（チェックポイントと同じ） |
| 生成物 | 成人向け表現を含む生成物とモデルの規約は、利用者の責任（README と同じ） | 変更なし |

対象外（今回はやらない。D7 で決定）: テキストだけからの動画、動画の入力（VHS）、最初と最後のフレームの指定（Wan の FLF2V）、音声、5 秒を超える延長、アップスケール、cirka のツール、macOS の `scripts/*.sh`、チャットタブや画像タブからの文での案内。

## 10. 決めたこと・決めること

D1〜D8 は 2026-11-18 に利用者が推奨どおりと決めた。D9、D10 はケモノ向け LoRA の追加（要件 6）で出た項目で、同日に利用者が決めた。

| | 内容 | 決定 |
|---|---|---|
| D1 | 仕様の改定: AGENTS.md の禁止「動画生成ワークフロー（Wan、LTX など）」、specification.md の「動画生成モデルはロードしない」、llm-comfyui-workflow-design.md の「作ってはいけないもの: 動画生成ワークフロー」を、「画像タブとチャットタブでは動画を生成しない。動画の生成は、動画タブ（graph `video`）の画像から動画への変換だけ」に改める | 改める |
| D2 | GGUF の扱い: AGENTS.md の「ComfyUI の中で GGUF を動かさない」は、LLM（llama-cpp-python）を ComfyUI に入れないための規則。動画モデルの重みを ComfyUI-GGUF（PyTorch で展開し、llama-cpp-python は使わない）で読むことを例外とする | 例外として認める |
| D3 | 動画の返し方（§6.4） | LangGraph が配信する（`/media/video`） |
| D4 | 設定の置き場: モデルの情報はカタログ、環境の調整値は `.env` の `VIDEO_*`（`.env.example` にも足す） | `.env` に置く |
| D5 | UI の範囲: 長さの選択と「この画像を動画にする」ボタン | 両方入れる |
| D6 | セットアップ: `setup.ps1` に含め、`-SkipVideo` で外せるようにする（既定のモデルで 23〜28GB を取得） | `setup.ps1` に含める |
| D7 | 対象外（§9） | このまま |
| D8 | フェーズ 0 の計測のために、`tools/comfyui` へ ComfyUI-GGUF を入れ、モデル（Q4_K_M と Q3_K_M の両方と共通のファイルで約 42GB）を取得する。使わなかった量子化は計測の後に消す | 進める（作業の依頼の後） |
| D9 | ケモノ向け LoRA の入手: Furry Enhancer の Wan 2.2 V3（I2V 用の High / Low）を、利用者が Civitai から入手して `Documents\ComfyUI\models\loras`（または `tools\comfyui\models\loras`）に置く。フェーズ 0 の前に要る | 利用者が置いた（2026-11-18） |
| D10 | 参考案の「H-LoRA Anime」「Anime Style」: 配布元を特定できなかった。使いたい LoRA（Civitai のページかファイル）を教えてもらい、フェーズ 0 で比べてから採否を決める。教えてもらえないときは Furry Enhancer だけで進める | Furry Enhancer だけで進める（利用者、2026-11-18） |

## 11. 実装記録

### 11.1 実装（2026-11-18、`feat/image-to-video`）

| フェーズ | コミット | 内容 |
|---|---|---|
| 1 仕様の改定 | `17e7b4a`、`e30b6c1` | AGENTS.md、specification.md、llm-comfyui-workflow-design.md（D1、D2）。ロックは画像タブと同じく投入までと書き直した |
| 2 ComfyUI | `ad4dfaa`、`a672db9` | `FurryJaTextEncoderAfter`、`FurryJaVAELoaderAfter`、`FurryJaVideoModelAfter`、`FurryJaReleaseAfter`。`build_workflows.py` が `workflows/wan22/i2v.api.json` とマップを生成（既存のテンプレートはバイト単位で同じ） |
| 3 LangGraph | `b227551`、`d022d3b` | カタログの `video`（High / Low 別の LoRA、`distill_loras`）、`GET /models` の `video` と `video_options`、graph `video`、`video_plan.py`、`/media/video` |
| 4 セットアップ | `f5516a2`、`8740857` | `setup-video-models.ps1`、`setup.ps1 -SkipVideo`、`doctor.ps1`、`.env.example` の `VIDEO_*`。カタログの先頭（既定）を Q3_K_M に |
| 5 UI | `ff3c1d3` | 動画タブ、長さの選択、動画モデルのピッカー、再生と保存、「この画像を動画にする」 |
| 7 文書 | `2167c19` ほか | setup、usage、configuration、architecture、models、troubleshooting、third-party-licenses、decisions、README、CHANGELOG |

### 11.2 フェーズ 0 の計測

条件: この端末（§2）。開始画像は画像タブで作った 832×1216 のケモノ（`outputs/20261008-192023_7442fd37_furry_ja_00018_.png`）、448×656・49 フレーム（3 秒）、seed 20261122、動きの説明は固定の英文（LLM のノードを外して投入）、VAE のタイル 256 / 時間方向 16、umT5 は GPU。スクリプトと結果は `artifacts/video/`（git 管理外）。番号は作業指示書のフェーズ 0 の表。

| # | 量子化 | LoRA | 全体 | 1 ステップ（High / Low） | GPU に載った量（High / Low） | 空き RAM の最小 / コミットの最大 | 出力 |
|---|---|---|---|---|---|---|---|
| 1 | Q3_K_M | lightx2v だけ | 23.4 分 | 342 / 255 秒 | 3.6 / 3.0GB（残りは CPU 側） | 0.02GB / 35.8GB | 振り向いて笑う。黒いフレームなし。マズル・耳・尾・毛並みは最後まで保たれた |
| 2 | Q4_K_M | lightx2v だけ | 23.9 分 | 308 / 284 秒 | 0 / 0GB（全部 CPU 側） | 0.02GB / 41.4GB | 壊れていない（全部を CPU 側から読んでも、以前の ROCm の画像のような破損は出なかった）。#1 と同じ動き、線が少し整う |
| 3 | — | Furry Enhancer を Low だけ | — | — | — | — | 測らなかった（#4 で High に当てても黒くならなかったため） |
| 4 | Q3_K_M | lightx2v + Furry Enhancer V3（High と Low、1.0） | 27.0 分 | 348 / 351 秒 | 1.5 / 1.6GB | 0.01GB / 40.6GB | 黒いフレームなし。毛並みの質感が増え、開始画像の細部（汗のマーク）も残った。動きの量は #1 と同じくらい |
| 5 | — | アニメ調の LoRA | — | — | — | — | 対象外（D10） |
| 6 | — | 832×480・81 フレーム | — | — | — | — | 測らなかった。トークン数が #1 の約 2 倍で、1 本 50 分を超える見込みのため（§11.3） |

内訳（#1）: umT5 の読み込みとエンコード 51 秒、開始画像のエンコード 34 秒、High 11.7 分、Low 8.9 分、タイルデコード 73 秒（#2・#4 は 86 秒）、保存 1 秒。各段の release は、次のローダの前にモデルが 1 つも残っていないことを報告した（`alive before=[] after=[]`。`--cache-none` で使い終えたエキスパートが GC で外れている）。

わかったこと:

- 時間はほぼ計算で決まる。GPU に載る量が 3.6GB でも 0GB でも、1 ステップは 300〜350 秒で変わらない（Q4_K_M の展開は Q3_K_M より軽く、むしろ少し速い）。短くするには、画素数かフレーム数を減らすしかない。
- この端末の GPU の予算（9.17GB）は、他のアプリ（dwm など約 1GB）と ROCm のランタイムに削られる。ComfyUI の起動直後は umT5 を外した後の空きが 6.9GB、2 本目以降は 0.2〜4.5GB（LLM を通した実行では 0.2GB）で、エキスパートの大半が CPU 側に置かれる（ComfyUI の推定に基づく部分読み込み。ROCm 7.2 では動的 VRAM が使えない）。それでも出力は壊れなかった。
- サンプリング中は空き RAM がほぼ 0 になり、Windows のページファイルが広がる（システム管理のページファイルが 13.5GB まで伸びた）。生成中は他のアプリを閉じたほうがよい（usage と troubleshooting に書いた）。

### 11.3 決めた既定値

| 項目 | 値 | 理由 |
|---|---|---|
| `DEFAULT_VIDEO_MODEL` | `wan22-i2v-a14b-q3`（カタログの先頭） | Q4_K_M もこの端末で動き速さも同じだが、RAM を約 2.4GB 多く使い、ケモノ向け LoRA を当てた組み合わせを確かめたのは Q3_K_M。メモリの余裕を優先した。Q4_K_M は「画質優先」としてピッカーで選べる |
| ケモノ向け LoRA | Furry Enhancer V3 を High と Low に強度 1.0（カタログの値のまま） | #4 で黒くならず、ケモノの特徴がよく残った |
| `VIDEO_MAX_PIXELS` | 307200（832×1216 の画像から 448×656） | 1 本（3 秒）が 25 分前後に収まる。480p の面積（399,360）に近く、これより下げると Wan の画質が落ちる |
| `VIDEO_SECONDS` / `VIDEO_MAX_SECONDS` | 3 / 5 | 3 秒で 25〜30 分（LLM を含む）。5 秒は 1 ステップが約 2 倍になり、50 分前後の見込み（1 ステップは `AGENT_IDLE_TIMEOUT_S` の 1200 秒に収まる） |
| `VIDEO_VAE_TILE` / `VIDEO_VAE_TEMPORAL` | 256 / 16 | デコードは 73〜87 秒で、メモリ不足は起きなかった |
| `VIDEO_TEXT_ENCODER_DEVICE` | `default` | GPU で 12〜30 秒。CPU に置いても共有メモリでは物理メモリは減らない |

### 11.4 設計からの変更

- `VIDEO_INTERPOLATE`（RIFE のフレーム補間、任意の機能）は見送った。補間モデルの取得が増え、この端末では 1 本が長いため（作業指示書の「やること」にも入っていない）。
- D8 の「使わなかった量子化を消す」: Q4_K_M もこの端末で動き、ピッカーで選べる候補になったため消さなかった（`tools/comfyui/models/diffusion_models` の 2 ファイル、18.4GB）。要らなければ消してよい（一覧で「使えない」と表示されるだけ）。
- Q4_K_M の位置づけを「GPU のメモリが 10GB を超える環境向け」から「画質優先（この端末でも動くが RAM を多く使う）」にした。
- `langgraph dev` は既定で同時に 1 つの実行しか処理しない（`n_jobs_per_worker` = 1）。動画の生成中に送った別の要求は、LangGraph の中で `pending` のまま待つ（既存の動作。画像タブと同じ）。
- 作業中、Windows の Redirection Guard で ComfyUI が `custom_nodes\furry_ja` のジャンクションをたどれなかった。ジャンクションを作り直して解消し、troubleshooting に書いた。
- モデルの取得: Hugging Face の xet が止まったため、作業では範囲を分けた並列取得で SHA-256 を照合した（`setup-video-models.ps1` は `hf download` のまま）。

### 11.5 確認した動作

2026-11-18、この端末で ComfyUI・LLM のルータ・LangGraph・agent-chat-ui・Tor を起動して確かめた。

- Web 画面から（受け入れ基準 1〜3、5、6）: この端末のヘッドレス Edge で LAN のアドレス（`http://192.168.11.41:3000`、LangGraph は `:2024`）の動画タブを開き、画像タブで作った 832×1216 のケモノの画像 1 枚と「しっぽを振りながら、こちらを向いて笑う」を、既定の長さ（3 秒）と既定のモデルで送った。送信から保存まで 32.7 分（27B の動きの説明 4.5 分、umT5 0.7 分、High 12.0 分、Low 12.3 分、デコードと保存 1.9 分）。27B は開始画像を見て JSON を返し（`split mode=json`。種族・毛色・耳・尾を書き、「振り向いて笑い、尾を左右に振る。耳とひげが少し動く。カメラは固定」）、448×656・49 フレーム・16fps の mp4（1.25MB）が ComfyUI の `output/furry_ja/video/` と `outputs/video/<UUID>.mp4`（条件の `.json` つき）に保存された。応答の `<video>` は LAN のアドレスの `/media/video/<UUID>.mp4` から読み込まれ（3.06 秒、448×656、エラーなし）、「… を保存」のリンクは `?download=1`。PC の幅と 390px の幅のどちらでも横にはみ出さない。キャラクターは振り向いて笑い、マズル・耳・尾・毛並みは最後まで保たれた。
- 1 モデルずつ（受け入れ基準 3）: `logs/furry_agent.log` に、各ローダが前の段の後にルータが空であることを確かめて読み込んだこと（`LLM router unloaded=[True]`）と、各 release の時点でモデルが 1 つも残っていないこと（`alive_before=[]`、`diffusion_before=[]`）が残った。
- 2 本目（受け入れ基準 6、7）: 1 本目の生成中に、正方形（1024×1024）の画像を指示なし・2 秒で API から送った。LangGraph の中で `pending` のまま待ち、1 本目の後に始まった。27B は既定の指示から控えめな動き（耳と尾が少し動き、呼吸とまばたき。カメラ固定）を書き、544×544・33 フレームで 20.6 分（1 ステップ約 200 秒）。
- `.env` の反映（受け入れ基準 12）: `VIDEO_MAX_SECONDS=3`、`VIDEO_MAX_PIXELS=200000` を環境に置いて LangGraph を再起動すると、`GET /models` の長さの選択肢が 2 / 3 秒になり、5 秒の要求は「長さは上限の 3 秒にしました」の注記つきで 3 秒・368×528 になった。元に戻して再起動すると 2 / 3 / 5 秒に戻った。
- 中断（受け入れ基準 11）: 上の要求を 27B の段で取り消すと（LangGraph の取り消し API。UI の停止と同じ）、実行は `interrupted` になり、ComfyUI に中断が届いた。27B の応答が返った時点で ComfyUI は止まり（`execution_interrupted`、出力なし）、動画モデルは読み込まれなかった。ルータの 27B は次の実行の eject まで載ったまま（画像タブと同じ）。
- 拒否（受け入れ基準 7、9）: 画像なし「動かしたい画像を 1 枚添付してください」、2 枚「動画タブで添付できる画像は 1 枚だけです（2 枚添付されています）」、未知の id「動画モデル 'wan99-nope' はホストのモデル一覧にありません」。どれも生成せず、別のモデルに替えない。
- UI: タブ（画像 / チャット / 動画）、動画タブの入力欄の案内、長さの選択（既定の 3 秒が選ばれている）、ピッカー（Q3_K_M が既定、Q4_K_M も使える、LoRA の行）、添付するまで送信できないこと、画像タブのスレッドの「この画像を動画にする」で動画タブに画像が添付された状態で開くこと、390px の幅で横にはみ出さないこと。
- セットアップ: `setup-video-models.ps1` を 2 回（既定のモデルと `-Ids wan22-i2v-a14b-q4`）実行し、どちらも取り直さずに SHA-256 の照合だけで終わった（49 秒、58 秒）。`doctor.ps1` は動画のノード（9 個）と既定の動画モデル（2 エキスパートと LoRA 4 つ）、graph `video` を OK と報告した。
- デグレ確認（同日、動画タブの実装の後）: 画像タブ（既定の SDXL、テキストのみ。eject の確認、`split mode=json`）、Tor 検索（速いモード、主張の突き合わせまで）、cirka（ファイルの作成からコマンドの実行まで）。
- テスト: `uv run pytest` 778 件成功（2 件スキップ）。`build_workflows.py` を流し直しても差分なし。agent-chat-ui の型検査（`tsc --noEmit`）成功。

確かめられなかったこと:

- 別ホストの実機のブラウザとスマートフォン（この端末のヘッドレス Edge から LAN のアドレスで確かめた）。
- 5 秒の動画と、832×480・81 フレーム（1 本 50 分前後の見込み。§11.3）。
- UI の停止ボタンそのもの（LangGraph の取り消し API で確かめた）。動画の生成中にチャットタブ・画像タブ・cirka が ComfyUI のキューを見て待つこと（単体テストのみ）。
- LLM が JSON を壊したときの生文字列の扱い（受け入れ基準 8。単体テストのみ。実機ではどれも JSON だった）、`loras` を空にしたエントリ、LoRA のファイルが無いときの「使えない」表示（単体テストのみ）。
