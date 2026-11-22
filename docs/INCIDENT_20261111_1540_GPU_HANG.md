# 情况通报 · 2026-11-11 15:40 GPU 引擎超时（第二次）导致整机冻结

> 通报编号：INC-20261111-02（第一次为 14:50 的 INC-20261111-01）
> 通报时间：2026-11-11 15:55
> 影响系统：K:\CONFI 本地 ComfyUI 视频工作台（MiniMax-H3 / RTX 4070 Laptop 8GB / 32GB RAM）

## 一、事件基本情况

| 项 | 内容 |
|---|---|
| 发生时间 | 2026-11-11 15:40:34–15:40:56（生成过程中） |
| 现象 | 15s 视频生成进行到第 6/10 步时 GPU 引擎超时，ComfyUI 报 `CUDA error: unknown error` 并中止，整机停止响应，用户按电源键强制重启 |
| 重启时间 | 15:44:27（本次启动干净） |
| 事件定级 | **Ⅱ级（较大）**：1 小时内第 2 次同类整机冻结，需硬复位，存在文件系统/数据受损风险 |
| 与上次关系 | 与 14:50 事故**同源复发**（同一故障类别 LiveKernelEvent 0x141），上次的参数调整未奏效 |

## 二、事实经过（时间线）

| 时刻 | 事件 | 证据 |
|---|---|---|
| 15:24:38 | 系统重启（为启用 TdrDelay=60） | Kernel-General 12 |
| 15:29 | ComfyUI + Web UI 启动（新参数：`--fast fp16_accumulation … --vram-headroom 1.0`） | comfy_stderr 启动行 |
| 15:33:17 | 任务 `da4309ae` 提交：`minimax_h3_r2v`，**15s/362 帧**，608×352，**10 步**，`optimize=max` | data/jobs.json |
| ~15:35:00 | 模型初始化完成（1:43）；扩散模型 19995MB / TE 14956MB / VideoVAE 4965MB 经动态显存装载 | comfy_stderr |
| 15:35–15:39 | 采样第 1–5 步，约 31s/步 | 进度条 |
| ~15:39:3x–15:40:34 | 第 6 步卡住（进度条出现 52.78s/it 异常间隔），GPU 挂起 | 进度条 |
| 15:40:34 | `Prompt executed in 436.78 seconds` → `Fatal Python error: Aborted` | comfy_stderr |
| 15:40:47 | **LiveKernelEvent 0x141**（GPU 视频引擎超时，新指针 `ffff808e…`），同时记录 0x1b8 | Application 1001 |
| 15:40:52–54 | MCP 侧 `ComfyUI 不可达` → 守卫判定 `system_stats failed 3 times`（**这是挂死后的结果，不是原因**） | logs/mcp.log |
| 15:40:55 | 系统最后一次落盘；日志记录“上次关机是意外” | EventLog 6008 |
| 15:40:56.877 | 检测到**电源键按压**（非长按） | Kernel-Power 41 `PowerButtonTimestamp` |
| 15:44:27 | 重启完成；`BugcheckCode=0`（非蓝屏） | Kernel-General 12 / KP 41 |

## 三、现场证据

1. **LiveKernelEvent `0x141`（VIDEO_ENGINE_TIMEOUT_DETECTED）**，与新指针值，证明是本次新发事件，非历史残留。
2. **ComfyUI 崩溃栈**（两次事故均落在同一函数）：
   `cast_maybe_lowvram_patch([pin], dest, offload_stream)` → `cast_to_gathered` → `dest_view.copy_(tensor, non_blocking=True)` → `torch.AcceleratorError: CUDA error: unknown error`；随后 `reset_cast_buffers → offload_stream.synchronize()` 再次报同一错误 → 进程 abort。
