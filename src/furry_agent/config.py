from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    comfyui_url: str
    ckpt_name: str | None
    workflows_dir: Path
    outputs_dir: Path
    logs_dir: Path
    timeout_s: float
    model_family: str = "sdxl"
    loras: str = ""

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
            # Template family under workflows/<family>/ (only sdxl is registered).
            model_family=os.environ.get("COMFY_MODEL_FAMILY") or "sdxl",
            # "name[:model_strength[:clip_strength]]", comma separated. Empty = no LoRA.
            loras=os.environ.get("LORAS", ""),
        )
