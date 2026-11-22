"""Stress test runner: N x SEG_S continuous segments, chained via first frame.

Usage examples:
  python tools/stress_1min_film.py                      # 4x15s, 8 steps, optimize=max
  python tools/stress_1min_film.py --steps 15 --optimize lowmem --two-pass --tag crash_repro
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(r"K:\CONFI\comfy_mcp")
APP = Path(r"K:\CONFI\mian\ComfyUI_windows_portable")
COMFY = APP / "ComfyUI"
OUTPUT = COMFY / "output" / "video"
FFMPEG = r"C:\ffmpeg\ffmpeg-master-latest-win64-gpl-shared\bin\ffmpeg.exe"
FFPROBE = FFMPEG.replace("ffmpeg.exe", "ffprobe.exe")
BASE = "http://127.0.0.1:8188"

sys.path.insert(0, str(ROOT))
import workflow  # noqa: E402
import psutil  # noqa: E402

W, H, SEG_S = 608, 352, 15
RAM_ABORT_MB = 800

SHOTS = [
    "Cinematic continuous take, photorealistic live-action film. Aerial drone shot gliding forward "
    "over a coastal city at golden hour: waves breaking on the shore, traffic flowing on a seaside road, "
    "seagulls crossing the frame, warm sunlight and long shadows, continuous forward bank and slow yaw, "
    "strong parallax, natural motion blur, film grain, no text, no watermark. Audio: waves, city hum, "
    "orchestral swell, distant gulls.",
    "Continuing the SAME single continuous shot without a cut: the camera descends from the aerial view "
    "toward the waterfront promenade, following a cyclist moving along the railing, then tracks alongside "
    "him as boats drift in the harbor beyond, golden hour light, seagulls, continuous forward motion, "
    "smooth camera glide, photorealistic, film grain, no text, no watermark. Audio: wheels on pavement, "
    "harbor ambience, strings building.",
    "Continuing the SAME continuous shot without a cut: the camera rises again and swings over the harbor "
    "toward a rail bridge, tracking a commuter train crossing it, reflections on the water, cargo ships "
    "moving in the distance, sun sinking behind clouds, continuous lateral and forward camera movement, "
    "photorealistic, anamorphic flare, film grain, no text, no watermark. Audio: train rumble, horn, "
    "city ambience, orchestral transition.",
    "Continuing the SAME continuous shot without a cut: the camera climbs into the sunset clouds above the "
    "city, moving forward through volumetric light, the skyline and coastline revealed below with city "
    "lights switching on, the sun setting into the sea, continuous ascending glide then slow fade to "
    "warm white, photorealistic, cinematic color grade, film grain, no text, no watermark. Audio: wind, "
    "distant city, piano and strings resolving, soft chime on the fade.",
]
SEEDS = [2026111110, 2026111111, 2026111112, 2026111113]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--segments", type=int, default=4)
    ap.add_argument("--steps", type=int, default=8)
    ap.add_argument("--optimize", default="max")
    ap.add_argument("--two-pass", action="store_true")
    ap.add_argument("--two-pass-scale", type=float, default=1.9)
    ap.add_argument("--tag", default="stress_1min")
    args = ap.parse_args()

    art = ROOT / "artifacts" / args.tag
    logp = ROOT / "logs" / f"{args.tag}.log"
    art.mkdir(parents=True, exist_ok=True)
    logp.parent.mkdir(parents=True, exist_ok=True)

    def log(msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        with logp.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def get(path: str, timeout: int = 10):
        return requests.get(BASE + path, timeout=timeout).json()

    def wait_job(pid: str, timeout_s: int = 3600) -> tuple[str, dict]:
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            avail = psutil.virtual_memory().available / (1024 * 1024)
            if avail < RAM_ABORT_MB:
                log(f"!! RAM low {avail:.0f}MB < {RAM_ABORT_MB}MB -> interrupt")
                try:
                    requests.post(BASE + "/interrupt", timeout=10)
                except Exception:
                    pass
                return "ram_abort", {}
            hist = get("/history", timeout=15)
            entry = hist.get(pid)
            if entry:
                st = entry.get("status") or {}
                if st.get("completed") or st.get("status_str") == "success":
                    return "completed", entry
                if st.get("status_str") in ("error", "failed"):
                    return "failed", entry
            time.sleep(4)
        return "timeout", {}

    def upload_image(p: Path) -> str:
        with p.open("rb") as fh:
            r = requests.post(BASE + "/upload/image",
                              files={"image": (p.name, fh, "image/png")},
                              data={"overwrite": "true"}, timeout=60)
        r.raise_for_status()
        return r.json()["name"]

    def last_frame(mp4: Path, png: Path) -> bool:
        r = subprocess.run([FFMPEG, "-y", "-sseof", "-0.20", "-i", str(mp4), "-frames:v", "1",
                            "-q:v", "2", str(png)], capture_output=True, text=True)
        return r.returncode == 0 and png.exists()

    log(f"start {args.tag}: {args.segments} x {SEG_S}s, {W}x{H}, {args.steps} steps, "
        f"optimize={args.optimize}, two_pass={args.two_pass} scale={args.two_pass_scale}")
    stats = get("/system_stats", timeout=10)
    log("comfy up: " + str(stats.get("system", {}).get("comfyui_version")))

    segments: list[Path] = []
    first_frame = None
    for seg in range(1, args.segments + 1):
        t0 = time.time()
        params = {
            "prompt": SHOTS[(seg - 1) % len(SHOTS)],
            "width": W, "height": H, "duration_s": SEG_S,
            "steps": args.steps, "seed": SEEDS[(seg - 1) % len(SEEDS)],
            "optimize": args.optimize, "output_prefix": f"video/{args.tag}_seg{seg}",
        }
        if args.two_pass:
            params["two_pass"] = True
            params["two_pass_scale"] = args.two_pass_scale
        tmpl = "minimax_h3_t2v"
        if first_frame:
            tmpl = "minimax_h3_fl2v"
            params["first_frame"] = first_frame
        pkg = workflow.resolve(tmpl, params)
        api = pkg["api"]
        log(f"seg{seg}: submit ({tmpl}, steps={args.steps}, opt={args.optimize}, two_pass={args.two_pass})")
        r = requests.post(BASE + "/prompt", json={"prompt": api, "client_id": args.tag}, timeout=60)
        if r.status_code != 200:
            log(f"seg{seg}: submit HTTP {r.status_code}: {r.text[:300]}")
            return 2
        pid = r.json()["prompt_id"]
        log(f"seg{seg}: prompt_id={pid}")
        status, entry = wait_job(pid)
        dt = time.time() - t0
        log(f"seg{seg}: status={status} elapsed={dt:.1f}s")
        if status != "completed":
            log(f"seg{seg}: FAILED ({status}) - stop")
            return 3
        fname = None
        for node in (entry.get("outputs") or {}).values():
            for item in node.get("images", []):
                if item.get("filename", "").endswith(".mp4"):
                    fname = item["filename"]
                    break
            if fname:
                break
        if not fname:
            log(f"seg{seg}: no mp4 output")
            return 4
        dst = art / f"seg{seg}.mp4"
        shutil.copy2(OUTPUT / fname, dst)
        segments.append(dst)
        log(f"seg{seg}: saved {dst.name} ({dst.stat().st_size/1024/1024:.2f}MB)")
        if seg < args.segments:
            png = art / f"seg{seg}_last.png"
            if not last_frame(dst, png):
                log(f"seg{seg}: last frame extraction failed")
                return 5
            first_frame = upload_image(png)
            log(f"seg{seg}: first_frame={first_frame}")

    lst = art / "concat_list.txt"
    with lst.open("w", encoding="utf-8") as f:
        for p in segments:
            f.write(f"file '{p.as_posix()}'\n")
    film = art / f"{args.tag}.mp4"
    r = subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-c", "copy", "-movflags", "+faststart", str(film)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        log("concat failed: " + (r.stderr or "")[-600:])
        return 6
    probe = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration,size",
                            "-of", "json", str(film)], capture_output=True, text=True)
    log("concat OK -> " + str(film))
    log("final: " + (probe.stdout or "").strip().replace("\n", " "))
    log("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
