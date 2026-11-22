# 本地生成策略（2026-11-11 定稿）

> 依据：两轮压力测试（[STRESS_TEST_1MIN_20261111.md](./STRESS_TEST_1MIN_20261111.md)）+ 两次 0x141 事故（[INCIDENT_20261111_1540_GPU_HANG.md](./INCIDENT_20261111_1540_GPU_HANG.md)）+ 参数调整（[RUNTIME_PARAMS_20261111.md](./RUNTIME_PARAMS_20261111.md)）。
> 适用：RTX 4070 Laptop 8GB / 32GB RAM / ComfyUI 0.33.1 / MiniMax-H3。

## 1. 约束基线（实测）

| 维度 | 实测值 | 含义 |
|---|---|---|
| VRAM | 8GB；15s 段运行峰值占用 7.86GB | 几乎贴顶，禁并发 |
| 主机内存 | 15s 段起跑需 ~23GB 空闲；运行中最低 **1.15GB**；commit 峰值 **47GB** | **第二硬约束**，比温度更容易触发失败 |
| 温度 | 负载段平均 **88.7°C**、峰值 **94.7°C**（设计目标 87°C） | 稳定运行在热极限之上，热限流偶发（个位数采样） |
| 功耗 | 平均 68W / 峰值 89–94W（上限 115W TGP、140W Max） | 被温度墙限制，Dynamic Boost 基本不介入 |
| 频率 | SM 2310 → 1080MHz 波动 | 热/功耗管理在持续调节 |
| 结论 | **温度不是崩机原因**（25 分钟 88–95°C 不崩）；**内存压力 + 换入路径**才是关键 | 策略以"控内存/控时长"为核心 |

## 2. 配置矩阵（照此选，不要即兴）

| 用途 | 时长 / 帧数 | 分辨率 | steps | optimize | two_pass | 模型 | 实测耗时 |
|---|---|---|---|---|---|---|---|
| 探针 / 构图草稿 | 2s / 56 | 608×352 | 4 | max | 可 | int8 + 4-step LoRA | ~1.5–2 min |
| 5s 段落 | 5s / 124 | 608×352 | 4 | max | **可**（仅 ≤5s） | int8 + LoRA | ~2.5–4 min |
| 10s 段落 | 10s / 238 | 608×352 | 8 | max | 否 | int8 + LoRA | ~4–6 min |
| **15s 长段（上限）** | 15s / 362 | 608×352 | **8–10** | **max** | **禁止** | int8 + LoRA | ~5.5–7 min |
| 1 分钟成片 | 4×15s 首帧衔接 | 608×352 | 8–10 | max | 否 | int8 + LoRA | ~24 min（实测 60.364s 成片） |
| 低显存备选（W4A8） | 5s / 124 | **480×288** | 4 | off | 否 | W4A8（11.68GB） | ~2.3 min |
| 二采精修 | **≤5s** | 608×352 → 1152×640 | 4 | off | 是 | int8 | 2s 约 6 min；**15s 禁** |

## 3. 参数规则

### 硬规则（代码已强制，违反直接报错）
1. **`two_pass` 仅允许 `length ≤ 124`（约 5.2s）**。15s 段开二采时 1152×640 × 362 帧的第二阶段单步实测 **20 分钟**，不可行。
2. **帧数上限 362（15.08s）**，超出自动截断并警告（模型训练区间 124–362）。
3. 宽高必须同时给；自动对齐 32 的倍数；像素上限 `max_pixels=1920×1088`。

### 软规则（触发警告）
4. 长段（≥260 帧，约 10s 以上）`steps > 12` 给警告；推荐 **8–10 步**（15 步耗时约 3 倍且无质量收益）。
5. `steps < 4` 或帧数 < 124 时画质不稳（训练区间下限）。

### 默认值（`config.py` / `settings.json` 已更新）
| 档位 key | 时长 | steps | megapixels |
|---|---|---|---|
| draft | 2s | 4 | 0.2 |
| short / standard | 5s | 4 | 0.2 |
| high | 5s | 8 | 0.2 |
| medium | 10s | 8 | 0.2 |
| compact | 15s | 8 | 0.2 |

`generation.policy`：`two_pass_max_length=124`、`max_recommended_steps=12`、`long_length_threshold=260`、`min_free_ram_gb=3.0`。

## 4. 提交前检查清单（顺序执行）

1. ComfyUI（8188）与 Web（8190）在运行；队列 0/0。
2. **跑长段前先释放内存**：`mcp__comfy__free`（RAM 应回到 ≥20GB；VRAM <2GB）。
3. 空闲 RAM ≥ 3GB（守卫准入）；15s 段建议 ≥20GB。
4. GPU 温度 < 80°C 再开跑（连续多段时允许 85–90°C）。
5. 显示由**核显**驱动（`nvidia-smi --query-gpu=display_active` 为 `Disabled`）——dGPU 挂死不拖垮桌面。
6. 关闭其它 GPU 重负载（浏览器硬件加速、游戏、渲染）。
7. 磁盘空闲 ≥ 8GB。

## 5. 运行时监控与中止阈值

- 遥测：`tools/capture_stress_telemetry.ps1`（2s 采样：温度/功耗/频率/利用率/限流标志/RAM/commit）。
- **立即中止**：
  - 温度 > 97°C 持续 30s，或热限流持续 > 1 分钟；
  - 空闲 RAM < 0.8GB（防整机冻结）；
  - 单段无进度 > 15 分钟；
  - 出现 `CUDA error` / LiveKernelEvent 141 / 冻屏迹象 → 停止后续段、保留现场。
