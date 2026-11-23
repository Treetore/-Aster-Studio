# 项目描述

## 一句话

Aster Studio 是面向单机工作站的本地 AI 视频 / 图像生成工作台，以 ComfyUI 为推理后端，提供 Web 界面、桌面启动器、MCP 接口、长片分段、任务队列、模型市场与后期处理能力。

## 短描述（GitHub About / 应用商店，≤350 字）

本地 ComfyUI AI 视频 / 图像工作站：支持 MiniMax H3 视频、Qwen-Image 2.1、MOSS-Image、长片自动分段、自定义时长与尺寸、任务优先级队列、项目与预设、ComfyUI 模型市场、MCP / HTTP / 机器人桥接。Windows + NVIDIA，单机部署，模型权重不随仓库分发。

## 长描述

Aster Studio 将 ComfyUI 的模型推理能力封装为可日常使用的本地工作台，面向希望在自己的 Windows + NVIDIA 机器上完成视频与图像创作、又不想直接操作节点图的用户。

主要能力：

- **视频生成**：MiniMax H3 文生视频、首尾帧、参考图；长片自动分段、首帧接力、拼接成片。
- **图像生成**：Qwen-Image 2.1 文生图/图像编辑，MOSS-Image 文生图/编辑。
- **规格控制**：视频时长 1–120s 自定义，宽高自定义；长片总时长 5–600s，按 124 帧/段自动换算。
- **主机适配**：低显存 / 标准 / 高配三档；直出上限与像素上限随硬件变化，超限自动分段。
- **任务系统**：优先级队列、任务状态、取消、日志、资源守卫、进程清理。
- **创作管理**：项目/合集、提示词预设、作品加入项目、模型市场、通知 Webhook。
- **接口**：Web 工作台、桌面启动器、MCP stdio、HTTP API、AstrBot 桥接（可选插件）。
- **安装**：首次启动向导、环境自检、模型选择/下载、主机模式配置。

技术栈：Python / FastAPI / ComfyUI / PyTorch / ffmpeg / 原生 Web 前端。  
平台：Windows 10/11 + NVIDIA；默认只监听本机回环地址。  
许可：Apache-2.0；ComfyUI、ffmpeg 与模型权重遵循各自上游许可，模型权重不随仓库分发。

## 关键词

`comfyui` `ai-video` `text-to-video` `image-generation` `minimax-h3` `qwen-image` `moss-image` `fastapi` `mcp` `windows` `local-first`

## 社群

- QQ 群：https://qm.qq.com/q/Ww5b5dF5mO
- Issues：https://github.com/Treetore/-Aster-Studio/issues
- 反馈模板：`docs/COMMUNITY.md`
