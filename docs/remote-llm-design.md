# 別ホストの LLM（リモートの llama.cpp、OpenAI 互換サービス）（v0.13.0）

対象: チャットタブ（graph `chat`）と cirka の `POST /coder/turn` が使う推論モデル。
この文書は要件、設計、実装記録をまとめる。モデルの選択そのものは [host-model-selection-design.md](host-model-selection-design.md)、この端末の LLM サーバは [llamacpp-router-design.md](llamacpp-router-design.md) に従う。

## 1. 要件（利用者の指定、2026-10-06）

1. この端末で動かす llama.cpp（ルータ）のほかに、別ホストで動いている llama.cpp や OpenAI 互換のサービスを推論モデルとして使えるようにする。
2. リモートのモデルの情報（接続先の URL、モデル名）は `config/host_models.json` に直接書く。`.env` と `.env.example` は変えない。
3. 既存の画像生成、Tor 検索、cirka のエージェント機能を劣化させない。

変えないもの（AGENTS.md・specification.md）: 画像タブの経路（ComfyUI のワークフローがこの端末のルータでタグを作り、eject で unload してから拡散モデルを読む）、ノード ID と JSON 契約、ComfyUI・ルータ・Tor・検索用 llama-server のループバック待受、`job_lock` での直列化、未知・使えない id を拒否して別のモデルへ替えないこと、クラウド API へのフォールバックをしないこと。

## 2. 設計

### 2.1 カタログ: 推論モデルの `endpoint`

推論モデルの項目に `gguf` の代わりに `endpoint` を書くと、別ホストのモデルになる。

```json
{
  "id": "mac-bonsai-2-27b-abliterated",
  "label": "Bonsai 2 27B abliterated（Mac の llama.cpp）",
  "endpoint": {"kind": "llamacpp", "url": "http://127.0.0.1:18090/v1", "model": "bonsai-2-27b-abliterated"},
  "context": 8192,
  "thinking": "off",
  "vision": false
}
```

| キー | 内容 |
|---|---|
| `kind` | `llamacpp`（llama-server。ルータでも単体でもよい）か `openai`（OpenAI 互換の Chat Completions を話すサービス: vLLM、Ollama、LM Studio など） |
| `url` | ベース URL（`http://` か `https://`。`/chat/completions` と `/models` をこの下に付ける。多くは `/v1` で終わる） |
| `model` | 接続先に送るモデル名（llama.cpp のルータならプリセットの節の名前） |
| `api_key` | 任意。`Authorization: Bearer` に載せる。`GET /models` の応答やログには出さない |

- `gguf` / `mlx` と `endpoint` は同時に書けない（読み込み時に拒否する）。`context`・`thinking`・`vision` はローカルのモデルと同じ意味。
- 別ホストのモデルはこの端末のルータのプリセットに入らない（`scripts/host_models.py preset` が飛ばす）。`setup-llm.sh` が一覧の先頭を既定に使うため、リモートの項目は一覧の後ろに置く。
- 読み込み時に、未知のキー、`kind` の誤り、URL の形、モデル名の欠落を拒否する（黙って外さない）。

### 2.2 呼び出し: `RemoteLLM`

`llm_client.RemoteLLM` は `LlamaRouter` と同じ呼び出し（`chat`、`model_ids`、`reachable`、`loaded`、`unload_all`）を持つ。

- 補完は今までと同じ `POST <url>/chat/completions`（ストリーム、無応答の時間だけで打ち切る）。`api_key` があれば `Authorization: Bearer` に載せる。
- `kind: "openai"` では llama.cpp 固有の `chat_template_kwargs`（思考の切り替え）を送らず、`json_schema` は `strict: false` で頼む（汎用のサービスは未知のフィールドや閉じていないスキーマを拒否するため）。思考の出力（`reasoning_content` / `reasoning` / `<think>`）は今までどおり本文から分ける。
- `loaded()` は常に空、`unload_all()` は何もしない。リモートのモデルはこの端末のメモリを使わないので、この端末の unload の順序（画像タブ、検索の reader）に関わらない。相手のホストが何を載せておくかは相手が決める。

### 2.3 チャットタブと `/coder/turn`

- `chat_common.with_inference` が、選んだ項目が `endpoint` を持てば URL・モデル名・鍵・種類を実行の設定に入れ、`make_llm` がそのクライアントを作る（ローカルなら今までの `LlamaRouter`）。`coder_gate` も同じ関数を使う。
- 実行の始めの確認（`check_router_model`）は、リモートなら接続先の `GET <url>/models` にモデル名があるかを見る。接続できないときは最初の呼び出しが理由を返す。
- 検索: 計画をリモートのモデルが行ったときは、そのモデルを統合役に残す（`mode=resident`、`leader_remote`）。この端末のメモリを使わないので、reader を載せるための unload も代理リーダー（Ternary-Bonsai-2-27B）の起動もしない。フィルタと reader の選択では、リモートのモデルを「この端末に 27B が載っている」とは数えない。
- `job_lock` は今までどおり握る（同時に複数の生成を走らせない規則は、リモートのモデルでも変えない）。

### 2.4 `GET /models`

