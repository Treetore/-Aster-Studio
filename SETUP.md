# Aster Studio 安装与部署

## 1. 环境要求

| 项目 | 要求 |
|---|---|
| 操作系统 | Windows 10/11 |
| Python | 3.10 及以上，推荐 3.12/3.13 |
| GPU | NVIDIA，支持 CUDA；显存 8GB 及以上 |
| 内存 | 16GB 及以上，推荐 32GB |
| 磁盘 | 至少 60GB 可用空间（模型体积较大） |
| 其他 | ffmpeg（视频拼接与后期处理） |

## 2. 获取项目

```powershell
git clone https://github.com/Treetore/-Aster-Studio.git
cd -- -Aster-Studio
```

如使用发行包，解压后进入项目目录。

## 3. 安装 ComfyUI

项目提供自动下载脚本，默认安装到 `../mian/ComfyUI_windows_portable/ComfyUI`：

```powershell
python -m setup.bootstrap
```

如已有 ComfyUI，可设置环境变量指向安装目录：

```powershell
$env:COMFYUI_ROOT = "D:\ComfyUI"
```

## 4. 安装 Python 依赖

使用 ComfyUI 便携版自带的 `python_embeded` 运行，或使用独立虚拟环境安装 `requirements.txt`。

```powershell
python -m pip install -r requirements.txt
```

## 5. 环境自检

```powershell
python -m setup.doctor
```

自检覆盖 Python 版本、ComfyUI 路径、页面资源、ffmpeg、所选模型与数据目录写权限。

错误提示支持两种风格：

- `easy`：白话描述与简化处理步骤；
- `pro`：错误码、具体路径与技术修复建议。

提示方式保存在 `data/user_mode.json`，也可在首次启动页面选择。

## 6. 启动服务

```powershell
start_all.bat
```

或分别启动：

```powershell
python server.py --stdio
python -m uvicorn webapp.app:app --host 127.0.0.1 --port 8190
```

Web 工作台：`http://127.0.0.1:8190/studio`

桌面启动器：运行 `python launcher/run.py`，或使用 `launcher/AsterLauncher.bat`。

## 7. 首次启动

首次访问 Web 工作台时执行以下配置：

1. 选择提示方式：`易懂版` 或 `专业版`；
2. 确认主机检测结果与推荐模式（`low_vram` / `normal` / `high`）；
3. 在模型列表中选择需要使用的模型，并填写下载地址或手动放置模型文件。

模型目录结构：

```
ComfyUI/models/
  diffusion_models/
  text_encoders/
  vae/
  loras/
```

模型清单定义在 `setup/models.json`，可通过以下命令查看与下载：

```powershell
python -m setup.models --list
python -m setup.models --download h3-base,h3-te,h3-vae
```

`url` 为空表示需要手动填写下载地址，或将文件直接放入对应目录。

## 8. 配置

主要配置文件：

| 文件 | 内容 |
|---|---|
| `data/settings.json` | 运行参数、资源守卫、主机模式 |
| `data/selected_models.json` | 已选择的模型 |
| `data/user_mode.json` | 提示方式（easy/pro） |
| `data/ip_allowlist.json` | 桥接访问白名单（可选） |

环境变量：

| 变量 | 说明 |
|---|---|
| `COMFYUI_ROOT` | ComfyUI 安装目录 |
| `COMFY_BRIDGE_TOKEN` | 桥接访问令牌（可选） |

## 9. 可选组件：AstrBot

`astrbot_plugin_comfy_bridge` 为独立插件，仅在使用 AstrBot 聊天入口时需要安装。安装方式参见该目录下的 `README.md`。不安装该插件不影响本机 Web 工作台。

## 10. 运行维护

- 日志目录：`logs/`
- 任务记录：`data/jobs.json`
- 启动器自检结果：`logs/launcher_selfcheck.log`
- 停止服务：`stop_all.bat`

每次生成任务提交前自动执行自检；启动器启动时执行一次自检。
