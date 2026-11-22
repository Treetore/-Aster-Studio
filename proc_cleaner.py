"""任务期间清理无用进程 + 任务结束只停止**本项目自己**起的编码进程（可开关）。

安全边界（重要，2026-11 加固）：

* 只会强杀通过 :func:`register_owned` 登记过的 PID。名字叫 ``ffmpeg.exe`` 不等于可以杀：
  用户自己开的压制/录屏进程永远不会被「按名字」命中。
* 空闲巡检只处理两类目标：
    1. 登记过但已经跨任务 / 失效的 owned 孤儿进程（:data:`IDLE_OWNED_GRACE_S` 之后）；
    2. 命令行明确带项目标记（``<项目目录>\\tools\\`` 或 ``COMFY_MCP_JOB`` 环境变量）
       的遗留工具进程。
* 任何情况下都保护：当前进程、父进程链、ComfyUI / webapp / 隧道 / ``segment_film.py``。
* 日志按大小轮转（:func:`maybe_rotate_log`），避免 ``logs/process_cleanup.log`` 无限增长。

对外 API：
    enabled / auto_stop_encode / status
    register_owned / unregister_owned / owned_pids / is_owned
    cleanup / start_for_job / finish_job
    maybe_rotate_log
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import psutil

ROOT = Path(r"K:\CONFI\comfy_mcp")
SETTINGS = ROOT / "data" / "settings.json"
LOG = ROOT / "logs" / "process_cleanup.log"

# 日志轮转默认值（可用 settings.process_cleanup.log_max_bytes / log_backups 覆盖）
DEFAULT_LOG_MAX_BYTES = 2 * 1024 * 1024
DEFAULT_LOG_BACKUPS = 3

# 环境变量标记：本项目启动的子进程可以带上它，便于识别「项目标签」
OWNED_TAG_ENV = "COMFY_MCP_JOB"

# 空闲阈值（秒）
IDLE_OWNED_GRACE_S = 180.0        # owned 进程登记后多久算「闲置」
TAGGED_PYTHON_IDLE_S = 7200.0     # 项目 tools 下的 python 脚本闲置多久算孤儿
TAGGED_TUNNEL_IDLE_S = 600.0      # tunnel.ps1 闲置多久算孤儿
ENCODER_IDLE_CPU = 3.0            # 低于该 CPU 百分比才视为闲置
ENCODER_NAMES = frozenset({"ffmpeg.exe", "ffprobe.exe", "ffmpeg", "ffprobe"})
PYTHON_NAMES = frozenset({"python.exe", "pythonw.exe", "python", "python3"})
SHELL_NAMES = frozenset({"powershell.exe", "pwsh.exe", "powershell", "pwsh"})
PROTECTED_CMDLINE_TOKENS = (
    "comfyui\\main.py",
    "comfyui/main.py",
    "webapp.app:app",
    "server.py --stdio",
    "launcher\\run.py",
    "launcher/run.py",
    "cloudflared",
    "segment_film.py",
)

THREAD_JOIN_S = 6.0
DEFAULT_INTERVAL_S = 60.0

_lock = threading.Lock()          # 进程扫描锁（同一时刻只允许一次 kill 扫描）
_thread_lock = threading.Lock()   # 清理线程生命周期锁（避免重复线程）
_registry_lock = threading.RLock()

_thread: threading.Thread | None = None
_stop = threading.Event()
_reason = ""
_active_job: str | None = None
_owned: dict[int, dict] = {}


# --------------------------------------------------------------------- 配置 / 日志
def _cfg() -> dict:
    try:
        data = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except Exception:
        return {}
    cfg = data.get("process_cleanup") if isinstance(data, dict) else None
    return cfg if isinstance(cfg, dict) else {}


def enabled() -> bool:
    return bool(_cfg().get("enabled", True))


def auto_stop_encode() -> bool:
    return bool(_cfg().get("auto_stop_encode", True))


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def maybe_rotate_log(
    path: str | os.PathLike[str] | None = None,
    max_bytes: int | None = None,
    backups: int | None = None,
) -> dict:
    """按大小轮转日志（``path -> path.1 -> path.2 ...``）。

    只在文件确实超过 ``max_bytes`` 时动作；返回 ``{rotated, path, size, max_bytes, backups}``，
    任何 IO 失败都只返回 ``rotated=False``，不抛异常。
    """
    target = Path(path) if path is not None else Path(LOG)
    cfg = _cfg()
    limit = _as_int(max_bytes if max_bytes is not None else cfg.get("log_max_bytes"),
                    DEFAULT_LOG_MAX_BYTES)
    keep = _as_int(backups if backups is not None else cfg.get("log_backups"), DEFAULT_LOG_BACKUPS)
    keep = max(1, keep) if limit > 0 else 0
    result = {"rotated": False, "path": str(target), "size": 0, "max_bytes": limit,
              "backups": keep}
    try:
        size = target.stat().st_size
    except OSError:
        return result
    result["size"] = int(size)
    if limit <= 0 or size < limit:
        return result
    # 从最旧的一份开始挪：.N 删除，.N-1 -> .N，... .1 -> .2，最后 base -> .1
    for idx in range(keep, 0, -1):
        src = target.with_name(f"{target.name}.{idx}")
        if not src.exists():
            continue
        if idx >= keep:
            try:
                src.unlink()
            except OSError:
                pass
        else:
            try:
                os.replace(src, target.with_name(f"{target.name}.{idx + 1}"))
            except OSError:
                pass
    try:
        os.replace(target, target.with_name(f"{target.name}.1"))
    except OSError:
        return result
    result["rotated"] = True
    return result


def rotate_log(**kwargs: Any) -> dict:
    """:func:`maybe_rotate_log` 的显式别名（给工具/测试用）。"""
    return maybe_rotate_log(**kwargs)


def _log(msg: str) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        maybe_rotate_log(LOG)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg + "\n")
    except Exception:
        pass


# ------------------------------------------------------------------- owned 登记表
def _coerce_pid(pid: Any) -> int | None:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def register_owned(pid: Any, kind: str = "encoder", job_id: str | None = None,
                   cmdline: str = "") -> bool:
    """登记一个由本项目启动的 PID（任务收尾时只可能强杀这些 PID）。

    返回 ``False`` 表示 PID 非法 / 等于当前进程；重复登记会刷新记录。
    """
    value = _coerce_pid(pid)
    if value is None or value == os.getpid():
        return False
    with _registry_lock:
        job = job_id if job_id is not None else _active_job
        _owned[value] = {
            "pid": value,
            "kind": str(kind or "encoder"),
            "job_id": None if job is None else str(job),
            "cmdline": str(cmdline or "")[:500],
            "registered_at": time.time(),
        }
    return True


def unregister_owned(pid: Any) -> bool:
    """移除登记；返回是否真的存在过。"""
    value = _coerce_pid(pid)
    if value is None:
        return False
    with _registry_lock:
        return _owned.pop(value, None) is not None


def owned_pids() -> set[int]:
    with _registry_lock:
        return set(_owned)


def is_owned(pid: Any) -> bool:
    value = _coerce_pid(pid)
    if value is None:
        return False
    with _registry_lock:
        return value in _owned


def owned_record(pid: Any) -> dict | None:
    value = _coerce_pid(pid)
    if value is None:
        return None
    with _registry_lock:
        record = _owned.get(value)
        return dict(record) if record else None


def status() -> dict:
    """当前清理器状态（只读，不扫进程、不杀进程）。"""
    with _registry_lock:
        owned = {str(pid): dict(rec) for pid, rec in _owned.items()}
        job = _active_job
    thread = _thread
    return {
        "enabled": enabled(),
        "auto_stop_encode": auto_stop_encode(),
        "running": bool(thread is not None and thread.is_alive()),
        "thread": thread.name if thread is not None else None,
        "job": job,
        "reason": _reason,
        "owned": owned,
        "log": str(LOG),
    }


def reset_state() -> None:
    """重置内存状态（测试/热重载用；不会杀进程）。"""
    global _thread, _reason, _active_job
    _stop.set()
    with _thread_lock:
        _thread = None
    _reason = ""
    with _registry_lock:
        _owned.clear()
        _active_job = None


# ---------------------------------------------------------------- 进程识别 / 保护
def _norm(text: Any) -> str:
    return str(text or "").replace("/", "\\").lower()


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _pid_exists(pid: int) -> bool:
    """单独包一层，方便测试打桩（避免真查 OS）。"""
    try:
        return bool(psutil.pid_exists(pid))
    except Exception:
        return False


def _proc_environ(proc: Any) -> dict:
    """尽力读取进程环境变量；读不到就返回空字典（Windows 上可能需要权限）。"""
    getter = getattr(proc, "environ", None)
    if not callable(getter):
        return {}
    try:
        env = getter()
    except Exception:
        return {}
    return env if isinstance(env, dict) else {}


def _is_project_tagged(proc: Any, pid: int, low_cmdline: str) -> bool:
    """命令行 / 环境变量里是否有清晰的本项目标签。"""
    root = _norm(ROOT).rstrip("\\")
    if root:
        token = root + "\\tools\\"
        if token in low_cmdline:
            return True
    if (OWNED_TAG_ENV.lower() + "=") in low_cmdline:
        return True
    env = _proc_environ(proc)
    for key in (OWNED_TAG_ENV, "COMFY_MCP_OWNED"):
        value = env.get(key) or env.get(key.lower()) or env.get(key.upper())
        if value:
            return True
    return False


def _protected_pids() -> set[int]:
    keep = {os.getpid()}
    try:
        for p in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmd = _norm(" ".join(p.info.get("cmdline") or []))
                if any(token in cmd for token in PROTECTED_CMDLINE_TOKENS):
                    keep.add(int(p.info["pid"]))
            except Exception:
                continue
    except Exception:
        pass
    try:
        me = psutil.Process(os.getpid())
        for parent in me.parents():
            keep.add(parent.pid)
    except Exception:
        pass
    return keep


def _decision(proc: Any, pid: int, record: dict | None, now: float, force_owned: bool,
              active_job: str | None) -> str | None:
    """返回杀进程的理由；``None`` 表示必须放过。纯函数式（不产生副作用）。"""
    info = getattr(proc, "info", None) or {}
    name = _norm(info.get("name")).strip()
    cmdline = [str(part) for part in (info.get("cmdline") or [])]
    low = _norm(" ".join(cmdline))
    create_time = _as_float(info.get("create_time"), now)
    cpu = _as_float(info.get("cpu_percent"), 0.0)

    if record is not None:
        kind = str(record.get("kind") or "").lower()
        registered_at = _as_float(record.get("registered_at"), now)
        age = max(0.0, now - registered_at)
        cmd_hint = str(record.get("cmdline") or "").strip()
        first = ""
        if cmd_hint:
            first = os.path.basename(cmd_hint.split()[0].strip('"')).strip().lower()
        name_matches = (not first) or first == name
        is_encoder = kind == "encoder" or name in ENCODER_NAMES
        encoder_image = name in ENCODER_NAMES
        if is_encoder and not name_matches:
            # PID 可能已被系统复用给别的程序：不认识的镜像一律放过
            return None
        if is_encoder and not encoder_image and not cmd_hint:
            # 只有 kind 标记、没有命令行证据时，只认明确的 ffmpeg/ffprobe 镜像
            return None
        rec_job = record.get("job_id")
        same_job = not (active_job and rec_job and str(rec_job) != str(active_job))
        if force_owned and is_encoder and same_job:
            return "owned encoder on finish"
        if not same_job and age > IDLE_OWNED_GRACE_S:
            # 跨任务孤儿：只清理「已经闲置」的，避免并发场景误杀别的任务
            return "owned orphan from previous job"
        if (cmd_hint and name_matches) or encoder_image:
            if age > IDLE_OWNED_GRACE_S and cpu < ENCODER_IDLE_CPU:
                return "owned idle"
        return None

    # 没登记过的进程：只有「ffmpeg 之外 + 明确项目标签」才可能在闲置后清理
    if name in ENCODER_NAMES:
        return None
    if not _is_project_tagged(proc, pid, low):
        return None
    age = max(0.0, now - create_time)
    if name in PYTHON_NAMES:
        if "segment_film.py" in low:
            return None
        if age > TAGGED_PYTHON_IDLE_S and cpu < 2.0:
            return "tagged idle python tool"
        return None
    if name in SHELL_NAMES and "tunnel.ps1" in low and age > TAGGED_TUNNEL_IDLE_S:
        return "tagged idle tunnel"
    return None


# ---------------------------------------------------------------------- 清理主流程
def cleanup(reason: str = "job", force_owned: bool = False, job_id: str | None = None,
            force_encoders: bool | None = None) -> list[int]:
    """清理明确的临时进程；返回被杀 PID 列表。

    * ``force_owned=True``：任务收尾，只强杀**已登记**的 encoder（ffmpeg/ffprobe）。
    * 默认模式：只清理登记过且闲置/跨任务的 owned 进程，或明确带项目标签的遗留工具。
    * 用户自己的 ffmpeg/ffprobe 永不触碰。

    ``force_encoders`` 是旧参数名的兼容别名（语义已收窄为「owned only」）。
    """
    if force_encoders is not None:
        force_owned = force_owned or bool(force_encoders)
    if not enabled():
        return []

    with _lock:
        keep = _protected_pids()
        with _registry_lock:
            owned_snapshot = {pid: dict(rec) for pid, rec in _owned.items()}
            active_job = _active_job
        killed: list[int] = []
        seen: set[int] = set()
        now = time.time()

        try:
            iterator = psutil.process_iter(
                ["pid", "name", "cmdline", "create_time", "cpu_percent"]
            )
        except Exception:
            return []

        for p in iterator:
            try:
                pid = int(p.info["pid"])
            except Exception:
                continue
            seen.add(pid)
            if pid in keep:
                continue
            try:
                why = _decision(p, pid, owned_snapshot.get(pid), now, force_owned, active_job)
            except Exception:
                continue
            if not why:
                continue
            try:
                p.kill()
            except Exception:
                continue
            killed.append(pid)
            with _registry_lock:
                _owned.pop(pid, None)
            _log(f"{reason}: killed {pid} ({why})")

        # 已登记但扫描不到的：确认真的消失才移除登记（扫描失败不会误删）
        for pid in list(owned_snapshot):
            if pid in seen or pid in killed:
                continue
            if not _pid_exists(pid):
                with _registry_lock:
                    _owned.pop(pid, None)

        if killed:
            _log(f"{reason}: killed {killed}")
        return killed


# ------------------------------------------------------------------------ 线程控制
def _loop(interval: float = DEFAULT_INTERVAL_S, job_id: str | None = None) -> None:
    while not _stop.wait(interval):
        try:
            cleanup(_reason or "job", job_id=job_id)
        except Exception:
            pass


def start_for_job(reason: str = "job", job_id: str | None = None) -> dict:
    """启动（或复用）清理线程，并记录当前任务的 owned 标记。

    幂等：线程已活着时只更新 job 标记，不会再起第二个线程。
    """
    global _thread, _reason, _active_job
    job = str(job_id if job_id is not None else reason or "job")
    if not enabled():
        return {"ok": False, "started": False, "enabled": False, "job_id": job}

    _reason = str(reason or job)
    _stop.clear()
    with _registry_lock:
        _active_job = job

    with _thread_lock:
        thread = _thread
        if thread is not None and thread.is_alive():
            _log(f"cleaner already running; job marker -> {job}")
            return {"ok": True, "started": False, "already_running": True,
                    "job_id": job, "thread": thread.name}
        thread = threading.Thread(
            target=_loop, args=(DEFAULT_INTERVAL_S, job), name="proc-cleaner", daemon=True
        )
        _thread = thread
        thread.start()
    _log(f"cleaner started for {job}")
    return {"ok": True, "started": True, "already_running": False,
            "job_id": job, "thread": thread.name}


def finish_job(reason: str = "job", job_id: str | None = None) -> dict:
    """停止清理线程，并按任务标记收尾（只强杀 owned encoder）。幂等。"""
    global _thread, _active_job
    with _registry_lock:
        job = str(job_id if job_id is not None else _active_job or reason or "job")

    _stop.set()
    with _thread_lock:
        thread = _thread
        _thread = None
    if thread is not None and thread.is_alive():
        thread.join(timeout=THREAD_JOIN_S)

    killed: list[int] = []
    if auto_stop_encode():
        killed = cleanup(f"{reason}:finish", force_owned=True, job_id=job)

    with _registry_lock:
        _active_job = None
    _log(f"cleaner stopped for {job} (killed={killed})")
    return {"ok": True, "job_id": job, "killed": killed, "stopped": True,
            "thread_alive": bool(thread is not None and thread.is_alive())}
