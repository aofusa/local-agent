# 画像から動画（I2V）作業指示書

対象: local-agent に、画像 1 枚と日本語の指示から短い動画を作る「動画タブ」を足す。
読者: 実装を担当する AI、または実装者。
方針: 設計は [image-to-video-design.md](image-to-video-design.md) に従う。画像タブ・チャットタブ・cirka の経路と契約は変えない。設計書 §10 の決めること（D1〜D8）が決まり、フェーズ 0 の計測が終わるまで、本実装（フェーズ 1 以降）に入らない。

作成日: 2026-11-18
ブランチ: `feat/image-to-video`（`main` の `fcc9787` から）
予定の版: v0.14.0
ステータス: 利用者の確認待ち

---

## 1. 目的

他ホストのブラウザの動画タブから、画像 1 枚と日本語の指示を送ると、この端末（ROG Xbox Ally X）の ComfyUI が Wan 2.2 I2V で短い動画（mp4）を作り、UI で再生できる。生成中に大きなモデルが同時に載ることはなく、この端末に合わせた既定値を `.env` で環境ごとに変えられる。

## 2. 範囲

### 2.1 やること

- 動画タブ（graph `video`）と、その ComfyUI ワークフロー（Wan 2.2 I2V A14B の GGUF と lightx2v の 4 ステップ LoRA）
- 段ごとの読み込みと unload（27B → umT5 → High → Low）を固定する ComfyUI のノード
- `config/host_models.json` の `video` と、`GET /models` の `video`
- 環境ごとの調整値（`.env` の `VIDEO_*`）
- セットアップ（ComfyUI-GGUF とモデルの取得、SHA-256 の照合）と、`doctor.ps1` の確認
- agent-chat-ui の動画タブ（添付 1 枚、動画の表示、モデルのピッカー。D5 で決まれば、長さの選択と「この画像を動画にする」）
- 仕様と文書の改定（D1、D2 で決まった内容）

### 2.2 やらないこと

テキストだけからの動画、動画の入力（VHS）、最初と最後のフレームの指定、音声、5 秒を超える延長、アップスケール、cirka のツール、macOS のスクリプト、チャットタブ・画像タブの変更（文での案内を含む）、認証の追加、クラウド API。

## 3. 事前確認（2026-11-18、済み）

- 端末: Radeon 890M（gfx1150）。ComfyUI から見える GPU メモリは 9.17GB、RAM は 23.1GB、ディスクの空きは 92GB（設計書 §2）。
- ComfyUI v0.38.0-32 には、動画の標準ノード（WanImageToVideo、KSamplerAdvanced、ModelSamplingSD3、LoraLoaderModelOnly、VAEDecodeTiled、CreateVideo、SaveVideo、FrameInterpolate）がある。ComfyUI-GGUF と VHS は無い。
- 候補のファイルと大きさ（Hugging Face で確認）: High / Low の GGUF は 1 本あたり Q4_K_M 9.65GB、Q4_K_S 8.75GB、Q3_K_M 7.18GB。lightx2v の I2V 用 LoRA は `…_4step_1022`（High 635MB、Low 739MB）。
- agent-chat-ui: タブは graph id そのもの（`?assistantId=`）。AI メッセージの `type: "video"` のブロックは、今は何も表示されない。
- 作業ブランチ `feat/presence-turn`（未マージ、別プロジェクトで開発中）も `src/furry_agent/coder_app.py`、`config/host_models.json`、`CHANGELOG.md`、`docs/decisions.md` を変えている。後から `main` に入れる側で小さな衝突が出る。

## 4. 作業の手順

各フェーズの完了条件を満たしてから次へ進む。コミットは英語で、本文に理由を書く（AGENTS.md）。

### フェーズ 0: 事前計測（リポジトリの成果物は変えない）

スクリプトと結果は `artifacts/video/` に置く。

