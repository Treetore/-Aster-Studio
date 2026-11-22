#!/usr/bin/env python
"""ComfyUI 实时监控 Web 面板（标准库实现，无第三方依赖）。

用法:
    K:\\CONFI\\mian\\ComfyUI_windows_portable\\python_embeded\\python.exe tools\\monitor_server.py --port 8190
然后浏览器打开 http://127.0.0.1:8190/  （每 2 秒自动刷新）

显示：ComfyUI 状态/队列、GPU 显存/利用率/温度/功耗、RAM/commit、
当前任务（/api/jobs）、采样进度（解析 comfy_stderr.log 的 tqdm）、日志尾部。
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import urlopen

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ERR_LOG = os.path.join(ROOT, "logs", "comfy_stderr.log")
COMFY = "http://127.0.0.1:8188"
ANSI = re.compile(r"\x1b\[[0-9;]*m")
TQDM = re.compile(r"(\d+)%\|[^|]*\|\s*(\d+)/(\d+)\s*\[([^\]]+)\]")


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def memory_info() -> dict:
    try:
        m = MEMORYSTATUSEX()
        m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        gb = 1024.0 ** 3
        return {
            "ram_total_gb": round(m.ullTotalPhys / gb, 1),
            "ram_free_gb": round(m.ullAvailPhys / gb, 1),
            "commit_total_gb": round(m.ullTotalPageFile / gb, 1),
            "commit_free_gb": round(m.ullAvailPageFile / gb, 1),
            "commit_used_gb": round((m.ullTotalPageFile - m.ullAvailPageFile) / gb, 1),
        }
    except Exception:
        return {}


def gpu_info() -> dict:
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu,power.draw",
                "--format=csv,noheader,nounits",
            ],
            timeout=4,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).decode("utf-8", "replace").strip()
        first = out.splitlines()[0]
        parts = [p.strip() for p in first.split(",")]
        return {
            "name": parts[0],
            "vram_used_mb": int(float(parts[1])),
            "vram_total_mb": int(float(parts[2])),
            "util_pct": int(float(parts[3])),
            "temp_c": float(parts[4]),
            "power_w": float(parts[5]),
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def http_json(path: str, timeout: float = 2.0):
    try:
        with urlopen(COMFY + path, timeout=timeout) as r:
            return json.load(r)
    except Exception:
        return None


def tail_lines(path: str, n: int) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return [ANSI.sub("", ln.rstrip("\n")) for ln in lines[-n:]]
    except OSError:
        return []


_CACHE = {"data": None, "ts": 0.0}
_CACHE_LOCK = threading.Lock()


def cached_status() -> dict:
    """后台线程预采集，HTTP 请求只读缓存，避免高负载时把监控自身拖慢。"""
    global _CACHE
    with _CACHE_LOCK:
        data = _CACHE["data"]
    if data is not None:
        return data
    data = build_status()
    with _CACHE_LOCK:
        _CACHE = {"data": data, "ts": time.time()}
    return data


def _refresh_cache() -> None:
    global _CACHE
    while True:
        try:
            data = build_status()
        except Exception as exc:  # noqa: BLE001
            data = {"error": str(exc)}
        with _CACHE_LOCK:
            _CACHE = {"data": data, "ts": time.time()}
        time.sleep(2.0)


_LAST_JOBS: list = []


def build_status() -> dict:
    global _LAST_JOBS
    stats = http_json("/system_stats")
    queue = http_json("/queue") or {}
    raw_jobs = http_json("/api/jobs?limit=5")
    if raw_jobs is not None:
        jobs = (raw_jobs.get("jobs") or []) if isinstance(raw_jobs, dict) else []
        if jobs:
            _LAST_JOBS = jobs
    else:
        # ComfyUI 忙于加载/采样时 /api/jobs 可能超时，沿用上一次快照，避免面板突然变 idle
        jobs = _LAST_JOBS
    running = any(j.get("status") in ("in_progress", "pending") for j in jobs)
    progress = ""
    if running:
        for line in reversed(tail_lines(ERR_LOG, 300)):
            m = TQDM.search(line)
            if m:
                progress = f"{m.group(2)}/{m.group(3)} steps  [{m.group(4)}]"
                break
    mem = memory_info()
    gpu = gpu_info()
    now_ms = int(time.time() * 1000)
    job_view = []
    for j in jobs[:3]:
        if j.get("status") == "in_progress" and j.get("execution_start_time"):
            elapsed = int((now_ms - int(j["execution_start_time"])) / 1000)
        elif j.get("execution_start_time") and j.get("execution_end_time"):
            elapsed = int((int(j["execution_end_time"]) - int(j["execution_start_time"])) / 1000)
        else:
            elapsed = None
        job_view.append(
            {
                "id": (j.get("id") or "")[:8],
                "status": j.get("status"),
                "elapsed_s": elapsed,
                "outputs": j.get("outputs") or {},
            }
        )
    return {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "comfy": {
            "up": stats is not None,
            "version": ((stats or {}).get("system") or {}).get("comfyui_version"),
            "queue_running": len(queue.get("queue_running") or []),
            "queue_pending": len(queue.get("queue_pending") or []),
        },
        "gpu": gpu,
        "memory": mem,
        "jobs": job_view,
        "progress": progress,
        "log": tail_lines(ERR_LOG, 8),
    }


INDEX_HTML = r"""<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ComfyUI 实时监控</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; background: #0b1020; color: #e6edf7; font: 14px/1.5 "Segoe UI", "Microsoft YaHei", monospace; }
  .wrap { max-width: 1100px; margin: 0 auto; padding: 20px; }
  h1 { font-size: 18px; margin: 0 0 4px; color: #8bd5ff; letter-spacing: 1px; }
  .sub { color: #7b8db3; font-size: 12px; margin-bottom: 16px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; }
  .card { background: #121a33; border: 1px solid #23305a; border-radius: 10px; padding: 12px 14px; }
  .card h2 { font-size: 12px; color: #7b8db3; margin: 0 0 8px; font-weight: 600; letter-spacing: 1px; }
  .kv { display: flex; justify-content: space-between; gap: 10px; }
  .kv span:first-child { color: #8fa3c8; }
  .big { font-size: 22px; font-weight: 700; color: #7ee0a3; }
  .warn { color: #ffcf6b; } .bad { color: #ff7b7b; }
  .bar { height: 10px; background: #1c2547; border-radius: 6px; overflow: hidden; margin-top: 8px; }
  .bar > i { display: block; height: 100%; background: linear-gradient(90deg,#2f7bff,#7ee0a3); width: 0%; transition: width .3s; }
  pre { background: #0d1428; border: 1px solid #1d2748; border-radius: 8px; padding: 12px; overflow: auto; max-height: 260px; font-size: 12px; color: #a9bce0; margin: 0; }
  .ok { color: #7ee0a3; } .down { color: #ff7b7b; }
  .row { display: flex; gap: 10px; align-items: baseline; }
</style>
</head>
<body>
<div class="wrap">
  <h1>ComfyUI 实时监控</h1>
  <div class="sub">每 2 秒自动刷新 · <span id="clock">--</span></div>

  <div class="grid">
    <div class="card">
      <h2>COMFYUI</h2>
      <div class="row"><span id="comfyState" class="big">--</span><span id="comfyVersion" class="sub"></span></div>
      <div class="kv"><span>队列 running / pending</span><span id="queue">--</span></div>
    </div>
    <div class="card">
      <h2>GPU</h2>
      <div class="big" id="gpuUtil">--</div>
      <div class="bar"><i id="gpuBar"></i></div>
      <div class="kv"><span>显存</span><span id="gpuVram">--</span></div>
      <div class="kv"><span>温度 / 功耗</span><span id="gpuTm">--</span></div>
    </div>
    <div class="card">
      <h2>内存</h2>
      <div class="big" id="ramFree">--</div>
      <div class="kv"><span>空闲 / 总量</span><span id="ramText">--</span></div>
      <div class="kv"><span>commit</span><span id="commit">--</span></div>
    </div>
    <div class="card">
      <h2>当前任务</h2>
      <div class="big" id="jobStatus">--</div>
      <div class="kv"><span>任务</span><span id="jobId">--</span></div>
      <div class="kv"><span>已运行</span><span id="jobElapsed">--</span></div>
      <div class="kv"><span>采样进度</span><span id="progress">--</span></div>
    </div>
  </div>

  <div class="card" style="margin-top:12px">
    <h2>日志尾部</h2>
    <pre id="log">--</pre>
  </div>
  <div class="sub" style="margin-top:10px">数据源：ComfyUI /api/jobs + nvidia-smi + Windows API + logs/comfy_stderr.log</div>
</div>
<script>
async function tick() {
  try {
    const r = await fetch('/api/status', { cache: 'no-store' });
    if (!r.ok) throw new Error(r.status);
    const d = await r.json();
    document.getElementById('clock').textContent = d.time;
    const c = d.comfy || {};
    const ce = document.getElementById('comfyState');
    ce.textContent = c.up ? 'RUNNING' : 'DOWN';
    ce.className = 'big ' + (c.up ? 'ok' : 'down');
    document.getElementById('comfyVersion').textContent = c.version ? 'v' + c.version : '';
    document.getElementById('queue').textContent = (c.queue_running ?? 0) + ' / ' + (c.queue_pending ?? 0);
    const g = d.gpu || {};
    if (g.error) {
      document.getElementById('gpuUtil').textContent = 'nvidia-smi 不可用';
    } else {
      document.getElementById('gpuUtil').textContent = g.util_pct + '%  ' + (g.name || '');
      document.getElementById('gpuBar').style.width = g.util_pct + '%';
      document.getElementById('gpuVram').textContent = g.vram_used_mb + ' / ' + g.vram_total_mb + ' MB';
      document.getElementById('gpuTm').textContent = g.temp_c + '°C  ' + g.power_w + 'W';
    }
    const m = d.memory || {};
    document.getElementById('ramFree').textContent = m.ram_free_gb + ' GB 空闲';
    document.getElementById('ramText').textContent = m.ram_free_gb + ' / ' + m.ram_total_gb + ' GB';
    document.getElementById('commit').textContent = (m.commit_used_gb ?? '--') + ' / ' + (m.commit_total_gb ?? '--') + ' GB';
    const j = (d.jobs && d.jobs[0]) || null;
    const js = document.getElementById('jobStatus');
    if (j) {
      js.textContent = j.status;
      js.className = 'big ' + (j.status === 'completed' ? 'ok' : (j.status === 'in_progress' ? 'warn' : ''));
      document.getElementById('jobId').textContent = j.id + '…';
      document.getElementById('jobElapsed').textContent = j.elapsed_s == null ? '--' : j.elapsed_s + 's';
      document.getElementById('progress').textContent = d.progress || '--';
    } else {
      js.textContent = 'idle'; js.className = 'big';
      document.getElementById('jobId').textContent = '--';
      document.getElementById('jobElapsed').textContent = '--';
      document.getElementById('progress').textContent = '--';
    }
    document.getElementById('log').textContent = (d.log || []).join('\n') || '(empty)';
  } catch (e) {
    document.getElementById('clock').textContent = '监控服务连接失败: ' + e;
  }
}
tick();
setInterval(tick, 2000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/status"):
            body = json.dumps(cached_status(), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path in ("/", "/index.html"):
            body = INDEX_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def log_message(self, fmt, *args):  # 安静
        pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8190)
    args = ap.parse_args()
    threading.Thread(target=_refresh_cache, daemon=True).start()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[monitor] http://{args.host}:{args.port}/", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
