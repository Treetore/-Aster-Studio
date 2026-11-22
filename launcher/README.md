# Aster · ComfyUI 桌面启动器（本项目定制版）

参考 [绘世启动器](https://www.bilibili.com/video/BV1ne4y1V7QU) 的交互形态，把 `comfy_mcp` 现有能力
（ComfyUI 便携包、Web 工作台、资源守卫、任务记录、日志、模型、文档）收进一个桌面窗口。

- 形态：本地 FastAPI（`127.0.0.1:8191`）+ Edge/Chrome `--app` 独立窗口（无地址栏，接近原生桌面应用）
- 不引入额外运行时：复用 `ComfyUI_windows_portable\python_embeded` 的 FastAPI/uvicorn/psutil
- 关闭窗口只退出启动器，不停止 ComfyUI（与绘世行为一致，可在「启动」页显式停止）

## 打开方式

| 方式 | 操作 |
|---|---|
| 桌面快捷方式 | 双击「Aster启动器」 |
| 批处理 | `launcher\启动启动器.bat` |
| 命令行 | `python launcher\run.py`（会自动开窗口） |
| 纯浏览器 | `python -m uvicorn launcher.app:app --port 8191`，访问 http://127.0.0.1:8191/ |

## 功能对照（绘世 → 本启动器）

| 绘世 | 本启动器 |
|---|---|
| 启动界面 | 一键启动 / 仅 ComfyUI / 仅 Web / 重启 / 停止全部；ComfyUI、Web、GPU、显存、内存、磁盘、端口 PID、TDR 状态卡片 |
| 高级选项 | 启动模式（普通 / 低显存 / 自定义）+ 启动参数逐行编辑，写入 `data/settings.json → comfy.start_args` |
| 模型管理 | 按 `diffusion_models / text_encoders / vae / loras / checkpoints / upscale_models ...` 分类列大小/时间，删除=移入 `models/_trash`，打开目录 |
| 疑难解答 | 体检：ComfyUI/Web 健康、GPU、内存、磁盘、TDR、核心模型文件、日志异常扫描，每项带「修复」按钮（重启服务 / TDR / 释放显存 / 打开目录） |
| 版本管理 | 显示 ComfyUI 版本、PyTorch 版本、启动器版本（更新仍走项目现有流程） |
| 小工具 | 释放显存、写 TDR=60、备份（settings/jobs/docs → `backups/*.zip`）、创建桌面快捷方式、Qwen 1024 快速测试、H3 2s 快速测试 |
| 控制台 | ComfyUI stderr/stdout、Web stderr/stdout、MCP 日志，300 行滚动 + 自动刷新 + 复制 |
| 任务 | 最近 20 条任务（模板/状态/时间/输出），运行中可取消 |
| 设置 | 深/浅色主题、自动打开 Web、刷新间隔 |
| 部署/内核安装 | 未实现（本项目使用现成便携包；如需可从零部署再扩展） |

## 目录

```
launcher/
  app.py               FastAPI 后端（状态/启停/模型/诊断/日志/任务/工具）
  index.html           启动器界面
  run.py               启动后端 + 打开 Edge app 窗口
  启动启动器.bat        双击入口（pythonw，无控制台）
  create_shortcut.ps1  创建桌面快捷方式（UTF-8 BOM，PS5.1 兼容）
```

## 兼容性修复记录

- ComfyUI 启动必须带 `PYTHONUTF8=1 / PYTHONIOENCODING=utf-8`：某自定义节点加载失败时会打印含 emoji 的
  traceback，GBK 控制台会二次抛 `UnicodeEncodeError` 并导致启动崩溃。启动器的 `spawn()` 已注入这两个环境变量。
- 快捷方式脚本含中文，PowerShell 5.1 需 UTF-8 **带 BOM**，否则解析报「字符串缺少终止符」。

## API（供二次开发）

```
GET  /api/overview            状态总览
POST /api/start  {scope}      all|comfy|web
POST /api/stop   {scope}      all|comfy|web
POST /api/restart{scope}
GET  /api/settings            comfy.start_args + launcher 段
POST /api/settings            保存参数/主题/自动打开/刷新间隔
GET  /api/models              模型分组与文件
POST /api/models/delete       {group,name} → models/_trash
GET  /api/doctor              体检项 + fix id
POST /api/action/{free|apply_tdr|restart_comfy|restart_web|open_logs|open_models|backup|shortcut}
POST /api/action/quicktest/{qwen|h3}
GET  /api/logs?name=comfy_err&tail=300
GET  /api/jobs?limit=20
POST /api/jobs/{id}/cancel
```


## 个性化（Aster 1.1.0）

- 设置页「主题与外观」：**深色 / 浅色 / 莫奈（印象派）** 三套主题
- 背景图片：内置 3 张本机 Qwen-Image 2.1 生成的印象派背景（`launcher/assets/bg_monet_lilies|garden|sunrise.png`），
  也可上传自定义图片（png/jpg/webp ≤20MB，保存到 `launcher/assets/`）
- 可调：背景模糊 0–30px、遮罩浓度 0–0.9、强调色（取色器）
- 配置持久化在 `data/settings.json → launcher.{theme,bg,bg_blur,bg_dim,accent}`
- 主题切换即时预览，「保存外观」写入配置；无背景时回退纯色主题
