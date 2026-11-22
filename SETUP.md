# Aster Studio 安装与部署

本文说明环境要求、路径解析规则、ComfyUI 与依赖安装、首次启动配置以及常见问题。项目定位为单机工作站工具，默认可通过本机回环地址访问。

## 1. 环境要求

| 项目 | 要求 |
|---|---|
| 操作系统 | Windows 10/11 |
| Python | 3.10 及以上，推荐 3.12 |
| GPU | NVIDIA，支持 CUDA，显存 8GB 及以上 |
| 内存 | 16GB 及以上，推荐 32GB |
| 磁盘 | 视所选模型而定，建议预留 60GB 以上 |
| 外部组件 | ComfyUI、ffmpeg/ffprobe、模型权重（均需自行获取） |
| 端口 | 8188（ComfyUI）、8190（Web）、8191（启动器） |

## 2. 获取代码

```powershell
git clone https://github.com/Treetore/-Aster-Studio.git
cd -- -Aster-Studio
```

如使用发行压缩包，解压后进入项目目录即可。

## 3. 路径解析与可移植性

`config.py` 从自身位置推导所有项目内路径，不含写死盘符。ComfyUI 目录按下列优先级解析：

1. 环境变量 `COMFYUI_ROOT`（也接受 `COMFY_MCP_COMFY_ROOT`、`COMFY_ROOT`）。变量可写两种形式：含 `main.py` 的 ComfyUI 目录，或便携版根目录（含 `ComfyUI\` 与 `python_embeded\`）。
2. 同级目录探测：`<工作区>\mian\ComfyUI_windows_portable\ComfyUI`、`<工作区>\ComfyUI_windows_portable\ComfyUI`、项目内同名目录、上一层级的同名布局等。
3. 都未命中时，保留首选默认位置并在来源标记中说明，便于自检与安装脚本报告问题。

解释器解析顺序：`COMFY_MCP_PYTHON`（或 `COMFY_PYTHON`）→ 便携版 `python_embeded\python.exe` → 当前解释器。

需要安装到其他位置时，推荐使用项目根的 `.env`：

```powershell
Copy-Item .env.example .env
notepad .env
```

`.env` 只补充尚未存在的环境变量，系统环境变量优先。可用变量：

| 变量 | 作用 |
|---|---|
| `COMFYUI_ROOT` | ComfyUI 目录或便携版根目录 |
| `COMFY_MCP_PYTHON` | 运行 ComfyUI 的解释器 |
| `COMFY_MCP_URL` | ComfyUI HTTP 地址，默认 `http://127.0.0.1:8188` |
| `COMFY_BRIDGE_TOKEN` | bridge 令牌；未设置时读取 `data/bridge_token.txt` |
| `FFMPEG_PATH` / `FFPROBE_PATH` | ffmpeg / ffprobe 可执行文件或所在目录 |

注意：`tools/` 下部分 PowerShell 脚本（隧道、页文件、TDR 等）仍以内联本地路径为例，迁移机器时需按实际路径调整；主流程与启动脚本不使用这些内联路径。

## 4. 安装 ComfyUI

仓库不包含 ComfyUI，也不分发模型。二选一：

**方式 A：一条命令做环境准备（推荐）**

```powershell
python setup/install.py
```

脚本只做环境准备：检查运行时依赖与路径、在缺失时询问是否拉取 ComfyUI、打印后续步骤。它不下载模型、不启动 ComfyUI、不执行生成。常用参数：

```powershell
python setup/install.py --yes          # 非交互，缺 ComfyUI 时直接拉取
python setup/install.py --skip-comfy   # 只检查依赖与路径
python setup/install.py --json         # 机器可读输出
```

**方式 B：仅拉取 ComfyUI**

```powershell
python -m setup.bootstrap                                   # 拉取到默认同级布局
python -m setup.bootstrap --dest D:\AI\ComfyUI --ref master # 指定目录与分支
```

如已安装 ComfyUI，跳过本节，只需在 `.env` 中设置 `COMFYUI_ROOT`。

首次启动向导**不包含** ComfyUI 下载入口；向导只处理提示风格、主机模式与模型勾选。