1. ComfyUI-GGUF を `tools/comfyui/custom_nodes` にコミットを固定して入れ、ComfyUI を再起動してノード一覧に出ることを確かめる。
2. モデルを Hugging Face のキャッシュ経由で取得し、`tools/comfyui/models` にリンクする（High / Low の Q4_K_M と Q3_K_M、umT5 fp8、Wan 2.1 VAE、lightx2v の LoRA。約 42GB）。
3. 設計書 §5 の順序（LLM → eject → umT5 → release → High → release → Low → release → タイルデコード → SaveVideo）の API ワークフローを、スクリプトから ComfyUI に投入する。
4. 次の条件で測る。

   | 量子化 | 大きさ × フレーム数 | VAE |
   |---|---|---|
   | Q3_K_M / Q4_K_M | 448×656 × 49（縦長、約 3 秒） | タイルあり（256 / 16） |
   | Q3_K_M / Q4_K_M | 832×480 × 81（モデルの既定、約 5 秒） | タイルあり / なし |

   記録する値: 各段の時間（LLM、umT5、High、Low、デコード、保存）、1 ステップの秒数、全体の時間、各段の RAM と GPU メモリの最大値、部分読み込みかどうか（ComfyUI のログ）、出力が壊れていないか、見た目の品質（同じ画像と seed で比べる）。
5. 結果を設計書 §11 に書き、既定値（`DEFAULT_VIDEO_MODEL`、`VIDEO_MAX_PIXELS`、`VIDEO_SECONDS`、`VIDEO_MAX_SECONDS`、タイルの値）を決めて利用者に報告する。

完了条件: 少なくとも 448×656 × 49 の 2 つの量子化が最後まで動き、既定値を決める根拠がそろう。A14B が実用にならない（出力が壊れる、1 本が 1 時間を超える、など）ときは、そこで止めて利用者に相談する（Wan 2.2 TI2V-5B への切り替えなど）。

### フェーズ 1: 仕様の改定（D1、D2 の決定に従う）

- AGENTS.md: 目的（動画タブ）、構成の図、固定の規則（`job_lock` に動画タブ、1 モデルずつの順序）、禁止（動画生成と GGUF の文言）、リポジトリの表。
- `docs/specification.md`: プロセスの分担、待受の表（D3 が B なら `/media/video`）、動画の契約（ノード ID、順序、JSON 契約）、UI の変更範囲、受け入れ条件。
- `docs/llm-comfyui-workflow-design.md`: 「動画生成ワークフローを作らない」は画像のワークフローの話で、動画は `image-to-video-design.md` に従う、と注記する。

完了条件: 改定した文言を利用者が確認している。

### フェーズ 2: ComfyUI 側

- `comfyui_nodes/furry_ja` に新しいノード 4 つ（設計書 §5.3）。
- `scripts/build_workflows.py` に動画の系統 `wan22`（テンプレート `i2v` とマップ）。`prompts/system_video_i2v.txt` と `prompts/system_vision_video.txt`。
- `uv run python scripts/build_workflows.py` で生成し、既存のテンプレートに差分が無いことを確かめる。
- テスト: ノード ID、依存の順序（設計書 §5.2）、スロット、新しいノードの素通しと unload（ComfyUI を立てない単体テスト）。

完了条件: `uv run pytest` が通り、既存の `workflows/` が変わっていない。

### フェーズ 3: LangGraph 側

- `model_catalog.py`（`video` の読み込みと検証、`downloads` の id のキー）、`models_api.py`（`video`、`defaults.video`）。
- `video_plan.py`（大きさ、長さ、seed）、`video_graph.py`、`langgraph.json` の `video`、`job_lock` の `video`。
- 動画の返し方（D3）。B なら `GET /media/video/{name}`（UUID の形の名前だけを受け付ける）。
- テスト: 偽の ComfyClient で ingest から await_video まで。拒否（画像 0 枚、2 枚以上、未知・使えない id）、ロックの待ち合わせ、中断、カタログの検証、`/models`、配信する名前の検証。

完了条件: `uv run pytest` が通り、`graph.py` と `chat_graph.py` に差分が無い。

### フェーズ 4: セットアップと設定

- `scripts/setup-video-models.ps1`（新、UTF-8 BOM 付き）: ComfyUI-GGUF をコミットを固定して入れ、`gguf` を ComfyUI の venv に入れる。モデルは、この端末に既にあればリンクし、無ければ取得して SHA-256 を照合する。
- `setup.ps1` への組み込み（D6）。`doctor.ps1` に動画の確認（ノード、ファイル）。
- `.env.example` に `VIDEO_*`（D4）。
- テスト: スクリプトのテスト（`tests/test_scripts.py` の形）。

