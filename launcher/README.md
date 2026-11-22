# Aster 桌面启动器

Aster 桌面启动器为本地生成服务提供统一的桌面入口，负责服务启停、状态监控、模型管理、诊断与日志查看。

## 运行方式

| 方式 | 操作 |
|---|---|
| 桌面快捷方式 | 双击 `Aster Studio` |
| 批处理 | `launcher\AsterLauncher.bat` |
| 命令行 | `python launcher\run.py` |
| 浏览器模式 | `python -m uvicorn launcher.app:app --port 8191`，访问 `http://127.0.0.1:8191/` |

启动器使用本地 FastAPI 服务（`127.0.0.1:8191`），通过 Edge/Chrome 的 `--app` 模式打开独立窗口。关闭窗口仅退出启动器，不停止 ComfyUI；服务停止需在「启动」页执行。

## 功能

| 模块 | 说明 |
|---|---|
| 启动管理 | 一键启动、单独启动 ComfyUI/Web、重启、全部停止 |
| 状态监控 | ComfyUI、Web、GPU、显存、内存、磁盘、端口、PID |
| 启动参数 | 普通/低显存/自定义模式，写入 `data/settings.json` |
| 模型管理 | 按目录分类浏览、上传、删除、打开目录 |
| 诊断 | ComfyUI/Web 健康检查、GPU、内存、磁盘、TDR、核心模型文件、日志异常扫描 |
| 版本信息 | ComfyUI 版本、PyTorch 版本、启动器版本 |
| 工具 | 释放显存、设置 TDR、备份配置、创建快捷方式 |
| 控制台 | ComfyUI 与 Web 的 stdout/stderr、MCP 日志 |
| 任务 | 最近任务列表，运行中任务可取消 |
| 设置 | 主题、自动打开 Web、刷新间隔 |
| 自检 | 启动时执行环境自检，结果写入 `logs/launcher_selfcheck.log` |

## 目录

```
launcher/
  app.py          FastAPI 后端
  index.html      启动器界面
  run.py          启动后端并打开应用窗口
  AsterLauncher.bat
  create_shortcut.ps1
```
