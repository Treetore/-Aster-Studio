# 上下文压缩 / 交接文档（下个对话窗口先读这份）

> ⚠️ **本文档已过时（2026-11-10）**。最新交接请读 **[`CONTEXT_HANDOFF_20261111.md`](./CONTEXT_HANDOFF_20261111.md)**（含二采、画布页、任务 pill、页文件 D 盘、stale 清理、TESpeed 修复等）。

> 生成时间：2026-11-10 · 项目：`K:\CONFI\comfy_mcp`（ComfyUI MiniMax-H3 本地视频工作台）
> 硬件：RTX 4070 Laptop **8GB VRAM** / **32GB RAM** / Windows · 这是全部决策的硬约束。

---

## 1. 项目一句话

用本地 ComfyUI（MiniMax-H3 文/图生视频，含原生立体声）生成新海诚风格动画短片，
配 MCP 服务（给 agent 用）、FastAPI Web 工作台（给用户用）、ffmpeg 剪辑、超分脚本、
一键启动脚本；全部围绕 8GB 显存做资源守卫与降级策略。

## 2. 环境与路径

| 项 | 值 |
|---|---|
| ComfyUI | `K:\CONFI\mian\ComfyUI_windows_portable`，版本 0.33.1，端口 8188 |
| 嵌入 Python | `K:\CONFI\mian\ComfyUI_windows_portable\python_embeded\python.exe`（3.13，torch 2.13+cu130） |
| 项目根 | `K:\CONFI\comfy_mcp` |
| Web UI | `http://127.0.0.1:8190/`（根路径跳 `/studio`）；`/simple` 旧监控页；`/docs` OpenAPI |
| MCP 插件名 | `include:comfy-mcp`（DSH profile 中，工具前缀 `mcp__comfy__`） |
| Harness | Electron `K:\DSH\DeepSeek Harness.exe`（无 CLI 提示词注入，靠剪贴板/文件） |
| 视频输出 | `ComfyUI\output\video\*.mp4`；项目成品在 `K:\CONFI\comfy_mcp\artifacts\` |

## 3. 已交付成果（文件）

| 文件 | 内容 |
|---|---|
| `artifacts\shinkai_15s.mp4` | 新海诚风格 15s 样片，608×352、24fps、15.08s、2.2MB |
| `artifacts\shinkai60_1min.mp4` | **1 分钟成片**（4×15s crossfade 拼接），59.43s、608×352、6.4MB |
| `artifacts\shinkai60_1min_4x.mp4` | 上片 4x 超分：2432×1408、59.4s、83.9MB（anime_6B 模型） |
| `artifacts\shinkai_15s_4x.mp4` | 15s 样片 4x 超分：2432×1408、30.2MB |
| `ComfyUI\output\video\shinkai60_p1..p4_00001_.mp4` | 1 分钟成片 4 段原始分段 |
| `artifacts\upscale_check\check_30s.jpg` | 超分后画面检查帧 |

## 4. 代码结构

```
K:\CONFI\comfy_mcp\
├── server.py / mcp_protocol.py   MCP stdio 服务（17 工具：status/hardware/start/stop/free/
│                                 templates/plan/generate/job/jobs/cancel/wait/upload/edit/
│                                 probe_media/settings/logs）
├── client.py                     ComfyUI HTTP 客户端
├── workflow.py                   模板 resolve/validate/ui_to_api；optimize 节点链；模型覆盖
├── safety.py                     Guard（单飞、RAM 阈值、watchdog、nvidia-smi）
├── jobs.py                       任务存储 data/jobs.json（原子写）
├── editing.py                    ffmpeg 剪辑（14 个函数）
├── templates\minimax_h3_{r2v,fl2v,t2v}.api.json
├── webapp\app.py                 FastAPI：REST + /studio 页面（即梦风格）+ /simple
├── webapp\studio.html            单页 UI（创作/作品/超分/剪辑/设置 + 硬件监控）
├── tools\upscale_video.py        逐帧超分（ffmpeg 管道流式，RRDBNet 系）
├── tools\watch_comfy.ps1         PowerShell 监控仪表盘
├── tools\start_comfy_console.ps1 可见控制台启动 ComfyUI
├── tools\monitor_server.py       旧版 Web 监控（8190，已被 FastAPI 取代，可不用）
├── tools\download_w4a8.py        hf-mirror 下载 W4A8 模型（含限流重试）
├── start_all.ps1 / start_all.bat 一键链式启动（ComfyUI→Web→提示词→Harness）
├── stop_all.ps1 / stop_all.bat   一键停止
├── data\settings.json            运行时设置（含守卫阈值/档位）
├── data\harness_prompt.txt       启动脚本写入的 Harness 提示词
└── docs\  WORKFLOWS / EDITING / OPTIMIZATION / ARCHITECTURE / 本文件
```

## 5. 关键实现要点

- **模板/模型**：`minimax_h3_t2v`（fl2va 模型）、`minimax_h3_fl2v`（首尾帧）、`minimax_h3_r2v`（参考图，ref2va）。
- **帧数**：24fps，帧数须 17k+5；**362 帧 = 15.08s**（单段上限，训练区间 124–362）。
- **4 步加速**：LoRA `minimax_h3\minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors`；
  **8 步 turbo LoRA 在本机不可行**（卡在初始化 40 分钟未进采样，已放弃）。
- **workflow.resolve 参数面**：prompt/profile/width/height/megapixels/aspect/duration_s/length/
  steps/sampler/scheduler/seed/lora_name/lora_strength/**optimize**（off/sage/lowmem/max）/
  **attn_head_chunks/ff_chunks/ff_seq_threshold/te_speed_device**（optimize 细项）/
  **unet_name/clip_name/vae_name/audio_vae_name**（模型覆盖，可切 W4A8）。
- **optimize 节点链**（本机已装，KJNodes/TE-Speed）：
  `MiniMaxH3MemoryEfficientSageAttentionPatch` → `MiniMaxLowVRAMAttention` → `MiniMaxChunkFeedForward`
  → `TESpeedMiniMaxH3`（max 档，TESpeed 默认 0.12/0.1/0.9/mcs2/device auto）。
- **Guard**：默认 `min_ram_free_gb=8`、`trip_ram_free_gb=3.5`、`single_flight=true`；
  生成时 RAM 峰值可到 1.5–2GB，需要临时放宽（`min 2.5 / trip 1.0`）放行，跑完恢复。
- **Web UI**：FastAPI + studio.html；`/api/status|templates|models|videos|plan|generate|jobs|
  jobs/{id}|jobs/{id}/cancel|edit|upscale|upscale/jobs|upscale/models|media|upload|hardware|settings|logs`。
- **超分脚本**：`tools/upscale_video.py` 逐帧流式（ffmpeg rawvideo 管道），内存恒定；
  支持 RRDB 架构（anime_6B/UltraSharp/Remacri/RealESRGAN_x4plus），HAT/SwinIR 需 spandrel（ComfyUI 已装）。
- **MCP 修改后必须重启插件**（disable/enable `include:comfy-mcp`），否则进程仍用旧代码。

## 6. 实测基线（8GB，重要参考）

| 配置 | init | 采样(4步) | 总 | 备注 |
|---|---:|---:|---:|---|
| 15s / 362 帧，608×352，冷启动 | 11:41 | 9:38 | **22:26** | P1，含解码 |
| 同上 P2 / P3 / P4 | 5:08 / 4:03 / 1:48 | 5:03 / 4:28 / 4:34 | 14:50 / 11:35 / 8:31 | 每段前 kill+重启冷启动 |
| **5s / 124 帧**，冷启动 | **1:36** | **53s** | **4:58** | 缩短分镜立竿见影 |
| 5s 同参数重复提交 | — | — | **2.9s** | ComfyUI 节点缓存命中（改 seed 才重算） |
| 4x 超分 15s/362 帧 | — | — | 425s | 0.85 fps |
| 4x 超分 1 分钟/1426 帧 | — | — | 1757s（29 分钟） | 0.8 fps |
| W4A8 模型 5s | **44s** | 1:07 | ❌ | VAE 解码卡死 >11 分钟（8GB 换入问题），已弃用；模型保留 |

## 7. 已知坑 / 处理办法

1. **卡在 `Model Initializing`**（>10 分钟无进展）：`taskkill /PID <comfy pid> /T /F` → 等 10–15s → 重启 ComfyUI → 重新提交。冷启动反而更快（1:48–5:08）。
2. 热缓存（模型驻留）不保证免初始化，且可能更慢/卡死；**每段前冷启动**是最稳策略。
3. ComfyUI 启动偶发卡在 `Using pytorch attention` → kill 后再启动一次。
4. 生成前 `status` 看 RAM；峰值低至 1.5GB 时需临时放宽守卫，否则 `guard_trip` 中断。
5. PowerShell 5.1：不支持 `-Form`/`-Environment`；中文脚本需 **UTF-8 BOM**；`>` 重定向会写 UTF-16；JSON 参数用 Python/文件传。
6. MCP 与 Web 同时写 `data/jobs.json` 存在跨进程竞态：批量操作只用一个入口。
7. Web UI 依赖已装：`fastapi`、`uvicorn`、`python-multipart`（embedded Python）。
8. 超分 4x 后 2432×1408 对 8GB 解码/编码慢，但逐帧流式不会 OOM。

## 8. 当前运行状态（截至本文件生成）

- ComfyUI：8188 运行中（`comfy_up=True`）；Web UI：8190 运行中（uvicorn 进程）。
- 守卫：`min_ram_free_gb=8`、`trip_ram_free_gb=3.5`、`single_flight=true`。
- 超分模型（`ComfyUI\models\upscale_models\`）：`RealESRGAN_x4plus_anime_6B.pth`(17MB)、
  `4x-UltraSharp.pth`(64MB)、`4x_foolhardy_Remacri.pth`(64MB)、`RealESRGAN_x4plus.pth`(64MB)、
  `Real_HAT_GAN_SRx4.pth`(162MB)、`SwinIR-L_x4_GAN`(136MB)。
- H3 模型：`diffusion_models\minimax_h3_fl2va_pruned_int8_convrot.safetensors`（当前默认）、
  `minimax_h3_fl2va_pruned_w4a8_mixed.safetensors`（11.68GB，W4A8 实验用）、
  `text_encoders\qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`、两个 VAE。

## 9. 常用操作

```powershell
# 启动全部服务（双击也可以）
cmd /c "K:\CONFI\comfy_mcp\start_all.bat"
# 只启 Web UI + 不启 Harness
powershell -ExecutionPolicy Bypass -File K:\CONFI\comfy_mcp\start_all.ps1 -NoHarness
# 停止
cmd /c "K:\CONFI\comfy_mcp\stop_all.bat"

