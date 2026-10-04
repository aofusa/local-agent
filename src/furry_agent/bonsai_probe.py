"""Probe the local search models on this machine and write tools/bonsai/rank.json (design doc §5.6).

    uv run python -m furry_agent.bonsai_probe [--models id,id] [--tasks task,task]
    (scripts\\probe-bonsai.ps1 wraps this)

Each model in config/search_models.json whose GGUF exists is started once on the PrismML llama-server (Vulkan;
retried on the CPU when the Vulkan start fails) and given one short fixed test per task it is listed for.
Long autonomous work is not measured on purpose: the graph keeps every model call short (one tool round trip,
one JSON object). Recorded per model: boot seconds, memory taken (drop of available physical memory), decode
tokens/s, the -ngl that worked, and pass/fail per task. ``order`` lists, per task, the models that passed in the
catalog's preference order; chat_graph only picks from those. The file is machine-specific and not committed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from furry_agent import search_agent as sa
from furry_agent.bonsai_select import TASKS, Catalog, ModelSpec, available_models, free_memory_mb
from furry_agent.bonsai_worker import OPEN_PAGE_TOOL, LlamaServer, WorkerError, free_port, hits_block
from furry_agent.config import REPO_ROOT, ChatSettings
from furry_agent.llm_client import LLMError, OpenAICompatClient, strip_thinking

HITS = [
    {"url": "https://www.example-news.jp/tech/2025/handheld", "title": "新型携帯ゲーム機、メモリ 24GB で発売",
     "snippet": "新型の携帯ゲーム機は 24GB の LPDDR5X メモリを搭載し、2025 年 10 月 16 日に発売された。"},
    {"url": "https://recipes.example.org/curry", "title": "簡単カレーの作り方",
     "snippet": "玉ねぎを炒めてからルーを入れます。"},
    {"url": "https://www.example-maker.com/product/spec", "title": "製品仕様 | Example Maker",
     "snippet": "Memory: 24GB LPDDR5X-8000. Release date: October 16, 2025."},
]
PAGE = ("製品仕様 Example Maker。Memory: 24GB LPDDR5X-8000. Release date: October 16, 2025. "
        "Storage: 1TB. 本機は 2025 年 10 月 16 日に発売されました。")
QUESTION = "新型携帯ゲーム機のメモリ容量と発売日は？"


def _prompt(name: str) -> str:
    return (REPO_ROOT / "prompts" / name).read_text(encoding="utf-8").strip()


def _japanese(text: str) -> bool:
    return bool(re.search(r"[぀-ヿ一-鿿]", text))


async def _route(client: OpenAICompatClient) -> bool:
    system = {"role": "system", "content": _prompt("system_search_route.txt")}
    yes = await sa.ask_json(client, [system, {"role": "user", "content": "今日の東京の天気と最新ニュースは？"}],
                            sa.RouteDecision, max_tokens=80, temperature=0.0)
    no = await sa.ask_json(client, [system, {"role": "user", "content": "こんにちは、元気？"}],
                           sa.RouteDecision, max_tokens=80, temperature=0.0)
    return bool(yes and yes.kind == "SEARCH" and yes.query and no and no.kind != "SEARCH")


async def _plan(client: OpenAICompatClient) -> bool:
    plan = await sa.ask_json(client, [{"role": "system", "content": _prompt("system_search_plan.txt")},
                                      {"role": "user", "content": QUESTION}], sa.Plan, max_tokens=300)
    return bool(plan and 1 <= len(plan.intents) <= 4 and all(len(i.q) <= 120 for i in plan.intents))


async def _filter(client: OpenAICompatClient) -> bool:
    hits = [dict(h, intent_id=0) for h in HITS]
    result = await sa.ask_json(client, [{"role": "system", "content": _prompt("system_search_filter.txt")},
                                        {"role": "user", "content": sa.filter_input(QUESTION, hits)}],
                               sa.Relevance, max_tokens=60, temperature=0.0)
    return bool(result and 2 not in result.relevant and {1, 3} & set(result.relevant))


async def _worker(client: OpenAICompatClient) -> tuple[bool, bool]:
    """(tool_ok, cards_ok): a real open_page call for a listed URL, then valid cards with a verbatim quote."""
    messages = [{"role": "system", "content": _prompt("system_bonsai_worker.txt")},
                {"role": "user", "content": f"質問: {QUESTION}\n調べる意図: 仕様と発売日\n\n検索結果:\n{hits_block(HITS)}\n\n"
                                            "質問に答えるのに最も役立つ結果を open_page で開いてください（最大 2 ページ）。"}]
    reply = await client.chat(messages, tools=[OPEN_PAGE_TOOL], tool_choice="auto", max_tokens=96, temperature=0.1)
    urls = {h["url"] for h in HITS}
    call = next((c for c in reply.tool_calls if c.name == "open_page"), None)
    tool_ok = bool(call and call.arguments.get("url") in urls and call.arguments.get("url") != HITS[1]["url"])
    url = call.arguments.get("url") if tool_ok else HITS[2]["url"]
    messages += [{"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {
                     "name": "open_page", "arguments": json.dumps({"url": url})}}]},
                 {"role": "tool", "tool_call_id": "c1", "content": f"```text\n{PAGE}\n```"},
                 {"role": "user", "content": "開いたページと検索結果の抜粋から、質問に関係する事実・数値・日付・反証だけを URL ごとに抜き出してください。"}]
    cards = await sa.ask_json(client, messages, sa.Cards, max_tokens=500, temperature=0.1)
    found = sa.card_dicts(cards, urls)
    sources = {h["url"]: h["title"] + " " + h["snippet"] for h in HITS}
    sources[url] = sources.get(url, "") + " " + PAGE
    verified = sa.verify_cards(found, sources)
    cards_ok = any(c.get("quote_ok") for card in verified for c in card["claims"]) and \
        all(_japanese(c["claim"]) for card in verified for c in card["claims"])
    return tool_ok, cards_ok


CARDS = [{"id": "0.1", "url": HITS[2]["url"], "claims": [{"claim": "メモリは 24GB LPDDR5X", "quote_ok": True},
                                            {"claim": "2025 年 10 月 16 日発売", "quote_ok": True}]}]
REFS = [{"n": 1, "url": HITS[2]["url"], "title": HITS[2]["title"]}]


async def _critique(client: OpenAICompatClient) -> bool:
    """Think mode's critic: the spec is answered by card 0.1, the price is not (no card), so it stays open
    and gets a next intent."""
    search = {"goal": QUESTION + " 価格も知りたい。", "intents": [{"tool": "web", "q": "handheld spec", "round": 0}],
              "subquestions": [{"id": "q1", "question": "仕様（メモリ）と発売日は？", "status": "open"},
                               {"id": "q2", "question": "価格はいくらか？", "status": "open"}]}
    result = await sa.ask_json(client, [{"role": "system", "content": _prompt("system_search_critique.txt")},
                                        {"role": "user", "content": sa.reflect_input(search, CARDS, REFS)}],
                               sa.Reflect, max_tokens=500)
    if result is None:
        return False
    out = sa.apply_reflect(search, result, CARDS, 1, 3)
    return "q2" in out["open"] and len(result.next_intents) <= 3


async def _synthesize(client: OpenAICompatClient) -> bool:
    reply = await client.chat([{"role": "system", "content": _prompt("system_search.txt")},
                               {"role": "user", "content": sa.leader_input(QUESTION, CARDS, REFS)}],
                              max_tokens=500, temperature=0.3)
    text = strip_thinking(reply.content)
    return _japanese(text) and "[1]" in text and "24" in text and "<think" not in reply.content


async def probe_model(settings: ChatSettings, spec: ModelSpec, path: Path, tasks: list[str], log=print) -> dict:
    entry: dict = {"label": spec.label, "file": spec.file, "tasks": {}}
    for ngl in (99, 0):
        before = free_memory_mb()
        server = LlamaServer(settings.llama_server, path, free_port(settings.base_port + 20),
                             max(settings.ctx, 8192) if spec.large else settings.ctx, ngl, spec.label, settings.logs_dir)
        try:
            entry["boot_s"] = round(await server.start(600 if spec.large else 180), 1)
            entry["mem_mb"] = max(1, before - free_memory_mb())
            entry["ngl"] = ngl
            client = server.client(300)
            speed = await client.chat([{"role": "user", "content": "1 から 20 まで数字を並べて。"}], max_tokens=64,
                                      temperature=0.0)
            entry["tok_s"] = round(speed.tokens_per_s or 0, 1)
            for task in tasks:
                started = time.monotonic()
                try:
                    if task == "worker":
                        entry["tool_ok"], ok = await _worker(client)
                        ok = ok and entry["tool_ok"]
                    else:
                        ok = await {"route": _route, "plan": _plan, "filter": _filter, "critique": _critique,
                                    "synthesize": _synthesize}[task](client)
                except LLMError as exc:
                    ok = False
                    entry.setdefault("errors", {})[task] = str(exc)[:200]
                entry["tasks"][task] = bool(ok)
                log(f"    {task:<10} {'OK' if ok else 'NG'}  {time.monotonic() - started:5.1f}s")
            return entry
        except WorkerError as exc:
            entry["error"] = str(exc)
            log(f"    -ngl {ngl}: {exc}")
        finally:
            await server.stop()
    return entry


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--models", default="", help="comma separated ids (default: every downloaded model)")
    parser.add_argument("--tasks", default="", help=f"comma separated tasks ({', '.join(TASKS)})")
    args = parser.parse_args(argv)
    settings = ChatSettings.from_env()
    if not settings.llama_server or not Path(settings.llama_server).is_file():
        print("BONSAI_LLAMA_SERVER がありません。scripts\\setup-llamacpp.ps1 を実行してください", file=sys.stderr)
        return 1
    catalog = Catalog.load(settings.catalog_path)
    available = available_models(catalog, settings.models_dir)
    wanted = [m for m in (args.models.split(",") if args.models else catalog.models) if m in available]
    only = set(args.tasks.split(",")) if args.tasks else set(TASKS)
    previous = {}
    if settings.rank_path.exists():
        previous = json.loads(settings.rank_path.read_text(encoding="utf-8")).get("models") or {}
    results = dict(previous)
    for model_id in wanted:
        spec = catalog.models[model_id]
        tasks = [t for t in TASKS if model_id in catalog.tasks.get(t, []) and t in only]
        if not tasks:
            continue
        print(f"==> {spec.label}  ({', '.join(tasks)})", flush=True)
        entry = await probe_model(settings, spec, available[model_id], tasks, lambda s: print(s, flush=True))
        merged = dict(previous.get(model_id) or {})
        merged.update({k: v for k, v in entry.items() if k != "tasks"})
        merged["tasks"] = {**(merged.get("tasks") or {}), **entry["tasks"]}
        results[model_id] = merged
        print(f"    boot {entry.get('boot_s')}s  mem {entry.get('mem_mb')} MB  {entry.get('tok_s')} tok/s  "
              f"ngl {entry.get('ngl')}", flush=True)
    order = {task: [m for m in catalog.tasks.get(task, []) if (results.get(m) or {}).get("tasks", {}).get(task)]
             for task in TASKS}
    rank = {"generated": datetime.now().isoformat(timespec="seconds"), "llama_server": settings.llama_server,
            "models": results, "order": order}
    settings.rank_path.parent.mkdir(parents=True, exist_ok=True)
    settings.rank_path.write_text(json.dumps(rank, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{settings.rank_path}")
    for task in TASKS:
        print(f"  {task:<10} {' > '.join(order[task]) or '（合格なし）'}")
    return 0


def _load_dotenv() -> None:
    env = REPO_ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"'))


if __name__ == "__main__":
    _load_dotenv()
    sys.exit(asyncio.run(main()))
