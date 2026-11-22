# 项目结构

## 目录

```text
comfy_mcp/
├─ webapp/                  Web 工作台与 API（FastAPI）
│  ├─ app.py
│  ├─ studio.html
│  ├─ static/               拆分后的 CSS / JS
│  └─ i18n.py
├─ launcher/                本机启动器（Tk/Web 控制面板）
├─ setup/                   安装、自检、模型目录、ComfyUI 市场桥接
├─ templates/               H3 / Qwen / MOSS 工作流模板
├─ tools/                   命令行工具与运维脚本
├─ optional/                可选 AstrBot 插件（默认不安装）
├─ tests/                   离线测试与 fixtures
├─ docs/
│  ├─ architecture/         架构、工作流、运行参数、SPEC
│  ├─ policies/             生成策略、时长限制、压力测试结论
│  ├─ features/             编辑、长片、MOSS、素材引用
│  ├─ integrations/         外部集成
│  └─ release/              发布检查清单
├─ scripts/                 辅助脚本（长片、CLI、安全启动）
├─ server.py                MCP / API 服务入口
├─ workflow.py              工作流解析与模板生成
├─ config.py                路径、设置与环境变量
├─ start_all.ps1 / .bat     服务启动入口
├─ stop_all.ps1 / .bat      服务停止入口
├─ pyproject.toml
└─ README.md / SETUP.md
```

## 约定

- 根目录只放应用入口、核心运行时模块与项目级配置。
- 面向用户的页面资源放 `webapp/`，页面样式/脚本不再内联在 HTML。
- 安装与模型相关逻辑放 `setup/`；外部市场同步放 `setup/market.py`。
- 运行数据写入 `data/`、`logs/`、`artifacts/`、`dist/`，均被 `.gitignore` 忽略。
- 文档按主题归档；新增文档请放入对应目录并更新 `docs/README.md`。
- 辅助脚本放 `scripts/`；根目录 `start_all` / `stop_all` 保持为唯一服务启停入口。
- 测试不依赖 GPU、模型权重或外部 ComfyUI 工作流；缺失 fixture 时自动 skip。
