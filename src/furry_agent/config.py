from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from furry_agent.families import CHROMA_HD, SDXL, canonical_family

REPO_ROOT = Path(__file__).resolve().parents[2]

# Chroma1-HD model files: env name -> template slot. Empty = the value stored in workflows/maps/chroma_hd.json.
CHROMA_MODEL_ENV = {
    "CHROMA_UNET_NAME": "ckpt_name",
    "CHROMA_TEXT_ENCODER": "clip_name",
    "CHROMA_VAE": "vae_name",
    "CHROMA_WEIGHT_DTYPE": "weight_dtype",
}


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
    chroma_enabled: bool = True
    chroma_loras: str = ""
    chroma_models: dict[str, str] = field(default_factory=dict)

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
            # Default family: sdxl (yiffInHell) or chroma_hd (Chroma1-HD). Aliases: illustrious, chroma.
            model_family=canonical_family(os.environ.get("COMFY_MODEL_FAMILY")) or SDXL,
            # "name[:model_strength[:clip_strength]]", comma separated. Empty = no LoRA.
            loras=os.environ.get("LORAS", ""),
            # Rollback switch (WI §9): 0 keeps every request on sdxl and refuses /model chroma.
            chroma_enabled=os.environ.get("CHROMA_HD_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off"),
            # SDXL LoRAs do not fit Chroma, so Chroma has its own list.
            chroma_loras=os.environ.get("CHROMA_LORAS", ""),
            chroma_models={slot: os.environ[env] for env, slot in CHROMA_MODEL_ENV.items() if os.environ.get(env)},
        )

    def ckpt_for(self, family: str) -> str | None:
        """CKPT_NAME belongs to COMFY_MODEL_FAMILY; another family chosen per message uses its own setting."""
        if family == self.model_family and self.ckpt_name:
            return self.ckpt_name
        if family == CHROMA_HD:
            return self.chroma_models.get("ckpt_name")
        return None

    def loras_for(self, family: str) -> str:
        return self.chroma_loras if family == CHROMA_HD else self.loras

    def model_overrides(self, family: str) -> dict[str, str]:
        """Template slot -> value for the text encoder / VAE / weight dtype (Chroma only)."""
        if family != CHROMA_HD:
            return {}
        return {k: v for k, v in self.chroma_models.items() if k != "ckpt_name"}
