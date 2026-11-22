"""Generate Monet-style launcher backgrounds with Qwen-Image 2.1 (local ComfyUI)."""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

import requests

ROOT = Path(r"K:\CONFI\comfy_mcp")
OUT = ROOT / "launcher" / "assets"
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))
import workflow  # noqa: E402

BASE = "http://127.0.0.1:8188"
COMFY_OUT = Path(r"K:\CONFI\mian\ComfyUI_windows_portable\ComfyUI\output")

JOBS = [
    ("bg_monet_lilies.png",
     "Claude Monet impressionist oil painting, water lilies pond at dawn, soft pastel blues greens and lavender, "
     "loose visible brushstrokes, dreamy light, no text, no watermark"),
    ("bg_monet_garden.png",
     "Claude Monet impressionist garden path with blooming pink and white flowers, dappled sunlight through leaves, "
     "soft pastel palette, thick oil brushstrokes, no text"),
    ("bg_monet_sunrise.png",
     "Claude Monet Impression Sunrise style harbor at dawn, misty pastel orange lavender and teal sky, "
     "reflections on water, loose brushwork, no text"),
]


def main() -> int:
    for name, prompt in JOBS:
        pkg = workflow.resolve("qwen_image", {"prompt": prompt, "width": 1280, "height": 720,
                                              "steps": 25, "seed": 2026111701})
        r = requests.post(BASE + "/prompt", json={"prompt": pkg["api"], "client_id": "bgmonet"}, timeout=60)
        r.raise_for_status()
        pid = r.json()["prompt_id"]
        print("submitted", name, pid, flush=True)
        t0 = time.time()
        while time.time() - t0 < 420:
            h = requests.get(f"{BASE}/history/{pid}", timeout=20).json().get(pid)
            if h:
                st = h.get("status") or {}
                if st.get("completed") or st.get("status_str") == "success":
                    imgs = []
                    for node in (h.get("outputs") or {}).values():
                        for it in node.get("images", []):
                            imgs.append(COMFY_OUT / (it.get("subfolder") or "") / it.get("filename"))
                    if imgs:
                        shutil.copy2(imgs[0], OUT / name)
                        print("saved", OUT / name, "in", round(time.time() - t0), "s", flush=True)
                    break
                if st.get("status_str") in ("error", "failed"):
                    print("FAILED", name, str(st)[:300], flush=True)
                    break
            time.sleep(3)
        else:
            print("timeout", name, flush=True)
    print("DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
