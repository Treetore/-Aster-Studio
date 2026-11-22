"""全局单任务锁：同一时间只允许一条生成任务，跨 MCP / Web / 桥进程生效。

用法：
    token = acquire("bridge", "moss_image")
    if not token["ok"]: return busy_response(token)
    ...
    bind(token, job_id)
    release(job_id=job_id)
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(r"K:\CONFI\comfy_mcp")
DATA = ROOT / "data"
LOCK_FILE = DATA / "global_job.lock"
MAX_AGE_S = 6 * 3600


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _pid_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        import psutil
        return psutil.pid_exists(pid)
    except Exception:
        try:
            os.kill(pid, 0)
            return True
        except Exception:
            return False


def _read() -> dict:
    try:
        return json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write(info: dict) -> None:
    tmp = LOCK_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(LOCK_FILE)


def _stale(info: dict) -> bool:
    if not info:
        return True
    pid = int(info.get("pid") or 0)
    started = float(info.get("started_ts") or 0)
    if started and time.time() - started > MAX_AGE_S:
        return True
    if pid and not _pid_alive(pid):
        return True
    return False


def acquire(source: str, template: str = "") -> dict:
    """尝试占锁；返回 {ok, holder?, message?}。"""
    for attempt in range(2):
        try:
            fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            info = {"pid": os.getpid(), "source": source, "template": template,
                    "job_id": None, "started_at": _now(), "started_ts": time.time()}
            os.write(fd, json.dumps(info, ensure_ascii=False).encode("utf-8"))
            os.close(fd)
            return {"ok": True, "holder": info}
        except FileExistsError:
            info = _read()
            if _stale(info):
                try:
                    LOCK_FILE.unlink()
                except Exception:
                    pass
                continue
            return {"ok": False, "busy": True, "holder": info,
                    "message": "请等待上一条任务完成（当前：%s · %s · 开始于 %s）" % (
                        info.get("template") or "未知模型", info.get("source") or "未知来源",
                        info.get("started_at") or "?")}
    return {"ok": False, "busy": True, "message": "请等待上一条任务完成"}


def bind(job_id: str) -> None:
    info = _read()
    if info and int(info.get("pid") or 0) == os.getpid():
        info["job_id"] = job_id
        try:
            _write(info)
        except Exception:
            pass


def release(job_id: str | None = None) -> None:
    info = _read()
    if not info:
        return
    if job_id and info.get("job_id") and info.get("job_id") != job_id:
        return
    try:
        LOCK_FILE.unlink()
    except Exception:
        pass


def status() -> dict:
    info = _read()
    if not info:
        return {"busy": False}
    if _stale(info):
        return {"busy": False, "stale": True, "holder": info}
    return {"busy": True, "holder": info}
