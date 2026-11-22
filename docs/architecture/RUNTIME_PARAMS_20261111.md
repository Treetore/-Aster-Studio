# 运行时参数调整 · 2026-11-11 15:12

> 背景：14:50 发生 GPU 引擎超时（LiveKernelEvent **0x141**，`C:\Windows\LiveKernelReports\WATCHDOG\WATCHDOG-20261111-1450.dmp`，挂起进程 `python.exe`），整机冻结后硬重启。
> 本文记录为降低复发风险所做的参数调整、验证结果与回滚方法。

## 1. 系统级（管理员，已应用）

注册表 `HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers`：

| 值 | 旧值 | 新值 | 说明 |
|---|---|---|---|
| `TdrDelay` | 不存在（默认 2s） | **60** | 长内核/大块拷贝不再被 2s 误判为挂死 |
| `TdrDdiDelay` | 不存在 | **60** | DDI 调用超时同步放宽 |

- **生效时机：重启后**（显示驱动重新加载时读取；未重启前仍是旧阈值）。
- 已创建 `C:\Windows\Minidump`（此前该目录不存在，导致本次事故无 minidump 可分析；`CrashDumpEnabled=3`、`AutoReboot=1` 保持不变）。
- 脚本：`tools\apply_tdr_windows.ps1`（幂等，可重复执行；纯 ASCII，避免 PowerShell 5.1 无 BOM 中文解析问题）。
- 另外开启了用户级 WER 本地转储（无需管理员）：`HKCU\Software\Microsoft\Windows\Windows Error Reporting\LocalDumps\python.exe`
  → `DumpFolder=K:\CONFI\comfy_mcp\logs\wer_dumps`、`DumpType=1`(mini)、`DumpCount=5`，用于以后 python/ComfyUI 崩溃取证。

## 2. ComfyUI 启动参数（`data/settings.json`）

| 项 | 旧 | 新 | 理由 |
|---|---|---|---|
| `--fast` | 裸参＝开启全部未测试特性 | `--fast fp16_accumulation` | 关闭 `autotune`/`cublas_ops`/`fp8_matrix_mult`（官方注明「might crash your comfyui」） |
| `--vram-headroom` | 无 | `1.0` | DynamicVRAM 额外预留 1GB 显存 |
| lowmem / vram_only 档 `--reserve-vram` | `0.8` | `1.0` | 给 OS/桌面留更多显存 |

实际启动命令（已通过 MCP 验证）：

```
python.exe -s ...\ComfyUI\main.py --windows-standalone-build --listen 127.0.0.1 --port 8188 \
  --fast fp16_accumulation --cuda-malloc --disable-auto-launch --vram-headroom 1.0
[lowmem 档追加] --lowvram --cache-none --reserve-vram 1.0
```

## 3. 资源守卫（`data/settings.json`）

| 项 | 旧 | 新 |
|---|---|---|
| `min_ram_free_gb` | 2.5 | **3.0** |
| `trip_ram_free_gb` | 1.0 | **1.2** |

目的：内存压力过大时让 MCP 守卫**提前中止任务**，而不是拖到系统级冻结。

## 4. 回滚

```powershell
# 1) 设置回滚（保留的备份）
Copy-Item K:\CONFI\comfy_mcp\data\settings.json.bak-20261111-1508 K:\CONFI\comfy_mcp\data\settings.json -Force
# 2) 注册表回滚（管理员）
Remove-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\GraphicsDrivers' -Name TdrDelay,TdrDdiDelay
# 3) WER 本地转储回滚
Remove-Item 'HKCU:\Software\Microsoft\Windows\Windows Error Reporting\LocalDumps\python.exe' -Recurse -Force
# 4) 之后重启 ComfyUI / 系统
```

注意：`data/settings.json` 其余启动/守卫参数以 MCP `settings` 工具为准（`mcp__comfy__settings action=get`），修改文件后需 disable/enable 插件或调用一次 `settings set` 才会被 MCP 进程重新加载。

## 5. 验证（2026-11-11 15:13）

- `main.py --help` 接受全部新参数（exit 0，无 unrecognized 报错）。
- MCP `start` → ComfyUI pid 30644，命令行含新参数；`/queue` 0/0。
- Web UI（8190）已恢复，`/api/status` 返回 `guards.min=3 / trip=1.2`。
- 启动日志确认：`Enabled pinned memory 13024.0`、`DynamicVRAM support detected and enabled`（pinned 仍为 40% RAM，未关闭）。

## 6. 暂未启用 / 后续可评估

- `--disable-pinned-memory`：直接绕开本次报错的 pinned host buffer 拷贝路径，但会损失一部分加载性能；**若 0x141 复现再启用**。
- `--fast-disk`：K: 盘为 SATA SSD（非 NVMe），不适合磁盘后备动态加载，**不建议**。
- `--disable-nvml-pressure`：动态显存压力检测改用 CUDA 路径，属备选实验项。
- 使用侧约束（比参数更重要）：15s 段不要同时开 `two_pass`；二采仅用于 ≤5s 段；15s 先用 4–6 步跑基线。
