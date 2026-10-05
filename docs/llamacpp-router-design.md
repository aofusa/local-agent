# LM Studio から llama.cpp ルータへ（v0.11.0）

対象: ComfyUI のタグ生成、チャットタブ、cirka の `/coder/turn` が使う 27B の LLM サーバと、ComfyUI 本体の導入、チャットタブのコード実行（Docker）の起動方針。
この文書は要件、設計、実装記録をまとめる。ComfyUI の連携の細部は [llm-comfyui-workflow-design.md](llm-comfyui-workflow-design.md)（旧 `lmstudio-comfyui-workflow-design.md`）に従う。

## 1. 要件（利用者の指定）

1. LM Studio への依存を切り、LLM はすべて llama.cpp で動かす。
2. セットアップスクリプトの中で llama.cpp と ComfyUI も自前で導入し、それを使う。
3. Docker: `docker` コマンドが使えればそれを優先して使う。`docker` が接続できないときは Docker Desktop の起動を試みる（v0.10 までの動作）。`docker` コマンドが無ければ起動は行わない。
4. 既存の SDXL / Chroma の画像生成、Tor 検索、cirka のエージェント機能を劣化させない。

変えないもの（AGENTS.md の制約）: ノード ID、JSON 契約、unload 順（LLM がタグを返す → LLM を unload → チェックポイント → KSampler）、LLM と ComfyUI のループバック待受、27B とチェックポイントを同時に載せないこと、ComfyUI のプロセス内で GGUF を動かさないこと。

## 2. 設計

### 2.1 LLM サーバ: llama-server のルータモード

LM Studio が受け持っていたことは 3 つだった。OpenAI 互換 API で答える、要求が来たらモデルを載せる（JIT）、API で外す（unload）。
llama.cpp の llama-server には、この 3 つを 1 プロセスで行う **ルータモード**（`--models-preset <ini>`）がある。
モデルごとに子プロセスの llama-server を起動し、要求の `model` でその子へ振り分け、`POST /models/unload` で子プロセスを止める。
子プロセスが終わればメモリは OS に戻るので、LM Studio の unload と同じ意味になる。

```
ComfyUI の LM Connect ノード ─┐
チャットタブ（LangGraph）     ├─ OpenAI 互換 API ─▶ llama-server --models-preset tools\llm\models.ini
cirka の /coder/turn          ─┘                      127.0.0.1:8080（ルータ。モデルを持たない）
                                                        │ 要求の model ごとに子プロセス
                                                        ▼
                                                      llama-server（Qwen3.8 27B IQ3_M + mmproj）
FurryJaEjectLLM / ckpt ゲート / チャットタブ ── POST /models/unload, GET /models（状態の確認）
```

| LM Studio（v0.10 まで） | llama.cpp ルータ（v0.11.0） |
|---|---|
| `http://127.0.0.1:1234/v1` | `http://127.0.0.1:8080/v1`（`LLM_URL`。llama-server の既定ポート） |
| モデルキー `huihui-qwen3.8-27b-abliterated@iq3_m`（`LMSTUDIO_MODEL`） | プリセットの節の名前 `qwen3.8-27b-abliterated`（`LLM_MODEL`）。量子化を変えても名前は変わらない |
| JIT ロード | 要求の `model` で子プロセスを起動（`--models-autoload` 既定） |
| `GET /api/v1/models` の `loaded_instances` | `GET /models` の `status.value`（`loaded` / `loading` / `unloading` / `sleeping` を常駐とみなす） |
| `POST /api/v1/models/unload {"instance_id"}` | `POST /models/unload {"model"}`。非同期なので、`GET /models` で常駐が無くなるまで待つ |
| JIT TTL 300 秒（eject の保険） | プリセットの `sleep-idle-seconds = 300`（子が重みを手放す。実測 14GB → 0.2GB） |
| モデル既定値（context 4096、GPU offload 0.45、flash attention、並列 1、thinking off、temperature 0.4） | プリセットの `ctx-size 4096`、`n-gpu-layers`（層数 × 0.45）、`flash-attn on`、`parallel 1`、`reasoning off`、`temp 0.4` |
| 思考の切り替えは `reasoning_effort`（LM Studio は `chat_template_kwargs` を無視した） | `chat_template_kwargs.enable_thinking`。思考は `--reasoning-format deepseek` で `reasoning_content` に分かれる |
| `lms runtime update`、LM Studio の GUI | `scripts/setup-llamacpp.ps1`（PrismML fork の Vulkan 版。検索モデルと同じ build） |

