# ComfyUI 视频生成 MCP 服务（comfy_mcp）

把本机 **ComfyUI 便携版 + MiniMax-H3 视频模型** 封装成一套 MCP 工具：Agent 可以直接
写工作流、排队生成、监控进度、做资源守卫、再用 ffmpeg 剪辑成片。

- 工程目录：`K:\CONFI\comfy_mcp`
- ComfyUI：`K:\CONFI\mian\ComfyUI_windows_portable`（v0.33.1，端口 8188）
- 已注册到 DSH profile，工具前缀：`mcp__comfy__*`（共 17 个）
- 兼容 8GB VRAM / 32GB RAM 的笔记本（RTX 4070 Laptop），默认**单飞 + 资源守卫**

---

## 1. 它解决什么问题

| 痛点 | 本服务的做法 |
|---|---|
| ComfyUI 的 UI 工作流是「画布 JSON」，不能直接喂 `/prompt` | 3 个 **API 格式模板**（r2v / fl2v / t2v），参数化后直接提交 |
| 40GB 权重 + 8GB 显存，跑大了就卡死/爆内存 | 提交前 `preflight` 检查 RAM/磁盘/队列；运行中 `wait`/watchdog 在 RAM 触底时自动 `interrupt` |
| 生成要几十分钟，Agent 不能一直阻塞 | `generate` 立即返回 job_id；`job`/`wait` 轮询；任务落盘 `data/jobs.json` |
| 生成完还要裁剪/拼接/配乐/字幕/封面 | `edit` 工具封装 ffmpeg（trim/concat/transform/mux_audio/burn_subtitles/overlay/speed/…） |
| 便携版在 GBK 控制台遇到 emoji 日志直接崩 | 启动脚本强制 `PYTHONUTF8=1`，MCP 服务自身全程 UTF-8 |
| 换模型/换工作流要改代码 | `templates` + `plan` 干跑，参数面自描述；新增 UI 工作流可用 `ui_to_api` 导入 |

## 2. 架构

```
DSH / Agent
   │  MCP stdio (JSON-RPC)
   ▼
comfy_mcp/server.py  ──工具层──  status/hardware/start/stop/free/templates/plan/generate/job/jobs/cancel/wait/upload/edit/probe_media/settings/logs
   ├── workflow.py    模板(API JSON) + 参数解析(resolve) + object_info 校验 + UI→API 转换
   ├── client.py      ComfyUI HTTP 客户端（/prompt、/api/jobs、/history、/upload/image、/view…）
   ├── safety.py      硬件探测、成本/风险估算、Guard.preflight、后台 Watchdog
   ├── jobs.py        本地任务存储（原子写、线程安全）
   └── editing.py     ffmpeg/ffprobe 剪辑（限线程、超时、原子输出）
```

- **模板**：`templates/minimax_h3_r2v.api.json`、`minimax_h3_fl2v.api.json`、`minimax_h3_t2v.api.json`
- **原始工作流**（只读参考）：`ComfyUI\user\default\workflows\video_minimax_h3_r2v.json`、`VIDOH3.json`
- **守卫阈值**：`data/settings.json` 可调（见 `settings` 工具）

## 3. 快速开始

### 3.1 作为 MCP 工具（本会话已生效）

```
mcp__comfy__status                          # 先看状态
mcp__comfy__templates                       # 看模板与参数
mcp__comfy__plan                            # 干跑：校验 + 风险估算（不排队）
mcp__comfy__generate                        # 提交任务，立即返回 job_id
mcp__comfy__job / wait / cancel / jobs      # 查询/等待/取消
mcp__comfy__edit / probe_media              # 剪辑
```

### 3.2 命令行（不依赖 MCP 客户端）

```powershell
cd K:\CONFI\comfy_mcp
.\comfy.ps1 list
.\comfy.ps1 status
.\comfy.ps1 plan --args-file .\artifacts\args_plan_t2v.json
```

> Windows 下 PowerShell 直接传 JSON 字符串会被吃掉引号，推荐 `--args-file`（UTF-8 JSON 文件）。

### 3.3 生成一段视频

