# 上下文交接文档 · 2026-11-11

> 本文档给下一个对话窗口接手用。按「环境 → 服务 → 文件 → 已完成 → 已知坑 → 待办」组织，默认读者已了解 ComfyUI 基本概念。

## 1. 环境

| 项 | 值 |
|---|---|
| 机器 | Windows 11 笔记本，**RTX 4070 Laptop 8GB VRAM / 32GB RAM**，驱动 621.14 |
| ComfyUI | `K:\CONFI\mian\ComfyUI_windows_portable\ComfyUI`，版本 **0.33.1** |
| Python | `K:\CONFI\mian\ComfyUI_windows_portable\python_embeded\python.exe`（3.13，torch 2.13+cu130） |
| ffmpeg | `C:\ffmpeg\ffmpeg-master-latest-win64-gpl-shared\bin\ffmpeg.exe` |
| 项目根 | `K:\CONFI\comfy_mcp` |
| 页文件 | **C 2GB + D 16–32GB**（2026-11-11 重启后生效，D 盘实际分配约 19.5GB；C 保留 2GB 供崩溃转储） |

## 2. 服务与启动

| 服务 | 地址 / 方式 | 说明 |
|---|---|---|
| ComfyUI | http://127.0.0.1:8188 | 启动参数 `--fast --cuda-malloc --disable-auto-launch` |
| Web UI | http://127.0.0.1:8190 → `/studio`（`/simple` 旧页，`/docs` OpenAPI） | uvicorn |
| MCP | stdio 插件 `include:comfy-mcp`，17 个工具 `mcp__comfy__*` | 默认单飞（single_flight） |
| 一键启动 | `K:\CONFI\comfy_mcp\start_all.bat` | ComfyUI → Web → 提示词文件/剪贴板 → Harness |
| 一键停止 | `K:\CONFI\comfy_mcp\stop_all.bat` | Web + ComfyUI |

**修改生效规则（重要）**：

- 改 `server.py` / `workflow.py` 等 MCP 侧代码 → **必须 disable/enable 插件**（`plugin_manager`，`include:comfy-mcp`）才重新加载；
- 改 `webapp/app.py` → **重启 uvicorn**；
- 改 `webapp/studio.html` → 浏览器刷新即可（每次请求动态读取）。

## 3. 核心文件

| 文件 | 作用 |
|---|---|
| `server.py` | ComfyService、17 个工具、资源守卫（watchdog）、`active_jobs()` 会向 ComfyUI 对账清理 stale 记录、`object_info_cache` 刷新重试 |
| `client.py` | ComfyUI HTTP 客户端（queue/jobs/job/prompt/interrupt/history/upload） |
| `workflow.py` | 模板解析/参数/校验/优化链；`TEMPLATES` = r2v/fl2v/t2v；`two_pass` 二采实现 |
| `safety.py` / `jobs.py` / `editing.py` | 资源守卫 / 任务存储 / ffmpeg 剪辑 |
| `webapp/app.py` | REST API（status/queue/jobs 合并视图/upload/generate/plan/upscale/edit/film/settings/logs/hardware/realtime/free）+ `WS /ws/comfy` 代理 |
| `webapp/studio.html` | 即梦风格 Web UI（创作 / 任务 / **画布** / 我的作品 / 超分 / 剪辑 / 设置） |
| `tools/run_1min_film.py` | 8×8s 电影片段流水线（生成→拼接→超分，含 kill ComfyUI / commit 守卫） |
| `tools/upscale_video.py` | Real-ESRGAN 流式超分（`--max-temp`、`--pause-every`、`--min-commit-gb`） |
| `tools/upscale_segmented.py` | **分段超分**：切段→独立进程超分（释放内存）→拼接→原音覆盖 |
| `tools/run_portrait_p2.py` | 直连 HTTP 提交段2（绕过 MCP 看门狗误杀） |
| `tools/apply_pagefile_d.ps1` | 页文件改 D 盘（需管理员） |
| `data/settings.json` | 守卫/档位/ffmpeg 配置（当前 `min_ram_free_gb=2.5`、`trip_ram_free_gb=1.0`、`single_flight=true`） |
| `data/jobs.json` | 本地任务记录 |
| `data/film_state.json` | 60s 成片流水线状态（**旧**，2026-11-10 19:39，upscale failed） |
| `docs/GPU_LOST_INCIDENT_20261110.md` | 掉驱动事故分析（commit 耗尽） |
| `docs/W4A8_DECODE_FINDINGS.md` | W4A8 解码卡死结论（分辨率相关） |

## 4. 已完成工作（2026-11-10 ~ 11-11）