3. **无蓝屏证据**：`BugcheckCode=0`；`C:\Windows\Minidump` 为空；无 `MEMORY.DMP`。
4. **无硬件总线错误**：近 1 小时无 WHEA-Logger 事件；无 nvlddmkm/Display 4101（驱动复位未成功）。
5. **转储缺失（证据局限）**：WER 报告引用 `WATCHDOG-20261111-1540.dmp` 与 `WATCHDOG4400-20261111-1540.dmp`，但两文件均未落盘（目录 mtime 15:40:47，WER 报告 15:47 生成）——冻结导致转储写入失败，本次无法做内核转储级分析。
6. **TDR 参数已生效但无效**：本次启动时 `TdrDelay=TdrDdiDelay=60`（15:11 写入、15:24 重启加载）。表现是“挂起→约 60s 后才报 141/CUDA 错误”，即**只延后了检测时间，没有阻止挂死**。

## 四、原因分析

### 直接原因
GPU 视频引擎（计算引擎）在 15s/362 帧长序列采样过程中超时挂死（0x141），CUDA 上下文失效后 ComfyUI 中止；整机在驱动恢复阶段失去响应，最终由电源键复位。

### 共同点（两次事故对比）

| 维度 | 14:50 事故 | 15:40 事故 |
|---|---|---|
| 任务形态 | 15s / 362 帧 / 608×352 | **相同** |
| 步数 | 15 | 10 |
| optimize | lowmem（Sage+LowVRAM+ChunkFF） | **max（Sage+LowVRAM+ChunkFF）** |
| two_pass | 开（1.9×） | 关 |
| 崩溃位置 | `cast_maybe_lowvram_patch` 显存换入拷贝 | **同一函数** |
| 结果 | 0x141 + 整机冻结 + 电源键 | 相同 |

→ **可排除“二采/步数/档位选择”作为单一触发条件**；两次唯一稳定共现的是：
1. **362 帧（15s）长序列**；
2. **动态显存（DynamicVRAM）+ LowVRAMAttention 补丁的权重换入/换出拷贝路径**；
3. 8GB VRAM 上装载 20GB 扩散模型 + 15GB 文本编码器 + 5GB VAE（pinned 主机内存 13GB）。

### 根本原因（判定）
在当前 ComfyUI 0.33.1 + 8GB VRAM 环境下，**MiniMax-H3 以动态显存方式跑 362 帧长序列时，显存换入路径（KJNodes LowVRAMAttention / VBAR cast 拷贝）会触发 GPU 引擎挂死**。属于“软硬件组合的工作负载越界”，不是单一参数错误；TdrDelay 调整不改变该结论。

### 未能排除
- NVIDIA 驱动 621.14 在该负载下的缺陷；
- GPU 供电/散热或硬件退化（现有日志无 WHEA 证据，无法排除）。

## 五、结论（通报定性）

1. **本次为 14:50 事故的同源复发**，故障类别为 GPU 视频引擎超时（0x141），非蓝屏、非掉电、非操作系统 bugcheck。
2. **15:11 的参数调整（TdrDelay/TdrDdiDelay=60、`--fast fp16_accumulation`、`--vram-headroom 1.0`、守卫 3.0/1.2）未能阻止复发**；TDR=60 仅把“挂起到判断”的等待从 2s 拉长到约 60s。
3. **触发条件已收敛**：15s/362 帧 + 动态显存/LowVRAM 换入路径；与步数、二采、optimize 档位无明显单一绑定。
4. **继续以 15s 段生成将大概率再次冻结**，且每次硬复位都增加文件系统与正在写入的 jobs.json/输出文件损坏风险。
5. 本次因转储未落盘，**缺少内核转储级证据**，结论基于 ComfyUI 栈、事件日志、WER 记录与两次对比。

## 六、影响与风险

- 生产中断：两次崩溃共损失约 15 分钟生成时间 + 2 次硬重启；当前队列清空、无新增产物。
- 数据：`jobs.json` 完好（任务 `da4309ae` 状态停留 `running`，重启后由 MCP 对账清理为 stale）；无已生成视频丢失。
- 风险：硬复位期间若正在写盘，可能损坏 `jobs.json` / 输出 mp4 / 页文件。

