"""comfy_mcp Web 可视化界面 / REST API（FastAPI）。

启动:
    K:\\CONFI\\mian\\ComfyUI_windows_portable\\python_embeded\\python.exe -m uvicorn webapp.app:app --host 127.0.0.1 --port 8190
    （或直接运行 K:\\CONFI\\comfy_mcp\\start_all.ps1）

- 页面:  http://127.0.0.1:8190/
- API 文档（OpenAPI）: http://127.0.0.1:8190/docs   ← 其他 agent 可直接发现并调用
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import secrets
import os
import requests
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import aiohttp
from fastapi import FastAPI, Header, File as FastFile, HTTPException, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
for _p in (str(PROJECT), str(PROJECT.parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import config  # noqa: E402
import job_lock  # noqa: E402
import model_policy  # noqa: E402
import workflow  # noqa: E402
try:
    from setup import doctor as selfcheck
except Exception:  # noqa: BLE001
    selfcheck = None
from server import ComfyService  # noqa: E402  (复用 MCP 服务层：client/safety/jobs/workflow/editing)

SERVICE = ComfyService()
LOCK = threading.Lock()
UPSCALE_JOBS: dict[str, dict] = {}
UPSCALE_LOCK = threading.Lock()
UPSCALE_STORE = Path(config.DATA_DIR) / "upscale_jobs.json"

ALLOWED_ROOTS = [
    Path(config.OUTPUT_DIR).resolve(),
    Path(config.ARTIFACTS_DIR).resolve(),
    Path(config.COMFY_ROOT, "input").resolve(),
]


def _load_upscale_jobs() -> None:
    try:
        if UPSCALE_STORE.is_file():
            data = json.loads(UPSCALE_STORE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                UPSCALE_JOBS.update(data)
    except Exception:
        pass


def _save_upscale_jobs() -> None:
    try:
        UPSCALE_STORE.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(UPSCALE_STORE) + ".tmp"
        with UPSCALE_LOCK:
            snapshot = json.dumps(UPSCALE_JOBS, ensure_ascii=False, indent=1, default=str)
        Path(tmp).write_text(snapshot, encoding="utf-8")
        os.replace(tmp, UPSCALE_STORE)
    except Exception:
        pass


_load_upscale_jobs()


class GenerateReq(BaseModel):
    template: str
    params: dict[str, Any]
    allow_risky: bool = False


class UpscaleReq(BaseModel):
    src: str
    model: str = "RealESRGAN_x4plus_anime_6B.pth"
    scale_out: float = 0.0
    crf: int = 18


class EditReq(BaseModel):
    action: str
    kwargs: dict[str, Any] = {}


class SettingsReq(BaseModel):
    values: dict[str, Any]


def _safe_path(raw: str) -> Path:
    path = Path(raw).resolve()
    for root in ALLOWED_ROOTS:
        if path == root or root in path.parents:
            return path
    raise HTTPException(status_code=403, detail="path outside allowed roots")


def _comfy_jobs(limit: int = 20) -> list[dict]:
    """ComfyUI 实时任务列表（含脚本直连提交、非 MCP 发起的任务）。"""
    try:
        return [j for j in SERVICE.client.jobs(limit=limit) if isinstance(j, dict)]
    except Exception:
        return []


def _comfy_queue_snapshot() -> dict:
    snap: dict = {"running": [], "pending": [], "error": None}
    try:
        q = SERVICE.client.queue()
        snap["running"] = q.get("queue_running") or []
        snap["pending"] = q.get("queue_pending") or []
    except Exception as exc:  # noqa: BLE001
        snap["error"] = f"{type(exc).__name__}: {exc}"
    return snap


def _list_videos() -> list[dict]:
    out: list[dict] = []
    for root in (Path(config.OUTPUT_DIR), Path(config.ARTIFACTS_DIR)):
        if not root.is_dir():
            continue
        for p in root.rglob("*.mp4"):
            if p.is_file():
                out.append({"path": str(p), "name": p.name, "mb": round(p.stat().st_size / 1048576, 2)})
    out.sort(key=lambda x: x["mb"], reverse=True)
    return out[:200]


def _gallery_category(name: str, kind: str) -> str:
    n = (name or "").lower()
    if kind == "video":
        return "h3_video" if n.startswith("minimax_h3") else "video"
    if n.startswith("qwen_image_2.1_edit"):
        return "qwen_edit"
    if n.startswith("qwen_image_2.1"):
        return "qwen_t2i"
    if n.startswith("minimax_h3"):
        return "h3_still"
    return "image"


def _list_gallery() -> list[dict]:
    """output 目录下的图片+视频，按 mtime 倒序并去重（避免作品页堆积）。"""
    out: list[dict] = []
    root = Path(config.OUTPUT_DIR)
    if not root.is_dir():
        return out
    seen: set[tuple[str, int]] = set()
    for ext in ("*.mp4", "*.png", "*.jpg", "*.jpeg", "*.webp", "*.webm"):
        for p in root.rglob(ext):
            if not p.is_file():
                continue
            st = p.stat()
            key = (p.name, int(st.st_size))
            if key in seen:
                continue
            seen.add(key)
            kind = "video" if ext == "*.mp4" else "image"
            out.append({"path": str(p), "name": p.name, "mb": round(st.st_size / 1048576, 2),
                        "mtime": st.st_mtime, "kind": kind,
                        "category": _gallery_category(p.name, kind)})
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


def _list_images() -> list[dict]:
    out: list[dict] = []
    for root in (Path(config.OUTPUT_DIR), Path(config.ARTIFACTS_DIR)):
        if not root.is_dir():
            continue
        for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
            for p in root.rglob(ext):
                if p.is_file():
                    st = p.stat()
                    out.append({"path": str(p), "name": p.name,
                                "mb": round(st.st_size / 1048576, 2), "mtime": st.st_mtime})
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out[:200]


def _upscale_models() -> list[str]:
    d = Path(config.COMFY_ROOT) / "models" / "upscale_models"
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.glob("*.pth"))


def _run_upscale(job_id: str, req: UpscaleReq) -> None:
    src = Path(req.src).resolve()
    stem = src.stem
    dst = Path(config.ARTIFACTS_DIR) / "upscaled" / f"{stem}_{req.model.replace('.pth','')}_{uuid.uuid4().hex[:6]}.mp4"
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(PROJECT / "tools" / "upscale_video.py"),
           "--src", str(src), "--dst", str(dst), "--model", req.model, "--crf", str(req.crf)]
    if req.scale_out:
        cmd += ["--scale-out", str(req.scale_out)]
    log = Path(config.LOGS_DIR) / f"upscale_{job_id}.log"
    with UPSCALE_LOCK:
        UPSCALE_JOBS[job_id].update({"status": "running", "started_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    _save_upscale_jobs()
    try:
        with open(log, "w", encoding="utf-8") as f:
            proc = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            rc = proc.wait()
        with UPSCALE_LOCK:
            UPSCALE_JOBS[job_id].update({
                "status": "completed" if rc == 0 else "failed",
                "returncode": rc,
                "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "output": str(dst) if rc == 0 else None,
                "log": str(log),
            })
    except Exception as exc:  # noqa: BLE001
        with UPSCALE_LOCK:
            UPSCALE_JOBS[job_id].update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
    _save_upscale_jobs()


app = FastAPI(
    title="ComfyUI Studio API",
    version="1.0.0",
    description="MiniMax-H3 视频生成 / 超分 / 剪辑 的本地 REST API 与可视化界面。",
)

IP_ALLOWLIST_FILE = Path(config.DATA_DIR) / "ip_allowlist.json"
_LOCAL_IPS = {"127.0.0.1", "::1", "localhost"}


def _ip_allowlist() -> set[str] | None:
    try:
        cfg = json.loads(IP_ALLOWLIST_FILE.read_text(encoding="utf-8-sig"))
        if not cfg.get("enabled", True):
            return None
        return {str(x).strip() for x in cfg.get("ips", []) if str(x).strip()}
    except Exception:
        return {"203.0.113.10"}


BRIDGE_ACCESS_LOG = Path(config.LOGS_DIR) / "bridge_access.log"


def _bridge_log(msg: str) -> None:
    try:
        with BRIDGE_ACCESS_LOG.open("a", encoding="utf-8") as f:
            f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg + "\n")
    except Exception:
        pass


@app.middleware("http")
async def ip_guard(request, call_next):
    """仅允许本机与白名单 IP（隧道场景读取 CF-Connecting-IP / X-Forwarded-For）。"""
    allowed = _ip_allowlist()
    # 媒体下载走 token 校验，允许 NapCat 等容器直接下载（不依赖其出口 IP）
    if request.url.path.startswith("/api/bridge/media/"):
        allowed = None
    if allowed is not None:
        client = (request.headers.get("cf-connecting-ip")
                  or (request.headers.get("x-forwarded-for") or "").split(",")[0].strip())
        client = (client or "").strip()
        if client and client not in allowed and client not in _LOCAL_IPS:
            return JSONResponse({"ok": False, "error": f"ip not allowed: {client}"}, status_code=403)
    t0 = time.time()
    response = await call_next(request)
    if request.url.path.startswith("/api/bridge"):
        _bridge_log(f"{request.method} {request.url.path} -> {response.status_code} "
                    f"ip={request.headers.get('cf-connecting-ip') or request.client.host if request.client else '?'} "
                    f"{(time.time()-t0)*1000:.0f}ms")
    return response



# 生成策略护栏等校验错误以 400 返回，前端可直接展示原因（而不是 500 堆栈）
try:  # pragma: no cover - 兼容不同导入路径
    import mcp_protocol as _mcp_protocol
except ImportError:  # pragma: no cover
    _mcp_protocol = None

if _mcp_protocol is not None:
    from fastapi.responses import JSONResponse as _JSONResponse

    @app.exception_handler(_mcp_protocol.ToolError)
    async def _tool_error_handler(_request, exc):  # type: ignore[no-untyped-def]
        return _JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})


@app.get("/api/status")
def api_status() -> dict:
    with LOCK:
        data = SERVICE.tool_status()
    # 用 ComfyUI 实时任务覆盖本地历史里的 active_jobs，避免已结束的任务显示成 running
    live = _comfy_jobs(limit=20)
    running = [j for j in live if j.get("status") in ("running", "in_progress", "queued", "pending")]
    data["active_jobs"] = running[:5]
    data["comfy_jobs"] = live[:20]
    data["queue_snapshot"] = _comfy_queue_snapshot()
    data["synced_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return data


@app.get("/api/queue")
def api_queue() -> dict:
    snap = _comfy_queue_snapshot()
    return {"ok": snap["error"] is None, "time": time.strftime("%Y-%m-%d %H:%M:%S"), **snap}


@app.post("/api/free")
def api_free() -> dict:
    with LOCK:
        return SERVICE.tool_free()


@app.get("/api/realtime")
def api_realtime() -> dict:
    """前端实时通道参数：浏览器经同源代理连接，避免 ComfyUI 的 Origin 校验。"""
    comfy_url = str(config.COMFY_URL).rstrip("/")
    ws_base = comfy_url.replace("https://", "wss://").replace("http://", "ws://")
    client_id = getattr(SERVICE.client, "client_id", "") if SERVICE.client else ""
    query = f"?clientId={client_id}" if client_id else ""
    return {
        "ok": True,
        "comfy_url": comfy_url,
        "client_id": client_id,
        "proxy": True,
        "ws_url": f"/ws/comfy{query}",
        "direct_ws_url": f"{ws_base}/ws{query}",
    }


@app.websocket("/ws/comfy")
async def ws_comfy(websocket: WebSocket) -> None:
    """把浏览器的同源 WebSocket 转发到 ComfyUI，事件内容不经过解析。"""
    await websocket.accept()
    client_id = websocket.query_params.get("clientId") or (
        getattr(SERVICE.client, "client_id", "") if SERVICE.client else ""
    )
    ws_base = str(config.COMFY_URL).rstrip("/").replace("https://", "wss://").replace("http://", "ws://")
    upstream_url = f"{ws_base}/ws" + (f"?clientId={client_id}" if client_id else "")
    session = aiohttp.ClientSession()
    try:
        upstream = await session.ws_connect(upstream_url, heartbeat=20, max_msg_size=16 * 1024 * 1024)
    except Exception:
        await session.close()
        await websocket.close(code=1011, reason="ComfyUI 事件通道不可用")
        return

    async def client_to_upstream() -> None:
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return
                if message.get("text") is not None:
                    await upstream.send_str(message["text"])
                elif message.get("bytes") is not None:
                    await upstream.send_bytes(message["bytes"])
        except (WebSocketDisconnect, RuntimeError):
            return

    async def upstream_to_client() -> None:
        try:
            async for message in upstream:
                if message.type == aiohttp.WSMsgType.TEXT:
                    await websocket.send_text(message.data)
                elif message.type == aiohttp.WSMsgType.BINARY:
                    await websocket.send_bytes(message.data)
                elif message.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.ERROR):
                    return
        except (WebSocketDisconnect, RuntimeError):
            return

    tasks = [asyncio.create_task(client_to_upstream()), asyncio.create_task(upstream_to_client())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await upstream.close()
        await session.close()
        try:
            await websocket.close()
        except RuntimeError:
            pass


@app.get("/api/templates")
def api_templates() -> dict:
    with LOCK:
        return SERVICE.tool_templates()


@app.post("/api/plan")
def api_plan(body: GenerateReq) -> dict:
    with LOCK:
        return SERVICE.tool_plan(body.template, body.params)


def _apply_prompt_mentions(template: str, params: dict) -> tuple[str, dict]:
    """把提示词里的 @文件名 解析为 H3 参考图，并自动切到 r2v。"""
    if not template.startswith("minimax_h3"):
        return template, params
    prompt = str(params.get("prompt") or "")
    if "@" not in prompt:
        return template, params
    root = Path(config.INPUT_DIR)
    names = re.findall(r"@([^\s@，。！？；：、]+)", prompt)
    resolved: list[str] = []
    for n in names:
        cand = root / n
        if cand.is_file() and cand.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            resolved.append(n)
    if not resolved:
        return template, params
    p = dict(params)
    refs = list(p.get("ref_images") or [])
    for n in resolved:
        if n not in refs:
            refs.append(n)
    p["ref_images"] = refs
    for n in resolved:
        prompt = prompt.replace("@" + n, " ")
    p["prompt"] = " ".join(prompt.split())
    if template in ("minimax_h3_t2v", "minimax_h3_fl2v", "minimax_h3_t2v_720p15s"):
        template = "minimax_h3_r2v"
    return template, p


@app.post("/api/generate")
def api_generate(body: GenerateReq) -> dict:
    if selfcheck is not None:
        chk = selfcheck.check()
        if not chk.get("ok"):
            return {"ok": False, "error": "自检未通过，请先处理缺失项", "selfcheck": chk}
    template, params = _apply_prompt_mentions(body.template, body.params or {})
    try:
        refs = params.get("ref_images") or []
        if refs or "@" in str(params.get("prompt") or ""):
            with open(Path(config.LOGS_DIR) / "generate_refs.log", "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%F %T')} template={template} refs={refs} prompt={str(params.get('prompt'))[:160]}\n")
    except Exception:
        pass
    if template.startswith("minimax_h3") and not template.endswith("_image"):
        try:
            dur = params.get("duration_s")
            if dur is None:
                ln = params.get("length")
                dur = (float(ln) / 24.0) if ln else 5.0
            dur = float(dur)
        except Exception:
            dur = 5.0
        if dur >= 12 and not params.get("segmented_done"):
            segs = max(3, int((dur + 4.999) // 5))
            res = _start_film_job(str(params.get("prompt") or ""),
                                  list(params.get("ref_images") or []), segs, 124,
                                  int(params.get("width") or 1280), int(params.get("height") or 736),
                                  int(params.get("steps") or 8), "seg")
            if res.get("ok"):
                res.update({"segmented": True, "duration_s": dur,
                            "message": f"{dur:g}s ≥12s，已自动转分段模式（{segs}×5s）"})
            return res
    with LOCK:
        return SERVICE.tool_generate(template, params, allow_risky=body.allow_risky)


@app.get("/api/jobs")
def api_jobs(limit: int = 20, status: str | None = None) -> dict:
    with LOCK:
        local = SERVICE.tool_jobs(limit=max(limit, 30), status=status.split(",") if status else None)
    comfy = _comfy_jobs(limit=max(limit, 30))
    merged: dict[str, dict] = {}
    for item in comfy:
        jid = str(item.get("id") or "")
        if jid:
            merged[jid] = {**item, "source": "comfy"}
    for item in (local.get("jobs") or []):
        jid = str(item.get("id") or "")
        if not jid:
            continue
        if jid in merged:
            merged[jid] = {**item, **merged[jid], "source": "comfy+local"}
        else:
            merged[jid] = {**item, "source": "local"}
            if item.get("status") in ("running", "queued", "pending"):
                merged[jid]["stale"] = True  # 本地记录 running，但 ComfyUI 队列/历史里没有
    items = list(merged.values())
    if status:
        wanted = set(status.split(","))
        items = [x for x in items if x.get("status") in wanted]
    items.sort(key=lambda x: str(x.get("created_at") or x.get("finished_at") or ""), reverse=True)
    return {"ok": True, "jobs": items[:limit], "comfy_count": len(comfy),
            "local_count": len(local.get("jobs") or []), "synced_at": time.strftime("%Y-%m-%d %H:%M:%S")}


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str) -> dict:
    with LOCK:
        return SERVICE.tool_job(job_id)


@app.post("/api/jobs/{job_id}/cancel")
def api_cancel(job_id: str) -> dict:
    with LOCK:
        return SERVICE.tool_cancel(job_id)


@app.post("/api/edit")
def api_edit(body: EditReq) -> dict:
    with LOCK:
        return SERVICE.tool_edit(body.action, **body.kwargs)


@app.get("/api/doctor")
def api_doctor(style: str | None = None, force: bool = False) -> dict:
    if selfcheck is None:
        return {"ok": False, "error": "self-check module missing"}
    return selfcheck.check(force=force, style=style)


@app.get("/api/setup/status")
def api_setup_status() -> dict:
    import system_profile
    try:
        from setup import models as model_catalog
        models = model_catalog.status()
    except Exception:
        models = []
    chk = selfcheck.check() if selfcheck else {"ok": False, "issues": []}
    return {"ok": True, "first_run": selfcheck.first_run() if selfcheck else False,
            "style": selfcheck.get_style() if selfcheck else "easy",
            "profile": system_profile.detect(), "doctor": chk, "models": models}


class SetupModeReq(BaseModel):
    style: str = "easy"


@app.post("/api/setup/mode")
def api_setup_mode(req: SetupModeReq) -> dict:
    if selfcheck is None:
        return {"ok": False, "error": "self-check module missing"}
    return {"ok": True, "style": selfcheck.set_style(req.style)}


@app.get("/api/settings")
def api_settings() -> dict:
    with LOCK:
        return SERVICE.tool_settings("get")


@app.post("/api/settings")
def api_settings_set(body: SettingsReq) -> dict:
    with LOCK:
        return SERVICE.tool_settings("set", body.values)


@app.get("/api/logs")
def api_logs(kind: str = "mcp", tail: int = 120) -> dict:
    with LOCK:
        return SERVICE.tool_logs(kind, tail)


@app.get("/api/videos")
def api_videos() -> dict:
    return {"videos": _list_videos()}


@app.get("/api/images")
def api_images() -> dict:
    return {"images": _list_images()}


@app.get("/api/gallery")
def api_gallery(limit: int = 24, offset: int = 0, category: str = "all") -> dict:
    limit = max(1, min(120, int(limit)))
    offset = max(0, int(offset))
    all_items = _list_gallery()
    counts: dict[str, int] = {"all": len(all_items), "image": 0, "video": 0,
                              "qwen_t2i": 0, "qwen_edit": 0, "h3_still": 0, "h3_video": 0}
    for it in all_items:
        counts[it["kind"]] = counts.get(it["kind"], 0) + 1
        if it["category"] != it["kind"]:
            counts[it["category"]] = counts.get(it["category"], 0) + 1
    cat = (category or "all").strip()
    if cat in ("", "all"):
        items = all_items
    elif cat in ("image", "video"):
        items = [i for i in all_items if i["kind"] == cat]
    else:
        items = [i for i in all_items if i["category"] == cat]
    return {"items": items[offset:offset + limit], "total": len(items),
            "counts": counts, "category": cat}


@app.get("/api/models")
def api_models() -> dict:
    root = Path(config.COMFY_ROOT) / "models"

    def _ls(sub: str, suffix: tuple[str, ...] = (".safetensors",)) -> list[str]:
        d = root / sub
        if not d.is_dir():
            return []
        return sorted(p.name for p in d.iterdir() if p.is_file() and p.suffix in suffix)

    return {"unet": _ls("diffusion_models"), "clip": _ls("text_encoders"), "vae": _ls("vae")}


SEG_FILM_STATE = Path(config.DATA_DIR) / "segment_film_state.json"
SEG_FILM_CANCEL = Path(config.DATA_DIR) / "segment_film_cancel.flag"
SEG_FILM_LOG = Path(config.DATA_DIR).parent / "logs" / "segment_film_web.log"


def _start_film_job(prompt: str, refs: list[str], segments: int, frames: int,
                    width: int, height: int, steps: int, mode: str = "seg",
                    tag: str | None = None) -> dict:
    st = _film_state()
    if st and st.get("status") in ("running", "concat"):
        return {"ok": False, "error": "已有长片任务在运行", "state": st}
    if SEG_FILM_CANCEL.is_file():
        SEG_FILM_CANCEL.unlink()
    py = Path(config.COMFY_ROOT).parent / "python_embeded" / "python.exe"
    tool = Path(config.DATA_DIR).parent / "tools" / "segment_film.py"
    tag = tag or time.strftime("film_%Y%m%d_%H%M%S")
    cmd = [str(py), "-u", str(tool), "--prompt", prompt, "--segments", str(segments),
           "--frames", str(frames), "--width", str(width), "--height", str(height),
           "--steps", str(steps), "--mode", mode, "--tag", tag,
           "--state", str(SEG_FILM_STATE), "--cancel-flag", str(SEG_FILM_CANCEL)]
    if refs:
        cmd += ["--refs", *refs]
    SEG_FILM_LOG.parent.mkdir(parents=True, exist_ok=True)
    fo = open(SEG_FILM_LOG, "ab")
    subprocess.Popen(cmd, cwd=str(Path(config.DATA_DIR).parent), stdout=fo,
                     stderr=subprocess.STDOUT, creationflags=0x08000000 | 0x00000200)
    return {"ok": True, "tag": tag, "segments": segments, "frames": frames}


class FilmStartReq(BaseModel):
    prompt: str
    refs: list[str] = []
    segments: int = 3
    frames: int = 124
    width: int = 1280
    height: int = 736
    steps: int = 4
    mode: str = "seg"
    tag: str | None = None


def _film_state() -> dict | None:
    try:
        return json.loads(SEG_FILM_STATE.read_text(encoding="utf-8"))
    except Exception:
        return None


@app.post("/api/film/start")
def api_film_start(req: FilmStartReq) -> dict:
    return _start_film_job(req.prompt, req.refs, req.segments, req.frames, req.width,
                           req.height, req.steps, req.mode, req.tag)


@app.get("/api/film/state")
def api_film_state() -> dict:
    st = _film_state()
    tail = ""
    if SEG_FILM_LOG.is_file():
        try:
            tail = SEG_FILM_LOG.read_text(encoding="utf-8", errors="ignore")[-1500:]
        except Exception:
            tail = ""
    return {"ok": bool(st), "state": st, "log_tail": tail}


@app.post("/api/film/cancel")
def api_film_cancel() -> dict:
    SEG_FILM_CANCEL.parent.mkdir(parents=True, exist_ok=True)
    SEG_FILM_CANCEL.write_text("1", encoding="utf-8")
    return {"ok": True}


BRIDGE_TEMPLATES = {"moss_image", "qwen_image", "h3_image", "qwen_image_edit", "moss_image_edit",
                    "minimax_h3_t2v", "minimax_h3_fl2v", "minimax_h3_r2v",
                    "minimax_h3_t2v_720p15s"}


def _bridge_token() -> str:
    env = os.environ.get("COMFY_BRIDGE_TOKEN")
    if env:
        return env.strip()
    p = Path(config.DATA_DIR) / "bridge_token.txt"
    if p.is_file():
        tok = p.read_text(encoding="utf-8").strip()
        if tok:
            return tok
    tok = secrets.token_urlsafe(24)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(tok, encoding="utf-8")
    return tok


class BridgeReq(BaseModel):
    prompt: str
    template: str = "moss_image"
    width: int | None = None
    height: int | None = None
    steps: int | None = None
    cfg: float | None = None
    negative_prompt: str | None = None
    seed: int | None = None
    duration_s: float | None = None
    length: int | None = None
    optimize: str | None = None
    lora_name: str | None = None
    timeout_s: int | None = None


def _bridge_catalog() -> dict:
    import workflow as wf
    image, video = [], []
    for name, meta in wf.TEMPLATES.items():
        if name not in BRIDGE_TEMPLATES:
            continue
        kind = str(meta.get("kind") or "")
        is_video = kind.startswith("video") or str(meta.get("node_class") or "").startswith("MiniMaxH3")
        _size = meta.get("default_size") or (0, 0)
        _w, _h = int(_size[0] or 0), int(_size[1] or 0)
        _steps = meta.get("default_steps")
        _len = meta.get("default_length")
        _dur = None
        if is_video:
            _w = _w or 608
            _h = _h or 352
            _steps = _steps or 8
            _dur = round((_len or 120) / 24, 1)
        entry = {
            "name": name,
            "title": meta.get("title"),
            "kind": kind or ("video" if is_video else "image"),
            "defaults": {
                "width": _w,
                "height": _h,
                "steps": _steps,
                "cfg": meta.get("default_cfg"),
                "sampler": meta.get("default_sampler"),
                "scheduler": meta.get("default_scheduler"),
                "length": _len,
                "duration_s": _dur,
                "optimize": meta.get("default_optimize"),
                "lora": meta.get("default_lora") if meta.get("auto_lora") else None,
            },
            "params": sorted((wf._TEMPLATE_PARAMETERS.get(name) or {}).keys()),
        }
        (video if is_video else image).append(entry)
    return {"image": image, "video": video}


@app.get("/api/bridge/media/{job_id}/{media_key}")
def api_bridge_media_key(job_id: str, media_key: str):
    """路径式媒体地址：避免部分下载器丢弃 query 参数。"""
    with BRIDGE_JOBS_LOCK:
        job = dict(BRIDGE_JOBS.get(job_id) or {})
    if not job or media_key != job.get("media_key"):
        return JSONResponse({"ok": False, "error": "invalid media key"}, status_code=403)
    if job.get("status") != "done" or not job.get("path"):
        return JSONResponse({"ok": False, "error": "media not ready"}, status_code=404)
    path = Path(job["path"])
    if not path.is_file():
        return JSONResponse({"ok": False, "error": "media missing"}, status_code=404)
    return FileResponse(str(path), media_type=job.get("mime") or "application/octet-stream",
                        filename=path.name)


@app.get("/api/bridge/media/{job_id}")
def api_bridge_media(job_id: str, token: str = "", x_bridge_token: str | None = Header(default=None, alias="X-Bridge-Token")):
    """给 NapCat/QQ 等协议端下载媒体用：URL 带 token，避免跨容器文件路径不可见。"""
    _expected = _bridge_token()
    if token != _expected and x_bridge_token != _expected:
        _bridge_log(f"media token mismatch query_len={len(token)} header_len={len(x_bridge_token or '')} expected_len={len(_expected)}")
        return JSONResponse({"ok": False, "error": "invalid token"}, status_code=403)
    with BRIDGE_JOBS_LOCK:
        job = dict(BRIDGE_JOBS.get(job_id) or {})
    if not job or job.get("status") != "done" or not job.get("path"):
        return JSONResponse({"ok": False, "error": "media not ready"}, status_code=404)
    path = Path(job["path"])
    if not path.is_file():
        return JSONResponse({"ok": False, "error": "media missing"}, status_code=404)
    return FileResponse(str(path), media_type=job.get("mime") or "application/octet-stream",
                        filename=path.name)


@app.get("/api/bridge/jobs")
def api_bridge_jobs(x_bridge_token: str | None = Header(default=None, alias="X-Bridge-Token")) -> dict:
    if x_bridge_token != _bridge_token():
        return {"ok": False, "error": "invalid token"}
    with BRIDGE_JOBS_LOCK:
        items = [{"job_id": k, **v} for k, v in BRIDGE_JOBS.items()]
    items.sort(key=lambda x: x.get("created_ts", 0), reverse=True)
    return {"ok": True, "busy": job_lock.status().get("busy", False),
            "lock": job_lock.status(), "jobs": items[:50]}


@app.get("/api/bridge/models")
def api_bridge_models(x_bridge_token: str | None = Header(default=None, alias="X-Bridge-Token")) -> dict:
    if x_bridge_token != _bridge_token():
        return {"ok": False, "error": "invalid token"}
    cat = _bridge_catalog()
    return {"ok": True, "image": cat["image"], "video": cat["video"]}


@app.get("/api/bridge/health")
def api_bridge_health(x_bridge_token: str | None = Header(default=None, alias="X-Bridge-Token")) -> dict:
    if x_bridge_token != _bridge_token():
        return {"ok": False, "error": "invalid token"}
    try:
        r = requests.get(f"{config.COMFY_URL}/system_stats", timeout=5)
        comfy = r.status_code == 200
    except Exception:
        comfy = False
    return {"ok": True, "comfy": comfy, "templates": sorted(BRIDGE_TEMPLATES)}


BRIDGE_JOBS: dict[str, dict] = {}
BRIDGE_JOBS_LOCK = threading.Lock()


def _bridge_prepare(req: BridgeReq) -> tuple[dict | None, dict | None, dict | None]:
    template = (req.template or "moss_image").strip()
    if template not in BRIDGE_TEMPLATES:
        return None, None, {"ok": False, "error": f"template not allowed: {template}"}
    prompt = (req.prompt or "").strip()
    if not prompt:
        return None, None, {"ok": False, "error": "empty prompt"}
    if len(prompt) > 8000:
        return None, None, {"ok": False, "error": "prompt too long"}
    params: dict = {"prompt": prompt}
    for key in ("width", "height", "steps", "cfg", "negative_prompt", "seed",
                "duration_s", "length", "optimize", "lora_name"):
        val = getattr(req, key)
        if val is not None:
            params[key] = val
    try:
        pkg = workflow.resolve(template, params)
    except Exception as exc:  # noqa: BLE001
        return None, None, {"ok": False, "error": f"resolve failed: {exc}"}
    return template, params, pkg


def _bridge_find_media(outputs: dict) -> tuple[Path | None, str]:
    media_path = None
    media_mime = "image/png"
    for node in (outputs or {}).values():
        for key in ("images", "videos", "gifs"):
            for it in node.get(key, []):
                fn = it.get("filename") or ""
                low = fn.lower()
                if low.endswith((".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm", ".gif")):
                    candidate = Path(config.OUTPUT_DIR) / (it.get("subfolder") or "") / fn
                    if candidate.is_file():
                        media_path = candidate
                        if low.endswith((".mp4", ".webm")):
                            media_mime = "video/mp4"
                        elif low.endswith(".gif"):
                            media_mime = "image/gif"
                        break
            if media_path:
                break
        if media_path:
            break
    return media_path, media_mime


def _bridge_worker(job_id: str, pid: str, template: str) -> None:
    t0 = time.time()
    job = BRIDGE_JOBS[job_id]
    deadline = time.time() + 3600
    try:
        while time.time() < deadline:
            try:
                h = requests.get(f"{config.COMFY_URL}/history/{pid}", timeout=20).json().get(pid)
            except Exception:
                h = None
            if h:
                st = h.get("status") or {}
                if st.get("completed") or st.get("status_str") == "success":
                    media, mime = _bridge_find_media(h.get("outputs") or {})
                    if not media:
                        job.update(status="failed", error="no media output")
                    else:
                        job.update(status="done", path=str(media), mime=mime,
                                   elapsed_s=round(time.time() - t0, 1))
                    return
                if st.get("status_str") in ("error", "failed"):
                    job.update(status="failed", error="comfy execution failed",
                               detail=str(st.get("messages"))[:600])
                    return
            job["elapsed_s"] = round(time.time() - t0, 1)
            time.sleep(2)
        job.update(status="failed", error="bridge timeout")
    finally:
        job_lock.release(job_id)
        proc_cleaner.finish_job(job_id)
        cutoff = time.time() - 3600
        with BRIDGE_JOBS_LOCK:
            for jid, j in list(BRIDGE_JOBS.items()):
                if j.get("created_ts", 0) < cutoff:
                    BRIDGE_JOBS.pop(jid, None)


@app.post("/api/bridge/submit")
def api_bridge_submit(req: BridgeReq,
                      x_bridge_token: str | None = Header(default=None, alias="X-Bridge-Token")) -> dict:
    if x_bridge_token != _bridge_token():
        return {"ok": False, "error": "invalid token"}
    if selfcheck is not None:
        chk = selfcheck.check()
        if not chk.get("ok"):
            return {"ok": False, "error": "self-check failed", "selfcheck": chk}
    template, params, pkg = _bridge_prepare(req)
    if pkg is None:
        return params if isinstance(params, dict) and "ok" in params else {"ok": False, "error": "bad request"}
    if not pkg.get("ok", True) and "api" not in pkg:
        return pkg
    lock = job_lock.acquire("bridge", template)
    if not lock.get("ok"):
        return {"ok": False, "busy": True, "error": lock.get("message"),
                "holder": lock.get("holder")}
    try:
        r = requests.post(f"{config.COMFY_URL}/prompt",
                          json={"prompt": pkg["api"], "client_id": "astrbot-bridge"}, timeout=60)
        if r.status_code != 200:
            job_lock.release()
            return {"ok": False, "error": f"submit failed {r.status_code}: {r.text[:300]}"}
        pid = r.json()["prompt_id"]
        job_lock.bind(pid)
    except Exception as exc:  # noqa: BLE001
        job_lock.release()
        return {"ok": False, "error": f"submit error: {exc}"}
    with BRIDGE_JOBS_LOCK:
        BRIDGE_JOBS[pid] = {"status": "running", "template": template,
                            "created_ts": time.time(), "elapsed_s": 0,
                            "kind": pkg["resolved"].get("kind"),
                            "width": pkg["resolved"].get("width"),
                            "height": pkg["resolved"].get("height"),
                            "duration_s": pkg["resolved"].get("duration_s"),
                            "media_key": secrets.token_hex(16)}
    proc_cleaner.start_for_job(pid)
    threading.Thread(target=_bridge_worker, args=(pid, pid, template), daemon=True).start()
    return {"ok": True, "job_id": pid, "template": template,
            "width": pkg["resolved"].get("width"), "height": pkg["resolved"].get("height"),
            "duration_s": pkg["resolved"].get("duration_s")}


@app.get("/api/bridge/result/{job_id}")
def api_bridge_result(job_id: str,
                      x_bridge_token: str | None = Header(default=None, alias="X-Bridge-Token")) -> dict:
    if x_bridge_token != _bridge_token():
        return {"ok": False, "error": "invalid token"}
    with BRIDGE_JOBS_LOCK:
        job = dict(BRIDGE_JOBS.get(job_id) or {})
    if not job:
        return {"ok": False, "error": "job not found"}
    resp = {"ok": True, "status": job.get("status"), "template": job.get("template"),
            "elapsed_s": job.get("elapsed_s", 0),
            "width": job.get("width"), "height": job.get("height"),
            "duration_s": job.get("duration_s"), "media_key": job.get("media_key")}
    if job.get("status") == "done":
        try:
            raw = Path(job["path"]).read_bytes()
            resp.update({"mime": job.get("mime") or "image/png", "filename": Path(job["path"]).name,
                         "image_b64": base64.b64encode(raw).decode("ascii")})
        except Exception as exc:  # noqa: BLE001
            resp.update({"ok": False, "status": "failed", "error": f"read media failed: {exc}"})
    elif job.get("status") == "failed":
        resp.update({"ok": False, "status": "failed", "error": job.get("error"),
                     "detail": job.get("detail")})
    return resp


@app.post("/api/bridge/generate")
def api_bridge_generate(req: BridgeReq,
                        x_bridge_token: str | None = Header(default=None, alias="X-Bridge-Token")) -> dict:
    if x_bridge_token != _bridge_token():
        return {"ok": False, "error": "invalid token"}
    template = (req.template or "moss_image").strip()
    if template not in BRIDGE_TEMPLATES:
        return {"ok": False, "error": f"template not allowed: {template}"}
    prompt = (req.prompt or "").strip()
    if not prompt:
        return {"ok": False, "error": "empty prompt"}
    if len(prompt) > 8000:
        return {"ok": False, "error": "prompt too long"}
    params: dict = {"prompt": prompt}
    for key in ("width", "height", "steps", "cfg", "negative_prompt", "seed",
                "duration_s", "length", "optimize", "lora_name"):
        val = getattr(req, key)
        if val is not None:
            params[key] = val
    try:
        pkg = workflow.resolve(template, params)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"resolve failed: {exc}"}
    lock = job_lock.acquire("bridge", template)
    if not lock.get("ok"):
        return {"ok": False, "busy": True, "error": lock.get("message"),
                "holder": lock.get("holder")}
    policy_info = model_policy.maybe_release(
        lambda: requests.post(f"{config.COMFY_URL}/free",
                              json={"unload_models": True, "free_memory": True}, timeout=30),
        lambda: requests.get(f"{config.COMFY_URL}/system_stats", timeout=10).json(),
        template, str(pkg["resolved"].get("kind") or ""), params,
    )
    t0 = time.time()
    try:
        r = requests.post(f"{config.COMFY_URL}/prompt",
                          json={"prompt": pkg["api"], "client_id": "astrbot-bridge"}, timeout=60)
        if r.status_code != 200:
            job_lock.release()
            return {"ok": False, "error": f"submit failed {r.status_code}: {r.text[:300]}"}
        pid = r.json()["prompt_id"]
        job_lock.bind(pid)
    except Exception as exc:  # noqa: BLE001
        job_lock.release()
        return {"ok": False, "error": f"submit error: {exc}"}
    timeout_s = max(30, min(int(req.timeout_s or 600), 3600))
    deadline = time.time() + timeout_s
    outputs = None
    status = None
    while time.time() < deadline:
        try:
            h = requests.get(f"{config.COMFY_URL}/history/{pid}", timeout=20).json().get(pid)
        except Exception:
            h = None
        if h:
            st = h.get("status") or {}
            if st.get("completed") or st.get("status_str") == "success":
                outputs = h.get("outputs") or {}
                status = "completed"
                break
            if st.get("status_str") in ("error", "failed"):
                job_lock.release()
                return {"ok": False, "error": "comfy execution failed",
                        "detail": str(st.get("messages"))[:600]}
        time.sleep(1.0)
    if status != "completed":
        job_lock.release()
        return {"ok": False, "error": "timeout waiting for image"}
    media_path = None
    media_mime = "image/png"
    for node in outputs.values():
        for key in ("images", "videos", "gifs"):
            for it in node.get(key, []):
                fn = it.get("filename") or ""
                low = fn.lower()
                if low.endswith((".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm", ".gif")):
                    candidate = Path(config.OUTPUT_DIR) / (it.get("subfolder") or "") / fn
                    if candidate.is_file():
                        media_path = candidate
                        if low.endswith((".mp4", ".webm")):
                            media_mime = "video/mp4"
                        elif low.endswith(".gif"):
                            media_mime = "image/gif"
                        break
            if media_path:
                break
        if media_path:
            break
    model_policy.mark_done(template, str(pkg["resolved"].get("kind") or ""), params)
    if not media_path:
        job_lock.release()
        return {"ok": False, "error": "no media output"}
    raw = media_path.read_bytes()
    job_lock.release()
    return {"ok": True, "template": template, "elapsed_s": round(time.time() - t0, 1),
            "width": pkg["resolved"].get("width"), "height": pkg["resolved"].get("height"),
            "duration_s": pkg["resolved"].get("duration_s"),
            "filename": media_path.name, "mime": media_mime,
            "policy": policy_info,
            "image_b64": base64.b64encode(raw).decode("ascii")}


@app.get("/api/film")
def api_film() -> dict:
    p = Path(config.DATA_DIR) / "film_state.json"
    if not p.is_file():
        return {"ok": False, "state": None}
    try:
        return {"ok": True, "state": json.loads(p.read_text(encoding="utf-8"))}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@app.get("/api/upscale/models")
def api_upscale_models() -> dict:
    return {"models": _upscale_models()}


@app.post("/api/upscale")
def api_upscale(body: UpscaleReq) -> dict:
    src = _safe_path(body.src)
    if not src.is_file():
        raise HTTPException(status_code=404, detail="src not found")
    if body.model not in _upscale_models():
        raise HTTPException(status_code=400, detail=f"unknown model: {body.model}")
    job_id = uuid.uuid4().hex[:12]
    with UPSCALE_LOCK:
        UPSCALE_JOBS[job_id] = {
            "id": job_id, "status": "queued", "src": str(src), "model": body.model,
            "scale_out": body.scale_out, "crf": body.crf,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "output": None,
        }
    _save_upscale_jobs()
    threading.Thread(target=_run_upscale, args=(job_id, body), daemon=True).start()
    return {"ok": True, "job_id": job_id}


@app.get("/api/upscale/jobs")
def api_upscale_jobs(limit: int = 20) -> dict:
    with UPSCALE_LOCK:
        rows = sorted(UPSCALE_JOBS.values(), key=lambda j: j.get("created_at") or "", reverse=True)[:limit]
    return {"jobs": rows}


@app.get("/api/upscale/jobs/{job_id}")
def api_upscale_job(job_id: str) -> dict:
    with UPSCALE_LOCK:
        job = UPSCALE_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return {"job": job}


@app.get("/api/media")
def api_media(path: str, inline: bool = False) -> FileResponse:
    p = _safe_path(path)
    if not p.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(str(p), filename=p.name,
                        content_disposition_type="inline" if inline else "attachment")


@app.get("/studio", response_class=HTMLResponse)
def studio(response: Response) -> str:
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return STUDIO_HTML.read_text(encoding="utf-8")


STUDIO_HTML = Path(__file__).with_name("studio.html")


@app.get("/api/assets")
def api_assets(limit: int = 60) -> dict:
    """列出 input 目录里的素材，供提示词 @ 引用。"""
    root = Path(config.INPUT_DIR)
    items = []
    try:
        for f in sorted(root.glob("*"), key=lambda x: x.stat().st_mtime if x.is_file() else 0, reverse=True):
            if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                items.append({"name": f.name, "path": str(f), "mtime": f.stat().st_mtime})
            if len(items) >= limit:
                break
    except Exception:
        pass
    return {"ok": True, "assets": items}


@app.post("/api/upload")
async def api_upload(file: UploadFile = FastFile(...)) -> dict:
    raw = await file.read()
    if len(raw) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file too large (max 50MB)")
    name = os.path.basename(file.filename or "upload.png")
    safe = "".join(c for c in name if c.isalnum() or c in "._- ").strip().replace(" ", "_") or "upload.png"
    dest = Path(config.INPUT_DIR) / f"web_{uuid.uuid4().hex[:8]}_{safe}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)
    return {"ok": True, "name": dest.name, "path": str(dest)}


@app.get("/api/hardware")
def api_hardware() -> dict:
    import ctypes

    gpu: dict[str, Any] = {}
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu,power.draw",
             "--format=csv,noheader,nounits"],
            timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).decode("utf-8", "replace").strip().splitlines()[0]
        p = [x.strip() for x in out.split(",")]
        gpu = {"name": p[0], "vram_used_mb": int(float(p[1])), "vram_total_mb": int(float(p[2])),
               "util_pct": int(float(p[3])), "temp_c": float(p[4]), "power_w": float(p[5])}
    except Exception as exc:  # noqa: BLE001
        gpu = {"error": str(exc)}

    mem: dict[str, Any] = {}
    try:
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        m = MEMORYSTATUSEX()
        m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        gb = 1024.0 ** 3
        mem = {"ram_total_gb": round(m.ullTotalPhys / gb, 1), "ram_free_gb": round(m.ullAvailPhys / gb, 1),
               "commit_total_gb": round(m.ullTotalPageFile / gb, 1),
               "commit_used_gb": round((m.ullTotalPageFile - m.ullAvailPageFile) / gb, 1)}
    except Exception as exc:  # noqa: BLE001
        mem = {"error": str(exc)}

    queue = {"running": 0, "pending": 0}
    active: list[dict] = []
    try:
        q = SERVICE.client.queue()
        queue = {"running": len(q.get("queue_running") or []), "pending": len(q.get("queue_pending") or [])}
        active = SERVICE.active_jobs()[:3]
    except Exception:
        pass
    return {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "gpu": gpu, "memory": mem,
            "queue": queue, "active_jobs": active}


@app.get("/")
def index() -> RedirectResponse:
    return RedirectResponse("/studio")


@app.get("/simple", response_class=HTMLResponse)
def simple() -> str:
    return INDEX_HTML


INDEX_HTML = r"""<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>ComfyUI Studio</title>
<style>
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:#0b1020;color:#e6edf7;font:13px/1.5 "Segoe UI","Microsoft YaHei",monospace}
.wrap{max-width:1280px;margin:0 auto;padding:16px}
h1{font-size:17px;margin:0 0 8px;color:#8bd5ff;letter-spacing:1px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:10px}
.card{background:#121a33;border:1px solid #23305a;border-radius:10px;padding:10px 12px}
.card h2{font-size:11px;color:#7b8db3;margin:0 0 8px;letter-spacing:1px}
.kv{display:flex;justify-content:space-between;gap:8px;font-size:12px}.kv span:first-child{color:#8fa3c8}
.big{font-size:20px;font-weight:700;color:#7ee0a3}.warn{color:#ffcf6b}.bad{color:#ff7b7b}
.bar{height:8px;background:#1c2547;border-radius:5px;overflow:hidden;margin-top:6px}.bar>i{display:block;height:100%;background:linear-gradient(90deg,#2f7bff,#7ee0a3);width:0}
input,select,textarea,button{font:12px "Segoe UI","Microsoft YaHei";background:#0d1428;color:#e6edf7;border:1px solid #23305a;border-radius:6px;padding:6px 8px}
textarea{width:100%;min-height:70px}button{cursor:pointer;background:#1d3a6b}button:hover{background:#26508f}
table{width:100%;border-collapse:collapse;font-size:12px}td,th{padding:4px 6px;border-bottom:1px solid #1d2748;text-align:left}th{color:#7b8db3;font-weight:600}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:6px 0}.row label{color:#8fa3c8}
pre{background:#0d1428;border:1px solid #1d2748;border-radius:8px;padding:8px;max-height:200px;overflow:auto;font-size:11px;color:#a9bce0;margin:0}
.video{width:100%;max-height:360px;background:#000;border-radius:8px}
a{color:#8bd5ff}
</style></head>
<body><div class="wrap">
<h1>ComfyUI Studio · MiniMax-H3</h1>
<div style="color:#7b8db3;font-size:12px;margin-bottom:10px">实时监控 + 生成 + 超分 + 剪辑 · <a href="/docs">REST API 文档 /docs</a> · 2 秒刷新</div>

<div class="grid">
<div class="card"><h2>COMFYUI</h2><div class="big" id="comfyState">--</div><div class="kv"><span>版本</span><span id="comfyVer">--</span></div><div class="kv"><span>队列 running/pending</span><span id="queue">--</span></div></div>
<div class="card"><h2>GPU</h2><div class="big" id="gpuUtil">--</div><div class="bar"><i id="gpuBar"></i></div><div class="kv"><span>显存</span><span id="gpuVram">--</span></div><div class="kv"><span>温度/功耗</span><span id="gpuTm">--</span></div></div>
<div class="card"><h2>内存</h2><div class="big" id="ramFree">--</div><div class="kv"><span>空闲/总量</span><span id="ramText">--</span></div><div class="kv"><span>commit</span><span id="commit">--</span></div></div>
<div class="card"><h2>当前任务</h2><div class="big" id="jobStatus">--</div><div class="kv"><span>任务</span><span id="jobId">--</span></div><div class="kv"><span>采样进度</span><span id="progress">--</span></div></div>
</div>

<div class="card" style="margin-top:10px"><h2>生成视频</h2>
<div class="row"><label>模板</label><select id="tpl"></select><label>档位</label><select id="profile"><option>draft</option><option>short</option><option>medium</option><option>standard</option><option>compact</option><option>high</option></select>
<label>时长s</label><input id="dur" type="number" value="5" step="0.5" style="width:70px"><label>宽</label><input id="w" type="number" value="608" style="width:70px"><label>高</label><input id="h" type="number" value="352" style="width:70px"><label>步数</label><input id="steps" type="number" value="4" style="width:60px"></div>
<div class="row"><label>LoRA</label><input id="lora" style="flex:1" value="minimax_h3\minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors"><label>优化</label><select id="optimize"><option>off</option><option>sage</option><option>lowmem</option><option>max</option></select><label>seed</label><input id="seed" type="number" value="2026111001" style="width:120px"></div>
<textarea id="prompt" placeholder="提示词（新海诚风格动画等）"></textarea>
<div class="row"><button onclick="gen()">提交生成</button><span id="genMsg" style="color:#7ee0a3"></span></div></div>

<div class="card" style="margin-top:10px"><h2>任务</h2><table><thead><tr><th>ID</th><th>模板</th><th>状态</th><th>创建</th><th>输出</th><th></th></tr></thead><tbody id="jobs"></tbody></table></div>

<div class="card" style="margin-top:10px"><h2>视频超分（4x 模型）</h2>
<div class="row"><label>源视频</label><select id="usrc" style="max-width:420px"></select><label>模型</label><select id="umodel"></select><label>输出倍数</label><select id="uscale"><option value="0">4x</option><option value="2">2x</option></select><button onclick="upscale()">开始超分</button><span id="uMsg" style="color:#7ee0a3"></span></div>
<table><thead><tr><th>ID</th><th>源</th><th>模型</th><th>状态</th><th>输出</th></tr></thead><tbody id="ujobs"></tbody></table></div>

<div class="card" style="margin-top:10px"><h2>预览</h2><video id="player" class="video" controls></video></div>
<div class="card" style="margin-top:10px"><h2>日志</h2><pre id="log">--</pre></div>
</div>
<script>
async function j(u,o){const r=await fetch(u,o);if(!r.ok)throw new Error(await r.text());return r.json()}
let tpls={};
async function init(){
 const t=await j('/api/templates');tpls=t.templates||[];const s=document.getElementById('tpl');
 for(const x of tpls){const o=document.createElement('option');o.value=x.name;o.textContent=x.title;o.title=x.description;s.appendChild(o)}
 const v=await j('/api/videos');const vs=document.getElementById('usrc');
 for(const x of (v.videos||[])){const o=document.createElement('option');o.value=x.path;o.textContent=x.name+' ('+x.mb+'MB)';vs.appendChild(o)}
 const m=await j('/api/upscale/models');const ms=document.getElementById('umodel');
 for(const x of (m.models||[])){const o=document.createElement('option');o.value=x;o.textContent=x;ms.appendChild(o)}
 if(ms.options.length){document.getElementById('umodel').value='RealESRGAN_x4plus_anime_6B.pth'}
}
function pct(a,b){return b?Math.round(a*100/b):0}
async function tick(){
 try{
  const d=await j('/api/status');const c=d.comfy||{},g=d.hardware||{};
  const ce=document.getElementById('comfyState');ce.textContent=c.up?'RUNNING':'DOWN';ce.className='big '+(c.up?'':'bad');
  document.getElementById('comfyVer').textContent=c.version||'--';
  document.getElementById('queue').textContent=(c.queue?.running??0)+' / '+(c.queue?.pending??0);
  document.getElementById('gpuUtil').textContent=(g.gpu_name||'')+' '+(g.vram_used_mb||0)+'MB';
  document.getElementById('gpuBar').style.width=pct(g.vram_used_mb||0,g.vram_total_mb||1)+'%';
  document.getElementById('gpuVram').textContent=(g.vram_used_mb||0)+' / '+(g.vram_total_mb||0)+' MB';
  document.getElementById('gpuTm').textContent=(g.vram_free_mb??'--')+' MB free';
  document.getElementById('ramFree').textContent=(g.ram_free_gb??'--')+' GB 空闲';
  document.getElementById('ramText').textContent=(g.ram_free_gb??'--')+' / '+(g.ram_total_gb??'--')+' GB';
  document.getElementById('commit').textContent='--';
  const jobs=await j('/api/jobs?limit=12');
  const tb=document.getElementById('jobs');tb.innerHTML='';
  let cur=null;
  for(const x of (jobs.jobs||[])){
   if(x.status==='running'||x.status==='queued')cur=x;
   const tr=document.createElement('tr');
   const out=(x.outputs&&x.outputs[0])||'';
   tr.innerHTML='<td>'+x.id.slice(0,8)+'</td><td>'+(x.template||'')+'</td><td>'+x.status+'</td><td>'+(x.created_at||'')+'</td><td>'+(out?out.split('\\').pop():'')+'</td>';
   if(out){const b=document.createElement('button');b.textContent='播放';b.onclick=()=>document.getElementById('player').src='/api/media?path='+encodeURIComponent(out);tr.appendChild(b);tb.appendChild(tr)}
   else{tr.innerHTML+='<td></td>';tb.appendChild(tr)}
  }
  if(cur){document.getElementById('jobStatus').textContent=cur.status;document.getElementById('jobId').textContent=cur.id.slice(0,8);
   const jd=await j('/api/jobs/'+cur.id);document.getElementById('progress').textContent=(jd.progress?.last_event||'')+' '+(jd.progress?.elapsed_s??'')+'s'}
  else{document.getElementById('jobStatus').textContent='idle';document.getElementById('jobId').textContent='--';document.getElementById('progress').textContent='--'}
  const uj=await j('/api/upscale/jobs?limit=10');const ut=document.getElementById('ujobs');ut.innerHTML='';
  for(const x of (uj.jobs||[])){const tr=document.createElement('tr');
   tr.innerHTML='<td>'+x.id+'</td><td>'+(x.src||'').split('\\').pop()+'</td><td>'+x.model+'</td><td>'+x.status+'</td>';
   const td=document.createElement('td');if(x.output){const b=document.createElement('button');b.textContent='播放';b.onclick=()=>document.getElementById('player').src='/api/media?path='+encodeURIComponent(x.output);td.appendChild(b)}
   tr.appendChild(td);ut.appendChild(tr)}
  try{const lg=await j('/api/logs?kind=comfy_stderr&tail=6');document.getElementById('log').textContent=lg.tail||''}catch(e){}
 }catch(e){document.getElementById('comfyState').textContent='API 连接失败'}
}
async function gen(){
 const body={template:document.getElementById('tpl').value,params:{
  prompt:document.getElementById('prompt').value,profile:document.getElementById('profile').value,
  duration_s:parseFloat(document.getElementById('dur').value),width:parseInt(document.getElementById('w').value),height:parseInt(document.getElementById('h').value),
  steps:parseInt(document.getElementById('steps').value),seed:parseInt(document.getElementById('seed').value),
  lora_name:document.getElementById('lora').value,optimize:document.getElementById('optimize').value}};
 const r=await j('/api/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 document.getElementById('genMsg').textContent=r.ok?('已提交 '+r.job_id.slice(0,8)):('失败: '+(r.error||r.reason||''));
}
async function upscale(){
 const r=await j('/api/upscale',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
  src:document.getElementById('usrc').value,model:document.getElementById('umodel').value,scale_out:parseFloat(document.getElementById('uscale').value)})});
 document.getElementById('uMsg').textContent=r.ok?('已开始 '+r.job_id):('失败 '+JSON.stringify(r));
}
init().then(()=>{tick();setInterval(tick,2000)});
</script></body></html>"""