1. **GPU lost 事故根因与修复**：ComfyUI 模型驻留（27.4GB commit）+ 超分 6.5GB vs 页文件 6.5GB → 虚拟内存耗尽。已：页文件改 D 盘；超分前 kill ComfyUI；commit 守卫；分段超分工具；`docs/GPU_LOST_INCIDENT_20261110.md`。
2. **W4A8 解码卡死**：分辨率/pixel 数相关——480×288 可用，608×352 卡；非模型文件问题。见 `W4A8_DECODE_FINDINGS.md`。
3. **`optimize=max` 失败**：`TESpeedMiniMaxH3` 与 ComfyUI 0.33.1 的 `MiniMaxH3Model._forward` 不兼容（`unsupported MiniMaxH3Model._forward version`）→ 已从 max 档移除 TESpeed，仅保留 Sage+LowVRAM+ChunkFF。
4. **图片校验失败（上传后）**：`object_info` 是进程缓存，新上传文件不在列表 → `workflow.validate` 对 input 目录真实存在的文件名直接放行；`tool_generate` 校验失败时清缓存重试。
5. **single_flight 被 stale 任务永久阻塞**：`active_jobs()` 现在向 ComfyUI 实时对账，自动清理不存在/已结束记录。
6. **手动提交被资源守卫拒绝**：`min_ram_free_gb` 8→**2.5**、`trip` 3.5→**1.0**；前端失败时显示具体原因；新增 `/api/free` + 硬件面板「释放模型」按钮。
7. **二采（latent 放大精修）已集成**：
   - 安装 LBH `Comfyui_Minimax_h3_latent_Upscaler` 节点；模型：2D bf16 347MB + **3D fp16 659MB**（`models/latent_upscale_models/`）；
   - `workflow.py` 新参数：`two_pass` / `two_pass_scale`（默认 1.9，clamp 0.82MP、32 对齐）/ `two_pass_sigmas`（`0.9035,0.6316,0.3158,0`）/ `two_pass_model`；
   - 链路：拆分 AV latent → 3D 放大 → 重组 → 重建条件 → 二阶段采样 → 解码 → 封装（音频直通）；
   - 验证：2s 测试 608×352 → **1152×672**，359s 完成；
   - Web UI 创作页新增「二采（latent 放大精修）」下拉 + 倍率输入。
8. **Web 可用性 / 布局**：间距统一、参数网格对齐、LIVE TASK 日志空态提示、作品卡渐变占位、窄屏响应式（≤1200 单列、≤900 侧栏横向）。
9. **「画布」页**：侧栏新增，iframe 嵌入 ComfyUI 节点画布，带刷新/新标签；支持 `?tab=canvas`。
10. **右上角状态实时化**：`tickHw` 独立启动 + 2s 轮询（GPU/显存/温度/RAM）；`updateTaskPill` 取代旧「成片 pill」，每 3s 反映 `/api/queue` 当前任务（生成中/排队/隐藏），点击跳任务页。

## 5. 已知问题 / 坑

- **2s 超短段解码偶发卡死**（int8 模型，GPU 100%、日志停在 AudioVAE→VideoVAE 之间）→ 建议用 5s/10s/15s 段；遇到 kill ComfyUI 重启。
- **MCP 看门狗误杀**：init 期间 `/system_stats` 连续 3 次超时（15s）会中断任务（段2 曾被杀）。临时绕过：直连 HTTP 提交；根治：改 `server.py` watchdog 阈值（未做）。
- 脚本直连提交（如 `run_portrait_p2.py`）不写 `jobs.json`，但 `/api/jobs` 合并视图（ComfyUI `/api/jobs` + 本地）能看到。
- ComfyUI 重启后其 `/api/jobs` 历史只保留当前进程任务。
- 4x 超分必须**先 kill ComfyUI**，否则 commit 再次耗尽；当前页文件已加宽，但别同时驻留模型 + 超分 + ffmpeg。
- `film_state.json` 是 2026-11-10 的旧状态（8 段完成、拼接 OK、超分失败/历史），右上角 pill 已改用任务状态，不再读它。
- 二采仅在 2s 段验证过；15s 段未实测（时间约 ×1.5）。

## 6. 待办（按优先级）

1. **补 4x 超分 60s 成片**：`python tools/upscale_segmented.py --src artifacts\film_cinematic_1min.mp4 --dst artifacts\film_cinematic_1min_4x.mp4 --seg 15`（先 kill ComfyUI，约 35–45 分钟）。
2. **二采 15s 实测**：`two_pass=true` 跑 15s 段（608×352→1152×640），看画质/耗时/音画同步，必要时调 `two_pass_sigmas`。
3. **修 MCP 看门狗**（init 宽限）：改 `safety.py`/`server.py` 的 watchdog 判定，避免 init 误杀。
4. 文档更新：`README.md` / `ARCHITECTURE.md` 增加二采、画布页、任务 pill、`/api/queue`、`/api/free`、`/api/jobs` 合并视图说明。
5. 可选：`film_state` 重置/归档；把「成片」信息移回超分页历史卡。

