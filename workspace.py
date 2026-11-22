"""Projects, collections and prompt presets (file-backed, no database)."""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STORE = ROOT / "data" / "workspace.json"

DEFAULT_PRESETS = [
    {"id": "cinematic", "name": "电影感", "prompt": "cinematic lighting, shallow depth of field, film grain, 24fps", "builtin": True},
    {"id": "anime", "name": "新海诚动画", "prompt": "Makoto Shinkai anime style, vibrant gradient sky, detailed background, lens flare", "builtin": True},
    {"id": "product", "name": "产品展示", "prompt": "clean studio background, softbox lighting, product hero shot, high detail", "builtin": True},
    {"id": "portrait", "name": "人像特写", "prompt": "close-up portrait, natural skin texture, soft rim light, 85mm lens", "builtin": True},
]


def _load() -> dict:
    try:
        data = json.loads(STORE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError
        data.setdefault("projects", [])
        data.setdefault("presets", [])
        return data
    except Exception:
        return {"projects": [], "presets": []}


def _save(data: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def projects() -> list[dict]:
    return _load()["projects"]


def create_project(name: str, description: str = "") -> dict:
    data = _load()
    item = {"id": uuid.uuid4().hex[:10], "name": (name or "未命名项目").strip()[:80],
            "description": (description or "").strip()[:300],
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "items": []}
    data["projects"].append(item)
    _save(data)
    return item


def delete_project(pid: str) -> bool:
    data = _load()
    before = len(data["projects"])
    data["projects"] = [p for p in data["projects"] if p["id"] != pid]
    if len(data["projects"]) == before:
        return False
    _save(data)
    return True


def add_item(pid: str, path: str, kind: str = "") -> dict | None:
    data = _load()
    for p in data["projects"]:
        if p["id"] == pid:
            item = {"path": path, "kind": kind, "added": time.strftime("%Y-%m-%dT%H:%M:%S")}
            p.setdefault("items", []).append(item)
            _save(data)
            return p
    return None


def presets() -> list[dict]:
    data = _load()
    custom = data.get("presets") or []
    return DEFAULT_PRESETS + [p for p in custom if not p.get("builtin")]


def add_preset(name: str, prompt: str) -> dict:
    data = _load()
    item = {"id": uuid.uuid4().hex[:10], "name": (name or "预设").strip()[:60],
            "prompt": (prompt or "").strip()[:2000], "builtin": False}
    data["presets"].append(item)
    _save(data)
    return item