## 5. 安装 Python 依赖

```powershell
python -m pip install -r requirements.txt
```

- `requirements.txt` 只包含 Web/MCP 运行所需的包，版本为宽松下界；
- `torch`、`torchvision` 与 ComfyUI 节点依赖由 ComfyUI 便携版自带，不在本文件中声明；
- 若希望 Web 与 ComfyUI 共用同一环境，请使用便携版的 `python_embeded\python.exe` 安装本文件；
- 开发用途可安装 `pyproject.toml` 中的 `dev` 附加项（含 `pytest`）。

## 6. 安装 ffmpeg

ffmpeg/ffprobe 为外部二进制，不是 pip 包。解析优先级：环境变量（`FFMPEG_PATH` / `FFPROBE_PATH`）→ `data/settings.json` 的 `ffmpeg.path` → `PATH` → 项目内 `tools/` 目录。

缺失时 `ffmpeg` 相关检查会报告为警告（非阻断），但视频拼接、字幕、混流、抽帧等功能不可用。

## 7. 环境自检

```powershell
python -m setup.doctor
```

自检项包括：Python 版本、ComfyUI `main.py`、Web 页面资源、模板、ffmpeg、所选模型文件、数据目录写权限、可选 CUDA/PyTorch 与 ComfyUI 端口可达性。阻断项与警告项分开报告：

- `easy`：白话描述与简化处理步骤；
- `pro`：错误码、具体路径与技术修复建议。

提示风格保存在 `data/user_mode.json`，也可在首次启动页面选择。同一自检也可通过 `GET /api/doctor` 或 MCP 工具 `selfcheck` 触发。

## 8. 启动服务

```powershell
start_all.bat
```

常用开关（透传给 `start_all.ps1`）：

| 开关 | 作用 |
|---|---|
| `-Port 8191` | 更换 Web 端口（默认 8190） |
| `-ComfyPort 8188` | 更换 ComfyUI 端口 |
| `-NoComfyUI` | 只启动 Web，不启动 ComfyUI |
| `-ComfyRoot <路径>` | 本次启动指定 ComfyUI 目录 |
| `-ComfyPython <路径>` | 本次启动指定解释器 |
| `-DryRun` | 只打印解析结果与命令，不启动进程 |
| `-Tunnel` | 同时启动隧道（对外暴露前请阅读 `SECURITY.md`） |

也可分别启动：

```powershell
python server.py --stdio
python -m uvicorn webapp.app:app --host 127.0.0.1 --port 8190
```

- Web 工作台：`http://127.0.0.1:8190/studio`
- 桌面启动器：`python launcher/run.py`，或 `launcher\AsterLauncher.bat`（本地服务 `127.0.0.1:8191`）
- MCP 服务：stdio，由客户端（如 Harness）拉起，不监听端口

## 9. 首次启动配置

首次访问 Web 工作台会进入两步配置：

1. 选择提示风格：`易懂版`（`easy`）或 `专业版`（`pro`）；
2. 确认主机检测结果与推荐模式（`low_vram` / `normal` / `high`，可选择 `auto` 或手动覆盖），勾选需要的模型并保存；`下载所选` 会对勾选项执行下载。

模型下载的前提是 `setup/models.json` 中该条目的 `url` 已填写。默认 `url` 为空，此时下载会返回明确错误，需要：

- 编辑 `setup/models.json` 填入稳定下载地址后重试；或
- 手动把文件放入对应目录（推荐，最可控）。

模型目录结构：

```
ComfyUI/models/
  diffusion_models/
  text_encoders/
  vae/
  loras/
```

模型目录的 CLI 用法：

```powershell
python -m setup.models --list
python -m setup.models --download h3-base,h3-te,h3-vae
```

`--download` 同样要求条目已配置 `url`。

## 10. 运行配置

