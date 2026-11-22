"""1-minute 720p: 8 shots -> 12 x 5s segments, grouped by model, then concat."""
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
LOGS = ROOT / "logs"
WORK = LOGS / "film_1min_720p"
FF = r"C:\ffmpeg\ffmpeg-master-latest-win64-gpl-shared\bin\ffmpeg.exe"
STYLE = ("Makoto Shinkai anime style, ultra-detailed background art, hyper-realistic lighting, "
         "vibrant gradient sky, dramatic lens flare, 2D cel-shaded characters blended with painterly "
         "scenery, 16:9, 4K. ")
GIRL = "the same Japanese high school girl (long black hair, white shirt, navy pleated skirt, red ribbon)"
BOY = "the same Japanese high school boy (short black hair, dark school uniform)"

SHOTS = [
    ("01a", "Wide establishing shot, late summer twilight, towering white and golden-orange cumulonimbus clouds billowing in a gradient blue-to-violet sky. Silhouetted utility poles and power lines criss-cross over a quiet Japanese town bathed in warm sunset glow, crepuscular rays, gentle breeze, slow cinematic camera push-in.", "t2v", [], 6),
    ("01b", "Continuing the same establishing shot: camera keeps pushing slowly forward over town rooftops and power lines, clouds drifting, warm golden-orange light fading into violet, subtle parallax, cinematic anime.", "t2v", [], 6),
    ("02", "Close-up of a Japanese rural railroad crossing at sunset: red-and-white barrier lowering slowly, warning lights blinking warmly, train tracks reflecting golden sunlight, foxtail grass swaying, subtle breathing camera float, rich lens flare.", "t2v", [], 6),
    ("04", "A silver-green Japanese commuter train speeds rapidly across the foreground, sunset clouds reflecting on the windows, golden light flickering between cars, leaves and dust kicked up, dynamic motion blur, cinematic transition.", "t2v", [], 6),
    ("08a", "Sweeping wide crane-up shot: camera tilts upward from the two characters over glowing town rooftops and power lines into a breathtaking starry night sky, deep indigo and violet galaxy clouds, a brilliant cyan-blue comet streaking across the sky.", "t2v", [], 6),
    ("08b", "Continuing the same crane-up: the comet's long cyan trail crosses the galaxy, its light illuminating the coastal town and distant sea below, stars sparkling, awe-inspiring 4K anime finale.", "t2v", [], 6),
    ("03a", f"Medium close-up of {GIRL} standing alone on an empty train platform. Golden sunset backlight rims her long black hair, evening wind blows her hair and ribbon, she gazes softly toward the distant sky, sunset clouds reflected in her eyes, slow push-in with subtle arc.", "r2v", ["girl"], 8),
    ("03b", f"Continuing the same shot of {GIRL} on the platform: she lowers her gaze slightly, ribbon and hair fluttering, warm backlight and lens flare, slow camera arc, cinematic anime.", "r2v", ["girl"], 8),
    ("05a", f"Dynamic side-tracking shot: {BOY} runs urgently down coastal town stone steps toward the train station. Deepening dusk paints the sky magenta, violet and orange, streetlights and a vending machine glowing softly, wind whipping his uniform, fast camera movement.", "r2v", ["boy"], 8),
    ("05b", f"Continuing the side-tracking shot of {BOY}: he keeps running along the slope, breathless, warm streetlights streaking past, dusk sky deepening, motion blur, cinematic anime.", "r2v", ["boy"], 8),
    ("06", f"Signature extreme close-up of {GIRL}'s expressive eyes, detailed irises clearly reflecting the vibrant purple-pink twilight sky, early stars and fading train lights, strands of hair brushing her cheek, a subtle tear glistening, ultra-detailed anime lighting.", "r2v", ["girl"], 8),
    ("07", f"Wide shot: the train disappears and the railroad crossing barrier rises. {GIRL} stands at the platform edge while {BOY} stops on the opposite side of the tracks. They look at each other across the rails during blue hour twilight, glowing dust motes, slow camera pull-back.", "r2v", ["boy", "girl"], 8),
]
ORDER = ["01a", "01b", "02", "03a", "03b", "04", "05a", "05b", "06", "07", "08a", "08b"]
GEN_ORDER = [s for s in SHOTS if s[2] == "t2v"] + [s for s in SHOTS if s[2] == "r2v"]


