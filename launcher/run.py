"""Aster · ComfyUI 桌面启动器入口。

- 在本机 8191 端口启动 FastAPI（launcher.app）
- 用 Edge/Chrome 的 --app 模式打开独立窗口（无地址栏，接近原生桌面应用）
- 关闭窗口后退出启动器进程（不停止 ComfyUI）
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent  # 项目根目录，从本文件位置推导
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_env() -> None:
    """读取项目根 .env（复用 config 的无依赖解析器），失败不影响启动。"""
    try:
        import config as _cfg

        _cfg.load_env_file(ROOT / ".env")
    except Exception:  # pragma: no cover - 仅在 config 缺失/损坏时
        pass


_load_env()

PORT = int(os.environ.get("LAUNCHER_PORT", "8191"))
URL = f"http://127.0.0.1:{PORT}/"
PROFILE = ROOT / "data" / "launcher_profile"


def _browser_candidates() -> list[str]:
    """浏览器可执行文件候选：环境变量 > Windows 常见安装位置 > macOS/Linux 常见位置。"""
    found: list[str] = []
    env_exe = os.environ.get("LAUNCHER_BROWSER")
    if env_exe:
        found.append(env_exe)
    if os.name == "nt":
        for base in (
            os.environ.get("ProgramFiles(x86)"),
            os.environ.get("ProgramFiles"),
            os.environ.get("LOCALAPPDATA"),
        ):
            if not base:
                continue
            for rel in (
                ("Microsoft", "Edge", "Application", "msedge.exe"),
                ("Google", "Chrome", "Application", "chrome.exe"),
            ):
                found.append(str(Path(base).joinpath(*rel)))
    else:
        found.extend([
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ])
        for name in ("microsoft-edge", "google-chrome", "chromium", "chromium-browser"):
            exe = shutil.which(name)
            if exe:
                found.append(exe)
    seen: set[str] = set()
    out: list[str] = []
    for item in found:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


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
    for exe in _browser_candidates():
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
