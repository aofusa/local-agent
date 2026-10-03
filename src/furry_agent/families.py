"""Model family names (Chroma HD work instruction §5.2) and the per-family checks LangGraph runs.

Two families are registered: ``sdxl`` (yiffInHell / Illustrious, Danbooru tags, the default) and
``chroma_hd`` (Chroma1-HD, a Flux.1-schnell derivative that reads English prose through T5).
The family decides which ``workflows/<family>/`` templates and ``workflows/maps/<family>.json`` are used;
the LLM call, eject and checkpoint gate stay inside the ComfyUI workflow for both.

The family is chosen only by ``COMFY_MODEL_FAMILY`` in ``.env`` (``chroma`` / ``chroma_hd`` -> Chroma1-HD,
anything else registered -> that family, empty -> sdxl). Messages never switch it.
"""

from __future__ import annotations

import re

SDXL = "sdxl"
CHROMA_HD = "chroma_hd"
ALIASES = {
    "sdxl": SDXL, "illustrious": SDXL, "yiffinhell": SDXL, "yih": SDXL,
    "chroma_hd": CHROMA_HD, "chroma": CHROMA_HD, "chromahd": CHROMA_HD, "chroma-hd": CHROMA_HD,
    "chroma1-hd": CHROMA_HD, "chroma1hd": CHROMA_HD, "chroma1_hd": CHROMA_HD, "クロマ": CHROMA_HD,
}
LABELS = {SDXL: "SDXL（yiffInHell / Danbooru タグ）", CHROMA_HD: "Chroma1-HD（英語の説明文）"}

_FULLWIDTH = str.maketrans("ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ０１２３４５６７８９／＿－",
                               "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789/_-")


class FamilyError(ValueError):
    """The requested combination cannot be used with this family (shown to the user)."""


def canonical_family(name: str | None) -> str | None:
    """Alias -> registered family id. Unknown names pass through so the template loader can report them."""
    if not name:
        return None
    key = name.strip().translate(_FULLWIDTH).lower()
    return ALIASES.get(key) or ALIASES.get(re.sub(r"[\s_-]+", "", key)) or key


def check_roles(family_map: dict, roles: dict[str, str]) -> tuple[dict[str, str], list[str], float | None]:
    """Fit resolved roles (image_id -> role) to what the family supports (§5.5).

    Returns (roles to use, notes for the reply, denoise override). Raises FamilyError when the combination is refused,
    so an unsupported reference never silently turns into a plain text-to-image run.
    """
    if "supported_roles" not in family_map:
        return roles, [], None  # sdxl: every role combination has a template
    supported = set(family_map["supported_roles"])
    label = family_map.get("label") or family_map.get("family", "")
    notes: list[str] = []
    denoise = None
    result = dict(roles)
    pose_ids = [i for i, r in roles.items() if r == "pose"]
    if pose_ids and "pose" not in supported:
        if family_map.get("pose_fallback") == "img2img" and "base" not in roles.values():
            result[pose_ids[0]] = "base"
            denoise = float(family_map.get("pose_fallback_denoise", 0.65))
            notes.append(f"{label} はポーズの ControlNet に未対応のため、ポーズ画像を元画像として"
                         f"denoise {denoise} で寄せています（ポーズの厳密一致ではありません）")
        else:
            raise FamilyError(f"{label} 経路はポーズ ControlNet 未対応です。ポーズ参照を使うときは "
                              ".env の COMFY_MODEL_FAMILY を sdxl に戻すか、ポーズ画像を外してください")
    unsupported = sorted({r for r in result.values() if r not in supported})
    if unsupported:
        names = {"character": "キャラクター参照", "style": "画風参照", "mask": "マスク（部分修正）", "pose": "ポーズ参照"}
        listed = "、".join(names.get(r, r) for r in unsupported)
        raise FamilyError(f"{label} 経路は {listed} の画像に未対応です（使えるのは元画像 1 枚の img2img だけです）。"
                          "画風は文章で指示するか、.env の COMFY_MODEL_FAMILY を sdxl に戻してください")
    return result, notes, denoise


_SENTENCE_END = re.compile(r"[.!?](?:\s|$)")


def looks_like_tag_list(text: str) -> bool:
    """True when a Chroma positive reads as comma-separated tags rather than sentences (§5.7)."""
    parts = [p.strip() for p in (text or "").split(",") if p.strip()]
    if len(parts) < 8:
        return False
    short = sum(1 for p in parts if len(p.split()) <= 3)
    return short / len(parts) >= 0.75 and len(_SENTENCE_END.findall(text)) <= 1