def smi() -> dict:
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
                "vram_peak": max(r["vram"] for r in self.rows)}


def submit(api: dict) -> str:
    r = requests.post(BASE + "/prompt", json={"prompt": api, "client_id": "film1min"}, timeout=60)
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


def find_media(h: dict, ext: tuple[str, ...]) -> Path | None:
    for node in (h.get("outputs") or {}).values():
        for key in ("images", "videos", "gifs"):
            for it in node.get(key, []):
                name = it.get("filename") or ""
                if name.lower().endswith(ext):
                    p = OUT / (it.get("subfolder") or "") / name
                    if p.is_file():
                        return p
    return None


def concat(parts: list[Path], dst: Path) -> bool:
    args: list[str] = []
    for p in parts:
        args += ["-i", str(p)]
    n = len(parts)
    filt = "".join(f"[{i}:v][{i}:a]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v][a]"
    subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", *args,
                    "-filter_complex", filt, "-map", "[v]", "-map", "[a]",
                    "-c:v", "libx264", "-crf", "17", "-preset", "medium",
                    "-c:a", "aac", "-b:a", "192k", str(dst)], check=False)
    return dst.is_file() and dst.stat().st_size > 0


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    report = {"started": time.strftime("%F %T"), "segments": []}
    t_all = time.time()
    for sid, prompt, tmpl, refs, steps in GEN_ORDER:
        seg = WORK / f"{sid}.mp4"
        if seg.is_file() and seg.stat().st_size > 0:
            print(f"[skip] {sid}", flush=True)
            continue
        params = {"prompt": STYLE + prompt, "width": 1280, "height": 736, "duration_s": 5,
                  "steps": steps, "optimize": "max", "seed": 2026112200 + int(sid[:2])}
        if tmpl == "r2v":
            params["ref_images"] = [f"char_{r}.png" for r in refs]
            pkg = workflow.resolve("minimax_h3_r2v", params)
        else:
            pkg = workflow.resolve("minimax_h3_t2v", params)
        mon = Mon()
        mon.start()
        t0 = time.time()
        try:
            pid = submit(pkg["api"])
            res = wait(pid, 2400)
        finally:
            mon.stop.set()
            mon.join(timeout=2)
        media = find_media(res["h"], (".mp4",)) if res["h"] else None
        rec = {"id": sid, "template": tmpl, "status": res["status"],
               "seconds": round(time.time() - t0, 1), "telemetry": mon.summary(),
               "path": str(seg) if media else None}
        if media:
            shutil.copy2(media, seg)
        else:
            rec["error"] = str((res["h"].get("status") or {}).get("messages"))[:600] if res["h"] else ""
        report["segments"].append(rec)
        (LOGS / "film_1min_720p_progress.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(rec, ensure_ascii=False), flush=True)
        if res["status"] != "done":
            break
    parts = [WORK / f"{sid}.mp4" for sid in ORDER if (WORK / f"{sid}.mp4").is_file()]
    report["output"] = None
    if len(parts) == len(ORDER):
        dst = OUT / "video" / "Summer_End_Railway_1min_720p.mp4"
        report["output"] = str(dst) if concat(parts, dst) else None
    report["total_seconds"] = round(time.time() - t_all, 1)
    (LOGS / "film_1min_720p_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": report["output"], "total_seconds": report["total_seconds"],
                      "segments": len(report["segments"])}, ensure_ascii=False), flush=True)
    return 0 if report["output"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