完了条件: この端末で `setup-video-models.ps1` を 2 回実行しても安全で、`doctor.ps1` が動画の準備の状態を報告する。

### フェーズ 5: UI

- 動画タブ、添付 1 枚、動画の表示、モデルのピッカー（設計書 §8）。D5 で決まれば、長さの選択と「この画像を動画にする」ボタン。
- タブをまたいだ添付の扱いと、履歴の読み直し（設計書 §8 の注意）を確かめる。

完了条件: UI のビルド（`scripts/start-ui.ps1`）が通り、この端末と他ホストのブラウザで表示と送信ができる。

### フェーズ 6: 実機確認

- 他ホストのブラウザ（PC とスマートフォン）の動画タブから、画像 1 枚と日本語の指示を送り、動画が表示・再生でき、この端末（ComfyUI の output と `outputs/video/`）に保存される。
- ログで順序を確かめる: eject の確認 → umT5 → release → High → release → Low → release → デコード。High と Low のサンプリング中、ルータに何も載っていない。
- 縦長と横長の画像、指示なし、画像なしと 2 枚の拒否、使えないモデル id の拒否、UI からの中断、ほかのタブとの待ち合わせ。
- `.env` の値（画素数、長さ、タイル）を変え、再起動の後に反映される。
- デグレ確認: 画像タブ（SDXL のテキストのみと元画像 1 枚）、Tor 検索（速い）、cirka（ファイルの作成からコマンドの実行まで）。
- 確かめられなかったことを記録する。

### フェーズ 7: 文書と仕上げ

- README（概要に動画タブ、動作環境のディスク）。
- `docs/setup.md`、`usage.md`、`configuration.md`（`VIDEO_*` と環境ごとの目安、カタログの `video`）、`architecture.md`（ファイルとノード）、`models.md`（選定の理由と計測）、`troubleshooting.md`、`third-party-licenses.md`、`decisions.md`。
- `CHANGELOG.md` に v0.14.0。設計書 §11 に実装記録。

## 5. 受け入れ基準

1. 他ホストの agent-chat-ui の動画タブから、画像 1 枚と日本語 1 文を送ると、mp4 が 1 本、UI で再生できる。
2. 同じ動画がこの端末に保存されている（ComfyUI の output と `outputs/video/`。条件は同名の `.json`）。
3. 大きなモデルが同時に載らない: umT5 を読む前にルータの 27B が unload されている。High を読む前に umT5 が、Low を読む前に High が unload されている（ログで確かめる）。同時常駐による OOM や、ページングによる停止が起きたら失敗。
4. 出力は開始画像の縦横比のままで、`VIDEO_MAX_PIXELS` 以下の 16 の倍数。フレーム数は 4k+1、fps はカタログの値。
5. 指示が空でも、既定の動きで生成する。画像が 0 枚・2 枚以上のときは、理由を返して生成しない。
6. LLM が JSON を壊しても、生文字列を positive にして生成まで進む。
7. 未知・使えない動画モデルの id は理由つきで拒否し、別のモデルに替えない。
8. 動画の生成中、画像タブ・チャットタブ・cirka は待つ。その逆も同じ（`job_lock`）。
9. UI で止めると、ComfyUI の実行も止まる。
10. `.env` の `VIDEO_*` を変えると、LangGraph の再起動の後に反映される。
11. ComfyUI と llama.cpp のルータは `127.0.0.1` のまま。
12. 画像タブ、チャットタブ（Tor 検索）、cirka の動作が変わらない（デグレ確認）。`uv run pytest` が通る。

## 6. ロールバック

- `main` に入れる前なら、ブランチを捨てれば元に戻る。
- 入れた後に止めたいとき: `langgraph.json` の `video` と UI のタブを外せば、既存の機能は元のまま動く（`graph.py` と `chat_graph.py` は変えないため）。`tools/comfyui/custom_nodes/ComfyUI-GGUF` と動画のモデルを消せば、ComfyUI も元に戻る。

## 7. 完了報告に書くこと

- フェーズ 0 の計測の結果と、決めた既定値
- 変えたファイルと、仕様の改定の内容
- テストの件数（pytest）
- 実機確認の結果と、確かめられなかったこと
- 残った問題と、次にやるとよいこと
