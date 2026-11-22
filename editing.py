# -*- coding: utf-8 -*-
"""ffmpeg 视频剪辑工具库（comfy_mcp.editing）。

设计要点（与 SPEC.md 的 editing 接口契约一致）：

- 所有 ffmpeg/ffprobe 调用都通过 ``subprocess`` 以**参数列表**执行（``shell=False``），
  因此包含中文、空格、括号、单引号的 Windows 路径可以正常工作；不对命令做字符串拼接。
- 统一附加 ``-hide_banner -loglevel error -nostdin``；按设置里的 ``ffmpeg.threads``
  （默认 4）限制线程；超时默认取 ``ffmpeg.timeout_s``（默认 1800s），超时后杀掉子进程。
- 输出先写 ``<dst>.part``，成功后再 ``os.replace`` 原子替换；失败时清理 ``.part``。
- 每个操作返回 ``{"ok": True, "dst": ..., "cmd": [...], "probe": {...}, "warnings": [...]}``，
  失败抛出 :class:`EditError`（消息里带 stderr 尾部 2000 字符）。
- 所有视频类操作都会尽量保留音轨；``concat`` 允许输入音轨不一致
  （没有音轨的片段用 ``anullsrc`` 补静音），也支持 ``xfade + acrossfade`` 交叉淡化。
- Windows 下 ``subtitles`` 滤镜对盘符冒号/反斜杠非常敏感：``burn_subtitles`` 会先把
  srt 统一转成 UTF-8 并复制到一个纯 ASCII 临时目录，再对滤镜路径做转义后调用。
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from functools import wraps

try:  # 正常作为包导入：``from . import config``
    from . import config
except ImportError:  # 允许把 editing.py 单独放到别处直接 import
    import config  # type: ignore[no-redef]

__all__ = [
    "EditError",
    "toolchain",
    "probe",
    "trim",
    "concat",
    "transform",
    "mux_audio",
    "burn_subtitles",
    "overlay",
    "speed",
    "extract_frames",
    "extract_audio",
    "thumbnail",
    "to_gif",
    "run_ffmpeg",
]

#: 统一附加的 ffmpeg 全局参数（不输出 banner、只保留错误、禁止读 stdin）
_GLOBAL_ARGS = ["-hide_banner", "-loglevel", "error", "-nostdin"]

#: 音频统一转成 44.1kHz / fltp / 立体声，方便 concat、amix、xfade 混流
_AUDIO_FORMAT = "aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo"

#: 输出扩展名 -> ffmpeg 容器（muxer）名。显式 -f 可以避免 ``.part`` 后缀导致
#: ffmpeg 无法根据扩展名推断格式。
_MUXERS = {
    ".mp4": "mp4",
    ".m4v": "mp4",
    ".mov": "mov",
    ".mkv": "matroska",
    ".webm": "webm",
    ".avi": "avi",
    ".ts": "mpegts",
    ".gif": "gif",
    ".jpg": "image2",
    ".jpeg": "image2",
    ".png": "image2",
    ".mp3": "mp3",
    ".m4a": "ipod",
    ".aac": "adts",
    ".wav": "wav",
    ".flac": "flac",
    ".ogg": "ogg",
    ".opus": "opus",
}

#: 音频输出扩展名 -> 编码器
_AUDIO_ENCODERS = {
    ".m4a": "aac",
    ".mp4": "aac",
    ".aac": "aac",
    ".mp3": "libmp3lame",
    ".wav": "pcm_s16le",
    ".flac": "flac",
    ".ogg": "libvorbis",
    ".opus": "libopus",
}

_STDERR_TAIL = 2000


class EditError(RuntimeError):
    """ffmpeg/ffprobe 调用失败、超时或参数非法。"""


# ---------------------------------------------------------------------------
# 内部通用工具
# ---------------------------------------------------------------------------


def _settings() -> dict:
    return config.load_settings()


def _tool_paths() -> tuple[str, str]:
    return config.resolve_ffmpeg(_settings())


def _threads() -> int:
    cfg = _settings().get("ffmpeg") or {}
    try:
        value = int(cfg.get("threads", 4))
    except (TypeError, ValueError):
        value = 4
    if value < 0:
        value = 4
    return min(value, 64)


def _default_timeout() -> int:
    cfg = _settings().get("ffmpeg") or {}
    try:
        value = float(cfg.get("timeout_s", 1800))
    except (TypeError, ValueError):
        value = 1800.0
    if value <= 0:
        value = 1800.0
    return int(value)


def _resolve_timeout(timeout_s) -> float:
    if timeout_s is None:
        return float(_default_timeout())
    try:
        value = float(timeout_s)
    except (TypeError, ValueError):
        raise EditError("timeout_s 必须是数字: %r" % (timeout_s,)) from None
    if value <= 0:
        raise EditError("timeout_s 必须大于 0: %r" % (timeout_s,))
    return value


def _ts(value: float) -> str:
    """把秒数格式化成 ffmpeg 友好的字符串（最多 6 位小数，去掉末尾 0）。"""
    text = ("%.6f" % float(value)).rstrip("0").rstrip(".")
    return text or "0"


def _num(value, name: str, minimum=None, maximum=None) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise EditError("%s 必须是数字: %r" % (name, value)) from None
    if not math.isfinite(number):
        raise EditError("%s 必须是有限数字: %r" % (name, value))
    if minimum is not None and number < minimum:
        raise EditError("%s 不能小于 %s: %r" % (name, minimum, value))
    if maximum is not None and number > maximum:
        raise EditError("%s 不能大于 %s: %r" % (name, maximum, value))
    return number


def _require_file(path, role: str = "输入文件") -> str:
    if not path:
        raise EditError("%s路径为空" % role)
    try:
        abs_path = os.path.abspath(os.fspath(path))
    except TypeError:
        raise EditError("%s路径非法: %r" % (role, path)) from None
    if not os.path.isfile(abs_path):
        raise EditError("%s不存在: %s" % (role, abs_path))
    return abs_path


def _ensure_parent(path: str) -> str:
    abs_path = os.path.abspath(os.fspath(path))
    parent = os.path.dirname(abs_path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    return abs_path


def _remove_quietly(path: str) -> None:
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def _same_path(left, right) -> bool:
    try:
        return os.path.normcase(os.path.abspath(os.fspath(left))) == os.path.normcase(
            os.path.abspath(os.fspath(right))
        )
    except (TypeError, ValueError):
        return False


def _muxer_for(dst: str) -> str | None:
    return _MUXERS.get(os.path.splitext(dst)[1].lower())


def _container_args(dst: str) -> list[str]:
    if os.path.splitext(dst)[1].lower() in (".mp4", ".m4v", ".mov"):
        return ["-movflags", "+faststart"]
    return []


def _encode_args(dst: str, has_audio: bool, crf: int = 20, audio_bitrate: str = "128k") -> list[str]:
    """通用 H.264/AAC 输出参数（H3 产物本身是带音轨的 mp4，默认保留音轨）。"""
    args = [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", str(int(crf)),
        "-pix_fmt", "yuv420p",
    ]
    if has_audio:
        args += ["-c:a", "aac", "-b:a", audio_bitrate]
    else:
        args += ["-an"]
    args += _container_args(dst)
    return args


def _require_exe(path: str, name: str) -> str:
    if not path:
        raise EditError("找不到 %s 可执行文件（请在 data/settings.json 的 ffmpeg 段配置）" % name)
    if os.path.isfile(path):
        return path
    found = shutil.which(path)
    if found:
        return found
    raise EditError(
        "找不到 %s: %s（请在 data/settings.json 的 ffmpeg.path / ffmpeg.probe 配置）" % (name, path)
    )


def _short(text, limit: int = 300) -> str:
    text = str(text or "").replace("\r", " ").replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "..."


def _exec_ffmpeg(body: list[str], out_args: list[str] | None = None, timeout_s=None) -> tuple[list[str], str]:
    """执行一次 ffmpeg，成功返回 (cmd, stderr_tail)，失败抛 EditError。"""
    ffmpeg, _ = _tool_paths()
    ffmpeg = _require_exe(ffmpeg, "ffmpeg")
    timeout = _resolve_timeout(timeout_s)
    cmd = (
        [ffmpeg]
        + _GLOBAL_ARGS
        + ["-y"]
        + list(body)
        + ["-threads", str(_threads())]
        + list(out_args or [])
    )
    try:
        proc = subprocess.run(
            cmd,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        # subprocess.run 在超时后已经 kill 并回收子进程
        raise EditError(
            "ffmpeg 超时（>%.1fs）已终止: %s" % (timeout, _short(" ".join(cmd), 800))
        ) from None
    except OSError as exc:
        raise EditError("无法执行 ffmpeg: %s" % exc) from exc
    tail = (proc.stderr or "")[-_STDERR_TAIL:]
    if proc.returncode != 0:
        raise EditError(
            "ffmpeg 退出码 %s: %s" % (proc.returncode, tail.strip() or "(无 stderr)")
        )
    return cmd, tail


def _run_ffmpeg_to(dst: str, body: list[str], timeout_s=None) -> tuple[str, list[str], str]:
    """把输出写到 ``<dst>.part`` 再原子替换；返回 (dst, cmd, stderr_tail)。"""
    dst = _ensure_parent(dst)
    muxer = _muxer_for(dst)
    if muxer is None and "-f" not in body:
        raise EditError(
            "无法根据扩展名推断输出容器: %s（可用: %s）"
            % (dst, ", ".join(sorted(_MUXERS)))
        )
    part = dst + ".part"
    _remove_quietly(part)
    out_args = ["-f", muxer] if muxer else []
    out_args.append(part)
    try:
        cmd, tail = _exec_ffmpeg(body, out_args, timeout_s=timeout_s)
    except EditError:
        _remove_quietly(part)
        raise
    if not os.path.isfile(part):
        _remove_quietly(part)
        raise EditError("ffmpeg 执行成功但未生成输出文件: %s" % dst)
    os.replace(part, dst)
    return dst, cmd, tail


def _result(dst: str, cmd: list[str], warnings=None) -> dict:
    return {
        "ok": True,
        "dst": dst,
        "cmd": cmd,
        "probe": probe(dst),
        "warnings": list(warnings or []),
    }


def _finish(body: list[str], dst: str, warnings=None) -> dict:
    """常规单文件输出的收尾：原子落盘 + probe。"""
    abs_dst, cmd, _tail = _run_ffmpeg_to(dst, body)
    return _result(abs_dst, cmd, warnings=warnings)


def _require_video_info(info: dict, role: str = "输入") -> None:
    if info.get("video_codec") is None:
        raise EditError("%s没有视频轨: %s" % (role, info.get("path")))


def _accept_src_alias(func):
    """兼容 `src=` 首参别名。

    SPEC 里 ``mux_audio/burn_subtitles/overlay/extract_frames/extract_audio/thumbnail``
    的第一个参数名是 ``video``；而 MCP 的 ``edit`` 工具 schema 统一用 ``src`` 字段透传。
    这里额外接受 ``src=`` 关键字（优先使用 ``video=``/位置参数），使工具层无需逐个改名；
    ``functools.wraps`` 保证 ``inspect.signature`` 仍然显示 SPEC 约定的签名。
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        if "src" in kwargs:
            src_value = kwargs.pop("src")
            if not args and "video" not in kwargs:
                kwargs["video"] = src_value
        return func(*args, **kwargs)

    return wrapper


