"""Parallel resumable downloader for Qwen-Image 2.1 models (huggingface.co direct).

Usage:
  python tools/download_qwen21.py --test            # VAE only, measure speed
  python tools/download_qwen21.py                   # full set (diffusion int8 + TE + VAE)
  python tools/download_qwen21.py --turbo           # also turbo model + 5-step LoRA
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import sys
import threading
import time
from pathlib import Path

import requests

BOARD = Path(r"K:\CONFI\mian\ComfyUI_windows_portable\ComfyUI\models")
BASE = "https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/main/"
CHUNK = 32 * 1024 * 1024
WORKERS = 6
STATE = Path(r"K:\CONFI\comfy_mcp\logs\qwen21_dl_state.json")

FILES = {
    "vae": ("vae/qwen_image_2.1_vae_bf16.safetensors", "vae"),
    "diffusion": ("diffusion_models/qwen_image_2.1_int8_convrot.safetensors", "diffusion_models"),
    "te": ("text_encoders/qwen3vl_8b_int8_convrot.safetensors", "text_encoders"),
    "turbo": ("diffusion_models/qwen_image_2.1_turbo_int8_convrot.safetensors", "diffusion_models"),
    "lora5": ("loras/p_q_image_2.1_5_steps_v0.1_comfyui_bf16.safetensors", "loras"),
}


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, STATE)


def remote_size(url: str) -> int:
    r = requests.head(url, allow_redirects=True, timeout=30)
    r.raise_for_status()
    return int(r.headers.get("content-length") or 0)


def download(key: str, rel: str, subdir: str, st: dict) -> Path:
    dest = BOARD / subdir / Path(rel).name
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = BASE + rel
    size = remote_size(url)
    if size <= 0:
        raise RuntimeError(f"no content-length for {url}")
    done = dest.exists() and dest.stat().st_size == size
    if done:
        print(f"[skip] {dest.name} already {size/2**30:.2f}GB")
        return dest
    print(f"[get ] {dest.name} {size/2**30:.2f}GB -> {dest}")
    if not dest.exists():
        with dest.open("wb") as f:
            f.truncate(size)
    done_chunks = set(st.get(key, {}).get("chunks", []))
    chunks = list(range(0, size, CHUNK))
    todo = [c for c in chunks if c not in done_chunks]
    lock = threading.Lock()
    t0 = time.time()
    prog = {"bytes": sum(min(CHUNK, size - c) for c in done_chunks)}

    def fetch(off: int) -> None:
        end = min(off + CHUNK, size) - 1
        headers = {"Range": f"bytes={off}-{end}"}
        for attempt in range(6):
            try:
                r = requests.get(url, headers=headers, timeout=120, stream=True)
                if r.status_code not in (200, 206):
                    raise RuntimeError(f"HTTP {r.status_code}")
                buf = bytearray()
                for data in r.iter_content(1024 * 256):
                    buf.extend(data)
                if len(buf) != end - off + 1:
                    raise RuntimeError(f"short read {len(buf)} != {end-off+1}")
                with lock:
                    with dest.open("r+b") as f:
                        f.seek(off)
                        f.write(buf)
                    done_chunks.add(off)
                    prog["bytes"] += len(buf)
                    st.setdefault(key, {})["chunks"] = sorted(done_chunks)
                    if len(done_chunks) % 8 == 0:
                        save_state(st)
                    dt = max(1e-6, time.time() - t0)
                    print(f"  {dest.name}: {prog['bytes']/2**30:.2f}/{size/2**30:.2f}GB "
                          f"({prog['bytes']/2**20/dt:.1f}MB/s)", flush=True)
                return
            except Exception as exc:
                print(f"  chunk {off} attempt {attempt+1} failed: {exc}", flush=True)
                time.sleep(2 + attempt * 2)
        raise RuntimeError(f"chunk {off} failed permanently")

    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        list(ex.map(fetch, todo))
    save_state(st)
    if dest.stat().st_size != size:
        raise RuntimeError(f"final size mismatch {dest.stat().st_size} != {size}")
    print(f"[ok  ] {dest.name} in {(time.time()-t0)/60:.1f} min")
    return dest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="download VAE only")
    ap.add_argument("--turbo", action="store_true", help="also turbo model + 5-step LoRA")
    args = ap.parse_args()
    st = load_state()
    keys = ["vae"] if args.test else (["vae", "diffusion", "te"] + (["turbo", "lora5"] if args.turbo else []))
    for k in keys:
        rel, sub = FILES[k]
        download(k, rel, sub, st)
    save_state(st)
    print("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
