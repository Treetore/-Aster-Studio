"""H3 720p benchmark harness: builds a t2v prompt with optional context windows,
LoRA and tiled VAE decode, submits it, samples GPU telemetry and reports.

Usage:
  python tools/h3_bench.py --width 1280 --height 736 --frames 362 --steps 8 --tag t1
  ... --lora minimax_h3\\minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors --context 124 --overlap 39
  ... --tiled
"""
from __future__ import annotations

import argparse
import json
import statistics
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
COMFY_OUT = Path(r"K:\CONFI\mian\ComfyUI_windows_portable\ComfyUI\output")
LOGS = ROOT / "logs"


def gpu_sample() -> dict:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used,temperature.gpu,utilization.gpu,power.draw",
             "--format=csv,noheader,nounits"], text=True, timeout=5,
            creationflags=0x08000000).strip()
        used, temp, util, power = [x.strip() for x in out.split(",")]
        return {"vram_mb": int(float(used)), "temp": float(temp), "util": int(float(util)),
                "power": float(power)}
    except Exception:
        return {}


class Sampler(threading.Thread):
    def __init__(self, interval=3.0):
        super().__init__(daemon=True)
        self.interval = interval
        self.stop = threading.Event()
        self.rows: list[dict] = []

    def run(self):
        while not self.stop.is_set():
            s = gpu_sample()
            if s:
                s["t"] = time.time()
                self.rows.append(s)
            self.stop.wait(self.interval)

    def summary(self) -> dict:
        if not self.rows:
            return {}
        vram = [r["vram_mb"] for r in self.rows]
        temp = [r["temp"] for r in self.rows]
        return {"vram_peak": max(vram), "vram_avg": round(statistics.mean(vram)),
                "temp_max": max(temp), "temp_avg": round(statistics.mean(temp), 1),
                "power_max": max(r["power"] for r in self.rows),
                "samples": len(self.rows)}


def build(width: int, height: int, frames: int, steps: int, prompt: str, seed: int,
          lora: str | None, lora_strength: float, context: int, overlap: int,
          tiled: bool, tile_size: int, tile_overlap: int, temporal: int, temporal_overlap: int,
          optimize: str, sampler: str, scheduler: str) -> dict:
    pkg = workflow.resolve("minimax_h3_t2v", {
        "prompt": prompt, "width": width, "height": height, "length": frames,
        "steps": steps, "seed": seed, "optimize": optimize,
        "sampler": sampler, "scheduler": scheduler,
        "lora_name": lora, "lora_strength": lora_strength,
    })
    api = pkg["api"]
    model_src = "90" if lora else "1"
    if context:
        api["250"] = {
            "class_type": "MiniMaxH3ContextWindows",
            "inputs": {"model": [model_src, 0], "context_length": context,
                       "context_overlap": overlap, "fuse_method": "pyramid",
                       "freenoise": True, "split_conds_to_windows": False},
            "_meta": {"title": "MiniMaxH3ContextWindows"},
        }
        model_src = "250"
    for nid in ("7", "8"):
        if api.get(nid, {}).get("inputs", {}).get("model"):
            api[nid]["inputs"]["model"] = [model_src, 0]
    if tiled:
        api["11"] = {
            "class_type": "VAEDecodeTiled",
            "inputs": {"samples": ["9", 0], "vae": ["3", 0], "tile_size": tile_size,
                       "overlap": tile_overlap, "temporal_size": temporal,
                       "temporal_overlap": temporal_overlap},
            "_meta": {"title": "VAEDecodeTiled"},
        }
    return {"api": api, "resolved": pkg["resolved"], "warnings": pkg.get("warnings")}


def submit(api: dict) -> str:
    r = requests.post(BASE + "/prompt", json={"prompt": api, "client_id": "h3bench"}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"submit failed {r.status_code}: {r.text[:500]}")
    return r.json()["prompt_id"]


def wait(pid: str, timeout_s: int = 5400) -> dict:
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=736)
    ap.add_argument("--frames", type=int, default=362)
    ap.add_argument("--steps", type=int, default=8)
    ap.add_argument("--prompt", default="cinematic wide shot, a red apple on a rustic wooden table by a window, soft morning light, slow camera push in")
    ap.add_argument("--seed", type=int, default=2026111801)
    ap.add_argument("--lora", default=None)
    ap.add_argument("--lora-strength", type=float, default=1.0)
    ap.add_argument("--context", type=int, default=124)
    ap.add_argument("--overlap", type=int, default=39)
    ap.add_argument("--tiled", action="store_true")
    ap.add_argument("--tile-size", type=int, default=512)
    ap.add_argument("--tile-overlap", type=int, default=64)
    ap.add_argument("--temporal", type=int, default=64)
    ap.add_argument("--temporal-overlap", type=int, default=8)
    ap.add_argument("--optimize", default="max")
    ap.add_argument("--sampler", default="res_multistep")
    ap.add_argument("--scheduler", default="simple")
    ap.add_argument("--tag", default="bench")
    args = ap.parse_args()

    built = build(args.width, args.height, args.frames, args.steps, args.prompt, args.seed,
                  args.lora, args.lora_strength, args.context, args.overlap, args.tiled,
                  args.tile_size, args.tile_overlap, args.temporal, args.temporal_overlap,
                  args.optimize, args.sampler, args.scheduler)
    LOGS.mkdir(exist_ok=True)
    cfg_path = LOGS / f"h3bench_{args.tag}.json"
    cfg_path.write_text(json.dumps({"args": vars(args), "resolved": built["resolved"]},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
    print("resolved:", json.dumps(built["resolved"], ensure_ascii=False)[:400], flush=True)
    if built.get("warnings"):
        print("warnings:", built["warnings"], flush=True)
    sampler = Sampler()
    sampler.start()
    t0 = time.time()
    try:
        pid = submit(built["api"])
        print("prompt_id:", pid, flush=True)
        res = wait(pid)
    finally:
        sampler.stop.set()
    sampler.join(timeout=2)
    summary = sampler.summary()
    out = {"tag": args.tag, "args": vars(args), "result": res["status"],
           "seconds": res["seconds"], "gpu": summary}
    if res["history"]:
        outs = []
        for node in (res["history"].get("outputs") or {}).values():
            for it in node.get("gifs", []) + node.get("videos", []) + node.get("images", []):
                p = COMFY_OUT / (it.get("subfolder") or "") / it.get("filename")
                if p.is_file():
                    outs.append(str(p))
        out["outputs"] = outs
        err = (res["history"].get("status") or {}).get("messages")
        if res["status"] != "completed" and err:
            out["messages"] = str(err)[:800]
    print(json.dumps(out, ensure_ascii=False, indent=2), flush=True)
    (LOGS / f"h3bench_{args.tag}_result.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if res["status"] == "completed" else 3


if __name__ == "__main__":
    raise SystemExit(main())
