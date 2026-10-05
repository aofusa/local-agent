from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from furry_agent.families import FLUX, SDXL, canonical_family

REPO_ROOT = Path(__file__).resolve().parents[2]

# Chroma1-HD model files: env name -> template slot. Empty = the value stored in workflows/maps/flux.json.
CHROMA_MODEL_ENV = {
    "CHROMA_UNET_NAME": "ckpt_name",
    "CHROMA_TEXT_ENCODER": "clip_name",
    "CHROMA_VAE": "vae_name",
    "CHROMA_WEIGHT_DTYPE": "weight_dtype",
}


# Chroma sampler defaults: env name -> key of workflows/maps/flux.json "defaults".
CHROMA_DEFAULT_ENV = {"CHROMA_MAX_PIXELS": "max_pixels", "CHROMA_STEPS": "steps"}


# How long the agent waits while nothing at all comes back (no token, no progress event, no output), in seconds.
# Anything that keeps answering is waited for without a limit: a local machine can take long (AGENT_IDLE_TIMEOUT_S).
IDLE_TIMEOUT_DEFAULT_S = 1200.0
IDLE_TIMEOUT_MIN_S = 30.0


def idle_timeout_from_env() -> float:
    raw = os.environ.get("AGENT_IDLE_TIMEOUT_S", "").strip()
    value = float(raw) if raw else IDLE_TIMEOUT_DEFAULT_S
    return max(IDLE_TIMEOUT_MIN_S, value)


@dataclass(frozen=True)
class Settings:
    comfyui_url: str
    ckpt_name: str | None
    workflows_dir: Path
    outputs_dir: Path
    logs_dir: Path
    # Idle timeout of a ComfyUI run: no progress event for this long (AGENT_IDLE_TIMEOUT_S).
    timeout_s: float
    model_family: str = SDXL
    loras: str = ""
    chroma_loras: str = ""
    chroma_models: dict[str, str] = field(default_factory=dict)
    chroma_defaults: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            comfyui_url=os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188"),
            # Empty -> keep the name stored in the workflow templates.
            ckpt_name=os.environ.get("CKPT_NAME") or None,
            workflows_dir=Path(os.environ.get("WORKFLOWS_DIR", REPO_ROOT / "workflows")),
            outputs_dir=Path(os.environ.get("OUTPUTS_DIR", REPO_ROOT / "outputs")),
            logs_dir=Path(os.environ.get("LOGS_DIR", REPO_ROOT / "logs")),
            timeout_s=idle_timeout_from_env(),
            # sdxl (yiffInHell, Danbooru tags; default) or flux (Chroma1-HD, prose). Alias: illustrious.
            model_family=canonical_family(os.environ.get("COMFY_MODEL_FAMILY")) or SDXL,
            # "name[:model_strength[:clip_strength]]", comma separated. Empty = no LoRA.
            loras=os.environ.get("LORAS", ""),
            # SDXL LoRAs do not fit Chroma, so Chroma has its own list.
            chroma_loras=os.environ.get("CHROMA_LORAS", ""),
            chroma_models={slot: os.environ[env] for env, slot in CHROMA_MODEL_ENV.items() if os.environ.get(env)},
            # Machine-specific speed knobs: 1024x1024 x 28 steps takes ~30 min on a Radeon 890M.
            chroma_defaults={key: int(os.environ[env]) for env, key in CHROMA_DEFAULT_ENV.items()
                             if os.environ.get(env, "").strip()},
        )

    def ckpt_for(self, family: str) -> str | None:
        """CKPT_NAME belongs to COMFY_MODEL_FAMILY; CHROMA_UNET_NAME is the Chroma-specific fallback."""
        if family == self.model_family and self.ckpt_name:
            return self.ckpt_name
        if family == FLUX:
            return self.chroma_models.get("ckpt_name")
        return None

    def loras_for(self, family: str) -> str:
        return self.chroma_loras if family == FLUX else self.loras

    def plan_defaults(self, family: str, defaults: dict | None) -> dict:
        """The family map's defaults with the .env overrides (Chroma only)."""
        return {**(defaults or {}), **(self.chroma_defaults if family == FLUX else {})}

    def model_overrides(self, family: str) -> dict[str, str]:
        """Template slot -> value for the text encoder / VAE / weight dtype (Chroma only)."""
        if family != FLUX:
            return {}
        return {k: v for k, v in self.chroma_models.items() if k != "ckpt_name"}


def env_int(name: str, default: int, lo: int | None = None, hi: int | None = None) -> int:
    """An integer from the environment (.env), clamped to [lo, hi]. Empty = the default.

    Module constants use this too (read once at import: langgraph dev loads .env before it imports the graphs).
    """
    raw = os.environ.get(name, "").strip()
    value = int(raw) if raw else default
    if lo is not None:
        value = max(lo, value)
    if hi is not None:
        value = min(hi, value)
    return value


def env_float(name: str, default: float, lo: float | None = None, hi: float | None = None) -> float:
    raw = os.environ.get(name, "").strip()
    value = float(raw) if raw else default
    if lo is not None:
        value = max(lo, value)
    if hi is not None:
        value = min(hi, value)
    return value


