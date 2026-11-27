# Aster Studio

> 面向单机工作站的本地 AI 视频 / 图像生成工作台：以用户自行安装的 ComfyUI 为推理后端，提供 Web 工作台、桌面启动器、MCP 接口、任务队列、资源守卫、模型目录与 ffmpeg 后期处理。

- 社群反馈：**[QQ 群](https://qm.qq.com/q/Ww5b5dF5mO)** · [Issues](https://github.com/Treetore/-Aster-Studio/issues)

Aster Studio 是面向单台本地工作站的 AI 视频与图像生成工作台。项目以用户自行安装的 ComfyUI 为推理后端，提供 Web 工作台、桌面启动器、MCP 工具接口、任务队列、资源守卫、模型目录管理与 ffmpeg 后期处理能力。

- 代码许可：Apache License 2.0（见 [LICENSE](LICENSE)）
- 第三方组件与本项目不分发模型权重的声明：见 [NOTICE](NOTICE)
- 当前版本：1.0.0，源码分发

## 项目简介

Aster Studio 将 ComfyUI 的模型推理能力封装为可日常使用的本地工作台：视频（MiniMax H3）、图像（Qwen-Image / MOSS）、长片分段、超分与剪辑集中在同一界面；同时保留 MCP 与桥接接口，便于接入聊天机器人和自动化脚本。

项目不随仓库分发模型权重；ComfyUI、ffmpeg 与各模型遵循各自上游许可。当前版本面向 Windows + NVIDIA 单机环境，默认只监听本机回环地址。

## 项目状态

| 维度 | 现状 |
|---|---|
| 部署形态 | 单机、单 GPU、同一时刻单个生成任务 |
| 平台 | Windows 10/11 为主；其他平台未验证 |
| 分发方式 | 源码检出，无安装程序 |
| 推理后端 | 外部 ComfyUI（默认 `127.0.0.1:8188`），不随仓库分发 |
| 模型权重 | 不分发，需用户自行获取并遵守上游许可 |

## 已实现能力

| 能力 | 说明 | 主要位置 |
|---|---|---|
| Web 工作台 | 创作页、任务列表、画廊、素材 `@` 引用、日志与设置；`/studio` | `webapp/` |
| 桌面启动器 | 服务启停、状态监控、模型浏览与删除、诊断、控制台、最近任务、主题 | `launcher/` |
| MCP 工具接口 | stdio JSON-RPC：`status`、`hardware`、`start`、`stop`、`free`、`templates`、`plan`、`generate`、`job`、`jobs`、`cancel`、`wait`、`upload`、`edit`、`probe_media`、`settings`、`selfcheck`、`logs` | `server.py` |
| 工作流模板 | `minimax_h3_t2v`、`minimax_h3_fl2v`、`minimax_h3_r2v`、`minimax_h3_t2v_720p15s`、`minimax_h3_image`、`qwen_image`、`qwen_image_edit`、`moss_image`、`moss_image_edit` | `workflow.py`、`templates/` |
| 参数解析与校验 | 统一参数面（`profile`/尺寸/时长/帧数/步数/种子/参考图/首尾帧），解析优先级与节点校验 | `workflow.py` |
| 主机能力检测 | CPU/内存/显存探测，推荐 `low_vram`/`normal`/`high` 模式及对应限制，可手动覆盖 | `system_profile.py` |
| 首次启动流程 | 选择提示风格（`easy`/`pro`）、确认主机模式、勾选模型、对勾选项执行下载 | `webapp/studio.html` |
| 模型目录 | 标识、目标目录、文件名、体积估算、上游主页；CLI `--list`/`--download` | `setup/models.json`、`setup/models.py` |
| 环境准备脚本 | 依赖与路径检查、可选拉取 ComfyUI、打印后续步骤 | `setup/install.py`、`setup/bootstrap.py` |
| 自检 | Python 版本、ComfyUI、页面资源、ffmpeg、所选模型、数据目录写权限；两种提示风格 | `setup/doctor.py` |
| 资源守卫 | 预检（内存/显存/磁盘/队列/并发）与运行中监控，内存触底时中断任务 | `safety.py` |
| 任务设施 | 原子写入的任务记录、查询、取消、全局单任务锁 | `jobs.py`、`job_lock.py` |
| ffmpeg 后期 | 裁剪、拼接、缩放、混音、字幕、水印、变速、抽帧、抽音、封面、GIF、探测 | `editing.py` |
| 长片与超分 | 分段生成、首帧衔接、拼接；视频超分端点 | `tools/segment_film.py`、`tools/auto_film.py`、`tools/upscale_*.py` |
| 聊天桥接接口 | 目录、提交、结果轮询、令牌化媒体下载、健康检查、IP 白名单、访问日志 | `webapp/app.py` |
| 隧道控制 | 启动器内 cloudflared 启停与状态，配合白名单与令牌对外暴露 | `launcher/`、`tools/tunnel.ps1` |
| 手绘风格 Skill | 372 风格、36 主题色、170 排版；已集成生图页 | `optional/skills/handraw-style/` |
| 可选聊天插件 | AstrBot 插件源码，默认不安装 | `optional/astrbot_plugin_comfy_bridge/` |

## 未实现与限制

以下内容不属于 1.0.0 的能力范围，文档、界面文案或历史记录中若与此冲突，以本节为准：

| 项目 | 现状 |
|---|---|
| 上游模型地址自动解析 | 未实现。`setup/models.json` 的 `url` 默认为空，需自行填写或手动放置文件 |
| 模型文件上传 | 未实现。界面只做浏览与删除，模型需放入 `ComfyUI/models/` |
| 向导内安装 ComfyUI | 未实现。使用 `python setup/install.py` 或 `python -m setup.bootstrap` |
| 多用户与鉴权 | 未实现。Web 界面无登录，依赖 loopback 绑定与 IP 白名单 |
| 并发生成 | 未设计。全局单任务锁，`allow_concurrent` 默认关闭 |
| 跨平台支持 | 未验证。脚本以 Windows 与 PowerShell 为主 |
| 打包/安装程序 | 未提供 |
| 聊天编辑模式 | 插件仅覆盖文生图/文生视频；图生图需源图，聊天流程未接入上传 |

已知实现缺陷与文档口径差异记录在 [CHANGELOG.md](CHANGELOG.md) 的 Known limitations 章节。

## 架构

```
Browser / Desktop Launcher
        │
        ▼
FastAPI Webapp (8190) ── MCP Server (stdio) ── ComfyUI (8188)
        │                                            │
        ├── Job store / global lock                   ├── Diffusion models
        ├── Resource guard                            ├── Text encoders
        ├── Model policy                              └── VAE / LoRA
        └── ffmpeg pipeline
```

| 模块 | 说明 |
|---|---|
| `webapp/` | Web 工作台、REST 接口、bridge 接口、素材与媒体访问 |
| `launcher/` | 桌面启动器、服务启停、硬件监控、隧道、诊断与日志 |
| `server.py` | MCP 服务与生成服务层 |
| `workflow.py` | 工作流模板解析、参数校验、API 格式转换 |
| `safety.py` | 资源预检与运行中守卫 |
| `jobs.py` / `job_lock.py` | 任务记录与全局单任务锁 |
| `model_policy.py` / `proc_cleaner.py` | 模型切换与显存释放、任务期间进程清理 |
| `editing.py` | ffmpeg 后期处理封装 |
| `setup/` | 环境准备、ComfyUI 拉取、模型目录、自检 |
| `system_profile.py` | 主机能力检测与模式推荐 |
| `tools/` | 分段、长片、超分、隧道与诊断脚本 |
| `tests/` | 离线单元测试与固定数据集 |

## 环境要求

| 项目 | 要求 |
|---|---|
| 操作系统 | Windows 10/11 |
| Python | 3.10 及以上（推荐 3.12） |
| GPU | NVIDIA，支持 CUDA，显存 8GB 及以上 |
| 内存 | 16GB 及以上（推荐 32GB） |
| 磁盘 | 视模型而定，建议 60GB 以上可用空间 |
| 外部依赖 | ComfyUI、ffmpeg/ffprobe、模型权重 |

## 快速开始

```powershell
# 1. 获取代码
git clone https://github.com/Treetore/-Aster-Studio.git
cd -- -Aster-Studio

# 2. 安装运行时依赖
python -m pip install -r requirements.txt

# 3. 环境准备（检查依赖与路径，可选拉取 ComfyUI，不下载模型）
python setup/install.py

# 4. 自检
python -m setup.doctor

# 5. 启动
start_all.bat
```

启动后访问 `http://127.0.0.1:8190/studio`。若 ComfyUI 已存在，可在 `.env` 或环境变量中指向它，无需拉取。

详细步骤、路径解析顺序与排错见 [SETUP.md](SETUP.md)。

## 配置

### 配置文件

| 文件 | 内容 |
|---|---|
| `data/settings.json` | 运行参数、资源守卫、生成档位、ffmpeg 配置 |
| `data/selected_models.json` | 已选择的模型标识 |
| `data/user_mode.json` | 提示风格（`easy` / `pro`） |
| `data/host_mode.json` | 主机模式覆盖（`low_vram` / `normal` / `high`） |
| `data/ip_allowlist.json` | bridge 与全局 IP 白名单（可选） |
| `data/bridge_token.txt` | bridge 令牌（缺失时自动生成） |
| `.env` | 本地环境变量，参考 `.env.example`；已被系统环境变量覆盖 |

`data/`、`logs/`、`.env` 不纳入版本控制。

### 环境变量

| 变量 | 作用 |
|---|---|
| `COMFYUI_ROOT`（或 `COMFY_MCP_COMFY_ROOT`） | ComfyUI 目录或便携版根目录 |
| `COMFY_MCP_PYTHON` | 运行 ComfyUI 的解释器 |
| `COMFY_MCP_URL` | ComfyUI HTTP 地址，默认 `http://127.0.0.1:8188` |
| `COMFY_BRIDGE_TOKEN` | bridge 令牌，未设置时读取 `data/bridge_token.txt` |
| `FFMPEG_PATH` / `FFPROBE_PATH` | ffmpeg / ffprobe 可执行文件或所在目录 |

### 可移植性

- 项目内路径由 `config.py` 从文件位置推导，不含写死盘符；ComfyUI 默认按同级布局探测 `<工作区>/mian/ComfyUI_windows_portable/ComfyUI` 等候选位置。
- 部署到其他目录时，优先用 `.env` 或环境变量覆盖，无需改代码。
- `tools/` 下部分 PowerShell 脚本（隧道、页文件、TDR 等）仍以内联本地路径为例，迁移机器时需按实际路径调整。

## 自检

自检覆盖 Python 版本、ComfyUI 安装、页面资源、ffmpeg、所选模型文件与数据目录写权限。

- 启动器启动时执行一次，结果写入 `logs/launcher_selfcheck.log`；
- 生成任务提交前执行；
- 也可通过 `python -m setup.doctor`、`GET /api/doctor` 或 MCP 工具 `selfcheck` 主动触发；
- 提示风格：`easy`（白话与简化步骤）、`pro`（错误码、路径与技术建议）。

## API 概览

Web 服务默认监听 `127.0.0.1:8190`，完整定义以运行后的 `/docs`（OpenAPI）为准。

| 分组 | 代表端点 |
|---|---|
| 状态与硬件 | `GET /api/status`、`/api/queue`、`/api/realtime`、`/api/hardware`、`/api/doctor` |
| 生成 | `GET /api/templates`、`POST /api/plan`、`POST /api/generate`、`GET /api/jobs/{id}`、`POST /api/jobs/{id}/cancel` |
| 设置与安装 | `GET/POST /api/settings`、`GET /api/setup/status`、`POST /api/setup/mode`、`POST /api/setup/hostmode`、`POST /api/setup/models`、`POST/GET /api/setup/download` |
| 素材与媒体 | `POST /api/upload`、`GET /api/assets`、`/api/images`、`/api/videos`、`/api/gallery`、`GET /api/media` |
| 后期 | `POST /api/edit`、`GET /api/logs` |
| 长片与超分 | `POST /api/film/start`、`GET /api/film/state`、`POST /api/film/cancel`、`POST /api/upscale`、`GET /api/upscale/jobs/{id}` |
| 聊天桥接 | `GET /api/bridge/models`、`POST /api/bridge/submit`、`GET /api/bridge/result/{id}`、`GET /api/bridge/media/{id}`、`GET /api/bridge/health` |
| 模型与显存 | `GET /api/models`、`POST /api/free` |

启动器另提供 `127.0.0.1:8191` 的本地接口（服务启停、`/api/tunnel`、模型浏览与删除等）。

## 目录结构

```
comfy_mcp/
  webapp/            Web 工作台与 REST/bridge 接口
  launcher/          桌面启动器
  setup/             环境准备、ComfyUI 拉取、模型目录、自检
  tools/             分段、长片、超分、隧道等脚本
  templates/         工作流 API 模板
  tests/             离线单元测试与固定数据集
  optional/          可选组件（默认不安装）
  docs/              分类文档（architecture / policies / features / integrations / release）
scripts/           辅助脚本（长片、CLI、安全启动）
PROJECT_STRUCTURE.md 目录结构与约定
  data/              运行数据（不纳入版本控制）
  logs/              日志（不纳入版本控制）
```

## 测试与 CI

```powershell
# 在包含 comfy_mcp 目录的父目录下执行
python -m pytest comfy_mcp/tests -q
```

- 单元测试离线运行：HTTP 部分使用本地假服务，硬件部分打桩，不需要 GPU、模型或在线 ComfyUI；
- 只读在线检查与 ffmpeg 素材测试在条件不满足时自动跳过；
- CI 定义于 [.github/workflows/ci.yml](.github/workflows/ci.yml)：`windows-latest` + Python 3.12，执行字节编译、跟踪文件守卫与离线测试子集；
- 离线测试套件不依赖 GPU 或模型权重，CI 运行完整 `pytest tests`。

## 可选组件：AstrBot 插件

`optional/astrbot_plugin_comfy_bridge/` 提供 AstrBot 聊天入口插件源码，用于把聊天提示词转发到本机生成服务并回传结果。该组件默认不安装、不随主流程加载，安装与配置见 [optional/README.md](optional/README.md)。不安装不影响 Web 工作台与 MCP 接口。

## 文档

- [SETUP.md](SETUP.md)：安装、路径解析与部署
- [CONTRIBUTING.md](CONTRIBUTING.md)：开发环境、测试与提交约定
- [SECURITY.md](SECURITY.md)：暴露面、令牌处理与漏洞报告
- [CHANGELOG.md](CHANGELOG.md)：版本变更与已知限制
- [docs/release/RELEASE_CHECKLIST.md](docs/release/RELEASE_CHECKLIST.md)：发布检查清单
- [docs/architecture/ARCHITECTURE.md](docs/architecture/ARCHITECTURE.md)：系统架构
- [docs/architecture/WORKFLOWS.md](docs/architecture/WORKFLOWS.md)：工作流模板与参数
- [docs/features/EDITING.md](docs/features/EDITING.md)：ffmpeg 后期处理
- [docs/architecture/OPTIMIZATION.md](docs/architecture/OPTIMIZATION.md)：性能与显存优化
- [docs/policies/AUTO_VIDEO_POLICY_20261122.md](docs/policies/AUTO_VIDEO_POLICY_20261122.md)：视频时长与分段策略

## 许可

- 源代码：Apache License 2.0，全文见 [LICENSE](LICENSE)。
- 第三方组件、模型权重不分发的声明与上游许可提示：见 [NOTICE](NOTICE)。
- ComfyUI、模型权重、ffmpeg 与 AstrBot 均为外部组件，各自遵循其上游许可；使用者需自行遵守。


## 社群与反馈

- QQ 群：**[点击加入](https://qm.qq.com/q/Ww5b5dF5mO)**
- 问题反馈：GitHub Issues
- 细节与反馈模板：[docs/COMMUNITY.md](docs/COMMUNITY.md)

## 后续计划

后续计划在本地工作区维护，不随仓库上传。
