# presence ターン（vrc-pilot 用）

vrc-pilot（VRChat デスクトップの同伴エージェント、別リポジトリ）のプランナが呼ぶ推論口。正本の設計は vrc-pilot の `docs/04-inference-and-local-agent.md`。ここにはこのリポジトリで決めたことだけを書く。

## 何を足したか

| ファイル | 内容 |
| --- | --- |
| `src/furry_agent/presence.py` | `POST /presence/turn` と `GET /presence/health`。契約の検査、モデルの呼び出し、書き直しの依頼 |
| `src/furry_agent/coder_app.py` | 上の 2 つのルートを `/coder/turn` と同じ http.app に登録（`app.router.routes.extend`） |
| `prompts/presence_system.txt` | システムプロンプト。vrc-pilot の `docs/prompts/planner_system.txt` の写し（正本は向こう） |
| `config/host_models.json` | `presence-qwen3.5-4b`（ゲーミングノートの llama.cpp）と `presence-gemma4-e4b`（Mac の mlx-vlm、チャットのみの予備） |
| `tests/test_presence.py` | 固定のモックで say が返る、145 文字は切らずに 422、move の方針違反は 422、ツール呼び出しは noop、4 秒で 504 |

画像グラフ（`graph.py`）、チャット・検索グラフ（`chat_graph.py`）、`langgraph.json` のグラフ ID、`/coder/turn` は変えていない。

## 契約

```
POST /presence/turn
{"world": {...}, "persona": {...}, "policy": {"allow_move": false, "allow_say": true, "max_actions": 2},
 "model": "（任意）config/host_models.json の推論 id"}
→ 200 {"actions": [ActionCommand, ...], "belief": "...", "model": "...", "ms": 412}
→ 400 本文が不正、未知のモデル id
→ 422 {"error": "contract", "errors": [...]}  行動が契約か方針に反する
→ 502 モデルの呼び出しの失敗
→ 504 PRESENCE_TIMEOUT_S（既定 4 秒）以内に返らない
```

- `action_id` と `ts` はサーバが埋める。小さいモデルが返す平らな形（`{"kind":"say","text":…}`）は `say` オブジェクトへ直す。
- `say.text` が 144 文字を超えたら、モデルに 1 回だけ書き直させる。それでも超えたら 422。切り詰めない。
- `policy.allow_move` が false の `move` / `look`、`policy.allow_say` が false の `say`、`max_actions` を超える行動、1 ターン 2 つの `say` は 422。
- JSON でない応答には 1 回だけ「JSON だけで書き直せ」と頼む。
- モデルがツール呼び出しを返したら捨てて `noop`。presence にツールは無い。

## 決めたこと

- **job_lock を取らない。** 4 秒の予算で画像生成の終わりを待てない。presence のモデルは別の推論モデル（多くは別ホスト）で、ルータの 27B とメモリを取り合わない。ルータのモデルを presence に選んだ場合は、画像生成と同時に動くことがありうる（その組み合わせは既定にしない）。
- **モデルは `PRESENCE_MODEL`（推論 id）で選ぶ。** 無ければホストの既定。vrc-pilot の既定は `presence-qwen3.5-4b`。ゲーミングノート（RTX 3060 6GB）の llama.cpp で Qwen3.5-4B Q4_K_M を動かし、Ally から `ssh -L 18081:127.0.0.1:18081` で届ける（ノートのファイアウォールを開けず、常駐の登録もしない）。思考は切る。温かい状態で 1 ターン約 0.4 秒。
- **予備は Gemma 4 E4B のテキストのみ（`presence-gemma4-e4b`）。** Mac の mlx-vlm（画像認識と同じサーバ）へ `ssh -L 18093:192.168.11.53:8093` で届ける。移動は出させない（vrc-pilot 側の `chat_only`）。
- **スキーマ検証はライブラリを足さずに書いた。** vrc-pilot の `action_command.schema.json` と同じ規則を `presence.action_errors` に持つ。契約を変えるときは両方を直す。
