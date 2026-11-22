"""Self-check with two message styles: easy (plain language) and pro (technical)."""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = Path(__file__).with_name("models.json")
STYLE_FILE = ROOT / "data" / "user_mode.json"
_cache: dict = {"at": 0.0, "raw": None}


def get_style() -> str:
    try:
        style = json.loads(STYLE_FILE.read_text(encoding="utf-8")).get("error_style")
        return style if style in ("easy", "pro") else "easy"
    except Exception:
        return "easy"


def set_style(style: str) -> str:
    style = style if style in ("easy", "pro") else "easy"
    STYLE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STYLE_FILE.write_text(json.dumps({"error_style": style}, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return style


def first_run() -> bool:
    return not STYLE_FILE.is_file()


def comfy_root() -> Path:
    env = os.environ.get("COMFYUI_ROOT")
    if env:
        return Path(env)
    for candidate in (ROOT.parent / "mian" / "ComfyUI_windows_portable" / "ComfyUI",
                      ROOT / "ComfyUI"):
        if (candidate / "main.py").is_file():
            return candidate
    return ROOT.parent / "mian" / "ComfyUI_windows_portable" / "ComfyUI"


def catalog() -> list[dict]:
    try:
        return json.loads(CATALOG.read_text(encoding="utf-8")).get("models") or []
    except Exception:
        return []


def selected_models() -> list[str]:
    try:
        return json.loads((ROOT / "data" / "selected_models.json").read_text(encoding="utf-8"))
    except Exception:
        return []


def _issue(code: str, path: str, pro: tuple[str, str], easy: tuple[str, str]) -> dict:
    return {"code": code, "path": path, "blocking": code != "ffmpeg_missing",
            "message_pro": pro[0], "fix_pro": pro[1],
            "message_easy": easy[0], "fix_easy": easy[1]}


def _raw_check() -> list[dict]:
    issues: list[dict] = []
    if sys.version_info < (3, 10):
        issues.append(_issue(
            "python_version", "",
            (f"Python {sys.version.split()[0]} < 3.10", "安装 Python 3.12/3.13，并使用对应解释器启动。"),
            ("电脑上的 Python 版本太旧", "安装新版 Python 3.12 或 3.13，再重新启动。"),
        ))
    croot = comfy_root()
    if not (croot / "main.py").is_file():
        issues.append(_issue(
            "comfyui_missing", str(croot),
            ("ComfyUI main.py not found", "运行 setup/bootstrap.py 或设置 COMFYUI_ROOT 指向正确目录。"),
            ("还没下载 ComfyUI，或路径不对", "打开首次启动向导点“自动下载”，或把 ComfyUI 放到提示的目录。"),
        ))
    for name in ("webapp/studio.html", "launcher/index.html"):
        if not (ROOT / name).is_file():
            issues.append(_issue(
                "asset_missing", str(ROOT / name),
                (f"missing asset: {name}", "重新拉取完整仓库，确认 webapp/launcher 目录存在。"),
                ("安装包不完整，缺了页面文件", "重新下载完整项目压缩包并解压。"),
            ))
    if not shutil.which("ffmpeg"):
        issues.append(_issue(
            "ffmpeg_missing", "",
            ("ffmpeg not found in PATH", "安装 ffmpeg 并加入 PATH，或在 settings.json 配置 ffmpeg.path。"),
            ("电脑上找不到 ffmpeg", "安装 ffmpeg，或把 ffmpeg.exe 放到项目 tools 目录并在设置里选它。"),
        ))
    for m in catalog():
        if m.get("id") not in selected_models():
            continue
        target = croot / "models" / m.get("target", "") / m.get("file", "")
        if not target.is_file():
            issues.append(_issue(
                "model_missing", str(target),
                (f"model not found: {m.get('id')}", f"下载 {m.get('name')} 到 {target}。"),
                (f"还缺一个模型：{m.get('name')}", "打开首次启动向导，勾选它并点下载。"),
            ))
    try:
        (ROOT / "data").mkdir(parents=True, exist_ok=True)
        probe = ROOT / "data" / ".write_test"
        probe.write_text("1", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except Exception as exc:
        issues.append(_issue(
            "data_not_writable", str(ROOT / "data"),
            (f"data directory not writable: {exc}", "修复目录 ACL/权限，或以有写权限的用户运行。"),
            ("程序不能往 data 文件夹写东西", "把项目放到有写权限的位置，或右键以管理员身份运行。"),
        ))
    return issues


def check(force: bool = False, style: str | None = None) -> dict:
    now = time.time()
    if not force and _cache["raw"] is not None and now - _cache["at"] < 20:
        raw = _cache["raw"]
    else:
        raw = _raw_check()
        _cache.update({"at": now, "raw": raw})
    style = style if style in ("easy", "pro") else get_style()
    issues = []
    for it in raw:
        issues.append({"code": it["code"], "path": it["path"], "blocking": it["blocking"],
                       "message": it[f"message_{style}"], "fix": it[f"fix_{style}"]})
    return {"ok": not any(i["blocking"] for i in issues), "style": style, "issues": issues,
            "comfy_root": str(comfy_root()), "python": sys.version.split()[0],
            "checked_at": time.strftime("%F %T")}


if __name__ == "__main__":
    print(json.dumps(check(force=True), ensure_ascii=False, indent=2))
    raise SystemExit(0 if check().get("ok") else 2)
