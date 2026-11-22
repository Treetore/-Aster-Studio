"""Lightweight in-process priority queue for generation jobs.

The queue does not bypass the global job lock: the worker waits until the lock is
free, then calls the configured runner (normally ComfyService.tool_generate).
Lower priority numbers run first; equal priorities are FIFO.
"""
from __future__ import annotations

import heapq
import threading
import time
import uuid
from typing import Any, Callable

import job_lock


class JobQueue:
    def __init__(self, is_busy: Callable[[], bool] | None = None, poll_s: float = 2.0) -> None:
        self._heap: list[tuple[int, float, str]] = []
        self._items: dict[str, dict] = {}
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._is_busy = is_busy or (lambda: bool(job_lock.status().get("busy")))
        self._poll_s = poll_s

    def submit(self, template: str, params: dict, priority: int = 5,
               meta: dict | None = None) -> dict:
        qid = uuid.uuid4().hex[:12]
        item = {"id": qid, "template": template, "params": params,
                "priority": int(priority), "status": "queued",
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "started_at": None, "finished_at": None,
                "result": None, "error": None, "meta": meta or {}}
        with self._lock:
            self._items[qid] = item
            heapq.heappush(self._heap, (int(priority), time.time(), qid))
        self._ensure_worker()
        self._wake.set()
        return item

    def list(self) -> list[dict]:
        with self._lock:
            items = list(self._items.values())
        order = {"running": 0, "queued": 1, "completed": 2, "failed": 2, "cancelled": 2}
        items.sort(key=lambda x: (order.get(x["status"], 3), x["priority"], x["created_at"]))
        return items

    def get(self, qid: str) -> dict | None:
        with self._lock:
            item = self._items.get(qid)
            return dict(item) if item else None

    def cancel(self, qid: str) -> bool:
        with self._lock:
            item = self._items.get(qid)
            if not item or item["status"] in ("completed", "failed", "cancelled"):
                return False
            item["status"] = "cancelled"
            item["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            return True

    def _next(self) -> dict | None:
        with self._lock:
            while self._heap:
                _p, _ts, qid = heapq.heappop(self._heap)
                item = self._items.get(qid)
                if item and item["status"] == "queued":
                    return item
            return None

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run, name="generation-queue", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        runner = getattr(self, "_runner", None)
        while True:
            item = self._next()
            if item is None:
                self._wake.wait(5)
                self._wake.clear()
                continue
            if self._is_busy():
                with self._lock:
                    item["status"] = "queued"
                    heapq.heappush(self._heap, (item["priority"], time.time(), item["id"]))
                time.sleep(self._poll_s)
                continue
            item["status"] = "running"
            item["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            try:
                result = runner(item["template"], item["params"]) if runner else {
                    "ok": False, "error": "queue runner not configured"}
                item["result"] = result
                item["status"] = "completed" if result.get("ok") else "failed"
                if not result.get("ok"):
                    item["error"] = result.get("error") or result.get("reason") or "generation failed"
            except Exception as exc:  # noqa: BLE001
                item["status"] = "failed"
                item["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                item["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    def set_runner(self, runner: Callable[[str, dict], Any]) -> None:
        self._runner = runner
        self._ensure_worker()
