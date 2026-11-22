"""路径与可调设置（可移植版）。

项目内所有路径都从本文件位置（``__file__``）推导，不含任何写死的盘符；
ComfyUI 便携版根目录按「环境变量 > 项目同级目录（sibling）> 同名默认位置」解析。

支持的环境变量（也可写在项目根的 ``.env`` 里，见 ``.env.example``）：
    COMFYUI_ROOT / COMFY_MCP_COMFY_ROOT
                            ComfyUI 目录（含 main.py），或便携版根目录
                            （含 ComfyUI\\ 与 python_embeded\\）
    COMFY_MCP_PYTHON        运行 ComfyUI 的 Python 可执行文件
    COMFY_MCP_URL           ComfyUI HTTP 地址（默认 http://127.0.0.1:8188）
    COMFY_BRIDGE_TOKEN      桥接接口 token（webapp 读取，未设置时用 data/bridge_token.txt）
    FFMPEG_PATH / FFPROBE_PATH
                            ffmpeg / ffprobe 可执行文件（或所在目录）

解析结果附带来源标记（``COMFY_ROOT_SOURCE`` / ``COMFY_PYTHON_SOURCE`` /
``FFMPEG_SOURCE``），便于 setup/install.py 与 doctor 打印诊断信息。
"""
from __future__ import annotations

import copy
import glob
import json
import os
import sys
from pathlib import Path
from shutil import which

PROJECT_DIR = str(Path(__file__).resolve().parent)
PROJECT_ROOT = Path(PROJECT_DIR)
ROOT = PROJECT_ROOT  # 别名，供脚本统一使用

ENV_FILE = PROJECT_ROOT / ".env"


# --------------------------------------------------------------------------- #
# .env 读取（极简实现，避免额外依赖；已存在的环境变量优先）
# --------------------------------------------------------------------------- #
def load_env_file(path: str | os.PathLike | None = None) -> list[str]:
    """读取 ``.env`` 并写入 ``os.environ``；不覆盖已有变量。返回写入的键名。"""
    target = Path(path) if path is not None else ENV_FILE
    applied: list[str] = []
    try:
        text = target.read_text(encoding="utf-8-sig")
    except OSError:
        return applied
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.lower().startswith("export "):
            line = line[7:].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key or key in os.environ:
            continue
        os.environ[key] = value
        applied.append(key)
    return applied


load_env_file()

DATA_DIR = os.path.join(PROJECT_DIR, "data")
TEMPLATES_DIR = os.path.join(PROJECT_DIR, "templates")
LOGS_DIR = os.path.join(PROJECT_DIR, "logs")
ARTIFACTS_DIR = os.path.join(PROJECT_DIR, "artifacts")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")

# ComfyUI 便携版目录名（Windows 官方包）
PORTABLE_DIRNAME = "ComfyUI_windows_portable"
COMFY_SUBDIR = "ComfyUI"


def _env_first(*names: str) -> str:
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return ""


def _expand(raw: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(raw)))


def _normalize_comfy_root(raw: str) -> str:
    """把 COMFYUI_ROOT 的写法规范化成「含 main.py 的 ComfyUI 目录」。

    接受三种写法：ComfyUI 目录本身、便携版根目录（含 ComfyUI\\）、不存在的路径
    （按原样返回，便于报错时显示用户配置）。
    """
    p = _expand(raw)
    if (p / "main.py").is_file():
        return str(p)
    nested = p / COMFY_SUBDIR
    if (nested / "main.py").is_file() or nested.is_dir():
        return str(nested)
    if p.is_dir() and (p / "python_embeded").is_dir():
        return str(nested)
    return str(p)


def comfy_root_candidates(root: Path | None = None) -> list[Path]:
    """按优先级给出 ComfyUI 目录候选（sibling 布局在前，通用布局在后）。

    典型布局（默认）::

        <workspace>/
            comfy_mcp/                    <- 本项目
            mian/ComfyUI_windows_portable/ComfyUI/
    """
    r = Path(root) if root is not None else PROJECT_ROOT
    return [
        r.parent / "mian" / PORTABLE_DIRNAME / COMFY_SUBDIR,
        r.parent / PORTABLE_DIRNAME / COMFY_SUBDIR,
        r / "mian" / PORTABLE_DIRNAME / COMFY_SUBDIR,
        r / PORTABLE_DIRNAME / COMFY_SUBDIR,
        r.parent / COMFY_SUBDIR,
        r / COMFY_SUBDIR,
        r.parent.parent / "mian" / PORTABLE_DIRNAME / COMFY_SUBDIR,
        r.parent.parent / PORTABLE_DIRNAME / COMFY_SUBDIR,
    ]


