"""Threaded resumable downloader for MOSS-Image models (Comfy-Org/MOSS-Image)."""
from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

BASE = "https://hf-mirror.com/Comfy-Org/MOSS-Image/resolve/main/"
MODELS = Path(r"K:\CONFI\mian\ComfyUI_windows_portable\ComfyUI\models")
STATE = Path(r"K:\CONFI\comfy_mcp\logs\moss_dl_state.json")
CHUNK = 16 * 1024 * 1024
WORKERS = 6

FILES = [
    ("diffusion_models/moss_image_sft_int8_convrot.safetensors", "diffusion_models"),
    ("text_encoders/qwen3.5_9b_w4a8_gptq.safetensors", "text_encoders"),
    ("vae/flux2-vae_small.safetensors", "vae"),
]


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


EXPECT = {
    "moss_image_sft_int8_convrot.safetensors": 5413698958,
    "qwen3.5_9b_w4a8_gptq.safetensors": 7343591088,
    "flux2-vae_small.safetensors": 249521340,
}


_S = requests.Session()
_S.trust_env = False


def remote_size(url: str, name: str = "") -> int:
    for attempt in range(4):
        try:
            r = _S.head(url, allow_redirects=True, timeout=30)
            r.raise_for_status()
            return int(r.headers.get("content-length") or 0)
        except Exception:
            if attempt == 3 and name in EXPECT:
                return EXPECT[name]
            time.sleep(2 + attempt * 2)
    return EXPECT.get(name, 0)


def download(rel: str, subdir: str, st: dict) -> Path:
    dest = MODELS / subdir / Path(rel).name
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = BASE + rel
    size = remote_size(url, dest.name)
    chunks_all = list(range(0, size, CHUNK))
    prior = set(st.get(rel, {}).get("chunks", []))
    if dest.exists() and dest.stat().st_size == size and len(prior) == len(chunks_all):
        print(f"[skip] {dest.name} {size/2**30:.2f}GB")
        return dest
    print(f"[get ] {dest.name} {size/2**30:.2f}GB -> {dest}", flush=True)
    if not dest.exists():
        with dest.open("wb") as f:
            f.truncate(size)
    done = set(st.get(rel, {}).get("chunks", []))
    chunks = list(range(0, size, CHUNK))
    todo = [c for c in chunks if c not in done]
    lock = threading.Lock()
    prog = {"bytes": sum(min(CHUNK, size - c) for c in done)}
    t0 = time.time()

    def fetch(off: int) -> None:
        end = min(off + CHUNK, size) - 1
        for attempt in range(12):
            try:
                u = url if attempt < 8 else url.replace("huggingface.co","hf-mirror.com")
                r = _S.get(u, headers={"Range": f"bytes={off}-{end}"}, timeout=240, stream=True)
                if r.status_code not in (200, 206):
                    raise RuntimeError(f"HTTP {r.status_code}")
                buf = bytearray()
                for data in r.iter_content(1024 * 256):
                    buf.extend(data)
                if len(buf) != end - off + 1:
                    raise RuntimeError(f"short {len(buf)}")
                with lock:
                    with dest.open("r+b") as f:
                        f.seek(off)
                        f.write(buf)
                    done.add(off)
                    prog["bytes"] += len(buf)
                    st.setdefault(rel, {})["chunks"] = sorted(done)
                    if len(done) % 8 == 0:
                        save_state(st)
                    dt = max(1e-6, time.time() - t0)
                    print(f"  {dest.name}: {prog['bytes']/2**30:.2f}/{size/2**30:.2f}GB "
                          f"({prog['bytes']/2**20/dt:.1f}MB/s)", flush=True)
                return
            except Exception as exc:  # noqa: BLE001
                print(f"  chunk {off} attempt {attempt+1}: {exc}", flush=True)
                time.sleep(2 + attempt * 2)
        raise RuntimeError(f"chunk {off} failed")

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        list(ex.map(fetch, todo))
    save_state(st)
    if dest.stat().st_size != size:
        raise RuntimeError("size mismatch")
    print(f"[ok  ] {dest.name} in {(time.time()-t0)/60:.1f} min", flush=True)
    return dest


def main() -> int:
    st = load_state()
    for rel, sub in FILES:
        download(rel, sub, st)
    print("DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