使う llama.cpp は、検索モデル（Bonsai の Q1_0 / PTQ1_0 / PQ2_0）のために既に入れていた PrismML fork の Vulkan 版（`prism-b10754`）にした。
Qwen3.8 27B（`qwen35` アーキテクチャ）と mmproj を読み込め、ルータモードと `llama-quantize` も入っているので、build を 1 つにまとめられる。

### 2.2 ComfyUI の側

- `eject` ノードは LM Connect の `LMConnectEjectLMStudioModel`（LM Studio のネイティブ API を叩く）から、このリポジトリの `FurryJaEjectLLM` に替えた。ノード ID は `eject` のまま。ルータの全モデルを unload し、`GET /models` で常駐が無くなるまで待ってからテキストを通す。
- `ckpt`（`FurryJaCheckpointLoaderAfterEject` / `FurryJaDiffusionLoaderAfterEject`）のゲートも同じ確認をルータに対して行う。入力名は `lmstudio_base_url` から `llm_base_url` に変えた（ノード ID と構造は同じ）。UI に返す印は `llm_unloaded`。
- LM Connect のバックエンドノード（`LMConnectLMStudioBackend`）、`LMConnectPromptWithSystem`、`LMConnectVision` はそのまま使う。中身は OpenAI 互換のストリーミングクライアントで、ルータの URL を指せば動く。auto-eject は LM Studio の API を叩くので off にした（unload は `eject` が行う）。
- ワークフローのテンプレートは `scripts/build_workflows.py` で作り直した（`LLM_URL` / `LLM_MODEL` が変わると `setup-llm.ps1` が作り直す）。

### 2.3 LangGraph の側

- `llm_client.LMStudio` を `LlamaRouter` にした。`reachable()` は `/v1/models`、`loaded()` は `/models` の状態、`unload_all()` はモデルごとに 1 回 unload を頼み、常駐が無くなるまで 1 秒おきに確かめる（既定 60 秒で諦めて `LLMError`）。
- 設定のキーは `LMSTUDIO_URL` / `LMSTUDIO_MODEL` / `LMSTUDIO_CONTEXT` から `LLM_URL` / `LLM_MODEL` / `LLM_CONTEXT` に替えた。`SEARCH_PLANNER=lmstudio` は `llm` と同じ意味で受け付ける。
- `/coder/health` はモデルの状態を `llm` で返す。古い cirka のために同じ値を `lmstudio` でも返す。cirka 0.3.1 は `llm` を読み、無ければ `lmstudio` を読む。
- LM Studio 固有の処理（`reasoning_effort`、「Model is unloaded」の再送）は消した。

### 2.4 セットアップ

| スクリプト | 内容 |
|---|---|
| `setup-llamacpp.ps1` | PrismML fork の Vulkan 版を `tools\llama-prism` に取得（SHA-256 照合）。`BONSAI_LLAMA_SERVER` と `LLM_SERVER` を保存 |
| `setup-llm.ps1`（新規。`setup-lmstudio.ps1` を置き換え） | `config\llm_model.json` に固定した元の GGUF と mmproj を Hugging Face から `tools\models\llm` に取得し SHA-256 を照合（`-SourceModel` で手元のファイルをハードリンク）。`llama-quantize` で IQ3_M に再量子化。GGUF のヘッダ（`scripts\gguf_info.py`）から層数を読み、GPU に載せる層数を決めてプリセット `tools\llm\models.ini` を書く。`LLM_*` を `.env` に保存し、ワークフローを作り直す |
| `start-llm.ps1`（新規） | `llama-server --models-preset ... --models-max 1 --host 127.0.0.1 --port 8080` |
| `setup-comfyui.ps1`（書き直し） | ComfyUI を検証済みのコミット（v0.38.0-32、`e9027f2b`）で `tools\comfyui` に clone し、専用の Python 3.12 venv を作り、GPU に合う PyTorch（Radeon は AMD の ROCm 7.2 Windows 版、NVIDIA は CUDA 12.8、ほかは CPU）と requirements を入れる。LM Connect と furry_ja を `custom_nodes` に入れる。`-ModelsDir` で既存のモデルフォルダを `extra_model_paths.yaml` 経由でそのまま読む。Comfy Desktop の検出と起動引数の書き換えはやめた |
| `setup-comfyui-refs.ps1` / `setup-comfyui-chroma.ps1` | ComfyUI が読むフォルダのどこかに同名のモデルがあれば取得しない |
| `start-all.ps1` / `doctor.ps1` | ルータを起動する / ルータの待受（ループバックのみ）、プリセット、モデルの画像入力、ワークフローの接続先を確認する |