```json
{
  "template": "minimax_h3_r2v",
  "params": {
    "prompt": "<Picture 1> 电影感镜头：夜晚河边，一位女孩缓缓走过，暖黄路灯，水面金色反光，轻微镜头横移",
    "profile": "draft",
    "ref_images": ["K:\\path\\to\\ref.png"],
    "duration_s": 5,
    "seed": 20261108
  }
}
```

- `ref_images` 传**本地绝对路径**时 `generate` 会自动上传到 ComfyUI input；也可以直接传 input 目录里已存在的文件名。
- 想更快出片：`profile: draft`（0.2MP / 2s / 8 步）。
- 想要质量：`profile: standard`（0.4MP / 5s / 20 步，124 帧起，约 20-40 分钟量级）；`high`（0.8MP）有风险。
- fl2v/t2v 模板支持 `first_frame`/`last_frame`，fl2v 还可挂 4-step turbo LoRA（`lora_name` + `steps: 4~8`）。

### 3.4 剪辑

```json
{"action":"trim","src":"K:\\...\\MiniMax_H3_r2v_00001_.mp4","start_s":0.5,"duration_s":3}
{"action":"concat","inputs":["a.mp4","b.mp4"],"crossfade_s":0.4}
{"action":"burn_subtitles","src":"a.mp4","srt_path":"subs.srt"}
{"action":"to_gif","src":"a.mp4","fps":12,"width":640}
```

默认输出到 `artifacts\edited\`，返回 `dst` 和 ffmpeg 命令；每步都能 `probe_media` 校验。

> 已通过真实 MCP 工具验证的 action：`probe_media`、`trim`、`concat`（含 crossfade）、`transform`、`mux_audio`、`burn_subtitles`（中文 SRT）、`overlay`、`speed`、`extract_frames`、`extract_audio`、`thumbnail`、`to_gif`。

### 3.5 一次完整的「生成 → 剪辑」编排示例

1. `status` 看机器与队列 → 2. `plan` 干跑确认尺寸/帧数/风险 → 3. `generate` 提交 draft 任务拿到 `job_id`
→ 4. 循环 `job`（或 `wait`，单次 ≤45s，避免工具调用超时）直到 `completed`
→ 5. `job` 返回输出绝对路径 → 6. `probe_media` 检查分辨率/时长/音轨
→ 7. `edit` 做 trim / concat / burn_subtitles / overlay 等 → 8. 交付 `artifacts/edited/`。

> MCP 工具单次调用有客户端超时（约 60 秒），所以 `wait` 上限刻意设为 45 秒；长任务请多次调用，或提交后先做别的再回来查。

## 4. 资源守卫（重点）

8GB VRAM + 32GB RAM 跑 ~40GB 权重，核心是**不并发、不冒进、随时能刹车**。

提交前（`Guard.preflight`，`generate` 自动调用）：
- ComfyUI 必须在线；已有排队/运行任务且 `single_flight=true` → 拒绝（`busy`）
- 空闲 RAM < 8GB、磁盘 < 8GB、VRAM free < 300MB、代价评估 high → 拒绝并给 `suggestions`
- 低内存时自动把未指定档位的请求降到 `draft`

运行中（`Guard.watch` / `wait` / `job`）：
- 每 5 秒采样；**RAM 空闲 < 3.5GB → 调 `/api/jobs/{id}/cancel` 中止并记录 `guard_trip`**
- MCP 进程重启会丢 watchdog，所以 `job` 查询时也会做一次 RAM 兜底检查并自动中止
- ComfyUI `/system_stats` 连续失败 3 次 → 中止
- VRAM 紧张只告警，不打断（H3 的 DynamicVRAM 会自己换入换出）

手动兜底：
- `start_comfyui_safe.ps1 -Guard`：独立 PowerShell 守卫循环（MCP 会话结束后仍有效）
- `start(mode="lowmem")`：用 `--lowvram --cache-none --reserve-vram 0.8` 启动，更省内存但更慢
- `free` 工具：卸载模型/释放显存
- `settings` 工具：`min_ram_free_gb` / `trip_ram_free_gb` / `single_flight` 等

## 5. 工作流要点（MiniMax-H3）

- 帧数走 **17k+5 网格**、24fps：`duration_to_length(5)=124`、`duration_to_length(15)=362`；训练区间约 124-362 帧，**短于 124 帧画质可能不稳**。
- 画布短边基准 768、面积上限 768×1344、两轴 32 对齐。
- r2v 用 `<Picture i>`、`<Video k>`、`<Audio j>`（从 1 开始）引用素材；提示词顺序必须与连接顺序一致。
- r2v 的参考图在 API 里是 autogrow 键：`"ref_images.ref_image_0": ["<LoadImage节点>", 0]`（已实测通过提交校验）。
- 采样链：`RandomNoise → KSamplerSelect(res_multistep) → BasicScheduler → BasicGuider → SamplerCustomAdvanced → VAEDecode / VAEDecodeAudio → CreateVideo(24fps) → SaveVideo`。
- 输出：`ComfyUI\output\video\<prefix>_00001_.mp4`（带原生立体声）。
- 旧工作流里 `TESpeedMiniMaxH3` 是 bypass 状态（mode=4），模板里没有启用；需要时可单独加。

## 6. 目录

```
comfy_mcp\
  server.py / mcp_protocol.py / config.py     MCP 服务与配置
  模块：client.py safety.py jobs.py workflow.py editing.py
  examples\automation_example.py             端到端脚本示例（提交→轮询→剪辑）
  templates\*.api.json                         API 工作流模板
  tests\（unittest，141 用例）                  模块测试
  docs\WORKFLOWS.md / EDITING.md              工作流与剪辑文档
  bundle\                                     DSH 插件包（已安装）
  comfy.ps1 / start_comfyui_safe.ps1          命令行与安全启动
  data\settings.json data/jobs.json           运行时设置与任务记录
  logs\                                       mcp.log / comfy_stdout.log / comfy_stderr.log
  artifacts\                                  剪辑产物、测试素材、干跑结果
