"""One-off: 720p character refs (girl/boy) + H3 r2v 5s video, with telemetry."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests

ROOT = Path(r"K:\CONFI\comfy_mcp")
sys.path.insert(0, str(ROOT))
import workflow  # noqa: E402

BASE = "http://127.0.0.1:8188"
COMFY = Path(r"K:\CONFI\mian\ComfyUI_windows_portable\ComfyUI")
OUT = COMFY / "output"
INP = COMFY / "input"
LOGS = ROOT / "logs"
STYLE = ("Makoto Shinkai anime style, ultra-detailed background art, hyper-realistic lighting, "
         "vibrant gradient sky, dramatic lens flare, 2D cel-shaded characters blended with painterly "
         "scenery, 16:9, 4K. ")


def smi() -> dict:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used,temperature.gpu,utilization.gpu,power.draw",
             "--format=csv,noheader,nounits"], text=True, timeout=5,
            creationflags=0x08000000).strip()
        used, temp, util, power = [x.strip() for x in out.split(",")]
        return {"vram": int(float(used)), "temp": float(temp), "util": int(float(util)),
                "power": float(power)}
    except Exception:
        return {}


class Mon(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.stop = threading.Event()
        self.rows: list[dict] = []

    def run(self):
        while not self.stop.is_set():
            s = smi()
            if s:
                self.rows.append(s)
            self.stop.wait(2)

    def summary(self) -> dict:
        if not self.rows:
            return {}
        return {"temp_max": max(r["temp"] for r in self.rows),
                "temp_avg": round(sum(r["temp"] for r in self.rows) / len(self.rows), 1),
                "power_max": max(r["power"] for r in self.rows),
                "power_avg": round(sum(r["power"] for r in self.rows) / len(self.rows), 1),
                "vram_peak": max(r["vram"] for r in self.rows),
                "samples": len(self.rows)}


def submit(api: dict) -> str:
    r = requests.post(BASE + "/prompt", json={"prompt": api, "client_id": "char720"}, timeout=60)
    r.raise_for_status()
    return r.json()["prompt_id"]


def wait(pid: str, timeout: int = 2400) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        h = requests.get(f"{BASE}/history/{pid}", timeout=20).json().get(pid)
        if h:
            st = h.get("status") or {}
            if st.get("completed") or st.get("status_str") == "success":
                return {"status": "done", "seconds": round(time.time() - t0, 1), "h": h}
            if st.get("status_str") in ("error", "failed"):
                return {"status": "failed", "seconds": round(time.time() - t0, 1), "h": h}
        time.sleep(4)
    return {"status": "timeout", "seconds": round(time.time() - t0, 1), "h": None}


def media(history: dict, ext: tuple[str, ...]) -> Path | None:
    for node in (history.get("outputs") or {}).values():
        for key in ("images", "videos", "gifs"):
            for it in node.get(key, []):
                name = (it.get("filename") or "")
                if name.lower().endswith(ext):
                    p = OUT / (it.get("subfolder") or "") / name
                    if p.is_file():
                        return p
    return None


def gen_image(name: str, prompt: str, steps: int = 20) -> Path:
    pkg = workflow.resolve("qwen_image", {"prompt": STYLE + prompt, "width": 1024,
                                          "height": 1024, "steps": steps, "seed": 2026112100})
    pid = submit(pkg["api"])
    res = wait(pid, 600)
    if res["status"] != "done":
        raise RuntimeError(f"{name} image failed: {res['status']}")
    p = media(res["h"], (".png", ".jpg", ".jpeg"))
    if not p:
        raise RuntimeError(f"{name} image no output")
    dst = INP / f"char_{name}.png"
    shutil.copy2(p, dst)
    return dst


def main() -> int:
    LOGS.mkdir(exist_ok=True)
    girl_prompt = ("Character reference sheet, a Japanese high school girl, long black hair with a ribbon, "
                   "white short-sleeve shirt, navy pleated skirt, gentle expression, standing, soft sunset "
                   "backlight, clean single character, full body.")
    boy_prompt = ("Character reference sheet, a Japanese high school boy, short black hair, dark school "
                  "uniform, slender, earnest expression, standing, soft dusk light, clean single character, "
                  "full body.")
    report: dict = {"started": time.strftime("%F %T")}
    t0 = time.time()
    girl = gen_image("girl", girl_prompt)
    boy = gen_image("boy", boy_prompt)
    report["images"] = {"girl": str(girl), "boy": str(boy), "seconds": round(time.time() - t0, 1)}

    video_prompt = STYLE + ("Wide shot, the train disappears into the distance and the railroad crossing "
                            "barrier rises. The SAME girl from the reference (white shirt, navy pleated skirt, "
                            "long black hair) stands at the platform edge while the SAME boy from the reference "
                            "(dark school uniform, short black hair) stops on the opposite side of the tracks. "
                            "They look at each other across the rails during blue hour twilight, glowing dust "
                            "motes, slow camera pull-back, cinematic anime.")
    pkg = workflow.resolve("minimax_h3_r2v", {
        "prompt": video_prompt, "ref_images": [girl.name, boy.name],
        "width": 1280, "height": 736, "duration_s": 5, "steps": 8,
        "optimize": "max", "seed": 2026112161,
    })
    mon = Mon()
    mon.start()
    v0 = time.time()
    try:
        pid = submit(pkg["api"])
        res = wait(pid, 2400)
    finally:
        mon.stop.set()
        mon.join(timeout=2)
    vp = media(res["h"], (".mp4", ".webm")) if res["h"] else None
    report["video"] = {"status": res["status"], "seconds": round(time.time() - v0, 1),
                       "path": str(vp) if vp else None,
                       "resolution": "1280x736", "steps": 8, "duration_s": 5,
                       "telemetry": mon.summary()}
    if res["status"] != "done" and res["h"]:
        report["video"]["error"] = str((res["h"].get("status") or {}).get("messages"))[:800]
    (LOGS / "run_720p_char_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                                    encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if res["status"] == "done" else 3


if __name__ == "__main__":
    raise SystemExit(main())
