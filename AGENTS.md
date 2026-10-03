# AGENTS.md

このファイルは、このリポジトリで作業する AI エージェントへの指示である。実装に入る前に、このファイルと `docs/lmstudio-comfyui-workflow-design.md` を読む。

## 現状

リポジトリにある仕様は、このファイルと `docs/lmstudio-comfyui-workflow-design.md` だけである。LangGraph のグラフ、ワークフロー JSON、UI、README はまだ無い。利用者が実装を指示するまで、それらを作り始めない。

## 目的

この端末で起動済みの ComfyUI と LM Studio を使い、同じ端末上の LangGraph エージェントへ、他ホストからプロンプトを送れるようにする。利用者は LangGraph 公式 UI の agent-chat-ui に日本語のプロンプトと、任意で画像または動画を入れる。LangGraph が ComfyUI へその入力を渡し、ComfyUI 上のワークフローが LM Studio の LLM でタグを作り、拡散モデルで静止画を生成する。生成画像はローカルに保存し、同じ画像を agent-chat-ui の応答として表示する。

想定する利用者の操作は次だけである。

1. 他ホストのブラウザで、この端末の agent-chat-ui を開く。
2. 日本語テキストを送る。必要なら画像 0〜2 枚、または動画 1 本を添える。
3. UI 上で進捗のあと、生成された静止画を見る。

## 必読

| 文書 | 役割 |
|---|---|
| このファイル | 入口、UI、他ホストからの到達、画像を UI へ返す責任、作業規則 |
| `docs/lmstudio-comfyui-workflow-design.md` | ComfyUI と LM Studio の連携。実装指示であり、ノード、順序、モデル、メモリ、禁止事項を固定する |

ComfyUI と LM Studio の呼び出し順、ノード ID、プロンプト契約、メモリ上の制約がこのファイルと設計書で食い違う場合は、設計書を優先する。入口、待受、UI、他ホストから画像が見えることに食い違う場合は、このファイルを優先する。どちらにも書かれていない食い違いを見つけたら、実装を進めず利用者に確認する。曖昧な箇所を埋めるために、別の連携方式へ乗り換えない。

設計書のフェーズ 2 に書かれた `frontend/`（単一 HTML と小さな Python）は、このプロジェクトの利用者向け UI ではない。利用者向け UI は agent-chat-ui とする。`frontend/` は、利用者に頼まれない限り作らない。

## 構成

```
他ホストのブラウザ
        │  プロンプト、任意の画像・動画
        ▼
agent-chat-ui（この端末。他ホストから到達できる）
        │
        ▼
LangGraph サーバ（この端末。グラフ id は agent）
        │  ComfyUI HTTP API のみ
        ▼
ComfyUI  http://127.0.0.1:8188
        │  OpenAI 互換 API。ワークフローが呼ぶ
        ▼
LM Studio Local Server  http://127.0.0.1:1234/v1
        │  英語タグの JSON を返す
        ▼
ComfyUI が LLM を unload してから KSampler
        │
        ▼
静止画をローカル保存し、LangGraph が画像の実体を UI へ返す
```

三つのプロセスの分担は固定する。

- LangGraph は、チャット入力の受付、参照メディアの受け取り、ComfyUI への投入、完了待ち、画像の回収、ローカル保存の確認、UI へ返るメッセージの組み立てを行う。
- ComfyUI は、設計書のワークフローで参照画像の取り込み、LM Studio の呼び出し、モデルの eject、タグの分割、チェックポイントによる静止画生成、Save Image を行う。
- LM Studio の LLM は、日本語と参照画像から Danbooru / e621 系タグの JSON を返す。画素は作らない。画素を作るのは ComfyUI のチェックポイントと KSampler である。

LangGraph から LM Studio を直接呼んで、タグ生成や画像生成の経路を置き換えない。LLM のロードと unload の順序は、設計書の ComfyUI グラフが決める。同時に複数の生成を走らせない。前の Queue が終わるまで次を投入しない。

## 待受と到達範囲

