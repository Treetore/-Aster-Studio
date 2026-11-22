"""ComfyUI-Manager public model marketplace bridge (cache + download)."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "model_market_cache.json"
REMOTE_URL = ("https://raw.githubusercontent.com/ltdrdata/ComfyUI-Manager/"
              "main/model-list.json")
SAVE_PATHS = {"checkpoints", "diffusion_models", "text_encoders", "vae", "loras",
              "controlnet", "upscale_models", "clip_vision", "embeddings", "vae_approx",
              "style_models", "gligen", "photomaker", "hypernetworks"}

_lock = threading.Lock()
_download: dict = {"running": False, "file": "", "bytes": 0, "error": None, "started": 0}


def comfy_root() -> Path:
    env = os.environ.get("COMFYUI_ROOT")
    if env:
        p = Path(env)
        return p if (p / "main.py").is_file() else p / "ComfyUI"
    for p in (ROOT.parent / "mian" / "ComfyUI_windows_portable" / "ComfyUI", ROOT / "ComfyUI"):
        if (p / "main.py").is_file():
            return p
    return ROOT.parent / "mian" / "ComfyUI_windows_portable" / "ComfyUI"


def _target(save_path: str, filename: str) -> Path:
    rel = (save_path or "").replace("\\", "/").strip("/")
    if rel not in SAVE_PATHS:
        rel = "checkpoints"
    safe = "".join(c for c in (filename or "") if c.isalnum() or c in "._- ").strip()
    if not safe:
        safe = "download.bin"
    return comfy_root() / "models" / rel / safe


def _normalize(item: dict, idx: int) -> dict:
    save_path = str(item.get("save_path") or "checkpoints").replace("\\", "/")
    filename = str(item.get("filename") or "").strip()
    target = _target(save_path, filename)
    return {
        "id": f"comfy:{idx}",
        "name": item.get("name") or filename,
        "type": item.get("type") or "",
        "base": item.get("base") or "",
        "save_path": save_path,
        "filename": filename,
        "size": item.get("size") or "",
        "description": item.get("description") or "",
        "reference": item.get("reference") or "",
        "url": item.get("url") or "",
        "license": "上游模型许可（见 reference）",
        "license_url": item.get("reference") or "",
        "target": save_path,
        "file": filename,
        "installed": target.is_file(),
        "path": str(target),
        "source": "comfyui-manager",
    }


def fetch(force: bool = False) -> dict:
    if not force and CACHE.is_file():
        try:
            data = json.loads(CACHE.read_text(encoding="utf-8"))
            if time.time() - float(data.get("fetched_at") or 0) < 6 * 3600:
                return data
        except Exception:
            pass
    try:
        r = requests.get(REMOTE_URL, timeout=30)
        r.raise_for_status()
        raw = r.json().get("models") or []
    except Exception as exc:
        try:
            data = json.loads(CACHE.read_text(encoding="utf-8"))
            data["stale"] = True
            data["error"] = str(exc)
            return data
        except Exception:
            return {"ok": False, "error": str(exc), "models": [], "fetched_at": 0}
    models = [_normalize(x, i) for i, x in enumerate(raw) if isinstance(x, dict)]
    data = {"ok": True, "fetched_at": time.time(), "count": len(models), "models": models}
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return data


def download(url: str, save_path: str, filename: str) -> dict:
    if _download["running"]:
        return {"ok": False, "error": "a model download is already running"}
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "invalid download url"}
    dest = _target(save_path, filename)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")

    def worker() -> None:
        _download.update({"running": True, "file": str(dest), "bytes": 0,
                          "error": None, "started": time.time()})
        try:
            headers = {}
            mode = "wb"
            if tmp.is_file() and tmp.stat().st_size > 0:
                headers["Range"] = f"bytes={tmp.stat().st_size}-"
                mode = "ab"
            with requests.get(url, headers=headers, stream=True, timeout=60) as r:
                r.raise_for_status()
                with tmp.open(mode) as f:
                    for chunk in r.iter_content(1024 * 1024):
                        if chunk:
                            f.write(chunk)
                            _download["bytes"] += len(chunk)
            tmp.replace(dest)
        except Exception as exc:  # noqa: BLE001
            _download["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            _download["running"] = False

    threading.Thread(target=worker, name="model-market-download", daemon=True).start()
    return {"ok": True, "started": True, "file": str(dest)}


def status() -> dict:
    return dict(_download)
