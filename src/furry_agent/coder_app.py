"""Entry point for langgraph.json ``http.app``: LangGraph loads this file by path (not as a package module), so the
gate itself lives in furry_agent.coder_gate and is imported from the package here."""

from furry_agent.coder_gate import app

__all__ = ["app"]