この端末は Windows である。設計書の対象機は ROG Ally X 級（共有メモリ 24GB 級）で、LM Studio と ComfyUI は実装開始時点でローカル起動済みである前提とする。このリポジトリは、その二つのアプリのインストーラではない。

| プロセス | 待受 | 誰がアクセスするか |
|---|---|---|
| LM Studio | `127.0.0.1:1234` | この端末の ComfyUI だけ |
| ComfyUI | `127.0.0.1:8188` | この端末の LangGraph だけ |
| LangGraph | 他ホストから到達できるアドレス。開発時の既定ポートは `2024` | agent-chat-ui、および他ホスト |
| agent-chat-ui | 他ホストから到達できるアドレス。開発時の既定ポートは `3000` | 利用者のブラウザ |

ComfyUI は `--listen 127.0.0.1 --port 8188` のままにする。LM Studio もループバックのままにする。他ホストへ開くのは LangGraph と agent-chat-ui だけである。

他ホストのブラウザが UI を開くとき、UI が接続する LangGraph の URL は、そのブラウザから到達できるこの端末のアドレスにする。UI をこの端末で動かしていても、他ホスト向けの接続先を `localhost` のままにすると、相手のブラウザは自分自身へ接続しにいく。グラフ id は `agent` とし、agent-chat-ui の既定 `NEXT_PUBLIC_ASSISTANT_ID` と揃える。

開発サーバは、他ホストから届く待受で起動する。例として LangGraph は `langgraph dev --host 0.0.0.0 --port 2024`、UI もホスト `0.0.0.0` で起動する。認証方式は未確定である。利用者が決めるまで認証を独自に足さない。公開範囲と起動コマンドは、実装時に README へ書く。

LAN に出すのは開発用の到達であり、LangSmith へのクラウドデプロイやクラウド API へのフォールバックを意味しない。

## ComfyUI と LM Studio の契約

詳細、システムプロンプトの要件、失敗時の切り分け、フェーズ順は設計書に従う。実装時にずらしやすい点だけここに置く。

- LLM は LM Studio 上の Qwen3.8 27B abliterated。画像入力にはテキスト GGUF に加え `mmproj` をロードする。API は `http://127.0.0.1:1234/v1`。thinking は無効。
- 画像生成は ComfyUI 上の furry 系チェックポイント。既定ファイル名は `yiffinhell.safetensors`。環境変数 `CKPT_NAME` で差し替える。
- 27B とチェックポイントは同時常駐しない。順序は、LLM がタグを返す、LM Studio から unload する、その後に CheckpointLoader と KSampler、で固定する。eject の前に KSampler へ進めたら失敗である。
- ComfyUI プロセス内に GGUF を載せない。ローカル LLM の実行は LM Studio のサーバだけが行う。
- LLM の出力は次の JSON だけである。説明、Markdown の囲み、思考タグは禁止する。

```json
{"positive":"danbooru tags, short english phrase","negative":"low quality, worst quality, ..."}
```

- 分割に失敗してもリトライしない。生文字列を positive にし、negative は固定の画質タグにして生成まで進む。
- テキストだけのときは Empty Latent、denoise 1.0。参照画像がある img2img の初期 denoise は 0.45。解像度の初期値は 832×1216。KSampler の初期値は steps 28、cfg 5.5、euler ancestral、normal。batch は 1。
- 参照画像は 0〜2 枚。同一性を残す経路（VAE Encode）と、キャプションへ落とす経路（Vision）を同時に使える。Vision の長辺は 768。画像が 0 枚の実行では Vision を走らせず、Load Image の欠損で落とさない。
- 動画は参照フレームの供給源に限る。VHS で 1〜4 フレームを抜き、1 枚目を img2img、残りを Vision へ渡す。動画生成モデルはロードしない。VHS が無い間は動画を対象外にし、その旨を README に書く。
- IP-Adapter はフェーズ 1 の必須にしない。
- ノード ID は設計書 §4.1 のまま固定する。LangGraph が書き換えてよい入力は、設計書の表で「フロントが書き換える入力」とされたもの（日本語指示、参照画像のファイル名、seed、および img2img のとき latent 側）に限る。`llm_backend`、`user_prompt`、`ref_image`、`vision`、`prompt_node`、`eject`、`split`、`ckpt`、`positive`、`negative`、`latent`、`sampler`、`decode`、`save` を別の ID に変えない。
- API 形式ワークフローの投入手順は設計書 §5 に従う。`POST /upload/image`、API JSON の書き換え、`POST /prompt`、WebSocket `/ws` で完了待ち、`GET /history/{prompt_id}`、`/view` で画像を取る。タイムアウトは 10 分。この呼び出しを行うのは LangGraph である。

