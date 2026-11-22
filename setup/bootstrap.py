"""把 ComfyUI 源码拉取到项目同级布局（可移植，无写死盘符）。

默认目标目录按以下顺序确定：
    1. ``--dest`` 显式参数
    2. 环境变量 ``COMFYUI_ROOT`` / ``COMFY_MCP_COMFY_ROOT``（可指向便携版根目录）
    3. 项目同级布局：``<parent>/mian/ComfyUI_windows_portable/ComfyUI``
       以及 ``<parent>/ComfyUI_windows_portable/ComfyUI``、``<root>/ComfyUI`` 等

用法::

    python setup/bootstrap.py                       # 拉取到同级布局（默认分支 master）
    python setup/bootstrap.py --ref v0.3.40         # 指定分支/标签
    python setup/bootstrap.py --dest D:\\ComfyUI     # 指定目录
    python setup/bootstrap.py --print-dest          # 只打印解析到的目标目录
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:  # 复用 config 的候选布局逻辑（仅标准库依赖）
    from config import COMFY_SUBDIR, PORTABLE_DIRNAME, comfy_root_candidates
except Exception:  # pragma: no cover - config 不可用时退回等价常量
    PORTABLE_DIRNAME = "ComfyUI_windows_portable"
    COMFY_SUBDIR = "ComfyUI"

    def comfy_root_candidates(root: Path | None = None) -> list[Path]:
        r = Path(root) if root is not None else ROOT
        return [
            r.parent / "mian" / PORTABLE_DIRNAME / COMFY_SUBDIR,
            r.parent / PORTABLE_DIRNAME / COMFY_SUBDIR,
            r / "mian" / PORTABLE_DIRNAME / COMFY_SUBDIR,
            r / PORTABLE_DIRNAME / COMFY_SUBDIR,
            r.parent / COMFY_SUBDIR,
            r / COMFY_SUBDIR,
        ]


DEFAULT_REF = os.environ.get("COMFYUI_CLONE_REF") or "master"
REPO_URL = os.environ.get("COMFYUI_REPO_URL") or "https://github.com/comfyanonymous/ComfyUI.git"


def default_dest() -> Path:
    """解析默认安装目录：env > 已存在的同级布局 > 首选同级布局。"""
    raw = os.environ.get("COMFYUI_ROOT") or os.environ.get("COMFY_MCP_COMFY_ROOT")
    if raw:
        p = Path(os.path.expandvars(os.path.expanduser(raw.strip())))
        if (p / "main.py").is_file():
            return p
        if (p / COMFY_SUBDIR).is_dir() or (p / COMFY_SUBDIR / "main.py").is_file():
            return p / COMFY_SUBDIR
        return p
    candidates = comfy_root_candidates()
    for cand in candidates:
        if (cand / "main.py").is_file():
            return cand
    return candidates[0]


def _pip_hint(dest: Path) -> str:
    """给出安装 ComfyUI 依赖的建议命令（有便携版 Python 时优先用它）。"""
    req = dest / "requirements.txt"
    py = dest.parent / "python_embeded" / ("python.exe" if os.name == "nt" else "python")
    launcher = f'"{py}"' if py.is_file() else "python"
    return f"{launcher} -m pip install -r \"{req}\""


def download(dest: Path, ref: str = DEFAULT_REF) -> dict:
    """克隆/下载 ComfyUI 到 ``dest``。已存在 main.py 时直接跳过。"""
    dest = Path(dest).expanduser().resolve()
    if (dest / "main.py").is_file():
        return {"ok": True, "path": str(dest), "ref": ref, "skipped": True}
    dest.parent.mkdir(parents=True, exist_ok=True)
    git = shutil.which("git")
    if git:
        r = subprocess.run(
            [git, "clone", "--depth", "1", "--branch", ref, REPO_URL, str(dest)],
            capture_output=True,
            text=True,
        )
        if r.returncode == 0:
            return {"ok": True, "path": str(dest), "ref": ref, "method": "git",
                    "next": _pip_hint(dest)}
        git_err = (r.stderr or "").strip().splitlines()[-1:] or [""]
    else:
        git_err = ["git not found"]

    try:
        import requests  # 懒加载：只跑 --print-dest / 检查时不需要 requests
    except Exception as exc:  # pragma: no cover
        return {"ok": False, "path": str(dest), "ref": ref,
                "error": f"{exc}; git 也失败: {'; '.join(git_err)}"}

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
        ok = (dest / "main.py").is_file()
        out = {"ok": ok, "path": str(dest), "ref": ref, "method": "zip"}
        if ok:
            out["next"] = _pip_hint(dest)
        return out
    except Exception as exc:
        return {"ok": False, "path": str(dest), "ref": ref, "error": str(exc)}
    finally:
        tmp.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="拉取 ComfyUI 源码到项目同级布局")
    ap.add_argument("--dest", default=None,
                    help="目标目录（含 main.py 的 ComfyUI 目录）；默认按 env/同级布局解析")
    ap.add_argument("--ref", default=DEFAULT_REF, help=f"git 分支/标签（默认 {DEFAULT_REF}）")
    ap.add_argument("--print-dest", action="store_true", help="只打印解析到的目标目录")
    args = ap.parse_args(argv)

    dest = Path(args.dest) if args.dest else default_dest()
    if args.print_dest:
        print(json.dumps({"dest": str(dest), "ref": args.ref, "exists": (dest / "main.py").is_file()},
                         ensure_ascii=False, indent=2))
        return 0
    result = download(dest, args.ref)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