def _detect_comfy_root() -> tuple[str, str]:
    """返回 (comfy_root, source)。env 无效时退回同级布局，并在 source 里注明。"""
    raw = _env_first("COMFYUI_ROOT", "COMFY_MCP_COMFY_ROOT", "COMFY_ROOT")
    normalized = _normalize_comfy_root(raw) if raw else ""
    if normalized and (Path(normalized) / "main.py").is_file():
        return normalized, f"env:{raw}"

    candidates = comfy_root_candidates()
    for cand in candidates:
        if (cand / "main.py").is_file():
            note = f"（COMFYUI_ROOT={raw} 未找到 main.py）" if raw else ""
            return str(cand), f"sibling:{cand}{note}"

    # 未找到实际安装：env 有配置就沿用（便于报错定位），否则用首选默认位置
    if normalized:
        return normalized, f"env-invalid:{raw} (未找到 main.py)"
    fallback = candidates[0]
    return str(fallback), f"fallback:{fallback} (未找到 main.py，可设置 COMFYUI_ROOT)"


COMFY_ROOT, COMFY_ROOT_SOURCE = _detect_comfy_root()
COMFY_PORTABLE_ROOT = os.path.dirname(COMFY_ROOT)
COMFY_MAIN = os.path.join(COMFY_ROOT, "main.py")


def _detect_comfy_python() -> tuple[str, str]:
    raw = _env_first("COMFY_MCP_PYTHON", "COMFY_PYTHON")
    if raw:
        candidate = _expand(raw)
        if candidate.is_file():
            return str(candidate), f"env:{raw}"

    for cand in (
        Path(COMFY_PORTABLE_ROOT) / "python_embeded" / "python.exe",
        Path(COMFY_PORTABLE_ROOT) / "python_embeded" / "python",
        Path(COMFY_ROOT) / "python_embeded" / "python.exe",
        Path(COMFY_ROOT).parent / "venv" / "Scripts" / "python.exe",
        Path(COMFY_ROOT) / ".venv" / "Scripts" / "python.exe",
    ):
        if cand.is_file():
            note = f"（COMFY_MCP_PYTHON={raw} 不存在）" if raw else ""
            return str(cand), f"portable:{cand}{note}"

    # 便携版缺失时退回当前解释器（ComfyUI 依赖须装在该解释器里）
    fallback = sys.executable or "python"
    if raw:
        return str(_expand(raw)), f"env-invalid:{raw} (文件不存在)"
    return fallback, f"fallback:{fallback} (未找到便携版 python，可设置 COMFY_MCP_PYTHON)"


COMFY_PYTHON, COMFY_PYTHON_SOURCE = _detect_comfy_python()
COMFY_URL = (os.environ.get("COMFY_MCP_URL") or "http://127.0.0.1:8188").rstrip("/")
COMFY_HOST = "127.0.0.1"
COMFY_PORT = 8188
OUTPUT_DIR = os.path.join(COMFY_ROOT, "output")
INPUT_DIR = os.path.join(COMFY_ROOT, "input")
MODELS_DIR = os.path.join(COMFY_ROOT, "models")


# --------------------------------------------------------------------------- #
# ffmpeg / ffprobe 发现
# --------------------------------------------------------------------------- #
_SEARCH_DIRS_CACHE: list[Path] | None = None