リモートの項目ごとに接続先の `GET <url>/models` を並列に聞き（4 秒）、使えない理由を返す: 接続できない、応答しない、認証を拒否した（HTTP 401/403）、モデル名が一覧に無い、URL の形が違う。使える項目には `remote: {kind, host, model}` を付け、Web のピッカーは「リモート（llama.cpp）host」を副行に出す。`api_key` は返さない。

### 2.5 画像タブ

画像のタグ生成は、選んだ推論モデルによらずこの端末のルータ（ワークフローの `llm_backend` = `LLM_URL`）のまま。eject はこの端末のルータの unload を確認してから拡散モデルを読む契約で、リモートのモデルに替えるとその確認の意味が変わる（specification.md の固定の順序）。

## 3. 決めたこと

| 項目 | 決め方 | 理由 |
|---|---|---|
| 接続先の書き場所 | `config/host_models.json` の `endpoint` に直接書く（`url`、`model`、任意の `api_key`） | 利用者の指定（実装の途中で `.env` のキーから参照する形を作ったが、`.env` と `.env.example` を変えないよう指示があり直した） |
| `api_key` | カタログに書けるが、`GET /models`、ログ、応答のメタには出さない | 利用者の指定（情報はカタログに書く）。鍵の要るサービスの鍵をコミットしないのは AGENTS.md の規則のままなので、同梱の項目には鍵を書かない |
| 種類 | `llamacpp` と `openai` の 2 つ | llama.cpp は思考の切り替え（`chat_template_kwargs`）と strict な JSON スキーマを受け付ける。汎用のサービスにはそれを送らない |
| unload | リモートには何もしない | この端末のメモリを使わない。他人のホストのモデルを外さない |
| 検索の統合役 | リモートのモデルがそのまま行う | 選んだモデルに計画・批評・統合を寄せる（host-model-selection-design.md §5）。この端末で 27B の代理を起動しないので速く、reader にメモリが回る |
| 画像のタグ生成 | この端末のルータのまま | 2.5 |
| フォールバック | しない | 接続できないときは理由を返す。ローカルのモデルにも別のリモートにも替えない（クラウド API へのフォールバックの禁止と同じ考え方） |

## 4. 実装記録（2026-10-06）

### 4.1 確認の構成

- リモート: macOS の確認機（Apple M4、`192.168.11.53`）の `tools/llm/models.ini`（Ternary-Bonsai-2-27B abliterated の節）で、llama-server のルータを `--host 127.0.0.1 --port 8090` で起動した（作業開始時に Mac で動いていた local-agent は `scripts/stop-all.sh` で止めた）。
- この端末からは SSH のポート転送（`ssh -L 127.0.0.1:18090:127.0.0.1:8090`）で届く。Mac の llama-server を LAN に開く（`--host 0.0.0.0`）操作は作業環境の安全確認で止められたため、相手のホストでもループバックのまま使い、転送もこの端末のループバックだけに開いた。同梱の 2 項目（`mac-bonsai-2-27b-abliterated` = `llamacpp`、`mac-bonsai-2-27b-openai` = `openai`）の URL はこの転送先（`http://127.0.0.1:18090/v1`）。Mac のサーバを LAN に開いたら URL を `http://192.168.11.53:8090/v1` に直すだけで使える。転送が無いあいだは一覧で「接続先に接続できません」と出る。
- API キー: 認証の付いた接続も確かめるため、最初は Mac の llama-server を `--api-key` 付きで起動し、鍵なしの要求が 401 になること、鍵ありで一覧・補完が通ることを確かめた。カタログに鍵を書いてコミットしないよう、その後は鍵なしで起動し直した（Mac 側もループバックだけで待ち受ける）。

確認の後（2026-10-07、利用者の指示）、Mac の llama-server と SSH のポート転送は止めた。同梱の 2 項目はそのため一覧で「接続先に接続できません」と出る（転送とサーバを起こせば、そのまま使える）。

### 4.2 確認した動作

| 確認 | 結果 |
|---|---|
| `GET /models` | 4 つの推論モデルがすべて使える。リモートの 2 つは `remote`（kind、host、model）付き。転送を止めると理由つきで使えない |
| チャット（`llamacpp`、速い） | Mac の Bonsai が答えた。この端末のルータには何も載らない |
| `/coder/turn`（`openai`、tool calling） | `list_files` の tool call（引数 `{"path":"."}`）を返した。`chat_template_kwargs` は送らない |
| Tor 検索（`llamacpp`、速い） | 計画と統合を Mac の Bonsai が行い（`mode=resident`）、代理リーダーは起動せず、reader はこの端末の Ternary-Bonsai-8B。主張の突き合わせまで終わった |
| Tor 検索（`openai`、思考） | 4 ラウンド、14 ページ、参照 8 件、主張の検証と監査まで終わった（約 35 分。統合と検証の呼び出しはリモートで、この端末のメモリは reader に回った） |

## 5. 制約

- 認証は鍵の受け渡し（`api_key`）だけで、LangGraph 側の待受の認証は未確定のまま（AGENTS.md）。
- リモートのモデルの文脈の大きさはカタログの `context` を信じる。接続先が小さい文脈で動いていると、長い会話は接続先が拒否する（エラーが応答に出る）。
- 画像入力（`vision`）はリモートでも項目の値に従うが、チャットタブは画像を受け取らないため今は使われない。
