# MiniMax-H3 本机优化笔记（8GB VRAM / 32GB RAM）

> 2026-11-09 实测 + 联网调研整理。所有"本机实测"都来自这台 RTX 4070 Laptop（8GB）/ 32GB RAM / ComfyUI 0.33.1。

## 1. 本机已实测的耗时基线

| 配置 | init（Model Initializing） | 采样（4 步） | 总耗时 | 产物 |
|---|---:|---:|---:|---|
| **15s / 362 帧**，608×352，4 步，无优化，冷启动 | 11:41 | 9:38 | **22:26** | 1.78 MB |
| **5s / 124 帧**，608×352，4 步，无优化，冷启动 | 1:36 | 0:53 | **4:58** | 0.55 MB |
| 5s / 124 帧，热缓存（模型驻留，min_ram 放宽到 2.5） | 见 2.x 补充 | — | — | — |

关键观察：**init 与采样都随帧数显著下降**。5s 段总时间是 15s 段的约 1/4.5。

## 2. 优化手段（按"收益/成本"排序）

### 2.1 缩短分镜时长（最直接、已验证）

- 15s→5s：单段总耗时 22:26 → 4:58（init 也从 11:41 降到 1:36）。
- 但 1 分钟成片段数会从 4 段变 12 段：**12 × ~5 min ≈ 60 min**，与 4 × 15s（约 60–90 min）接近；好处是分镜更丰富、单段失败重试成本低（重跑一段只要 5 分钟）。
- 推荐：**8–10s 段（243 帧以内）**做 1 分钟成片（6–7 段），兼顾分镜丰富度与总时间。

### 2.2 已装节点级加速（无需下载，本服务已支持 `optimize` 参数）

`workflow.resolve()` 新增 `optimize` 档位（`off/sage/lowmem/max`），在 UNET/LoRA 后串接：

| 档位 | 节点链 | 作用 |
|---|---|---|
| `sage` | `MiniMaxH3MemoryEfficientSageAttentionPatch` | SageAttention 替代 SDPA：社区 microbenchmark 约 2.4x attention 加速，端到端 DiT 段约 1.2x |
| `lowmem` | +`MiniMaxLowVRAMAttention(head_chunks=4)` +`MiniMaxChunkFeedForward(chunks=2)` | 降低 attention/FFN 峰值显存，输出与原模型一致（KJNodes） |
| `max` | +`TESpeedMiniMaxH3`（4-step 模式） | TE-Speed 3.2 的 H3 专用缓存/分块加速，配 4-step LoRA |

示例：`"optimize": "max"`、`"optimize": "lowmem"`。四种档位已通过 `validate()`（141 个单测全绿）。

### 2.3 模型级（需要下载，收益最大）

| 方案 | 大小 | 说明 |
|---|---|---|
| **W4A8 ConvRot**（AX1Y2JP/MiniMax-H3-W4A8-ConvRot） | 12.5 GB（vs pruned int8 21 GB） | 社区 3070 Ti 8GB + 16GB RAM 实测：**5s、864×480、4-step、SageAttention 总计约 80–90 秒**；comfy-kitchen 的 W4A8/ConvRot kernel 在本机可启用（日志已显示 w4a8_int8 可用） |
| GGUF Q2/Q3 + GGUF 文本编码器 | 6–10 GB | 8GB 档推荐，质量有损；ComfyUI-GGUF 已装 |
| 更小模型（Wan2.1 T2V 1.3B / LTX-Video） | 2–8 GB | 8GB 原生友好，5s 480p 分钟级；需新增模板 |

### 2.4 参数与启动

- **LoRA 强度 0.75**（社区横评建议）：强度 1.0 时画面易偏离提示词；4 步 LoRA 不要加到 6/8 步（可能声音异常）。
- **SageAttention 对 5s 有效，对 15s 无本质帮助**；TE-Speed 挑 LoRA（lightx2v 配合较好，与 kijai v1 搭配可能糊）。
- **不要叠加** `--lowvram` / `--cache-none` / `--disable-smart-memory`，遇 OOM 再逐个加；本机现有 `--fast --cuda-malloc` + DynamicVRAM + pinned + 2-stream async 已接近社区最优。
- `torch.compile` / CUDA Graph 与 mixed quant + DynamicVRAM 不兼容，不要开。
- 内存优先：32GB RAM 是关键；生成前关浏览器等；模型常驻时 `min_ram_free_gb` 需临时放宽（本机 8→2.5）才能继续提交。

### 2.5 流程级

- 每段前 **kill + 重启 ComfyUI** 走冷启动，本机实测 init 1:36–5:08，比热缓存卡死（15–25 分钟）更稳。
- 生成失败重试：卡在 `Model Initializing` 超过 ~10 分钟就 kill 重启重提。
- 先出 5s 草稿确认提示词/构图，再用同参数出 10–15s 成段。

## 3. 联网调研来源

- [MiniMax H3 显存需求与量化选择（8/12/16/24/32GB）](https://minimaxh3.app/zh/posts/minimax-h3-vram-requirements)：8GB 现实门槛是 GGUF Q2/Q3 或 int4 + 32GB 内存；内存是真正瓶颈；Turbo LoRA 提速大于量化切换；先缩时长再降分辨率。
- [RTX 3070 Ti 8GB 上加速 H3（日文实测）](https://zenn.dev/voxeldraft/articles/bb4ffa02b3e823)：W4A8 + SageAttention + 4-step LoRA + DynamicVRAM/2-stream，864×480 5s 约 80–90s；SageAttention 让 DiT 段 70→58 秒。
- [8GB 显存加速 LoRA 横评（掘金）](https://juejin.cn/post/7671692631495344163)：5s 736×416 约 5–6.5 分钟；10s 约 13 分钟；**15s 在 8GB 上全部 OOM**；LoRA 强度 0.75；TE 加速挑 LoRA。
- [Kijai/MiniMax-H3_comfy（低显存 attention / chunk FF / 加速 LoRA 来源）](https://huggingface.co/Kijai/MiniMax-H3_comfy)
- [ComfyUI MiniMax H3 官方教程](https://docs.comfy.org/tutorials/video/minimax/minimax-h3)
- [AX1Y2JP/MiniMax-H3-W4A8-ConvRot](https://huggingface.co/AX1Y2JP/MiniMax-H3-W4A8-ConvRot)

> 说明：以上社区数据来自 2026-08~09 的第三方报告，硬件/版本不同结果会差很多；本机数据才是唯一直接可比基线。
