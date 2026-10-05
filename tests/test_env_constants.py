"""Module constants read from .env (env_int / env_float): defaults unchanged, overrides and clamps applied."""

import json
import os
import subprocess
import sys

from furry_agent.config import ChatSettings, env_float, env_int

PROBE = """
import json
from furry_agent import (bonsai_worker, chat_common, chat_graph, chat_models, claim_nodes, code_nodes, coder_gate,
                         coding, control_nodes, job_lock, media, sandbox, search_agent, search_client, tor_service,
                         write_nodes, writing)
print(json.dumps({
    "page_timeout": bonsai_worker.PAGE_TIMEOUT_S, "tool_rounds": bonsai_worker.MAX_TOOL_ROUNDS,
    "page_chars": bonsai_worker.PAGE_CHARS, "renew": chat_common.RENEW_EVERY_S,
    "history": chat_common.HISTORY_CHARS, "think_reserve": chat_common.THINK_RESERVE,
    "chat_tokens": chat_graph.CHAT_TOKENS, "synth": chat_graph.SYNTH_TOKENS_THINK, "leader_ctx": chat_models.LEADER_CTX,
    "audit": claim_nodes.AUDIT_MAX, "verify": claim_nodes.VERIFY_TOKENS, "code": code_nodes.CODE_TOKENS,
    "coder": coder_gate.DEFAULT_MAX_TOKENS, "fix_err": coding.FIX_STDERR_CHARS,
    "decision": control_nodes.DECISION_TOKENS, "research": control_nodes.RESEARCH_CHARS,
    "lease": job_lock.job_lock.lease_s, "image_bytes": media.MAX_IMAGE_BYTES, "image_side": media.MAX_IMAGE_SIDE,
    "files": sandbox.MAX_FILES, "desktop": sandbox.DESKTOP_START_S, "sandbox_timeout": sandbox.TIMEOUT_S,
    "intents": search_agent.MAX_INTENTS, "redirects": search_client.MAX_REDIRECTS,
    "text": search_client.TEXT_LIMIT, "tor": tor_service.BOOTSTRAP_TIMEOUT_S,
    "outline": write_nodes.OUTLINE_TOKENS, "draft": write_nodes.DRAFT_TOKENS, "edits": writing.MAX_EDITS,
}))
"""


PREFIXES = ("BONSAI_", "CHAT_", "SEARCH_", "CLAIM_", "CODE_", "CODER_", "CONTROLLER_", "JOB_LOCK_", "IMAGE_",
            "SANDBOX_", "TOR_", "WRITE_")


def _probe(**env) -> dict:
    base = {k: v for k, v in os.environ.items() if not k.upper().startswith(PREFIXES)}
    out = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True, check=True, env={**base, **env})
    return json.loads(out.stdout)


def test_defaults_are_unchanged():
    v = _probe()
    assert (v["page_timeout"], v["tool_rounds"], v["page_chars"], v["renew"], v["history"]) == (20.0, 2, 1200, 60.0, 4000)
    assert (v["chat_tokens"], v["synth"], v["leader_ctx"], v["audit"], v["verify"]) == (1536, 1600, 8192, 24, 2000)
    assert (v["code"], v["coder"], v["fix_err"], v["decision"], v["research"]) == (3500, 2048, 1500, 400, 1500)
    assert (v["lease"], v["image_bytes"], v["image_side"], v["files"], v["desktop"]) == (900.0, 10 << 20, 4096, 20, 180)
    assert (v["intents"], v["redirects"], v["text"], v["tor"]) == (3, 3, 4000, 90.0)
    assert (v["outline"], v["draft"], v["edits"], v["think_reserve"]) == (1500, 3000, 8, 256)


def test_env_overrides_the_constants():
    v = _probe(BONSAI_PAGE_TIMEOUT_S="45", SEARCH_MAX_INTENTS="5", WRITE_DRAFT_TOKENS="6000", CLAIM_AUDIT_MAX="80",
               JOB_LOCK_LEASE_S="1800", IMAGE_MAX_MB="25", WRITE_RESEARCH_CHARS="3000", TOR_BOOTSTRAP_TIMEOUT_S="300",
               SANDBOX_TIMEOUT_S="999")
    assert (v["page_timeout"], v["intents"], v["draft"], v["audit"]) == (45.0, 5, 6000, 80)
    assert (v["lease"], v["image_bytes"], v["research"], v["tor"]) == (1800.0, 25 << 20, 3000, 300.0)
    assert v["sandbox_timeout"] == 60  # the container limits stay fixed (AGENTS.md)


def test_clamps(monkeypatch):
    monkeypatch.setenv("X_TEST_INT", "99")
    assert env_int("X_TEST_INT", 1, 1, 7) == 7
    monkeypatch.setenv("X_TEST_INT", "")
    assert env_int("X_TEST_INT", 3, 1, 7) == 3
    monkeypatch.setenv("X_TEST_FLOAT", "0.5")
    assert env_float("X_TEST_FLOAT", 20.0, 1.0) == 1.0
    # SEARCH_MAX_INTENTS / SEARCH_FANOUT_WIDTH stop at 7: readers use BONSAI_BASE_PORT + 0..6.
    monkeypatch.setenv("SEARCH_FANOUT_WIDTH", "50")
    assert ChatSettings.from_env().fanout_width == 7


def test_raised_ranges(monkeypatch):
    for name, value in (("SEARCH_MAX_ROUNDS", "10"), ("SEARCH_MAX_PAGES", "60"), ("CLAIM_MAX", "30"),
                        ("CLAIM_QUOTE_CHARS", "1200"), ("CONTROLLER_MAX_STEPS", "8"), ("SEARCH_MAX_RESULTS", "15"),
                        ("SEARCH_FETCH_PAGES", "4"), ("SEARCH_HITS_PER_INTENT", "10"), ("SEARCH_INTENT_TIMEOUT_S", "200")):
        monkeypatch.setenv(name, value)
    s = ChatSettings.from_env()
    assert (s.search_max_rounds, s.search_max_pages, s.claim_max, s.claim_quote_chars) == (10, 60, 30, 1200)
    assert (s.controller_max_steps, s.search_max_results, s.search_fetch_pages, s.hits_per_intent) == (8, 15, 4, 10)
    assert s.intent_timeout_s == 200.0
    monkeypatch.setenv("SEARCH_INTENT_TIMEOUT_S", "")
    monkeypatch.setenv("SEARCH_TIMEOUT_S", "40")
    assert ChatSettings.from_env().intent_timeout_s == 120.0  # default: 3 x SEARCH_TIMEOUT_S
