# comfy_mcp 视频剪辑模块（editing.py）

`comfy_mcp/editing.py` 是 ComfyUI MCP 服务的本地 ffmpeg 剪辑层：把「裁剪、拼接、转封装、
混音、烧字幕、贴图、变速、抽帧、抽音频、缩略图、GIF」封装成一组结构统一的 Python 函数，
供 `server.py` 的 `edit` / `probe_media` 工具调用，也可以脱离 MCP 直接当 ffmpeg 工具库用。

- 只用 **标准库**（`subprocess` 调外部 ffmpeg/ffprobe），不新增第三方依赖。
- 所有命令以**参数列表**执行（`shell=False`），因此 Windows 下含**中文、空格、括号、
  单引号**的路径都能正常工作，不需要手工拼命令。
- 所有命令统一附加 `-hide_banner -loglevel error -nostdin`，并按设置追加
  `-threads N` 限制编码线程，避免剪辑抢满 CPU 影响 ComfyUI 推理。
- 所有单文件输出先写 `<dst>.part`，成功后再 `os.replace` 原子替换；失败会清理 `.part`。
- 使用 `ffmpeg.threads`（默认 4）与 `ffmpeg.timeout_s`（默认 1800 秒）控制资源；
  超时会终止 ffmpeg 进程并抛 `EditError`。

---

## 1. 依赖与配置

ffmpeg/ffprobe 路径的解析顺序（`config.resolve_ffmpeg()`）：

1. `data/settings.json` 中的 `ffmpeg.path` / `ffmpeg.probe`；
2. 系统 `PATH` 里的 `ffmpeg` / `ffprobe`；
3. 内置回退路径（`C:\ffmpeg\ffmpeg-master-latest-win64-gpl-shared\bin\`）。

默认设置（`config.DEFAULT_SETTINGS["ffmpeg"]`）：

```json
{
  "ffmpeg": {
    "path": null,
    "probe": null,
    "threads": 4,
    "timeout_s": 1800
  }
}
```

`data/settings.json` 可以覆盖这些值，例如：

```json
{
  "ffmpeg": {
    "path": "D:\\tools\\ffmpeg\\bin\\ffmpeg.exe",
    "probe": "D:\\tools\\ffmpeg\\bin\\ffprobe.exe",
    "threads": 2,
    "timeout_s": 600
  }
}
```

用 `editing.toolchain()` 可以先确认环境：

```python
from comfy_mcp import editing

print(editing.toolchain())
# {'ffmpeg': 'C:\\ffmpeg\\...\\ffmpeg.exe', 'ffprobe': 'C:\\ffmpeg\\...\\ffprobe.exe',
#  'version': 'N-121437-gf4a87d8ca4-20251015', 'ok': True}
```

如果 `ok` 为 `False`，先检查 ffmpeg 是否安装、`ffmpeg.path` 是否写对。

---

## 2. 返回结构与错误

成功时所有剪辑函数返回同一结构：

```python
{
    "ok": True,
    "dst": r"K:\out\clip.mp4",     # 绝对输出路径（抽帧时为输出目录）
    "cmd": ["C:\\ffmpeg\\...\\ffmpeg.exe", "-hide_banner", ...],  # 实际执行的命令
    "probe": { ... },              # 对产物的 ffprobe 结果（见下）
    "warnings": [],                # 非致命提示，例如「copy 失败已回退重编码」
}
```

失败时抛出 `editing.EditError`，消息里带 ffmpeg stderr 的**末尾 2000 字符**，
方便直接定位是编码参数、滤镜还是文件路径的问题：

```python
from comfy_mcp.editing import EditError

try:
    editing.trim(src, dst)
except EditError as exc:
    print("剪辑失败:", exc)
