# GPU 掉驱动事件调查报告（2026-11-10 19:38–19:40）

> 结论先行：**不是显存爆，也不是超分算法问题；直接原因是「虚拟内存（commit）耗尽」**。
> 超分期间 ComfyUI 仍驻留上一段生成的 H3 int8 模型（约 27.4GB commit），叠加超分进程 6.5GB + ffmpeg 1.6GB，
> 而系统 pagefile 仅 6.5GB（commit 上限约 38.5GB），19:38:56 Windows 判定虚拟内存耗尽，
> 随后 GPU 驱动 WATCHDOG 挂起（GPU lost）、系统冻结，19:40 被强制重启。

## 1. 时间轴（2026-11-10，均为本机时间）

| 时间 | 事件 |
|---|---|
| 18:25:42 | 第 1 段生成完成（脚本每段前 kill+重启 ComfyUI） |
| 18:56–19:01 | 第 8 段生成（ComfyUI 启动后**未再停止**，H3 int8 模型常驻） |
| 19:01:31 | 8 段全部完成，开始 concat |
| 19:01:33 | 60s 成片完成；**4x 超分启动**（RealESRGAN_x4plus，608×352→2432×1408，1426 帧） |
| 19:01–19:38 | 超分运行（约 0.6 fps，已完成约 1392 帧）；ComfyUI 仍占 ~27.4GB commit |
| **19:38:56** | **Windows Resource-Exhaustion-Detector（System 事件 ID 2004）**：诊断到虚拟内存不足；python.exe(9484) 占 **27,396,689,920 B ≈ 27.4GB**、python.exe(4784) 占 6.5GB、ffmpeg.exe(21436) 占 1.6GB |
| 19:38→19:39:16 | GPU 驱动挂起，生成 WATCHDOG dump：`C:\Windows\LiveKernelReports\WATCHDOG-20261110-1938.dmp`（**1944MB**） |
| 19:39:17 | 4x 输出文件最后一次写入（103.9MB / 58.79s / 1392 帧） |
| 19:39:20 | 超分进程返回失败；`film_1min.log` 记录 `upscale: FAILED` |
| 19:39:27 | `nvidia-smi` 报 **GPU is lost（0000:01:00.0）**；`torch.cuda.is_available()=False`、`device_count=0` |
| 19:40:15 | 系统非正常关机（Application/System 事件 6008：unexpected shutdown） |
| 19:40:26 | 重启动后生成第二个小 dump `WATCHDOG-20261110-1940.dmp`（0.4MB） |
| 19:40:55 | Kernel-Power **事件 41**（Critical）：系统未正常关机就重启 |
| 19:41:10 | EventLog 事件 6008：上次关机发生在 19:40:15，属意外 |
| 19:45 | 复检 GPU 正常：RTX 4070 Laptop、驱动 **621.14**、62–65°C、空载 31W |

## 2. 关键证据

- **内存事件**（最直接）：`Microsoft-Windows-Resource-Exhaustion-Detector` ID 2004，19:38:56，
  明确列出三个进程的虚拟内存占用（27.4GB / 6.5GB / 1.6GB）。
- **pagefile 只有 6.5GB**：`C:\pagefile.sys` AllocatedBaseSize=6656MB（系统托管），
  32GB 物理内存 + 6.5GB 页文件 → commit 上限约 38.5GB，被上述进程占满。
- **LiveKernelReports**：`WATCHDOG-20261110-1938.dmp`（1.9GB，19:39:16）——GPU/驱动挂起的看门狗转储；
  重启动后再出现两个小 WATCHDOG dump，说明是驱动/内核看门狗路径，不是普通应用崩溃。
- **Kernel-Power 41 + Event 6008**：19:40:15 系统已挂起，只能强制重启（所以 nvidia-smi 报 GPU lost）。
- **进程身份**：4784 已确认是超分进程（`upscale_video.py ... film_cinematic_1min.mp4 --dst ..._4x.mp4`）；
  9484 占 27.4GB，只可能是 ComfyUI（H3 int8 模型 + 缓存驻留），系最后一段生成后未停止所致。
