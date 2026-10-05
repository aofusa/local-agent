"""Role resolution, template selection and parameter clamping (work instruction WI-IMG-MULTI-REF-001 §4).

Everything here is a pure function. LangGraph never calls the LLM router (AGENTS.md), so ``classify_intent``
is a deterministic rule-based reader of the Japanese instruction: role keywords, ordinals
("1枚目", "画像2", "A/B/C") and vague words. The LLM still turns the instruction and the role-specific
image tags into Danbooru tags inside the ComfyUI workflow.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Literal, TypedDict

Role = Literal["auto", "style", "pose", "character", "base", "mask"]
ROLES: tuple[str, ...] = ("auto", "style", "pose", "character", "base", "mask")
REFERENCE_ROLES: tuple[str, ...] = ("character", "pose", "style")  # order used in template ids
PosePreprocessor = Literal["openpose", "dwpose", "depth", "canny"]

CONFIDENCE_THRESHOLD = 0.65
MAX_REFERENCES = 4

# §4.7 clamps. Width/height/steps/cfg defaults follow the design doc (832x1216, 28, 5.5).
SIZE_MIN, SIZE_MAX, SIZE_STEP = 512, 1536, 64
DEFAULT_WIDTH, DEFAULT_HEIGHT = 832, 1216
DEFAULT_STEPS, DEFAULT_CFG = 28, 5.5
DENOISE_RANGE = (0.15, 0.85)
STRENGTH_RANGES = {"style": (0.2, 1.0), "pose": (0.3, 1.2), "character": (0.4, 1.2)}
DEFAULT_STRENGTH = {"style": 0.55, "pose": 0.80, "character": 0.85}
# img2img denoise: design doc 0.45 for plain/style edits; WI 0.65 when the pose or the character changes.
DENOISE_I2I = 0.45
DENOISE_I2I_CHANGE = 0.65
DENOISE_INPAINT = 0.75

_ROLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "style": ("画風", "絵柄", "作風", "タッチ", "塗り", "スタイル", "雰囲気", "画調", "色使い", "色調", "テイスト"),
    "pose": ("ポージング", "ポーズ", "姿勢", "体勢", "構図", "アングル"),
    "character": ("キャラクター", "キャラ", "顔", "人物", "この子", "本人", "見た目", "容姿", "外見"),
    "base": ("元画像", "元の画像", "ベース", "下絵", "背景を", "修正", "直して", "描き直", "手直し"),
    "mask": ("マスク",),
}
_KEEP_WORDS = ("維持", "そのまま", "残し", "残す", "キープ", "保っ", "保ち", "変えず")
_VAGUE_WORDS = ("いい感じ", "良い感じ", "合わせて", "それっぽく", "混ぜて", "適当に", "うまく")
_EXPRESSION_PREFIX = "笑泣横寝真素怒困照変"
_NEW_SCENE_WORDS = ("描いて", "生成", "新しく", "新規", "別の", "シーン", "場面")
_STRONG_WORDS = ("強め", "強く", "しっかり")
_WEAK_WORDS = ("弱め", "弱く", "控えめ", "軽く", "少し")
_REUSE_WORDS = ("さっきの", "前回の", "前の画像", "今の画像", "生成した画像", "生成された画像", "出来た画像", "できた画像")
_REUSE_WORDS_NO_ATTACHMENT = ("この画像", "この絵", "これを", "これの")

_FULLWIDTH = str.maketrans("０１２３４５６７８９ＡＢＣＤａｂｃｄ×：＝", "0123456789ABCDabcdx:=")
_KANJI_NUM = {"一": 1, "二": 2, "三": 3, "四": 4}
_ORDINAL_PATTERNS = (
    re.compile(r"([1-4一二三四])\s*(?:枚目|つ目|番目)"),
    re.compile(r"(?:画像|写真|イラスト|絵)\s*([1-4])(?![0-9])"),
    re.compile(r"(?<![A-Za-z])([A-D])(?=\s*(?:の|は|を|が|で|に|、|,))"),
)
_SEED = re.compile(r"(?:seed|シード)\s*[:=]?\s*(\d{1,10})", re.IGNORECASE)
_SIZE = re.compile(r"(?<![0-9])(\d{3,4})\s*[x*]\s*(\d{3,4})(?![0-9])", re.IGNORECASE)
_DENOISE = re.compile(r"denoise\s*[:=]?\s*([0-9]*\.?[0-9]+)", re.IGNORECASE)


class ReferenceImage(TypedDict):
    image_id: str
    role: str                     # what the user asked for (auto when unspecified)
    resolved_role: str | None     # after classification and conflict resolution
    filename_on_comfy: str | None
    sha256: str
    width: int
    height: int
    strength: float | None
    source: str                   # "attachment" or "previous_output"
    local_path: str | None        # previous output only (outputs/...)
    mime: str


class GenerationPlan(TypedDict):
    template_id: str
    model_family: str
    positive: str                 # Japanese instruction; the workflow's LLM makes the tags
    negative: str
    width: int
    height: int
    seed: int
    steps: int
    cfg: float
    sampler_name: str | None      # None: the template's value (sdxl)
    scheduler: str | None
    denoise: float | None
    strengths: dict[str, float]
    pose_preprocessor: str | None
    needs_confirmation: bool
    confirmation_reason: str | None
    notes: list[str]


@dataclass
class Intent:
    """Output of classify_intent (the WI §4.9 contract, produced by rules instead of an LLM)."""

    role_guesses: dict[str, tuple[str, float]] = field(default_factory=dict)  # image_id -> (role, confidence)
    vague: bool = False
    conflicts: list[str] = field(default_factory=list)
    seed: int | None = None
    width: int | None = None
    height: int | None = None
    denoise: float | None = None
    pose_preprocessor: str | None = None
    strength_hints: dict[str, float] = field(default_factory=dict)
    text: str = ""                # instruction with machine parameters (seed, size) removed


def normalize(text: str) -> str:
    return (text or "").translate(_FULLWIDTH)


def wants_previous_output(text: str, has_attachments: bool) -> bool:
    text = normalize(text)
    words = _REUSE_WORDS if has_attachments else _REUSE_WORDS + _REUSE_WORDS_NO_ATTACHMENT
    return any(w in text for w in words)


def _role_mentions(text: str) -> list[tuple[int, str]]:
    """(position, role) for each role keyword, in text order. Pose/character followed by 'keep' means base."""
    found: list[tuple[int, int, str]] = []
    for role, words in _ROLE_KEYWORDS.items():
        for word in words:
            for m in re.finditer(re.escape(word), text):
                found.append((m.start(), m.end(), role))
    found.sort(key=lambda f: (f[0], -(f[1] - f[0])))
    mentions, last_end = [], -1
    for start, end, role in found:
        if start < last_end:
            continue  # overlapping shorter keyword (キャラ inside キャラクター)
        if role == "character" and text[start:end] == "顔" and start and text[start - 1] in _EXPRESSION_PREFIX:
            continue  # 笑顔, 横顔 ... are expressions/angles, not identity
        last_end = end
        if role in ("pose", "character") and any(k in text[end:end + 8] for k in _KEEP_WORDS):
            role = "base"
        mentions.append((start, role))
    return mentions


def _ordinal_segments(text: str, n_images: int) -> list[tuple[int, int, int]]:
    """(image index 0-based, segment start, segment end) for each ordinal mention."""
    hits: list[tuple[int, int, int]] = []
    for pattern in _ORDINAL_PATTERNS:
        for m in pattern.finditer(text):
            token = m.group(1)
            index = _KANJI_NUM.get(token) or (ord(token) - ord("A") + 1 if token.isalpha() else int(token))
            if 1 <= index <= n_images:
                hits.append((m.start(), m.end(), index - 1))
    hits.sort()
    segments = []
    for i, (start, end, index) in enumerate(hits):
        stop = hits[i + 1][0] if i + 1 < len(hits) else len(text)
        segments.append((index, end, stop))
    return segments


def _strength_hint(text: str, pos: int) -> float | None:
    window = text[pos:pos + 14]
    if any(w in window for w in _STRONG_WORDS):
        return 0.25
    if any(w in window for w in _WEAK_WORDS):
        return -0.2
    return None


def classify_intent(text: str, references: list[ReferenceImage]) -> Intent:
    raw = normalize(text)
    intent = Intent()

    if m := _SEED.search(raw):
        intent.seed = int(m.group(1))
    if m := _SIZE.search(raw):
        intent.width, intent.height = int(m.group(1)), int(m.group(2))
    elif "横長" in raw:
        intent.width, intent.height = 1216, 832
    elif "縦長" in raw:
        intent.width, intent.height = 832, 1216
    elif "正方形" in raw:
        intent.width, intent.height = 1024, 1024
    if m := _DENOISE.search(raw):
        intent.denoise = float(m.group(1))
    if any(w in raw for w in ("線画", "輪郭", "エッジ")):
        intent.pose_preprocessor = "canny"
    elif any(w in raw for w in ("奥行き", "深度", "立体感")):
        intent.pose_preprocessor = "depth"
    intent.text = _SIZE.sub("", _SEED.sub("", raw)).strip(" 、,。") or text.strip()

    mentions = _role_mentions(raw)
    for pos, role in mentions:
        if role in DEFAULT_STRENGTH and (hint := _strength_hint(raw, pos)) is not None:
            intent.strength_hints[role] = DEFAULT_STRENGTH[role] + hint
    intent.vague = any(w in raw for w in _VAGUE_WORDS)

    taken = {r["role"] for r in references if r["role"] != "auto"}
    auto = [r for r in references if r["role"] == "auto"]
    n = len(references)

    # 1. Ordinals: "1枚目のキャラ", "Bのポーズ", "画像3の画風".
    in_segments: set[int] = set()
    for index, start, stop in _ordinal_segments(raw, n):
        ref = references[index]
        roles_here = [(p, r) for p, r in mentions if start <= p < stop]
        in_segments.update(p for p, _ in roles_here)
        if ref["role"] != "auto" or not roles_here:
            continue
        kinds = {r for _, r in roles_here}
        if {"style", "character"} <= kinds:
            intent.conflicts.append(f"{ref['image_id']} に画風とキャラクターの両方が指定されています")
        role = roles_here[0][1]
        intent.role_guesses[ref["image_id"]] = (role, 0.9)
        taken.add(role)

    # 2. Remaining auto images take the remaining mentioned roles in text order.
    pending = [r for r in auto if r["image_id"] not in intent.role_guesses]
    free_roles: list[str] = []
    for pos, role in mentions:
        if pos not in in_segments and role not in taken and role not in free_roles:
            free_roles.append(role)
    if len(pending) == 1 and len(references) == 1:
        intent.role_guesses[pending[0]["image_id"]] = _single_image_role(raw, free_roles, intent.vague)
    elif pending:
        confident = len(free_roles) >= len(pending)
        for ref, role in zip(pending, free_roles):
            intent.role_guesses[ref["image_id"]] = (role, 0.7 if confident else 0.5)
        for ref in pending[len(free_roles):]:
            intent.role_guesses[ref["image_id"]] = ("auto", 0.0)
    return intent


def _single_image_role(text: str, mentions: list[str], vague: bool) -> tuple[str, float]:
    """WI §4.11: one image is a base for editing unless the text clearly asks for a style or pose source."""
    for role in mentions:
        if role in ("style", "pose", "mask"):
            return (role, 0.8) if role != "mask" else ("base", 0.5)
        if role == "character" and any(w in text for w in _NEW_SCENE_WORDS):
            return "character", 0.7
    return "base", 0.5 if vague else 0.8


@dataclass
class Resolution:
    roles: dict[str, str]                  # image_id -> resolved role (only used images)
    ignored: dict[str, str]                # image_id -> reason
    needs_confirmation: bool
    reason: str | None
    proposal: dict[str, str]               # image_id -> role proposed to the user (always complete)


def resolve_roles(references: list[ReferenceImage], intent: Intent) -> Resolution:
    """Explicit roles win, then guesses. Duplicate roles keep the first image (FR-7)."""
    reasons: list[str] = list(intent.conflicts)
    proposal: dict[str, str] = {}
    low_confidence = []
    for ref in references:
        if ref["role"] != "auto":
            proposal[ref["image_id"]] = ref["role"]
            continue
        role, confidence = intent.role_guesses.get(ref["image_id"], ("auto", 0.0))
        if role == "auto" or confidence < CONFIDENCE_THRESHOLD:
            low_confidence.append(ref["image_id"])
        proposal[ref["image_id"]] = role
    if low_confidence:
        reasons.append("画像の役割を指示から決められませんでした（" + "、".join(low_confidence) + "）")
    if intent.vague and sum(1 for r in references if r["role"] == "auto") >= 2:
        reasons.append("指示が曖昧で、役割未指定の画像が 2 枚以上あります")

    # Fill unresolved proposals with free roles so "approve" always yields a runnable plan.
    used = [r for r in proposal.values() if r != "auto"]
    for image_id, role in proposal.items():
        if role == "auto":
            free = [r for r in ("character", "pose", "style", "base") if r not in used]
            proposal[image_id] = free[0] if free else "style"
            used.append(proposal[image_id])

    roles, ignored = {}, {}
    seen: set[str] = set()
    for ref in references:
        role = proposal[ref["image_id"]]
        if role in seen:
            ignored[ref["image_id"]] = f"{role} は先頭の画像を採用したため無視しました"
            continue
        seen.add(role)
        roles[ref["image_id"]] = role
    if "mask" in seen and "base" not in seen:
        reasons.append("mask には base（修正対象の画像）が必要です")
    return Resolution(
        roles=roles,
        ignored=ignored,
        needs_confirmation=bool(reasons),
        reason="。".join(dict.fromkeys(reasons)) or None,
        proposal=proposal,
    )


def select_workflow(roles: set[str] | list[str] | tuple[str, ...]) -> str:
    """Deterministic template id for a role set (NFR-2). ``auto`` must already be resolved."""
    roles = set(roles)
    unknown = roles - set(ROLES) | ({"auto"} & roles)
    if unknown:
        raise ValueError(f"unresolved or unknown roles: {sorted(unknown)}")
    if "mask" in roles and "base" not in roles:
        raise ValueError("mask requires base")
    mode = "inpaint" if "mask" in roles else "i2i" if "base" in roles else "t2i"
    refs = [r for r in REFERENCE_ROLES if r in roles]
    return f"{'_'.join(refs)}_{mode}" if refs else f"{mode}_basic"


def template_roles(template_id: str) -> set[str]:
    """Inverse of select_workflow (used to check required slots)."""
    head, mode = template_id.rsplit("_", 1)
    roles = set() if head in ("t2i", "i2i", "inpaint") else set(head.split("_"))
    if mode == "basic":
        mode = head
    if mode in ("i2i", "inpaint"):
        roles.add("base")
    if mode == "inpaint":
        roles.add("mask")
    return roles


def clamp_size(value: int, low: int = SIZE_MIN, high: int = SIZE_MAX) -> int:
    value = min(high, max(low, int(value)))
    return int(round(value / SIZE_STEP) * SIZE_STEP)


def fit_pixels(width: int, height: int, max_pixels: int | None) -> tuple[int, int]:
    """Scale down (keeping the aspect ratio, multiples of SIZE_STEP) until width x height <= max_pixels."""
    if not max_pixels or width * height <= max_pixels:
        return width, height
    scale = (max_pixels / (width * height)) ** 0.5
    w, h = int(width * scale) // SIZE_STEP * SIZE_STEP, int(height * scale) // SIZE_STEP * SIZE_STEP
    return max(SIZE_STEP, w), max(SIZE_STEP, h)


def _clamp(value: float, low: float, high: float) -> float:
    return round(min(high, max(low, float(value))), 3)


def default_denoise(roles: set[str]) -> float | None:
    if "mask" in roles:
        return DENOISE_INPAINT
    if "base" in roles:
        return DENOISE_I2I_CHANGE if {"pose", "character"} & roles else DENOISE_I2I
    return None


def build_plan(
    template_id: str,
    model_family: str,
    references: list[ReferenceImage],
    roles: dict[str, str],
    intent: Intent,
    rng: random.Random | None = None,
    defaults: dict | None = None,
) -> GenerationPlan:
    """Clamp every number into §4.7 ranges and note each adjustment for the reply.

    ``defaults`` is the family map's ``defaults`` (size, steps, cfg, sampler, size bounds); sdxl has none
    and keeps the design-doc constants.
    """
    d = defaults or {}
    notes: list[str] = []
    present = set(roles.values())

    def clamp_note(label: str, value: float, low: float, high: float) -> float:
        clamped = _clamp(value, low, high)
        if abs(clamped - float(value)) > 1e-9:
            notes.append(f"{label} の指定値 {value} を範囲内の {clamped} へ調整しました")
        return clamped

    width, height = fit_pixels(int(d.get("width", DEFAULT_WIDTH)), int(d.get("height", DEFAULT_HEIGHT)),
                               d.get("max_pixels"))
    if intent.width and intent.height:
        low, high = int(d.get("size_min", SIZE_MIN)), int(d.get("size_max", SIZE_MAX))
        width, height = fit_pixels(clamp_size(intent.width, low, high), clamp_size(intent.height, low, high),
                                   d.get("max_pixels"))
        if (width, height) != (intent.width, intent.height):
            notes.append(f"サイズ {intent.width}x{intent.height} を {width}x{height} へ調整しました")

    strengths: dict[str, float] = {}
    by_role = {role: next(r for r in references if r["image_id"] == image_id) for image_id, role in roles.items()}
    for role in ("style", "pose", "character"):
        if role not in present:
            continue
        low, high = STRENGTH_RANGES[role]
        value = by_role[role].get("strength")
        if value is None:
            value = intent.strength_hints.get(role, DEFAULT_STRENGTH[role])
        strengths[role] = clamp_note(f"{role} 強度", value, low, high)

    denoise = default_denoise(present)
    if denoise is not None:
        base_strength = by_role["base"].get("strength")
        if intent.denoise is not None:
            denoise = clamp_note("denoise", intent.denoise, *DENOISE_RANGE)
        elif base_strength is not None:
            denoise = clamp_note("base 強度（denoise）", base_strength, *DENOISE_RANGE)

    seed = intent.seed if intent.seed is not None else (rng or random).randint(0, 2**32 - 1)
    return GenerationPlan(
        template_id=template_id,
        model_family=model_family,
        positive=intent.text,
        negative="",
        width=width,
        height=height,
        seed=int(seed),
        steps=int(d.get("steps", DEFAULT_STEPS)),
        cfg=float(d.get("cfg", DEFAULT_CFG)),
        sampler_name=d.get("sampler_name"),
        scheduler=d.get("scheduler"),
        denoise=denoise,
        strengths=strengths,
        pose_preprocessor=(intent.pose_preprocessor or "openpose") if "pose" in present else None,
        needs_confirmation=False,
        confirmation_reason=None,
        notes=notes,
    )
