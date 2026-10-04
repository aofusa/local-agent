"""CLAIM_* and DOC_* in ChatSettings.from_env (docs/claim-verification-design.md §5.7, local-doc §5.2)."""

from furry_agent.config import ChatSettings


def test_defaults(monkeypatch):
    for name in ("CLAIM_VERIFY", "CLAIM_VERIFY_FAIL_OPEN", "LOCAL_DOC_ROOTS", "DOC_EXTENSIONS", "DOC_PLANNER"):
        monkeypatch.delenv(name, raising=False)
    s = ChatSettings.from_env()
    assert s.claim_verify and not s.claim_fail_open and s.claim_max == 12
    assert s.local_doc_roots == () and s.doc_planner == "auto"
    assert s.doc_extensions == (".md", ".txt", ".log", ".json", ".toml", ".yaml", ".yml")
    assert (s.doc_max_files, s.doc_max_depth, s.doc_max_chunks, s.doc_chunk_chars) == (30, 4, 12, 3000)


def test_env_values(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAIM_VERIFY", "0")
    monkeypatch.setenv("CLAIM_VERIFY_FAIL_OPEN", "1")
    monkeypatch.setenv("CLAIM_MAX", "40")
    monkeypatch.setenv("LOCAL_DOC_ROOTS", f"{tmp_path}, not/absolute")
    monkeypatch.setenv("DOC_EXTENSIONS", "md, .TXT")
    monkeypatch.setenv("DOC_MAX_CHUNKS", "99")
    s = ChatSettings.from_env()
    assert not s.claim_verify and s.claim_fail_open and s.claim_max == 12  # capped at the design's 12
    assert s.local_doc_roots == (tmp_path.resolve(),)
    assert s.doc_extensions == (".md", ".txt") and s.doc_max_chunks == 12