- MCP 守卫：`min_ram_free_gb=3.0`、`trip_ram_free_gb=1.0`（**不要回调 1.2**：15s 段运行中最低 1.15GB，1.2 会误杀）。

## 6. 失败处置流程

| 现象 | 处置 |
|---|---|
| 卡 `Model Initializing` >10 分钟 | kill ComfyUI → 重启 → 重新提交（冷启动反而更快） |
| `CUDA error: unknown error` | 停止后续段；`free`；重启 ComfyUI；查事件 141；按 INCIDENT 流程取证 |
| 守卫中止（RAM < trip） | 先 `free` 再重跑；连续 2 次 → 降档（15s→10s，或加 `--disable-pinned-memory`） |
| 400 策略拒绝（two_pass 长段等） | 按返回原因改配置，不要绕过 |
| 单段耗时明显变慢（>1.5×） | 检查温度是否持续 >92°C、RAM 是否 <2GB，必要时 `free` 后重试 |

## 7. 已禁用 / 已排除（附依据）

| 项 | 状态 | 依据 |
|---|---|---|
| 15s + `two_pass` | **禁止**（代码强制） | 第二阶段单步 1222s（20 分钟），单段无法完成 |
| 15s + 15 步 | 不推荐（警告） | 采样 6.5 分钟 vs 4 分钟，质量无收益；15 步配置曾参与首次崩溃 |
| W4A8 @ 608×352 | 禁止 | VAE 解码卡死 >11 分钟（11-10 实验） |
| 15s 段不 free 直接连跑 | 不推荐 | commit 从 41GB 累积到 47GB，RAM 触底 1.15GB |
| dGPU 驱屏做长任务 | 不推荐 | 两次 0x141 崩溃均发生在 dGPU 驱屏状态，崩后整机冻结 |

## 8. 数据依据摘要

- 压力测试第一轮（8 步/max）：4×15s，24m57s 连续负载，温度 avg 79.6 / max 95.3°C，**零崩溃**。
- 第二轮 A（15 步/lowmem/two_pass）：第一遍 15/15 完成（此前第 5 步崩），二采不可行中止。
- 第二轮 B（10 步/max）：4 段全部完成，60.364s 成片，温度 avg 83.6 / max 94.7°C，负载段 avg 88.7°C，**零崩溃**。
- 两次真实崩溃的共性：dGPU 驱屏 + 内存高压力（commit 43–47GB）+ 15s 长段；故障点均在动态显存/LowVRAM 换入拷贝路径。

## 9. 变更与回滚

- `config.py`：`DEFAULT_SETTINGS.generation.profiles` 调整 + 新增 `policy`。
- `workflow.py`：`resolve()` 增加二采长度硬护栏 + 长段步数警告。
- `webapp/app.py`：校验错误由 500 改为 **400 + 明确原因**（前端可直接展示）。
- `data/settings.json`：已同步新档位与 policy（`mcp__comfy__settings get` 可查）。
- 回滚：恢复 `data/settings.json.bak-20261111-1508`（仅 settings）；代码回滚需撤销上述三处改动。
- 测试：`python -m unittest discover -s tests -t .` → **141 passed**。

## 10. 维护备注

- 改 `server.py` / `workflow.py` 后需 disable/enable 插件（MCP 侧）才生效；Web 侧重启 uvicorn 即生效（本次已重启）。
- 遥测 CSV 保留在 `logs/telemetry_*.csv`，出问题先看这三列：`temp_c`、`ram_free_mb`、`thermal`。

## 11. 自动化（一条命令跑完整条流水线）

新工具：`tools/auto_film.py`（双击版 `auto_film.bat`）。它把本策略的**检查、生成、监控、中止、重试、拼接、报告**全部自动完成：

```powershell
# 干跑：只做预检 + 解析参数，不生成
python tools\auto_film.py --dry-run --tag dry

# 正式：4×15s 一分钟成片（策略默认：8 步 / max / 无二采）
python tools\auto_film.py --segments 4 --duration-s 15 --steps 8 --optimize max --tag my_film

# 快速验证：单段 2s
python tools\auto_film.py --segments 1 --duration-s 2 --steps 4 --tag smoke
```

自动执行的流程：
1. **预检**：ComfyUI 未启动则自动拉起；等队列清空；**自动 `free()` 并等待 RAM 达标**（15s 段默认要求 ≥20GB，短段 ≥8GB）；检查起跑温度 ≤82°C、核显驱屏、磁盘 ≥8GB。
2. **生成**：按策略解析参数（二采长度护栏生效），逐段生成、抽取尾帧作为下一段首帧。
3. **监控与中止**（每 3 秒）：温度 ≥97°C 持续 30s → 中断；空闲 RAM <800MB → 中断；单段无进度 >15 分钟 → 判 stall；日志出现 `CUDA error` → 判失败。
4. **自愈重试**：段失败/stall 时自动 kill + 重启 ComfyUI，重试 1 次（`--retries` 可调）。
5. **产出**：拼接成片 + `ffprobe` 校验 + `artifacts/<tag>/report.json`、`report.md`、`logs/<tag>_telemetry.csv`。

退出码：`0` 成功 / `2` 预检失败 / `3` 生成失败或中止（报告里写明原因）。
已验证：`--dry-run` 预检通过；`--segments 1 --duration-s 2 --steps 4` 冒烟通过（247s，报告与遥测齐备）。
