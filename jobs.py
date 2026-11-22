"""本地任务存储（``data/jobs.json``）。

单进程、多线程使用：所有读写都在 ``threading.RLock`` 内完成，落盘用
``tmp + os.replace`` 原子替换，进程被杀不会留下半截 JSON。

记录字段（与 SPEC 一致）::

    id, prompt_id, template, params, resolved,
    status (queued|running|completed|failed|cancelled|aborted),
    created_at, updated_at, started_at, finished_at,
    error, outputs: list[str], client_id, mode, pprompt (截断 500 字)
"""
from __future__ import annotations

import copy
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

try:
    from . import config as _config
except Exception:  # pragma: no cover - 单独导入本文件时的回退
    try:
        import config as _config  # type: ignore
    except Exception:  # pragma: no cover
        _config = None  # type: ignore

STATUSES = ("queued", "running", "completed", "failed", "cancelled", "aborted")
TERMINAL_STATUSES = ("completed", "failed", "cancelled", "aborted")
PROMPT_TRUNCATE = 500

_DEFAULTS: dict[str, Any] = {
    "id": None,
    "prompt_id": None,
    "template": None,
    "params": {},
    "resolved": {},
    "status": "queued",
    "created_at": None,
    "updated_at": None,
    "started_at": None,
    "finished_at": None,
    "error": None,
    "outputs": [],
    "client_id": None,
    "mode": None,
    "pprompt": None,
}


def _utcnow() -> str:
    """固定格式的 UTC 时间戳（字典序 == 时间序）。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_path() -> str:
    data_dir = getattr(_config, "DATA_DIR", None) or os.path.join(os.getcwd(), "data")
    return os.path.join(data_dir, "jobs.json")


class JobStore:
    """线程安全的 JSON 文件任务存储。"""

    def __init__(self, path: str | None = None) -> None:
        self.path = os.path.abspath(path or default_path())
        self._lock = threading.RLock()
        self._items: list[dict] = []
        self._index: dict[str, dict] = {}
        self._load()

    # ------------------------------------------------------------- 磁盘 I/O
    def _load(self) -> None:
        if not os.path.isfile(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            # 文件损坏时宁可当空库，也不让整个 MCP 服务起不来。
            return
        records: Iterable[Any]
        if isinstance(raw, dict):
            records = raw.get("jobs") or []
        elif isinstance(raw, list):
            records = raw
        else:
            records = []
        for rec in records:
            if not isinstance(rec, dict) or not rec.get("id"):
                continue
            job = self._normalize(rec)
            if job["id"] in self._index:
                continue
            self._items.append(job)
            self._index[job["id"]] = job

    def _save_locked(self) -> None:
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        payload = {"version": 1, "updated_at": _utcnow(), "jobs": self._items}
        tmp = f"{self.path}.{os.getpid()}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)
            os.replace(tmp, self.path)
        except OSError:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:  # pragma: no cover
                pass
            raise

    # ------------------------------------------------------------- 内部工具
    @staticmethod
    def _normalize(record: dict) -> dict:
        job = copy.deepcopy(_DEFAULTS)
        job.update({k: copy.deepcopy(v) for k, v in record.items()})
        if not job.get("outputs"):
            job["outputs"] = []
        if not isinstance(job.get("params"), dict):
            job["params"] = {}
        if not isinstance(job.get("resolved"), dict):
            job["resolved"] = {}
        if job.get("status") not in STATUSES:
            job["status"] = "queued"
        return job

    @staticmethod
    def _with_prompt(record: dict) -> dict:
        """把调用方给的 ``prompt`` 收进 ``pprompt``（截断 500 字）。"""
        out = dict(record)
        prompt = out.pop("prompt", None)
        if prompt is not None and not out.get("pprompt"):
            out["pprompt"] = str(prompt)[:PROMPT_TRUNCATE]
        if out.get("pprompt") is not None:
            out["pprompt"] = str(out["pprompt"])[:PROMPT_TRUNCATE]
        return out

    @staticmethod
    def _apply_status_times(job: dict) -> None:
        """状态流转时补 started_at / finished_at（已被显式赋值则不动）。"""
        status = job.get("status")
        if status == "running" and not job.get("started_at"):
            job["started_at"] = _utcnow()
        if status in TERMINAL_STATUSES and not job.get("finished_at"):
            job["finished_at"] = _utcnow()

    # --------------------------------------------------------------- 公开 API
    def add(self, record: dict) -> dict:
        """新增任务记录，返回存储中的副本。"""
        if not isinstance(record, dict):
            raise TypeError("JobStore.add expects a dict record")
        with self._lock:
            prepared = self._with_prompt(record)
            job = self._normalize(prepared)
            if not job.get("id"):
                job["id"] = str(job.get("prompt_id") or uuid.uuid4().hex)
            job["id"] = str(job["id"])
            if not job.get("prompt_id"):
                job["prompt_id"] = job["id"]
            if job["id"] in self._index:
                raise ValueError(f"duplicate job id: {job['id']}")
            now = _utcnow()
            if not job.get("created_at"):
                job["created_at"] = now
            job["updated_at"] = now
            self._apply_status_times(job)
            self._items.append(job)
            self._index[job["id"]] = job
            self._save_locked()
            return copy.deepcopy(job)

    def update(self, job_id: str, **fields: Any) -> dict:
        """更新任务字段（``id`` 不可改；未知字段原样保存）。"""
        with self._lock:
            job = self._index.get(str(job_id))
            if job is None:
                raise KeyError(f"job not found: {job_id}")
            if "id" in fields and str(fields["id"]) != job["id"]:
                raise ValueError("JobStore.update cannot change a job id")
            prepared = self._with_prompt({k: v for k, v in fields.items() if k != "id"})
            for key, value in prepared.items():
                job[key] = copy.deepcopy(value)
            if job.get("status") not in STATUSES:
                job["status"] = "queued"
            self._apply_status_times(job)
            job["updated_at"] = _utcnow()
            self._save_locked()
            return copy.deepcopy(job)

    def get(self, job_id: str) -> dict | None:
        """按 id 取任务，不存在返回 ``None``。"""
        with self._lock:
            job = self._index.get(str(job_id))
            return copy.deepcopy(job) if job is not None else None

    def list(self, limit: int = 50, status: list[str] | None = None) -> list[dict]:
        """按创建顺序倒序（最新在前）返回任务列表。"""
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 50
        if limit <= 0:
            return []
        wanted = {str(s) for s in (status or []) if s}
        with self._lock:
            rows = [
                copy.deepcopy(job)
                for job in reversed(self._items)
                if not wanted or job.get("status") in wanted
            ]
        return rows[:limit]

    def delete(self, job_id: str) -> bool:
        """删除任务，返回是否删掉了东西。"""
        with self._lock:
            job = self._index.pop(str(job_id), None)
            if job is None:
                return False
            self._items = [item for item in self._items if item.get("id") != job_id]
            self._save_locked()
            return True

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"JobStore(path={self.path!r}, jobs={len(self)})"