```

`probe()` 返回字段：

| 字段 | 说明 |
| --- | --- |
| `path` | 绝对路径 |
| `width` / `height` | 视频宽高；纯音频为 `None` |
| `fps` | 平均帧率（`avg_frame_rate` 优先），纯音频为 `None` |
| `duration_s` | 时长（秒，来自 format 或流时长） |
| `video_codec` / `audio_codec` | 编码名（如 `h264` / `aac`），没有则为 `None` |
| `has_audio` | 是否有音轨 |
| `bitrate` | 总码率（bit/s） |
| `size_mb` | 文件大小（MiB） |
| `rotation` | 旋转角度（读取 `rotate` tag 或 side data），默认 0 |

---

## 3. 常用示例

```python
from comfy_mcp import editing

src = r"K:\CONFI\comfy_mcp\artifacts\sample.mp4"   # 320x180@24fps 2s，带 aac
src2 = r"K:\CONFI\comfy_mcp\artifacts\sample2.mp4"  # 无声 2s
out = r"K:\CONFI\comfy_mcp\artifacts\demo_output"

# 1) 裁剪：从 0.5s 开始取 1s（默认重编码，时长精确，保留音轨）
editing.trim(src, out + r"\trim.mp4", start_s=0.5, duration_s=1.0)

# 2) 拼接：直接首尾相接（自动统一分辨率/帧率；缺音轨的片段自动补静音）
editing.concat([src, src2, src], out + r"\concat.mp4")

# 3) 交叉淡化拼接：2+2-0.5 = 3.5s，视频 xfade + 音频 acrossfade
editing.concat([src, src], out + r"\xfade.mp4", crossfade_s=0.5)

# 4) 缩放/黑边/改帧率
editing.transform(src, out + r"\pad.mp4", width=240, height=240, pad=True)
editing.transform(src, out + r"\fps12.mp4", fps=12)

# 5) 混音：把 sample.mp4 的音轨替换到无声视频上（0.1s 延迟、0.8 音量）
editing.extract_audio(src, out + r"\audio.m4a")
editing.mux_audio(src2, out + r"\mux.mp4", audio=out + r"\audio.m4a",
                  volume=0.8, offset_s=0.1, replace=True)

# 6) 烧字幕（Windows 上自动把 srt 转 UTF-8 并复制到 ASCII 临时目录）
editing.burn_subtitles(src, out + r"\burned.mp4", out + r"\subs.srt",
                       font_size=28, margin_v=30)

# 7) 贴图（png 透明通道可用；scale 是相对视频宽度的比例）
editing.thumbnail(src, out + r"\logo.png", at_s=0.5, width=64)
editing.overlay(src, out + r"\overlay.mp4", out + r"\logo.png",
                position="top-right", margin=10, opacity=0.7, scale=0.2)

# 8) 变速：2 倍速（音调用 atempo 同步），也会保留音轨
editing.speed(src, out + r"\speed2x.mp4", factor=2.0, keep_audio=True)

# 9) 抽帧 / 抽音频 / 缩略图 / GIF
editing.extract_frames(src, out + r"\frames", fps=6)               # frame_000001.jpg 序列
editing.extract_audio(src, out + r"\audio.m4a")
editing.thumbnail(src, out + r"\thumb.jpg", at_s=1.0, width=640)
editing.to_gif(src, out + r"\anim.gif", fps=12, width=640,
               start_s=0.0, duration_s=2.0)