def _ffmpeg_search_dirs() -> list[Path]:
    """常见本地工具目录（不要求存在，仅按顺序探测；结果在进程内缓存）。"""
    global _SEARCH_DIRS_CACHE
    if _SEARCH_DIRS_CACHE is not None:
        return _SEARCH_DIRS_CACHE
    dirs = [
        PROJECT_ROOT / "tools" / "ffmpeg" / "bin",
        PROJECT_ROOT / "tools" / "bin",
        PROJECT_ROOT / "bin",
        PROJECT_ROOT / ".tools" / "ffmpeg" / "bin",
        PROJECT_ROOT / "vendor" / "ffmpeg" / "bin",
        Path(COMFY_PORTABLE_ROOT) / "ffmpeg" / "bin",
        Path(COMFY_PORTABLE_ROOT) / "python_embeded" / "Scripts",
        Path(COMFY_ROOT) / "ffmpeg" / "bin",
    ]
    local_app = os.environ.get("LOCALAPPDATA")
    if local_app:
        dirs.append(Path(local_app) / "Microsoft" / "WinGet" / "Links")
    for env_name in ("FFMPEG_HOME", "FFMPEG_DIR"):
        value = os.environ.get(env_name)
        if value:
            base = _expand(value)
            dirs.extend([base, base / "bin"])
    # 允许发现已解压的自定义安装（例如 <盘符>:\ffmpeg\*\bin），但不是必需项
    for pattern in (r"C:\ffmpeg\*\bin", r"C:\tools\ffmpeg\*\bin", r"C:\ProgramData\chocolatey\bin"):
        dirs.extend(Path(p) for p in glob.glob(pattern))
    _SEARCH_DIRS_CACHE = dirs
    return dirs


def _resolve_tool(
    name: str,
    configured: str | None,
    env_value: str,
    search_dirs: list[Path] | None = None,
) -> tuple[str, str]:
    """返回 (可执行文件路径或裸命令名, 来源说明)。"""
    exe_name = f"{name}.exe" if os.name == "nt" else name

    def as_exe(value: str) -> str | None:
        p = _expand(value)
        if p.is_dir():
            for cand in (p / exe_name, p / name):
                if cand.is_file():
                    return str(cand)
            return None
        if p.is_file():
            return str(p)
        return None

    if env_value:
        found = as_exe(env_value)
        if found:
            return found, "env"
    if configured:
        found = as_exe(configured)
        if found:
            return found, "settings"

    found = which(name)
    if found:
        return found, "PATH"

    for directory in search_dirs if search_dirs is not None else _ffmpeg_search_dirs():
        for cand in (directory / exe_name, directory / name):
            if cand.is_file():
                return str(cand), "local"

    # 实在找不到就返回裸命令名，由调用方在运行时报错
    return name, "unresolved"


def discover_ffmpeg(settings: dict | None = None) -> dict:
    """探测 ffmpeg/ffprobe，返回路径与来源（供 setup/install.py 打印）。"""
    s = settings if settings is not None else load_settings()
    cfg = s.get("ffmpeg", {}) or {}
    search = _ffmpeg_search_dirs()
    ffmpeg, ff_src = _resolve_tool(
        "ffmpeg", cfg.get("path"), _env_first("FFMPEG_PATH", "FFMPEG_BIN"), search
    )
    ffprobe, fp_src = _resolve_tool(
        "ffprobe", cfg.get("probe"), _env_first("FFPROBE_PATH", "FFPROBE_BIN"), search
    )
    return {"ffmpeg": ffmpeg, "ffprobe": ffprobe, "ffmpeg_source": ff_src, "ffprobe_source": fp_src}


