"""Priority queue unit tests (no GPU, no ComfyUI)."""
from __future__ import annotations

import time

from job_queue import JobQueue


def wait_for(fn, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.02)
    return False


def test_priority_order_and_completion():
    busy = {"v": True}
    order: list[int] = []

    def runner(template, params):
        order.append(params["n"])
        return {"ok": True, "job_id": f"j{params['n']}"}

    q = JobQueue(is_busy=lambda: busy["v"], poll_s=0.02)
    q.set_runner(runner)
    a = q.submit("t", {"n": 1}, priority=5)
    b = q.submit("t", {"n": 2}, priority=1)
    time.sleep(0.1)
    assert q.get(a["id"])["status"] == "queued"
    busy["v"] = False
    assert wait_for(lambda: q.get(a["id"])["status"] == "completed")
    assert wait_for(lambda: q.get(b["id"])["status"] == "completed")
    assert order == [2, 1]


def test_cancel_queued_item():
    q = JobQueue(is_busy=lambda: True, poll_s=0.05)
    q.set_runner(lambda t, p: {"ok": True})
    item = q.submit("t", {})
    assert q.cancel(item["id"]) is True
    assert q.get(item["id"])["status"] == "cancelled"
    assert q.cancel(item["id"]) is False