# 10) 进阶：直接跑任意 ffmpeg 参数（同样有全局参数/线程/原子写/超时保护）
editing.run_ffmpeg(
    ["-i", src, "-t", "1.0", "-c", "copy"],
    out + r"\copy_1s.mp4",
)
```

所有函数都会自动创建 `dst` 的父目录。

---

## 4. 函数与参数

> 首参兼容说明：SPEC 里 `mux_audio / burn_subtitles / overlay / extract_frames /
> extract_audio / thumbnail` 的第一个参数名是 `video`，而 MCP `edit` 工具的 schema
> 统一用 `src` 字段透传。本模块对这几个函数**同时接受 `src=` 关键字**
> （`functools.wraps` 保证 `inspect.signature` 仍显示 SPEC 约定的 `video`），
> 所以工具层可以直接 `fn(**kwargs)`。其余函数（`trim / transform / speed / to_gif`）
> 首参本来就是 `src`。

### 4.1 `probe(path)`
读取媒体信息（见上表）。文件不存在或格式非法抛 `EditError`。

### 4.2 `trim(src, dst, start_s=0.0, duration_s=None, reencode=True)`
裁剪片段，默认重编码并保留音轨。

| 参数 | 说明 |
| --- | --- |
| `start_s` | 起始时间（秒），默认 0 |
| `duration_s` | 裁剪时长（秒）；`None` 表示到结尾 |
| `reencode` | `True` 精确重编码；`False` 流复制（快，但起点受关键帧位置影响） |

### 4.3 `concat(inputs, dst, crossfade_s=0.0, reencode=True)`
拼接多个视频文件。

- 重编码模式统一「首段」的分辨率/帧率：等比缩放 + 黑边填充（`scale + pad`），
  因此不同分辨率/帧率的输入也能拼。
- **音轨不一致**：某段没有音轨时自动用 `anullsrc` 补等长静音；全部无音轨时输出无声视频。
- `crossfade_s > 0` 使用 `xfade` + `acrossfade`，必须小于最短输入时长，否则报错。
- 交叉淡化后的总时长公式（N 段，每段时长 dᵢ，淡化 x）：
  `total = Σdᵢ - (N-1) * x`。例如两段各 2s、`crossfade_s=0.5` → 3.5s。
- `reencode=False` 走 concat demuxer 流复制；如果输入参数不一致（分辨率/帧率/编码/音轨）
  或复制失败，会**自动回退重编码**，并在 `warnings` 中说明。

### 4.4 `transform(src, dst, width=None, height=None, fps=None, pad=False, reencode=True)`
缩放与改帧率。

- 只给 `width`：`scale=width:-2`（高度按比例，保持偶数）；只给 `height` 同理。
- 同时给宽高：默认拉伸到目标尺寸；`pad=True` 改为等比缩放 + 黑边居中。
- `reencode=False` 只能做「尺寸/帧率不变」的转封装，否则抛 `EditError`。

### 4.5 `mux_audio(video, dst, audio=None, volume=1.0, offset_s=0.0, replace=True, normalize=False)`

| 参数 | 说明 |
| --- | --- |
| `audio` | 音频文件；`None` 表示处理视频自带音轨 |
| `volume` | 音量倍数（0 静音；作用于 `audio`，`audio=None` 时作用于原音轨） |
| `offset_s` | 音频延迟（秒）；负数表示剪掉音频开头 |
| `replace` | `True` 替换原音轨；`False` 与原音轨 `amix` 混合（原视频无音轨时退化为替换） |
| `normalize` | `True` 使用 EBU R128 `loudnorm` 单遍响度归一化（目标 -16 LUFS） |

视频流 `-c:v copy` 不重编码，音频统一编码成 AAC；输出时长按视频时长截断（`-shortest`）。
视频没有音轨且 `audio=None` 时抛 `EditError`。

### 4.6 `burn_subtitles(video, dst, srt_path, font_size=24, margin_v=40)`
把 SRT 烧进画面（保留音轨）。Windows 关键处理：

1. 读取 srt 字节，按 `utf-8-sig → utf-8 → gb18030 → latin-1` 顺序解码，**统一写成 UTF-8**；
2. 复制到纯 ASCII 临时目录（优先 `%TEMP%`，其次 `%SystemDrive%\Temp` 等）；
3. 对滤镜里的路径转义盘符冒号（`C\:/...`）后再调用
   `subtitles='...':force_style='FontSize=...,MarginV=...'`；
4. 结束后删除临时目录。

`font_size` / `margin_v` 通过 `force_style` 传给 libass。字体由系统 fontconfig 解析，
中文默认字体的显示效果取决于本机安装的字体。

### 4.7 `overlay(video, dst, image_path, position="bottom-right", margin=20, opacity=1.0, scale=0.2)`
贴图叠加（保留音轨）。

- `position`：`top-left` / `top-right` / `bottom-left` / `bottom-right` / `center`（`middle` 同 center）。
- `margin`：距边缘像素（center 时忽略）。
- `opacity`：0~1，小于 1 时用 `colorchannelmixer=aa=` 做透明。
- `scale`：叠加图宽度相对视频宽度的比例（0.2 = 20%），范围 0.001~1。

### 4.8 `speed(src, dst, factor=1.0, keep_audio=True)`
变速。视频用 `setpts=PTS/factor`；`keep_audio=True` 且原视频有音轨时，
用串联的 `atempo`（每级限制在 0.5~2.0）保持音调不变地同步音频。
`factor=2` → 时长减半；`factor=0.5` → 时长翻倍。`keep_audio=False` 输出无声视频。

### 4.9 `extract_frames(video, out_dir, fps=None, start_s=None, end_s=None, prefix="frame")`
抽帧为 `<out_dir>/<prefix>_000001.jpg`（`-q:v 2`）。

- `fps=None` 导出全部帧；建议对大视频指定 fps。
- `start_s`/`end_s` 为秒；`end_s` 必须大于 `start_s`。
- 返回的 `probe` 形如：`{"count": 12, "files": ["frame_000001.jpg", ...], "width": 320, "height": 180}`；
  `files` 最多列出前 20 个，`files_truncated` 标记是否截断。
- 一帧都没抽到时抛 `EditError`（常见于时间范围超出视频长度）。
- 多文件输出无法使用 `.part` 原子替换；输出目录建议每次使用空目录。

### 4.10 `extract_audio(video, dst)`
抽取音轨，按扩展名选编码器：

| 扩展名 | 编码 |
| --- | --- |
| `.m4a` / `.mp4` / `.aac` | AAC（192k） |
| `.mp3` | libmp3lame（192k） |
| `.wav` | pcm_s16le |
| `.flac` | flac |
| `.ogg` / `.opus` | libvorbis / libopus（192k） |

视频没有音轨时抛 `EditError`。

### 4.11 `thumbnail(video, dst, at_s=0.0, width=1280)`
抓取指定时刻的单帧图片（`.jpg` / `.jpeg` / `.png`）。
`at_s` 超出视频时长时自动回退到时长末尾前 0.1s，不会报错；`width` 按比例缩放。

### 4.12 `to_gif(src, dst, fps=12, width=640, start_s=0.0, duration_s=None)`
调色板两遍法生成高质量 GIF：

1. `palettegen=stats_mode=diff` 生成调色板；
2. `paletteuse=dither=bayer:bayer_scale=5` 用调色板编码，`-loop 0` 无限循环。

调色板文件为 `<dst>.palette.png`，调用结束自动删除。`width` 保持偶数，GIF 无音轨。

### 4.13 `run_ffmpeg(args, dst, timeout_s=None)`
进阶通用入口。`args` 是 ffmpeg 参数列表；**允许**省略开头的 ffmpeg 可执行文件、
**允许**把 `dst` 作为最后一个参数传进去（会自动识别并替换成 `.part`）。
本函数负责补充全局参数、`-threads`、原子落盘、超时保护，返回：

```python
{"ok": True, "cmd": [...], "stderr_tail": "...", "dst": "...", "duration_s": 1.0}
```

`dst` 扩展名决定容器（`.mp4/.mov/.mkv/.webm/.gif/.jpg/.png/.mp3/.m4a/.wav/...`）；
未知扩展名请自己在 `args` 里带 `-f <muxer>`。不接受字符串形式的 `args`（禁止 shell 拼接）。

---

## 5. MCP `edit` 工具映射

`server.py` 的 `edit` 工具参数为 `{"action": "...", ...}`，`action` 与函数对应：

| action | 对应函数 |
| --- | --- |
| `trim` | `trim` |
| `concat` | `concat` |
| `transform` | `transform` |
| `mux_audio` | `mux_audio` |
| `burn_subtitles` | `burn_subtitles` |
| `overlay` | `overlay` |
| `speed` | `speed` |
| `extract_frames` | `extract_frames` |
| `extract_audio` | `extract_audio` |
| `thumbnail` | `thumbnail` |
| `to_gif` | `to_gif` |

其余参数同名直接透传（如 `{"action": "trim", "src": "...", "dst": "...",
"start_s": 1.0, "duration_s": 3.0}`）。

---

## 6. 常见坑（Windows / ffmpeg）

1. **不要用字符串拼命令**。路径里出现空格、中文、括号时字符串拼接会被 cut；
   本模块全部用 list 参数，调用方也请传原始路径字符串。
2. **`.part` 与容器格式**。ffmpeg 靠扩展名推断容器，`out.mp4.part` 会推断失败；
   模块内部对已知扩展名显式加 `-f <muxer>`，所以请使用支持的扩展名。
3. **流复制（`reencode=False`）不是万能的**：改分辨率/帧率/fps/交叉淡化都需要重编码；
   copy 裁剪的精度受关键帧限制。需要精确结果时保持默认 `reencode=True`。
4. **时长不是绝对精确**。AAC 编码器 priming、容器时间基、`-ss` 定位方式都会带来
   几十毫秒误差，判断时用容差（测试里用 ±0.2s）。
5. **字幕滤镜的路径转义**是 Windows 最大的坑：盘符冒号会被 filtergraph 当成选项分隔符，
   反斜杠会被当成转义符。本模块的做法是「UTF-8 + ASCII 临时目录 + `C\:/...` 转义」；
   如果自己写 `-vf subtitles=...`，务必照抄这个思路。
6. **字幕编码**：libass 只认 UTF-8。GBK/ANSI 的 srt 会让中文变乱码或直接报错，
   本模块会自动转码；自己调用 ffmpeg 时请先另存为 UTF-8。
7. **concat 混音**：一段有音轨、一段没有时，`concat` 滤镜要求每段都有音频流；
   本模块用 `anullsrc` 补静音，自己写滤镜时注意别直接 `concat=a=1`。
8. **批量抽帧**会生成很多小文件（2s@24fps = 48 张，长视频请务必传 `fps`）；
   抽帧目录建议每次用新的空目录，避免旧帧混入统计。
9. **线程与超时**：`ffmpeg.threads` 默认 4，避免和 ComfyUI 抢 CPU；长视频转码
   若超过 `ffmpeg.timeout_s`（默认 1800s）会被终止，请按需调大。
10. **磁盘空间**：剪辑产物默认写到调用方给的 `dst`；建议输出到 `artifacts/` 并定期清理。
11. **原子写**：输出成功前不会出现目标文件；异常中断只可能残留 `<dst>.part`，
    模块会在失败路径清理它。抽帧是多文件输出，不使用原子替换。
12. **GIF 体积**：GIF 是 256 色调色板格式，尺寸/帧率越大体积增长越快；
    先用 `fps=12、width=640` 试，必要时降到 `fps=10、width=480`。

---

## 7. 测试

```powershell
& 'K:\CONFI\mian\ComfyUI_windows_portable\python_embeded\python.exe' `
  -m unittest discover -s K:\CONFI\comfy_mcp\tests -p test_editing.py -v
```

测试使用 `artifacts/sample.mp4`（320x180@24fps 2s，带 aac）与 `artifacts/sample2.mp4`
（无声 2s），覆盖：工具链、probe、trim（重编码/流复制/中文空格路径）、
concat（重编码/混音轨/无声/交叉淡化/流复制/非法参数）、transform（缩放+pad/帧率/单边宽/
copy 拒绝）、mux_audio（替换/混合/响度归一化/无音轨报错）、burn_subtitles（UTF-8 与 GBK 字幕）、
overlay（png 透明/非法位置）、speed（加速/减速/去音轨）、extract_frames（整段/区间/越界）、
extract_audio（成功/无音轨）、thumbnail（jpg/png/超时长）、to_gif（两遍调色板）、
run_ffmpeg（直接调用/失败/超时/拒绝字符串参数）。

全部产物写在独立 `tempfile.mkdtemp()` 目录，测试结束删除；整套耗时通常 < 15 秒。
