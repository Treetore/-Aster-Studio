"""comfy_mcp MCP 服务器（stdio JSON-RPC）+ 本地 CLI。

用法：
    # 作为 MCP stdio 服务（DSH / Claude 等客户端）
    python server.py --stdio

    # 本地直接调用某个工具（便于调试 / 未注册 MCP 时使用）
    python server.py --call status --args "{}"
    python server.py --list-tools

设计要点：
- 工具调用必须短、可预期：generate 只提交不阻塞；wait 最多约 110 秒。
- 所有生成请求先过 workflow.validate + safety.Guard.preflight，默认单飞。
- 网关/后台线程守护运行中的任务：RAM 低于阈值自动 interrupt，避免本机崩溃。
- stdout 只写协议；日志写 logs/mcp.log（UTF-8）。
"""
from __future__ import annotations

import importlib
import json
import logging
import os
import subprocess
import sys
import threading
import time
import traceback
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)
for p in (HERE, PARENT):
    if p not in sys.path:
        sys.path.insert(0, p)

# 兼容「包内导入」和「脚本直接导入」两种方式
try:  # pragma: no cover - 运行环境相关
    import config  # type: ignore
    from mcp_protocol import McpServer, ToolError  # type: ignore
except ImportError:  # pragma: no cover
    from comfy_mcp import config  # type: ignore
    from comfy_mcp.mcp_protocol import McpServer, ToolError  # type: ignore

from mcp_protocol import McpServer as _McpServer, ToolError as _ToolError  # noqa: E402

import job_lock  # noqa: E402
import proc_cleaner  # noqa: E402
import notify  # noqa: E402
import model_policy  # noqa: E402

LOG_PATH = os.path.join(config.LOGS_DIR, "mcp.log")
logger = logging.getLogger("comfy_mcp")


def _setup_logging() -> None:
    os.makedirs(config.LOGS_DIR, exist_ok=True)
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        # 注意：stdout 是协议通道，任何日志都不能去那里
        err = logging.StreamHandler(sys.stderr)
        err.setLevel(logging.INFO)
        err.setFormatter(logging.Formatter("[comfy_mcp] %(message)s"))
        logger.addHandler(err)


_setup_logging()


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _jsonable(obj):
    return json.loads(json.dumps(obj, ensure_ascii=False, default=str))