DEFAULT_SETTINGS = {
    "comfy": {
        "url": COMFY_URL,
        "start_args": [
            "--windows-standalone-build",
            "--listen", "127.0.0.1",
            "--port", "8188",
            "--fast",
            "--cuda-malloc",
            "--disable-auto-launch",
        ],
        "start_timeout_s": 300,
        "stop_on_exit": False,
        # 备选启动参数：实测 8GB/32GB 机器第一次跑 H3 时，TE(14.6GB)+UNet(19.5GB)
        # 会同时驻留把空闲 RAM 打到 2GB 以下；lowmem 档位让 ComfyUI 更积极释放模型/缓存。
        # 用 start(mode="lowmem") 或 comfy.ps1 start 选择。
        "start_profiles": {
            "normal": [],
            "lowmem": [
                "--lowvram",
                "--cache-none",
                "--reserve-vram", "0.8",
            ],
            "vram_only": [
                "--cache-none",
                "--reserve-vram", "0.8",
            ],
        },
    },
    "safety": {
        "min_ram_free_gb": 8.0,
        "trip_ram_free_gb": 3.5,
        "watch_interval_s": 5.0,
        "single_flight": True,
        "min_disk_free_gb": 8.0,
        # 任务编排：默认严格串行，队列最多 2 个待跑任务
        "orchestrate": {
            "enabled": True,
            "allow_concurrent": False,
            "max_queue": 2,
            "vram_budget_mb": 7600,
        },
    },
    "generation": {
        "default_profile": "draft",
        "profiles": {
            # 8GB VRAM / 32GB RAM 的安全档位（越小越稳）
            "draft": {
                "aspect": "16:9", "megapixels": 0.2, "duration_s": 2,
                "steps": 4, "sampler": "res_multistep", "scheduler": "simple",
            },
            "standard": {
                "aspect": "16:9", "megapixels": 0.2, "duration_s": 5,
                "steps": 4, "sampler": "res_multistep", "scheduler": "simple",
            },
            "high": {
                "aspect": "16:9", "megapixels": 0.2, "duration_s": 5,
                "steps": 8, "sampler": "res_multistep", "scheduler": "simple",
            },
            # 以下三档用于「缩短分镜 / 低显存」A/B 实验（配合 4-step LoRA）
            "short": {
                "aspect": "16:9", "megapixels": 0.2, "duration_s": 5,
                "steps": 4, "sampler": "res_multistep", "scheduler": "simple",
            },
            "medium": {
                "aspect": "16:9", "megapixels": 0.2, "duration_s": 10,
                "steps": 8, "sampler": "res_multistep", "scheduler": "simple",
            },
            "compact": {
                "aspect": "16:9", "megapixels": 0.2, "duration_s": 15,
                "steps": 8, "sampler": "res_multistep", "scheduler": "simple",
            },
        },
        # 生成策略护栏（数据来自 2026-11-11 压力测试，见 docs/GENERATION_POLICY_20261111.md）
        "policy": {
            "two_pass_max_length": 124,        # 二采仅允许 <=5s（362 帧的二采单步 ~20 分钟，不可行）
            "max_recommended_steps": 12,       # 长段建议 <=10 步
            "long_length_threshold": 260,      # >=10s 视为长段
            "max_length": 362,                 # 模型训练上限 15.08s
            "min_free_ram_gb": 3.0,            # 提交前最小空闲 RAM
        },
        "max_pixels": 1920 * 1088,
        "max_length": 362,
        "output_prefix": "video/comfy_mcp",
    },
    "ffmpeg": {
        "path": None,
        "probe": None,
        "threads": 4,
        "timeout_s": 1800,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_settings() -> dict:
    """默认设置 + data/settings.json 覆盖。环境变量优先级最高。"""
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    try:
        if os.path.isfile(SETTINGS_PATH):
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                user = json.load(f)
            if isinstance(user, dict):
                settings = _deep_merge(settings, user)
    except (OSError, ValueError):
        pass
    # 环境变量覆盖
    if os.environ.get("COMFY_MCP_URL"):
        settings["comfy"]["url"] = COMFY_URL
    return settings


def save_settings(settings: dict) -> str:
    """原子写入 data/settings.json。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = SETTINGS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SETTINGS_PATH)
    return SETTINGS_PATH


def settings_snapshot() -> dict:
    """返回不含敏感信息的设置快照（给工具展示）。"""
    s = load_settings()
    return s


def resolve_ffmpeg(settings: dict | None = None) -> tuple[str, str]:
    """返回 (ffmpeg, ffprobe) 路径。

    优先级：环境变量（FFMPEG_PATH/FFPROBE_PATH）> data/settings.json >
    PATH > 项目内/便携版/常见本地工具目录 > 裸命令名。
    """
    found = discover_ffmpeg(settings)
    return found["ffmpeg"], found["ffprobe"]


def path_report() -> dict:
    """可移植性诊断：打印各关键路径与来源（供 setup/install.py 使用）。"""
    ff = discover_ffmpeg()
    return {
        "project_dir": PROJECT_DIR,
        "env_file": str(ENV_FILE) if ENV_FILE.is_file() else None,
        "comfy_root": COMFY_ROOT,
        "comfy_root_source": COMFY_ROOT_SOURCE,
        "comfy_main_exists": os.path.isfile(COMFY_MAIN),
        "comfy_portable_root": COMFY_PORTABLE_ROOT,
        "comfy_python": COMFY_PYTHON,
        "comfy_python_source": COMFY_PYTHON_SOURCE,
        "comfy_url": COMFY_URL,
        "logs_dir": LOGS_DIR,
        "data_dir": DATA_DIR,
        **ff,
    }


for _d in (DATA_DIR, LOGS_DIR, ARTIFACTS_DIR):
    os.makedirs(_d, exist_ok=True)
