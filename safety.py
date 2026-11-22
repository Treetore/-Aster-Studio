"""硬件探测 / 生成成本估算 / 资源守卫。

这个模块负责在 8GB VRAM + 32GB RAM 的笔记本上尽量「别把机器跑崩」：

* :func:`hardware` / :func:`vram_usage` / :func:`ram_free_gb` —— 只读探测。
  GPU 走 ``nvidia-smi``（子进程，不新增依赖），RAM 走 Windows
  ``GlobalMemoryStatusEx``（ctypes），探测不到时由 Guard 回退到 ComfyUI 的
  ``/system_stats``。
* :func:`estimate_cost` —— 纯函数，从 ``workflow.resolve()`` 的 ``resolved``
  字典估风险 / 耗时，不触碰硬件。
* :class:`Guard` —— ``preflight`` 起任务前闸门；``watch`` 是后台线程看门狗，
  RAM 触底 / ComfyUI 掉线 / 显存长期告急时通过 ``client.interrupt()`` 打断。

所有面向用户的字符串都用 ASCII 英文：Windows 控制台默认 GBK，带 emoji 或
特殊符号的 print 会直接 UnicodeEncodeError 中断服务。
"""
from __future__ import annotations

import ctypes
import inspect
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Callable

try:
    from . import config as _config
except Exception:  # pragma: no cover - 单独导入本文件时的回退
    try:
        import config as _config  # type: ignore
    except Exception:  # pragma: no cover
        _config = None  # type: ignore

GB = 1024.0 ** 3
MB = 1024.0

NVIDIA_SMI = "nvidia-smi"
# GPU 名称 / 显存总量 / 已用 / 空闲（MiB）
_GPU_FIELDS = "name,memory.total,memory.used,memory.free"
_NVIDIA_TIMEOUT = 10.0
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# estimate_cost 的硬上限（与 config.DEFAULT_SETTINGS["generation"] 保持一致）
MAX_PIXELS = 1920 * 1088
MAX_LENGTH = 362
DEFAULT_FPS = 24.0
VRAM_BUDGET_MB = 7600
IMAGE_MAX_PIXELS = 2048 * 2048  # 单帧图像模板上限（2K 原生输出）

# 看门狗阈值
VRAM_WARN_FREE_MB = 300
VRAM_WARN_HITS = 3
SYS_FAIL_HITS = 3
TRIP_RAM_MIN = 2.5
MIN_RAM_DEFAULT = 6.0

# 自检（setup.doctor 复用）：最低 Python、torch 探测超时与缓存
MIN_PYTHON = (3, 10)
TORCH_PROBE_TIMEOUT = 30.0
TORCH_CACHE_TTL_S = 120.0
COMFY_PORT = 8188

_RISK_ORDER = {"low": 0, "medium": 1, "high": 2}


def _max_risk(a: str, b: str) -> str:
    return b if _RISK_ORDER.get(str(b), 0) > _RISK_ORDER.get(str(a), 0) else a


# ---------------------------------------------------------------- 子进程 / GPU
def _run(cmd: list[str], timeout: float = _NVIDIA_TIMEOUT) -> tuple[int, str, str]:
    """跑一个短命子进程；失败返回 ``(-1, "", err)``，绝不抛异常。"""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_CREATE_NO_WINDOW,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, "", str(exc)


def _gpu_info() -> dict | None:
    """``nvidia-smi`` → ``{name, total_mb, used_mb, free_mb}``；不可用时 ``None``。"""
    code, out, _err = _run(
        [NVIDIA_SMI, f"--query-gpu={_GPU_FIELDS}", "--format=csv,noheader,nounits"]
    )
    if code != 0 or not out.strip():
        return None
    line = out.strip().splitlines()[0]
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 3:
        return None
    try:
        total = int(float(parts[1]))
        used = int(float(parts[2]))
    except (TypeError, ValueError):
        return None
    if len(parts) >= 4:
        try:
            free = int(float(parts[3]))
        except (TypeError, ValueError):
            free = total - used
    else:
        free = total - used
    return {
        "name": parts[0] or None,
        "total_mb": total,
        "used_mb": used,
        "free_mb": free,
    }