# MCP 工具（agent 侧）
mcp__comfy__status / plan / generate / job / wait / edit / settings

# REST（脚本/其他 agent）
curl http://127.0.0.1:8190/api/status
curl -X POST http://127.0.0.1:8190/api/generate -H "Content-Type: application/json" -d "{\"template\":\"minimax_h3_t2v\",\"params\":{\"prompt\":\"...\",\"duration_s\":5,\"steps\":4}}"

# 命令行超分
python K:\CONFI\comfy_mcp\tools\upscale_video.py --src in.mp4 --dst out.mp4 --model RealESRGAN_x4plus_anime_6B.pth
```

## 10. 下个窗口建议先做

1. 读 `README.md`、`docs/ARCHITECTURE.md`、`docs/OPTIMIZATION.md`、`SPEC.md`。
2. `mcp__comfy__status` 确认 ComfyUI/守卫/显存；再决定是否生成（5s 草稿最稳）。
3. 若要继续出片：5s/10s 分段（每段前冷启动），失败按第 7 节重试；1 分钟成片需 6–12 段。
4. 若要超分对比：用同一条 15s 片分别跑 anime_6B / Remacri / HAT（每条约 5–10 分钟）。
5. 若要继续 W4A8：先试 480×288、或在解码前 `free` 模型再解码；否则保持 int8。
6. 改 `server.py`/`workflow.py`/`config.py` 后：**disable/enable 插件** 才能生效；改 `webapp/*` 后重启 uvicorn。
