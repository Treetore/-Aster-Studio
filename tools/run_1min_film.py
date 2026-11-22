"""1 分钟电影风格分镜：8×8s 生成 → 拼接 60s → 4x 超分。

用法（后台运行，可中断重跑自动续）：
  python tools/run_1min_film.py                 # 全流程
  python tools/run_1min_film.py --from 4        # 从第 4 段开始续跑
  python tools/run_1min_film.py --post-only     # 只做拼接 + 超分
  python tools/run_1min_film.py --no-upscale    # 只生成 + 拼接
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = Path(r"K:\CONFI\mian\ComfyUI_windows_portable")
PY = APP / "python_embeded" / "python.exe"
COMFY = APP / "ComfyUI"
OUTPUT = COMFY / "output" / "video"
ART = ROOT / "artifacts"
STATE = ROOT / "data" / "film_state.json"
LOG = ROOT / "logs" / "film_1min.log"
COMFY_LOG = ROOT / "logs" / "comfy_stderr.log"
FFMPEG = r"C:\ffmpeg\ffmpeg-master-latest-win64-gpl-shared\bin\ffmpeg.exe"
BASE = "http://127.0.0.1:8188"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))
import workflow  # noqa: E402

SEG_S, COUNT, W, H, STEPS = 8, 8, 608, 352, 4
LORA = r"minimax_h3\minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors"
SEEDS = [2026111100 + i for i in range(COUNT)]
PREFIX = "video/film_seg"
COMMON = ("photorealistic cinematic film, 35mm anamorphic lens, shallow depth of field, "
          "natural light, film grain, cinematic color grade, no anime, no cartoon, "
          "no text, no watermark, no UI. ")
SHOTS = [
    "An empty coastal road at dawn, sea fog drifting over the water, low camera slowly pushing forward along the asphalt, distant gulls, muted blue-grey palette. Audio: waves, wind, faint gull cries.",
    "A rain-soaked city street in early morning, wet asphalt mirroring shop signs, a few people with umbrellas walking, slow lateral dolly along the sidewalk, overcast soft light. Audio: light rain, footsteps, distant traffic.",
    "A quiet cafe window seat, warm morning light through wooden blinds, a cup of coffee steaming, a person's hands turning a book, slow push-in, shallow focus. Audio: soft clatter, low murmur, rain outside.",
    "An autumn park path, leaves drifting down, an empty wooden bench, camera slowly pulling back and rising, golden afternoon light through branches. Audio: wind in leaves, birds.",
    "A rooftop at dusk overlooking a dense city skyline, silhouettes of a person standing at the railing, slow upward crane shot, orange to blue gradient sky, lens flare. Audio: city hum, wind.",
    "A neon-lit street at night after rain, reflections of signs on wet pavement, camera follows a person's back walking away, handheld feel, shallow depth of field. Audio: rain, passing cars, distant music.",
    "A dim bedroom at night, a single warm desk lamp, a person reading in silhouette, slow dolly-in, dust in the lamplight, very quiet mood. Audio: turning pages, a wall clock ticking.",
    "A wide beach at first light, a lone figure walking toward the sea, camera slowly pulling back to a vast empty frame, pale sky and long shadows. Audio: gentle waves, morning wind.",
]


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def free_commit_gb() -> float:
    """当前可用 commit（虚拟内存）GB，用于防止 2026-11-10 式内存耗尽。"""
    import ctypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    m = MEMORYSTATUSEX()
    m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return round(m.ullAvailPageFile / 1024 ** 3, 1)


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {"segments": {}, "updated": None}


def save_state(state: dict) -> None:
    state["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(STATE) + ".tmp"
    Path(tmp).write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, STATE)


def http_get(path: str, timeout: int = 10) -> dict | None:
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def http_post(path: str, obj: dict, timeout: int = 30) -> dict | None:
    data = json.dumps(obj).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as exc:
        log(f"POST {path} failed: {type(exc).__name__}: {exc}")
        return None


def wait_api(timeout_s: int = 240) -> bool:
    for _ in range(timeout_s // 2):
        if http_get("/system_stats", timeout=3):
            return True
        time.sleep(2)
    return False


def comfy_pids() -> list[int]:
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
          "Where-Object { $_.CommandLine -like '*main.py*' } | Select-Object -ExpandProperty ProcessId")
    try:
        out = subprocess.check_output(["powershell", "-NoProfile", "-Command", ps], timeout=30).decode("utf-8", "replace")
        return [int(x) for x in out.split() if x.strip().isdigit()]
    except Exception:
        return []


def kill_comfy() -> None:
    for pid in comfy_pids():
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=30)
        log(f"killed ComfyUI pid {pid}")
    time.sleep(8)


def start_comfy() -> bool:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    out = COMFY_LOG.open("a", encoding="utf-8", errors="replace")
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    subprocess.Popen(
        [str(PY), "-s", str(COMFY / "main.py"), "--windows-standalone-build", "--listen", "127.0.0.1",
         "--port", "8188", "--fast", "--cuda-malloc", "--disable-auto-launch"],
        cwd=str(APP), env=env, stdout=out, stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    log("ComfyUI starting ...")
    return wait_api()


def submit(api: dict) -> str | None:
    r = http_post("/prompt", {"prompt": api, "client_id": str(uuid.uuid4())})
    return (r or {}).get("prompt_id")


def wait_job(pid: str, timeout_s: int = 1800) -> tuple[str, dict]:
    t0 = time.time()
    last = ""
    while time.time() - t0 < timeout_s:
        j = http_get(f"/api/jobs/{pid}", timeout=10)
        st = (j or {}).get("status") or "unknown"
        if st != last:
            log(f"job {pid[:8]} status -> {st}")
            last = st
        if st in ("completed", "failed", "cancelled"):
            return st, (j or {})
        time.sleep(5)
    return "timeout", {}


def find_output(seg: int) -> Path | None:
    files = sorted(OUTPUT.glob(f"film_seg{seg}_*.mp4"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def run_segment(seg: int, retry: bool = True) -> bool:
    prompt = COMMON + SHOTS[seg - 1]
    seed = SEEDS[seg - 1]
    params = {"prompt": prompt, "width": W, "height": H, "duration_s": SEG_S, "steps": STEPS,
              "seed": seed, "lora_name": LORA, "optimize": "off",
              "output_prefix": f"{PREFIX}{seg}"}
    pkg = workflow.resolve("minimax_h3_t2v", params)
    api = pkg["api"]
    t0 = time.time()
    kill_comfy()
    if not start_comfy():
        log(f"seg {seg}: ComfyUI 未就绪，失败")
        return False
    pid = submit(api)
    if not pid:
        log(f"seg {seg}: 提交失败")
        return False
    st, job = wait_job(pid)
    elapsed = round(time.time() - t0, 1)
    out = find_output(seg) if st == "completed" else None
    state = load_state()
    state["segments"][str(seg)] = {
        "status": st, "elapsed_s": elapsed, "job": pid, "seed": seed,
        "output": str(out) if out else None, "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    save_state(state)
    log(f"seg {seg}: {st} elapsed={elapsed}s out={out}")
    if st == "completed" and out:
        return True
    if retry:
        log(f"seg {seg}: 重试一次")
        return run_segment(seg, retry=False)
    return False


def ffmpeg(args: list[str]) -> bool:
    log("ffmpeg " + " ".join(args[-6:]))
    r = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args], capture_output=True, timeout=3600)
    if r.returncode != 0:
        log("ffmpeg failed: " + r.stderr.decode("utf-8", "replace")[-400:])
    return r.returncode == 0


def post_process(no_upscale: bool) -> None:
    state = load_state()
    # 2026-11-10 事故修复：超分/拼接前先停止 ComfyUI，释放其常驻 20GB+ 内存/commit。
    # 当时 ComfyUI 占 27.4GB commit + 超分 6.5GB + ffmpeg 1.6GB，19:38:56 触发虚拟内存耗尽，
    # 随后 GPU 驱动 WATCHDOG 挂起、系统冻结重启。见 docs/GPU_LOST_INCIDENT_20261110.md
    kill_comfy()
    log(f"commit free before post: {free_commit_gb():.1f} GB")
    segs = []
    for i in range(1, COUNT + 1):
        info = state["segments"].get(str(i)) or {}
        out = info.get("output")
        if not out or not Path(out).is_file():
            log(f"post: 缺少段 {i}，跳过")
            return
        segs.append(out)
    listfile = ART / "film_concat.txt"
    listfile.write_text("".join(f"file '{p}'\n" for p in segs), encoding="utf-8")
    raw64 = ART / "film_cinematic_64s.mp4"
    ok = ffmpeg(["-f", "concat", "-safe", "0", "-i", str(listfile), "-c", "copy", "-movflags", "+faststart", str(raw64)])
    state["concat"] = {"ok": ok, "file": str(raw64) if ok else None, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    save_state(state)
    if not ok:
        return
    final = ART / "film_cinematic_1min.mp4"
    ok2 = ffmpeg(["-i", str(raw64), "-t", "60", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                  "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(final)])
    state["final"] = {"ok": ok2, "file": str(final) if ok2 else None, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    save_state(state)
    log(f"final 60s: {'ok' if ok2 else 'FAILED'} {final}")
    if not ok2 or no_upscale:
        return
    up = ART / "film_cinematic_1min_4x.mp4"
    if free_commit_gb() < 8.0:
        log(f"skip upscale: commit free {free_commit_gb():.1f} GB < 8 GB（内存不足，防再次掉卡）")
        return
    cmd = [str(PY), str(ROOT / "tools" / "upscale_segmented.py"), "--src", str(final), "--dst", str(up),
           "--model", "RealESRGAN_x4plus.pth", "--seg", "15"]
    log("upscale start (segmented 15s/part, RealESRGAN_x4plus)")
    r = subprocess.run(cmd, capture_output=True, timeout=7200,
                       env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"))
    ok3 = r.returncode == 0 and up.is_file()
    state["upscale"] = {"ok": ok3, "file": str(up) if ok3 else None,
                        "tail": r.stdout.decode("utf-8", "replace")[-500:], "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    save_state(state)
    log(f"upscale: {'ok' if ok3 else 'FAILED'} {up}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", type=int, default=1)
    ap.add_argument("--post-only", action="store_true")
    ap.add_argument("--no-upscale", action="store_true")
    args = ap.parse_args()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    log(f"==== film run start from={args.start} post_only={args.post_only} ====")
    if not args.post_only:
        for seg in range(args.start, COUNT + 1):
            ok = run_segment(seg)
            if not ok:
                log(f"seg {seg} 最终失败，终止（可稍后 --from {seg} 续跑）")
                return 1
    post_process(args.no_upscale)
    log("==== film run done ====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