def vram_usage() -> dict | None:
    """显存占用 ``{total_mb, used_mb, free_mb}``；nvidia-smi 不可用返回 ``None``。"""
    info = _gpu_info()
    if not info:
        return None
    return {
        "total_mb": info["total_mb"],
        "used_mb": info["used_mb"],
        "free_mb": info["free_mb"],
    }


# ------------------------------------------------------------------- RAM / 磁盘
class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _windows_mem() -> dict | None:
    """Windows ``GlobalMemoryStatusEx`` → ``{total_gb, free_gb}``。"""
    if os.name != "nt":
        return None
    try:
        stat = _MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))  # type: ignore[attr-defined]
        if not ok:
            return None
        return {
            "total_gb": stat.ullTotalPhys / GB,
            "free_gb": stat.ullAvailPhys / GB,
        }
    except (AttributeError, OSError, ValueError):  # pragma: no cover - 非 Windows
        return None


def _linux_mem() -> dict | None:
    """``/proc/meminfo`` 兜底（非 Windows 跑测试时用）。"""
    if os.name == "nt":
        return None
    try:
        values: dict[str, int] = {}
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                num = rest.strip().split(" ")[0]
                if num.isdigit():
                    values[key.strip()] = int(num) * 1024  # kB -> bytes
        total = values.get("MemTotal")
        free = values.get("MemAvailable", values.get("MemFree"))
        if total is None or free is None:
            return None
        return {"total_gb": total / GB, "free_gb": free / GB}
    except (OSError, ValueError):  # pragma: no cover
        return None


def _mem_info() -> dict | None:
    """``{total_gb, free_gb}``；探测不到返回 ``None``。"""
    return _windows_mem() or _linux_mem()


def ram_free_gb() -> float:
    """空闲物理内存（GB）；探测失败返回 ``-1.0``（调用方自行决定回退）。"""
    mem = _mem_info()
    if not mem:
        return -1.0
    return round(float(mem["free_gb"]), 2)


