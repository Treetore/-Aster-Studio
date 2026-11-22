"""MiniMax H3 720p 15s 成片：3×5s 分段 + 首帧接力 + ffmpeg 拼接。

每段 124 帧（17*7+5）1280x736、4 步 turbo LoRA、Sage 优化；段间用上一段最后一帧作为首帧。
用法：
  python tools/h3_720p15s.py --segments 3 --tag film1
  python tools/h3_720p15s.py --segments 1 --tag seg1   # 只跑第一段验证质量
"""
from __future__ import annotations

import argparse
import atexit
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(r"K:\CONFI\comfy_mcp")
sys.path.insert(0, str(ROOT))
import job_lock  # noqa: E402
import workflow  # noqa: E402

BASE = "http://127.0.0.1:8188"
COMFY = Path(r"K:\CONFI\mian\ComfyUI_windows_portable\ComfyUI")
OUT = COMFY / "output"
INP = COMFY / "input"
LOGS = ROOT / "logs"
FF = shutil.which("ffmpeg") or r"C:\ffmpeg\ffmpeg-master-latest-win64-gpl-shared\bin\ffmpeg.exe"
TURBO4 = r"minimax_h3\minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors"


def run_ff(args: list[str], timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", *args],
                          capture_output=True, text=True, timeout=timeout)


def submit(api: dict) -> str:
    r = requests.post(BASE + "/prompt", json={"prompt": api, "client_id": "h3-720p15s"}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"submit failed {r.status_code}: {r.text[:600]}")
    return r.json()["prompt_id"]


def wait(pid: str, timeout_s: int = 1800) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        h = requests.get(f"{BASE}/history/{pid}", timeout=20).json().get(pid)
        if h:
            st = h.get("status") or {}
            if st.get("completed") or st.get("status_str") == "success":
                return {"status": "completed", "seconds": round(time.time() - t0, 1), "history": h}
            if st.get("status_str") in ("error", "failed"):
                return {"status": "failed", "seconds": round(time.time() - t0, 1), "history": h}
        time.sleep(5)
    return {"status": "timeout", "seconds": round(time.time() - t0, 1), "history": None}


def output_video(history: dict) -> Path | None:
    for node in (history.get("outputs") or {}).values():
        for it in node.get("images", []):
            name = it.get("filename") or ""
            if name.endswith(".mp4"):
                p = OUT / (it.get("subfolder") or "") / name
                if p.is_file():
                    return p
    return None


def extract_last_frame(video: Path, dst: Path) -> bool:
    # 取最后一帧（-sseof 保证定位到结尾）
    r = run_ff(["-sseof", "-0.08", "-i", str(video), "-frames:v", "1", "-update", "1", str(dst)])
    if dst.is_file():
        return True
    r2 = run_ff(["-i", str(video), "-vf", "reverse", "-frames:v", "1", str(dst)])
    return dst.is_file()


def make_prompt(tag: str, segment: int, frames: int, width: int, height: int, steps: int,
                seed: int, first_frame: str | None, prompt: str) -> dict:
    params = {
        "prompt": prompt, "width": width, "height": height, "length": frames,
        "steps": steps, "seed": seed + segment * 7, "optimize": "sage",
        "sampler": "res_multistep", "scheduler": "simple",
        "lora_name": TURBO4, "lora_strength": 1.0,
    }
    if first_frame:
        pkg = workflow.resolve("minimax_h3_fl2v", {**params, "first_frame": first_frame})
    else:
        pkg = workflow.resolve("minimax_h3_t2v", params)
    return pkg