## 画像の保存と UI への返却

生成のたびに、次の両方を満たす。

1. ComfyUI の Save Image で、この端末のローカルに静止画が残る。
2. 同じ画像が agent-chat-ui の応答として、他ホストのブラウザに表示される。

ComfyUI の `/view` やこの端末のファイルパスは、他ホストのブラウザから開けない。LangGraph がこの端末上で画像のバイトを取得し、採用した版の agent-chat-ui が描画できるメッセージへ載せる。実装時に、その版が画像として描画する content block を確認してから形式を決める。パス文字列や `127.0.0.1:8188` の URL だけを返して完了にしない。

このリポジトリ側の複製は `outputs/` に置く。実行のたびに増える生成画像であり、ソースではない。`outputs/` は git に含めない。エージェントの作業メモを `outputs/` に置かない。

## 置く場所

プログラム、ワークフロー、プロンプト、UI は、このリポジトリの中に作る。ComfyUI 本体と LM Studio 本体は、それぞれの既存インストールのまま使う。カスタムノードの導入先は、ComfyUI インストール配下の `custom_nodes/` である。そのパスは未確定なので、推測で固定しない。発見できないときは利用者に確認する。

実装時の配置は次とする。

```
AGENTS.md
docs/lmstudio-comfyui-workflow-design.md
README.md                         起動順、待受、UMA の注記、既知の対象外
langgraph.json                    graphs.agent がグラフを指す
src/                              LangGraph のグラフと ComfyUI クライアント
workflows/furry_ja.json           UI 形式
workflows/furry_ja_api.json       API 形式。LangGraph が読む
prompts/system_furry_tags.txt
agent-chat-ui/                    公式 UI。設定で接続する
outputs/                          生成画像の複製。git に含めない
artifacts/                        下記。git に含めない
```

`agent-chat-ui/` は公式アプリをこのリポジトリへ置き、環境変数でこの端末の LangGraph へ接続する。在庫の UI が返却画像を表示できないと確認できたときだけ、表示に必要な最小限の変更を加える。グラフ id、待受、公式の導入手順が版で変わった場合は、実装時点の公式クイックスタートに合わせ、結果を README に残す。

設計書 §9 の `frontend/` は作らない。

## artifacts と git

プロジェクトの成果物に直接かかわらない、AI が作業中に作る一時生成物は `artifacts/` に置く。該当するものは、調査メモ、ログ、HTTP のダンプ、試行用スクリプト、不採用の下書き、中間出力である。

成果物は `artifacts/` に置かない。成果物は、このファイル、設計書、README、グラフのソース、`langgraph.json`、ワークフロー JSON、システムプロンプト、UI とその接続設定である。

`artifacts/` は git の管理に含めない。リポジトリの ignore がまだ無い、または `artifacts/` を含んでいない場合は、最初のコミットより前に `.gitignore` へ次を入れる。

```
artifacts/
outputs/
.env
.env.*
!.env.example
```

Python と Node の依存ディレクトリ、キャッシュ、チェックポイント、`mmproj`、その他のモデルウェイトもコミットしない。モデルは LM Studio と ComfyUI の既存の置き場にあるものを使う。

## 実装を指示されたときの順序

設計書 §6 の完了条件を、その順で満たす。呼び出し元は設計書に書かれた薄いフロントではなく LangGraph である。各段が終わるまで次へ進まない。

