# 视频自动匹配 / 进程清理 / 自动结束压制（2026-11-22）

## 1. 按规格自动匹配

- 时长 < 12s：直出模式；
  - 720p 10s 直出（实测可行）；
  - 720p >10s 自动启用 H3 上下文窗口 + 分块 VAE + optimize=max；
- 时长 >= 12s：自动转分段模式（`segment_film`）：
  - 每段 5s（124 帧）；
  - 段数 = ceil(时长/5)，最少 3 段；
  - 保留 @ 素材/ref_images，首段用参考图，后续首帧链式续接；
  - 返回 `{ok:true, segmented:true, tag, segments}`，前端提示“已转分段模式”。

## 2. 任务期间清理无用进程（开关）

- `data/settings.json`：
  ```json
  "process_cleanup": {"enabled": true, "auto_stop_encode": true}
  ```
- 启动器/Studio「设置」页有复选框：任务期间清理、任务完成后自动结束压制；
- 只清理：闲置 ffmpeg/ffprobe（>3 分钟且 CPU<3%）、超 2 小时的临时 tools 脚本、超 10 分钟的 tunnel.ps1；
- 保护：ComfyUI、Web、MCP、launcher、cloudflared、segment_film 及其父进程。

## 3. 任务完成自动结束压制

- 任务进入 completed/failed/cancelled 后，`proc_cleaner.finish_job()` 调用
  `cleanup(force_encoders=True)`，结束超过 30s 的 ffmpeg/ffprobe 压制进程；
- 开关关闭时不做任何清理。

## 4. 接口

- `POST /api/generate`：>=12s 自动返回 segmented 响应；
- `GET/POST /api/film/state|start|cancel`：分段任务状态/启动/取消；
- 清理日志：`logs/process_cleanup.log`。