- **温度背景**：超分期间 GPU 98%、88–90°C、显存约 5.2GB（显存不是瓶颈；温度偏高但非直接死因）。
- **驱动/TDR**：驱动 621.14；`TdrDelay` 未显式设置（系统默认）；Power Limit 上限 140W。

## 3. 根因链

```
最后一段生成完未停 ComfyUI（H3 int8 模型驻留 ~27.4GB commit）
   + 超分进程 ~6.5GB commit
   + ffmpeg 编码 ~1.6GB
   = 约 35.5GB > 32GB RAM + 6.5GB pagefile（commit 上限 ~38.5GB，且系统自身还要占用）
→ 19:38:56 Windows 判定 low virtual memory
→ 为分配/换页压力下，GPU 驱动引擎挂起（WATCHDOG dump 1.9GB）
→ GPU lost，超分中断；系统冻结 → 19:40 强制重启
```

高温（88–90°C）可能降低了驱动挂起阈值，但不是主因——显存只用了 5.2GB/8GB。

## 4. 已应用的修复（代码，已验证编译）

1. `tools/run_1min_film.py`
   - `post_process()` 开始前 **kill ComfyUI**（释放 20GB+ commit）；
   - 超分前检查 `commit free < 8GB` 则跳过超分并记录，避免再次触发；
   - 超分调用增加 `--pause-every 180 --pause-s 20`（每 180 帧休息 20s）。
2. `tools/upscale_video.py`
   - 新增 `--pause-every` / `--pause-s`（周期休息散热、降低持续压力）；
   - 新增 `--max-temp`（默认 88°C）/`--temp-check-every`：超过温度自动暂停降温（每 24 帧查一次 nvidia-smi）。

## 5. 可行解决方案（按优先级）

### 立即（避免复发）
1. **超分前卸载模型**：kill ComfyUI 或 `mcp__comfy__free`；确认 `commit free > 10GB` 再开始超分。
2. **增大 pagefile**：系统属性 → 高级 → 虚拟内存，C 盘设「自定义 16–32GB」（或把页文件放到空间更大的盘），
   重启动后 commit 上限从 ~38.5GB 提升到 50GB+，给 ComfyUI 驻留 + 超分留足余量。
3. **超分加散热节流**（已实现）：`--pause-every 180 --pause-s 20 --max-temp 85`。

### 短期（本机稳定性）
4. **控温**：GPU 长期 88–90°C 偏高——抬高机身、清理风道、外接散热垫；用厂商工具/MSI Afterburner
   把温度上限设 80–85°C、Power Limit 设 90–110W（上限 140W），风扇曲线更激进。
5. **避免同时驻留**：生成任务结束后统一 `kill ComfyUI`（或改脚本每段后 kill，而非只段前 kill）。
6. **监控**：超分脚本每 24 帧记录温度/显存；Web 顶部已显示 GPU/温度/RAM，可人工盯。
7. **驱动**：当前 621.14；若再出现 WATCHDOG/GPU lost，尝试更新或回滚一个稳定版驱动；
   可关闭「硬件加速 GPU 计划（HAGS）」降低驱动挂起概率。

### 长期（如频繁复发）
8. 检查散热硅脂/风扇/供电（笔记本长期高负载易积灰）；
9. 把超分拆成多段进程（每 5 分钟一段独立进程），从根本上避免长时间单进程高负载；
10. 需要 4x 大图时优先在生成阶段直接出更高分辨率/更长片段，减少后处理时长。

## 6. 附：日志与证据位置

- `logs/film_1min.log`：段生成/拼接/超分时间线（19:01:33 开始超分、19:39:20 FAILED）
- `logs/comfy_stderr.log`：ComfyUI 段生成日志
- `C:\Windows\LiveKernelReports\WATCHDOG-*.dmp`：驱动看门狗转储（19:38 主 dump 1.9GB）
- 事件查看器：System → `Resource-Exhaustion-Detector`(2004, 19:38:56)、`Kernel-Power`(41, 19:40:55)、EventLog 6008
