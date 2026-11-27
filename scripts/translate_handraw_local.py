"""Generate data/handraw_zh.json with a local Qwen model (no API key)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from transformers import AutoModelForCausalLM, AutoTokenizer
import torch
import os
torch.set_num_threads(max(8, (os.cpu_count() or 8)//2))

ROOT = Path(r"K:\CONFI\comfy_mcp")
MODEL = ROOT / "data" / "qwen-translate"
STYLES = ROOT / "optional" / "skills" / "handraw-style" / "skills" / "handdraw-style-prompter" / "references" / "styles.json"
OUT = ROOT / "data" / "handraw_zh.json"

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
styles = json.loads(STYLES.read_text(encoding="utf-8"))
tok = AutoTokenizer.from_pretrained(MODEL)
model = AutoModelForCausalLM.from_pretrained(MODEL, torch_dtype=torch.float32)
cache = {}
if OUT.is_file():
    try:
        cache = json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:
        cache = {}
pending = [{"id": s["number"], "en": s["generation_name"]} for s in styles if s["number"] not in cache]
print("pending", len(pending), flush=True)

SYSTEM = "You are a precise English-to-Chinese translator. Output only valid JSON."
for start in range(0, len(pending), 15):
    batch = pending[start:start + 20]
    prompt = (
        "Translate these AI image style names into natural, concise Simplified Chinese. "
        "No English words, no explanation, keep the id.\n"
        'Examples: [{"id":"X1","en":"Minimal Ink Cartoon"}] -> [{"id":"X1","zh":"极简墨水漫画"}]\n'
        'Examples: [{"id":"X2","en":"Warm Watercolor Storybook"}] -> [{"id":"X2","zh":"温暖水彩绘本"}]\n'
        + json.dumps(batch, ensure_ascii=False)
        + "\nOutput only a JSON array."
    )
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
    text = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
    ids = tok(text, return_tensors="pt")
    out = model.generate(**ids, max_new_tokens=240, do_sample=False)
    raw = tok.decode(out[0][ids.input_ids.shape[1]:], skip_special_tokens=True)
    rows = []
    m = re.search(r"\[.*\]", raw, re.S)
    if m:
        try:
            rows = json.loads(m.group(0))
        except Exception:
            rows = []
    if not rows:
        rows = [{"id": a, "zh": b} for a, b in re.findall(
            r'\{[^{}]*?"id"\s*:\s*"([^"]+)"[^{}]*?"zh"\s*:\s*"([^"]+)"[^{}]*?\}', raw, re.S)]
    for row in rows:
        if isinstance(row, dict) and row.get("id") and row.get("zh"):
            cache[str(row["id"])] = str(row["zh"]).strip()[:80]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{start + len(batch)}/{len(pending)} cache={len(cache)}", flush=True)
print("done", len(cache))
