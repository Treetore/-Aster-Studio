"""comfy_mcp Web 可视化界面 / REST API（FastAPI）。

启动:
    K:\\CONFI\\mian\\ComfyUI_windows_portable\\python_embeded\\python.exe -m uvicorn webapp.app:app --host 127.0.0.1 --port 8190
    （或直接运行 K:\\CONFI\\comfy_mcp\\start_all.ps1）

- 页面:  http://127.0.0.1:8190/
- API 文档（OpenAPI）: http://127.0.0.1:8190/docs   ← 其他 agent 可直接发现并调用
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File as FastFile, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
for _p in (str(PROJECT), str(PROJECT.parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import config  # noqa: E402
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


@app.get("/api/status")
def api_status() -> dict:
    with LOCK:
        return SERVICE.tool_status()


@app.get("/api/templates")
def api_templates() -> dict:
    with LOCK:
        return SERVICE.tool_templates()


@app.post("/api/plan")
def api_plan(body: GenerateReq) -> dict:
    with LOCK:
        return SERVICE.tool_plan(body.template, body.params)


@app.post("/api/generate")
def api_generate(body: GenerateReq) -> dict:
    with LOCK:
        return SERVICE.tool_generate(body.template, body.params, allow_risky=body.allow_risky)


@app.get("/api/jobs")
def api_jobs(limit: int = 20, status: str | None = None) -> dict:
    with LOCK:
        return SERVICE.tool_jobs(limit=limit, status=status.split(",") if status else None)


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


@app.get("/api/models")
def api_models() -> dict:
    root = Path(config.COMFY_ROOT) / "models"

    def _ls(sub: str, suffix: tuple[str, ...] = (".safetensors",)) -> list[str]:
        d = root / sub
        if not d.is_dir():
            return []
        return sorted(p.name for p in d.iterdir() if p.is_file() and p.suffix in suffix)

    return {"unet": _ls("diffusion_models"), "clip": _ls("text_encoders"), "vae": _ls("vae")}


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
def api_media(path: str) -> FileResponse:
    p = _safe_path(path)
    if not p.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(str(p), filename=p.name)


@app.get("/studio", response_class=HTMLResponse)
def studio() -> str:
    return STUDIO_HTML.read_text(encoding="utf-8")


STUDIO_HTML = Path(__file__).with_name("studio.html")


@app.post("/api/upload")
async def api_upload(file: UploadFile = FastFile(...)) -> dict:
    raw = await file.read()
    if len(raw) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file too large (max 50MB)")
    name = os.path.basename(file.filename or "upload.png")
    safe = "".join(c for c in name if c.isalnum() or c in "._- ").strip() or "upload.png"
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
