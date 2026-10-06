"""Which local model runs which search task, and how many at once (design doc §5.6).

Candidates come from config/search_models.json (8 GGUFs run by the PrismML llama.cpp fork). Per task there is a
preference order; scripts/probe-bonsai.ps1 writes tools/bonsai/rank.json with what each model actually passed on
this machine (tool call, JSON, verification, Japanese synthesis), its boot time and the memory it took. At run
time the order is filtered by: the file exists, the probe passed (when a rank file exists), large 27B-class
models are not started while the 27B (llama.cpp router) or a ComfyUI checkpoint is resident, and the free memory.
BONSAI_MODEL pins the worker model; a pinned model that does not fit is refused, never silently replaced.
"""

from __future__ import annotations

import ctypes
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

TASKS = ("route", "plan", "filter", "worker", "critique", "synthesize")
LARGE = "large"


class SelectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelSpec:
    id: str
    label: str
    file: str
    size: int
    mem_mb: int
    cls: str = "small"
    repo: str = ""

    @property
    def large(self) -> bool:
        return self.cls == LARGE


@dataclass
class Catalog:
    models: dict[str, ModelSpec]
    tasks: dict[str, list[str]]
    raw: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "Catalog":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        models = {
            m["id"]: ModelSpec(m["id"], m.get("label", m["id"]), m["file"], int(m.get("size", 0)),
                               int(m.get("mem_mb", 2000)), m.get("class", "small"), m.get("repo", ""))
            for m in raw["models"]
        }
        tasks = {task: [i for i in raw.get("tasks", {}).get(task, []) if i in models] for task in TASKS}
        return cls(models, tasks, raw)


@dataclass
class Rank:
    """Probe results: per model {boot_s, mem_mb, tok_s, ngl, tasks: {task: bool}}; order per task."""

    models: dict[str, dict] = field(default_factory=dict)
    order: dict[str, list[str]] = field(default_factory=dict)
    present: bool = False

    @classmethod
    def load(cls, path: Path) -> "Rank":
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        return cls(raw.get("models") or {}, raw.get("order") or {}, True)

    def passed(self, model_id: str, task: str) -> bool:
        """False only for a model the probe ran on this task and that failed; unprobed models are allowed."""
        entry = self.models.get(model_id) or {}
        tasks = entry.get("tasks") or {}
        if task in tasks:
            return bool(tasks[task])
        if task == "worker" and "tool_ok" in entry:
            return bool(entry["tool_ok"])  # the design doc's key
        return True

    def mem_mb(self, spec: ModelSpec) -> int:
        measured = (self.models.get(spec.id) or {}).get("mem_mb") or (self.models.get(spec.id) or {}).get("rss_mb")
        return int(measured) if measured else spec.mem_mb

    def ngl(self, model_id: str) -> int:
        value = (self.models.get(model_id) or {}).get("ngl")
        return 99 if value is None else int(value)


@dataclass
class Selection:
    task: str
    model: ModelSpec
    width: int
    mem_mb: int
    path: Path
    ngl: int = 99
    skipped: dict[str, str] = field(default_factory=dict)


def free_memory_mb() -> int:
    """Available physical memory (MB). On the Ally X the iGPU allocates from the same shared pool."""
    if sys.platform == "win32":
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))  # type: ignore[attr-defined]
        return int(status.ullAvailPhys // (1024 * 1024))
    if sys.platform == "darwin":
        return _darwin_free_mb()
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 0


_DARWIN_PAGES = ("vm.page_free_count", "vm.page_speculative_count", "vm.page_purgeable_count",
                 "vm.page_pageable_external_count")


def _sysctl_int(name: str) -> int:
    libc = ctypes.CDLL(None)
    value = ctypes.c_uint64(0)
    size = ctypes.c_size_t(ctypes.sizeof(value))
    if libc.sysctlbyname(name.encode(), ctypes.byref(value), ctypes.byref(size), None, ctypes.c_size_t(0)) != 0:
        return 0
    return int(value.value) & ((1 << (8 * size.value)) - 1)


def _darwin_free_mb() -> int:
    """macOS: free + speculative + purgeable + file-backed pageable pages (what the kernel hands out without
    swapping; vm_stat's free + inactive + speculative). Unified memory: Metal (llama.cpp, MLX, ComfyUI on MPS)
    allocates from the same pool. sysctlbyname through ctypes: no subprocess, so it can run on the event loop."""
    page = _sysctl_int("hw.pagesize") or 16384
    return sum(_sysctl_int(name) for name in _DARWIN_PAGES) * page // (1024 * 1024)


def parse_vm_stat(out: str) -> int:
    import re

    size = re.search(r"page size of (\d+) bytes", out)
    page = int(size.group(1)) if size else 16384
    pages = 0
    for name in ("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable"):
        match = re.search(rf"{name}:\s+(\d+)", out)
        if match:
            pages += int(match.group(1))
    return pages * page // (1024 * 1024)


def available_models(catalog: Catalog, models_dir: Path) -> dict[str, Path]:
    found = {}
    for spec in catalog.models.values():
        path = Path(models_dir) / spec.file
        if path.is_file() and (not spec.size or path.stat().st_size == spec.size):
            found[spec.id] = path
    return found


def task_order(catalog: Catalog, rank: Rank, task: str) -> list[str]:
    order = list(rank.order.get(task) or []) if rank.present else []
    order = [i for i in order if i in catalog.models]
    # Models the probe did not order keep their catalog position after the probed ones.
    return order + [i for i in catalog.tasks.get(task, []) if i not in order]


def select_model(task: str, catalog: Catalog, rank: Rank, available: dict[str, Path], free_mb: int, *,
                 leader_resident: bool, comfy_resident: bool = False, reserve_mb: int = 3072,
                 max_width: int = 1, override: str = "", exclude: tuple[str, ...] = ()) -> Selection:
    """Pick the first usable model for ``task`` and how many instances fit (``max_width`` at most)."""
    if task not in TASKS:
        raise SelectionError(f"不明なタスク: {task}")
    if override:
        if override not in catalog.models:
            raise SelectionError(f"BONSAI_MODEL={override} は config/search_models.json にありません")
        candidates = [override]
    else:
        candidates = task_order(catalog, rank, task)
    skipped: dict[str, str] = {}
    budget = free_mb - reserve_mb
    for model_id in candidates:
        spec = catalog.models[model_id]
        if model_id in exclude:
            skipped[model_id] = "前回起動に失敗"
            continue
        if model_id not in available:
            skipped[model_id] = "重みファイルがありません"
            continue
        if not rank.passed(model_id, task):
            skipped[model_id] = "検証に通っていません"
            continue
        if spec.large and leader_resident:
            skipped[model_id] = "27B（llama.cpp）が載っています"
            continue
        if spec.large and comfy_resident:
            skipped[model_id] = "ComfyUI がモデルを保持しています"
            continue
        need = rank.mem_mb(spec)
        width = min(max_width, budget // need if need > 0 else max_width)
        if width <= 0:
            skipped[model_id] = f"メモリ不足（必要 {need} MB / 空き {free_mb} MB − 予約 {reserve_mb} MB）"
            continue
        return Selection(task, spec, int(width), need, available[model_id], rank.ngl(model_id), skipped)
    detail = "、".join(f"{catalog.models[k].label}: {v}" for k, v in skipped.items()) or "候補がありません"
    raise SelectionError(f"{task} に使えるモデルがありません（{detail}）")
