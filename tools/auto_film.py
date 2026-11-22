"""Automated film generation according to docs/GENERATION_POLICY_20261111.md.

One command does everything:
  preflight (start ComfyUI if needed, free memory, wait queue, check RAM/VRAM/temp/disk)
  -> generate N segments via direct HTTP (policy enforced by workflow.resolve)
  -> live telemetry + abort rules (temp / RAM / stall / CUDA error)
  -> retry once after auto-restarting ComfyUI when a segment fails
  -> concat + ffprobe + JSON/Markdown run report

Examples:
  python tools/auto_film.py --dry-run
  python tools/auto_film.py --segments 4 --duration-s 15 --steps 8 --optimize max --tag my_film
  python tools/auto_film.py --segments 1 --duration-s 2 --steps 4 --tag smoke
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil
import requests

ROOT = Path(r"K:\CONFI\comfy_mcp")
APP = Path(r"K:\CONFI\mian\ComfyUI_windows_portable")
COMFY = APP / "ComfyUI"
PY = APP / "python_embeded" / "python.exe"
OUTPUT = COMFY / "output" / "video"
FFMPEG = r"C:\ffmpeg\ffmpeg-master-latest-win64-gpl-shared\bin\ffmpeg.exe"
FFPROBE = FFMPEG.replace("ffmpeg.exe", "ffprobe.exe")
SMI = r"C:\Windows\System32\nvidia-smi.exe"
BASE = "http://127.0.0.1:8188"

sys.path.insert(0, str(ROOT))
import config  # noqa: E402
import workflow  # noqa: E402

PROMPTS = [
    "Cinematic continuous take, photorealistic live-action film. Aerial drone shot gliding forward "
    "over a coastal city at golden hour: waves on the shore, traffic on a seaside road, seagulls, warm "
    "sunlight, continuous forward bank and slow yaw, strong parallax, natural motion blur, film grain, "
    "no text, no watermark. Audio: waves, city hum, orchestral swell.",
    "Continuing the SAME single continuous shot without a cut: the camera descends toward the waterfront "
    "promenade and follows a cyclist along the railing, boats drifting in the harbor beyond, golden hour, "
    "continuous forward motion, smooth glide, photorealistic, film grain, no text. Audio: wheels, harbor, strings.",
    "Continuing the SAME shot: the camera rises over the harbor toward a rail bridge, tracking a commuter "
    "train crossing it, reflections on the water, sun sinking behind clouds, continuous lateral motion, "
    "anamorphic flare, film grain, no text. Audio: train rumble, horn, city ambience.",
    "Continuing the SAME shot: the camera climbs into sunset clouds above the city, volumetric light, skyline "
    "and coastline below with city lights switching on, slow fade to warm white, cinematic grade, film grain, "
    "no text. Audio: wind, piano and strings resolve, soft chime.",
]


# ---------------------------------------------------------------- helpers
def http_get(path: str, timeout: float = 10.0):
    return requests.get(BASE + path, timeout=timeout)


def http_json(path: str, timeout: float = 10.0):
    r = http_get(path, timeout)
    return r.json() if r.status_code == 200 else None


def smi_query(fields: str) -> list[str]:
    try:
        out = subprocess.run([SMI, f"--query-gpu={fields}", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=15)
        return [x.strip() for x in out.stdout.strip().splitlines() if x.strip()]
    except Exception:
        return []


def gpu_reading() -> dict:
    row = smi_query("temperature.gpu,power.draw,clocks.sm,utilization.gpu,"
                    "clocks_throttle_reasons.hw_thermal_slowdown,clocks_throttle_reasons.sw_power_cap")
    if not row:
        return {}
    f = [x.strip() for x in row[0].split(",")]
    try:
        return {"temp": float(f[0]), "power": float(f[1]), "sm": int(float(f[2])),
                "util": int(float(f[3])), "thermal": f[4], "pcap": f[5]}
    except Exception:
        return {}


def display_active() -> str:
    row = smi_query("display_active")
    return row[0] if row else "unknown"


def ram_free_gb() -> float:
    return psutil.virtual_memory().available / (1024 ** 3)


def disk_free_gb() -> float:
    return shutil.disk_usage("K:\\").free / (1024 ** 3)


def comfy_up() -> bool:
    try:
        return http_get("/system_stats", timeout=3).status_code == 200
    except Exception:
        return False


def queue_counts() -> tuple[int, int]:
    try:
        q = http_json("/queue") or {}
        return len(q.get("queue_running") or []), len(q.get("queue_pending") or [])
    except Exception:
        return -1, -1


def comfy_pid() -> int | None:
    for conn in psutil.net_connections(kind="inet"):
        if conn.laddr and conn.laddr.port == 8188 and conn.status == psutil.CONN_LISTEN:
            return conn.pid
    return None


def kill_comfy() -> None:
    pid = comfy_pid()
    if pid:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        time.sleep(6)


def start_comfy(log) -> bool:
    if comfy_up():
        return True
    args = list((config.load_settings().get("comfy") or {}).get("start_args") or [])
    if not args:
        args = ["--windows-standalone-build", "--listen", "127.0.0.1", "--port", "8188",
                "--fast", "fp16_accumulation", "--cuda-malloc", "--disable-auto-launch",
                "--vram-headroom", "1.0"]
    out = (ROOT / "logs" / "comfy_stdout.log").open("a", encoding="utf-8", errors="replace")
    err = (ROOT / "logs" / "comfy_stderr.log").open("a", encoding="utf-8", errors="replace")
    log("starting ComfyUI: " + " ".join(args))
    subprocess.Popen([str(PY), "-s", str(COMFY / "main.py"), *args], cwd=str(APP),
                     stdout=out, stderr=err,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for _ in range(90):
        if comfy_up():
            log("ComfyUI ready")
            return True
        time.sleep(2)
    return False


# ---------------------------------------------------------------- telemetry
class Telemetry(threading.Thread):
    def __init__(self, path: Path):
        super().__init__(daemon=True)
        self.path = path
        self.stop_evt = threading.Event()
        self.samples: list[dict] = []

    def run(self) -> None:
        with self.path.open("w", encoding="utf-8") as f:
            f.write("time,temp_c,power_w,sm_mhz,util_pct,thermal,powercap,ram_free_mb,commit_mb,queue_running\n")
            while not self.stop_evt.is_set():
                g = gpu_reading()
                vm = psutil.virtual_memory()
                r, _p = queue_counts()
                if g:
                    line = (f"{time.strftime('%H:%M:%S')},{g['temp']},{g['power']},{g['sm']},"
                            f"{g['util']},{g['thermal']},{g['pcap']},"
                            f"{int(vm.available / 1048576)},{int((vm.total - vm.available) / 1048576)},{r}")
                    f.write(line + "\n")
                    f.flush()
                    self.samples.append({"temp": g["temp"], "ram_mb": vm.available / 1048576})
                self.stop_evt.wait(2)

    def stop(self) -> None:
        self.stop_evt.set()
        self.join(timeout=5)


# ---------------------------------------------------------------- preflight
def preflight(args, log) -> tuple[bool, dict]:
    info: dict = {"checks": [], "ok": True}

    def check(name: str, ok: bool, detail: str, fatal: bool = True) -> None:
        info["checks"].append({"name": name, "ok": ok, "detail": detail, "fatal": fatal})
        log(f"  [{'OK' if ok else 'FAIL'}] {name}: {detail}")
        if not ok and fatal:
            info["ok"] = False

    log("preflight:")
    if not comfy_up() and args.start_comfy:
        start_comfy(log)
    check("comfy_up", comfy_up(), "ComfyUI 8188")

    r, p = queue_counts()
    check("queue_empty", r == 0 and p == 0, f"running={r} pending={p}")

    if not args.no_free and comfy_up():
        try:
            requests.post(BASE + "/free",
                          json={"unload_models": True, "free_memory": True}, timeout=20)
            log("  free() called, waiting RAM recovery ...")
        except Exception as exc:
            log(f"  free() failed: {exc}")
        for _ in range(20):
            if ram_free_gb() >= args.min_ram_gb:
                break
            time.sleep(1.5)
    check("ram", ram_free_gb() >= args.min_ram_gb,
          f"free RAM {ram_free_gb():.1f}GB >= {args.min_ram_gb}GB")

    g = gpu_reading()
    if g:
        check("gpu_temp", g["temp"] <= args.max_start_temp,
              f"{g['temp']:.1f}C <= {args.max_start_temp}C", fatal=False)
    disp = display_active()
    check("igpu_display", disp.lower() == "disabled", f"display_active={disp}", fatal=False)
    check("disk", disk_free_gb() >= 8.0, f"{disk_free_gb():.1f}GB free")
    info["ram_free_gb"] = round(ram_free_gb(), 2)
    info["gpu"] = g
    return info["ok"], info


# ---------------------------------------------------------------- generation
def wait_job(job_id: str, args, log, seg_tag: str, tel: Telemetry) -> tuple[str, dict]:
    """Returns (status, detail). status: completed | failed | aborted | stall."""
    t0 = time.time()
    last_progress = None
    last_change = time.time()
    hot_since = None
    stderr_path = ROOT / "logs" / "comfy_stderr.log"
    stderr_off = stderr_path.stat().st_size if stderr_path.exists() else 0
    err_scan_off = stderr_off

    while time.time() - t0 < args.segment_timeout_min * 60:
        # 1) abort rules
        g = gpu_reading()
        if g and g["temp"] >= args.abort_temp:
            hot_since = hot_since or time.time()
            if time.time() - hot_since >= args.abort_temp_sec:
                log(f"!! {seg_tag}: temp {g['temp']:.1f}C >= {args.abort_temp}C for {args.abort_temp_sec}s -> interrupt")
                requests.post(BASE + "/interrupt", timeout=10)
                return "aborted", {"reason": "overheat", "temp": g["temp"]}
        else:
            hot_since = None
        free_mb = psutil.virtual_memory().available / 1048576
        if free_mb < args.abort_ram_mb:
            log(f"!! {seg_tag}: RAM {free_mb:.0f}MB < {args.abort_ram_mb}MB -> interrupt")
            requests.post(BASE + "/interrupt", timeout=10)
            return "aborted", {"reason": "low_ram", "ram_mb": round(free_mb)}

        # 2) CUDA error scan (incremental)
        if stderr_path.exists():
            size = stderr_path.stat().st_size
            if size > err_scan_off:
                with stderr_path.open("rb") as f:
                    f.seek(err_scan_off)
                    chunk = f.read(size - err_scan_off).decode("utf-8", "replace")
                err_scan_off = size
                if "CUDA error" in chunk or "AcceleratorError" in chunk:
                    log(f"!! {seg_tag}: CUDA error detected in comfy log")
                    return "failed", {"reason": "cuda_error"}

        # 3) status / progress
        job = http_json(f"/api/jobs/{job_id}") or {}
        status = job.get("status") or ""
        prog = job.get("progress") or {}
        marker = (prog.get("event_count"), prog.get("last_event"))
        if marker != last_progress:
            last_progress = marker
            last_change = time.time()
        if status in ("completed", "success"):
            return "completed", job
        if status in ("failed", "error", "cancelled"):
            return "failed", job

        # 4) stall detection: no status/progress change AND log not growing
        log_size = stderr_path.stat().st_size if stderr_path.exists() else 0
        if time.time() - last_change > args.stall_min * 60 and log_size == stderr_off:
            return "stall", {"reason": "no_progress", "stderr_size": log_size}
        if log_size != stderr_off:
            stderr_off = log_size
            last_change = time.time()

        time.sleep(args.poll_s)
    return "failed", {"reason": "timeout"}


def run_segment(seg: int, args, model_first_frame, log, art: Path, tel: Telemetry) -> tuple[Path | None, dict]:
    params = {
        "prompt": (args.prompts[seg - 1] if args.prompts else PROMPTS[(seg - 1) % len(PROMPTS)]),
        "width": args.width, "height": args.height, "duration_s": args.duration_s,
        "steps": args.steps, "optimize": args.optimize, "seed": args.seed + seg,
        "output_prefix": f"video/{args.tag}_seg{seg}",
    }
    if args.two_pass:
        params["two_pass"] = True
        params["two_pass_scale"] = args.two_pass_scale
    tmpl = "minimax_h3_t2v" if not model_first_frame else "minimax_h3_fl2v"
    if model_first_frame:
        params["first_frame"] = model_first_frame

    try:
        pkg = workflow.resolve(tmpl, params)
    except Exception as exc:
        return None, {"reason": "policy", "error": str(exc)}

    t0 = time.time()
    r = requests.post(BASE + "/prompt", json={"prompt": pkg["api"], "client_id": args.tag}, timeout=60)
    if r.status_code != 200:
        return None, {"reason": "submit", "error": r.text[:300]}
    job_id = r.json()["prompt_id"]
    log(f"{args.tag} seg{seg}: submitted {job_id} ({tmpl}, {args.duration_s}s, {args.steps} steps, {args.optimize})")

    status, detail = wait_job(job_id, args, log, f"seg{seg}", tel)
    elapsed = time.time() - t0
    log(f"{args.tag} seg{seg}: {status} in {elapsed:.0f}s")
    if status != "completed":
        return None, {"reason": status, "detail": detail, "elapsed_s": round(elapsed, 1)}

    job = detail
    fname = None
    for node in (job.get("outputs") or {}).values():
        for item in node.get("images", []):
            if str(item.get("filename", "")).endswith(".mp4"):
                fname = item["filename"]
                break
        if fname:
            break
    if not fname:
        # fall back to history
        hist = http_json("/history") or {}
        entry = hist.get(job_id) or {}
        for node in (entry.get("outputs") or {}).values():
            for item in node.get("images", []):
                if str(item.get("filename", "")).endswith(".mp4"):
                    fname = item["filename"]
                    break
            if fname:
                break
    if not fname:
        return None, {"reason": "no_output"}
    dst = art / f"seg{seg}.mp4"
    shutil.copy2(OUTPUT / fname, dst)
    log(f"{args.tag} seg{seg}: saved {dst.name} ({dst.stat().st_size / 1048576:.2f}MB)")
    return dst, {"elapsed_s": round(elapsed, 1), "file": dst.name}


def last_frame(mp4: Path, png: Path) -> bool:
    r = subprocess.run([FFMPEG, "-y", "-sseof", "-0.20", "-i", str(mp4), "-frames:v", "1",
                        "-q:v", "2", str(png)], capture_output=True, text=True)
    return r.returncode == 0 and png.exists()


def upload_image(p: Path) -> str:
    with p.open("rb") as fh:
        r = requests.post(BASE + "/upload/image", files={"image": (p.name, fh, "image/png")},
                          data={"overwrite": "true"}, timeout=60)
    r.raise_for_status()
    return r.json()["name"]


def concat(art: Path, segments: list[Path], tag: str, log) -> Path | None:
    lst = art / "concat_list.txt"
    lst.write_text("\n".join(f"file '{p.as_posix()}'" for p in segments), encoding="utf-8")
    out = art / f"{tag}.mp4"
    r = subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-c", "copy", "-movflags", "+faststart", str(out)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        log("concat failed: " + (r.stderr or "")[-500:])
        return None
    return out


def summarize(tel: Telemetry, film: Path | None) -> dict:
    samples = tel.samples
    if not samples:
        return {}
    temps = [s["temp"] for s in samples]
    rams = [s["ram_mb"] for s in samples]
    return {
        "samples": len(samples),
        "temp_avg_c": round(sum(temps) / len(temps), 1),
        "temp_max_c": round(max(temps), 1),
        "ram_min_gb": round(min(rams) / 1024, 2),
        "film": film.name if film else None,
        "film_mb": round(film.stat().st_size / 1048576, 2) if film else None,
    }


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--segments", type=int, default=4)
    ap.add_argument("--duration-s", type=float, default=15)
    ap.add_argument("--steps", type=int, default=8)
    ap.add_argument("--optimize", default="max")
    ap.add_argument("--width", type=int, default=608)
    ap.add_argument("--height", type=int, default=352)
    ap.add_argument("--two-pass", action="store_true")
    ap.add_argument("--two-pass-scale", type=float, default=1.9)
    ap.add_argument("--tag", default="auto_film")
    ap.add_argument("--seed", type=int, default=2026111100)
    ap.add_argument("--prompts", nargs="*", default=None)
    ap.add_argument("--min-ram-gb", type=float, default=None)
    ap.add_argument("--max-start-temp", type=float, default=82.0)
    ap.add_argument("--abort-temp", type=float, default=97.0)
    ap.add_argument("--abort-temp-sec", type=float, default=30.0)
    ap.add_argument("--abort-ram-mb", type=float, default=800.0)
    ap.add_argument("--stall-min", type=float, default=15.0)
    ap.add_argument("--segment-timeout-min", type=float, default=90.0)
    ap.add_argument("--poll-s", type=float, default=3.0)
    ap.add_argument("--retries", type=int, default=1)
    ap.add_argument("--no-free", action="store_true")
    ap.add_argument("--no-restart", action="store_true")
    ap.add_argument("--start-comfy", action="store_true", default=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.min_ram_gb is None:
        args.min_ram_gb = 20.0 if args.duration_s >= 10 else 8.0

    art = ROOT / "artifacts" / args.tag
    art.mkdir(parents=True, exist_ok=True)
    logp = ROOT / "logs" / f"{args.tag}.log"
    tele_p = ROOT / "logs" / f"{args.tag}_telemetry.csv"
    logp.parent.mkdir(parents=True, exist_ok=True)

    def log(msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        with logp.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    log(f"auto_film tag={args.tag} segments={args.segments} {args.duration_s}s "
        f"{args.width}x{args.height} steps={args.steps} optimize={args.optimize} two_pass={args.two_pass}")

    report: dict = {"tag": args.tag, "config": vars(args), "segments": [], "status": "unknown"}

    ok, pf = preflight(args, log)
    report["preflight"] = pf
    if not ok:
        log("preflight FAILED -> abort")
        report["status"] = "preflight_failed"
        (art / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return 2
    if args.dry_run:
        log("dry-run: preflight ok, plan:")
        log(f"  {args.segments} x {args.duration_s}s, steps={args.steps}, optimize={args.optimize}")
        try:
            pkg = workflow.resolve("minimax_h3_t2v",
                                   {"prompt": "dry", "width": args.width, "height": args.height,
                                    "duration_s": args.duration_s, "steps": args.steps,
                                    "optimize": args.optimize, **({"two_pass": True} if args.two_pass else {})})
        except Exception as exc:
            log(f"  policy rejected: {exc}")
            report["status"] = "policy_rejected"
            report["error"] = str(exc)
            (art / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            return 3
        log(f"  resolved: {pkg['resolved']['length']} frames, two_pass={pkg['resolved']['two_pass']}")
        report["status"] = "dry_run"
        (art / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0

    tel = Telemetry(tele_p)
    tel.start()
    segments: list[Path] = []
    first_frame = None
    for seg in range(1, args.segments + 1):
        attempts = 0
        while True:
            out, meta = run_segment(seg, args, first_frame, log, art, tel)
            if out:
                segments.append(out)
                report["segments"].append({"seg": seg, **meta})
                break
            attempts += 1
            report["segments"].append({"seg": seg, "attempt": attempts, **meta})
            reason = meta.get("reason")
            if reason == "policy":
                log("policy rejected config -> stop")
                report["status"] = "policy_rejected"
            elif attempts > args.retries:
                log(f"seg{seg}: giving up after {attempts} attempt(s) -> stop")
                report["status"] = f"failed_{reason}"
            if report["status"] != "unknown":
                break
            log(f"seg{seg}: failure ({reason}); restarting ComfyUI and retrying")
            kill_comfy()
            if not start_comfy(log):
                report["status"] = "comfy_restart_failed"
                break
        if report["status"] != "unknown":
            break
        if seg < args.segments:
            png = art / f"seg{seg}_last.png"
            if not last_frame(segments[-1], png):
                report["status"] = "last_frame_failed"
                break
            first_frame = upload_image(png)
            log(f"seg{seg}: first_frame={first_frame}")

    tel.stop()
    film = None
    if report["status"] == "unknown":
        film = concat(art, segments, args.tag, log)
        if film is None:
            report["status"] = "concat_failed"
        else:
            probe = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration,size",
                                    "-of", "json", str(film)], capture_output=True, text=True)
            report["probe"] = probe.stdout
            report["status"] = "completed"
            log(f"film -> {film} ({film.stat().st_size / 1048576:.2f}MB)")

    report["summary"] = summarize(tel, film)
    (art / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md = art / "report.md"
    s = report.get("summary") or {}
    md.write_text(
        f"# auto_film {args.tag}\n\n"
        f"- status: **{report['status']}**\n"
        f"- config: {args.segments}x{args.duration_s}s {args.width}x{args.height} "
        f"steps={args.steps} optimize={args.optimize} two_pass={args.two_pass}\n"
        f"- film: {s.get('film')} ({s.get('film_mb')} MB)\n"
        f"- temp avg/max: {s.get('temp_avg_c')}/{s.get('temp_max_c')} C\n"
        f"- RAM min: {s.get('ram_min_gb')} GB\n"
        f"- telemetry: {tele_p.name}\n",
        encoding="utf-8")
    log(f"report -> {art / 'report.json'}")
    return 0 if report["status"] == "completed" else 3


if __name__ == "__main__":
    sys.exit(main())