## 七、处置措施（已执行）

1. ✅ 15:52 用 `start_all.ps1 -NoHarness` 恢复 ComfyUI（8188）与 Web UI（8190），刷新 `data/comfy_pid.json`。
2. ✅ 保留 TdrDelay/TdrDdiDelay=60（利于后续定位，但需接受冻结时间变长）。
3. ✅ 事故证据归档：本文件 + `logs/comfy_stderr.log`（含完整崩溃栈）+ `data/jobs.json`。

## 八、后续工作（按优先级）

1. **暂停 15s 生成**（含 Web/MCP 两条入口），先以 **5s 段**跑通稳定性基线。
2. **定位补丁路径**：用 `optimize=off` 跑一次 15s（不带 LowVRAMAttention/Sage 补丁）——
   - 若稳定 → 锁定为 LowVRAM 补丁/动态显存换入问题，可考虑关闭 `optimize` 或升级/回退 KJNodes；
   - 若仍 0x141 → 转步骤 3。
3. **关闭动态显存**：`--disable-dynamic-vram`（回退估算式加载）再试 15s，观察是否仍挂死。
4. **降低换入压力**：`--disable-pinned-memory`；或 15s 用 512×288（latent 更小）。
5. **修 MCP 守卫误记**：init 期间 system_stats 3 次超时即中断任务（已知缺陷），并区分“挂死后不可达”与“任务本身异常”，避免污染任务状态。
6. **转储可靠性**：核查 WER 对 `LiveKernelReports` 的处置（本次转储丢失）；确认磁盘与转储策略。
7. 若 1–4 全部无效：按硬件/驱动方向排查（驱动回退/压力测试/降频/供电）。

## 九、证据局限

- 15:40 的 0x141 内核转储未落盘，无法解析挂死引擎/驱动栈；
- 无 RAM/commit 时间序列（MCP 仅记录阈值事件），无法量化崩溃瞬间内存压力；
- 任务由 Web 端提交，MCP `jobs.json` 仅保留状态与参数，不含 GPU 侧 Xid 细节。

## 十、附录：核显（iGPU）接管显示后的对照实测（2026-11-11 16:10–16:20）

用户于 16:10 重启并在 BIOS/厂商工具中启用 CPU 核显（Intel Iris Xe）承担显示输出，dGPU 的 `display_active` 变为 `Disabled`。此后用同一任务配置（`minimax_h3_t2v`，5s/124 帧，608×352，4 步，`optimize=off`）各做一次 1 秒间隔遥测探针：

| 指标 | A：dGPU 驱屏（16:00） | B：iGPU 驱屏（16:15） |
|---|---|---|
| 空闲功耗 / 温度 | ~33.8W / ~70°C | **~20W** / **62–68°C** |
| 负载峰值温度 | 92.5°C | **94.0°C** |
| 高负载段（util≥90）平均温度 | 88.5°C | 88.3°C |
| 高负载段平均 / 峰值功耗 | 69.4W / 77.3W | 62.8W / 76.3W |
| 高负载段 SM 频率 平均 / 最低 | 1873 / 1575 MHz | **1659 / 1260 MHz** |
| 任务结果 | 完成 252.9s | 完成 212.5s |

**判读**：
1. iGPU 接管显示仅降低**空闲**功耗与温度（-14W / -6~8°C），**未改善负载热墙**——峰值反而到 94°C，频率更低，说明散热仍是瓶颈；dGPU 仍只跑到 ~76W（远低于 115W TGP/140W 上限），符合"温度墙限制 Dynamic Boost"的判断。
2. 主要收益在**影响面**：桌面不再依赖 dGPU，若再发 0x141，桌面理论上可存活、驱动复位有机会完成、系统无需硬复位，且更易采集恢复证据（CUDA 错误、事件 141、可能的 nvlddmkm 4101）。
3. 两次 5s 探针在 92–94°C 下均成功完成，再次印证"≤5s 稳定、长任务（15s）+ 换入路径组合才触发挂死"。
