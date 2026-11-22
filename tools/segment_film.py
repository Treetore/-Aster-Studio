"""自动长片管线：分段生成 / 整段直出，自动首帧接力、拼接、进度状态。

供 Web「长片」页与启动器调用：
  python tools/segment_film.py --prompt "..." --refs a.png b.png --segments 3 --mode seg --state data/segment_film_state.json
  python tools/segment_film.py --prompt "..." --mode single --state ...
"""
from __future__ import annotations

import argparse
import atexit
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
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
DATA = ROOT / "data"
FF = r"C:\ffmpeg\ffmpeg-master-latest-win64-gpl-shared\bin\ffmpeg.exe"
TURBO4 = r"minimax_h3\minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors"


def now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def write_state(path: Path, st: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    st["updated_at"] = now()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def run_ff(args: list[str], timeout: int = 3600) -> subprocess.CompletedProcess:
    return subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", *args],
                          capture_output=True, text=True, timeout=timeout)


def submit(api: dict) -> str:
    r = requests.post(BASE + "/prompt", json={"prompt": api, "client_id": "segment-film"}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"submit failed {r.status_code}: {r.text[:500]}")
    return r.json()["prompt_id"]


def history_video(pid: str) -> Path | None:
    h = requests.get(f"{BASE}/history/{pid}", timeout=20).json().get(pid) or {}
    for node in (h.get("outputs") or {}).values():
        for it in node.get("images", []):
            name = it.get("filename") or ""
            if name.endswith(".mp4"):
                p = OUT / (it.get("subfolder") or "") / name
                if p.is_file():
                    return p
    return None


def wait_job(pid: str, cancel: Path, state: Path, st: dict, timeout_s: int = 5400) -> tuple[str, float]:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if cancel.is_file():
            try:
                requests.post(BASE + "/interrupt", json={}, timeout=10)
                requests.post(BASE + "/queue", json={"clear": True}, timeout=10)
            except Exception:
                pass
            return "cancelled", time.time() - t0
        h = requests.get(f"{BASE}/history/{pid}", timeout=20).json().get(pid)
        if h:
            stt = h.get("status") or {}
            if stt.get("completed") or stt.get("status_str") == "success":
                return "completed", time.time() - t0
            if stt.get("status_str") in ("error", "failed"):
                return "failed", time.time() - t0
        st["elapsed_s"] = round(time.time() - t0, 1)
        write_state(state, st)
        time.sleep(4)
    return "timeout", time.time() - t0


def last_frame(video: Path, dst: Path) -> bool:
    run_ff(["-sseof", "-0.08", "-i", str(video), "-frames:v", "1", "-update", "1", str(dst)])
    if dst.is_file():
        return True
    run_ff(["-i", str(video), "-vf", "reverse", "-frames:v", "1", str(dst)])
    return dst.is_file()


