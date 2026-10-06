"""The models a client may pick (docs/host-model-selection-design.md): config/host_models.json.

Clients send a catalog ``id``; the host checks it here and never falls back to another model on its own:
an unknown id, a disabled id (HOST_MODELS_DISABLE) or one whose files are missing is refused with the reason.
Requests without an id use DEFAULT_INFERENCE_MODEL / DEFAULT_IMAGE_MODEL.

Inference models are sections of the llama.cpp router preset (scripts/setup-llm writes one per entry whose GGUF
exists), so picking one only changes the ``model`` the chat tab and /coder/turn send to the router; ``context``
and ``thinking`` follow the entry. An inference entry with an ``endpoint`` runs on another host instead (a llama.cpp
server or an OpenAI-compatible service, docs/remote-llm-design.md): the chat tab and /coder/turn call that server,
and it is usable when the server lists its model. Image models give the image graph its family, file, LoRAs and sampler
parameters; the ComfyUI workflow still calls the LLM and ejects it before the model is loaded.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from furry_agent.templates import LoraSpec, TemplateError, parse_loras

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = REPO_ROOT / "config" / "host_models.json"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,63}$")
THINKING = ("qwen3", "off")
PROMPT_STYLES = {"danbooru": "tags", "prose": "prose"}
# Sampler parameters an image entry may set (everything else in "params" is refused at load).
IMAGE_PARAMS = {"steps": int, "cfg": float, "sampler_name": str, "scheduler": str, "width": int, "height": int,
                "quality_prefix": str, "negative": str}
LORA_WEIGHT = (0.0, 2.0)  # exclusive low, inclusive high (design doc §4)
# Family -> the prompt style its workflows are built for (workflows/maps/<family>.json "prompt_style").
FAMILY_STYLES = {"sdxl": "danbooru", "anima": "danbooru", "flux": "prose", "krea2": "prose"}
# A model on another host (docs/remote-llm-design.md): a llama.cpp server or an OpenAI-compatible service.
ENDPOINT_KINDS = ("llamacpp", "openai")
ENDPOINT_KEYS = {"kind", "url", "url_env", "model", "model_env", "api_key_env"}
ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


class CatalogError(ValueError):
    """config/host_models.json is malformed (raised at load: a bad entry is never skipped silently)."""


class ModelChoiceError(ValueError):
    """A client asked for a model the host does not offer. ``code`` is unknown_model or model_unavailable."""

    def __init__(self, message: str, code: str = "unknown_model"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Endpoint:
    """A model served by another host: a llama.cpp server (``llamacpp``) or any OpenAI-compatible service
    (``openai``). The URL and the model name are written in the catalog or read from the .env keys it names; the API
    key is only ever read from .env (``api_key_env``), never written in the catalog."""

    kind: str
    url: str = ""
    url_env: str = ""
    model: str = ""
    model_env: str = ""
    api_key_env: str = ""

    def resolved(self) -> tuple[str, str, str]:
        """(base URL ending in /v1 or the service's own path, model name, API key); "" for what is not set."""
        url = (os.environ.get(self.url_env, "").strip() if self.url_env else "") or self.url
        model = (os.environ.get(self.model_env, "").strip() if self.model_env else "") or self.model
        key = os.environ.get(self.api_key_env, "").strip() if self.api_key_env else ""
        return url.rstrip("/"), model, key

    def missing(self) -> str:
        """Why the endpoint cannot be called yet ("" when its URL and model are set)."""
        url, model, _ = self.resolved()
        if not url:
            return f"接続先の URL がありません（.env の {self.url_env} に設定してください）" if self.url_env else \
                "接続先の URL がありません"
        if not url.startswith(("http://", "https://")):
            return f"接続先の URL は http:// か https:// で書いてください（{url}）"
        if not model:
            return f"モデル名がありません（.env の {self.model_env} に設定してください）" if self.model_env else \
                "モデル名がありません"
        return ""


@dataclass(frozen=True)
class InferenceModel:
    id: str
    label: str
    gguf: dict
    context: int
    thinking: str
    vision: bool = False
    params: dict = field(default_factory=dict)
    mlx: dict = field(default_factory=dict)
    gpu_offload: float | None = None
    # Set for a model on another host (no GGUF here, never a section of this host's router preset).
    endpoint: Endpoint | None = None

    @property
    def thinks(self) -> bool:
        return self.thinking != "off"

    @property
    def remote(self) -> bool:
        return self.endpoint is not None


@dataclass(frozen=True)
class ImageModel:
    id: str
    label: str
    family: str
    ckpt: str
    prompt_style: str
    loras: tuple[LoraSpec, ...] = ()
    params: dict = field(default_factory=dict)
    models: dict = field(default_factory=dict)

    @property
    def lora_text(self) -> list[str]:
        return [f"{s.name}:{s.strength_model:g}" for s in self.loras]


@dataclass(frozen=True)
class HostCatalog:
    inference: dict[str, InferenceModel]
    image: dict[str, ImageModel]
    downloads: dict[str, list[dict]] = field(default_factory=dict)


def _check_id(value: Any, where: str) -> str:
    if not isinstance(value, str) or not ID_RE.match(value):
        raise CatalogError(f"{where}: id {value!r} は英小文字・数字・ハイフン・ドットで書いてください")
    return value


def parse_catalog_loras(items: Any, where: str) -> tuple[LoraSpec, ...]:
    """["<lora>:<weight>", ...] -> specs (clip strength = model strength). Anything else is refused."""
    if not isinstance(items, list):
        raise CatalogError(f"{where}: loras は配列です")
    specs = []
    for item in items:
        if not isinstance(item, str) or item.count(":") != 1:
            raise CatalogError(f"{where}: LoRA は \"<ファイル>:<強度>\" で書いてください（{item!r}）")
        name, weight = (part.strip() for part in item.split(":"))
        try:
            value = float(weight)
        except ValueError as exc:
            raise CatalogError(f"{where}: LoRA の強度が数値ではありません（{item!r}）") from exc
        if not name or not LORA_WEIGHT[0] < value <= LORA_WEIGHT[1]:
            raise CatalogError(f"{where}: LoRA の強度は 0 より大きく 2 以下です（{item!r}）")
        try:
            specs.extend(parse_loras(f"{name}:{value}"))
        except TemplateError as exc:
            raise CatalogError(f"{where}: {exc}") from exc
    return tuple(specs)


def _image_params(raw: Any, where: str) -> dict:
    params = dict(raw or {})
    for key, value in params.items():
        cast = IMAGE_PARAMS.get(key)
        if cast is None:
            raise CatalogError(f"{where}: params の {key} は使えません（{', '.join(IMAGE_PARAMS)}）")
        try:
            params[key] = cast(value)
        except (TypeError, ValueError) as exc:
            raise CatalogError(f"{where}: params の {key} が不正です（{value!r}）") from exc
    return params


def _endpoint(raw: Any, where: str) -> Endpoint:
    if not isinstance(raw, dict):
        raise CatalogError(f"{where}: endpoint はオブジェクトです")
    unknown = set(raw) - ENDPOINT_KEYS
    if unknown & {"api_key", "key", "token", "password"}:
        raise CatalogError(f"{where}: API キーは一覧に書かず、.env のキー名を api_key_env で指定してください")
    if unknown:
        raise CatalogError(f"{where}: endpoint の {', '.join(sorted(unknown))} は使えません（{', '.join(sorted(ENDPOINT_KEYS))}）")
    kind = raw.get("kind")
    if kind not in ENDPOINT_KINDS:
        raise CatalogError(f"{where}: endpoint の kind は {' / '.join(ENDPOINT_KINDS)} です")
    for key in ("url_env", "model_env", "api_key_env"):
        if raw.get(key) and not ENV_RE.match(str(raw[key])):
            raise CatalogError(f"{where}: endpoint の {key} は .env のキー名（英大文字・数字・_）です（{raw[key]!r}）")
    if not raw.get("url") and not raw.get("url_env"):
        raise CatalogError(f"{where}: endpoint には url か url_env が要ります")
    if not raw.get("model") and not raw.get("model_env"):
        raise CatalogError(f"{where}: endpoint には model か model_env が要ります")
    return Endpoint(kind, str(raw.get("url") or "").strip(), str(raw.get("url_env") or ""),
                    str(raw.get("model") or "").strip(), str(raw.get("model_env") or ""),
                    str(raw.get("api_key_env") or ""))


def parse_catalog(raw: dict) -> HostCatalog:
    inference: dict[str, InferenceModel] = {}
    for item in raw.get("inference") or []:
        model_id = _check_id(item.get("id"), "inference")
        where = f"inference {model_id}"
        if model_id in inference:
            raise CatalogError(f"{where}: id が重複しています")
        endpoint = _endpoint(item["endpoint"], where) if item.get("endpoint") is not None else None
        gguf = item.get("gguf") or {}
        if endpoint is not None:
            if gguf or item.get("mlx"):
                raise CatalogError(f"{where}: endpoint のモデルに gguf / mlx は書けません（このホストでは動かしません）")
        elif gguf.get("catalog") not in ("llm_model", "search_models") or (
                gguf.get("catalog") == "search_models" and not gguf.get("id")):
            raise CatalogError(f"{where}: gguf は {{\"catalog\": \"llm_model\"}} か "
                               "{\"catalog\": \"search_models\", \"id\": ...} です（別ホストのモデルは endpoint）")
        thinking = item.get("thinking", "off")
        if thinking not in THINKING:
            raise CatalogError(f"{where}: thinking は {' / '.join(THINKING)} です")
        inference[model_id] = InferenceModel(
            model_id, str(item.get("label") or model_id), dict(gguf), int(item.get("context") or 4096), thinking,
            bool(item.get("vision", False)), dict(item.get("params") or {}), dict(item.get("mlx") or {}),
            float(item["gpu_offload"]) if item.get("gpu_offload") is not None else None, endpoint)
    image: dict[str, ImageModel] = {}
    for item in raw.get("image") or []:
        model_id = _check_id(item.get("id"), "image")
        where = f"image {model_id}"
        if model_id in image:
            raise CatalogError(f"{where}: id が重複しています")
        family = str(item.get("family") or "")
        style = str(item.get("prompt_style") or FAMILY_STYLES.get(family, ""))
        if family not in FAMILY_STYLES:
            raise CatalogError(f"{where}: family {family!r} は登録されていません（{', '.join(FAMILY_STYLES)}）")
        if style != FAMILY_STYLES[family]:
            raise CatalogError(f"{where}: {family} の prompt_style は {FAMILY_STYLES[family]} です（{style!r}）")
        image[model_id] = ImageModel(
            model_id, str(item.get("label") or model_id), family, str(item.get("ckpt") or "").strip(), style,
            parse_catalog_loras(item.get("loras") or [], where), _image_params(item.get("params"), where),
            {k: str(v) for k, v in (item.get("models") or {}).items()})
    return HostCatalog(inference, image, {k: list(v) for k, v in (raw.get("downloads") or {}).items()})


_cache: dict[str, tuple[float, HostCatalog]] = {}


def _load(path: Path) -> HostCatalog:
    try:
        mtime = path.stat().st_mtime
        cached = _cache.get(str(path))
        if cached and cached[0] == mtime:
            return cached[1]
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError(f"モデルの一覧 {path} を読めません: {exc}") from exc
    catalog = parse_catalog(raw)
    _cache[str(path)] = (mtime, catalog)
    return catalog


def catalog_path() -> Path:
    raw = os.environ.get("HOST_MODELS_PATH", "").strip()
    path = Path(raw) if raw else DEFAULT_PATH
    return path if path.is_absolute() else REPO_ROOT / path


def load_catalog(path: Path | None = None, *, cached: bool = False) -> HostCatalog:
    """The catalog, re-read when the file changed. ``cached=True`` returns the copy read last without touching the
    disk (for code on the event loop; the run's first node refreshes it in a thread)."""
    path = Path(path or catalog_path())
    if cached and str(path) in _cache:
        return _cache[str(path)][1]
    return _load(path)


def disabled_ids() -> set[str]:
    return {x.strip() for x in os.environ.get("HOST_MODELS_DISABLE", "").split(",") if x.strip()}


def default_ids(catalog: HostCatalog) -> dict[str, str]:
    """The ids used when a request names none (.env; else the first entry of each list)."""
    inference = os.environ.get("DEFAULT_INFERENCE_MODEL", "").strip() or next(iter(catalog.inference), "")
    image = os.environ.get("DEFAULT_IMAGE_MODEL", "").strip() or next(iter(catalog.image), "")
    return {"inference": inference, "image": image}


def requested_id(value: Any) -> str:
    """configurable / body value -> id ("" = the default). Non-strings are refused, not ignored."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ModelChoiceError(f"モデルの指定は id の文字列です（{value!r}）", "bad_request")
    return value.strip()


def _pick(kind: str, entries: dict, requested: Any, default: str, unavailable: dict[str, str] | None):
    model_id = requested_id(requested) or default
    label = "推論モデル" if kind == "inference" else "画像モデル"
    if model_id not in entries:
        raise ModelChoiceError(f"{label} {model_id!r} はホストのモデル一覧にありません"
                               f"（{', '.join(entries) or 'なし'}）", "unknown_model")
    if model_id in disabled_ids():
        raise ModelChoiceError(f"{label} {model_id} はこのホストで無効です（HOST_MODELS_DISABLE）", "model_unavailable")
    reason = (unavailable or {}).get(model_id)
    if reason:
        raise ModelChoiceError(f"{label} {entries[model_id].label}（{model_id}）は使えません: {reason}",
                               "model_unavailable")
    return entries[model_id]


def resolve_inference(requested: Any = None, catalog: HostCatalog | None = None,
                      unavailable: dict[str, str] | None = None) -> InferenceModel:
    catalog = catalog or load_catalog()
    return _pick("inference", catalog.inference, requested, default_ids(catalog)["inference"], unavailable)


def resolve_image(requested: Any = None, catalog: HostCatalog | None = None,
                  unavailable: dict[str, str] | None = None) -> ImageModel:
    catalog = catalog or load_catalog()
    return _pick("image", catalog.image, requested, default_ids(catalog)["image"], unavailable)


# --- availability -------------------------------------------------------------------------------------------------


def gguf_path(model: InferenceModel, root: Path = REPO_ROOT) -> Path | None:
    """Where scripts/setup-llm puts the entry's GGUF (used when the router cannot be asked)."""
    try:
        if model.gguf["catalog"] == "llm_model":
            spec = json.loads((root / "config" / "llm_model.json").read_text(encoding="utf-8"))
            quant = (spec.get("quantized") or {}).get(spec.get("quant") or "")
            name = (quant or {}).get("file") or spec["file"]
            return root / "tools" / "models" / "llm" / name
        spec = json.loads((root / "config" / "search_models.json").read_text(encoding="utf-8"))
        entry = next(m for m in spec["models"] if m["id"] == model.gguf["id"])
        models_dir = os.environ.get("BONSAI_MODELS_DIR", "").strip()
        return (Path(models_dir) if models_dir else root / "tools" / "models") / entry["file"]
    except (OSError, KeyError, StopIteration, json.JSONDecodeError):
        return None


def remote_unavailable(model: InferenceModel, offered: list[str] | str | None) -> str:
    """Why a model on another host cannot be used ("" = usable). ``offered`` is the model list its server returned,
    or the reason it could not be asked (a string), or None when it was not asked."""
    assert model.endpoint is not None
    missing = model.endpoint.missing()
    if missing:
        return missing
    if isinstance(offered, str):
        return offered
    _, name, _ = model.endpoint.resolved()
    if offered is not None and name not in offered:
        return f"接続先にモデル {name} がありません（{', '.join(offered[:5]) or 'なし'}）"
    return ""


def inference_unavailable(catalog: HostCatalog, router_models: list[str] | None,
                          remote: dict[str, list[str] | str | None] | None = None) -> dict[str, str]:
    """id -> reason for the inference entries that cannot be used. ``router_models`` is the router's model list
    (None when the router is not reachable: the GGUF files decide). ``remote`` is id -> what the other host's
    server answered for the entries with an endpoint (see remote_unavailable)."""
    out = {}
    for model in catalog.inference.values():
        if model.id in disabled_ids():
            out[model.id] = "このホストで無効（HOST_MODELS_DISABLE）"
        elif model.remote:
            reason = remote_unavailable(model, (remote or {}).get(model.id))
            if reason:
                out[model.id] = reason
        elif router_models is not None:
            if model.id not in router_models:
                out[model.id] = "LLM ルータのプリセットにありません（scripts/setup-llm でモデルを導入してください）"
        else:
            path = gguf_path(model)
            if path is None or not path.exists():
                out[model.id] = "モデルファイルがこのホストにありません"
    return out


def image_files(model: ImageModel, family_map: dict) -> dict[str, str]:
    """slot -> file name the entry needs in ComfyUI (the ckpt, and the family's text encoder / VAE)."""
    files = {"ckpt_name": model.ckpt}
    models = {**(family_map.get("models") or {}), **model.models}
    for slot in ("clip_name", "vae_name"):
        if models.get(slot):
            files[slot] = models[slot]
    return files


def image_unavailable(catalog: HostCatalog, maps: dict[str, dict],
                      comfy_files: dict[str, dict[str, list[str]]] | None,
                      comfy_loras: list[str] | None) -> dict[str, str]:
    """id -> reason. ``comfy_files`` is family -> slot -> the names ComfyUI offers for that family's loader (None:
    ComfyUI is not reachable, nothing is refused for missing files then; the run checks again before it queues)."""
    from furry_agent.templates import resolve_lora_names

    out = {}
    for model in catalog.image.values():
        if model.id in disabled_ids():
            out[model.id] = "このホストで無効（HOST_MODELS_DISABLE）"
            continue
        if not model.ckpt:
            out[model.id] = "ckpt が空です"
            continue
        if model.family not in maps:
            out[model.id] = f"モデル系統 {model.family} のテンプレートがありません"
            continue
        if comfy_files is None:
            continue
        offered = comfy_files.get(model.family) or {}
        missing = [name for slot, name in image_files(model, maps[model.family]).items()
                   if name not in (offered.get(slot) or [])]
        if missing:
            out[model.id] = f"ComfyUI にファイルがありません: {', '.join(missing)}"
            continue
        if model.loras and comfy_loras is not None:
            try:
                resolve_lora_names(list(model.loras), comfy_loras)
            except TemplateError as exc:
                out[model.id] = str(exc)
    return out
