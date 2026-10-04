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


@dataclass(frozen=True)
class Settings:
    comfyui_url: str
    ckpt_name: str | None
    workflows_dir: Path
    outputs_dir: Path
    logs_dir: Path
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
            timeout_s=float(os.environ.get("COMFYUI_TIMEOUT_S", "600")),
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


def _int(name: str, default: int, lo: int | None = None, hi: int | None = None) -> int:
    raw = os.environ.get(name, "").strip()
    value = int(raw) if raw else default
    if lo is not None:
        value = max(lo, value)
    if hi is not None:
        value = min(hi, value)
    return value


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    return float(raw) if raw else default


def _path(name: str, default: Path) -> Path:
    raw = os.environ.get(name, "").strip()
    path = Path(raw) if raw else default
    return path if path.is_absolute() else REPO_ROOT / path


@dataclass(frozen=True)
class ChatSettings:
    """Chat tab settings (design doc §5.9). The image tab never reads these."""

    lmstudio_url: str = "http://127.0.0.1:1234/v1"
    lmstudio_model: str = ""
    # One model call of the chat tab (conversation, writing, code, synthesis). 20 minutes: the 27B on this machine
    # needs minutes for a long draft or for thinking tokens.
    chat_timeout_s: float = 1200.0
    history_turns: int = 12
    tor_socks_url: str = "socks5h://127.0.0.1:9050"
    tor_required: bool = True
    tor_exe: str = ""
    tor_autostart: bool = True
    search_max_results: int = 5
    search_fetch_pages: int = 1
    search_timeout_s: float = 30.0
    search_total_timeout_s: float = 150.0
    fanout_width: int = 3
    # Deep search (think mode; docs/chat-deep-search-creative-sandbox.md §3.4). Width stays, rounds grow.
    search_max_rounds: int = 4
    search_max_pages: int = 12
    search_wall_clock_s: float = 1200.0
    hits_per_intent: int = 4
    # Thinking tokens added to max_tokens in think mode.
    think_tokens: int = 3072
    # Code sandbox (§5): docker CLI, the uid:gid inside the container, where runs are kept, how long a run waits
    # for the image tab to finish.
    docker_exe: str = "docker"
    sandbox_user: str = "10001:10001"
    code_dir: Path = REPO_ROOT / "artifacts" / "code"
    sandbox_wait_s: float = 600.0
    search_planner: str = "lmstudio"
    search_filter: bool = True
    search_critique: bool = True
    auto_route: bool = True
    job_lock_timeout_s: float = 30.0
    llama_server: str = ""
    models_dir: Path = REPO_ROOT / "tools" / "models"
    model_override: str = ""
    ctx: int = 4096
    reserve_mb: int = 3072
    rank_path: Path = REPO_ROOT / "tools" / "bonsai" / "rank.json"
    catalog_path: Path = REPO_ROOT / "config" / "search_models.json"
    base_port: int = 18181
    worker_timeout_s: float = 120.0
    logs_dir: Path = REPO_ROOT / "logs"
    prompts_dir: Path = REPO_ROOT / "prompts"
    comfyui_url: str = "http://127.0.0.1:8188"

    @classmethod
    def from_env(cls) -> "ChatSettings":
        socks = os.environ.get("TOR_SOCKS_URL", "").strip() or "socks5h://127.0.0.1:9050"
        if not socks.startswith("socks5h://"):
            # socks5:// resolves names locally and leaks DNS outside Tor (design doc §5.7).
            raise ValueError(f"TOR_SOCKS_URL は socks5h:// で指定してください（現在 {socks}）")
        return cls(
            lmstudio_url=os.environ.get("LMSTUDIO_URL", "").strip() or "http://127.0.0.1:1234/v1",
            lmstudio_model=os.environ.get("LMSTUDIO_MODEL", "").strip(),
            chat_timeout_s=_float("CHAT_TIMEOUT_S", 1200.0),
            history_turns=_int("CHAT_HISTORY_TURNS", 12, 1, 100),
            tor_socks_url=socks,
            tor_required=os.environ.get("TOR_REQUIRED", "1").strip() != "0",
            tor_exe=os.environ.get("TOR_EXE", "").strip(),
            tor_autostart=os.environ.get("TOR_AUTOSTART", "1").strip() != "0",
            search_max_results=_int("SEARCH_MAX_RESULTS", 5, 1, 8),
            search_fetch_pages=_int("SEARCH_FETCH_PAGES", 1, 0, 2),
            search_timeout_s=_float("SEARCH_TIMEOUT_S", 30.0),
            search_total_timeout_s=_float("SEARCH_TOTAL_TIMEOUT_S", 150.0),
            fanout_width=_int("SEARCH_FANOUT_WIDTH", 3, 1, 3),
            search_max_rounds=_int("SEARCH_MAX_ROUNDS", 4, 1, 4),
            search_max_pages=_int("SEARCH_MAX_PAGES", 12, 1, 12),
            search_wall_clock_s=_float("SEARCH_WALL_CLOCK_S", 1200.0),
            hits_per_intent=_int("SEARCH_HITS_PER_INTENT", 4, 1, 8),
            think_tokens=_int("CHAT_THINK_TOKENS", 3072, 0, 16384),
            docker_exe=os.environ.get("SANDBOX_DOCKER", "").strip() or "docker",
            sandbox_user=os.environ.get("SANDBOX_USER", "").strip() or "10001:10001",
            code_dir=_path("SANDBOX_CODE_DIR", REPO_ROOT / "artifacts" / "code"),
            sandbox_wait_s=_float("SANDBOX_WAIT_S", 600.0),
            # lmstudio = the Qwen3.8 27B plans first and is unloaded when the readers do not fit next to it;
            # local = the Ternary-Bonsai-2-27B proxy plans too (LM Studio is not loaded for a search at all).
            search_planner=(os.environ.get("SEARCH_PLANNER", "").strip().lower() or "lmstudio"),
            search_filter=os.environ.get("SEARCH_FILTER", "1").strip() != "0",
            search_critique=os.environ.get("SEARCH_CRITIQUE", "1").strip() != "0",
            auto_route=os.environ.get("SEARCH_AUTO_ROUTE", "1").strip() != "0",
            job_lock_timeout_s=_float("JOB_LOCK_TIMEOUT_S", 30.0),
            llama_server=os.environ.get("BONSAI_LLAMA_SERVER", "").strip(),
            models_dir=_path("BONSAI_MODELS_DIR", REPO_ROOT / "tools" / "models"),
            model_override=os.environ.get("BONSAI_MODEL", "").strip(),
            ctx=_int("BONSAI_CTX", 4096, 1024, 32768),
            reserve_mb=_int("BONSAI_RESERVE_MB", 3072, 0),
            rank_path=_path("BONSAI_RANK", REPO_ROOT / "tools" / "bonsai" / "rank.json"),
            base_port=_int("BONSAI_BASE_PORT", 18181, 1024, 65000),
            worker_timeout_s=_float("BONSAI_WORKER_TIMEOUT_S", 120.0),
            logs_dir=Path(os.environ.get("LOGS_DIR", REPO_ROOT / "logs")),
            comfyui_url=os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188"),
        )