class ComfyService:
    """工具层状态：一个进程内单例。"""

    def __init__(self):
        self.settings = config.load_settings()
        self.client = None
        self.store = None
        self.guard = None
        self._object_info_cache = None
        self._pid_file = os.path.join(config.DATA_DIR, "comfy_pid.json")
        self._init_modules()

    # ------------------------------------------------------------- 惰性初始化
    def _init_modules(self):
        import importlib

        try:
            client_mod = importlib.import_module("client")
        except ImportError:
            client_mod = importlib.import_module("comfy_mcp.client")
        try:
            jobs_mod = importlib.import_module("jobs")
        except ImportError:
            jobs_mod = importlib.import_module("comfy_mcp.jobs")
        try:
            safety_mod = importlib.import_module("safety")
        except ImportError:
            safety_mod = importlib.import_module("comfy_mcp.safety")
        self._client_mod = client_mod
        self._jobs_mod = jobs_mod
        self._safety_mod = safety_mod
        self.client = client_mod.ComfyClient(self.settings["comfy"]["url"])
        self.store = jobs_mod.JobStore()
        self.guard = safety_mod.Guard(self.client, self.settings)

    # ---------------------------------------------------------------- 辅助
    def _workflow(self):
        import importlib

        try:
            return importlib.import_module("workflow")
        except ImportError:
            return importlib.import_module("comfy_mcp.workflow")

    def _editing(self):
        import importlib

        try:
            return importlib.import_module("editing")
        except ImportError:
            return importlib.import_module("comfy_mcp.editing")

    def object_info(self) -> dict | None:
        if self._object_info_cache is None:
            try:
                self._object_info_cache = self.client.object_info()
            except Exception as exc:
                logger.warning("object_info 拉取失败: %s", exc)
                return None
        return self._object_info_cache

    def active_jobs(self) -> list[dict]:
        """本地 queued/running 记录；先与 ComfyUI 实时状态对账，剔除 stale 记录。

        2026-11-10 问题：脚本/任务被强杀后，jobs.json 里的 running 记录永远不会自己结束，
        导致 single_flight 一直判定「有任务在跑」而拒绝所有新提交。
        """
        try:
            jobs = self.store.list(limit=20, status=["queued", "running"])
        except Exception:
            return []
        if not jobs:
            return []
        live: list[dict] = []
        unreachable = False
        for job in jobs:
            jid = str(job.get("id") or "")
            if not jid:
                continue
            try:
                remote = self.client.job(jid)
            except Exception:
                unreachable = True
                break
            if remote is None:
                try:
                    self.store.update(jid, status="cancelled", error="stale: ComfyUI 中不存在该任务，已自动清理")
                except Exception:
                    pass
                logger.warning("active_jobs: 清理 stale 记录 %s", jid[:8])
                continue
            status = str((remote or {}).get("status") or "")
            if status in ("running", "in_progress", "queued", "pending"):
                live.append({**job, "status": "running"})
            else:
                try:
                    self.store.update(jid, status=status or "completed")
                except Exception:
                    pass
                continue
        if unreachable:
            logger.warning("active_jobs: ComfyUI 不可达，跳过 reconcile，返回本地记录")
            return jobs
        return live

    def _read_pid(self) -> int | None:
        try:
            with open(self._pid_file, "r", encoding="utf-8") as f:
                return int(json.load(f).get("pid"))
        except (OSError, ValueError, TypeError):
            return None

    def _write_pid(self, pid: int | None) -> None:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        tmp = self._pid_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"pid": pid, "at": _now_iso()}, f)
        os.replace(tmp, self._pid_file)

    def _pid_alive(self, pid: int | None) -> bool:
        if not pid:
            return False
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=10,
                encoding="utf-8", errors="replace",
            )
            return str(pid) in (out.stdout or "")
        except Exception:
            return False

    # ------------------------------------------------------------- 工具实现
    def tool_status(self) -> dict:
        up = False
        stats = None
        err = None
        try:
            up = self.client.is_up()
            if up:
                stats = self.client.system_stats()
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
        hw = {}
        try:
            hw = self._safety_mod.hardware()
        except Exception as exc:  # noqa: BLE001
            hw = {"error": str(exc)}
        queue = {"running": 0, "pending": 0}
        if up:
            try:
                q = self.client.queue()
                queue = {
                    "running": len(q.get("queue_running") or []),
                    "pending": len(q.get("queue_pending") or []),
                }
            except Exception:
                pass
        active = self.active_jobs()
        pid = self._read_pid()
        return {
            "ok": True,
            "comfy": {
                "url": self.settings["comfy"]["url"],
                "up": up,
                "version": (stats or {}).get("system", {}).get("comfyui_version") if stats else None,
                "queue": queue,
                "pid": pid,
                "pid_alive": self._pid_alive(pid),
                "error": err,
            },
            "hardware": hw,
            "guards": self.settings["safety"],
            "active_jobs": active[:3],
            "log": LOG_PATH,
        }

    def tool_hardware(self) -> dict:
        hw = self._safety_mod.hardware()
        profiles = self.settings["generation"]["profiles"]
        return {
            "ok": True,
            "hardware": hw,
            "profiles": profiles,
            "default_profile": self.settings["generation"]["default_profile"],
            "notes": [
                "8GB VRAM 机器：draft 档最稳；standard 需约 20-40 分钟；high 有 OOM/交换风险。",
                "生成前会用 Guard.preflight 检查空闲 RAM/磁盘/队列，默认单飞。",
                "实测(2026-11-08)：首次跑 H3 时 TE 14.6GB + UNet 19.5GB 动态加载约 16 分钟，空闲 RAM 从 19GB 降到 1.9GB，采样初始化 7 分钟后仍未完成第 1 步，第 23.5 分钟被安全中止。",
                "建议：start(mode='lowmem')（--lowvram --cache-none --reserve-vram 0.8）；生成前关掉浏览器等大内存程序；模型常驻后不要重启 ComfyUI。",
            ],
        }

    def tool_start(self, wait_s: int = 90, mode: str = "normal") -> dict:
        wait_s = max(5, min(int(wait_s), 110))
        if self.client.is_up():
            return {"ok": True, "already_running": True, "url": self.settings["comfy"]["url"]}
        profiles = self.settings["comfy"].get("start_profiles") or {"normal": []}
        if mode not in profiles:
            return {"ok": False, "error": f"未知启动档位 {mode}，可选: {list(profiles)}"}
        args = list(self.settings["comfy"]["start_args"]) + list(profiles.get(mode) or [])
        stdout_path = os.path.join(config.LOGS_DIR, "comfy_stdout.log")
        stderr_path = os.path.join(config.LOGS_DIR, "comfy_stderr.log")
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
        cmd = [config.COMFY_PYTHON, "-s", config.COMFY_MAIN] + list(args)
        logger.info("启动 ComfyUI(%s): %s", mode, " ".join(cmd))
        with open(stdout_path, "ab") as out, open(stderr_path, "ab") as err:
            proc = subprocess.Popen(
                cmd,
                cwd=config.COMFY_PORTABLE_ROOT,
                stdout=out,
                stderr=err,
                env=env,
                creationflags=creationflags,
                close_fds=True,
            )
        self._write_pid(proc.pid)
        deadline = time.time() + wait_s
        while time.time() < deadline:
            if self.client.is_up():
                return {
                    "ok": True,
                    "started": True,
                    "pid": proc.pid,
                    "url": self.settings["comfy"]["url"],
                    "stdout": stdout_path,
                    "stderr": stderr_path,
                }
            if proc.poll() is not None:
                tail = _tail(stderr_path, 4000)
                return {
                    "ok": False,
                    "error": f"ComfyUI 进程已退出（exit={proc.returncode}），常见原因：GBK 编码 emoji 崩溃（需 PYTHONUTF8=1）或缺少依赖。",
                    "stderr_tail": tail,
                    "stdout_tail": _tail(stdout_path, 2000),
                }
            time.sleep(1.5)
        return {
            "ok": True,
            "started": False,
            "still_starting": True,
            "pid": proc.pid,
            "hint": "仍在启动（首次加载自定义节点可能 1-3 分钟），稍后用 status 复查。",
            "stderr": stderr_path,
        }

    def tool_stop(self, force: bool = False) -> dict:
        active = self.active_jobs()
        if active and not force:
            return {
                "ok": False,
                "error": "仍有排队/运行中的任务，拒绝停止（force=true 可强制）。",
                "active_jobs": active,
            }
        pid = self._read_pid()
        if not pid:
            return {
                "ok": False,
                "error": "没有由本服务启动的 ComfyUI 进程记录（可能是别的启动器启动的，请手动关闭）。",
                "up": self.client.is_up(),
            }
        if not self._pid_alive(pid):
            self._write_pid(None)
            return {"ok": True, "stopped": False, "note": "记录的进程已不存在。"}
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=30)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"taskkill 失败: {exc}"}
        self._write_pid(None)
        for _ in range(10):
            if not self.client.is_up():
                break
            time.sleep(0.5)
        return {"ok": True, "stopped": True, "up": self.client.is_up()}

    def tool_free(self, unload_models: bool = True, free_memory: bool = True) -> dict:
        if not self.client.is_up():
            return {"ok": False, "error": "ComfyUI 未运行。"}
        self.client.free(unload_models=bool(unload_models), free_memory=bool(free_memory))
        time.sleep(1.0)
        hw = self._safety_mod.hardware()
        return {"ok": True, "called_free": True, "hardware_after": hw}

    def tool_templates(self) -> dict:
        workflow = self._workflow()
        return {"ok": True, "templates": workflow.list_templates()}

    def tool_handraw_prompt(self, styles: list[str], colors: list[str] | None = None,
                            theme: str = "", lang: str = "zh") -> dict:
        """基于 handraw-style 规则生成/优化提示词（不调用图像模型）。"""
        import handraw
        try:
            return {"ok": True, **handraw.build_prompt(styles, colors or [], theme, lang)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def tool_handraw_agent(self, styles: list[str], colors: list[str] | None = None,
                          theme: str = "", lang: str = "zh", key: str = "") -> dict:
        """调用 DeepSeek 基于 handraw-style 元数据优化提示词（需配置 Key）。"""
        import agent as agent_mod
        import handraw
        try:
            style = handraw.resolve_style(styles[0]) if styles else None
            if not style:
                return {"ok": False, "error": "style is required"}
            color = handraw.resolve_color(colors[0]) if colors else None
            return {"ok": True, **agent_mod.optimize(theme, style, color, lang, key)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def tool_plan(self, template: str, params: dict | None = None) -> dict:
        workflow = self._workflow()
        params = dict(params or {})
        profile = params.get("profile") or self.settings["generation"]["default_profile"]
        params.setdefault("profile", profile)
        try:
            resolved_pkg = workflow.resolve(template, params)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"参数解析失败: {type(exc).__name__}: {exc}")
        api = resolved_pkg["api"]
        validation = workflow.validate(api, self.object_info())
        estimate = self._safety_mod.estimate_cost(resolved_pkg["resolved"])
        active = self.active_jobs()
        decision = self.guard.preflight(resolved_pkg["resolved"], active_jobs=len(active))
        classes = sorted({n["class_type"] for n in api.values()})
        return {
            "ok": True,
            "dry_run": True,
            "template": template,
            "resolved": resolved_pkg["resolved"],
            "warnings": resolved_pkg.get("warnings", []),
            "validation": validation,
            "estimate": estimate,
            "guard": decision,
            "active_jobs": active,
            "api": {"node_count": len(api), "classes": classes},
        }

    def tool_generate(
        self,
        template: str,
        params: dict | None = None,
        allow_risky: bool = False,
        wait: bool = False,
        wait_s: int = 60,
    ) -> dict:
        workflow = self._workflow()
        params = dict(params or {})
        hw = self._safety_mod.hardware()
        default_profile = self.settings["generation"]["default_profile"]
        # 低内存时自动降档，避免直接冲 high
        if not any(k in params for k in ("profile", "width", "height", "megapixels", "duration_s", "length")):
            ram_free = float(hw.get("ram_free_gb") or 0)
            if ram_free < 10.0 and default_profile != "draft":
                params["profile"] = "draft"
                logger.warning("空闲 RAM %.1fGB 偏低，自动降到 draft 档", ram_free)
            else:
                params["profile"] = default_profile
        try:
            resolved_pkg = workflow.resolve(template, params)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"参数解析失败: {type(exc).__name__}: {exc}")
        api = resolved_pkg["api"]
        validation = workflow.validate(api, self.object_info())
        if not validation.get("ok") and any("LoadImage" in str(e) for e in (validation.get("errors") or [])):
            # object_info 可能缓存了旧 input 目录列表（例如页面刚上传的参考图/首帧），刷新后重试一次
            self._object_info_cache = None
            validation = workflow.validate(api, self.object_info())
        if not validation.get("ok"):
            return {"ok": False, "blocked": True, "reason": "workflow_validation_failed", "validation": validation}

        active = self.active_jobs()
        single_flight = bool(self.settings["safety"].get("single_flight", True))
        if single_flight and active:
            return {
                "ok": False,
                "busy": True,
                "reason": "single_flight",
                "active_jobs": active,
                "hint": "等待当前任务结束或 cancel 它；确需并发把 data/settings.json 的 safety.single_flight 设为 false。",
            }
        if not active:
            self._maybe_free_ram()
        decision = self.guard.preflight(resolved_pkg["resolved"], active_jobs=len(active))
        if not decision.get("ok") and not allow_risky:
            return {"ok": False, "blocked": True, "reason": "resource_guard", "guard": decision, "hardware": hw}

        lock = job_lock.acquire("mcp", template)
        if not lock.get("ok"):
            return {"ok": False, "busy": True, "reason": "global_busy",
                    "error": lock.get("message"), "message": lock.get("message"),
                    "holder": lock.get("holder")}

        # 上传本地参考素材（绝对路径）并回填 LoadImage 节点
        uploads = []
        try:
            uploads = self._apply_uploads(api)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"素材上传失败: {type(exc).__name__}: {exc}"}

        # 模型切换策略：不同模型族先释放全部模型（不动服务/隧道）
        policy_info = model_policy.maybe_release(
            lambda: self.client.free(unload_models=True, free_memory=True),
            self.client.system_stats,
            template,
            str(resolved_pkg["resolved"].get("kind") or ""),
            params,
        )
        if policy_info.get("released"):
            logger.info("模型切换释放: %s -> %s", policy_info.get("from"), policy_info.get("to"))

        prompt_id = str(uuid.uuid4())  # ComfyUI 要求规范的小写带连字符 UUID
        try:
            resp = self.client.prompt(api, prompt_id=prompt_id)
        except Exception as exc:  # noqa: BLE001
            job_lock.release()
            return {
                "ok": False,
                "error": f"提交失败: {type(exc).__name__}: {exc}",
                "validation": validation,
            }
        job_id = resp.get("prompt_id") or prompt_id
        job_lock.bind(job_id)
        self._cleaner_start(job_id)
        self._watch_lock(job_id)
        model_policy.mark_done(template, str(resolved_pkg["resolved"].get("kind") or ""), params)
        record = {
            "id": job_id,
            "prompt_id": job_id,
            "template": template,
            "profile": resolved_pkg["resolved"].get("profile"),
            "params": _jsonable({k: (v[:500] if isinstance(v, str) else v) for k, v in params.items()}),
            "resolved": _jsonable(resolved_pkg["resolved"]),
            "status": "queued",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "outputs": [],
            "guard_trip": None,
            "uploads": uploads,
            "warnings": resolved_pkg.get("warnings", []),
            "policy": policy_info,
        }
        try:
            self.store.add(record)
        except Exception as exc:  # noqa: BLE001
            logger.error("任务落盘失败: %s", exc)
        try:
            self.guard.watch(job_id, on_trip=self._on_guard_trip)
        except Exception as exc:  # noqa: BLE001
            logger.warning("守卫线程启动失败: %s", exc)
        self._sync_job(job_id)
        result = {
            "ok": True,
            "job_id": job_id,
            "prompt_id": job_id,
            "template": template,
            "resolved": resolved_pkg["resolved"],
            "guard": decision,
            "uploads": uploads,
            "hint": "用 job/wait 查询进度；完成后 job 返回输出绝对路径。",
        }
        if wait:
            result["wait"] = self.tool_wait(job_id, timeout_s=wait_s)
        return result

    def _maybe_free_ram(self) -> None:
        """空闲且物理内存很低时卸载模型，避免下一次任务被 RAM 守卫打断。"""
        try:
            hw = self._safety_mod.hardware()
            free = float(hw.get("ram_free_gb") or -1)
            if 0 <= free < 2.5:
                self.client.free(unload_models=True, free_memory=True)
                logger.warning("空闲 RAM %.2fGB 偏低，已卸载 ComfyUI 模型释放内存", free)
        except Exception as exc:  # noqa: BLE001
            logger.warning("空闲释放失败: %s", exc)

    def _cleaner_start(self, job_id: str) -> None:
        """启动进程清理线程，并把 job_id 作为 owned 标记（只登记，不杀进程）。"""
        try:
            info = proc_cleaner.start_for_job(job_id, job_id=job_id)
            logger.debug("进程清理启动: %s", info)
        except Exception as exc:  # noqa: BLE001 - 清理器故障不能影响生成
            logger.warning("进程清理启动失败 %s: %s", job_id, exc)

    def _cleaner_finish(self, job_id: str) -> None:
        """任务收尾：停止清理线程，只强杀本项目登记的 encoder（幂等）。"""
        try:
            info = proc_cleaner.finish_job(job_id, job_id=job_id)
            logger.debug("进程清理收尾: %s", info)
        except Exception as exc:  # noqa: BLE001
            logger.warning("进程清理收尾失败 %s: %s", job_id, exc)

    def _watch_lock(self, job_id: str) -> None:
        """后台等待任务结束并释放全局锁（即使没人轮询 job）。"""
        def run() -> None:
            deadline = time.time() + 6 * 3600
            while time.time() < deadline:
                try:
                    remote = self._remote_job(job_id) or {}
                    status = remote.get("status") or ""
                    if status in ("completed", "failed", "cancelled"):
                        self._cleaner_finish(job_id)
                        job_lock.release(job_id)
                        return
                except Exception:
                    pass
                time.sleep(5)
            self._cleaner_finish(job_id)
            job_lock.release(job_id)

        try:
            threading.Thread(target=run, name="lock-watch", daemon=True).start()
        except Exception:
            pass

    def _on_guard_trip(self, job_id: str, reason: str) -> None:
        logger.warning("守卫触发 %s: %s", job_id, reason)
        try:
            self.store.update(job_id, guard_trip=reason, updated_at=_now_iso())
        except Exception:
            pass
        self._cleaner_finish(job_id)

    def _apply_uploads(self, api: dict) -> list[dict]:
        """把 API 里 LoadImage 的本地绝对路径上传到 ComfyUI input，并替换成文件名。"""
        uploads = []
        for node_id, node in api.items():
            if node.get("class_type") != "LoadImage":
                continue
            image = (node.get("inputs") or {}).get("image")
            if not isinstance(image, str) or not image:
                continue
            candidate = image
            if not os.path.isabs(candidate):
                candidate = os.path.abspath(candidate)
            if os.path.isfile(candidate):
                info = self.client.upload_image(candidate)
                node["inputs"]["image"] = info.get("name") or os.path.basename(candidate)
                uploads.append({"node": node_id, "src": candidate, "name": node["inputs"]["image"]})
        return uploads

    def _remote_job(self, job_id: str) -> dict | None:
        try:
            return self.client.job(job_id)
        except Exception:
            return None

    def _progress(self, remote: dict | None) -> dict:
        if not remote:
            return {}
        status = (remote.get("execution_status") or {})
        messages = status.get("messages") or []
        events = [m[0] for m in messages if isinstance(m, (list, tuple)) and m]
        started = remote.get("execution_start_time")
        ended = remote.get("execution_end_time")
        elapsed = None
        if started:
            end_ms = ended or int(time.time() * 1000)
            elapsed = round((end_ms - started) / 1000.0, 1)
        return {
            "status_str": status.get("status_str"),
            "completed": status.get("completed"),
            "event_count": len(events),
            "last_event": events[-1] if events else None,
            "elapsed_s": elapsed,
        }

    def _resolve_outputs(self, job_id: str) -> list[dict]:
        out = []
        try:
            items = self.client.job_outputs(job_id)
        except Exception:
            items = []
        for it in items or []:
            filename = it.get("filename")
            subfolder = it.get("subfolder") or ""
            ftype = it.get("type") or "output"
            if not filename:
                continue
            base = config.OUTPUT_DIR if ftype == "output" else os.path.join(config.COMFY_ROOT, ftype)
            abs_path = os.path.join(base, subfolder, filename)
            out.append(
                {
                    **it,
                    "abs_path": abs_path,
                    "exists": os.path.isfile(abs_path),
                    "size_mb": round(os.path.getsize(abs_path) / 1048576, 2) if os.path.isfile(abs_path) else None,
                }
            )
        return out

    def _sync_job(self, job_id: str) -> dict | None:
        record = None
        try:
            record = self.store.get(job_id)
        except Exception:
            record = None
        remote = self._remote_job(job_id)
        if remote:
            status = remote.get("status") or "queued"
            mapping = {
                "pending": "queued",
                "in_progress": "running",
                "completed": "completed",
                "failed": "failed",
            }
            local_status = mapping.get(status, status)
            fields = {"status": local_status, "updated_at": _now_iso()}
            if local_status == "running" and record and not record.get("started_at"):
                fields["started_at"] = _now_iso()
            if local_status in ("completed", "failed", "cancelled"):
                fields["finished_at"] = _now_iso()
                job_lock.release(job_id)
                previous_status = str((record or {}).get("status") or "")
                if previous_status not in ("completed", "failed", "cancelled", "aborted"):
                    self._cleaner_finish(job_id)
                outputs = self._resolve_outputs(job_id)
                fields["outputs"] = [
                    {"path": o["abs_path"], "size_mb": o["size_mb"], "type": o.get("type")}
                    for o in outputs
                ]
                if local_status == "failed":
                    fields["error"] = (remote.get("execution_status") or {}).get("status_str") or "failed"
            if record is None:
                record = {
                    "id": job_id,
                    "prompt_id": job_id,
                    "template": None,
                    "status": local_status,
                    "created_at": _now_iso(),
                    "outputs": [],
                }
                self.store.add(record)
                record = self.store.get(job_id) or record
            try:
                record = self.store.update(job_id, **fields)
            except Exception:
                if record is not None:
                    record.update(fields)
        elif record is None:
            return None
        return record

    def tool_job(self, job_id: str) -> dict:
        record = self._sync_job(job_id)
        if record is None:
            return {"ok": False, "error": f"未知任务: {job_id}", "hint": "用 jobs 查看本地记录。"}
        remote = self._remote_job(job_id)
        # 资源守卫兜底：MCP 进程重启会丢 watchdog，查询任务时补一次 RAM 检查
        if remote and remote.get("status") in ("in_progress", "pending"):
            try:
                hw = self._safety_mod.hardware()
                trip_gb = float(self.settings.get("safety", {}).get("trip_ram_free_gb", 3.5))
                if float(hw.get("ram_free_gb") or 99.0) < trip_gb:
                    reason = f"RAM 空闲 {hw.get('ram_free_gb')}GB < trip {trip_gb}GB，自动中止"
                    logger.warning("守卫触发（job 查询）%s: %s", job_id, reason)
                    try:
                        self.client.cancel(job_id)
                    except Exception:
                        pass
                    try:
                        self.store.update(
                            job_id, status="aborted", error=reason, guard_trip=reason,
                            finished_at=_now_iso(), updated_at=_now_iso(),
                        )
                        record = self.store.get(job_id) or record
                        remote = self._remote_job(job_id)
                    except Exception:
                        pass
            except Exception:
                pass
        outputs = self._resolve_outputs(job_id) if remote and remote.get("status") in ("completed", "failed") else []
        if not outputs and record.get("outputs"):
            outputs = [{"abs_path": o.get("path"), "size_mb": o.get("size_mb"), "type": o.get("type")} for o in record["outputs"]]
        return {
            "ok": True,
            "job": {
                "id": record.get("id"),
                "template": record.get("template"),
                "status": record.get("status"),
                "profile": record.get("profile"),
                "created_at": record.get("created_at"),
                "started_at": record.get("started_at"),
                "finished_at": record.get("finished_at"),
                "error": record.get("error"),
                "guard_trip": record.get("guard_trip"),
                "outputs": outputs,
            },
            "progress": self._progress(remote),
            "remote_status": (remote or {}).get("status"),
            "preview": (remote or {}).get("preview_output"),
        }

    def tool_jobs(self, limit: int = 20, status: list[str] | None = None) -> dict:
        limit = max(1, min(int(limit), 100))
        items = self.store.list(limit=limit, status=status)
        return {
            "ok": True,
            "jobs": [
                {
                    "id": j.get("id"),
                    "template": j.get("template"),
                    "status": j.get("status"),
                    "profile": j.get("profile"),
                    "created_at": j.get("created_at"),
                    "finished_at": j.get("finished_at"),
                    "outputs": [o.get("path") for o in (j.get("outputs") or [])],
                    "guard_trip": j.get("guard_trip"),
                }
                for j in items
            ],
        }

    def tool_cancel(self, job_id: str) -> dict:
        try:
            resp = self.client.cancel(job_id)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"取消失败: {type(exc).__name__}: {exc}"}
        try:
            self.store.update(job_id, status="cancelled", finished_at=_now_iso(), updated_at=_now_iso())
        except Exception:
            pass
        try:
            self.guard.stop_watch()
        except Exception:
            pass
        self._cleaner_finish(job_id)
        job_lock.release(job_id)
        return {"ok": True, "cancel_response": resp, "job_id": job_id}

    def tool_wait(self, job_id: str, timeout_s: int = 60) -> dict:
        timeout_s = max(1, min(int(timeout_s), 45))
        deadline = time.time() + timeout_s
        last = None
        trip_gb = float(self.settings.get("safety", {}).get("trip_ram_free_gb", 2.5))
        while True:
            # wait 期间也做资源守卫：RAM 触底就中止，避免整机卡死（CLI 模式下唯一的安全网）
            try:
                hw = self._safety_mod.hardware()
                if float(hw.get("ram_free_gb") or 99) < trip_gb:
                    reason = f"RAM 空闲 {hw.get('ram_free_gb')}GB < trip {trip_gb}GB，自动中止"
                    logger.warning("守卫触发（wait）%s: %s", job_id, reason)
                    try:
                        self.client.cancel(job_id)
                    except Exception:
                        pass
                    try:
                        self.store.update(
                            job_id, status="aborted", error=reason,
                            finished_at=_now_iso(), updated_at=_now_iso(),
                        )
                    except Exception:
                        pass
                    return {
                        "ok": True,
                        "aborted": True,
                        "reason": reason,
                        "job": self.store.get(job_id) if self.store else None,
                        "hardware": hw,
                    }
            except Exception:
                pass
            info = self.tool_job(job_id)
            last = info
            status = (info.get("job") or {}).get("status")
            if not info.get("ok") or status in ("completed", "failed", "cancelled", "aborted"):
                return info
            if time.time() >= deadline:
                return {
                    "ok": True,
                    "timed_out": True,
                    "job": info.get("job"),
                    "progress": info.get("progress"),
                    "hint": "任务仍在进行，稍后再次 wait / job。长任务（standard/high）可能 20-60 分钟。",
                }
            time.sleep(2.0)

    def tool_upload(self, paths: list[str], subfolder: str = "") -> dict:
        if isinstance(paths, str):
            paths = [paths]
        uploaded = []
        for p in paths or []:
            if not os.path.isfile(p):
                uploaded.append({"src": p, "ok": False, "error": "文件不存在"})
                continue
            try:
                info = self.client.upload_image(p, subfolder=subfolder)
                uploaded.append({"src": p, "ok": True, **info})
            except Exception as exc:  # noqa: BLE001
                uploaded.append({"src": p, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return {"ok": all(u.get("ok") for u in uploaded), "uploads": uploaded}

    # ---------------------------------------------------------------- 剪辑
    _EDIT_EXT = {
        "trim": ".mp4", "concat": ".mp4", "transform": ".mp4", "mux_audio": ".mp4",
        "burn_subtitles": ".mp4", "overlay": ".mp4", "speed": ".mp4",
        "extract_audio": ".m4a", "thumbnail": ".jpg", "to_gif": ".gif",
    }

    def tool_edit(self, action: str, **kwargs) -> dict:
        editing = self._editing()
        fn = getattr(editing, action, None)
        if fn is None or not callable(fn):
            raise ToolError(f"未知剪辑动作: {action}；可用: trim/concat/transform/mux_audio/burn_subtitles/overlay/speed/extract_frames/extract_audio/thumbnail/to_gif")
        kwargs = {k: v for k, v in kwargs.items() if v is not None}
        # 统一首参：部分 action 的规范签名用 video，工具层统一用 src
        if "src" in kwargs and "video" not in kwargs:
            try:
                import inspect as _inspect

                if "video" in _inspect.signature(fn).parameters:
                    kwargs["video"] = kwargs.pop("src")
            except Exception:
                pass
        if action == "extract_frames":
            out_dir = kwargs.get("out_dir") or os.path.join(config.ARTIFACTS_DIR, "frames", time.strftime("%Y%m%d_%H%M%S"))
            kwargs["out_dir"] = out_dir
        else:
            if not kwargs.get("dst"):
                stamp = time.strftime("%Y%m%d_%H%M%S")
                ext = self._EDIT_EXT.get(action, ".mp4")
                kwargs["dst"] = os.path.join(config.ARTIFACTS_DIR, "edited", f"{action}_{stamp}{ext}")
        os.makedirs(os.path.dirname(kwargs["dst"]) if action != "extract_frames" else kwargs["out_dir"], exist_ok=True)
        try:
            return fn(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"{type(exc).__name__}: {exc}")

    def tool_probe_media(self, path: str) -> dict:
        editing = self._editing()
        try:
            return {"ok": True, "probe": editing.probe(path)}
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"{type(exc).__name__}: {exc}")

    # -------------------------------------------------------------- settings
    def tool_settings(self, action: str = "get", values: dict | None = None) -> dict:
        if action == "get":
            return {"ok": True, "settings": self.settings, "path": config.SETTINGS_PATH}
        if action == "set":
            if not isinstance(values, dict):
                raise ToolError("values 必须是对象")
            merged = _deep_merge(self.settings, values)
            config.save_settings(merged)
            self.settings = config.load_settings()
            self.guard = self._safety_mod.Guard(self.client, self.settings)
            return {"ok": True, "updated": True, "settings": self.settings, "path": config.SETTINGS_PATH}
        raise ToolError("action 只支持 get/set")

    # -------------------------------------------------------------- self-check
    @staticmethod
    def _doctor_module():
        """惰性导入 setup.doctor（MCP/CLI/webapp 三种加载方式都兼容）。"""
        for name in ("setup.doctor", "comfy_mcp.setup.doctor"):
            try:
                return importlib.import_module(name)
            except Exception:
                continue
        return None

    def tool_selfcheck(self, force: bool = False, style: str | None = None) -> dict:
        """环境自检：Python / ComfyUI / 资源 / ffmpeg / 模型 / 写权限 + 非阻塞告警。"""
        doctor = self._doctor_module()
        if doctor is None:
            raise ToolError("自检模块不可用（setup/doctor.py 缺失）")
        try:
            return doctor.check(force=bool(force), style=style)
        except Exception as exc:  # noqa: BLE001 - 体检失败也要给出可读错误
            raise ToolError(f"自检执行失败: {type(exc).__name__}: {exc}")

    def tool_logs(self, kind: str = "mcp", tail: int = 120) -> dict:
        tail = max(10, min(int(tail), 1000))
        files = {
            "mcp": LOG_PATH,
            "comfy_stdout": os.path.join(config.LOGS_DIR, "comfy_stdout.log"),
            "comfy_stderr": os.path.join(config.LOGS_DIR, "comfy_stderr.log"),
        }
        path = files.get(kind)
        if not path:
            raise ToolError(f"kind 支持: {list(files)}")
        return {"ok": True, "kind": kind, "path": path, "tail": _tail(path, tail, by_lines=True)}


def _deep_merge(base: dict, override: dict) -> dict:
    import copy as _copy

    out = _copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _tail(path: str, n: int, by_lines: bool = False) -> str:
    try:
        if by_lines:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            return "".join(lines[-n:])
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - n))
            data = f.read()
        return data.decode("utf-8", errors="replace")
    except OSError as exc:
        return f"<无法读取 {path}: {exc}>"


