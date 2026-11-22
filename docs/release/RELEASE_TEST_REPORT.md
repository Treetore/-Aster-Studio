# 1.0.0 发布前测试报告

- 日期：2026-11-22
- 分支/提交：`main` / 以仓库最新提交为准
- 环境：Windows 11，RTX 4070 Laptop 8GB，32GB RAM
- 测试方式：离线单元测试 + 本机服务冒烟/集成/系统检查；**不执行重型生成**

## 1. 单元测试

```powershell
python -m pytest tests -q
```

结果：**exit 0，全部通过**。覆盖配置、工作流、客户端、安全守卫、编辑、队列、进程清理、安装/自检、工作区 API。

## 2. 冒烟测试

| 检查 | 结果 |
|---|---|
| `compileall` | 0 |
| `node --check webapp/static/studio.js` | 0 |
| `start_all.ps1 -DryRun` | 0 |
| `/api/health` | 200 |
| `/studio` | 200 |
| `/static/studio.css` / `.js` | 200 |
| `/api/setup/status` | 200 |
| `/api/doctor` | 200 |
| `/api/models/market` | 200，569 个模型 |

## 3. 集成测试

| 检查 | 结果 |
|---|---|
| 项目创建/删除 | 通过 |
| 提示词预设 API | 200 |
| i18n API | 200 |
| 队列 API | 200 |
| 模板加载 | 9 个模板 |
| 模型市场同步 | 569 条，含类型/目录/许可/安装状态 |
| 低显存 15s | 正确阻止并提示分段 |
| 高配 15s | 正确放行 |

统一检查脚本：

```powershell
python scripts/release_checks.py
```

结果：**16/16 通过**。

## 4. 系统测试

| 检查 | 结果 |
|---|---|
| Web/ComfyUI 端口 | 8190 / 8188 在线 |
| 启动脚本路径解析 | 通过 |
| 主机模式限制 | low_vram / normal / high 生效 |
| 静态资源与 API 同源 | 通过 |
| 首次启动状态 | 可重置为 fresh |

未执行生成、未占用 GPU 长时间算力。

## 5. 回归测试

结构整理后发现的唯一回归为文档路径断言：

- `tests/test_workflow.py` 仍指向旧 `docs/WORKFLOWS.md`；
- 已改为 `docs/architecture/WORKFLOWS.md`；
- 回归后 `pytest` 再次全绿。

GitHub Actions 离线测试通过。

## 6. 验收测试（由项目所有者执行）

建议验收顺序：

1. 全新目录解压发布包；
2. 运行 `start_all.bat`；
3. 打开 `http://127.0.0.1:8190/`，确认首次启动向导；
4. 选择主机模式、勾选模型、测试模型市场搜索/许可链接；
5. 生成 5s 视频与一张图片；
6. 用 15s 任务确认低配分段/高配直出行为；
7. 检查项目页创建项目、播放器“加入项目”；
8. 检查任务队列、通知 Webhook、i18n 切换；
9. 运行 `python scripts/release_checks.py`。

验收通过后再打正式 1.0.0 tag。

## 已知限制

- 市场部分模型下载需要上游登录或手动确认许可；
- 队列为进程内队列，重启不保留；
- 前端尚未迁移组件框架；
- 完整表单级 i18n 仍待补充；
- 模型识别依赖文件名与目录。
