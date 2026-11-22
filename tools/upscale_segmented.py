"""分段超分：把长视频切成 N 段，每段用独立进程超分（进程退出释放内存/CUDA context），最后拼接。

这是 2026-11-10 GPU lost 事故的软件层修复之一：单进程 60s/1426 帧超分会把
commit 撑到 6.5GB+ 且持续 38 分钟；分段后每段 15s 进程退出，内存/驱动压力周期性归零。

用法:
  python tools/upscale_segmented.py --src film.mp4 --dst film_4x.mp4 \
      --model RealESRGAN_x4plus.pth --seg 15
"""
from __future__ import annotations

import argparse
import ctypes
import math
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
UPSCALE_PY = ROOT / "tools" / "upscale_video.py"
FFMPEG = r"C:\ffmpeg\ffmpeg-master-latest-win64-gpl-shared\bin\ffmpeg.exe"
FFPROBE = r"C:\ffmpeg\ffmpeg-master-latest-win64-gpl-shared\bin\ffprobe.exe"
LOG = ROOT / "logs" / "upscale_segmented.log"


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def free_commit_gb() -> float:
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


def wait_commit(min_gb: float, timeout_s: int = 600) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        free = free_commit_gb()
        if free >= min_gb:
            return True
        log(f"commit free {free:.1f}GB < {min_gb:.1f}GB, wait 15s ...")
        time.sleep(15)
    return False


def probe_duration(path: str) -> float:
    out = subprocess.check_output(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path],
        text=True, timeout=30,
    ).strip()
    return float(out)


def run(cmd: list[str], timeout: int = 7200) -> bool:
    log("run: " + " ".join(str(c) for c in cmd[-4:]))
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    r = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env)
    if r.returncode != 0:
        log("FAILED: " + r.stderr.decode("utf-8", "replace")[-300:])
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--dst", required=True)
    ap.add_argument("--model", default="RealESRGAN_x4plus.pth")
    ap.add_argument("--seg", type=float, default=15.0, help="每段秒数")
    ap.add_argument("--min-commit-gb", type=float, default=10.0, help="每段开始前要求的最小空闲 commit")
    ap.add_argument("--keep-parts", action="store_true")
    args = ap.parse_args()

    src = Path(args.src).resolve()
    dst = Path(args.dst).resolve()
    work = ROOT / "artifacts" / "_upscale_parts" / f"{src.stem}_{time.strftime('%H%M%S')}"
    work.mkdir(parents=True, exist_ok=True)
    dur = probe_duration(str(src))
    n = max(1, math.ceil(dur / args.seg))
    log(f"==== segmented upscale start: {src.name} dur={dur:.2f}s seg={args.seg}s parts={n} ====")
    log(f"commit free at start: {free_commit_gb():.1f} GB")

    parts: list[str] = []
    for i in range(n):
        ss = i * args.seg
        length = min(args.seg, max(0.1, dur - ss))
        if not wait_commit(args.min_commit_gb):
            log(f"part {i}: commit 不足，放弃")
            return 2
        cut = work / f"part{i:02d}.mp4"
        if not run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{ss:.3f}", "-t", f"{length:.3f}",
                    "-i", str(src), "-c", "copy", "-avoid_negative_ts", "make_zero", str(cut)]):
            return 1
        out = work / f"part{i:02d}_4x.mp4"
        log(f"part {i+1}/{n}: {ss:.1f}s..{ss+length:.1f}s upscale (commit free {free_commit_gb():.1f}GB)")
        ok = run([PY, str(UPSCALE_PY), "--src", str(cut), "--dst", str(out), "--model", args.model,
                  "--pause-every", "180", "--pause-s", "20", "--max-temp", "85",
                  "--min-commit-gb", str(args.min_commit_gb)], timeout=3600)
        if not ok or not out.is_file():
            log(f"part {i}: 超分失败")
            return 1
        parts.append(str(out))
        log(f"part {i+1} done: {out.name} {out.stat().st_size/1048576:.1f}MB; commit free {free_commit_gb():.1f}GB")

    listfile = work / "concat.txt"
    listfile.write_text("".join(f"file '{p}'\n" for p in parts), encoding="utf-8")
    merged = work / "merged_video.mp4"
    if not run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                "-i", str(listfile), "-c", "copy", str(merged)]):
        return 1
    # 用原视频音轨覆盖（分段 copy 裁切的音轨可能有微小缝隙）
    if not run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(merged), "-i", str(src),
                "-map", "0:v:0", "-map", "1:a:0?", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                "-shortest", "-movflags", "+faststart", str(dst)]):
        return 1
    log(f"==== segmented upscale done: {dst} {dst.stat().st_size/1048576:.1f}MB ====")
    if not args.keep_parts:
        for p in work.glob("*"):
            try:
                p.unlink()
            except Exception:
                pass
        try:
            work.rmdir()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
