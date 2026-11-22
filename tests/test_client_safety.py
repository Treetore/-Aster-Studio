"""client.py / safety.py / jobs.py 的离线单元测试（外加可跳过的只读在线检查）。

原则：
* 默认全部离线：HTTP 部分用本机 ``ThreadingHTTPServer`` 假服务，硬件部分用
  ``unittest.mock`` 打桩，不依赖真实 ComfyUI，也不提交任何 prompt。
* 只有 ``ComfyLiveReadOnlyTest`` 在 127.0.0.1:8188 真的在线时才跑，且只做 GET。
* 断言里不出现 emoji/中文打印（GBK 控制台安全），全部信息用 ASCII。
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock
from urllib.parse import parse_qs, urlparse

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from comfy_mcp import client as client_mod  # noqa: E402
from comfy_mcp import config  # noqa: E402
from comfy_mcp import jobs as jobs_mod  # noqa: E402
from comfy_mcp import safety as safety_mod  # noqa: E402

GB = 1024 ** 3


# --------------------------------------------------------------------------- 假服务
class _FakeHandler(BaseHTTPRequestHandler):
    """极简 ComfyUI 假 HTTP 服务，行为由 ``self.server.state`` 控制。"""

    protocol_version = "HTTP/1.0"

    def log_message(self, format, *args):  # noqa: A002,D401 - 静默访问日志
        pass

    # -- 工具 --
    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        return self.rfile.read(length) if length > 0 else b""

    def _send(self, code: int, payload=None, raw: bytes | None = None, ctype: str = "application/json") -> None:
        if raw is not None:
            body = raw
        elif payload is not None:
            body = json.dumps(payload).encode("utf-8")
        else:
            body = b""
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    # -- GET --
    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler 约定
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        state = self.server.state
        state["get_calls"].append((path, query))
        state["last_get"] = (path, query)

        if path == "/system_stats":
            if state.get("stats_mode") == "html":
                self._send(200, raw=b"<html>nope</html>", ctype="text/html")
            elif state.get("stats_mode") == "fail":
                self._send(500, {"error": "boom"})
            else:
                self._send(
                    200,
                    {
                        "system": {
                            "ram_total": 32 * GB,
                            "ram_free": 12 * GB,
                            "comfyui_version": "0.33.1",
                        },
                        "devices": [],
                    },
                )
        elif path == "/queue":
            self._send(200, state.get("queue") or {"queue_running": [], "queue_pending": []})
        elif path == "/api/jobs":
            if state.get("jobs_api_disabled"):
                self._send(404, {"error": "not found"})
                return
            limit = 50
            try:
                limit = int((query.get("limit") or ["50"])[0])
            except (TypeError, ValueError):
                pass
            statuses = query.get("status") or []
            jobs = list(state.get("jobs") or [])
            if statuses:
                jobs = [job for job in jobs if job.get("status") in statuses]
            self._send(
                200,
                {
                    "jobs": jobs[:limit],
                    "pagination": {"offset": 0, "limit": limit, "total": len(jobs), "has_more": False},
                },
            )
        elif path.startswith("/api/jobs/"):
            job_id = path.split("/")[-1]
            job = next((j for j in (state.get("jobs") or []) if j.get("id") == job_id), None)
            if job is None:
                self._send(404, {"error": "job not found"})
            else:
                self._send(200, job)
        elif path == "/object_info":
            self._send(200, {"Ping": {"input": {"required": {}}}})
        elif path.startswith("/object_info/"):
            self._send(200, {"Ping": {"input": {"required": {}}}})
        elif path == "/history":
            self._send(200, state.get("history") or {})
        elif path.startswith("/history/"):
            job_id = path.split("/")[-1]
            entry = (state.get("history") or {}).get(job_id)
            self._send(200, {job_id: entry} if entry else {})
        elif path == "/view":
            state["last_view"] = query
            filename = (query.get("filename") or [""])[0]
            if filename == "missing.png":
                self._send(404, {"error": "file not found"})
            else:
                self._send(200, raw=b"IMAGEDATA", ctype="image/png")
        else:
            self._send(404, {"error": "no route"})

    # -- POST --
    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler 约定
        parsed = urlparse(self.path)
        path = parsed.path
        raw = self._read_body()
        state = self.server.state

        if path == "/prompt":
            try:
                state["prompt_payload"] = json.loads(raw.decode("utf-8"))
            except ValueError:
                state["prompt_payload"] = None
            if state.get("prompt_400"):
                self._send(
                    400,
                    {
                        "error": {"type": "prompt_outputs_failed_validation", "message": "bad"},
                        "node_errors": {
                            "1": {
                                "errors": [
                                    {
                                        "type": "custom_validation_failed",
                                        "message": "x" * 5000,
                                        "details": "image - Invalid image file",
                                    }
                                ],
                                "class_type": "LoadImage",
                            }
                        },
                    },
                )
            else:
                self._send(200, {"prompt_id": "pid-1", "number": 1, "node_errors": {}})
        elif path.startswith("/api/jobs/") and path.endswith("/cancel"):
            state["cancelled"] = path.split("/")[-2]
            self._send(200, {"cancelled": True})
        elif path == "/interrupt":
            try:
                state["interrupt"] = json.loads(raw.decode("utf-8")) if raw else {}
            except ValueError:
                state["interrupt"] = {"raw": raw.decode("utf-8", "replace")}
            self._send(200, raw=b"")
        elif path == "/free":
            try:
                state["free"] = json.loads(raw.decode("utf-8")) if raw else {}
            except ValueError:
                state["free"] = {}
            self._send(200, {"ok": True})
        elif path == "/upload/image":
            state["upload"] = {
                "content_type": self.headers.get("Content-Type", ""),
                "body": raw,
            }
            self._send(200, {"name": "uploaded.png", "subfolder": "", "type": "input"})
        else:
            self._send(404, {"error": "no route"})


class FakeComfyServer:
    """在本机随机端口起一个假 ComfyUI。"""

    def __init__(self) -> None:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _FakeHandler)
        self.httpd.state = {
            "get_calls": [],
            "last_get": None,
            "jobs": [],
            "history": {},
            "queue": {"queue_running": [], "queue_pending": []},
            "prompt_payload": None,
            "prompt_400": False,
            "stats_mode": "ok",
            "jobs_api_disabled": False,
        }
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self.thread.start()

    @property
    def state(self) -> dict:
        return self.httpd.state

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}"

    def close(self) -> None:
        try:
            self.httpd.shutdown()
        finally:
            self.httpd.server_close()
            self.thread.join(timeout=5.0)


def _unused_tcp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


# --------------------------------------------------------------------- client
class ComfyClientTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = FakeComfyServer()
        self.client = client_mod.ComfyClient(self.server.url, timeout=5.0)

    def tearDown(self) -> None:
        self.client.session.close()
        self.server.close()

    # -- 探测 --
    def test_is_up_true(self) -> None:
        self.assertTrue(self.client.is_up())

    def test_is_up_false_when_unreachable(self) -> None:
        dead = client_mod.ComfyClient(f"http://127.0.0.1:{_unused_tcp_port()}", timeout=0.5)
        try:
            self.assertFalse(dead.is_up())
            with self.assertRaises(client_mod.ComfyUnavailable):
                dead.system_stats()
            with self.assertRaises(client_mod.ComfyError):
                dead.prompt({"1": {"class_type": "LoadImage", "inputs": {}}})
        finally:
            dead.session.close()

    def test_system_stats(self) -> None:
        stats = self.client.system_stats()
        self.assertEqual(stats["system"]["comfyui_version"], "0.33.1")
        self.assertGreater(stats["system"]["ram_total"], 0)

    def test_system_stats_invalid_json(self) -> None:
        self.server.state["stats_mode"] = "html"
        with self.assertRaises(client_mod.ComfyError):
            self.client.system_stats()

    def test_system_stats_http_error(self) -> None:
        self.server.state["stats_mode"] = "fail"
        with self.assertRaises(client_mod.ComfyError) as ctx:
            self.client.system_stats()
        self.assertIn("500", str(ctx.exception))

    def test_object_info(self) -> None:
        self.assertIn("Ping", self.client.object_info())
        self.assertIn("Ping", self.client.object_info("Ping"))
        self.assertIn("/object_info/Ping", [p for p, _q in self.server.state["get_calls"]])

    def test_queue(self) -> None:
        data = self.client.queue()
        self.assertIn("queue_running", data)
        self.assertIn("queue_pending", data)

    # -- 提交 --
    def test_prompt_success(self) -> None:
        workflow = {"1": {"class_type": "LoadImage", "inputs": {"image": "example.png"}}}
        result = self.client.prompt(workflow, prompt_id="pid-1")
        self.assertEqual(result["prompt_id"], "pid-1")
        payload = self.server.state["prompt_payload"]
        self.assertEqual(payload["prompt"], workflow)
        self.assertEqual(payload["client_id"], self.client.client_id)
        self.assertEqual(payload["prompt_id"], "pid-1")

    def test_prompt_400_includes_node_errors(self) -> None:
        self.server.state["prompt_400"] = True
        with self.assertRaises(client_mod.ComfyError) as ctx:
            self.client.prompt({"1": {"class_type": "LoadImage", "inputs": {"image": "nope.png"}}})
        msg = str(ctx.exception)
        self.assertIn("node_errors", msg)
        self.assertIn("custom_validation_failed", msg)
        self.assertIn("400", msg)
        self.assertLess(len(msg), 2600)  # node_errors JSON 截断到 2000 字符

    # -- 任务 API --
    def test_jobs_unwraps_and_filters(self) -> None:
        self.server.state["jobs"] = [
            {"id": "a", "status": "completed", "outputs": {}},
            {"id": "b", "status": "pending", "outputs": {}},
            {"id": "c", "status": "failed", "outputs": {}},
        ]
        self.assertEqual(len(self.client.jobs()), 3)
        self.assertEqual([j["id"] for j in self.client.jobs(status=["failed"])], ["c"])
        self.assertEqual([j["id"] for j in self.client.jobs(status=["pending", "failed"])], ["b", "c"])
        self.assertEqual(len(self.client.jobs(limit=2)), 2)
        query = self.server.state["last_get"][1]
        self.assertEqual(query["limit"], ["2"])

    def test_job_found_and_missing(self) -> None:
        self.server.state["jobs"] = [
            {"id": "a", "status": "completed", "outputs": {"2": {"images": [{"filename": "x.png"}]}}}
        ]
        job = self.client.job("a")
        self.assertIsNotNone(job)
        self.assertEqual(job["id"], "a")
        self.assertIsNone(self.client.job("nope"))

    def test_job_history_fallback(self) -> None:
        self.server.state["history"] = {
            "hist-1": {
                "outputs": {"2": {"images": [{"filename": "h.png", "subfolder": "", "type": "output"}]}},
                "status": {"status_str": "success", "completed": True, "messages": []},
            }
        }
        job = self.client.job("hist-1")
        self.assertIsNotNone(job)
        self.assertEqual(job["id"], "hist-1")
        self.assertEqual(job["status"], "completed")
        self.assertIn("2", job["outputs"])

    def test_jobs_fallback_when_api_missing(self) -> None:
        self.server.state["jobs_api_disabled"] = True
        self.server.state["queue"] = {
            "queue_running": [[0, "run-1", {}, {}, ["2"]]],
            "queue_pending": [[1, "pend-1", {}, {}, ["2"]]],
        }
        self.server.state["history"] = {
            "done-1": {
                "outputs": {},
                "status": {"status_str": "success", "completed": True, "messages": []},
            }
        }
        rows = self.client.jobs()
        self.assertEqual({row["id"] for row in rows}, {"run-1", "pend-1", "done-1"})
        by_id = {row["id"]: row["status"] for row in rows}
        self.assertEqual(by_id["run-1"], "in_progress")
        self.assertEqual(by_id["pend-1"], "pending")
        self.assertEqual(by_id["done-1"], "completed")
        self.assertEqual([row["id"] for row in self.client.jobs(status=["in_progress"])], ["run-1"])

    def test_cancel_interrupt_free(self) -> None:
        self.assertTrue(self.client.cancel("abc")["cancelled"])
        self.assertEqual(self.server.state["cancelled"], "abc")
        self.assertTrue(self.client.interrupt())
        self.assertEqual(self.server.state["interrupt"], {})
        self.assertTrue(self.client.interrupt("p-9"))
        self.assertEqual(self.server.state["interrupt"], {"prompt_id": "p-9"})
        self.assertTrue(self.client.free())
        self.assertEqual(self.server.state["free"], {"unload_models": True, "free_memory": True})
        self.assertTrue(self.client.free(unload_models=False, free_memory=True))
        self.assertEqual(self.server.state["free"], {"unload_models": False, "free_memory": True})

    def test_history(self) -> None:
        self.assertEqual(self.client.history(), {})
        self.assertEqual(self.client.history("p-1"), {})
        self.assertIn("/history/p-1", [p for p, _q in self.server.state["get_calls"]])

    # -- 上传 / 下载 --
    def test_upload_image_multipart(self) -> None:
        tmpdir = tempfile.mkdtemp(prefix="comfy_up_")
        try:
            src = os.path.join(tmpdir, "ref image.png")
            with open(src, "wb") as fh:
                fh.write(b"\x89PNG\r\n\x1a\nfake")
            result = self.client.upload_image(src)
            self.assertEqual(result["name"], "uploaded.png")
            self.assertEqual(result["type"], "input")
            upload = self.server.state["upload"]
            self.assertIn("multipart/form-data", upload["content_type"])
            body = upload["body"]
            self.assertIn(b'name="image"', body)
            self.assertIn(b'filename="ref image.png"', body)
            self.assertIn(b'name="type"', body)
            self.assertIn(b"input", body)
            self.assertIn(b'name="overwrite"', body)
            self.assertIn(b"true", body)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_upload_image_missing_file(self) -> None:
        with self.assertRaises(client_mod.ComfyError):
            self.client.upload_image(os.path.join(tempfile.gettempdir(), "no_such_file_xyz.png"))

    def test_download_and_save_output(self) -> None:
        self.assertEqual(self.client.download("a.png"), b"IMAGEDATA")
        view = self.server.state["last_view"]
        self.assertEqual(view["filename"], ["a.png"])
        self.assertEqual(view["type"], ["output"])
        with self.assertRaises(client_mod.ComfyError):
            self.client.download("missing.png")

        tmpdir = tempfile.mkdtemp(prefix="comfy_dl_")
        try:
            dest = os.path.join(tmpdir, "nested", "out.png")
            saved = self.client.save_output("a.png", "", "output", dest)
            self.assertEqual(saved, os.path.abspath(dest))
            with open(dest, "rb") as fh:
                self.assertEqual(fh.read(), b"IMAGEDATA")
            self.assertFalse(os.path.exists(dest + ".tmp"))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_job_outputs_extracts_files(self) -> None:
        self.server.state["jobs"] = [
            {
                "id": "j1",
                "status": "completed",
                "outputs": {
                    "2": {
                        "images": [{"filename": "a.png", "subfolder": "", "type": "output"}],
                        "videos": [{"filename": "v.mp4", "subfolder": "video", "type": "output"}],
                    },
                    "3": {"gifs": [{"filename": "temp.gif", "subfolder": "", "type": "temp"}]},
                },
            }
        ]
        rows = self.client.job_outputs("j1")
        self.assertEqual(len(rows), 3)
        by_name = {row["filename"]: row for row in rows}
        self.assertEqual(by_name["a.png"]["node_id"], "2")
        self.assertEqual(by_name["a.png"]["type"], "output")
        self.assertIn("filename_abs", by_name["a.png"])
        self.assertTrue(by_name["a.png"]["filename_abs"].endswith(os.path.join("", "a.png")))
        self.assertIn("filename_abs", by_name["v.mp4"])
        self.assertNotIn("filename_abs", by_name["temp.gif"])
        self.assertEqual(self.client.job_outputs("missing-job"), [])


# -------------------------------------------------------------------- JobStore
class JobStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="comfy_jobs_")
        self.path = os.path.join(self.tmpdir, "jobs.json")
        self.store = jobs_mod.JobStore(self.path)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_add_get_defaults_and_file(self) -> None:
        job = self.store.add({"template": "t1", "params": {"a": 1}})
        self.assertTrue(job["id"])
        self.assertEqual(job["status"], "queued")
        self.assertTrue(job["created_at"])
        self.assertTrue(job["updated_at"])
        self.assertEqual(job["outputs"], [])
        self.assertEqual(job["params"], {"a": 1})
        self.assertEqual(self.store.get(job["id"])["template"], "t1")
        self.assertIsNone(self.store.get("nope"))
        self.assertTrue(os.path.isfile(self.path))
        with open(self.path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        self.assertEqual(len(raw["jobs"]), 1)
        self.assertEqual(raw["jobs"][0]["id"], job["id"])

    def test_add_uses_prompt_id_as_default_id(self) -> None:
        job = self.store.add({"prompt_id": "p-9", "template": "t"})
        self.assertEqual(job["id"], "p-9")
        self.assertEqual(job["prompt_id"], "p-9")

    def test_prompt_truncated_to_500(self) -> None:
        job = self.store.add({"prompt": "y" * 900})
        self.assertEqual(len(job["pprompt"]), 500)
        self.assertEqual(self.store.get(job["id"])["pprompt"], "y" * 500)

    def test_update_and_timestamps(self) -> None:
        job = self.store.add({"template": "t"})
        running = self.store.update(job["id"], status="running")
        self.assertEqual(running["status"], "running")
        self.assertTrue(running["started_at"])
        finished = self.store.update(job["id"], status="completed", outputs=["a.png"], error=None)
        self.assertEqual(finished["status"], "completed")
        self.assertTrue(finished["finished_at"])
        self.assertEqual(finished["outputs"], ["a.png"])
        with self.assertRaises(KeyError):
            self.store.update("no-such-id", status="failed")
        with self.assertRaises(ValueError):
            self.store.update(job["id"], id="other-id")

    def test_list_order_and_filter(self) -> None:
        first = self.store.add({"template": "a"})
        second = self.store.add({"template": "b"})
        third = self.store.add({"template": "c"})
        self.store.update(second["id"], status="completed")
        self.assertEqual([j["id"] for j in self.store.list()], [third["id"], second["id"], first["id"]])
        self.assertEqual([j["id"] for j in self.store.list(status=["completed"])], [second["id"]])
        self.assertEqual(len(self.store.list(limit=2)), 2)
        self.assertEqual(self.store.list(limit=0), [])

    def test_delete(self) -> None:
        job = self.store.add({"template": "t"})
        self.assertTrue(self.store.delete(job["id"]))
        self.assertFalse(self.store.delete(job["id"]))
        self.assertIsNone(self.store.get(job["id"]))
        self.assertEqual(len(self.store), 0)

    def test_reload_from_disk(self) -> None:
        job = self.store.add({"template": "t", "params": {"x": [1, 2]}})
        self.store.update(job["id"], status="completed", outputs=["o.mp4"])
        reopened = jobs_mod.JobStore(self.path)
        self.assertEqual(len(reopened), 1)
        loaded = reopened.get(job["id"])
        self.assertEqual(loaded["params"], {"x": [1, 2]})
        self.assertEqual(loaded["outputs"], ["o.mp4"])
        self.assertEqual(loaded["status"], "completed")

    def test_corrupt_file_is_tolerated(self) -> None:
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{this is not json")
        broken = jobs_mod.JobStore(self.path)
        self.assertEqual(len(broken), 0)
        broken.add({"template": "t"})
        self.assertEqual(len(broken), 1)

    def test_concurrent_adds(self) -> None:
        errors: list[BaseException] = []

        def worker(index: int) -> None:
            try:
                for i in range(20):
                    self.store.add({"template": f"t{index}", "params": {"i": i}})
            except BaseException as exc:  # noqa: BLE001 - 收集后统一断言
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30.0)

        self.assertEqual(errors, [])
        rows = self.store.list(limit=1000)
        self.assertEqual(len(rows), 160)
        self.assertEqual(len({row["id"] for row in rows}), 160)
        with open(self.path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        self.assertEqual(len(raw["jobs"]), 160)


# ---------------------------------------------------------------- estimate_cost
class EstimateCostTest(unittest.TestCase):
    def test_draft_is_low_risk(self) -> None:
        cost = safety_mod.estimate_cost({"width": 608, "height": 352, "length": 48, "steps": 8})
        self.assertEqual(cost["pixels"], 608 * 352)
        self.assertEqual(cost["frames"], 48)
        self.assertAlmostEqual(cost["duration_s"], 2.0, places=1)
        self.assertEqual(cost["vram_risk"], "low")
        self.assertEqual(cost["ram_risk"], "low")
        self.assertGreater(cost["eta_minutes"], 0.0)
        self.assertIsInstance(cost["notes"], list)

    def test_large_is_high_risk(self) -> None:
        cost = safety_mod.estimate_cost(
            {"width": 2048, "height": 1152, "length": 400, "steps": 24}
        )
        self.assertEqual(cost["vram_risk"], "high")
        self.assertEqual(cost["ram_risk"], "high")
        text = " ".join(cost["notes"])
        self.assertIn("max_pixels", text)
        self.assertIn("max_length", text)

    def test_duration_only_and_empty(self) -> None:
        cost = safety_mod.estimate_cost({"duration_s": 3.0, "width": 608, "height": 352})
        self.assertEqual(cost["frames"], 72)
        self.assertAlmostEqual(cost["duration_s"], 3.0, places=1)
        empty = safety_mod.estimate_cost({})
        for key in ("pixels", "frames", "duration_s", "vram_risk", "ram_risk", "eta_minutes", "notes"):
            self.assertIn(key, empty)
        self.assertGreater(empty["frames"], 0)
        self.assertGreater(empty["pixels"], 0)

    def test_notes_are_ascii(self) -> None:
        for resolved in (
            {"width": 608, "height": 352, "length": 48},
            {"width": 2048, "height": 1152, "length": 400},
            {"width": 1000, "height": 570, "length": 49, "profile": "high"},
        ):
            for note in safety_mod.estimate_cost(resolved)["notes"]:
                self.assertTrue(note.isascii(), f"note is not ASCII-safe: {note!r}")


# ----------------------------------------------------------------------- Guard
class _StubClient:
    """Guard 用的最小 client 假件。"""

    def __init__(
        self,
        up: bool = True,
        stats: dict | None = None,
        stats_error: BaseException | None = None,
        queue_data: dict | None = None,
    ) -> None:
        self.up = up
        self.stats = stats if stats is not None else {"system": {"ram_total": 32 * GB, "ram_free": 12 * GB}}
        self.stats_error = stats_error
        self.queue_data = queue_data or {"queue_running": [], "queue_pending": []}
        self.interrupted: list[str | None] = []

    def is_up(self) -> bool:
        return self.up

    def system_stats(self) -> dict:
        if self.stats_error is not None:
            raise self.stats_error
        return self.stats

    def queue(self) -> dict:
        return self.queue_data

    def interrupt(self, prompt_id: str | None = None) -> bool:
        self.interrupted.append(prompt_id)
        return True


def _settings(**overrides) -> dict:
    settings = copy.deepcopy(config.DEFAULT_SETTINGS)
    settings["safety"]["watch_interval_s"] = 0.01
    settings["safety"]["unlimited_h3"] = False
    settings["safety"].update(overrides)
    return settings


def _hardware(ram_free=12.0, disk=50.0, vram_free=6000) -> dict:
    return {
        "gpu_name": "NVIDIA GeForce RTX 4070 Laptop GPU",
        "vram_total_mb": 8188,
        "vram_used_mb": 8188 - vram_free,
        "vram_free_mb": vram_free,
        "ram_total_gb": 32.0,
        "ram_free_gb": ram_free,
        "disk_free_gb": disk,
        "checks": {"nvidia_smi_ok": True, "memory_api_ok": True, "disk_ok": True, "os": "nt"},
    }


SMALL = {"width": 608, "height": 352, "length": 48, "steps": 8}
BIG = {"width": 2048, "height": 1152, "length": 400, "steps": 24}


class GuardPreflightTest(unittest.TestCase):
    def _run(self, hw: dict, client: _StubClient, resolved: dict = SMALL, active_jobs: int = 0) -> dict:
        with mock.patch.object(safety_mod, "hardware", return_value=hw):
            guard = safety_mod.Guard(client, _settings())
            result = guard.preflight(resolved, active_jobs=active_jobs)
        for key in ("ok", "risk", "reasons", "suggestions", "snapshot"):
            self.assertIn(key, result)
        self.assertIn("cost", result["snapshot"])
        self.assertIsInstance(result["reasons"], list)
        self.assertIsInstance(result["suggestions"], list)
        return result

    def test_ok_path(self) -> None:
        result = self._run(_hardware(), _StubClient())
        self.assertTrue(result["ok"], result["reasons"])
        self.assertIn(result["risk"], ("low", "medium"))
        self.assertIn("hardware", result["snapshot"])
        self.assertTrue(result["snapshot"]["comfy"]["up"])

    def test_trip_ram_free_blocks(self) -> None:
        result = self._run(_hardware(ram_free=1.0), _StubClient())
        self.assertFalse(result["ok"])
        self.assertEqual(result["risk"], "high")
        self.assertTrue(any("RAM" in reason and "trip" in reason for reason in result["reasons"]))

    def test_below_min_ram_blocks(self) -> None:
        result = self._run(_hardware(ram_free=4.0), _StubClient())
        self.assertFalse(result["ok"])
        self.assertTrue(any("minimum" in reason for reason in result["reasons"]))
        self.assertTrue(result["suggestions"])

    def test_low_disk_blocks(self) -> None:
        result = self._run(_hardware(disk=2.0), _StubClient())
        self.assertFalse(result["ok"])
        self.assertTrue(any("disk" in reason.lower() for reason in result["reasons"]))

    def test_low_vram_blocks(self) -> None:
        result = self._run(_hardware(vram_free=100), _StubClient())
        self.assertFalse(result["ok"])
        self.assertTrue(any("VRAM" in reason for reason in result["reasons"]))

    def test_comfy_down_blocks(self) -> None:
        result = self._run(
            _hardware(), _StubClient(stats_error=client_mod.ComfyError("connection refused"))
        )
        self.assertFalse(result["ok"])
        self.assertTrue(any("not reachable" in reason for reason in result["reasons"]))

    def test_active_job_blocks(self) -> None:
        result = self._run(_hardware(), _StubClient(), active_jobs=1)
        self.assertFalse(result["ok"])
        self.assertTrue(any("single_flight" in reason for reason in result["reasons"]))

    def test_queue_full_blocks(self) -> None:
        queue_data = {"queue_running": [], "queue_pending": [[0, "p1", {}, {}, []], [1, "p2", {}, {}, []]]}
        result = self._run(_hardware(), _StubClient(queue_data=queue_data))
        self.assertFalse(result["ok"])
        self.assertTrue(any("queue" in reason for reason in result["reasons"]))

    def test_big_job_blocks(self) -> None:
        result = self._run(_hardware(), _StubClient(), resolved=BIG)
        self.assertFalse(result["ok"])
        self.assertEqual(result["risk"], "high")
        self.assertTrue(any("risk is high" in reason for reason in result["reasons"]))

    def test_unknown_ram_is_medium_risk(self) -> None:
        stats = {"system": {"ram_total": 32 * GB, "ram_free": 0}}
        result = self._run(_hardware(ram_free=None), _StubClient(stats=stats))
        self.assertTrue(result["ok"], result["reasons"])
        self.assertEqual(result["risk"], "medium")
        self.assertTrue(any("unknown" in reason for reason in result["reasons"]))


class GuardWatchTest(unittest.TestCase):
    def _cb(self):
        event = threading.Event()
        reasons: list[str] = []

        def on_trip(reason: str) -> None:
            reasons.append(reason)
            event.set()

        return event, reasons, on_trip

    def test_watch_trips_on_low_ram(self) -> None:
        client = _StubClient()
        event, reasons, on_trip = self._cb()
        with mock.patch.object(safety_mod, "ram_free_gb", return_value=1.0), mock.patch.object(
            safety_mod, "vram_usage", return_value=None
        ):
            guard = safety_mod.Guard(client, _settings())
            guard.watch("job-ram", on_trip)
            try:
                self.assertTrue(event.wait(5.0), "watch did not trip on low RAM")
            finally:
                guard.stop_watch()
        self.assertIn("RAM", guard.trip_reason() or "")
        self.assertEqual(client.interrupted, ["job-ram"])
        self.assertEqual(len(reasons), 1)
        self.assertFalse(guard.watching)

    def test_watch_supports_two_arg_callback(self) -> None:
        # server.py 的回调风格：_on_guard_trip(job_id, reason)
        client = _StubClient()
        event = threading.Event()
        calls: list[tuple[str, str]] = []

        def on_trip(job_id: str, reason: str) -> None:
            calls.append((job_id, reason))
            event.set()

        with mock.patch.object(safety_mod, "ram_free_gb", return_value=1.0), mock.patch.object(
            safety_mod, "vram_usage", return_value=None
        ):
            guard = safety_mod.Guard(client, _settings())
            guard.watch("job-two", on_trip)
            try:
                self.assertTrue(event.wait(5.0), "two-arg callback was not invoked")
            finally:
                guard.stop_watch()
        self.assertEqual(calls[0][0], "job-two")
        self.assertIn("RAM", calls[0][1])
        self.assertEqual(client.interrupted, ["job-two"])

    def test_watch_supports_zero_arg_callback(self) -> None:
        client = _StubClient()
        event = threading.Event()

        def on_trip() -> None:
            event.set()

        with mock.patch.object(safety_mod, "ram_free_gb", return_value=1.0), mock.patch.object(
            safety_mod, "vram_usage", return_value=None
        ):
            guard = safety_mod.Guard(client, _settings())
            guard.watch("job-zero", on_trip)
            try:
                self.assertTrue(event.wait(5.0), "zero-arg callback was not invoked")
            finally:
                guard.stop_watch()
        self.assertIn("RAM", guard.trip_reason() or "")

    def test_watch_trips_on_system_stats_failures(self) -> None:
        client = _StubClient(stats_error=client_mod.ComfyError("down"))
        event, _reasons, on_trip = self._cb()
        with mock.patch.object(safety_mod, "ram_free_gb", return_value=12.0), mock.patch.object(
            safety_mod, "vram_usage", return_value=None
        ):
            guard = safety_mod.Guard(client, _settings())
            guard.watch("job-sys", on_trip)
            try:
                self.assertTrue(event.wait(5.0), "watch did not trip on system_stats failures")
            finally:
                guard.stop_watch()
        self.assertIn("system_stats", guard.trip_reason() or "")
        self.assertEqual(client.interrupted, ["job-sys"])

    def test_watch_warns_but_does_not_trip_on_low_vram(self) -> None:
        client = _StubClient()
        _event, _reasons, on_trip = self._cb()
        with mock.patch.object(safety_mod, "ram_free_gb", return_value=12.0), mock.patch.object(
            safety_mod,
            "vram_usage",
            return_value={"total_mb": 8188, "used_mb": 8100, "free_mb": 88},
        ):
            guard = safety_mod.Guard(client, _settings())
            guard.watch("job-vram", on_trip)
            try:
                time.sleep(0.2)
            finally:
                guard.stop_watch()
        self.assertIsNone(guard.trip_reason())
        self.assertEqual(client.interrupted, [])
        self.assertTrue(any("VRAM" in warning for warning in guard.warnings()))

    def test_watch_healthy_does_not_trip(self) -> None:
        client = _StubClient()
        event, _reasons, on_trip = self._cb()
        with mock.patch.object(safety_mod, "ram_free_gb", return_value=12.0), mock.patch.object(
            safety_mod,
            "vram_usage",
            return_value={"total_mb": 8188, "used_mb": 2000, "free_mb": 6188},
        ):
            guard = safety_mod.Guard(client, _settings())
            guard.watch("job-ok", on_trip)
            try:
                time.sleep(0.15)
                self.assertTrue(guard.watching)
            finally:
                guard.stop_watch()
        self.assertFalse(event.is_set())
        self.assertIsNone(guard.trip_reason())
        self.assertEqual(client.interrupted, [])
        self.assertFalse(guard.watching)
        guard.stop_watch()  # 幂等：再停一次不该炸


# --------------------------------------------------------------- 真实硬件探测
class HardwareLiveTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("nvidia-smi"), "nvidia-smi not on PATH")
    def test_hardware_and_vram(self) -> None:
        hw = safety_mod.hardware()
        for key in (
            "gpu_name",
            "vram_total_mb",
            "vram_used_mb",
            "vram_free_mb",
            "ram_total_gb",
            "ram_free_gb",
            "disk_free_gb",
            "checks",
        ):
            self.assertIn(key, hw)
        self.assertIn("nvidia_smi_ok", hw["checks"])
        if hw["checks"]["nvidia_smi_ok"]:
            self.assertGreater(hw["vram_total_mb"], 0)
            usage = safety_mod.vram_usage()
            self.assertIsNotNone(usage)
            self.assertGreater(usage["total_mb"], 0)
            self.assertGreaterEqual(usage["free_mb"], 0)

    def test_ram_free_gb(self) -> None:
        if os.name != "nt" and not os.path.exists("/proc/meminfo"):
            self.skipTest("no memory probe on this platform")
        self.assertGreater(safety_mod.ram_free_gb(), 0.0)


# ----------------------------------------------------------- 只读在线（可跳过）
def _comfy_online() -> bool:
    try:
        probe = client_mod.ComfyClient(config.COMFY_URL, timeout=2.0)
        try:
            return probe.is_up()
        finally:
            probe.session.close()
    except Exception:  # noqa: BLE001 - 探测失败就是不在线
        return False


@unittest.skipUnless(_comfy_online(), "ComfyUI is not running at " + config.COMFY_URL)
class ComfyLiveReadOnlyTest(unittest.TestCase):
    """只读 GET，绝不提交 prompt / interrupt / free。"""

    def setUp(self) -> None:
        self.client = client_mod.ComfyClient(config.COMFY_URL, timeout=10.0)

    def tearDown(self) -> None:
        self.client.session.close()

    def test_read_only_endpoints(self) -> None:
        self.assertTrue(self.client.is_up())
        stats = self.client.system_stats()
        self.assertIn("system", stats)
        self.assertGreater(stats["system"]["ram_total"], 0)
        queue_data = self.client.queue()
        self.assertIn("queue_running", queue_data)
        self.assertIn("queue_pending", queue_data)
        jobs = self.client.jobs(limit=3)
        self.assertIsInstance(jobs, list)
        info = self.client.object_info("SaveImage")
        self.assertIn("SaveImage", info)
        self.assertIsInstance(self.client.history(), dict)
        if jobs:
            detail = self.client.job(jobs[0]["id"])
            self.assertIsNotNone(detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
