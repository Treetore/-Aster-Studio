"""Optional task notifications via webhook (non-blocking, best-effort)."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent


def _webhook() -> str:
    import config
    try:
        s = config.load_settings()
        return str((s.get("notify") or {}).get("webhook") or "").strip()
    except Exception:
        return ""


def send(event: str, payload: dict | None = None) -> None:
    url = _webhook()
    if not url:
        return
    data = {"event": event, "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "payload": payload or {}}

    def worker() -> None:
        try:
            requests.post(url, json=data, timeout=10)
        except Exception:
            pass

    threading.Thread(target=worker, name="notify", daemon=True).start()