### 2.5 Docker（チャットタブのコード実行）

`sandbox.engine_state()` が `docker version` の結果を 4 つに分ける。

| 状態 | 条件 | 動作 |
|---|---|---|
| READY | `docker` が Linux のエンジンにつながる（Docker Desktop でも Docker Engine でも、別のコンテキストでも） | そのまま使う。Docker Desktop には触れない |
| STOPPED | `docker` はあるが、エンジンにつながらない | 承認後、その実行のためだけに Docker Desktop を起動する（`docker desktop start`。CLI プラグインが無ければ `Docker Desktop.exe`）。CLI プラグインで起動したときだけ、終わったら止める |
| MISSING | `docker` コマンドが無い | 何も起動しない。ファイルを書いて理由を返す |
| WRONG_OS | エンジンが Windows コンテナ | 実行しない。理由を返す |

`setup-sandbox.ps1` も同じ順で、`docker` が無いときは警告して終わる（何も起動しない）。

## 3. 実装記録（2026-10-05、ROG Ally X / Radeon 890M / 共有メモリ 24GB）

- **読み込み方式**: 既定の読み込み（`--load-mode auto`）では、CPU 側の重みが Vulkan の pinned メモリに置かれ、`ErrorOutOfDeviceMemory` で 27B を読めなかった（`-ngl 0` でも同じ）。`load-mode = mmap` にすると読めた。プリセットに入れている。
- **速度**: 27B（IQ3_M、`n-gpu-layers 29`、context 4096）の生成は約 2.0 トークン/秒、プロンプト処理は 7〜13 トークン/秒。LM Studio（0.9〜1.5 トークン/秒）より速い。ロードは約 20 秒。
- **確認した機能**: JSON のタグ生成、`chat_template_kwargs` での思考の切り替え（思考は `reasoning_content` に分かれる）、tool calling（`finish_reason=tool_calls`、引数は JSON）、mmproj による画像入力。
- **unload**: `POST /models/unload` はすぐに `{"success":true}` を返し、子プロセスは数秒後に終わる。そのため unload の後は `GET /models` で状態が `unloaded` になるまで待つ。
- **アイドル sleep**: `sleep-idle-seconds` を過ぎると状態が `sleeping` になり、子プロセスのメモリが 14GB から 0.2GB に減った。`sleeping` も常駐として扱い、unload の対象にする。
- **一覧の余分な項目**: ルータの `/models` は Hugging Face のキャッシュにあるモデルも `unloaded` で並べる。常駐の判定には影響しない。
- **GPU 層数**: Qwen3.8 27B の GGUF は `block_count` 65（うち 1 つは llama.cpp が使わない MTP 層）。64 層 × 0.45 = 29 層を GPU に置く（LM Studio の offload 0.45 と同じ）。
- **ComfyUI**: Comfy Desktop が入れていたものと同じ ComfyUI のコミットと ROCm 版 PyTorch（`2.9.1+rocmsdk20260116`）を `tools\comfyui` に入れた。既存のモデルは `-ModelsDir` で読む。

デグレ確認の結果は [CHANGELOG.md](../CHANGELOG.md) の v0.11.0 に記録する。

## 4. 制約

- ルータは 1 度に 1 モデル（`--models-max 1`）。27B と検索モデルの同時常駐の判定は今までどおりチャットタブ（`bonsai_select`）が行う。
- `LMConnectLMStudioBackend` というノード名は LM Connect 側の名前で、LM Studio は使っていない。
- ComfyUI のモデルの入手（チェックポイント、LoRA、Chroma の拡散モデル）は今までどおり利用者が行う（`-CheckpointUrl` で URL から取得はできる）。
