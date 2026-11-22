# 情况通报 · 2026-11-14 19:02 W4A8 @1280×720 显存耗尽导致 ComfyUI 卡死

> 现象：ComfyUI 队列卡在 1 个 running 任务，`/system_stats` 返回 HTTP 500，Web UI 显示 ComfyUI 不可用；CPU/GPU 空闲但显存 7818/8188MB 占满，任务永不结束。
> 处置：已执行 `stop_all.ps1 -Comfy` 停止 Web(8940) + ComfyUI(12032)；端口 8188/8190 已释放，显存 390MB、commit 从 56.2GB 回落到 18.9GB。

## 一、卡死任务

| 项 | 值 |
|---|---|
| 任务 ID | `2de5f8b1-6349-49e7-bb9f-9d3e98c83b8e` |
| 提交时间 | 2026-11-14 19:02:29（Web UI 提交） |
| 模板 | `minimax_h3_r2v`（参考图） |
| 模型 | **W4A8**：`minimax_h3_fl2va_pruned_w4a8_mixed.safetensors`（11.68GB） |
| 分辨率 | **1280×720**（节点参数对齐为 1280×704） |
| 帧数/时长 | **362 帧 / 15.08s** |
| 步数 / 优化 | 8 步 / `optimize=lowmem`（Sage + LowVRAMAttention + ChunkFF） |

## 二、故障链（日志证据）

`logs/comfy_stderr.log`（18:15 重启后）：

1. 行 132 `got prompt` → VAE/TE/W4A8 模型装载：`Model MiniMaxH3 prepared for dynamic VRAM loading. **11956MB Staged**`。
2. 行 139（采样进度 `0/8`，**模型初始化阶段**）：
   `aimdo: src/hostbuf.c:283:ERROR:hostbuf_read_file_slice: device copy failed`
   → `RuntimeError: HostBuffer.read_file_slice failed`
   → `torch.AcceleratorError: CUDA error: out of memory`（`cudaErrorMemoryAllocation`）
3. 行 343 `Got an OOM, unloading all loaded models` —— 但 CUDA 上下文已失效：卸载路径里的 `torch.cuda.mem_get_info` 同样抛 OOM。
4. 行 344 起 `prompt_worker` 线程异常退出；此后**每个请求**（`/system_stats`、`/free`、`/interrupt`）都在 `cudaMemGetInfo` 处报 `CUDA error: out of memory` → `/system_stats` 返回 500。
5. 行 630 `Interrupting prompt 2de5f8b1`（人工尝试中断）**无效**——上下文已死，队列条目无法清除。
6. 全程**无** K-Power 41 / 6008 / LiveKernelEvent 141 / nvlddmkm 4101 / WHEA —— 不是驱动级挂死或蓝屏，是 ComfyUI 进程内 CUDA 显存耗尽后上下文损坏。

## 三、根因

**W4A8 模型 + 1280×720 + 362 帧，显存需求远超 8GB，在模型初始化/首步就把 CUDA 显存打爆。**

- W4A8 只压缩**模型权重**（11.68GB vs int8 19.5GB），**不减少**激活/latent/VAE 解码显存；1280×720（0.92MP）是 608×352（0.214MP）的 **4.3 倍像素**，362 帧再乘帧数。
- 已知 W4A8 在 **608×352 就会 VAE 解码卡死 >11 分钟**（见 `W4A8_DECODE_FINDINGS.md`）；这次直接上 1280×720，在采样初始化阶段即 OOM。
- 策略（`GENERATION_POLICY_20261111.md`）规定 **W4A8 仅限 480×288、15s 段最大 608×352**，本次两项都被突破，且 Web UI 没有对应硬拦截（`max_pixels=2.08MP` 允许 1280×720）。

**次生问题**：CUDA OOM 后 ComfyUI 没有自动恢复机制（context 已不可用，interrupt/free 均失效），任务永久卡在 running，`/system_stats` 500 让 MCP/Web 误判为服务异常。

## 四、处置与恢复

1. ✅ 已停止服务：Web pid 8940、ComfyUI pid 12032；8188/8190 已关闭。
2. ✅ 显存/内存已释放：VRAM 390MB、RAM 18.9GB、commit 18.9GB。
3. 任务记录 `2de5f8b1` 仍为 `running`（stale），下次启动 ComfyUI 后 MCP 对账会自动清理。
4. 重启服务：`start_all.bat -NoHarness`（或 `powershell -File start_all.ps1 -NoHarness`）。

## 五、防止复发（建议，待确认）

| 措施 | 说明 |
|---|---|
| **硬性分辨率上限** | 长段（length>124）最大 **608×352**；W4A8 一律 **≤480×288**（违者 `workflow.resolve` 直接报错） |
| Web UI 联动 | 选 W4A8 时把分辨率选项锁到 ≤480×288，或提交前弹提示 |
| 自动恢复 | MCP/`auto_film` 检测到 `/system_stats` 500 + 日志出现 `CUDA error: out of memory` → 自动 kill + 重启 ComfyUI，清理 stale 任务 |
| 提交前检查 | 显存需求估算：`pixels × frames` 超阈值直接拒绝（当前 `max_pixels` 只按单帧像素，未考虑帧数） |

## 六、证据文件

- `logs/comfy_stderr.log`（本次运行完整栈，行 132–730）
- `data/jobs.json`（任务 `2de5f8b1` 参数）
- `/queue` 快照：`queue_running=1`，`UNETLoader=w4a8`、`MiniMaxH3ReferenceToVideo width=1280 height=704 length=362`
- `logs/mcp.log`、`logs/webapp_stderr.log`（对账与 500 记录）