# --------------------------------------------------------------------- 装配
def build_server() -> tuple[McpServer, ComfyService]:
    service = ComfyService()
    srv = McpServer("comfy-mcp", "1.0.0", log=lambda *a: logger.debug(" ".join(str(x) for x in a)))

    def args_schema(properties: dict, required: list[str] | None = None) -> dict:
        return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}

    srv.tool(
        "status",
        "ComfyUI 运行状态：地址/版本/队列/进程、硬件（VRAM/RAM/磁盘）、守卫阈值、活动任务。任何操作前先调用。",
        args_schema({}),
    )(service.tool_status)

    srv.tool("hardware", "硬件与推荐生成档位（draft/standard/high）。", args_schema({}))(service.tool_hardware)

    srv.tool(
        "start",
        "若 ComfyUI 未运行则用安全参数启动便携版（UTF-8 环境，避免 GBK emoji 崩溃），最多等待 wait_s 秒。"
        "低内存机器用 mode=lowmem（--lowvram --cache-none --reserve-vram 0.8）更稳。",
        args_schema(
            {
                "wait_s": {"type": "integer", "minimum": 5, "maximum": 110, "default": 90},
                "mode": {
                    "type": "string",
                    "enum": ["normal", "lowmem", "vram_only"],
                    "default": "normal",
                    "description": "启动档位；lowmem 更省内存但更慢",
                },
            }
        ),
    )(service.tool_start)

    srv.tool(
        "stop",
        "停止由本服务启动的 ComfyUI（有活动任务时默认拒绝）。",
        args_schema({"force": {"type": "boolean", "default": False}}),
    )(service.tool_stop)

    srv.tool(
        "free",
        "请求 ComfyUI 卸载模型/释放显存（生成间隔或切模型前用）。",
        args_schema(
            {
                "unload_models": {"type": "boolean", "default": True},
                "free_memory": {"type": "boolean", "default": True},
            }
        ),
    )(service.tool_free)

    srv.tool(
        "templates",
        "列出内置视频工作流模板（minimax_h3_r2v / fl2v / t2v）及参数面。",
        args_schema({}),
    )(service.tool_templates)

    srv.tool(
        "handraw_prompt",
        "基于已安装的 handraw-style Skill 生成/优化生图提示词（372 风格 + 36 主题色），不提交生成。",
        args_schema(
            {
                "styles": {"type": "array", "items": {"type": "string"}, "description": "风格编号，如 FA-001"},
                "colors": {"type": "array", "items": {"type": "string"}, "description": "主题色编号，如 C-01"},
                "theme": {"type": "string", "description": "画面主题"},
                "lang": {"type": "string", "default": "zh"},
            },
            ["styles"],
        ),
    )(service.tool_handraw_prompt)

    srv.tool(
        "handraw_agent",
        "调用 DeepSeek + handraw-style 优化生图提示词；Key 来自参数/环境变量/本地配置。",
        args_schema(
            {
                "styles": {"type": "array", "items": {"type": "string"}},
                "colors": {"type": "array", "items": {"type": "string"}},
                "theme": {"type": "string"},
                "lang": {"type": "string", "default": "zh"},
                "key": {"type": "string", "description": "可选；留空使用已保存 Key"},
            },
            ["styles"],
        ),
    )(service.tool_handraw_agent)

    srv.tool(
        "plan",
        "干跑：解析参数 → 生成 API 工作流 → object_info 校验 → 成本/风险估算 → 资源守卫。不排队。",
        args_schema(
            {
                "template": {"type": "string", "description": "模板名，如 minimax_h3_r2v"},
                "params": {"type": "object", "description": "prompt / profile / megapixels / duration_s / seed / ref_images 等"},
            },
            ["template"],
        ),
    )(service.tool_plan)

    srv.tool(
        "generate",
        "提交视频生成任务（默认单飞 + 资源守卫）。立即返回 job_id，不阻塞；用 job/wait 查询。",
        args_schema(
            {
                "template": {"type": "string"},
                "params": {
                    "type": "object",
                    "description": "必填 prompt；可选 profile(draft/standard/high)、megapixels、aspect、duration_s、seed、steps、ref_images(本地路径或已上传文件名)、first_frame/last_frame、output_prefix 等",
                },
                "allow_risky": {"type": "boolean", "default": False, "description": "守卫判定风险高时仍强制提交（不推荐）"},
                "wait": {"type": "boolean", "default": False, "description": "提交后顺便等待 wait_s 秒"},
                "wait_s": {"type": "integer", "minimum": 1, "maximum": 45, "default": 30},
            },
            ["template", "params"],
        ),
    )(service.tool_generate)

    srv.tool(
        "job",
        "查询单个任务：本地状态 + ComfyUI 远端状态 + 进度 + 输出绝对路径。",
        args_schema({"job_id": {"type": "string"}}, ["job_id"]),
    )(service.tool_job)

    srv.tool(
        "jobs",
        "列出本地任务记录（可按状态过滤）。",
        args_schema(
            {
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                "status": {"type": "array", "items": {"type": "string"}, "description": "queued/running/completed/failed/cancelled"},
            }
        ),
    )(service.tool_jobs)

    srv.tool(
        "cancel",
        "取消排队中或运行中的任务（/api/jobs/{id}/cancel，幂等）。",
        args_schema({"job_id": {"type": "string"}}, ["job_id"]),
    )(service.tool_cancel)

    srv.tool(
        "wait",
        "阻塞等待任务结束，最多 110 秒；超时返回当前进度（长任务需多次调用）。",
        args_schema(
            {
                "job_id": {"type": "string"},
                "timeout_s": {"type": "integer", "minimum": 1, "maximum": 45, "default": 30},
            },
            ["job_id"],
        ),
    )(service.tool_wait)

    srv.tool(
        "upload",
        "上传本地图片到 ComfyUI input 目录（参考图/首尾帧可直接在 params 传绝对路径，generate 会自动上传）。",
        args_schema(
            {
                "paths": {"type": "array", "items": {"type": "string"}},
                "subfolder": {"type": "string", "default": ""},
            },
            ["paths"],
        ),
    )(service.tool_upload)

    srv.tool(
        "edit",
        "ffmpeg 剪辑：trim/concat/transform/mux_audio/burn_subtitles/overlay/speed/extract_frames/extract_audio/thumbnail/to_gif。",
        args_schema(
            {
                "action": {"type": "string"},
                "src": {"type": "string"},
                "dst": {"type": "string"},
                "inputs": {"type": "array", "items": {"type": "string"}},
                "start_s": {"type": "number"},
                "duration_s": {"type": "number"},
                "end_s": {"type": "number"},
                "factor": {"type": "number"},
                "width": {"type": "integer"},
                "height": {"type": "integer"},
                "fps": {"type": "number"},
                "pad": {"type": "boolean"},
                "crossfade_s": {"type": "number"},
                "reencode": {"type": "boolean"},
                "audio": {"type": "string"},
                "volume": {"type": "number"},
                "offset_s": {"type": "number"},
                "replace": {"type": "boolean"},
                "normalize": {"type": "boolean"},
                "srt_path": {"type": "string"},
                "font_size": {"type": "integer"},
                "margin_v": {"type": "integer"},
                "image_path": {"type": "string"},
                "position": {"type": "string"},
                "margin": {"type": "integer"},
                "opacity": {"type": "number"},
                "scale": {"type": "number"},
                "at_s": {"type": "number"},
                "out_dir": {"type": "string"},
                "prefix": {"type": "string"},
                "keep_audio": {"type": "boolean"},
            },
            ["action"],
        ),
    )(service.tool_edit)

    srv.tool(
        "probe_media",
        "ffprobe 读取视频/音频元信息（分辨率、帧率、时长、编码、是否含音轨）。",
        args_schema({"path": {"type": "string"}}, ["path"]),
    )(service.tool_probe_media)

    srv.tool(
        "selfcheck",
        "环境自检（不要求 ComfyUI 正在运行）：Python / ComfyUI main.py / web 资源 / ffmpeg / 已选模型 / data 写权限；"
        "模板目录、torch+CUDA、8188 端口只做非阻塞告警。",
        args_schema(
            {
                "force": {"type": "boolean", "default": False, "description": "跳过 20s 结果缓存"},
                "style": {
                    "type": "string",
                    "enum": ["easy", "pro"],
                    "description": "easy=大白话，pro=技术细节",
                },
            }
        ),
    )(service.tool_selfcheck)

    srv.tool(
        "settings",
        "读取或修改 data/settings.json（守卫阈值、档位、ffmpeg 等）。",
        args_schema(
            {
                "action": {"type": "string", "enum": ["get", "set"], "default": "get"},
                "values": {"type": "object"},
            }
        ),
    )(service.tool_settings)

    srv.tool(
        "logs",
        "读取日志尾部：mcp / comfy_stdout / comfy_stderr。",
        args_schema(
            {
                "kind": {"type": "string", "enum": ["mcp", "comfy_stdout", "comfy_stderr"], "default": "mcp"},
                "tail": {"type": "integer", "minimum": 10, "maximum": 1000, "default": 120},
            }
        ),
    )(service.tool_logs)

    return srv, service


