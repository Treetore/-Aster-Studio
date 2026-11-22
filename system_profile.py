"""Host capability detection used by setup, launcher and generation policy."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

import psutil

MODE_FILE = Path(__file__).resolve().parent / "data" / "host_mode.json"


def get_mode_override() -> str | None:
    try:
        import json
        mode = json.loads(MODE_FILE.read_text(encoding="utf-8")).get("mode")
        return mode if mode in ("low_vram", "normal", "high") else None
    except Exception:
        return None


def set_mode_override(mode: str) -> str:
    import json
    mode = mode if mode in ("low_vram", "normal", "high") else "auto"
    MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
    MODE_FILE.write_text(json.dumps({"mode": mode}, ensure_ascii=False, indent=2), encoding="utf-8")
    return mode


def _gpu() -> dict:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return {}
    try:
        out = subprocess.check_output(
            [exe, "--query-gpu=name,memory.total,memory.free,driver_version",
             "--format=csv,noheader,nounits"],
            text=True, timeout=8, creationflags=0x08000000,
        ).strip().splitlines()
        if not out:
            return {}
        name, total, free, driver = [x.strip() for x in out[0].split(",")]
        return {"name": name, "vram_total_mb": int(float(total)),
                "vram_free_mb": int(float(free)), "driver": driver}
    except Exception:
        return {}


def detect() -> dict:
    vm = psutil.virtual_memory()
    gpu = _gpu()
    vram = int(gpu.get("vram_total_mb") or 0)
    ram = round(vm.total / 1024**3, 1)
    low_vram = (vram and vram < 9000) or ram < 24
    detected = "low_vram" if low_vram else "normal"
    if vram and vram >= 16000 and ram >= 48:
        detected = "high"
    override = get_mode_override()
    mode = override or detected
    return {
        "os": platform.system(),
        "os_version": platform.version(),
        "python": platform.python_version(),
        "cpu": platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER", ""),
        "cpu_count": psutil.cpu_count(logical=True),
        "ram_gb": ram,
        "ram_free_gb": round(vm.available / 1024**3, 1),
        "gpu": gpu,
        "vram_total_mb": vram,
        "mode": mode,
        "detected_mode": detected,
        "mode_override": override,
        "limits": {
            "direct_720p_max_s": 10 if mode == "low_vram" else 12,
            "segment_threshold_s": 12,
            "max_width_720p": 1280,
            "max_height_720p": 736,
            "recommended_optimize": "max" if mode == "low_vram" else "sage",
            "recommended_steps": 8,
        },
    }


if __name__ == "__main__":
    import json
    print(json.dumps(detect(), ensure_ascii=False, indent=2))