def _disk_free_gb() -> float | None:
    base = getattr(_config, "DATA_DIR", None) or os.getcwd()
    probe = base
    while probe and not os.path.isdir(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            probe = os.getcwd()
            break
        probe = parent
    try:
        return round(shutil.disk_usage(probe).free / GB, 2)
    except OSError:  # pragma: no cover
        return None


def hardware() -> dict:
    """一次性硬件快照（只读，不会抛异常）。"""
    gpu = _gpu_info()
    mem = _mem_info()
    disk = _disk_free_gb()
    return {
        "gpu_name": gpu["name"] if gpu else None,
        "vram_total_mb": gpu["total_mb"] if gpu else None,
        "vram_used_mb": gpu["used_mb"] if gpu else None,
        "vram_free_mb": gpu["free_mb"] if gpu else None,
        "ram_total_gb": round(mem["total_gb"], 2) if mem else None,
        "ram_free_gb": round(mem["free_gb"], 2) if mem else None,
        "disk_free_gb": disk,
        "checks": {
            "nvidia_smi_ok": gpu is not None,
            "memory_api_ok": mem is not None,
            "disk_ok": disk is not None,
            "os": os.name,
        },
    }


# ------------------------------------------------------------------ 环境自检探测
def python_supported(version: Any = None) -> bool:
    """Python 版本是否达到 :data:`MIN_PYTHON`；``version`` 传 ``(3, 12, 4)`` 之类。"""
    value = tuple(sys.version_info)[:2] if version is None else version
    try:
        parts = tuple(int(part) for part in tuple(value)[:2])
    except (TypeError, ValueError):
        return False
    return len(parts) == 2 and parts >= MIN_PYTHON


def comfy_python() -> str:
    """ComfyUI 使用的 Python 解释器（设置环境变量 > config.COMFY_PYTHON > 当前解释器）。"""
    candidates: list[str] = []
    env = os.environ.get("COMFY_MCP_PYTHON")
    if env:
        candidates.append(env)
    configured = getattr(_config, "COMFY_PYTHON", None) if _config is not None else None
    if configured:
        candidates.append(str(configured))
    candidates.append(sys.executable)
    for candidate in candidates:
        try:
            if candidate and os.path.isfile(candidate):
                return candidate
        except OSError:  # pragma: no cover - 极端路径
            continue
    return sys.executable


_TORCH_PROBE_CODE = r"""
import json
result = {"torch": False, "torch_version": None, "cuda": False,
          "cuda_version": None, "device": None}
try:
    import torch
    result["torch"] = True
    result["torch_version"] = str(getattr(torch, "__version__", "") or "")
    try:
        result["cuda"] = bool(torch.cuda.is_available())
        if result["cuda"]:
            result["cuda_version"] = str(getattr(torch.version, "cuda", "") or "")
            try:
                result["device"] = str(torch.cuda.get_device_name(0))
            except Exception:
                result["device"] = None
    except Exception as exc:
        result["cuda_error"] = "%s: %s" % (type(exc).__name__, exc)
except Exception as exc:
    result["error"] = "%s: %s" % (type(exc).__name__, exc)
print(json.dumps(result))
"""

_torch_cache: dict = {"key": None, "at": 0.0, "value": None}


def _blank_torch_info(error: str = "") -> dict:
    return {"torch": False, "cuda": False, "torch_version": None, "cuda_version": None,
            "device": None, "error": error or None}


def probe_torch(python_exe: str | None = None, timeout: float = TORCH_PROBE_TIMEOUT,
                use_cache: bool = False) -> dict:
    """在目标解释器里探测 ``import torch`` / CUDA；任何失败都返回结构化结果，绝不抛异常。

    用子进程而不是直接 ``import torch``：doctor 自己跑在 launcher 的 Python 上，
    而 torch 装在 ComfyUI 的嵌入式 Python 里，直接 import 会误判。
    """
    exe = str(python_exe or comfy_python())
    key = (exe, float(timeout))
    now = time.time()
    if use_cache:
        cached = _torch_cache.get("value")
        if cached is not None and _torch_cache.get("key") == key and \
                now - float(_torch_cache.get("at") or 0.0) < TORCH_CACHE_TTL_S:
            return dict(cached)
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    try:
        proc = subprocess.run(
            [exe, "-c", _TORCH_PROBE_CODE],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1.0, float(timeout)),
            creationflags=_CREATE_NO_WINDOW,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return _blank_torch_info(f"{type(exc).__name__}: {exc}")
    info = _blank_torch_info()
    payload = None
    for line in reversed((proc.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                payload = json.loads(line)
            except ValueError:
                payload = None
            if isinstance(payload, dict):
                break
    if isinstance(payload, dict):
        info.update({key: payload.get(key) for key in info if key in payload})
        info["torch"] = bool(payload.get("torch"))
        info["cuda"] = bool(payload.get("cuda"))
        if payload.get("error"):
            info["error"] = str(payload.get("error"))[:300]
    elif proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        info["error"] = (tail[-1] if tail else f"probe exit {proc.returncode}")[:300]
    else:
        info["error"] = "probe returned no JSON"
    if use_cache:
        _torch_cache.update({"key": key, "at": now, "value": dict(info)})
    return info


def port_open(host: str = "127.0.0.1", port: int = COMFY_PORT,
              timeout: float = 0.25) -> bool:
    """TCP 端口是否可连（只读探测；失败一律返回 ``False``）。"""
    try:
        with socket.create_connection((str(host), int(port)), timeout=max(0.05, float(timeout))):
            return True
    except (OSError, ValueError):
        return False


def selfcheck(python_exe: str | None = None, host: str = "127.0.0.1",
              port: int = COMFY_PORT, timeout: float = 0.25,
              use_cache: bool = True) -> dict:
    """聚合自检：Python 版本 / torch+CUDA / ComfyUI 端口。只读、不抛异常。"""
    version = tuple(sys.version_info)[:3]
    torch_info = probe_torch(python_exe, use_cache=use_cache)
    return {
        "python": {
            "version": ".".join(str(part) for part in version),
            "supported": python_supported(version),
            "executable": sys.executable,
            "comfy_python": str(python_exe or comfy_python()),
        },
        "torch": torch_info,
        "comfy_port": {
            "host": str(host),
            "port": int(port),
            "open": port_open(host, port, timeout),
        },
    }


# --------------------------------------------------------------------- 成本估算
def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def estimate_cost(resolved: dict) -> dict:
    """从 ``workflow.resolve()`` 的 ``resolved`` 估算风险与耗时（纯函数）。

    返回 ``{pixels, frames, duration_s, vram_risk, ram_risk, eta_minutes, notes}``，
    风险取值 ``low|medium|high``。``eta_minutes`` 只是经验公式，不是精度承诺。
    """
    resolved = resolved if isinstance(resolved, dict) else {}

    width = max(1, _to_int(resolved.get("width"), 1024))
    height = max(1, _to_int(resolved.get("height"), 576))
    pixels = width * height
    megapixels = pixels / 1_000_000.0

    length = _to_int(resolved.get("length"), 0)
    duration_s = _to_float(resolved.get("duration_s"), 0.0)
    fps = _to_float(resolved.get("fps"), DEFAULT_FPS) or DEFAULT_FPS
    if length <= 0:
        if duration_s > 0:
            length = max(5, int(round(duration_s * fps)))
        else:
            length = 49
            duration_s = length / fps
    if duration_s <= 0:
        duration_s = length / fps
    frames = max(1, length)
    steps = max(1, _to_int(resolved.get("steps"), 8))

    notes: list[str] = []
    vram_risk = "low"
    ram_risk = "low"

    # 单帧图像模板（Qwen-Image / H3 静帧）：显存由模型权重主导，像素上限放宽到 2K
    kind = str(resolved.get("kind") or "")
    is_image = kind.startswith("image")
    pixel_limit = IMAGE_MAX_PIXELS if is_image else MAX_PIXELS

    if pixels > pixel_limit:
        vram_risk = "high"
        notes.append(
            f"pixels {pixels} exceed max_pixels {pixel_limit}; ComfyUI may run out of VRAM"
        )
    elif megapixels > 0.5 or frames > 150:
        vram_risk = "medium"
    if frames > MAX_LENGTH:
        ram_risk = "high"
        vram_risk = _max_risk(vram_risk, "high")
        notes.append(f"length {frames} exceeds max_length {MAX_LENGTH}")
    elif frames > 240 or megapixels > 1.0:
        ram_risk = "medium"

    if vram_risk == "high":
        notes.append("high VRAM risk on an 8 GB GPU: reduce megapixels/length or use the draft profile")
    elif vram_risk == "medium":
        notes.append("moderate VRAM use: prefer draft/standard profile and watch free VRAM")
    if ram_risk == "high":
        notes.append("high RAM risk: close other applications before running")
    if width % 32 or height % 32:
        notes.append("width/height are not multiples of 32; ComfyUI may pad or reject them")
    if resolved.get("profile") == "high" and vram_risk == "low":
        notes.append("profile 'high' usually resolves above 0.5 MP; re-check after resolve")

    # 经验耗时：steps * frames * megapixels 的线性模型 + 固定开销。
    eta_minutes = round(0.4 + (steps * frames * megapixels) / 60.0, 1)
    notes.append("eta_minutes is a rough heuristic, not a measured value")

    return {
        "pixels": pixels,
        "pixel_limit": pixel_limit,
        "frames": frames,
        "duration_s": round(float(duration_s), 2),
        "vram_risk": vram_risk,
        "ram_risk": ram_risk,
        "eta_minutes": eta_minutes,
        "notes": notes,
    }


# ------------------------------------------------------------------------- Guard
class Guard:
    """资源守卫：起任务前的 ``preflight`` + 运行中的 ``watch`` 看门狗。"""

    def __init__(self, client: Any, settings: dict) -> None:
        self.client = client
        settings = settings if isinstance(settings, dict) else {}
        # 允许传完整 settings，也允许直接传 safety 子字典
        safety = settings.get("safety") if isinstance(settings.get("safety"), dict) else settings
        self.settings = settings
        self.min_ram_free_gb = _to_float(safety.get("min_ram_free_gb"), MIN_RAM_DEFAULT)
        self.trip_ram_free_gb = _to_float(safety.get("trip_ram_free_gb"), TRIP_RAM_MIN)
        self.ram_trip_consecutive = max(1, _to_int(safety.get("ram_trip_consecutive"), 3))
        self.min_disk_free_gb = _to_float(safety.get("min_disk_free_gb"), 8.0)
        self.watch_interval_s = max(0.01, _to_float(safety.get("watch_interval_s"), 5.0))
        self.single_flight = bool(safety.get("single_flight", True))
        orchestrate = safety.get("orchestrate") if isinstance(safety.get("orchestrate"), dict) else {}
        self.allow_concurrent = bool(orchestrate.get("allow_concurrent", False))
        self.max_queue = max(0, _to_int(orchestrate.get("max_queue"), 2))
        self.vram_budget_mb = _to_float(orchestrate.get("vram_budget_mb"), VRAM_BUDGET_MB)
        self.vram_warn_free_mb = _to_float(safety.get("vram_warn_free_mb"), VRAM_WARN_FREE_MB)
        # H3 视频默认解除显存/RAM 阈值限制（用户可在 settings.safety.unlimited_h3 关闭）
        self.unlimited_h3 = bool(safety.get("unlimited_h3", True))

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._trip: str | None = None
        self._warnings: list[str] = []

    # ------------------------------------------------------------- 快照 / 闸门
    def snapshot(self) -> dict:
        """硬件 + ComfyUI 状态 + 队列长度（探测失败也只记录错误，不抛异常）。"""
        snap: dict[str, Any] = {
            "timestamp": time.time(),
            "pid": os.getpid(),
            "hardware": hardware(),
            "comfy": {"up": False},
            "queue": {"running": 0, "pending": 0},
        }
        try:
            stats = self.client.system_stats()
        except Exception as exc:  # noqa: BLE001 - 任何失败都只记录
            snap["comfy"] = {"up": False, "error": str(exc)[:300]}
            stats = None
        if isinstance(stats, dict):
            system = stats.get("system") if isinstance(stats.get("system"), dict) else {}
            ram_total = _to_float(system.get("ram_total"), 0.0)
            ram_free = _to_float(system.get("ram_free"), 0.0)
            snap["comfy"] = {
                "up": True,
                "ram_total_gb": round(ram_total / GB, 2) if ram_total > 0 else None,
                "ram_free_gb": round(ram_free / GB, 2) if ram_free > 0 else None,
                "comfyui_version": system.get("comfyui_version"),
            }
        try:
            queue_data = self.client.queue()
        except Exception as exc:  # noqa: BLE001
            snap["queue"] = {"running": 0, "pending": 0, "error": str(exc)[:300]}
        else:
            if isinstance(queue_data, dict):
                snap["queue"] = {
                    "running": len(queue_data.get("queue_running") or []),
                    "pending": len(queue_data.get("queue_pending") or []),
                }
        return snap

    def preflight(self, resolved: dict, active_jobs: int = 0) -> dict:
        """起任务前的资源闸门。

        返回 ``{ok, risk, reasons, suggestions, snapshot}``；
        ``snapshot`` 里额外带 ``cost``（:func:`estimate_cost` 的结果）。
        """
        cost = estimate_cost(resolved or {})
        _kind = str((resolved or {}).get("kind") or "")
        _h3_unlimited = self.unlimited_h3 and not _kind.startswith("image")
        snap = self.snapshot()
        snap["cost"] = cost
        hw = snap.get("hardware") or {}
        comfy = snap.get("comfy") or {}
        queue = snap.get("queue") or {}

        reasons: list[str] = []
        suggestions: list[str] = []
        risk = "low"
        ok = True

        def fail(reason: str, level: str = "high", suggestion: str | None = None) -> None:
            nonlocal ok, risk
            ok = False
            risk = _max_risk(risk, level)
            reasons.append(reason)
            if suggestion:
                suggestions.append(suggestion)

        if not comfy.get("up"):
            detail = comfy.get("error")
            fail(
                "ComfyUI is not reachable" + (f" ({detail})" if detail else ""),
                "high",
                "start ComfyUI first (server tool 'start')",
            )

        ram_free = hw.get("ram_free_gb")
        if not isinstance(ram_free, (int, float)) or ram_free < 0:
            ram_free = comfy.get("ram_free_gb")
        if not isinstance(ram_free, (int, float)):
            risk = _max_risk(risk, "medium")
            reasons.append("free RAM is unknown (memory probe failed)")
            suggestions.append("install/repair nvidia-smi and verify /system_stats")
        elif _h3_unlimited:
            suggestions.append("H3 已解除 RAM 阈值限制（unlimited_h3=true）")
        elif ram_free < self.trip_ram_free_gb:
            fail(
                f"free RAM {ram_free:.2f} GB is below trip threshold {self.trip_ram_free_gb:.2f} GB",
                "high",
                "close memory-hungry applications, then retry",
            )
        elif ram_free < self.min_ram_free_gb:
            fail(
                f"free RAM {ram_free:.2f} GB is below minimum {self.min_ram_free_gb:.2f} GB",
                "high",
                "free up RAM (close apps / lower other jobs) before generating",
            )

        disk_free = hw.get("disk_free_gb")
        if isinstance(disk_free, (int, float)) and disk_free < self.min_disk_free_gb:
            fail(
                f"disk free {disk_free:.2f} GB is below minimum {self.min_disk_free_gb:.2f} GB",
                "high",
                "free disk space on the ComfyUI drive",
            )

        vram_free = hw.get("vram_free_mb")
        if _h3_unlimited:
            suggestions.append("H3 已解除 VRAM 阈值限制（unlimited_h3=true）")
        elif isinstance(vram_free, (int, float)) and vram_free < self.vram_warn_free_mb:
            fail(
                f"free VRAM {int(vram_free)} MB is below {int(self.vram_warn_free_mb)} MB",
                "high",
                "free VRAM first (server tool 'free') or wait for other jobs to finish",
            )

        if active_jobs > 0:
            if self.single_flight or not self.allow_concurrent:
                fail(
                    f"{active_jobs} job(s) already active and single_flight is enabled",
                    "high",
                    "wait for the active job to finish (tool 'wait')",
                )
            else:
                risk = _max_risk(risk, "medium")
                reasons.append(f"{active_jobs} job(s) already active")

        pending = _to_int(queue.get("pending"), 0)
        if pending >= self.max_queue:
            fail(
                f"queue already has {pending} pending job(s) (max_queue={self.max_queue})",
                "high",
                "wait for the queue to drain or raise safety.orchestrate.max_queue",
            )

        if _h3_unlimited:
            if cost["vram_risk"] != "low":
                suggestions.append(f"H3 忽略显存风险等级 {cost['vram_risk']}")
        elif cost["vram_risk"] == "high":
            fail(
                f"estimated VRAM risk is high for this machine "
                f"({cost.get('pixels')} px, frames={cost.get('frames')}, limit {cost.get('pixel_limit')} px)",
                "high",
                "reduce megapixels/length (e.g. 1024x1024) or use the draft profile",
            )
        elif cost["vram_risk"] == "medium":
            risk = _max_risk(risk, "medium")
            suggestions.append("moderate VRAM usage: keep an eye on free VRAM while running")

        if cost["ram_risk"] == "high" and not _h3_unlimited:
            fail(
                "estimated RAM risk is high for this machine",
                "high",
                "reduce length/duration (fewer frames) before generating",
            )
        elif cost["ram_risk"] == "medium":
            risk = _max_risk(risk, "medium")

        return {
            "ok": ok,
            "risk": risk,
            "reasons": reasons,
            "suggestions": suggestions,
            "snapshot": snap,
        }

    # ------------------------------------------------------------- 看门狗
    def watch(self, job_id: str, on_trip: Callable[[str], None] | None = None) -> None:
        """后台守护线程：每 ``watch_interval_s`` 检查一次，触发时打断任务。"""
        self.stop_watch()
        with self._lock:
            self._trip = None
            self._warnings = []
        self._stop.clear()
        thread = threading.Thread(
            target=self._watch_loop,
            args=(str(job_id), on_trip),
            name="comfy-guard-watch",
            daemon=True,
        )
        self._thread = thread
        thread.start()

    def stop_watch(self) -> None:
        """停止看门狗线程并等待它退出（幂等）。"""
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=self.watch_interval_s + 2.0)

    def trip_reason(self) -> str | None:
        """最近一次触发原因；未触发返回 ``None``。"""
        with self._lock:
            return self._trip

    def warnings(self) -> list[str]:
        """运行期间的告警（例如显存持续偏低），不触发打断。"""
        with self._lock:
            return list(self._warnings)

    @property
    def watching(self) -> bool:
        thread = self._thread
        return bool(thread is not None and thread.is_alive())

    def _record_trip(self, reason: str) -> None:
        with self._lock:
            self._trip = reason

    def _record_warning(self, reason: str) -> None:
        with self._lock:
            if reason not in self._warnings:
                self._warnings.append(reason)

    def _trip_now(self, reason: str, job_id: str, on_trip: Callable[..., None] | None) -> None:
        self._record_trip(reason)
        try:
            self.client.interrupt(job_id)
        except Exception:  # noqa: BLE001 - 打断失败也要继续通知
            pass
        self._invoke_on_trip(on_trip, job_id, reason)

    @staticmethod
    def _on_trip_arity(callback: Callable[..., None]) -> int | None:
        """回调需要几个位置参数；取不到签名时返回 ``None``。"""
        try:
            params = list(inspect.signature(callback).parameters.values())
        except (TypeError, ValueError):  # 内建 / 不透明 callable
            return None
        required = 0
        total = 0
        variadic = False
        for param in params:
            if param.kind is param.VAR_POSITIONAL:
                variadic = True
                continue
            if param.kind in (param.POSITIONAL_ONLY, param.POSITIONAL_OR_KEYWORD):
                total += 1
                if param.default is param.empty:
                    required += 1
        if required >= 2:
            return 2
        if required == 1:
            return 1
        if total >= 2 or variadic:
            return 2
        return total

    def _invoke_on_trip(
        self,
        on_trip: Callable[..., None] | None,
        job_id: str,
        reason: str,
    ) -> None:
        """兼容两种回调风格：``on_trip(reason)`` 与 ``on_trip(job_id, reason)``。"""
        if on_trip is None:
            return
        arity = self._on_trip_arity(on_trip)
        if arity is None:
            attempts: list[tuple] = [(job_id, reason), (reason,), ()]
        elif arity >= 2:
            attempts = [(job_id, reason)]
        elif arity == 1:
            attempts = [(reason,)]
        else:
            attempts = [()]
        for args in attempts:
            try:
                on_trip(*args)
                return
            except TypeError:
                continue
            except Exception:  # noqa: BLE001 - 回调异常不能弄死看门狗线程
                return

    def _local_ram_free_gb(self) -> float:
        try:
            return float(ram_free_gb())
        except Exception:  # noqa: BLE001
            return -1.0

    def _stats_ram_free_gb(self, stats: dict | None) -> float | None:
        if not isinstance(stats, dict):
            return None
        system = stats.get("system") if isinstance(stats.get("system"), dict) else {}
        value = _to_float(system.get("ram_free"), 0.0)
        if value <= 0:
            return None
        return value / GB

    def _watch_loop(
        self, job_id: str, on_trip: Callable[[str], None] | None
    ) -> None:
        sys_fail = 0
        low_vram_hits = 0
        ram_low_hits = 0
        while not self._stop.wait(self.watch_interval_s):
            # 1) ComfyUI 是否还活着
            stats: dict | None = None
            try:
                candidate = self.client.system_stats()
            except Exception:  # noqa: BLE001
                candidate = None
            if not isinstance(candidate, dict):
                sys_fail += 1
                if sys_fail >= SYS_FAIL_HITS:
                    self._trip_now(
                        f"ComfyUI system_stats failed {sys_fail} times in a row",
                        job_id,
                        on_trip,
                    )
                    return
            else:
                sys_fail = 0
                stats = candidate

            # 2) RAM：本地 ctypes 优先，取不到时用 /system_stats 兜底
            free = self._local_ram_free_gb()
            if free < 0:
                fallback = self._stats_ram_free_gb(stats)
                free = fallback if fallback is not None else -1.0
            if free >= 0 and free < self.trip_ram_free_gb:
                ram_low_hits += 1
                if ram_low_hits >= self.ram_trip_consecutive:
                    self._trip_now(
                        f"free RAM {free:.2f} GB < {self.trip_ram_free_gb:.2f} GB "
                        f"for {ram_low_hits} checks",
                        job_id,
                        on_trip,
                    )
                    return
            else:
                ram_low_hits = 0

            # 3) 显存：连续偏低只告警，不打断（避免误杀）
            usage: dict | None
            try:
                usage = vram_usage()
            except Exception:  # noqa: BLE001
                usage = None
            free_mb = usage.get("free_mb") if isinstance(usage, dict) else None
            if isinstance(free_mb, (int, float)) and free_mb < self.vram_warn_free_mb:
                low_vram_hits += 1
                if low_vram_hits >= VRAM_WARN_HITS:
                    self._record_warning(
                        f"free VRAM {int(free_mb)} MB < {int(self.vram_warn_free_mb)} MB "
                        f"for {low_vram_hits} consecutive checks"
                    )
                    low_vram_hits = 0
            else:
                low_vram_hits = 0