# ----------------------------------------------------------------------- CLI
def _main(argv: list[str]) -> int:
    if "--list-tools" in argv:
        srv, _ = build_server()
        print(json.dumps([srv._public_tool(t) for t in srv._tools.values()], ensure_ascii=False, indent=2))
        return 0
    if "--call" in argv:
        i = argv.index("--call")
        try:
            name = argv[i + 1]
        except IndexError:
            print("用法: --call <tool> [--args '<json>']", file=sys.stderr)
            return 2
        args = {}
        if "--args-file" in argv:
            k = argv.index("--args-file")
            with open(argv[k + 1], "r", encoding="utf-8") as f:
                args = json.load(f)
        elif "--args" in argv:
            j = argv.index("--args")
            args = json.loads(argv[j + 1]) if j + 1 < len(argv) else {}
        srv, service = build_server()
        tool = srv._tools.get(name)
        if tool is None:
            print(f"未知工具 {name}; 可用: {list(srv._tools)}", file=sys.stderr)
            return 2
        try:
            result = tool["handler"](**args)
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()}, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if (not isinstance(result, dict) or result.get("ok", True)) else 1
    if "--selftest" in argv:
        srv, service = build_server()
        print(json.dumps({"tools": srv.tool_names(), "log": LOG_PATH}, ensure_ascii=False, indent=2))
        print(json.dumps(service.tool_status(), ensure_ascii=False, indent=2, default=str))
        try:
            print(json.dumps(service.tool_selfcheck(), ensure_ascii=False, indent=2, default=str))
        except Exception as exc:  # noqa: BLE001 - 自检失败不隐藏前面的输出
            print(json.dumps({"ok": False, "selfcheck_error": f"{type(exc).__name__}: {exc}"},
                             ensure_ascii=False, indent=2))
        return 0
    # 默认 stdio MCP
    srv, _ = build_server()
    return srv.run()


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
