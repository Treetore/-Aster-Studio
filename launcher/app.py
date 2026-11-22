"""Aster ComfyUI 桌面启动器 —— 本地 FastAPI 后端（端口 8191）。

功能对齐绘世启动器：启动/停止、高级启动参数、模型管理、疑难解答（诊断）、
控制台日志、任务队列、版本信息、小工具、设置、备份与快捷方式。
复用现有 comfy_mcp 项目（settings.json / logs / jobs.json / ComfyUI 便携包）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil
import requests
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

ROOT = Path(r"K:\CONFI\comfy_mcp")
APP_DIR = Path(r"K:\CONFI\mian\ComfyUI_windows_portable")
PY = APP_DIR / "python_embeded" / "python.exe"
COMFY_DIR = APP_DIR / "ComfyUI"
LOGS = ROOT / "logs"
DATA = ROOT / "data"
SETTINGS = DATA / "settings.json"
BACKUPS = ROOT / "backups"
MODEL_ROOT = COMFY_DIR / "models"
COMFY_URL = "http://127.0.0.1:8188"
WEB_URL = "http://127.0.0.1:8190"
LAUNCHER_PORT = int(os.environ.get("LAUNCHER_PORT", "8191"))
CREATE_NO_WINDOW = 0x08000000
NEW_GROUP = 0x00000200

DEFAULT_START_ARGS = [
    "--windows-standalone-build", "--listen", "127.0.0.1", "--port", "8188",
    "--fast", "fp16_accumulation", "--cuda-malloc", "--disable-auto-launch",
    "--vram-headroom", "1.0",
]
LOWMEM_ARGS = ["--windows-standalone-build", "--listen", "127.0.0.1", "--port", "8188",
               "--lowvram", "--cache-none", "--reserve-vram", "1.0",
               "--fast", "fp16_accumulation", "--cuda-malloc", "--disable-auto-launch"]

app = FastAPI(title="Aster启动器", version="1.1.0")


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def read_settings() -> dict:
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_settings(cfg: dict) -> None:
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, SETTINGS)


def launcher_cfg() -> dict:
    cfg = read_settings()
    return cfg.setdefault("launcher", {
        "theme": "monet", "auto_open_web": True, "refresh_ms": 3000, "mode": "normal",
        "bg": "bg_monet_lilies.png", "bg_blur": 0, "bg_dim": 0.55, "accent": "#8b7cf6",
    })


def http_json(url: str, timeout: float = 2.5) -> tuple[bool, Any]:
    try:
        r = requests.get(url, timeout=timeout)
        if r.status_code >= 400:
            return False, None
        return True, r.json()
    except Exception:
        return False, None


def port_owner(port: int) -> int | None:
    try:
        for c in psutil.net_connections(kind="inet"):
            if c.laddr and c.laddr.port == port and c.status == psutil.CONN_LISTEN:
                return c.pid
    except Exception:
        pass
    return None


def pid_cmdline(pid: int) -> str:
    try:
        return " ".join(psutil.Process(pid).cmdline())
    except Exception:
        return ""


def find_pids(pattern: str) -> list[int]:
    out = []
    for p in psutil.process_iter(["pid", "name"]):
        try:
            if p.info["name"] and p.info["name"].lower() != "python.exe":
                continue
            if pattern.lower() in " ".join(p.cmdline()).lower():
                out.append(p.info["pid"])
        except Exception:
            continue
    return out


def comfy_state() -> dict:
    ok, st = http_json(f"{COMFY_URL}/system_stats", 2.5)
    if not ok or not isinstance(st, dict):
        return {"up": False, "version": None, "queue_running": None, "queue_pending": None}
    q_ok, q = http_json(f"{COMFY_URL}/queue", 2.0)
    return {
        "up": True,
        "version": (st.get("system") or {}).get("comfyui_version"),
        "torch": (st.get("system") or {}).get("pytorch_version"),
        "vram_total_mb": round((st.get("devices") or [{}])[0].get("vram_total", 0) / 1048576),
        "vram_free_mb": round((st.get("devices") or [{}])[0].get("vram_free", 0) / 1048576),
        "queue_running": len((q or {}).get("queue_running") or []) if q_ok else None,
        "queue_pending": len((q or {}).get("queue_pending") or []) if q_ok else None,
    }


def web_state() -> dict:
    ok, st = http_json(f"{WEB_URL}/api/status", 2.5)
    if not ok or not isinstance(st, dict):
        return {"up": False}
    return {"up": True, "comfy": st.get("comfy"), "version": (st.get("comfy") or {}).get("comfyui_version")}


def hw_state() -> dict:
    gpu = {}
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,temperature.gpu,utilization.gpu,power.draw",
             "--format=csv,noheader,nounits"], text=True, timeout=5,
            creationflags=CREATE_NO_WINDOW).strip().splitlines()
        if out:
            parts = [x.strip() for x in out[0].split(",")]
            gpu = {"name": parts[0], "used_mb": int(float(parts[1])), "total_mb": int(float(parts[2])),
                   "temp_c": round(float(parts[3]), 1), "util": int(float(parts[4])),
                   "power_w": parts[5] if len(parts) > 5 else None}
    except Exception:
        gpu = {}
    vm = psutil.virtual_memory()
    disk = shutil.disk_usage("K:\\")
    return {
        "gpu": gpu,
        "ram_total_gb": round(vm.total / 2**30, 1),
        "ram_used_gb": round((vm.total - vm.available) / 2**30, 1),
        "ram_free_gb": round(vm.available / 2**30, 1),
        "disk_free_gb": round(disk.free / 2**30, 1),
        "disk_total_gb": round(disk.total / 2**30, 1),
    }


def tdr_state() -> dict:
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers")
        out = {}
        for name in ("TdrDelay", "TdrDdiDelay"):
            try:
                out[name] = winreg.QueryValueEx(k, name)[0]
            except OSError:
                out[name] = None
        return out
    except Exception:
        return {"TdrDelay": None, "TdrDdiDelay": None}


def comfy_start_args(cfg: dict | None = None) -> list[str]:
    cfg = cfg or read_settings()
    args = ((cfg.get("comfy") or {}).get("start_args")) or DEFAULT_START_ARGS
    return [str(a) for a in args]


def spawn(cmd: list[str], cwd: Path, log: str, err: str) -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    fo = open(LOGS / log, "ab")
    fe = open(LOGS / err, "ab")
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.Popen(cmd, cwd=str(cwd), stdout=fo, stderr=fe, env=env,
                         creationflags=CREATE_NO_WINDOW | NEW_GROUP, close_fds=True)
    return p.pid


def wait_http(url: str, seconds: int = 120) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        ok, _ = http_json(url, 1.5)
        if ok:
            return True
        time.sleep(2)
    return False


def kill_pids(pids: list[int]) -> list[int]:
    killed = []
    for pid in pids:
        try:
            proc = psutil.Process(pid)
            for child in proc.children(recursive=True):
                try:
                    child.kill()
                except Exception:
                    pass
            proc.kill()
            killed.append(pid)
        except Exception:
            continue
    return killed


# --------------------------------------------------------------------------- #
# 启动 / 停止
# --------------------------------------------------------------------------- #
class StartReq(BaseModel):
    scope: str = "all"          # all | comfy | web
    mode: str | None = None     # normal | lowmem
    wait: bool = True


class StopReq(BaseModel):
    scope: str = "all"          # all | comfy | web


class SettingsReq(BaseModel):
    start_args: list[str] | None = None
    mode: str | None = None
    theme: str | None = None
    auto_open_web: bool | None = None
    refresh_ms: int | None = None
    bg: str | None = None
    bg_blur: int | None = None
    bg_dim: float | None = None
    accent: str | None = None


def start_comfy(mode: str | None = None, wait: bool = True) -> dict:
    if comfy_state()["up"]:
        return {"ok": True, "already": True, "pid": port_owner(8188)}
    cfg = read_settings()
    args = comfy_start_args(cfg)
    if mode == "lowmem":
        args = LOWMEM_ARGS
    elif mode == "normal" and not args:
        args = DEFAULT_START_ARGS
    pid = spawn([str(PY), "-s", str(COMFY_DIR / "main.py"), *args], APP_DIR,
                "comfy_stdout.log", "comfy_stderr.log")
    ok = wait_http(f"{COMFY_URL}/system_stats", 180) if wait else True
    if ok:
        (DATA / "comfy_pid.json").write_text(json.dumps({"pid": pid, "ts": time.time()}), encoding="utf-8")
    return {"ok": ok, "pid": pid, "args": args}


def start_web(wait: bool = True) -> dict:
    if web_state()["up"]:
        return {"ok": True, "already": True, "pid": port_owner(8190)}
    pid = spawn([str(PY), "-m", "uvicorn", "webapp.app:app", "--host", "127.0.0.1", "--port", "8190"],
                ROOT, "webapp_stdout.log", "webapp_stderr.log")
    ok = wait_http(f"{WEB_URL}/api/status", 60) if wait else True
    return {"ok": ok, "pid": pid}


@app.post("/api/start")
def api_start(req: StartReq) -> dict:
    out: dict[str, Any] = {"ok": True}
    if req.scope in ("all", "comfy"):
        out["comfy"] = start_comfy(req.mode, req.wait)
    if req.scope in ("all", "web"):
        out["web"] = start_web(req.wait)
    if req.scope == "all":
        _start_tunnel()
    out["ok"] = all(v.get("ok") for k, v in out.items() if isinstance(v, dict))
    return out


def _start_tunnel() -> None:
    try:
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", str(ROOT / "tools" / "tunnel.ps1"), "start"],
                       capture_output=True, timeout=60, creationflags=CREATE_NO_WINDOW)
    except Exception:
        pass


def _stop_tunnel() -> None:
    try:
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", str(ROOT / "tools" / "tunnel.ps1"), "stop"],
                       capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW)
    except Exception:
        pass


@app.post("/api/stop")
def api_stop(req: StopReq) -> dict:
    out: dict[str, Any] = {"ok": True}
    if req.scope == "all":
        _stop_tunnel()
    if req.scope in ("all", "comfy"):
        pids = list(set(find_pids("comfyui\\main.py") + find_pids("comfyui/main.py")))
        out["comfy_killed"] = kill_pids(pids)
    if req.scope in ("all", "web"):
        out["web_killed"] = kill_pids(list(set(find_pids("webapp.app"))))
    time.sleep(1)
    return out


@app.post("/api/restart")
def api_restart(req: StopReq) -> dict:
    api_stop(req)
    time.sleep(2)
    return api_start(StartReq(scope=req.scope))


# --------------------------------------------------------------------------- #
# 概览 / 设置
# --------------------------------------------------------------------------- #
@app.get("/api/overview")
def api_overview() -> dict:
    cfg = read_settings()
    return {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "comfy": comfy_state(),
        "web": web_state(),
        "hw": hw_state(),
        "ports": {p: port_owner(p) for p in (8188, 8190, LAUNCHER_PORT)},
        "tdr": tdr_state(),
        "settings": {"start_args": comfy_start_args(cfg),
                     "mode": (launcher_cfg() or {}).get("mode", "normal")},
        "launcher": launcher_cfg(),
        "version": {"comfyui": _comfy_version_file(), "torch": _torch_version()},
    }


def _comfy_version_file() -> str | None:
    try:
        txt = (COMFY_DIR / "comfyui_version.py").read_text(encoding="utf-8")
        m = re.search(r'__version__\s*=\s*"([^"]+)"', txt)
        return m.group(1) if m else None
    except Exception:
        return None


_TORCH_CACHE: str | None | bool = False


def _torch_version() -> str | None:
    global _TORCH_CACHE
    if _TORCH_CACHE is not False:
        return _TORCH_CACHE if isinstance(_TORCH_CACHE, str) else None
    try:
        out = subprocess.check_output([str(PY), "-c", "import torch;print(torch.__version__)"],
                                      text=True, timeout=30, creationflags=CREATE_NO_WINDOW)
        _TORCH_CACHE = out.strip()
    except Exception:
        _TORCH_CACHE = None
    return _TORCH_CACHE if isinstance(_TORCH_CACHE, str) else None


class SafetyReq(BaseModel):
    unlimited_h3: bool


TUNNEL_CFG = DATA / "tunnel.json"
TUNNEL_URL_FILE = DATA / "tunnel_url.txt"
ALLOWLIST_FILE = DATA / "ip_allowlist.json"


class TunnelCfg(BaseModel):
    mode: str = "quick"
    token: str = ""
    domain: str = ""


class AllowlistCfg(BaseModel):
    enabled: bool = True
    ips: list[str] = []


def _read_tunnel_cfg() -> dict:
    cfg = {"mode": "quick", "token": "", "domain": ""}
    try:
        if TUNNEL_CFG.is_file():
            cfg.update(json.loads(TUNNEL_CFG.read_text(encoding="utf-8")))
    except Exception:
        pass
    return cfg


def _tunnel_status() -> dict:
    """直接读 pid/url 文件，避免每次轮询都启动 powershell 导致控制台窗口闪烁。"""
    cfg = _read_tunnel_cfg()
    pid = 0
    try:
        txt = (DATA / "tunnel.pid").read_text(encoding="ascii").strip()
        if txt.isdigit():
            pid = int(txt)
    except Exception:
        pid = 0
    running = False
    if pid:
        try:
            import psutil
            p = psutil.Process(pid)
            running = "cloudflared" in (p.name() or "").lower()
        except Exception:
            running = False
    url = ""
    try:
        url = (DATA / "tunnel_url.txt").read_text(encoding="ascii").strip()
    except Exception:
        pass
    return {"running": running, "pid": pid if running else 0, "url": url,
            "mode": cfg.get("mode", ""), "domain": cfg.get("domain", "")}


@app.get("/api/tunnel")
def api_tunnel_get() -> dict:
    cfg = _read_tunnel_cfg()
    st = _tunnel_status()
    st["config"] = cfg
    try:
        st["allowlist"] = json.loads(ALLOWLIST_FILE.read_text(encoding="utf-8"))
    except Exception:
        st["allowlist"] = {"enabled": True, "ips": []}
    return st


@app.post("/api/tunnel")
def api_tunnel_save(req: TunnelCfg) -> dict:
    TUNNEL_CFG.parent.mkdir(parents=True, exist_ok=True)
    TUNNEL_CFG.write_text(json.dumps({"mode": req.mode, "token": req.token.strip(),
                                      "domain": req.domain.strip()}, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return {"ok": True, "config": _read_tunnel_cfg()}


@app.post("/api/tunnel/action/{act}")
def api_tunnel_action(act: str) -> dict:
    if act not in ("start", "stop", "restart"):
        return {"ok": False, "error": "act must be start/stop/restart"}
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                              "-File", str(ROOT / "tools" / "tunnel.ps1"), act],
                             capture_output=True, text=True, timeout=90,
                             creationflags=CREATE_NO_WINDOW)
        ok = out.returncode == 0
        return {"ok": ok, "output": ((out.stdout or "") + (out.stderr or ""))[-800:],
                "status": _tunnel_status()}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@app.post("/api/tunnel/test")
def api_tunnel_test() -> dict:
    st = _tunnel_status()
    url = (st.get("url") or "").rstrip("/")
    if not url:
        try:
            url = TUNNEL_URL_FILE.read_text(encoding="utf-8").strip().rstrip("/")
        except Exception:
            url = ""
    if not url:
        return {"ok": False, "error": "隧道未运行或没有 URL"}
    token = ""
    try:
        token = (DATA / "bridge_token.txt").read_text(encoding="utf-8").strip()
    except Exception:
        pass
    try:
        import urllib.request
        req = urllib.request.Request(url + "/api/bridge/health",
                                     headers={"X-Bridge-Token": token})
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read().decode("utf-8", "ignore")
            return {"ok": True, "url": url, "status": r.status, "body": body[:400]}
    except Exception as exc:  # noqa: BLE001
        status = getattr(exc, "code", None)
        if status == 403:
            return {"ok": True, "url": url, "status": 403,
                    "note": "隧道可达，但当前来源 IP 不在白名单（AstrBot 服务器访问会放行）"}
        return {"ok": False, "url": url, "error": str(exc)}


CLOUDFLARED_EXE = Path(r"C:\Tools\cloudflared\cloudflared.exe")
CLOUDFLARED_CERT = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".cloudflared" / "cert.pem"


@app.get("/api/tunnel/login/status")
def api_tunnel_login_status() -> dict:
    return {"ok": True, "cert": CLOUDFLARED_CERT.is_file(),
            "path": str(CLOUDFLARED_CERT), "exe": str(CLOUDFLARED_EXE),
            "exe_exists": CLOUDFLARED_EXE.is_file()}


@app.post("/api/tunnel/login")
def api_tunnel_login() -> dict:
    if not CLOUDFLARED_EXE.is_file():
        return {"ok": False, "error": "cloudflared 不存在"}
    subprocess.Popen([str(CLOUDFLARED_EXE), "tunnel", "login"],
                     creationflags=0x08000000 | 0x00000200)
    return {"ok": True, "note": "已打开浏览器，请登录 Cloudflare 并选择域名，完成后回到本页刷新"}


class TunnelSetupReq(BaseModel):
    name: str = "comfy-bridge"
    domain: str = ""


@app.post("/api/tunnel/setup")
def api_tunnel_setup(req: TunnelSetupReq) -> dict:
    if not CLOUDFLARED_EXE.is_file():
        return {"ok": False, "error": "cloudflared 不存在"}
    if not CLOUDFLARED_CERT.is_file():
        return {"ok": False, "error": "尚未登录 Cloudflare，请先点“登录 Cloudflare”"}
    domain = req.domain.strip().replace("https://", "").replace("http://", "").strip("/")
    if not domain or "." not in domain:
        return {"ok": False, "error": "请填写完整域名，例如 bridge.example.com"}
    name = (req.name or "comfy-bridge").strip()
    logs: list[str] = []

    def run(args: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
        cp = subprocess.run([str(CLOUDFLARED_EXE), *args], capture_output=True, text=True,
                            timeout=timeout, creationflags=CREATE_NO_WINDOW)
        logs.append((" ".join(args) + "\n" + (cp.stdout or "") + (cp.stderr or ""))[-500:])
        return cp

    try:
        cp = run(["tunnel", "create", name])
        if cp.returncode != 0 and "already exists" not in ((cp.stdout or "") + (cp.stderr or "")).lower():
            return {"ok": False, "error": "创建隧道失败", "logs": logs[-2:]}
        cp = run(["tunnel", "route", "dns", name, domain])
        if cp.returncode != 0 and "already" not in ((cp.stdout or "") + (cp.stderr or "")).lower():
            return {"ok": False, "error": "绑定域名失败（域名需托管在 Cloudflare）", "logs": logs[-2:]}
        cp = run(["tunnel", "token", name])
        token = ""
        for line in reversed((cp.stdout or "").splitlines()):
            line = line.strip()
            if line and not line.startswith("20") and " " not in line and len(line) > 40:
                token = line
                break
        if not token:
            return {"ok": False, "error": "获取隧道 token 失败", "logs": logs[-2:]}
        TUNNEL_CFG.write_text(json.dumps({"mode": "token", "token": token, "domain": domain},
                                          ensure_ascii=False, indent=2), encoding="utf-8")
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", str(ROOT / "tools" / "tunnel.ps1"), "restart"],
                       capture_output=True, text=True, timeout=120,
                       creationflags=CREATE_NO_WINDOW)
        return {"ok": True, "domain": domain, "url": f"https://{domain}", "logs": logs[-1:]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "logs": logs[-2:]}


@app.post("/api/security/allowlist")
def api_allowlist_save(req: AllowlistCfg) -> dict:
    ALLOWLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    ALLOWLIST_FILE.write_text(json.dumps({"enabled": bool(req.enabled),
                                          "ips": [x.strip() for x in req.ips if x.strip()]},
                                         ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "allowlist": json.loads(ALLOWLIST_FILE.read_text(encoding="utf-8"))}


@app.get("/api/safety")
def api_safety() -> dict:
    cfg = read_settings()
    s = cfg.setdefault("safety", {})
    return {"unlimited_h3": bool(s.get("unlimited_h3", True)),
            "min_ram_free_gb": s.get("min_ram_free_gb"),
            "trip_ram_free_gb": s.get("trip_ram_free_gb")}


@app.post("/api/safety")
def api_safety_save(req: SafetyReq) -> dict:
    cfg = read_settings()
    cfg.setdefault("safety", {})["unlimited_h3"] = bool(req.unlimited_h3)
    write_settings(cfg)
    return {"ok": True, "unlimited_h3": bool(req.unlimited_h3)}


@app.get("/api/settings")
def api_settings() -> dict:
    return {"comfy": {"start_args": comfy_start_args()}, "launcher": launcher_cfg()}


@app.post("/api/settings")
def api_settings_save(req: SettingsReq) -> dict:
    cfg = read_settings()
    launcher = launcher_cfg()
    if req.start_args is not None:
        cfg.setdefault("comfy", {})["start_args"] = [str(x) for x in req.start_args]
    if req.mode is not None:
        launcher["mode"] = req.mode
    if req.theme is not None:
        launcher["theme"] = req.theme
    if req.auto_open_web is not None:
        launcher["auto_open_web"] = bool(req.auto_open_web)
    if req.refresh_ms is not None:
        launcher["refresh_ms"] = max(1000, min(10000, int(req.refresh_ms)))
    if req.bg is not None:
        launcher["bg"] = str(req.bg)
    if req.bg_blur is not None:
        launcher["bg_blur"] = max(0, min(40, int(req.bg_blur)))
    if req.bg_dim is not None:
        launcher["bg_dim"] = max(0.0, min(0.95, float(req.bg_dim)))
    if req.accent is not None:
        launcher["accent"] = str(req.accent)
    cfg["launcher"] = launcher
    write_settings(cfg)
    return {"ok": True, "settings": {"comfy": {"start_args": comfy_start_args()}, "launcher": launcher}}


# --------------------------------------------------------------------------- #
# 模型管理
# --------------------------------------------------------------------------- #
MODEL_GROUPS = ["diffusion_models", "text_encoders", "vae", "loras", "checkpoints",
                "upscale_models", "clip_vision", "controlnet", "model_patches"]


@app.get("/api/models")
def api_models() -> dict:
    groups = []
    total = 0
    for g in MODEL_GROUPS:
        d = MODEL_ROOT / g
        files = []
        size = 0
        if d.is_dir():
            for p in sorted(d.rglob("*")):
                if p.is_file() and p.suffix.lower() in (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf"):
                    try:
                        st = p.stat()
                    except OSError:
                        continue
                    files.append({"name": str(p.relative_to(d)).replace("\\", "/"),
                                  "mb": round(st.st_size / 1048576, 1),
                                  "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%m-%d %H:%M")})
                    size += st.st_size
        total += size
        groups.append({"group": g, "files": files, "gb": round(size / 2**30, 2)})
    return {"groups": groups, "total_gb": round(total / 2**30, 2)}


class ModelDeleteReq(BaseModel):
    group: str
    name: str


@app.post("/api/models/delete")
def api_models_delete(req: ModelDeleteReq) -> dict:
    if req.group not in MODEL_GROUPS:
        raise HTTPException(400, "invalid group")
    root = (MODEL_ROOT / req.group).resolve()
    target = (root / req.name).resolve()
    if not str(target).startswith(str(root)) or not target.is_file():
        raise HTTPException(400, "invalid path")
    trash = MODEL_ROOT / "_trash" / datetime.now().strftime("%Y%m%d_%H%M%S")
    trash.mkdir(parents=True, exist_ok=True)
    dst = trash / target.name
    shutil.move(str(target), str(dst))
    return {"ok": True, "moved_to": str(dst)}


@app.post("/api/open")
def api_open_folder(req: dict) -> dict:
    path = Path(str(req.get("path") or ""))
    allowed = [MODEL_ROOT, LOGS, ROOT / "docs", DATA, COMFY_DIR / "output"]
    if not any(str(path).lower().startswith(str(a).lower()) for a in allowed) or not path.exists():
        raise HTTPException(400, "path not allowed")
    os.startfile(str(path))  # noqa: S606
    return {"ok": True}


# --------------------------------------------------------------------------- #
# 诊断（疑难解答）
# --------------------------------------------------------------------------- #
@app.get("/api/doctor")
def api_doctor() -> dict:
    checks = []

    def add(cid, title, ok, detail, fix=None, level="error"):
        checks.append({"id": cid, "title": title, "ok": bool(ok), "detail": detail,
                       "fix": fix, "level": "ok" if ok else level})

    c = comfy_state()
    add("comfy", "ComfyUI 服务 (8188)", c["up"],
        f"版本 {c.get('version')} · 运行 {c.get('queue_running')} / 排队 {c.get('queue_pending')}" if c["up"] else "未响应",
        fix="restart_comfy")
    add("web", "Web 工作台 (8190)", web_state()["up"], "已就绪" if web_state()["up"] else "未响应", fix="restart_web")

    hw = hw_state()
    add("gpu", "GPU", bool(hw["gpu"]), (hw["gpu"].get("name") or "未检测到") +
        (f" · {hw['gpu'].get('used_mb')}/{hw['gpu'].get('total_mb')}MB · {hw['gpu'].get('temp_c')}°C" if hw["gpu"] else ""),
        level="warn")
    add("ram", "内存", hw["ram_free_gb"] >= 3.0, f"空闲 {hw['ram_free_gb']}GB / {hw['ram_total_gb']}GB", level="warn")
    add("disk", "磁盘 K:", hw["disk_free_gb"] >= 10, f"空闲 {hw['disk_free_gb']}GB / {hw['disk_total_gb']}GB", level="warn")

    tdr = tdr_state()
    add("tdr", "TDR 超时设置", (tdr.get("TdrDelay") or 0) >= 60,
        f"TdrDelay={tdr.get('TdrDelay')} TdrDdiDelay={tdr.get('TdrDdiDelay')}", fix="apply_tdr", level="warn")

    required = [
        ("diffusion_models/qwen_image_2.1_int8_convrot.safetensors", "Qwen-Image 2.1 UNET"),
        ("text_encoders/qwen3vl_8b_int8_convrot.safetensors", "Qwen 文本编码器"),
        ("vae/qwen_image_2.1_vae_bf16.safetensors", "Qwen VAE"),
        ("diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors", "H3 视频模型"),
    ]
    missing = [label for rel, label in required if not (MODEL_ROOT / rel).is_file()]
    add("models", "核心模型文件", not missing,
        "齐全" if not missing else "缺失：" + "、".join(missing), fix="open_models", level="warn")

    log_err = _scan_log_errors()
    add("logs", "日志异常扫描", not log_err, log_err[:160] if log_err else "最近日志无 Traceback/OOM", fix="open_logs", level="warn")

    return {"checks": checks, "ok": all(x["ok"] for x in checks),
            "errors": sum(1 for x in checks if not x["ok"])}


def _scan_log_errors() -> str:
    for name in ("comfy_stderr.log", "webapp_stderr.log"):
        p = LOGS / name
        if not p.is_file():
            continue
        try:
            with p.open("rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 120_000))
                text = f.read().decode("utf-8", "ignore")
        except Exception:
            continue
        lines = text.splitlines()[-300:]
        bad = re.compile(r"CUDA error|out of memory|Error occurred|Prompt outputs failed|"
                         r"Exception during processing|RuntimeError:", re.I)
        hits = [ln for ln in lines if bad.search(ln)]
        if hits:
            return f"{name}: " + hits[-1][-150:]
    return ""


@app.post("/api/action/{name}")
def api_action(name: str) -> dict:
    if name == "free":
        try:
            r = requests.post(f"{COMFY_URL}/free", json={"unload_models": True, "free_memory": True}, timeout=20)
            return {"ok": r.ok, "detail": "已请求 ComfyUI 释放显存/内存"}
        except Exception as exc:
            return {"ok": False, "detail": str(exc)}
    if name == "apply_tdr":
        script = ROOT / "tools" / "apply_tdr_windows.ps1"
        if not script.is_file():
            return {"ok": False, "detail": "缺少 tools/apply_tdr_windows.ps1"}
        subprocess.Popen(["powershell", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                         creationflags=CREATE_NO_WINDOW)
        return {"ok": True, "detail": "已调用 TDR 设置脚本（需重启生效）"}
    if name == "restart_comfy":
        api_stop(StopReq(scope="comfy"))
        time.sleep(2)
        return start_comfy()
    if name == "restart_web":
        api_stop(StopReq(scope="web"))
        time.sleep(1)
        return start_web()
    if name == "open_logs":
        os.startfile(str(LOGS))
        return {"ok": True}
    if name == "open_models":
        os.startfile(str(MODEL_ROOT))
        return {"ok": True}
    if name == "backup":
        return backup_all()
    if name == "shortcut":
        return _do_shortcut()
    if name == "film720p":
        script = ROOT / "tools" / "h3_720p15s.py"
        if not script.is_file():
            return {"ok": False, "detail": "缺少 tools/h3_720p15s.py"}
        log = LOGS / "h3_720p15s_launcher.log"
        fo = open(log, "ab")
        p = subprocess.Popen([str(PY), "-u", str(script), "--segments", "3", "--tag", "launcher"],
                             cwd=str(ROOT), stdout=fo, stderr=subprocess.STDOUT,
                             creationflags=CREATE_NO_WINDOW | NEW_GROUP)
        return {"ok": True, "detail": f"已开始 720p 15s 分段成片（pid {p.pid}，日志 {log.name}，约 18 分钟）"}
    raise HTTPException(404, "unknown action")


# --------------------------------------------------------------------------- #
# 日志
# --------------------------------------------------------------------------- #
LOG_FILES = {
    "comfy_err": ("comfy_stderr.log", "ComfyUI stderr"),
    "comfy_out": ("comfy_stdout.log", "ComfyUI stdout"),
    "web_err": ("webapp_stderr.log", "Web UI stderr"),
    "web_out": ("webapp_stdout.log", "Web UI stdout"),
    "mcp": ("mcp.log", "MCP 服务"),
}


@app.get("/api/logs")
def api_logs(name: str = "comfy_err", tail: int = 200) -> dict:
    if name not in LOG_FILES:
        raise HTTPException(400, "unknown log")
    fname, title = LOG_FILES[name]
    p = LOGS / fname
    if not p.is_file():
        return {"name": name, "title": title, "text": "(日志尚未生成)"}
    tail = max(20, min(2000, int(tail)))
    try:
        with p.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - tail * 400))
            data = f.read().decode("utf-8", "ignore")
    except Exception as exc:
        return {"name": name, "title": title, "text": f"(读取失败: {exc})"}
    lines = data.splitlines()[-tail:]
    clean = [re.sub(r"\x1b\[[0-9;]*m", "", ln) for ln in lines]
    return {"name": name, "title": title, "text": "\n".join(clean)}


# --------------------------------------------------------------------------- #
# 任务
# --------------------------------------------------------------------------- #
@app.get("/api/jobs")
def api_jobs(limit: int = 20) -> dict:
    p = DATA / "jobs.json"
    if not p.is_file():
        return {"jobs": []}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"jobs": [], "error": "jobs.json 解析失败"}
    jobs = (d.get("jobs") or [])[-max(1, min(100, limit)):]
    out = []
    for j in reversed(jobs):
        outs = j.get("outputs") or []
        out.append({
            "id": j.get("id"), "template": j.get("template"), "status": j.get("status"),
            "created_at": j.get("created_at"), "finished_at": j.get("finished_at"),
            "error": (j.get("error") or "")[:200],
            "output": (outs[0].get("filename") or outs[0].get("path")) if outs else None,
            "size_mb": outs[0].get("size_mb") if outs else None,
        })
    return {"jobs": out}


@app.post("/api/jobs/{job_id}/cancel")
def api_job_cancel(job_id: str) -> dict:
    try:
        r = requests.post(f"{WEB_URL}/api/jobs/{job_id}/cancel", timeout=15)
        return {"ok": r.ok, "detail": r.text[:200]}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


# --------------------------------------------------------------------------- #
# 小工具 / 备份 / 快捷方式
# --------------------------------------------------------------------------- #
def backup_all() -> dict:
    BACKUPS.mkdir(parents=True, exist_ok=True)
    name = f"launcher_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    target = BACKUPS / name
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for p in [SETTINGS, DATA / "jobs.json", DATA / "upscale_jobs.json"]:
            if p.is_file():
                z.write(p, p.relative_to(ROOT))
        for p in sorted((ROOT / "docs").glob("*.md")):
            z.write(p, p.relative_to(ROOT))
    return {"ok": True, "detail": f"已备份到 {target}", "path": str(target)}


@app.post("/api/action/quicktest/{kind}")
def api_quicktest(kind: str) -> dict:
    presets = {
        "qwen": {"template": "qwen_image", "params": {"prompt": "a red apple on a wooden table, soft light",
                                                       "width": 1024, "height": 1024, "steps": 25}},
        "h3": {"template": "minimax_h3_t2v", "params": {"prompt": "a red apple on a wooden table, slow push in",
                                                        "width": 608, "height": 352, "duration_s": 2,
                                                        "steps": 4, "optimize": "max"}},
        "h3_720p": {"template": "minimax_h3_t2v_720p15s",
                    "params": {"prompt": "cinematic wide shot, a red apple on a rustic wooden table by a window, soft morning light, slow camera push in"}},
    }
    if kind not in presets:
        raise HTTPException(404, "unknown quicktest")
    try:
        r = requests.post(f"{WEB_URL}/api/generate", json=presets[kind], timeout=60)
        d = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        return {"ok": r.ok and d.get("ok", False), "detail": d.get("job_id") or r.text[:200]}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


def _do_shortcut() -> dict:
    ps = ROOT / "launcher" / "create_shortcut.ps1"
    if not ps.is_file():
        return {"ok": False, "detail": "缺少 launcher/create_shortcut.ps1"}
    try:
        out = subprocess.check_output(["powershell", "-ExecutionPolicy", "Bypass", "-File", str(ps)],
                                      text=True, timeout=60, creationflags=CREATE_NO_WINDOW)
        return {"ok": True, "detail": out.strip() or "桌面快捷方式已创建"}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


# --------------------------------------------------------------------------- #
# 个性化主题
# --------------------------------------------------------------------------- #
ASSETS = Path(__file__).with_name("assets")
ASSETS.mkdir(parents=True, exist_ok=True)


@app.get("/api/theme")
def api_theme() -> dict:
    files = sorted([p.name for p in ASSETS.iterdir()
                    if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")])
    return {"assets": files, "launcher": launcher_cfg()}


@app.post("/api/theme/upload")
async def api_theme_upload(file: UploadFile = File(...)) -> dict:
    suffix = Path(file.filename or "bg.png").suffix.lower()
    if suffix not in (".png", ".jpg", ".jpeg", ".webp"):
        raise HTTPException(400, "仅支持 png/jpg/webp")
    raw = await file.read()
    if len(raw) > 20 * 1024 * 1024:
        raise HTTPException(400, "图片需小于 20MB")
    name = f"custom_{datetime.now().strftime('%Y%m%d_%H%M%S')}{suffix}"
    (ASSETS / name).write_bytes(raw)
    cfg = read_settings()
    launcher_cfg()["bg"] = name
    cfg["launcher"] = launcher_cfg()
    write_settings(cfg)
    return {"ok": True, "name": name}


# --------------------------------------------------------------------------- #
# 页面
# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (Path(__file__).with_name("index.html")).read_text(encoding="utf-8")


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/assets/{name}")
def asset(name: str):
    p = ASSETS / Path(name).name
    if not p.is_file():
        raise HTTPException(404)
    return FileResponse(str(p))


@app.get("/favicon.ico")
def favicon():
    p = Path(__file__).with_name("favicon.ico")
    if p.is_file():
        return FileResponse(str(p))
    raise HTTPException(404)