```

## 7. 测试

```powershell
& 'K:\CONFI\mian\ComfyUI_windows_portable\python_embeded\python.exe' -m unittest discover -s K:\CONFI\comfy_mcp\tests -t K:\CONFI\comfy_mcp -v
```

## 8. 实测记录（2026-11-08，RTX 4070 Laptop 8GB / 32GB RAM）

| 项目 | 结果 |
|---|---|
| 单元测试 | 141 tests OK（客户端/安全/任务/工作流/剪辑） |
| MCP 直连 | `status`/`hardware`/`templates`/`plan`/`job`/`jobs`/`settings`/`logs`/`upload` 全部连通 8188 |
| 提交链路 | r2v draft（608×352、56 帧、8 步、1 张参考图）**被 ComfyUI 接受并开始执行**，`ref_images.ref_image_0` autogrow 键名实测有效 |
| 资源守卫 | 第二次提交被正确拒绝（`single_flight` + VRAM 不足）；取消后 `free` 把 VRAM 从 7.8GB 降到 1.2GB、RAM 从 2.2GB 恢复到 11GB |
| 剪辑 | 12 个 ffmpeg action 全部在真实 MCP 调用下通过（含中文路径/字幕/overlay/GIF） |
| 生成耗时 | 首次运行：VAE 4.9GB→文本编码器 14.6GB→UNet 19.5GB 动态加载约 **16 分钟**，随后采样初始化 7 分钟仍未完成第 1 步；第 **23.5 分钟**时 RAM 空闲降到 **1.9GB**，被主动中止（`execution_interrupted`，无输出视频） |
| **15s 成片（成功）** | t2v + 4-step turbo LoRA，608×352、**362 帧**、4 步：模型加载/初始化约 20 分钟 + 采样 **4m47s**（55s/步）+ 解码，`Prompt executed in 00:32:21`；输出 2.2MB，H.264+AAC、24fps、15.083s；产物 `artifacts/shinkai_15s.mp4` |
| **1 分钟测试片（成功）** | 4×15s（362 帧/段，4 步 + 4step LoRA）连续生成后 ffmpeg crossfade 拼接：P1 22:26 / P2 14:50 / P3 11:35 / P4 8:31（P4 前两次卡在 Model Initializing 15-25 分钟，第三次 1:48 成功）；成片 59.43s、608×352、24fps、6.4MB；产物 `artifacts/shinkai60_1min.mp4` |

> 1 分钟测试片的关键实测：**模型对象在任务间会驻留（日志无重新 “Requested to load”），但每个任务仍要做一次 “Model Initializing”（权重 staging + 首次 forward），耗时 1:48~11:41 不等且不稳定**；热缓存时 RAM 紧张反而更容易卡住。实践中最稳的是每段生成前 `kill ComfyUI + 重启` 走冷启动（init 1:48~5:08），并保持 `trip_ram_free_gb=1.0 / min_ram_free_gb=2.5` 的临时放宽档，生成完再恢复 3.5/8。

> 15s 成功的关键：用 `minimax_h3_fl2v_lightx2v_turbo_4step` LoRA 把步数压到 4；本次为放行低内存峰值，临时把 `trip_ram_free_gb` 从 3.5 降到 1.0（另设 0.9GB 独立守护），生成完已恢复 3.5。峰值 commit 约 49GB（Windows 自动扩了 pagefile），磁盘队列一度 70+，属于在硬件极限上硬跑，建议仍按下面的建议优化。

**结论与建议**：这台机器能跑出 H3 成片，但 40GB 权重在 8GB 显存 + 32GB 内存上主要卡在
权重动态换入换出（15s 单条约 32 分钟，其中约 20 分钟是加载/初始化），RAM 余量很小。建议：

1. 用 `start(mode="lowmem")` 重启 ComfyUI（`--lowvram --cache-none --reserve-vram 0.8`），
   或直接用 TE 启动器的低显存/低内存选项；
2. 生成前关闭浏览器等大内存程序，保证 **空闲 RAM ≥ 24GB**；
3. 模型加载完成后**不要重启 ComfyUI**，同模型任务复用已加载权重；
4. 若追求产出效率，建议把该 MCP 指向显存 ≥24GB 的机器（`COMFY_MCP_URL` 或 `settings.comfy.url`
   改一下即可，无需改代码）；
5. 本机只做 draft 级实验，务必保持 `single_flight=true` 与守卫开启。

## 9. 常见问题

- **ComfyUI 起不来、日志里有 `UnicodeEncodeError: 'gbk' codec`**：必须设置 `PYTHONUTF8=1`；用 `start_comfyui_safe.ps1` 或 `mcp__comfy__start`。
- **`/prompt` 返回 400 `invalid_prompt_id`**：`prompt_id` 必须是规范小写带连字符 UUID（本服务已处理）。
- **生成很慢 / RAM 掉得快**：H3 首次运行要把 4.9GB VAE + 14.6GB 文本编码器 + 19.5GB UNet 动态加载进来（实测约 16 分钟），期间空闲 RAM 会掉到 2GB 左右；建议 `start(mode="lowmem")`、关闭其它大内存程序、模型常驻后不要重启。
- **显存/内存告警**：用 `status` 看 `hardware`，用 `free` 卸载；把 `profile` 降到 `draft`。
- **想支持新的 UI 工作流**：`workflow.ui_to_api(ui_json)` 可把画布 JSON 转 API（含 subgraph 展开），然后注册到 `workflow.TEMPLATES`。

## 10. 已知限制

- 长任务建议“提交后离开、定期 `job`/`wait`”，MCP 工具单次调用上限约 110 秒是刻意的（防止阻塞）；`wait` 默认只等 45 秒。
- `eta_minutes` 是经验公式，不是实测；H3 在 8GB 卡上的真实耗时以实测为准（首次 23.5 分钟仍未出第一步）。
- 参考视频/参考音频（r2v 的 `ref_videos`/`ref_audios`）暂未开放参数面，可在 API 模板上扩展。
- ComfyUI 由外部启动器启动时，`stop` 工具拒绝停止（没有 PID 记录），请手动关闭。

## 11. 后续可做（未实现）

- **换 8GB 友好的视频模型**：装 Wan2.1/2.2 T2V 1.3B、LTX-Video 等小模型，按现有模板机制加一个
  `templates/wan_t2v.api.json` + `workflow.TEMPLATES` 条目即可（r2v/fl2v/t2v 三套骨架可直接复用）。
- **参考视频/音频参数面**：r2v 的 `ref_videos` / `ref_audios` 已在节点层支持，补齐 `resolve()` 的
  连接与点号键即可。
- **远程 ComfyUI**：把 `settings.comfy.url` 或环境变量 `COMFY_MCP_URL` 指向显存更大的机器，
  代码不需要改；本机只用作编排端。
- **时间线级剪辑**：当前是单步 ffmpeg 工具，可再加一个 `timeline` 工具接受
  `[{clip, in, out, transition}]` 列表自动生成 filtergraph（concat 已有 crossfade 基础）。

## 12. Web 可视化界面 / 一键启动 / 架构

### 一键链式启动

```powershell
powershell -ExecutionPolicy Bypass -File K:\CONFI\comfy_mcp\start_all.ps1
# 可选：-NoHarness（不启动 Harness）、-NoComfyUI、-Port 8191、-PromptFile <自定义提示词>
```

**双击 `start_all.bat` 也能一键启动全部服务**（等价于上面命令，结束时显示结果；带参数调用时不暂停）。
停止：双击 `stop_all.bat` 或运行 `stop_all.ps1`（`-Comfy` 同时停 ComfyUI）。

脚本依次：启动 ComfyUI（8188）→ 停旧监控并启动 FastAPI Web UI（8190）→ 把内置提示词写入
`data/harness_prompt.txt` 并**复制到剪贴板** → 必要时启动 DeepSeek Harness。
停止：`stop_all.ps1`（加 `-Comfy` 同时停 ComfyUI）。

### Web UI / REST API（FastAPI）

- 主界面（即梦风格）：**http://127.0.0.1:8190/studio**（根路径 `/` 自动跳转）；旧版简易监控页：`/simple`。
  功能：创作（文生视频/首尾帧/参考图 + 比例/时长/档位/步数/seed/优化/LoRA）、我的作品画廊、
  视频超分、剪辑（11 种 ffmpeg 动作）、设置；顶部常驻硬件监控（GPU 利用率/显存/温度/功耗/RAM/commit/队列，点击展开详情）。
- API 文档（OpenAPI，供其他 agent 自动发现）：**http://127.0.0.1:8190/docs**
- **实时事件通道**：`GET /api/realtime` 返回同源 WebSocket 地址，`WS /ws/comfy` 把浏览器双向转发到
  ComfyUI 的 `/ws`（execution_start / executing / progress / executed / success / error / interrupted），
  页面由 2 秒轮询升级为事件驱动（阶段条：排队→加载→编码→采样→解码→封装），断线指数退避重连。
- **任务控制台**：`任务` 标签页（队列/历史统计、任务历史与过滤、日志控制台自动滚动/复制/清空、模拟演示动效）。
- **ComfyUI PID 持久化**：`data/comfy_pid.json` 记录启动 PID，`status` 返回 `pid_alive`，
  `stop` 会先校验存活再 taskkill（修复"外部启动实例无法停止"的限制）。
- 主要接口：`/api/status`、`/api/templates`、`/api/plan`、`/api/generate`、`/api/jobs`、
  `/api/edit`、`/api/upscale`、`/api/videos`、`/api/upscale/models`、`/api/media`、`/api/settings`。
- 代码：`webapp/app.py`；复用 `server.ComfyService`，与 MCP 同一套领域模块。

### 超分模型（`ComfyUI\models\upscale_models\`，已下载）

`RealESRGAN_x4plus_anime_6B.pth`（17MB，动画首选）、`4x-UltraSharp.pth`（64MB）、
`4x_foolhardy_Remacri.pth`（64MB）、`RealESRGAN_x4plus.pth`（64MB，通用照片）、
`Real_HAT_GAN_SRx4.pth`（162MB，HAT 细节 SOTA）、`SwinIR-L x4 GAN`（136MB，干净图像）。
逐帧超分工具：`tools/upscale_video.py`（ffmpeg 管道流式处理，内存恒定）；Web UI 或
`POST /api/upscale` 可直接调用。

### 架构与扩展

见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)：分层、REST/MCP 接口清单、启动链路、
扩展点（新增模板/优化节点/超分模型/API/MCP 工具）。