## 7. 常用命令

```powershell
# Web（工作目录 K:\CONFI\comfy_mcp）
& 'K:\CONFI\mian\ComfyUI_windows_portable\python_embeded\python.exe' -m uvicorn webapp.app:app --host 127.0.0.1 --port 8190

# 单测（141 tests）
cd K:\CONFI\comfy_mcp; & 'K:\CONFI\mian\ComfyUI_windows_portable\python_embeded\python.exe' -m unittest discover -s tests -t .

# 二采（MCP 工具或 Web 参数）
two_pass=true, two_pass_scale=1.9, two_pass_sigmas="0.9035,0.6316,0.3158,0"

# 分段超分
& 'K:\CONFI\mian\ComfyUI_windows_portable\python_embeded\python.exe' tools\upscale_segmented.py --src <in.mp4> --dst <out.mp4> --seg 15

# 页文件（管理员）
powershell -ExecutionPolicy Bypass -File tools\apply_pagefile_d.ps1
```

## 8. 当前运行状态（2026-11-11 12:59 快照）

- ComfyUI：**运行中**（8188），队列 0/0
- Web：**运行中**（8190），守卫 `min=2.5 / trip=1.0`
- 页文件：C 2GB + D ≈19.5GB（已生效）
- 二采模型：2D bf16 347MB + 3D fp16 659MB 就绪
- 上次成功产物：`artifacts\portrait30_30s.mp4`（608×352 30s，人像）、`artifacts\film_cinematic_1min.mp4`（608×352 60s 电影片段，未超分）、`two_pass_probe_00001_.mp4`（二采 1152×672 2.3s）
- **14:50 事故**：15s + 15 步 + 二采任务运行中 GPU 引擎超时（LiveKernelEvent `0x141`，`C:\Windows\LiveKernelReports\WATCHDOG\WATCHDOG-20261111-1450.dmp`，进程 `python.exe`），整机冻结后电源键重启；无蓝屏/minidump/WHEA。
- **参数调整（15:12）**：见 `docs/RUNTIME_PARAMS_20261111.md` —— TdrDelay/TdrDdiDelay=60（**重启系统后生效**）、`--fast fp16_accumulation`、`--vram-headroom 1.0`、reserve-vram 1.0、守卫 `min=3.0 / trip=1.2`。ComfyUI 与 Web 已重启验证。
- **15:40 事故（复发）**：15s/362 帧 + `optimize=max` + 10 步任务在第 6 步 GPU 引擎再次超时（LiveKernelEvent `0x141`，转储 `WATCHDOG-20261111-1540.dmp` **未落盘**），整机冻结、电源键重启（15:44:27）。**TDR=60 未阻止，只延后约 60s**。详见 `docs/INCIDENT_20261111_1540_GPU_HANG.md`。结论：**暂停 15s 段**，先用 5s 基线；再按 `optimize=off` → `--disable-dynamic-vram` → `--disable-pinned-memory` 逐级定位。
- **压力测试结论（20:01–21:41）**：25 分钟连续负载 + 两种崩溃原配置复测，**均未复现崩溃**；负载段 88.7°C、峰值 94.7°C、热限流仅个位数采样 → **散热不是崩机原因**；`15s + two_pass` 实测单步 20 分钟、不可行。详见 `docs/STRESS_TEST_1MIN_20261111.md`。
- **生成策略（22:30 定稿，必读）**：`docs/GENERATION_POLICY_20261111.md` —— 15s 段 8–10 步、`optimize=max`、**禁 two_pass**；长段前必须 `free`（起跑 RAM ≥20GB）；保持核显驱屏；守卫 `min=3.0 / trip=1.0`（1.2 会误杀 15s 任务）。代码侧 `workflow.resolve` 已加二采长度硬护栏，Web 校验错误改为 400。
- **自动化入口（22:35）**：`python tools\auto_film.py --segments 4 --duration-s 15 --steps 8 --optimize max --tag <name>`（双击版 `auto_film.bat`）——自动预检/`free`、按策略生成、每 3s 遥测并在过热/内存见底/卡死时自动中止、失败自动重启 ComfyUI 重试 1 次、拼接并输出 `artifacts/<tag>/report.{json,md}` 与 `logs/<tag>_telemetry.csv`。`--dry-run` 只做预检。
- **图像生成模板（11-15 新增）**：`minimax_h3_image`（H3 出静帧 PNG，768×768/8 步/max，实测 ~3 分钟）；详见 `docs/WORKFLOW_H3_IMAGE.md`，`resolve()` 已兼容 SaveImage 并拦截 two_pass。
- **ComfyUI 升级（11-15）**：v0.33.1 → **v0.45.3**（为 Qwen-Image 2.1）；代码备份 `K:\CONFI\mian\_comfy_backup_v0331_code.zip`，依赖快照 `logs/pip_freeze_before_v045.txt`；H3 回归通过（2s t2v 193s 正常出片）。
- **Qwen-Image 2.1 工作流（11-15 新增）**：`qwen_image` 模板（1024×1024/25 步/euler，实测 51s）；模型已下载（int8 6.76GB + TE 8.71GB + VAE 0.63GB）；详见 `docs/WORKFLOW_QWEN_IMAGE21.md`；下载工具 `tools/download_qwen21.py`，出图工具 `tools/gen_image.py`。
- **Qwen 图像编辑 + Web UI（11-15）**：新增 `qwen_image_edit`（images autogrow→LoadImage，参考图 latent，实测 42s 水彩/素描）；Web 新增模式「文生图 · Qwen / 图像编辑 · Qwen / H3 静帧」，结果面板支持 PNG，作品页合并 `/api/images`。
- **Aster启动器（11-15 新增，原绘世风格）**：`launcher/`（FastAPI 8191 + Edge --app 窗口），含启动/停止、高级参数、模型管理、体检修复、控制台、任务、小工具、设置；桌面快捷方式「绘世 ComfyUI 启动器」；详见 `launcher/README.md`；支持深/浅/莫奈主题、图片背景（内置 3 张 Qwen 生成的印象派背景，可上传自定义）、模糊/遮罩/强调色。启动子进程必须注入 PYTHONUTF8/PYTHONIOENCODING，否则 GBK emoji 会导致 ComfyUI 启动崩溃。
- **720p 15s 优化（11-15）**：`tools/h3_720p15s.py`（3×5s 分段+首帧接力+ffmpeg 拼接）、模板 `minimax_h3_t2v_720p15s`（ContextWindows 124/39 单次版）；Sage 每步 74→44s；720p 5s 8步 max 总 15:30、4步 turbo 分段首段 13:58；详见 `docs/H3_720P15S_20261115.md`。
- **长片自动管线（11-15）**：Web「长片」页 + `POST /api/film/start|state|cancel` + `tools/segment_film.py`：自动选 r2v/fl2v/t2v、抽尾帧作下一段首帧、concat、进度/取消；整段直出为 `mode=single` 备选；冒烟 1×22f/608x352 233s 通过。
- **H3 解除显存限制（11-15）**：`safety.unlimited_h3=true` + ComfyUI `--vram-headroom 0`；启动器设置页可开关；仅 H3 视频生效，Qwen 图像守卫不变。
- **MOSS-Image 接入（11-15）**：`moss_image` 模板（int8 5.04G + qwen3.5_9b_w4a8 6.84G + flux2-vae_small），Web 生图新增「MOSS 文生图」+ CFG；1024²/25步 实测 50s；见 `docs/MOSS_IMAGE_20261115.md`。
- **MOSS 编辑（11-15）**：`moss_image_edit` 模板 + edit LoRA(283MB)，Web「MOSS 编辑」模式；30步/cfg4/euler/normal，源图 1MP；实测 70s；小字可能被重绘。
- **AstrBot 桥（11-15）**：webapp `/api/bridge/{models,generate,health}`（token= data/bridge_token.txt）+ 插件 `K:\CONFI\astrbot_plugin_comfy_bridge`（/模型 /画图 /视频）；图像 26s、视频 149s 实测；见 `docs/ASTRBOT_BRIDGE_20261115.md`。
- **桥安全/映射（11-15）**：token 已轮换；`data/ip_allowlist.json` 仅放行 203.0.113.10；`tools/tunnel.ps1` + start_all/启动器自动起停 cloudflared，URL 在 `data/tunnel_url.txt`。
- **隧道 Web 可视化（11-15）**：启动器「隧道」页：quick/token 模式、Cloudflare 登录+一键创建固定域名、IP 白名单、测试；cloudflared 强制 http2；接口 /api/tunnel*。
- **模型切换释放（11-15）**：`model_policy.py`：不同模型族生成前自动 /free 并等待显存；同族（moss/qwen 编辑）不释放；配置 `data/model_policy.json`，日志 `logs/model_policy.log`；不影响隧道。
- **全局单任务锁（11-15）**：`job_lock.py` 文件锁覆盖 MCP/Web/桥/长片；并发返回“请等待上一条任务完成”；完成自动释放+watch 兜底；不影响隧道。\n- **插件帮助升级（11-15）**：v1.1.0：新增 /配置、/画图帮助、/视频帮助，/生图帮助 全量参数；中英参数别名；schema hints；README 参数表。
- **快捷方式修复（11-16）**：pythonw 下 sys.stdout/stderr 为 None 导致 uvicorn 日志崩溃；run.py 增加流重定向 + log_config=None；快捷方式启动后自动拉起 ComfyUI/Web（LAUNCHER_NO_AUTOSTART=1 可关）并恢复隧道。
