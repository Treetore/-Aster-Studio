"""Low-VRAM direct ladder: 12s/15s 720p with H3 context windows + tiled VAE decode."""
from __future__ import annotations

import json
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
LOGS = ROOT / "logs"
STYLE = ("Makoto Shinkai anime style, ultra-detailed background art, hyper-realistic lighting, "
         "vibrant gradient sky, dramatic lens flare, 2D cel-shaded characters blended with painterly "
         "scenery, 16:9, 4K. ")
SHOT1 = ("Wide establishing shot, late summer twilight, towering white and golden-orange cumulonimbus "
         "clouds billowing in a gradient blue-to-violet sky. Silhouetted utility poles and power lines "
         "criss-cross over a quiet Japanese town bathed in warm sunset glow, crepuscular rays, gentle "
         "breeze rustling treetops, slow cinematic camera push-in.")
SHOT12 = ("One continuous 15-second shot: begin on a wide establishing view of late summer twilight, "
          "towering cumulonimbus clouds in a blue-to-violet sky over a quiet Japanese town, power lines "
          "silhouetted; the camera pushes slowly forward and descends toward a rural railroad crossing, "
          "where the red-and-white barrier lowers, warning lights blink, tracks reflect golden sunset, "
          "foxtail grass sways in the evening breeze, seamless cinematic move.")
TESTS = [(12, SHOT1), (15, SHOT12)]


def smi():
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used,temperature.gpu,utilization.gpu,power.draw",
             "--format=csv,noheader,nounits"], text=True, timeout=5,
            creationflags=0x08000000).strip()
        u, t, l, p = [x.strip() for x in out.split(",")]
        return {"vram": int(float(u)), "temp": float(t), "util": int(float(l)), "power": float(p)}
    except Exception:
        return {}


class Mon(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.stop = threading.Event()
        self.rows = []

    def run(self):
        while not self.stop.is_set():
            s = smi()
            if s:
                self.rows.append(s)
            self.stop.wait(2)

    def summary(self):
        if not self.rows:
            return {}
        return {"temp_max": max(r["temp"] for r in self.rows),
                "temp_avg": round(sum(r["temp"] for r in self.rows) / len(self.rows), 1),
                "power_max": max(r["power"] for r in self.rows),
                "power_avg": round(sum(r["power"] for r in self.rows) / len(self.rows), 1),
                "vram_peak": max(r["vram"] for r in self.rows)}


def submit(api):
    r = requests.post(BASE + "/prompt", json={"prompt": api, "client_id": "directctx"}, timeout=60)
    r.raise_for_status()
    return r.json()["prompt_id"]


def wait(pid, timeout=3600):
    t0 = time.time()
    while time.time() - t0 < timeout:
        h = requests.get(f"{BASE}/history/{pid}", timeout=20).json().get(pid)
        if h:
            st = h.get("status") or {}
            if st.get("completed") or st.get("status_str") == "success":
                return {"status": "done", "h": h, "seconds": round(time.time() - t0, 1)}
            if st.get("status_str") in ("error", "failed"):
                return {"status": "failed", "h": h, "seconds": round(time.time() - t0, 1)}
        time.sleep(5)
    return {"status": "timeout", "h": None, "seconds": round(time.time() - t0, 1)}


def find_video(h):
    for node in (h.get("outputs") or {}).values():
        for key in ("videos", "gifs", "images"):
            for it in node.get(key, []):
                name = it.get("filename") or ""
                if name.lower().endswith((".mp4", ".webm")):
                    p = OUT / (it.get("subfolder") or "") / name
                    if p.is_file():
                        return p
    return None


def main():
    report = {"started": time.strftime("%F %T"), "tests": []}
    for secs, prompt in TESTS:
        pkg = workflow.resolve("minimax_h3_t2v", {
            "prompt": STYLE + prompt, "width": 1280, "height": 736,
            "duration_s": secs, "steps": 8, "optimize": "max",
            "context_length": 124, "context_overlap": 39,
            "decode_tiled": True, "seed": 2026112400 + secs,
        })
        mon = Mon(); mon.start(); t0 = time.time()
        try:
            pid = submit(pkg["api"])
            res = wait(pid, 3600)
        finally:
            mon.stop.set(); mon.join(timeout=2)
        media = find_video(res["h"]) if res["h"] else None
        rec = {"seconds": secs, "status": res["status"], "elapsed_s": round(time.time() - t0, 1),
               "telemetry": mon.summary(), "path": str(media) if media else None,
               "scheme": "ctx124/39 + tiled VAE"}
        if res["status"] != "done":
            rec["error"] = str((res["h"].get("status") or {}).get("messages"))[:1200] if res["h"] else "no history"
        report["tests"].append(rec)
        (LOGS / "direct_ctx_12_15.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(rec, ensure_ascii=False), flush=True)
        if res["status"] != "done":
            break
    print(json.dumps({"tested": [t["seconds"] for t in report["tests"]],
                      "last": report["tests"][-1]["status"]}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
