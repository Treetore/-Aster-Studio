"""Aster · ComfyUI 桌面启动器入口。

- 在本机 8191 端口启动 FastAPI（launcher.app）
- 用 Edge/Chrome 的 --app 模式打开独立窗口（无地址栏，接近原生桌面应用）
- 关闭窗口后退出启动器进程（不停止 ComfyUI）
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import requests

ROOT = Path(r"K:\CONFI\comfy_mcp")
PORT = int(os.environ.get("LAUNCHER_PORT", "8191"))
URL = f"http://127.0.0.1:{PORT}/"
BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]
PROFILE = ROOT / "data" / "launcher_profile"


def _ensure_streams() -> None:
    """pythonw 下 sys.stdout/stderr 为 None，uvicorn 日志会因此崩溃，这里重定向到文件。"""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        logdir = ROOT / "logs"
        logdir.mkdir(parents=True, exist_ok=True)
        f = open(logdir / "launcher_pythonw.log", "a", buffering=1, encoding="utf-8")
    except Exception:
        return
    if sys.stdout is None:
        sys.stdout = f
    if sys.stderr is None:
        sys.stderr = f


def serve() -> None:
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    _ensure_streams()
    import uvicorn
    uvicorn.run("launcher.app:app", host="127.0.0.1", port=PORT,
                log_level="warning", log_config=None)


def wait_ready(timeout: float = 20.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            r = requests.get(URL + "health", timeout=1.0)
            if r.ok:
                return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


def open_window() -> subprocess.Popen | None:
    for exe in BROWSERS:
        if not Path(exe).is_file():
            continue
        PROFILE.mkdir(parents=True, exist_ok=True)
        cmd = [exe, f"--app={URL}", "--window-size=1320,900",
               f"--user-data-dir={PROFILE}", "--no-first-run", "--no-default-browser-check",
               "--disable-features=Translate,msEdgeSidebarV2"]
        try:
            return subprocess.Popen(cmd)
        except Exception:
            continue
    webbrowser.open(URL)
    return None


def autostart_project() -> None:
    """快捷方式启动时顺便拉起 ComfyUI/Web（可用 LAUNCHER_NO_AUTOSTART=1 关闭）。"""
    if os.environ.get("LAUNCHER_NO_AUTOSTART") == "1":
        return
    try:
        requests.post(URL + "api/start",
                      json={"scope": "all", "mode": "normal", "wait": False},
                      timeout=30)
    except Exception:
        pass


def main() -> int:
    t = threading.Thread(target=serve, daemon=True)
    t.start()
    if not wait_ready():
        _ensure_streams()
        print("launcher server failed to start", file=sys.stderr)
        return 2
    def self_check() -> None:
        try:
            r = requests.get(URL + "api/doctor", timeout=30)
            data = r.json()
            log = ROOT / "logs" / "launcher_selfcheck.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    threading.Thread(target=self_check, daemon=True).start()
    threading.Thread(target=autostart_project, daemon=True).start()
    proc = open_window()
    if proc is None:
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0
    proc.wait()
    time.sleep(1.5)
    # Edge 可能把 --app 转发给已有实例后自身立即退出；此时旧窗口仍在，保持后端运行
    try:
        import psutil
        def app_alive() -> bool:
            for p in psutil.process_iter(["name", "cmdline"]):
                try:
                    name = (p.info.get("name") or "").lower()
                    if "msedge" not in name and "chrome" not in name:
                        continue
                    if any(f"--app={URL}" in a for a in (p.info.get("cmdline") or [])):
                        return True
                except Exception:
                    continue
            return False
        while app_alive():
            time.sleep(3)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
