"""Sequential resumable downloader (single stream per file) for MOSS-Image."""
from __future__ import annotations

import shutil
import time
from pathlib import Path

import requests

BASE = "https://huggingface.co/Comfy-Org/MOSS-Image/resolve/main/"
MIRROR = "https://hf-mirror.com/Comfy-Org/MOSS-Image/resolve/main/"
MODELS = Path(r"K:\CONFI\mian\ComfyUI_windows_portable\ComfyUI\models")
FILES = [
    ("diffusion_models/moss_image_sft_int8_convrot.safetensors", "diffusion_models", 5413698958),
    ("text_encoders/qwen3.5_9b_w4a8_gptq.safetensors", "text_encoders", 7343591088),
    ("vae/flux2-vae_small.safetensors", "vae", 249521340),
]


def fetch(rel: str, sub: str, size: int) -> Path:
    dest = MODELS / sub / Path(rel).name
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    if dest.exists() and dest.stat().st_size == size:
        print(f"[skip] {dest.name}", flush=True)
        return dest
    if dest.exists():
        dest.unlink()
    attempts = 0
    while True:
        have = part.stat().st_size if part.exists() else 0
        if have >= size:
            break
        url = (BASE if attempts % 4 < 3 else MIRROR) + rel
        headers = {"Range": f"bytes={have}-", "Connection": "close"}
        try:
            with requests.get(url, headers=headers, stream=True, timeout=(30, 180)) as r:
                if r.status_code not in (200, 206):
                    raise RuntimeError(f"HTTP {r.status_code}")
                mode = "ab" if have and r.status_code == 206 else "wb"
                if mode == "wb":
                    have = 0
                with part.open(mode) as f:
                    last = time.time()
                    for chunk in r.iter_content(1024 * 512):
                        if not chunk:
                            continue
                        f.write(chunk)
                        have += len(chunk)
                        if time.time() - last > 8:
                            last = time.time()
                            print(f"  {dest.name}: {have/2**30:.2f}/{size/2**30:.2f}GB", flush=True)
            attempts = 0
        except Exception as exc:  # noqa: BLE001
            attempts += 1
            print(f"  retry {dest.name} at {have/2**30:.2f}GB ({attempts}): {exc}", flush=True)
            time.sleep(2 + min(10, attempts))
    if part.stat().st_size != size:
        raise RuntimeError(f"{dest.name} size {part.stat().st_size} != {size}")
    part.replace(dest)
    print(f"[ok  ] {dest.name} {size/2**30:.2f}GB", flush=True)
    return dest


def main() -> int:
    for rel, sub, size in FILES:
        fetch(rel, sub, size)
    print("DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
