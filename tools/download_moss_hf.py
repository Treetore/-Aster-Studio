import json, os, shutil, time
from pathlib import Path
from huggingface_hub import hf_hub_download
MODELS=Path(r"K:\CONFI\mian\ComfyUI_windows_portable\ComfyUI\models")
BAD=Path(r"K:\CONFI\comfy_mcp\logs\_moss_bad"); BAD.mkdir(exist_ok=True)
files=[("diffusion_models/moss_image_sft_int8_convrot.safetensors","diffusion_models"),
       ("text_encoders/qwen3.5_9b_w4a8_gptq.safetensors","text_encoders"),
       ("vae/flux2-vae_small.safetensors","vae")]
for rel,sub in files:
    dst=MODELS/sub/Path(rel).name
    if dst.exists():
        size=dst.stat().st_size
        print(f"move existing {dst.name} ({size/2**30:.2f}GB) -> bad", flush=True)
        shutil.move(str(dst), str(BAD/f"{dst.name}.{int(time.time())}"))
    print(f"[hf] {rel}", flush=True)
    p=hf_hub_download(repo_id="Comfy-Org/MOSS-Image", filename=rel, local_dir=str(MODELS))
    print("  ->", p, Path(p).stat().st_size, flush=True)
print("DONE")