_int = env_int


def _float(name: str, default: float) -> float:
    return env_float(name, default)



def _optional_float(name: str) -> float | None:
    raw = os.environ.get(name, "").strip()
    return float(raw) if raw else None


def _path(name: str, default: Path) -> Path:
    raw = os.environ.get(name, "").strip()
    path = Path(raw) if raw else default
    return path if path.is_absolute() else REPO_ROOT / path


@dataclass(frozen=True)
class ChatSettings:
    """Chat tab settings (design doc §5.9). The image tab never reads these."""

    llm_url: str = "http://127.0.0.1:8080/v1"
    llm_model: str = ""
    # Context window of the 27B (llama.cpp router) (scripts/setup-llm.ps1 loads it with 4096: more does not fit).
    llm_ctx: int = 4096
    # No response for this long ends a model call or a wait (AGENT_IDLE_TIMEOUT_S, 20 minutes). Model calls stream,
    # so every token counts as a response; a call that keeps producing tokens is never cut off.
    idle_timeout_s: float = IDLE_TIMEOUT_DEFAULT_S
    history_turns: int = 12
    tor_socks_url: str = "socks5h://127.0.0.1:9050"
    tor_required: bool = True
    tor_exe: str = ""
    tor_autostart: bool = True
    search_max_results: int = 5
    search_fetch_pages: int = 1
    # One HTTP request through Tor (a search page, a result page): a stalled page is skipped, the run goes on.
    search_timeout_s: float = 30.0
    # One search intent (a query tried on the providers in turn; 0 = 3 x SEARCH_TIMEOUT_S).
    search_intent_timeout_s: float = 0.0
    # Optional budget of one reader over all its pages (0 = no limit; its model calls use the idle timeout).
    search_total_timeout_s: float = 0.0
    fanout_width: int = 3
    # Deep search (think mode; docs/chat-deep-search-creative-sandbox.md §3.4). Width stays, rounds grow.
    search_max_rounds: int = 4
    search_max_pages: int = 12
    # Optional budget of a think-mode search before it stops adding rounds (0 = no limit; rounds and pages still
    # bound it).
    search_wall_clock_s: float = 0.0
    hits_per_intent: int = 4
    # Thinking tokens added to max_tokens in think mode.
    think_tokens: int = 3072
    # Code sandbox (§5): docker CLI, the uid:gid inside the container, where runs are kept, how long a run waits
    # for the image tab to finish.
    docker_exe: str = "docker"
    sandbox_user: str = "10001:10001"
    code_dir: Path = REPO_ROOT / "artifacts" / "code"
    sandbox_wait_s: float | None = None
    search_planner: str = "llm"
    search_filter: bool = True
    search_critique: bool = True
    auto_route: bool = True
    # Waiting for the other tab's job (None = AGENT_IDLE_TIMEOUT_S; the holder renews its lease while it works).
    job_lock_timeout_s: float | None = None
    llama_server: str = ""
    models_dir: Path = REPO_ROOT / "tools" / "models"
    model_override: str = ""
    ctx: int = 4096
    reserve_mb: int = 3072
    rank_path: Path = REPO_ROOT / "tools" / "bonsai" / "rank.json"
    catalog_path: Path = REPO_ROOT / "config" / "search_models.json"
    base_port: int = 18181
    logs_dir: Path = REPO_ROOT / "logs"
    prompts_dir: Path = REPO_ROOT / "prompts"
    comfyui_url: str = "http://127.0.0.1:8188"
    # Claim verification (docs/claim-verification-design.md §5.7): extract -> verify -> synthesize -> audit.
    claim_verify: bool = True
    claim_max: int = 12
    claim_quote_chars: int = 400
    # Optional budget of the whole claim check (0 = no limit; its model calls use the idle timeout).
    claim_timeout_s: float = 0.0
    claim_fail_open: bool = False
    # Control loop (docs/autonomous-controller-design.md §10): tools run per compound think request, and an
    # optional wall clock of the whole loop (0 = SEARCH_WALL_CLOCK_S; both 0 = no limit, the steps bound it).
    controller_max_steps: int = 3
    controller_wall_clock_s: float = 0.0

    @property
    def controller_budget_s(self) -> float:
        """The control loop's wall clock (0 = no limit). It never exceeds a search budget when one is set."""
        limits = [x for x in (self.controller_wall_clock_s, self.search_wall_clock_s) if x > 0]
        return min(limits) if limits else 0.0

    @property
    def intent_timeout_s(self) -> float:
        return self.search_intent_timeout_s if self.search_intent_timeout_s > 0 else self.search_timeout_s * 3

    @property
    def lock_wait_s(self) -> float:
        """How long to wait for the other tab's job (JOB_LOCK_TIMEOUT_S, else the idle timeout)."""
        return self.job_lock_timeout_s if self.job_lock_timeout_s is not None else self.idle_timeout_s

    @property
    def sandbox_wait_limit_s(self) -> float:
        return self.sandbox_wait_s if self.sandbox_wait_s is not None else self.idle_timeout_s

    @classmethod
    def from_env(cls) -> "ChatSettings":
        socks = os.environ.get("TOR_SOCKS_URL", "").strip() or "socks5h://127.0.0.1:9050"
        if not socks.startswith("socks5h://"):
            # socks5:// resolves names locally and leaks DNS outside Tor (design doc §5.7).
            raise ValueError(f"TOR_SOCKS_URL は socks5h:// で指定してください（現在 {socks}）")
        planner = os.environ.get("SEARCH_PLANNER", "").strip().lower() or "llm"
        planner = "llm" if planner == "lmstudio" else planner  # the value before v0.11.0
        return cls(
            llm_url=os.environ.get("LLM_URL", "").strip() or "http://127.0.0.1:8080/v1",
            llm_model=os.environ.get("LLM_MODEL", "").strip(),
            llm_ctx=_int("LLM_CONTEXT", 4096, 1024, 262144),
            idle_timeout_s=idle_timeout_from_env(),
            history_turns=_int("CHAT_HISTORY_TURNS", 12, 1, 100),
            tor_socks_url=socks,
            tor_required=os.environ.get("TOR_REQUIRED", "1").strip() != "0",
            tor_exe=os.environ.get("TOR_EXE", "").strip(),
            tor_autostart=os.environ.get("TOR_AUTOSTART", "1").strip() != "0",
            search_max_results=_int("SEARCH_MAX_RESULTS", 5, 1, 30),
            search_fetch_pages=_int("SEARCH_FETCH_PAGES", 1, 0, 10),
            search_timeout_s=_float("SEARCH_TIMEOUT_S", 30.0),
            search_intent_timeout_s=_float("SEARCH_INTENT_TIMEOUT_S", 0.0),
            search_total_timeout_s=_float("SEARCH_TOTAL_TIMEOUT_S", 0.0),
            # Readers use the ports BONSAI_BASE_PORT + 0..6 (7..9 are the router, the filter and the leader).
            fanout_width=_int("SEARCH_FANOUT_WIDTH", 3, 1, 7),
            search_max_rounds=_int("SEARCH_MAX_ROUNDS", 4, 1, 50),
            search_max_pages=_int("SEARCH_MAX_PAGES", 12, 1, 500),
            search_wall_clock_s=_float("SEARCH_WALL_CLOCK_S", 0.0),
            hits_per_intent=_int("SEARCH_HITS_PER_INTENT", 4, 1, 30),
            think_tokens=_int("CHAT_THINK_TOKENS", 3072, 0, 16384),
            docker_exe=os.environ.get("SANDBOX_DOCKER", "").strip() or "docker",
            sandbox_user=os.environ.get("SANDBOX_USER", "").strip() or "10001:10001",
            code_dir=_path("SANDBOX_CODE_DIR", REPO_ROOT / "artifacts" / "code"),
            sandbox_wait_s=_optional_float("SANDBOX_WAIT_S"),
            # llm = the Qwen3.8 27B plans first and is unloaded when the readers do not fit next to it;
            # local = the Ternary-Bonsai-2-27B proxy plans too (the LLM router is not loaded for a search at all).
            search_planner=planner,
            search_filter=os.environ.get("SEARCH_FILTER", "1").strip() != "0",
            search_critique=os.environ.get("SEARCH_CRITIQUE", "1").strip() != "0",
            auto_route=os.environ.get("SEARCH_AUTO_ROUTE", "1").strip() != "0",
            job_lock_timeout_s=_optional_float("JOB_LOCK_TIMEOUT_S"),
            llama_server=os.environ.get("BONSAI_LLAMA_SERVER", "").strip(),
            models_dir=_path("BONSAI_MODELS_DIR", REPO_ROOT / "tools" / "models"),
            model_override=os.environ.get("BONSAI_MODEL", "").strip(),
            ctx=_int("BONSAI_CTX", 4096, 1024, 32768),
            reserve_mb=_int("BONSAI_RESERVE_MB", 3072, 0),
            rank_path=_path("BONSAI_RANK", REPO_ROOT / "tools" / "bonsai" / "rank.json"),
            base_port=_int("BONSAI_BASE_PORT", 18181, 1024, 65000),
            logs_dir=Path(os.environ.get("LOGS_DIR", REPO_ROOT / "logs")),
            comfyui_url=os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188"),
            claim_verify=os.environ.get("CLAIM_VERIFY", "1").strip() != "0",
            claim_max=_int("CLAIM_MAX", 12, 1, 200),
            claim_quote_chars=_int("CLAIM_QUOTE_CHARS", 400, 80, 20000),
            claim_timeout_s=_float("CLAIM_TIMEOUT_S", 0.0),
            claim_fail_open=os.environ.get("CLAIM_VERIFY_FAIL_OPEN", "0").strip() == "1",
            controller_max_steps=_int("CONTROLLER_MAX_STEPS", 3, 1, 50),
            controller_wall_clock_s=_float("CONTROLLER_WALL_CLOCK_S", 0.0),
        )
