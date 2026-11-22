"""任务期间清理无用进程 + 任务结束自动停止压制/编码进程（可开关）。"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import psutil

ROOT = Path(r"K:\CONFI\comfy_mcp")
SETTINGS = ROOT / "data" / "settings.json"
LOG = ROOT / "logs" / "process_cleanup.log"

_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop = threading.Event()
_reason = ""


def _cfg() -> dict:
    try:
        data = json.loads(SETTINGS.read_text(encoding="utf-8"))
        return data.get("process_cleanup") or {}
    except Exception:
        return {}


def enabled() -> bool:
    return bool(_cfg().get("enabled", True))


def auto_stop_encode() -> bool:
    return bool(_cfg().get("auto_stop_encode", True))


def _log(msg: str) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg + "\n")
    except Exception:
        pass


def _protected_pids() -> set[int]:
    keep = {os.getpid()}
    try:
        for p in psutil.process_iter(["pid", "cmdline"]):
            cmd = " ".join(p.info.get("cmdline") or []).lower()
            if any(k in cmd for k in ("comfyui\\main.py", "comfyui/main.py", "webapp.app:app",
                                      "server.py --stdio", "launcher\\run.py", "cloudflared",
                                      "segment_film.py")):
                keep.add(p.info["pid"])
            if p.info["pid"] == os.getpid():
                keep.add(p.info["pid"])
    except Exception:
        pass
    try:
        me = psutil.Process(os.getpid())
        for parent in me.parents():
            keep.add(parent.pid)
    except Exception:
        pass
    return keep


def cleanup(reason: str = "job", force_encoders: bool = False) -> list[int]:
    """只清理明确的临时进程；系统/服务进程全部保护。"""
    if not enabled():
        return []
    with _lock:
        keep = _protected_pids()
        killed: list[int] = []
        now = time.time()
        for p in psutil.process_iter(["pid", "name", "cmdline", "create_time", "cpu_percent"]):
            try:
                pid = p.info["pid"]
                if pid in keep:
                    continue
                name = (p.info.get("name") or "").lower()
                cmd = " ".join(p.info.get("cmdline") or [])
                low = cmd.lower()
                age = now - float(p.info.get("create_time") or now)
                cpu = float(p.info.get("cpu_percent") or 0.0)
                do_kill = False
                if name in ("ffmpeg.exe", "ffprobe.exe"):
                    if force_encoders and age > 30:
                        do_kill = True
                    elif age > 180 and cpu < 3:
                        do_kill = True
                elif name == "python.exe" and "comfy_mcp\\tools\\" in low:
                    if "segment_film.py" not in low and age > 7200 and cpu < 2:
                        do_kill = True
                elif name == "powershell.exe" and "tunnel.ps1" in low and age > 600:
                    do_kill = True
                if do_kill:
                    p.kill()
                    killed.append(pid)
            except Exception:
                continue
        if killed:
            _log(f"{reason}: killed {killed}")
        return killed


def _loop(interval: float = 60.0) -> None:
    while not _stop.wait(interval):
        try:
            cleanup(_reason or "job")
        except Exception:
            pass


def start_for_job(reason: str = "job") -> None:
    global _thread, _reason
    if not enabled():
        return
    _reason = reason
    _stop.clear()
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, name="proc-cleaner", daemon=True)
    _thread.start()
    _log(f"cleaner started for {reason}")


def finish_job(reason: str = "job") -> None:
    _stop.set()
    if auto_stop_encode():
        cleanup(reason + ":finish", force_encoders=True)
    _log(f"cleaner stopped for {reason}")
