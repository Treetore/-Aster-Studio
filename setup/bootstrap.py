"""Download latest ComfyUI into the expected layout."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent


def download(dest: Path, ref: str = "master") -> dict:
    dest = dest.resolve()
    if (dest / "main.py").is_file():
        return {"ok": True, "path": str(dest), "skipped": True}
    dest.parent.mkdir(parents=True, exist_ok=True)
    git = shutil.which("git")
    if git:
        r = subprocess.run([git, "clone", "--depth", "1", "--branch", ref,
                            "https://github.com/comfyanonymous/ComfyUI.git", str(dest)],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return {"ok": True, "path": str(dest), "method": "git"}
    url = f"https://github.com/comfyanonymous/ComfyUI/archive/refs/heads/{ref}.zip"
    tmp = dest.with_suffix(".zip")
    try:
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with tmp.open("wb") as f:
                for chunk in r.iter_content(1024 * 1024):
                    if chunk:
                        f.write(chunk)
        with zipfile.ZipFile(tmp) as z:
            z.extractall(dest.parent)
        extracted = dest.parent / f"ComfyUI-{ref}"
        if extracted.is_dir() and not dest.exists():
            extracted.rename(dest)
        return {"ok": (dest / "main.py").is_file(), "path": str(dest), "method": "zip"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        tmp.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default=str(ROOT.parent / "mian" / "ComfyUI_windows_portable" / "ComfyUI"))
    ap.add_argument("--ref", default="master")
    args = ap.parse_args()
    import json
    print(json.dumps(download(Path(args.dest), args.ref), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