1. `http://127.0.0.1:1234/v1/models` と `http://127.0.0.1:8188/system_stats` を確認する。応答が無いときは起動手順を README に書き、起動できないことだけを理由にリポジトリ内の実装を放棄しない。起動そのものをエージェントが勝手に広範囲へ変更しない。
2. ComfyUI の既存インストールへ `eedali/LM_Connect` を導入し、再起動後にノード一覧へ出ることを確認する。ローカル GGUF バックエンドは使わない。CUDA 版 llama-cpp-python は入れない。フォールバック条件は設計書 §3.2 に従う。
3. `workflows/furry_ja_api.json` と `workflows/furry_ja.json`、`prompts/system_furry_tags.txt` を作る。
4. LangGraph のグラフを作り、テキストだけで 1 枚生成できることを確認する。ログに、eject が成功したことと、KSampler 開始時に LM Studio のモデルが unloaded であることを残す。
5. 生成画像が `outputs/` と ComfyUI の Save Image の両方に残り、グラフの応答として UI に出ることを確認する。
6. agent-chat-ui をこのリポジトリで起動し、他ホストから日本語 1 文を送って UI 上に静止画が返ることを確認する。
7. 参照画像 1 枚の img2img を確認する。
8. VHS があるときだけ動画フレーム経路を足す。無いときは README に「未導入のため対象外」と書く。

## 受け入れ条件

- 他ホストの agent-chat-ui から日本語 1 文を送り、yiffinhell の静止画が 1 枚、UI に表示される。
- 同じ画像がこの端末のローカルに保存されている。
- 生成された positive がタグ中心で、日本語の主題が落ちていない。
- KSampler の前に LM Studio の 27B が unload されている。同時常駐で OOM になったら失敗。
- 参照画像を渡した実行で、denoise 0.45 のとき元画像の構図が残る。
- 参照が無い実行は Empty Latent を使い、Load Image の欠損で落ちない。
- LLM が JSON を壊しても、生文字列で生成まで進む。
- ComfyUI と LM Studio は `127.0.0.1` のままである。

## 禁止

設計書が採用しないもの、およびこのファイルで入口を固定したことに反するものは作らない。

- LM Studio のチャット GUI や MCP、ImageMCP から ComfyUI を叩く経路。
- LangGraph が LM Studio を直接叩いて、ComfyUI グラフの LLM 呼び出しと eject を置き換えること。
- LLM とチェックポイントの同時常駐。ComfyUI 内での GGUF 常駐。
- クラウド API へのフォールバック。LangSmith クラウドへのデプロイを、このローカル連携の代替にすること。
- 動画生成ワークフロー。Wan、LTX などを含む。
- 設計書 §5 の薄い `frontend/`。
- ComfyUI または LM Studio をループバック以外へ開くこと。
- ノード ID、JSON 契約、unload 順の変更。

## 未確定

次は決めていない。実装中に都合のよい値へ確定しない。必要になったら利用者に確認する。

- ComfyUI と LM Studio のインストールディレクトリ。
- チェックポイントの実ファイル名。接続できるまでは `CKPT_NAME` の既定 `yiffinhell.safetensors` を使う。
- 他ホスト向け待受の認証方式。
- agent-chat-ui の、画像を描画する content block の正確な形。採用する版の実装を見て決める。
- 共有メモリの UMA 割当。実装対象外であり、README に注記するだけにする。

## 作業規則

- 利用者の指示の範囲を超えて、周辺の機能や別アーキテクチャを足さない。
- 設計書に無い連携方式を、調査メモの結論として採用しない。
- 一時ファイル、試行スクリプト、ログは `artifacts/` に置き、成果物のディレクトリへ混ぜない。
- コマンドは PowerShell で動く形にする。
- モデルウェイト、API キー、`.env` の秘密をコミットしない。
- 起動していない外部プロセスを、ドキュメントに書いたコマンドの範囲を超えて修復しない。
- UI を変更した場合は、この端末で表示と送信を確認し、可能なら他ホストから同じ操作を確認する。他ホストから確認できなかった場合は、その旨を結果に書く。
