"""Submit one generation from a comfy_mcp template and wait for the output.

Usage:
  python tools/gen_image.py --template qwen_image --prompt "a red apple" --width 1024 --height 1024 --steps 25
  python tools/gen_image.py --template qwen_image --params '{"prompt":"...","width":768,"height":768,"steps":8}'
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(r"K:\CONFI\comfy_mcp")
BASE = "http://127.0.0.1:8188"
sys.path.insert(0, str(ROOT))
import workflow  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", default="qwen_image")
    ap.add_argument("--params", default=None, help="JSON params; overrides individual flags")
    ap.add_argument("--params-file", default=None, help="path to JSON params file (UTF-8)")
    ap.add_argument("--prompt", default="a red apple on a wooden table, soft light")
    ap.add_argument("--width", type=int, default=1024)
    ap.add_argument("--height", type=int, default=1024)
    ap.add_argument("--steps", type=int, default=25)
    ap.add_argument("--seed", type=int, default=2026111501)
    ap.add_argument("--timeout-min", type=float, default=60)
    args = ap.parse_args()

    if args.params_file:
        params = json.loads(Path(args.params_file).read_text(encoding="utf-8"))
    elif args.params:
        params = json.loads(args.params)
    else:
        params = {
            "prompt": args.prompt, "width": args.width, "height": args.height,
            "steps": args.steps, "seed": args.seed,
        }
    pkg = workflow.resolve(args.template, params)
    print("resolved:", json.dumps(pkg["resolved"], ensure_ascii=False)[:500], flush=True)
    if pkg.get("warnings"):
        print("warnings:", pkg["warnings"], flush=True)
    r = requests.post(BASE + "/prompt", json={"prompt": pkg["api"], "client_id": "gen_image"}, timeout=60)
    r.raise_for_status()
    pid = r.json()["prompt_id"]
    print("prompt_id:", pid, flush=True)
    t0 = time.time()
    while time.time() - t0 < args.timeout_min * 60:
        h = requests.get(f"{BASE}/history/{pid}", timeout=20).json()
        entry = h.get(pid)
        if entry:
            st = (entry.get("status") or {})
            if st.get("completed") or st.get("status_str") == "success":
                outs = []
                for node in (entry.get("outputs") or {}).values():
                    for it in node.get("images", []):
                        outs.append((it.get("subfolder") or "", it.get("filename")))
                print("completed in %.0fs" % (time.time() - t0))
                for sub, fn in outs:
                    print("output:", (Path(r"K:\CONFI\mian\ComfyUI_windows_portable\ComfyUI\output") / sub / fn))
                return 0
            if st.get("status_str") in ("error", "failed"):
                print("FAILED:", json.dumps(st, ensure_ascii=False)[:800])
                return 3
        time.sleep(3)
    print("timeout")
    return 4


if __name__ == "__main__":
    sys.exit(main())
