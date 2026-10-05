"""CLAIM_* and CONTROLLER_* in ChatSettings.from_env (docs/claim-verification-design.md §5.7)."""

from furry_agent.config import ChatSettings


def test_defaults(monkeypatch):
    for name in ("CLAIM_VERIFY", "CLAIM_VERIFY_FAIL_OPEN", "CONTROLLER_MAX_STEPS", "CONTROLLER_WALL_CLOCK_S"):
        monkeypatch.delenv(name, raising=False)
    s = ChatSettings.from_env()
    assert s.claim_verify and not s.claim_fail_open and s.claim_max == 12
    assert s.controller_max_steps == 3 and s.controller_budget_s == s.search_wall_clock_s
    assert not hasattr(s, "local_doc_roots")  # /docs was removed


def test_env_values(monkeypatch):
    monkeypatch.setenv("CLAIM_VERIFY", "0")
    monkeypatch.setenv("CLAIM_VERIFY_FAIL_OPEN", "1")
    monkeypatch.setenv("CLAIM_MAX", "40")
    monkeypatch.setenv("CONTROLLER_MAX_STEPS", "9")
    s = ChatSettings.from_env()
    assert not s.claim_verify and s.claim_fail_open and s.claim_max == 40  # .env may go past the design's 12
    assert s.controller_max_steps == 9


async def test_from_env_does_not_touch_the_disk_on_the_event_loop(monkeypatch):
    # langgraph dev runs nodes under blockbuster; every node reads the settings.
    from blockbuster import blockbuster_ctx

    with blockbuster_ctx():
        assert ChatSettings.from_env().claim_verify in (True, False)
