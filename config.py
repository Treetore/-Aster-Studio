"""路径与可调设置。

所有路径按「便携版 ComfyUI 就装在固定位置」假定；可用环境变量覆盖：
    COMFY_MCP_COMFY_ROOT   ComfyUI 目录（含 main.py）
    COMFY_MCP_PYTHON       嵌入 Python 可执行文件
    COMFY_MCP_URL          ComfyUI HTTP 地址（默认 http://127.0.0.1:8188）
"""
from __future__ import annotations

import copy
import json
import os

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PROJECT_DIR, "data")
TEMPLATES_DIR = os.path.join(PROJECT_DIR, "templates")
LOGS_DIR = os.path.join(PROJECT_DIR, "logs")
ARTIFACTS_DIR = os.path.join(PROJECT_DIR, "artifacts")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")

COMFY_ROOT = os.environ.get(
    "COMFY_MCP_COMFY_ROOT",
    r"K:\CONFI\mian\ComfyUI_windows_portable\ComfyUI",
)
COMFY_PYTHON = os.environ.get(
    "COMFY_MCP_PYTHON",
    r"K:\CONFI\mian\ComfyUI_windows_portable\python_embeded\python.exe",
)
COMFY_PORTABLE_ROOT = os.path.dirname(COMFY_ROOT)
COMFY_MAIN = os.path.join(COMFY_ROOT, "main.py")
COMFY_URL = os.environ.get("COMFY_MCP_URL", "http://127.0.0.1:8188").rstrip("/")
COMFY_HOST = "127.0.0.1"
COMFY_PORT = 8188
OUTPUT_DIR = os.path.join(COMFY_ROOT, "output")
INPUT_DIR = os.path.join(COMFY_ROOT, "input")
MODELS_DIR = os.path.join(COMFY_ROOT, "models")

FFMPEG_FALLBACK = r"C:\ffmpeg\ffmpeg-master-latest-win64-gpl-shared\bin\ffmpeg.exe"
FFPROBE_FALLBACK = r"C:\ffmpeg\ffmpeg-master-latest-win64-gpl-shared\bin\ffprobe.exe"

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
    """返回 (ffmpeg, ffprobe) 路径；优先设置里配置的，其次 PATH，最后内置回退。"""
    s = settings or load_settings()
    cfg = s.get("ffmpeg", {})

    def pick(configured: str | None, name: str, fallback: str) -> str:
        if configured and os.path.isfile(configured):
            return configured
        from shutil import which

        found = which(name)
        if found:
            return found
        if os.path.isfile(fallback):
            return fallback
        return name

    return (
        pick(cfg.get("path"), "ffmpeg", FFMPEG_FALLBACK),
        pick(cfg.get("probe"), "ffprobe", FFPROBE_FALLBACK),
    )


for _d in (DATA_DIR, LOGS_DIR, ARTIFACTS_DIR):
    os.makedirs(_d, exist_ok=True)
