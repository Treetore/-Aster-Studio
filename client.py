"""ComfyUI HTTP 客户端。

只依赖标准库 + requests（嵌入 Python 已装）。所有网络失败统一转成
:class:`ComfyError` / :class:`ComfyUnavailable`，上层 ``server.py`` 直接把
异常文本变成 MCP 错误响应即可。

设计要点：
* 会话复用（``requests.Session``），所有请求都有超时，不会挂死。
* ``prompt()`` 收到 HTTP 400 时把 ComfyUI 的 ``node_errors`` JSON 带进异常
  （截断到 2000 字符），方便 Agent 直接看到哪个节点哪个输入不合法。
* 新式 ``/api/jobs`` 不可用时（老版本 ComfyUI），``jobs()`` / ``job()``
  会自动回退到老的 ``/queue`` + ``/history``。
* 写文件（``save_output``）用 tmp + ``os.replace`` 原子落盘。
"""
from __future__ import annotations

import json
import mimetypes
import os
import uuid
from typing import Any

import requests

try:  # 正常包内导入
    from . import config as _config
except Exception:  # pragma: no cover - 单独导入本文件时的回退
    try:
        import config as _config  # type: ignore
    except Exception:  # pragma: no cover
        _config = None  # type: ignore

DEFAULT_URL = getattr(_config, "COMFY_URL", "http://127.0.0.1:8188")
OUTPUT_DIR = getattr(_config, "OUTPUT_DIR", None)

DEFAULT_TIMEOUT = 30.0
UPLOAD_TIMEOUT = 120.0
_ERROR_CHARS = 2000
_UP_TIMEOUT = 3.0


def _truncate(text: Any, limit: int = _ERROR_CHARS) -> str:
    """把任意文本截断到 limit 个字符（ComfyUI 的报错有时非常长）。"""
    try:
        s = text if isinstance(text, str) else str(text)
    except Exception:  # pragma: no cover - 极端对象不可 str 化
        s = repr(text)
    if len(s) <= limit:
        return s
    return s[:limit]


class ComfyError(RuntimeError):
    """ComfyUI 请求失败（HTTP 错误状态、响应不是 JSON、业务报错等）。"""


class ComfyUnavailable(ComfyError):
    """连不上 ComfyUI：连接被拒、超时、DNS 失败等。"""


