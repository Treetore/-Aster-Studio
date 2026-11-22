# W4A8 社区模型「解码卡死」彻查结论（2026-11-10）

模型：`minimax_h3_fl2va_pruned_w4a8_mixed.safetensors`（11.68GB，W4A8 mixed ConvRot，来源 AX1Y2JP/MiniMax-H3-W4A8-ConvRot）
环境：RTX 4070 Laptop 8GB VRAM / 32GB RAM / ComfyUI 0.33.1 / 4 步 LightX2V LoRA。
守卫：本次为放行测试，`min_ram_free_gb=2.5`、`trip_ram_free_gb=1.0`。

## 测试矩阵（全部实测）

| 配置 | 帧数 | 分辨率（像素） | init | 采样 4 步 | 解码 | 结果 |
|---|---:|---|---:|---:|---:|---|
| W4A8，2s | 56 | 480×288（138k） | 46s | 1:56 | ~50s | ✅ 完成，总 195s |
| W4A8，5s | 124 | 480×288（138k） | ~23s | 39s | ~40s | ✅ 完成，总 **135.5s** |
| W4A8，5s | 124 | 608×352（214k） | 44s | 67s | **卡死 >11 分钟** | ❌ 无输出（GPU 100%、日志停在 AudioVAE 后） |
| int8，5s（对照） | 124 | 608×352（214k） | 1:36 | 53s | ~2.5min | ✅ 完成，总 4:58 |

## 结论

1. **不是模型文件损坏、也不是随机故障**：W4A8 在 480×288 下 2s/5s 都能完整解码出片（5s 仅 135.5s，比 int8 608×352 的 298s 快约 2.2 倍）。
2. **卡死由「分辨率→像素量」触发，与帧数无关**：同样是 124 帧，480×288 正常、608×352 卡死；差别只在 VAE 解码阶段的显存峰值（W4A8 11.96GB staging + VideoVAE 4.9GB，8GB 显存换入换出死循环）。
3. 卡死时表现为：日志停在 `Model MiniMaxH3AudioVAE prepared ...` 之后、`Requested to load MiniMaxH3VideoVAE` 之前，GPU 100%、显存 7.8GB 满、`/api/jobs` 无进展，且 `cancel` 无法立即生效（需强杀 ComfyUI）。
4. **社区印证**：Kijai/MiniMax-H3_comfy 有用户报告 W4A8 模型「incompatible，进程报错并完全冻结」；ComfyUI 上游也有 VAE 解码问题/修复 PR（PR #15456 在 VAE 解码重试前释放其他模型内存、Issue #15246/#15251 讨论 H3 VAE 解码与设备不匹配）。

参考：
- https://huggingface.co/Kijai/MiniMax-H3_comfy/discussions/6
- https://github.com/Comfy-Org/ComfyUI/pull/15456
- https://github.com/Comfy-Org/ComfyUI/issues/15246
- https://github.com/Comfy-Org/ComfyUI/issues/15251

## 使用建议

- 想用 W4A8 提速：**固定 480×288（或更小）**，不要 608×352；W4A8 在 5s/480×288 约 2 分 15 秒可出片。
- 需要 608×352 或 15s 段：用 **int8**（已验证稳定）。
- 若必须在 608×352 用 W4A8：可试「先 `free` 卸载模型再解码」「降低 VideoVAE 精度/分块」「等 ComfyUI 上游修复」，当前 0.33.1 下不推荐。
- 证据日志：`logs/w4a8_probe2.log`（2s 成功）、`logs/w4a8_probe5s.log`（5s/480×288 成功）、`logs/comfy_stderr.log`（卡的现场）。
