"""Entry point for langgraph.json ``http.app``: LangGraph loads this file by path (not as a package module), so the
gate itself lives in furry_agent.coder_gate and is imported from the package here. The presence turn for vrc-pilot
(furry_agent.presence) is registered on the same app."""

from furry_agent.coder_gate import app
from furry_agent.presence import routes as presence_routes

app.router.routes.extend(presence_routes)

__all__ = ["app"]
