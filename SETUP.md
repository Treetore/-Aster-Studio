# Aster Studio

本地视频 / 图像生成工作台，基于 ComfyUI，可选接入 AstrBot。

## 快速开始

1. 运行 `setup/bootstrap.py` 下载最新 ComfyUI（或设置 `COMFYUI_ROOT` 指向已有安装）。
2. 运行 `python -m setup.doctor` 自检；首次启动 Web 会要求选择「易懂版 / 专业版」提示方式。
3. 启动 `start_all.bat`，打开 http://127.0.0.1:8190/studio 。
4. 首次启动向导中选择低显存方案并勾选需要的模型。

## 主机自适应

`system_profile.py` 自动检测 CPU / RAM / GPU VRAM，输出 `low_vram` / `normal` / `high` 模式；
720p 直出上限、分段阈值和推荐优化参数随之调整。

## 模型下载

`setup/models.json` 是可编辑目录；填写 `url` 后可用：

`
python -m setup.models --list
python -m setup.models --download h3-base,h3-te,h3-vae
`

也可以直接把模型放进 `ComfyUI/models/<target>/`。

## 自检

- 每个生成任务提交前自动自检；
- 启动器启动时写 `logs/launcher_selfcheck.log`；
- 错误提示支持两种风格：`easy`（白话）与 `pro`（错误码/路径/技术修复）。

## AstrBot（可选）

`astrbot_plugin_comfy_bridge` 为独立可选插件，不安装不影响本机 Web 使用。

## 目录

- `webapp/` 本地 Web 工作台
- `launcher/` 桌面启动器
- `setup/` 安装、模型、自检
- `docs/` 架构与策略
