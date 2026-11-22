# Aster Studio

Aster Studio 是面向本地工作站的 AI 视频与图像生成工作台。项目以 ComfyUI 为推理后端，提供 Web 管理界面、桌面启动器、MCP 工具接口、任务队列、资源守卫、模型管理与 ffmpeg 后期处理能力。

## 功能

- **多模型工作流**：支持 MiniMax H3 文生视频、首尾帧、参考图视频，以及 Qwen Image 2.1、MOSS Image 的图像生成与编辑。
- **主机自适应**：启动时检测 CPU、内存与 GPU 显存，自动选择 `low_vram`、`normal` 或 `high` 模式，并调整分辨率、步数、优化策略与直出时长上限。
- **任务管理**：统一任务锁、任务记录、进度查询、取消与失败重试。
- **资源守卫**：提交前检查内存、磁盘与队列；运行中监控内存/显存，异常时中断任务并记录原因。
- **长视频分段**：时长达到阈值时自动切换分段生成，支持首帧衔接、参考图与音视频拼接。
- **后期处理**：基于 ffmpeg 的裁剪、拼接、变速、字幕、混音、封面与超分。
- **模型管理**：按模型目录分类浏览、上传、删除与选择性下载。
- **安装自检**：启动器与每个生成任务执行前进行自检，输出缺失文件、路径与修复建议。
- **可选组件**：AstrBot 桥接插件为独立可选模块，不影响本机 Web 使用。

## 架构

```
Browser / Desktop Launcher
        │
        ▼
FastAPI Webapp (8190) ── MCP Server ── ComfyUI (8188)
        │                                   │
        ├── Job store / global lock          ├── Diffusion models
        ├── Resource guard                   ├── Text encoders
        ├── Model policy                     └── VAE / LoRA
        └── ffmpeg pipeline
```

主要模块：

| 模块 | 说明 |
|---|---|
| `webapp/` | Web 工作台、任务接口、生成参数与素材管理 |
| `launcher/` | 桌面启动器、服务启停、硬件监控、日志与诊断 |
| `server.py` | MCP 服务与生成服务层 |
| `workflow.py` | 工作流模板解析、参数校验与 API 格式转换 |
| `safety.py` | 资源预检与运行中守卫 |
| `job_lock.py` | 全局单任务锁 |
| `model_policy.py` | 模型切换与显存释放策略 |
| `proc_cleaner.py` | 任务期间临时进程清理与编码进程回收 |
| `setup/` | 安装引导、模型目录、自检与 ComfyUI 下载 |
| `system_profile.py` | 主机能力检测与模式推荐 |
| `editing.py` | ffmpeg 后期处理封装 |

## 快速开始

环境要求：

- Windows 10/11
- Python 3.10 及以上（推荐 3.12/3.13）
- 支持 CUDA 的 NVIDIA GPU；显存 8GB 及以上
- 内存 16GB 及以上（推荐 32GB）
- ffmpeg（用于视频拼接与后期处理）

安装步骤：

1. 获取项目代码并进入项目目录。
2. 运行 `python -m setup.bootstrap` 下载最新 ComfyUI，或设置 `COMFYUI_ROOT` 指向已有安装。
3. 运行 `python -m setup.doctor` 执行环境自检。
4. 启动 `start_all.bat`，访问 `http://127.0.0.1:8190/studio`。
5. 首次启动时选择提示方式、主机模式与需要下载的模型。

详细说明见 [SETUP.md](SETUP.md)。

## 配置

- 运行配置：`data/settings.json`
- 模型选择：`data/selected_models.json`
- 提示方式：`data/user_mode.json`（`easy` / `pro`）
- 主机模式：由 `system_profile.py` 检测，可在首次启动向导中覆盖
- 环境变量：`COMFYUI_ROOT`、`COMFY_BRIDGE_TOKEN`

## 自检

自检覆盖 Python 版本、ComfyUI 安装、页面资源、ffmpeg、所选模型文件与数据目录写权限。

- 启动器启动时执行一次，结果写入 `logs/launcher_selfcheck.log`；
- 每个生成任务提交前执行；
- 错误提示支持两种风格：
  - `easy`：白话描述与简化处理步骤；
  - `pro`：错误码、具体路径与技术修复建议。

## API

Web 服务默认监听 `127.0.0.1:8190`。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 服务健康状态 |
| GET | `/api/templates` | 可用工作流模板 |
| POST | `/api/plan` | 参数校验与资源预检 |
| POST | `/api/generate` | 提交生成任务 |
| GET | `/api/jobs/{id}` | 查询任务 |
| POST | `/api/jobs/{id}/cancel` | 取消任务 |
| GET | `/api/doctor` | 执行自检 |
| GET | `/api/setup/status` | 安装与主机状态 |
| POST | `/api/setup/mode` | 设置提示方式 |

完整接口见运行后的 `/docs`。

## 目录结构

```
comfy_mcp/
  webapp/            Web 工作台
  launcher/          桌面启动器
  setup/             安装、模型与自检
  tools/             分段、超分、隧道等辅助脚本
  templates/         工作流 API 模板
  docs/              架构与策略文档
  data/              运行数据（不纳入版本控制）
  logs/              日志（不纳入版本控制）
```

## 可选组件

AstrBot 桥接插件位于独立目录 `astrbot_plugin_comfy_bridge`，用于将聊天消息转发到本机生成服务。该组件不参与本机 Web 运行，可按需安装。

## 文档

- [SETUP.md](SETUP.md)：安装与部署
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)：系统架构
- [docs/WORKFLOWS.md](docs/WORKFLOWS.md)：工作流与参数
- [docs/OPTIMIZATION.md](docs/OPTIMIZATION.md)：性能与显存优化
- [docs/AUTO_VIDEO_POLICY_20261122.md](docs/AUTO_VIDEO_POLICY_20261122.md)：视频时长与分段策略

## 状态

当前版本：1.0.0。项目处于持续开发阶段，接口与配置可能调整。