def _require_audio_info(info: dict, role: str = "输入") -> None:
    if not info.get("has_audio"):
        raise EditError("%s没有音频轨: %s" % (role, info.get("path")))


# ---------------------------------------------------------------------------
# ffprobe / 工具链
# ---------------------------------------------------------------------------


def _exec_ffprobe(path: str) -> dict:
    _, ffprobe = _tool_paths()
    ffprobe = _require_exe(ffprobe, "ffprobe")
    timeout = min(_default_timeout(), 120)
    cmd = [
        ffprobe,
        "-hide_banner",
        "-loglevel",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        path,
    ]
    try:
        proc = subprocess.run(
            cmd,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise EditError("ffprobe 超时（>%ss）: %s" % (timeout, path)) from None
    except OSError as exc:
        raise EditError("无法执行 ffprobe: %s" % exc) from exc
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-_STDERR_TAIL:]
        raise EditError("ffprobe 失败（退出码 %s）: %s: %s" % (proc.returncode, path, tail.strip()))
    try:
        data = json.loads(proc.stdout or "{}")
    except ValueError as exc:
        raise EditError("ffprobe 输出不是合法 JSON: %s: %s" % (path, exc)) from None
    if not isinstance(data, dict):
        raise EditError("ffprobe 输出结构异常: %s" % path)
    return data


