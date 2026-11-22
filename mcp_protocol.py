"""最小 MCP（Model Context Protocol）stdio 服务器实现。

只依赖标准库：newline-delimited JSON-RPC 2.0 over stdin/stdout。
- 支持 initialize / notifications/initialized / ping / tools/list / tools/call / shutdown
- stdout 只输出协议消息；日志一律走 stderr / 文件
"""
from __future__ import annotations

import json
import sys
import traceback

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")


class ToolError(Exception):
    """工具执行失败：返回 isError=true 而不是 JSON-RPC error。"""


class McpServer:
    def __init__(self, name: str, version: str = "1.0.0", log=None):
        self.name = name
        self.version = version
        self._tools = {}
        self._log = log or (lambda *a, **k: None)

    # ------------------------------------------------------------------ tools
    def tool(self, name: str, description: str, input_schema: dict | None = None):
        def deco(fn):
            self._tools[name] = {
                "name": name,
                "description": description,
                "inputSchema": input_schema or {"type": "object", "properties": {}, "additionalProperties": True},
                "handler": fn,
            }
            return fn

        return deco

    def tool_names(self) -> list[str]:
        return list(self._tools)

    # --------------------------------------------------------------- protocol
    def handle(self, msg: dict) -> dict | None:
        """处理单条 JSON-RPC 消息；返回响应 dict（通知返回 None）。"""
        if not isinstance(msg, dict):
            return self._error(None, -32600, "Invalid Request")
        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params") or {}

        if method is None:  # 客户端发来的响应，忽略
            return None
        if msg_id is None and method.startswith("notifications/"):
            self._log("notification", method)
            return None

        try:
            if method == "initialize":
                requested = str(params.get("protocolVersion") or "")
                version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
                return self._ok(
                    msg_id,
                    {
                        "protocolVersion": version,
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": self.name, "version": self.version},
                        "instructions": "本地 ComfyUI（MiniMax-H3 视频生成）MCP 工具集：模板/参数、排队与监控、资源守卫、ffmpeg 剪辑。默认单飞，避免 8GB VRAM 崩溃。",
                    },
                )
            if method == "ping":
                return self._ok(msg_id, {})
            if method == "tools/list":
                return self._ok(msg_id, {"tools": [self._public_tool(t) for t in self._tools.values()]})
            if method == "tools/call":
                return self._call_tool(msg_id, params)
            if method == "shutdown":
                return self._ok(msg_id, {})
            return self._error(msg_id, -32601, f"Method not found: {method}")
        except Exception as exc:  # 防御：协议层不因工具异常崩溃
            self._log("error", f"{method}: {exc}", traceback.format_exc())
            return self._error(msg_id, -32603, f"Internal error: {exc}")

    def _public_tool(self, t: dict) -> dict:
        return {
            "name": t["name"],
            "description": t["description"],
            "inputSchema": t["inputSchema"],
        }

    def _call_tool(self, msg_id, params: dict) -> dict:
        name = params.get("name")
        args = params.get("arguments") or {}
        tool = self._tools.get(name)
        if tool is None:
            return self._error(msg_id, -32602, f"Unknown tool: {name}")
        if not isinstance(args, dict):
            return self._error(msg_id, -32602, "arguments must be an object")
        try:
            result = tool["handler"](**args)
        except TypeError as exc:
            # 参数错误要给人看的信息，而不是内部栈
            return self._tool_result(
                msg_id,
                {"ok": False, "error": f"参数错误: {exc}", "tool": name},
                is_error=True,
            )
        except ToolError as exc:
            return self._tool_result(msg_id, {"ok": False, "error": str(exc)}, is_error=True)
        except Exception as exc:
            self._log("tool_error", f"{name}: {exc}", traceback.format_exc())
            return self._tool_result(
                msg_id,
                {"ok": False, "error": f"{type(exc).__name__}: {exc}", "tool": name},
                is_error=True,
            )
        return self._tool_result(msg_id, result)

    # ------------------------------------------------------------------ run
    def run(self) -> int:
        self._log("start", f"MCP server {self.name} v{self.version} ready, tools={self.tool_names()}")
        stream = sys.stdin
        while True:
            line = stream.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as exc:
                self._send(self._error(None, -32700, f"Parse error: {exc}"))
                continue
            if isinstance(msg, list):  # 批量
                responses = [r for r in (self.handle(m) for m in msg) if r is not None]
                if responses:
                    self._send(responses)
                continue
            resp = self.handle(msg)
            if resp is not None:
                self._send(resp)
        return 0

    def _send(self, payload) -> None:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    # ------------------------------------------------------------- envelopes
    @staticmethod
    def _ok(msg_id, result) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _error(msg_id, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    def _tool_result(self, msg_id, result, is_error: bool = False) -> dict:
        text = json.dumps(result, ensure_ascii=False, default=str)
        payload = {
            "content": [{"type": "text", "text": text}],
            "structuredContent": result if isinstance(result, dict) else {"value": result},
        }
        if is_error:
            payload["isError"] = True
        return self._ok(msg_id, payload)
