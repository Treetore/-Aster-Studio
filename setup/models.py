"""Selective model catalog/download. Usage: python -m setup.models --list|--download id1,id2"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CATALOG = Path(__file__).with_name("models.json")
SELECTED = ROOT / "data" / "selected_models.json"


def load() -> list[dict]:
    return json.loads(CATALOG.read_text(encoding="utf-8")).get("models") or []


def comfy_root() -> Path:
    env = os.environ.get("COMFYUI_ROOT")
    return Path(env) if env else ROOT.parent / "mian" / "ComfyUI_windows_portable" / "ComfyUI"


def selected() -> list[str]:
    try:
        return json.loads(SELECTED.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_selected(ids: list[str]) -> None:
    SELECTED.parent.mkdir(parents=True, exist_ok=True)
    SELECTED.write_text(json.dumps(sorted(set(ids)), ensure_ascii=False, indent=2), encoding="utf-8")


def installed(m: dict) -> bool:
    return (comfy_root() / "models" / m.get("target", "") / m.get("file", "")).is_file()


def status() -> list[dict]:
    return [{**m, "installed": installed(m)} for m in load()]


def _resolve_hf_url(m: dict) -> str:
    """当 url 为空时，尝试在 HuggingFace 公共模型中查找同名文件。"""
    filename = m.get("file") or ""
    if not filename:
        return ""
    try:
        r = requests.get("https://huggingface.co/api/models",
                         params={"search": filename.rsplit(".", 1)[0], "limit": 8}, timeout=20)
        r.raise_for_status()
        for item in r.json() or []:
            repo_id = item.get("id")
            if not repo_id:
                continue
            info = requests.get(f"https://huggingface.co/api/models/{repo_id}", timeout=20)
            if info.status_code != 200:
                continue
            for sib in info.json().get("siblings") or []:
                if sib.get("rfilename") == filename:
                    return f"https://huggingface.co/{repo_id}/resolve/main/{filename}"
    except Exception:
        return ""
    return ""


def download(ids: list[str], force: bool = False) -> dict:
    out = {"ok": True, "downloaded": [], "errors": []}
    for m in load():
        if m["id"] not in ids:
            continue
        dest = comfy_root() / "models" / m["target"] / m["file"]
        if dest.is_file() and not force:
            out["downloaded"].append({"id": m["id"], "skipped": True})
            continue
        url = (m.get("url") or "").strip() or _resolve_hf_url(m)
        if not url:
            out["errors"].append({"id": m["id"], "error": "未配置 url，请编辑 setup/models.json 或手动放入目录"})
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            headers = {}
            mode = "wb"
            if tmp.is_file() and tmp.stat().st_size > 0:
                headers["Range"] = f"bytes={tmp.stat().st_size}-"
                mode = "ab"
            with requests.get(url, headers=headers, stream=True, timeout=60) as r:
                if r.status_code == 416:
                    tmp.unlink(missing_ok=True)
                    raise RuntimeError("resume range invalid; removed partial file")
                r.raise_for_status()
                with tmp.open(mode) as f:
                    for chunk in r.iter_content(1024 * 1024):
                        if chunk:
                            f.write(chunk)
            expected = (m.get("sha256") or "").strip().lower()
            if expected:
                h = hashlib.sha256()
                with tmp.open("rb") as f:
                    for chunk in iter(lambda: f.read(1024 * 1024), b""):
                        h.update(chunk)
                if h.hexdigest() != expected:
                    tmp.unlink(missing_ok=True)
                    raise RuntimeError("sha256 mismatch")
            tmp.replace(dest)
            out["downloaded"].append({"id": m["id"], "path": str(dest)})
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            out["errors"].append({"id": m["id"], "error": str(exc)})
    if out["errors"]:
        out["ok"] = False
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--download", default="")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if args.list:
        print(json.dumps(status(), ensure_ascii=False, indent=2))
    if args.download:
        ids = [x.strip() for x in args.download.split(",") if x.strip()]
        save_selected(ids)
        print(json.dumps(download(ids, force=args.force), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