| 文件 | 内容 |
|---|---|
| `data/settings.json` | 生成档位、资源守卫阈值、ComfyUI 启动参数、ffmpeg 配置 |
| `data/selected_models.json` | 已选择的模型标识 |
| `data/user_mode.json` | 提示风格（`easy` / `pro`） |
| `data/host_mode.json` | 主机模式覆盖 |
| `data/ip_allowlist.json` | IP 白名单（未配置时使用占位条目，仅本机可用） |
| `data/bridge_token.txt` | bridge 令牌，缺失时自动生成 |
| `data/jobs.json` | 任务记录 |
| `.env` | 本地环境变量（不纳入版本控制） |

## 11. 可选组件：AstrBot 插件

`optional/astrbot_plugin_comfy_bridge/` 为可选聊天插件源码，默认不安装、不参与主流程。安装步骤、配置项与安全注意事项见 [optional/README.md](optional/README.md)，插件指令与参数说明见 [optional/astrbot_plugin_comfy_bridge/README.md](optional/astrbot_plugin_comfy_bridge/README.md)。不安装不影响 Web 工作台与 MCP 接口。

插件通过 `X-Bridge-Token` 访问本机 `/api/bridge/*`。对外暴露前，请阅读 [SECURITY.md](SECURITY.md) 关于隧道、白名单与令牌的要求。

## 12. 运行维护

| 项目 | 位置 |
|---|---|
| 日志 | `logs/` |
| 启动器自检结果 | `logs/launcher_selfcheck.log` |
| bridge 访问日志 | `logs/bridge_access.log` |
| 任务记录 | `data/jobs.json` |
| 停止服务 | `stop_all.bat` |

备份建议：`data/settings.json`、`data/selected_models.json`、`data/host_mode.json` 与 `.env`。令牌文件请单独保管，不要纳入版本控制。

## 13. 端口与网络

| 服务 | 默认地址 | 说明 |
|---|---|---|
| ComfyUI | `127.0.0.1:8188` | 启动脚本默认 `--listen 127.0.0.1` |
| Web 工作台 | `127.0.0.1:8190` | 无登录，依赖 loopback 绑定 |
| 启动器 | `127.0.0.1:8191` | 本地管理界面 |
| MCP | stdio | 无网络监听 |

不建议把上述端口绑定到 `0.0.0.0`。需要远程或聊天平台访问时，请只通过隧道暴露 `/api/bridge/*`，并同时启用令牌与 IP 白名单；细节见 [SECURITY.md](SECURITY.md)。

## 14. 常见问题

| 自检项 / 现象 | 处理 |
|---|---|
| `python_version` | 升级到 Python 3.10 以上（推荐 3.12），并用该解释器启动 |
| `comfyui_main` | 运行 `python setup/install.py`，或设置 `COMFYUI_ROOT` 指向含 `main.py` 的目录 |
| `web_assets` | 重新获取完整仓库，确认 `webapp/` 与 `launcher/` 目录存在 |
| `ffmpeg` | 安装 ffmpeg 并加入 `PATH`，或设置 `FFMPEG_PATH`、`data/settings.json` 的 `ffmpeg.path` |
| `models` | 补齐所选模型文件，或取消勾选暂不使用的模型 |
| `data_writable` | 将项目放到可写目录，或修正目录权限 |
| `comfy_port`（警告） | ComfyUI 未启动或端口不一致，检查 `-ComfyPort` 与 `COMFY_MCP_URL` |
| `torch`（警告） | 便携版解释器缺少 CUDA 版 torch 时出现；确认使用 ComfyUI 自带解释器 |
| 生成任务提示模型缺失 | 检查 `ComfyUI/models/` 下的文件名是否与 `setup/models.json` 的 `file` 一致 |
| 下载按钮报错 | 条目 `url` 为空；填写下载地址或手动放置文件 |
| 端口被占用 | 使用 `-Port` / `-ComfyPort` 更换端口，或先 `stop_all.bat` |

说明：`easy` 风格的 ComfyUI 缺失提示会指向 `setup/install.py` 或 `setup/bootstrap.py`；首次启动向导不负责下载 ComfyUI。

## 15. 许可

源码遵循 Apache License 2.0（[LICENSE](LICENSE)）。仓库不分发模型权重；ComfyUI、模型、ffmpeg 与 AstrBot 各自遵循其上游许可，使用前请阅读 [NOTICE](NOTICE)。
