"""Self-check with two message styles: easy (plain language) and pro (technical).

检查项（2026-11 加固版）：

阻塞项（``blocking=True``，会让 ``ok=False``）：
    python_version      解释器 < 3.10
    comfyui_missing     ComfyUI ``main.py`` 不存在
    asset_missing       web 页面/启动器资源缺失
    model_missing       已勾选模型文件缺失
    data_not_writable   ``data/`` 不可写
    ffmpeg_missing      **仅当**当前选择会用到视频（选中 video 模型或设置里声明需要视频）时才阻塞

非阻塞告警（``blocking=False``，只提示不影响 ``ok``）：
    templates_missing   模板目录缺失 / 没有 ``*.api.json``
    torch_missing       目标解释器 import torch 失败
    cuda_unavailable    torch 在但 CUDA 不可用
    comfyui_not_running 8188 端口未监听（ComfyUI 没启动是正常状态，不要求它一直开着）
    ffmpeg_missing      纯图像工作流时 ffmpeg 缺失只告警

设计原则：体检本身永远不抛异常、不要求 ComfyUI 正在运行；每条问题都同时提供
easy/pro 两套文案与修复建议。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

try:  # 作为 comfy_mcp.setup.doctor 导入
    from .. import safety as _safety
except Exception:  # pragma: no cover - 作为 setup.doctor / 脚本导入
    try:
        _ROOT_FOR_IMPORT = Path(__file__).resolve().parent.parent
        if str(_ROOT_FOR_IMPORT) not in sys.path:
            sys.path.insert(0, str(_ROOT_FOR_IMPORT))
        import safety as _safety  # type: ignore
    except Exception:
        _safety = None  # type: ignore

ROOT = Path(__file__).resolve().parent.parent
CATALOG = Path(__file__).with_name("models.json")
STYLE_FILE = ROOT / "data" / "user_mode.json"
SETTINGS_FILE = ROOT / "data" / "settings.json"
TEMPLATES_DIR = ROOT / "templates"
WEB_ASSETS = ("webapp/studio.html", "launcher/index.html")

MIN_PYTHON = (3, 10)
COMFY_HOST = "127.0.0.1"
COMFY_PORT = 8188
PORT_TIMEOUT_S = 0.25

VIDEO_KINDS = ("video",)

_cache: dict = {"at": 0.0, "raw": None}


# --------------------------------------------------------------------- 样式 / 基础
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
    env_root = os.environ.get("COMFYUI_ROOT")
    if env_root:
        return Path(env_root)
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


def _settings() -> dict:
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _config_mod():
    try:
        from .. import config as _config
        return _config
    except Exception:
        try:
            import config as _config  # type: ignore
            return _config
        except Exception:
            return None


# ------------------------------------------------------------------ 可打桩的探测层
def _python_version() -> tuple[int, int, int]:
    try:
        return (int(sys.version_info[0]), int(sys.version_info[1]), int(sys.version_info[2]))
    except Exception:  # pragma: no cover
        return (0, 0, 0)


def _ffmpeg_available(settings: dict | None = None) -> bool:
    """配置路径 / PATH / config 内置回退，任意一个能找到 ffmpeg 就算可用。"""
    settings = settings if isinstance(settings, dict) else _settings()
    ffmpeg_cfg = settings.get("ffmpeg") if isinstance(settings.get("ffmpeg"), dict) else {}
    configured = ffmpeg_cfg.get("path")
    if isinstance(configured, str) and configured and Path(configured).is_file():
        return True
    if shutil.which("ffmpeg"):
        return True
    cfg = _config_mod()
    resolver = getattr(cfg, "resolve_ffmpeg", None) if cfg is not None else None
    if callable(resolver):
        try:
            ffmpeg, _probe = resolver(settings)
        except Exception:
            ffmpeg = ""
        if ffmpeg:
            try:
                if Path(str(ffmpeg)).is_file() or shutil.which(str(ffmpeg)):
                    return True
            except OSError:  # pragma: no cover
                pass
    return False


def _comfy_address(settings: dict | None = None) -> tuple[str, int]:
    settings = settings if isinstance(settings, dict) else _settings()
    comfy_cfg = settings.get("comfy") if isinstance(settings.get("comfy"), dict) else {}
    url = comfy_cfg.get("url") or f"http://{COMFY_HOST}:{COMFY_PORT}"
    try:
        parsed = urlparse(str(url))
        host = parsed.hostname or COMFY_HOST
        port = int(parsed.port or COMFY_PORT)
        return host, port
    except (ValueError, TypeError):
        return COMFY_HOST, COMFY_PORT


def _port_open(host: str = COMFY_HOST, port: int = COMFY_PORT,
               timeout: float = PORT_TIMEOUT_S) -> bool:
    if _safety is None:
        return False
    try:
        return bool(_safety.port_open(host, port, timeout))
    except Exception:
        return False


def _port_probe() -> bool:
    host, port = _comfy_address()
    return _port_open(host, port, PORT_TIMEOUT_S)


def _torch_probe() -> dict:
    if _safety is None:
        return {"torch": False, "cuda": False, "error": "safety module unavailable"}
    try:
        return _safety.probe_torch(use_cache=True)
    except Exception as exc:  # pragma: no cover - 防御性
        return {"torch": False, "cuda": False, "error": f"{type(exc).__name__}: {exc}"}


def _settings_wants_video(settings: dict) -> bool:
    """设置里是否声明需要视频/编码能力（可被 setup 或用户显式打开）。"""
    video = settings.get("video")
    if video is True:
        return True
    if isinstance(video, dict):
        if any(bool(video.get(key)) for key in ("enabled", "required", "default")):
            return True
    ffmpeg_cfg = settings.get("ffmpeg")
    if isinstance(ffmpeg_cfg, dict) and ffmpeg_cfg.get("required") is True:
        return True
    features = settings.get("features")
    if isinstance(features, dict) and (features.get("video") is True):
        return True
    generation = settings.get("generation")
    if isinstance(generation, dict) and generation.get("video_required") is True:
        return True
    return False


def _video_required(selected: list[str] | None = None, models: list[dict] | None = None,
                    settings: dict | None = None) -> bool:
    """当前选择是否会用到 ffmpeg：选中 video 模型或设置声明需要视频。"""
    settings = settings if isinstance(settings, dict) else _settings()
    if _settings_wants_video(settings):
        return True
    ids = set(selected if selected is not None else selected_models())
    if not ids:
        return False
    for model in (models if models is not None else catalog()):
        if model.get("id") not in ids:
            continue
        kind = str(model.get("kind") or "").lower()
        if kind.startswith(VIDEO_KINDS):
            return True
    return False


# ------------------------------------------------------------------------ 问题构造
def _issue(code: str, path: str | Path, pro: tuple[str, str], easy: tuple[str, str],
           blocking: bool = False) -> dict:
    return {
        "code": code,
        "path": str(path or ""),
        "blocking": bool(blocking),
        "level": "error" if blocking else "warning",
        "message_pro": pro[0],
        "fix_pro": pro[1],
        "message_easy": easy[0],
        "fix_easy": easy[1],
    }


def _collect() -> dict:
    """跑全部检查，返回 ``{"issues": [...], "checks": [...]}``。"""
    issues: list[dict] = []
    checks: list[dict] = []
    settings = _settings()

    def record(code: str, ok: bool, blocking: bool = False, path: str | Path = "",
               detail: str = "") -> None:
        checks.append({
            "code": code,
            "ok": bool(ok),
            "blocking": bool(blocking) and not bool(ok),
            "level": "ok" if ok else ("error" if blocking else "warning"),
            "path": str(path or ""),
            "detail": str(detail or ""),
        })

    # 1) Python 解释器
    version = tuple(_python_version())[:3]
    python_ok = tuple(version[:2]) >= MIN_PYTHON
    record("python_version", python_ok, True, detail=".".join(str(part) for part in version))
    if not python_ok:
        issues.append(_issue(
            "python_version", "",
            (f"Python {'.'.join(str(p) for p in version)} < "
             f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}",
             "安装 Python 3.12/3.13，并使用对应解释器启动 launcher / MCP。"),
            ("电脑上的 Python 版本太旧",
             "安装新版 Python 3.12 或 3.13，再重新启动。"),
            blocking=True,
        ))

    # 2) ComfyUI 主程序
    croot = comfy_root()
    main_file = croot / "main.py"
    main_ok = main_file.is_file()
    record("comfyui_main", main_ok, True, path=croot, detail=str(main_file))
    if not main_ok:
        issues.append(_issue(
            "comfyui_missing", croot,
            ("ComfyUI main.py not found", "运行 setup/bootstrap.py 或设置 COMFYUI_ROOT 指向正确目录。"),
            ("还没下载 ComfyUI，或路径不对", "运行 setup/install.py 或 setup/bootstrap.py 自动下载，或把 ComfyUI 放到提示的目录。"),
            blocking=True,
        ))

    # 3) Web 资源
    missing_assets = [name for name in WEB_ASSETS if not (ROOT / name).is_file()]
    record("web_assets", not missing_assets, True, path=ROOT,
           detail=", ".join(missing_assets) or "ok")
    for name in missing_assets:
        issues.append(_issue(
            "asset_missing", ROOT / name,
            (f"missing asset: {name}", "重新拉取完整仓库，确认 webapp/launcher 目录存在。"),
            ("安装包不完整，缺了页面文件", "重新下载完整项目压缩包并解压。"),
            blocking=True,
        ))

    # 4) ffmpeg（只有视频相关时才阻塞）
    video_needed = _video_required(settings=settings)
    ffmpeg_ok = _ffmpeg_available(settings)
    record("ffmpeg", ffmpeg_ok, video_needed,
           detail="required by video models/settings" if video_needed
           else "image-only: warning only")
    if not ffmpeg_ok:
        if video_needed:
            issues.append(_issue(
                "ffmpeg_missing", "",
                ("ffmpeg not found (required for video output)",
                 "安装 ffmpeg 并加入 PATH，或在 data/settings.json 的 ffmpeg.path 指定 ffmpeg.exe。"),
                ("做视频要用到 ffmpeg，但电脑上找不到",
                 "安装 ffmpeg，或把 ffmpeg.exe 放到项目 tools 目录并在设置里选它。"),
                blocking=True,
            ))
        else:
            issues.append(_issue(
                "ffmpeg_missing", "",
                ("ffmpeg not found (image-only use is unaffected; editing/probe will fail)",
                 "安装 ffmpeg 并加入 PATH，或设置 ffmpeg.path；只做图像生成可以忽略。"),
                ("找不到 ffmpeg（只生成图片不影响，剪辑/查视频信息会用到）",
                 "需要剪辑视频时再装 ffmpeg，或在设置里选 ffmpeg.exe。"),
                blocking=False,
            ))

    # 5) 已选模型
    selected = list(selected_models())
    models = catalog()
    missing_models: list[tuple[dict, Path]] = []
    for model in models:
        if model.get("id") not in selected:
            continue
        target = croot / "models" / str(model.get("target", "")) / str(model.get("file", ""))
        if not target.is_file():
            missing_models.append((model, target))
    record("models", not missing_models, True, path=croot / "models",
           detail=f"{len(selected)} selected, {len(missing_models)} missing")
    for model, target in missing_models:
        issues.append(_issue(
            "model_missing", target,
            (f"model not found: {model.get('id')}", f"下载 {model.get('name')} 到 {target}。"),
            (f"还缺一个模型：{model.get('name')}", "打开首次启动向导，勾选它并点下载。"),
            blocking=True,
        ))

    # 6) data 目录可写
    data_dir = ROOT / "data"
    data_error = ""
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".write_test"
        probe.write_text("1", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001 - 任何失败都只是体检结论
        data_error = f"{type(exc).__name__}: {exc}"
    record("data_writable", not data_error, True, path=data_dir, detail=data_error)
    if data_error:
        issues.append(_issue(
            "data_not_writable", data_dir,
            (f"data directory not writable: {data_error}",
             "修复目录 ACL/权限，或以有写权限的用户运行。"),
            ("程序不能往 data 文件夹写东西",
             "把项目放到有写权限的位置，或右键以管理员身份运行。"),
            blocking=True,
        ))

    # 7) 模板目录（非阻塞）
    try:
        template_files = sorted(TEMPLATES_DIR.glob("*.api.json")) if TEMPLATES_DIR.is_dir() else []
    except OSError:
        template_files = []
    templates_ok = bool(template_files)
    record("templates", templates_ok, False, path=TEMPLATES_DIR,
           detail=f"{len(template_files)} template(s)")
    if not templates_ok:
        issues.append(_issue(
            "templates_missing", TEMPLATES_DIR,
            ("template directory missing or empty (templates/*.api.json)",
             "重新拉取 templates/ 目录；缺少模板时 plan/generate 会报未知模板。"),
            ("模板文件不见了", "重新下载完整项目压缩包，确认 templates 文件夹里有 *.api.json。"),
            blocking=False,
        ))

    # 8) torch / CUDA（非阻塞）
    torch_info = _torch_probe()
    if not isinstance(torch_info, dict):
        torch_info = {}
    torch_ok = bool(torch_info.get("torch"))
    cuda_ok = bool(torch_info.get("cuda"))
    torch_detail = (f"torch={torch_info.get('torch_version') or '-'} "
                    f"cuda={torch_info.get('cuda_version') or '-'}")
    record("torch", torch_ok and cuda_ok, False, detail=torch_detail)
    if not torch_ok:
        issues.append(_issue(
            "torch_missing", "",
            (f"torch import failed in ComfyUI python: {torch_info.get('error') or 'unknown'}",
             "在 ComfyUI 的 python_embeded 中安装与 CUDA 匹配的 torch。"),
            ("生成引擎依赖还没装好（torch 导入失败）",
             "打开首次启动向导，重新点“安装依赖”。"),
            blocking=False,
        ))
    elif not cuda_ok:
        issues.append(_issue(
            "cuda_unavailable", "",
            ("torch is installed but CUDA is unavailable; generation will be slow or fail",
             "检查显卡驱动/CUDA 版 torch（torch.cuda.is_available() 应为 True）。"),
            ("显卡加速没生效（torch 装了但用不到 CUDA）",
             "更新显卡驱动，或在向导里重装对应 CUDA 版本的 torch。"),
            blocking=False,
        ))

    # 9) ComfyUI 端口（非阻塞；没启动是正常状态）
    port_ok = _port_probe()
    host, port = _comfy_address()
    record("comfy_port", port_ok, False, detail=f"{host}:{port}")
    if not port_ok:
        issues.append(_issue(
            "comfyui_not_running", "",
            (f"ComfyUI not listening on {host}:{port} (fine before start)",
             "需要生成时用 server 工具 start 或 launcher 启动 ComfyUI。"),
            (f"ComfyUI 现在没在运行（{port} 端口没开，不影响体检）",
             "要生成时在启动页点“启动 ComfyUI”就行。"),
            blocking=False,
        ))

    return {"issues": issues, "checks": checks}


# --------------------------------------------------------------------------- 对外
def _reset_cache() -> None:
    """清空 20s 结果缓存（测试 / 强制重扫用）。"""
    _cache.update({"at": 0.0, "raw": None})


def check(force: bool = False, style: str | None = None) -> dict:
    now = time.time()
    if not force and _cache["raw"] is not None and now - _cache["at"] < 20:
        raw = _cache["raw"]
    else:
        raw = _collect()
        _cache.update({"at": now, "raw": raw})
    style = style if style in ("easy", "pro") else get_style()

    issues = []
    for item in raw.get("issues") or []:
        issues.append({
            "code": item["code"],
            "path": item["path"],
            "blocking": item["blocking"],
            "level": item["level"],
            "message": item[f"message_{style}"],
            "fix": item[f"fix_{style}"],
        })
    warnings = [item for item in issues if not item["blocking"]]
    checks = [dict(item) for item in raw.get("checks") or []]
    blocking_count = sum(1 for item in issues if item["blocking"])
    return {
        "ok": blocking_count == 0,
        "style": style,
        "issues": issues,
        "warnings": warnings,
        "checks": checks,
        "summary": {
            "blocking": blocking_count,
            "warnings": len(warnings),
            "passed": sum(1 for item in checks if item["ok"]),
            "total": len(checks),
        },
        "comfy_root": str(comfy_root()),
        "python": ".".join(str(part) for part in tuple(_python_version())[:3]),
        "checked_at": time.strftime("%F %T"),
    }


def warnings_only(result: dict | None = None) -> list[dict]:
    """只取非阻塞告警（给 UI/日志用）。"""
    data = result if isinstance(result, dict) else check()
    return list(data.get("warnings") or [])


if __name__ == "__main__":
    payload = check(force=True)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(0 if payload.get("ok") else 2)
