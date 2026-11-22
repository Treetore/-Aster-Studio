# 项目架构与扩展指南（comfy_mcp）

> 面向后续 agent：本文件说明分层、接口、扩展点与启动链路。改代码前先读此文件与 `SPEC.md`。

## 1. 分层

```
K:\CONFI\comfy_mcp\
├── server.py / mcp_protocol.py     MCP stdio 服务（17 个工具，DSH 插件加载）
├── client.py                       ComfyUI HTTP 客户端（/prompt、/api/jobs、/upload…）
├── workflow.py                     工作流模板 + resolve/validate/ui_to_api + 模型覆盖 + optimize
├── safety.py                       硬件探测 / 成本估算 / Guard 守卫（单飞、RAM 阈值、watchdog）
├── jobs.py                         本地任务存储（原子写 JSON）
├── editing.py                      ffmpeg 剪辑（14 个函数）
├── templates\*.api.json            API 格式工作流模板（r2v / fl2v / t2v）
├── webapp\app.py                   FastAPI：REST API + 单页可视化界面（/docs 自动 OpenAPI）
├── tools\                          超分脚本、监控面板、下载脚本、启动脚本
├── examples\automation_example.py  无 MCP 的端到端示例
└── docs\                           WORKFLOWS / EDITING / OPTIMIZATION / ARCHITECTURE
```

数据流：`Web/MCP -> ComfyService(server.py) -> workflow/safety/jobs/client -> ComfyUI 8188`；剪辑与超分走 `editing.py` / `tools/upscale_video.py`。

## 2. 对外接口

### MCP（给 agent）
`mcp__comfy__status|hardware|start|stop|free|templates|plan|generate|job|jobs|cancel|wait|upload|edit|probe_media|settings|logs`

### REST（给脚本/其他 agent，自动文档在 `/docs`）
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/status` | ComfyUI/硬件/守卫/活动任务快照 |
| GET | `/api/templates` | 模板元数据与参数面 |
| POST | `/api/plan` | 干跑：resolve + validate + 成本估算 |
| POST | `/api/generate` | 提交生成（默认单飞+守卫） |
| GET | `/api/jobs`、`/api/jobs/{id}` | 任务列表/详情 |
| POST | `/api/jobs/{id}/cancel` | 取消 |
| POST | `/api/edit` | ffmpeg 剪辑（action + kwargs） |
| POST | `/api/upscale`、GET `/api/upscale/jobs` | 视频超分（后台任务） |
| GET | `/api/videos`、`/api/upscale/models` | 可选视频/超分模型 |
| GET | `/api/media?path=` | 安全读取 output/artifacts 媒体 |
| GET | `/api/realtime` | ComfyUI 实时事件通道信息（ws_url / client_id / direct_ws_url） |
| WS | `/ws/comfy` | 同源 WebSocket 代理 → ComfyUI `/ws`（execution_start/executing/progress/success/error…） |
| GET | `/api/hardware` | GPU 利用率/显存/温度/功耗 + RAM/commit + 队列（Web 顶部监控） |
| POST | `/api/upload` | 上传参考图/首尾帧到 ComfyUI input，返回文件名 |
| GET/POST | `/api/settings` | 读取/修改守卫与档位 |

## 3. 启动链路（start_all.ps1）

1. 检查 8188，必要时启动 ComfyUI（日志 `logs/comfy_*.log`）；
2. 停旧监控/旧 Web UI，启动 uvicorn `webapp.app:app`（端口 8190）；
3. 内置提示词写入 `data/harness_prompt.txt` 并复制到剪贴板，必要时启动 Harness；
4. 打印 Web UI 与 API 文档地址。

停止：`stop_all.ps1`（`-Comfy` 同时停 ComfyUI）。Windows 下可直接双击 `start_all.bat` / `stop_all.bat`。

## 4. 扩展点

- **新增工作流模板**：`templates/*.api.json` + `workflow.TEMPLATES` 元数据 + `_TEMPLATE_PARAMETERS`；用 `workflow.validate(api, object_info)` 验证。
- **新增节点级优化**：`workflow.resolve` 的 `optimize` 链（off/sage/lowmem/max）；新增节点时把 `model` 串接后的引用 rewrite 复用现有逻辑。
- **新增超分模型**：放入 `ComfyUI\models\upscale_models\*.pth`，`tools/upscale_video.py` 的 RRDBNet 加载器支持 RRDB 系；HAT/SwinIR 等非 RRDB 需用 spandrel 加载（ComfyUI 环境已装 spandrel）。
- **新增 REST 接口**：`webapp/app.py` 增加路由，复用 `SERVICE.tool_*`，写操作走 `LOCK`。
- **新增 MCP 工具**：`server.py build_server()` 注册工具 + schema，保持 handler 短小、返回 `{"ok": bool}`。

## 5. 约束

- 8GB VRAM / 32GB RAM：默认单飞；`min_ram_free_gb` / `trip_ram_free_gb` 可调（`data/settings.json`）。
- 生成任务与超分任务分开存储：`data/jobs.json`（MCP/Web 共用）与 `data/upscale_jobs.json`（Web）。
- MCP 与 Web 同时写 `jobs.json` 存在跨进程竞态风险：批量操作时只用一个入口。