def concat(videos: list[Path], dst: Path) -> bool:
    if len(videos) == 1:
        shutil.copy2(videos[0], dst)
        return True
    inputs: list[str] = []
    for v in videos:
        inputs += ["-i", str(v)]
    n = len(videos)
    filt = "".join(f"[{i}:v][{i}:a]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v][a]"
    run_ff(inputs + ["-filter_complex", filt, "-map", "[v]", "-map", "[a]",
                     "-c:v", "libx264", "-crf", "16", "-preset", "medium",
                     "-c:a", "aac", "-b:a", "192k", str(dst)])
    if dst.is_file() and dst.stat().st_size > 0:
        return True
    filt_v = "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0[v]"
    run_ff(inputs + ["-filter_complex", filt_v, "-map", "[v]",
                     "-c:v", "libx264", "-crf", "16", "-preset", "medium", str(dst)])
    return dst.is_file() and dst.stat().st_size > 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--refs", nargs="*", default=[])
    ap.add_argument("--segments", type=int, default=3)
    ap.add_argument("--frames", type=int, default=124)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=736)
    ap.add_argument("--steps", type=int, default=4)
    ap.add_argument("--lora", default=TURBO4)
    ap.add_argument("--lora-strength", type=float, default=1.0)
    ap.add_argument("--optimize", default="sage")
    ap.add_argument("--mode", choices=["seg", "single"], default="seg")
    ap.add_argument("--seed", type=int, default=2026112001)
    ap.add_argument("--tag", default="")
    ap.add_argument("--state", default=str(DATA / "segment_film_state.json"))
    ap.add_argument("--cancel-flag", default=str(DATA / "segment_film_cancel.flag"))
    args = ap.parse_args()

    lock = job_lock.acquire("film", f"segments={args.segments}")
    if not lock.get("ok"):
        state = {"ok": False, "status": "busy", "message": lock.get("message"),
                 "segments_total": args.segments, "segments_done": 0}
        write_state(Path(args.state), state)
        print(lock.get("message"), flush=True)
        return 2
    atexit.register(job_lock.release)

    LOGS.mkdir(exist_ok=True)
    INP.mkdir(exist_ok=True)
    tag = args.tag or datetime.now().strftime("film_%Y%m%d_%H%M%S")
    state_path = Path(args.state)
    cancel = Path(args.cancel_flag)
    if cancel.is_file():
        cancel.unlink()
    seg_dir = LOGS / f"segment_film_{tag}"
    seg_dir.mkdir(exist_ok=True)
    st: dict = {
        "ok": True, "status": "running", "mode": args.mode, "tag": tag,
        "prompt": args.prompt, "refs": args.refs, "started_at": now(),
        "segments_total": args.segments if args.mode == "seg" else 1,
        "segments_done": 0, "current_segment": 1, "elapsed_s": 0,
        "message": "准备中", "output": None, "segments": [],
        "params": {"width": args.width, "height": args.height, "frames": args.frames,
                   "steps": args.steps, "lora": args.lora, "optimize": args.optimize},
    }
    write_state(state_path, st)
    videos: list[Path] = []
    last_frame_name: str | None = None

    if args.mode == "single":
        params = {"prompt": args.prompt, "width": args.width, "height": args.height,
                  "length": args.frames * max(1, args.segments), "steps": args.steps,
                  "seed": args.seed, "optimize": args.optimize,
                  "lora_name": args.lora, "lora_strength": args.lora_strength}
        st["message"] = "整段直出（ContextWindows）"
        write_state(state_path, st)
        try:
            if len(args.refs) >= 2:
                params["ref_images"] = args.refs
                pkg = workflow.resolve("minimax_h3_r2v", params)
            elif len(args.refs) == 1:
                params["first_frame"] = args.refs[0]
                pkg = workflow.resolve("minimax_h3_fl2v", params)
            else:
                pkg = workflow.resolve("minimax_h3_t2v_720p15s", {**params, "context_length": 124})
        except Exception as exc:  # noqa: BLE001
            st.update({"status": "failed", "ok": False, "message": f"参数错误：{exc}"})
            write_state(state_path, st)
            return 3
        pid = submit(pkg["api"])
        st["message"] = "直出采样中"
        status, secs = wait_job(pid, cancel, state_path, st)
        video = history_video(pid) if status == "completed" else None
        if video:
            dst = OUT / "video" / f"{tag}_single.mp4"
            shutil.copy2(video, dst)
            st["segments"].append({"seg": 1, "status": "completed", "seconds": round(secs, 1), "video": str(dst)})
            st["output"] = str(dst)
            st["status"] = "done"
            st["segments_done"] = 1
        else:
            st.update({"status": status, "ok": False, "message": f"直出失败：{status}"})
        write_state(state_path, st)
        return 0 if st["status"] == "done" else 3

    for seg in range(args.segments):
        st["current_segment"] = seg + 1
        st["status"] = "running"
        st["message"] = f"分段 {seg + 1}/{args.segments} 生成中"
        write_state(state_path, st)
        seg_mp4 = seg_dir / f"seg{seg + 1:02d}.mp4"
        if seg_mp4.is_file() and seg_mp4.stat().st_size > 0:
            videos.append(seg_mp4)
            st["segments_done"] = seg + 1
            st["segments"].append({"seg": seg + 1, "status": "cached", "video": str(seg_mp4)})
            last_frame_name = f"sf_{tag}_seg{seg + 1:02d}_last.png"
            write_state(state_path, st)
            continue
        params = {"prompt": args.prompt, "width": args.width, "height": args.height,
                  "length": args.frames, "steps": args.steps, "seed": args.seed + seg * 7,
                  "optimize": args.optimize, "lora_name": args.lora,
                  "lora_strength": args.lora_strength}
        try:
            if seg == 0 and len(args.refs) >= 2:
                params["ref_images"] = args.refs
                tmpl = "minimax_h3_r2v"
            elif seg == 0 and len(args.refs) == 1:
                params["first_frame"] = args.refs[0]
                tmpl = "minimax_h3_fl2v"
            elif last_frame_name:
                params["first_frame"] = last_frame_name
                tmpl = "minimax_h3_fl2v"
            else:
                tmpl = "minimax_h3_t2v"
            pkg = workflow.resolve(tmpl, params)
        except Exception as exc:  # noqa: BLE001
            st.update({"status": "failed", "ok": False, "message": f"参数错误：{exc}"})
            write_state(state_path, st)
            return 3
        t0 = time.time()
        pid = submit(pkg["api"])
        status, secs = wait_job(pid, cancel, state_path, st)
        if status != "completed":
            st.update({"status": status, "ok": False,
                       "message": f"分段 {seg + 1} {status}"})
            write_state(state_path, st)
            return 3
        video = history_video(pid)
        if not video:
            st.update({"status": "failed", "ok": False, "message": "无视频输出"})
            write_state(state_path, st)
            return 3
        shutil.copy2(video, seg_mp4)
        videos.append(seg_mp4)
        nf = f"sf_{tag}_seg{seg + 1:02d}_last.png"
        ok_frame = last_frame(seg_mp4, INP / nf)
        last_frame_name = nf if ok_frame else None
        st["segments_done"] = seg + 1
        st["segments"].append({"seg": seg + 1, "status": "completed",
                               "seconds": round(secs, 1), "video": str(seg_mp4),
                               "first_frame_next": last_frame_name})
        st["message"] = f"分段 {seg + 1}/{args.segments} 完成"
        write_state(state_path, st)

    st["status"] = "concat"
    st["message"] = "拼接成片"
    write_state(state_path, st)
    dst = OUT / "video" / f"{tag}.mp4"
    ok = concat(videos, dst)
    if ok:
        st.update({"status": "done", "message": "完成", "output": str(dst), "ok": True})
    else:
        st.update({"status": "failed", "ok": False, "message": "拼接失败"})
    write_state(state_path, st)
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