def _parse_fraction(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in ("N/A", "0/0"):
        return None
    try:
        if "/" in text:
            num, den = text.split("/", 1)
            denominator = float(den)
            if denominator == 0:
                return None
            return float(num) / denominator
        return float(text)
    except (TypeError, ValueError):
        return None


def _int_or_none(value) -> int | None:
    try:
        if value is None:
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _float_or_none(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def toolchain() -> dict:
    """返回 ffmpeg / ffprobe 路径、版本与可用性：``{ffmpeg, ffprobe, version, ok}``。"""
    try:
        settings = _settings()
        ffmpeg, ffprobe = config.resolve_ffmpeg(settings)
    except Exception as exc:  # pragma: no cover - 配置异常时仍然返回结构化结果
        return {"ffmpeg": "", "ffprobe": "", "version": "", "ok": False, "error": str(exc)}
    ffmpeg_ok = bool(shutil.which(ffmpeg) or os.path.isfile(ffmpeg))
    ffprobe_ok = bool(shutil.which(ffprobe) or os.path.isfile(ffprobe))
    version = ""
    if ffmpeg_ok:
        try:
            proc = subprocess.run(
                [ffmpeg, "-hide_banner", "-version"],
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                encoding="utf-8",
                errors="replace",
                timeout=20,
            )
            first_line = (proc.stdout or "").splitlines()[0] if (proc.stdout or "").strip() else ""
            match = re.search(r"ffmpeg version (\S+)", first_line)
            if match:
                version = match.group(1)
            elif first_line:
                version = first_line.strip()
        except (OSError, subprocess.TimeoutExpired):
            version = ""
    return {
        "ffmpeg": os.path.abspath(ffmpeg) if os.path.isfile(ffmpeg) else ffmpeg,
        "ffprobe": os.path.abspath(ffprobe) if os.path.isfile(ffprobe) else ffprobe,
        "version": version,
        "ok": bool(ffmpeg_ok and ffprobe_ok),
    }


def probe(path: str) -> dict:
    """用 ffprobe 读取媒体信息。

    返回：``{path,width,height,fps,duration_s,video_codec,audio_codec,has_audio,bitrate,size_mb,rotation}``。
    纯音频文件 width/height/fps 为 None。
    """
    abs_path = _require_file(path, "媒体文件")
    data = _exec_ffprobe(abs_path)
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    width = _int_or_none(video.get("width")) if video else None
    height = _int_or_none(video.get("height")) if video else None

    fps = None
    if video:
        fps = _parse_fraction(video.get("avg_frame_rate"))
        if not fps:
            fps = _parse_fraction(video.get("r_frame_rate"))
        if fps:
            fps = round(fps, 3)

    duration = _float_or_none(fmt.get("duration"))
    if duration is None:
        candidates = [_float_or_none(s.get("duration")) for s in streams]
        candidates = [c for c in candidates if c]
        if candidates:
            duration = max(candidates)
    if duration is None and video and fps:
        nb_frames = _int_or_none(video.get("nb_frames"))
        if nb_frames:
            duration = nb_frames / fps
    if duration is not None:
        duration = round(duration, 3)

    bitrate = _int_or_none(fmt.get("bit_rate"))
    if bitrate is None:
        parts = [_int_or_none(s.get("bit_rate")) for s in streams]
        parts = [p for p in parts if p]
        if parts:
            bitrate = sum(parts)

    rotation = 0
    if video:
        tags = video.get("tags") or {}
        rotation = _int_or_none(tags.get("rotate")) or 0
        if not rotation:
            for side_data in video.get("side_data_list") or []:
                if "rotation" in side_data:
                    rotation = _int_or_none(side_data.get("rotation")) or 0
                    break

    try:
        size_mb = round(os.path.getsize(abs_path) / (1024.0 * 1024.0), 3)
    except OSError:
        size_mb = None

    return {
        "path": abs_path,
        "width": width,
        "height": height,
        "fps": fps,
        "duration_s": duration,
        "video_codec": video.get("codec_name") if video else None,
        "audio_codec": audio.get("codec_name") if audio else None,
        "has_audio": audio is not None,
        "bitrate": bitrate,
        "size_mb": size_mb,
        "rotation": rotation,
    }


# ---------------------------------------------------------------------------
# 基础剪辑
# ---------------------------------------------------------------------------


def trim(src: str, dst: str, start_s: float = 0.0, duration_s: float | None = None, reencode: bool = True) -> dict:
    """裁剪视频片段（保留音轨）。``reencode=False`` 时用 stream copy（关键帧对齐）。"""
    info = probe(src)
    _require_video_info(info, "源视频")
    start = _num(start_s, "start_s", minimum=0.0)
    duration = None if duration_s is None else _num(duration_s, "duration_s", minimum=0.001)
    body: list[str] = []
    if reencode:
        # 重编码：-ss 放在 -i 前更快（解码定位）
        if start > 0:
            body += ["-ss", _ts(start)]
        body += ["-i", info["path"]]
        if duration is not None:
            body += ["-t", _ts(duration)]
        body += _encode_args(dst, info["has_audio"])
    else:
        # 流复制：-ss 放在 -i 后（丢弃包），时长才准确；放在前面会受关键帧位置影响
        body += ["-i", info["path"]]
        if start > 0:
            body += ["-ss", _ts(start)]
        if duration is not None:
            body += ["-t", _ts(duration)]
        body += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
        body += _container_args(dst)
    return _finish(body, dst)


def _concat_escape(path: str) -> str:
    """concat demuxer 列表文件的路径转义：正斜杠 + 单引号转义。"""
    text = os.path.abspath(path).replace("\\", "/")
    return text.replace("'", "'\\''")


def _concat_mismatch(infos: list[dict]) -> list[str]:
    base = infos[0]
    problems: list[str] = []
    for info in infos[1:]:
        if info.get("width") != base.get("width") or info.get("height") != base.get("height"):
            problems.append("分辨率")
        base_fps = base.get("fps") or 0.0
        fps = info.get("fps") or 0.0
        if abs(fps - base_fps) > 0.01:
            problems.append("帧率")
        if bool(info.get("has_audio")) != bool(base.get("has_audio")):
            problems.append("音轨")
        if info.get("video_codec") != base.get("video_codec"):
            problems.append("视频编码")
        if info.get("has_audio") and base.get("has_audio") and (
            info.get("audio_codec") != base.get("audio_codec")
        ):
            problems.append("音频编码")
    # 去重且保持顺序
    seen = set()
    unique = []
    for item in problems:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def _audio_segment_filter(duration: float) -> str:
    """把某段音频规整为固定时长（短了补静音、长了截断），保证 concat 对齐。"""
    return "%s,asetpts=PTS-STARTPTS,apad,atrim=duration=%s" % (_AUDIO_FORMAT, _ts(duration))


def _plain_concat_graph(graph: list[str], count: int, any_audio: bool) -> tuple[str, str | None, float]:
    chain = "".join("[v%d]" % i + ("[a%d]" % i if any_audio else "") for i in range(count))
    graph.append(
        "%sconcat=n=%d:v=1:a=%d[vout]" % (chain, count, 1 if any_audio else 0)
        + ("[aout]" if any_audio else "")
    )
    return "vout", ("aout" if any_audio else None), 0.0


def _crossfade_graph(
    graph: list[str], durations: list[float], crossfade: float, any_audio: bool
) -> tuple[str, str | None, float]:
    count = len(durations)
    current_video = "v0"
    current_audio = "a0" if any_audio else None
    total = durations[0]
    for index in range(1, count):
        offset = max(0.0, total - crossfade)
        graph.append(
            "[%s][v%d]xfade=transition=fade:duration=%s:offset=%s[xv%d]"
            % (current_video, index, _ts(crossfade), _ts(offset), index)
        )
        current_video = "xv%d" % index
        if any_audio:
            graph.append(
                "[%s][a%d]acrossfade=d=%s:c1=tri:c2=tri[xa%d]"
                % (current_audio, index, _ts(crossfade), index)
            )
            current_audio = "xa%d" % index
        total += durations[index] - crossfade
    return current_video, current_audio, total


def _concat_reencode(
    infos: list[dict],
    durations: list[float],
    dst: str,
    crossfade: float,
    warnings: list[str],
) -> dict:
    count = len(infos)
    any_audio = any(bool(info.get("has_audio")) for info in infos)
    target_w = int(infos[0].get("width") or 640)
    target_h = int(infos[0].get("height") or 360)
    target_w = max(2, target_w - target_w % 2)
    target_h = max(2, target_h - target_h % 2)
    target_fps = float(infos[0].get("fps") or 24.0)

    graph: list[str] = []
    for index, info in enumerate(infos):
        graph.append(
            "[%d:v]scale=%d:%d:force_original_aspect_ratio=decrease,"
            "pad=%d:%d:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=%s,setpts=PTS-STARTPTS,format=yuv420p[v%d]"
            % (index, target_w, target_h, target_w, target_h, _ts(target_fps), index)
        )
        duration = durations[index]
        if info.get("has_audio"):
            graph.append("[%d:a]%s[a%d]" % (index, _audio_segment_filter(duration), index))
        elif any_audio:
            # 没有音轨的片段补一段等长静音，保证 concat 的 a=1 成立
            graph.append(
                "anullsrc=channel_layout=stereo:sample_rate=44100,atrim=duration=%s,asetpts=PTS-STARTPTS[a%d]"
                % (_ts(duration), index)
            )

    body: list[str] = []
    for info in infos:
        body += ["-i", info["path"]]

    if crossfade > 0:
        video_label, audio_label, total = _crossfade_graph(graph, durations, crossfade, any_audio)
    else:
        video_label, audio_label, total = _plain_concat_graph(graph, count, any_audio)
        total = sum(durations)

    body += ["-filter_complex", ";".join(graph), "-map", "[%s]" % video_label]
    if any_audio and audio_label:
        body += ["-map", "[%s]" % audio_label]
    body += ["-t", _ts(total)]
    body += _encode_args(dst, any_audio)
    return _finish(body, dst, warnings=warnings)


def concat(
    inputs: list[str],
    dst: str,
    crossfade_s: float = 0.0,
    reencode: bool = True,
) -> dict:
    """拼接多个视频；``crossfade_s > 0`` 时用 xfade + acrossfade 交叉淡化。

    - 输入音轨不一致时自动用静音补齐（没有音轨的段）。
    - ``reencode=False`` 走 concat demuxer 流复制；参数不一致或复制失败会自动回退重编码，
      并在返回的 ``warnings`` 里说明。
    """
    if not inputs:
        raise EditError("concat 至少需要 1 个输入文件")
    infos = [probe(item) for item in inputs]
    for index, info in enumerate(infos):
        if info.get("video_codec") is None:
            raise EditError("第 %d 个输入没有视频轨: %s" % (index + 1, info.get("path")))
        duration = info.get("duration_s")
        if not duration or duration <= 0:
            raise EditError("无法读取第 %d 个输入的时长: %s" % (index + 1, info.get("path")))

    crossfade = _num(crossfade_s, "crossfade_s", minimum=0.0)
    durations = [float(info["duration_s"]) for info in infos]
    warnings: list[str] = []

    if crossfade > 0:
        if len(infos) < 2:
            raise EditError("crossfade 至少需要 2 个输入文件")
        shortest = min(durations)
        if crossfade >= shortest:
            raise EditError(
                "crossfade_s=%s 过大，必须小于最短输入时长 %ss" % (_ts(crossfade), _ts(shortest))
            )
        if not reencode:
            warnings.append("crossfade 需要重编码，已忽略 reencode=False")
        return _concat_reencode(infos, durations, dst, crossfade, warnings)

    if not reencode:
        mismatch = _concat_mismatch(infos)
        if not mismatch:
            try:
                tmpdir = tempfile.mkdtemp(prefix="comfy_mcp_concat_")
                try:
                    list_path = os.path.join(tmpdir, "concat.txt")
                    with open(list_path, "w", encoding="utf-8", newline="\n") as handle:
                        for info in infos:
                            handle.write("file '%s'\n" % _concat_escape(info["path"]))
                    body = ["-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy"]
                    body += _container_args(dst) or []
                    abs_dst, cmd, _tail = _run_ffmpeg_to(dst, body)
                    return _result(abs_dst, cmd, warnings=warnings)
                finally:
                    shutil.rmtree(tmpdir, ignore_errors=True)
            except EditError as exc:
                warnings.append("stream copy 拼接失败，已回退重编码: %s" % _short(exc, 200))
        else:
            warnings.append("输入参数不一致（%s），已自动改为重编码" % "、".join(mismatch))
    return _concat_reencode(infos, durations, dst, 0.0, warnings)


def transform(
    src: str,
    dst: str,
    width: int | None = None,
    height: int | None = None,
    fps: float | None = None,
    pad: bool = False,
    reencode: bool = True,
) -> dict:
    """缩放 / 改帧率。``pad=True`` 且同时给出宽高时使用等比缩放 + 黑边填充。"""
    info = probe(src)
    _require_video_info(info, "源视频")
    target_w = None if width is None else int(_num(width, "width", minimum=1))
    target_h = None if height is None else int(_num(height, "height", minimum=1))
    target_fps = None if fps is None else _num(fps, "fps", minimum=0.001)

    if target_w is not None:
        target_w = max(2, target_w - target_w % 2)
    if target_h is not None:
        target_h = max(2, target_h - target_h % 2)

    need_scale = (
        (target_w is not None and target_w != info.get("width"))
        or (target_h is not None and target_h != info.get("height"))
    )
    source_fps = info.get("fps")
    need_fps = bool(
        target_fps is not None
        and (source_fps is None or abs(float(target_fps) - float(source_fps)) > 0.01)
    )

    body = ["-i", info["path"]]
    if not reencode:
        if need_scale or need_fps:
            raise EditError("reencode=False 时不能缩放或修改帧率（请使用 reencode=True）")
        body += ["-c", "copy"]
        body += _container_args(dst)
        return _finish(body, dst)

    filters: list[str] = []
    if target_w is not None and target_h is not None:
        if pad:
            filters.append(
                "scale=%d:%d:force_original_aspect_ratio=decrease,"
                "pad=%d:%d:(ow-iw)/2:(oh-ih)/2" % (target_w, target_h, target_w, target_h)
            )
        else:
            filters.append("scale=%d:%d" % (target_w, target_h))
    elif target_w is not None:
        filters.append("scale=%d:-2" % target_w)
    elif target_h is not None:
        filters.append("scale=-2:%d" % target_h)
    if target_fps is not None:
        filters.append("fps=%s" % _ts(target_fps))
    if not filters:
        filters.append("null")
    filters.append("setsar=1")
    body += ["-vf", ",".join(filters)]
    body += _encode_args(dst, info["has_audio"])
    return _finish(body, dst)


def _mux_chain(volume: float, normalize: bool, offset_s: float, pad: bool = True) -> str:
    filters: list[str] = []
    if abs(volume - 1.0) > 1e-9:
        filters.append("volume=%s" % _ts(volume))
    if normalize:
        filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    filters.append(_AUDIO_FORMAT)
    if offset_s > 0:
        filters.append("adelay=%d:all=1" % int(round(offset_s * 1000)))
    elif offset_s < 0:
        filters.append("atrim=start=%s" % _ts(-offset_s))
        filters.append("asetpts=PTS-STARTPTS")
    if pad:
        filters.append("apad")
    return ",".join(filters)


@_accept_src_alias
def mux_audio(
    video: str,
    dst: str,
    audio: str | None = None,
    volume: float = 1.0,
    offset_s: float = 0.0,
    replace: bool = True,
    normalize: bool = False,
) -> dict:
    """给视频混入/替换音轨（视频流直接 copy，音频重新编码成 AAC）。

    - ``audio=None``：处理视频自带音轨（调节音量/响度/延迟）。
    - ``replace=True``（默认）：用 ``audio`` 替换原音轨；``audio=None`` 时即重做原音轨。
    - ``replace=False``：把 ``audio`` 与原音轨 amix 混合；原视频没有音轨时退化为替换。
    - ``volume`` 作用于 ``audio``（``audio=None`` 时作用域为原音轨）；
      ``offset_s`` 为音频相对视频起点的延迟（负数表示剪掉开头）；
      ``normalize=True`` 使用 EBU R128 loudnorm 单遍响度归一化。
    """
    vinfo = probe(video)
    _require_video_info(vinfo, "视频")
    volume_value = _num(volume, "volume", minimum=0.0)
    offset = _num(offset_s, "offset_s")
    warnings: list[str] = []

    ainfo = None
    if audio is not None:
        ainfo = probe(audio)
        _require_audio_info(ainfo, "音频")
    if ainfo is None and not vinfo["has_audio"]:
        raise EditError("视频没有音轨，且未提供 audio")

    body = ["-i", vinfo["path"]]
    if ainfo is not None:
        body += ["-i", ainfo["path"]]

    graph: list[str] = []
    if ainfo is None:
        graph.append("[0:a]%s[aout]" % _mux_chain(volume_value, normalize, offset))
    elif replace or not vinfo["has_audio"]:
        if not replace and not vinfo["has_audio"]:
            warnings.append("视频没有音轨，replace=False 已按替换处理")
        graph.append("[1:a]%s[aout]" % _mux_chain(volume_value, normalize, offset))
    else:
        graph.append("[0:a]%s[a0]" % _AUDIO_FORMAT)
        graph.append("[1:a]%s[a1]" % _mux_chain(volume_value, normalize, offset))
        graph.append("[a0][a1]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0[aout]")

    body += ["-filter_complex", ";".join(graph), "-map", "0:v:0", "-map", "[aout]"]
    body += ["-c:v", "copy", "-c:a", "aac", "-b:a", "128k"]
    duration = vinfo.get("duration_s")
    if duration:
        body += ["-t", _ts(duration)]
    body += ["-shortest"]
    body += _container_args(dst)
    return _finish(body, dst, warnings=warnings)


# ---------------------------------------------------------------------------
# 字幕 / 叠加 / 变速
# ---------------------------------------------------------------------------


def _decode_text(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _is_safe_ascii_base(path: str) -> bool:
    """临时目录候选：纯 ASCII 且不含空格/引号/逗号等滤镜敏感字符。"""
    if not path:
        return False
    abs_path = os.path.abspath(path)
    if any(ord(char) > 127 for char in abs_path):
        return False
    for char in ' ,;[]\'':
        if char in abs_path:
            return False
    return True


def _make_ascii_tempdir(prefix: str = "comfy_mcp_subs_") -> str:
    """创建一个纯 ASCII 临时目录，用于字幕文件复制（避开 Windows 路径转义坑）。"""
    system_drive = os.environ.get("SystemDrive") or "C:"
    candidates = [
        os.environ.get("TEMP"),
        os.environ.get("TMP"),
        os.path.join(system_drive, "Temp"),
        os.path.join(getattr(config, "DATA_DIR", ""), "tmp") if getattr(config, "DATA_DIR", "") else None,
    ]
    seen = set()
    for base in candidates:
        if not base:
            continue
        try:
            abs_base = os.path.abspath(base)
        except (OSError, ValueError):
            continue
        if abs_base in seen or not _is_safe_ascii_base(abs_base):
            continue
        seen.add(abs_base)
        try:
            os.makedirs(abs_base, exist_ok=True)
            return tempfile.mkdtemp(prefix=prefix, dir=abs_base)
        except OSError:
            continue
    # 兜底：系统临时目录（可能非 ASCII，交给滤镜路径转义处理）
    return tempfile.mkdtemp(prefix=prefix)


def _escape_filter_path(path: str) -> str:
    """libavfilter 路径转义：正斜杠 + 转义 ``'``、``:``、`,`、``[``、``]``、``;``。"""
    text = os.path.abspath(path).replace("\\", "/")
    out = []
    for char in text:
        if char in "':,[];":
            out.append("\\" + char)
        else:
            out.append(char)
    return "".join(out)


@_accept_src_alias
def burn_subtitles(
    video: str,
    dst: str,
    srt_path: str,
    font_size: int = 24,
    margin_v: int = 40,
) -> dict:
    """把 srt 字幕烧录到视频画面里（保留音轨）。

    Windows 关键处理：srt 先转 UTF-8 并复制到纯 ASCII 临时目录，再对 ``subtitles``
    滤镜里的路径做转义（盘符冒号 ``C\\:``、正斜杠），避免 filtergraph 解析失败。
    """
    vinfo = probe(video)
    _require_video_info(vinfo, "视频")
    srt_file = _require_file(srt_path, "字幕文件")
    raw = b""
    with open(srt_file, "rb") as handle:
        raw = handle.read()
    text = _decode_text(raw).replace("\r\n", "\n").replace("\r", "\n")
    size = int(_num(font_size, "font_size", minimum=1, maximum=512))
    margin = int(_num(margin_v, "margin_v", minimum=0))
    style = "FontSize=%d,MarginV=%d" % (size, margin)

    tmpdir = _make_ascii_tempdir()
    try:
        srt_tmp = os.path.join(tmpdir, "subs.srt")
        with open(srt_tmp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text if text.endswith("\n") else text + "\n")
        filter_value = "subtitles='%s':force_style='%s'" % (
            _escape_filter_path(srt_tmp),
            style,
        )
        body = ["-i", vinfo["path"], "-vf", filter_value]
        body += _encode_args(dst, vinfo["has_audio"])
        return _finish(body, dst)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


_POSITIONS = {
    "top-left": "x={m}:y={m}",
    "top-right": "x=W-w-{m}:y={m}",
    "bottom-left": "x={m}:y=H-h-{m}",
    "bottom-right": "x=W-w-{m}:y=H-h-{m}",
    "center": "x=(W-w)/2:y=(H-h)/2",
    "middle": "x=(W-w)/2:y=(H-h)/2",
}


@_accept_src_alias
def overlay(
    video: str,
    dst: str,
    image_path: str,
    position: str = "bottom-right",
    margin: int = 20,
    opacity: float = 1.0,
    scale: float = 0.2,
) -> dict:
    """把图片（支持 png 透明通道）叠加到视频上。

    ``scale`` 是叠加图宽度相对视频宽度的比例（0.2 = 20%），``opacity`` 取 0~1。
    """
    vinfo = probe(video)
    _require_video_info(vinfo, "视频")
    image = _require_file(image_path, "叠加图片")
    key = str(position or "bottom-right").strip().lower()
    if key not in _POSITIONS:
        raise EditError(
            "position 不支持: %r（可用: %s）" % (position, ", ".join(sorted(set(_POSITIONS))))
        )
    margin_px = int(_num(margin, "margin", minimum=0))
    opacity_value = _num(opacity, "opacity", minimum=0.0, maximum=1.0)
    scale_value = _num(scale, "scale", minimum=0.001, maximum=1.0)
    video_width = int(vinfo.get("width") or 0)
    if video_width <= 0:
        raise EditError("无法读取视频宽度: %s" % vinfo["path"])
    overlay_width = max(2, int(round(video_width * scale_value)))

    filters = ["scale=%d:-1" % overlay_width, "format=rgba"]
    if opacity_value < 1.0:
        filters.append("colorchannelmixer=aa=%s" % _ts(opacity_value))
    expression = _POSITIONS[key].format(m=margin_px)
    filter_complex = "[1:v]%s[ov];[0:v][ov]overlay=%s:format=auto[vout]" % (
        ",".join(filters),
        expression,
    )
    body = ["-i", vinfo["path"], "-i", image, "-filter_complex", filter_complex, "-map", "[vout]"]
    if vinfo["has_audio"]:
        body += ["-map", "0:a:0"]
    body += _encode_args(dst, vinfo["has_audio"])
    return _finish(body, dst)


def _atempo_chain(factor: float) -> str:
    """把任意倍速拆成若干个 atempo（每个限 0.5~2.0）串联。"""
    remaining = float(factor)
    parts: list[str] = []
    guard = 0
    while remaining > 2.0 and guard < 12:
        parts.append("atempo=2.0")
        remaining /= 2.0
        guard += 1
    while remaining < 0.5 and guard < 12:
        parts.append("atempo=0.5")
        remaining /= 0.5
        guard += 1
    parts.append("atempo=%s" % _ts(remaining))
    return ",".join(parts)


def speed(src: str, dst: str, factor: float = 1.0, keep_audio: bool = True) -> dict:
    """变速。``factor=2`` 两倍速、``factor=0.5`` 半速；``keep_audio`` 时用 atempo 同步音频。"""
    info = probe(src)
    _require_video_info(info, "源视频")
    value = _num(factor, "factor", minimum=0.01, maximum=100.0)
    body = ["-i", info["path"], "-vf", "setpts=PTS/%s" % _ts(value)]
    keep = bool(keep_audio and info["has_audio"])
    if keep:
        body += ["-af", _atempo_chain(value)]
    body += _encode_args(dst, keep)
    return _finish(body, dst)


# ---------------------------------------------------------------------------
# 抽帧 / 抽音频 / 缩略图 / GIF
# ---------------------------------------------------------------------------


@_accept_src_alias
def extract_frames(
    video: str,
    out_dir: str,
    fps: float | None = None,
    start_s: float | None = None,
    end_s: float | None = None,
    prefix: str = "frame",
) -> dict:
    """按 fps 抽帧为 ``<out_dir>/<prefix>_000001.jpg``。

    不指定 fps 时导出全部帧。多文件输出无法做单文件原子替换，
    因此先写正常文件名（ffmpeg 自身覆盖写）。
    """
    info = probe(video)
    _require_video_info(info, "视频")
    abs_dir = os.path.abspath(os.fspath(out_dir))
    try:
        os.makedirs(abs_dir, exist_ok=True)
    except OSError as exc:
        raise EditError("无法创建输出目录 %s: %s" % (abs_dir, exc)) from exc

    start = 0.0 if start_s is None else _num(start_s, "start_s", minimum=0.0)
    duration = None
    if end_s is not None:
        end = _num(end_s, "end_s", minimum=0.0)
        if end <= start:
            raise EditError("end_s 必须大于 start_s（start_s=%s, end_s=%s）" % (_ts(start), _ts(end)))
        duration = end - start
    if fps is not None:
        _num(fps, "fps", minimum=0.001)

    safe_prefix = str(prefix or "frame").strip().replace("/", "_").replace("\\", "_")
    if not safe_prefix:
        safe_prefix = "frame"
    pattern = os.path.join(abs_dir, "%s_%%06d.jpg" % safe_prefix)

    body: list[str] = []
    if start > 0:
        body += ["-ss", _ts(start)]
    body += ["-i", info["path"]]
    if duration is not None:
        body += ["-t", _ts(duration)]
    if fps is not None:
        body += ["-vf", "fps=%s" % _ts(fps)]
    body += ["-q:v", "2"]

    cmd, _tail = _exec_ffmpeg(body, ["-f", "image2", pattern])

    files = sorted(
        os.path.join(abs_dir, name)
        for name in os.listdir(abs_dir)
        if name.startswith(safe_prefix + "_") and name.lower().endswith((".jpg", ".jpeg"))
    )
    if not files:
        raise EditError("没有提取到任何帧（检查 start_s/end_s/fps 是否超出视频范围）: %s" % info["path"])

    first = probe(files[0])
    shown = [os.path.basename(item) for item in files[:20]]
    return {
        "ok": True,
        "dst": abs_dir,
        "cmd": cmd,
        "probe": {
            "count": len(files),
            "files": shown,
            "files_truncated": len(files) > len(shown),
            "width": first.get("width"),
            "height": first.get("height"),
        },
        "warnings": [],
    }


@_accept_src_alias
def extract_audio(video: str, dst: str) -> dict:
    """抽取音轨。扩展名决定编码：.m4a/.mp4/.aac -> AAC；.mp3/.wav/.flac/.ogg/.opus 同理。"""
    info = probe(video)
    _require_audio_info(info, "视频")
    ext = os.path.splitext(os.fspath(dst))[1].lower()
    encoder = _AUDIO_ENCODERS.get(ext)
    if encoder is None:
        raise EditError(
            "不支持的音频输出扩展名: %r（可用: %s）" % (ext, ", ".join(sorted(_AUDIO_ENCODERS)))
        )
    body = ["-i", info["path"], "-vn", "-c:a", encoder]
    if encoder in ("aac", "libmp3lame", "libvorbis", "libopus"):
        body += ["-b:a", "192k"]
    return _finish(body, dst)


@_accept_src_alias
def thumbnail(video: str, dst: str, at_s: float = 0.0, width: int = 1280) -> dict:
    """抓取某一时刻的单帧图片（.jpg/.jpeg/.png）。"""
    info = probe(video)
    _require_video_info(info, "视频")
    ext = os.path.splitext(os.fspath(dst))[1].lower()
    if ext not in (".jpg", ".jpeg", ".png"):
        raise EditError("thumbnail 仅支持 .jpg/.jpeg/.png 输出: %s" % dst)
    at = _num(at_s, "at_s", minimum=0.0)
    duration = info.get("duration_s")
    if duration and duration > 0 and at >= duration:
        at = max(0.0, float(duration) - 0.1)
    body: list[str] = []
    if at > 0:
        body += ["-ss", _ts(at)]
    body += ["-i", info["path"], "-frames:v", "1"]
    if width is not None:
        target_w = int(_num(width, "width", minimum=2))
        target_w = max(2, target_w - target_w % 2)
        body += ["-vf", "scale=%d:-2" % target_w]
    body += ["-q:v", "2", "-update", "1"]
    return _finish(body, dst)


def to_gif(
    src: str,
    dst: str,
    fps: float = 12,
    width: int = 640,
    start_s: float = 0.0,
    duration_s: float | None = None,
) -> dict:
    """用「调色板两遍法」生成高质量 GIF（先 palettegen，再 paletteuse）。"""
    info = probe(src)
    _require_video_info(info, "源视频")
    ext = os.path.splitext(os.fspath(dst))[1].lower()
    if ext != ".gif":
        raise EditError("to_gif 仅支持 .gif 输出: %s" % dst)
    fps_value = _num(fps, "fps", minimum=0.001, maximum=60.0)
    width_value = int(_num(width, "width", minimum=2))
    width_value = max(2, width_value - width_value % 2)
    start = _num(start_s, "start_s", minimum=0.0)
    duration = None if duration_s is None else _num(duration_s, "duration_s", minimum=0.001)

    abs_dst = _ensure_parent(dst)
    palette = abs_dst + ".palette.png"
    input_args: list[str] = []
    if start > 0:
        input_args += ["-ss", _ts(start)]
    input_args += ["-i", info["path"]]

    try:
        # 第一遍：生成调色板
        palette_body = list(input_args)
        palette_body += [
            "-vf",
            "fps=%s,scale=%d:-1:flags=lanczos,palettegen=stats_mode=diff" % (_ts(fps_value), width_value),
            "-update", "1",
        ]
        if duration is not None:
            palette_body += ["-t", _ts(duration)]
        _run_ffmpeg_to(palette, palette_body)
        # 第二遍：用调色板编码 GIF
        gif_body = list(input_args)
        gif_body += ["-i", palette]
        gif_body += [
            "-lavfi",
            "fps=%s,scale=%d:-1:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5"
            % (_ts(fps_value), width_value),
            "-loop", "0",
        ]
        if duration is not None:
            gif_body += ["-t", _ts(duration)]
        abs_dst, cmd, _tail = _run_ffmpeg_to(abs_dst, gif_body)
        return _result(abs_dst, cmd)
    finally:
        _remove_quietly(palette)
        _remove_quietly(palette + ".part")


# ---------------------------------------------------------------------------
# 通用入口
# ---------------------------------------------------------------------------


def _looks_like_ffmpeg(value) -> bool:
    text = os.path.basename(os.fspath(value)).lower()
    if text in ("ffmpeg", "ffmpeg.exe"):
        return True
    try:
        ffmpeg, _ = _tool_paths()
        return _same_path(value, ffmpeg)
    except Exception:  # pragma: no cover - 配置异常兜底
        return False


def run_ffmpeg(args: list[str], dst: str, timeout_s: int | None = None) -> dict:
    """通用 ffmpeg 执行入口（进阶用）。

    ``args`` 是 ffmpeg 参数列表（可省略开头的 ffmpeg 可执行文件，也可省略末尾的输出路径，
    两种写法都会自动处理）；本函数负责追加全局参数、``-threads``、``.part`` 原子落盘与超时。

    返回 ``{ok, cmd, stderr_tail, dst, duration_s}``；失败抛 :class:`EditError`。
    """
    if not isinstance(args, (list, tuple)):
        raise EditError("args 必须是 list[str]，不能是字符串（禁止 shell 拼接）: %r" % (args,))
    body: list[str] = []
    for item in args:
        try:
            body.append(os.fspath(item))
        except TypeError:
            raise EditError("args 里的参数必须是字符串或路径: %r" % (item,)) from None
    if body and _looks_like_ffmpeg(body[0]):
        body = body[1:]
    if not dst:
        raise EditError("dst 不能为空")
    abs_dst = os.path.abspath(os.fspath(dst))
    if body and _same_path(body[-1], abs_dst):
        body = body[:-1]

    abs_dst, cmd, tail = _run_ffmpeg_to(abs_dst, body, timeout_s=timeout_s)

    duration = None
    try:
        duration = probe(abs_dst).get("duration_s")
    except EditError:
        duration = None
    return {
        "ok": True,
        "cmd": cmd,
        "stderr_tail": tail,
        "dst": abs_dst,
        "duration_s": duration,
    }