def concat(videos: list[Path], dst: Path) -> bool:
    if len(videos) == 1:
        shutil.copy2(videos[0], dst)
        return True
    # 用 concat filter 重新编码，避免时间戳/音轨不连续
    inputs: list[str] = []
    for v in videos:
        inputs += ["-i", str(v)]
    n = len(videos)
    filt = "".join(f"[{i}:v][{i}:a]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v][a]"
    r = run_ff(inputs + ["-filter_complex", filt, "-map", "[v]", "-map", "[a]",
                         "-c:v", "libx264", "-crf", "16", "-preset", "medium",
                         "-c:a", "aac", "-b:a", "192k", str(dst)])
    if dst.is_file() and dst.stat().st_size > 0:
        return True
    # 无音轨回退
    filt_v = "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0[v]"
    r2 = run_ff(inputs + ["-filter_complex", filt_v, "-map", "[v]",
                          "-c:v", "libx264", "-crf", "16", "-preset", "medium", str(dst)])
    return dst.is_file() and dst.stat().st_size > 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--segments", type=int, default=3)
    ap.add_argument("--frames", type=int, default=124)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=736)
    ap.add_argument("--steps", type=int, default=4)
    ap.add_argument("--seed", type=int, default=2026111821)
    ap.add_argument("--prompt", default="cinematic wide shot, a red apple on a rustic wooden table by a window, "
                                        "soft morning light, slow camera push in, steady continuous motion")
    ap.add_argument("--tag", default="film")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    lock = job_lock.acquire("h3_720p15s", f"segments={args.segments}")
    if not lock.get("ok"):
        print(lock.get("message"), flush=True)
        return 2
    atexit.register(job_lock.release)

    LOGS.mkdir(exist_ok=True)
    INP.mkdir(exist_ok=True)
    seg_dir = LOGS / f"h3_720p15s_{args.tag}"
    seg_dir.mkdir(exist_ok=True)
    report: dict = {"tag": args.tag, "args": vars(args), "segments": []}
    seg_videos: list[Path] = []
    last_frame: str | None = None

    for seg in range(args.segments):
        seg_mp4 = seg_dir / f"seg{seg + 1:02d}.mp4"
        if seg_mp4.is_file() and seg_mp4.stat().st_size > 0:
            print(f"[seg {seg+1}] cached {seg_mp4}", flush=True)
            seg_videos.append(seg_mp4)
            last_frame = f"h3_{args.tag}_seg{seg + 1:02d}_last.png"
            continue
        pkg = make_prompt(args.tag, seg, args.frames, args.width, args.height, args.steps,
                          args.seed, last_frame, args.prompt)
        print(f"[seg {seg+1}] resolve:", json.dumps(pkg["resolved"], ensure_ascii=False)[:220], flush=True)
        t0 = time.time()
        pid = submit(pkg["api"])
        res = wait(pid)
        print(f"[seg {seg+1}] {res['status']} in {res['seconds']}s", flush=True)
        if res["status"] != "completed":
            report["segments"].append({"seg": seg + 1, "status": res["status"],
                                       "seconds": res["seconds"]})
            break
        video = output_video(res["history"] or {})
        if not video:
            print("[seg] no mp4 output", flush=True)
            break
        shutil.copy2(video, seg_mp4)
        seg_videos.append(seg_mp4)
        # 提取最后一帧给下一段
        next_name = f"h3_{args.tag}_seg{seg + 1:02d}_last.png"
        ok = extract_last_frame(seg_mp4, INP / next_name)
        if not ok:
            print("[seg] last-frame extraction failed", flush=True)
        last_frame = next_name if ok else None
        report["segments"].append({"seg": seg + 1, "status": "completed", "seconds": res["seconds"],
                                   "video": str(seg_mp4), "first_frame_next": last_frame})

    if len(seg_videos) == args.segments:
        dst = Path(args.out) if args.out else (OUT / "video" / f"MiniMax_H3_720p15s_{args.tag}.mp4")
        dst.parent.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        ok = concat(seg_videos, dst)
        report["concat"] = {"ok": ok, "seconds": round(time.time() - t0, 1), "output": str(dst)}
        print("[concat]", report["concat"], flush=True)
        if ok:
            probe = run_ff(["-i", str(dst)])
            report["probe"] = (probe.stderr or "")[:1500]
    report["ok"] = len(seg_videos) == args.segments and report.get("concat", {}).get("ok", False)
    (LOGS / f"h3_720p15s_{args.tag}_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2)[-2000:], flush=True)
    return 0 if report["ok"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
