"""AstrBot 插件：把聊天提示词转发到本机 ComfyUI，回传图像/视频。

本机侧提供 HTTP 桥：
  GET  /api/bridge/models    列出可用图像/视频模型与默认参数
  POST /api/bridge/generate  提交生成，返回 base64 媒体
  GET  /api/bridge/health    健康检查
"""
from __future__ import annotations

import asyncio
import base64
import json
import tempfile
import time
import uuid
from pathlib import Path

import aiohttp

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter

try:  # 兼容不同 AstrBot 版本
    from astrbot.api.event import MessageChain
except Exception:  # noqa: BLE001
    try:
        from astrbot.core.message.message_event_result import MessageChain  # type: ignore
    except Exception:  # noqa: BLE001
        MessageChain = None
from astrbot.api.star import Context, Star, register

PLUGIN_ID = "astrbot_plugin_comfy_bridge"


@register(PLUGIN_ID, "YG", "把提示词转发到本机 ComfyUI 生图/生视频，并把结果发回聊天", "1.0.0")
class ComfyBridge(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.tmp = Path(tempfile.gettempdir()) / "astrbot_comfy_bridge"
        self.tmp.mkdir(parents=True, exist_ok=True)
        self._tasks: set = set()
        self._cleanup_cache()
        try:
            loop = asyncio.get_event_loop()
            self._cleanup_task = loop.create_task(self._cleanup_loop())
        except Exception:
            self._cleanup_task = None
        logger.info("comfy_bridge v1.5 base_url=%s", self._base())
        self._prune()

    # ------------------------------------------------------------------ utils
    def _cfg(self, key: str, default=None):
        try:
            return self.config.get(key, default)
        except Exception:
            return default

    def _base(self) -> str:
        return str(self._cfg("base_url", "http://127.0.0.1:8190")).rstrip("/")

    def _headers(self) -> dict:
        return {"X-Bridge-Token": str(self._cfg("token", "")).strip()}

    def _cleanup_cache(self) -> None:
        """删除插件缓存目录中超过保留期的图片/视频。"""
        try:
            hours = float(self._cfg("cache_max_age_hours", 24) or 24)
        except Exception:
            hours = 24
        cutoff = time.time() - hours * 3600
        removed = 0
        try:
            for p in self.tmp.rglob("*"):
                try:
                    if p.is_file() and p.stat().st_mtime < cutoff:
                        p.unlink(missing_ok=True)
                        removed += 1
                except Exception:
                    continue
        except Exception:
            pass
        if removed:
            logger.info("comfy_bridge cache cleaned: %s files (>%sh)", removed, hours)

    async def _cleanup_loop(self) -> None:
        while True:
            try:
                interval = float(self._cfg("cache_cleanup_interval_hours", 6) or 6)
            except Exception:
                interval = 6
            await asyncio.sleep(max(1.0, interval) * 3600)
            self._cleanup_cache()

    async def _send_text(self, umo: str, text: str) -> None:
        try:
            chain = MessageChain().message(text)
        except Exception:
            chain = MessageChain([Comp.Plain(text)])
        await self.context.send_message(umo, chain)

    def _prune(self) -> None:
        cutoff = time.time() - 3 * 3600
        try:
            for p in self.tmp.iterdir():
                if p.is_file() and p.stat().st_mtime < cutoff:
                    p.unlink(missing_ok=True)
        except Exception:
            pass

    @staticmethod
    async def _read_json(r) -> dict:
        """读取 JSON；非 JSON（如 Cloudflare 524 HTML）返回可读错误，避免 JSONDecodeError 直抛。"""
        text = await r.text()
        try:
            return json.loads(text)
        except Exception:
            snippet = " ".join(text.split())[:200]
            return {"ok": False, "error": f"HTTP {r.status} 非 JSON 响应：{snippet or '空响应'}"}

    async def _get_json(self, path: str, timeout: int = 30) -> dict:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as s:
            async with s.get(self._base() + path, headers=self._headers()) as r:
                return await self._read_json(r)

    async def _generate(self, payload: dict, timeout: int) -> dict:
        """异步提交 + 轮询，避免 Cloudflare 100 秒超时（524）导致空/HTML 响应。"""
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
            async with s.post(self._base() + "/api/bridge/submit", json=payload,
                              headers=self._headers()) as r:
                sub = await self._read_json(r)
            if not sub.get("ok"):
                return sub
            job_id = sub.get("job_id")
            if not job_id:
                return {"ok": False, "error": "提交成功但未返回 job_id"}
            deadline = time.time() + timeout
            last = sub
            while time.time() < deadline:
                await asyncio.sleep(2)
                try:
                    async with s.get(f"{self._base()}/api/bridge/result/{job_id}",
                                     headers=self._headers()) as r2:
                        data = await self._read_json(r2)
                except Exception as exc:  # noqa: BLE001
                    last = {"ok": False, "error": f"轮询失败: {exc}"}
                    continue
                last = data
                if data.get("status") in ("done", "failed"):
                    return data
            return {"ok": False, "error": f"等待超时（>{timeout}s）", "last": last}

    @staticmethod
    def _tail(event: AstrMessageEvent, names: tuple[str, ...]) -> str:
        """取命令后的参数文本（兼容 /命令 与 命令）。"""
        text = (event.message_str or "").strip()
        for name in names:
            for prefix in (f"/{name}", name):
                if text.startswith(prefix):
                    return text[len(prefix):].strip()
        return text

    FLAG_ALIASES = {
        "model": "model", "模型": "model",
        "size": "size", "尺寸": "size",
        "steps": "steps", "步数": "steps",
        "cfg": "cfg",
        "seed": "seed", "种子": "seed",
        "neg": "neg", "negative": "neg", "负面": "neg",
        "dur": "dur", "duration": "dur", "时长": "dur",
        "length": "length", "帧数": "length",
    }

    @staticmethod
    def _parse_args(message: str, flags_with_value: set[str]) -> tuple[dict, str, str | None]:
        tokens = (message or "").split()
        flags: dict[str, str] = {}
        prompt_parts: list[str] = []
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok.startswith("--"):
                key, eq, val = tok[2:].partition("=")
                key = key.strip().lower()
                key = ComfyBridge.FLAG_ALIASES.get(key, key)
                if key not in flags_with_value:
                    return flags, "", f"未知参数 --{key}（发 /生图帮助 查看全部参数）"
                if not eq:
                    if i + 1 >= len(tokens):
                        return flags, "", f"参数 --{key} 缺少值"
                    val = tokens[i + 1]
                    i += 1
                flags[key] = val.strip()
            else:
                prompt_parts.append(tok)
            i += 1
        return flags, " ".join(prompt_parts).strip(), None

    IMAGE_WORDS = {
        "竖图": (768, 1344), "竖版": (768, 1344), "手机壁纸": (768, 1344),
        "横图": (1344, 768), "横版": (1344, 768), "宽屏": (1344, 768),
        "方图": (1024, 1024), "正方形": (1024, 1024),
    }
    VIDEO_WORDS = {
        "竖屏": (576, 1024), "横屏": (960, 544),
    }

    @staticmethod
    def _natural(text: str, kind: str) -> tuple[str, dict, list[str]]:
        """从描述里识别口语词（竖图/高清/快速/用Qwen 等），自动转参数并清理提示词。

        优先级：方向词（竖/横/方）决定尺寸；“高清/2K”在已有方向时改为提高步数/质量，
        不覆盖方向，避免“竖图高清”变成方图。
        """
        out = text or ""
        params: dict = {}
        notes: list[str] = []
        words = ComfyBridge.IMAGE_WORDS if kind == "image" else ComfyBridge.VIDEO_WORDS
        for word, size in words.items():
            if word in out:
                params["width"], params["height"] = size
                notes.append(f"{word}→{size[0]}x{size[1]}")
                out = out.replace(word, " ")
        # 高清/2K/720p
        hi_words = ["高清", "2K", "2k"] if kind == "image" else ["高清", "720p", "720P"]
        for word in hi_words:
            if word not in out:
                continue
            if "width" in params:
                params["steps"] = 30 if kind == "image" else 8
                notes.append(f"{word}→{params['steps']}步")
            else:
                size = (1536, 1536) if kind == "image" else (1280, 736)
                params["width"], params["height"] = size
                notes.append(f"{word}→{size[0]}x{size[1]}")
            out = out.replace(word, " ")
        quality = [("快速", 8 if kind == "image" else 4),
                   ("精细", 30 if kind == "image" else 8),
                   ("高质量", 30 if kind == "image" else 8)]
        for word, steps in quality:
            if word in out:
                params["steps"] = steps
                notes.append(f"{word}→{steps}步")
                out = out.replace(word, " ")
        model_words = [("用moss", "moss_image"), ("用MOSS", "moss_image"),
                       ("用qwen", "qwen_image"), ("用Qwen", "qwen_image"),
                       ("用h3", "h3_image" if kind == "image" else "minimax_h3_t2v"),
                       ("用H3", "h3_image" if kind == "image" else "minimax_h3_t2v")]
        for word, model in model_words:
            if word in out:
                params["model"] = model
                notes.append(f"{word}→{model}")
                out = out.replace(word, " ")
        if kind == "video":
            dur_words = [("短视频", 2), ("2秒", 2), ("5秒", 5), ("10秒", 10), ("15秒", 15)]
            for word, dur in dur_words:
                if word in out:
                    params["duration_s"] = dur
                    notes.append(f"{word}→{dur}s")
                    out = out.replace(word, " ")
        return " ".join(out.split()), params, notes

    @staticmethod
    def _parse_size(text: str) -> tuple[int, int] | None:
        try:
            w, h = text.lower().replace("×", "x").split("x", 1)
            w, h = int(w), int(h)
            if w % 16 or h % 16 or w < 64 or w > 4096:
                return None
            return w, h
        except Exception:
            return None

    def _save_media(self, data: dict, suffix: str) -> Path:
        self.tmp.mkdir(parents=True, exist_ok=True)
        path = self.tmp / f"{uuid.uuid4().hex}{suffix}"
        path.write_bytes(base64.b64decode(data["image_b64"]))
        return path

    def _config_summary(self) -> str:
        token = str(self._cfg("token", ""))
        masked = (token[:4] + "…" + token[-4:]) if len(token) > 8 else ("已设置" if token else "未设置")
        lines = [
            "【本机生图桥 · 当前设置】",
            f"地址：{self._base()}",
            f"Token：{masked}",
            f"默认图像模型：{self._cfg('default_image_template', 'moss_image')}",
            f"默认视频模型：{self._cfg('default_video_template', 'minimax_h3_t2v')}",
            f"默认尺寸：图像 {self._cfg('image_size') or '模型默认'} · 视频 {self._cfg('video_size') or '模型默认'}",
            f"默认步数/CFG：{self._cfg('steps', 0) or '模型默认'} / {self._cfg('cfg', 0) or '模型默认'}",
            f"默认视频时长：{self._cfg('duration_s', 5)}s",
            f"超时：图像 {self._cfg('image_timeout_s', 300)}s · 视频 {self._cfg('video_timeout_s', 1800)}s",
            f"进度提示：{'开' if self._cfg('send_progress', True) else '关'}",
            f"提示词上限：{self._cfg('max_prompt_chars', 2000)} 字符",
            "在 AstrBot WebUI 插件配置里可修改以上项。",
        ]
        return "\n".join(lines)

    @staticmethod
    def _image_usage() -> str:
        return ("【图像 /画图】\n"
                "用法：/画图 [参数] 提示词\n"
                "  --model  模型名（默认 moss_image）\n"
                "           可选：moss_image / qwen_image / h3_image / moss_image_edit / qwen_image_edit\n"
                "  --size   宽x高（默认模型默认，16 的倍数，如 1024x1024）\n"
                "  --steps  步数（默认模型默认；MOSS/Qwen 25，编辑 30）\n"
                "  --cfg    CFG（MOSS 2.5，MOSS 编辑 4.0）\n"
                "  --seed   随机种子（整数）\n"
                "  --neg    负面提示词（编辑模式有官方默认）\n"
                "不用参数也行：/画图 一只猫；在描述里加“竖图/横图/高清/快速/用Qwen”即可自动调参。\n"
                "也支持中文参数：--模型 --尺寸 --步数 --种子 --负面\n"
                "示例：/画图 --model qwen_image --size 1024x1024 --steps 30 雨夜街头\n"
                "发 /模型 查看模型与默认值，/配置 查看当前插件设置")

    @staticmethod
    def _video_usage() -> str:
        return ("【视频 /视频】\n"
                "用法：/视频 [参数] 提示词\n"
                "  --model  模型名（默认 minimax_h3_t2v）\n"
                "           可选：minimax_h3_t2v / minimax_h3_t2v_720p15s / minimax_h3_fl2v / minimax_h3_r2v\n"
                "  --size   宽x高（默认 608x352；720p 用 1280x736，32 的倍数更稳）\n"
                "  --dur    时长秒（默认 5；720p15s 建议 15）\n"
                "  --length 帧数（优先于 --dur，124 帧≈5s）\n"
                "  --steps  步数（默认模型默认 8；turbo 可 4）\n"
                "  --cfg    CFG（H3 通常 1）\n"
                "  --seed   随机种子\n"
                "不用参数也行：/视频 一只猫；在描述里加“竖屏/高清/2秒/快速”即可自动调参。\n"
                "也支持中文参数：--模型 --尺寸 --时长 --帧数 --步数 --种子\n"
                "示例：/视频 --model minimax_h3_t2v --size 608x352 --dur 2 红苹果镜头推进\n"
                "注意：长视频耗时较长，且全局同时只允许一条任务。")

    # ------------------------------------------------------------------ 模型列表
    async def _models(self, event: AstrMessageEvent, name: str = ""):
        try:
            data = await self._get_json("/api/bridge/models")
        except Exception as exc:  # noqa: BLE001
            yield event.plain_result(f"查询失败：无法连接本机桥（{exc}）")
            return
        if not data.get("ok"):
            yield event.plain_result(f"查询失败：{data.get('error', '未知错误')}")
            return
        name = (name or "").strip()
        if name:
            for group, label in (("image", "图像"), ("video", "视频")):
                for m in data.get(group, []):
                    if m["name"] == name:
                        d = m.get("defaults") or {}
                        yield event.plain_result(
                            f"{m['name']} · {m.get('title') or ''}（{label}）\n"
                            f"默认：{d.get('width')}x{d.get('height')} · {d.get('steps')} 步 · "
                            f"cfg {d.get('cfg')} · {d.get('sampler') or '-'}\n"
                            f"可调参数：{', '.join(m.get('params') or []) or '无'}")
                        return
            yield event.plain_result(f"未找到模型 {name}，发 /模型 查看列表。")
            return
        lines = ["🖼 图像模型"]
        for m in data.get("image", []):
            d = m.get("defaults") or {}
            lines.append(f"• {m['name']} — {m.get('title') or ''} | {d.get('width')}x{d.get('height')} "
                         f"{d.get('steps')}步 cfg{d.get('cfg')}")
        lines.append("🎬 视频模型")
        for m in data.get("video", []):
            d = m.get("defaults") or {}
            dur = d.get("duration_s") or (round((d.get("length") or 0) / 24, 1) if d.get("length") else "")
            lines.append(f"• {m['name']} — {m.get('title') or ''} | {d.get('width')}x{d.get('height')} "
                         f"{d.get('steps')}步 {dur}s")
        lines.append("查看详情：/模型 <名称>；用法：/画图 或 /视频")
        yield event.plain_result("\n".join(lines))

    @filter.command("模型")
    async def models(self, event: AstrMessageEvent):
        """列出本机可用的图像/视频模型与默认参数；可跟模型名查看详情。"""
        name = self._tail(event, ("模型",)).split()[0] if self._tail(event, ("模型",)) else ""
        async for r in self._models(event, name):
            yield r

    async def _submit(self, payload: dict) -> dict:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
            async with s.post(self._base() + "/api/bridge/submit", json=payload,
                              headers=self._headers()) as r:
                return await self._read_json(r)

    async def _poll(self, job_id: str, timeout: int) -> dict:
        deadline = time.time() + timeout
        last: dict = {}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
            while time.time() < deadline:
                await asyncio.sleep(2)
                try:
                    async with s.get(f"{self._base()}/api/bridge/result/{job_id}",
                                     headers=self._headers()) as r:
                        last = await self._read_json(r)
                except Exception as exc:  # noqa: BLE001
                    last = {"ok": False, "error": f"轮询失败: {exc}"}
                    continue
                if last.get("status") in ("done", "failed"):
                    return last
        return {"ok": False, "error": f"等待超时（>{timeout}s）", "last": last}

    async def _deliver(self, umo: str, job_id: str, timeout: int, is_video: bool) -> None:
        """后台等待任务并主动发送结果，避免长任务卡住消息处理器。"""
        try:
            data = await self._poll(job_id, timeout)
            if not data.get("ok") or data.get("status") != "done":
                msg = data.get("error") or "生成失败"
                await self._send_text(umo, f"❌ {msg}")
                return
            caption = (f"{data.get('template')} · {data.get('width')}x{data.get('height')} · "
                       f"{data.get('elapsed_s')}s")
            if MessageChain is None:
                raise RuntimeError("当前 AstrBot 版本缺少 MessageChain，无法主动发送")
            # 跨容器（AstrBot 与 NapCat 分离）时本地路径不可见，改用可下载 URL
            from urllib.parse import quote
            media_key = data.get("media_key") or ""
            if media_key:
                media_url = f"{self._base()}/api/bridge/media/{job_id}/{media_key}"
            else:
                media_url = (f"{self._base()}/api/bridge/media/{job_id}"
                             f"?token={quote(str(self._cfg('token','')))}")
            comp = (Comp.Video.fromURL(url=media_url) if is_video else Comp.Image.fromURL(media_url))
            chain = MessageChain()
            if hasattr(chain, "chain"):
                chain.chain.append(comp)
                chain.chain.append(Comp.Plain("\n" + caption))
            else:
                chain = MessageChain([comp, Comp.Plain("\n" + caption)])
            await self.context.send_message(umo, chain)
            logger.info("bridge delivered url=%s to %s", media_url, umo)
        except Exception as exc:  # noqa: BLE001
            logger.error("bridge deliver failed: %s", exc)
            try:
                await self._send_text(umo, f"生成完成但发送失败：{exc}")
            except Exception:
                pass

    # ------------------------------------------------------------------ 图像
    @filter.command("画图")
    async def image(self, event: AstrMessageEvent):
        """用本机模型生成图像。"""
        async for r in self._image(event):
            yield r

    @filter.command("生图")
    async def image_alias(self, event: AstrMessageEvent):
        """生成图像（别名）。"""
        async for r in self._image(event):
            yield r

    @filter.command("图")
    async def image_short(self, event: AstrMessageEvent):
        """生成图像（短指令）。"""
        async for r in self._image(event):
            yield r

    async def _image(self, event: AstrMessageEvent):
        message = self._tail(event, ("画图", "生图"))
        flags, prompt, err = self._parse_args(
            message, {"model", "size", "steps", "cfg", "seed", "neg"})
        if err:
            yield event.plain_result(err + "\n" + self._image_usage())
            return
        prompt, natural, notes = self._natural(prompt, "image")
        for key in ("model", "steps"):
            if key not in flags and key in natural:
                flags[key] = str(natural[key])
        if "width" in natural and "size" not in flags:
            flags["size"] = f"{natural['width']}x{natural['height']}"
        if not prompt:
            yield event.plain_result(self._image_usage())
            return
        if len(prompt) > int(self._cfg("max_prompt_chars", 2000)):
            yield event.plain_result("提示词太长，请精简后重试。")
            return
        template = flags.get("model") or self._cfg("default_image_template", "moss_image")
        payload: dict = {"prompt": prompt, "template": template}
        size = flags.get("size") or self._cfg("image_size", "")
        if size:
            parsed = self._parse_size(size)
            if not parsed:
                yield event.plain_result("尺寸格式错误，示例：--size 1024x1024（16 的倍数）")
                return
            payload["width"], payload["height"] = parsed
        steps = int(flags.get("steps") or self._cfg("steps", 0) or 0)
        if steps:
            payload["steps"] = steps
        cfg = float(flags.get("cfg") or self._cfg("cfg", 0) or 0)
        if cfg:
            payload["cfg"] = cfg
        if flags.get("seed"):
            try:
                payload["seed"] = int(flags["seed"])
            except ValueError:
                yield event.plain_result("--seed 需要整数")
                return
        neg = flags.get("neg")
        if neg:
            payload["negative_prompt"] = neg
        timeout = int(self._cfg("image_timeout_s", 300))
        try:
            sub = await self._submit(payload)
        except Exception as exc:  # noqa: BLE001
            yield event.plain_result(f"提交失败：{exc}")
            return
        if not sub.get("ok"):
            yield event.plain_result(f"提交失败：{sub.get('error', '未知错误')}")
            return
        job_id = sub.get("job_id")
        if self._cfg("send_progress", True):
            yield event.plain_result(f"🎨 已提交（{template}），完成后会自动发图…")
        umo = event.unified_msg_origin
        task = asyncio.create_task(self._deliver(umo, job_id, timeout, False))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ------------------------------------------------------------------ 视频
    @filter.command("视频")
    async def video(self, event: AstrMessageEvent):
        """用本机 H3 生成视频。"""
        async for r in self._video(event):
            yield r

    @filter.command("生视频")
    async def video_alias(self, event: AstrMessageEvent):
        """生成视频（别名）。"""
        async for r in self._video(event):
            yield r

    @filter.command("片")
    async def video_short(self, event: AstrMessageEvent):
        """生成视频（短指令）。"""
        async for r in self._video(event):
            yield r

    async def _video(self, event: AstrMessageEvent):
        message = self._tail(event, ("视频", "生视频"))
        flags, prompt, err = self._parse_args(
            message, {"model", "size", "steps", "cfg", "seed", "dur", "length"})
        if err:
            yield event.plain_result(err + "\n" + self._video_usage())
            return
        prompt, natural, notes = self._natural(prompt, "video")
        for key in ("model", "steps", "duration_s"):
            if key not in flags and key in natural:
                flags[key if key != "duration_s" else "dur"] = str(natural[key])
        if "width" in natural and "size" not in flags:
            flags["size"] = f"{natural['width']}x{natural['height']}"
        if not prompt:
            yield event.plain_result(self._video_usage())
            return
        template = flags.get("model") or self._cfg("default_video_template", "minimax_h3_t2v")
        payload: dict = {"prompt": prompt, "template": template}
        size = flags.get("size") or self._cfg("video_size", "")
        if size:
            parsed = self._parse_size(size)
            if not parsed:
                yield event.plain_result("尺寸格式错误，示例：--size 1280x736（32 的倍数更稳）")
                return
            payload["width"], payload["height"] = parsed
        steps = int(flags.get("steps") or self._cfg("steps", 0) or 0)
        if steps:
            payload["steps"] = steps
        cfg = float(flags.get("cfg") or self._cfg("cfg", 0) or 0)
        if cfg:
            payload["cfg"] = cfg
        dur = flags.get("dur") or flags.get("duration")
        if dur:
            try:
                payload["duration_s"] = float(dur)
            except ValueError:
                yield event.plain_result("--dur 需要数字（秒）")
                return
        elif flags.get("length"):
            payload["length"] = int(flags["length"])
        elif float(self._cfg("duration_s", 5) or 0) > 0:
            payload["duration_s"] = float(self._cfg("duration_s", 5))
        if flags.get("seed"):
            payload["seed"] = int(flags["seed"])
        timeout = int(self._cfg("video_timeout_s", 1800))
        try:
            sub = await self._submit(payload)
        except Exception as exc:  # noqa: BLE001
            yield event.plain_result(f"提交失败：{exc}")
            return
        if not sub.get("ok"):
            yield event.plain_result(f"提交失败：{sub.get('error', '未知错误')}")
            return
        job_id = sub.get("job_id")
        if self._cfg("send_progress", True):
            yield event.plain_result(f"🎬 已提交视频（{template}），完成后会自动发送…")
        umo = event.unified_msg_origin
        task = asyncio.create_task(self._deliver(umo, job_id, timeout, True))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @filter.command("生图帮助")
    async def help_cmd(self, event: AstrMessageEvent):
        """显示本机生图桥完整用法与参数说明。"""
        yield event.plain_result(
            "【本机生图桥 · 帮助】\n"
            f"当前地址：{self._base()}\n\n"
            + self._image_usage() + "\n\n"
            + self._video_usage() + "\n\n"
            "【其他指令】\n"
            "/模型          列出可用图像/视频模型与默认参数\n"
            "/模型 <名称>   查看单个模型详情\n"
            "/配置          查看当前插件设置（地址/Token/默认值/超时）\n"
            "/画图帮助      只看图像参数\n"
            "/视频帮助      只看视频参数"
        )

    @filter.command("画图帮助")
    async def image_help(self, event: AstrMessageEvent):
        """只显示图像参数说明。"""
        yield event.plain_result(self._image_usage())

    @filter.command("视频帮助")
    async def video_help(self, event: AstrMessageEvent):
        """只显示视频参数说明。"""
        yield event.plain_result(self._video_usage())

    @filter.command("诊断")
    async def diag_cmd(self, event: AstrMessageEvent):
        """检查与别机桥的连通性（地址、延迟、ComfyUI 状态）。"""
        t0 = time.time()
        try:
            h = await self._get_json("/api/bridge/health", timeout=15)
            ms = int((time.time() - t0) * 1000)
            yield event.plain_result(
                f"地址：{self._base()}\n"
                f"延迟：{ms}ms\n"
                f"桥：{'正常' if h.get('ok') else '异常'}\n"
                f"ComfyUI：{'在线' if h.get('comfy') else '离线'}\n"
                f"模板数：{len(h.get('templates') or [])}")
        except Exception as exc:  # noqa: BLE001
            yield event.plain_result(f"诊断失败：{exc}\n地址：{self._base()}")

    @filter.command("配置")
    async def config_cmd(self, event: AstrMessageEvent):
        """查看当前插件设置（Token 已脱敏）。"""
        yield event.plain_result(self._config_summary())

    async def terminate(self):
        """插件卸载时清理临时文件。"""
        self._prune()