class ComfyClient:
    """本地 ComfyUI 的同步 HTTP 客户端。"""

    def __init__(self, url: str | None = None, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.url = str(url or DEFAULT_URL).rstrip("/")
        self.timeout = float(timeout)
        self.client_id = uuid.uuid4().hex
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    # ------------------------------------------------------------------ 低层
    def _request(self, method: str, path: str, *, timeout: float | None = None, **kwargs: Any) -> requests.Response:
        """发请求；网络层错误一律转 :class:`ComfyUnavailable`。"""
        try:
            return self.session.request(
                method,
                self.url + path,
                timeout=self.timeout if timeout is None else float(timeout),
                **kwargs,
            )
        except requests.RequestException as exc:
            raise ComfyUnavailable(f"ComfyUI unreachable at {self.url}: {exc}") from exc

    def _json(self, resp: requests.Response, what: str) -> Any:
        """把响应解析成 JSON；非 2xx 或非法 JSON 抛 :class:`ComfyError`。"""
        if resp.status_code >= 400:
            raise ComfyError(
                f"{what} failed: HTTP {resp.status_code}: {_truncate(resp.text)}"
            )
        try:
            return resp.json()
        except ValueError as exc:
            raise ComfyError(
                f"{what} returned invalid JSON: {_truncate(resp.text, 500)}"
            ) from exc

    # ------------------------------------------------------------------ 探测
    def is_up(self) -> bool:
        """ComfyUI 是否可用（永不抛异常）。"""
        try:
            resp = self._request("GET", "/system_stats", timeout=min(self.timeout, _UP_TIMEOUT))
            if resp.status_code != 200:
                return False
            data = resp.json()
            return isinstance(data, dict)
        except (ComfyError, ValueError):
            return False
        except Exception:  # pragma: no cover - 防御性兜底
            return False

    def system_stats(self) -> dict:
        """``GET /system_stats`` → ``{"system": {...}, "devices": [...]}``。"""
        resp = self._request("GET", "/system_stats")
        data = self._json(resp, "GET /system_stats")
        if not isinstance(data, dict):
            raise ComfyError("GET /system_stats returned unexpected payload")
        return data

    def object_info(self, node_class: str | None = None) -> dict:
        """``GET /object_info[/{class}]``：节点 schema（全部或单个）。"""
        path = "/object_info" if not node_class else f"/object_info/{node_class}"
        resp = self._request("GET", path)
        data = self._json(resp, f"GET {path}")
        if not isinstance(data, dict):
            raise ComfyError(f"GET {path} returned unexpected payload")
        return data

    def queue(self) -> dict:
        """``GET /queue`` → ``{"queue_running": [...], "queue_pending": [...]}``。"""
        resp = self._request("GET", "/queue")
        data = self._json(resp, "GET /queue")
        if not isinstance(data, dict):
            raise ComfyError("GET /queue returned unexpected payload")
        return data

    # ------------------------------------------------------------------ 提交
    def prompt(
        self,
        api_workflow: dict,
        prompt_id: str | None = None,
        extra_data: dict | None = None,
    ) -> dict:
        """``POST /prompt`` 提交 API 格式工作流。

        HTTP 400（校验失败）时抛 :class:`ComfyError`，消息里带 ComfyUI 返回的
        ``error`` / ``node_errors`` JSON（截断 2000 字符）。
        """
        if not isinstance(api_workflow, dict):
            raise ComfyError("prompt() expects an API-format workflow dict")
        payload: dict[str, Any] = {"prompt": api_workflow, "client_id": self.client_id}
        if prompt_id:
            payload["prompt_id"] = str(prompt_id)
        if extra_data:
            payload["extra_data"] = extra_data

        resp = self._request("POST", "/prompt", json=payload)
        if resp.status_code >= 400:
            raise ComfyError(
                f"ComfyUI rejected prompt (HTTP {resp.status_code}): "
                + self._prompt_error_detail(resp)
            )
        data = self._json(resp, "POST /prompt")
        if not isinstance(data, dict):
            raise ComfyError("POST /prompt returned unexpected payload")
        node_errors = data.get("node_errors")
        if node_errors and not data.get("prompt_id"):
            raise ComfyError(
                "ComfyUI rejected prompt: node_errors="
                + _truncate(json.dumps(node_errors, ensure_ascii=False))
            )
        return data

    @staticmethod
    def _prompt_error_detail(resp: requests.Response) -> str:
        try:
            data = resp.json()
        except ValueError:
            return _truncate(resp.text)
        if not isinstance(data, dict):
            return _truncate(json.dumps(data, ensure_ascii=False, default=str))
        parts: list[str] = []
        err = data.get("error")
        if err:
            parts.append(
                err if isinstance(err, str) else json.dumps(err, ensure_ascii=False, default=str)
            )
        node_errors = data.get("node_errors")
        if node_errors:
            parts.append("node_errors=" + json.dumps(node_errors, ensure_ascii=False, default=str))
        return _truncate(" | ".join(parts) if parts else resp.text)

    # ------------------------------------------------------------------ 任务
    def jobs(self, status: list[str] | None = None, limit: int = 50) -> list[dict]:
        """``GET /api/jobs`` → 任务列表（新式任务 API）。

        老版本没有 ``/api/jobs`` 时自动回退到 ``/queue`` + ``/history``。
        ``status`` 的合法值：pending / in_progress / completed / failed / cancelled。
        """
        params: dict[str, Any] = {"limit": int(limit)}
        statuses = [str(s) for s in (status or []) if s]
        if statuses:
            params["status"] = statuses
        resp = self._request("GET", "/api/jobs", params=params)
        if resp.status_code in (404, 405):
            return self._jobs_fallback(statuses, int(limit))
        data = self._json(resp, "GET /api/jobs")
        if isinstance(data, dict):
            items = data.get("jobs") or []
        elif isinstance(data, list):
            items = data
        else:
            raise ComfyError("GET /api/jobs returned unexpected payload")
        items = [item for item in items if isinstance(item, dict)]
        if statuses:
            wanted = set(statuses)
            items = [item for item in items if item.get("status") in wanted]
        return items[: int(limit)]

    def job(self, job_id: str) -> dict | None:
        """``GET /api/jobs/{id}`` → 任务详情；不存在返回 ``None``。"""
        resp = self._request("GET", f"/api/jobs/{job_id}")
        if resp.status_code == 404:
            # 可能是「没这个任务」，也可能是「老版本没有 /api/jobs」。
            history = self._history_entry(job_id)
            if history is None:
                return None
            return self._history_to_job(job_id, history)
        data = self._json(resp, f"GET /api/jobs/{job_id}")
        if not data:
            return None
        if not isinstance(data, dict):
            raise ComfyError(f"GET /api/jobs/{job_id} returned unexpected payload")
        if not data.get("id"):
            data["id"] = job_id
        return data

    def cancel(self, job_id: str) -> dict:
        """取消任务：优先``POST /api/jobs/{id}/cancel``，老版本回退 ``/interrupt``。"""
        resp = self._request("POST", f"/api/jobs/{job_id}/cancel", json={})
        if resp.status_code in (404, 405):
            ok = self.interrupt(job_id)
            return {"cancelled": bool(ok), "id": job_id, "fallback": "interrupt"}
        data = self._json(resp, f"POST /api/jobs/{job_id}/cancel")
        if not isinstance(data, dict):
            data = {"cancelled": True, "id": job_id}
        data.setdefault("id", job_id)
        return data

    def interrupt(self, prompt_id: str | None = None) -> bool:
        """``POST /interrupt``：打断当前执行（可带 prompt_id）。"""
        payload: dict[str, Any] = {}
        if prompt_id:
            payload["prompt_id"] = str(prompt_id)
        resp = self._request("POST", "/interrupt", json=payload)
        if resp.status_code >= 400:
            raise ComfyError(
                f"POST /interrupt failed: HTTP {resp.status_code}: {_truncate(resp.text)}"
            )
        return True

    def free(self, unload_models: bool = True, free_memory: bool = True) -> bool:
        """``POST /free``：卸载模型 / 释放显存。"""
        resp = self._request(
            "POST",
            "/free",
            json={"unload_models": bool(unload_models), "free_memory": bool(free_memory)},
        )
        if resp.status_code >= 400:
            raise ComfyError(
                f"POST /free failed: HTTP {resp.status_code}: {_truncate(resp.text)}"
            )
        return True

    def history(self, prompt_id: str | None = None) -> dict:
        """``GET /history[/{id}]``：历史记录（全部或单个 prompt 的包装字典）。"""
        path = "/history" if not prompt_id else f"/history/{prompt_id}"
        resp = self._request("GET", path)
        data = self._json(resp, f"GET {path}")
        if not isinstance(data, dict):
            raise ComfyError(f"GET {path} returned unexpected payload")
        return data

    # ------------------------------------------------------- 文件上传 / 下载
    def upload_image(self, path: str, subfolder: str = "") -> dict:
        """``POST /upload/image``（multipart 字段 ``image``）→ ``{name, subfolder, type}``。"""
        abs_path = os.path.abspath(path)
        if not os.path.isfile(abs_path):
            raise ComfyError(f"upload_image: file not found: {path}")
        name = os.path.basename(abs_path)
        mime = mimetypes.guess_type(abs_path)[0] or "application/octet-stream"
        data: dict[str, Any] = {"type": "input", "overwrite": "true"}
        if subfolder:
            data["subfolder"] = subfolder
        try:
            with open(abs_path, "rb") as fh:
                resp = self._request(
                    "POST",
                    "/upload/image",
                    files={"image": (name, fh, mime)},
                    data=data,
                    timeout=max(self.timeout, UPLOAD_TIMEOUT),
                )
        except OSError as exc:
            raise ComfyError(f"upload_image: cannot read {path}: {exc}") from exc
        payload = self._json(resp, "POST /upload/image")
        if not isinstance(payload, dict):
            raise ComfyError("POST /upload/image returned unexpected payload")
        return {
            "name": payload.get("name") or name,
            "subfolder": payload.get("subfolder", subfolder or ""),
            "type": payload.get("type", "input"),
        }

    def download(
        self,
        filename: str,
        subfolder: str = "",
        folder_type: str = "output",
    ) -> bytes:
        """``GET /view`` 取输出文件原始字节。"""
        if not filename:
            raise ComfyError("download: filename is required")
        params = {"filename": filename, "subfolder": subfolder or "", "type": folder_type or "output"}
        resp = self._request("GET", "/view", params=params)
        if resp.status_code == 404:
            raise ComfyError(
                f"download: file not found: {folder_type}/{subfolder}/{filename}"
            )
        if resp.status_code >= 400:
            raise ComfyError(
                f"GET /view failed: HTTP {resp.status_code}: {_truncate(resp.text)}"
            )
        return resp.content

    def save_output(
        self,
        filename: str,
        subfolder: str,
        folder_type: str,
        dest: str,
    ) -> str:
        """下载输出文件并原子写入 ``dest``（tmp + ``os.replace``），返回 ``dest``。"""
        data = self.download(filename, subfolder=subfolder, folder_type=folder_type)
        dest_abs = os.path.abspath(dest)
        parent = os.path.dirname(dest_abs)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = dest_abs + ".tmp"
        try:
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, dest_abs)
        except OSError as exc:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:  # pragma: no cover
                pass
            raise ComfyError(f"save_output: cannot write {dest}: {exc}") from exc
        return dest_abs

    def job_outputs(self, job_id: str) -> list[dict]:
        """从任务详情 / history 中提取输出文件列表。

        返回 ``[{filename, subfolder, type, filename_abs?, node_id, kind}]``。
        """
        job = self.job(job_id)
        if not job:
            return []
        outputs = job.get("outputs") or {}
        if not isinstance(outputs, dict):
            return []
        result: list[dict] = []
        for node_id, node_out in outputs.items():
            if not isinstance(node_out, dict):
                continue
            for kind, items in node_out.items():
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict) or not item.get("filename"):
                        continue
                    rec = {
                        "filename": item["filename"],
                        "subfolder": item.get("subfolder", "") or "",
                        "type": item.get("type", "output") or "output",
                        "node_id": str(node_id),
                        "kind": str(kind),
                    }
                    if rec["type"] == "output" and OUTPUT_DIR:
                        rec["filename_abs"] = os.path.join(
                            OUTPUT_DIR, rec["subfolder"], rec["filename"]
                        )
                    result.append(rec)
        return result

    # ---------------------------------------------------------- 老式 API 回退
    def _history_entry(self, job_id: str) -> dict | None:
        try:
            history = self.history(job_id)
        except ComfyError:
            return None
        if not isinstance(history, dict):
            return None
        entry = history.get(job_id)
        return entry if isinstance(entry, dict) else None

    @staticmethod
    def _history_to_job(job_id: str, entry: dict) -> dict:
        status = entry.get("status") if isinstance(entry.get("status"), dict) else {}
        status_str = (status.get("status_str") or "").lower()
        mapped = {"success": "completed", "error": "failed"}.get(status_str, status_str or "completed")
        return {
            "id": job_id,
            "prompt_id": job_id,
            "status": mapped,
            "outputs": entry.get("outputs") or {},
            "execution_status": status,
            "workflow": None,
        }

    @staticmethod
    def _queue_entry_to_job(entry: Any, status: str) -> dict | None:
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            return None
        return {
            "id": str(entry[1]),
            "prompt_id": str(entry[1]),
            "status": status,
            "outputs": {},
            "execution_status": {},
            "workflow": None,
        }

    def _jobs_fallback(self, statuses: list[str], limit: int) -> list[dict]:
        items: list[dict] = []
        try:
            queue_data = self.queue()
        except ComfyError:
            queue_data = {}
        for key, mapped in (("queue_running", "in_progress"), ("queue_pending", "pending")):
            for entry in queue_data.get(key) or []:
                rec = self._queue_entry_to_job(entry, mapped)
                if rec:
                    items.append(rec)
        try:
            history = self.history()
        except ComfyError:
            history = {}
        if isinstance(history, dict):
            for pid, entry in history.items():
                if not isinstance(entry, dict):
                    continue
                items.append(self._history_to_job(str(pid), entry))
        if statuses:
            wanted = set(statuses)
            items = [item for item in items if item.get("status") in wanted]
        return items[:limit]

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"ComfyClient(url={self.url!r}, timeout={self.timeout!r})"
